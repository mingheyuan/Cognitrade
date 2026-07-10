import {
  Activity,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Eye,
  Filter,
  LoaderCircle,
  Search,
  Waypoints,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { listAgentOSRuns } from '../api'
import type { AgentOSRunSummary } from '../research-contracts'

type RunsProps = {
  activeRunId?: string
  refreshToken: number
  onSelectRun: (runId: string) => void
}

type RunFilter = 'all' | 'active' | 'completed' | 'attention'

const terminalStatuses = new Set(['completed', 'cancelled', 'failed', 'gated'])

function statusIcon(status: string) {
  if (status === 'completed') return <CheckCircle2 size={12} />
  if (status === 'running' || status === 'ready' || status === 'paused') return <Activity size={12} />
  return <CircleAlert size={12} />
}

function formatTimestamp(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

export function Runs({ activeRunId, refreshToken, onSelectRun }: RunsProps) {
  const [filter, setFilter] = useState<RunFilter>('all')
  const [query, setQuery] = useState('')
  const [runs, setRuns] = useState<AgentOSRunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadRuns = useCallback(async () => {
    setLoading(true)
    try {
      setRuns(await listAgentOSRuns())
      setError('')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to load runs.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadRuns()
  }, [loadRuns, refreshToken])

  const rows = useMemo(() => {
    const term = query.trim().toLowerCase()
    return runs.filter((run) => {
      const filterMatch =
        filter === 'all' ||
        (filter === 'active' && !terminalStatuses.has(run.status)) ||
        (filter === 'completed' && run.status === 'completed') ||
        (filter === 'attention' && ['failed', 'gated', 'cancelled'].includes(run.status))
      const searchMatch = !term || `${run.run_id} ${run.intent} ${run.domain_id}`.toLowerCase().includes(term)
      return filterMatch && searchMatch
    })
  }, [filter, query, runs])

  const completedCount = runs.filter((run) => run.status === 'completed').length
  const activeCount = runs.filter((run) => !terminalStatuses.has(run.status)).length

  return (
    <main className="page-content runs-page">
      <header className="page-heading">
        <div>
          <span className="page-kicker">AGENT OS OBSERVABILITY</span>
          <h1>Research Runs</h1>
          <p>ExecutionGraph state, ArtifactBoard outputs, graph revisions, and terminal gates.</p>
        </div>
        <button className="secondary-button" onClick={() => void loadRuns()} disabled={loading}>
          {loading ? <LoaderCircle size={15} className="spin" /> : <Filter size={15} />}
          Refresh
        </button>
      </header>

      <section className="runs-stat-grid">
        <article><span>Total runs</span><strong>{runs.length}</strong><small>Canonical Agent OS history</small></article>
        <article><span>Completed</span><strong>{completedCount}</strong><small className="signal-positive">Delivery gate passed</small></article>
        <article><span>Active</span><strong>{activeCount}</strong><small>Ready, running, or paused</small></article>
        <article><span>Selected</span><strong>{activeRunId ? '1' : '0'}</strong><small>{activeRunId ?? 'No run selected'}</small></article>
      </section>

      <section className="dashboard-panel run-history-panel">
        <div className="run-history-toolbar">
          <div className="segmented-control" aria-label="Filter runs">
            {(['all', 'active', 'completed', 'attention'] as RunFilter[]).map((item) => (
              <button key={item} className={filter === item ? 'is-active' : ''} onClick={() => setFilter(item)}>
                {item}
              </button>
            ))}
          </div>
          <label className="table-search">
            <Search size={14} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search run ID or intent" />
          </label>
        </div>
        {error && <div className="empty-table-state">{error}</div>}
        <div className="candidate-table-wrap">
          <table className="candidate-table run-history-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Intent</th>
                <th>Domain</th>
                <th>Status</th>
                <th>Graph</th>
                <th>Updated</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => (
                <tr key={run.run_id} className={run.run_id === activeRunId ? 'is-selected' : ''}>
                  <td><strong>{run.run_id}</strong><small>{run.request_id}</small></td>
                  <td><span className="intent-table-badge">{run.intent}</span></td>
                  <td>{run.domain_id}</td>
                  <td>
                    <span className={`status-table-badge status-table-badge--${run.status}`}>
                      {statusIcon(run.status)}
                      {run.status}
                    </span>
                  </td>
                  <td><span className="intent-table-badge">r{run.graph_revision}</span></td>
                  <td><span><Clock3 size={12} /> {formatTimestamp(run.updated_at)}</span></td>
                  <td>
                    <button className="row-action" onClick={() => onSelectRun(run.run_id)} aria-label={`Open ${run.run_id}`}>
                      <Eye size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && !error && rows.length === 0 && <div className="empty-table-state">No matching Agent OS runs.</div>}
        <footer className="table-footer">
          <span><Waypoints size={13} /> Data is read from canonical execution snapshots and ArtifactBoard records.</span>
          <span>{rows.length} runs</span>
        </footer>
      </section>
    </main>
  )
}
