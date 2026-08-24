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
      structurally_complete: false, report_ready: false,
      dimensions: [
        { dimension: 'first', ordinal: 0, status: 'available', eligible: true, stale: true, conflict: false, snapshot_id: 's1', evidence_grade: 'A' },
        { dimension: 'later', ordinal: 1, status: 'available', eligible: false, stale: false, conflict: true, snapshot_id: 's2', evidence_grade: 'B' },
      ],
    })
    expect(coverage.counts).toEqual({ available: 1, verified_empty: 0, data_gap: 0, conflict: 1, stale: 1 })
    expect(coverage.dimensions[0]).toMatchObject({ dimension_id: 'first', freshness_status: 'stale' })
    expect(coverage.dimensions[1]).toMatchObject({ dimension_id: 'later', status: 'conflict' })
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
