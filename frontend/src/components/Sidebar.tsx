import {
  Activity,
  Archive,
  BarChart3,
  BrainCircuit,
  CandlestickChart,
  ChevronsUpDown,
  CircleDot,
  Command,
  LayoutDashboard,
  PanelLeftClose,
  Plus,
  Search,
  Sparkles,
  WalletCards,
  type LucideIcon,
} from 'lucide-react'
import { useCognitrade } from '../CognitradeContext'
import { type ViewId } from '../data'
import { isActiveStatus, runListItem } from '../live-data'

type SidebarProps = {
  activeView: ViewId
  onNavigate: (view: ViewId) => void
  onNewResearch: () => void
  onOpenRun: (runId: string) => void
}

const navigation: Array<{ id: ViewId; label: string; icon: LucideIcon }> = [
  { id: 'workbench', label: '研究工作台', icon: Sparkles },
  { id: 'overview', label: '研究总览', icon: LayoutDashboard },
  { id: 'market', label: '行情数据', icon: CandlestickChart },
  { id: 'paper', label: '模拟交易', icon: WalletCards },
  { id: 'screening', label: '智能筛选', icon: BarChart3 },
  { id: 'methodology', label: '方法论', icon: BrainCircuit },
  { id: 'runs', label: '运行记录', icon: Activity },
]

export function Sidebar({ activeView, onNavigate, onNewResearch, onOpenRun }: SidebarProps) {
  const { runs, selectedRunId, connectionState, systemStatus } = useCognitrade()
  const recentRuns = runs.slice(0, 4).map(runListItem)
  const providerName = typeof systemStatus?.provider_name === 'string' ? systemStatus.provider_name : 'provider'
  const totalRuns = typeof systemStatus?.total_runs === 'number' ? systemStatus.total_runs : runs.length

  return (
    <aside className="sidebar">
      <div className="brand-row">
        <button className="brand" onClick={() => onNavigate('workbench')} aria-label="返回研究工作台">
          <span className="brand-mark" aria-hidden="true">
            <span>C</span>
            <i className="brand-node brand-node--one" />
            <i className="brand-node brand-node--two" />
            <i className="brand-node brand-node--three" />
          </span>
          <span className="brand-copy"><strong>COGNITRADE</strong><small>RESEARCH OS</small></span>
        </button>
        <button className="icon-button sidebar-collapse" aria-label="收起侧边栏" disabled><PanelLeftClose size={17} /></button>
      </div>

      <button className="new-research-button" onClick={onNewResearch}>
        <Plus size={17} strokeWidth={2.2} /><span>新建研究</span><kbd>⌘ K</kbd>
      </button>

      <nav className="primary-nav" aria-label="主导航">
        <p className="nav-section-label">工作区</p>
        {navigation.map(({ id, label, icon: Icon }) => (
          <button key={id} className={`nav-item ${activeView === id ? 'is-active' : ''}`} onClick={() => onNavigate(id)} aria-current={activeView === id ? 'page' : undefined}>
            <Icon size={17} /><span>{label}</span>
          </button>
        ))}
      </nav>

      <section className="thread-section" aria-labelledby="recent-runs-title">
        <div className="thread-section-heading">
          <p id="recent-runs-title" className="nav-section-label">最近研究</p>
          <button className="small-icon-button" aria-label="查看运行记录" onClick={() => onNavigate('runs')}><Search size={14} /></button>
        </div>
        <div className="thread-list">
          {recentRuns.map((run) => (
            <button key={run.id} className={`thread-item ${activeView === 'workbench' && selectedRunId === run.id ? 'is-current' : ''}`} onClick={() => onOpenRun(run.id)}>
              <span className="thread-title-row">
                <span className={`thread-status thread-status--${isActiveStatus(run.status) ? 'running' : run.status === 'completed' ? 'completed' : 'needs_review'}`} aria-label={run.statusLabel} />
                <strong>{run.title}</strong><time>{run.time}</time>
              </span>
              <span className="thread-subtitle">{run.intentLabel} · {run.id}</span>
            </button>
          ))}
          {!recentRuns.length && <div className="sidebar-empty-state">连接 API 后显示真实运行记录</div>}
        </div>
        <button className="archive-link" onClick={() => onNavigate('runs')}><Archive size={14} />查看全部记录</button>
      </section>

      <div className="sidebar-footer">
        <div className="system-state">
          <span className={`online-dot ${connectionState !== 'online' ? 'is-offline' : ''}`} />
          <span>
            <strong>{connectionState === 'online' ? '研究运行时已连接' : '研究运行时未连接'}</strong>
            <small>{systemStatus ? `${providerName} · ${totalRuns} runs` : '等待系统状态'}</small>
          </span>
          <ChevronsUpDown size={14} />
        </div>
        <div className="profile-row">
          <span className="avatar">MY</span>
          <span><strong>研究员工作区</strong><small>本地 · 实时 API</small></span>
          <Command size={14} />
        </div>
      </div>
      <div className="mobile-brand" aria-hidden="true"><CircleDot size={18} /></div>
    </aside>
  )
}
