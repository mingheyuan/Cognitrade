import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Bookmark,
  Building2,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  FileSearch,
  GitCompareArrows,
  Lightbulb,
  MessageSquareText,
  MoreHorizontal,
  Radar,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Target,
  TrendingUp,
  UserRound,
} from 'lucide-react'
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
import { priceSeries } from '../data'
import { ResearchComposer } from './ResearchComposer'

type WorkbenchProps = {
  running: boolean
  progress: number
  lastQuery: string
  onSubmit: (query: string) => void
  onSelectEvidence: (id: string) => void
}

const chartTooltipStyle = {
  background: '#101c19',
  border: '1px solid rgba(148, 180, 169, 0.18)',
  borderRadius: '8px',
  color: '#eaf4f0',
  fontSize: '12px',
  boxShadow: '0 10px 30px rgba(0,0,0,.3)',
}

function CitationButton({ id, onSelect }: { id: string; onSelect: (id: string) => void }) {
  return (
    <button className="citation" onClick={() => onSelect(id)} title={`查看证据 ${id}`}>
      {id}
    </button>
  )
}

export function Workbench({ running, progress, lastQuery, onSubmit, onSelectEvidence }: WorkbenchProps) {
  return (
    <div className="workbench-column">
      <header className="research-thread-header">
        <div>
          <div className="thread-context-line">
            <span className="intent-badge">个股体检</span>
            <span className="stock-code-badge">SZ · 300750</span>
            <span className="data-cutoff">数据截至 2026-07-10 15:00</span>
          </div>
          <h1>宁德时代基本面与估值体检</h1>
        </div>
        <div className="thread-header-actions">
          <button className="secondary-button">
            <Bookmark size={15} />
            加入观察
          </button>
          <button className="icon-button" aria-label="更多操作">
            <MoreHorizontal size={17} />
          </button>
        </div>
      </header>

      <main className="conversation-stream">
        <article className="message-row message-row--user">
          <div className="message-avatar">
            <UserRound size={15} />
          </div>
          <div className="user-message">
            <div className="message-meta">
              <strong>你</strong>
              <time>11:41</time>
            </div>
            <p>{lastQuery || '帮我看下宁德时代，重点关注现金流改善是否能支撑当前估值，以及海外扩产风险。'}</p>
          </div>
        </article>

        {running && (
          <section className="live-research-strip" aria-live="polite">
            <span className="live-agent-orbit">
              <Sparkles size={15} />
            </span>
            <div>
              <strong>正在执行新的研究任务</strong>
              <span>
                {progress < 35
                  ? 'Planner 正在拆解问题与选择研究维度'
                  : progress < 70
                    ? 'Collector 与 Retriever 正在并行采集证据'
                    : 'Reviewer 正在交叉校验，准备生成报告'}
              </span>
            </div>
            <strong>{progress}%</strong>
          </section>
        )}

        <article className="message-row message-row--assistant">
          <div className="message-avatar message-avatar--assistant">
            <span>C</span>
          </div>
          <div className="assistant-message">
            <div className="assistant-response-header">
              <div>
                <span className="assistant-label">COGNITRADE RESEARCH</span>
                <span className="confidence-label">
                  <CheckCircle2 size={13} />
                  证据完整度 92%
                </span>
              </div>
              <time>完成于 11:42 · 48 秒</time>
            </div>

            <section className="report-hero">
              <div className="company-identity">
                <span className="company-mark">宁</span>
                <div>
                  <span>300750.SZ</span>
                  <h2>宁德时代</h2>
                  <small>电池 · 创业板 · 大盘成长</small>
                </div>
              </div>
              <div className="price-block">
                <span>演示收盘价</span>
                <strong>255.40</strong>
                <small className="market-up">
                  <ArrowUpRight size={13} />
                  +2.84% · +7.06
                </small>
              </div>
              <div className="research-verdict">
                <span>研究判断</span>
                <strong>中性偏多</strong>
                <small>建议继续跟踪</small>
              </div>
            </section>

            <section className="thesis-callout">
              <div className="thesis-icon">
                <Lightbulb size={17} />
              </div>
              <div>
                <span>核心判断</span>
                <p>
                  经营性现金流质量与相对趋势共同改善，当前估值处于方法论基准区间中部；但海外扩产兑现、价格竞争与资本开支仍是决定估值能否继续修复的三项关键变量。
                </p>
              </div>
              <span className="thesis-confidence">置信度 78%</span>
            </section>

            <section className="report-section">
              <div className="report-section-heading">
                <div>
                  <span className="section-index">01</span>
                  <div>
                    <h3>关键指标</h3>
                    <p>硬数据快照与方法论信号</p>
                  </div>
                </div>
                <button className="text-button">
                  查看完整快照
                  <ArrowRight size={13} />
                </button>
              </div>

              <div className="metric-grid">
                <div className="metric-card">
                  <span>近 30 日相对收益</span>
                  <strong className="market-up">+12.3 pct</strong>
                  <small>
                    <TrendingUp size={12} />
                    相对沪深 300
                  </small>
                </div>
                <div className="metric-card">
                  <span>滚动市盈率 PE</span>
                  <strong>21.8×</strong>
                  <small>历史 3 年分位 46%</small>
                </div>
                <div className="metric-card">
                  <span>经营现金流 / 净利润</span>
                  <strong>1.31×</strong>
                  <small className="signal-positive">
                    <CheckCircle2 size={12} />
                    质量改善
                  </small>
                </div>
                <div className="metric-card">
                  <span>方法论综合得分</span>
                  <strong>79 / 100</strong>
                  <small>6 个维度 · 27 条证据</small>
                </div>
              </div>
            </section>

            <section className="report-section chart-section">
              <div className="report-section-heading">
                <div>
                  <span className="section-index">02</span>
                  <div>
                    <h3>相对趋势</h3>
                    <p>归一化走势，基期 = 100</p>
                  </div>
                </div>
                <div className="range-selector" aria-label="选择时间范围">
                  <button className="is-active">1月</button>
                  <button>3月</button>
                  <button>1年</button>
                </div>
              </div>
              <div className="price-chart" aria-label="宁德时代与沪深 300 相对走势折线图">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={priceSeries} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid stroke="rgba(148,180,169,.10)" strokeDasharray="3 5" vertical={false} />
                    <XAxis
                      dataKey="day"
                      axisLine={false}
                      tickLine={false}
                      tick={{ fill: '#738d84', fontSize: 11 }}
                      minTickGap={22}
                    />
                    <YAxis
                      domain={['dataMin - 2', 'dataMax + 2']}
                      axisLine={false}
                      tickLine={false}
                      tick={{ fill: '#738d84', fontSize: 11 }}
                    />
                    <Tooltip contentStyle={chartTooltipStyle} cursor={{ stroke: 'rgba(125,226,189,.2)' }} />
                    <Legend
                      iconType="plainline"
                      wrapperStyle={{ fontSize: 11, color: '#9bb0a9', paddingTop: 8 }}
                    />
                    <Line
                      type="monotone"
                      dataKey="stock"
                      name="宁德时代"
                      stroke="#f07272"
                      strokeWidth={2.2}
                      dot={false}
                      activeDot={{ r: 4, fill: '#f07272', stroke: '#07110f', strokeWidth: 2 }}
                    />
                    <Line
                      type="monotone"
                      dataKey="benchmark"
                      name="沪深 300"
                      stroke="#6e8a82"
                      strokeWidth={1.5}
                      strokeDasharray="5 5"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="chart-insight">
                <Radar size={14} />
                <span>
                  6 月下旬后相对强度持续抬升，成交额同步扩张，趋势信号与基本面信号方向一致。
                  <CitationButton id="E01" onSelect={onSelectEvidence} />
                </span>
              </div>
            </section>

            <section className="report-section">
              <div className="report-section-heading">
                <div>
                  <span className="section-index">03</span>
                  <div>
                    <h3>多空逻辑与证据</h3>
                    <p>结论与反证同屏呈现</p>
                  </div>
                </div>
              </div>

              <div className="thesis-columns">
                <article className="argument-card argument-card--bull">
                  <div className="argument-title">
                    <span>
                      <ArrowUpRight size={15} />
                    </span>
                    <div>
                      <strong>支撑逻辑</strong>
                      <small>3 项高相关证据</small>
                    </div>
                  </div>
                  <ul>
                    <li>
                      <span>01</span>
                      <p>
                        经营现金流增速高于利润增速，盈利含金量改善。
                        <CitationButton id="E02" onSelect={onSelectEvidence} />
                      </p>
                    </li>
                    <li>
                      <span>02</span>
                      <p>动力电池龙头地位与规模效应仍在，单位成本具韧性。</p>
                    </li>
                    <li>
                      <span>03</span>
                      <p>相对指数趋势转强，资金与基本面信号暂未背离。</p>
                    </li>
                  </ul>
                </article>

                <article className="argument-card argument-card--bear">
                  <div className="argument-title">
                    <span>
                      <ArrowDownRight size={15} />
                    </span>
                    <div>
                      <strong>反方与风险</strong>
                      <small>3 项待验证变量</small>
                    </div>
                  </div>
                  <ul>
                    <li>
                      <span>01</span>
                      <p>
                        海外扩产面临政策、认证与爬坡速度的不确定性。
                        <CitationButton id="E04" onSelect={onSelectEvidence} />
                      </p>
                    </li>
                    <li>
                      <span>02</span>
                      <p>行业价格竞争若加剧，毛利率修复可能弱于预期。</p>
                    </li>
                    <li>
                      <span>03</span>
                      <p>资本开支重新抬头可能削弱自由现金流改善斜率。</p>
                    </li>
                  </ul>
                </article>
              </div>
            </section>

            <section className="report-section valuation-section">
              <div className="report-section-heading">
                <div>
                  <span className="section-index">04</span>
                  <div>
                    <h3>估值区间</h3>
                    <p>Comps + DCF + 现金流韧性</p>
                  </div>
                </div>
                <span className="methodology-tag">Methodology v1.8</span>
              </div>
              <div className="valuation-layout">
                <div className="valuation-scale-card">
                  <div className="valuation-labels">
                    <span>
                      <small>保守</small>
                      <strong>218</strong>
                    </span>
                    <span>
                      <small>基准</small>
                      <strong>262</strong>
                    </span>
                    <span>
                      <small>乐观</small>
                      <strong>308</strong>
                    </span>
                  </div>
                  <div className="valuation-track">
                    <span className="valuation-band" />
                    <i className="valuation-current" style={{ left: '47%' }}>
                      <b>255.4</b>
                    </i>
                  </div>
                  <div className="valuation-axis">
                    <span>200</span>
                    <span>260</span>
                    <span>320</span>
                  </div>
                </div>
                <div className="valuation-summary">
                  <span>基准情景空间</span>
                  <strong className="market-up">+2.6%</strong>
                  <small>当前价格接近基准估值中枢</small>
                  <CitationButton id="E03" onSelect={onSelectEvidence} />
                </div>
              </div>
              <div className="model-warning">
                <CircleAlert size={15} />
                <p>估值对长期增速与资本开支假设敏感；区间用于研究比较，不构成目标价或买卖建议。</p>
              </div>
            </section>

            <section className="report-section next-step-section">
              <div className="next-step-heading">
                <Target size={17} />
                <div>
                  <h3>下一步研究建议</h3>
                  <p>把结论转化为可验证的问题，而不是直接交易指令</p>
                </div>
              </div>
              <div className="next-step-list">
                <button>
                  <span>01</span>
                  <div>
                    <strong>跟踪海外工厂产能利用率</strong>
                    <small>验证海外扩产兑现速度与盈利贡献</small>
                  </div>
                  <ExternalLink size={14} />
                </button>
                <button>
                  <span>02</span>
                  <div>
                    <strong>与比亚迪、亿纬锂能同口径比较</strong>
                    <small>比较现金流、估值与盈利质量</small>
                  </div>
                  <GitCompareArrows size={14} />
                </button>
                <button>
                  <span>03</span>
                  <div>
                    <strong>财报后复核当前判断</strong>
                    <small>用新增硬数据验证三项关键变量</small>
                  </div>
                  <RefreshCw size={14} />
                </button>
              </div>
            </section>

            <footer className="report-footer">
              <div>
                <ShieldAlert size={14} />
                <span>本报告用于研究辅助，不构成任何投资建议。演示数据仅用于界面展示。</span>
              </div>
              <button className="text-button">
                <FileSearch size={14} />
                查看 27 条完整证据链
              </button>
            </footer>
          </div>
        </article>
      </main>

      <div className="composer-dock">
        <div className="follow-up-suggestions">
          <span>继续追问</span>
          {['与比亚迪比较', '展开现金流假设', '列出主要风险证据'].map((suggestion) => (
            <button key={suggestion} onClick={() => onSubmit(suggestion)} disabled={running}>
              {suggestion}
            </button>
          ))}
        </div>
        <ResearchComposer onSubmit={onSubmit} running={running} compact />
        <div className="composer-context-note">
          <MessageSquareText size={12} />
          当前追问会沿用本线程的研究上下文与 27 条证据
          <Building2 size={12} />
          本地工作区
        </div>
      </div>
    </div>
  )
}
