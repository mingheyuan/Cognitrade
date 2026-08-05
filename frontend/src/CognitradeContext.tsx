/* eslint-disable react-refresh/only-export-components -- provider and its typed hook form one public context boundary */
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  CognitradeApiClient,
  CognitradeApiError,
  loadApiConnection,
  saveApiConnection,
  type ApiConnectionSettings,
} from './api-client'
import { hasFinalDelivery, isActiveStatus } from './live-data'
import type {
  AgentOSDashboardView,
  ResearchIntent,
  ResearchResponse,
  RunEvent,
  RunDetail,
  RunHistoryRecord,
  SystemStatus,
} from './research-contracts'

type ConnectionState = 'connecting' | 'online' | 'auth-required' | 'offline'

type CognitradeContextValue = {
  settings: ApiConnectionSettings
  connectionState: ConnectionState
  systemStatus: SystemStatus | null
  runs: RunHistoryRecord[]
  overviewDashboard: AgentOSDashboardView | null
  screeningDashboard: AgentOSDashboardView | null
  methodologyDashboard: AgentOSDashboardView | null
  selectedRunId: string
  chatThreadId: string
  selectedRun: RunDetail | null
  screeningRun: RunDetail | null
  selectedRunLoading: boolean
  submitting: boolean
  submittedQuery: string
  lastResponse: ResearchResponse | null
  liveProgress: string
  liveAnswer: string
  liveEvents: RunEvent[]
  error: string
  configure: (settings: ApiConnectionSettings) => void
  refresh: () => Promise<void>
  selectRun: (runId: string) => void
  reloadRun: (runId: string) => Promise<void>
  createRun: (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => Promise<ResearchResponse>
  cancelRun: (runId: string) => Promise<void>
  rerunRun: (runId: string, fromNode: string) => Promise<ResearchResponse>
  clearError: () => void
}

const CognitradeContext = createContext<CognitradeContextValue | null>(null)
const CHAT_THREAD_STORAGE_KEY = 'cognitrade.chat.thread-id'
function errorMessage(error: unknown): string {
  if (error instanceof CognitradeApiError) {
    const requestId = error.requestId ? ` · request ${error.requestId}` : ''
    return `${error.message}${requestId}`
  }
  return error instanceof Error ? error.message : String(error)
}

function connectionFromError(error: unknown): ConnectionState {
  if (!(error instanceof CognitradeApiError)) return 'offline'
  if (error.status === 401) return 'auth-required'
  return error.status === 0 ? 'offline' : 'online'
}

export function CognitradeProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<ApiConnectionSettings>(() => loadApiConnection())
  const [connectionState, setConnectionState] = useState<ConnectionState>('connecting')
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [runs, setRuns] = useState<RunHistoryRecord[]>([])
  const [overviewDashboard, setOverviewDashboard] = useState<AgentOSDashboardView | null>(null)
  const [screeningDashboard, setScreeningDashboard] = useState<AgentOSDashboardView | null>(null)
  const [methodologyDashboard, setMethodologyDashboard] = useState<AgentOSDashboardView | null>(null)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [chatThreadId, setChatThreadId] = useState(
    () => localStorage.getItem(CHAT_THREAD_STORAGE_KEY) || '',
  )
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null)
  const [screeningRun, setScreeningRun] = useState<RunDetail | null>(null)
  const [selectedRunLoading, setSelectedRunLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [lastResponse, setLastResponse] = useState<ResearchResponse | null>(null)
  const [liveProgress, setLiveProgress] = useState('')
  const [liveAnswer, setLiveAnswer] = useState('')
  const [liveEvents, setLiveEvents] = useState<RunEvent[]>([])
  const [error, setError] = useState('')
  const streamAbort = useRef<AbortController | null>(null)
  const streamCursors = useRef<Record<string, string>>({})
  const client = useMemo(() => new CognitradeApiClient(settings), [settings])

  const loadRun = useCallback(
    async (runId: string, quiet = false) => {
      if (!runId) {
        setSelectedRun(null)
        return
      }
      if (!quiet) setSelectedRunLoading(true)
      try {
        const detail = await client.getRunDetail(runId)
        setSelectedRun(detail)
        setConnectionState('online')
      } catch (loadError) {
        setError(errorMessage(loadError))
        setConnectionState(connectionFromError(loadError))
      } finally {
        if (!quiet) setSelectedRunLoading(false)
      }
    },
    [client],
  )

  const refresh = useCallback(async () => {
    setConnectionState('connecting')
    setError('')
    try {
      const status = await client.getSystemStatus()
      setSystemStatus(status)
      const [runResult, overviewResult, screeningResult, methodologyResult] = await Promise.allSettled([
        client.listAllRuns(),
        client.getDashboard('finance_ashare', 'overview'),
        client.getDashboard('finance_ashare', 'screening'),
        client.getDashboard('finance_ashare', 'methodology'),
      ])
      if (runResult.status === 'fulfilled') setRuns(runResult.value)
      else setError(errorMessage(runResult.reason))
      if (overviewResult.status === 'fulfilled') setOverviewDashboard(overviewResult.value)
      else setError(errorMessage(overviewResult.reason))
      if (screeningResult.status === 'fulfilled') setScreeningDashboard(screeningResult.value)
      else setError(errorMessage(screeningResult.reason))
      if (methodologyResult.status === 'fulfilled') setMethodologyDashboard(methodologyResult.value)
      else setError(errorMessage(methodologyResult.reason))
      setConnectionState('online')
    } catch (refreshError) {
      setSystemStatus(null)
      setConnectionState(connectionFromError(refreshError))
      setError(errorMessage(refreshError))
    }
  }, [client])

  const subscribeToRun = useCallback(
    async (runId: string) => {
      if (!runId) return
      streamAbort.current?.abort()
      const controller = new AbortController()
      streamAbort.current = controller
      let reconnectAttempt = 0
      let terminalEventSeen = false
      setLiveEvents([])
      setLiveProgress('正在连接 Agent OS 事件流…')

      while (!controller.signal.aborted) {
        try {
          await client.streamRunEvents(
            runId,
            (event) => {
              const eventId = String(event.event_id || '').trim()
              if (eventId) streamCursors.current[runId] = eventId
              setLiveEvents((current) => {
                if (eventId && current.some((item) => item.event_id === eventId)) return current
                return [...current, event]
              })
              const eventMessage = String(event.message || '')
              const isFinalMessageEvent = ['assistant_message_delta', 'assistant_message_ready'].includes(String(event.event_type || ''))
              const isReportProgressEvent = String(event.event_type || '') === 'agent_progress' && eventMessage.startsWith('research_report:')
              if (event.message && !isFinalMessageEvent && !isReportProgressEvent) setLiveProgress(event.message)
              if (event.event_type === 'assistant_message_delta' && event.message) {
                setLiveAnswer((current) => `${current}${event.message}`)
              }
              if (event.event_type === 'assistant_message_ready' && event.message) {
                setLiveAnswer(event.message)
              }
              const eventType = String(event.event_type || '')
              const isTerminal = ['main_agent_completed', 'main_agent_failed'].includes(eventType)
                || (eventType === 'assistant_message_ready' && String(event.delivery_status || '') !== 'paused')
              if (isTerminal) {
                terminalEventSeen = true
                void loadRun(runId, true)
                void client.listAllRuns().then(setRuns).catch(() => undefined)
              }
            },
            controller.signal,
            streamCursors.current[runId],
          )
          if (terminalEventSeen) return
          reconnectAttempt += 1
          if (reconnectAttempt > 5) return
          await waitForStreamReconnect(reconnectAttempt, controller.signal)
        } catch (streamError) {
          if (controller.signal.aborted) return
          reconnectAttempt += 1
          if (reconnectAttempt > 5) {
            setError(errorMessage(streamError))
            return
          }
          setLiveProgress(`Agent OS 事件流断开，正在重连 (${reconnectAttempt}/5)`)
          await waitForStreamReconnect(reconnectAttempt, controller.signal)
        }
      }
    },
    [client, loadRun],
  )

  useEffect(() => {
    queueMicrotask(() => void refresh())
  }, [refresh])

  useEffect(() => {
    if (!selectedRunId && runs[0]?.run_id) {
      const firstRunId = runs[0].run_id
      queueMicrotask(() => setSelectedRunId(firstRunId))
    }
  }, [runs, selectedRunId])

  useEffect(() => {
    if (selectedRunId) queueMicrotask(() => void loadRun(selectedRunId))
  }, [loadRun, selectedRunId])

  useEffect(() => {
    const screeningRunId = runs.find((run) => run.intent === 'stock_screen')?.run_id
    if (!screeningRunId) {
      queueMicrotask(() => setScreeningRun(null))
      return
    }
    void client.getRunDetail(screeningRunId).then(setScreeningRun).catch(() => setScreeningRun(null))
  }, [client, runs])

  const selectedStatus = runs.find((run) => run.run_id === selectedRunId)?.status
  const selectedNeedsDelivery = Boolean(
    selectedRunId
      && (
        isActiveStatus(String(selectedStatus || ''))
        || (['completed', 'gated', 'failed', 'cancelled', 'timed_out'].includes(String(selectedStatus || ''))
          && !hasFinalDelivery(selectedRun))
      ),
  )
  useEffect(() => {
    if (!selectedRunId || !selectedNeedsDelivery) return
    let disposed = false
    queueMicrotask(() => {
      if (!disposed) void subscribeToRun(selectedRunId)
    })
    return () => {
      disposed = true
      streamAbort.current?.abort()
    }
  }, [selectedNeedsDelivery, selectedRunId, subscribeToRun])

  useEffect(() => {
    if (!selectedRunId || !selectedNeedsDelivery) return
    const timer = window.setInterval(() => {
      void loadRun(selectedRunId, true)
      void client.listAllRuns().then(setRuns).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [client, loadRun, selectedNeedsDelivery, selectedRunId])

  const configure = useCallback((nextSettings: ApiConnectionSettings) => {
    setSettings(saveApiConnection(nextSettings))
    setError('')
  }, [])

  const createChatThread = useCallback(async () => {
    const created = await client.createChatThread()
    const nextThreadId = String(created.thread_id || '').trim()
    if (!nextThreadId) throw new Error('Chat API returned no thread_id')
    localStorage.setItem(CHAT_THREAD_STORAGE_KEY, nextThreadId)
    setChatThreadId(nextThreadId)
    return nextThreadId
  }, [client])

  const ensureChatThread = useCallback(async () => {
    if (chatThreadId) return chatThreadId
    return createChatThread()
  }, [chatThreadId, createChatThread])

  const createRun = useCallback(
    async (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => {
      setSubmitting(true)
      setSubmittedQuery(query)
      setLiveProgress('正在提交到 Agent OS…')
      setLiveAnswer('')
      setLiveEvents([])
      setError('')
      try {
        let activeThreadId = await ensureChatThread()
        const nextConstraints = {
          ...(constraints || {}),
          background: true,
        }
        let response: ResearchResponse
        try {
          response = await client.submitChatRun(activeThreadId, {
            user_query: query,
            ...(intent ? { intent } : {}),
            constraints: nextConstraints,
          })
        } catch (submitError) {
          if (!(submitError instanceof CognitradeApiError) || submitError.status !== 404) {
            throw submitError
          }
          localStorage.removeItem(CHAT_THREAD_STORAGE_KEY)
          setChatThreadId('')
          activeThreadId = await createChatThread()
          response = await client.submitChatRun(activeThreadId, {
            user_query: query,
            ...(intent ? { intent } : {}),
            constraints: nextConstraints,
          })
        }
        setLastResponse(response)
        if (response.run_id) {
          setSelectedRunId(response.run_id)
          await loadRun(response.run_id)
          void subscribeToRun(response.run_id)
        }
        await refresh()
        if (response.run_id) {
          // Refresh updates the history list asynchronously; reload the submitted run
          // afterward so an older selected run cannot replace the new result.
          setSelectedRunId(response.run_id)
          await loadRun(response.run_id, true)
        }
        return response
      } catch (runError) {
        setConnectionState(connectionFromError(runError))
        setError(errorMessage(runError))
        throw runError
      } finally {
        setSubmitting(false)
      }
    },
    [client, createChatThread, ensureChatThread, loadRun, refresh, subscribeToRun],
  )

  const cancelRun = useCallback(
    async (runId: string) => {
      setError('')
      try {
        await client.cancelRun(runId)
        await Promise.all([refresh(), loadRun(runId)])
      } catch (cancelError) {
        setError(errorMessage(cancelError))
        throw cancelError
      }
    },
    [client, loadRun, refresh],
  )

  const rerunRun = useCallback(
    async (runId: string, fromNode: string) => {
      setSubmitting(true)
      setError('')
      try {
        const response = await client.rerunRun(runId, fromNode)
        setLastResponse(response)
        if (response.run_id) setSelectedRunId(response.run_id)
        await refresh()
        if (response.run_id) await loadRun(response.run_id)
        return response
      } catch (rerunError) {
        setError(errorMessage(rerunError))
        throw rerunError
      } finally {
        setSubmitting(false)
      }
    },
    [client, loadRun, refresh],
  )

  const value = useMemo<CognitradeContextValue>(
    () => ({
      settings,
      connectionState,
      systemStatus,
      runs,
      overviewDashboard,
      screeningDashboard,
      methodologyDashboard,
      selectedRunId,
      chatThreadId,
      selectedRun,
      screeningRun,
      selectedRunLoading,
      submitting,
      submittedQuery,
      lastResponse,
      liveProgress,
      liveAnswer,
      liveEvents,
      error,
      configure,
      refresh,
      selectRun: setSelectedRunId,
      reloadRun: loadRun,
      createRun,
      cancelRun,
      rerunRun,
      clearError: () => setError(''),
    }),
    [
      settings,
      connectionState,
      systemStatus,
      runs,
      overviewDashboard,
      screeningDashboard,
      methodologyDashboard,
      selectedRunId,
      chatThreadId,
      selectedRun,
      screeningRun,
      selectedRunLoading,
      submitting,
      submittedQuery,
      lastResponse,
      liveProgress,
      liveAnswer,
      liveEvents,
      error,
      configure,
      refresh,
      loadRun,
      createRun,
      cancelRun,
      rerunRun,
    ],
  )

  return <CognitradeContext.Provider value={value}>{children}</CognitradeContext.Provider>
}

async function waitForStreamReconnect(attempt: number, signal: AbortSignal): Promise<void> {
  const delay = Math.min(5000, 250 * 2 ** Math.max(0, attempt - 1))
  await new Promise<void>((resolve) => {
    const timer = window.setTimeout(resolve, delay)
    signal.addEventListener('abort', () => {
      window.clearTimeout(timer)
      resolve()
    }, { once: true })
  })
}

export function useCognitrade(): CognitradeContextValue {
  const value = useContext(CognitradeContext)
  if (!value) throw new Error('useCognitrade must be used inside CognitradeProvider')
  return value
}
