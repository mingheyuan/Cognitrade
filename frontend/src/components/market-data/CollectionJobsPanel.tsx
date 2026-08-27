import { Ban, CalendarRange, ChevronDown, Play, RefreshCw, RotateCcw } from 'lucide-react'
import { useState, type FormEvent, type ReactNode } from 'react'
import { collectionJobDurations, formatDateTime, isActiveCollectionJob, jobStatusMeta } from './status'
import { EmptyState, LoadingState, StatusBadge } from './shared'
import { CollectionJobProgress } from './CollectionJobProgress'
import { FundCollectionOverview, FundCollectionProgress } from './FundCollectionOverview'
import type { CollectionJobInput, MarketDataContract, MarketDataUrlState, PageResult, CollectionJob } from './types'

function JobDetails({ className, initiallyOpen, onOpen, children }: { className: string; initiallyOpen: boolean; onOpen?: () => void; children: ReactNode }) {
  const [open, setOpen] = useState(initiallyOpen)
  return <details className={className} open={open} onToggle={(event) => {
    const nextOpen = event.currentTarget.open
    setOpen(nextOpen)
    if (nextOpen) onOpen?.()
  }}>{children}</details>
}

export function CollectionJobsPanel({ contract, urlState, jobs, jobDetails, loading, actionLoading, onCreate, onRefresh, onLoadDetail, onRetry, onCancel }: {
  contract: MarketDataContract
  urlState: MarketDataUrlState
  jobs: PageResult<CollectionJob>
  jobDetails: Record<string, CollectionJob>
  loading: boolean
  actionLoading: boolean
  onCreate: (payload: CollectionJobInput) => Promise<unknown>
  onRefresh: () => Promise<unknown>
  onLoadDetail: (jobId: string) => Promise<CollectionJob | null>
  onRetry: (jobId: string, scope?: { symbols?: string[]; dimensions?: string[] }) => Promise<unknown>
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
  const hasActiveJobs = jobs.items.some((job) => isActiveCollectionJob(job.status))
  const parentIds = new Set(jobs.items.filter((job) => !job.parent_job_id).map((job) => job.job_id))
  const topLevelJobs = jobs.items.filter((job) => !job.parent_job_id || !parentIds.has(job.parent_job_id))
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
        <label className="mdc-check-row"><input type="checkbox" checked={publish} onChange={(event) => setPublish(event.target.checked)} /><span>22 个采集槽均已落库后自动发布不可变 Bundle（发布过程不联网）</span></label>
        <button className="primary-button" type="submit" disabled={actionLoading || !symbols.trim()}><Play size={15} />{actionLoading ? '正在提交…' : '创建显式采集任务'}</button>
      </form>

      <section className="mdc-panel mdc-job-history" aria-labelledby="mdc-jobs-title">
        <header className="mdc-panel-header">
          <div>
            <span className="mdc-eyebrow">CHECKPOINT &amp; RETRY</span>
            <h2 id="mdc-jobs-title">任务与槽位进度</h2>
            <p>{hasActiveJobs ? '运行中任务每 3 秒静默刷新；切换到后台后暂停。' : '没有运行中任务；可手动刷新最新状态。'}</p>
          </div>
          <button className="secondary-button" type="button" onClick={() => void onRefresh()} disabled={loading}><RefreshCw className={loading ? 'is-spinning' : ''} size={14} />刷新</button>
        </header>
        {loading && !jobs.items.length ? <LoadingState label="正在读取持久化任务…" /> : null}
        {!loading && !jobs.items.length ? <EmptyState title="还没有采集任务" detail="数据采集默认关闭；请在左侧显式创建。" /> : null}
        <div className="mdc-job-list" aria-live="polite">
          {topLevelJobs.map((job, index) => {
            const detailedJob = jobDetails[job.job_id] || job
            const status = jobStatusMeta(job.status)
            const active = isActiveCollectionJob(job.status)
            const childJobs = jobs.items.filter((candidate) => candidate.parent_job_id === job.job_id)
            const retryableTaskCount = job.asset_family === 'fund'
              ? job.portfolio_summary?.retryable_slots || 0
              : job.task_summary?.retryable_failed ?? (detailedJob.tasks || []).filter((task) => (
                task.retryable
                && ['failed', 'partial'].includes(task.status)
                && task.attempt < (task.max_attempts ?? Number.POSITIVE_INFINITY)
              )).length
            const blockedTaskCount = job.asset_family === 'fund'
              ? job.portfolio_summary?.blocked_slots || 0
              : job.task_summary?.blocked_failed || 0
            const degradedCount = detailedJob.resolution_summary?.degraded
              ?? job.resolution_summary?.degraded
              ?? 0
            const durations = collectionJobDurations(job)
            return (
              <JobDetails
                key={job.job_id}
                className={`mdc-job-card ${job.asset_family === 'fund' ? 'is-fund' : ''}`}
                initiallyOpen={job.asset_family === 'fund' && index === 0 && ['partial', 'failed'].includes(job.status)}
                onOpen={job.asset_family === 'equity' ? () => { void onLoadDetail(job.job_id) } : undefined}
              >
                <summary>
                  <span><StatusBadge label={status.label} tone={status.tone} />{retryableTaskCount > 0 ? <span className="mdc-retry-available"><RotateCcw size={11} />可重试 {retryableTaskCount}</span> : null}{blockedTaskCount > 0 ? <span className="mdc-blocked-count">阻断 {blockedTaskCount}</span> : null}{degradedCount > 0 ? <span className="mdc-blocked-count">降级披露 {degradedCount}</span> : null}<code>{job.job_id}</code></span>
                  <span><strong>{job.asset_family === 'fund' ? `基金 ${job.symbols.join('、')} · 前十大持仓` : job.symbols.join('、')}</strong><small>{job.start_date} — {job.end_date} · 并发 {job.max_concurrency}</small></span>
                  {job.asset_family === 'fund' ? <FundCollectionProgress job={job} childJobs={childJobs} /> : <CollectionJobProgress progress={job.progress} />}
                  <ChevronDown size={16} />
                </summary>
                <div className="mdc-job-card-body">
                  <div className="mdc-job-meta"><span>模式 {job.mode}</span><span>创建 {formatDateTime(job.created_at)}</span><span>排队 {durations.queue}</span><span>运行 {durations.run}</span><span>自动发布 {job.publish_when_complete ? '开启' : '关闭'}</span></div>
                  {job.asset_family === 'fund' ? (
                    <FundCollectionOverview
                      job={job}
                      childJobs={childJobs}
                      jobDetails={jobDetails}
                      actionLoading={actionLoading}
                      onLoadDetail={onLoadDetail}
                      onRetry={onRetry}
                    />
                  ) : null}
                  {job.asset_family !== 'fund' && detailedJob.error ? <p className="mdc-job-error">{detailedJob.error}</p> : null}
                  {job.asset_family !== 'fund' && !jobDetails[job.job_id] ? <LoadingState label="展开后读取槽位明细…" /> : null}
                  {job.asset_family !== 'fund' && !!detailedJob.tasks?.length && (
                    <div className="mdc-table-scroll"><table className="mdc-table"><thead><tr><th>证券</th><th>槽位</th><th>状态</th><th>Attempt</th><th>说明</th></tr></thead><tbody>{detailedJob.tasks.map((task) => <tr key={task.task_id}><td>{task.symbol}</td><td><code>{task.dimension_id}</code></td><td>{task.resolution_status === 'audit_only' ? 'UZI 可用' : task.resolution_status === 'degraded' ? '降级披露' : jobStatusMeta(task.status).label}</td><td>{task.attempt}{task.max_attempts ? ` / ${task.max_attempts}` : ''}</td><td>{task.resolution_status === 'audit_only' ? '扩展契约仅作审计，不再阻断' : task.resolution_status === 'degraded' ? '历史不可恢复，已排除评分' : task.error_message || '—'}</td></tr>)}</tbody></table></div>
                  )}
                  <div className="mdc-panel-actions">
                    {job.asset_family !== 'fund' && retryableTaskCount > 0 && <button className="secondary-button" type="button" disabled={actionLoading} onClick={() => void onRetry(job.job_id).catch(() => undefined)}><RotateCcw size={14} />重试 {retryableTaskCount} 个可重试槽</button>}
                    {active && <button className="mdc-danger-button" type="button" disabled={actionLoading} onClick={() => void onCancel(job.job_id).catch(() => undefined)}><Ban size={14} />取消任务</button>}
                  </div>
                </div>
              </JobDetails>
            )
          })}
        </div>
      </section>
    </div>
  )
}
