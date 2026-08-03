export type ResearchIntent =
  | 'stock_check'
  | 'stock_compare'
  | 'stock_screen'
  | 'fund_research'
  | 'sector_research'
  | 'distill_kol_logic'
  | 'review_thesis'

export type RunStatus =
  | 'received'
  | 'identifying_intent'
  | 'needs_clarification'
  | 'ready'
  | 'running'
  | 'waiting_approval'
  | 'waiting_external'
  | 'gated'
  | 'completed'
  | 'completed_partial'
  | 'cancelled'
  | 'failed'
  | 'timed_out'
  | 'unknown'

export type ResearchRequest = {
  request_id?: string
  thread_id?: string
  user_id?: string
  tenant_id?: string
  idempotency_key?: string
  intent?: ResearchIntent
  user_query: string
  conversation_context?: Array<Record<string, unknown>>
  slots?: Record<string, unknown>
  constraints?: Record<string, unknown>
  clarification_status?: 'not_needed' | 'pending' | 'resolved'
}

export type ProgressState = {
  current_phase?: string | null
  active_agent?: string | null
  latest_event?: string | null
  status_message: string
  percent: number
  updated_at: string
}

export type EvidenceRecord = {
  evidence_id?: string
  id?: string
  source_type?: string
  source_name?: string
  title?: string
  summary?: string
  published_at?: string
  confidence?: number
  quality?: number
  metadata?: Record<string, unknown>
  source_refs?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export type ResearchReport = {
  conclusion?: string
  conclusion_source?: 'main_agent_synthesis' | 'pack_report' | string
  synthesis_available?: boolean
  synthesis_unavailable?: boolean
  evidence?: EvidenceRecord[]
  risk?: string | string[]
  follow_up?: string | string[]
  degraded?: boolean
  degraded_note?: string
  degrade_reason?: string
  scorecard?: Record<string, unknown>
  screening_scorecard?: Record<string, unknown>
  candidates?: Array<Record<string, unknown>>
  methodologies?: Array<Record<string, unknown>>
  review_summary?: Record<string, unknown>
  presentation?: Record<string, unknown>
  valuation_model?: Record<string, unknown>
  investor_panel_review?: Record<string, unknown>
  risk_scan?: Record<string, unknown>
  [key: string]: unknown
}

export type ResearchResult = {
  intent?: ResearchIntent
  report?: ResearchReport
  [key: string]: unknown
}

export type ResearchResponse = {
  request_id: string
  run_id?: string | null
  thread_id?: string | null
  turn_id?: string | null
  task_type?: 'regular' | 'review' | 'compact' | string | null
  mode?: 'core' | 'task' | 'graph' | null
  intent?: ResearchIntent | null
  status: RunStatus
  message: string
  clarification?: {
    question: string
    missing_fields: string[]
  } | null
  progress: ProgressState
  result?: ResearchResult | null
  artifacts: Array<Record<string, unknown>>
  errors: string[]
  created_at: string
  updated_at: string
}

export type RunHistoryRecord = {
  run_id: string
  request_id?: string
  intent?: ResearchIntent | string
  status?: RunStatus | string
  message?: string
  progress?: ProgressState
  display_summary?: string
  child_thread_count?: number
  turn_id?: string | null
  task_type?: string | null
  execution_mode?: string | null
  task_selection?: Record<string, unknown>
  child_turn_ids?: string[]
  created_at?: string
  updated_at?: string
  [key: string]: unknown
}

export type RunEvent = {
  event_id?: string
  event_type?: string
  phase?: string
  agent?: string
  message?: string
  payload?: Record<string, unknown>
  created_at?: string
  [key: string]: unknown
}

export type RunEventsPayload = {
  run_id: string
  request?: ResearchRequest
  summary?: Record<string, unknown>
  display_summary?: string
  events: RunEvent[]
  event_counts?: Record<string, number>
  turn?: Record<string, unknown> | null
  task?: Record<string, unknown> | null
  task_selection?: Record<string, unknown>
  [key: string]: unknown
}

export type RunStep = {
  step_index?: number
  action_id?: string | null
  node_id?: string | null
  phase?: string | null
  agent?: string | null
  objective?: string | null
  status?: string
  tags?: string[]
  supervisor_note?: string | null
  [key: string]: unknown
}

export type RunStepsPayload = {
  run_id: string
  request?: ResearchRequest
  summary?: Record<string, unknown>
  display_summary?: string
  steps: RunStep[]
  step_counts?: Record<string, number>
  turn?: Record<string, unknown> | null
  task_selection?: Record<string, unknown>
  [key: string]: unknown
}

export type RunMetricsPayload = {
  run_id: string
  request?: ResearchRequest
  summary?: Record<string, unknown>
  display_summary?: string
  counts?: Record<string, number>
  rates?: Record<string, number | null>
  latency_ms?: Record<string, unknown>
  degrade_reason_counts?: Record<string, number>
  [key: string]: unknown
}

export type RunReplayPayload = {
  run_id: string
  request?: ResearchRequest
  result?: ResearchResult | null
  summary?: Record<string, unknown>
  display_summary?: string
  events?: RunEvent[]
  artifacts?: Array<Record<string, unknown>>
  snapshot?: Record<string, unknown>
  turn?: Record<string, unknown> | null
  task?: Record<string, unknown> | null
  [key: string]: unknown
}

export type SystemStatus = {
  status?: string
  project_root?: string
  workspace_root?: string
  runs_root?: string
  qdrant_mode?: string
  qdrant_collection_name?: string
  provider_name?: string
  default_model?: string
  total_runs?: number
  status_counts?: Record<string, number>
  recent_runs?: RunHistoryRecord[]
  openai_configured?: boolean
  qdrant_configured?: boolean
  qdrant_available?: boolean
  redis_configured?: boolean
  redis_available?: boolean
  providers?: Record<string, unknown>
  [key: string]: unknown
}

export type RunDetail = {
  runId: string
  replay: RunReplayPayload
  events: RunEventsPayload
  steps: RunStepsPayload
  metrics: RunMetricsPayload
}

export type ApiErrorBody = {
  error?: {
    code?: string
    message?: string
    request_id?: string
  }
  detail?: string | Array<Record<string, unknown>>
}

export type AgentOSDashboardProvenance = {
  artifact_id?: string
  artifact_type?: string
  artifact_version?: number
  run_id?: string
  thread_id?: string
  created_at?: string
  confidence?: number | null
  risk_flags?: string[]
  source_refs?: Array<Record<string, unknown>>
  memory_id?: string
  memory_type?: string
  status?: string
  source_run_ids?: string[]
  source_artifact_ids?: string[]
  evaluations?: Array<Record<string, unknown>>
}

export type AgentOSDashboardItem = {
  data: unknown
  provenance: AgentOSDashboardProvenance
}

export type AgentOSDashboardResource = {
  source: 'artifacts' | 'memory' | 'manifest'
  artifact_type?: string
  layer?: string
  count?: number
  total_count?: number
  items?: AgentOSDashboardItem[]
  value?: unknown
}

export type AgentOSDashboardView = {
  domain_id: string
  view_id: string
  generated_at: string
  resources: Record<string, AgentOSDashboardResource>
}
