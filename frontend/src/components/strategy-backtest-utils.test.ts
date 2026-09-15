import { describe, expect, it } from 'vitest'
import type { StrategyBacktest } from '../strategy-contracts'
import { compactBacktestRuns } from './strategy-backtest-utils'

function run(overrides: Partial<StrategyBacktest> = {}): StrategyBacktest {
  return {
    id: 'run-1',
    strategy_id: 'strategy-1',
    strategy_version_id: 'version-1',
    strategy_content_hash: 'a'.repeat(64),
    fund_code: '110011',
    mode: 'fund_only',
    start_date: '2026-08-07',
    end_date: '2026-08-14',
    initial_cash: 1_000_000,
    benchmark: { fund_buy_and_hold: true, category_index: true },
    report_policy: 'existing_only',
    status: 'failed',
    metrics: {},
    created_at: '2026-08-15T00:00:00Z',
    ...overrides,
  }
}

describe('compactBacktestRuns', () => {
  it('groups retries by logical request and surfaces the latest failure', () => {
    const groups = compactBacktestRuns([
      run({ id: 'failed-before', input_snapshot_hash: 'execution-a' }),
      run({ id: 'completed', input_snapshot_hash: 'execution-b', status: 'completed', created_at: '2026-08-16T00:00:00Z' }),
      run({ id: 'failed-after', input_snapshot_hash: 'execution-c', created_at: '2026-08-17T00:00:00Z' }),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0]).toMatchObject({ count: 3, run: { id: 'failed-after' } })
  })

  it('does not let an older active attempt replace the latest terminal result', () => {
    const groups = compactBacktestRuns([
      run({ id: 'running', status: 'running', created_at: '2026-08-15T00:00:00Z' }),
      run({ id: 'completed', status: 'completed', created_at: '2026-08-16T00:00:00Z' }),
    ])

    expect(groups[0]).toMatchObject({ count: 2, run: { id: 'completed' } })
  })

  it('normalizes benchmark key order but keeps report policies separate', () => {
    const groups = compactBacktestRuns([
      run({ id: 'existing-a', input_snapshot_hash: 'execution-a' }),
      run({ id: 'existing-b', input_snapshot_hash: 'execution-b', benchmark: { category_index: true, fund_buy_and_hold: true } }),
      run({ id: 'strict', report_policy: 'require_complete' }),
    ])

    expect(groups).toHaveLength(2)
    expect(groups.map((group) => group.count).sort()).toEqual([1, 2])
  })
})
