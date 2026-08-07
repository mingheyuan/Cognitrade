import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  CircleAlert,
  Gauge,
  LineChart,
  ShieldAlert,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { businessFieldLabel } from '../artifacts/labels'
import { asNumber, asRecord, asString, asStringList } from '../live-data'
import type { ResearchReport } from '../research-contracts'

type ResearchReportPresentationProps = {
  report: ResearchReport
}

function numberText(value: unknown, digits = 2): string {
  const number = asNumber(value)
  return number === null ? '—' : number.toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

function moneyText(value: unknown, symbol: string): string {
  const formatted = numberText(value)
  return formatted === '—' ? formatted : `${symbol}${formatted}`
}

function percentText(value: unknown, digits = 1): string {
  const number = asNumber(value)
  return number === null ? '—' : `${number.toFixed(digits)}%`
}

function ratioPercentText(value: unknown, digits = 1): string {
  const number = asNumber(value)
  return number === null ? '—' : `${(number * 100).toFixed(digits)}%`
}

function verdictLabel(value: string): string {
  return {
    below_proxy_value: '代理价值高于现价',
    above_proxy_value: '代理价值低于现价',
    near_proxy_value: '代理价值接近现价',
    data_gap: '数据缺口',
    assumption_only: '假设敏感',
    low_observed: '低观察风险',
    attention: '需要关注',
    high: '高风险信号',
  }[value] || value || '未返回'
}

function signalLabel(value: string): string {
  return {
    bullish: '偏多',
    bearish: '偏空',
    neutral: '中性',
    skip: '不适用',
    insufficient_evidence: '证据不足',
  }[value] || value || '未返回'
}

function signalClass(value: string): string {
  if (value === 'bullish') return 'is-bullish'
  if (value === 'bearish') return 'is-bearish'
  return 'is-neutral'
}

function statusText(value: unknown, fallback = 'data_gap'): string {
  return asString(value, fallback)
}

function AdvancedSummary({
  label,
  status,
  summary,
  detail,
}: {
  label: string
  status: string
  summary: string
  detail: string
}) {
  return (
    <article className="research-presentation-advanced-card">
      <div className="research-presentation-advanced-card__topline">
        <span>{label}</span>
        <span className={`research-presentation-status ${status === 'available' ? 'is-available' : 'is-gap'}`}>
          {status === 'available' ? <CheckCircle2 size={12} /> : <CircleAlert size={12} />}
          {verdictLabel(status)}
        </span>
      </div>
      <strong>{summary}</strong>
      <small>{detail}</small>
    </article>
  )
}

export function ResearchReportPresentation({ report }: ResearchReportPresentationProps) {
  const presentation = asRecord(report.presentation)
  const hasFinancePresentation = Object.keys(presentation).length > 0
    || Object.keys(asRecord(report.valuation_model)).length > 0
    || Object.keys(asRecord(report.investor_panel_review)).length > 0
  if (!hasFinancePresentation) return null

  const subject = asRecord(presentation.subject)
  const hero = asRecord(presentation.hero)
  const dashboard = asRecord(presentation.dashboard)
  const price = asRecord(dashboard.price)
  const valuation = asRecord(dashboard.valuation)
  const panel = asRecord(presentation.panel)
  const panelRoleplay = asRecord(panel.roleplay)
  const agentRoleplayCompleted = panelRoleplay.status === 'completed' && panelRoleplay.agent_reviewed === true
  const greatDivide = asRecord(presentation.great_divide)
  const profile = asString(presentation.profile, '未记录')
  const valuationModel = asRecord(report.valuation_model)
  const valuationModels = Array.isArray(valuationModel.models)
    ? valuationModel.models.map((model) => asRecord(model))
    : []
  const primaryModel = valuationModels[0] || {}
  const advanced = asRecord(presentation.advanced_methods)
  const dataPipeline = asRecord(presentation.data_pipeline || report.uzi_data_pipeline)
  const integrity = asRecord(dataPipeline.uzi_integrity)
  const integrityBefore = asRecord(integrity.before_recovery || integrity)
  const integrityAfter = asRecord(integrity.after_recovery || integrity)
  const recovery = asRecord(dataPipeline.uzi_recovery)
  const qualitativeStage = asRecord(recovery.qualitative_stage)
  const playwrightStage = asRecord(recovery.playwright_stage)
  const integritySymbols = Array.isArray(integrityAfter.symbols) ? integrityAfter.symbols.map(asRecord) : []
  const recoveryAttempts = [
    ...(Array.isArray(qualitativeStage.attempts) ? qualitativeStage.attempts.map(asRecord) : []),
    ...(Array.isArray(playwrightStage.attempts) ? playwrightStage.attempts.map(asRecord) : []),
  ]
  const institutional = asRecord(advanced.institutional_analysis)
  const workflow = asRecord(advanced.research_workflow)
  const deep = asRecord(advanced.deep_analysis)
  const dimensions = Array.isArray(presentation.radar)
    ? presentation.radar.map(asRecord)
    : []
  const deepScan = Array.isArray(presentation.deep_scan)
    ? presentation.deep_scan.map(asRecord)
    : dimensions
  const panelRounds = Array.isArray(greatDivide.rounds)
    ? greatDivide.rounds.map(asRecord)
    : []
  const panelInsights = Array.isArray(presentation.panel_insights)
    ? presentation.panel_insights.map(asRecord)
    : []
  const institutionalModeling = Array.isArray(presentation.institutional_modeling)
    ? presentation.institutional_modeling.map(asRecord)
    : []
  const buyZones = Array.isArray(presentation.buy_zones)
    ? presentation.buy_zones.map(asRecord)
    : []
  const metricValues = Array.isArray(subject.metrics)
    ? subject.metrics
    : Array.isArray(dashboard.metrics)
      ? dashboard.metrics
      : []
  const metrics = metricValues.map((metric) => asRecord(metric))
  const votes = Array.isArray(panel.votes) ? panel.votes.map(asRecord) : []
  const riskValues = Array.isArray(presentation.risk_list) && presentation.risk_list.length
    ? presentation.risk_list
    : asStringList(report.risk).map((finding) => ({ finding, status: 'data_gap' }))
  const risks = riskValues.map((risk) => asRecord(risk))
  const catalysts = Array.isArray(dashboard.catalysts) ? dashboard.catalysts.map(asRecord) : []
  const topBull = asRecord(greatDivide.bull && asRecord(greatDivide.bull).top_vote)
  const topBear = asRecord(greatDivide.bear && asRecord(greatDivide.bear).top_vote)
  const symbol = asString(subject.symbol || hero.symbol || primaryModel.symbol, '未识别标的')
  const name = asString(subject.name || hero.name, symbol)
  const sector = asString(subject.sector || hero.sector, '行业未提供')
  const currentPrice = asNumber(subject.price ?? hero.price ?? price.current ?? primaryModel.market_price)
  const currencySymbol = asString(
    subject.currency_symbol
      || hero.currency_symbol
      || price.currency_symbol
      || valuation.currency_symbol
      || primaryModel.currency_symbol
      || presentation.currency_symbol,
    '',
  )
  const change = asNumber(subject.change_pct ?? hero.change_pct ?? price.change_pct)
  const score = asNumber(hero.score ?? panel.average_score)
  const verdict = statusText(hero.verdict || primaryModel.verdict)
  const safety = statusText(hero.safety || asRecord(report.risk_scan).risk_level)
  const coreConclusion = asString(presentation.core_conclusion || report.conclusion, '后端未返回核心结论。')
  const dataGap = Array.isArray(presentation.data_gap) ? presentation.data_gap.map(String) : []
  const panelDistribution = asRecord(panel.distribution)
  const bullCount = asNumber(greatDivide.bull && asRecord(greatDivide.bull).count) ?? asNumber(panelDistribution.bullish) ?? 0
  const bearCount = asNumber(greatDivide.bear && asRecord(greatDivide.bear).count) ?? asNumber(panelDistribution.bearish) ?? 0

  return (
    <section className="research-presentation" aria-label="结构化研究报告">
      <div className="research-presentation__eyebrow">
        <span>REPORT PRESENTATION / {profile.toUpperCase()}</span>
        <span>{asString(subject.as_of, '数据日期由来源证据决定')}</span>
      </div>

      <div className="research-presentation__hero-grid">
        <article className="research-presentation__identity">
          <span className="research-presentation__label">// SUBJECT</span>
          <h2>{name}</h2>
          <p>{symbol} · {sector}</p>
          <div className="research-presentation__price-row">
            <strong>{moneyText(currentPrice, currencySymbol)}</strong>
            <span className={change === null ? '' : change >= 0 ? 'is-up' : 'is-down'}>
              {change === null ? '涨跌未提供' : `${change >= 0 ? '+' : ''}${percentText(change)}`}
            </span>
          </div>
          <div className="research-presentation__chip-row">
            {metrics.map((metric, index) => (
              <span key={asString(metric.key, String(index))}>
                <b>{asString(metric.label, '指标')}</b>{numberText(metric.value)}{asString(metric.unit)}
              </span>
            ))}
          </div>
        </article>

        <article className="research-presentation__score">
          <span className="research-presentation__label">ALPHA SCORE</span>
          <strong>{score === null ? '—' : numberText(score, 1)}</strong>
          <p>{score === null ? '暂无 Panel 平均分' : `${bullCount} 派看多 · ${bearCount} 派看空`}</p>
          <small>{agentRoleplayCompleted ? 'Agent 依据冻结证据与 UZI persona 逐席评审；不代表真实投资人意见' : '规则化方法论映射，不代表真实投资人意见'}</small>
        </article>

        <article className={`research-presentation__safety ${safety === 'low_observed' ? 'is-safe' : 'is-caution'}`}>
          <ShieldAlert size={19} />
          <span className="research-presentation__label">RESEARCH SAFETY</span>
          <strong>{verdictLabel(safety)}</strong>
          <p>{dataGap.length ? `${dataGap.length} 项输入仍需核验` : '当前报告未标记额外数据缺口'}</p>
        </article>
      </div>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>01 / CORE</span>
          <h3>核心结论</h3>
          <i />
        </div>
        <div className="research-presentation__core-grid">
          <article className="research-presentation__conclusion">
            <span>// ONE-SHOT CONCLUSION</span>
            <p>{coreConclusion}</p>
          </article>
          <div className="research-presentation__metric-grid">
            {metrics.map((metric, index) => (
              <article key={asString(metric.key, String(index))} className={statusText(metric.status) === 'data_gap' ? 'is-gap' : ''}>
                <span>{asString(metric.label, '研究指标')}</span>
                <strong>{numberText(metric.value)}{asString(metric.unit)}</strong>
                <small>{asString(metric.source, statusText(metric.status) === 'data_gap' ? '数据缺口' : '来源已记录')}</small>
              </article>
            ))}
          </div>
        </div>
        <div className="research-presentation__signal-grid">
          <article>
            <span><AlertTriangle size={14} /> 关键风险</span>
            <p>{risks.map((risk, index) => `${index + 1}. ${asString(risk.finding || risk.recommendation, '未返回风险说明')}`).join(' ') || '后端未返回风险清单。'}</p>
          </article>
          <article>
            <span><Sparkles size={14} /> 催化剂与事件</span>
            <p>{catalysts.length ? catalysts.map((event) => asString(event.title, '未命名事件')).join(' · ') : '暂无已结构化的催化剂日历。'}</p>
          </article>
        </div>
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>02 / CLASH</span>
          <h3>方法论分歧</h3>
          <i />
        </div>
        <div className="research-presentation__divide-grid">
          <article className="research-presentation__side is-bullish">
            <div><TrendingUp size={17} /><span>支持条件</span><strong>{bullCount}</strong></div>
            <p>{asString(topBull.headline || topBull.reasoning, '暂无偏多方法论观点。')}</p>
            <small>{asString(topBull.perspective_label, '方法论席位')}</small>
          </article>
          <article className="research-presentation__side is-bearish">
            <div><TrendingDown size={17} /><span>谨慎条件</span><strong>{bearCount}</strong></div>
            <p>{asString(topBear.headline || topBear.reasoning, '暂无偏空方法论观点。')}</p>
            <small>{asString(topBear.perspective_label, '方法论席位')}</small>
          </article>
        </div>
        <div className="research-presentation__punchline">
          <Target size={16} />
          <span>{asString(greatDivide.punchline, '不同方法论视角需要结合证据来源共同复核。')}</span>
        </div>
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>03 / JURY</span>
          <h3>评委评分板</h3>
          <i />
          <small>{asNumber(panel.registered_seat_count) ?? votes.length} 个方法论席位 · {panelRoleplay.required === true ? agentRoleplayCompleted ? 'Deep Agent 评审完成' : 'Deep Agent 评审未完成' : '规则评审'}</small>
        </div>
        {votes.length ? (
          <div className="research-presentation__vote-grid">
            {votes.map((vote, index) => {
              const signal = asString(vote.signal, 'neutral')
              const voteScore = asNumber(vote.score)
              const scoreText = signal === 'skip' ? '不适用' : voteScore === null ? '—' : numberText(voteScore, 1)
              return (
                <article key={asString(vote.perspective_id, String(index))} className={signalClass(signal)}>
                  <div><span>{asString(vote.perspective_label, '方法论视角')}</span><strong>{scoreText}</strong></div>
                  <div className="research-presentation__vote-track"><i style={{ width: `${Math.max(0, Math.min(100, voteScore ?? 0))}%` }} /></div>
                  <small>{signalLabel(signal)} · {vote.agent_roleplay_applied === true ? 'Agent 角色评审' : '规则参考'} · {asString(vote.headline || vote.reasoning, '未返回解释')}</small>
                </article>
              )
            })}
          </div>
        ) : <div className="research-presentation__empty"><Gauge size={17} />后端未返回方法论评分。</div>}
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>04 / DEEP SCAN</span>
          <h3>研究维度覆盖</h3>
          <i />
          <small>{deepScan.length} 个来源与派生维度</small>
        </div>
        {deepScan.length ? (
          <div className="research-presentation__radar-grid">
            {deepScan.map((dimension, index) => {
              const dimensionScore = asNumber(dimension.score)
              const dimensionStatus = statusText(dimension.status)
              return (
                <article key={asString(dimension.dimension_id, String(index))} className={dimensionStatus === 'data_gap' ? 'is-gap' : ''}>
                  <div><span>{asString(dimension.label, '研究维度')} · {asString(dimension.layer, 'source')}</span><strong>{dimensionScore === null ? verdictLabel(dimensionStatus) : numberText(dimensionScore, 1)}</strong></div>
                  <div className="research-presentation__radar-track"><i style={{ width: `${dimensionScore === null ? 0 : Math.max(0, Math.min(100, dimensionScore * 10))}%` }} /></div>
                  <small>{dimensionStatus === 'data_gap' ? asStringList(dimension.data_gap).join(' · ') || '数据缺口' : asStringList(dimension.evidence).join(' · ') || asStringList(dimension.source_refs).join(' · ') || '证据已提供'}</small>
                </article>
              )
            })}
          </div>
        ) : <div className="research-presentation__empty"><BarChart3 size={17} />暂无结构化维度覆盖。</div>}
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>05 / DATA RECOVERY</span>
          <h3>数据完整性与真实兜底</h3>
          <i />
          <small>{recoveryAttempts.length} 次兜底尝试</small>
        </div>
        <div className="research-presentation__advanced-grid">
          <AdvancedSummary
            label="主数据采集"
            status={statusText(dataPipeline.quality_status)}
            summary={`${asNumber(asRecord(dataPipeline.quality_counts).available) ?? 0} 个可用快照`}
            detail={asStringList(dataPipeline.degrade_reasons).join(' · ') || '主数据源未标记降级'}
          />
          <AdvancedSummary
            label="恢复前完整性"
            status={integrityBefore.critical_missing ? 'data_gap' : 'available'}
            summary={`覆盖率 ${numberText(integrityBefore.coverage_pct, 1)}%`}
            detail={`${asNumber(integrityBefore.recovery_task_count) ?? 0} 个恢复任务`}
          />
          <AdvancedSummary
            label="恢复后完整性"
            status={integrityAfter.critical_missing ? 'data_gap' : 'available'}
            summary={`覆盖率 ${numberText(integrityAfter.coverage_pct, 1)}%`}
            detail={integrityAfter.ok ? '完整性门已通过' : '仍有真实数据缺口'}
          />
          <AdvancedSummary
            label="MX → DDGS → Playwright"
            status={(asNumber(recovery.failed) ?? 0) > 0 ? 'data_gap' : 'available'}
            summary={`成功 ${asNumber(recovery.succeeded) ?? 0} / 尝试 ${asNumber(recovery.attempted) ?? 0}`}
            detail={asStringList(recovery.stage_order).join(' → ') || '按 UZI 3.9.2 顺序执行'}
          />
        </div>
        {integritySymbols.length > 0 && (
          <div className="research-presentation__institutional-grid">
            {integritySymbols.map((item, index) => {
              const critical = Array.isArray(item.missing_critical) ? item.missing_critical.map(asRecord) : []
              const optional = Array.isArray(item.missing_optional) ? item.missing_optional.map(asRecord) : []
              const enrichment = Array.isArray(item.missing_enrichment) ? item.missing_enrichment.map(asRecord) : []
              const tasks = Array.isArray(item.recovery_tasks) ? item.recovery_tasks.map(asRecord) : []
              return (
                <article key={asString(item.symbol, String(index))} className={`research-presentation__method-card ${item.ok ? '' : 'is-gap'}`}>
                  <div className="research-presentation__method-card__head">
                    <span>{asString(item.symbol, '标的')} · 完整性检查</span>
                    <strong>{numberText(item.coverage_pct, 1)}%</strong>
                  </div>
                  <p>关键缺口：{critical.map((row) => asString(row.label || row.path)).join('、') || '无'}</p>
                  <small>可选缺口：{optional.map((row) => asString(row.label || row.path)).join('、') || '无'}</small>
                  <small>扩展维度：{enrichment.map((row) => asString(row.label || row.dim)).join('、') || '无'}</small>
                  {tasks.length > 0 && <small className="research-presentation__method-card__gap">恢复建议：{tasks.map((task) => `${asString(task.label || task.field)}（${asStringList(task.suggested_actions).join(' / ')}）`).join(' · ')}</small>}
                </article>
              )
            })}
          </div>
        )}
        {recoveryAttempts.length > 0 && (
          <div className="research-presentation__institutional-grid">
            {recoveryAttempts.map((attempt, index) => {
              const trace = Array.isArray(attempt.source_trace) ? attempt.source_trace.map(asRecord) : []
              return (
                <article key={`${asString(attempt.symbol, 'global')}-${asString(attempt.snapshot_type, 'dimension')}-${index}`} className={`research-presentation__method-card ${statusText(attempt.status) === 'available' ? '' : 'is-gap'}`}>
                  <div className="research-presentation__method-card__head">
                    <span>{asString(attempt.symbol, '全局')} · {businessFieldLabel(asString(attempt.snapshot_type, 'dimension'))}</span>
                    <strong>{verdictLabel(statusText(attempt.status))}</strong>
                  </div>
                  <p>{asString(attempt.query || attempt.reason, '按数据质量阈值触发兜底')}</p>
                  <small>来源：{asString(attempt.source_id) || asStringList(attempt.source_ids).join('、') || '未取得'}</small>
                  <small>链路：{trace.map((row) => `${asString(row.strategy_id || row.source_id, 'source')}:${asString(row.status, 'unknown')}`).join(' → ') || '无可用来源返回'}</small>
                </article>
              )
            })}
          </div>
        )}
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>06 / PANEL INSIGHTS</span>
          <h3>派系评审洞察</h3>
          <i />
          <small>{panelInsights.length} 个方法论席位</small>
        </div>
        {panelRounds.length ? (
          <div className="research-presentation__round-grid">
            {panelRounds.map((round, index) => (
              <article key={asString(round.round_id, String(index))} className="research-presentation__round-card">
                <div className="research-presentation__round-card__head">
                  <span>{asString(round.label, '方法论派系')}</span>
                  <strong>{asNumber(round.average_score) === null ? '—' : numberText(round.average_score, 1)}</strong>
                </div>
                <small>{asNumber(round.seat_count) ?? 0} 席 · 偏多 {asNumber(round.bullish_count) ?? 0} · 偏空 {asNumber(round.bearish_count) ?? 0} · 中性 {asNumber(round.neutral_count) ?? 0} · 不适用 {asNumber(round.skip_count) ?? 0}</small>
                <p>{asString(asRecord(round.bull_case).headline || asRecord(round.bull_case).reasoning, '该派系没有偏多席位。')}</p>
                <p className="is-bearish-text">{asString(asRecord(round.bear_case).headline || asRecord(round.bear_case).reasoning, '该派系没有偏空席位。')}</p>
              </article>
            ))}
          </div>
        ) : null}
        {panelInsights.length ? (
          <div className="research-presentation__insight-grid">
            {panelInsights.map((insight, index) => {
              const signal = asString(insight.signal, 'insufficient_evidence')
              return (
                <article key={asString(insight.perspective_id, String(index))} className={signalClass(signal)}>
                  <div className="research-presentation__insight-card__head">
                    <span>{asString(insight.perspective_label, '方法论席位')}</span>
                    <strong>{signal === 'skip' ? '不适用' : numberText(insight.score, 1)}</strong>
                  </div>
                  <small>{asString(insight.school_label, '方法论派系')} · {signalLabel(signal)} · 置信度 {percentText(asNumber(insight.confidence) === null ? null : asNumber(insight.confidence)! * 100)}</small>
                  <p>{asString(insight.headline || insight.reasoning, '未返回评审解释。')}</p>
                  <small>方法来源：{asString(insight.methodology_source, 'UZI 3.9.2 规则库')}</small>
                  <small>观察周期：{asString(insight.time_horizon, '—')}</small>
                  <small>仓位方法：{asString(insight.position_sizing, '—')}</small>
                  <small>改变判断：{asString(insight.what_would_change_my_mind, '—')}</small>
                </article>
              )
            })}
          </div>
        ) : <div className="research-presentation__empty"><Gauge size={17} />后端未返回派系评审洞察。</div>}
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>07 / MODELING</span>
          <h3>机构模型与研究工作流</h3>
          <i />
        </div>
        <div className="research-presentation__advanced-grid">
          <AdvancedSummary
            label="机构估值模型"
            status={statusText(institutional.status)}
            summary={asString(institutional.summary, '机构模型尚未返回摘要')}
            detail={asStringList(institutional.data_gap).join(' · ') || '方法输出已写入 ArtifactBoard'}
          />
          <AdvancedSummary
            label="研究工作流"
            status={statusText(workflow.status)}
            summary={asString(workflow.summary, '工作流尚未返回摘要')}
            detail={asStringList(workflow.risk_flags).join(' · ') || '覆盖业绩、催化剂和 Thesis'}
          />
          <AdvancedSummary
            label="Deep 交叉检验"
            status={statusText(deep.status)}
            summary={asString(deep.summary, 'Deep 分析尚未返回摘要')}
            detail={asStringList(deep.risk_flags).join(' · ') || '不生成未验证的仓位建议'}
          />
          <article className="research-presentation-advanced-card research-presentation-advanced-card--valuation">
            <div className="research-presentation-advanced-card__topline"><span><LineChart size={13} />估值参考</span><span>{verdictLabel(statusText(primaryModel.status || verdict))}</span></div>
            <strong>{moneyText(valuation.fair_value ?? primaryModel.fair_value, currencySymbol)}</strong>
            <small>当前价 {moneyText(valuation.current ?? price.current ?? currentPrice, currencySymbol)} · 安全边际 {ratioPercentText(valuation.margin_of_safety ?? primaryModel.margin_of_safety, 1)}</small>
          </article>
        </div>
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>08 / METHODS</span>
          <h3>机构模型明细</h3>
          <i />
          <small>{institutionalModeling.reduce((count, item) => count + (Array.isArray(item.methods) ? item.methods.length : 0), 0)} 个模型输出</small>
        </div>
        {institutionalModeling.length ? (
          <div className="research-presentation__institutional-grid">
            {institutionalModeling.flatMap((item, itemIndex) => (Array.isArray(item.methods) ? item.methods : []).map((method, methodIndex) => {
              const methodRecord = asRecord(method)
              const sensitivity = Array.isArray(methodRecord.sensitivity) ? methodRecord.sensitivity.map(asRecord) : []
              const assumptions = asRecord(methodRecord.assumptions)
              return (
                <article key={`${asString(item.symbol, String(itemIndex))}-${asString(methodRecord.method_id, String(methodIndex))}`} className={`research-presentation__method-card ${statusText(methodRecord.status) === 'data_gap' ? 'is-gap' : ''}`}>
                  <div className="research-presentation__method-card__head">
                    <span>{asString(methodRecord.label, '机构方法')} · {asString(item.symbol, symbol)}</span>
                    <strong>{verdictLabel(statusText(methodRecord.status))}</strong>
                  </div>
                  <p>{asString(methodRecord.summary, '未返回模型摘要。')}</p>
                  {Object.keys(assumptions).length > 0 && <small className="research-presentation__method-card__meta">假设：{Object.entries(assumptions).map(([key, value]) => `${businessFieldLabel(key)}=${Array.isArray(value) ? value.join('、') : String(value)}`).join(' · ')}</small>}
                  {sensitivity.length > 0 && (
                    <div className="research-presentation__sensitivity-grid" aria-label="估值敏感性矩阵">
                      {sensitivity.map((cell, cellIndex) => <span key={cellIndex}>{numberText(cell.fair_value, 0)}</span>)}
                    </div>
                  )}
                  {asStringList(methodRecord.methodology_log).length > 0 && <small className="research-presentation__method-card__log">{asStringList(methodRecord.methodology_log).join(' · ')}</small>}
                  {asStringList(methodRecord.data_gap).length > 0 && <small className="research-presentation__method-card__gap">缺口：{asStringList(methodRecord.data_gap).join(' · ')}</small>}
                </article>
              )
            }))}
          </div>
        ) : <div className="research-presentation__empty"><LineChart size={17} />暂无机构模型明细。</div>}
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>09 / RISKS</span>
          <h3>核心风险清单</h3>
          <i />
          <small>{risks.length} 个风险信号</small>
        </div>
        {risks.length ? (
          <div className="research-presentation__risk-list">
            {risks.map((risk, index) => {
              const refs = Array.isArray(risk.evidence_refs) ? risk.evidence_refs.map(asRecord) : []
              return (
                <article key={asString(risk.signal_id, String(index))} className={`research-presentation__risk-card ${statusText(risk.status) === 'data_gap' ? 'is-gap' : ''}`}>
                  <div className="research-presentation__risk-card__head">
                    <span>{String(index + 1).padStart(2, '0')} · {asString(risk.signal_id, 'risk_signal')}</span>
                    <strong>{asString(risk.severity, 'medium')} / {verdictLabel(statusText(risk.status, 'data_gap'))}</strong>
                  </div>
                  <p>{asString(risk.finding || risk.recommendation, '未返回风险说明。')}</p>
                  {asString(risk.recommendation) && <small>建议：{asString(risk.recommendation)}</small>}
                  {refs.length > 0 && <small>证据：{refs.map((ref) => asString(ref.id || ref.provider || ref.type, 'source')).join(' · ')}</small>}
                </article>
              )
            })}
          </div>
        ) : <div className="research-presentation__empty"><ShieldAlert size={17} />暂无结构化风险信号。</div>}
      </section>

      {buyZones.length > 0 && (
        <section className="research-presentation__section">
          <div className="research-presentation__section-head">
            <span>10 / OBSERVATION</span>
            <h3>价格观察参考</h3>
            <i />
          </div>
          <div className="research-presentation__price-observation-grid">
            {buyZones.map((zone, index) => (
              <article key={asString(zone.zone_id, String(index))}>
                <div><span>{asString(zone.label, '现价观察参考')}</span><strong>{moneyText(zone.price, asString(zone.currency_symbol, currencySymbol))}</strong></div>
                <small>{verdictLabel(statusText(zone.status))} · {asString(zone.kind, 'observation')}</small>
                <p>{asString(zone.rationale, '缺少足够证据，不形成价格区间。')}</p>
              </article>
            ))}
          </div>
        </section>
      )}

      <footer className="research-presentation__footer">
        <CircleAlert size={14} />
        <span>以上内容来自后端结构化研究产物；数据缺口、假设和来源状态均保留，不构成投资建议或买卖指令。</span>
      </footer>
    </section>
  )
}
