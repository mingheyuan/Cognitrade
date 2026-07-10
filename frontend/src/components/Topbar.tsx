import { Bell, ChevronDown, CircleHelp, Database, Search } from 'lucide-react'
import type { ViewId } from '../data'

const viewTitles: Record<ViewId, { eyebrow: string; title: string }> = {
  workbench: { eyebrow: '研究空间', title: '研究工作台' },
  overview: { eyebrow: '工作区', title: '研究总览' },
  screening: { eyebrow: '研究工具', title: '智能筛选' },
  methodology: { eyebrow: '认知系统', title: '方法论' },
  runs: { eyebrow: '运行时', title: '运行记录' },
}

export function Topbar({ activeView }: { activeView: ViewId }) {
  const view = viewTitles[activeView]

  return (
    <header className="topbar">
      <div className="page-identity">
        <span>{view.eyebrow}</span>
        <strong>{view.title}</strong>
      </div>
      <div className="topbar-actions">
        <button className="global-search" aria-label="全局搜索">
          <Search size={15} />
          <span>搜索股票、板块或研究记录</span>
          <kbd>⌘ /</kbd>
        </button>
        <div className="snapshot-badge" title="当前页面使用可替换的演示数据层">
          <Database size={14} />
          <span>演示快照</span>
        </div>
        <button className="icon-button" aria-label="帮助中心">
          <CircleHelp size={17} />
        </button>
        <button className="icon-button notification-button" aria-label="通知，2 条未读">
          <Bell size={17} />
          <span />
        </button>
        <button className="workspace-switcher">
          <span className="avatar avatar--small">MY</span>
          <ChevronDown size={14} />
        </button>
      </div>
    </header>
  )
}
