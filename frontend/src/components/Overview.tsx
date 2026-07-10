import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BrainCircuit,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Database,
  FileText,
  Layers3,
  ListChecks,
  SearchCheck,
  Sparkles,
  TrendingUp,
} from 'lucide-react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useEffect, useState } from 'react'
import { listAgentOSRuns } from '../api'
import { marketBreadth, screeningCandidates } from '../data'
import { ResearchComposer } from './ResearchComposer'
import type { AgentOSRunSummary, ComposerIntent } from '../research-contracts'

type OverviewProps = {
  onSubmit: (query: string, intent?: ComposerIntent) => void
  running: boolean
  onOpenWorkbench: () => void
  onOpenRuns: () => void
}

const chartTooltipStyle = {
  background: '#101c19',
  border: '1px solid rgba(148, 180, 169, 0.18)',
  borderRadius: '8px',
  color: '#eaf4f0',
  fontSize: '12px',
}

export function Overview({ onSubmit, running, onOpenWorkbench, onOpenRuns }: OverviewProps) {
  const [recentRuns, setRecentRuns] = useState<AgentOSRunSummary[]>([])

  useEffect(() => {
    void listAgentOSRuns().then(setRecentRuns).catch(() => setRecentRuns([]))
  }, [])

  const runHistory = recentRuns.map((run) => ({
    id: run.run_id,
    title: run.intent,
    intent: run.domain_id,
    status: run.status,
    evidence: run.graph_revision,
    time: run.updated_at,
  }))

  return (
    <main className="page-content overview-page">
      <section className="overview-hero">
        <div className="overview-hero-copy">
          <div className="eyebrow-line">
            <CalendarDays size={14} />
            2026 年 7 月 10 日 · 周五
          </div>
          <h1>今天想研究什么？</h1>
          <p>从一个问题开始，Cognitrade 会组织数据、证据和 Agent 协作完成研究。</p>
          <ResearchComposer onSubmit={onSubmit} running={running} />
          <div className="query-presets">
            <span>常用研究</span>
            <button onClick={() => onSubmit('筛选现金流质量改善且估值合理的公司')}>现金流筛选</button>
            <button onClick={() => onSubmit('梳理人形机器人板块的近期催化与主要风险')}>板块催化</button>
            <button onClick={() => onSubmit('复核上一轮创新药板块研究判断')}>复核判断</button>
          </div>
        </div>
        <aside className="daily-focus-card">
          <div className="daily-focus-topline">
            <span>
              <Sparkles size={14} />
              今日研究焦点
            </span>
            <span className="demo-data-label">演示快照</span>
          </div>
          <div className="focus-company">
            <span className="company-mark company-mark--small">宁</span>
            <div>
              <strong>宁德时代</strong>
              <small>300750 · 电池</small>
            </div>
            <span className="market-up">+2.84%</span>
          </div>
          <p>现金流质量与相对趋势形成共振，海外扩产兑现仍是核心观察变量。</p>
          <div className="focus-score-row">
            <span>
              <small>研究评分</small>
              <strong>79</strong>
            </span>
            <span>
              <small>证据完整度</small>
              <strong>92%</strong>
            </span>
            <span>
              <small>新增证据</small>
              <strong>+6</strong>
            </span>
          </div>
          <button className="primary-inline-button" onClick={onOpenWorkbench}>
            打开研究报告
            <ArrowRight size={15} />
          </button>
        </aside>
      </section>

      <section className="overview-stat-grid">
        <article className="overview-stat-card">
          <span className="stat-icon stat-icon--mint">
            <Activity size={17} />
          </span>
          <div>
            <span>本周完成研究</span>
            <strong>18</strong>
            <small className="signal-positive">较上周 +5</small>
          </div>
          <span className="mini-spark mini-spark--one" aria-hidden="true" />
        </article>
        <article className="overview-stat-card">
          <span className="stat-icon stat-icon--gold">
            <SearchCheck size={17} />
          </span>
          <div>
            <span>已验证证据</span>
            <strong>327</strong>
            <small>来自 12 类数据源</small>
          </div>
          <span className="mini-spark mini-spark--two" aria-hidden="true" />
        </article>
        <article className="overview-stat-card">
          <span className="stat-icon stat-icon--blue">
            <Layers3 size={17} />
          </span>
          <div>
            <span>观察候选</span>
            <strong>24</strong>
            <small>核心池 9 · 观察池 15</small>
          </div>
          <span className="mini-spark mini-spark--three" aria-hidden="true" />
        </article>
        <article className="overview-stat-card">
          <span className="stat-icon stat-icon--rose">
            <BrainCircuit size={17} />
          </span>
          <div>
            <span>方法论版本</span>
            <strong className="stat-version">v1.8</strong>
            <small className="warning-copy">2 项提案待审核</small>
          </div>
          <span className="mini-spark mini-spark--four" aria-hidden="true" />
        </article>
      </section>

      <div className="overview-grid">
        <section className="dashboard-panel candidate-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">WATCHLIST</span>
              <h2>优先研究候选</h2>
            </div>
            <button className="text-button">
              查看候选池
              <ChevronRight size={13} />
            </button>
          </div>
          <div className="candidate-table-wrap">
            <table className="candidate-table compact-table">
              <thead>
                <tr>
                  <th>公司</th>
                  <th>综合分</th>
                  <th>当日</th>
                  <th>核心理由</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {screeningCandidates.slice(0, 4).map((candidate) => (
                  <tr key={candidate.code}>
                    <td>
                      <strong>{candidate.name}</strong>
                      <small>{candidate.code}</small>
                    </td>
                    <td>
                      <span className="score-pill">{candidate.score}</span>
                    </td>
                    <td className={candidate.change >= 0 ? 'market-up' : 'market-down'}>
                      {candidate.change >= 0 ? '+' : ''}
                      {candidate.change.toFixed(2)}%
                    </td>
                    <td>{candidate.reason}</td>
                    <td>
                      <button className="row-action" aria-label={`研究${candidate.name}`}>
                        <ArrowUpRight size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="dashboard-panel breadth-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">SECTOR SIGNALS</span>
              <h2>板块研究热度</h2>
            </div>
            <span className="panel-meta">近 7 日</span>
          </div>
          <div className="breadth-chart" aria-label="板块研究热度条形图">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={marketBreadth} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid horizontal={false} stroke="rgba(148,180,169,.09)" />
                <XAxis type="number" domain={[0, 100]} hide />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={52}
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: '#9bb0a9', fontSize: 11 }}
                />
                <Tooltip contentStyle={chartTooltipStyle} cursor={{ fill: 'rgba(125,226,189,.05)' }} />
                <Bar dataKey="score" radius={[0, 3, 3, 0]} barSize={9}>
                  {marketBreadth.map((item, index) => (
                    <Cell key={item.name} fill={index < 3 ? '#7de2bd' : '#3d6358'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="breadth-footer">
            <CircleAlert size={13} />
            热度代表研究信号密度，不等同于涨跌预测
          </div>
        </section>

        <section className="dashboard-panel recent-runs-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">RECENT RUNS</span>
              <h2>最近研究运行</h2>
            </div>
            <button className="text-button" onClick={onOpenRuns}>
              全部记录
              <ChevronRight size={13} />
            </button>
          </div>
          <div className="recent-run-list">
            {runHistory.slice(0, 4).map((run) => (
              <button key={run.id} className="recent-run-item" onClick={onOpenRuns}>
                <span className={`run-list-icon ${run.status === '运行中' ? 'is-running' : ''}`}>
                  {run.status === '已完成' ? (
                    <CheckCircle2 size={14} />
                  ) : run.status === '运行中' ? (
                    <Activity size={14} />
                  ) : (
                    <CircleAlert size={14} />
                  )}
                </span>
                <span className="recent-run-copy">
                  <strong>{run.title}</strong>
                  <small>
                    {run.intent} · {run.evidence} 条证据
                  </small>
                </span>
                <span className="recent-run-time">
                  <strong>{run.status}</strong>
                  <small>{run.time}</small>
                </span>
              </button>
            ))}
          </div>
        </section>

        <section className="dashboard-panel system-panel">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">SYSTEM HEALTH</span>
              <h2>研究基础设施</h2>
            </div>
            <span className="health-badge">
              <span /> 正常
            </span>
          </div>
          <div className="system-health-list">
            <div>
              <span className="system-icon">
                <Database size={15} />
              </span>
              <span>
                <strong>Provider Chain</strong>
                <small>行情 · 财务 · 公告</small>
              </span>
              <b>3 / 3</b>
            </div>
            <div>
              <span className="system-icon">
                <FileText size={15} />
              </span>
              <span>
                <strong>Qdrant Evidence</strong>
                <small>混合检索就绪</small>
              </span>
              <b>Ready</b>
            </div>
            <div>
              <span className="system-icon">
                <ListChecks size={15} />
              </span>
              <span>
                <strong>Methodology Memory</strong>
                <small>最后更新 2 小时前</small>
              </span>
              <b>v1.8</b>
            </div>
          </div>
          <div className="system-panel-footer">
            <TrendingUp size={13} />
            本周运行成功率 <strong>96.8%</strong>
          </div>
        </section>
      </div>
    </main>
  )
}
