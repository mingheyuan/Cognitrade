import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { UziMethods } from './UziMethods'

const mocks = vi.hoisted(() => ({
  createRun: vi.fn(async (query: string, intent?: string, constraints?: Record<string, unknown>) => {
    void query
    void intent
    void constraints
    return { run_id: 'run-new' }
  }),
  selectRun: vi.fn(),
  openRun: vi.fn(),
}))

vi.mock('../CognitradeContext', () => ({
  useCognitrade: () => ({
    createRun: mocks.createRun,
    selectedRunId: 'run-tier1',
    selectRun: mocks.selectRun,
    submitting: false,
    selectedRun: {
      replay: {
        artifacts: [
          {
            artifact_type: 'returns_attribution',
            content: {
              quality_status: 'degraded',
              observed_weight_coverage: 0.6,
              observed_contribution_pct: 6,
              total_return_pct: null,
              holdings: [
                { ticker: '600519', name: '贵州茅台', return_pct: 10, contribution_pct: 6 },
                { ticker: '300750', name: '宁德时代', return_pct: null, contribution_pct: null },
              ],
              data_gap: ['return_pct_missing:300750'],
            },
          },
        ],
      },
    },
  }),
}))

describe('UziMethods', () => {
  afterEach(cleanup)

  beforeEach(() => {
    mocks.createRun.mockClear()
    mocks.selectRun.mockClear()
    mocks.openRun.mockClear()
  })

  it('renders coverage and gaps without raw JSON', () => {
    render(<UziMethods onOpenRun={mocks.openRun} />)

    expect(screen.getByText('专项研究与组合方法')).toBeInTheDocument()
    expect(screen.getByText('60.0%')).toBeInTheDocument()
    expect(screen.getByText('贵州茅台')).toBeInTheDocument()
    expect(screen.getByText('1 项数据缺口')).toBeInTheDocument()
  })

  it('does not submit an implicit fee model or partial target set', async () => {
    render(<UziMethods onOpenRun={mocks.openRun} />)
    fireEvent.click(screen.getByRole('button', { name: /组合归因与再平衡/ }))
    fireEvent.change(screen.getByLabelText('持仓 1 代码'), { target: { value: '600519' } })
    fireEvent.change(screen.getByLabelText('持仓 1 权重'), { target: { value: '60' } })
    fireEvent.change(screen.getByLabelText('持仓 1 收益'), { target: { value: '8' } })
    fireEvent.change(screen.getByLabelText('持仓 1 目标'), { target: { value: '50' } })
    fireEvent.change(screen.getByLabelText('持仓 2 代码'), { target: { value: '300750' } })
    fireEvent.change(screen.getByLabelText('持仓 2 权重'), { target: { value: '40' } })
    fireEvent.change(screen.getByLabelText('持仓 2 收益'), { target: { value: '-2' } })
    fireEvent.click(screen.getByRole('button', { name: '运行归因与再平衡' }))

    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1))
    const [, intent, constraints = {}] = mocks.createRun.mock.calls[0]
    expect(intent).toBe('portfolio_research')
    expect(constraints).not.toHaveProperty('fee_model')
    expect(constraints).not.toHaveProperty('rebalance_targets')
    expect(constraints.portfolio_holdings).toEqual([
      expect.objectContaining({ ticker: '600519', weight: 60, return_pct: 8 }),
      expect.objectContaining({ ticker: '300750', weight: 40, return_pct: -2 }),
    ])
  })
})
