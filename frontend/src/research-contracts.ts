export type ResearchIntent =
  | 'stock_check'
  | 'stock_compare'
  | 'stock_screen'
  | 'sector_research'
  | 'distill_kol_logic'
  | 'review_thesis'
  | 'methodology_evolution'

export type ComposerIntent = ResearchIntent | 'auto'

export type ResearchStatus =
  | 'accepted'
  | 'needs_clarification'
  | 'running'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'timed_out'

export type ResearchRequest = {
  request_id?: string
  intent?: ResearchIntent
  user_query: string
  conversation_context?: Array<Record<string, unknown>>
  slots?: Record<string, unknown>
  constraints?: Record<string, unknown>
}

export type ProgressState = {
  current_phase?: string
  active_agent?: string
  latest_event?: string
  status_message: string
  percent: number
  updated_at: string
}

export type EvidenceRecord = {
  source_type?: string
  source_name?: string
  summary?: string
  published_at?: string
  confidence?: number
  metadata?: Record<string, unknown>
}

export type ResearchReport = {
  conclusion?: string
  evidence?: EvidenceRecord[]
  risk?: string | string[]
  follow_up?: string | string[]
  degraded?: boolean
  degrade_reason?: string
  scorecard?: Record<string, unknown>
  screening_scorecard?: Record<string, unknown>
  candidates?: Array<Record<string, unknown>>
  methodologies?: Array<Record<string, unknown>>
  review_summary?: Record<string, unknown>
}

export type ResearchResponse = {
  request_id: string
  run_id?: string
  intent?: ResearchIntent
  status: ResearchStatus
  message: string
  clarification?: {
    question: string
    missing_fields: string[]
  }
  progress: ProgressState
  result?: {
    protocol?: 'agent_os'
    graph_id?: string
    graph_revision?: number
    artifact_ids?: string[]
    final_artifact_ids?: string[]
    report?: ResearchReport
  }
  artifacts: Array<Record<string, unknown>>
  errors: string[]
  created_at: string
  updated_at: string
}

export type AgentOSRunSummary = {
  run_id: string
  request_id: string
  intent: string
  domain_id: string
  status: string
  graph_id: string
  graph_revision: number
  updated_at: string
}

export type AgentOSArtifact = {
  artifact_id: string
  artifact_type: string
  schema_version: string
  content: Record<string, unknown>
  source_refs?: Array<Record<string, unknown>>
  parent_artifact_ids?: string[]
  confidence?: number | null
  risk_flags?: string[]
  status?: string
  created_at?: string
  producer?: {
    executor?: string
    capability?: string
    domain_id?: string
  }
}

export type AgentOSNode = {
  node_id: string
  node_kind: 'compound' | 'subagent'
  capability?: string | null
  executor?: string | null
  objective?: string | null
  status?: string
  depends_on?: string[]
  output_artifact_ids?: string[]
}

export type AgentOSReplay = {
  run_id: string
  request?: Record<string, unknown> | null
  snapshot: {
    run_id: string
    status: string
    graph: {
      graph_id: string
      domain_id: string
      intent?: string | null
      objective: string
      revision: number
      nodes: AgentOSNode[]
    }
    node_states: Record<string, { status: string; output_artifact_ids?: string[] }>
    gate_evaluations?: Array<{ gate_id: string; verdict: string; reason: string; blocking: boolean }>
  }
  artifacts: AgentOSArtifact[]
  events: Array<Record<string, unknown>>
}

export interface ResearchGateway {
  createRun(request: ResearchRequest): Promise<ResearchResponse>
  getRun(runId: string): Promise<ResearchResponse>
  cancelRun(runId: string): Promise<ResearchResponse>
  subscribeToRun(runId: string, onEvent: (event: unknown) => void): () => void
}
