import { Archive, CheckCircle2, CircleDashed, Clock3, FileLock2, ShieldAlert } from 'lucide-react'
import type { CollectionJob, MarketDataContract, MarketDataCoverage } from './types'
import { shortHash } from './status'
import { StatusBadge } from './shared'

export function ReadinessSummary({ contract, coverage, fundJob }: { contract: MarketDataContract; coverage: MarketDataCoverage; fundJob?: CollectionJob | null }) {
  const isFund = contract.asset_family === 'fund'
  const parentReady = coverage.counts.available + coverage.counts.verified_empty
  const resolvedCount = coverage.resolved_dimension_count || parentReady
  const parentExpected = coverage.expected_dimension_count || contract.expected_dimension_count
  const fallbackMissing = fundJob?.expanded_holdings.reduce((sum, holding) => sum + (holding.missing_dimensions?.length || 0), 0) || 0
  const portfolio = fundJob?.portfolio_summary
  const ready = isFund ? (portfolio?.ready_holdings || 0) : resolvedCount
  const expected = isFund ? (portfolio?.total_holdings || 10) : parentExpected
  const progress = expected ? Math.round((ready / expected) * 100) : 0
  const missingSlots = portfolio?.missing_slots ?? fallbackMissing
  const totalSlots = portfolio?.total_slots || ((portfolio?.total_holdings || 10) * 22)
  const readySlots = portfolio?.ready_slots ?? Math.max(0, totalSlots - missingSlots)
  const portfolioReady = Boolean(fundJob && ready === expected && missingSlots === 0 && ['completed', 'succeeded'].includes(fundJob.status))
  const reportGeneratable = coverage.report_ready || coverage.report_generatable
  return (
    <section className="mdc-readiness" aria-labelledby="mdc-readiness-title">
      <div className="mdc-readiness-primary">
        <div className={`mdc-readiness-ring ${(isFund ? portfolioReady : reportGeneratable) ? 'is-ready' : ''}`} style={{ '--readiness': `${progress * 3.6}deg` } as React.CSSProperties}>
          <span><strong>{ready}</strong><small>/ {expected}</small></span>
        </div>
        <div>
          <span className="mdc-eyebrow">UZI 3.9.4 DATA GATE</span>
          <h2 id="mdc-readiness-title">{coverage.symbol} · {coverage.as_of_date}</h2>
          <p>{isFund ? `截止 ${coverage.cutoff_at || '市场本地日终'}，主指标按前十大股票的日级 Bundle 计算。` : `22 槽完整即可冻结为 Agent 输入；UZI 原生 medium 数据预检用于触发补数，最终仅由完整 Agent 工作流的 self-review 决定能否出报告。`}</p>
        </div>
      </div>
      <div className="mdc-readiness-gates">
        {isFund ? <>
          <article>
            {coverage.report_ready ? <CheckCircle2 size={18} /> : <CircleDashed size={18} />}
            <span>父持仓入口</span>
            <StatusBadge label={coverage.report_ready ? `${parentReady}/${parentExpected} · Bundle 可用` : `${parentReady}/${parentExpected} · 未就绪`} tone={coverage.report_ready ? 'good' : 'warning'} />
          </article>
          <article>
            {portfolioReady ? <FileLock2 size={18} /> : <ShieldAlert size={18} />}
            <span>组合报告就绪</span>
            <StatusBadge label={portfolioReady ? '可冻结' : `${ready}/${expected} 只股票`} tone={portfolioReady ? 'good' : 'danger'} />
          </article>
          <article>
            <Archive size={18} />
            <span>股票契约槽</span>
            <strong>{readySlots} / {totalSlots}</strong>
          </article>
          <article>
            <Clock3 size={18} />
            <span>缺口处置</span>
            <strong>{portfolio ? `可重试 ${portfolio.retryable_slots} · 阻断 ${portfolio.blocked_slots}` : '等待组合任务'}</strong>
          </article>
        </> : <>
          <article>
            {coverage.structurally_complete ? <CheckCircle2 size={18} /> : <CircleDashed size={18} />}
            <span>结构完整</span>
            <StatusBadge label={coverage.structurally_complete ? '通过' : '未通过'} tone={coverage.structurally_complete ? 'good' : 'warning'} />
          </article>
          <article>
            {reportGeneratable ? <FileLock2 size={18} /> : <ShieldAlert size={18} />}
            <span>UZI 数据预检</span>
            <StatusBadge
              label={coverage.report_ready ? `通过 · ${coverage.uzi_integrity?.coverage_pct ?? 0}%` : reportGeneratable ? `待 Agent 修复 · ${coverage.uzi_integrity?.critical_count ?? 0} 项` : '采集槽未完整'}
              tone={coverage.report_ready ? 'good' : reportGeneratable ? 'warning' : 'danger'}
            />
          </article>
        <article>
          <Archive size={18} />
          <span>最新 Bundle</span>
          <strong title={coverage.latest_bundle?.manifest_hash || undefined}>{coverage.latest_bundle ? shortHash(coverage.latest_bundle.manifest_hash || coverage.latest_bundle.bundle_id) : '尚未发布'}</strong>
        </article>
        <article>
          <Clock3 size={18} />
          <span>审计状态</span>
          <strong>{coverage.audit_ready ? '全部通过' : `${coverage.audit_ready_dimension_count || 0}/${parentExpected} · 不阻断`}</strong>
        </article>
        </>}
      </div>
    </section>
  )
}
