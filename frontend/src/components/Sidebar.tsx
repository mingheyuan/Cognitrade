import {
  Activity,
  Archive,
  BarChart3,
  BrainCircuit,
  ChevronsUpDown,
  CircleDot,
  Command,
  LayoutDashboard,
  PanelLeftClose,
  Plus,
  Search,
  Sparkles,
  type LucideIcon,
} from 'lucide-react'
import { threads, type ThreadItem, type ViewId } from '../data'

type SidebarProps = {
  activeView: ViewId
  onNavigate: (view: ViewId) => void
  onNewResearch: () => void
}

const navigation: Array<{ id: ViewId; label: string; icon: LucideIcon }> = [
  { id: 'workbench', label: '研究工作台', icon: Sparkles },
  { id: 'overview', label: '研究总览', icon: LayoutDashboard },
  { id: 'screening', label: '智能筛选', icon: BarChart3 },
  { id: 'methodology', label: '方法论', icon: BrainCircuit },
  { id: 'runs', label: '运行记录', icon: Activity },
]

function ThreadStatus({ status }: { status: ThreadItem['status'] }) {
  return (
    <span
      className={`thread-status thread-status--${status}`}
      aria-label={
        status === 'running' ? '运行中' : status === 'needs_review' ? '待复核' : '已完成'
      }
    />
  )
}

export function Sidebar({ activeView, onNavigate, onNewResearch }: SidebarProps) {
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
          <span className="brand-copy">
            <strong>COGNITRADE</strong>
            <small>RESEARCH OS</small>
          </span>
        </button>
        <button className="icon-button sidebar-collapse" aria-label="收起侧边栏">
          <PanelLeftClose size={17} />
        </button>
      </div>

      <button className="new-research-button" onClick={onNewResearch}>
        <Plus size={17} strokeWidth={2.2} />
        <span>新建研究</span>
        <kbd>⌘ K</kbd>
      </button>

      <nav className="primary-nav" aria-label="主导航">
        <p className="nav-section-label">工作区</p>
        {navigation.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            className={`nav-item ${activeView === id ? 'is-active' : ''}`}
            onClick={() => onNavigate(id)}
            aria-current={activeView === id ? 'page' : undefined}
          >
            <Icon size={17} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <section className="thread-section" aria-labelledby="recent-threads-title">
        <div className="thread-section-heading">
          <p id="recent-threads-title" className="nav-section-label">
            最近研究
          </p>
          <button className="small-icon-button" aria-label="搜索研究记录">
            <Search size={14} />
          </button>
        </div>

        <div className="thread-list">
          {threads.map((thread, index) => (
            <button
              key={thread.id}
              className={`thread-item ${activeView === 'workbench' && index === 0 ? 'is-current' : ''}`}
              onClick={() => onNavigate('workbench')}
            >
              <span className="thread-title-row">
                <ThreadStatus status={thread.status} />
                <strong>{thread.title}</strong>
                <time>{thread.time}</time>
              </span>
              <span className="thread-subtitle">{thread.subtitle}</span>
            </button>
          ))}
        </div>

        <button className="archive-link" onClick={() => onNavigate('runs')}>
          <Archive size={14} />
          查看全部记录
        </button>
      </section>

      <div className="sidebar-footer">
        <div className="system-state">
          <span className="online-dot" />
          <span>
            <strong>研究运行时正常</strong>
            <small>3 / 3 数据层可用</small>
          </span>
          <ChevronsUpDown size={14} />
        </div>
        <div className="profile-row">
          <span className="avatar">MY</span>
          <span>
            <strong>研究员工作区</strong>
            <small>本地 · 演示模式</small>
          </span>
          <Command size={14} />
        </div>
      </div>

      <div className="mobile-brand" aria-hidden="true">
        <CircleDot size={18} />
      </div>
    </aside>
  )
}
