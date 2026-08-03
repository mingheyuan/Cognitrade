import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Database,
  Layers3,
  Play,
  RefreshCw,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'

type FundsProps = {
  onOpenRun: (runId: string) => void
}

type FundCandidate = {
  fund_code?: string
  fund_name?: string
  fund_type?: string
  rank?: number | null
  score?: number | null
  score_coverage?: number
  selection_eligible?: boolean
  returns_pct?: Record<string, number>
  risk?: Record<string, number | string | null>
  fee_pct?: number | null
  top10_concentration_pct?: number | null
  holdings_period?: string | null
  top_holdings?: Array<Record<string, unknown>>
  data_gap?: string[]
}

export function Funds({ onOpenRun }: FundsProps) {
  const {
    createRun,
    runs,
    selectedRun,
    selectedRunId,
    selectRun,
    selectedRunLoading,
    submitting,
  } = useCognitrade()
  const [category, setCategory] = useState('混合型')
  const [cycle, setCycle] = useState('long_term')
  const [codes, setCodes] = useState('')
  const [candidatePoolSize, setCandidatePoolSize] = useState(8)

  const latestFundRun = useMemo(
    () => runs.find((run) => String(run.intent || '') === 'fund_research'),
    [runs],
  )
  const selectedRecord = runs.find((run) => run.run_id === selectedRunId)
  useEffect(() => {
    if (selectedRecord?.intent === 'fund_research' || !latestFundRun?.run_id) return
    selectRun(latestFundRun.run_id)
  }, [latestFundRun?.run_id, selectRun, selectedRecord?.intent])

  const analysis = useMemo(() => {
    const artifacts = Array.isArray(selectedRun?.replay?.artifacts)
      ? selectedRun.replay.artifacts
      : []
    const artifact = [...artifacts].reverse().find((row) => (
      asRecord(row).artifact_type === 'fund_analysis'
    ))
    return asRecord(asRecord(artifact).content)
  }, [selectedRun])
  const candidates = (Array.isArray(analysis.candidates) ? analysis.candidates : [])
    .map((row) => asRecord(row) as FundCandidate)
  const recommendations = Array.isArray(analysis.recommendations)
    ? analysis.recommendations.map((row) => asRecord(row))
    : []
  const dataGaps = Array.isArray(analysis.data_gap) ? analysis.data_gap.map(String) : []
  const sourceRunId = selectedRecord?.intent === 'fund_research' ? selectedRunId : latestFundRun?.run_id

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const fundCodes = codes
      .split(/[\s,，、]+/)
      .map((value) => value.trim())
      .filter(Boolean)
    const effectiveCategory = fundCodes.length ? '全部' : category
    const query = fundCodes.length
      ? `比较并分析基金 ${fundCodes.join('、')}，按${cycleLabel(cycle)}评估风险收益和前十大持仓。`
      : `从${category}基金中选择风险收益更均衡的候选，按${cycleLabel(cycle)}评估。`
    const response = await createRun(query, 'fund_research', {
      fund_category: effectiveCategory,
      fund_codes: fundCodes,
      investment_cycle: cycle,
      candidate_pool_size: candidatePoolSize,
    })
    if (response.run_id) selectRun(response.run_id)
  }

  return (
    <main className="finance-page fund-page">
      <section className="finance-hero fund-hero">
        <div>
          <span className="section-kicker">FUND RESEARCH · UZI 3.9.2 · AGENT OS</span>
          <h1>基金研究与选择</h1>
          <p>比较基金本体的真实收益、净值回撤、波动率、费率和定期披露持仓；缺失数据只标记缺口，不做模拟补值。</p>
        </div>
        {sourceRunId && (
          <button className="secondary-button" onClick={() => onOpenRun(sourceRunId)}>
            <Database size={15} />打开证据链
          </button>
        )}
      </section>

      <div className="fund-workspace">
        <form className="fund-config-card" onSubmit={submit}>
          <header>
            <div><span className="section-kicker">SELECTION SCOPE</span><h2>创建选基任务</h2></div>
            <span className="quality-pill quality-pill--available">真实数据</span>
          </header>
          <label>
            <span>基金类别（指定代码时按真实类型分组）</span>
            <select value={category} disabled={Boolean(codes.trim())} onChange={(event) => setCategory(event.target.value)}>
              <option>股票型</option><option>混合型</option><option>债券型</option>
              <option>指数型</option><option>ETF</option><option>LOF</option>
              <option>QDII</option><option>FOF</option>
            </select>
          </label>
          <label>
            <span>指定基金代码（可选，逗号分隔）</span>
            <input value={codes} onChange={(event) => setCodes(event.target.value)} placeholder="例如 110011, 005827" />
          </label>
          <div className="fund-form-grid">
            <label>
              <span>研究周期</span>
              <select value={cycle} onChange={(event) => setCycle(event.target.value)}>
                <option value="short_term">短期</option>
                <option value="mid_term">中期</option>
                <option value="long_term">长期</option>
              </select>
            </label>
            <label>
              <span>候选池规模</span>
              <input type="number" min="2" max="20" value={candidatePoolSize} onChange={(event) => setCandidatePoolSize(Number(event.target.value))} />
            </label>
          </div>
          <button className="primary-button fund-run-button" type="submit" disabled={submitting}>
            {submitting ? <RefreshCw size={15} className="is-spinning" /> : <Play size={15} />}
            {submitting ? 'Agent OS 正在采集' : '开始真实选基'}
          </button>
          <p className="fund-config-note"><ShieldAlert size={14} />不同大类基金不会混在一起硬排名；单只基金只能评估，不能伪造横向名次。</p>
        </form>

        <section className="fund-result-shell">
          <header className="fund-result-header">
            <div><span className="section-kicker">LATEST FUND ANALYSIS</span><h2>最近一次基金结果</h2></div>
            <span className={`quality-pill quality-pill--${String(analysis.quality_status || 'data_gap')}`}>
              {qualityLabel(String(analysis.quality_status || 'data_gap'))}
            </span>
          </header>

          {selectedRunLoading ? (
            <div className="fund-empty"><RefreshCw size={22} className="is-spinning" /><strong>正在读取 ArtifactBoard</strong></div>
          ) : candidates.length ? (
            <>
              <div className="fund-summary-grid">
                <article><Layers3 size={17} /><span>同类候选</span><strong>{candidates.length}</strong><small>{String(analysis.fund_category || category)}</small></article>
                <article><CheckCircle2 size={17} /><span>通过证据门槛</span><strong>{candidates.filter((row) => row.selection_eligible).length}</strong><small>覆盖率至少 60%</small></article>
                <article><BarChart3 size={17} /><span>优先候选</span><strong>{recommendations.length}</strong><small>仅从可比较样本产生</small></article>
                <article><AlertCircle size={17} /><span>运行级缺口</span><strong>{dataGaps.length}</strong><small>不以零值代替</small></article>
              </div>
              <div className="fund-candidate-list">
                {candidates.map((candidate) => (
                  <FundCandidateCard key={candidate.fund_code} candidate={candidate} />
                ))}
              </div>
            </>
          ) : (
            <div className="fund-empty">
              <Database size={24} />
              <strong>暂无基金分析 Artifact</strong>
              <p>创建 fund_research 任务后，这里会展示真实排行、净值风险与披露持仓。</p>
            </div>
          )}
        </section>
      </div>
    </main>
  )
}

function FundCandidateCard({ candidate }: { candidate: FundCandidate }) {
  const oneYear = numberValue(candidate.returns_pct?.year_1)
  const drawdown = numberValue(candidate.risk?.max_drawdown_pct)
  const volatility = numberValue(candidate.risk?.annualized_volatility_pct)
  const holdings = Array.isArray(candidate.top_holdings) ? candidate.top_holdings.slice(0, 5) : []
  return (
    <article className={`fund-candidate-card ${candidate.selection_eligible ? 'is-eligible' : ''}`}>
      <header>
        <span className="fund-rank">{candidate.rank ? `#${candidate.rank}` : '—'}</span>
        <div><strong>{candidate.fund_name || candidate.fund_code}</strong><small>{candidate.fund_code} · {candidate.fund_type || '类型待补充'}</small></div>
        <div className="fund-score"><span>综合分</span><strong>{candidate.score == null ? '—' : Number(candidate.score).toFixed(1)}</strong><small>覆盖 {Math.round(Number(candidate.score_coverage || 0) * 100)}%</small></div>
      </header>
      <div className="fund-metric-row">
        <Metric icon={<TrendingUp size={14} />} label="近 1 年" value={percent(oneYear)} tone={oneYear == null ? undefined : oneYear >= 0 ? 'up' : 'down'} />
        <Metric icon={<TrendingDown size={14} />} label="最大回撤" value={percent(drawdown)} tone="down" />
        <Metric icon={<BarChart3 size={14} />} label="年化波动" value={percent(volatility)} />
        <Metric icon={<Layers3 size={14} />} label="前十大集中度" value={percent(numberValue(candidate.top10_concentration_pct))} />
      </div>
      <div className="fund-holdings-row">
        <span>{candidate.holdings_period || '持仓期次缺失'}</span>
        <div>{holdings.map((row, index) => <em key={`${String(row.code || '')}-${index}`}>{String(row.name || row.code || '未知持仓')} {percent(numberValue(row.weight_pct))}</em>)}</div>
      </div>
      {candidate.data_gap?.length ? <footer><AlertCircle size={13} />{candidate.data_gap.length} 项数据缺口：{candidate.data_gap.slice(0, 3).join('、')}</footer> : null}
    </article>
  )
}

function Metric({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone?: 'up' | 'down' }) {
  return <div><span>{icon}{label}</span><strong className={tone ? `number-${tone}` : ''}>{value}</strong></div>
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function numberValue(value: unknown): number | null {
  const number = Number(value)
  return value == null || value === '' || !Number.isFinite(number) ? null : number
}

function percent(value: number | null): string {
  return value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function cycleLabel(value: string): string {
  return value === 'short_term' ? '短期' : value === 'long_term' ? '长期' : '中期'
}

function qualityLabel(value: string): string {
  return value === 'available' ? '证据可用' : value === 'degraded' ? '部分可用' : '等待数据'
}
