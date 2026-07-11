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
  RefreshCcw,
  RotateCcw,
  ShieldCheck,
  Square,
  Waypoints,
} from 'lucide-react'
import { useCognitrade } from '../CognitradeContext'
import {
  asNumber,
  asString,
  evidenceFromReport,
  formatDate,
  isActiveStatus,
  latestEvent,
  reportFromDetail,
  statusFromDetail,
  statusLabel,
  stepProgress,
  type EvidenceItem,
} from '../live-data'

type RunInspectorProps = {
  selectedEvidence: string
  onSelectEvidence: (id: string) => void
}

function EvidenceIcon({ type }: { type: EvidenceItem['type'] }) {
  if (type === 'market') return <Activity size={14} />
  if (type === 'filing') return <FileCheck2 size={14} />
  if (type === 'methodology') return <Gauge size={14} />
  return <FileText size={14} />
}

export function RunInspector({ selectedEvidence, onSelectEvidence }: RunInspectorProps) {
  const { selectedRunId, selectedRun, submitting, cancelRun, reloadRun } = useCognitrade()
  const report = reportFromDetail(selectedRun)
  const evidences = evidenceFromReport(report)
  const steps = selectedRun?.steps.steps || []
  const events = selectedRun?.events.events || []
  const currentEvent = latestEvent(events)
  const status = statusFromDetail(selectedRun)
  const running = submitting || isActiveStatus(status)
  const progress = submitting ? 0 : status === 'completed' ? 100 : stepProgress(steps)
  const counts = selectedRun?.metrics.counts || {}
  const fallbackRate = asNumber(selectedRun?.metrics.rates?.fallback_rate)

  return (
    <aside className="run-inspector" aria-label="运行详情">
      <section className="inspector-section inspector-run-card">
        <div className="section-heading-row">
          <div><span className="section-kicker">CURRENT RUN</span><h2>{selectedRunId ? statusLabel(status) : '未选择运行'}</h2></div>
          <span className={`run-state-badge ${status === 'completed' ? 'is-complete' : 'is-running'}`}>
            {status === 'completed' ? <Check size={12} /> : <Activity size={12} />}{selectedRunId ? statusLabel(status) : '空闲'}
          </span>
        </div>

        <div className="run-progress-copy"><span>{submitting ? '同步研究请求执行中' : asString(currentEvent?.message, '等待运行事件')}</span><strong>{progress}%</strong></div>
        <div className={`progress-track progress-track--large ${submitting ? 'is-indeterminate' : ''}`} role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}><span style={{ width: submitting ? '30%' : `${progress}%` }} /></div>

        <div className="run-meta-grid">
          <span><Clock3 size={13} /><small>最后事件</small><strong>{currentEvent?.created_at ? formatDate(currentEvent.created_at) : '—'}</strong></span>
          <span><Waypoints size={13} /><small>执行步骤</small><strong>{steps.length}</strong></span>
        </div>

        <div className="run-actions">
          <button disabled={!selectedRunId || !running || submitting} onClick={() => selectedRunId && void cancelRun(selectedRunId)}><Square size={13} />停止</button>
          <button disabled={!selectedRunId} onClick={() => selectedRunId && void reloadRun(selectedRunId)}><RotateCcw size={13} />刷新</button>
        </div>
      </section>

      <section className="inspector-section">
        <div className="section-heading-row section-heading-row--compact"><h3>Agent 执行轨迹</h3><span className="count-badge">{steps.length}</span></div>
        {steps.length ? (
          <ol className="agent-timeline">
            {steps.map((step, index) => {
              const stepStatus = asString(step.status, 'pending')
              const complete = ['completed', 'skipped'].includes(stepStatus)
              const active = ['active', 'running'].includes(stepStatus)
              return (
                <li key={asString(step.node_id || step.action_id, `step-${index}`)} className={active ? 'is-active' : complete ? 'is-complete' : ''}>
                  <span className="timeline-marker">{complete ? <Check size={11} /> : active ? <Activity size={11} /> : <Circle size={8} />}</span>
                  <span className="timeline-copy"><strong>{asString(step.objective || step.node_id, `步骤 ${index + 1}`)}</strong><small>{asString(step.agent || step.executor || step.capability || step.phase, '未指定 Agent')}</small></span>
                  <time>{stepStatus}</time>
                </li>
              )
            })}
          </ol>
        ) : <div className="inline-empty-state"><Waypoints size={16} />后端未返回步骤。</div>}
      </section>

      <section className="inspector-section">
        <div className="section-heading-row section-heading-row--compact"><h3>运行质量</h3><span className="quality-score">{report.degraded ? 'DEGRADED' : 'LIVE'}</span></div>
        <div className="quality-list">
          {[
            ['事件', Number(counts.events || events.length)],
            ['证据', evidences.length],
            ['子 Agent', Number(counts.subagents || 0)],
            ['降级项', Number(counts.degradations || 0)],
          ].map(([label, value]) => (
            <div className="quality-row" key={label}><span>{label}</span><div className="progress-track"><i style={{ width: `${Math.min(100, Number(value) * 10)}%` }} /></div><strong>{value}</strong></div>
          ))}
        </div>
        <div className="quality-note"><ShieldCheck size={14} /><span>{fallbackRate === null ? '后端未计算 fallback_rate' : `fallback rate ${(fallbackRate * 100).toFixed(1)}%`}</span></div>
      </section>

      <section className="inspector-section evidence-section">
        <div className="section-heading-row section-heading-row--compact"><h3>关键证据</h3><span className="count-badge">{evidences.length}</span></div>
        <div className="evidence-list">
          {evidences.map((evidence) => (
            <button key={evidence.id} className={`evidence-item ${selectedEvidence === evidence.id ? 'is-selected' : ''}`} onClick={() => onSelectEvidence(evidence.id)}>
              <span className={`evidence-icon evidence-icon--${evidence.type}`}><EvidenceIcon type={evidence.type} /></span>
              <span className="evidence-copy"><small>{evidence.id} · {evidence.source}</small><strong>{evidence.title}</strong><span>{evidence.meta}</span></span>
              <span className="evidence-quality">{evidence.quality || '—'}</span>
            </button>
          ))}
          {!evidences.length && <div className="inline-empty-state"><FileText size={16} />暂无结构化证据。</div>}
        </div>
      </section>

      <section className="inspector-section run-technical-meta">
        <div><Database size={13} /><span>{selectedRunId || 'no run'}</span></div>
        <div><RefreshCcw size={13} /><span>{events.length} events</span></div>
        {currentEvent && <div><ChevronRight size={13} /><span>{asString(currentEvent.event_type, 'event')}</span></div>}
      </section>
    </aside>
  )
}
