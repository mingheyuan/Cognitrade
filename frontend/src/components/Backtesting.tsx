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
import { useCognitrade } from '../CognitradeContext'
import type {
  BacktestResult,
  BacktestSummary,
  QuantEngineStatus,
} from '../finance-contracts'

export function Backtesting() {
  const { settings } = useCognitrade()
  const client = useMemo(() => new CognitradeApiClient(settings), [settings])
  const [engine, setEngine] = useState<QuantEngineStatus | null>(null)
  const [history, setHistory] = useState<BacktestSummary[]>([])
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [symbol, setSymbol] = useState('600519')
  const [initialCash, setInitialCash] = useState(1_000_000)
  const [fastPeriod, setFastPeriod] = useState(5)
  const [slowPeriod, setSlowPeriod] = useState(20)
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
        client.listBacktests(12),
      ])
      setEngine(enginePayload)
      setHistory(historyPayload.results)
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

  const runBacktest = async (event: FormEvent) => {
    event.preventDefault()
    setRunning(true)
    setError('')
    try {
      const payload = await client.runBacktest({
        symbol: symbol.trim(),
        strategy: 'ma_cross',
        initial_cash: initialCash,
        fast_period: fastPeriod,
        slow_period: slowPeriod,
        trade_size: tradeSize,
      })
      setResult(payload)
      setSymbol(payload.symbol)
      const historyPayload = await client.listBacktests(12)
      setHistory(historyPayload.results)
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

      <div className="backtest-workspace">
        <form className="backtest-config-card" onSubmit={runBacktest}>
          <header>
            <div><span className="section-kicker">STRATEGY CONFIG</span><h2>双均线策略</h2></div>
            <span className="quality-pill quality-pill--available">MA CROSS</span>
          </header>
          <label><span>股票代码或名称</span><input value={symbol} onChange={(event) => setSymbol(event.target.value)} required /></label>
          <div className="backtest-form-grid">
            <label><span>短周期</span><input type="number" min="2" max="120" value={fastPeriod} onChange={(event) => setFastPeriod(Number(event.target.value))} required /></label>
            <label><span>长周期</span><input type="number" min="3" max="250" value={slowPeriod} onChange={(event) => setSlowPeriod(Number(event.target.value))} required /></label>
          </div>
          <label><span>初始资金</span><input type="number" min="10000" step="10000" value={initialCash} onChange={(event) => setInitialCash(Number(event.target.value))} required /></label>
          <label><span>每次交易数量</span><div className="quantity-input-row"><input type="number" min="100" step="100" value={tradeSize} onChange={(event) => setTradeSize(Number(event.target.value))} required /><span>股</span></div></label>
          <div className="backtest-rule-note">
            <Database size={15} />
            <span>采用当前行情模块返回的完整日 K；佣金万 2.5（最低 5 元）、印花税万 5、过户费万 0.1。</span>
          </div>
          <button className="primary-button backtest-run-button" disabled={running || !engine?.available || slowPeriod <= fastPeriod}>
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
              <span><strong>{item.name || item.symbol}</strong><small>{item.symbol} · MA {String(item.parameters.fast_period)}/{String(item.parameters.slow_period)}</small></span>
              <span><strong className={item.metrics.total_return_pct >= 0 ? 'number-up' : 'number-down'}>{formatPercent(item.metrics.total_return_pct)}</strong><small>{item.period.start} → {item.period.end}</small></span>
            </button>
          ))}
          {!history.length && !loading && <div className="backtest-history-empty">尚无已保存的回测结果</div>}
        </div>
      </section>
    </main>
  )
}

function BacktestResultPanel({ result }: { result: BacktestResult }) {
  const positive = result.metrics.total_return_pct >= 0
  return (
    <>
      <header className="backtest-result-heading">
        <div><span className="section-kicker">{result.symbol} · {result.engine.name.toUpperCase()}</span><h2>{result.name} 回测结果</h2><p>{result.period.start} 至 {result.period.end} · {result.period.bar_count} 根日 K</p></div>
        <span className={`backtest-return-badge ${positive ? 'is-positive' : 'is-negative'}`}>{formatPercent(result.metrics.total_return_pct)}</span>
      </header>
      <div className="backtest-metrics-grid">
        <ResultMetric icon={<TrendingUp size={16} />} label="期末权益" value={formatMoney(result.metrics.final_equity)} />
        <ResultMetric icon={<Activity size={16} />} label="年化收益" value={formatPercent(result.metrics.annualized_return_pct)} tone={result.metrics.annualized_return_pct >= 0 ? 'up' : 'down'} />
        <ResultMetric icon={<TrendingDown size={16} />} label="最大回撤" value={formatPercent(result.metrics.max_drawdown_pct)} tone="down" />
        <ResultMetric icon={<Gauge size={16} />} label="已平仓交易" value={`${result.metrics.closed_trade_count} 笔`} />
      </div>
      <EquityCurve points={result.equity_curve} initialCash={result.metrics.initial_cash} />
      <div className="backtest-trade-table-wrap">
        <h3>成交记录</h3>
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

function apiErrorMessage(error: unknown): string {
  if (error instanceof CognitradeApiError) return `${error.message}${error.requestId ? ` · request ${error.requestId}` : ''}`
  return error instanceof Error ? error.message : String(error)
}
