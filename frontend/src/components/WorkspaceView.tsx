import { lazy } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import type { ViewId } from '../data'
import type { ResearchIntent } from '../research-contracts'
import type { MarketDataViewState } from './MarketData'
import { RunInspector } from './RunInspector'

const Workbench = lazy(() => import('./Workbench').then((module) => ({ default: module.Workbench })))
const Overview = lazy(() => import('./Overview').then((module) => ({ default: module.Overview })))
const MarketData = lazy(() => import('./MarketData').then((module) => ({ default: module.MarketData })))
const PaperTrading = lazy(() => import('./PaperTrading').then((module) => ({ default: module.PaperTrading })))
const Backtesting = lazy(() => import('./Backtesting').then((module) => ({ default: module.Backtesting })))
const Screening = lazy(() => import('./Screening').then((module) => ({ default: module.Screening })))
const Funds = lazy(() => import('./Funds').then((module) => ({ default: module.Funds })))
const Methodology = lazy(() => import('./Methodology').then((module) => ({ default: module.Methodology })))
const Runs = lazy(() => import('./Runs').then((module) => ({ default: module.Runs })))

type WorkspaceViewProps = {
  activeView: ViewId
  navigate: (view: ViewId) => void
  marketDataState: MarketDataViewState
  setMarketDataState: Dispatch<SetStateAction<MarketDataViewState>>
  selectedEvidence: string
  setSelectedEvidence: Dispatch<SetStateAction<string>>
  onResearch: (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => Promise<void>
  onOpenRun: (runId: string) => void
}

export function WorkspaceView({
  activeView,
  navigate,
  marketDataState,
  setMarketDataState,
  selectedEvidence,
  setSelectedEvidence,
  onResearch,
  onOpenRun,
}: WorkspaceViewProps) {
  if (activeView === 'workbench') {
    return (
      <div className="workspace-grid">
        <Workbench onSubmit={onResearch} onSelectEvidence={setSelectedEvidence} />
        <RunInspector selectedEvidence={selectedEvidence} onSelectEvidence={setSelectedEvidence} />
      </div>
    )
  }
  if (activeView === 'overview') {
    return <Overview onSubmit={onResearch} onOpenWorkbench={() => navigate('workbench')} onOpenRuns={() => navigate('runs')} onOpenRun={onOpenRun} />
  }
  if (activeView === 'market') return <MarketData state={marketDataState} setState={setMarketDataState} />
  if (activeView === 'paper') return <PaperTrading onOpenMarketData={() => navigate('market')} />
  if (activeView === 'backtest') return <Backtesting />
  if (activeView === 'screening') return <Screening onSubmit={onResearch} onOpenRun={onOpenRun} />
  if (activeView === 'funds') return <Funds onOpenRun={onOpenRun} />
  if (activeView === 'methodology') return <Methodology />
  return <Runs onOpenRun={onOpenRun} onResearch={onResearch} />
}
