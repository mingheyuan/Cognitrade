import {
  Activity,
  Check,
  Circle,
  CircleAlert,
  Clock3,
  Database,
  FileCheck2,
  FileText,
  Gauge,
  ShieldCheck,
  Waypoints,
} from 'lucide-react'
import type { AgentOSArtifact, AgentOSReplay } from '../research-contracts'

type RunInspectorProps = {
  running: boolean
  progress: number
  selectedEvidence: string
  onSelectEvidence: (id: string) => void
  run: AgentOSReplay | null
}

function artifactIcon(artifact: AgentOSArtifact) {
  if (artifact.artifact_type.includes('source') || artifact.artifact_type.includes('evidence')) return <FileCheck2 size={14} />
  if (artifact.artifact_type.includes('analysis') || artifact.artifact_type.includes('report')) return <Gauge size={14} />
  return <FileText size={14} />
}

function contentSummary(content: Record<string, unknown>): string {
  for (const key of ['summary', 'conclusion', 'title', 'message', 'query']) {
    const value = content[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  const text = JSON.stringify(content)
  return text.length > 110 ? `${text.slice(0, 107)}...` : text
}

function formatDuration(events: Array<Record<string, unknown>>): string {
  const timestamps = events
    .map((event) => new Date(String(event.created_at ?? '')).getTime())
    .filter((value) => Number.isFinite(value))
  if (timestamps.length < 2) return '--'
  return `${((Math.max(...timestamps) - Math.min(...timestamps)) / 1000).toFixed(1)}s`
}

export function RunInspector({ running, progress, selectedEvidence, onSelectEvidence, run }: RunInspectorProps) {
  const graph = run?.snapshot.graph
  const state = run?.snapshot.status ?? (running ? 'running' : 'idle')
  const nodes = graph?.nodes.filter((node) => node.node_kind === 'subagent') ?? []
  const artifacts = (run?.artifacts ?? []).filter((artifact) => artifact.artifact_type !== 'user_request')
  const completedNodes = nodes.filter((node) => run?.snapshot.node_states[node.node_id]?.status === 'completed').length
  const displayProgress = running ? progress : nodes.length ? Math.round((completedNodes / nodes.length) * 100) : 0

  return (
    <aside className="run-inspector" aria-label="Execution graph details">
      <section className="inspector-section inspector-run-card">
        <div className="section-heading-row">
          <div>
            <span className="section-kicker">CANONICAL RUN</span>
            <h2>{graph?.objective ?? 'No run selected'}</h2>
          </div>
          <span className={`run-state-badge ${state === 'running' ? 'is-running' : 'is-complete'}`}>
            {state === 'completed' ? <Check size={12} /> : <Activity size={12} />}
            {state}
          </span>
        </div>

        <div className="run-progress-copy">
          <span>{graph ? `${graph.domain_id} / revision ${graph.revision}` : 'Submit a research request to inspect its graph.'}</span>
          <strong>{displayProgress}%</strong>
        </div>
        <div className="progress-track progress-track--large" role="progressbar" aria-valuenow={displayProgress} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${displayProgress}%` }} />
        </div>

        <div className="run-meta-grid">
          <span><Clock3 size={13} /><small>Duration</small><strong>{formatDuration(run?.events ?? [])}</strong></span>
          <span><Waypoints size={13} /><small>Leaf nodes</small><strong>{completedNodes} / {nodes.length}</strong></span>
        </div>
      </section>

      <section className="inspector-section">
        <div className="section-heading-row section-heading-row--compact">
          <h3>ExecutionGraph</h3>
          <span className="count-badge">{nodes.length} nodes</span>
        </div>
        <ol className="agent-timeline">
          {nodes.map((node) => {
            const nodeState = run?.snapshot.node_states[node.node_id]?.status ?? node.status ?? 'pending'
            const complete = nodeState === 'completed'
            const active = nodeState === 'running'
            return (
              <li key={node.node_id} className={active ? 'is-active' : complete ? 'is-complete' : ''}>
                <span className="timeline-marker">{complete ? <Check size={11} /> : active ? <Activity size={11} /> : <Circle size={8} />}</span>
                <span className="timeline-copy"><strong>{node.capability ?? node.node_id}</strong><small>{node.executor ?? node.node_id}</small></span>
                <time>{nodeState}</time>
              </li>
            )
          })}
          {!graph && <li><span className="timeline-marker"><Circle size={8} /></span><span className="timeline-copy"><strong>Waiting for a run</strong><small>Agent OS will populate the graph here.</small></span></li>}
        </ol>
      </section>

      <section className="inspector-section">
        <div className="section-heading-row section-heading-row--compact">
          <h3>Governance gates</h3>
          <span className="quality-score">{run?.snapshot.gate_evaluations?.length ?? 0}</span>
        </div>
        <div className="quality-list">
          {(run?.snapshot.gate_evaluations ?? []).map((gate) => (
            <div className="quality-row" key={gate.gate_id}>
              <span>{gate.gate_id}</span>
              <div className="progress-track"><i style={{ width: gate.verdict === 'pass' ? '100%' : '35%' }} /></div>
              <strong>{gate.verdict}</strong>
            </div>
          ))}
          {(!run?.snapshot.gate_evaluations || run.snapshot.gate_evaluations.length === 0) && <div className="quality-note"><ShieldCheck size={14} /><span>No delivery gates recorded yet.</span></div>}
        </div>
      </section>

      <section className="inspector-section evidence-section">
        <div className="section-heading-row section-heading-row--compact">
          <h3>ArtifactBoard</h3>
          <span className="count-badge">{artifacts.length}</span>
        </div>
        <div className="evidence-list">
          {artifacts.map((artifact) => (
            <button key={artifact.artifact_id} className={`evidence-item ${selectedEvidence === artifact.artifact_id ? 'is-selected' : ''}`} onClick={() => onSelectEvidence(artifact.artifact_id)}>
              <span className="evidence-icon evidence-icon--news">{artifactIcon(artifact)}</span>
              <span className="evidence-copy"><small>{artifact.artifact_type}</small><strong>{contentSummary(artifact.content)}</strong><span>{artifact.producer?.capability ?? artifact.producer?.executor ?? 'Agent OS artifact'}</span></span>
              <span className="evidence-quality">{artifact.confidence == null ? '--' : Math.round(artifact.confidence * 100)}</span>
            </button>
          ))}
          {!artifacts.length && <div className="empty-table-state">No reusable artifacts have been produced.</div>}
        </div>
      </section>

      {run && <section className="inspector-section run-technical-meta"><div><Database size={13} /><span>{run.run_id}</span></div><div><CircleAlert size={13} /><span>{run.events.length} execution events</span></div></section>}
    </aside>
  )
}
