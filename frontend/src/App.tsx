import { AlertTriangle, CheckCircle2, X } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { useCognitrade } from './CognitradeContext'
import { RunInspector } from './components/RunInspector'
import { Sidebar } from './components/Sidebar'
import { Topbar } from './components/Topbar'
import type { ViewId } from './data'
import type { ResearchIntent } from './research-contracts'

const Workbench = lazy(() => import('./components/Workbench').then((module) => ({ default: module.Workbench })))
const Overview = lazy(() => import('./components/Overview').then((module) => ({ default: module.Overview })))
const Screening = lazy(() => import('./components/Screening').then((module) => ({ default: module.Screening })))
const Methodology = lazy(() => import('./components/Methodology').then((module) => ({ default: module.Methodology })))
const Runs = lazy(() => import('./components/Runs').then((module) => ({ default: module.Runs })))

function App() {
  const {
    submitting,
    createRun,
    selectRun,
    error,
    clearError,
  } = useCognitrade()
  const [activeView, setActiveView] = useState<ViewId>('workbench')
  const [selectedEvidence, setSelectedEvidence] = useState('')
  const [toast, setToast] = useState('')
  const toastTimer = useRef<number | undefined>(undefined)

  const showToast = useCallback((message: string) => {
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    setToast(message)
    toastTimer.current = window.setTimeout(() => setToast(''), 4200)
  }, [])

  const focusComposer = useCallback(() => {
    setActiveView('workbench')
    window.requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('#research-composer')?.focus())
  }, [])

  const handleResearch = useCallback(
    async (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => {
      setActiveView('workbench')
      setSelectedEvidence('')
      try {
        const response = await createRun(query, intent, constraints)
        showToast(
          response.status === 'completed'
            ? '研究运行已完成，已加载真实报告与证据链。'
            : response.clarification?.question || response.message,
        )
      } catch {
        // The shared context renders the transport error with its request id.
      }
    },
    [createRun, showToast],
  )

  const openRun = useCallback(
    (runId: string) => {
      selectRun(runId)
      setSelectedEvidence('')
      setActiveView('workbench')
    },
    [selectRun],
  )

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
      if (toastTimer.current) window.clearTimeout(toastTimer.current)
    }
  }, [focusComposer])

  const renderView = () => {
    if (activeView === 'workbench') {
      return (
        <div className="workspace-grid">
          <Workbench onSubmit={handleResearch} onSelectEvidence={setSelectedEvidence} />
          <RunInspector selectedEvidence={selectedEvidence} onSelectEvidence={setSelectedEvidence} />
        </div>
      )
    }
    if (activeView === 'overview') {
      return <Overview onSubmit={handleResearch} onOpenWorkbench={() => setActiveView('workbench')} onOpenRuns={() => setActiveView('runs')} onOpenRun={openRun} />
    }
    if (activeView === 'screening') return <Screening onSubmit={handleResearch} onOpenRun={openRun} />
    if (activeView === 'methodology') return <Methodology />
    return <Runs onOpenRun={openRun} />
  }

  return (
    <div className="app-shell">
      <Sidebar activeView={activeView} onNavigate={setActiveView} onNewResearch={focusComposer} onOpenRun={openRun} />
      <section className="app-workspace">
        <Topbar activeView={activeView} />
        {error && (
          <div className="api-error-banner" role="alert">
            <AlertTriangle size={16} />
            <span>{error}</span>
            <button onClick={clearError} aria-label="关闭错误"><X size={14} /></button>
          </div>
        )}
        <Suspense fallback={<div className="page-loader" role="status"><span /><strong>正在载入研究视图</strong></div>}>
          {renderView()}
        </Suspense>
      </section>

      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={17} /><span>{toast}</span>
          <button onClick={() => setToast('')} aria-label="关闭提示"><X size={14} /></button>
        </div>
      )}
      {submitting && <div className="global-run-indicator"><span /><strong>后端正在同步执行研究任务</strong><small>当前 v1 会在运行结束后返回 run_id</small></div>}
    </div>
  )
}

export default App
