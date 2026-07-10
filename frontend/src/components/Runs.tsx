import {
  Activity,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  CircleDot,
  Clock3,
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
import { runHistory } from '../data'

type RunFilter = '全部' | '已完成' | '运行中' | '待复核'

export function Runs() {
  const [filter, setFilter] = useState<RunFilter>('全部')
  const [query, setQuery] = useState('')

  const rows = useMemo(
    () =>
      runHistory.filter(
        (run) =>
          (filter === '全部' || run.status === filter) &&
          (!query.trim() || `${run.title}${run.id}${run.intent}`.includes(query.trim())),
      ),
    [filter, query],
  )

  return (
    <main className="page-content runs-page">
      <header className="page-heading">
        <div>
          <span className="page-kicker">RUNTIME OBSERVABILITY</span>
          <h1>研究运行记录</h1>
          <p>回看每次研究的阶段、证据、降级原因与最终产物。</p>
        </div>
        <div className="page-heading-actions">
          <button className="secondary-button">
            <TerminalSquare size={15} />
            运行时状态
          </button>
          <button className="secondary-button">
            <Download size={15} />
            导出日志
          </button>
        </div>
      </header>

      <section className="active-run-card">
        <div className="active-run-header">
          <div className="active-run-icon">
            <Activity size={19} />
          </div>
          <div>
            <span className="live-label"><i /> LIVE RUN</span>
            <h2>人形机器人产业链梳理</h2>
            <p>sector_research · run_c913e4 · 启动于 10:18:06</p>
          </div>
          <div className="active-run-progress">
            <span><strong>68%</strong> · 26.8 秒</span>
            <div className="progress-track progress-track--large"><i style={{ width: '68%' }} /></div>
          </div>
          <button className="stop-run-button"><Pause size={14} /> 停止</button>
        </div>
        <div className="run-dag">
          {[
            ['Router', '完成', '0.6s'],
            ['Planner', '完成', '2.4s'],
            ['Collector', '完成', '12.8s'],
            ['Retriever', '运行中', '10.2s'],
            ['Reviewer', '等待', '—'],
            ['Reporter', '等待', '—'],
          ].map(([name, status, time], index) => (
            <div className="dag-fragment" key={name}>
              <div className={`dag-node ${status === '完成' ? 'is-complete' : status === '运行中' ? 'is-running' : ''}`}>
                <span>{status === '完成' ? <CheckCircle2 size={13} /> : status === '运行中' ? <Activity size={13} /> : <CircleDot size={12} />}</span>
                <strong>{name}</strong>
                <small>{time}</small>
              </div>
              {index < 5 && <ArrowRight size={14} className="dag-arrow" />}
            </div>
          ))}
        </div>
        <div className="active-run-events">
          <span><Clock3 size={13} /> 10:18:29</span>
          <strong>Retriever</strong>
          <p>已召回 18 条高相关证据，正在按来源独立性与发布时间重排。</p>
          <button>查看实时事件 <ArrowRight size={13} /></button>
        </div>
      </section>

      <section className="runs-stat-grid">
        <article><span>本周运行</span><strong>24</strong><small>成功 22 · 待复核 2</small></article>
        <article><span>成功率</span><strong>96.8%</strong><small className="signal-positive">近 30 日 +1.4 pct</small></article>
        <article><span>平均耗时</span><strong>52.6s</strong><small>P95 1m 34s</small></article>
        <article><span>平均证据数</span><strong>28.4</strong><small>每次研究运行</small></article>
      </section>

      <section className="dashboard-panel run-history-panel">
        <div className="run-history-toolbar">
          <div className="segmented-control">
            {(['全部', '已完成', '运行中', '待复核'] as RunFilter[]).map((item) => (
              <button key={item} className={filter === item ? 'is-active' : ''} onClick={() => setFilter(item)}>
                {item}
              </button>
            ))}
          </div>
          <label className="table-search">
            <Search size={14} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 run ID 或研究标题" />
          </label>
          <button className="secondary-button table-filter-button"><Filter size={14} /> 筛选 <ChevronDown size={13} /></button>
        </div>
        <div className="candidate-table-wrap">
          <table className="candidate-table run-history-table">
            <thead>
              <tr>
                <th>研究任务</th>
                <th>意图</th>
                <th>状态</th>
                <th>耗时</th>
                <th>证据</th>
                <th>时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => (
                <tr key={run.id}>
                  <td>
                    <strong>{run.title}</strong>
                    <small>{run.id}</small>
                  </td>
                  <td><span className="intent-table-badge">{run.intent}</span></td>
                  <td>
                    <span className={`status-table-badge status-table-badge--${run.status === '已完成' ? 'complete' : run.status === '运行中' ? 'running' : 'review'}`}>
                      {run.status === '已完成' ? <CheckCircle2 size={12} /> : run.status === '运行中' ? <Activity size={12} /> : <CircleAlert size={12} />}
                      {run.status}
                    </span>
                  </td>
                  <td>{run.duration}</td>
                  <td>{run.evidence} 条</td>
                  <td>{run.time}</td>
                  <td>
                    <div className="table-row-actions">
                      <button aria-label={`重放${run.title}`}><Play size={13} /></button>
                      <button aria-label={`从${run.title}派生研究`}><GitBranch size={13} /></button>
                      <button aria-label={`重新运行${run.title}`}><RotateCcw size={13} /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && <div className="empty-table-state">没有匹配的研究运行。</div>}
        <footer className="table-footer">
          <span><Waypoints size={13} /> 普通视图仅展示阶段摘要；原始 Agent trace 默认折叠。</span>
          <span>共 {rows.length} 条</span>
        </footer>
      </section>
    </main>
  )
}
