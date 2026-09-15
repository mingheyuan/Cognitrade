import { Archive, CheckCircle2, CircleDashed, Clock3, FileLock2, ShieldAlert } from 'lucide-react'
import type { CollectionJob, MarketDataContract, MarketDataCoverage } from './types'
import { shortHash } from './status'
import { StatusBadge } from './shared'
import { fundPortfolioSummary } from './portfolio'

export function ReadinessSummary({ contract, coverage, fundJob }: { contract: MarketDataContract; coverage: MarketDataCoverage; fundJob?: CollectionJob | null }) {
  const isFund = contract.asset_family === 'fund'
  const parentReady = coverage.counts.available + coverage.counts.verified_empty
  const parentExpected = coverage.expected_dimension_count || contract.expected_dimension_count
  const fallbackMissing = fundJob?.expanded_holdings.reduce((sum, holding) => sum + (holding.missing_dimensions?.length || 0), 0) || 0
  const portfolio = fundJob ? fundPortfolioSummary(fundJob) : undefined
  const ready = isFund ? (portfolio?.ready_holdings || 0) : parentReady
  const expected = isFund ? (portfolio?.total_holdings || 10) : parentExpected
  const progress = expected ? Math.round((ready / expected) * 100) : 0
  const missingSlots = portfolio?.missing_slots ?? fallbackMissing
  const totalSlots = portfolio?.total_slots || ((portfolio?.total_holdings || 10) * 22)
  const readySlots = portfolio?.ready_slots ?? 0
  const strictlyComplete = Boolean(fundJob && ready === expected && missingSlots === 0 && ['completed', 'succeeded'].includes(fundJob.status))
  const portfolioFrozen = fundJob?.freeze_status === 'frozen'
  const portfolioReady = Boolean(portfolioFrozen || fundJob?.can_confirm_freeze || ['ready', 'ready_degraded', 'awaiting_confirmation'].includes(String(fundJob?.freeze_status || '').toLowerCase()))
  const reportGeneratable = coverage.report_ready || coverage.report_generatable
  const pendingInputs = coverage.company_profile_dependencies?.can_freeze === false
  return (
    <section className="mdc-readiness" aria-labelledby="mdc-readiness-title">
      <div className="mdc-readiness-primary">
        <div className={`mdc-readiness-ring ${(isFund ? strictlyComplete : reportGeneratable && !pendingInputs) ? 'is-ready' : ''}`} style={{ '--readiness': `${progress * 3.6}deg` } as React.CSSProperties}>
          <span><strong>{ready}</strong><small>/ {expected}</small></span>
        </div>
        <div>
          <span className="mdc-eyebrow">UZI 3.9.4 DATA GATE</span>
          <h2 id="mdc-readiness-title">{coverage.symbol} · {coverage.as_of_date}</h2>
          <p>{isFund ? `截止 ${coverage.cutoff_at || '市场本地日终'}，核查前十大股票的日级数据；候选需确认才冻结 Bundle。` : '22 槽采集落库后形成数据冻结候选；需要补采时显式启动任务。'} 数据与研究引用缺口作为提示交给完整 Agent，不单独禁止报告启动。冻结 hash、来源权限和 PIT 安全校验独立执行；最终报告按原生 UZI 自审判定。</p>
        </div>
      </div>
      <div className="mdc-readiness-gates">
        {pendingInputs && <article>
          <ShieldAlert size={18} /><span>输入版本一致性</span>
          <StatusBadge label="输入版本待补齐" tone="warning" />
          <small>当前草稿暂不能冻结；待重建 {coverage.company_profile_dependencies?.pending_dimensions.join('、') || '公司信息依赖'}。此项独立于 UZI 预检，不影响已有冻结版本。</small>
        </article>}
        {isFund ? <>
          <article>
            {coverage.report_ready ? <CheckCircle2 size={18} /> : <CircleDashed size={18} />}
            <span>父持仓入口</span>
            <StatusBadge label={coverage.report_ready ? `${parentReady}/${parentExpected} · 数据可用` : `${parentReady}/${parentExpected} · 未就绪`} tone={coverage.report_ready ? 'good' : 'warning'} />
          </article>
          <article>
            {portfolioReady ? <FileLock2 size={18} /> : <ShieldAlert size={18} />}
            <span>组合 Bundle</span>
            <StatusBadge label={portfolioFrozen ? '已冻结' : portfolioReady ? missingSlots ? '可降级冻结' : '待确认冻结' : `${ready}/${expected} 只严格完整`} tone={portfolioFrozen ? 'good' : portfolioReady ? 'info' : 'danger'} />
          </article>
          <article>
            <Archive size={18} />
            <span>股票严格槽</span>
            <strong>{readySlots} / {totalSlots}</strong>
          </article>
          <article>
            <Clock3 size={18} />
            <span>缺口处置</span>
            <strong>{portfolio ? `可重试 ${portfolio.retryable_slots} · 不可重试/已降级 ${portfolio.blocked_slots}${portfolio.unassessed_slots ? ` · 未评估 ${portfolio.unassessed_slots}` : ''}` : '等待组合任务'}</strong>
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
              label={coverage.report_ready ? `通过 · ${coverage.uzi_integrity?.coverage_pct ?? 0}%` : reportGeneratable ? `存在缺口 · ${coverage.uzi_integrity?.critical_count ?? 0} 项关键问题` : '采集槽未完整'}
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
          <strong>{coverage.audit_ready ? '全部通过' : `${coverage.audit_ready_dimension_count || 0}/${parentExpected} · 独立核查`}</strong>
        </article>
        </>}
        <article>
          <CircleDashed size={18} />
          <span>最终报告自审</span>
          <strong>报告生成后独立判定</strong>
        </article>
      </div>
    </section>
  )
}
