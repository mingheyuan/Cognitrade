import { AlertTriangle, CheckCircle2, X } from 'lucide-react'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { useCognitrade } from './CognitradeContext'
import { Sidebar } from './components/Sidebar'
import { Topbar } from './components/Topbar'
import { WorkspaceView } from './components/WorkspaceView'
import { useViewNavigation } from './hooks/useViewNavigation'
import type { MarketDataViewState } from './components/MarketData'
import type { BacktestTarget } from './finance-contracts'
import type { ResearchIntent } from './research-contracts'
import type { ViewId } from './data'

function App() {
  const {
    submitting,
    createRun,
    selectRun,
    error,
    clearError,
  } = useCognitrade()
  const { activeView, navigate } = useViewNavigation()
  const [marketDataState, setMarketDataState] = useState<MarketDataViewState>({
    query: '600519',
    data: null,
    loading: false,
    error: '',
  })
  const [selectedEvidence, setSelectedEvidence] = useState('')
  const [backtestTarget, setBacktestTarget] = useState<BacktestTarget | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [toast, setToast] = useState('')
  const toastTimer = useRef<number | undefined>(undefined)

  const showToast = useCallback((message: string) => {
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    setToast(message)
    toastTimer.current = window.setTimeout(() => setToast(''), 4200)
  }, [])

  const focusComposer = useCallback(() => {
    navigate('workbench')
    window.requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('#research-composer')?.focus())
  }, [navigate])

  const handleResearch = useCallback(
    async (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => {
      navigate('workbench')
      setSelectedEvidence('')
      try {
        const response = await createRun(query, intent, constraints)
        showToast(
          ['completed', 'completed_partial'].includes(String(response.status))
            ? '研究运行已完成，已加载真实报告与证据链。'
            : response.clarification?.question || response.message,
        )
      } catch {
        // The shared context renders the transport error with its request id.
      }
    },
    [createRun, navigate, showToast],
  )

  const openRun = useCallback(
    (runId: string) => {
      selectRun(runId)
      setSelectedEvidence('')
      navigate('workbench')
    },
    [navigate, selectRun],
  )

  const openBacktest = useCallback((target: BacktestTarget) => {
    setBacktestTarget(target)
    navigate('backtest')
  }, [navigate])

  const navigateView = useCallback((view: ViewId) => {
    if (view === 'backtest') setBacktestTarget(null)
    navigate(view)
  }, [navigate])

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

  return (
    <div className={`app-shell ${sidebarCollapsed ? 'is-sidebar-collapsed' : ''}`}>
      <Sidebar
        activeView={activeView}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
        onNavigate={navigateView}
        onNewResearch={focusComposer}
        onOpenRun={openRun}
      />
      <section className="app-workspace">
        <Topbar activeView={activeView} onOpenRun={openRun} />
        {error && (
          <div className="api-error-banner" role="alert">
            <AlertTriangle size={16} />
            <span>{error}</span>
            <button onClick={clearError} aria-label="关闭错误"><X size={14} /></button>
          </div>
        )}
        <Suspense fallback={<div className="page-loader" role="status"><span /><strong>正在载入研究视图</strong></div>}>
          <WorkspaceView
            activeView={activeView}
            navigate={navigateView}
            marketDataState={marketDataState}
            setMarketDataState={setMarketDataState}
            selectedEvidence={selectedEvidence}
            setSelectedEvidence={setSelectedEvidence}
            onResearch={handleResearch}
            onOpenRun={openRun}
            backtestTarget={backtestTarget}
            onOpenBacktest={openBacktest}
          />
        </Suspense>
      </section>

      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={17} /><span>{toast}</span>
          <button onClick={() => setToast('')} aria-label="关闭提示"><X size={14} /></button>
        </div>
      )}
      {submitting && <div className="global-run-indicator"><span /><strong>Agent OS 正在后台执行研究任务</strong><small>可通过实时事件流查看分析进度</small></div>}
    </div>
  )
}

export default App
