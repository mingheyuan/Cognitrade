import {
  Database,
  Play,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react'
import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import { FundAnalysisDossier } from './FundAnalysisDossier'

type FundsProps = {
  onOpenRun: (runId: string) => void
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

  const artifacts = useMemo(
    () => (Array.isArray(selectedRun?.replay?.artifacts)
      ? selectedRun.replay.artifacts
      : []),
    [selectedRun],
  )
  const analysis = useMemo(() => {
    const artifact = [...artifacts].reverse().find((row) => (
      asRecord(row).artifact_type === 'fund_analysis'
    ))
    return asRecord(asRecord(artifact).content)
  }, [artifacts])
  const hasFundArtifacts = artifacts.some((row) => ['fund_snapshot', 'fund_analysis'].includes(String(asRecord(row).artifact_type || '')))
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
          ) : hasFundArtifacts ? (
            <FundAnalysisDossier artifacts={artifacts} />
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function cycleLabel(value: string): string {
  return value === 'short_term' ? '短期' : value === 'long_term' ? '长期' : '中期'
}

function qualityLabel(value: string): string {
  return value === 'available' ? '证据可用' : value === 'degraded' ? '部分可用' : '等待数据'
}
