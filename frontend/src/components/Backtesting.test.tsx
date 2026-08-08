import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Backtesting } from './Backtesting'

vi.mock('../CognitradeContext', () => ({
  useCognitrade: () => ({
    settings: { baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' },
  }),
}))

const engine = {
  engine: 'hikyuu', available: true, version: '2.6.5', mode: 'isolated_external_kdata',
  data_source: 'Finance Pack K-line snapshot',
  strategies: [
    {
      id: 'ma_cross', name: '双均线交叉', description: '双均线策略',
      parameters: [
        { id: 'fast_period', label: '短周期', type: 'integer', default: 5, minimum: 2, maximum: 120, step: 1 },
        { id: 'slow_period', label: '长周期', type: 'integer', default: 20, minimum: 3, maximum: 250, step: 1 },
      ],
    },
    {
      id: 'price_ma_cross', name: '价格均线趋势', description: '价格穿越均线策略',
      parameters: [
        { id: 'lookback_period', label: '均线周期', type: 'integer', default: 20, minimum: 3, maximum: 250, step: 1 },
      ],
    },
  ],
}

const result = {
  backtest_id: '01J00000000000000000000001', created_at: '2026-08-07T00:00:00Z',
  engine: { name: 'hikyuu', version: '2.6.5' }, symbol: '688012', name: '中微公司',
  strategy: { id: 'price_ma_cross', name: '价格均线趋势' },
  parameters: { initial_cash: 1_000_000, lookback_period: 30, trade_size: 1000, commission_rate: .00025, minimum_commission: 5, stamp_duty_rate: .0005, transfer_fee_rate: .00001 },
  period: { start: '2025-01-01', end: '2026-08-07', bar_count: 300, frequency: 'DAY' },
  metrics: { initial_cash: 1_000_000, final_equity: 1_050_000, total_return_pct: 5, annualized_return_pct: 3, max_drawdown_pct: -4, closed_trade_count: 2, winning_trade_rate_pct: 50, profit_loss_ratio: 1.2 },
  equity_curve: [{ date: '2025-01-01', equity: 1_000_000 }, { date: '2026-08-07', equity: 1_050_000 }],
  trades: [], data_quality_status: 'available', data_source: { provider_id: 'test' },
}

describe('Backtesting', () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/finance/backtests/engine')) return new Response(JSON.stringify(engine), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.includes('/finance/backtests?')) return new Response(JSON.stringify({ results: [], total_count: 0, limit: 100, offset: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      if (url.endsWith('/finance/backtests') && init?.method === 'POST') return new Response(JSON.stringify(result), { status: 201, headers: { 'Content-Type': 'application/json' } })
      return new Response(JSON.stringify(result), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
  })

  it('uses a fund holding as the target and submits the selected registered strategy', async () => {
    render(<Backtesting initialTarget={{ symbol: '688012', name: '中微公司', fundCode: '020464', fundName: '测试基金' }} />)

    expect(screen.getByDisplayValue('688012')).toBeInTheDocument()
    expect(screen.getByText(/来自 测试基金 · 中微公司/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('回测策略')).toHaveValue('ma_cross'))
    fireEvent.change(screen.getByLabelText('回测策略'), { target: { value: 'price_ma_cross' } })
    fireEvent.change(screen.getByLabelText('均线周期'), { target: { value: '30' } })
    fireEvent.click(screen.getByRole('button', { name: '开始回测' }))

    await waitFor(() => {
      const call = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).endsWith('/finance/backtests') && init?.method === 'POST')
      expect(JSON.parse(String(call?.[1]?.body))).toEqual(expect.objectContaining({
        symbol: '688012',
        strategy: 'price_ma_cross',
        strategy_parameters: { lookback_period: 30 },
      }))
    })
  })
})
