import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { BacktestResult, PaperOrder, PaperPortfolio, QuantEngineStatus } from '../finance-contracts'
import { BacktestEngineDisclosure, BacktestResultPanel } from './Backtesting'
import { PaperAccountDisclosure, PaperMatchDisclosure, PaperOrderAudit, PaperQuoteDisclosure } from './PaperTrading'

describe('complete trading disclosures', () => {
  afterEach(cleanup)

  it('renders the complete paper account, rules, engine capabilities and order audit', () => {
    const portfolio: PaperPortfolio = {
      account: {
        account_id: 'paper-account-complete', generation: 3, status: 'active', currency: 'CNY', initial_cash: 1_000_000,
        cash: 899_000, created_at: '2026-08-01T01:00:00Z', updated_at: '2026-08-05T02:00:00Z',
        rules: { market: 'A-share paper trading', board_lot: 100, settlement: 'T+1', short_selling: false, price_source: 'Finance Pack quote', matching_engine: 'veighna-paperaccount', market_hours_enforced: true, calendar: 'SSE/SZSE', timezone: 'Asia/Shanghai', sessions: ['09:30-11:30', '13:00-15:00'] },
      },
      summary: { cash: 899_000, cash_balance: 899_000, frozen_cash: 0, market_value: 101_000, equity: 1_000_000, cost_basis: 100_000, total_pnl: 0, total_return_pct: 0, position_count: 1, open_order_count: 0 },
      positions: [], as_of: '2026-08-05T02:00:00Z',
    }
    const engine: QuantEngineStatus = { engine: 'veighna-paperaccount', available: true, version: '4.1', versions: { vnpy: '4.1', paperaccount: '1.0' }, mode: 'local-paper', cash_ledger: 'cognitrade', capabilities: { market_orders: true, limit_orders: true, stop_orders: false, tick_crossing: true, fund_accounting: false } }
    const order: PaperOrder = {
      order_id: 'complete-order-id', client_order_id: 'complete-client-id', symbol: '600519', name: '贵州茅台', side: 'sell', order_type: 'limit', limit_price: 1600, quantity: 100, quote_price: 1599, fill_price: 1600, gross_amount: 160000,
      fees: { commission: 40, transfer_fee: 1.6, stamp_duty: 80, total: 121.6 }, cash_effect: 159878.4, realized_pnl: 59878.4, quote_as_of: '2026-08-05T02:00:00Z', quote_source: 'eastmoney_via_akshare', trade_date: '2026-08-05', status: 'filled', matching_engine: 'veighna-paperaccount', reserved_cash: 0, reserved_quantity: 0, created_at: '2026-08-05T02:00:00Z', updated_at: '2026-08-05T02:00:01Z', filled_at: '2026-08-05T02:00:01Z', idempotent_replay: false,
    }

    render(<><PaperAccountDisclosure portfolio={portfolio} engine={engine} /><PaperQuoteDisclosure quote={{ symbol: '600519', name: '贵州茅台', latest_price: 1600, bid_price_1: 1599.8, ask_price_1: 1600.2, pct_change: 1.25, as_of: '2026-08-05T02:00:00Z', source: { provider_id: 'quote-provider-complete' }, quality_status: 'available' }} /><PaperMatchDisclosure result={{ matched: [order], unchanged_order_ids: ['unchanged-order-complete'], errors: [{ order_id: 'failed-order-complete', error: "Connection aborted: RemoteDisconnected('closed')" }], matched_count: 1, pending_count: 1 }} /><PaperOrderAudit order={order} /></>)
    for (const value of ['paper-account-complete', 'SSE/SZSE', '本地模拟撮合', '限价撮合', '资金核算', 'paperaccount 版本', '持仓总成本', 'quote-provider-complete', 'unchanged-order-complete', 'failed-order-complete', 'complete-order-id', 'complete-client-id', 'eastmoney_via_akshare', '+¥59,878.40']) {
      expect(screen.getByText(value)).toBeInTheDocument()
    }
    expect(screen.getByText('行情数据源暂时不可用，请稍后重试。')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('RemoteDisconnected')
    expect(document.body.textContent).not.toContain('{"')
  })

  it('renders every backtest metric, parameter, data source and trade', () => {
    const result: BacktestResult = {
      backtest_id: 'complete-backtest-id', created_at: '2026-08-05T02:00:00Z', engine: { name: 'hikyuu', version: '2.6.5' }, symbol: '600519', name: '贵州茅台', strategy: { id: 'ma_cross', name: '双均线交叉' },
      parameters: { initial_cash: 1_000_000, fast_period: 5, slow_period: 20, trade_size: 1000, commission_rate: .00025, minimum_commission: 5, stamp_duty_rate: .0005, transfer_fee_rate: .00001 },
      period: { start: '2020-01-01', end: '2026-08-05', bar_count: 1600, frequency: 'DAY' },
      metrics: { initial_cash: 1_000_000, final_equity: 1_350_000, total_return_pct: 35, annualized_return_pct: 7.2, max_drawdown_pct: -12.5, closed_trade_count: 18, winning_trade_rate_pct: 61.11, profit_loss_ratio: 1.82 },
      equity_curve: [{ date: '2020-01-01', equity: 1_000_000 }, { date: '2026-08-05', equity: 1_350_000 }],
      trades: [{ date: '2026-07-01', side: 'buy', price: 1500, quantity: 1000, fees: 380, cash_after: 200_000 }], data_quality_status: 'available', data_source: { provider_id: 'akshare', vendor: 'eastmoney', dataset_id: 'daily-kline' },
    }

    render(<><BacktestEngineDisclosure engine={{ engine: 'hikyuu', available: true, version: '2.6.5', mode: 'isolated_external_kdata', data_source: 'Finance Pack K-line snapshot', strategies: [{ id: 'ma_cross', name: '双均线交叉', description: '完整策略说明' }] }} /><BacktestResultPanel result={result} /></>)
    expect(document.body.textContent).toContain('complete-backtest-id')
    for (const value of ['+35.00%', '+7.20%', '-12.50%', '+61.11%', '1.82', '0.025%', '0.050%', 'daily-kline']) {
      expect(screen.getAllByText(value).length).toBeGreaterThan(0)
    }
    expect(document.body.textContent).toContain('1,000 股')
    expect(screen.getByText('完整策略说明')).toBeInTheDocument()
    expect(screen.getByText(/全部成交记录/)).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('{"')
  })
})
