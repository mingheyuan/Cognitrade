export type DimensionStatus = 'available' | 'verified_empty' | 'data_gap' | 'conflict' | 'revoked' | 'unknown'
export type FreshnessStatus = 'fresh' | 'stale' | 'not_applicable' | 'unknown'
export type EvidenceGrade = 'A' | 'B' | 'C' | null
export type MarketDataTab = 'coverage' | 'evidence' | 'jobs' | 'bundles' | 'archive'
export type AssetFamily = 'auto' | 'equity' | 'fund'
export type ResolvedAssetFamily = Exclude<AssetFamily, 'auto'>
export type InstrumentAssetType = 'stock' | 'exchange_fund' | 'otc_fund' | 'index'

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

export interface DimensionDefinition {
  id: string
  label: string
  order: number
  description?: string
  snapshot_type?: string
  freshness_seconds: number | null
  history_requirement?: string
  min_evidence_count?: number
  verified_empty_policy: string | null
  verified_empty_requirement?: string | null
  required_fields?: string[]
  required_sources?: string[]
}

export interface MarketDataContract {
  contract_version: string
  expected_dimension_count: number
  dimensions: DimensionDefinition[]
  asset_family: ResolvedAssetFamily
  collector_count?: number
  server_time?: string
}

export interface InstrumentIdentity {
  symbol: string
  code: string
  asset_type: InstrumentAssetType
  market: string
  exchange: string
  exchange_timezone: string
  security_type: string
  name: string | null
  currency: string
  resolution_source: string
  instrument_id: string
}

export interface InstrumentResolution {
  query: string
  candidates: InstrumentIdentity[]
  resolved: InstrumentIdentity | null
  ambiguous: boolean
}

export interface DimensionCoverage {
  dimension_id: string
  status: DimensionStatus
  freshness_status: FreshnessStatus
  reason: string | null
  snapshot_id: string | null
  evidence_grade: EvidenceGrade
  content_hash?: string | null
  published_at?: string | null
  observed_at?: string | null
  conflict_id?: string | null
}

export interface CoverageCounts {
  available: number
  verified_empty: number
  data_gap: number
  conflict: number
  stale: number
}

export interface BundleReference {
  bundle_id: string
  status?: string
  manifest_hash?: string | null
  revision?: number | null
  published_at?: string | null
}

export interface MarketDataCoverage {
  symbol: string
  market: string
  as_of_date: string
  cutoff_at: string
  expected_dimension_count: number
  counts: CoverageCounts
  structurally_complete: boolean
  report_ready: boolean
  dimensions: DimensionCoverage[]
  latest_bundle: BundleReference | null
  contract_version?: string
}

export interface EvidenceRecord {
  evidence_id: string
  provider?: string | null
  source_url?: string | null
  mime_type?: string | null
  content_hash?: string | null
  grade?: EvidenceGrade
  published_at?: string | null
  observed_at?: string | null
  field_paths?: string[]
  purpose?: string | null
  metadata?: Record<string, JsonValue>
}

export interface SnapshotSummary {
  snapshot_id: string
  symbol: string
  market?: string
  dimension_id: string
  status: DimensionStatus
  evidence_grade: EvidenceGrade
  content_hash?: string | null
  effective_at?: string | null
  published_at?: string | null
  observed_at?: string | null
  ingested_at?: string | null
  revoked_at?: string | null
  revoke_reason?: string | null
  superseded_by_id?: string | null
  source_provider?: string | null
}

export interface SnapshotDetail extends SnapshotSummary {
  contract_version?: string
  payload?: JsonValue
  validation?: Record<string, JsonValue>
  query_manifest?: Record<string, JsonValue>
  field_provenance?: Record<string, JsonValue>
  evidence?: EvidenceRecord[]
  conflicts?: MarketDataConflict[]
}

export interface MarketDataConflict {
  conflict_id: string
  snapshot_id?: string | null
  dimension_id?: string | null
  field_path: string
  status: 'open' | 'resolved' | 'dismissed' | string
  reason?: string | null
  candidates?: Array<{
    candidate_id: string
    value: JsonValue
    provider?: string | null
    evidence_id?: string | null
  }>
  resolved_candidate_id?: string | null
  resolution_reason?: string | null
}

export interface CollectionProgress {
  total: number
  queued: number
  running: number
  completed: number
  failed: number
  skipped: number
}

export interface CollectionTask {
  task_id: string
  symbol: string
  dimension_id: string
  status: string
  attempt: number
  max_attempts?: number
  error_code?: string | null
  error_message?: string | null
  retry_at?: string | null
  snapshot_id?: string | null
}

export interface ExpandedFundHolding {
  rank: number
  source_rank?: number
  symbol: string
  code: string
  name: string
  asset_type?: string
  market: string
  exchange?: string
  status?: string
  fund_weight_pct: number | null
  disclosed_weight_pct?: number | null
  holding_source?: string
  disclosure_date?: string
  missing_dimensions?: string[]
}

export interface CollectionJob {
  job_id: string
  parent_job_id?: string | null
  status: string
  mode: string
  job_type?: string
  asset_family: ResolvedAssetFamily
  top_n: number | null
  symbols: string[]
  dimensions?: string[] | 'all'
  start_date: string
  end_date: string
  max_concurrency: number
  publish_when_complete: boolean
  progress: CollectionProgress
  requested_by?: string | null
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  error?: string | null
  parent_bundle_id?: string | null
  parent_bundle_hash?: string | null
  expanded_holdings: ExpandedFundHolding[]
  child_collection_job_ids: string[]
  tasks?: CollectionTask[]
}

export interface MarketDataBundle {
  bundle_id: string
  symbol: string
  market: string
  as_of_date: string
  cutoff_at: string
  status: string
  contract_version: string
  expected_dimension_count: number
  available_count: number
  verified_empty_count: number
  manifest_hash: string
  revision?: number | null
  published_at?: string | null
  supersedes_bundle_id?: string | null
  report_reference_count?: number
  invalidated_at?: string | null
  invalidation_reason?: string | null
  items?: Array<{
    dimension_id: string
    snapshot_id: string
    content_hash?: string | null
    status?: DimensionStatus
  }>
}

export interface MarketDataProvider {
  provider_id: string
  label: string
  configured: boolean
  installed: boolean
  enabled?: boolean
  healthy?: boolean | null
  capabilities?: string[]
  missing_requirements?: string[]
  last_tested_at?: string | null
  last_error?: string | null
}

export interface MarketDataSubscription {
  subscription_id?: string
  symbol: string
  market: string
  enabled: boolean
  schedule_time?: string
  timezone?: string
  dimensions?: string[] | 'all'
  max_concurrency?: number
  last_run_at?: string | null
  last_status?: string | null
  last_error?: string | null
  scheduler_enabled?: boolean
}

export interface ReportManifestResult {
  manifest_id: string
  manifest_hash?: string
  status: string
  report_run_id?: string | null
  bundle_id?: string | null
  message?: string | null
}

export interface PageResult<T> {
  items: T[]
  total_count: number
  limit: number
  offset: number
}

export interface CoverageQuery {
  symbol: string
  market?: string
  as_of_date: string
  cutoff: string
}

export interface CollectionJobInput {
  symbols: string[]
  asset_family: AssetFamily
  top_n: 10
  start_date: string
  end_date: string
  dimensions: string[] | 'all'
  mode: 'missing_or_stale' | 'force_all'
  max_concurrency: number
  publish_when_complete: boolean
}

export interface ManualSnapshotInput {
  symbol: string
  market: string
  dimension_id: string
  as_of_date: string
  cutoff_at: string
  status: 'available' | 'verified_empty'
  evidence_grade: 'B'
  effective_at?: string
  published_at: string
  reason: string
  empty_reason?: string
  payload: Record<string, JsonValue>
  field_sources: Record<string, JsonValue>
  query_manifest: Record<string, JsonValue>
  evidence: Array<{
    provider: string
    source_url: string
    source_kind: string
    published_at: string
    observed_at: string
    grade: 'B'
    immutable_proof: true
    is_manual: true
    metadata: Record<string, JsonValue>
  }>
}

export interface MarketDataUrlState extends CoverageQuery {
  market: string
  asset_family: AssetFamily
  tab: MarketDataTab
  dimension: string
}
