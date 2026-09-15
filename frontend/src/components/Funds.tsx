import {
  Database,
  Play,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react'
import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { CognitradeApiClient } from '../api-client'
import { useCognitrade } from '../CognitradeContext'
import { asRecord } from '../utils/value'
import { FundAnalysisDossier } from './FundAnalysisDossier'
import { AppSelect } from './AppSelect'
import type { StrategyWorkspaceContext } from '../strategy-contracts'

type FundsProps = {
  onOpenRun: (runId: string) => void
  onOpenStrategy?: (context: StrategyWorkspaceContext) => void
}

type FundSelectionMode = 'category' | 'codes'

export function Funds({ onOpenRun, onOpenStrategy = () => undefined }: FundsProps) {
  const {
    createRun,
    settings,
    runs,
    selectedRun,
    selectedRunId,
    selectRun,
    reloadRun,
    selectedRunLoading,
    submitting,
  } = useCognitrade()
  const client = useMemo(() => new CognitradeApiClient(settings), [settings])
  const [category, setCategory] = useState('混合型')
  const [selectionMode, setSelectionMode] = useState<FundSelectionMode>('category')
  const [cycle, setCycle] = useState('')
  const [codes, setCodes] = useState('')
  const [candidatePoolSize, setCandidatePoolSize] = useState(8)

  const latestFundRun = useMemo(
    () => runs.find((run) => String(run.intent || '') === 'fund_research'),
    [runs],
  )
  const selectedRecord = runs.find((run) => run.run_id === selectedRunId)
  const sourceRunId = selectedRecord?.intent === 'fund_research'
    ? selectedRunId
    : latestFundRun?.run_id || ''
  useEffect(() => {
    if (!sourceRunId) return
    if (selectedRunId !== sourceRunId) {
      selectRun(sourceRunId)
      return
    }
    if (!selectedRunLoading && selectedRun?.runId !== sourceRunId) {
      void reloadRun(sourceRunId)
    }
  }, [reloadRun, selectRun, selectedRun?.runId, selectedRunId, selectedRunLoading, sourceRunId])

  const fundRun = selectedRun?.runId === sourceRunId ? selectedRun : null

  const artifacts = useMemo(
    () => (Array.isArray(fundRun?.replay?.artifacts)
      ? fundRun.replay.artifacts
      : []),
    [fundRun],
  )
  const analysis = useMemo(() => {
    const artifact = [...artifacts].reverse().find((row) => (
      asRecord(row).artifact_type === 'fund_analysis'
    ))
    return asRecord(asRecord(artifact).content)
  }, [artifacts])
  const hasFundArtifacts = artifacts.some((row) => ['fund_snapshot', 'fund_analysis'].includes(String(asRecord(row).artifact_type || '')))
  const restoringFundRun = Boolean(sourceRunId && (!fundRun || selectedRunLoading))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!cycle) return
    const fundCodes = (selectionMode === 'codes' ? codes : '')
      .split(/[\s,，、]+/)
      .map((value) => value.trim())
      .filter(Boolean)
    const effectiveCategory = selectionMode === 'codes' ? '全部' : category
    const effectiveCandidatePoolSize = fundCodes.length || candidatePoolSize
    const query = fundCodes.length === 1
      ? `分析基金 ${fundCodes[0]}，按${cycleLabel(cycle)}评估风险收益、基准表现和前十大持仓。`
      : fundCodes.length > 1
      ? `比较并分析基金 ${fundCodes.join('、')}，按${cycleLabel(cycle)}评估风险收益和前十大持仓。`
      : `从${category}基金中选择风险收益更均衡的候选，按${cycleLabel(cycle)}评估。`
    const response = await createRun(query, 'fund_research', {
      fund_category: effectiveCategory,
      ...(fundCodes.length ? { fund_codes: fundCodes } : {}),
      investment_cycle: cycle,
      candidate_pool_size: effectiveCandidatePoolSize,
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
          <div className="fund-selection-mode" role="group" aria-label="基金研究方式">
            <button type="button" className={selectionMode === 'category' ? 'is-active' : ''} onClick={() => setSelectionMode('category')}>按类别选基</button>
            <button type="button" className={selectionMode === 'codes' ? 'is-active' : ''} onClick={() => setSelectionMode('codes')}>指定基金分析</button>
          </div>
          {selectionMode === 'category' ? (
            <label>
              <span>基金类别</span>
              <AppSelect aria-label="基金类别" value={category} onChange={(event) => setCategory(event.target.value)}>
                <option>股票型</option><option>混合型</option><option>债券型</option>
                <option>指数型</option><option>ETF</option><option>LOF</option>
                <option>QDII</option><option>FOF</option>
              </AppSelect>
              <small className="fund-field-help">按所选类别建立真实候选池，不会混排其他大类基金。</small>
            </label>
          ) : (
            <label>
              <span>基金代码（逗号分隔，最多 20 只）</span>
              <input aria-label="基金代码" value={codes} onChange={(event) => setCodes(event.target.value)} placeholder="例如 110011, 005827" required />
              <small className="fund-field-help">一只基金执行独立分析；填写多只时才按真实基金类型分组比较。</small>
            </label>
          )}
          <div className="fund-form-grid">
            <label>
              <span>研究周期</span>
              <AppSelect value={cycle} onChange={(event) => setCycle(event.target.value)}>
                <option value="">请选择研究周期</option>
                <option value="short_term">短期</option>
                <option value="mid_term">中期</option>
                <option value="long_term">长期</option>
              </AppSelect>
            </label>
            {selectionMode === 'category' ? <label>
              <span>候选池规模</span>
              <input type="number" min="2" max="20" value={candidatePoolSize} onChange={(event) => setCandidatePoolSize(Number(event.target.value))} />
            </label> : <div className="fund-code-count"><span>本次分析数量</span><strong>{parseFundCodes(codes).length || '—'}</strong><small>最多 20 只</small></div>}
          </div>
          <button className="primary-button fund-run-button" type="submit" disabled={submitting || !cycle}>
            {submitting ? <RefreshCw size={15} className="is-spinning" /> : <Play size={15} />}
            {submitting ? 'Agent OS 正在采集' : !cycle ? '请先选择研究周期' : selectionMode === 'category' ? '开始真实选基' : '开始基金分析'}
          </button>
          <p className="fund-config-note"><ShieldAlert size={14} />单只基金独立评估且不要求对照组；只有多基金或按类别选基时才生成横向排名。</p>
        </form>

        <section className="fund-result-shell">
          <header className="fund-result-header">
            <div><span className="section-kicker">LATEST FUND ANALYSIS</span><h2>最近一次基金结果</h2></div>
            <span className={`quality-pill quality-pill--${String(analysis.quality_status || 'data_gap')}`}>
              {qualityLabel(String(analysis.quality_status || 'data_gap'))}
            </span>
          </header>

          {restoringFundRun ? (
            <div className="fund-empty"><RefreshCw size={22} className="is-spinning" /><strong>正在读取 ArtifactBoard</strong></div>
          ) : hasFundArtifacts ? (
            <FundAnalysisDossier
              artifacts={artifacts}
              sourceRunId={sourceRunId || ''}
              client={client}
              onOpenRun={onOpenRun}
              onOpenStrategy={onOpenStrategy}
            />
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

function cycleLabel(value: string): string {
  return value === 'short_term' ? '短期' : value === 'long_term' ? '长期' : '中期'
}

function qualityLabel(value: string): string {
  return value === 'available' ? '证据可用' : value === 'degraded' ? '部分可用' : '等待数据'
}

function parseFundCodes(value: string): string[] {
  return value.split(/[\s,，、]+/).map((code) => code.trim()).filter(Boolean)
}
