import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  ChevronDown,
  Database,
  LineChart as LineChartIcon,
  Scale,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { useMemo, useState } from 'react'
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

type FundAnalysisDossierProps = {
  artifacts: Array<Record<string, unknown>>
}

type RecordValue = Record<string, unknown>
type TrendRange = '1y' | '3y' | 'all'

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

export function FundAnalysisDossier({ artifacts }: FundAnalysisDossierProps) {
  const analysis = artifactContent(artifacts, 'fund_analysis')
  const snapshot = artifactContent(artifacts, 'fund_snapshot')
  const snapshotFunds = new Map(records(snapshot.funds).map((row) => [String(row.fund_code || ''), row]))
  const candidates = records(analysis.candidates).map((row) => ({
    ...snapshotFunds.get(String(row.fund_code || '')),
    ...row,
  }))
  const recommendations = records(analysis.recommendations)
  const policy = asRecord(analysis.selection_policy)
  const errors = [...records(snapshot.provider_errors), ...records(analysis.provider_errors)]
  const gaps = Array.from(new Set([
    ...(Array.isArray(snapshot.data_gap) ? snapshot.data_gap.map(String) : []),
    ...(Array.isArray(analysis.data_gap) ? analysis.data_gap.map(String) : []),
  ]))

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
        <article><ShieldCheck size={16} /><span>证据达标</span><strong>{candidates.filter((row) => row.selection_eligible === true).length}</strong><small>按真实字段覆盖计算</small></article>
        <article><CheckCircle2 size={16} /><span>优先候选</span><strong>{recommendations.length}</strong><small>只来自可比样本</small></article>
        <article><AlertCircle size={16} /><span>运行级缺口</span><strong>{gaps.length}</strong><small>{errors.length} 个数据源异常</small></article>
      </div>

      <section className="fund-recommendation-board">
        <header><div><CheckCircle2 size={16} /><h3>选择结论</h3></div><span>{recommendations.length} 项</span></header>
        {recommendations.length ? (
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

      <div className="fund-candidate-dossiers">
        {candidates.map((candidate, index) => <FundCandidateDossier key={String(candidate.fund_code || index)} candidate={candidate} defaultOpen={index === 0} />)}
      </div>

      <div className="fund-audit-grid">
        <section>
          <header><Scale size={15} /><h3>选择规则</h3></header>
          <p>{String(policy.description || '后端未返回选择规则说明')}</p>
          <div className="fund-policy-metrics"><span>最低证据覆盖<strong>{percent((numberValue(policy.minimum_score_coverage) || 0) * 100, false)}</strong></span><span>最小可比样本<strong>{numberText(policy.minimum_comparable_sample, 0)}</strong></span></div>
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

function FundCandidateDossier({ candidate, defaultOpen }: { candidate: RecordValue; defaultOpen: boolean }) {
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
  const gaps = Array.isArray(candidate.data_gap) ? candidate.data_gap.map(String) : []
  const usesSinceInceptionReturn = numberValue(returns.year_3) == null
  return (
    <details className={`fund-candidate-dossier ${candidate.selection_eligible ? 'is-eligible' : ''}`} open={defaultOpen}>
      <summary>
        <span className="fund-rank">{candidate.rank ? `#${candidate.rank}` : '—'}</span>
        <span><strong>{String(candidate.fund_name || candidate.fund_code)}</strong><small>{String(candidate.fund_code || '—')} · {String(candidate.fund_type || '类型未返回')}</small></span>
        <span><small>综合分</small><strong>{numberText(candidate.score, 1)}</strong></span>
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
          <header><BarChart3 size={15} /><h4>评分拆解</h4><span>{scoreComponents.length} 个已观测维度</span></header>
          {scoreComponents.length ? <div>{scoreComponents.map((row) => (
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
          <header><ShieldCheck size={15} /><h4>完整披露持仓</h4><span>{String(candidate.holdings_period || '披露期未返回')} · {holdings.length} 项</span></header>
          {holdings.length ? <div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>序号</th><th>代码</th><th>名称</th><th>净值占比</th><th>持股数（万股）</th><th>市值（万元）</th></tr></thead><tbody>{holdings.map((row, index) => <tr key={`${String(row.code)}-${index}`}><td>{String(row.rank || index + 1)}</td><td>{String(row.code || '—')}</td><td>{String(row.name || '—')}</td><td>{percent(row.weight_pct, false)}</td><td>{numberText(row.shares_10k)}</td><td>{numberText(row.market_value_10k)}</td></tr>)}</tbody></table></div> : <p>本次运行未取得披露持仓。</p>}
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
