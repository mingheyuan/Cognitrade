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
import { asNumber, asRecord, asString, candidatesFromReport, reportFromDetail } from '../live-data'
import type { ResearchIntent } from '../research-contracts'

type ScreeningProps = {
  onSubmit: (query: string, intent?: ResearchIntent) => void
  onOpenRun: (runId: string) => void
}

export function Screening({ onSubmit, onOpenRun }: ScreeningProps) {
  const { screeningRun, submitting } = useCognitrade()
  const [tier, setTier] = useState('全部候选')
  const [query, setQuery] = useState('')
  const report = useMemo(() => reportFromDetail(screeningRun), [screeningRun])
  const allCandidates = useMemo(() => candidatesFromReport(report), [report])
  const scorecard = asRecord(report.screening_scorecard || report.scorecard)
  const tiers = ['全部候选', ...Array.from(new Set(allCandidates.map((candidate) => candidate.tier).filter(Boolean)))]
  const candidates = useMemo(
    () => allCandidates.filter((candidate) => (tier === '全部候选' || candidate.tier === tier) && (!query.trim() || `${candidate.name}${candidate.code}${candidate.sector}`.toLowerCase().includes(query.trim().toLowerCase()))),
    [allCandidates, query, tier],
  )

  const exportResults = () => {
    if (!screeningRun) return
    const blob = new Blob([JSON.stringify(screeningRun.replay.result, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${screeningRun.runId}-screening.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <main className="page-content screening-page">
      <header className="page-heading">
        <div><span className="page-kicker">STOCK SCREENING · LIVE API</span><h1>智能筛选</h1><p>候选只来自最近一次 stock_screen 运行的 report.candidates，不再使用页面内置样本。</p></div>
        <div className="page-heading-actions">
          <button className="secondary-button" onClick={exportResults} disabled={!screeningRun}><Download size={15} />导出结果</button>
          <button className="primary-button" onClick={() => onSubmit('筛选现金流质量改善、估值合理且风险可解释的 A 股候选', 'stock_screen')} disabled={submitting}><Sparkles size={15} />新建筛选任务</button>
        </div>
      </header>

      <section className="screening-query-card">
        <div className="screening-query-main"><Filter size={17} /><div><span>最近筛选任务</span><strong>{screeningRun ? asString(screeningRun.replay.request?.user_query, '未提供查询') : '尚无 stock_screen 运行'}</strong></div>{screeningRun && <button className="secondary-button" onClick={() => onOpenRun(screeningRun.runId)}>打开运行</button>}</div>
      </section>

      <section className="screening-summary-grid">
        <article><span>候选数量</span><strong>{allCandidates.length}</strong><small>report.candidates</small></article>
        <article><span>深度核验</span><strong>{allCandidates.filter((candidate) => candidate.deepChecked).length}</strong><small>deep_checked=true</small></article>
        <article><span>评分卡候选</span><strong>{asNumber(scorecard.candidate_count) ?? '—'}</strong><small>screening_scorecard</small></article>
        <article><span>运行状态</span><strong>{asString(screeningRun?.replay.summary?.status, '—')}</strong><small>{screeningRun?.runId || '暂无 run'}</small></article>
      </section>

      <section className="dashboard-panel screening-results-panel">
        <div className="screening-toolbar">
          <div className="segmented-control">
            {tiers.map((item) => <button key={item} className={tier === item ? 'is-active' : ''} onClick={() => setTier(item)}>{item}<span>{item === '全部候选' ? allCandidates.length : allCandidates.filter((candidate) => candidate.tier === item).length}</span></button>)}
          </div>
          <label className="table-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索公司或代码" /></label>
          <button className="icon-button" aria-label="重置筛选" onClick={() => { setTier('全部候选'); setQuery('') }}><RotateCcw size={15} /></button>
        </div>

        <div className="candidate-table-wrap screening-table-wrap">
          <table className="candidate-table screening-table">
            <thead><tr><th>排名</th><th>公司</th><th>分层</th><th>综合分</th><th>涨跌</th><th>估值</th><th>现金流</th><th>趋势</th><th>核心理由 / 风险</th></tr></thead>
            <tbody>
              {candidates.map((candidate) => (
                <tr key={candidate.code}>
                  <td><span className={`rank-number rank-number--${candidate.rank}`}>{candidate.rank}</span></td>
                  <td><div className="company-cell"><span className="mini-company-mark">{candidate.name.slice(0, 1)}</span><span><strong>{candidate.name}</strong><small>{candidate.code} · {candidate.sector}</small></span></div></td>
                  <td><span className={`tier-badge ${candidate.deepChecked ? 'is-core' : ''}`}>{candidate.deepChecked && <Check size={11} />}{candidate.tier}</span></td>
                  <td><div className="table-score-cell"><strong>{candidate.score ?? '—'}</strong>{candidate.score !== null && <span><i style={{ width: `${Math.max(0, Math.min(100, candidate.score))}%` }} /></span>}</div></td>
                  <td className={candidate.change === null ? '' : candidate.change >= 0 ? 'market-up' : 'market-down'}>{candidate.change === null ? '—' : <>{candidate.change >= 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}{Math.abs(candidate.change).toFixed(2)}%</>}</td>
                  <td><span className="valuation-state">{candidate.valuation}</span></td>
                  <td><span className="factor-score">{candidate.cashflow ?? '—'}</span></td>
                  <td><span className="factor-score">{candidate.momentum ?? '—'}</span></td>
                  <td className="reason-cell">{candidate.reason}{candidate.risk !== '未提供' && <small className="candidate-risk">风险：{candidate.risk}</small>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!candidates.length && <div className="live-empty-state"><Sparkles size={20} /><strong>暂无真实筛选候选</strong><p>创建 stock_screen 任务后，后端返回的候选会出现在这里。</p></div>}
        <footer className="table-footer"><span><Info size={13} />缺失字段显示为“—”，不会用演示值补齐。</span><span>显示 {candidates.length} / {allCandidates.length} 个真实候选</span></footer>
      </section>
    </main>
  )
}
