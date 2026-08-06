import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Funds } from './Funds'

const mocks = vi.hoisted(() => ({
  createRun: vi.fn(async () => ({ run_id: 'run-new' })),
  selectRun: vi.fn(),
  openRun: vi.fn(),
}))

vi.mock('../CognitradeContext', () => ({
  useCognitrade: () => ({
    createRun: mocks.createRun,
    runs: [{ run_id: 'run-fund', intent: 'fund_research', status: 'completed' }],
    selectedRunId: 'run-fund',
    selectRun: mocks.selectRun,
    selectedRunLoading: false,
    submitting: false,
    selectedRun: {
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
                  fund_type: '混合型-偏股',
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
                  target_fund_holdings: [],
                  lookthrough_holdings: [],
                  holdings_coverage: {
                    asset_allocation: 'available', direct_equity: 'available', bond: 'available',
                    target_fund: 'not_applicable', lookthrough: 'not_applicable', is_fund_of_funds: false,
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
    mocks.openRun.mockClear()
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
    expect(screen.getByText('单位净值完整趋势')).toBeInTheDocument()
    expect(screen.getByText('易方达基金')).toBeInTheDocument()
  })

  it('lets the user select a category and omits empty fund codes', async () => {
    render(<Funds onOpenRun={mocks.openRun} />)
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
})
