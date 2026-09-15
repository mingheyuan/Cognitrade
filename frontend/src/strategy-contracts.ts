export type BacktestMode = 'fund_only' | 'holdings_portfolio'
export type ResearchProfile = 'lite' | 'medium' | 'deep'
type StrategyLifecycle = 'draft' | 'published' | 'archived'
type GovernanceState = 'published' | 'verified' | 'promoted'
type SignalState = 'ALLOW_BUY' | 'WATCH' | 'AVOID' | 'BLOCKED'

export type StrategyDocumentV1 = {
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
    research_profile: ResearchProfile
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
    otc_fund: {
      subscription_fee_rate_fallback: number
      redemption_fee_rate_fallback: number
      subscription_confirmation_days: number
      redemption_confirmation_days: number
      require_open_status: boolean
      missing_open_status_policy: string
      max_position_pct?: number
      entry_tranche_pct?: number
      max_entry_tranches?: number
      minimum_entry_interval_days?: number
      [key: string]: string | number | boolean | undefined
    }
  }
  schedule_defaults: {
    enabled: boolean
    timezone: 'Asia/Shanghai'
    local_time: string
    settlement_cutoff: string
    skip_non_trading_days: true
  }
}

export type StrategyDocumentV2 = Omit<StrategyDocumentV1,
  'metadata' | 'fund_composite' | 'factor_weights' | 'signal_thresholds' | 'execution'> & {
  metadata: { schema_version: '2'; description: string }
  report_signal: {
    model: 'report_conditions_v1'
    source: 'dashboard.battle_plan'
    extractor_version: 'uzi-battle-plan-v1'
    entry_fraction: 0.5
    entry_valid_trading_days: 3
    evaluation: 'daily_close_next_open'
    position_plan: 'lock_on_entry'
    gap_policy: 'cancel_outside_stop_target'
    weight_policy: 'original_disclosed_no_normalization'
  }
  execution: { max_single_stock_pct: number }
}

export type StrategyDocument = StrategyDocumentV1 | StrategyDocumentV2

export function isReportConditionsDocument(document: StrategyDocument | null | undefined): document is StrategyDocumentV2 {
  return document?.metadata.schema_version === '2'
}

export type ReportExecutionPlan = {
  model: 'report_conditions_v1'
  native_verdict: string
  native_verdict_label: string
  native_overall_score: number | null
  prices: { entry: number | null; stop: number | null; target: number | null }
  original_disclosed_weight_pct: number | null
  entry_fraction: number
  entry_permitted: boolean
  executable: boolean
  errors: string[]
  source_artifact_id: string | null
  source_content_hash: string | null
  source_field: string
  source_node_id: string
  source_report_set_id: string
  source_report_revision: number
  [key: string]: unknown
}

type StrategyVersion = {
  id: string
  strategy_id: string
  version_number: number
  content_hash: string
  governance_state: GovernanceState
  artifact_id?: string | null
  artifact_run_id?: string | null
  published_at: string
  published_by: string
  research_profile?: ResearchProfile
  schema_version?: '1' | '2'
  signal_model?: string
  document?: StrategyDocument
}

type StrategyDraft = {
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
  completed_at?: string | null
}

export type ReportRepairPlan = {
  job_id: string
  automatic_collection: false
  items: Array<{
    symbol: string
    as_of_date: string
    recovery: { kind: string; action: string }
    dimensions: Array<{ dimension: string; reason: string; retryable: boolean }>
    pending_evidence: Array<{ dimension: string; url: string }>
    collection_request: {
      symbols: string[]; asset_family: 'equity'; start_date: string; end_date: string
      dimensions: string[]; mode: 'missing_or_stale' | 'force_all'; max_concurrency: number
      publish_when_complete: false; include_research_archive: boolean
      research_document_requests?: Array<{ dimension: string; url: string }>
    } | null
    notes: string[]
  }>
}

export type ReportEvidenceCollection = {
  id: string
  status: string
  freeze_status?: string
  can_confirm_freeze?: boolean
  publication_candidate?: { proposal_hash: string; degraded?: boolean } | null
  research_archive?: { succeeded?: number; failed?: number; attempted?: number; persisted_snapshot_count?: number } | null
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
  execution_plan?: ReportExecutionPlan | null
  [key: string]: unknown
}

type SignalCalculation = {
  subject_code?: string
  state: SignalState
  score_lower_bound: number
  score_upper_bound: number
  coverage: number
  confidence: number
  reasons: string[]
  execution_plan?: ReportExecutionPlan | null
}

export type SignalPreviewResult = {
  persisted: false
  assignment_id: string
  strategy_version_id: string
  strategy_content_hash: string
  report_set_id: string
  fund_signal: SignalCalculation | null
  stock_signals: SignalCalculation[]
}

export type BacktestPreflight = {
  signal_model?: 'report_conditions_v1' | string
  executable_plan_count?: number
  blocked_plan_count?: number
  executable_plan_coverage?: number
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
  can_run?: boolean
  report_coverage?: number
  signal_coverage?: number
  market_data_checked?: boolean
  market_data_missing?: Array<{ symbol: string; code: string; message: string; suggested_collection?: unknown }>
  signal_uncovered_dates?: string[]
  carry_days: number
}

type BacktestEquityPoint = {
  date: string
  equity?: number
  cash?: number
  market_value?: number
  pending_confirmation_value?: number
  committed_position_value?: number
  position_weight_pct?: number
  value?: number
  return_pct?: number
}

type BacktestTrade = {
  date: string
  order_date?: string
  side: string
  instrument_id?: string
  symbol?: string
  price: number
  quantity: number
  gross_value: number
  cash_after?: number
  fees?: Record<string, number>
  signal_id?: string
  signal_ids?: string[]
  source_report_set_id?: string
  technical_reason?: string
  entry_tranche_index?: number
  entry_tranche_limit?: number
}

type BacktestBenchmark = {
  status: string
  start?: string
  end?: string
  return_pct?: number
  symbol?: string
  name?: string
  reason?: string
  equity_curve?: BacktestEquityPoint[]
}

export type StrategyBacktestResult = {
  schema_version?: string
  mode?: BacktestMode
  period?: { start?: string; end?: string }
  equity_curve: BacktestEquityPoint[]
  trades: BacktestTrade[]
  benchmarks: Record<string, BacktestBenchmark>
  metrics: Record<string, number>
  report_set_ids?: string[]
  execution_adapter?: Record<string, unknown>
  ending_positions?: Record<string, number>
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
  report_policy?: 'existing_only' | 'require_complete'
  benchmark?: Record<string, unknown>
  input_snapshot_hash?: string | null
  status: string
  metrics: Record<string, number>
  result?: StrategyBacktestResult
  result_content_hash?: string | null
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

export type StrategyWorkspaceContext = {
  strategyId?: string
  tab?: 'rules' | 'signals' | 'versions' | 'assignments' | 'backtests' | 'governance'
  fundCode: string
  fundName?: string
  sourceRunId?: string
  sourceArtifactId?: string
  fundBundleId?: string
  reportSetId?: string
  strategyVersionId?: string
  assignmentId?: string
  mode?: BacktestMode
  holdingSymbol?: string
}
