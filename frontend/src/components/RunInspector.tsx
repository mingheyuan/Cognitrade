import {
  Activity,
  Check,
  ChevronRight,
  Circle,
  Clock3,
  Database,
  FileCheck2,
  FileText,
  Gauge,
  Pause,
  RefreshCcw,
  RotateCcw,
  ShieldCheck,
  Square,
  Waypoints,
} from 'lucide-react'
import { evidenceItems } from '../data'

type RunInspectorProps = {
  running: boolean
  progress: number
  selectedEvidence: string
  onSelectEvidence: (id: string) => void
}

const baseSteps = [
  { name: '识别研究意图', agent: 'Router', threshold: 8 },
  { name: '拆解研究任务', agent: 'Planner', threshold: 22 },
  { name: '采集行情与财务快照', agent: 'Collector', threshold: 46 },
  { name: '召回证据与方法论', agent: 'Retriever', threshold: 68 },
  { name: '交叉校验关键判断', agent: 'Reviewer', threshold: 84 },
  { name: '生成结构化报告', agent: 'Reporter', threshold: 100 },
]

function EvidenceIcon({ type }: { type: (typeof evidenceItems)[number]['type'] }) {
  if (type === 'market') return <Activity size={14} />
  if (type === 'filing') return <FileCheck2 size={14} />
  if (type === 'methodology') return <Gauge size={14} />
  return <FileText size={14} />
}

export function RunInspector({
  running,
  progress,
  selectedEvidence,
  onSelectEvidence,
}: RunInspectorProps) {
  const displayProgress = running ? progress : 100
  const activeStepIndex = baseSteps.findIndex((step) => displayProgress < step.threshold)
  const normalizedActiveIndex = activeStepIndex === -1 ? baseSteps.length : activeStepIndex

  return (
    <aside className="run-inspector" aria-label="运行详情">
      <section className="inspector-section inspector-run-card">
        <div className="section-heading-row">
          <div>
            <span className="section-kicker">CURRENT RUN</span>
            <h2>{running ? '研究执行中' : '研究已完成'}</h2>
          </div>
          <span className={`run-state-badge ${running ? 'is-running' : 'is-complete'}`}>
            {running ? <Activity size={12} /> : <Check size={12} />}
            {running ? '运行中' : '已完成'}
          </span>
        </div>

        <div className="run-progress-copy">
          <span>{running ? '正在编排多 Agent 研究任务' : '全部阶段执行成功'}</span>
          <strong>{displayProgress}%</strong>
        </div>
        <div
          className="progress-track progress-track--large"
          role="progressbar"
          aria-valuenow={displayProgress}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span style={{ width: `${displayProgress}%` }} />
        </div>

        <div className="run-meta-grid">
          <span>
            <Clock3 size={13} />
            <small>耗时</small>
            <strong>{running ? '00:26' : '00:48'}</strong>
          </span>
          <span>
            <Waypoints size={13} />
            <small>并行任务</small>
            <strong>{running ? '3 active' : '6 / 6'}</strong>
          </span>
        </div>

        <div className="run-actions">
          <button disabled={!running}>
            {running ? <Square size={13} /> : <Pause size={13} />}
            {running ? '停止' : '已结束'}
          </button>
          <button>
            <RotateCcw size={13} />
            重放
          </button>
        </div>
      </section>

      <section className="inspector-section">
        <div className="section-heading-row section-heading-row--compact">
          <h3>Agent 执行轨迹</h3>
          <button className="text-button">
            详细事件
            <ChevronRight size={13} />
          </button>
        </div>
        <ol className="agent-timeline">
          {baseSteps.map((step, index) => {
            const complete = index < normalizedActiveIndex
            const active = running && index === normalizedActiveIndex
            return (
              <li key={step.name} className={active ? 'is-active' : complete ? 'is-complete' : ''}>
                <span className="timeline-marker">
                  {complete ? <Check size={11} /> : active ? <Activity size={11} /> : <Circle size={8} />}
                </span>
                <span className="timeline-copy">
                  <strong>{step.name}</strong>
                  <small>{step.agent}</small>
                </span>
                <time>
                  {complete ? `${Math.max(1, Math.round(step.threshold / 6))}.${index}s` : active ? '进行中' : '等待'}
                </time>
              </li>
            )
          })}
        </ol>
      </section>

      <section className="inspector-section">
        <div className="section-heading-row section-heading-row--compact">
          <h3>数据完整度</h3>
          <span className="quality-score">92 / 100</span>
        </div>
        <div className="quality-list">
          {[
            ['行情快照', 100],
            ['财务与估值', 96],
            ['公告与事件', 88],
            ['资金与情绪', 84],
          ].map(([label, value]) => (
            <div className="quality-row" key={label}>
              <span>{label}</span>
              <div className="progress-track">
                <i style={{ width: `${value}%` }} />
              </div>
              <strong>{value}%</strong>
            </div>
          ))}
        </div>
        <div className="quality-note">
          <ShieldCheck size={14} />
          <span>关键结论均有至少 2 个独立来源支撑</span>
        </div>
      </section>

      <section className="inspector-section evidence-section">
        <div className="section-heading-row section-heading-row--compact">
          <h3>关键证据</h3>
          <span className="count-badge">4 / 27</span>
        </div>
        <div className="evidence-list">
          {evidenceItems.map((evidence) => (
            <button
              key={evidence.id}
              className={`evidence-item ${selectedEvidence === evidence.id ? 'is-selected' : ''}`}
              onClick={() => onSelectEvidence(evidence.id)}
            >
              <span className={`evidence-icon evidence-icon--${evidence.type}`}>
                <EvidenceIcon type={evidence.type} />
              </span>
              <span className="evidence-copy">
                <small>
                  {evidence.id} · {evidence.source}
                </small>
                <strong>{evidence.title}</strong>
                <span>{evidence.meta}</span>
              </span>
              <span className="evidence-quality">{evidence.quality}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="inspector-section run-technical-meta">
        <div>
          <Database size={13} />
          <span>run_7f82a1</span>
        </div>
        <div>
          <RefreshCcw size={13} />
          <span>快照 2026-07-10 15:00</span>
        </div>
      </section>
    </aside>
  )
}
