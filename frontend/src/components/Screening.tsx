import {
  ArrowDown,
  ArrowUp,
  Check,
  Download,
  Filter,
  Info,
  RotateCcw,
  Search,
  Sparkles,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { useCognitrade } from '../CognitradeContext'
import { asNumber, asRecord, asString, dashboardCandidates, dashboardValue } from '../live-data'
import type { ResearchIntent } from '../research-contracts'

type ScreeningProps = {
  onSubmit: (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => void
  onOpenRun: (runId: string) => void
}

const tierLabel = (tier: string) => tier === 'core' ? '核心池' : tier === 'watchlist' ? '观察池' : tier

export function Screening({ onSubmit, onOpenRun }: ScreeningProps) {
  const { screeningDashboard, submitting } = useCognitrade()
  const [tier, setTier] = useState('all')
  const [query, setQuery] = useState('')
  const allCandidates = useMemo(() => dashboardCandidates(screeningDashboard), [screeningDashboard])
  const scorecard = dashboardValue(screeningDashboard, 'scorecard')
  const strategy = asRecord(scorecard.screening_strategy)
  const screenStyle = asString(strategy.screen_style)
  const tiers = ['all', ...Array.from(new Set(allCandidates.map((candidate) => candidate.tier).filter(Boolean)))]
  const candidates = useMemo(
    () => allCandidates.filter((candidate) => (tier === 'all' || candidate.tier === tier) && (!query.trim() || `${candidate.name}${candidate.code}${candidate.sector}`.toLowerCase().includes(query.trim().toLowerCase()))),
    [allCandidates, query, tier],
  )
  const sourceRunId = allCandidates[0]?.runId

  const exportResults = () => {
    if (!screeningDashboard) return
    const blob = new Blob([JSON.stringify(screeningDashboard, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${sourceRunId || 'agent-os'}-screening-dashboard.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <main className="page-content screening-page">
      <header className="page-heading">
        <div><span className="page-kicker">STOCK SCREENING · AGENT OS</span><h1>智能筛选</h1><p>候选、评分、数据缺口和来源均来自最新 `screening_result` Artifact。</p></div>
        <div className="page-heading-actions">
          <button className="secondary-button" onClick={exportResults} disabled={!screeningDashboard}><Download size={15} />导出结果</button>
          <button className="primary-button" onClick={() => onSubmit('筛选现金流质量改善、估值合理且风险可解释的 A 股候选', 'stock_screen')} disabled={submitting}><Sparkles size={15} />新建筛选任务</button>
        </div>
      </header>

      <section className="screening-query-card">
        <div className="screening-query-main"><Filter size={17} /><div><span>当前筛选方法</span><strong>{screenStyle ? `${screenStyle} · DomainPack 契约` : '尚无 Agent OS 筛选运行'}</strong></div>{sourceRunId && <button className="secondary-button" onClick={() => onOpenRun(sourceRunId)}>打开运行</button>}</div>
      </section>

      <section className="screening-summary-grid">
        <article><span>候选数量</span><strong>{asNumber(scorecard.candidate_count) ?? allCandidates.length}</strong><small>最新 screening_result</small></article>
        <article><span>深度核验</span><strong>{asNumber(scorecard.deep_checked_count) ?? allCandidates.filter((candidate) => candidate.deepChecked).length}</strong><small>市场、财务与披露证据齐备</small></article>
        <article><span>核心候选</span><strong>{asNumber(scorecard.top_candidate_count) ?? allCandidates.filter((candidate) => candidate.tier === 'core').length}</strong><small>当前策略优先结果</small></article>
        <article><span>观察候选</span><strong>{asNumber(scorecard.watchlist_candidate_count) ?? allCandidates.filter((candidate) => candidate.tier === 'watchlist').length}</strong><small>数据缺口会明确保留</small></article>
      </section>

      <section className="dashboard-panel screening-results-panel">
        <div className="screening-toolbar">
          <div className="segmented-control">
            {tiers.map((item) => <button key={item} className={tier === item ? 'is-active' : ''} onClick={() => setTier(item)}>{item === 'all' ? '全部候选' : tierLabel(item)}<span>{item === 'all' ? allCandidates.length : allCandidates.filter((candidate) => candidate.tier === item).length}</span></button>)}
          </div>
          <label className="table-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索公司、代码或板块" /></label>
          <button className="icon-button" aria-label="重置筛选" onClick={() => { setTier('all'); setQuery('') }}><RotateCcw size={15} /></button>
        </div>

        <div className="candidate-table-wrap screening-table-wrap">
          <table className="candidate-table screening-table">
            <thead><tr><th>排名</th><th>公司</th><th>分层</th><th>综合分</th><th>涨跌</th><th>方法论覆盖</th><th>数据缺口</th><th>核心理由 / 风险</th></tr></thead>
            <tbody>
              {candidates.map((candidate) => (
                <tr key={`${candidate.runId}-${candidate.code}`}>
                  <td><span className={`rank-number rank-number--${candidate.rank}`}><ArrowDown size={11} />{candidate.rank}</span></td>
                  <td><div className="company-cell"><span className="mini-company-mark">{candidate.name.slice(0, 1)}</span><span><strong>{candidate.name}</strong><small>{candidate.code} · {candidate.sector}</small></span></div></td>
                  <td><span className={`tier-badge ${candidate.tier === 'core' ? 'is-core' : ''}`}>{candidate.tier === 'core' && <Check size={11} />}{tierLabel(candidate.tier)}</span></td>
                  <td><div className="table-score-cell"><strong>{candidate.score === null ? '—' : candidate.score.toFixed(2)}</strong></div></td>
                  <td className={candidate.change === null ? '' : candidate.change >= 0 ? 'market-up' : 'market-down'}>{candidate.change === null ? '—' : <>{candidate.change >= 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}{Math.abs(candidate.change).toFixed(2)}%</>}</td>
                  <td>{candidate.methodologyCoverage === null ? '待补充' : `${Math.round(candidate.methodologyCoverage * 100)}%`}</td>
                  <td>{candidate.dataGapCount ? `${candidate.dataGapCount} 项` : '无'}</td>
                  <td className="reason-cell">{candidate.reason}{candidate.risk !== '未提供' && <small className="candidate-risk">风险：{candidate.risk}</small>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!candidates.length && <div className="live-empty-state"><Sparkles size={20} /><strong>暂无真实筛选候选</strong><p>创建 stock_screen 任务后，这里会读取带来源的 ArtifactBoard 结果。</p></div>}
        <footer className="table-footer"><span><Info size={13} />缺失字段会明确保留，不会用演示值或中性值补齐。</span><span>显示 {candidates.length} / {allCandidates.length} 个真实候选</span></footer>
      </section>
    </main>
  )
}
