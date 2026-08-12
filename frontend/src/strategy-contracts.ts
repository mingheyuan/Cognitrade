export type BacktestMode = 'fund_only' | 'holdings_portfolio'
export type StrategyLifecycle = 'draft' | 'published' | 'archived'
export type GovernanceState = 'published' | 'verified' | 'promoted'
export type SignalState = 'ALLOW_BUY' | 'WATCH' | 'AVOID' | 'BLOCKED'

export type StrategyDocument = {
  metadata: { schema_version: '1'; description: string }
  pins: {
    uzi_methodology_version: string
    uzi_source_commit: string
    model_id: string
    model_parameters: Record<string, string | number | boolean | null>
    prompt_hash: string
    output_schema_versions: Record<string, string>
    snapshot_schema_version: string
    factor_extractor_version: string
  }
  report_inputs: {
    holding_uzi_required: true
    research_profile: 'deep'
    top_n: number
    independent_holding_signals_enabled: boolean
    enabled_backtest_modes: BacktestMode[]
    supported_fund_types: string[]
  }
  fund_composite: {
    fund_performance_risk: number
    holding_stock_quality: number
    holding_structure_quality: number
  }
  factor_weights: {
    fund_performance_risk: Record<string, number>
    holding_stock_quality: Record<string, number>
    holding_structure_quality: Record<string, number>
  }
  signal_thresholds: {
    buy: number
    watch: number
    minimum_confidence: number
    missing_report_carry_days: number
  }
  quality_gates: {
    normal_coverage: number
    minimum_coverage: number
    max_holding_staleness_days: number
    require_report_allowed: boolean
    require_no_critical: boolean
    require_symbol_match: boolean
    point_in_time_required: true
  }
  execution: {
    technical_rule: 'ma_cross'
    fast_period: number
    slow_period: number
    max_single_stock_pct: number
    rebalance_drift_pct_points: number
  }
  asset_execution: {
    stock: Record<string, string | number>
    exchange_fund: Record<string, string | number>
    otc_fund: Record<string, string | number | boolean>
  }
  schedule_defaults: {
    enabled: boolean
    timezone: 'Asia/Shanghai'
    local_time: string
    settlement_cutoff: string
    skip_non_trading_days: true
  }
}

export type StrategyVersion = {
  id: string
  strategy_id: string
  version_number: number
  content_hash: string
  governance_state: GovernanceState
  artifact_id?: string | null
  artifact_run_id?: string | null
  published_at: string
  published_by: string
  document?: StrategyDocument
}

export type StrategyDraft = {
  strategy_id: string
  base_version_id?: string | null
  draft_revision: number
  document: StrategyDocument
  validation: { valid: boolean; errors: string[] }
  updated_by: string
  updated_at: string
}

export type StrategyAssignment = {
  id: string
  fund_code: string
  fund_name?: string | null
  strategy_id: string
  active_version_id: string
  pending_version_id?: string | null
  effective_from: string
  mode: BacktestMode
  schedule: Record<string, unknown>
  enabled: boolean
  paused: boolean
  created_at: string
  updated_at: string
}

export type StrategyRecord = {
  id: string
  name: string
  kind: string
  system_key?: string | null
  is_system_template: boolean
  lifecycle: StrategyLifecycle
  has_draft: boolean
  draft_revision: number
  version_count: number
  assignment_count: number
  latest_version?: StrategyVersion | null
  archived_at?: string | null
  created_at: string
  updated_at: string
  draft?: StrategyDraft | null
  versions?: StrategyVersion[]
  assignments?: StrategyAssignment[]
}

export type VersionDiff = {
  strategy_id: string
  from: StrategyVersion
  to: StrategyVersion
  changes: Array<{ path: string; before: unknown; after: unknown }>
  unified_diff: string[]
}

export type ReportJob = {
  id: string
  status: string
  job_type: string
  request: Record<string, unknown>
  progress: Record<string, unknown>
  pause_requested: boolean
  cancel_requested: boolean
  error_summary?: string | null
  attempts?: Array<Record<string, unknown>>
  created_at: string
  updated_at: string
}

export type DailyReportSet = {
  id: string
  assignment_id?: string | null
  fund_code: string
  fund_name?: string | null
  as_of_date: string
  effective_date: string
  revision: number
  status: string
  max_top_n: number
  coverage: number
  count_coverage: number
  weight_coverage?: number | null
  data_gaps: string[]
  nodes?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export type TradeSignal = {
  id: string
  assignment_id: string
  strategy_version_id: string
  source_report_set_id: string
  kind: 'fund' | 'holding' | 'holding_stock'
  subject_code: string
  as_of_date: string
  effective_from: string
  expires_after?: string | null
  state: SignalState
  score_lower_bound?: number | null
  score_upper_bound?: number | null
  coverage: number
  confidence: number
  reasons: string[]
  content_hash?: string
  [key: string]: unknown
}

export type SignalCalculation = {
  subject_code?: string
  state: SignalState
  score_lower_bound: number
  score_upper_bound: number
  coverage: number
  confidence: number
  reasons: string[]
}

export type SignalPreviewResult = {
  persisted: false
  assignment_id: string
  strategy_version_id: string
  strategy_content_hash: string
  report_set_id: string
  fund_signal: SignalCalculation
  stock_signals: SignalCalculation[]
}

export type BacktestPreflight = {
  assignment_id: string
  strategy_version_id: string
  strategy_content_hash: string
  fund_code: string
  mode: BacktestMode
  trading_day_count: number
  available_report_count: number
  missing_report_dates: string[]
  signal_count: number
  ready: boolean
  carry_days: number
}

export type StrategyBacktest = {
  id: string
  parent_backtest_run_id?: string | null
  strategy_id: string
  strategy_version_id: string
  strategy_content_hash: string
  fund_code: string
  fund_name?: string | null
  mode: BacktestMode
  start_date: string
  end_date: string
  initial_cash: number
  status: string
  metrics: Record<string, number>
  result?: Record<string, unknown>
  error_summary?: string | null
  created_at: string
  completed_at?: string | null
  [key: string]: unknown
}

export type StrategyVerification = {
  id: string
  strategy_version_id: string
  backtest_run_id: string
  status: string
  checks: Record<string, { passed: boolean; value?: unknown; required?: boolean }>
  thresholds: Record<string, number | boolean>
  machine_passed: boolean
  confirmed_by?: string | null
  confirmed_at?: string | null
  created_at: string
}

export type StrategyEvent = {
  id: string
  event_type: string
  actor_id: string
  payload: Record<string, unknown>
  created_at: string
}

export type PaperStrategyBinding = {
  id: string
  account_id: string
  fund_code: string
  mode: BacktestMode
  strategy_version_id: string
  promoted_by: string
  promoted_at: string
}

export type StrategyPaperOrder = {
  id: string
  binding_id: string
  strategy_version_id: string
  signal_id: string
  source_report_set_id: string
  client_order_id: string
  paper_order_id?: string | null
  symbol: string
  side: 'buy' | 'sell'
  quantity: number
  technical_reason: string
  status: string
  paper_order: Record<string, unknown>
  created_at: string
  idempotent_replay: boolean
}

export type StrategyWorkspaceContext = {
  strategyId?: string
  tab?: 'rules' | 'signals' | 'versions' | 'assignments' | 'backtests' | 'governance'
  fundCode: string
  fundName?: string
  sourceRunId?: string
  sourceArtifactId?: string
  reportSetId?: string
  strategyVersionId?: string
  assignmentId?: string
  mode: BacktestMode
  holdingSymbol?: string
}
