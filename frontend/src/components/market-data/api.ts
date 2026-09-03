import { CognitradeApiError, type ApiConnectionSettings } from '../../api-client'
import { asRecord } from '../../utils/value'
import type {
  CollectionJob,
  CollectionJobInput,
  CollectionRetryInput,
  CoverageQuery,
  ExpandedFundHolding,
  InstrumentIdentity,
  InstrumentResolution,
  ManualSnapshotInput,
  MarketDataBundle,
  MarketDataContract,
  MarketDataCoverage,
  MarketDataProvider,
  MarketDataSubscription,
  PageResult,
  ReportManifestResult,
  SnapshotDetail,
  SnapshotSummary,
} from './types'

type RequestOptions = RequestInit & { signal?: AbortSignal }

function createIdempotencyKey() {
  return `market-data-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`
}

function asV2Base(baseUrl: string) {
  return baseUrl.trim().replace(/\/+$/, '').replace(/\/api\/v1$/, '/api/v2')
}

function queryString<T extends object>(values: T) {
  const query = new URLSearchParams()
  Object.entries(values as Record<string, string | number | boolean | null | undefined>).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value))
  })
  return query.size ? `?${query.toString()}` : ''
}

function normalizePage<T>(payload: PageResult<T> | T[] | Record<string, unknown>, itemKey?: string): PageResult<T> {
  if (Array.isArray(payload)) return { items: payload, total_count: payload.length, limit: payload.length, offset: 0 }
  const record = payload as Record<string, unknown>
  const candidate = (record.items || (itemKey ? record[itemKey] : undefined) || []) as T[]
  const items = Array.isArray(candidate) ? candidate : []
  return {
    items,
    total_count: Number(record.total_count ?? record.total ?? items.length),
    limit: Number(record.limit ?? items.length),
    offset: Number(record.offset ?? 0),
  }
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord) : []
}

function stringValue(value: unknown, fallback = '') {
  return value == null ? fallback : String(value)
}

function numberValue(value: unknown, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function marketValue(value: unknown, fallback = 'cn_equity') {
  const normalized = stringValue(value, fallback).trim().toUpperCase()
  if (['CN', 'A', 'A_SHARE', 'A-SHARE', 'ASHARE', 'CHINA', 'SH', 'SZ', 'CN_EQUITY'].includes(normalized)) return 'CN_EQUITY'
  if (['HK', 'HONG_KONG', 'HONG-KONG', 'HK_EQUITY'].includes(normalized)) return 'HK_EQUITY'
  if (['US', 'USA', 'UNITED_STATES', 'UNITED-STATES', 'US_EQUITY'].includes(normalized)) return 'US_EQUITY'
  if (['FUND', 'CN_FUND'].includes(normalized)) return 'CN_FUND'
  return normalized || fallback.toUpperCase()
}

function normalizeInstrumentIdentity(payload: unknown): InstrumentIdentity | null {
  const source = asRecord(payload)
  const assetType = stringValue(source.asset_type) as InstrumentIdentity['asset_type']
  const symbol = stringValue(source.symbol)
  if (!symbol || !['stock', 'exchange_fund', 'otc_fund', 'index'].includes(assetType)) return null
  return {
    symbol,
    code: stringValue(source.code),
    asset_type: assetType,
    market: marketValue(source.market),
    exchange: stringValue(source.exchange),
    exchange_timezone: stringValue(source.exchange_timezone),
    security_type: stringValue(source.security_type),
    name: stringValue(source.name) || null,
    currency: stringValue(source.currency),
    resolution_source: stringValue(source.resolution_source),
    instrument_id: stringValue(source.instrument_id),
  }
}

export function normalizeInstrumentResolution(payload: unknown): InstrumentResolution {
  const source = asRecord(payload)
  return {
    query: stringValue(source.query),
    candidates: records(source.candidates).map(normalizeInstrumentIdentity).filter((item): item is InstrumentIdentity => Boolean(item)),
    resolved: normalizeInstrumentIdentity(source.resolved),
    ambiguous: Boolean(source.ambiguous),
  }
}

export function normalizeMarketDataContract(payload: unknown): MarketDataContract {
  const source = asRecord(payload)
  const dimensions = records(source.dimensions).map((item, index) => ({
    id: stringValue(item.id ?? item.dimension),
    label: stringValue(item.label, stringValue(item.id ?? item.dimension)),
    order: numberValue(item.order ?? item.ordinal, index),
    description: stringValue(item.description) || undefined,
    snapshot_type: stringValue(item.snapshot_type) || undefined,
    freshness_seconds: item.freshness_seconds == null ? null : numberValue(item.freshness_seconds),
    history_requirement: stringValue(item.history_requirement) || undefined,
    min_evidence_count: item.min_evidence_count == null ? undefined : numberValue(item.min_evidence_count),
    verified_empty_policy: item.verified_empty_policy == null ? null : stringValue(item.verified_empty_policy),
    verified_empty_requirement: stringValue(item.verified_empty_requirement) || null,
    required_fields: Array.isArray(item.required_fields) ? item.required_fields.map(String) : undefined,
    required_sources: Array.isArray(item.required_sources) ? item.required_sources.map(String) : undefined,
  })).sort((left, right) => left.order - right.order)
  return {
    contract_version: stringValue(source.contract_version),
    expected_dimension_count: numberValue(source.expected_dimension_count, dimensions.length),
    dimensions,
    asset_family: stringValue(source.asset_family).toLowerCase() === 'fund' ? 'fund' : 'equity',
    collector_count: source.collector_count == null ? undefined : numberValue(source.collector_count),
    server_time: stringValue(source.server_time) || undefined,
  }
}

export function normalizeMarketDataCoverage(payload: unknown): MarketDataCoverage {
  const source = asRecord(payload)
  const rawCounts = asRecord(source.counts)
  const dimensions = records(source.dimensions).map((item) => {
    const conflict = Boolean(item.conflict)
    const stale = Boolean(item.stale)
    const rawStatus = stringValue(item.status)
    return {
      dimension_id: stringValue(item.dimension_id ?? item.dimension),
      status: conflict ? 'conflict' as const : (rawStatus || 'data_gap') as MarketDataCoverage['dimensions'][number]['status'],
      freshness_status: stale ? 'stale' as const : rawStatus ? 'fresh' as const : 'unknown' as const,
      reason: stringValue(item.reason) || null,
      snapshot_id: stringValue(item.snapshot_id) || null,
      evidence_grade: ['A', 'B', 'C'].includes(stringValue(item.evidence_grade)) ? stringValue(item.evidence_grade) as 'A' | 'B' | 'C' : null,
      content_hash: stringValue(item.content_hash) || null,
      published_at: stringValue(item.published_at) || null,
      observed_at: stringValue(item.observed_at) || null,
      conflict_id: stringValue(item.conflict_id) || null,
      degraded: Boolean(item.degraded),
      excluded_from_scoring: Boolean(item.excluded_from_scoring),
      eligible: Boolean(item.eligible),
      native_quality: stringValue(item.native_quality) || null,
      audit_eligible: Boolean(item.audit_eligible),
      audit_reason: stringValue(item.audit_reason) || null,
      audit_errors: Array.isArray(item.audit_errors) ? item.audit_errors.map(String) : [],
    }
  })
  const latest = asRecord(source.latest_bundle)
  const normalizeUziPrecheck = (value: unknown) => {
    const item = asRecord(value)
    return {
      coverage_pct: numberValue(item.coverage_pct),
      passed_checks: numberValue(item.passed_checks),
      total_checks: numberValue(item.total_checks),
      critical_count: numberValue(item.critical_count),
      warning_count: numberValue(item.warning_count),
      gate_passed: Boolean(item.gate_passed),
      profile: stringValue(item.profile) || undefined,
      gate_policy: stringValue(item.gate_policy) || undefined,
      issues: records(item.issues).map((issue) => ({
        severity: stringValue(issue.severity),
        check: stringValue(issue.check),
        dimension: stringValue(issue.dimension) || undefined,
        message: stringValue(issue.message) || undefined,
      })),
    }
  }
  const byProfile = asRecord(source.uzi_integrity_by_profile)
  return {
    symbol: stringValue(source.symbol),
    market: marketValue(source.market),
    as_of_date: stringValue(source.as_of_date),
    cutoff_at: stringValue(source.cutoff_at),
    expected_dimension_count: numberValue(source.expected_dimension_count, dimensions.length),
    counts: {
      available: numberValue(rawCounts.available ?? source.available_count),
      verified_empty: numberValue(rawCounts.verified_empty ?? source.verified_empty_count),
      data_gap: numberValue(rawCounts.data_gap ?? source.gap_count),
      conflict: numberValue(rawCounts.conflict ?? source.conflict_count),
      stale: numberValue(rawCounts.stale ?? source.stale_count),
      degraded: numberValue(rawCounts.degraded ?? source.degraded_dimension_count),
    },
    structurally_complete: Boolean(source.structurally_complete),
    report_ready: Boolean(source.report_ready),
    report_generatable: Boolean(source.report_generatable ?? source.report_ready),
    resolved_dimension_count: numberValue(
      source.resolved_dimension_count,
      dimensions.filter((item) => item.snapshot_id).length,
    ),
    degraded_dimension_count: numberValue(source.degraded_dimension_count),
    quality: (['strict', 'degraded', 'blocked'].includes(stringValue(source.quality))
      ? stringValue(source.quality)
      : source.report_ready ? 'strict' : 'blocked') as MarketDataCoverage['quality'],
    dimensions,
    latest_bundle: Object.keys(latest).length ? {
      bundle_id: stringValue(latest.bundle_id ?? latest.id),
      status: stringValue(latest.status) || undefined,
      manifest_hash: stringValue(latest.manifest_hash) || null,
      revision: latest.revision == null ? null : numberValue(latest.revision),
      published_at: stringValue(latest.published_at) || null,
    } : null,
    contract_version: stringValue(source.contract_version) || undefined,
    gate_policy: stringValue(source.gate_policy) || undefined,
    uzi_integrity: Object.keys(asRecord(source.uzi_integrity)).length
      ? normalizeUziPrecheck(source.uzi_integrity)
      : undefined,
    uzi_integrity_by_profile: Object.keys(byProfile).length ? {
      lite: normalizeUziPrecheck(byProfile.lite),
      medium: normalizeUziPrecheck(byProfile.medium),
      deep: normalizeUziPrecheck(byProfile.deep),
    } : undefined,
    audit_ready_dimension_count: numberValue(source.audit_ready_dimension_count),
    audit_ready: Boolean(source.audit_ready),
  }
}

function normalizeSnapshot(payload: unknown): SnapshotDetail {
  const source = asRecord(payload)
  const evidence = records(source.evidence).map((item) => ({
    evidence_id: stringValue(item.evidence_id ?? item.id),
    provider: stringValue(item.provider) || null,
    source_url: stringValue(item.source_url) || null,
    mime_type: stringValue(item.mime_type) || null,
    content_hash: stringValue(item.content_hash) || null,
    grade: ['A', 'B', 'C'].includes(stringValue(item.grade)) ? stringValue(item.grade) as 'A' | 'B' | 'C' : null,
    published_at: stringValue(item.published_at) || null,
    observed_at: stringValue(item.observed_at) || null,
    field_paths: Array.isArray(item.field_paths) ? item.field_paths.map(String) : undefined,
    purpose: stringValue(item.purpose) || null,
    metadata: asRecord(item.metadata) as SnapshotDetail['evidence'] extends Array<infer E> ? E extends { metadata?: infer M } ? M : never : never,
  }))
  const conflicts = records(source.conflicts).map((item) => ({
    conflict_id: stringValue(item.conflict_id ?? item.id),
    snapshot_id: stringValue(item.snapshot_id) || null,
    dimension_id: stringValue(item.dimension_id ?? item.dimension) || null,
    field_path: stringValue(item.field_path),
    status: stringValue(item.status, 'open'),
    reason: stringValue(item.reason) || null,
    candidates: records(item.candidates).map((candidate) => ({
      candidate_id: stringValue(candidate.candidate_id ?? candidate.snapshot_id ?? candidate.id),
      value: (candidate.value ?? null) as never,
      provider: stringValue(candidate.provider) || null,
      evidence_id: stringValue(candidate.evidence_id) || null,
    })),
    resolved_candidate_id: stringValue(item.resolved_candidate_id ?? item.selected_snapshot_id) || null,
    resolution_reason: stringValue(item.resolution_reason) || null,
  }))
  return {
    snapshot_id: stringValue(source.snapshot_id ?? source.id),
    symbol: stringValue(source.symbol),
    market: source.market == null ? undefined : marketValue(source.market),
    dimension_id: stringValue(source.dimension_id ?? source.dimension),
    status: (stringValue(source.status, 'unknown')) as SnapshotDetail['status'],
    evidence_grade: ['A', 'B', 'C'].includes(stringValue(source.evidence_grade)) ? stringValue(source.evidence_grade) as 'A' | 'B' | 'C' : null,
    content_hash: stringValue(source.content_hash) || null,
    effective_at: stringValue(source.effective_at) || null,
    published_at: stringValue(source.published_at) || null,
    observed_at: stringValue(source.observed_at) || null,
    ingested_at: stringValue(source.ingested_at) || null,
    revoked_at: stringValue(source.revoked_at) || null,
    revoke_reason: stringValue(source.revoke_reason) || null,
    superseded_by_id: stringValue(source.superseded_by_id) || null,
    source_provider: stringValue(source.source_provider ?? source.provider) || null,
    contract_version: stringValue(source.contract_version) || undefined,
    payload: (source.payload ?? source.payload_json) as SnapshotDetail['payload'],
    validation: asRecord(source.validation ?? source.validation_json) as SnapshotDetail['validation'],
    query_manifest: asRecord(source.query_manifest ?? source.query_manifest_json) as SnapshotDetail['query_manifest'],
    field_provenance: asRecord(source.field_provenance ?? source.field_sources ?? source.field_sources_json) as SnapshotDetail['field_provenance'],
    evidence,
    conflicts,
  }
}

function normalizeExpandedHolding(payload: unknown, index: number): ExpandedFundHolding {
  const source = asRecord(payload)
  return {
    rank: numberValue(source.rank, index + 1),
    source_rank: source.source_rank == null ? undefined : numberValue(source.source_rank),
    symbol: stringValue(source.symbol),
    code: stringValue(source.code),
    name: stringValue(source.name),
    asset_type: stringValue(source.asset_type) || undefined,
    market: marketValue(source.market),
    exchange: stringValue(source.exchange) || undefined,
    status: stringValue(source.status) || undefined,
    fund_weight_pct: source.fund_weight_pct == null ? null : numberValue(source.fund_weight_pct),
    disclosed_weight_pct: source.disclosed_weight_pct == null ? null : numberValue(source.disclosed_weight_pct),
    holding_source: stringValue(source.holding_source) || undefined,
    disclosure_date: stringValue(source.disclosure_date) || undefined,
    missing_dimensions: Array.isArray(source.missing_dimensions) ? source.missing_dimensions.map(String) : undefined,
  }
}

function normalizeDiagnosticBuckets(payload: unknown) {
  return records(payload).map((item) => ({
    key: stringValue(item.key),
    count: numberValue(item.count),
    retryable_count: numberValue(item.retryable_count),
    blocked_count: numberValue(item.blocked_count),
    active_count: numberValue(item.active_count),
    sample_message: stringValue(item.sample_message) || null,
  }))
}

function normalizeTaskSummary(payload: unknown) {
  const source = asRecord(payload)
  if (!Object.keys(source).length) return undefined
  const rawStatuses = asRecord(source.by_status)
  return {
    total: numberValue(source.total),
    by_status: Object.fromEntries(Object.entries(rawStatuses).map(([key, value]) => [key, numberValue(value)])),
    active: numberValue(source.active),
    succeeded: numberValue(source.succeeded),
    skipped: numberValue(source.skipped),
    retryable_failed: numberValue(source.retryable_failed),
    blocked_failed: numberValue(source.blocked_failed),
    by_error_code: normalizeDiagnosticBuckets(source.by_error_code),
    by_dimension: normalizeDiagnosticBuckets(source.by_dimension),
  }
}

function normalizePortfolioSummary(payload: unknown) {
  const source = asRecord(payload)
  if (!Object.keys(source).length) return undefined
  return {
    total_holdings: numberValue(source.total_holdings, 10),
    ready_holdings: numberValue(source.ready_holdings),
    dimensions_per_holding: numberValue(source.dimensions_per_holding, 22),
    total_slots: numberValue(source.total_slots, 220),
    ready_slots: numberValue(source.ready_slots),
    missing_slots: numberValue(source.missing_slots),
    retryable_slots: numberValue(source.retryable_slots),
    blocked_slots: numberValue(source.blocked_slots),
    active_slots: numberValue(source.active_slots),
    attempt: numberValue(source.attempt),
    current_child_job_ids: Array.isArray(source.current_child_job_ids) ? source.current_child_job_ids.map(String) : [],
    by_error_code: normalizeDiagnosticBuckets(source.by_error_code),
    by_dimension: normalizeDiagnosticBuckets(source.by_dimension),
  }
}

function countValue(value: unknown, fallback = 0) {
  return Array.isArray(value) ? value.length : numberValue(value, fallback)
}

function normalizePublicationScope(payload: unknown) {
  const source = asRecord(payload)
  return {
    symbol: stringValue(source.symbol) || undefined,
    market: stringValue(source.market) || undefined,
    as_of_date: stringValue(source.as_of_date) || undefined,
    cutoff_at: stringValue(source.cutoff_at) || undefined,
    can_publish: source.can_publish == null ? undefined : Boolean(source.can_publish),
    quality: stringValue(source.quality ?? source.quality_gate) || undefined,
    missing_dimensions: Array.isArray(source.missing_dimensions) ? source.missing_dimensions.map(String) : [],
    bundle_id: stringValue(source.bundle_id) || null,
  }
}

function qualityGateValue(value: unknown) {
  if (typeof value === 'string') return value
  const source = asRecord(value)
  return stringValue(source.status ?? source.quality ?? source.result ?? source.decision) || undefined
}

function normalizeJob(payload: unknown): CollectionJob {
  const source = asRecord(payload)
  const rawProgress = asRecord(source.progress)
  const byStatus = asRecord(rawProgress.by_status)
  const rawProviderProgress = asRecord(rawProgress.provider)
  const checkpoint = asRecord(source.checkpoint)
  const rawChildJobIds = source.child_collection_job_ids ?? checkpoint.child_job_ids
  const total = numberValue(rawProgress.total)
  const assetFamily = stringValue(source.asset_family).toLowerCase() === 'fund' ? 'fund' : 'equity'
  const rawResolution = asRecord(source.resolution_summary)
  const rawCandidate = asRecord(source.publication_candidate ?? checkpoint.publication_candidate)
  const candidateScopes = records(rawCandidate.scopes).map(normalizePublicationScope)
  const candidatePreviews = records(rawCandidate.previews ?? rawCandidate.bundle_previews ?? rawCandidate.bundles).map(normalizePublicationScope)
  const publicationCandidate = Object.keys(rawCandidate).length ? {
    can_publish: rawCandidate.can_publish == null ? true : Boolean(rawCandidate.can_publish),
    quality_gate: qualityGateValue(rawCandidate.quality_gate ?? rawCandidate.quality),
    proposal_hash: stringValue(rawCandidate.proposal_hash) || undefined,
    degraded: rawCandidate.degraded == null ? undefined : Boolean(rawCandidate.degraded),
    bundle_count: rawCandidate.bundle_count == null ? undefined : numberValue(rawCandidate.bundle_count),
    missing_dimensions: Array.isArray(rawCandidate.missing_dimensions) ? rawCandidate.missing_dimensions.map(String) : [],
    scopes: candidateScopes,
    previews: candidatePreviews,
  } : undefined
  const rawRecovery = asRecord(source.agent_recovery ?? checkpoint.agent_recovery)
  const agentRecovery = Object.keys(rawRecovery).length ? {
    status: stringValue(rawRecovery.status, 'unknown'),
    attempted: countValue(rawRecovery.attempted ?? rawRecovery.attempted_count ?? rawRecovery.attempts),
    succeeded: countValue(rawRecovery.succeeded ?? rawRecovery.succeeded_count ?? rawRecovery.recovered),
    failed: countValue(rawRecovery.failed ?? rawRecovery.failed_count ?? rawRecovery.failures),
    started_at: stringValue(rawRecovery.started_at) || null,
    completed_at: stringValue(rawRecovery.completed_at) || null,
    message: stringValue(rawRecovery.message ?? rawRecovery.error) || null,
  } : undefined
  const parentBundleId = stringValue(source.parent_bundle_id ?? checkpoint.parent_bundle_id) || null
  const rawFreezeStatus = stringValue(source.freeze_status ?? checkpoint.freeze_status).toLowerCase()
  const explicitFreezeStatus = rawFreezeStatus === 'ready' || rawFreezeStatus === 'ready_degraded'
    ? 'awaiting_confirmation'
    : rawFreezeStatus === 'blocked'
      ? 'awaiting_recovery'
      : rawFreezeStatus
  const validFreezeStatuses = new Set(['collecting', 'awaiting_recovery', 'awaiting_confirmation', 'frozen', 'not_ready'])
  const jobStatus = stringValue(source.status, 'unknown').toLowerCase()
  const active = ['queued', 'pending', 'running', 'processing', 'leased'].includes(jobStatus)
  const terminalSuccess = ['completed', 'succeeded', 'published'].includes(jobStatus)
  const hasFrozenReceipt = Boolean(
    parentBundleId
    || checkpoint.bundle_id
    || checkpoint.frozen_at
    || (Array.isArray(checkpoint.bundle_ids) && checkpoint.bundle_ids.length),
  )
  const freezeStatus = validFreezeStatuses.has(explicitFreezeStatus)
    ? explicitFreezeStatus
    : active
      ? 'collecting'
      : ['partial', 'failed', 'completed_partial'].includes(jobStatus)
        ? 'awaiting_recovery'
        : hasFrozenReceipt || (Boolean(source.publish_when_complete) && terminalSuccess)
          ? 'frozen'
          : publicationCandidate?.can_publish || terminalSuccess
            ? 'awaiting_confirmation'
            : 'not_ready'
  return {
    job_id: stringValue(source.job_id ?? source.id),
    parent_job_id: stringValue(source.parent_job_id) || null,
    status: stringValue(source.status, 'unknown'),
    mode: stringValue(source.mode),
    job_type: stringValue(source.job_type) || undefined,
    asset_family: assetFamily,
    top_n: source.top_n == null ? null : numberValue(source.top_n),
    symbols: Array.isArray(source.symbols) ? source.symbols.map(String) : [],
    dimensions: source.dimensions === 'all' ? 'all' : Array.isArray(source.dimensions) ? source.dimensions.map(String) : [],
    start_date: stringValue(source.start_date),
    end_date: stringValue(source.end_date),
    max_concurrency: numberValue(source.max_concurrency, 5),
    publish_when_complete: Boolean(source.publish_when_complete),
    freeze_status: freezeStatus as CollectionJob['freeze_status'],
    can_confirm_freeze: source.can_confirm_freeze == null ? false : Boolean(source.can_confirm_freeze),
    publication_candidate: publicationCandidate,
    agent_recovery: agentRecovery,
    scoped_retry_supported: Boolean(source.scoped_retry_supported),
    progress: {
      total,
      queued: numberValue(rawProgress.queued ?? byStatus.queued),
      running: numberValue(rawProgress.running ?? byStatus.running),
      completed: numberValue(rawProgress.succeeded ?? byStatus.succeeded ?? byStatus.completed ?? (Object.keys(byStatus).length ? 0 : rawProgress.completed)),
      failed: numberValue(rawProgress.failed ?? byStatus.failed),
      skipped: numberValue(rawProgress.skipped ?? byStatus.skipped ?? byStatus.cancelled),
      child_complete: rawProgress.child_complete == null ? undefined : numberValue(rawProgress.child_complete),
      child_total: rawProgress.child_total == null ? undefined : numberValue(rawProgress.child_total),
      provider: Object.keys(rawProviderProgress).length ? {
        as_of_date: stringValue(rawProviderProgress.as_of_date) || undefined,
        phase: stringValue(rawProviderProgress.phase, 'collecting'),
        snapshot_type: stringValue(rawProviderProgress.snapshot_type) || null,
        completed: numberValue(rawProviderProgress.completed),
        total: numberValue(rawProviderProgress.total),
        updated_at: stringValue(rawProviderProgress.updated_at) || undefined,
      } : undefined,
    },
    requested_by: stringValue(source.requested_by) || null,
    created_at: stringValue(source.created_at),
    started_at: stringValue(source.started_at) || null,
    completed_at: stringValue(source.completed_at) || null,
    error: stringValue(source.error ?? source.error_summary) || null,
    parent_bundle_id: parentBundleId,
    parent_bundle_hash: stringValue(checkpoint.parent_bundle_hash) || null,
    expanded_holdings: records(source.expanded_holdings ?? checkpoint.holdings).map(normalizeExpandedHolding),
    child_collection_job_ids: Array.isArray(rawChildJobIds) ? rawChildJobIds.map(String) : [],
    task_summary: normalizeTaskSummary(source.task_summary),
    resolution_summary: Object.keys(rawResolution).length ? {
      total: numberValue(rawResolution.total),
      strict: numberValue(rawResolution.strict),
      degraded: numberValue(rawResolution.degraded),
      skipped: numberValue(rawResolution.skipped),
      failed: numberValue(rawResolution.failed),
      by_status: Object.fromEntries(
        Object.entries(asRecord(rawResolution.by_status)).map(([key, value]) => [key, numberValue(value)]),
      ),
    } : undefined,
    portfolio_summary: normalizePortfolioSummary(source.portfolio_summary),
    tasks: records(source.tasks).map((task) => ({
      task_id: stringValue(task.task_id ?? task.id),
      symbol: stringValue(task.symbol),
      dimension_id: stringValue(task.dimension_id ?? task.dimension),
      status: stringValue(task.status),
      attempt: numberValue(task.attempt ?? task.attempt_count),
      max_attempts: task.max_attempts == null ? undefined : numberValue(task.max_attempts),
      retryable: Boolean(task.retryable),
      error_code: stringValue(task.error_code) || null,
      error_message: stringValue(task.error_message) || null,
      failure_kind: stringValue(task.failure_kind) || null,
      failure_stage: stringValue(task.failure_stage) || null,
      failure_label: stringValue(task.failure_label) || null,
      raw_data_present: Boolean(task.raw_data_present),
      retry_at: stringValue(task.retry_at ?? task.next_retry_at) || null,
      snapshot_id: stringValue(task.snapshot_id) || null,
      resolution_status: stringValue(task.resolution_status) || undefined,
      degraded_count: task.degraded_count == null ? undefined : numberValue(task.degraded_count),
      collection_outcome: stringValue(task.collection_outcome) || null,
      reason_code: stringValue(task.reason_code) || null,
      result_message: stringValue(task.result_message) || null,
      provider_status: stringValue(task.provider_status) || null,
      field_coverage: asRecord(task.field_coverage),
      source_trace: records(task.source_trace),
    })),
  }
}

function normalizeBundle(payload: unknown): MarketDataBundle {
  const source = asRecord(payload)
  return {
    bundle_id: stringValue(source.bundle_id ?? source.id),
    symbol: stringValue(source.symbol),
    market: marketValue(source.market),
    as_of_date: stringValue(source.as_of_date),
    cutoff_at: stringValue(source.cutoff_at),
    status: stringValue(source.status),
    contract_version: stringValue(source.contract_version),
    expected_dimension_count: numberValue(source.expected_dimension_count),
    available_count: numberValue(source.available_count),
    verified_empty_count: numberValue(source.verified_empty_count),
    manifest_hash: stringValue(source.manifest_hash),
    revision: source.revision == null ? null : numberValue(source.revision),
    published_at: stringValue(source.published_at) || null,
    supersedes_bundle_id: stringValue(source.supersedes_bundle_id) || null,
    report_reference_count: numberValue(source.report_reference_count),
    invalidated_at: stringValue(source.invalidated_at) || null,
    invalidation_reason: stringValue(source.invalidation_reason) || null,
    items: records(source.items).map((item) => ({
      dimension_id: stringValue(item.dimension_id ?? item.dimension),
      snapshot_id: stringValue(item.snapshot_id),
      content_hash: stringValue(item.content_hash ?? item.snapshot_content_hash) || null,
      status: (stringValue(item.status ?? item.snapshot_status, 'unknown')) as NonNullable<MarketDataBundle['items']>[number]['status'],
    })),
  }
}

function normalizeProvider(payload: unknown): MarketDataProvider {
  const source = asRecord(payload)
  const rawHistorical = asRecord(source.historical_capabilities)
  return {
    provider_id: stringValue(source.provider_id ?? source.id),
    label: stringValue(source.label ?? source.name ?? source.provider_id),
    configured: Boolean(source.configured),
    installed: Boolean(source.installed),
    enabled: source.enabled == null ? undefined : Boolean(source.enabled),
    healthy: source.healthy == null ? null : Boolean(source.healthy),
    capabilities: Array.isArray(source.capabilities) ? source.capabilities.map(String) : [],
    missing_requirements: Array.isArray(source.missing_requirements) ? source.missing_requirements.map(String) : [],
    last_tested_at: stringValue(source.last_tested_at) || null,
    last_error: stringValue(source.last_error) || null,
    supports_historical_reconstruction: Boolean(source.supports_historical_reconstruction),
    historical_capabilities: Object.fromEntries(
      Object.entries(rawHistorical)
        .filter(([, value]) => ['current_only', 'dated_series', 'published_archive'].includes(stringValue(value)))
        .map(([key, value]) => [key, stringValue(value)]),
    ) as MarketDataProvider['historical_capabilities'],
  }
}

function normalizeSubscription(payload: unknown): MarketDataSubscription {
  const source = asRecord(payload)
  const schedule = asRecord(source.schedule)
  return {
    subscription_id: stringValue(source.subscription_id ?? source.id) || undefined,
    symbol: stringValue(source.symbol),
    market: marketValue(source.market),
    enabled: Boolean(source.enabled),
    schedule_time: stringValue(source.schedule_time ?? source.local_time ?? schedule.local_time, '23:15'),
    timezone: stringValue(source.timezone ?? source.exchange_timezone ?? schedule.timezone, 'Asia/Shanghai'),
    dimensions: source.dimensions === 'all' ? 'all' : Array.isArray(source.dimensions) ? source.dimensions.map(String) : [],
    max_concurrency: numberValue(source.max_concurrency, 5),
    last_run_at: stringValue(source.last_run_at ?? source.updated_at) || null,
    last_status: stringValue(source.last_status) || null,
    last_error: stringValue(source.last_error) || null,
    scheduler_enabled: source.scheduler_enabled == null ? undefined : Boolean(source.scheduler_enabled),
  }
}

export class MarketDataCenterApi {
  private readonly base: string
  private readonly token: string

  constructor(settings: ApiConnectionSettings) {
    this.base = `${asV2Base(settings.baseUrl)}/finance/market-data`
    this.token = settings.token
  }

  async resolveInstrument(query: string, assetFamily: 'auto' | 'equity' | 'fund' = 'auto', signal?: AbortSignal) {
    return normalizeInstrumentResolution(await this.request<unknown>('/instruments/resolve', {
      method: 'POST',
      body: JSON.stringify({ query, asset_family: assetFamily }),
      signal,
    }))
  }

  async getContract(market: string, signal?: AbortSignal) {
    return normalizeMarketDataContract(await this.request<unknown>(`/contract${queryString({ market })}`, { signal }))
  }

  async getCoverage(values: CoverageQuery, signal?: AbortSignal) {
    const cutoffAt = values.cutoff === 'now' ? new Date().toISOString() : values.cutoff && values.cutoff !== 'eod' ? values.cutoff : undefined
    const response = await this.request<unknown>(`/coverage${queryString({
      symbol: values.symbol,
      market: values.market,
      as_of_date: values.as_of_date,
      cutoff_at: cutoffAt,
    })}`, { signal })
    return normalizeMarketDataCoverage(response)
  }

  async listSnapshots(values: CoverageQuery & { dimension_id?: string; limit?: number; offset?: number }, signal?: AbortSignal) {
    const response = await this.request<PageResult<SnapshotSummary> | SnapshotSummary[] | Record<string, unknown>>(`/snapshots${queryString({
      symbol: values.symbol,
      dimension_id: values.dimension_id,
      as_of_date: values.as_of_date,
      limit: values.limit,
      offset: values.offset,
    })}`, { signal })
    const page = normalizePage<unknown>(response as Record<string, unknown>, 'snapshots')
    return { ...page, items: page.items.map((item) => normalizeSnapshot(item)) }
  }

  async getSnapshot(snapshotId: string, signal?: AbortSignal) {
    return normalizeSnapshot(await this.request<unknown>(`/snapshots/${encodeURIComponent(snapshotId)}`, { signal }))
  }

  async listJobs(values: { symbol?: string; asset_family?: 'equity' | 'fund'; as_of_date?: string; status?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal) {
    const response = await this.request<PageResult<CollectionJob> | CollectionJob[] | Record<string, unknown>>(`/collection-jobs${queryString({
      symbol: values.symbol,
      asset_family: values.asset_family,
      as_of_date: values.as_of_date,
      status: values.status,
      limit: values.limit,
      offset: values.offset,
    })}`, { signal })
    const page = normalizePage<unknown>(response as Record<string, unknown>, 'jobs')
    return { ...page, items: page.items.map(normalizeJob) }
  }

  async getJob(jobId: string, signal?: AbortSignal) {
    return normalizeJob(await this.request<unknown>(`/collection-jobs/${encodeURIComponent(jobId)}`, { signal }))
  }

  createJob(payload: CollectionJobInput) {
    return this.request<unknown>('/collection-jobs', {
      method: 'POST',
      body: JSON.stringify({ ...payload, publish_when_complete: false }),
    }).then(normalizeJob)
  }

  retryFailed(jobId: string, scope?: CollectionRetryInput) {
    const scoped = Boolean(scope?.symbols?.length || scope?.dimensions?.length)
    return this.request<unknown>(`/collection-jobs/${encodeURIComponent(jobId)}/retry-failed`, {
      method: 'POST',
      body: scoped ? JSON.stringify(scope) : undefined,
    }).then(normalizeJob)
  }

  runAgentRecovery(jobId: string) {
    return this.request<unknown>(`/collection-jobs/${encodeURIComponent(jobId)}/agent-recovery`, {
      method: 'POST',
    }).then((payload) => {
      const response = asRecord(payload)
      return normalizeJob(Object.keys(asRecord(response.job)).length ? response.job : payload)
    })
  }

  confirmFreeze(jobId: string, proposalHash: string, acknowledgeDegraded: boolean) {
    return this.request<unknown>(`/collection-jobs/${encodeURIComponent(jobId)}/confirm-freeze`, {
      method: 'POST',
      body: JSON.stringify({
        proposal_hash: proposalHash,
        acknowledge_degraded: acknowledgeDegraded,
      }),
    }).then((payload) => {
      const response = asRecord(payload)
      return normalizeJob(Object.keys(asRecord(response.job)).length ? response.job : payload)
    })
  }

  cancelJob(jobId: string) {
    return this.request<unknown>(`/collection-jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }).then(normalizeJob)
  }

  createManualSnapshot(payload: ManualSnapshotInput) {
    return this.request<unknown>('/manual-snapshots', { method: 'POST', body: JSON.stringify(payload) }).then(normalizeSnapshot)
  }

  revokeSnapshot(snapshotId: string, payload: { reason: string; superseded_by_id?: string }) {
    return this.request<SnapshotDetail>(`/snapshots/${encodeURIComponent(snapshotId)}/revoke`, { method: 'POST', body: JSON.stringify(payload) })
  }

  resolveConflict(conflictId: string, payload: { selected_snapshot_id?: string; resolution: 'select_candidate' | 'mark_invalid' | 'manual_override'; reason: string; manual_value?: unknown }) {
    return this.request<Record<string, unknown>>(`/conflicts/${encodeURIComponent(conflictId)}/resolve`, { method: 'POST', body: JSON.stringify(payload) })
  }

  async listBundles(values: CoverageQuery & { limit?: number; offset?: number }, signal?: AbortSignal) {
    const response = await this.request<PageResult<MarketDataBundle> | MarketDataBundle[] | Record<string, unknown>>(`/bundles${queryString({
      symbol: values.symbol,
      as_of_date: values.as_of_date,
      limit: values.limit,
      offset: values.offset,
    })}`, { signal })
    const page = normalizePage<unknown>(response as Record<string, unknown>, 'bundles')
    return { ...page, items: page.items.map(normalizeBundle) }
  }

  async getBundle(bundleId: string, signal?: AbortSignal) {
    return normalizeBundle(await this.request<unknown>(`/bundles/${encodeURIComponent(bundleId)}`, { signal }))
  }

  publishBundle(payload: { symbol: string; market: string; as_of_date: string; cutoff_at?: string }) {
    return this.request<unknown>('/bundles/publish', { method: 'POST', body: JSON.stringify(payload) }).then(normalizeBundle)
  }

  async listSubscriptions(symbol?: string, signal?: AbortSignal) {
    const response = await this.request<PageResult<MarketDataSubscription> | MarketDataSubscription[] | Record<string, unknown>>('/subscriptions', { signal })
    const page = normalizePage<unknown>(response as Record<string, unknown>, 'subscriptions')
    const items = page.items.map(normalizeSubscription).filter((item) => !symbol || item.symbol === symbol)
    return { ...page, items, total_count: items.length }
  }

  updateSubscription(symbol: string, payload: MarketDataSubscription) {
    return this.request<unknown>(`/subscriptions/${encodeURIComponent(symbol)}`, {
      method: 'PUT',
      body: JSON.stringify({
        market: payload.market,
        enabled: payload.enabled,
        timezone: payload.timezone,
        local_time: payload.schedule_time || '23:15',
        dimensions: payload.dimensions || 'all',
        max_concurrency: payload.max_concurrency || 5,
      }),
    }).then(normalizeSubscription)
  }

  async listProviders(signal?: AbortSignal) {
    const response = await this.request<PageResult<MarketDataProvider> | MarketDataProvider[] | Record<string, unknown>>('/providers', { signal })
    const page = normalizePage<unknown>(response as Record<string, unknown>, 'providers')
    return { ...page, items: page.items.map(normalizeProvider) }
  }

  testProvider(providerId: string) {
    return this.request<unknown>(`/providers/${encodeURIComponent(providerId)}/test`, { method: 'POST' }).then(normalizeProvider)
  }

  async createReportManifest(payload: {
    symbol: string
    market: string
    as_of_date: string
    evaluation_at?: string
    profile: 'lite' | 'medium' | 'deep'
    asset_family: 'equity' | 'fund'
    fund_bundle_id?: string
  }) {
    const response = asRecord(await this.request<unknown>('/report-manifests', {
      method: 'POST',
      body: JSON.stringify({
        symbols: [payload.symbol],
        market_by_symbol: { [payload.symbol]: payload.market },
        as_of_date: payload.as_of_date,
        evaluation_at: payload.evaluation_at,
        report_kind: payload.asset_family === 'fund' ? 'fund_top_holdings' : 'single_stock',
        asset_family: payload.asset_family,
        fund_bundle_id: payload.asset_family === 'fund' ? payload.fund_bundle_id : undefined,
        workflow_pins: { profile: payload.profile },
        weights: {},
      }),
    }))
    return {
      manifest_id: stringValue(response.manifest_id ?? response.id),
      manifest_hash: stringValue(response.manifest_hash) || undefined,
      status: stringValue(response.status, 'frozen'),
      report_run_id: stringValue(response.report_run_id) || null,
      bundle_id: stringValue(response.bundle_id) || null,
      message: stringValue(response.message) || null,
    } satisfies ReportManifestResult
  }

  private async request<T>(path: string, init: RequestOptions = {}): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    if (init.body) headers.set('Content-Type', 'application/json')
    if (this.token) headers.set('Authorization', `Bearer ${this.token}`)
    const method = String(init.method || 'GET').toUpperCase()
    if (method !== 'GET' && method !== 'HEAD' && !headers.has('Idempotency-Key')) {
      headers.set('Idempotency-Key', createIdempotencyKey())
    }
    let response: Response
    try {
      response = await fetch(`${this.base}${path}`, { ...init, headers })
    } catch (error) {
      if (init.signal?.aborted) throw error
      throw new CognitradeApiError(error instanceof Error ? error.message : '无法连接行情数据中心。', 0, 'network_error')
    }
    if (!response.ok) {
      const contentType = response.headers.get('content-type') || ''
      const body = contentType.includes('application/json') ? await response.json() as Record<string, unknown> : null
      const nested = body?.error as Record<string, unknown> | undefined
      throw new CognitradeApiError(
        String(nested?.message || body?.message || `行情数据中心请求失败（HTTP ${response.status}）`),
        response.status,
        String(nested?.code || body?.code || '') || undefined,
        String(nested?.request_id || response.headers.get('X-Request-ID') || '') || undefined,
      )
    }
    if (response.status === 204) return {} as T
    const payload = await response.json() as T | { data: T }
    if (payload && typeof payload === 'object' && 'data' in payload) return payload.data
    return payload as T
  }
}
