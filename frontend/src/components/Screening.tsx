import {
  ArrowDown,
  ArrowUp,
  BookmarkPlus,
  Check,
  ChevronDown,
  Download,
  Filter,
  Info,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { screeningCandidates } from '../data'

type FilterTier = '全部候选' | '核心池' | '观察池'

export function Screening() {
  const [tier, setTier] = useState<FilterTier>('全部候选')
  const [query, setQuery] = useState('')

  const candidates = useMemo(
    () =>
      screeningCandidates.filter(
        (candidate) =>
          (tier === '全部候选' || candidate.tier === tier) &&
          (`${candidate.name}${candidate.code}${candidate.sector}`.includes(query.trim()) || !query.trim()),
      ),
    [query, tier],
  )

  return (
    <main className="page-content screening-page">
      <header className="page-heading">
        <div>
          <span className="page-kicker">STOCK SCREENING</span>
          <h1>智能筛选</h1>
          <p>先用确定性数据缩小候选池，再由 Agent 补充证据与风险复核。</p>
        </div>
        <div className="page-heading-actions">
          <button className="secondary-button">
            <Download size={15} />
            导出结果
          </button>
          <button className="primary-button">
            <Sparkles size={15} />
            新建筛选任务
          </button>
        </div>
      </header>

      <section className="screening-query-card">
        <div className="screening-query-main">
          <SlidersHorizontal size={17} />
          <div>
            <span>当前筛选逻辑</span>
            <strong>现金流质量改善 · 估值合理 · 排除 ST · 中低波动</strong>
          </div>
          <button className="secondary-button">
            编辑条件
            <ChevronDown size={14} />
          </button>
        </div>
        <div className="filter-chip-row">
          <span className="filter-chip">
            ROE &gt; 12% <button aria-label="移除 ROE 条件">×</button>
          </span>
          <span className="filter-chip">
            经营现金流 / 净利润 &gt; 1 <button aria-label="移除现金流条件">×</button>
          </span>
          <span className="filter-chip">
            PE 分位 &lt; 65% <button aria-label="移除估值条件">×</button>
          </span>
          <button className="add-filter-button">
            <Filter size={13} />
            添加条件
          </button>
        </div>
      </section>

      <section className="screening-summary-grid">
        <article>
          <span>初筛样本</span>
          <strong>5,284</strong>
          <small>全 A 股基础池</small>
        </article>
        <article>
          <span>进入候选</span>
          <strong>24</strong>
          <small>通过 7 项硬指标</small>
        </article>
        <article>
          <span>深度核验</span>
          <strong>9</strong>
          <small>已补充 Agent 研究</small>
        </article>
        <article>
          <span>数据完整度</span>
          <strong>91.6%</strong>
          <small className="warning-copy">3 个候选存在缺失项</small>
        </article>
      </section>

      <section className="dashboard-panel screening-results-panel">
        <div className="screening-toolbar">
          <div className="segmented-control">
            {(['全部候选', '核心池', '观察池'] as FilterTier[]).map((item) => (
              <button key={item} className={tier === item ? 'is-active' : ''} onClick={() => setTier(item)}>
                {item}
                <span>
                  {item === '全部候选'
                    ? screeningCandidates.length
                    : screeningCandidates.filter((candidate) => candidate.tier === item).length}
                </span>
              </button>
            ))}
          </div>
          <label className="table-search">
            <Search size={14} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索公司或代码" />
          </label>
          <button className="secondary-button table-filter-button">
            <Filter size={14} />
            更多筛选
          </button>
          <button className="icon-button" aria-label="重置筛选" onClick={() => { setTier('全部候选'); setQuery('') }}>
            <RotateCcw size={15} />
          </button>
        </div>

        <div className="candidate-table-wrap screening-table-wrap">
          <table className="candidate-table screening-table">
            <thead>
              <tr>
                <th>排名</th>
                <th>公司</th>
                <th>分层</th>
                <th>
                  综合分 <ArrowDown size={11} />
                </th>
                <th>当日涨跌</th>
                <th>估值状态</th>
                <th>现金流</th>
                <th>趋势</th>
                <th>核心理由</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {candidates.map((candidate) => (
                <tr key={candidate.code}>
                  <td>
                    <span className={`rank-number rank-number--${candidate.rank}`}>{candidate.rank}</span>
                  </td>
                  <td>
                    <div className="company-cell">
                      <span className="mini-company-mark">{candidate.name.slice(0, 1)}</span>
                      <span>
                        <strong>{candidate.name}</strong>
                        <small>
                          {candidate.code} · {candidate.sector}
                        </small>
                      </span>
                    </div>
                  </td>
                  <td>
                    <span className={`tier-badge ${candidate.tier === '核心池' ? 'is-core' : ''}`}>
                      {candidate.tier === '核心池' && <Check size={11} />}
                      {candidate.tier}
                    </span>
                  </td>
                  <td>
                    <div className="table-score-cell">
                      <strong>{candidate.score}</strong>
                      <span>
                        <i style={{ width: `${candidate.score}%` }} />
                      </span>
                    </div>
                  </td>
                  <td className={candidate.change >= 0 ? 'market-up' : 'market-down'}>
                    {candidate.change >= 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
                    {Math.abs(candidate.change).toFixed(2)}%
                  </td>
                  <td>
                    <span className={`valuation-state ${candidate.valuation === '偏低' ? 'is-low' : ''}`}>
                      {candidate.valuation}
                    </span>
                  </td>
                  <td>
                    <span className="factor-score">{candidate.cashflow}</span>
                  </td>
                  <td>
                    <span className="factor-score">{candidate.momentum}</span>
                  </td>
                  <td className="reason-cell">{candidate.reason}</td>
                  <td>
                    <button className="row-action" aria-label={`将${candidate.name}加入观察`}>
                      <BookmarkPlus size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {candidates.length === 0 && <div className="empty-table-state">没有符合当前搜索条件的候选。</div>}
        <footer className="table-footer">
          <span>
            <Info size={13} />
            排名必须结合证据完整度阅读；缺失数据不会被自动填充为中性分。
          </span>
          <span>显示 {candidates.length} / {screeningCandidates.length} 个演示候选</span>
        </footer>
      </section>
    </main>
  )
}
