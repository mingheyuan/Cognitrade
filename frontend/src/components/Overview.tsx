import {
  Activity,
  ArrowRight,
  BrainCircuit,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Database,
  Layers3,
  SearchCheck,
  Sparkles,
} from 'lucide-react'
import { useCognitrade } from '../CognitradeContext'
import { INTENT_LABELS } from '../data'
import {
  asString,
  candidatesFromReport,
  reportFromDetail,
  runListItem,
  statusTone,
} from '../live-data'
import type { ResearchIntent } from '../research-contracts'
import { ResearchComposer } from './ResearchComposer'

type OverviewProps = {
  onSubmit: (query: string, intent?: ResearchIntent) => void
  onOpenWorkbench: () => void
  onOpenRuns: () => void
  onOpenRun: (runId: string) => void
}

export function Overview({ onSubmit, onOpenWorkbench, onOpenRuns, onOpenRun }: OverviewProps) {
  const { runs, proposals, systemStatus, selectedRun, screeningRun, submitting } = useCognitrade()
  const selectedReport = reportFromDetail(selectedRun)
  const candidates = candidatesFromReport(reportFromDetail(screeningRun))
  const completed = runs.filter((run) => run.status === 'completed').length
  const active = runs.filter((run) => ['received', 'ready', 'running', 'waiting_external', 'gated'].includes(String(run.status))).length
  const pendingProposals = proposals.filter((proposal) => proposal.status === 'pending').length
  const events = Number(selectedRun?.metrics.counts?.events || selectedRun?.events.events.length || 0)
  const latestRuns = runs.slice(0, 5).map(runListItem)
  const today = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'full' }).format(new Date())

  return (
    <main className="page-content overview-page">
      <section className="overview-hero">
        <div className="overview-hero-copy">
          <div className="eyebrow-line"><CalendarDays size={14} />{today}</div>
          <h1>今天想研究什么？</h1>
          <p>问题会直接提交到 `/api/v1/runs`，完成后自动加载报告、事件、步骤和运行指标。</p>
          <ResearchComposer onSubmit={onSubmit} running={submitting} />
          <div className="query-presets">
            <span>常用研究</span>
            <button onClick={() => onSubmit('筛选现金流质量改善且估值合理的公司', 'stock_screen')}>现金流筛选</button>
            <button onClick={() => onSubmit('梳理人形机器人板块的近期催化与主要风险', 'sector_research')}>板块催化</button>
            <button onClick={() => onSubmit('复核上一轮创新药板块研究判断', 'review_thesis')}>复核判断</button>
          </div>
        </div>
        <aside className="daily-focus-card">
          <div className="daily-focus-topline"><span><Sparkles size={14} />最近选中运行</span><span className="live-data-label">实时 API</span></div>
          {selectedRun ? (
            <>
              <div className="focus-company"><span className="company-mark company-mark--small">C</span><div><strong>{INTENT_LABELS[asString(selectedRun.replay.result?.intent)] || '研究报告'}</strong><small>{selectedRun.runId}</small></div></div>
              <p>{asString(selectedReport.conclusion || selectedRun.replay.display_summary, '后端未提供结论')}</p>
              <div className="focus-score-row">
                <span><small>事件</small><strong>{events}</strong></span>
                <span><small>步骤</small><strong>{selectedRun.steps.steps.length}</strong></span>
                <span><small>证据</small><strong>{Array.isArray(selectedReport.evidence) ? selectedReport.evidence.length : 0}</strong></span>
              </div>
              <button className="primary-inline-button" onClick={onOpenWorkbench}>打开研究报告<ArrowRight size={15} /></button>
            </>
          ) : <div className="inline-empty-state"><Database size={17} />暂无可用运行。</div>}
        </aside>
      </section>

      <section className="overview-stat-grid">
        <article className="overview-stat-card"><span className="stat-icon stat-icon--mint"><Activity size={17} /></span><div><span>已完成研究</span><strong>{completed}</strong><small>{active} 个运行中</small></div></article>
        <article className="overview-stat-card"><span className="stat-icon stat-icon--gold"><SearchCheck size={17} /></span><div><span>当前运行事件</span><strong>{events}</strong><small>来自持久化事件流</small></div></article>
        <article className="overview-stat-card"><span className="stat-icon stat-icon--blue"><Layers3 size={17} /></span><div><span>筛选候选</span><strong>{candidates.length}</strong><small>来自最近 stock_screen 报告</small></div></article>
        <article className="overview-stat-card"><span className="stat-icon stat-icon--rose"><BrainCircuit size={17} /></span><div><span>待审提案</span><strong>{pendingProposals}</strong><small>{proposals.length} 条真实提案</small></div></article>
      </section>

      <div className="overview-grid">
        <section className="dashboard-panel candidate-panel">
          <div className="panel-heading"><div><span className="section-kicker">LATEST SCREEN</span><h2>最近筛选候选</h2></div><button className="text-button" onClick={onOpenWorkbench}>查看报告<ChevronRight size={13} /></button></div>
          {candidates.length ? (
            <div className="candidate-table-wrap"><table className="candidate-table compact-table"><thead><tr><th>公司</th><th>综合分</th><th>涨跌</th><th>核心理由</th><th /></tr></thead><tbody>
              {candidates.slice(0, 5).map((candidate) => <tr key={candidate.code}><td><strong>{candidate.name}</strong><small>{candidate.code}</small></td><td><span className="score-pill">{candidate.score ?? '—'}</span></td><td>{candidate.change === null ? '—' : `${candidate.change >= 0 ? '+' : ''}${candidate.change.toFixed(2)}%`}</td><td>{candidate.reason}</td><td /></tr>)}
            </tbody></table></div>
          ) : <div className="live-empty-state"><Layers3 size={20} /><strong>暂无筛选结果</strong><p>运行一次 stock_screen 后，这里会读取 report.candidates。</p></div>}
        </section>

        <section className="dashboard-panel market-panel api-gap-panel">
          <div className="panel-heading"><div><span className="section-kicker">MARKET DATA</span><h2>市场宽度</h2></div><CircleAlert size={16} /></div>
          <div className="live-empty-state"><CircleAlert size={20} /><strong>后端暂未提供市场宽度 API</strong><p>界面不再显示演示行情。缺口已记录到 `api-todo/`。</p></div>
        </section>

        <section className="dashboard-panel recent-runs-panel">
          <div className="panel-heading"><div><span className="section-kicker">RECENT RUNS</span><h2>最近运行</h2></div><button className="text-button" onClick={onOpenRuns}>全部运行<ChevronRight size={13} /></button></div>
          <div className="recent-run-list">
            {latestRuns.map((run) => (
              <button key={run.id} className="recent-run-row" onClick={() => onOpenRun(run.id)}>
                <span className={`run-icon run-icon--${statusTone(run.status)}`}>{run.status === 'completed' ? <CheckCircle2 size={14} /> : run.status === 'running' ? <Activity size={14} /> : <CircleAlert size={14} />}</span>
                <span><strong>{run.title}</strong><small>{run.intentLabel} · {run.id}</small></span><time>{run.time}</time>
              </button>
            ))}
            {!latestRuns.length && <div className="inline-empty-state"><Database size={16} />暂无运行记录。</div>}
          </div>
        </section>

        <section className="dashboard-panel system-panel">
          <div className="panel-heading"><div><span className="section-kicker">SYSTEM</span><h2>运行时状态</h2></div></div>
          <div className="guardrail-list">
            <div><span className="guardrail-icon"><Database size={15} /></span><span><strong>API</strong><small>{asString(systemStatus?.status, systemStatus ? '已连接' : '不可用')}</small></span></div>
            <div><span className="guardrail-icon"><Sparkles size={15} /></span><span><strong>模型 Provider</strong><small>{systemStatus ? `${asString(systemStatus.provider_name, '未报告')} · ${asString(systemStatus.default_model, '默认模型未报告')}` : '不可用'}</small></span></div>
            <div><span className="guardrail-icon"><SearchCheck size={15} /></span><span><strong>Qdrant</strong><small>{systemStatus ? `${asString(systemStatus.qdrant_mode, '模式未报告')} · ${asString(systemStatus.qdrant_collection_name, 'collection 未报告')}` : '不可用'}</small></span></div>
          </div>
        </section>
      </div>
    </main>
  )
}
