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
import { asNumber, asRecord, asString, asStringList } from '../live-data'
import type { ResearchReport } from '../research-contracts'

type ResearchReportPresentationProps = {
  report: ResearchReport
}

function numberText(value: unknown, digits = 2): string {
  const number = asNumber(value)
  return number === null ? '—' : number.toLocaleString('zh-CN', { maximumFractionDigits: digits })
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
  const greatDivide = asRecord(presentation.great_divide)
  const profile = asString(presentation.profile, 'lite')
  const valuationModel = asRecord(report.valuation_model)
  const valuationModels = Array.isArray(valuationModel.models)
    ? valuationModel.models.map((model) => asRecord(model))
    : []
  const primaryModel = valuationModels[0] || {}
  const advanced = asRecord(presentation.advanced_methods)
  const institutional = asRecord(advanced.institutional_analysis)
  const workflow = asRecord(advanced.research_workflow)
  const deep = asRecord(advanced.deep_analysis)
  const dimensions = Array.isArray(presentation.radar)
    ? presentation.radar.map(asRecord).slice(0, 12)
    : []
  const metricValues = Array.isArray(subject.metrics)
    ? subject.metrics
    : Array.isArray(dashboard.metrics)
      ? dashboard.metrics
      : []
  const metrics = metricValues
    .map((metric) => asRecord(metric))
    .slice(0, 6)
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
            <strong>{currentPrice === null ? '—' : `¥${numberText(currentPrice)}`}</strong>
            <span className={change === null ? '' : change >= 0 ? 'is-up' : 'is-down'}>
              {change === null ? '涨跌未提供' : `${change >= 0 ? '+' : ''}${percentText(change)}`}
            </span>
          </div>
          <div className="research-presentation__chip-row">
            {metrics.slice(0, 3).map((metric, index) => (
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
          <small>规则化方法论映射，不代表真实投资人意见</small>
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
            <p>{risks.slice(0, 2).map((risk, index) => `${index + 1}. ${asString(risk.finding || risk.recommendation, '未返回风险说明')}`).join(' ') || '后端未返回风险清单。'}</p>
          </article>
          <article>
            <span><Sparkles size={14} /> 催化剂与事件</span>
            <p>{catalysts.length ? catalysts.slice(0, 3).map((event) => asString(event.title, '未命名事件')).join(' · ') : '暂无已结构化的催化剂日历。'}</p>
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
          <small>{asNumber(panel.registered_seat_count) ?? votes.length} 个方法论席位</small>
        </div>
        {votes.length ? (
          <div className="research-presentation__vote-grid">
            {votes.slice(0, 10).map((vote, index) => {
              const signal = asString(vote.signal, 'neutral')
              const voteScore = asNumber(vote.score)
              return (
                <article key={asString(vote.perspective_id, String(index))} className={signalClass(signal)}>
                  <div><span>{asString(vote.perspective_label, '方法论视角')}</span><strong>{voteScore === null ? '—' : numberText(voteScore, 1)}</strong></div>
                  <div className="research-presentation__vote-track"><i style={{ width: `${Math.max(0, Math.min(100, voteScore ?? 0))}%` }} /></div>
                  <small>{signalLabel(signal)} · {asString(vote.headline || vote.reasoning, '未返回解释')}</small>
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
        </div>
        {dimensions.length ? (
          <div className="research-presentation__radar-grid">
            {dimensions.map((dimension, index) => {
              const dimensionScore = asNumber(dimension.score)
              const dimensionStatus = statusText(dimension.status)
              return (
                <article key={asString(dimension.dimension_id, String(index))} className={dimensionStatus === 'data_gap' ? 'is-gap' : ''}>
                  <div><span>{asString(dimension.label, '研究维度')}</span><strong>{dimensionScore === null ? '—' : numberText(dimensionScore, 1)}</strong></div>
                  <div className="research-presentation__radar-track"><i style={{ width: `${dimensionScore === null ? 0 : Math.max(0, Math.min(100, dimensionScore * 10))}%` }} /></div>
                  <small>{dimensionStatus === 'data_gap' ? asStringList(dimension.data_gap).join(' · ') || '数据缺口' : asStringList(dimension.evidence).join(' · ') || '证据已提供'}</small>
                </article>
              )
            })}
          </div>
        ) : <div className="research-presentation__empty"><BarChart3 size={17} />暂无结构化维度覆盖。</div>}
      </section>

      <section className="research-presentation__section">
        <div className="research-presentation__section-head">
          <span>05 / MODELING</span>
          <h3>机构模型与研究工作流</h3>
          <i />
        </div>
        <div className="research-presentation__advanced-grid">
          <AdvancedSummary
            label="机构估值模型"
            status={statusText(institutional.status)}
            summary={asString(institutional.summary, '机构模型尚未返回摘要')}
            detail={asStringList(institutional.data_gap).slice(0, 3).join(' · ') || '方法输出已写入 ArtifactBoard'}
          />
          <AdvancedSummary
            label="研究工作流"
            status={statusText(workflow.status)}
            summary={asString(workflow.summary, '工作流尚未返回摘要')}
            detail={asStringList(workflow.risk_flags).slice(0, 3).join(' · ') || '覆盖业绩、催化剂和 Thesis'}
          />
          <AdvancedSummary
            label="Deep 交叉检验"
            status={statusText(deep.status)}
            summary={asString(deep.summary, 'Deep 分析尚未返回摘要')}
            detail={asStringList(deep.risk_flags).slice(0, 3).join(' · ') || '不生成未验证的仓位建议'}
          />
          <article className="research-presentation-advanced-card research-presentation-advanced-card--valuation">
            <div className="research-presentation-advanced-card__topline"><span><LineChart size={13} />估值参考</span><span>{verdictLabel(statusText(primaryModel.status || verdict))}</span></div>
            <strong>¥{numberText(valuation.fair_value ?? primaryModel.fair_value)}</strong>
            <small>当前价 ¥{numberText(valuation.current ?? price.current ?? currentPrice)} · 安全边际 {ratioPercentText(valuation.margin_of_safety ?? primaryModel.margin_of_safety, 1)}</small>
          </article>
        </div>
      </section>

      <footer className="research-presentation__footer">
        <CircleAlert size={14} />
        <span>以上内容来自后端结构化研究产物；数据缺口、假设和来源状态均保留，不构成投资建议或买卖指令。</span>
      </footer>
    </section>
  )
}
