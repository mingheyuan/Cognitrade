import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MarketData, type MarketDataViewState } from './MarketData'

vi.mock('../CognitradeContext', () => ({
  useCognitrade: () => ({ settings: { baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' } }),
}))

const contract = {
  contract_version: 'uzi-test-v1',
  expected_dimension_count: 3,
  asset_family: 'equity',
  collector_count: 3,
  dimensions: [
    { dimension: 'd0', label: '基础行情', ordinal: 0, freshness_seconds: 3600, verified_empty_policy: 'never' },
    { dimension: 'd1', label: '财务数据', ordinal: 1, freshness_seconds: 86400, verified_empty_policy: 'never' },
    { dimension: 'd2', label: '龙虎榜', ordinal: 2, freshness_seconds: 3600, verified_empty_policy: 'bounded_zero' },
  ],
}

const stockIdentity = {
  symbol: '600519.SH', code: '600519', asset_type: 'stock', market: 'CN_EQUITY', exchange: 'SH',
  exchange_timezone: 'Asia/Shanghai', security_type: 'stock', name: '贵州茅台', currency: 'CNY',
  resolution_source: 'stock_symbol', instrument_id: 'stock:SH:600519',
}

const fundIdentity = {
  symbol: '020464', code: '020464', asset_type: 'otc_fund', market: 'CN_FUND', exchange: 'OTC',
  exchange_timezone: 'Asia/Shanghai', security_type: 'mutual_fund', name: '测试权益基金', currency: 'CNY',
  resolution_source: 'fund_security_master', instrument_id: 'otc_fund:OTC:020464',
}

function identityResolution(query: string) {
  const identity = query.includes('020464') ? fundIdentity : stockIdentity
  return { query, candidates: [identity], resolved: identity, ambiguous: false }
}

function coverage(ready = false) {
  return {
    symbol: '600519.SH', market: 'CN', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
    expected_dimension_count: 3,
    available_count: ready ? 2 : 1,
    verified_empty_count: 1,
    gap_count: ready ? 0 : 1,
    conflict_count: 0,
    stale_count: 0,
    ready_dimension_count: ready ? 3 : 2,
    structurally_complete: ready,
    report_ready: ready,
    contract_version: 'uzi-test-v1',
    dimensions: [
      { dimension: 'd0', ordinal: 0, status: 'available', stale: false, conflict: false, eligible: true, reason: null, snapshot_id: 'snap-0', evidence_grade: 'A' },
      { dimension: 'd1', ordinal: 1, status: ready ? 'available' : null, stale: false, conflict: false, eligible: ready, reason: ready ? null : '财报发布日期证据缺失', snapshot_id: ready ? 'snap-1' : null, evidence_grade: ready ? 'B' : null },
      { dimension: 'd2', ordinal: 2, status: 'verified_empty', stale: false, conflict: false, eligible: true, reason: '权威查询成功且 0 条', snapshot_id: 'snap-2', evidence_grade: 'A' },
    ],
  }
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

const pollingJob = {
  id: 'job-poll-1', status: 'running', mode: 'missing_or_stale', asset_family: 'equity', top_n: 10,
  symbols: ['600519.SH'], dimensions: ['d0', 'd1', 'd2'], start_date: '2026-08-20', end_date: '2026-08-20',
  max_concurrency: 5, publish_when_complete: true, progress: { total: 3, completed: 0, by_status: { running: 3 } },
  created_at: '2026-08-20T08:00:00Z',
}

function installApi(options: { ready?: boolean; jobResponses?: unknown[][] } = {}) {
  const requests: Array<{ url: string; init?: RequestInit }> = []
  let jobRequestIndex = 0
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    requests.push({ url, init })
    if (url.endsWith('/market-data/instruments/resolve')) {
      const body = JSON.parse(String(init?.body)) as { query: string }
      return json(identityResolution(body.query))
    }
    if (url.includes('/market-data/contract')) return json(contract)
    if (url.includes('/market-data/coverage')) return json(coverage(Boolean(options.ready)))
    if (url.includes('/market-data/snapshots/snap-2')) return json({
      id: 'snap-2', symbol: '600519.SH', dimension: 'd2', status: 'verified_empty', evidence_grade: 'A',
      content_hash: 'snapshot-hash', effective_at: '2026-08-20T00:00:00Z', published_at: '2026-08-20T08:00:00Z',
      observed_at: '2026-08-20T08:01:00Z', ingested_at: '2026-08-20T08:02:00Z', payload: {},
      query_manifest: { sources: ['official'], returned_count: 0 },
      evidence: [{ evidence_id: 'e-1', provider: 'official', grade: 'A', source_url: 'https://example.com/evidence' }],
    })
    if (url.includes('/market-data/snapshots')) return json({ items: [{
      id: 'snap-2', symbol: '600519.SH', dimension: 'd2', status: 'verified_empty', evidence_grade: 'A',
      content_hash: 'snapshot-hash', published_at: '2026-08-20T08:00:00Z',
    }], total_count: 1, limit: 50, offset: 0 })
    if (url.endsWith('/market-data/collection-jobs') && init?.method === 'POST') return json({
      id: 'job-1', status: 'queued', mode: 'missing_or_stale', asset_family: 'equity', top_n: 10, symbols: ['600519.SH'], dimensions: ['d0', 'd1', 'd2'],
      start_date: '2026-08-20', end_date: '2026-08-20', max_concurrency: 5, publish_when_complete: false,
      progress: { total: 3, completed: 0, by_status: { queued: 3 } }, created_at: '2026-08-20T08:00:00Z',
    })
    if (url.endsWith('/agent-recovery') && init?.method === 'POST') {
      const current = options.jobResponses?.[0]?.[0] as Record<string, unknown> | undefined
      const recovered = current ? {
        ...current,
        status: 'succeeded',
        freeze_status: 'awaiting_confirmation',
        can_confirm_freeze: true,
        agent_recovery: { status: 'completed', attempted: 1, succeeded: 1, failed: 0 },
        publication_candidate: { can_publish: true, quality_gate: 'strict', proposal_hash: 'c'.repeat(64), missing_dimensions: [], scopes: [{ symbol: '600519.SH', as_of_date: '2026-08-20', can_publish: true }] },
      } : {}
      if (options.jobResponses) options.jobResponses[0] = [recovered]
      return json(recovered)
    }
    if (url.endsWith('/confirm-freeze') && init?.method === 'POST') {
      const current = options.jobResponses?.[0]?.[0] as Record<string, unknown> | undefined
      const frozen = current ? { ...current, freeze_status: 'frozen', can_confirm_freeze: false } : {}
      if (options.jobResponses) options.jobResponses[0] = [frozen]
      return json(frozen)
    }
    if (/\/market-data\/collection-jobs\/[^/]+$/.test(url) && init?.method !== 'POST') {
      return json(options.jobResponses?.[0]?.[0] || {})
    }
    if (url.includes('/market-data/collection-jobs')) {
      const responses = options.jobResponses || []
      const items = responses[Math.min(jobRequestIndex, Math.max(0, responses.length - 1))] || []
      jobRequestIndex += 1
      return json({ items, total_count: items.length, limit: 50, offset: 0 })
    }
    if (url.endsWith('/market-data/manual-snapshots') && init?.method === 'POST') return json({
      id: 'manual-snapshot-1', symbol: '600519.SH', dimension: 'd0', status: 'available', evidence_grade: 'B',
    })
    if (url.includes('/market-data/bundles')) return json({
      items: options.ready ? [{
        id: 'bundle-1', symbol: '600519.SH', market: 'cn_equity', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
        status: 'published', contract_version: 'uzi-test-v1', expected_dimension_count: 3, available_count: 2,
        verified_empty_count: 1, manifest_hash: '0123456789abcdef', revision: 1, items: [],
      }] : [], total_count: options.ready ? 1 : 0, limit: 1, offset: 0,
    })
    if (url.endsWith('/market-data/report-manifests') && init?.method === 'POST') return json({ manifest_id: 'manifest-1', manifest_hash: 'a'.repeat(64), status: 'frozen' })
    return json({ items: [], total_count: 0, limit: 50, offset: 0 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, requests }
}

const fundContract = {
  contract_version: 'cognitrade-fund-top-holdings-pit-v1',
  expected_dimension_count: 1,
  collector_count: 1,
  asset_family: 'fund',
  dimensions: [{
    dimension: 'fund_top_holdings', label: '基金前十大持仓披露', ordinal: 0, freshness_seconds: 43200,
    verified_empty_policy: 'bounded_zero', description: '基金只作为十只 A 股子标的入口。',
  }],
}

const expandedHoldings = Array.from({ length: 10 }, (_, index) => ({
  rank: index + 1,
  source_rank: index + 1,
  symbol: `${600000 + index}.SH`,
  code: String(600000 + index),
  name: `持仓股票 ${index + 1}`,
  asset_type: 'stock',
  market: 'CN_EQUITY',
  exchange: 'SH',
  status: 'available',
  fund_weight_pct: 10 - index * 0.5,
  disclosed_weight_pct: 10 - index * 0.5,
  holding_source: 'direct_equity_disclosure',
  disclosure_date: '2026-06-30',
  missing_dimensions: [],
}))

const completedFundJob = {
  id: 'fund-job-1', job_id: 'fund-job-1', status: 'succeeded', mode: 'missing_or_stale', asset_family: 'fund', top_n: 10,
  symbols: ['020464'], dimensions: ['fund_top_holdings'], start_date: '2026-08-20', end_date: '2026-08-20',
  max_concurrency: 5, publish_when_complete: true, progress: { total: 1, completed: 1, by_status: { succeeded: 1 } },
  checkpoint: { parent_bundle_id: 'fund-bundle-1', parent_bundle_hash: 'fund-bundle-hash', holdings: expandedHoldings, child_job_ids: ['child-job-a', 'child-job-b'] },
  expanded_holdings: expandedHoldings, child_collection_job_ids: ['child-job-a', 'child-job-b'], created_at: '2026-08-20T08:00:00Z',
  task_summary: { total: 1, by_status: { succeeded: 1 }, active: 0, succeeded: 1, skipped: 0, retryable_failed: 0, blocked_failed: 0, by_error_code: [], by_dimension: [] },
  portfolio_summary: { total_holdings: 10, ready_holdings: 10, dimensions_per_holding: 22, total_slots: 220, ready_slots: 220, missing_slots: 0, retryable_slots: 0, blocked_slots: 0, active_slots: 0, attempt: 1, current_child_job_ids: ['child-job-a', 'child-job-b'], by_error_code: [], by_dimension: [] },
}

const equityDimensions = Array.from({ length: 22 }, (_, index) => `${index}_dimension`)
const partialHoldings = expandedHoldings.map((holding, index) => ({
  ...holding,
  missing_dimensions: index < 8 ? equityDimensions : index === 8 ? equityDimensions.slice(0, 20) : equityDimensions.slice(0, 1),
}))

const partialFundJob = {
  ...completedFundJob,
  status: 'partial',
  progress: { total: 1, completed: 1, child_complete: 0, child_total: 10, by_status: { partial: 1 } },
  checkpoint: { ...completedFundJob.checkpoint, holdings: partialHoldings },
  expanded_holdings: partialHoldings,
  task_summary: { total: 1, by_status: { partial: 1 }, active: 0, succeeded: 0, skipped: 0, retryable_failed: 1, blocked_failed: 0, by_error_code: [], by_dimension: [] },
  portfolio_summary: {
    total_holdings: 10, ready_holdings: 0, dimensions_per_holding: 22, total_slots: 220, ready_slots: 23, missing_slots: 197,
    retryable_slots: 134, blocked_slots: 63, active_slots: 0, attempt: 2, current_child_job_ids: ['child-job-b'],
    by_error_code: [
      { key: 'provider_degraded', count: 134, retryable_count: 134, blocked_count: 0, active_count: 0, sample_message: '上游暂时降级' },
      { key: 'strict_contract_gap', count: 63, retryable_count: 0, blocked_count: 63, active_count: 0, sample_message: '证据等级或字段合同不通过' },
    ],
    by_dimension: [{ key: '12_capital_flow', count: 10, retryable_count: 7, blocked_count: 3, active_count: 0, sample_message: '资金流证据缺失' }],
  },
  tasks: [{
    id: 'fund-task-1', symbol: '020464', dimension_id: 'fund_top_holdings', status: 'partial',
    attempt_count: 1, max_attempts: 3, retryable: true, error_code: 'fund_child_collection_incomplete',
    error_message: '一个或多个持仓股票仍缺少报告所需行情维度或已发布 Bundle',
  }],
}

function installFundApi(options: { job?: typeof completedFundJob | typeof partialFundJob } = {}) {
  const requests: Array<{ url: string; init?: RequestInit }> = []
  const responseJob = options.job || completedFundJob
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    requests.push({ url, init })
    if (url.endsWith('/market-data/instruments/resolve')) {
      const body = JSON.parse(String(init?.body)) as { query: string }
      return json(identityResolution(body.query))
    }
    if (url.includes('/market-data/contract')) return json(fundContract)
    if (url.includes('/market-data/coverage')) return json({
      symbol: '020464', market: 'CN_FUND', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
      expected_dimension_count: 1, available_count: 1, verified_empty_count: 0, gap_count: 0, conflict_count: 0, stale_count: 0,
      structurally_complete: true, report_ready: true, contract_version: fundContract.contract_version,
      latest_bundle: { bundle_id: 'fund-bundle-1', status: 'published', manifest_hash: 'fund-bundle-hash' },
      dimensions: [{ dimension: 'fund_top_holdings', ordinal: 0, status: 'available', stale: false, conflict: false, eligible: true, snapshot_id: 'fund-snapshot-1', evidence_grade: 'A' }],
    })
    if (url.endsWith('/retry-failed') && init?.method === 'POST') return json({ ...responseJob, status: 'queued' })
    if (url.endsWith('/market-data/collection-jobs') && init?.method === 'POST') return json({ ...completedFundJob, status: 'queued', progress: { total: 1, completed: 0, by_status: { queued: 1 } } })
    if (url.includes('/market-data/collection-jobs')) return json({ items: [responseJob], total_count: 1, limit: 50, offset: 0 })
    if (url.endsWith('/market-data/report-manifests') && init?.method === 'POST') return json({ manifest_id: 'fund-manifest-1', manifest_hash: 'b'.repeat(64), status: 'frozen' })
    if (url.includes('/market-data/bundles/fund-bundle-1')) return json({
      id: 'fund-bundle-1', symbol: '020464', market: 'CN_FUND', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
      status: 'published', contract_version: fundContract.contract_version, expected_dimension_count: 1, available_count: 1,
      verified_empty_count: 0, manifest_hash: 'fund-bundle-hash', revision: 1, items: [],
    })
    if (url.includes('/market-data/bundles')) return json({ items: [], total_count: 0, limit: 50, offset: 0 })
    return json({ items: [], total_count: 0, limit: 50, offset: 0 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, requests }
}

function researchHandler() {
  return vi.fn<(query: string, intent?: string, constraints?: Record<string, unknown>) => Promise<void>>(
    () => Promise.resolve(),
  )
}

function renderPage(onResearch = researchHandler()) {
  const state: MarketDataViewState = { query: '600519.SH', data: null, loading: false, error: '' }
  return render(<MarketData state={state} setState={vi.fn()} onResearch={onResearch} />)
}

describe('MarketDataCenter', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/market-data?symbol=600519.SH&as_of_date=2026-08-20&cutoff=eod&tab=coverage')
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('renders the backend-provided dimension contract and never collects during GET reads', async () => {
    const { requests } = installApi()
    renderPage()

    expect(await screen.findByRole('heading', { name: '采集槽完整度' })).toBeInTheDocument()
    expect(screen.getByText('基础行情')).toBeInTheDocument()
    expect(screen.getByText('财务数据')).toBeInTheDocument()
    expect(screen.getByText('龙虎榜')).toBeInTheDocument()
    expect(screen.getByText('/ 3')).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(5)
    expect(requests.some((request) => request.url.endsWith('/collection-jobs') && request.init?.method === 'POST')).toBe(false)
    expect(requests.some((request) => request.url.includes('refresh='))).toBe(false)
    expect(requests.some((request) => request.url.includes('cutoff=eod'))).toBe(false)
    expect(requests.some((request) => request.url.includes('contract?market=CN_FUND'))).toBe(false)
    expect(requests.some((request) => request.url.includes('contract?market=CN_EQUITY'))).toBe(true)
    expect(requests.some((request) => request.url.includes('coverage?') && request.url.includes('market=CN_EQUITY'))).toBe(true)
    expect(requests.every((request) => request.url.includes('/api/v2/finance/market-data/'))).toBe(true)
  })

  it('keeps the toolbar submit label horizontal while preserving visible form labels', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=020464&asset_family=fund&as_of_date=2026-08-20&cutoff=eod&tab=coverage')
    installFundApi()
    renderPage()

    await screen.findByText('测试权益基金')
    expect(screen.getByLabelText('代码或基金查询')).toBeVisible()
    expect(screen.getByLabelText('资产类型')).toBeVisible()
    expect(screen.getByLabelText('可见日期')).toBeVisible()
    expect(screen.getByLabelText('截止时点')).toBeVisible()

    const submit = screen.getByRole('button', { name: '解析并读取' })
    const label = within(submit).getByText('解析并读取')
    expect(submit).toHaveClass('mdc-toolbar-submit')
    expect(label).toHaveClass('mdc-toolbar-submit-label')
  })

  it('restores URL state and exposes verified-empty evidence without treating it as a timeout', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=600519.SH&as_of_date=2026-08-20&cutoff=eod&tab=evidence&dimension=d2')
    installApi()
    renderPage()

    expect(await screen.findByRole('heading', { name: '龙虎榜' })).toBeInTheDocument()
    expect(screen.getByText('A级 · 截止前观测')).toBeInTheDocument()
    expect(screen.getAllByText('official').length).toBeGreaterThan(0)
    expect(screen.getByText('returned_count')).toBeInTheDocument()
    expect(screen.getAllByText('0').length).toBeGreaterThan(0)
    expect(screen.getByRole('tab', { name: /数据与证据/ })).toHaveAttribute('aria-selected', 'true')
  })

  it('starts external acquisition only after an explicit user action with default concurrency five', async () => {
    const { requests } = installApi()
    renderPage()
    await screen.findByRole('heading', { name: '采集槽完整度' })

    expect(requests.some((request) => request.url.endsWith('/collection-jobs') && request.init?.method === 'POST')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: /补缺 \/ 补旧/ }))

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/collection-jobs') && request.init?.method === 'POST')).toBe(true))
    const creation = requests.find((request) => request.url.endsWith('/collection-jobs') && request.init?.method === 'POST')
    expect(JSON.parse(String(creation?.init?.body))).toMatchObject({
      symbols: ['600519.SH'], asset_family: 'equity', top_n: 10, dimensions: 'all', mode: 'missing_or_stale', max_concurrency: 5, publish_when_complete: false,
    })
    expect(await screen.findByRole('heading', { name: '新建采集任务' })).toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: '采集并发' })).toHaveAttribute('max', '5')
    expect(window.location.search).toContain('tab=jobs')
  })

  it('silently polls while a collection job is active and stops after it becomes terminal', async () => {
    const intervals: Array<{ id: number; callback: () => void }> = []
    let nextIntervalId = 41
    vi.spyOn(window, 'setInterval').mockImplementation((handler: TimerHandler) => {
      const id = nextIntervalId += 1
      if (typeof handler === 'function') intervals.push({ id, callback: handler as () => void })
      return id
    })
    const clearInterval = vi.spyOn(window, 'clearInterval')
    window.history.replaceState({}, '', '/market-data?symbol=600519.SH&as_of_date=2026-08-20&cutoff=eod&tab=jobs')
    const completedJob = {
      ...pollingJob,
      status: 'succeeded',
      progress: { total: 3, completed: 3, by_status: { running: 0, succeeded: 3 } },
      completed_at: '2026-08-20T08:03:00Z',
    }
    const jobResponses: unknown[][] = [[pollingJob]]
    const { requests } = installApi({ jobResponses })
    renderPage()

    expect(await screen.findByText('job-poll-1')).toBeInTheDocument()
    expect(screen.getByText(/运行中任务每 3 秒静默刷新/)).toBeInTheDocument()
    const collectionReads = () => requests.filter((request) => request.url.includes('/market-data/collection-jobs') && request.init?.method !== 'POST').length
    expect(collectionReads()).toBeGreaterThanOrEqual(1)
    expect(intervals.length).toBeGreaterThanOrEqual(1)
    const activeInterval = intervals.at(-1) as { id: number; callback: () => void }

    jobResponses[0] = [completedJob]
    act(() => activeInterval.callback())
    await waitFor(() => expect(screen.getByText('3 / 3 已结算')).toBeInTheDocument())
    expect(screen.getByText(/没有运行中任务/)).toBeInTheDocument()
    const readsAfterCompletion = collectionReads()
    expect(clearInterval).toHaveBeenCalledWith(activeInterval.id)

    act(() => activeInterval.callback())
    expect(collectionReads()).toBe(readsAfterCompletion)
  })

  it('keeps collection as a draft, runs Agent recovery, and freezes only after confirmation', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=600519.SH&as_of_date=2026-08-20&cutoff=eod&tab=jobs')
    const recoverableJob = {
      ...pollingJob,
      id: 'job-draft-1',
      status: 'partial',
      publish_when_complete: false,
      freeze_status: 'awaiting_recovery',
      can_confirm_freeze: false,
      progress: { total: 3, completed: 2, failed: 1, by_status: { succeeded: 2, failed: 1 } },
      task_summary: { total: 3, by_status: { succeeded: 2, failed: 1 }, active: 0, succeeded: 2, skipped: 0, retryable_failed: 0, blocked_failed: 1, by_error_code: [], by_dimension: [] },
      publication_candidate: { can_publish: false, quality_gate: 'blocked', missing_dimensions: ['d1'], scopes: [] },
    }
    const jobResponses: unknown[][] = [[recoverableJob]]
    const { requests } = installApi({ jobResponses })
    renderPage()

    const jobCode = await screen.findByText('job-draft-1')
    fireEvent.click(jobCode.closest('summary') as HTMLElement)
    expect(screen.getByText('Bundle 发布：需用户确认')).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: '启动 Agent 缺口补采' }))

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/collection-jobs/job-draft-1/agent-recovery') && request.init?.method === 'POST')).toBe(true))
    const freeze = await screen.findByRole('button', { name: '确认冻结 Bundle' })
    fireEvent.click(freeze)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('采集流程的提交边界')
    fireEvent.click(within(dialog).getByRole('checkbox', { name: '我确认将当前采集草稿发布为不可变 Bundle' }))
    fireEvent.click(within(dialog).getByRole('button', { name: '确认冻结 Bundle' }))

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/collection-jobs/job-draft-1/confirm-freeze') && request.init?.method === 'POST')).toBe(true))
    const confirmation = requests.find((request) => request.url.endsWith('/collection-jobs/job-draft-1/confirm-freeze') && request.init?.method === 'POST')
    expect(JSON.parse(String(confirmation?.init?.body))).toEqual({ proposal_hash: 'c'.repeat(64), acknowledge_degraded: false })
  })

  it('freezes a ready bundle and launches the selected report profile', async () => {
    const { requests } = installApi({ ready: true })
    const onResearch = researchHandler()
    renderPage(onResearch)
    await screen.findByRole('heading', { name: '采集槽完整度' })

    fireEvent.click(screen.getByRole('button', { name: '创建报告 Manifest' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('报告阶段外部数据调用数必须为 0')
    fireEvent.click(screen.getByRole('radio', { name: /Deep/ }))
    fireEvent.click(screen.getByRole('button', { name: '创建 DEEP Manifest' }))

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/report-manifests') && request.init?.method === 'POST')).toBe(true))
    const creation = requests.find((request) => request.url.endsWith('/report-manifests') && request.init?.method === 'POST')
    expect(JSON.parse(String(creation?.init?.body))).toMatchObject({
      symbols: ['600519.SH'],
      market_by_symbol: { '600519.SH': 'CN_EQUITY' },
      as_of_date: '2026-08-20',
      evaluation_at: '2026-08-20T15:59:59Z',
      report_kind: 'single_stock',
      asset_family: 'equity',
      workflow_pins: { profile: 'deep' },
      weights: {},
    })
    const launch = await screen.findByRole('button', { name: '使用此数据生成 DEEP 报告' })
    fireEvent.click(launch)
    await waitFor(() => expect(onResearch).toHaveBeenCalledWith(
      '使用冻结数据分析 600519.SH',
      'stock_check',
      {
        research_profile: 'deep',
        analysis_as_of_date: '2026-08-20',
        report_manifest_id: 'manifest-1',
        report_manifest_hash: 'a'.repeat(64),
        symbol: '600519.SH',
      },
    ))
  })

  it('builds a backend-valid B-grade manual evidence request and never offers manual grade A', async () => {
    const { requests } = installApi()
    renderPage()
    await screen.findByRole('heading', { name: '采集槽完整度' })

    fireEvent.click(screen.getByRole('button', { name: '手工录入' }))
    expect(screen.getByDisplayValue('B · 人工数据最高等级')).toHaveAttribute('readonly')
    expect(screen.queryByRole('option', { name: /A ·/ })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('证据 URL'), { target: { value: 'https://example.com/archive' } })
    fireEvent.change(screen.getByLabelText('录入原因'), { target: { value: '补录不可变历史公告证据' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /我确认该证据不可变/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交严格校验' }))

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/manual-snapshots') && request.init?.method === 'POST')).toBe(true))
    const creation = requests.find((request) => request.url.endsWith('/manual-snapshots') && request.init?.method === 'POST')
    expect(JSON.parse(String(creation?.init?.body))).toMatchObject({
      symbol: '600519.SH', market: 'CN_EQUITY', dimension_id: 'd0', as_of_date: '2026-08-20', cutoff_at: '2026-08-20T15:59:59Z',
      evidence_grade: 'B', status: 'available', payload: {}, field_sources: { $: 'manual.evidence' },
      query_manifest: { query_window: { start: '2026-08-20', end: '2026-08-20' }, required_sources: ['manual.evidence'], sources: [{ source: 'manual.evidence', status: 'success', result_count: 0 }], result_count: 0 },
      evidence: [{ provider: 'manual.evidence', source_url: 'https://example.com/archive', grade: 'B', immutable_proof: true, is_manual: true }],
    })
  })

  it('uses the CN_FUND contract as a single dynamic holdings entry instead of the equity contract', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=020464&asset_family=fund&as_of_date=2026-08-20&cutoff=eod&tab=coverage')
    const { requests } = installFundApi()
    renderPage()

    expect(await screen.findByRole('heading', { name: '基金持仓入口完整度' })).toBeInTheDocument()
    expect(screen.getByText('基金前十大持仓披露')).toBeInTheDocument()
    expect(screen.getByText('fund_top_holdings')).toBeInTheDocument()
    expect(screen.getByText('/ 10')).toBeInTheDocument()
    expect(screen.getByText('220 / 220')).toBeInTheDocument()
    expect(screen.queryByText('基础行情')).not.toBeInTheDocument()
    expect(screen.getByText(/基金本身只占一个持仓披露槽/)).toBeInTheDocument()
    expect(requests.some((request) => request.url.includes('/contract?market=CN_FUND'))).toBe(true)
  })

  it('sends the fixed fund collection contract and renders expanded holdings with child job ids', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=020464&asset_family=fund&as_of_date=2026-08-20&cutoff=eod&tab=jobs')
    const { requests } = installFundApi()
    renderPage()

    expect(await screen.findByRole('heading', { name: '新建采集任务' })).toBeInTheDocument()
    const jobCode = await screen.findByText('fund-job-1')
    fireEvent.click(jobCode.closest('summary') as HTMLElement)
    expect(screen.getByRole('heading', { name: '前十大持仓采集链路' })).toBeInTheDocument()
    expect(screen.getByText('持仓股票 1')).toBeInTheDocument()
    expect(screen.getByText('持仓股票 10')).toBeInTheDocument()
    expect(screen.getByText('child-job-a')).toBeInTheDocument()
    expect(screen.getByText('child-job-b')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /补缺 \/ 补旧/ }))
    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/collection-jobs') && request.init?.method === 'POST')).toBe(true))
    const creation = requests.find((request) => request.url.endsWith('/collection-jobs') && request.init?.method === 'POST')
    expect(JSON.parse(String(creation?.init?.body))).toMatchObject({
      symbols: ['020464'], asset_family: 'fund', top_n: 10, dimensions: 'all', max_concurrency: 5,
    })
  })

  it('groups a partial fund workflow and exposes recovery for retryable holding gaps', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=020464&asset_family=fund&as_of_date=2026-08-20&cutoff=eod&tab=jobs')
    const { requests } = installFundApi({ job: partialFundJob })
    renderPage()

    expect(await screen.findByRole('heading', { name: '前十大持仓采集链路' })).toBeInTheDocument()
    expect(screen.getByText('0/10 只就绪')).toBeInTheDocument()
    expect(screen.getAllByText('缺 22 槽')).toHaveLength(8)
    expect(screen.getByText('134 个槽可恢复，63 个槽需修复')).toBeInTheDocument()
    expect(screen.getByText(/合格 23\/220 槽/)).toBeInTheDocument()
    expect(screen.getByText('strict_contract_gap')).toBeInTheDocument()
    const retry = screen.getByRole('button', { name: /重试 134 个可重试槽/ })
    fireEvent.click(retry)

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/collection-jobs/fund-job-1/retry-failed') && request.init?.method === 'POST')).toBe(true))
  })

  it('freezes a fund report as the backend-derived ten-stock portfolio', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=020464&asset_family=fund&as_of_date=2026-08-20&cutoff=eod&tab=coverage')
    const { requests } = installFundApi()
    const onResearch = researchHandler()
    renderPage(onResearch)

    const freeze = await screen.findByRole('button', { name: '创建十大持仓 Manifest' })
    await waitFor(() => expect(freeze).toBeEnabled())
    fireEvent.click(freeze)
    expect(screen.getByRole('dialog')).toHaveTextContent('基金代码不会被当成股票分析')
    expect(screen.getByRole('dialog')).toHaveTextContent('10 只股票')
    fireEvent.click(screen.getByRole('radio', { name: /Deep/ }))
    fireEvent.click(screen.getByRole('button', { name: '创建十大持仓 · DEEP Manifest' }))

    await waitFor(() => expect(requests.some((request) => request.url.endsWith('/report-manifests') && request.init?.method === 'POST')).toBe(true))
    const creation = requests.find((request) => request.url.endsWith('/report-manifests') && request.init?.method === 'POST')
    expect(JSON.parse(String(creation?.init?.body))).toMatchObject({
      symbols: ['020464'], market_by_symbol: { '020464': 'CN_FUND' }, asset_family: 'fund',
      fund_bundle_id: 'fund-bundle-1', report_kind: 'fund_top_holdings', workflow_pins: { profile: 'deep' },
    })
    fireEvent.click(await screen.findByRole('button', { name: '使用此数据生成 DEEP 报告' }))
    await waitFor(() => expect(onResearch).toHaveBeenCalled())
    const [, intent, constraints = {}] = onResearch.mock.calls[0]
    expect(intent).toBe('uzi_portfolio_batch')
    expect(constraints).toMatchObject({
      research_profile: 'deep',
      analysis_as_of_date: '2026-08-20',
      report_manifest_id: 'fund-manifest-1',
      report_manifest_hash: 'b'.repeat(64),
      portfolio_name: '测试权益基金 前十大持仓',
    })
    expect(constraints.portfolio_holdings as unknown[]).toHaveLength(10)
  })

  it('blocks a mixed equity and fund batch before collection job creation', async () => {
    window.history.replaceState({}, '', '/market-data?symbol=600519.SH&asset_family=auto&as_of_date=2026-08-20&cutoff=eod&tab=jobs')
    const { requests } = installApi()
    renderPage()

    const input = await screen.findByLabelText(/股票代码（逗号或空格分隔；不可混入基金）/)
    fireEvent.change(input, { target: { value: '600519.SH, 020464' } })
    fireEvent.click(screen.getByRole('button', { name: '创建显式采集任务' }))

    expect(await screen.findByText('同一采集任务不能混合股票与基金，请分别创建任务。')).toBeInTheDocument()
    expect(requests.some((request) => request.url.endsWith('/collection-jobs') && request.init?.method === 'POST')).toBe(false)
  })
})
