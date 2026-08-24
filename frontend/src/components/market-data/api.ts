import { CognitradeApiError, type ApiConnectionSettings } from '../../api-client'
import type {
  CollectionJob,
  CollectionJobInput,
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

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record) : []
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
  const source = record(payload)
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
  const source = record(payload)
  return {
    query: stringValue(source.query),
    candidates: records(source.candidates).map(normalizeInstrumentIdentity).filter((item): item is InstrumentIdentity => Boolean(item)),
    resolved: normalizeInstrumentIdentity(source.resolved),
    ambiguous: Boolean(source.ambiguous),
  }
}

export function normalizeMarketDataContract(payload: unknown): MarketDataContract {
  const source = record(payload)
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
  const source = record(payload)
  const rawCounts = record(source.counts)
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
    }
  })
  const latest = record(source.latest_bundle)
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
    },
    structurally_complete: Boolean(source.structurally_complete),
    report_ready: Boolean(source.report_ready),
    dimensions,
    latest_bundle: Object.keys(latest).length ? {
      bundle_id: stringValue(latest.bundle_id ?? latest.id),
      status: stringValue(latest.status) || undefined,
      manifest_hash: stringValue(latest.manifest_hash) || null,
      revision: latest.revision == null ? null : numberValue(latest.revision),
      published_at: stringValue(latest.published_at) || null,
    } : null,
    contract_version: stringValue(source.contract_version) || undefined,
  }
}

function normalizeSnapshot(payload: unknown): SnapshotDetail {
  const source = record(payload)
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
    metadata: record(item.metadata) as SnapshotDetail['evidence'] extends Array<infer E> ? E extends { metadata?: infer M } ? M : never : never,
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
    validation: record(source.validation ?? source.validation_json) as SnapshotDetail['validation'],
    query_manifest: record(source.query_manifest ?? source.query_manifest_json) as SnapshotDetail['query_manifest'],
    field_provenance: record(source.field_provenance ?? source.field_sources ?? source.field_sources_json) as SnapshotDetail['field_provenance'],
    evidence,
    conflicts,
  }
}

function normalizeExpandedHolding(payload: unknown, index: number): ExpandedFundHolding {
  const source = record(payload)
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

function normalizeJob(payload: unknown): CollectionJob {
  const source = record(payload)
  const rawProgress = record(source.progress)
  const byStatus = record(rawProgress.by_status)
  const checkpoint = record(source.checkpoint)
  const rawChildJobIds = source.child_collection_job_ids ?? checkpoint.child_job_ids
  const total = numberValue(rawProgress.total)
  const assetFamily = stringValue(source.asset_family).toLowerCase() === 'fund' ? 'fund' : 'equity'
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
    progress: {
      total,
      queued: numberValue(rawProgress.queued ?? byStatus.queued),
      running: numberValue(rawProgress.running ?? byStatus.running),
      completed: numberValue(rawProgress.succeeded ?? byStatus.succeeded ?? byStatus.completed ?? (Object.keys(byStatus).length ? 0 : rawProgress.completed)),
      failed: numberValue(rawProgress.failed ?? byStatus.failed),
      skipped: numberValue(rawProgress.skipped ?? byStatus.skipped ?? byStatus.cancelled),
    },
    requested_by: stringValue(source.requested_by) || null,
    created_at: stringValue(source.created_at),
    started_at: stringValue(source.started_at) || null,
    completed_at: stringValue(source.completed_at) || null,
    error: stringValue(source.error ?? source.error_summary) || null,
    parent_bundle_id: stringValue(checkpoint.parent_bundle_id) || null,
    parent_bundle_hash: stringValue(checkpoint.parent_bundle_hash) || null,
    expanded_holdings: records(source.expanded_holdings ?? checkpoint.holdings).map(normalizeExpandedHolding),
    child_collection_job_ids: Array.isArray(rawChildJobIds) ? rawChildJobIds.map(String) : [],
    tasks: records(source.tasks).map((task) => ({
      task_id: stringValue(task.task_id ?? task.id),
      symbol: stringValue(task.symbol),
      dimension_id: stringValue(task.dimension_id ?? task.dimension),
      status: stringValue(task.status),
      attempt: numberValue(task.attempt ?? task.attempt_count),
      max_attempts: task.max_attempts == null ? undefined : numberValue(task.max_attempts),
      error_code: stringValue(task.error_code) || null,
      error_message: stringValue(task.error_message) || null,
      retry_at: stringValue(task.retry_at ?? task.next_retry_at) || null,
      snapshot_id: stringValue(task.snapshot_id) || null,
    })),
  }
}

function normalizeBundle(payload: unknown): MarketDataBundle {
  const source = record(payload)
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
  const source = record(payload)
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
  }
}

function normalizeSubscription(payload: unknown): MarketDataSubscription {
  const source = record(payload)
  const schedule = record(source.schedule)
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

  async listJobs(values: { symbol?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal) {
    const response = await this.request<PageResult<CollectionJob> | CollectionJob[] | Record<string, unknown>>(`/collection-jobs${queryString({ limit: values.limit, offset: values.offset })}`, { signal })
    const page = normalizePage<unknown>(response as Record<string, unknown>, 'jobs')
    return { ...page, items: page.items.map(normalizeJob) }
  }

  async getJob(jobId: string, signal?: AbortSignal) {
    return normalizeJob(await this.request<unknown>(`/collection-jobs/${encodeURIComponent(jobId)}`, { signal }))
  }

  createJob(payload: CollectionJobInput) {
    return this.request<unknown>('/collection-jobs', { method: 'POST', body: JSON.stringify(payload) }).then(normalizeJob)
  }

  retryFailed(jobId: string) {
    return this.request<unknown>(`/collection-jobs/${encodeURIComponent(jobId)}/retry-failed`, { method: 'POST' }).then(normalizeJob)
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
    const response = record(await this.request<unknown>('/report-manifests', {
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
