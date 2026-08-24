import { Ban, CalendarRange, ChevronDown, Play, RefreshCw, RotateCcw } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { formatDateTime, jobStatusMeta } from './status'
import { EmptyState, LoadingState, StatusBadge } from './shared'
import { CollectionJobProgress } from './CollectionJobProgress'
import type { CollectionJobInput, MarketDataContract, MarketDataUrlState, PageResult, CollectionJob } from './types'

export function CollectionJobsPanel({ contract, urlState, jobs, loading, actionLoading, onCreate, onRefresh, onRetry, onCancel }: {
  contract: MarketDataContract
  urlState: MarketDataUrlState
  jobs: PageResult<CollectionJob>
  loading: boolean
  actionLoading: boolean
  onCreate: (payload: CollectionJobInput) => Promise<unknown>
  onRefresh: () => Promise<unknown>
  onRetry: (jobId: string) => Promise<unknown>
  onCancel: (jobId: string) => Promise<unknown>
}) {
  const [symbols, setSymbols] = useState(urlState.symbol)
  const [startDate, setStartDate] = useState(urlState.as_of_date)
  const [endDate, setEndDate] = useState(urlState.as_of_date)
  const [mode, setMode] = useState<CollectionJobInput['mode']>('missing_or_stale')
  const [scope, setScope] = useState<'all' | 'current'>('all')
  const [concurrency, setConcurrency] = useState(5)
  const [publish, setPublish] = useState(true)
  const isFund = contract.asset_family === 'fund'
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const parsedSymbols = symbols.split(/[\s,，]+/).map((value) => value.trim()).filter(Boolean)
    if (!parsedSymbols.length) return
    void onCreate({
      symbols: parsedSymbols,
      asset_family: contract.asset_family,
      top_n: 10,
      start_date: startDate,
      end_date: endDate,
      dimensions: scope === 'all' ? 'all' : [urlState.dimension].filter(Boolean),
      mode,
      max_concurrency: concurrency,
      publish_when_complete: publish,
    }).catch(() => undefined)
  }
  return (
    <div className="mdc-jobs-layout">
      <form className="mdc-job-form mdc-panel" onSubmit={submit}>
        <header className="mdc-panel-header">
          <div><span className="mdc-eyebrow">EXPLICIT COLLECTION</span><h2>新建采集任务</h2></div>
          <CalendarRange size={20} />
        </header>
        <p className="mdc-form-note">{isFund ? '基金只作为前十大股票入口：先冻结一个持仓披露槽，再展开 10 只股票的完整采集子任务。' : '只有提交此表单才会调用外部数据源。打开页面、读取覆盖率和生成报告都不会自动采集。'}</p>
        <label>{isFund ? '基金代码（一次一只）' : '股票代码（逗号或空格分隔；不可混入基金）'}<textarea value={symbols} onChange={(event) => setSymbols(event.target.value)} rows={3} required /></label>
        <div className="mdc-form-grid">
          <label>{isFund ? '冻结日期' : '开始日期'}<input type="date" value={startDate} onChange={(event) => { setStartDate(event.target.value); if (isFund) setEndDate(event.target.value) }} required /></label>
          <label>结束日期<input type="date" value={endDate} min={startDate} disabled={isFund} onChange={(event) => setEndDate(event.target.value)} required /></label>
          <label>采集模式<select value={mode} onChange={(event) => setMode(event.target.value as CollectionJobInput['mode'])}><option value="missing_or_stale">仅补缺 / 过期</option><option value="force_all">强制重新查询</option></select></label>
          <label>维度范围<select value={scope} disabled={isFund} onChange={(event) => setScope(event.target.value as 'all' | 'current')}><option value="all">{isFund ? `基金持仓入口 ${contract.expected_dimension_count} 槽` : `契约全部 ${contract.expected_dimension_count} 槽`}</option>{!isFund && <option value="current">当前槽位</option>}</select></label>
          <label>采集并发<input type="number" min={1} max={5} value={concurrency} onChange={(event) => setConcurrency(Math.max(1, Math.min(5, Number(event.target.value) || 1)))} /></label>
          {isFund ? <label>展开股票数<input type="number" value={10} readOnly aria-label="固定展开前十大股票" /></label> : null}
        </div>
        <label className="mdc-check-row"><input type="checkbox" checked={publish} onChange={(event) => setPublish(event.target.checked)} /><span>完整通过后自动发布不可变 Bundle（发布过程不联网）</span></label>
        <button className="primary-button" type="submit" disabled={actionLoading || !symbols.trim()}><Play size={15} />{actionLoading ? '正在提交…' : '创建显式采集任务'}</button>
      </form>

      <section className="mdc-panel mdc-job-history" aria-labelledby="mdc-jobs-title">
        <header className="mdc-panel-header">
          <div><span className="mdc-eyebrow">CHECKPOINT &amp; RETRY</span><h2 id="mdc-jobs-title">任务与槽位进度</h2></div>
          <button className="secondary-button" type="button" onClick={() => void onRefresh()} disabled={loading}><RefreshCw className={loading ? 'is-spinning' : ''} size={14} />刷新</button>
        </header>
        {loading && !jobs.items.length ? <LoadingState label="正在读取持久化任务…" /> : null}
        {!loading && !jobs.items.length ? <EmptyState title="还没有采集任务" detail="数据采集默认关闭；请在左侧显式创建。" /> : null}
        <div className="mdc-job-list" aria-live="polite">
          {jobs.items.map((job) => {
            const status = jobStatusMeta(job.status)
            const active = ['queued', 'running', 'processing'].includes(job.status)
            return (
              <details key={job.job_id} className="mdc-job-card">
                <summary>
                  <span><StatusBadge label={status.label} tone={status.tone} /><code>{job.job_id}</code></span>
                  <span><strong>{job.symbols.join('、')}</strong><small>{job.start_date} — {job.end_date} · 并发 {job.max_concurrency}</small></span>
                  <CollectionJobProgress progress={job.progress} />
                  <ChevronDown size={16} />
                </summary>
                <div className="mdc-job-card-body">
                  <div className="mdc-job-meta"><span>模式 {job.mode}</span><span>创建 {formatDateTime(job.created_at)}</span><span>自动发布 {job.publish_when_complete ? '开启' : '关闭'}</span></div>
                  {job.asset_family === 'fund' ? (
                    <div className="mdc-fund-expansion">
                      <header>
                        <div><span className="mdc-eyebrow">FUND → EQUITY PORTFOLIO</span><h3>冻结前十大股票组合</h3></div>
                        <strong>{job.expanded_holdings.length} / {job.top_n || 10}</strong>
                      </header>
                      {job.expanded_holdings.length ? (
                        <div className="mdc-table-scroll"><table className="mdc-table"><thead><tr><th>排名</th><th>股票</th><th>基金权重</th><th>披露权重</th><th>子任务缺口</th></tr></thead><tbody>{job.expanded_holdings.map((holding) => <tr key={`${job.job_id}-${holding.symbol}`}><td>{holding.rank}</td><td><strong>{holding.name || holding.symbol}</strong><small>{holding.symbol}</small></td><td>{holding.fund_weight_pct == null ? '—' : `${holding.fund_weight_pct.toFixed(2)}%`}</td><td>{holding.disclosed_weight_pct == null ? '—' : `${holding.disclosed_weight_pct.toFixed(2)}%`}</td><td>{holding.missing_dimensions?.length ? `${holding.missing_dimensions.length} 槽待补` : '就绪'}</td></tr>)}</tbody></table></div>
                      ) : <p>持仓披露槽完成后，后端会在此返回严格的前十大股票展开结果。</p>}
                      <div className="mdc-child-job-list"><span>股票采集子任务</span>{job.child_collection_job_ids.length ? job.child_collection_job_ids.map((jobId) => <code key={jobId}>{jobId}</code>) : <small>尚未创建</small>}</div>
                    </div>
                  ) : null}
                  {job.error ? <p className="mdc-job-error">{job.error}</p> : null}
                  {!!job.tasks?.length && (
                    <div className="mdc-table-scroll"><table className="mdc-table"><thead><tr><th>证券</th><th>槽位</th><th>状态</th><th>Attempt</th><th>错误</th></tr></thead><tbody>{job.tasks.map((task) => <tr key={task.task_id}><td>{task.symbol}</td><td><code>{task.dimension_id}</code></td><td>{jobStatusMeta(task.status).label}</td><td>{task.attempt}{task.max_attempts ? ` / ${task.max_attempts}` : ''}</td><td>{task.error_message || '—'}</td></tr>)}</tbody></table></div>
                  )}
                  <div className="mdc-panel-actions">
                    {job.progress.failed > 0 && <button className="secondary-button" type="button" disabled={actionLoading} onClick={() => void onRetry(job.job_id).catch(() => undefined)}><RotateCcw size={14} />仅重试失败槽</button>}
                    {active && <button className="mdc-danger-button" type="button" disabled={actionLoading} onClick={() => void onCancel(job.job_id).catch(() => undefined)}><Ban size={14} />取消任务</button>}
                  </div>
                </div>
              </details>
            )
          })}
        </div>
      </section>
    </div>
  )
}
