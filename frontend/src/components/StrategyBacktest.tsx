import { ChevronDown, ShieldCheck } from 'lucide-react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type {
  ReportJob,
  StrategyBacktest,
  StrategyBacktestResult,
} from '../strategy-contracts'
import { backtestModeLabel } from './strategy-backtest-utils'

type BacktestChartDatum = {
  date: string
  strategy?: number
  fundBuyHold?: number
  categoryIndex?: number
  hs300?: number
}

type BacktestMetricTone = 'positive' | 'negative' | 'neutral'

export function BacktestResultPanel({ run, onClose }: { run: StrategyBacktest; onClose: () => void }) {
  const result = run.result as StrategyBacktestResult
  const metrics = { ...run.metrics, ...(result.metrics || {}) }
  const chartData = backtestChartData(result, run.initial_cash)
  const benchmarkReturn = finiteNumber(result.benchmarks?.fund_buy_and_hold?.return_pct)
  const strategyReturn = finiteNumber(metrics.total_return_pct) || 0
  const excessReturn = benchmarkReturn === null ? null : strategyReturn - benchmarkReturn
  const trades = result.trades || []

  return (
    <section id="backtest-result-panel" className="backtest-result-panel" aria-labelledby="backtest-result-title">
      <header className="backtest-result-header">
        <div>
          <span className="section-kicker">BACKTEST RESULT</span>
          <h4 id="backtest-result-title">{run.fund_name || run.fund_code} · {backtestModeLabel(run.mode)}</h4>
          <p>{run.start_date} — {run.end_date} · {run.id}</p>
        </div>
        <button className="secondary-button" onClick={onClose}><ChevronDown size={13} />收起</button>
      </header>

      <div className="backtest-result-metrics">
        <BacktestMetric label="策略收益" value={formatSignedPercent(strategyReturn)} tone={numberTone(strategyReturn)} />
        <BacktestMetric
          label="基金买入持有"
          value={benchmarkReturn === null ? '不可用' : formatSignedPercent(benchmarkReturn)}
          tone={numberTone(benchmarkReturn)}
        />
        <BacktestMetric
          label="相对基金超额"
          value={excessReturn === null ? '—' : formatSignedPercent(excessReturn)}
          tone={numberTone(excessReturn)}
        />
        <BacktestMetric
          label="最大回撤"
          value={formatSignedPercent(Number(metrics.max_drawdown_pct || 0))}
          tone={Number(metrics.max_drawdown_pct || 0) < 0 ? 'negative' : 'neutral'}
        />
        <BacktestMetric label="成交 / 换手" value={`${Number(metrics.trade_count || 0)} 笔 · ${Number(metrics.turnover_pct || 0).toFixed(2)}%`} />
        <BacktestMetric label="期末权益" value={formatCurrency(Number(metrics.final_equity || run.initial_cash))} />
      </div>

      <BacktestEquityChart
        chartData={chartData}
        strategyReturn={strategyReturn}
        benchmarkReturn={benchmarkReturn}
      />

      <div className="backtest-result-grid">
        <BacktestAudit run={run} result={result} />
        <BacktestTrades trades={trades} />
      </div>
    </section>
  )
}

function BacktestEquityChart({
  chartData,
  strategyReturn,
  benchmarkReturn,
}: {
  chartData: BacktestChartDatum[]
  strategyReturn: number
  benchmarkReturn: number | null
}) {
  const hasFundCurve = chartData.some((row) => row.fundBuyHold !== undefined)
  const hasCategoryCurve = chartData.some((row) => row.categoryIndex !== undefined)
  const hasHs300Curve = chartData.some((row) => row.hs300 !== undefined)

  return (
    <div className="backtest-chart-card">
      <header>
        <div><strong>累计收益曲线</strong><small>全部序列以回测起点归一为 0%</small></div>
        <span>{chartData.length} 个估值日</span>
      </header>
      {chartData.length ? (
        <div
          className="backtest-chart"
          role="img"
          aria-label={`策略累计收益 ${formatSignedPercent(strategyReturn)}，基金买入持有 ${benchmarkReturn === null ? '不可用' : formatSignedPercent(benchmarkReturn)}`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 12, right: 14, left: 0, bottom: 4 }} accessibilityLayer>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 5" vertical={false} />
              <XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fill: 'var(--muted)', fontSize: 9 }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
              <YAxis tickFormatter={(value) => `${Number(value).toFixed(1)}%`} tick={{ fill: 'var(--muted)', fontSize: 9 }} axisLine={false} tickLine={false} width={46} />
              <Tooltip formatter={(value) => `${Number(value).toFixed(3)}%`} labelFormatter={(value) => `估值日 ${value}`} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 8, color: 'var(--text)', fontSize: 10 }} />
              <Legend wrapperStyle={{ color: 'var(--text-soft)', fontSize: 9, paddingTop: 8 }} />
              <Line type="monotone" dataKey="strategy" name="策略" stroke="var(--mint)" strokeWidth={2.4} dot={{ r: 2.5 }} activeDot={{ r: 4 }} connectNulls isAnimationActive={false} />
              {hasFundCurve && <Line type="monotone" dataKey="fundBuyHold" name="基金买入持有" stroke="var(--gold)" strokeWidth={2} strokeDasharray="6 4" dot={{ r: 2 }} connectNulls isAnimationActive={false} />}
              {hasCategoryCurve && <Line type="monotone" dataKey="categoryIndex" name="类别指数" stroke="var(--blue)" strokeWidth={1.7} dot={false} connectNulls isAnimationActive={false} />}
              {hasHs300Curve && <Line type="monotone" dataKey="hs300" name="沪深300" stroke="var(--rose)" strokeWidth={1.7} dot={false} connectNulls isAnimationActive={false} />}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : <p className="strategy-inline-empty">结果中没有可绘制的权益曲线。</p>}
      <details className="backtest-data-table">
        <summary>查看逐日数据</summary>
        <div>
          <table>
            <thead><tr><th>日期</th><th>策略</th><th>基金基准</th><th>类别指数</th><th>沪深300</th></tr></thead>
            <tbody>{chartData.map((row) => <tr key={row.date}><td>{row.date}</td><td>{formatChartValue(row.strategy)}</td><td>{formatChartValue(row.fundBuyHold)}</td><td>{formatChartValue(row.categoryIndex)}</td><td>{formatChartValue(row.hs300)}</td></tr>)}</tbody>
          </table>
        </div>
      </details>
    </div>
  )
}

function BacktestAudit({ run, result }: { run: StrategyBacktest; result: StrategyBacktestResult }) {
  const benchmarkRows = Object.entries(result.benchmarks || {})
  const adapter = result.execution_adapter
  const openStatusAssumptions = Array.isArray(adapter?.open_status_assumptions)
    ? adapter.open_status_assumptions as Array<Record<string, unknown>>
    : []

  return (
    <section>
      <h5>基准与数据质量</h5>
      <div className="backtest-quality-list">
        {benchmarkRows.map(([key, benchmark]) => (
          <div key={key}>
            <strong>{benchmarkLabel(key)}</strong>
            <span className={benchmark.status === 'available' ? 'is-available' : 'is-unavailable'}>
              {benchmark.status === 'available'
                ? formatSignedPercent(Number(benchmark.return_pct || 0))
                : `不可用 · ${benchmark.reason || '无有效序列'}`}
            </span>
          </div>
        ))}
        {!benchmarkRows.length && <p>未配置比较基准。</p>}
      </div>
      <ul className="backtest-audit-notes">
        <li>{result.report_set_ids?.length || 0} 份日报进入本次计算</li>
        <li>结果哈希：{run.result_content_hash || '未返回'}</li>
        {adapter?.max_position_pct !== undefined && (
          <li>基金建仓规则：仓位上限 {Number(adapter.max_position_pct)}% · 单批 {Number(adapter.entry_tranche_pct)}% · 最多 {Number(adapter.max_entry_tranches)} 次 · 间隔 {Number(adapter.minimum_entry_interval_days)} 个估值日</li>
        )}
        {adapter?.degraded_estimate === true && <li>场外基金费用与确认周期采用已发布策略版本中的兜底参数</li>}
        {String(adapter?.open_status_validation || '') === 'order_date_and_side' && <li>申购/赎回开放状态仅在真实订单日按方向校验</li>}
        {openStatusAssumptions.length > 0 && (
          <li>历史开放状态缺失，按开放估算 {openStatusAssumptions.length} 次：{openStatusAssumptions.map((row) => `${String(row.date || '—')} ${row.side === 'sell' ? '赎回' : '申购'}`).join('、')}</li>
        )}
      </ul>
    </section>
  )
}

function BacktestTrades({ trades }: { trades: StrategyBacktestResult['trades'] }) {
  return (
    <section>
      <h5>成交与信号血缘</h5>
      {trades.length ? (
        <div className="backtest-trade-table">
          <table>
            <thead><tr><th>确认日</th><th>方向</th><th>标的</th><th>成交额</th><th>费用</th><th>信号</th></tr></thead>
            <tbody>{trades.map((trade, index) => (
              <tr key={`${trade.date}-${trade.symbol || ''}-${index}`}>
                <td>{trade.date}<small>{trade.order_date ? `下单 ${trade.order_date}` : ''}</small></td>
                <td>
                  <span className={`trade-side is-${trade.side}`}>{trade.side === 'buy' ? '申购/买入' : '赎回/卖出'}</span>
                  {trade.entry_tranche_index ? <small>第 {trade.entry_tranche_index}/{trade.entry_tranche_limit} 批</small> : null}
                </td>
                <td>{trade.symbol || trade.instrument_id || '—'}</td>
                <td>{formatCurrency(Number(trade.gross_value || 0))}</td>
                <td>{formatCurrency(Number(trade.fees?.total || 0))}</td>
                <td><code>{trade.signal_id || trade.signal_ids?.[0] || '—'}</code></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : (
        <div className="backtest-no-trades">
          <ShieldCheck size={18} />
          <div><strong>本次没有形成成交</strong><p>策略权益曲线仍然有效：它表示信号门禁下保持现金或既有仓位的实际结果。</p></div>
        </div>
      )}
    </section>
  )
}

function BacktestMetric({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: BacktestMetricTone }) {
  return <article className={`backtest-metric is-${tone}`}><span>{label}</span><strong>{value}</strong></article>
}

function backtestChartData(result: StrategyBacktestResult, initialCash: number): BacktestChartDatum[] {
  const rows = new Map<string, BacktestChartDatum>()
  const ensure = (dateValue: string) => {
    const current = rows.get(dateValue) || { date: dateValue }
    rows.set(dateValue, current)
    return current
  }
  for (const row of result.equity_curve || []) {
    const equity = finiteNumber(row.equity)
    if (!row.date || equity === null || initialCash <= 0) continue
    ensure(row.date).strategy = (equity / initialCash - 1) * 100
  }
  const benchmarkKeys: Array<[string, keyof Omit<BacktestChartDatum, 'date'>]> = [
    ['fund_buy_and_hold', 'fundBuyHold'],
    ['category_index', 'categoryIndex'],
    ['hs300', 'hs300'],
  ]
  for (const [benchmarkKey, dataKey] of benchmarkKeys) {
    for (const row of result.benchmarks?.[benchmarkKey]?.equity_curve || []) {
      const value = finiteNumber(row.return_pct)
      if (!row.date || value === null) continue
      ensure(row.date)[dataKey] = value
    }
  }
  return [...rows.values()].sort((left, right) => left.date.localeCompare(right.date))
}

function benchmarkLabel(key: string): string {
  return ({
    fund_buy_and_hold: '基金买入持有',
    category_index: '类别指数',
    hs300: '沪深300',
    initial_disclosed_weights_static: '期初披露持仓',
  } as Record<string, string>)[key] || key
}

function finiteNumber(value: unknown): number | null {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function numberTone(value: number | null): BacktestMetricTone {
  if (value === null || value === 0) return 'neutral'
  return value > 0 ? 'positive' : 'negative'
}

function formatSignedPercent(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatChartValue(value: number | undefined): string {
  return value === undefined ? '—' : formatSignedPercent(value)
}

function formatCurrency(value: number): string {
  return Number.isFinite(value)
    ? `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
    : '—'
}

export function BackfillProgress({ job, onPreflight }: { job: ReportJob; onPreflight: () => Promise<void> }) {
  const progress = reportJobProgress(job)
  const terminal = ['completed', 'completed_partial', 'failed', 'cancelled'].includes(job.status)
  return (
    <div className={`backfill-progress is-${job.status}`}>
      <header><div><strong>日报补跑 · {job.status.toUpperCase()}</strong><code>{job.id}</code></div><span>{progress.processed}/{progress.total} · {progress.percent}%</span></header>
      <ProgressTrack progress={progress} label="日报补跑进度" />
      <p>{progressSummary(job, progress, true)}</p>
      <ReportStageProgress job={job} />
      {job.error_summary && <p className="strategy-inline-error">{job.error_summary}</p>}
      {terminal && <button className="secondary-button" onClick={() => void onPreflight()}><ShieldCheck size={13} />重新执行预检</button>}
    </div>
  )
}

export function ReportJobProgressMeter({ job }: { job: ReportJob }) {
  const progress = reportJobProgress(job)
  return (
    <div className="report-job-progress">
      <div><span>{progress.processed}/{progress.total} · {progress.percent}%</span><small>{progressSummary(job, progress, false)}</small></div>
      <ProgressTrack progress={progress} label={`日报任务进度 ${job.id}`} />
      <ReportStageProgress job={job} />
    </div>
  )
}

function ReportStageProgress({ job }: { job: ReportJob }) {
  const progress = job.progress
  const batches = progress.batches && typeof progress.batches === 'object'
    ? Object.entries(progress.batches) : []
  const phase = String(progress.request_phase || '')
  const phaseLabel: Record<string, string> = { queued: '等待模型槽位', requesting: '模型请求中', backoff: '瞬时错误退避', queue_timeout: '模型准入超时', completed: '模型响应已返回，阶段仍需校验', failed: '模型请求失败', stage_deadline: '阶段预算已耗尽', cancelled: '模型请求已取消' }
  const stage = String(progress.current_action || progress.current_stage || '')
  const timing = progress.request_timing && typeof progress.request_timing === 'object'
    ? progress.request_timing as Record<string, unknown> : null
  if (!batches.length && !stage && !phase) return null
  return <div className="report-stage-progress">
    <p>{stage || (['queued', 'running'].includes(job.status) ? '持仓报告执行中，日报完成度将在结算后更新' : '持仓报告批次已记录')}{phase ? ` · ${phaseLabel[phase] || phase}` : ''}</p>
    {progress.holding_total !== undefined && <small>持仓完成 {Number(progress.holding_completed || 0)}/{Number(progress.holding_total)}</small>}
    {progress.progress_scope === 'stock_agent_stages' && <small>本股 Agent 已校验 {Number(progress.workflow_completed_stages || 0)}/{Number(progress.workflow_total_stages || 0)} 阶段（不代表整份报告通过交付）</small>}
    {timing && <small>{typeof timing.executor_wait_seconds === 'number' ? `执行器排队 ${timing.executor_wait_seconds.toFixed(1)}s · ` : ''}模型槽位排队 {Number(timing.queue_seconds || 0).toFixed(1)}s · 请求 {Number(timing.http_seconds || 0).toFixed(1)}s · 退避 {Number(timing.backoff_seconds || 0).toFixed(1)}s</small>}
    {timing && typeof timing.stage_elapsed_seconds === 'number' && <small>本阶段已耗时 {timing.stage_elapsed_seconds.toFixed(1)}s{typeof timing.remaining_budget_seconds === 'number' ? ` · 剩余预算 ${timing.remaining_budget_seconds.toFixed(1)}s` : ''}（包含入队等待，不代表整份报告完成度）</small>}
    {typeof progress.persistence_seconds === 'number' && <small>持仓累计持久化 {progress.persistence_seconds.toFixed(1)}s（不含复用报告）</small>}
    {progress.render_status === 'not_executed_in_job' && <small>任务内 HTML 渲染未执行（导出时按需计时）</small>}
    {Number(progress.cache_hit_count || 0) > 0 && <small>复用 {Number(progress.cache_hit_count)} 份报告 · {progress.cache_reason === 'fixed_manifest_and_pins_match' ? '冻结 Manifest 与版本约束一致' : String(progress.cache_reason || '已通过缓存匹配')}</small>}
    {batches.map(([day, id]) => <small key={day}>{day} · 批次 <code>{String(id)}</code></small>)}
  </div>
}

type ReportJobProgress = ReturnType<typeof reportJobProgress>

function ProgressTrack({ progress, label }: { progress: ReportJobProgress; label: string }) {
  return (
    <div className="backfill-progress-track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.processed}>
      <span style={{ width: `${progress.percent}%` }} />
    </div>
  )
}

function progressSummary(job: ReportJob, progress: ReportJobProgress, detailed: boolean): string {
  const completeLabel = detailed ? '完整日报' : '完整'
  const partialLabel = detailed ? '部分日报' : '部分'
  const blockedLabel = detailed ? '阻断日报' : '阻断'
  const failedLabel = detailed ? '执行异常' : '异常'
  return `已结算 ${progress.completed} · ${completeLabel} ${progress.completeReportDays} · ${partialLabel} ${progress.partialReportDays} · ${blockedLabel} ${progress.blockedReportDays} · ${failedLabel} ${progress.failed}${job.progress.current_date ? ` · 当前 ${String(job.progress.current_date)}` : ''}`
}

function reportJobProgress(job: ReportJob) {
  const total = Math.max(0, Number(job.progress.total) || 0)
  const completed = Math.max(0, Number(job.progress.completed) || 0)
  const failed = Math.max(0, Number(job.progress.failed) || 0)
  const processed = Math.min(total, completed + failed)
  const completeReportDays = Math.max(0, Number(job.progress.complete_report_days) || 0)
  const partialReportDays = Math.max(0, Number(job.progress.partial_report_days) || 0)
  const blockedReportDays = Math.max(0, Number(job.progress.blocked_report_days) || 0)
  return {
    total,
    completed,
    failed,
    processed,
    completeReportDays,
    partialReportDays,
    blockedReportDays,
    percent: total ? Math.round((processed / total) * 100) : 0,
  }
}
