import {
  Activity,
  Bell,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  RefreshCw,
  Search,
  ServerCog,
  WifiOff,
  X,
} from 'lucide-react'
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import type { ViewId } from '../data'
import { isActiveStatus, runListItem } from '../live-data'

const viewTitles: Record<ViewId, { eyebrow: string; title: string }> = {
  workbench: { eyebrow: '研究空间', title: '研究工作台' },
  overview: { eyebrow: '工作区', title: '研究总览' },
  market: { eyebrow: '数据中心', title: '行情与研究数据' },
  paper: { eyebrow: '交易实验室', title: '模拟交易' },
  screening: { eyebrow: '研究工具', title: '智能筛选' },
  funds: { eyebrow: '资产研究', title: '基金研究与选择' },
  uzi: { eyebrow: '专项方法', title: 'UZI 3.9.2 能力工作区' },
  strategies: { eyebrow: '策略治理', title: '策略、信号与回测' },
  runs: { eyebrow: '运行时', title: '运行记录' },
}

type TopbarProps = {
  activeView: ViewId
  onOpenRun: (runId: string) => void
}

export function Topbar({ activeView, onOpenRun }: TopbarProps) {
  const { settings, connectionState, configure, refresh, runs } = useCognitrade()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [baseUrl, setBaseUrl] = useState(settings.baseUrl)
  const [token, setToken] = useState(settings.token)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const view = viewTitles[activeView]
  const runItems = useMemo(() => runs.map(runListItem), [runs])
  const searchResults = useMemo(() => {
    const normalized = searchQuery.trim().toLowerCase()
    if (!normalized) return runItems.slice(0, 6)
    return runItems.filter((run) => (
      `${run.title} ${run.id} ${run.intent} ${run.summary}`.toLowerCase().includes(normalized)
    )).slice(0, 8)
  }, [runItems, searchQuery])
  const notifications = useMemo(
    () => runItems.filter((run) => (
      isActiveStatus(run.status)
      || ['failed', 'timed_out', 'needs_clarification'].includes(run.status)
    )).slice(0, 6),
    [runItems],
  )

  const openDialog = () => {
    setBaseUrl(settings.baseUrl)
    setToken(settings.token)
    setDialogOpen(true)
  }

  const save = (event: FormEvent) => {
    event.preventDefault()
    configure({ baseUrl, token })
    setDialogOpen(false)
  }

  const openSearch = useCallback(() => {
    setNotificationsOpen(false)
    setWorkspaceOpen(false)
    setSearchOpen(true)
  }, [])

  const openRun = (runId: string) => {
    setSearchOpen(false)
    setNotificationsOpen(false)
    onOpenRun(runId)
  }

  useEffect(() => {
    const handleKeyboard = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === '/') {
        event.preventDefault()
        openSearch()
      }
      if (event.key === 'Escape') {
        setSearchOpen(false)
        setNotificationsOpen(false)
        setWorkspaceOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyboard)
    return () => window.removeEventListener('keydown', handleKeyboard)
  }, [openSearch])

  useEffect(() => {
    if (searchOpen) window.requestAnimationFrame(() => searchInputRef.current?.focus())
  }, [searchOpen])

  const stateLabel = {
    connecting: '连接中',
    online: '实时 API',
    'auth-required': '需要 Token',
    offline: 'API 离线',
  }[connectionState]

  return (
    <>
      <header className="topbar">
        <div className="page-identity">
          <span>{view.eyebrow}</span>
          <strong>{view.title}</strong>
        </div>
        <div className="topbar-actions">
          <button className="global-search" aria-label="搜索已加载运行" onClick={openSearch} title="本地预览：搜索当前已加载的运行记录">
            <Search size={15} />
            <span>搜索已加载研究记录</span>
            <kbd>⌘ /</kbd>
          </button>
          <button
            className={`snapshot-badge connection-badge connection-badge--${connectionState}`}
            title={`${settings.baseUrl} · 点击配置连接`}
            onClick={openDialog}
          >
            {connectionState === 'online' ? <CheckCircle2 size={14} /> : connectionState === 'connecting' ? <RefreshCw size={14} /> : <WifiOff size={14} />}
            <span>{stateLabel}</span>
          </button>
          <button className="icon-button" aria-label="刷新 API 数据" onClick={() => void refresh()}>
            <RefreshCw size={17} />
          </button>
          <button className="icon-button" aria-label="帮助中心" title="接口配置见项目 README">
            <CircleHelp size={17} />
          </button>
          <button
            className="icon-button notification-button"
            aria-label="查看本地运行提醒"
            aria-expanded={notificationsOpen}
            onClick={() => {
              setSearchOpen(false)
              setWorkspaceOpen(false)
              setNotificationsOpen((open) => !open)
            }}
          >
            <Bell size={17} />
            {notifications.length > 0 && <span />}
          </button>
          <button
            className="workspace-switcher"
            aria-label="查看当前工作区"
            aria-expanded={workspaceOpen}
            onClick={() => {
              setSearchOpen(false)
              setNotificationsOpen(false)
              setWorkspaceOpen((open) => !open)
            }}
          >
            <span className="avatar avatar--small">MY</span>
            <ChevronDown size={14} />
          </button>
          {notificationsOpen && (
            <div className="topbar-popover topbar-popover--notifications">
              <header><strong>运行提醒</strong><span>本地预览</span></header>
              {notifications.length ? notifications.map((run) => (
                <button key={run.id} onClick={() => openRun(run.id)}>
                  <Activity size={14} />
                  <span><strong>{run.title}</strong><small>{run.statusLabel} · {run.time}</small></span>
                </button>
              )) : <p>当前已加载记录中没有待处理提醒。</p>}
            </div>
          )}
          {workspaceOpen && (
            <div className="topbar-popover topbar-popover--workspace">
              <header><strong>研究员工作区</strong><span>本地预览</span></header>
              <p>当前版本仅连接一个工作区；这里不会伪造其他租户或远端空间。</p>
              <small>{settings.baseUrl}</small>
            </div>
          )}
        </div>
      </header>

      {searchOpen && (
        <div className="local-search-backdrop" role="presentation" onMouseDown={() => setSearchOpen(false)}>
          <section className="local-search-dialog" role="dialog" aria-modal="true" aria-label="搜索已加载研究记录" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <Search size={17} />
              <input
                ref={searchInputRef}
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="输入标题、Run ID、意图或摘要"
                aria-label="搜索关键词"
              />
              <span>本地预览</span>
              <button className="icon-button" onClick={() => setSearchOpen(false)} aria-label="关闭搜索"><X size={15} /></button>
            </header>
            <div className="local-search-results">
              {searchResults.map((run) => (
                <button key={run.id} onClick={() => openRun(run.id)}>
                  <span className={`thread-status thread-status--${isActiveStatus(run.status) ? 'running' : ['completed', 'completed_partial'].includes(run.status) ? 'completed' : 'needs_review'}`} />
                  <span><strong>{run.title}</strong><small>{run.intentLabel} · {run.id}</small></span>
                  <time>{run.time}</time>
                </button>
              ))}
              {!searchResults.length && <p>当前已加载的 {runItems.length} 条记录中没有匹配项。</p>}
            </div>
            <footer>仅搜索浏览器内已加载的运行记录，不代表服务端全量搜索。</footer>
          </section>
        </div>
      )}

      {dialogOpen && (
        <div className="api-dialog-backdrop" role="presentation" onMouseDown={() => setDialogOpen(false)}>
          <form className="api-dialog" onSubmit={save} onMouseDown={(event) => event.stopPropagation()}>
            <div className="api-dialog-heading">
              <span className="stat-icon stat-icon--mint"><ServerCog size={18} /></span>
              <div>
                <span className="section-kicker">API CONNECTION</span>
                <h2>连接 Cognitrade 后端</h2>
              </div>
              <button type="button" className="icon-button" onClick={() => setDialogOpen(false)} aria-label="关闭">
                <X size={16} />
              </button>
            </div>
            <label>
              <span>API 地址</span>
              <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://127.0.0.1:8000/api/v1" required />
            </label>
            <label>
              <span>Bearer Token</span>
              <input type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="本机无认证模式可留空" autoComplete="off" />
            </label>
            <p>Token 只保存在当前浏览器标签会话的 sessionStorage；不会进入构建产物。使用 `--local-unauthenticated` 启动时可留空。</p>
            <div className="api-dialog-actions">
              <button type="button" className="secondary-button" onClick={() => setDialogOpen(false)}>取消</button>
              <button type="submit" className="primary-button">保存并连接</button>
            </div>
          </form>
        </div>
      )}
    </>
  )
}
