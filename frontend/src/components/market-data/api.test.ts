import { afterEach, describe, expect, it, vi } from 'vitest'
import { MarketDataCenterApi, normalizeInstrumentResolution, normalizeMarketDataContract, normalizeMarketDataCoverage } from './api'
import type { ManualSnapshotInput, MarketDataSubscription } from './types'

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('MarketDataCenterApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('normalizes the resolver wrapper without guessing identity from the query', () => {
    const resolution = normalizeInstrumentResolution({
      query: '020464',
      candidates: [{
        symbol: '020464', code: '020464', asset_type: 'otc_fund', market: 'CN_FUND', exchange: 'OTC',
        exchange_timezone: 'Asia/Shanghai', security_type: 'mutual_fund', name: '测试基金', currency: 'CNY',
        resolution_source: 'fund_security_master', instrument_id: 'otc_fund:OTC:020464',
      }],
      resolved: {
        symbol: '020464', code: '020464', asset_type: 'otc_fund', market: 'CN_FUND', exchange: 'OTC',
        exchange_timezone: 'Asia/Shanghai', security_type: 'mutual_fund', name: '测试基金', currency: 'CNY',
        resolution_source: 'fund_security_master', instrument_id: 'otc_fund:OTC:020464',
      },
      ambiguous: false,
    })

    expect(resolution.resolved).toMatchObject({ symbol: '020464', asset_type: 'otc_fund', market: 'CN_FUND' })
    expect(resolution.candidates).toHaveLength(1)
    expect(resolution.ambiguous).toBe(false)
  })

  it('normalizes the backend-native dimension/ordinal and flat coverage contract', () => {
    const contract = normalizeMarketDataContract({
      contract_version: 'v1',
      expected_dimension_count: 2,
      dimensions: [
        { dimension: 'later', ordinal: 1, label: '后', freshness_seconds: 10, verified_empty_policy: 'never' },
        { dimension: 'first', ordinal: 0, label: '先', freshness_seconds: 20, verified_empty_policy: 'bounded_zero' },
      ],
    })
    expect(contract.dimensions.map((item) => [item.id, item.order])).toEqual([['first', 0], ['later', 1]])

    const coverage = normalizeMarketDataCoverage({
      symbol: '600519.SH', market: 'cn_equity', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
      expected_dimension_count: 2, available_count: 1, verified_empty_count: 0, gap_count: 0, conflict_count: 1, stale_count: 1,
      structurally_complete: true, report_ready: false, report_generatable: true,
      gate_policy: 'uzi-3.9.4-native-data-self-review-medium',
      uzi_integrity: { profile: 'medium', coverage_pct: 50, critical_count: 0, warning_count: 1, gate_passed: true, issues: [] },
      uzi_integrity_by_profile: {
        lite: { profile: 'lite', coverage_pct: 100, critical_count: 0, warning_count: 0, gate_passed: true, issues: [] },
        medium: { profile: 'medium', coverage_pct: 50, critical_count: 0, warning_count: 1, gate_passed: true, issues: [] },
        deep: { profile: 'deep', coverage_pct: 50, critical_count: 0, warning_count: 1, gate_passed: true, issues: [] },
      },
      audit_ready_dimension_count: 1, audit_ready: false,
      dimensions: [
        { dimension: 'first', ordinal: 0, status: 'available', eligible: true, native_quality: 'available', audit_eligible: true, stale: true, conflict: false, snapshot_id: 's1', evidence_grade: 'A' },
        { dimension: 'later', ordinal: 1, status: 'available', eligible: true, native_quality: 'partial', audit_eligible: false, audit_reason: 'extended shape warning', stale: false, conflict: true, snapshot_id: 's2', evidence_grade: 'B' },
      ],
    })
    expect(coverage.counts).toEqual({ available: 1, verified_empty: 0, data_gap: 0, conflict: 1, stale: 1, degraded: 0 })
    expect(coverage.dimensions[0]).toMatchObject({ dimension_id: 'first', freshness_status: 'stale' })
    expect(coverage.dimensions[1]).toMatchObject({ dimension_id: 'later', status: 'conflict', eligible: true, native_quality: 'partial', audit_eligible: false })
    expect(coverage.report_generatable).toBe(true)
    expect(coverage.uzi_integrity).toMatchObject({ profile: 'medium', coverage_pct: 50, gate_passed: true })
    expect(coverage.uzi_integrity_by_profile?.lite).toMatchObject({ profile: 'lite', coverage_pct: 100 })
    expect(coverage.audit_ready_dimension_count).toBe(1)
  })

  it('maps UI cutoff semantics to the current read-only v2 query without legacy refresh collection', async () => {
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      requests.push(url)
      if (url.includes('/coverage')) return json({
        symbol: '600519.SH', market: 'cn_equity', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
        expected_dimension_count: 0, available_count: 0, verified_empty_count: 0, gap_count: 0, conflict_count: 0, stale_count: 0,
        structurally_complete: false, report_ready: false, dimensions: [],
      })
      return json({ items: [], total_count: 0, limit: 50, offset: 0 })
    }))
    const api = new MarketDataCenterApi({ baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' })
    await api.getCoverage({ symbol: '600519.SH', as_of_date: '2026-08-20', cutoff: 'eod' })
    await api.listSnapshots({ symbol: '600519.SH', as_of_date: '2026-08-20', cutoff: 'eod', dimension_id: '0_basic' })

    expect(requests[0]).toContain('/api/v2/finance/market-data/coverage?')
    expect(requests[0]).toContain('as_of_date=2026-08-20')
    expect(requests[0]).not.toContain('cutoff=eod')
    expect(requests[0]).not.toContain('cutoff_at=')
    expect(requests[1]).not.toContain('cutoff')
    expect(requests.some((url) => url.includes('refresh='))).toBe(false)
  })

  it('preserves fund child progress and task retryability from collection jobs', async () => {
    let requestedUrl = ''
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      requestedUrl = String(input)
      return json({
      items: [{
        id: 'fund-job-1', job_type: 'fund_holding_expansion', asset_family: 'fund', status: 'partial',
        symbols: ['020464'], dimensions: ['fund_top_holdings'], start_date: '2026-08-20', end_date: '2026-08-20',
        max_concurrency: 5, publish_when_complete: true, created_at: '2026-08-20T08:00:00Z',
        freeze_status: 'awaiting_recovery', can_confirm_freeze: false,
        publication_candidate: { can_publish: false, quality_gate: 'blocked', missing_dimensions: ['12_capital_flow'], scopes: [{ symbol: '600001.SH', can_publish: false }] },
        agent_recovery: { status: 'failed', attempted: ['12_capital_flow'], succeeded: [], failed: ['12_capital_flow'] },
        progress: { completed: 1, total: 1, child_complete: 3, child_total: 10, by_status: { partial: 1 },
          native_postfetch: { status: 'partial', attempted: 2, succeeded: 1, failed: 1, persisted_snapshot_count: 1 } },
        task_summary: { total: 1, by_status: { partial: 1 }, active: 0, succeeded: 0, skipped: 0, retryable_failed: 1, blocked_failed: 0, by_error_code: [], by_dimension: [] },
        portfolio_summary: { total_holdings: 10, ready_holdings: 3, dimensions_per_holding: 22, total_slots: 220, ready_slots: 91, missing_slots: 129, retryable_slots: 80, blocked_slots: 49, active_slots: 0, unassessed_slots: 0, degraded_slots: 49, by_holding: [{ symbol: '600001.SH', total_slots: 22, ready_slots: 0, missing_slots: 22, retryable_slots: 0, blocked_slots: 22, active_slots: 0, unassessed_slots: 0, degraded_slots: 22 }], attempt: 2, current_child_job_ids: ['child-2'], by_error_code: [{ key: 'strict_contract_gap', count: 49, retryable_count: 0, blocked_count: 49, active_count: 0 }], by_dimension: [] },
        tasks: [{
          id: 'task-1', symbol: '020464', dimension_id: 'fund_top_holdings', status: 'partial',
          attempt_count: 1, max_attempts: 3, retryable: true,
          collection_outcome: 'retryable_failure', reason_code: 'historical_provider_failure',
          result_message: 'fund archive: timeout', provider_status: 'timeout',
          field_coverage: { recovered_fields: [] },
          source_trace: [{ source_id: 'fund archive', status: 'timeout' }],
        }],
      }],
      total_count: 1,
      limit: 50,
      offset: 0,
    }) }))
    const api = new MarketDataCenterApi({ baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' })
    const page = await api.listJobs({ symbol: '020464', asset_family: 'fund', as_of_date: '2026-08-20', status: 'partial' })

    expect(page.items[0].progress).toMatchObject({ child_complete: 3, child_total: 10, completed: 0, partial: 1, running: 0, queued: 0 })
    expect(page.items[0].progress.native_postfetch).toMatchObject({ status: 'partial', attempted: 2, succeeded: 1, failed: 1, persisted_snapshot_count: 1 })
    expect(page.items[0].tasks?.[0]).toMatchObject({
      task_id: 'task-1', attempt: 1, max_attempts: 3, retryable: true,
      collection_outcome: 'retryable_failure', reason_code: 'historical_provider_failure',
      result_message: 'fund archive: timeout', provider_status: 'timeout',
      field_coverage: { recovered_fields: [] },
      source_trace: [{ source_id: 'fund archive', status: 'timeout' }],
    })
    expect(page.items[0].task_summary).toMatchObject({ retryable_failed: 1, blocked_failed: 0 })
    expect(page.items[0].portfolio_summary).toMatchObject({ ready_slots: 91, retryable_slots: 80, blocked_slots: 49 })
    expect(page.items[0].portfolio_summary).toMatchObject({ unassessed_slots: 0, degraded_slots: 49, by_holding: [{ symbol: '600001.SH', ready_slots: 0, degraded_slots: 22 }] })
    expect(page.items[0]).toMatchObject({ freeze_status: 'awaiting_recovery', can_confirm_freeze: false })
    expect(page.items[0].publication_candidate).toMatchObject({ can_publish: false, quality_gate: 'blocked', missing_dimensions: ['12_capital_flow'] })
    expect(page.items[0].agent_recovery).toMatchObject({ status: 'failed', attempted: 1, succeeded: 0, failed: 1 })
    expect(requestedUrl).toContain('symbol=020464')
    expect(requestedUrl).toContain('asset_family=fund')
    expect(requestedUrl).toContain('as_of_date=2026-08-20')
    expect(requestedUrl).toContain('status=partial')
  })

  it('sends a scoped retry body for one fund holding', async () => {
    let requestBody = ''
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestBody = String(init?.body || '')
      return json({
        id: 'fund-job-1', asset_family: 'fund', status: 'queued', symbols: ['020464'],
        dimensions: ['fund_top_holdings'], start_date: '2026-08-20', end_date: '2026-08-20',
        max_concurrency: 5, publish_when_complete: true, created_at: '2026-08-20T08:00:00Z',
        progress: { total: 1, by_status: { queued: 1 } }, expanded_holdings: [], child_collection_job_ids: [],
      })
    }))
    const api = new MarketDataCenterApi({ baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' })

    await api.retryFailed('fund-job-1', { symbols: ['600001.SH'], dimensions: ['0_basic'] })

    expect(JSON.parse(requestBody)).toEqual({ symbols: ['600001.SH'], dimensions: ['0_basic'] })
  })

  it('uses separate Agent recovery and user-confirmed Bundle freeze commands', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      return json({ job: {
        id: 'job-1', asset_family: 'equity', status: 'succeeded', symbols: ['600519.SH'], dimensions: ['0_basic'],
        start_date: '2026-08-20', end_date: '2026-08-20', max_concurrency: 5, publish_when_complete: false,
        freeze_status: url.endsWith('/confirm-freeze') ? 'frozen' : 'ready', can_confirm_freeze: !url.endsWith('/confirm-freeze'),
        progress: { total: 1, by_status: { succeeded: 1 } }, created_at: '2026-08-20T08:00:00Z',
      } })
    }))
    const api = new MarketDataCenterApi({ baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' })

    const recovered = await api.runAgentRecovery('job-1')
    const frozen = await api.confirmFreeze('job-1', 'a'.repeat(64), true)

    expect(calls[0].url).toContain('/collection-jobs/job-1/agent-recovery')
    expect(calls[0].init?.method).toBe('POST')
    expect(calls[1].url).toContain('/collection-jobs/job-1/confirm-freeze')
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ proposal_hash: 'a'.repeat(64), acknowledge_degraded: true })
    expect(recovered.freeze_status).toBe('awaiting_confirmation')
    expect(frozen.freeze_status).toBe('frozen')
  })

  it('sends strict manual evidence and strips subscription response-only fields', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url.endsWith('/manual-snapshots')) return json({ id: 'snapshot-1', symbol: '600519.SH', dimension: '0_basic', status: 'available', evidence_grade: 'B' })
      return json({ id: 'subscription-1', symbol: '600519.SH', market: 'cn_equity', enabled: true, scheduler_enabled: false, exchange_timezone: 'Asia/Shanghai', schedule: { local_time: '20:30' }, dimensions: [], max_concurrency: 5 })
    }))
    const api = new MarketDataCenterApi({ baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' })
    const manual: ManualSnapshotInput = {
      symbol: '600519.SH', market: 'cn_equity', dimension_id: '0_basic', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
      status: 'available', evidence_grade: 'B', effective_at: '2026-08-20T00:00:00Z', published_at: '2026-08-20T08:00:00Z', reason: '历史证据补录', payload: {},
      field_sources: { $: 'manual.evidence' }, query_manifest: { query_window: { start: '2026-08-20', end: '2026-08-20' }, required_sources: ['manual.evidence'], sources: [{ source: 'manual.evidence', status: 'success', result_count: 0 }], result_count: 0 }, evidence: [{ provider: 'manual.evidence', source_url: 'https://example.com/e', source_kind: 'manual_document', published_at: '2026-08-20T08:00:00Z', observed_at: '2026-08-20T09:00:00Z', grade: 'B', immutable_proof: true, is_manual: true, metadata: {} }],
    }
    await api.createManualSnapshot(manual)
    const subscription: MarketDataSubscription = {
      subscription_id: 'response-only', symbol: '600519.SH', market: 'cn_equity', enabled: true, timezone: 'Asia/Shanghai', schedule_time: '20:30', dimensions: [], max_concurrency: 5, last_error: 'response-only',
    }
    const updated = await api.updateSubscription('600519.SH', subscription)

    expect(JSON.parse(String(calls[0].init?.body))).toEqual(manual)
    expect(new Headers(calls[0].init?.headers).get('Idempotency-Key')).toMatch(/^market-data-/)
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      market: 'cn_equity', enabled: true, timezone: 'Asia/Shanghai', local_time: '20:30', dimensions: [], max_concurrency: 5,
    })
    expect(updated.scheduler_enabled).toBe(false)
  })

  it('creates a current report manifest with backend-owned bundle resolution', async () => {
    let body: Record<string, unknown> = {}
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>
      return json({ id: 'manifest-1', status: 'frozen', manifest_hash: 'hash-1' })
    }))
    const api = new MarketDataCenterApi({ baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' })
    const result = await api.createReportManifest({ symbol: '600519.SH', market: 'CN_EQUITY', as_of_date: '2026-08-20', evaluation_at: '2026-08-20T15:59:59Z', profile: 'deep', asset_family: 'equity' })

    expect(body).toEqual({
      symbols: ['600519.SH'], market_by_symbol: { '600519.SH': 'CN_EQUITY' }, as_of_date: '2026-08-20',
      evaluation_at: '2026-08-20T15:59:59Z',
      report_kind: 'single_stock', asset_family: 'equity', workflow_pins: { profile: 'deep' }, weights: {},
    })
    expect(result).toMatchObject({ manifest_id: 'manifest-1', status: 'frozen', manifest_hash: 'hash-1' })
  })

  it('uses the fund parent bundle and lets the backend derive the frozen ten-stock portfolio', async () => {
    let body: Record<string, unknown> = {}
    vi.stubGlobal('fetch', vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>
      return json({ manifest_id: 'fund-manifest-1', status: 'frozen' })
    }))
    const api = new MarketDataCenterApi({ baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' })
    await api.createReportManifest({
      symbol: '020464', market: 'CN_FUND', as_of_date: '2026-08-20', profile: 'deep',
      asset_family: 'fund', fund_bundle_id: 'fund-bundle-1',
    })

    expect(body).toMatchObject({
      symbols: ['020464'], market_by_symbol: { '020464': 'CN_FUND' }, report_kind: 'fund_top_holdings',
      asset_family: 'fund', fund_bundle_id: 'fund-bundle-1', workflow_pins: { profile: 'deep' },
    })
  })
})
