import { CheckCircle2, X } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { RunInspector } from './components/RunInspector'
import { Sidebar } from './components/Sidebar'
import { Topbar } from './components/Topbar'
import type { ViewId } from './data'

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
  const [toast, setToast] = useState('')
  const runTimer = useRef<number | undefined>(undefined)
  const toastTimer = useRef<number | undefined>(undefined)

  const focusComposer = useCallback(() => {
    setActiveView('workbench')
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>('#research-composer')?.focus()
    })
  }, [])

  const handleResearch = useCallback((query: string) => {
    if (runTimer.current) window.clearInterval(runTimer.current)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)

    setLastQuery(query)
    setActiveView('workbench')
    setProgress(8)
    setRunning(true)
    setToast('')

    let current = 8
    runTimer.current = window.setInterval(() => {
      current = Math.min(100, current + (current < 60 ? 11 : 7))
      setProgress(current)
      if (current >= 100) {
        if (runTimer.current) window.clearInterval(runTimer.current)
        runTimer.current = undefined
        window.setTimeout(() => {
          setRunning(false)
          setToast('研究运行已完成，结构化报告与证据链已更新。')
          toastTimer.current = window.setTimeout(() => setToast(''), 4200)
        }, 450)
      }
    }, 620)
  }, [])

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
      if (runTimer.current) window.clearInterval(runTimer.current)
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
          />
          <RunInspector
            running={running}
            progress={progress}
            selectedEvidence={selectedEvidence}
            onSelectEvidence={setSelectedEvidence}
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
    return <Runs />
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
