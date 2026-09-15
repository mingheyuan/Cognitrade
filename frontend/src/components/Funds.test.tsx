import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Funds } from './Funds'

const mocks = vi.hoisted(() => ({
  createRun: vi.fn(async () => ({ run_id: 'run-new' })),
  selectRun: vi.fn(),
  reloadRun: vi.fn(async () => undefined),
  openRun: vi.fn(),
  openStrategy: vi.fn(),
  selectedRunDetailId: 'run-fund',
}))

vi.mock('../CognitradeContext', () => ({
  useCognitrade: () => ({
    createRun: mocks.createRun,
    settings: { baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' },
    runs: [{ run_id: 'run-fund', intent: 'fund_research', status: 'completed' }],
    selectedRunId: 'run-fund',
    selectRun: mocks.selectRun,
    reloadRun: mocks.reloadRun,
    selectedRunLoading: false,
    submitting: false,
    selectedRun: {
      runId: mocks.selectedRunDetailId,
      replay: {
        artifacts: [
          {
            artifact_type: 'fund_snapshot',
            content: {
              analysis_mode: 'comparison',
              quality_status: 'available',
              fund_category: '混合型',
              candidate_pool_size: 1,
              data_gap: [],
              provider_errors: [],
              funds: [{
                fund_code: '110011',
                fund_name: '易方达优质精选混合',
                accumulated_nav: 5.23,
                nav_history: [{ date: '2025-01-01', nav: 1 }, { date: '2026-01-01', nav: 1.2 }],
                fund_overview: { fund_company: '易方达基金', custodian: '中国银行' },
                data_gap: [],
              }],
            },
          },
          {
            artifact_type: 'fund_analysis',
            content: {
              analysis_mode: 'comparison',
              quality_status: 'available',
              fund_category: '混合型',
              data_gap: [],
              recommendations: [{ fund_code: '110011' }],
              candidates: [
                {
                  fund_code: '110011',
                  fund_name: '易方达优质精选混合',
                  fund_type: 'ETF联接',
                  rank: 1,
                  score: 82.5,
                  score_coverage: 0.85,
                  selection_eligible: true,
                  returns_pct: { year_1: 16.2 },
                  risk: { max_drawdown_pct: -9.4, annualized_volatility_pct: 12.1 },
                  top10_concentration_pct: 17.7,
                  benchmark_definition: '沪深300指数收益率×80%+中债指数收益率×20%',
                  benchmark_performance: [{
                    period: '近1年', benchmark_return_pct: 8, excess_return_pct: 8.2,
                  }],
                  manager_tenures: [{
                    manager: '张经理', start_date: '2024-01-01', current: true, tenure_return_pct: 20,
                  }],
                  scale_change: {
                    latest_net_assets_100m_cny: 12,
                    latest_total_shares_100m: 10,
                    total_share_change_pct: -9.09,
                    latest_net_flow_100m_shares: -1,
                  },
                  holdings_period: '2026年2季度',
                  top_holdings: [{ code: '600519', name: '贵州茅台', weight_pct: 9.5 }],
                  asset_allocation_period: '2026-06-30',
                  asset_allocation: [
                    { asset_type: 'stock', label: '股票', weight_pct: 70 },
                    { asset_type: 'bond', label: '债券', weight_pct: 10 },
                  ],
                  bond_holdings: [{ code: '019785', name: '25国债13', weight_pct: 1.65 }],
                  target_fund_period: '2026-06-30',
                  target_fund_source_ref: 'https://pdf.dfcfw.com/example.pdf',
                  target_fund_holdings: [{ code: '561980', name: '招商中证半导体产业 ETF', weight_pct: 92.22, market_value_10k: 272647.2 }],
                  lookthrough_period: '2026年第二季度股票投资明细',
                  lookthrough_holdings: [{ code: '688012', name: '中微公司', weight_pct: 15.02, effective_weight_pct: 13.851444, market_value_10k: 112420.46 }],
                  holdings_coverage: {
                    asset_allocation: 'available', direct_equity: 'available', bond: 'available',
                    target_fund: 'available', lookthrough: 'available', is_fund_of_funds: true,
                  },
                  data_gap: [],
                },
              ],
            },
          },
        ],
      },
    },
  }),
}))

describe('Funds', () => {
  afterEach(cleanup)

  beforeEach(() => {
    mocks.createRun.mockClear()
    mocks.selectRun.mockClear()
    mocks.reloadRun.mockClear()
    mocks.openRun.mockClear()
    mocks.openStrategy.mockClear()
    mocks.selectedRunDetailId = 'run-fund'
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/holding-reports') && init?.method === 'POST') {
        return new Response(JSON.stringify({
          batch_id: 'batch-1', source_run_id: 'run-fund', source_artifact_id: 'artifact-fund',
          fund_code: '110011', fund_name: '易方达优质精选混合', fund_type: 'ETF联接',
          profile: 'lite', status: 'running', concurrency: 5,
          holding_source: 'target_etf_lookthrough', is_fund_of_funds: true,
          disclosure_date: null, disclosure_age_days: null, stale_level: 'unknown', cancel_requested: false,
          holdings: [], counts: { total: 0, supported: 0, queued: 0, running: 0, deliverable: 0, failed: 0, cancelled: 0, unsupported: 0 },
          data_gap: ['holding_disclosure_date_missing'], created_at: '2026-08-06T00:00:00Z',
          updated_at: '2026-08-06T00:00:00Z', completed_at: null,
        }), { status: 202, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
  })

  it('renders audited fund evidence instead of raw JSON', () => {
    render(<Funds onOpenRun={mocks.openRun} />)

    expect(screen.getByText('基金研究与选择')).toBeInTheDocument()
    expect(screen.getAllByText('易方达优质精选混合').length).toBeGreaterThan(0)
    expect(screen.getByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getAllByText('82.5').length).toBeGreaterThan(0)
    expect(screen.getByText(/沪深300指数收益率/)).toBeInTheDocument()
    expect(screen.getByText(/张经理/)).toBeInTheDocument()
    expect(screen.getByText('最新总份额')).toBeInTheDocument()
    expect(screen.getByText('10 亿份')).toBeInTheDocument()
    expect(screen.getByText('披露持仓结构')).toBeInTheDocument()
    expect(screen.getByText('直接股票持仓')).toBeInTheDocument()
    expect(screen.getByText('债券持仓')).toBeInTheDocument()
    expect(screen.getByText('25国债13')).toBeInTheDocument()
    expect(screen.getByText('目标 ETF 前十大穿透持仓')).toBeInTheDocument()
    expect(screen.getByText('招商中证半导体产业 ETF')).toBeInTheDocument()
    expect(screen.getByText('13.85%')).toBeInTheDocument()
    const disclosure = screen.getByText('持仓披露').closest('div')
    expect(disclosure).toHaveTextContent('2026-06-30')
    expect(disclosure).not.toHaveTextContent('日期未取得')
    expect(screen.getByRole('link', { name: '查看官方报告' })).toHaveAttribute('href', 'https://pdf.dfcfw.com/example.pdf')
    expect(screen.getByText('单位净值完整趋势')).toBeInTheDocument()
    expect(screen.getByText('易方达基金')).toBeInTheDocument()
  })

  it('does not render a stale holding batch as the selected fund run and reloads the parent run', async () => {
    mocks.selectedRunDetailId = 'batch-newer-than-fund'

    render(<Funds onOpenRun={mocks.openRun} />)

    expect(screen.getByText('正在读取 ArtifactBoard')).toBeInTheDocument()
    expect(screen.queryByText('基金完整分析')).not.toBeInTheDocument()
    await waitFor(() => expect(mocks.reloadRun).toHaveBeenCalledWith('run-fund'))
  })

  it('lets the user select a category and omits empty fund codes', async () => {
    render(<Funds onOpenRun={mocks.openRun} />)
    const submit = screen.getByRole('button', { name: '请先选择研究周期' })
    expect(submit).toBeDisabled()
    expect(screen.getByRole('combobox', { name: '研究周期' })).toHaveValue('')
    fireEvent.change(screen.getByRole('combobox', { name: '研究周期' }), { target: { value: 'long_term' } })
    fireEvent.change(screen.getByLabelText('基金类别'), { target: { value: '债券型' } })
    expect(screen.getByLabelText('基金类别')).toHaveValue('债券型')
    fireEvent.click(screen.getByRole('button', { name: '开始真实选基' }))

    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1))
    expect(mocks.createRun).toHaveBeenCalledWith(
      expect.stringContaining('从债券型基金中选择'),
      'fund_research',
      expect.objectContaining({ fund_category: '债券型', candidate_pool_size: 8 }),
    )
    expect(mocks.createRun).toHaveBeenCalledWith(
      expect.any(String),
      'fund_research',
      expect.not.objectContaining({ fund_codes: expect.anything() }),
    )
  })

  it('submits explicit fund codes through the dedicated analysis mode', async () => {
    render(<Funds onOpenRun={mocks.openRun} />)
    fireEvent.change(screen.getByRole('combobox', { name: '研究周期' }), { target: { value: 'long_term' } })
    fireEvent.click(screen.getByRole('button', { name: '指定基金分析' }))
    fireEvent.change(screen.getByPlaceholderText('例如 110011, 005827'), {
      target: { value: '110011, 005827' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始基金分析' }))

    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1))
    expect(mocks.createRun).toHaveBeenCalledWith(
      expect.stringContaining('110011、005827'),
      'fund_research',
      expect.objectContaining({
        fund_codes: ['110011', '005827'],
        fund_category: '全部',
        candidate_pool_size: 2,
      }),
    )
  })

  it('treats one explicit fund as an independent analysis instead of a comparison', async () => {
    render(<Funds onOpenRun={mocks.openRun} />)
    fireEvent.change(screen.getByRole('combobox', { name: '研究周期' }), { target: { value: 'long_term' } })
    fireEvent.click(screen.getByRole('button', { name: '指定基金分析' }))
    fireEvent.change(screen.getByPlaceholderText('例如 110011, 005827'), {
      target: { value: '020464' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始基金分析' }))

    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1))
    expect(mocks.createRun).toHaveBeenCalledWith(
      expect.stringMatching(/^分析基金 020464/),
      'fund_research',
      expect.objectContaining({
        fund_codes: ['020464'],
        candidate_pool_size: 1,
      }),
    )
  })

  it('starts a lite top-holding report batch from the frozen fund run', async () => {
    render(<Funds onOpenRun={mocks.openRun} />)
    await waitFor(() => expect(screen.getByRole('combobox', { name: '研究档位' })).toBeEnabled())
    fireEvent.change(screen.getByRole('combobox', { name: '研究档位' }), {
      target: { value: 'lite' },
    })
    fireEvent.click(screen.getByRole('button', { name: '生成 速判持仓股票报告' }))

    await waitFor(() => {
      const calls = vi.mocked(fetch).mock.calls
      expect(calls.some(([input, init]) => String(input).endsWith('/runs/run-fund/holding-reports') && init?.method === 'POST')).toBe(true)
    })
    const post = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).endsWith('/runs/run-fund/holding-reports') && init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual(expect.objectContaining({
      fund_code: '110011',
      profile: 'lite',
      force: false,
    }))
  })

  it('shows live holding progress from real child execution nodes', async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/runs/child-running/steps')) {
        return new Response(JSON.stringify({
          run_id: 'child-running',
          status: 'running',
          steps: [
            { node_id: 'collect-market', objective: '采集市场行情', status: 'completed' },
            { node_id: 'collect-finance', objective: '采集财务快照', status: 'completed' },
            {
              node_id: 'uzi-analysis', objective: '生成 UZI 结构化股票分析', status: 'running',
              current_action: 'UZI 第 1 轮已完成 4/9：dimensions_16_20', progress_percent: 50,
            },
            { node_id: 'quality-gate', objective: '执行质量门', status: 'pending' },
            { node_id: 'report', objective: '生成最终报告', status: 'pending' },
          ],
          step_counts: { total: 5, active: 1, completed: 2, pending: 2 },
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/holding-report-batches')) {
        return new Response(JSON.stringify([{
          batch_id: 'batch-running', source_run_id: 'run-fund', source_artifact_id: 'artifact-fund',
          fund_code: '110011', fund_name: '易方达优质精选混合', fund_type: 'ETF联接',
          profile: 'medium', status: 'running', concurrency: 1,
          holding_source: 'target_etf_lookthrough', is_fund_of_funds: true,
          disclosure_date: '2026-06-30', disclosure_age_days: 38, stale_level: 'current', cancel_requested: false,
          holdings: [
            {
              rank: 1, symbol: '688012', name: '中微公司', market: 'SH', supported: true,
              status: 'running', fund_weight_pct: 13.85, target_security_weight_pct: 15.02,
              holding_source: 'target_etf_lookthrough', disclosure_date: '2026-06-30',
              child_run_id: 'child-running', attempt_run_ids: ['child-running'], report_available: false,
              retry_count: 0, data_gap: [], error: null, started_at: '2026-08-10T00:00:00Z', completed_at: null,
            },
            {
              rank: 2, symbol: '002371', name: '北方华创', market: 'SZ', supported: true,
              status: 'queued', fund_weight_pct: 13.01, target_security_weight_pct: 14.11,
              holding_source: 'target_etf_lookthrough', disclosure_date: '2026-06-30',
              child_run_id: 'child-queued', attempt_run_ids: ['child-queued'], report_available: false,
              retry_count: 0, data_gap: [], error: null, started_at: null, completed_at: null,
            },
          ],
          counts: { total: 2, supported: 2, queued: 1, running: 1, deliverable: 0, failed: 0, cancelled: 0, unsupported: 0 },
          data_gap: [], created_at: '2026-08-10T00:00:00Z', updated_at: '2026-08-10T00:01:00Z', completed_at: null,
        }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    render(<Funds onOpenRun={mocks.openRun} />)

    await waitFor(() => expect(screen.getByText('UZI 第 1 轮已完成 4/9：dimensions_16_20')).toBeInTheDocument())
    expect(screen.getByText('实时执行进度')).toBeInTheDocument()
    expect(screen.getByText('2 / 5 节点')).toBeInTheDocument()
    expect(screen.getByText('等待可用并发槽位')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '持仓分析任务处理进度 25%' })).toHaveAttribute('aria-valuenow', '25')
  })

  it('restores a persisted batch, retries one failed report and opens the fund strategy context', async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const batch = {
        batch_id: 'batch-failed', source_run_id: 'run-fund', source_artifact_id: 'artifact-fund',
        fund_code: '110011', fund_name: '易方达优质精选混合', fund_type: 'ETF联接',
        profile: 'deep', status: 'failed', concurrency: 5,
        holding_source: 'target_etf_lookthrough', is_fund_of_funds: true,
        disclosure_date: '2026-06-30', disclosure_age_days: 38, stale_level: 'current', cancel_requested: false,
        holdings: [{
          rank: 1, symbol: '688012', name: '中微公司', market: 'SH', supported: true,
          status: init?.method === 'POST' ? 'queued' : 'failed', fund_weight_pct: 13.85,
          target_security_weight_pct: 15.02, holding_source: 'target_etf_lookthrough',
          disclosure_date: '2026-06-30', child_run_id: null, attempt_run_ids: [], report_available: false,
          retry_count: 0, data_gap: [], error: init?.method === 'POST' ? null : 'temporary failure',
          started_at: null, completed_at: null,
        }],
        counts: { total: 1, supported: 1, queued: init?.method === 'POST' ? 1 : 0, running: 0, deliverable: 0, failed: init?.method === 'POST' ? 0 : 1, cancelled: 0, unsupported: 0 },
        data_gap: [], created_at: '2026-08-07T00:00:00Z', updated_at: '2026-08-07T00:00:00Z', completed_at: '2026-08-07T00:10:00Z',
      }
      if (url.endsWith('/holding-report-batches')) return new Response(JSON.stringify([batch]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/holding-report-batches/batch-failed/retry') && init?.method === 'POST') return new Response(JSON.stringify(batch), { status: 200, headers: { 'Content-Type': 'application/json' } })
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    render(<Funds onOpenRun={mocks.openRun} onOpenStrategy={mocks.openStrategy} />)

    await waitFor(() => expect(screen.getByRole('combobox', { name: '研究档位' })).toHaveValue('deep'))
    expect(screen.getByText('批次状态')).toBeInTheDocument()
    expect(screen.getByText('执行失败')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '仅重试此项' }))
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([input]) => String(input).endsWith('/holding-report-batches/batch-failed/retry'))
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ symbols: ['688012'], force: false })
    })
    fireEvent.click(screen.getByRole('button', { name: '持仓组合回测' }))
    expect(mocks.openStrategy).toHaveBeenCalledWith(expect.objectContaining({
      tab: 'assignments',
      fundCode: '110011',
      mode: 'holdings_portfolio',
      holdingSymbol: '688012',
      sourceRunId: 'run-fund',
    }))
  })

  it('opens the fund-level action in fund-only mode while holding actions stay separate', async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/finance/strategies?')) {
        return new Response(JSON.stringify({ strategies: [{ id: 'strategy-1', name: '基金策略' }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/finance/strategy-assignments')) {
        return new Response(JSON.stringify({ assignments: [
          { id: 'assignment-fund', fund_code: '110011', strategy_id: 'strategy-1', active_version_id: 'version-1', mode: 'fund_only', enabled: true, paused: false },
          { id: 'assignment-holdings', fund_code: '110011', strategy_id: 'strategy-1', active_version_id: 'version-1', mode: 'holdings_portfolio', enabled: true, paused: false },
        ] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/finance/daily-report-sets')) {
        return new Response(JSON.stringify({ report_sets: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      if (url.endsWith('/finance/trade-signals')) {
        return new Response(JSON.stringify({ signals: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    render(<Funds onOpenRun={mocks.openRun} onOpenStrategy={mocks.openStrategy} />)

    const button = await screen.findByRole('button', { name: '回测基金净值' })
    fireEvent.click(button)
    expect(mocks.openStrategy).toHaveBeenCalledWith(expect.objectContaining({
      tab: 'backtests',
      fundCode: '110011',
      mode: 'fund_only',
      assignmentId: 'assignment-fund',
      strategyVersionId: 'version-1',
    }))
  })

  it('regenerates only one selected completed report without rerunning the batch', async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const batch = {
        batch_id: 'batch-completed', source_run_id: 'run-fund', source_artifact_id: 'artifact-fund',
        fund_code: '110011', fund_name: '易方达优质精选混合', fund_type: 'ETF联接',
        profile: 'medium', status: init?.method === 'POST' ? 'running' : 'completed', concurrency: 5,
        holding_source: 'target_etf_lookthrough', is_fund_of_funds: true,
        disclosure_date: '2026-06-30', disclosure_age_days: 38, stale_level: 'current', cancel_requested: false,
        holdings: [{
          rank: 1, symbol: '688012', name: '中微公司', market: 'SH', supported: true,
          status: init?.method === 'POST' ? 'queued' : 'completed', fund_weight_pct: 13.85,
          target_security_weight_pct: 15.02, holding_source: 'target_etf_lookthrough',
          disclosure_date: '2026-06-30', child_run_id: 'child-completed', attempt_run_ids: ['child-completed'],
          report_available: init?.method !== 'POST', retry_count: 0, data_gap: [], error: null,
          started_at: null, completed_at: '2026-08-07T00:10:00Z',
        }],
        counts: { total: 1, supported: 1, queued: init?.method === 'POST' ? 1 : 0, running: 0, deliverable: init?.method === 'POST' ? 0 : 1, failed: 0, cancelled: 0, unsupported: 0 },
        data_gap: [], created_at: '2026-08-07T00:00:00Z', updated_at: '2026-08-07T00:10:00Z', completed_at: '2026-08-07T00:10:00Z',
      }
      if (url.endsWith('/holding-report-batches')) return new Response(JSON.stringify([batch]), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/holding-report-batches/batch-completed/retry') && init?.method === 'POST') return new Response(JSON.stringify(batch), { status: 200, headers: { 'Content-Type': 'application/json' } })
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    render(<Funds onOpenRun={mocks.openRun} />)

    await waitFor(() => expect(screen.getByRole('button', { name: '重新生成此项' })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '重新生成此项' }))
    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([input, init]) => (
        String(input).endsWith('/holding-report-batches/batch-completed/retry') && init?.method === 'POST'
      ))
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ symbols: ['688012'], force: true })
    })
  })
})
