import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StrategyBacktest, StrategyDocument, StrategyRecord } from '../strategy-contracts'
import { Strategies } from './Strategies'

vi.mock('../CognitradeContext', () => ({
  useCognitrade: () => ({
    settings: { baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' },
    methodologyDashboard: {},
  }),
}))

const documentFixture: StrategyDocument = {
  metadata: { schema_version: '1', description: '基金报告复合策略' },
  pins: {
    uzi_methodology_version: '3.9.2', uzi_source_commit: 'commit-a', model_id: 'model-a',
    model_parameters: {}, prompt_hash: 'prompt-a', output_schema_versions: { research_report: '1' },
    snapshot_schema_version: '1', factor_extractor_version: 'fund-composite-v1',
  },
  report_inputs: {
    holding_uzi_required: true, research_profile: 'deep', top_n: 10,
    independent_holding_signals_enabled: true,
    enabled_backtest_modes: ['fund_only', 'holdings_portfolio'], supported_fund_types: ['股票型'],
  },
  fund_composite: { fund_performance_risk: 0.4, holding_stock_quality: 0.4, holding_structure_quality: 0.2 },
  factor_weights: {
    fund_performance_risk: {
      year_1_return: 0.2, since_inception_annualized: 0.15, benchmark_excess_year_1: 0.15,
      max_drawdown: 0.2, volatility: 0.15, fee: 0.05, return_consistency: 0.1,
    },
    holding_stock_quality: { uzi_overall_score: 1 },
    holding_structure_quality: {
      count_coverage: 0.25, weight_coverage: 0.25, disclosure_freshness: 0.2,
      concentration_risk: 0.1, lookthrough_completeness: 0.1, unsupported_weight: 0.1,
    },
  },
  signal_thresholds: { buy: 70, watch: 55, minimum_confidence: 0.65, missing_report_carry_days: 3 },
  quality_gates: {
    normal_coverage: 0.8, minimum_coverage: 0.6, max_holding_staleness_days: 120,
    require_report_allowed: true, require_no_critical: true, require_symbol_match: true,
    point_in_time_required: true,
  },
  execution: { technical_rule: 'ma_cross', fast_period: 5, slow_period: 20, max_single_stock_pct: 20, rebalance_drift_pct_points: 2 },
  asset_execution: {
    stock: { board_lot: 100, commission_rate: 0.00025, minimum_commission: 5, stamp_duty_rate: 0.0005, transfer_fee_rate: 0.00001, settlement: 'T+1' },
    exchange_fund: { board_lot: 100, commission_rate: 0.00025, minimum_commission: 5, settlement: 'T+1' },
    otc_fund: { subscription_fee_rate_fallback: 0.0015, redemption_fee_rate_fallback: 0.005, subscription_confirmation_days: 1, redemption_confirmation_days: 2, require_open_status: true },
  },
  schedule_defaults: { enabled: false, timezone: 'Asia/Shanghai', local_time: '16:30', settlement_cutoff: '08:30', skip_non_trading_days: true },
}

const version = {
  id: 'v1', strategy_id: 's1', version_number: 1, content_hash: 'a'.repeat(64),
  governance_state: 'published' as const, published_at: '2026-08-01T10:00:00Z', published_by: 'tester',
}

const assignment = {
  id: 'a1', fund_code: '110011', fund_name: '测试权益基金', strategy_id: 's1', active_version_id: 'v1',
  pending_version_id: null, effective_from: '2026-08-04', mode: 'holdings_portfolio' as const,
  schedule: {}, enabled: true, paused: false, created_at: '2026-08-01T10:00:00Z', updated_at: '2026-08-01T10:00:00Z',
}

function strategyFixture(overrides: Partial<StrategyRecord> = {}): StrategyRecord {
  return {
    id: 's1', name: '权益基金复合策略', kind: 'fund_report_composite', is_system_template: false,
    lifecycle: 'published', has_draft: true, draft_revision: 1, version_count: 1, assignment_count: 1,
    latest_version: version, created_at: '2026-08-01T10:00:00Z', updated_at: '2026-08-01T10:00:00Z',
    draft: {
      strategy_id: 's1', base_version_id: 'v1', draft_revision: 1, document: structuredClone(documentFixture),
      validation: { valid: true, errors: [] }, updated_by: 'tester', updated_at: '2026-08-01T10:00:00Z',
    },
    versions: [version], assignments: [assignment], ...overrides,
  }
}

describe('Strategies', () => {
  let current = strategyFixture()
  let strategyBacktests: StrategyBacktest[] = []
  let verificationRequests: Array<Record<string, unknown>> = []

  beforeEach(() => {
    current = strategyFixture()
    strategyBacktests = []
    verificationRequests = []
    localStorage.clear()
    window.history.replaceState({}, '', '/strategies')
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input))
      const path = `${url.pathname}${url.search}`
      if (url.pathname.endsWith('/finance/strategies') && init?.method === 'POST') {
        current = strategyFixture({ id: 's2', name: '新策略 2', lifecycle: 'draft', latest_version: null, version_count: 0, assignment_count: 0, assignments: [], versions: [], draft: { ...strategyFixture().draft!, strategy_id: 's2', base_version_id: null } })
        return json(current, 201)
      }
      if (url.pathname.endsWith('/finance/strategies/s1/draft') && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { name: string }
        current = strategyFixture({ name: body.name, draft_revision: 2, draft: { ...strategyFixture().draft!, draft_revision: 2 } })
        return json(current)
      }
      if (url.pathname.endsWith('/finance/strategies/s1/publish') && init?.method === 'POST') return json(version)
      if (url.pathname.endsWith('/finance/strategies/s1/events')) return json({ strategy_id: 's1', events: [] })
      if (url.pathname.endsWith('/finance/strategies/s1')) return json(current)
      if (url.pathname.endsWith('/finance/strategies/s2')) return json(current)
      if (url.pathname.endsWith('/finance/strategies') && url.searchParams.has('include_archived')) return json({ strategies: [current] })
      if (url.pathname.endsWith('/finance/report-jobs')) return json({ jobs: [] })
      if (url.pathname.endsWith('/finance/daily-report-sets')) return json({ report_sets: [{ id: 'r1', assignment_id: 'another-assignment', fund_code: '110011', fund_name: '测试权益基金', as_of_date: '2026-08-03', effective_date: '2026-08-04', revision: 1, status: 'final', max_top_n: 10, coverage: 1, count_coverage: 1, weight_coverage: 1, data_gaps: [] }] })
      if (url.pathname.endsWith('/finance/trade-signals')) return json({ signals: [{ id: 'sig1', assignment_id: 'a1', strategy_version_id: 'v1', source_report_set_id: 'r1', kind: 'fund', subject_code: '110011', as_of_date: '2026-08-03', effective_from: '2026-08-04', expires_after: '2026-08-07', state: 'ALLOW_BUY', score_lower_bound: 82, score_upper_bound: 82, coverage: 1, confidence: 1, reasons: ['score_at_or_above_buy'], content_hash: 'b'.repeat(64) }] })
      if (url.pathname.endsWith('/finance/strategy-backtests')) return json({ backtests: strategyBacktests })
      if (url.pathname.endsWith('/finance/strategy-verifications') && init?.method === 'POST') {
        verificationRequests.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return json({ id: 'verify-1', machine_passed: false, checks: {}, thresholds: {}, strategy_version_id: 'v1', backtest_run_id: 'bt1', status: 'failed', created_at: '2026-08-05T00:00:00Z' }, 201)
      }
      if (url.pathname.endsWith('/finance/strategy-verifications')) return json({ verifications: [] })
      if (url.pathname.endsWith('/finance/paper-strategy-bindings')) return json({ bindings: [] })
      throw new Error(`unexpected request: ${path} ${init?.method || 'GET'}`)
    }))
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('restores a real shared report and persisted signal from the URL context', async () => {
    window.history.replaceState({}, '', '/strategies?tab=signals&strategy_id=s1&fund_code=110011&assignment_id=a1&report_set_id=r1&mode=holdings_portfolio')
    render(<Strategies />)

    expect(await screen.findByText('基于真实日报预演信号')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('日报 revision')).toHaveValue('r1'))
    expect(await screen.findByText('ALLOW_BUY')).toBeInTheDocument()
    expect(screen.getByText('基金 · 110011')).toBeInTheDocument()
  })

  it('opens holdings-portfolio backtesting without free strategy overrides', async () => {
    window.history.replaceState({}, '', '/strategies?tab=backtests&strategy_id=s1&fund_code=110011&strategy_version_id=v1&mode=holdings_portfolio')
    render(<Strategies />)

    expect(await screen.findByText('回测预检')).toBeInTheDocument()
    expect(screen.getByLabelText('策略版本')).toHaveValue('v1')
    expect(screen.getByLabelText('目标基金')).toHaveValue('110011')
    expect(screen.getByLabelText('模式')).toHaveValue('holdings_portfolio')
    expect(screen.queryByLabelText('快均线')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('买入阈值')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('股票佣金率')).not.toBeInTheDocument()
  })

  it('saves a server draft with optimistic revision and then reviews publication', async () => {
    render(<Strategies />)
    const name = await screen.findByLabelText('策略名称')
    fireEvent.change(name, { target: { value: '修改后的策略' } })
    fireEvent.click(await screen.findByRole('button', { name: '保存草稿' }))

    await waitFor(() => {
      const patchCall = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).includes('/finance/strategies/s1/draft') && init?.method === 'PATCH')
      expect(new Headers(patchCall?.[1]?.headers).get('If-Match')).toBe('W/"1"')
      expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual(expect.objectContaining({ expected_revision: 1, name: '修改后的策略' }))
    })
    await waitFor(() => expect(screen.getByRole('button', { name: '发布版本' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '发布版本' }))
    expect(screen.getByRole('dialog', { name: '确认发布不可变版本' })).toBeInTheDocument()
    expect(screen.getByText('缺失不重分配')).toBeInTheDocument()
    expect(screen.getByText(/PIT 强制/)).toBeInTheDocument()
  })

  it('submits configurable verification performance thresholds', async () => {
    strategyBacktests = [{
      id: 'bt1', strategy_id: 's1', strategy_version_id: 'v1', strategy_content_hash: 'a'.repeat(64),
      fund_code: '110011', fund_name: '测试权益基金', mode: 'holdings_portfolio', start_date: '2026-01-01',
      end_date: '2026-08-01', initial_cash: 1_000_000, status: 'completed', metrics: {}, created_at: '2026-08-02T00:00:00Z',
    }]
    window.history.replaceState({}, '', '/strategies?tab=governance&strategy_id=s1')
    render(<Strategies />)

    const drawdown = await screen.findByLabelText('最大回撤上限 %')
    fireEvent.change(drawdown, { target: { value: '25' } })
    fireEvent.click(screen.getByRole('button', { name: '运行机器检查' }))
    await waitFor(() => expect(verificationRequests).toHaveLength(1))
    expect(verificationRequests[0]).toEqual(expect.objectContaining({
      strategy_version_id: 'v1',
      backtest_run_id: 'bt1',
      thresholds: expect.objectContaining({ maximum_drawdown_pct: 25, minimum_sharpe: -10 }),
    }))
  })
})

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}
