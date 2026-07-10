/* eslint-disable react-refresh/only-export-components -- provider and its typed hook form one public context boundary */
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react'
import {
  CognitradeApiClient,
  CognitradeApiError,
  loadApiConnection,
  saveApiConnection,
  type ApiConnectionSettings,
} from './api-client'
import { isActiveStatus } from './live-data'
import type {
  AgentOSDashboardView,
  ResearchIntent,
  ResearchResponse,
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
  selectedRun: RunDetail | null
  screeningRun: RunDetail | null
  selectedRunLoading: boolean
  submitting: boolean
  submittedQuery: string
  lastResponse: ResearchResponse | null
  error: string
  configure: (settings: ApiConnectionSettings) => void
  refresh: () => Promise<void>
  selectRun: (runId: string) => void
  reloadRun: (runId: string) => Promise<void>
  createRun: (query: string, intent?: ResearchIntent) => Promise<ResearchResponse>
  cancelRun: (runId: string) => Promise<void>
  rerunRun: (runId: string, fromNode: string) => Promise<ResearchResponse>
  clearError: () => void
}

const CognitradeContext = createContext<CognitradeContextValue | null>(null)
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
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null)
  const [screeningRun, setScreeningRun] = useState<RunDetail | null>(null)
  const [selectedRunLoading, setSelectedRunLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [lastResponse, setLastResponse] = useState<ResearchResponse | null>(null)
  const [error, setError] = useState('')
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
        client.listRuns(50),
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
  useEffect(() => {
    if (!selectedRunId || !isActiveStatus(String(selectedStatus || ''))) return
    const timer = window.setInterval(() => {
      void loadRun(selectedRunId, true)
      void client.listRuns(50).then(setRuns).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [client, loadRun, selectedRunId, selectedStatus])

  const configure = useCallback((nextSettings: ApiConnectionSettings) => {
    setSettings(saveApiConnection(nextSettings))
    setError('')
  }, [])

  const createRun = useCallback(
    async (query: string, intent?: ResearchIntent) => {
      setSubmitting(true)
      setSubmittedQuery(query)
      setError('')
      try {
        const response = await client.createRun({
          user_query: query,
          intent,
        })
        setLastResponse(response)
        if (response.run_id) {
          setSelectedRunId(response.run_id)
          await loadRun(response.run_id)
        }
        await refresh()
        return response
      } catch (runError) {
        setConnectionState(connectionFromError(runError))
        setError(errorMessage(runError))
        throw runError
      } finally {
        setSubmitting(false)
      }
    },
    [client, loadRun, refresh],
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
      selectedRun,
      screeningRun,
      selectedRunLoading,
      submitting,
      submittedQuery,
      lastResponse,
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
      selectedRun,
      screeningRun,
      selectedRunLoading,
      submitting,
      submittedQuery,
      lastResponse,
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

export function useCognitrade(): CognitradeContextValue {
  const value = useContext(CognitradeContext)
  if (!value) throw new Error('useCognitrade must be used inside CognitradeProvider')
  return value
}
