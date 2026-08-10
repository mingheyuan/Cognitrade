import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  Database,
  Download,
  ExternalLink,
  FileText,
  LineChart as LineChartIcon,
  LoaderCircle,
  RefreshCw,
  Scale,
  ShieldCheck,
  Square,
  Users,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
import { BusinessDataView } from '../artifacts/BusinessDataView'
import { dataGapLabel } from '../artifacts/labels'
import {
  CognitradeApiClient,
  CognitradeApiError,
  type FundHoldingReportBatch,
  type HoldingReportProfile,
  type RunReportExport,
  type RunReportFormat,
} from '../api-client'
import type { BacktestTarget } from '../finance-contracts'
import type { RunStepsPayload } from '../research-contracts'
import { errorMessageWithRequestId, localizeErrorMessage } from '../utils/error-message'

type FundAnalysisDossierProps = {
  artifacts: Array<Record<string, unknown>>
  sourceRunId: string
  client: CognitradeApiClient
  onOpenRun: (runId: string) => void
  onOpenBacktest: (target: BacktestTarget) => void
}

type RecordValue = Record<string, unknown>
type TrendRange = '1y' | '3y' | 'all'
type HoldingRunProgress = {
  currentNodeId: string
  currentObjective: string
  percent: number | null
  settledNodes: number
  totalNodes: number
}

const RETURN_FIELDS = [
  ['daily', '当日'],
  ['week_1', '近 1 周'],
  ['month_1', '近 1 月'],
  ['month_3', '近 3 月'],
  ['month_6', '近 6 月'],
  ['year_1', '近 1 年'],
  ['year_2', '近 2 年'],
  ['year_3', '近 3 年'],
  ['year_to_date', '今年以来'],
  ['since_inception', '成立以来'],
] as const

const SCORE_LABELS: Record<string, string> = {
  return_1y: '近一年收益',
  return_3y_annualized: '长期年化收益',
  benchmark_excess_1y: '近一年超额收益',
  max_drawdown: '最大回撤',
  volatility: '年化波动',
  fee: '费率',
  return_consistency: '收益一致性',
}

const REPORT_PROFILES: Array<{ value: HoldingReportProfile; label: string; detail: string }> = [
  { value: 'lite', label: '速判', detail: '1–2 分钟 · 10 位评委' },
  { value: 'medium', label: '标准', detail: '5–8 分钟 · 51 位评委' },
  { value: 'deep', label: '深度', detail: '15–20 分钟 · 66 位 Agent 角色评审与多空辩论' },
]

function asRecord(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as RecordValue
    : {}
}

function records(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(asRecord).filter((row) => Object.keys(row).length > 0) : []
}

function artifactContent(artifacts: Array<Record<string, unknown>>, type: string): RecordValue {
  const artifact = [...artifacts].reverse().find((row) => String(row.artifact_type || '') === type)
  return asRecord(artifact?.content)
}

function numberValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function percent(value: unknown, signed = true): string {
  const number = numberValue(value)
  return number == null ? '—' : `${signed && number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function disclosureStatus(value: unknown): string {
  return ({
    available: '已取得',
    unavailable: '未取得',
    not_applicable: '不适用',
  } as Record<string, string>)[String(value || '')] || '状态未知'
}

function numberText(value: unknown, digits = 2): string {
  const number = numberValue(value)
  return number == null ? '—' : number.toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

function qualityLabel(value: unknown): string {
  return ({
    available: '证据完整',
    degraded: '部分证据可用',
    data_gap: '证据不足',
  } as Record<string, string>)[String(value || '')] || '等待证据'
}

function dateMillis(value: unknown): number {
  const parsed = Date.parse(String(value || ''))
  return Number.isFinite(parsed) ? parsed : 0
}

function trendRows(rows: RecordValue[], dateKey: string, range: TrendRange): RecordValue[] {
  const sorted = [...rows].sort((left, right) => dateMillis(left[dateKey]) - dateMillis(right[dateKey]))
  if (range === 'all' || !sorted.length) return sorted
  const end = dateMillis(sorted.at(-1)?.[dateKey])
  const days = range === '1y' ? 366 : 1096
  return sorted.filter((row) => dateMillis(row[dateKey]) >= end - days * 86_400_000)
}

function FundTrendChart({
  rows,
  dateKey,
  series,
  title,
  pointLabel,
}: {
  rows: RecordValue[]
  dateKey: string
  series: Array<{ key: string; label: string; color: string }>
  title: string
  pointLabel: string
}) {
  const [range, setRange] = useState<TrendRange>('all')
  const visible = useMemo(() => trendRows(rows, dateKey, range), [dateKey, range, rows])
  if (rows.length < 2) {
    return <div className="fund-trend-empty"><LineChartIcon size={18} /><span>暂无足够历史点绘制{title}</span></div>
  }
  return (
    <section className="fund-trend-panel">
      <header>
        <div><LineChartIcon size={15} /><strong>{title}</strong><span>{rows.length} 个{pointLabel}</span></div>
        <div className="fund-trend-ranges" role="group" aria-label={`${title}区间`}>
          {([['1y', '近 1 年'], ['3y', '近 3 年'], ['all', '全部']] as const).map(([id, label]) => (
            <button key={id} type="button" className={range === id ? 'is-active' : ''} onClick={() => setRange(id)}>{label}</button>
          ))}
        </div>
      </header>
      <div className="fund-trend-chart" role="img" aria-label={`${title}，当前展示 ${visible.length} 个点，共 ${rows.length} 个点`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={visible} margin={{ top: 10, right: 18, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="rgba(126,160,148,.12)" vertical={false} />
            <XAxis dataKey={dateKey} minTickGap={44} tick={{ fill: '#78948a', fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis domain={['auto', 'auto']} width={56} tick={{ fill: '#78948a', fontSize: 10 }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={{ background: '#101c19', border: '1px solid rgba(148,180,169,.18)', borderRadius: 8, color: '#eaf4f0', fontSize: 11 }} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {series.map((item) => <Line key={item.key} dataKey={item.key} name={item.label} stroke={item.color} strokeWidth={1.8} dot={false} connectNulls />)}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <footer><span>覆盖 {String(visible[0]?.[dateKey] || '—')} 至 {String(visible.at(-1)?.[dateKey] || '—')}</span><span>显示 {visible.length} / {rows.length} 点</span></footer>
    </section>
  )
}

export function FundAnalysisDossier({ artifacts, sourceRunId, client, onOpenRun, onOpenBacktest }: FundAnalysisDossierProps) {
  const analysis = artifactContent(artifacts, 'fund_analysis')
  const snapshot = artifactContent(artifacts, 'fund_snapshot')
  const analysisMode = String(analysis.analysis_mode || snapshot.analysis_mode || 'comparison')
  const isSingleFund = analysisMode === 'single_fund'
  const snapshotFunds = new Map(records(snapshot.funds).map((row) => [String(row.fund_code || ''), row]))
  const candidates: RecordValue[] = records(analysis.candidates).map((row): RecordValue => ({
    ...snapshotFunds.get(String(row.fund_code || '')),
    ...row,
    analysis_mode: analysisMode,
  }))
  const recommendations = records(analysis.recommendations)
  const policy = asRecord(analysis.selection_policy)
  const errors = [...records(snapshot.provider_errors), ...records(analysis.provider_errors)]
  const gaps = Array.from(new Set([
    ...(Array.isArray(snapshot.data_gap) ? snapshot.data_gap.map(String) : []),
    ...(Array.isArray(analysis.data_gap) ? analysis.data_gap.map(String) : []),
  ]))
  const [holdingBatchSnapshot, setHoldingBatchSnapshot] = useState<{
    sourceRunId: string
    rows: FundHoldingReportBatch[]
  }>({ sourceRunId: '', rows: [] })
  const [holdingBatchesLoading, setHoldingBatchesLoading] = useState(Boolean(sourceRunId))
  const [holdingAction, setHoldingAction] = useState('')
  const [holdingError, setHoldingError] = useState('')
  const [holdingRunProgress, setHoldingRunProgress] = useState<Record<string, HoldingRunProgress>>({})
  const holdingBatchLoadSequence = useRef(0)
  const sourceRunIdRef = useRef(sourceRunId)
  const holdingBatches = useMemo(
    () => holdingBatchSnapshot.sourceRunId === sourceRunId ? holdingBatchSnapshot.rows : [],
    [holdingBatchSnapshot, sourceRunId],
  )
  const holdingBatchesRestoring = holdingBatchesLoading
    || Boolean(sourceRunId && holdingBatchSnapshot.sourceRunId !== sourceRunId)

  useEffect(() => {
    sourceRunIdRef.current = sourceRunId
  }, [sourceRunId])

  const refreshHoldingBatches = useCallback(async (quiet = false) => {
    const requestSequence = ++holdingBatchLoadSequence.current
    if (!sourceRunId) {
      setHoldingBatchSnapshot({ sourceRunId: '', rows: [] })
      setHoldingBatchesLoading(false)
      return
    }
    if (!quiet) setHoldingBatchesLoading(true)
    try {
      const rows = await client.listFundHoldingReportBatches(sourceRunId)
      if (sourceRunIdRef.current !== sourceRunId || holdingBatchLoadSequence.current !== requestSequence) return
      setHoldingBatchSnapshot({ sourceRunId, rows })
      if (!quiet) setHoldingError('')
    } catch (error) {
      if (sourceRunIdRef.current !== sourceRunId || holdingBatchLoadSequence.current !== requestSequence) return
      setHoldingBatchSnapshot({ sourceRunId, rows: [] })
      if (!quiet) setHoldingError(reportErrorMessage(error))
    } finally {
      if (!quiet && holdingBatchLoadSequence.current === requestSequence) {
        setHoldingBatchesLoading(false)
      }
    }
  }, [client, sourceRunId])

  useEffect(() => {
    queueMicrotask(() => void refreshHoldingBatches())
  }, [refreshHoldingBatches])

  const hasRunningBatch = holdingBatches.some((row) => row.status === 'running')
  useEffect(() => {
    if (!hasRunningBatch) return
    const timer = window.setInterval(() => void refreshHoldingBatches(true), 2000)
    return () => window.clearInterval(timer)
  }, [hasRunningBatch, refreshHoldingBatches])

  const activeHoldingRunIdsKey = useMemo(() => Array.from(new Set(
    holdingBatches.flatMap((batch) => batch.holdings
      .filter((item) => item.status === 'running' && item.child_run_id)
      .map((item) => item.child_run_id || '')),
  )).filter(Boolean).sort().join(','), [holdingBatches])

  useEffect(() => {
    const runIds = activeHoldingRunIdsKey.split(',').filter(Boolean)
    if (!runIds.length) return
    let cancelled = false
    let refreshing = false
    const refresh = async () => {
      if (refreshing) return
      refreshing = true
      try {
        const results = await Promise.allSettled(runIds.map(async (runId) => ({
          progress: holdingProgressFromSteps(await client.getSteps(runId)),
          runId,
        })))
        if (cancelled) return
        const next: Record<string, HoldingRunProgress> = {}
        results.forEach((result) => {
          if (result.status === 'fulfilled') next[result.value.runId] = result.value.progress
        })
        setHoldingRunProgress(next)
      } finally {
        refreshing = false
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [activeHoldingRunIdsKey, client])

  const runHoldingAction = useCallback(async (
    key: string,
    action: () => Promise<FundHoldingReportBatch>,
  ) => {
    setHoldingAction(key)
    setHoldingError('')
    try {
      const batch = await action()
      if (sourceRunIdRef.current !== sourceRunId) return
      setHoldingBatchSnapshot((current) => ({
        sourceRunId,
        rows: [
          batch,
          ...(current.sourceRunId === sourceRunId ? current.rows : [])
            .filter((row) => row.batch_id !== batch.batch_id),
        ],
      }))
    } catch (error) {
      setHoldingError(reportErrorMessage(error))
    } finally {
      setHoldingAction('')
    }
  }, [sourceRunId])

  const createHoldingBatch = useCallback((
    fundCode: string,
    profile: HoldingReportProfile,
    confirmStale: boolean,
    force: boolean,
  ) => runHoldingAction(
    `create:${fundCode}`,
    () => client.createFundHoldingReports(sourceRunId, {
      fund_code: fundCode,
      profile,
      confirm_stale: confirmStale,
      force,
    }),
  ), [client, runHoldingAction, sourceRunId])

  const mutateHoldingBatch = useCallback((
    batchId: string,
    action: 'cancel' | 'retry' | 'continue' | 'regenerate',
    symbols: string[] = [],
  ) => runHoldingAction(holdingMutationKey(action, batchId, symbols), () => {
    if (action === 'cancel') return client.cancelFundHoldingReportBatch(batchId)
    if (action === 'continue') return client.continueFundHoldingReportBatch(batchId, symbols)
    if (action === 'regenerate') return client.retryFundHoldingReportBatch(batchId, symbols, true)
    return client.retryFundHoldingReportBatch(batchId, symbols)
  }), [client, runHoldingAction])

  const downloadHoldingExport = useCallback(async (
    key: string,
    load: () => Promise<RunReportExport>,
  ) => {
    setHoldingAction(key)
    setHoldingError('')
    try {
      saveReportExport(await load())
    } catch (error) {
      setHoldingError(reportErrorMessage(error))
    } finally {
      setHoldingAction('')
    }
  }, [])

  if (!Object.keys(analysis).length && !Object.keys(snapshot).length) return null
  return (
    <section className="fund-dossier" aria-label="基金完整分析">
      <header className="fund-dossier-header">
        <div><span className="section-kicker">COMPLETE FUND DOSSIER</span><h2>基金完整分析</h2><p>将采集快照与选择方法合并展示；历史序列仅绘制真实观测，不补值。</p></div>
        <div className="fund-dossier-meta">
          <span className={`quality-pill quality-pill--${String(analysis.quality_status || snapshot.quality_status || 'data_gap')}`}>{qualityLabel(analysis.quality_status || snapshot.quality_status)}</span>
          <small>{String(analysis.methodology_version || '方法论版本未返回')}</small>
        </div>
      </header>

      <div className="fund-dossier-summary">
        <article><Database size={16} /><span>采集候选</span><strong>{numberText(snapshot.candidate_pool_size ?? candidates.length, 0)}</strong><small>{String(snapshot.fund_category || analysis.fund_category || '未分类')}</small></article>
        <article><ShieldCheck size={16} /><span>{isSingleFund ? '分析完成' : '证据达标'}</span><strong>{isSingleFund ? candidates.filter((row) => row.quality_status !== 'data_gap').length : candidates.filter((row) => row.selection_eligible === true).length}</strong><small>按真实字段覆盖计算</small></article>
        <article><CheckCircle2 size={16} /><span>{isSingleFund ? '分析对象' : '优先候选'}</span><strong>{isSingleFund ? candidates.length : recommendations.length}</strong><small>{isSingleFund ? '不执行横向排名' : '只来自可比样本'}</small></article>
        <article><AlertCircle size={16} /><span>运行级缺口</span><strong>{gaps.length}</strong><small>{errors.length} 个数据源异常</small></article>
      </div>

      <section className="fund-recommendation-board">
        <header><div><CheckCircle2 size={16} /><h3>{isSingleFund ? '分析口径' : '选择结论'}</h3></div><span>{isSingleFund ? '独立评估' : `${recommendations.length} 项`}</span></header>
        {isSingleFund ? (
          <div className="fund-dossier-empty"><ShieldCheck size={18} />本次为单只基金独立分析，数据完整度与风险收益结论不依赖同类对照组，也不生成横向名次。</div>
        ) : recommendations.length ? (
          <div>{recommendations.map((row) => (
            <article key={String(row.fund_code)}>
              <span>#{String(row.rank || '—')}</span>
              <div><strong>{String(row.fund_name || row.fund_code)}</strong><small>{String(row.fund_code)} · {String(row.comparison_group || row.fund_type || '未分组')}</small></div>
              <div><strong>{numberText(row.score, 1)}</strong><small>覆盖 {percent((numberValue(row.score_coverage) || 0) * 100, false)}</small></div>
              <p>{String(row.reason || '未返回推荐理由')}</p>
              <p className="is-risk">{String(row.risk || '未返回风险摘要')}</p>
            </article>
          ))}</div>
        ) : <div className="fund-dossier-empty"><AlertCircle size={18} />当前没有候选通过方法论的证据门槛。</div>}
      </section>

      {holdingError && <div className="fund-holding-report-error" role="alert"><AlertCircle size={15} />{holdingError}</div>}

      <div className="fund-candidate-dossiers">
        {candidates.map((candidate, index) => <FundCandidateDossier
          key={String(candidate.fund_code || index)}
          candidate={candidate}
          defaultOpen={index === 0}
          holdingBatches={holdingBatches.filter((row) => row.fund_code === String(candidate.fund_code || ''))}
          holdingBatchesLoading={holdingBatchesRestoring}
          holdingAction={holdingAction}
          holdingRunProgress={holdingRunProgress}
          onCreateHoldingBatch={createHoldingBatch}
          onMutateHoldingBatch={mutateHoldingBatch}
          onDownloadZip={(batchId) => downloadHoldingExport(`zip:${batchId}`, () => client.getFundHoldingReportZip(batchId))}
          onDownloadReport={(runId, format) => downloadHoldingExport(`${format}:${runId}`, () => client.getRunReport(runId, format))}
          onOpenRun={onOpenRun}
          onOpenBacktest={(target) => onOpenBacktest({ ...target, sourceRunId })}
        />)}
      </div>

      <div className="fund-audit-grid">
        <section>
          <header><Scale size={15} /><h3>选择规则</h3></header>
          <p>{String(policy.description || '后端未返回选择规则说明')}</p>
          <div className="fund-policy-metrics"><span>最低证据覆盖<strong>{percent((numberValue(policy.minimum_score_coverage) || 0) * 100, false)}</strong></span><span>{isSingleFund ? '横向比较' : '最小可比样本'}<strong>{isSingleFund ? '不要求' : numberText(policy.minimum_comparable_sample, 0)}</strong></span></div>
          <BusinessDataView value={policy.components || []} fieldKey="components" path="fund_analysis.selection_policy.components" compact />
          <small>{String(policy.non_imputation_rule || '缺失数据不补值。')}</small>
        </section>
        <section>
          <header><AlertCircle size={15} /><h3>数据缺口与源状态</h3></header>
          {gaps.length ? <ul>{gaps.map((gap) => <li key={gap}>{dataGapLabel(gap)}</li>)}</ul> : <p>运行级未报告数据缺口。</p>}
          {errors.length ? <BusinessDataView value={errors} fieldKey="provider_errors" path="fund.provider_errors" compact /> : <small>未报告数据源异常。</small>}
        </section>
      </div>
    </section>
  )
}

function FundCandidateDossier({
  candidate,
  defaultOpen,
  holdingBatches,
  holdingBatchesLoading,
  holdingAction,
  holdingRunProgress,
  onCreateHoldingBatch,
  onMutateHoldingBatch,
  onDownloadZip,
  onDownloadReport,
  onOpenRun,
  onOpenBacktest,
}: {
  candidate: RecordValue
  defaultOpen: boolean
  holdingBatches: FundHoldingReportBatch[]
  holdingBatchesLoading: boolean
  holdingAction: string
  holdingRunProgress: Record<string, HoldingRunProgress>
  onCreateHoldingBatch: (fundCode: string, profile: HoldingReportProfile, confirmStale: boolean, force: boolean) => void
  onMutateHoldingBatch: (batchId: string, action: 'cancel' | 'retry' | 'continue' | 'regenerate', symbols?: string[]) => void
  onDownloadZip: (batchId: string) => void
  onDownloadReport: (runId: string, format: RunReportFormat) => void
  onOpenRun: (runId: string) => void
  onOpenBacktest: (target: BacktestTarget) => void
}) {
  const returns = asRecord(candidate.returns_pct)
  const risk = asRecord(candidate.risk)
  const overview = asRecord(candidate.fund_overview)
  const scale = asRecord(candidate.scale_change)
  const scoreComponents = records(candidate.score_components)
  const navHistory = records(candidate.nav_history)
  const scaleHistory = records(candidate.scale_history)
  const performance = records(candidate.benchmark_performance)
  const managers = records(candidate.manager_tenures)
  const holdings = records(candidate.top_holdings)
  const assetAllocation = records(candidate.asset_allocation)
  const bondHoldings = records(candidate.bond_holdings)
  const targetFundHoldings = records(candidate.target_fund_holdings)
  const lookthroughHoldings = records(candidate.lookthrough_holdings)
  const holdingsCoverage = asRecord(candidate.holdings_coverage)
  const isFundOfFunds = holdingsCoverage.is_fund_of_funds === true
  const targetFund = targetFundHoldings[0] || {}
  const targetFundSource = String(candidate.target_fund_source_ref || '')
  const allocationTotal = assetAllocation.reduce(
    (total, row) => total + (numberValue(row.weight_pct) || 0),
    0,
  )
  const gaps = Array.isArray(candidate.data_gap) ? candidate.data_gap.map(String) : []
  const isSingleFund = String(candidate.analysis_mode || '') === 'single_fund'
  const usesSinceInceptionReturn = numberValue(returns.year_3) == null
  const [reportProfile, setReportProfile] = useState<HoldingReportProfile | ''>('')
  const [selectedBatchId, setSelectedBatchId] = useState('')
  const [confirmStale, setConfirmStale] = useState(false)
  const sortedBatches = useMemo(
    () => [...holdingBatches].sort((left, right) => String(right.created_at).localeCompare(String(left.created_at))),
    [holdingBatches],
  )
  const preferredBatch = sortedBatches.find((batch) => batch.status === 'running') || sortedBatches[0]
  const effectiveReportProfile = reportProfile || preferredBatch?.profile || ''
  const profileBatches = sortedBatches.filter((row) => row.profile === effectiveReportProfile)
  const currentBatch = profileBatches.find((row) => row.batch_id === selectedBatchId) || profileBatches[0]
  const currentBatchStatus = currentBatch?.status
  const [progressClock, setProgressClock] = useState(() => Date.now())
  useEffect(() => {
    if (currentBatchStatus !== 'running') return
    const timer = window.setInterval(() => setProgressClock(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [currentBatchStatus])
  const batchProgress = currentBatch ? holdingBatchProgress(currentBatch, holdingRunProgress) : 0
  const settledItems = currentBatch
    ? currentBatch.counts.supported - currentBatch.counts.running - currentBatch.counts.queued
    : 0
  const reportHoldings = isFundOfFunds ? lookthroughHoldings : holdings
  const reportEligibility = holdingReportEligibility(candidate, reportHoldings)
  const reportDisclosureDate = firstDisclosureDate(
    isFundOfFunds ? candidate.lookthrough_period : candidate.holdings_period,
    candidate.asset_allocation_period,
    candidate.target_fund_period,
  )
  const disclosureAge = dateAgeDays(reportDisclosureDate)
  const requiresConfirmation = disclosureAge != null && disclosureAge > 180
  const warnsStale = disclosureAge != null && disclosureAge > 120
  return (
    <details className={`fund-candidate-dossier ${candidate.selection_eligible ? 'is-eligible' : ''}`} open={defaultOpen}>
      <summary>
        <span className="fund-rank">{isSingleFund ? '分析' : candidate.rank ? `#${candidate.rank}` : '—'}</span>
        <span><strong>{String(candidate.fund_name || candidate.fund_code)}</strong><small>{String(candidate.fund_code || '—')} · {String(candidate.fund_type || '类型未返回')}</small></span>
        <span><small>{isSingleFund ? '分析状态' : '综合分'}</small><strong>{isSingleFund ? candidate.quality_status === 'data_gap' ? '证据不足' : '已完成' : numberText(candidate.score, 1)}</strong></span>
        <span><small>证据覆盖</small><strong>{percent((numberValue(candidate.score_coverage) || 0) * 100, false)}</strong></span>
        <span className={`quality-pill quality-pill--${String(candidate.quality_status || 'data_gap')}`}>{qualityLabel(candidate.quality_status)}</span>
        <ChevronDown size={16} />
      </summary>
      <div className="fund-candidate-dossier-body">
        <div className="fund-return-grid">
          {RETURN_FIELDS.map(([key, label]) => <article key={key}><span>{label}</span><strong className={(numberValue(returns[key]) || 0) >= 0 ? 'number-up' : 'number-down'}>{percent(returns[key])}</strong></article>)}
        </div>

        <div className="fund-core-grid">
          <section>
            <header><BarChart3 size={15} /><h4>风险与费用</h4></header>
            <dl>
              <div><dt>样本期最大回撤</dt><dd>{percent(risk.max_drawdown_pct)}</dd></div>
              <div><dt>年化波动率</dt><dd>{percent(risk.annualized_volatility_pct, false)}</dd></div>
              <div><dt>样本期收益</dt><dd>{percent(risk.period_return_pct)}</dd></div>
              <div><dt>样本数量</dt><dd>{numberText(risk.observation_count, 0)}</dd></div>
              <div><dt>申购费率</dt><dd>{percent(candidate.fee_pct, false)}</dd></div>
              <div><dt>前十大集中度</dt><dd>{percent(candidate.top10_concentration_pct, false)}</dd></div>
            </dl>
          </section>
          <section>
            <header><Database size={15} /><h4>基金概况</h4></header>
            <dl>
              <div><dt>基金管理人</dt><dd>{String(overview.fund_company || '—')}</dd></div>
              <div><dt>基金经理</dt><dd>{String(overview.fund_manager || '—')}</dd></div>
              <div><dt>成立日期</dt><dd>{String(overview.inception_date || '—')}</dd></div>
              <div><dt>托管人</dt><dd>{String(overview.custodian || '—')}</dd></div>
              <div><dt>单位净值</dt><dd>{numberText(candidate.unit_nav, 4)}</dd></div>
              <div><dt>累计净值</dt><dd>{numberText(candidate.accumulated_nav, 4)}</dd></div>
            </dl>
          </section>
          <section>
            <header><Scale size={15} /><h4>规模与份额</h4></header>
            <dl>
              <div><dt>最新净资产</dt><dd>{numberText(scale.latest_net_assets_100m_cny)} 亿元</dd></div>
              <div><dt>最新总份额</dt><dd>{numberText(scale.latest_total_shares_100m)} 亿份</dd></div>
              <div><dt>最新净流入</dt><dd>{numberText(scale.latest_net_flow_100m_shares)} 亿份</dd></div>
              <div><dt>总份额变化</dt><dd>{percent(scale.total_share_change_pct)}</dd></div>
              <div><dt>净资产变化</dt><dd>{percent(scale.net_asset_change_pct)}</dd></div>
              <div><dt>最新报告日</dt><dd>{String(scale.latest_report_date || '—')}</dd></div>
            </dl>
          </section>
        </div>

        <FundTrendChart rows={navHistory} dateKey="date" title="单位净值完整趋势" pointLabel="净值观测点" series={[{ key: 'nav', label: '单位净值', color: '#70e0bd' }]} />
        <FundTrendChart rows={scaleHistory} dateKey="report_date" title="规模、份额与净流完整趋势" pointLabel="披露期" series={[
          { key: 'net_assets_100m_cny', label: '净资产（亿元）', color: '#70e0bd' },
          { key: 'total_shares_100m', label: '总份额（亿份）', color: '#6fb6ff' },
          { key: 'net_flow_100m_shares', label: '净流入（亿份）', color: '#e6bb63' },
        ]} />

        <section className="fund-score-section">
          <header><BarChart3 size={15} /><h4>{isSingleFund ? '核心分析指标' : '评分拆解'}</h4><span>{isSingleFund ? Object.keys(asRecord(candidate.metrics)).length : scoreComponents.length} 个已观测维度</span></header>
          {isSingleFund ? <div>{Object.entries(asRecord(candidate.metrics)).map(([key, value]) => (
            <article key={key}>
              <span>{key === 'return_3y_annualized' && usesSinceInceptionReturn ? '成立以来年化收益' : SCORE_LABELS[key] || key}</span>
              <strong>{percent(value)}</strong>
              <small>{key === 'return_3y_annualized' && usesSinceInceptionReturn ? `${String(risk.period_start || '成立')} 至 ${String(risk.period_end || '最新')}` : '真实观测值，不参与横向排名'}</small>
            </article>
          ))}</div> : scoreComponents.length ? <div>{scoreComponents.map((row) => (
            <article key={String(row.component)}>
              <span>{String(row.component) === 'return_3y_annualized' && usesSinceInceptionReturn ? '成立以来年化收益' : SCORE_LABELS[String(row.component)] || String(row.component)}</span>
              <strong>{numberText(row.value, 2)}</strong>
              <div><i style={{ width: `${Math.max(0, Math.min(100, numberValue(row.percentile) || 0))}%` }} /></div>
              <small>同类百分位 {numberText(row.percentile, 1)} · 权重 {percent((numberValue(row.weight) || 0) * 100, false)}{String(row.component) === 'return_3y_annualized' && usesSinceInceptionReturn ? ` · ${String(risk.period_start || '成立')} 至 ${String(risk.period_end || '最新')}` : ''}</small>
            </article>
          ))}</div> : <p>没有足够可比数据形成评分拆解。</p>}
        </section>

        <div className="fund-detail-grid">
          <section>
            <header><BarChart3 size={15} /><h4>基金与基准表现</h4><span>{performance.length} 期</span></header>
            {performance.length ? <div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>期间</th><th>基金收益</th><th>基准收益</th><th>超额收益</th><th>基金回撤</th><th>基准回撤</th><th>同类排名</th></tr></thead><tbody>{performance.map((row, index) => <tr key={`${String(row.period)}-${index}`}><td>{String(row.period || '—')}</td><td>{percent(row.fund_return_pct)}</td><td>{percent(row.benchmark_return_pct)}</td><td>{percent(row.excess_return_pct)}</td><td>{percent(row.fund_max_drawdown_pct)}</td><td>{percent(row.benchmark_max_drawdown_pct)}</td><td>{String(row.peer_rank || '—')}</td></tr>)}</tbody></table></div> : <p>未取得基准对比数据。</p>}
            <small>业绩比较基准：{String(candidate.benchmark_definition || '未返回')}</small>
          </section>
          <section>
            <header><Users size={15} /><h4>基金经理任期</h4><span>{managers.length} 段</span></header>
            {managers.length ? <div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>经理</th><th>开始</th><th>结束</th><th>任期</th><th>任期回报</th></tr></thead><tbody>{managers.map((row, index) => <tr key={`${String(row.manager)}-${String(row.start_date)}-${index}`}><td>{String(row.manager || '—')}{row.current === true ? '（现任）' : ''}</td><td>{String(row.start_date || '—')}</td><td>{String(row.end_date || '至今')}</td><td>{String(row.tenure_text || '—')}</td><td>{percent(row.tenure_return_pct)}</td></tr>)}</tbody></table></div> : <p>未取得基金经理任期分段。</p>}
          </section>
        </div>

        <section className="fund-holdings-section">
          <header><ShieldCheck size={15} /><h4>披露持仓结构</h4><span>{String(candidate.asset_allocation_period || candidate.holdings_period || '披露期未返回')}</span></header>
          {assetAllocation.length ? <div className="fund-allocation-grid">{assetAllocation.map((row) => <article key={String(row.asset_type)}><span>{String(row.label || row.asset_type)}</span><strong>{percent(row.weight_pct, false)}</strong></article>)}</div> : <p>本次运行未取得资产配置比例。</p>}
          <div className="fund-holdings-grid">
            <article>
              <header><div><strong>直接股票持仓</strong><small>基金自身直接持有，不含目标 ETF 穿透成分</small></div><span>{disclosureStatus(holdingsCoverage.direct_equity)} · {holdings.length} 项</span></header>
              {holdings.length ? <div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>序号</th><th>代码</th><th>名称</th><th>净值占比</th><th>持股数（万股）</th><th>市值（万元）</th></tr></thead><tbody>{holdings.map((row, index) => <tr key={`${String(row.code)}-${index}`}><td>{String(row.rank || index + 1)}</td><td>{String(row.code || '—')}</td><td>{String(row.name || '—')}</td><td>{percent(row.weight_pct, false)}</td><td>{numberText(row.shares_10k)}</td><td>{numberText(row.market_value_10k)}</td></tr>)}</tbody></table></div> : <p>{holdingsCoverage.direct_equity === 'not_applicable' ? '披露资产结构中没有直接股票配置。' : '未取得直接股票持仓明细。'}</p>}
            </article>
            <article>
              <header><div><strong>债券持仓</strong><small>基金自身直接持有的已披露债券</small></div><span>{disclosureStatus(holdingsCoverage.bond)} · {bondHoldings.length} 项</span></header>
              {bondHoldings.length ? <div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>序号</th><th>代码</th><th>名称</th><th>净值占比</th></tr></thead><tbody>{bondHoldings.map((row, index) => <tr key={`${String(row.code)}-${index}`}><td>{String(row.rank || index + 1)}</td><td>{String(row.code || '—')}</td><td>{String(row.name || '—')}</td><td>{percent(row.weight_pct, false)}</td></tr>)}</tbody></table></div> : <p>{holdingsCoverage.bond === 'not_applicable' ? '披露资产结构中没有债券配置。' : '未取得债券持仓明细。'}</p>}
            </article>
            {isFundOfFunds && <article>
              <header><div><strong>目标基金 / ETF</strong><small>联接基金官方报告披露的基金份额投资</small></div><span>{disclosureStatus(holdingsCoverage.target_fund)} · {targetFundHoldings.length} 项</span></header>
              {targetFundHoldings.length ? <><div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>代码</th><th>名称</th><th>净值占比</th><th>公允价值（万元）</th></tr></thead><tbody>{targetFundHoldings.map((row, index) => <tr key={`${String(row.code)}-${index}`}><td>{String(row.code || '—')}</td><td>{String(row.name || '—')}</td><td>{percent(row.weight_pct, false)}</td><td>{numberText(row.market_value_10k)}</td></tr>)}</tbody></table></div><small>披露期：{String(candidate.target_fund_period || targetFund.report_date || '未返回')}{targetFundSource.startsWith('https://') ? <> · <a href={targetFundSource} target="_blank" rel="noreferrer">查看官方报告</a></> : ''}</small></> : <p>公开报告未返回目标基金名称与精确占比；系统未做名称推断。</p>}
            </article>}
            {isFundOfFunds && <article>
              <header><div><strong>目标 ETF 前十大穿透持仓</strong><small>{String(targetFund.name || targetFund.code || '目标基金')}公开披露的前十大证券</small></div><span>{disclosureStatus(holdingsCoverage.lookthrough)} · {lookthroughHoldings.length} 项</span></header>
              {lookthroughHoldings.length ? <><div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>序号</th><th>代码</th><th>名称</th><th>目标 ETF 占比</th><th>联接基金有效暴露</th><th>持股数（万股）</th><th>市值（万元）</th></tr></thead><tbody>{lookthroughHoldings.map((row, index) => <tr key={`${String(row.code)}-${index}`}><td>{String(row.rank || index + 1)}</td><td>{String(row.code || '—')}</td><td>{String(row.name || '—')}</td><td>{percent(row.weight_pct, false)}</td><td>{percent(row.effective_weight_pct, false)}</td><td>{numberText(row.shares_10k)}</td><td>{numberText(row.market_value_10k)}</td></tr>)}</tbody></table></div><small>目标 ETF 持仓披露期：{String(candidate.lookthrough_period || '未返回')}。有效暴露 = 联接基金持有目标 ETF 比例 × 目标 ETF 成分占比。</small></> : <p>目标基金身份或持有份额未由当前公开报告披露，无法形成可审计的穿透持仓。</p>}
            </article>}
          </div>
          <footer>资产配置合计 {percent(allocationTotal, false)}。各项按基金净资产计量时，负债会使资产比例合计超过 100%；目标 ETF 已从“其他”中拆出，证券穿透仅表示最新披露的前十大持仓，不代表完整组合。</footer>
        </section>

        <section className="fund-holding-report-section">
          <header>
            <div>
              <FileText size={16} />
              <div><h4>前十大持仓股票报告</h4><small>从本次基金快照冻结持仓，最多 2 个股票研究并行执行；不重新猜测或抓取基金持仓。</small></div>
            </div>
            <span className={`quality-pill ${reportEligibility.eligible ? 'quality-pill--available' : 'quality-pill--data_gap'}`}>
              {reportEligibility.eligible ? `${reportHoldings.length} 项可编排` : '当前不可编排'}
            </span>
          </header>

          {!reportEligibility.eligible ? (
            <div className="fund-holding-report-empty"><AlertCircle size={16} /><span>{reportEligibility.reason}</span></div>
          ) : (
            <>
              <div className="fund-holding-report-config">
                <label>
                  <span>研究档位</span>
                  <select value={effectiveReportProfile} onChange={(event) => { setReportProfile(event.target.value as HoldingReportProfile | ''); setSelectedBatchId('') }} disabled={holdingBatchesLoading || currentBatch?.status === 'running'}>
                    <option value="">请选择研究深度</option>
                    {REPORT_PROFILES.map((profile) => <option key={profile.value} value={profile.value}>{profile.label} · {profile.detail}</option>)}
                  </select>
                </label>
                <div className={`fund-holding-disclosure ${warnsStale ? 'is-stale' : ''}`}>
                  <span>持仓披露</span>
                  <strong>{reportDisclosureDate || '日期未取得'}</strong>
                  <small>{disclosureAge == null ? '无法判定新鲜度，将在报告中标记缺口' : `${disclosureAge} 天前${warnsStale ? ' · 请注意持仓可能已变化' : ''}`}</small>
                </div>
                {requiresConfirmation && <label className="fund-holding-confirm">
                  <input type="checkbox" checked={confirmStale} onChange={(event) => setConfirmStale(event.target.checked)} />
                  <span>我确认使用超过 180 天的历史披露持仓</span>
                </label>}
              </div>

              {holdingBatchesLoading && !currentBatch ? (
                <div className="fund-holding-report-empty" role="status"><LoaderCircle size={16} className="is-spinning" /><span>正在恢复本次基金研究绑定的历史报告批次…</span></div>
              ) : !currentBatch ? (
                <button
                  type="button"
                  className="primary-button fund-holding-start"
                  disabled={!effectiveReportProfile || (requiresConfirmation && !confirmStale) || holdingAction === `create:${String(candidate.fund_code || '')}`}
                  onClick={() => { if (effectiveReportProfile) onCreateHoldingBatch(String(candidate.fund_code || ''), effectiveReportProfile, confirmStale, false) }}
                >
                  {holdingAction === `create:${String(candidate.fund_code || '')}` ? <LoaderCircle size={15} className="is-spinning" /> : <FileText size={15} />}
                  {effectiveReportProfile ? `生成 ${REPORT_PROFILES.find((row) => row.value === effectiveReportProfile)?.label}持仓股票报告` : '请先选择研究深度'}
                </button>
              ) : (
                <div className="fund-holding-batch">
                  <div className="fund-holding-batch-summary">
                    <div><span>批次状态</span><strong>{holdingBatchStatusLabel(currentBatch.status)}</strong><small>{currentBatch.batch_id}</small></div>
                    <div><span>可交付</span><strong>{currentBatch.counts.deliverable} / {currentBatch.counts.supported}</strong><small>完成与部分完成均可导出</small></div>
                    <div><span>执行中</span><strong>{currentBatch.counts.running + currentBatch.counts.queued}</strong><small>并发上限 {currentBatch.concurrency}</small></div>
                    <div><span>失败 / 不支持</span><strong>{currentBatch.counts.failed} / {currentBatch.counts.unsupported}</strong><small>不支持项仍保留审计记录</small></div>
                  </div>
                  <div className={`fund-holding-live-progress ${currentBatch.status === 'running' ? 'is-live' : ''}`}>
                    <div className="fund-holding-live-progress-head">
                      <div>
                        {currentBatch.status === 'running' ? <LoaderCircle size={15} className="is-spinning" /> : <CheckCircle2 size={15} />}
                        <span><strong>{currentBatch.status === 'running' ? '实时执行进度' : '任务处理进度'}</strong><small>按真实任务终态与子运行节点折算，不代表剩余时间</small></span>
                      </div>
                      <strong>{batchProgress}%</strong>
                    </div>
                    <div
                      className="fund-holding-progress"
                      role="progressbar"
                      aria-label={`持仓分析任务处理进度 ${batchProgress}%`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={batchProgress}
                    >
                      <i style={{ width: `${batchProgress}%` }} />
                    </div>
                    <div className="fund-holding-live-stats">
                      <span><i className="is-complete" />已处理 {Math.max(0, settledItems)}</span>
                      <span><i className="is-running" />研究中 {currentBatch.counts.running}</span>
                      <span><i className="is-queued" />排队 {currentBatch.counts.queued}</span>
                      <span>每 2–3 秒同步</span>
                    </div>
                  </div>
                  <div className="fund-holding-batch-actions">
                    {currentBatch.status === 'running' && <button type="button" onClick={() => onMutateHoldingBatch(currentBatch.batch_id, 'cancel')} disabled={holdingAction === holdingMutationKey('cancel', currentBatch.batch_id)}><Square size={13} />取消未完成任务</button>}
                    {currentBatch.counts.failed > 0 && currentBatch.status !== 'running' && <button type="button" onClick={() => onMutateHoldingBatch(currentBatch.batch_id, 'retry')} disabled={holdingAction === holdingMutationKey('retry', currentBatch.batch_id)}><RefreshCw size={13} />重试全部失败项</button>}
                    {currentBatch.counts.cancelled > 0 && currentBatch.status !== 'running' && <button type="button" onClick={() => onMutateHoldingBatch(currentBatch.batch_id, 'continue')} disabled={holdingAction === holdingMutationKey('continue', currentBatch.batch_id)}><RefreshCw size={13} />继续未完成项</button>}
                    {currentBatch.counts.deliverable > 0 && <button type="button" onClick={() => onDownloadZip(currentBatch.batch_id)} disabled={holdingAction === `zip:${currentBatch.batch_id}`}><Download size={13} />下载 PDF 报告包</button>}
                    {currentBatch.status !== 'running' && <button type="button" onClick={() => { setSelectedBatchId(''); if (effectiveReportProfile) onCreateHoldingBatch(String(candidate.fund_code || ''), effectiveReportProfile, confirmStale, true) }} disabled={!effectiveReportProfile || (requiresConfirmation && !confirmStale)}><RefreshCw size={13} />重新生成</button>}
                  </div>

                  <div className="fund-holding-report-list">
                    {currentBatch.holdings.map((item) => {
                      const progress = item.child_run_id ? holdingRunProgress[item.child_run_id] : undefined
                      return <article key={`${item.rank}-${item.symbol}`} className={`is-${item.status}`}>
                      <div className="fund-holding-report-rank">{String(item.rank).padStart(2, '0')}</div>
                      <div className="fund-holding-report-name"><strong>{item.name}</strong><small>{item.symbol} · {item.market} · 基金权重 {percent(item.fund_weight_pct, false)}</small></div>
                      <span className="fund-holding-report-status">{item.status === 'running' && <LoaderCircle size={12} className="is-spinning" />}{holdingItemStatusLabel(item.status)}</span>
                      <div className="fund-holding-report-actions">
                        <button type="button" title="使用该持仓股票打开策略回测" onClick={() => onOpenBacktest({ symbol: item.symbol, name: item.name, fundCode: String(candidate.fund_code || ''), fundName: String(candidate.fund_name || '') })}><LineChartIcon size={13} />回测</button>
                        {item.status === 'failed' && <button type="button" title="只重试这一只股票，其他持仓报告不重新执行" onClick={() => onMutateHoldingBatch(currentBatch.batch_id, 'retry', [item.symbol])} disabled={holdingAction === holdingMutationKey('retry', currentBatch.batch_id, [item.symbol])}><RefreshCw size={13} />仅重试此项</button>}
                        {item.status === 'cancelled' && <button type="button" title="只继续这一只股票，其他持仓报告不重新执行" onClick={() => onMutateHoldingBatch(currentBatch.batch_id, 'continue', [item.symbol])} disabled={holdingAction === holdingMutationKey('continue', currentBatch.batch_id, [item.symbol])}><RefreshCw size={13} />仅继续此项</button>}
                        {['completed', 'completed_partial'].includes(item.status) && <button type="button" title="只重新生成这一只股票，其他持仓报告保持不变" onClick={() => onMutateHoldingBatch(currentBatch.batch_id, 'regenerate', [item.symbol])} disabled={holdingAction === holdingMutationKey('regenerate', currentBatch.batch_id, [item.symbol])}><RefreshCw size={13} />重新生成此项</button>}
                        {item.child_run_id && <button type="button" title="打开完整证据链" onClick={() => onOpenRun(item.child_run_id || '')}><ExternalLink size={13} />证据</button>}
                        {item.report_available && item.child_run_id && <>
                          <button type="button" onClick={() => onDownloadReport(item.child_run_id || '', 'html')} disabled={holdingAction === `html:${item.child_run_id}`}><FileText size={13} />HTML</button>
                          <button type="button" onClick={() => onDownloadReport(item.child_run_id || '', 'pdf')} disabled={holdingAction === `pdf:${item.child_run_id}`}><Download size={13} />PDF</button>
                        </>}
                      </div>
                      {item.status === 'running' && <div className="fund-holding-item-progress" role="status">
                        <div><span>{progress?.currentObjective || '正在初始化执行图并读取冻结持仓证据'}</span><strong>{progress?.totalNodes ? `${progress.settledNodes} / ${progress.totalNodes} 节点` : '准备中'}</strong></div>
                        <div className="fund-holding-item-progress-track"><i style={{ width: `${progress?.percent ?? 0}%` }} /></div>
                        <small>{progress?.currentNodeId ? `当前节点 ${progress.currentNodeId} · ` : ''}已运行 {elapsedText(item.started_at, progressClock)}{progress ? ' · 刚刚同步' : ' · 等待首个节点状态'}</small>
                      </div>}
                      {item.status === 'queued' && <div className="fund-holding-item-queued"><span>等待可用并发槽位</span><small>当前并发上限 {currentBatch.concurrency}，启动后将显示真实节点进度</small></div>}
                      {(item.data_gap.length > 0 || item.error) && <p>{item.error ? localizeErrorMessage(item.error, '持仓股票报告执行失败，请稍后重试。') : item.data_gap.map(dataGapLabel).join('；')}</p>}
                    </article>})}
                  </div>

                  {currentBatch.data_gap.length > 0 && <div className="fund-holding-batch-gaps"><AlertCircle size={14} /><span>{currentBatch.data_gap.map(dataGapLabel).join('；')}</span></div>}
                </div>
              )}

              {holdingBatches.length > 1 && <details className="fund-holding-history">
                <summary>历史报告批次（{holdingBatches.length}）<ChevronDown size={14} /></summary>
                <div>{sortedBatches.map((batch) => <button type="button" className={batch.batch_id === currentBatch?.batch_id ? 'is-current' : ''} key={batch.batch_id} onClick={() => { setReportProfile(batch.profile); setSelectedBatchId(batch.batch_id) }}><strong>{REPORT_PROFILES.find((row) => row.value === batch.profile)?.label}</strong><span>{holdingBatchStatusLabel(batch.status)} · {batch.counts.deliverable}/{batch.counts.supported} · {String(batch.created_at).slice(0, 16).replace('T', ' ')}</span><ExternalLink size={12} /></button>)}</div>
              </details>}
            </>
          )}
        </section>

        <section className="fund-objective-section">
          <div><strong>投资目标</strong><p>{String(overview.investment_objective || '未返回')}</p></div>
          <div><strong>投资策略</strong><p>{String(overview.investment_strategy || '未返回')}</p></div>
        </section>

        {gaps.length > 0 && <section className="fund-gap-section"><header><AlertCircle size={15} /><h4>该基金全部数据缺口</h4><span>{gaps.length} 项</span></header><ul>{gaps.map((gap) => <li key={gap}>{dataGapLabel(gap)}</li>)}</ul></section>}
      </div>
    </details>
  )
}

function holdingMutationKey(action: 'cancel' | 'retry' | 'continue' | 'regenerate', batchId: string, symbols: string[] = []): string {
  return `${action}:${batchId}${symbols.length ? `:${symbols.join(',')}` : ''}`
}

function holdingReportEligibility(candidate: RecordValue, reportHoldings: RecordValue[]): { eligible: boolean; reason: string } {
  const identity = `${String(candidate.fund_name || '')} ${String(candidate.fund_type || '')}`.toLocaleLowerCase('zh-CN')
  if (["债券", "货币", "商品", "黄金"].some((token) => identity.includes(token))) {
    return { eligible: false, reason: '该基金不是股票权益类基金，本功能不会把非股票资产误送入个股研究。' }
  }
  const equityFocused = identity.includes('股票') || identity.includes('equity') || identity.includes('etf') || identity.includes('指数')
  if (!equityFocused) {
    return { eligible: false, reason: '当前基金类型未明确为股票、权益、指数或 ETF，暂不生成持仓股票报告。' }
  }
  if (!reportHoldings.length) {
    return { eligible: false, reason: '本次冻结的基金快照没有可审计的股票持仓；系统不会推断或模拟持仓。' }
  }
  return { eligible: true, reason: '' }
}

function dateAgeDays(value: string): number | null {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return null
  return Math.max(0, Math.floor((Date.now() - timestamp) / 86_400_000))
}

function extractDisclosureDate(value: string): string {
  const match = value.match(/(?<!\d)(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?!\d)/)
  if (match) {
    return `${match[1]}-${String(Number(match[2])).padStart(2, '0')}-${String(Number(match[3])).padStart(2, '0')}`
  }
  const quarter = value.match(/(?<!\d)(\d{4})\s*年?\s*(?:第\s*)?([1-4一二三四])\s*(?:季度|季)/i)
    || value.match(/(?<!\d)(\d{4})\s*年?\s*[Qq]([1-4])(?!\d)/)
  if (!quarter) return ''
  const quarterNumber = ({ 一: 1, 二: 2, 三: 3, 四: 4 } as Record<string, number>)[quarter[2]] || Number(quarter[2])
  const month = quarterNumber * 3
  const lastDay = new Date(Date.UTC(Number(quarter[1]), month, 0)).getUTCDate()
  return `${quarter[1]}-${String(month).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`
}

function firstDisclosureDate(...values: unknown[]): string {
  for (const value of values) {
    const parsed = extractDisclosureDate(String(value || ''))
    if (parsed) return parsed
  }
  return ''
}

function holdingProgressFromSteps(payload: RunStepsPayload): HoldingRunProgress {
  const steps = Array.isArray(payload.steps) ? payload.steps : []
  const terminalStatuses = new Set(['completed', 'failed', 'skipped', 'cancelled'])
  const settledNodes = steps.filter((step) => terminalStatuses.has(String(step.status || ''))).length
  const activeSteps = steps.filter((step) => step.status === 'running')
  const currentStep = activeSteps[0] || steps.find((step) => ['pending', 'waiting'].includes(String(step.status || '')))
  const totalNodes = steps.length
  return {
    currentNodeId: String(currentStep?.node_id || ''),
    currentObjective: String(currentStep?.objective || '正在推进当前执行节点'),
    percent: totalNodes > 0 ? Math.min(96, Math.round(settledNodes / totalNodes * 100)) : null,
    settledNodes,
    totalNodes,
  }
}

function holdingBatchProgress(
  batch: FundHoldingReportBatch,
  progressByRun: Record<string, HoldingRunProgress>,
): number {
  const supported = batch.holdings.filter((item) => item.supported)
  if (!supported.length) return 100
  const terminalStatuses = new Set(['completed', 'completed_partial', 'failed', 'cancelled'])
  const total = supported.reduce((sum, item) => {
    if (terminalStatuses.has(item.status)) return sum + 100
    if (item.status !== 'running' || !item.child_run_id) return sum
    return sum + (progressByRun[item.child_run_id]?.percent || 0)
  }, 0)
  return Math.min(100, Math.round(total / supported.length))
}

function elapsedText(startedAt: string | null, now: number): string {
  const started = Date.parse(String(startedAt || ''))
  if (!Number.isFinite(started)) return '不到 1 分钟'
  const seconds = Math.max(0, Math.floor((now - started) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分钟`
}

function holdingBatchStatusLabel(value: FundHoldingReportBatch['status']): string {
  return ({
    running: '执行中',
    completed: '全部完成',
    completed_partial: '部分完成',
    failed: '执行失败',
    cancelled: '已取消',
  } as const)[value]
}

function holdingItemStatusLabel(value: FundHoldingReportBatch['holdings'][number]['status']): string {
  return ({
    queued: '排队中',
    running: '研究中',
    completed: '已完成',
    completed_partial: '部分完成',
    failed: '失败',
    cancelled: '已取消',
    unsupported: '市场不支持',
  } as const)[value]
}

function reportErrorMessage(error: unknown): string {
  if (error instanceof CognitradeApiError) {
    if (error.code === 'stale_holdings_confirmation_required') return '持仓披露超过 180 天，请确认历史披露风险后再生成。'
    return errorMessageWithRequestId(error.message, error.requestId)
  }
  return localizeErrorMessage(error)
}

function saveReportExport(exported: RunReportExport): void {
  const url = URL.createObjectURL(exported.blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = exported.filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
