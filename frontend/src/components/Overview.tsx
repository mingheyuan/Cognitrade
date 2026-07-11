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
import { useCognitrade } from '../CognitradeContext'
import { INTENT_LABELS } from '../data'
import {
  asString,
  dashboardCandidates,
  dashboardItems,
  dashboardSectorSignals,
  hasFinalDelivery,
  isActiveStatus,
  reportFromDetail,
  runListItem,
  statusTone,
} from '../live-data'
import type { ResearchIntent } from '../research-contracts'
import { ResearchComposer } from './ResearchComposer'

type OverviewProps = {
  onSubmit: (query: string, intent?: ResearchIntent, constraints?: Record<string, unknown>) => void
  onOpenWorkbench: () => void
  onOpenRuns: () => void
  onOpenRun: (runId: string) => void
}

const chartTooltipStyle = {
  background: '#101c19',
  border: '1px solid rgba(148, 180, 169, 0.18)',
  borderRadius: '8px',
  color: '#eaf4f0',
  fontSize: '12px',
}

export function Overview({ onSubmit, onOpenWorkbench, onOpenRuns, onOpenRun }: OverviewProps) {
  const {
    runs,
    overviewDashboard,
    methodologyDashboard,
    systemStatus,
    selectedRun,
    selectedRunId,
    submitting,
  } = useCognitrade()
  const selectedReport = reportFromDetail(selectedRun)
  const selectedStatus = String(runs.find((run) => run.run_id === selectedRunId)?.status || '')
  const synthesisPending = selectedReport.protocol === 'agent_os'
    && selectedReport.synthesis_available === false
    && !hasFinalDelivery(selectedRun)
    && (submitting || isActiveStatus(selectedStatus) || Boolean(selectedRun))
  const candidates = dashboardCandidates(overviewDashboard)
  const sectorSignals = dashboardSectorSignals(overviewDashboard)
  const verifiedPolicyMemory = dashboardItems(methodologyDashboard, 'verified_policy_memory')
  const completed = runs.filter((run) => run.status === 'completed').length
  const active = runs.filter((run) => ['received', 'ready', 'running', 'waiting_external', 'waiting_approval'].includes(String(run.status))).length
  const events = Number(selectedRun?.metrics.counts?.events || selectedRun?.events.events.length || 0)
  const latestRuns = runs.slice(0, 5).map(runListItem)
  const today = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'full' }).format(new Date())

  return (
    <main className="page-content overview-page">
      <section className="overview-hero">
        <div className="overview-hero-copy">
          <div className="eyebrow-line"><CalendarDays size={14} />{today}</div>
          <h1>今天想研究什么？</h1>
          <p>问题会直接提交到 `/api/v1/runs`，候选与板块信号则读取 DomainPack 声明的 Agent OS Dashboard。</p>
          <ResearchComposer onSubmit={onSubmit} running={submitting} />
          <div className="query-presets">
            <span>常用研究</span>
            <button onClick={() => onSubmit('筛选现金流质量改善且估值合理的公司', 'stock_screen')}>现金流筛选</button>
            <button onClick={() => onSubmit('梳理人形机器人板块的近期催化与主要风险', 'sector_research')}>板块催化</button>
            <button
              onClick={() => onSubmit(
                '复核上一轮研究判断，并用最新证据检查结论是否仍成立',
                'review_thesis',
                selectedRunId ? { review_target_run_id: selectedRunId } : undefined,
              )}
            >复核判断</button>
          </div>
        </div>
        <aside className="daily-focus-card">
          <div className="daily-focus-topline"><span><Sparkles size={14} />最近选中运行</span><span className="live-data-label">实时 API</span></div>
          {selectedRun ? (
            <>
              <div className="focus-company"><span className="company-mark company-mark--small">C</span><div><strong>{INTENT_LABELS[asString(selectedRun.replay.result?.intent)] || '研究报告'}</strong><small>{selectedRun.runId}</small></div></div>
              <p>{synthesisPending
                ? 'Pack 已完成证据整理，Main Agent 正在生成最终回答。'
                : selectedReport.synthesis_available === false
                  ? '该运行已完成交付，但没有保存 Main Agent 动态结论，请重试。'
                : asString(selectedReport.conclusion || selectedRun.replay.display_summary, '后端未提供结论')}</p>
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
        <article className="overview-stat-card"><span className="stat-icon stat-icon--blue"><Layers3 size={17} /></span><div><span>筛选候选</span><strong>{overviewDashboard?.resources.candidates?.total_count ?? candidates.length}</strong><small>来自 screening_result Artifact</small></div></article>
        <article className="overview-stat-card"><span className="stat-icon stat-icon--rose"><BrainCircuit size={17} /></span><div><span>已验证策略记忆</span><strong>{verifiedPolicyMemory.length}</strong><small>仅含 verified / promoted 记录</small></div></article>
      </section>

      <div className="overview-grid">
        <section className="dashboard-panel candidate-panel">
          <div className="panel-heading"><div><span className="section-kicker">LATEST SCREEN</span><h2>最近筛选候选</h2></div><button className="text-button" onClick={onOpenRuns}>查看运行<ChevronRight size={13} /></button></div>
          {candidates.length ? (
            <div className="candidate-table-wrap"><table className="candidate-table compact-table"><thead><tr><th>公司</th><th>综合分</th><th>涨跌</th><th>核心理由</th><th /></tr></thead><tbody>
              {candidates.slice(0, 5).map((candidate) => <tr key={`${candidate.runId}-${candidate.code}`}><td><strong>{candidate.name}</strong><small>{candidate.code}</small></td><td><span className="score-pill">{candidate.score === null ? '—' : candidate.score.toFixed(2)}</span></td><td className={candidate.change === null ? '' : candidate.change >= 0 ? 'market-up' : 'market-down'}>{candidate.change === null ? '—' : `${candidate.change >= 0 ? '+' : ''}${candidate.change.toFixed(2)}%`}</td><td>{candidate.reason}</td><td><button className="row-action" aria-label={`打开 ${candidate.name} 的来源运行`} disabled={!candidate.runId} onClick={() => onOpenRun(candidate.runId)}><ArrowRight size={14} /></button></td></tr>)}
            </tbody></table></div>
          ) : <div className="live-empty-state"><Layers3 size={20} /><strong>暂无筛选结果</strong><p>运行一次 stock_screen 后，这里会读取带来源的 screening_result Artifact。</p></div>}
        </section>

        <section className="dashboard-panel market-panel">
          <div className="panel-heading"><div><span className="section-kicker">SECTOR SIGNALS</span><h2>板块研究信号</h2></div><span className="panel-meta">最近可用 Artifact</span></div>
          {sectorSignals.length ? (
            <div className="breadth-chart" aria-label="板块研究信号条形图">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={sectorSignals} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
                  <CartesianGrid horizontal={false} stroke="rgba(148,180,169,.09)" />
                  <XAxis type="number" domain={[0, 100]} hide />
                  <YAxis type="category" dataKey="name" width={72} axisLine={false} tickLine={false} tick={{ fill: '#9bb0a9', fontSize: 11 }} />
                  <Tooltip contentStyle={chartTooltipStyle} cursor={{ fill: 'rgba(125,226,189,.05)' }} />
                  <Bar dataKey="score" radius={[0, 3, 3, 0]} barSize={9}>{sectorSignals.map((item, index) => <Cell key={`${item.runId}-${item.name}`} fill={index < 3 ? '#7de2bd' : '#3d6358'} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : <div className="live-empty-state"><CircleAlert size={20} /><strong>暂无板块分析</strong><p>运行 sector_research 后，这里会读取 sector_analysis Artifact。</p></div>}
          <div className="breadth-footer"><CircleAlert size={13} />数值来自来源字段或 Artifact 证据覆盖度，不等同于涨跌预测。</div>
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
            <div><span className="guardrail-icon"><SearchCheck size={15} /></span><span><strong>板块资源</strong><small>{sectorSignals.length ? `${sectorSignals.length} 个来源板块` : '等待 sector_analysis'}</small></span></div>
          </div>
        </section>
      </div>
    </main>
  )
}
