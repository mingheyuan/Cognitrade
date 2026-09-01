import {
  AlertTriangle,
  ChevronDown,
  CircleDashed,
  RotateCcw,
} from 'lucide-react'
import { Fragment, useEffect, useMemo, useState } from 'react'
import { isActiveCollectionJob, jobStatusMeta } from './status'
import { StatusBadge } from './shared'
import type { CollectionDiagnosticBucket, CollectionJob, CollectionTask, FundPortfolioSummary } from './types'

function dimensionCount(job: CollectionJob) {
  return job.dimensions === 'all' ? '全部' : job.dimensions?.length || 0
}

function activeChildSlots(childJobs: CollectionJob[]) {
  return childJobs
    .filter((child) => isActiveCollectionJob(child.status))
    .reduce((sum, child) => {
      const settled = child.progress.completed + child.progress.failed + child.progress.skipped
      return sum + Math.max(
        child.task_summary?.active || 0,
        child.progress.queued + child.progress.running,
        child.progress.total - settled,
        0,
      )
    }, 0)
}

function taskFailurePresentation(task: CollectionTask) {
  const kind = task.failure_kind || ''
  const diagnostic = task.error_message || ''
  if (kind === 'audit_only') return {
    label: task.failure_label || 'UZI 可用 · 扩展审计提示',
    summary: '该槽已按 UZI 原生口径判为可用；字段、证据或 PIT 扩展检查仅保留为审计信息。',
    tone: 'info' as const,
  }
  if (kind === 'source_timeout') return {
    label: task.failure_label || '数据源超时',
    summary: '上游请求超过时限，当前槽没有形成可验收快照。',
    tone: 'danger' as const,
  }
  if (kind === 'source_unavailable') return {
    label: task.failure_label || '数据源未返回',
    summary: diagnostic.includes('efinance_capital_flow') && diagnostic.includes('ConnectionError')
      ? diagnostic.includes('tushare_capital_flow') && diagnostic.includes('PermissionDenied')
        ? 'eFinance / 东财连接被上游中断；Tushare 账户没有 moneyflow 接口权限。'
        : 'eFinance / 东财连接被上游中断，本次没有形成可用原始记录。'
      : '本次查询没有拿到可用原始记录，可在数据源恢复后重试。',
    tone: 'danger' as const,
  }
  if (kind === 'evidence_gap') return {
    label: task.failure_label || 'UZI 可用 · 证据审计提示',
    summary: '原始数据已保存；证据等级只影响审计标记，不阻断 UZI 报告。',
    tone: 'info' as const,
  }
  if (kind === 'query_proof_gap') return {
    label: task.failure_label || 'UZI 可用 · 查询审计提示',
    summary: '原始数据已保存；查询窗口证明只作为审计信息。',
    tone: 'info' as const,
  }
  if (kind === 'normalization_gap' || task.raw_data_present) return {
    label: task.failure_label || 'UZI 可用 · 扩展审计提示',
    summary: '上游原始数据已保存；扩展字段检查不再阻断 UZI 报告。',
    tone: 'info' as const,
  }
  return null
}

function portfolioSummary(job: CollectionJob, childJobs: CollectionJob[] = []): FundPortfolioSummary {
  const totalHoldings = job.progress.child_total ?? (job.expanded_holdings.length || job.top_n || 10)
  const readyHoldings = job.progress.child_complete ?? job.expanded_holdings.filter((holding) => !holding.missing_dimensions?.length).length
  const holdingMissingSlots = job.expanded_holdings.reduce((sum, holding) => sum + (holding.missing_dimensions?.length || 0), 0)
  const totalSlots = totalHoldings * 22
  const summary = job.portfolio_summary || {
    total_holdings: totalHoldings,
    ready_holdings: readyHoldings,
    dimensions_per_holding: 22,
    total_slots: totalSlots,
    ready_slots: Math.max(0, totalSlots - holdingMissingSlots),
    missing_slots: holdingMissingSlots,
    retryable_slots: 0,
    blocked_slots: holdingMissingSlots,
    active_slots: 0,
    attempt: 0,
    current_child_job_ids: [],
    by_error_code: [],
    by_dimension: [],
  }
  const activeSlots = Math.max(summary.active_slots, activeChildSlots(childJobs))
  if (!activeSlots) return summary
  const missingSlots = Math.max(
    summary.missing_slots,
    activeSlots + summary.retryable_slots + summary.blocked_slots,
  )
  return {
    ...summary,
    ready_holdings: Math.min(summary.ready_holdings, job.progress.child_complete || 0),
    ready_slots: Math.min(summary.ready_slots, Math.max(0, summary.total_slots - missingSlots)),
    missing_slots: missingSlots,
    active_slots: activeSlots,
  }
}

function DiagnosticBuckets({ title, buckets }: { title: string; buckets: CollectionDiagnosticBucket[] }) {
  if (!buckets.length) return null
  return <section className="mdc-fund-diagnostics"><header><strong>{title}</strong><small>只统计当前仍缺失的槽位</small></header><div>{buckets.slice(0, 6).map((bucket) => (
    <article key={bucket.key} title={bucket.sample_message || undefined}>
      <code>{bucket.key}</code>
      <strong>{bucket.count}</strong>
      <small>可重试 {bucket.retryable_count} · 阻断 {bucket.blocked_count}{bucket.active_count ? ` · 运行 ${bucket.active_count}` : ''}</small>
    </article>
  ))}</div></section>
}

export function FundCollectionProgress({ job, childJobs = [] }: { job: CollectionJob; childJobs?: CollectionJob[] }) {
  const portfolio = portfolioSummary(job, childJobs)
  const value = portfolio.total_slots ? Math.round((portfolio.ready_slots / portfolio.total_slots) * 100) : 0
  return (
    <div className="mdc-progress mdc-fund-progress">
      <div className="mdc-progress-label"><span>{portfolio.ready_holdings} / {portfolio.total_holdings} 只股票就绪</span><strong>{value}% 槽完整</strong></div>
      <div className="mdc-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-label="基金十大持仓股票契约槽完整度">
        <span style={{ width: `${value}%` }} />
      </div>
      <div className="mdc-progress-breakdown"><span>持仓 {job.expanded_holdings.length}/10 已落库</span><span>合格 {portfolio.ready_slots}/{portfolio.total_slots} 槽</span><span>缺口 {portfolio.missing_slots} 槽</span></div>
    </div>
  )
}

export function FundCollectionOverview({ job, childJobs, jobDetails, actionLoading, onLoadDetail, onRetry }: {
  job: CollectionJob
  childJobs: CollectionJob[]
  jobDetails: Record<string, CollectionJob>
  actionLoading: boolean
  onLoadDetail: (jobId: string) => Promise<CollectionJob | null>
  onRetry: (jobId: string, scope?: { symbols?: string[]; dimensions?: string[] }) => Promise<unknown>
}) {
  const [expandedSymbol, setExpandedSymbol] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [pendingRetryKey, setPendingRetryKey] = useState('')
  const [retryFeedback, setRetryFeedback] = useState<{ symbol: string; tone: 'success' | 'error'; message: string } | null>(null)
  const holdings = job.expanded_holdings
  const portfolio = portfolioSummary(job, childJobs)
  const total = portfolio.total_holdings
  const ready = portfolio.ready_holdings
  const missingSlots = portfolio.missing_slots
  const collectionReady = total === 10 && ready === total && ['completed', 'succeeded'].includes(job.status)
  const bundleFrozen = job.freeze_status === 'frozen'
  const agentRecoveryActive = ['queued', 'pending', 'running', 'processing', 'cancelling'].includes(
    String(job.agent_recovery?.status || '').toLowerCase(),
  )
  const detailedChildren = useMemo(
    () => childJobs.map((child) => jobDetails[child.job_id] || child),
    [childJobs, jobDetails],
  )
  const activeDetailJobIds = childJobs
    .filter((child) => expandedSymbol && child.symbols.includes(expandedSymbol) && isActiveCollectionJob(child.status))
    .map((child) => child.job_id)
  const activeDetailJobKey = activeDetailJobIds.join('|')
  const relatedDetailStateKey = childJobs
    .filter((child) => expandedSymbol && child.symbols.includes(expandedSymbol))
    .map((child) => `${child.job_id}:${child.status}:${child.progress.running}:${child.progress.queued}:${child.progress.failed}:${child.progress.completed}`)
    .join('|')

  useEffect(() => {
    if (!activeDetailJobKey) return
    const jobIds = activeDetailJobKey.split('|')
    const intervalId = window.setInterval(() => {
      jobIds.forEach((jobId) => { void onLoadDetail(jobId) })
    }, 3000)
    return () => window.clearInterval(intervalId)
  }, [activeDetailJobKey, onLoadDetail])

  useEffect(() => {
    if (!relatedDetailStateKey) return
    const jobIds = relatedDetailStateKey.split('|').map((item) => item.split(':', 1)[0])
    jobIds.forEach((jobId) => { void onLoadDetail(jobId) })
  }, [relatedDetailStateKey, onLoadDetail])

  const tasksForHolding = (symbol: string) => {
    const dimensionOrder = new Map<string, number>()
    const latestByDimension = new Map<string, NonNullable<CollectionJob['tasks']>[number]>()
    detailedChildren
      .filter((child) => child.symbols.includes(symbol))
      .sort((left, right) => left.created_at.localeCompare(right.created_at))
      .forEach((child) => {
        if (Array.isArray(child.dimensions)) {
          child.dimensions.forEach((dimension) => {
            if (!dimensionOrder.has(dimension)) dimensionOrder.set(dimension, dimensionOrder.size)
          })
        }
        child.tasks?.filter((task) => task.symbol === symbol).forEach((task) => {
          latestByDimension.set(task.dimension_id, task)
        })
      })
    return [...latestByDimension.values()].sort((left, right) => (
      (dimensionOrder.get(left.dimension_id) ?? Number.MAX_SAFE_INTEGER)
      - (dimensionOrder.get(right.dimension_id) ?? Number.MAX_SAFE_INTEGER)
      || left.dimension_id.localeCompare(right.dimension_id)
    ))
  }

  const toggleHolding = async (symbol: string) => {
    if (expandedSymbol === symbol) {
      setExpandedSymbol('')
      return
    }
    setExpandedSymbol(symbol)
    const relatedIds = childJobs
      .filter((child) => child.symbols.includes(symbol))
      .map((child) => child.job_id)
    if (!relatedIds.length) return
    setDetailLoading(true)
    try {
      await Promise.all(relatedIds.map((jobId) => onLoadDetail(jobId)))
    } finally {
      setDetailLoading(false)
    }
  }

  const retryHoldingDimensions = async (symbol: string, dimensions: string[]) => {
    const retryKey = `${symbol}:${dimensions.join('|')}`
    setPendingRetryKey(retryKey)
    setRetryFeedback(null)
    try {
      await onRetry(job.job_id, { symbols: [symbol], dimensions })
      const target = dimensions.length === 1 ? dimensions[0] : `${dimensions.length} 个失败槽`
      setRetryFeedback({
        symbol,
        tone: 'success',
        message: `${symbol} · ${target} 已入队；运行状态将在 3 秒内自动刷新。`,
      })
    } catch {
      setRetryFeedback({
        symbol,
        tone: 'error',
        message: `${symbol} 重试提交失败；请查看页面顶部的具体错误。`,
      })
    } finally {
      setPendingRetryKey('')
    }
  }

  return (
    <section className="mdc-fund-workflow" aria-labelledby={`fund-workflow-${job.job_id}`}>
      <header>
        <div>
          <span className="mdc-eyebrow">FUND → EQUITY PORTFOLIO</span>
          <h3 id={`fund-workflow-${job.job_id}`}>前十大持仓采集链路</h3>
          <p>基金持仓与十只股票快照先落库为草稿；报告只消费用户确认冻结后的组合 Bundle。</p>
        </div>
        <StatusBadge label={bundleFrozen ? '组合 Bundle 已冻结' : collectionReady ? '待确认冻结' : `${ready}/${total} 只就绪`} tone={bundleFrozen ? 'good' : collectionReady ? 'info' : 'warning'} />
      </header>

      {holdings.length ? (
        <div className="mdc-table-scroll mdc-fund-holdings-table">
          <table className="mdc-table">
            <thead><tr><th>排名</th><th>持仓股票</th><th>基金权重</th><th>披露权重</th><th>数据完整度</th></tr></thead>
            <tbody>{holdings.map((holding) => {
              const gaps = holding.missing_dimensions || []
              const collecting = !gaps.length && childJobs.some((child) => (
                isActiveCollectionJob(child.status) && child.symbols.includes(holding.symbol)
              ))
              const expanded = expandedSymbol === holding.symbol
              const tasks = tasksForHolding(holding.symbol)
              const retryableTasks = tasks.filter((task) => (
                task.retryable
                && ['failed', 'partial'].includes(task.status)
                && task.attempt < (task.max_attempts ?? Number.POSITIVE_INFINITY)
              ))
              const activeTasks = tasks.filter((task) => isActiveCollectionJob(task.status))
              return (
                <Fragment key={`${job.job_id}-${holding.symbol}`}>
                  <tr>
                    <td>{holding.rank}</td>
                    <td><div className="mdc-holding-cell-stack"><strong>{holding.name || holding.symbol}</strong><small>{holding.symbol}</small></div></td>
                    <td>{holding.fund_weight_pct == null ? '—' : `${holding.fund_weight_pct.toFixed(2)}%`}</td>
                    <td>{holding.disclosed_weight_pct == null ? '—' : `${holding.disclosed_weight_pct.toFixed(2)}%`}</td>
                    <td>
                      <div className="mdc-holding-cell-stack">
                        <span className={`mdc-holding-readiness ${gaps.length || collecting ? 'is-gap' : 'is-ready'}`}>{gaps.length ? `缺 ${gaps.length} 槽` : collecting ? '采集中' : '已就绪'}</span>
                        {gaps.length ? <small title={gaps.join('、')}>{gaps.slice(0, 2).join(' · ')}{gaps.length > 2 ? ` +${gaps.length - 2}` : ''}</small> : collecting ? <small>等待维度结算</small> : null}
                        <button className="mdc-holding-detail-toggle" type="button" aria-expanded={expanded} onClick={() => void toggleHolding(holding.symbol)}>
                          {expanded ? '收起明细' : '查看 22 维'}<ChevronDown className={expanded ? 'is-open' : undefined} size={12} />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expanded ? <tr className="mdc-holding-detail-row">
                    <td colSpan={5}>
                      <section className="mdc-holding-detail" aria-label={`${holding.name || holding.symbol} 维度采集明细`}>
                        <header>
                          <div><strong>{holding.name || holding.symbol} · 逐维采集状态</strong><small>{tasks.length}/22 个维度已载入；运行中每 3 秒刷新</small></div>
                          {job.scoped_retry_supported && !bundleFrozen && !agentRecoveryActive && retryableTasks.length > 0 && !activeTasks.length ? <button className="secondary-button" type="button" disabled={actionLoading || Boolean(pendingRetryKey)} onClick={() => void retryHoldingDimensions(
                            holding.symbol,
                            retryableTasks.map((task) => task.dimension_id),
                          )}><RotateCcw size={13} />{pendingRetryKey.startsWith(`${holding.symbol}:`) ? '正在提交…' : `重试该股票 ${retryableTasks.length} 个失败槽`}</button> : null}
                        </header>
                        {retryFeedback?.symbol === holding.symbol ? <p className={`mdc-holding-retry-feedback is-${retryFeedback.tone}`} role={retryFeedback.tone === 'error' ? 'alert' : 'status'}>{retryFeedback.message}</p> : null}
                        {detailLoading && !tasks.length ? <p className="mdc-holding-detail-loading">正在读取股票维度明细…</p> : tasks.length ? (
                          <div className="mdc-holding-task-grid">{tasks.map((task) => {
                            const status = jobStatusMeta(task.status)
                            const failure = taskFailurePresentation(task)
                            const taskRetryable = Boolean(
                              task.retryable
                              && ['failed', 'partial'].includes(task.status)
                              && task.attempt < (task.max_attempts ?? Number.POSITIVE_INFINITY)
                            )
                            return <article key={`${holding.symbol}-${task.dimension_id}`}>
                              <div><code>{task.dimension_id}</code><StatusBadge label={task.resolution_status === 'degraded' ? '降级披露' : failure?.label || status.label} tone={task.resolution_status === 'degraded' ? 'warning' : failure?.tone || status.tone} /></div>
                              <p>{task.resolution_status === 'degraded' ? '历史不可恢复，已排除评分' : failure?.summary || task.error_message || (task.snapshot_id ? `快照 ${task.snapshot_id}` : '等待采集结果')}</p>
                              {failure && task.error_message ? <small className="mdc-holding-task-diagnostic" title={task.error_message}>{task.error_message}</small> : null}
                              <footer>
                                <small>Attempt {task.attempt}{task.max_attempts ? ` / ${task.max_attempts}` : ''}{taskRetryable ? ' · 可重试' : ''}</small>
                                {job.scoped_retry_supported && !bundleFrozen && !agentRecoveryActive && taskRetryable ? <button type="button" disabled={actionLoading || Boolean(pendingRetryKey)} aria-label={`重试 ${holding.symbol} ${task.dimension_id}`} onClick={() => void retryHoldingDimensions(holding.symbol, [task.dimension_id])}><RotateCcw size={10} />{pendingRetryKey === `${holding.symbol}:${task.dimension_id}` ? '正在提交…' : '重试此维度'}</button> : null}
                              </footer>
                            </article>
                          })}</div>
                        ) : <p className="mdc-holding-detail-loading">该股票尚未创建维度任务。</p>}
                      </section>
                    </td>
                  </tr> : null}
                </Fragment>
              )
            })}</tbody>
          </table>
        </div>
      ) : <p className="mdc-fund-waiting">持仓披露槽完成后，这里会展示已落库的十只股票及逐票缺口。</p>}

      <section className="mdc-fund-child-jobs" aria-label="股票采集子任务">
        <header><div><strong>股票采集子任务</strong><small>按缺失维度分批执行，失败恢复不会重采基金持仓</small></div><span>{childJobs.length || job.child_collection_job_ids.length} 个批次</span></header>
        {childJobs.length ? childJobs.map((child) => {
          const status = jobStatusMeta(child.status)
          const settled = child.progress.completed + child.progress.failed + child.progress.skipped
          return (
            <article key={child.job_id}>
              <StatusBadge label={status.label} tone={status.tone} />
              <div><strong>{child.symbols.length} 只股票 · {dimensionCount(child)} 个维度</strong><code>{child.job_id}</code></div>
              <div><span>成功 {child.progress.completed}</span><span>失败 {child.progress.failed}</span><small>{settled}/{child.progress.total} 已结算</small></div>
            </article>
          )
        }) : job.child_collection_job_ids.map((jobId) => <code className="mdc-child-job-code" key={jobId}>{jobId}</code>)}
        {!childJobs.length && !job.child_collection_job_ids.length ? <p>父持仓披露完成后才会创建股票子任务。</p> : null}
      </section>

      {missingSlots ? <div className="mdc-fund-diagnostic-grid">
        <DiagnosticBuckets title="按失败原因" buckets={portfolio.by_error_code} />
        <DiagnosticBuckets title="按数据维度" buckets={portfolio.by_dimension} />
      </div> : null}

      {!collectionReady ? (
        <div className={`mdc-fund-recovery ${portfolio.retryable_slots ? 'is-retryable' : portfolio.active_slots || !portfolio.blocked_slots ? 'is-active' : 'is-blocked'}`} role={portfolio.blocked_slots && !portfolio.retryable_slots && !portfolio.active_slots ? 'alert' : 'status'}>
          {portfolio.active_slots || !portfolio.blocked_slots ? <CircleDashed size={18} aria-hidden="true" /> : <AlertTriangle size={18} aria-hidden="true" />}
          <div>
            <strong>{portfolio.active_slots
              ? `${portfolio.active_slots} 个槽正在处理`
              : portfolio.retryable_slots
                ? `${portfolio.retryable_slots} 个槽可恢复，${portfolio.blocked_slots} 个槽需修复`
                : portfolio.blocked_slots
                  ? '当前缺口不能靠重复采集解决'
                  : '等待股票子任务启动或同步状态'}</strong>
            <p>{portfolio.active_slots
              ? '运行完成后会按 UZI 原生口径重新计算；已落库的基金持仓与可用快照不会重采。'
              : portfolio.retryable_slots
                ? `仅重新查询可重试的数据源缺口；已落库持仓和 ${portfolio.ready_slots} 个合格槽继续复用。当前恢复轮次 ${Math.max(1, portfolio.attempt)}。`
                : portfolio.blocked_slots
                  ? '阻断槽仅表示 UZI 原生关键数据缺失或采集失败；扩展证据与字段审计不会阻断。'
                  : '父任务已创建，正在等待股票采集批次写入最新进度。'}</p>
          </div>
          {portfolio.retryable_slots && !portfolio.active_slots && !bundleFrozen && !agentRecoveryActive ? <button className="primary-button" type="button" disabled={actionLoading} onClick={() => void onRetry(job.job_id).catch(() => undefined)}><RotateCcw size={15} />{actionLoading ? '正在提交…' : `重试 ${portfolio.retryable_slots} 个可重试槽`}</button> : null}
        </div>
      ) : null}
    </section>
  )
}
