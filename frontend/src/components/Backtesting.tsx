import {
  Activity,
  AlertCircle,
  BarChart3,
  Database,
  FlaskConical,
  Gauge,
  History,
  Play,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from 'lucide-react'
import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { CognitradeApiClient, CognitradeApiError } from '../api-client'
import { BusinessDataView } from '../artifacts/BusinessDataView'
import { useCognitrade } from '../CognitradeContext'
import type {
  BacktestResult,
  BacktestStrategy,
  BacktestSummary,
  BacktestTarget,
  QuantEngineStatus,
} from '../finance-contracts'
import { errorMessageWithRequestId, localizeErrorMessage } from '../utils/error-message'

type BacktestingProps = {
  initialTarget?: BacktestTarget | null
}

export function Backtesting({ initialTarget = null }: BacktestingProps) {
  const { settings } = useCognitrade()
  const client = useMemo(() => new CognitradeApiClient(settings), [settings])
  const [engine, setEngine] = useState<QuantEngineStatus | null>(null)
  const [history, setHistory] = useState<BacktestSummary[]>([])
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [symbol, setSymbol] = useState(initialTarget?.symbol || '600519')
  const [strategyId, setStrategyId] = useState('')
  const [strategyParameters, setStrategyParameters] = useState<Record<string, number>>({})
  const [initialCash, setInitialCash] = useState(1_000_000)
  const [tradeSize, setTradeSize] = useState(1000)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')

  const loadOverview = async () => {
    setLoading(true)
    setError('')
    try {
      const [enginePayload, historyPayload] = await Promise.all([
        client.getBacktestEngine(),
        loadAllBacktests(client),
      ])
      setEngine(enginePayload)
      setHistory(historyPayload)
    } catch (loadError) {
      setError(apiErrorMessage(loadError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let disposed = false
    queueMicrotask(() => {
      if (!disposed) void loadOverview()
    })
    return () => { disposed = true }
    // client identity is fully captured by its memoized instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client])

  const effectiveStrategyId = strategyId || engine?.strategies?.[0]?.id || ''
  const selectedStrategy = useMemo(
    () => engine?.strategies?.find((strategy) => strategy.id === effectiveStrategyId) || null,
    [effectiveStrategyId, engine?.strategies],
  )
  const effectiveStrategyParameters = useMemo(
    () => ({ ...(selectedStrategy ? strategyDefaults(selectedStrategy) : {}), ...strategyParameters }),
    [selectedStrategy, strategyParameters],
  )
  const strategyParameterError = validateStrategyParameters(selectedStrategy, effectiveStrategyParameters)

  const selectStrategy = (nextId: string) => {
    const strategy = engine?.strategies?.find((row) => row.id === nextId)
    setStrategyId(nextId)
    setStrategyParameters(strategy ? strategyDefaults(strategy) : {})
  }

  const runBacktest = async (event: FormEvent) => {
    event.preventDefault()
    setRunning(true)
    setError('')
    try {
      const payload = await client.runBacktest({
        symbol: symbol.trim(),
        strategy: effectiveStrategyId,
        strategy_parameters: effectiveStrategyParameters,
        initial_cash: initialCash,
        trade_size: tradeSize,
      })
      setResult(payload)
      setSymbol(payload.symbol)
      setHistory(await loadAllBacktests(client))
    } catch (runError) {
      setError(apiErrorMessage(runError))
    } finally {
      setRunning(false)
    }
  }

  const openResult = async (backtestId: string) => {
    setRunning(true)
    setError('')
    try {
      setResult(await client.getBacktest(backtestId))
    } catch (loadError) {
      setError(apiErrorMessage(loadError))
    } finally {
      setRunning(false)
    }
  }

  return (
    <main className="finance-page backtest-page">
      <section className="finance-hero backtest-hero">
        <div>
          <span className="section-kicker">HIKYUU · ISOLATED BACKTEST LAB</span>
          <h1>策略回测</h1>
          <p>使用 Finance Pack 历史 K 线驱动 Hikyuu 原生交易系统，回测结果独立保存，不影响模拟盘现金和持仓。</p>
          {initialTarget && <small className="backtest-linked-source">来自 {initialTarget.fundName || initialTarget.fundCode || '基金持仓'} · {initialTarget.name || initialTarget.symbol}；仅复用股票代码，不把研究评分转换为交易信号。</small>}
        </div>
        <button className="secondary-button" onClick={() => void loadOverview()} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'is-spinning' : ''} />刷新记录
        </button>
      </section>

      {error && (
        <div className="finance-alert" role="alert">
          <AlertCircle size={17} /><span>{error}</span><button onClick={() => setError('')}>关闭</button>
        </div>
      )}

      <section className="backtest-engine-strip">
        <span className={`backtest-engine-state ${engine?.available ? 'is-ready' : 'is-missing'}`}>
          <FlaskConical size={18} />
        </span>
        <div>
          <small>回测引擎</small>
          <strong>{engine?.available ? `Hikyuu ${engine.version || ''}` : 'Hikyuu 未安装'}</strong>
        </div>
        <div>
          <small>数据输入</small>
          <strong>{engine?.data_source || 'Finance Pack K-line snapshot'}</strong>
        </div>
        <div>
          <small>运行隔离</small>
          <strong>独立结果文件 · 不触碰模拟账户</strong>
        </div>
      </section>
      {engine && <BacktestEngineDisclosure engine={engine} />}

      <div className="backtest-workspace">
        <form className="backtest-config-card" onSubmit={runBacktest}>
          <header>
            <div><span className="section-kicker">STRATEGY CONFIG</span><h2>{selectedStrategy?.name || '选择回测策略'}</h2></div>
            <span className="quality-pill quality-pill--available">{effectiveStrategyId || 'LOADING'}</span>
          </header>
          <label><span>股票代码或名称</span><input value={symbol} onChange={(event) => setSymbol(event.target.value)} required /></label>
          <label><span>策略</span><select aria-label="回测策略" value={effectiveStrategyId} onChange={(event) => selectStrategy(event.target.value)} required>{engine?.strategies?.map((strategy) => <option key={strategy.id} value={strategy.id}>{strategy.name}</option>)}</select><small>{selectedStrategy?.description}</small></label>
          <div className="backtest-form-grid">{selectedStrategy?.parameters?.map((parameter) => <label key={parameter.id}><span>{parameter.label}</span><input type="number" min={parameter.minimum} max={parameter.maximum} step={parameter.step} value={effectiveStrategyParameters[parameter.id] ?? parameter.default} onChange={(event) => setStrategyParameters((current) => ({ ...current, [parameter.id]: Number(event.target.value) }))} required />{parameter.help && <small>{parameter.help}</small>}</label>)}</div>
          <label><span>初始资金</span><input type="number" min="10000" step="10000" value={initialCash} onChange={(event) => setInitialCash(Number(event.target.value))} required /></label>
          <label><span>每次交易数量</span><div className="quantity-input-row"><input type="number" min="100" step="100" value={tradeSize} onChange={(event) => setTradeSize(Number(event.target.value))} required /><span>股</span></div></label>
          <div className="backtest-rule-note">
            <Database size={15} />
            <span>采用当前行情模块返回的完整日 K；佣金万 2.5（最低 5 元）、印花税万 5、过户费万 0.1。</span>
          </div>
          {strategyParameterError && <div className="backtest-parameter-error"><AlertCircle size={13} />{strategyParameterError}</div>}
          <button className="primary-button backtest-run-button" disabled={running || !engine?.available || !effectiveStrategyId || Boolean(strategyParameterError)}>
            {running ? <RefreshCw className="is-spinning" size={16} /> : <Play size={16} />}
            {running ? 'Hikyuu 正在计算' : '开始回测'}
          </button>
        </form>

        <section className="backtest-result-card">
          {!result && (
            <div className="backtest-empty">
              <BarChart3 size={34} />
              <h2>等待一次真实回测</h2>
              <p>设置股票和均线周期后运行，资产曲线、最大回撤与成交明细会显示在这里。</p>
            </div>
          )}
          {result && <BacktestResultPanel result={result} />}
        </section>
      </div>

      <section className="backtest-history-card">
        <header><div><span className="section-kicker">PERSISTED RUNS</span><h2>历史回测</h2></div><History size={18} /></header>
        <div className="backtest-history-list">
          {history.map((item) => (
            <button key={item.backtest_id} onClick={() => void openResult(item.backtest_id)}>
              <span><strong>{item.name || item.symbol}</strong><small>{item.symbol} · {item.strategy?.name || item.strategy?.id || '策略未记录'}{backtestParameterSummary(item.parameters)}</small></span>
              <span><strong className={item.metrics.total_return_pct >= 0 ? 'number-up' : 'number-down'}>{formatPercent(item.metrics.total_return_pct)}</strong><small>{item.period.start} → {item.period.end}</small></span>
            </button>
          ))}
          {!history.length && !loading && <div className="backtest-history-empty">尚无已保存的回测结果</div>}
        </div>
        {history.length > 0 && <footer className="backtest-history-footer">已载入全部 {history.length} 条持久化回测记录</footer>}
      </section>
    </main>
  )
}

export function BacktestEngineDisclosure({ engine }: { engine: QuantEngineStatus }) {
  return (
    <section className="backtest-engine-disclosure" aria-label="完整回测引擎能力">
      <header><div><span className="section-kicker">ENGINE CAPABILITIES</span><h2>回测引擎能力</h2></div><span className={engine.available ? 'is-ready' : 'is-missing'}>{engine.available ? '可用' : '不可用'}</span></header>
      <dl><div><dt>引擎</dt><dd>{engine.engine}</dd></div><div><dt>版本</dt><dd>{engine.version || '未提供'}</dd></div><div><dt>运行模式</dt><dd>{engine.mode === 'isolated_external_kdata' ? '隔离进程 · 外部 K 线输入' : engine.mode}</dd></div><div><dt>数据输入</dt><dd>{engine.data_source || '未提供'}</dd></div></dl>
      <div className="backtest-engine-strategies"><h3>可用策略</h3>{engine.strategies?.length ? <ul>{engine.strategies.map((strategy) => <li key={strategy.id}><strong>{strategy.name}</strong><small>{strategy.id}</small><p>{strategy.description}</p><em>{strategy.parameters?.map((parameter) => parameter.label).join(' · ') || '无额外参数'}</em></li>)}</ul> : <p>后端未声明可用策略。</p>}</div>
      {engine.load_error && <p className="paper-engine-warning"><AlertCircle size={13} />回测依赖未完整加载，请检查服务端量化依赖。</p>}
    </section>
  )
}

export function BacktestResultPanel({ result }: { result: BacktestResult }) {
  const positive = result.metrics.total_return_pct >= 0
  return (
    <>
      <header className="backtest-result-heading">
        <div><span className="section-kicker">{result.symbol} · {result.engine.name.toUpperCase()} {result.engine.version}</span><h2>{result.name} 回测结果</h2><p>{result.period.start} 至 {result.period.end} · {result.period.bar_count} 根 {frequencyLabel(result.period.frequency)}</p><small className="backtest-result-id">{result.backtest_id} · {formatDateTime(result.created_at)}</small></div>
        <span className={`backtest-return-badge ${positive ? 'is-positive' : 'is-negative'}`}>{formatPercent(result.metrics.total_return_pct)}</span>
      </header>
      <div className="backtest-metrics-grid">
        <ResultMetric icon={<Database size={16} />} label="初始资金" value={formatMoney(result.metrics.initial_cash)} />
        <ResultMetric icon={<TrendingUp size={16} />} label="期末权益" value={formatMoney(result.metrics.final_equity)} />
        <ResultMetric icon={<TrendingUp size={16} />} label="累计收益" value={formatPercent(result.metrics.total_return_pct)} tone={result.metrics.total_return_pct >= 0 ? 'up' : 'down'} />
        <ResultMetric icon={<Activity size={16} />} label="年化收益" value={formatPercent(result.metrics.annualized_return_pct)} tone={result.metrics.annualized_return_pct >= 0 ? 'up' : 'down'} />
        <ResultMetric icon={<TrendingDown size={16} />} label="最大回撤" value={formatPercent(result.metrics.max_drawdown_pct)} tone="down" />
        <ResultMetric icon={<Gauge size={16} />} label="已平仓交易" value={`${result.metrics.closed_trade_count} 笔`} />
        <ResultMetric icon={<Gauge size={16} />} label="盈利交易比例" value={formatPercent(result.metrics.winning_trade_rate_pct)} />
        <ResultMetric icon={<Gauge size={16} />} label="平均盈亏比" value={formatRatio(result.metrics.profit_loss_ratio)} />
      </div>
      <EquityCurve points={result.equity_curve} initialCash={result.metrics.initial_cash} />
      <section className="backtest-contract-grid" aria-label="完整回测配置与数据说明">
        <article><h3>策略与费用参数</h3><dl><div><dt>策略</dt><dd>{result.strategy.name}（{result.strategy.id}）</dd></div>{strategyResultParameters(result.parameters).map(([key, value]) => <div key={key}><dt>{strategyParameterLabel(key)}</dt><dd>{String(value)}</dd></div>)}<div><dt>每次交易数量</dt><dd>{Number(result.parameters.trade_size || 0).toLocaleString()} 股</dd></div><div><dt>佣金率</dt><dd>{formatRate(result.parameters.commission_rate)}</dd></div><div><dt>最低佣金</dt><dd>{formatMoney(Number(result.parameters.minimum_commission || 0))}</dd></div><div><dt>印花税率</dt><dd>{formatRate(result.parameters.stamp_duty_rate)}</dd></div><div><dt>过户费率</dt><dd>{formatRate(result.parameters.transfer_fee_rate)}</dd></div></dl></article>
        <article><h3>数据与运行范围</h3><dl><div><dt>数据质量</dt><dd>{qualityLabel(result.data_quality_status)}</dd></div><div><dt>数据频率</dt><dd>{frequencyLabel(result.period.frequency)}</dd></div><div><dt>K 线数量</dt><dd>{result.period.bar_count.toLocaleString()} 根</dd></div><div><dt>开始日期</dt><dd>{result.period.start}</dd></div><div><dt>结束日期</dt><dd>{result.period.end}</dd></div><div><dt>回测引擎</dt><dd>{result.engine.name} {result.engine.version}</dd></div></dl>{result.data_source && <div className="backtest-source-disclosure"><h4>行情来源</h4><BusinessDataView value={result.data_source} fieldKey="source" path="backtest.data_source" compact /></div>}</article>
      </section>
      <div className="backtest-trade-table-wrap">
        <h3>全部成交记录 <small>{result.trades.length} 笔</small></h3>
        <table className="backtest-trade-table">
          <thead><tr><th>日期</th><th>方向</th><th>价格</th><th>数量</th><th>费用</th><th>成交后现金</th></tr></thead>
          <tbody>
            {result.trades.map((trade, index) => (
              <tr key={`${trade.date}-${trade.side}-${index}`}>
                <td>{trade.date}</td>
                <td><span className={`trade-side-badge trade-side-badge--${trade.side}`}>{trade.side === 'buy' ? '买入' : '卖出'}</span></td>
                <td>{formatMoney(trade.price)}</td>
                <td>{trade.quantity.toLocaleString('zh-CN')}</td>
                <td>{formatMoney(trade.fees)}</td>
                <td>{formatMoney(trade.cash_after)}</td>
              </tr>
            ))}
            {!result.trades.length && <tr><td colSpan={6} className="table-empty-cell">当前参数没有触发成交</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  )
}

async function loadAllBacktests(client: CognitradeApiClient): Promise<BacktestSummary[]> {
  const pageSize = 100
  const rows: BacktestSummary[] = []
  let offset = 0
  let total = Number.POSITIVE_INFINITY
  while (offset < total) {
    const page = await client.listBacktests(pageSize, offset)
    total = page.total_count
    if (!page.results.length) break
    rows.push(...page.results)
    offset += page.results.length
  }
  return Array.from(new Map(rows.map((item) => [item.backtest_id, item])).values())
}

function ResultMetric({ icon, label, value, tone }: { icon: React.ReactNode; label: string; value: string; tone?: 'up' | 'down' }) {
  return <div className="backtest-result-metric"><span>{icon}</span><div><small>{label}</small><strong className={tone ? `number-${tone}` : ''}>{value}</strong></div></div>
}

function EquityCurve({ points, initialCash }: { points: BacktestResult['equity_curve']; initialCash: number }) {
  if (points.length < 2) return null
  const width = 900
  const height = 250
  const padding = 22
  const values = points.map((point) => point.equity)
  const minimum = Math.min(...values, initialCash)
  const maximum = Math.max(...values, initialCash)
  const range = Math.max(maximum - minimum, 1)
  const path = points.map((point, index) => {
    const x = padding + (index / (points.length - 1)) * (width - padding * 2)
    const y = padding + ((maximum - point.equity) / range) * (height - padding * 2)
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
  const baselineY = padding + ((maximum - initialCash) / range) * (height - padding * 2)
  return (
    <div className="backtest-equity-chart">
      <div><span>账户权益曲线</span><small>{points[0].date} — {points[points.length - 1]?.date}</small></div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="回测账户权益曲线">
        <defs>
          <linearGradient id="backtest-equity-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--mint)" stopOpacity=".26" />
            <stop offset="100%" stopColor="var(--mint)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1={padding} x2={width - padding} y1={baselineY} y2={baselineY} className="backtest-chart-baseline" />
        <path d={`${path} L${width - padding},${height - padding} L${padding},${height - padding} Z`} className="backtest-chart-area" />
        <path d={path} className="backtest-chart-line" />
      </svg>
    </div>
  )
}

function formatMoney(value: number): string {
  return `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatPercent(value: number): string {
  return `${value > 0 ? '+' : ''}${Number(value || 0).toFixed(2)}%`
}

function formatRate(value: unknown): string {
  const rate = Number(value)
  return Number.isFinite(rate) ? `${(rate * 100).toFixed(3)}%` : '未提供'
}

function formatRatio(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '—'
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function frequencyLabel(value: string): string {
  return ({ DAY: '日 K', WEEK: '周 K', MONTH: '月 K' } as Record<string, string>)[value] || value
}

function qualityLabel(value?: string): string {
  return ({ available: '数据可用', degraded: '部分数据降级', data_gap: '数据不足' } as Record<string, string>)[String(value || '')] || value || '未提供'
}

function strategyDefaults(strategy: BacktestStrategy): Record<string, number> {
  return Object.fromEntries((strategy.parameters || []).map((parameter) => [parameter.id, parameter.default]))
}

function validateStrategyParameters(strategy: BacktestStrategy | null, values: Record<string, number>): string {
  if (!strategy) return ''
  for (const parameter of strategy.parameters || []) {
    const value = values[parameter.id]
    if (!Number.isFinite(value) || value < parameter.minimum || value > parameter.maximum) {
      return `${parameter.label}必须在 ${parameter.minimum} 到 ${parameter.maximum} 之间`
    }
    if (parameter.type === 'integer' && !Number.isInteger(value)) return `${parameter.label}必须是整数`
  }
  if (strategy.id === 'ma_cross' && values.slow_period <= values.fast_period) return '长周期必须大于短周期'
  return ''
}

const COMMON_BACKTEST_PARAMETERS = new Set([
  'initial_cash',
  'trade_size',
  'commission_rate',
  'minimum_commission',
  'stamp_duty_rate',
  'transfer_fee_rate',
])

function strategyResultParameters(parameters: BacktestResult['parameters']): Array<[string, number]> {
  return Object.entries(parameters).filter(([key]) => !COMMON_BACKTEST_PARAMETERS.has(key))
}

function strategyParameterLabel(key: string): string {
  return ({ fast_period: '短周期', slow_period: '长周期', lookback_period: '均线周期' } as Record<string, string>)[key] || key
}

function backtestParameterSummary(parameters: BacktestResult['parameters']): string {
  const values = strategyResultParameters(parameters)
    .map(([key, value]) => `${strategyParameterLabel(key)} ${value}`)
    .join(' / ')
  return values ? ` · ${values}` : ''
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof CognitradeApiError) return errorMessageWithRequestId(error.message, error.requestId)
  return localizeErrorMessage(error)
}
