import {
  Activity,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Download,
  Filter,
  GitBranch,
  Pause,
  Play,
  RotateCcw,
  Search,
  TerminalSquare,
  Waypoints,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import { asString, isActiveStatus, runListItem, statusTone } from '../live-data'

type RunsProps = { onOpenRun: (runId: string) => void }
type RunFilter = '全部' | '已完成' | '运行中' | '异常'

export function Runs({ onOpenRun }: RunsProps) {
  const { runs, selectedRun, refresh, cancelRun, rerunRun, submitting } = useCognitrade()
  const [filter, setFilter] = useState<RunFilter>('全部')
  const [query, setQuery] = useState('')
  const items = useMemo(() => runs.map(runListItem), [runs])
  const rows = useMemo(
    () => items.filter((run) => {
      const matchesFilter = filter === '全部'
        || (filter === '已完成' && run.status === 'completed')
        || (filter === '运行中' && isActiveStatus(run.status))
        || (filter === '异常' && ['failed', 'timed_out', 'cancelled', 'needs_clarification'].includes(run.status))
      return matchesFilter && (!query.trim() || `${run.title}${run.id}${run.intent}`.toLowerCase().includes(query.trim().toLowerCase()))
    }),
    [filter, items, query],
  )
  const activeRun = items.find((run) => isActiveStatus(run.status))
  const completed = items.filter((run) => run.status === 'completed').length
  const failed = items.filter((run) => ['failed', 'timed_out'].includes(run.status)).length

  const exportRuns = () => {
    const blob = new Blob([JSON.stringify(runs, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = 'cognitrade-runs.json'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const rerun = async (runId: string) => {
    const suggested = selectedRun?.runId === runId
      ? asString(selectedRun.steps.steps.find((step) => step.node_id)?.node_id)
      : ''
    const fromNode = window.prompt('从哪个顶层 node 重新运行？', suggested)?.trim()
    if (!fromNode) return
    try {
      const response = await rerunRun(runId, fromNode)
      if (response.run_id) onOpenRun(response.run_id)
    } catch {
      // Shared error banner contains the API response.
    }
  }

  return (
    <main className="page-content runs-page">
      <header className="page-heading">
        <div><span className="page-kicker">RUNTIME OBSERVABILITY · LIVE API</span><h1>研究运行记录</h1><p>运行列表、回放、取消与重跑均调用 `/api/v1`，不再使用本地演示数组。</p></div>
        <div className="page-heading-actions"><button className="secondary-button" onClick={() => void refresh()}><TerminalSquare size={15} />刷新状态</button><button className="secondary-button" onClick={exportRuns} disabled={!runs.length}><Download size={15} />导出列表</button></div>
      </header>

      {activeRun ? (
        <section className="active-run-card">
          <div className="active-run-header">
            <div className="active-run-icon"><Activity size={19} /></div>
            <div><span className="live-label"><i /> LIVE RUN</span><h2>{activeRun.title}</h2><p>{activeRun.intent} · {activeRun.id}</p></div>
            <div className="active-run-progress"><span><strong>{activeRun.statusLabel}</strong></span><div className="progress-track progress-track--large is-indeterminate"><i style={{ width: '35%' }} /></div></div>
            <button className="stop-run-button" onClick={() => void cancelRun(activeRun.id)}><Pause size={14} />停止</button>
          </div>
          <div className="active-run-events"><span>{activeRun.time}</span><strong>{activeRun.intentLabel}</strong><p>{activeRun.summary}</p><button onClick={() => onOpenRun(activeRun.id)}>查看运行<ArrowRight size={13} /></button></div>
        </section>
      ) : <section className="active-run-card active-run-card--empty"><CheckCircle2 size={20} /><div><h2>当前没有活动运行</h2><p>后端历史中没有 received/running/waiting 状态。</p></div></section>}

      <section className="runs-stat-grid">
        <article><span>已加载运行</span><strong>{items.length}</strong><small>GET /runs?limit=50</small></article>
        <article><span>已完成</span><strong>{completed}</strong><small>{items.length ? `${((completed / items.length) * 100).toFixed(1)}%` : '—'} 完成率</small></article>
        <article><span>运行中</span><strong>{items.filter((run) => isActiveStatus(run.status)).length}</strong><small>自动每 2 秒刷新详情</small></article>
        <article><span>失败 / 超时</span><strong>{failed}</strong><small>保留原始领域状态</small></article>
      </section>

      <section className="dashboard-panel run-history-panel">
        <div className="run-history-toolbar">
          <div className="segmented-control">{(['全部', '已完成', '运行中', '异常'] as RunFilter[]).map((item) => <button key={item} className={filter === item ? 'is-active' : ''} onClick={() => setFilter(item)}>{item}</button>)}</div>
          <label className="table-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 run ID、摘要或意图" /></label>
          <button className="secondary-button table-filter-button" disabled><Filter size={14} />服务端筛选</button>
        </div>
        <div className="candidate-table-wrap">
          <table className="candidate-table run-history-table">
            <thead><tr><th>研究任务</th><th>意图</th><th>状态</th><th>子线程</th><th>时间</th><th /></tr></thead>
            <tbody>
              {rows.map((run) => (
                <tr key={run.id}>
                  <td><strong>{run.title}</strong><small>{run.id}</small></td>
                  <td><span className="intent-table-badge">{run.intent}</span></td>
                  <td><span className={`status-table-badge status-table-badge--${statusTone(run.status)}`}>{run.status === 'completed' ? <CheckCircle2 size={12} /> : isActiveStatus(run.status) ? <Activity size={12} /> : <CircleAlert size={12} />}{run.statusLabel}</span></td>
                  <td>{run.childThreadCount}</td><td>{run.time}</td>
                  <td><div className="table-row-actions"><button aria-label={`打开${run.id}`} onClick={() => onOpenRun(run.id)}><Play size={13} /></button><button aria-label="派生研究（暂不可用）" disabled title="后端 v1 未提供从 run 派生新研究接口"><GitBranch size={13} /></button><button aria-label={`重新运行${run.id}`} onClick={() => void rerun(run.id)} disabled={submitting}><RotateCcw size={13} /></button></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows.length && <div className="empty-table-state">没有匹配的真实研究运行。</div>}
        <footer className="table-footer"><span><Waypoints size={13} />列表接口不提供耗时与证据数，详情页从 metrics 读取。</span><span>共 {rows.length} 条</span></footer>
      </section>
    </main>
  )
}
