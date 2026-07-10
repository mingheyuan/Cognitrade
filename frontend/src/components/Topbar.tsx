import {
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
import { type FormEvent, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import type { ViewId } from '../data'

const viewTitles: Record<ViewId, { eyebrow: string; title: string }> = {
  workbench: { eyebrow: '研究空间', title: '研究工作台' },
  overview: { eyebrow: '工作区', title: '研究总览' },
  screening: { eyebrow: '研究工具', title: '智能筛选' },
  methodology: { eyebrow: '认知系统', title: '方法论' },
  runs: { eyebrow: '运行时', title: '运行记录' },
}

export function Topbar({ activeView }: { activeView: ViewId }) {
  const { settings, connectionState, configure, refresh } = useCognitrade()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [baseUrl, setBaseUrl] = useState(settings.baseUrl)
  const [token, setToken] = useState(settings.token)
  const view = viewTitles[activeView]

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
          <button className="global-search" aria-label="全局搜索" disabled title="全局搜索 API 尚未实现">
            <Search size={15} />
            <span>搜索股票、板块或研究记录</span>
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
          <button className="icon-button notification-button" aria-label="通知" disabled>
            <Bell size={17} />
          </button>
          <button className="workspace-switcher" disabled>
            <span className="avatar avatar--small">MY</span>
            <ChevronDown size={14} />
          </button>
        </div>
      </header>

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
