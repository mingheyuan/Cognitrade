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
            artifact_type: 'uzi_native_method_result',
            content: {
              method_id: 'returns_attribution',
              quality_status: 'degraded',
              results: [
                {
                  symbol: 'portfolio',
                  result: {
                    observed_weight_coverage: 0.6,
                    observed_contribution_pct: 6,
                    total_return_pct: null,
                    holdings: [
                      { ticker: '600519', name: '贵州茅台', return_pct: 10, contribution_pct: 6 },
                      { ticker: '300750', name: '宁德时代', return_pct: null, contribution_pct: null },
                    ],
                  },
                },
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
    expect(screen.getByText('returns_attribution')).toBeInTheDocument()
    expect(screen.getAllByText('贵州茅台').length).toBeGreaterThan(0)
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
    expect(intent).toBe('uzi_returns')
    expect(constraints).not.toHaveProperty('fee_model')
    expect(constraints).not.toHaveProperty('rebalance_targets')
    expect(constraints.portfolio_holdings).toEqual([
      expect.objectContaining({ ticker: '600519', weight: 60, return_pct: 8 }),
      expect.objectContaining({ ticker: '300750', weight: 40, return_pct: -2 }),
    ])
  })

  it('dispatches each stock method through its independent Agent OS intent', async () => {
    render(<UziMethods onOpenRun={mocks.openRun} />)
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: '600519' } })
    fireEvent.click(screen.getByRole('button', { name: '运行 UZI 原生命令 / 方法' }))

    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1))
    const [, intent, constraints] = mocks.createRun.mock.calls[0]
    expect(intent).toBe('uzi_ai_readiness')
    expect(constraints).toEqual({ symbol: '600519' })
  })

  it('exposes all 20 pinned UZI commands through Web controls', () => {
    render(<UziMethods onOpenRun={mocks.openRun} />)
    for (const label of [
      '完整个股分析', '股票速判', '独立评审团', '杀猪盘扫描', 'DCF 估值', '可比公司',
      '快速 LBO', '分部模型', '首次覆盖', '财报解读', '催化剂日历', '投资逻辑追踪',
      '量化筛选', '尽调清单', '投委会备忘录', 'AI 就绪度', '财报前瞻', '模型更新',
    ]) {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument()
    }
    fireEvent.click(screen.getByRole('button', { name: /组合归因与再平衡/ }))
    const selector = screen.getByLabelText('组合方法')
    expect(selector).toHaveTextContent('收益归因')
    expect(selector).toHaveTextContent('持仓再平衡')
  })

  it('requires an explicit profile for the complete analyze-stock command', async () => {
    render(<UziMethods onOpenRun={mocks.openRun} />)
    fireEvent.click(screen.getByRole('button', { name: /完整个股分析/ }))
    fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: '600519' } })
    const submit = screen.getByRole('button', { name: '运行 UZI 原生命令 / 方法' })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('研究深度（必须显式选择）'), { target: { value: 'deep' } })
    fireEvent.click(submit)

    await waitFor(() => expect(mocks.createRun).toHaveBeenCalledTimes(1))
    const [, intent, constraints] = mocks.createRun.mock.calls[0]
    expect(intent).toBe('uzi_analyze_stock')
    expect(constraints).toEqual({ symbol: '600519', research_profile: 'deep' })
  })
})
