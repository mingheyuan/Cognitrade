import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReportJob, StrategyBacktest, StrategyDocument, StrategyRecord } from '../strategy-contracts'
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
    otc_fund: { subscription_fee_rate_fallback: 0.0015, redemption_fee_rate_fallback: 0.005, subscription_confirmation_days: 1, redemption_confirmation_days: 2, require_open_status: true, missing_open_status_policy: 'assume_open_degraded', max_position_pct: 100, entry_tranche_pct: 100, max_entry_tranches: 1, minimum_entry_interval_days: 0 },
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
  let reportJobs: ReportJob[] = []
  let verificationRequests: Array<Record<string, unknown>> = []
  let assignmentUpdates: Array<Record<string, unknown>> = []
  let backtestCreates: Array<Record<string, unknown>> = []
  let backtestActions: string[] = []
  let backtestPreflight: Record<string, unknown>

  beforeEach(() => {
    current = strategyFixture()
    strategyBacktests = []
    reportJobs = []
    verificationRequests = []
    assignmentUpdates = []
    backtestCreates = []
    backtestActions = []
    backtestPreflight = {
      ready: false,
      strategy_content_hash: 'a'.repeat(64),
      signal_count: 0,
      available_report_count: 0,
      trading_day_count: 6,
      missing_report_dates: ['2026-08-07', '2026-08-10', '2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14'],
    }
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
      if (url.pathname.endsWith('/finance/strategy-assignments/a1') && init?.method === 'PATCH') {
        const update = JSON.parse(String(init.body)) as Record<string, unknown>
        assignmentUpdates.push(update)
        const nextAssignment = { ...assignment, ...update }
        current = strategyFixture({ assignments: [nextAssignment] })
        return json(nextAssignment)
      }
      if (url.pathname.endsWith('/finance/strategies/s1/events')) return json({ strategy_id: 's1', events: [] })
      if (url.pathname.endsWith('/finance/strategies/s1')) return json(current)
      if (url.pathname.endsWith('/finance/strategies/s2')) return json(current)
      if (url.pathname.endsWith('/finance/strategies') && url.searchParams.has('include_archived')) return json({ strategies: [current] })
      if (url.pathname.endsWith('/finance/strategy-backtests/preflight') && init?.method === 'POST') {
        return json(backtestPreflight)
      }
      if (url.pathname.endsWith('/finance/report-backfills/estimate') && init?.method === 'POST') {
        return json({ assignment_id: 'a1', trading_day_count: 6, fund_report_count: 6, unique_stock_analysis_upper_bound: 60 })
      }
      if (url.pathname.endsWith('/finance/report-backfills') && init?.method === 'POST') {
        const request = JSON.parse(String(init.body)) as Record<string, unknown>
        const job: ReportJob = {
          id: 'job-backfill-1', status: 'running', job_type: 'historical_backfill', request,
          progress: { total: 6, completed: 2, failed: 1, complete_report_days: 1, partial_report_days: 1, blocked_report_days: 0, current_date: '2026-08-11' },
          pause_requested: false, cancel_requested: false, attempts: [{ attempt: 1 }],
          created_at: '2026-08-14T10:00:00Z', updated_at: '2026-08-14T10:01:00Z',
        }
        reportJobs = [job]
        return json(job, 202)
      }
      if (url.pathname.endsWith('/finance/report-jobs/job-backfill-1')) return json(reportJobs[0])
      if (url.pathname.endsWith('/finance/report-jobs')) return json({ jobs: reportJobs })
      if (url.pathname.endsWith('/finance/daily-report-sets')) return json({ report_sets: [{ id: 'r1', assignment_id: 'another-assignment', fund_code: '110011', fund_name: '测试权益基金', as_of_date: '2026-08-03', effective_date: '2026-08-04', revision: 1, status: 'final', max_top_n: 10, coverage: 1, count_coverage: 1, weight_coverage: 1, data_gaps: [] }] })
      if (url.pathname.endsWith('/finance/trade-signals')) return json({ signals: [{ id: 'sig1', assignment_id: 'a1', strategy_version_id: 'v1', source_report_set_id: 'r1', kind: 'fund', subject_code: '110011', as_of_date: '2026-08-03', effective_from: '2026-08-04', expires_after: '2026-08-07', state: 'ALLOW_BUY', score_lower_bound: 82, score_upper_bound: 82, coverage: 1, confidence: 1, reasons: ['score_at_or_above_buy'], content_hash: 'b'.repeat(64) }] })
      if (url.pathname.endsWith('/finance/strategy-backtests/bt-chart')) {
        return json({
          id: 'bt-chart', strategy_id: 's1', strategy_version_id: 'v1', strategy_content_hash: 'a'.repeat(64),
          fund_code: '110011', fund_name: '测试权益基金', mode: 'fund_only', start_date: '2026-08-07',
          end_date: '2026-08-14', initial_cash: 1_000_000, status: 'completed', result_content_hash: 'c'.repeat(64),
          metrics: { total_return_pct: 0, max_drawdown_pct: 0, trade_count: 0, turnover_pct: 0, final_equity: 1_000_000 },
          result: {
            equity_curve: [
              { date: '2026-08-07', equity: 1_000_000 },
              { date: '2026-08-14', equity: 1_000_000 },
            ],
            trades: [],
            benchmarks: {
              fund_buy_and_hold: {
                status: 'available', return_pct: -0.936708,
                equity_curve: [
                  { date: '2026-08-07', value: 100, return_pct: 0 },
                  { date: '2026-08-14', value: 99.063292, return_pct: -0.936708 },
                ],
              },
              category_index: { status: 'unavailable', reason: 'category_index_code_unavailable' },
            },
            metrics: { total_return_pct: 0, max_drawdown_pct: 0, trade_count: 0, turnover_pct: 0, final_equity: 1_000_000 },
            report_set_ids: ['r1', 'r2'],
            execution_adapter: {
              degraded_estimate: true,
              open_status_validation: 'order_date_and_side',
              open_status_assumptions: [{ date: '2026-08-12', side: 'buy', assumption: 'assumed_open', reason: 'historical_purchase_status_unavailable' }],
              max_position_pct: 60,
              entry_tranche_pct: 20,
              max_entry_tranches: 3,
              minimum_entry_interval_days: 2,
            },
          },
          created_at: '2026-08-15T00:00:00Z', completed_at: '2026-08-15T00:02:00Z',
        })
      }
      const backtestAction = url.pathname.match(/\/finance\/strategy-backtests\/([^/]+)\/(retry|cancel)$/)
      if (backtestAction && init?.method === 'POST') {
        backtestActions.push(`${backtestAction[2]}:${decodeURIComponent(backtestAction[1])}`)
        return json(strategyBacktests.find((row) => row.id === backtestAction[1]) || {})
      }
      if (url.pathname.endsWith('/finance/strategy-backtests') && init?.method === 'POST') {
        backtestCreates.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return json({ id: 'bt-created', status: 'queued' }, 202)
      }
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

  it('launches holdings-portfolio backtesting after inline preflight without free overrides', async () => {
    backtestPreflight = {
      ready: true,
      strategy_content_hash: 'a'.repeat(64),
      signal_count: 1,
      available_report_count: 6,
      trading_day_count: 6,
      missing_report_dates: [],
    }
    window.history.replaceState({}, '', '/strategies?tab=backtests&strategy_id=s1&fund_code=110011&strategy_version_id=v1&mode=holdings_portfolio')
    render(<Strategies />)

    expect(await screen.findByText('基金回测预检')).toBeInTheDocument()
    expect(screen.getByLabelText('策略版本')).toHaveValue('v1')
    expect(screen.getByLabelText('目标基金')).toHaveValue('110011')
    expect(screen.getByLabelText('回测模式')).toHaveValue('holdings_portfolio')
    expect(screen.queryByLabelText('快均线')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('买入阈值')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('股票佣金率')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-08-07' } })
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-08-14' } })
    fireEvent.click(screen.getByRole('button', { name: '检查并启动回测' }))

    await waitFor(() => expect(backtestCreates).toHaveLength(1))
    expect(backtestCreates[0]).toEqual({
      strategy_version_id: 'v1',
      fund_code: '110011',
      mode: 'holdings_portfolio',
      start_date: '2026-08-07',
      end_date: '2026-08-14',
      initial_cash: 1_000_000,
      benchmark: {
        fund_buy_and_hold: false,
        initial_disclosed_weights_static: true,
        hs300: true,
        category_index: false,
      },
      report_policy: 'existing_only',
    })
  })

  it('defaults new bindings to the fund itself', async () => {
    window.history.replaceState({}, '', '/strategies?tab=assignments&strategy_id=s1')
    render(<Strategies />)

    expect(await screen.findByText('创建基金绑定')).toBeInTheDocument()
    expect(screen.getByLabelText('回测模式')).toHaveValue('fund_only')
    expect(screen.getByLabelText('回测模式')).toHaveTextContent('基金本体（推荐）')
  })

  it('scopes history to the strategy and compacts identical fund runs', async () => {
    current = strategyFixture({ assignments: [{ ...assignment, mode: 'fund_only' }] })
    strategyBacktests = [
      {
        id: 'bt-new', strategy_id: 's1', strategy_version_id: 'v1', strategy_content_hash: 'a'.repeat(64),
        input_snapshot_hash: 'execution-snapshot-new', fund_code: '110011', fund_name: '测试权益基金', mode: 'fund_only',
        benchmark: { fund_buy_and_hold: true, category_index: true },
        start_date: '2026-08-07', end_date: '2026-08-14', initial_cash: 1_000_000, status: 'running',
        metrics: {}, error_summary: 'latest-run', created_at: '2026-08-16T00:00:00Z',
      },
      {
        id: 'bt-old', strategy_id: 's1', strategy_version_id: 'v1', strategy_content_hash: 'a'.repeat(64),
        input_snapshot_hash: 'execution-snapshot-old', fund_code: '110011', fund_name: '测试权益基金', mode: 'fund_only',
        benchmark: { category_index: true, fund_buy_and_hold: true },
        start_date: '2026-08-07', end_date: '2026-08-14', initial_cash: 1_000_000, status: 'failed',
        metrics: {}, error_summary: 'older-run', created_at: '2026-08-15T00:00:00Z',
      },
      {
        id: 'bt-holdings', strategy_id: 's1', strategy_version_id: 'v1', strategy_content_hash: 'a'.repeat(64),
        input_snapshot_hash: 'holdings-input', fund_code: '110011', fund_name: '测试权益基金', mode: 'holdings_portfolio',
        start_date: '2026-08-07', end_date: '2026-08-14', initial_cash: 1_000_000, status: 'completed',
        metrics: {}, error_summary: 'advanced-run', created_at: '2026-08-16T00:00:00Z',
      },
    ]
    window.history.replaceState({}, '', '/strategies?tab=backtests&strategy_id=s1&fund_code=110011&strategy_version_id=v1&mode=fund_only')
    render(<Strategies />)

    expect(await screen.findByText(/2 次相同输入/)).toBeInTheDocument()
    expect(screen.getByText(/latest-run/)).toBeInTheDocument()
    expect(screen.queryByText(/older-run/)).not.toBeInTheDocument()
    expect(screen.queryByText(/advanced-run/)).not.toBeInTheDocument()
    const listCall = vi.mocked(fetch).mock.calls.find(([input]) => String(input).includes('/finance/strategy-backtests?'))
    const listUrl = new URL(String(listCall?.[0]))
    expect(listUrl.searchParams.get('strategy_id')).toBe('s1')
    expect(listUrl.searchParams.get('limit')).toBe('200')

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(backtestActions).toContain('cancel:bt-new'))
    expect(screen.getByText(/基于策略最近 200 次原始运行/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开已加载的原始运行（另 1 次）' }))
    expect(await screen.findByText(/older-run/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(backtestActions).toContain('retry:bt-old'))
  })

  it('shows persisted progress on a running report task card', async () => {
    reportJobs = [{
      id: 'job-backfill-1', status: 'running', job_type: 'historical_backfill',
      request: { assignment_id: 'a1', strategy_version_id: 'v1', start_date: '2026-08-07', end_date: '2026-08-14' },
      progress: { total: 6, completed: 2, failed: 1, complete_report_days: 1, partial_report_days: 1, blocked_report_days: 0, current_date: '2026-08-11' },
      pause_requested: false, cancel_requested: false, attempts: [{ attempt: 1 }],
      created_at: '2026-08-14T10:00:00Z', updated_at: '2026-08-14T10:01:00Z',
    }]
    window.history.replaceState({}, '', '/strategies?tab=signals&strategy_id=s1&fund_code=110011&assignment_id=a1')
    render(<Strategies />)

    expect(await screen.findByText('日报生成、补跑与手动重试')).toBeInTheDocument()
    expect(screen.getByText('3/6 · 50%')).toBeInTheDocument()
    expect(screen.getByText('已结算 2 · 完整 1 · 部分 1 · 阻断 0 · 异常 1 · 当前 2026-08-11')).toBeInTheDocument()
    const progress = screen.getByRole('progressbar', { name: '日报任务进度 job-backfill-1' })
    expect(progress).toHaveAttribute('aria-valuenow', '3')
    expect(progress).toHaveAttribute('aria-valuemax', '6')
  })

  it('shows report backfill progress beside the backtest preflight', async () => {
    backtestPreflight = {
      ready: false,
      strategy_content_hash: 'a'.repeat(64),
      signal_count: 6,
      available_report_count: 0,
      trading_day_count: 6,
      missing_report_dates: ['2026-08-07', '2026-08-10', '2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14'],
    }
    window.history.replaceState({}, '', '/strategies?tab=backtests&strategy_id=s1&fund_code=110011&strategy_version_id=v1&mode=holdings_portfolio')
    render(<Strategies />)

    fireEvent.change(await screen.findByLabelText('开始日期'), { target: { value: '2026-08-07' } })
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-08-14' } })
    fireEvent.change(screen.getByLabelText('报告获取策略'), { target: { value: 'require_complete' } })
    fireEvent.click(screen.getByRole('button', { name: '检查并启动回测' }))
    expect(await screen.findByText(/6 个基金信号 · 0\/6 个日报交易日/)).toBeInTheDocument()
    expect(backtestCreates).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '估算补跑成本' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '确认补跑' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '确认补跑' }))

    expect(await screen.findByText('日报补跑 · RUNNING')).toBeInTheDocument()
    expect(screen.getByText('3/6 · 50%')).toBeInTheDocument()
    expect(screen.getByText('已结算 2 · 完整日报 1 · 部分日报 1 · 阻断日报 0 · 执行异常 1 · 当前 2026-08-11')).toBeInTheDocument()
    const backfillRequest = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).endsWith('/finance/report-backfills') && init?.method === 'POST')
    expect(JSON.parse(String(backfillRequest?.[1]?.body))).toEqual(expect.objectContaining({ strategy_version_id: 'v1' }))
    const progress = screen.getByRole('progressbar', { name: '日报补跑进度' })
    expect(progress).toHaveAttribute('aria-valuenow', '3')
    expect(progress).toHaveAttribute('aria-valuemax', '6')
    expect(screen.getByRole('button', { name: '补跑进行中' })).toBeDisabled()
  })

  it('shows the selected immutable version and materializes missing signals from complete reports', async () => {
    const v2 = {
      ...version,
      id: 'v2',
      version_number: 2,
      content_hash: 'c'.repeat(64),
      published_at: '2026-08-14T12:00:00Z',
    }
    current = strategyFixture({
      latest_version: v2,
      versions: [v2, version],
      version_count: 2,
      assignments: [{ ...assignment, pending_version_id: 'v2' }],
    })
    backtestPreflight = {
      ready: false,
      strategy_content_hash: 'c'.repeat(64),
      signal_count: 0,
      available_report_count: 6,
      trading_day_count: 6,
      missing_report_dates: [],
    }
    window.history.replaceState({}, '', '/strategies?tab=backtests&strategy_id=s1&fund_code=110011&strategy_version_id=v2&mode=holdings_portfolio')
    render(<Strategies />)

    fireEvent.change(await screen.findByLabelText('开始日期'), { target: { value: '2026-08-07' } })
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-08-14' } })
    expect(screen.getByText('本次回测固定使用 v2')).toBeInTheDocument()
    expect(screen.getByLabelText('策略版本')).toHaveTextContent('v2 · 最新')
    fireEvent.click(screen.getByRole('button', { name: '检查并启动回测' }))

    expect(await screen.findByText('0 个基金信号 · 6/6 个日报交易日')).toBeInTheDocument()
    expect(backtestCreates).toHaveLength(0)
    expect(screen.getByText(/不会重新运行持仓 UZI 分析/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '生成 v2 信号' }))

    await waitFor(() => {
      const request = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).endsWith('/finance/report-backfills') && init?.method === 'POST')
      expect(JSON.parse(String(request?.[1]?.body))).toEqual(expect.objectContaining({
        assignment_id: 'a1',
        strategy_version_id: 'v2',
        start_date: '2026-08-07',
        end_date: '2026-08-14',
      }))
    })
    expect(await screen.findByText('日报补跑 · RUNNING')).toBeInTheDocument()
  })

  it('makes an older selected version explicit and offers a one-click switch to latest', async () => {
    const v2 = {
      ...version,
      id: 'v2',
      version_number: 2,
      content_hash: 'c'.repeat(64),
      published_at: '2026-08-14T12:00:00Z',
    }
    current = strategyFixture({ latest_version: v2, versions: [v2, version], version_count: 2 })
    window.history.replaceState({}, '', '/strategies?tab=backtests&strategy_id=s1&fund_code=110011&strategy_version_id=v1&mode=holdings_portfolio')
    render(<Strategies />)

    expect(await screen.findByText('本次回测固定使用 v1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '切换到最新版 v2' }))
    expect(screen.getByLabelText('策略版本')).toHaveValue('v2')
    expect(screen.getByText('本次回测固定使用 v2')).toBeInTheDocument()
  })

  it('restores both enabled and paused flags for a disabled assignment', async () => {
    current = strategyFixture({ assignments: [{ ...assignment, enabled: false, paused: true }] })
    window.history.replaceState({}, '', '/strategies?tab=assignments&strategy_id=s1')
    render(<Strategies />)

    fireEvent.click(await screen.findByRole('button', { name: '恢复' }))

    await waitFor(() => expect(assignmentUpdates).toContainEqual({ enabled: true, paused: false }))
  })

  it('loads and displays a completed fund backtest chart with benchmark and audit details', async () => {
    current = strategyFixture({ assignments: [{ ...assignment, mode: 'fund_only' }] })
    strategyBacktests = [{
      id: 'bt-chart', strategy_id: 's1', strategy_version_id: 'v1', strategy_content_hash: 'a'.repeat(64),
      fund_code: '110011', fund_name: '测试权益基金', mode: 'fund_only', start_date: '2026-08-07',
      end_date: '2026-08-14', initial_cash: 1_000_000, status: 'completed',
      metrics: { total_return_pct: 0 }, created_at: '2026-08-15T00:00:00Z',
    }]
    window.history.replaceState({}, '', '/strategies?tab=backtests&strategy_id=s1&fund_code=110011&strategy_version_id=v1&mode=fund_only')
    render(<Strategies />)

    fireEvent.click(await screen.findByRole('button', { name: '查看图表' }))

    expect(await screen.findByText('累计收益曲线')).toBeInTheDocument()
    expect(screen.getByText('策略收益')).toBeInTheDocument()
    expect(screen.getAllByText('基金买入持有').length).toBeGreaterThan(0)
    expect(screen.getByText('本次没有形成成交')).toBeInTheDocument()
    expect(screen.getByText('2 份日报进入本次计算')).toBeInTheDocument()
    expect(screen.getByText('历史开放状态缺失，按开放估算 1 次：2026-08-12 申购')).toBeInTheDocument()
    expect(screen.getByText('基金建仓规则：仓位上限 60% · 单批 20% · 最多 3 次 · 间隔 2 个估值日')).toBeInTheDocument()
    expect(screen.getByText('不可用 · category_index_code_unavailable')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /策略累计收益 0.00%，基金买入持有 -0.94%/ })).toBeInTheDocument()

    fireEvent.click(screen.getByText('查看逐日数据'))
    expect(screen.getByRole('cell', { name: '2026-08-14' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '-0.94%' })).toBeInTheDocument()
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).endsWith('/finance/strategy-backtests/bt-chart'))).toBe(true)
  })

  it('saves a server draft with optimistic revision and then reviews publication', async () => {
    render(<Strategies />)
    const name = await screen.findByLabelText('策略名称')
    fireEvent.change(screen.getByLabelText('持仓分析强度'), { target: { value: 'lite' } })
    fireEvent.change(screen.getByLabelText('基金最大仓位 (%)'), { target: { value: '60' } })
    fireEvent.change(screen.getByLabelText('单次建仓比例 (%)'), { target: { value: '20' } })
    fireEvent.change(screen.getByLabelText('最大建仓次数'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('建仓间隔估值日'), { target: { value: '2' } })
    fireEvent.change(name, { target: { value: '修改后的策略' } })
    fireEvent.click(await screen.findByRole('button', { name: '保存草稿' }))

    await waitFor(() => {
      const patchCall = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input).includes('/finance/strategies/s1/draft') && init?.method === 'PATCH')
      expect(new Headers(patchCall?.[1]?.headers).get('If-Match')).toBe('W/"1"')
      const body = JSON.parse(String(patchCall?.[1]?.body))
      expect(body).toEqual(expect.objectContaining({ expected_revision: 1, name: '修改后的策略' }))
      expect(body.document.report_inputs.research_profile).toBe('lite')
      expect(body.document.asset_execution.otc_fund).toEqual(expect.objectContaining({
        max_position_pct: 60,
        entry_tranche_pct: 20,
        max_entry_tranches: 3,
        minimum_entry_interval_days: 2,
      }))
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
