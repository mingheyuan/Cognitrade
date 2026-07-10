import { CheckCircle2, X } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { RunInspector } from './components/RunInspector'
import { Sidebar } from './components/Sidebar'
import { Topbar } from './components/Topbar'
import type { ViewId } from './data'
import {
  createAgentOSRun,
  defaultIntent,
  getAgentOSReplay,
  subscribeToExecutionEvents,
} from './api'
import type { AgentOSReplay, ComposerIntent } from './research-contracts'

const Workbench = lazy(() => import('./components/Workbench').then((module) => ({ default: module.Workbench })))
const Overview = lazy(() => import('./components/Overview').then((module) => ({ default: module.Overview })))
const Screening = lazy(() => import('./components/Screening').then((module) => ({ default: module.Screening })))
const Methodology = lazy(() => import('./components/Methodology').then((module) => ({ default: module.Methodology })))
const Runs = lazy(() => import('./components/Runs').then((module) => ({ default: module.Runs })))

function App() {
  const [activeView, setActiveView] = useState<ViewId>('workbench')
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(100)
  const [lastQuery, setLastQuery] = useState('')
  const [selectedEvidence, setSelectedEvidence] = useState('E01')
  const [selectedRun, setSelectedRun] = useState<AgentOSReplay | null>(null)
  const [runRefreshToken, setRunRefreshToken] = useState(0)
  const [toast, setToast] = useState('')
  const unsubscribeEvents = useRef<(() => void) | undefined>(undefined)
  const toastTimer = useRef<number | undefined>(undefined)

  const focusComposer = useCallback(() => {
    setActiveView('workbench')
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>('#research-composer')?.focus()
    })
  }, [])

  const openRun = useCallback(async (runId: string) => {
    const replay = await getAgentOSReplay(runId)
    setSelectedRun(replay)
    const request = replay.request?.request as Record<string, unknown> | undefined
    setLastQuery(String(request?.user_query ?? ''))
    setSelectedEvidence(replay.artifacts[0]?.artifact_id ?? '')
  }, [])

  const handleResearch = useCallback(async (query: string, intent: ComposerIntent = 'auto') => {
    unsubscribeEvents.current?.()
    if (toastTimer.current) window.clearTimeout(toastTimer.current)

    setLastQuery(query)
    setActiveView('workbench')
    setProgress(5)
    setRunning(true)
    setToast('')

    try {
      const response = await createAgentOSRun({
        intent: defaultIntent(intent),
        user_query: query,
      })
      if (response.run_id) {
        await openRun(response.run_id)
        setRunRefreshToken((value) => value + 1)
        unsubscribeEvents.current = subscribeToExecutionEvents(
          response.run_id,
          (event) => {
            const eventType = String(event.event_type ?? '')
            if (eventType === 'graph_run_started') setProgress(15)
            else if (eventType === 'graph_frontier_ready') setProgress(45)
            else if (eventType === 'graph_node_completed') setProgress((value) => Math.min(90, value + 15))
            else if (eventType === 'graph_run_finished') setProgress(100)
          },
          (error) => setToast(error.message),
        )
      }
      setProgress(100)
      setRunning(false)
      setToast(response.status === 'completed' ? 'Research completed.' : response.message)
      toastTimer.current = window.setTimeout(() => setToast(''), 4200)
    } catch (error) {
      setRunning(false)
      setProgress(0)
      setToast(error instanceof Error ? error.message : 'Research request failed.')
    }
  }, [openRun])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        focusComposer()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      unsubscribeEvents.current?.()
      if (toastTimer.current) window.clearTimeout(toastTimer.current)
    }
  }, [focusComposer])

  const renderView = () => {
    if (activeView === 'workbench') {
      return (
        <div className="workspace-grid">
          <Workbench
            running={running}
            progress={progress}
            lastQuery={lastQuery}
            onSubmit={handleResearch}
            onSelectEvidence={setSelectedEvidence}
            run={selectedRun}
          />
          <RunInspector
            running={running}
            progress={progress}
            selectedEvidence={selectedEvidence}
            onSelectEvidence={setSelectedEvidence}
            run={selectedRun}
          />
        </div>
      )
    }

    if (activeView === 'overview') {
      return (
        <Overview
          onSubmit={handleResearch}
          running={running}
          onOpenWorkbench={() => setActiveView('workbench')}
          onOpenRuns={() => setActiveView('runs')}
        />
      )
    }

    if (activeView === 'screening') return <Screening />
    if (activeView === 'methodology') return <Methodology />
    return (
      <Runs
        activeRunId={selectedRun?.run_id}
        refreshToken={runRefreshToken}
        onSelectRun={(runId) => {
          void openRun(runId)
          setActiveView('workbench')
        }}
      />
    )
  }

  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onNavigate={setActiveView} onNewResearch={focusComposer} />
      <section className="app-workspace">
        <Topbar activeView={activeView} />
        <Suspense
          fallback={
            <div className="page-loader" role="status">
              <span />
              <strong>正在载入研究视图</strong>
            </div>
          }
        >
          {renderView()}
        </Suspense>
      </section>

      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={17} />
          <span>{toast}</span>
          <button onClick={() => setToast('')} aria-label="关闭提示">
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  )
}

export default App
