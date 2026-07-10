import {
  CheckCircle2,
  CircleAlert,
  FileSearch,
  MessageSquareText,
  ShieldAlert,
  Sparkles,
  UserRound,
  Waypoints,
} from 'lucide-react'
import { ResearchComposer } from './ResearchComposer'
import type { AgentOSArtifact, AgentOSReplay, ComposerIntent } from '../research-contracts'

type WorkbenchProps = {
  running: boolean
  progress: number
  lastQuery: string
  onSubmit: (query: string, intent?: ComposerIntent) => void
  onSelectEvidence: (id: string) => void
  run: AgentOSReplay | null
}

function artifactSummary(artifact: AgentOSArtifact): string {
  for (const key of ['summary', 'conclusion', 'verdict', 'title', 'message']) {
    const value = artifact.content[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  const text = JSON.stringify(artifact.content)
  return text.length > 300 ? `${text.slice(0, 297)}...` : text
}

function artifactJson(artifact: AgentOSArtifact): string {
  return JSON.stringify(artifact.content, null, 2)
}

export function Workbench({ running, progress, lastQuery, onSubmit, onSelectEvidence, run }: WorkbenchProps) {
  const graph = run?.snapshot.graph
  const artifacts = (run?.artifacts ?? []).filter((artifact) => artifact.artifact_type !== 'user_request')
  const completed = run?.snapshot.status === 'completed'

  return (
    <div className="workbench-column">
      <header className="research-thread-header">
        <div>
          <div className="thread-context-line">
            <span className="intent-badge">{graph?.intent ?? 'Agent OS'}</span>
            {graph && <span className="stock-code-badge">{graph.domain_id}</span>}
            {graph && <span className="data-cutoff">ExecutionGraph revision {graph.revision}</span>}
          </div>
          <h1>{graph?.objective ?? 'Start a research run'}</h1>
        </div>
        {run && <span className={`run-state-badge ${completed ? 'is-complete' : 'is-running'}`}>{completed ? <CheckCircle2 size={12} /> : <Sparkles size={12} />}{run.snapshot.status}</span>}
      </header>

      <main className="conversation-stream">
        <article className="message-row message-row--user">
          <div className="message-avatar"><UserRound size={15} /></div>
          <div className="user-message">
            <div className="message-meta"><strong>Research request</strong></div>
            <p>{lastQuery || 'Use the composer below to create a capability-driven Agent OS run.'}</p>
          </div>
        </article>

        {running && <section className="live-research-strip" aria-live="polite"><span className="live-agent-orbit"><Sparkles size={15} /></span><div><strong>Agent OS is executing the graph</strong><span>Planner, Runner, ArtifactBoard, and governance are sharing one run state.</span></div><strong>{progress}%</strong></section>}

        {run && <article className="message-row message-row--assistant">
          <div className="message-avatar message-avatar--assistant"><span>C</span></div>
          <div className="assistant-message">
            <div className="assistant-response-header">
              <div><span className="assistant-label">AGENT OS RESULT</span><span className="confidence-label"><CheckCircle2 size={13} /> {artifacts.length} artifacts</span></div>
              <span>{run.snapshot.status}</span>
            </div>

            <section className="thesis-callout">
              <div className="thesis-icon">{completed ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}</div>
              <div><span>Delivery status</span><p>{completed ? 'The execution graph completed and its final artifacts are available below.' : 'This run has not completed delivery. Inspect the graph and gate state before relying on any artifact.'}</p></div>
            </section>

            <section className="report-section">
              <div className="report-section-heading"><div><span className="section-index">01</span><div><h3>Reusable artifacts</h3><p>ArtifactBoard is the only collaboration data plane.</p></div></div><span className="methodology-tag">{graph?.domain_id ?? 'domain'}</span></div>
              <div className="thesis-columns">
                {artifacts.map((artifact, index) => (
                  <article className="argument-card argument-card--bull" key={artifact.artifact_id}>
                    <div className="argument-title"><span><FileSearch size={15} /></span><div><strong>{artifact.artifact_type}</strong><small>{artifact.producer?.capability ?? artifact.producer?.executor ?? 'Agent OS'}</small></div></div>
                    <p>{artifactSummary(artifact)}</p>
                    <button className="citation" onClick={() => onSelectEvidence(artifact.artifact_id)} title="Select artifact">A{index + 1}</button>
                    <pre className="artifact-json">{artifactJson(artifact)}</pre>
                  </article>
                ))}
                {!artifacts.length && <div className="empty-table-state">No artifacts are available for this run.</div>}
              </div>
            </section>

            <footer className="report-footer"><div><ShieldAlert size={14} /><span>Artifacts retain producer, source references, confidence, and graph provenance. They are research outputs, not trading instructions.</span></div><span className="intent-table-badge"><Waypoints size={13} /> {run.events.length} events</span></footer>
          </div>
        </article>}
      </main>

      <div className="composer-dock">
        <ResearchComposer onSubmit={onSubmit} running={running} compact />
        <div className="composer-context-note"><MessageSquareText size={12} /> New requests enter Agent OS through the standard Task Intake path.</div>
      </div>
    </div>
  )
}
