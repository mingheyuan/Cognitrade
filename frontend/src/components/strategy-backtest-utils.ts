import type { BacktestMode, StrategyBacktest } from '../strategy-contracts'

export function backtestModeLabel(value: BacktestMode): string {
  return value === 'fund_only' ? '基金本体' : '持仓组合'
}

export function backtestMetricSummary(metrics: Record<string, number>): string {
  if (!Object.keys(metrics || {}).length) return '等待结果'
  return `收益 ${Number(metrics.total_return_pct || 0).toFixed(2)}% · 回撤 ${Number(metrics.max_drawdown_pct || 0).toFixed(2)}% · Sharpe ${Number(metrics.sharpe || 0).toFixed(2)}`
}

export function compactBacktestRuns(runs: StrategyBacktest[]): Array<{ run: StrategyBacktest; count: number }> {
  const groups = new Map<string, { run: StrategyBacktest; count: number }>()
  const ordered = [...runs].sort((left, right) => right.created_at.localeCompare(left.created_at))
  for (const run of ordered) {
    const key = [
      run.strategy_version_id,
      run.fund_code,
      run.mode,
      run.start_date,
      run.end_date,
      run.initial_cash,
      run.report_policy || 'existing_only',
      stableObjectKey(run.benchmark),
    ].join('|')
    const current = groups.get(key)
    if (current) current.count += 1
    else groups.set(key, { run, count: 1 })
  }
  return [...groups.values()]
}

function stableObjectKey(value: Record<string, unknown> | undefined): string {
  if (!value) return ''
  return Object.entries(value)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${key}:${String(item)}`)
    .join(',')
}
