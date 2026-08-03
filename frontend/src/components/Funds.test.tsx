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
            artifact_type: 'fund_analysis',
            content: {
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
                  holdings_period: '2026年2季度',
                  top_holdings: [{ code: '600519', name: '贵州茅台', weight_pct: 9.5 }],
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
    expect(screen.getByText('易方达优质精选混合')).toBeInTheDocument()
    expect(screen.getByText('贵州茅台 +9.50%')).toBeInTheDocument()
    expect(screen.getByText('82.5')).toBeInTheDocument()
  })

  it('submits the dedicated fund intent and structured constraints', async () => {
    render(<Funds onOpenRun={mocks.openRun} />)
    fireEvent.change(screen.getByPlaceholderText('例如 110011, 005827'), {
      target: { value: '110011, 005827' },
    })
    fireEvent.click(screen.getByRole('button', { name: '开始真实选基' }))

    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1))
    expect(mocks.createRun).toHaveBeenCalledWith(
      expect.stringContaining('110011、005827'),
      'fund_research',
      expect.objectContaining({
        fund_codes: ['110011', '005827'],
        fund_category: '全部',
        candidate_pool_size: 8,
      }),
    )
  })
})
