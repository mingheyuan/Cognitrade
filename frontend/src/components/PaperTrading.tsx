import {
  AlertCircle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Clock3,
  Cpu,
  Info,
  Landmark,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Trash2,
  WalletCards,
  X,
} from 'lucide-react'
import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { CognitradeApiClient, CognitradeApiError } from '../api-client'
import { useCognitrade } from '../CognitradeContext'
import type { MarketQuote, PaperOrder, PaperPortfolio, QuantEngineStatus } from '../finance-contracts'
import { estimateOrder } from '../utils/paper-order'

export function PaperTrading({ onOpenMarketData }: { onOpenMarketData: () => void }) {
  const { settings } = useCognitrade()
  const client = useMemo(() => new CognitradeApiClient(settings), [settings])
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null)
  const [orders, setOrders] = useState<PaperOrder[]>([])
  const [symbol, setSymbol] = useState('600519')
  const [side, setSide] = useState<'buy' | 'sell'>('buy')
  const [quantity, setQuantity] = useState(100)
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market')
  const [limitPrice, setLimitPrice] = useState(0)
  const [quote, setQuote] = useState<MarketQuote | null>(null)
  const [engine, setEngine] = useState<QuantEngineStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [quoteLoading, setQuoteLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [resetOpen, setResetOpen] = useState(false)
  const [initialCash, setInitialCash] = useState(1_000_000)

  const refresh = async (quiet = false) => {
    if (!quiet) setLoading(true)
    setError('')
    try {
      const [portfolioPayload, ordersPayload, enginePayload] = await Promise.all([
        client.getPaperPortfolio(),
        client.listPaperOrders(80),
        client.getPaperEngine(),
      ])
      setPortfolio(portfolioPayload)
      setOrders(ordersPayload.orders)
      setEngine(enginePayload)
    } catch (loadError) {
      setError(apiErrorMessage(loadError))
    } finally {
      if (!quiet) setLoading(false)
    }
  }

  useEffect(() => {
    let disposed = false
    queueMicrotask(() => {
      if (!disposed) void refresh()
    })
    return () => { disposed = true }
    // client identity is fully captured by its memoized instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client])

  const loadQuote = async (requestedSymbol = symbol, force = false) => {
    const normalized = requestedSymbol.trim()
    if (!normalized) return
    setQuoteLoading(true)
    setError('')
    try {
      const result = await client.getMarketQuote(normalized, force)
      setQuote(result)
      setSymbol(result.symbol)
      setLimitPrice((current) => current > 0 ? current : result.latest_price)
    } catch (quoteError) {
      setQuote(null)
      setError(apiErrorMessage(quoteError))
    } finally {
      setQuoteLoading(false)
    }
  }

  const submitOrder = async (event: FormEvent) => {
    event.preventDefault()
    if (!symbol.trim() || quantity <= 0) return
    setSubmitting(true)
    setError('')
    setSuccess('')
    try {
      const result = await client.placePaperOrder({
        symbol: symbol.trim(),
        side,
        quantity,
        client_order_id: crypto.randomUUID(),
        order_type: orderType,
        ...(orderType === 'limit' ? { limit_price: limitPrice } : {}),
      })
      if (result.status === 'pending') {
        setSuccess(`${result.name} ${result.side === 'buy' ? '买入' : '卖出'} ${result.quantity} 股限价委托已进入 VeighNa 撮合队列，等待行情触价。`)
      } else {
        const cashAction = result.side === 'buy' ? '实际占用资金' : '实际回笼资金'
        const pricingLabel = result.pricing_mode === 'closing_price' ? '15:00 收盘价' : '盘中行情'
        setSuccess(`${result.name} ${result.side === 'buy' ? '买入' : '卖出'} ${result.quantity} 股已按${pricingLabel}模拟成交，成交价 ${formatMoney(result.fill_price)}；${cashAction} ${formatMoney(Math.abs(result.cash_effect))}，其中费用 ${formatMoney(result.fees.total)}。`)
      }
      await Promise.all([refresh(true), loadQuote(result.symbol)])
    } catch (orderError) {
      setError(apiErrorMessage(orderError))
    } finally {
      setSubmitting(false)
    }
  }

  const resetAccount = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      await client.resetPaperAccount(initialCash)
      setResetOpen(false)
      setQuote(null)
      setSuccess(`模拟账户已重置，初始资金 ${formatMoney(initialCash)}。历史账户已归档。`)
      await refresh(true)
    } catch (resetError) {
      setError(apiErrorMessage(resetError))
    } finally {
      setSubmitting(false)
    }
  }

  const matchOpenOrders = async () => {
    setSubmitting(true)
    setError('')
    try {
      const payload = await client.matchPaperOrders(true)
      setSuccess(payload.matched_count > 0
        ? `VeighNa 已按最新行情撮合 ${payload.matched_count} 笔委托，仍有 ${payload.pending_count} 笔等待触价。`
        : `已检查最新行情，${payload.pending_count} 笔限价委托尚未触价。`)
      await refresh(true)
    } catch (matchError) {
      setError(apiErrorMessage(matchError))
    } finally {
      setSubmitting(false)
    }
  }

  const cancelOrder = async (orderId: string) => {
    setSubmitting(true)
    setError('')
    try {
      await client.cancelPaperOrder(orderId)
      setSuccess('委托已撤销，冻结资金或可卖数量已经释放。')
      await refresh(true)
    } catch (cancelError) {
      setError(apiErrorMessage(cancelError))
    } finally {
      setSubmitting(false)
    }
  }

  const selectSell = (position: PaperPortfolio['positions'][number]) => {
    setSide('sell')
    setSymbol(position.symbol)
    setQuantity(position.available_quantity || position.quantity)
    void loadQuote(position.symbol)
    document.querySelector<HTMLElement>('#paper-order-ticket')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const selectSide = (nextSide: 'buy' | 'sell') => {
    setSide(nextSide)
    if (nextSide === 'buy') {
      setQuantity((current) => Math.max(100, Math.ceil(current / 100) * 100))
    }
  }

  const quantityIsValid = Number.isInteger(quantity)
    && (side === 'buy' ? quantity >= 100 && quantity % 100 === 0 : quantity > 0)
  const estimate = estimateOrder(orderType === 'limit' ? limitPrice : quote?.latest_price, side, quantity)
  const marketHoursEnforced = portfolio?.account.rules.market_hours_enforced
  const afterCloseSettlement = portfolio?.account.rules.after_close_settlement
  const tradingModeDescription = portfolio
    ? marketHoursEnforced
      ? afterCloseSettlement
        ? '盘中实时 + 盘后收盘：交易时段按实时行情撮合，交易日 15:00 后按当日收盘价结算。'
        : '交易时段模式：仅在交易日 09:30–11:30、13:00–15:00 接受委托。'
      : '全天模拟模式：非交易时段也可使用最新可用的服务端行情完成虚拟撮合。'
    : '正在读取模拟盘交易规则。'

  return (
    <main className="finance-page paper-page">
      <section className="finance-hero paper-hero">
        <div>
          <span className="section-kicker">PAPER LEDGER · SERVER-SIDE EXECUTION</span>
          <h1>模拟交易</h1>
          <p>用 Finance Pack 服务端行情进行虚拟撮合，真实记录现金、持仓、费用与盈亏。订单不会发送至券商。</p>
        </div>
        <div className="paper-hero-actions">
          <button className="secondary-button" onClick={onOpenMarketData}><Search size={14} />查看行情数据</button>
          <button className="secondary-button" onClick={() => void matchOpenOrders()} disabled={submitting || !portfolio?.summary.open_order_count}><Cpu size={14} />刷新撮合</button>
          <button className="secondary-button" onClick={() => void refresh()} disabled={loading}><RefreshCw className={loading ? 'is-spinning' : ''} size={14} />刷新账户</button>
          <button className="danger-ghost-button" onClick={() => setResetOpen(true)}><RotateCcw size={14} />重置账户</button>
        </div>
      </section>

      <div className="paper-safety-banner"><ShieldCheck size={17} /><div><strong>仅限模拟交易 · {engine?.engine || '正在检测撮合引擎'}</strong><span>{tradingModeDescription} VeighNa 负责订单与 Tick 到价撮合，Cognitrade 账本负责资金、费用、T+1 和审计；不会向券商发送请求。</span></div></div>
      {error && <div className="finance-alert" role="alert"><AlertCircle size={17} /><span>{error}</span><button onClick={() => setError('')}>关闭</button></div>}
      {success && <div className="finance-success" role="status"><ShieldCheck size={17} /><span>{success}</span><button onClick={() => setSuccess('')} aria-label="关闭提示"><X size={14} /></button></div>}

      {loading && <PaperSkeleton />}
      {!loading && portfolio && (
        <>
          <section className="paper-account-grid">
            <AccountMetric icon={<WalletCards size={18} />} label="账户权益" value={formatMoney(portfolio.summary.equity)} note={`第 ${portfolio.account.generation} 代模拟账户`} />
            <AccountMetric icon={<Landmark size={18} />} label="可用资金" value={formatMoney(portfolio.summary.cash)} note={portfolio.summary.frozen_cash > 0 ? `挂单冻结 ${formatMoney(portfolio.summary.frozen_cash)}` : `净资金变动 ${formatSignedMoney(portfolio.summary.cash_balance - portfolio.account.initial_cash)}`} />
            <AccountMetric icon={<ArrowDownToLine size={18} />} label="持仓市值" value={formatMoney(portfolio.summary.market_value)} note={`${portfolio.summary.position_count} 只持仓`} />
            <AccountMetric icon={<ArrowUpFromLine size={18} />} label="累计盈亏" value={formatSignedMoney(portfolio.summary.total_pnl)} note={formatPercent(portfolio.summary.total_return_pct)} tone={portfolio.summary.total_pnl >= 0 ? 'up' : 'down'} />
          </section>

          <div className="paper-trade-layout">
            <form id="paper-order-ticket" className="paper-order-ticket" onSubmit={submitOrder}>
              <header><div><span className="section-kicker">ORDER TICKET</span><h2>模拟委托</h2></div><span className="quality-pill quality-pill--available">{orderType === 'market' ? '市价撮合' : '限价挂单'}</span></header>
              <div className="order-side-switch" role="group" aria-label="交易方向">
                <button type="button" className={side === 'buy' ? 'is-active is-buy' : ''} onClick={() => selectSide('buy')}>买入</button>
                <button type="button" className={side === 'sell' ? 'is-active is-sell' : ''} onClick={() => selectSide('sell')}>卖出</button>
              </div>
              <div className="paper-order-type-switch" role="group" aria-label="委托类型">
                <button type="button" className={orderType === 'market' ? 'is-active' : ''} onClick={() => setOrderType('market')}>市价单</button>
                <button type="button" className={orderType === 'limit' ? 'is-active' : ''} onClick={() => { setOrderType('limit'); if (quote) setLimitPrice(quote.latest_price) }}>限价单</button>
              </div>
              <label><span>股票代码或名称</span><div className="quote-input-row"><input value={symbol} onChange={(event) => { setSymbol(event.target.value); setQuote(null) }} placeholder="600519" required /><button type="button" onClick={() => void loadQuote(symbol, true)} disabled={quoteLoading || !symbol.trim()}>{quoteLoading ? <RefreshCw className="is-spinning" size={14} /> : <Search size={14} />}取价</button></div></label>
              {orderType === 'limit' && <label><span>委托限价</span><div className="quantity-input-row"><input type="number" min="0.01" step="0.01" value={limitPrice || ''} onChange={(event) => setLimitPrice(Math.max(Number(event.target.value) || 0, 0))} required /><span>元</span></div></label>}
              <label><span>委托数量</span><div className="quantity-input-row"><input type="number" min={side === 'buy' ? 100 : 1} step={side === 'buy' ? 100 : 1} value={quantity} onChange={(event) => setQuantity(Math.max(Number(event.target.value) || 0, 0))} inputMode="numeric" aria-describedby="paper-order-rule" required /><span>股</span></div></label>
              <div className="quantity-presets">
                {[100, 500, 1000].map((value) => <button type="button" key={value} onClick={() => setQuantity(value)}>{value} 股</button>)}
                {side === 'sell' && portfolio.positions.find((position) => position.symbol === symbol)?.available_quantity ? <button type="button" onClick={() => setQuantity(portfolio.positions.find((position) => position.symbol === symbol)!.available_quantity)}>全部可卖</button> : null}
              </div>

              <div className="paper-quote-card">
                {quote ? <><div><span>{quote.symbol}</span><strong>{quote.name}</strong></div><strong>{formatMoney(quote.latest_price)}</strong><small className={(quote.pct_change || 0) >= 0 ? 'number-up' : 'number-down'}>{formatPercent(quote.pct_change)}</small><time>{quote.as_of || '行情日期未知'}</time></> : <><Info size={17} /><span>提交前请先获取服务端行情报价</span></>}
              </div>

              <dl className="order-estimate">
                <div><dt>预计成交金额</dt><dd>{formatMoney(estimate.gross)}</dd></div>
                <div><dt>预计费用</dt><dd>{formatMoney(estimate.fees)}</dd></div>
                <div><dt>{side === 'buy' ? '预计占用资金' : '预计回笼资金'}</dt><dd>{formatMoney(estimate.total)}</dd></div>
              </dl>
              <button className={`paper-submit-button paper-submit-button--${side}`} disabled={submitting || !quote || !quantityIsValid || (orderType === 'limit' && limitPrice <= 0)}>
                {submitting ? <RefreshCw className="is-spinning" size={15} /> : side === 'buy' ? <ArrowDownToLine size={15} /> : <ArrowUpFromLine size={15} />}
                {submitting ? '服务端撮合中' : `确认${side === 'buy' ? '买入' : '卖出'} ${quantity || 0} 股`}
              </button>
              <p id="paper-order-rule" className="order-rule-note"><Clock3 size={13} />{marketHoursEnforced ? afterCloseSettlement ? '盘中按实时行情、15:00 后按收盘价；' : '仅限连续竞价时段下单；' : '支持全天模拟下单；'}限价单未触价时可在下方撤单。</p>
            </form>

            <section className="paper-positions-card">
              <header><div><span className="section-kicker">POSITIONS</span><h2>当前持仓</h2></div><small>{formatDateTime(portfolio.as_of)}</small></header>
              {portfolio.positions.length ? (
                <div className="finance-table-scroll"><table className="finance-table paper-position-table"><thead><tr><th>证券</th><th>持仓 / 可卖</th><th>成本 / 现价</th><th>市值</th><th>浮动盈亏</th><th /></tr></thead><tbody>{portfolio.positions.map((position) => <tr key={position.symbol}><td><strong>{position.name}</strong><small>{position.symbol}{position.quote_stale ? ' · 估值回退' : ''}</small></td><td>{position.quantity.toLocaleString()}<small>{position.available_quantity.toLocaleString()} 可卖</small></td><td>{formatMoney(position.average_cost)}<small>{formatMoney(position.latest_price)}</small></td><td>{formatMoney(position.market_value)}</td><td className={position.unrealized_pnl >= 0 ? 'number-up' : 'number-down'}>{formatSignedMoney(position.unrealized_pnl)}<small>{formatPercent(position.unrealized_pnl_pct)}</small></td><td><button onClick={() => selectSell(position)} disabled={position.available_quantity <= 0}>{position.available_quantity > 0 ? '卖出' : 'T+1'}</button></td></tr>)}</tbody></table></div>
              ) : <div className="paper-empty"><WalletCards size={25} /><strong>尚无持仓</strong><span>获取报价并提交第一笔模拟买入。</span></div>}
            </section>
          </div>

          <section className="paper-orders-card">
            <header><div><span className="section-kicker">ORDER HISTORY</span><h2>委托与成交记录</h2></div><small>{portfolio.summary.open_order_count} 笔待成交 · 最近 {orders.length} 笔</small></header>
            {orders.length ? <div className="finance-table-scroll"><table className="finance-table"><thead><tr><th>时间</th><th>方向 / 类型</th><th>证券</th><th>数量</th><th>限价 / 成交价</th><th>成交额</th><th>费用 / 冻结</th><th>资金变动</th><th>状态</th><th /></tr></thead><tbody>{orders.map((order) => <tr key={order.order_id}><td>{formatDateTime(order.created_at)}<small>{order.matching_engine} · {order.pricing_mode === 'closing_price' ? '15:00 收盘价' : '盘中行情'}</small></td><td><span className={`order-side-label order-side-label--${order.side}`}>{order.side === 'buy' ? '买入' : '卖出'}</span><small>{order.order_type === 'limit' ? '限价单' : '市价单'}</small></td><td><strong>{order.name}</strong><small>{order.symbol}</small></td><td>{order.quantity.toLocaleString()}</td><td>{order.limit_price ? formatMoney(order.limit_price) : '市价'}<small>{order.status === 'filled' ? formatMoney(order.fill_price) : '等待触价'}</small></td><td>{order.status === 'filled' ? formatMoney(order.gross_amount) : '—'}</td><td>{order.status === 'pending' ? (order.reserved_cash ? formatMoney(order.reserved_cash) : `${order.reserved_quantity} 股`) : formatMoney(order.fees.total)}</td><td className={order.cash_effect >= 0 ? 'number-up' : 'number-down'}>{order.status === 'filled' ? formatSignedMoney(order.cash_effect) : '—'}</td><td><span className={`paper-order-status paper-order-status--${order.status}`}>{order.status === 'filled' ? '已成交' : order.status === 'pending' ? '待成交' : order.status === 'cancelled' ? '已撤单' : order.status}</span></td><td>{order.status === 'pending' && <button className="paper-cancel-order" onClick={() => void cancelOrder(order.order_id)} disabled={submitting} aria-label={`撤销 ${order.name} 委托`}><Trash2 size={13} />撤单</button>}</td></tr>)}</tbody></table></div> : <div className="paper-empty paper-empty--compact"><Clock3 size={22} /><span>还没有模拟委托记录</span></div>}
          </section>
        </>
      )}

      {resetOpen && (
        <div className="api-dialog-backdrop" role="presentation" onMouseDown={() => setResetOpen(false)}>
          <form className="api-dialog paper-reset-dialog" onSubmit={resetAccount} onMouseDown={(event) => event.stopPropagation()}>
            <div className="api-dialog-heading"><span className="stat-icon stat-icon--warning"><RotateCcw size={18} /></span><div><span className="section-kicker">RESET PAPER ACCOUNT</span><h2>重置模拟账户</h2></div><button type="button" className="icon-button" onClick={() => setResetOpen(false)} aria-label="关闭"><X size={16} /></button></div>
            <p>当前账户会被归档，持仓和现金不会带入新账户；历史数据库记录仍保留以便审计。</p>
            <label><span>新账户初始资金</span><input type="number" min="10000" max="1000000000" step="10000" value={initialCash} onChange={(event) => setInitialCash(Number(event.target.value))} required /></label>
            <div className="api-dialog-actions"><button type="button" className="secondary-button" onClick={() => setResetOpen(false)}>取消</button><button className="danger-button" disabled={submitting}>确认重置</button></div>
          </form>
        </div>
      )}
    </main>
  )
}

function AccountMetric({ icon, label, value, note, tone }: { icon: React.ReactNode; label: string; value: string; note: string; tone?: 'up' | 'down' }) {
  return <article className="paper-account-metric"><span>{icon}</span><div><small>{label}</small><strong className={tone ? `number-${tone}` : ''}>{value}</strong><p>{note}</p></div></article>
}

function PaperSkeleton() {
  return <div className="finance-data-stack"><div className="paper-account-grid">{Array.from({ length: 4 }, (_, index) => <div key={index} className="paper-account-metric finance-skeleton" />)}</div><div className="paper-trade-layout"><div className="paper-order-ticket finance-skeleton" /><div className="paper-positions-card finance-skeleton" /></div></div>
}

function formatMoney(value: number): string {
  return `¥${Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function formatSignedMoney(value: number): string {
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${formatMoney(Math.abs(value))}`
}

function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof CognitradeApiError) return `${error.message}${error.requestId ? ` · request ${error.requestId}` : ''}`
  return error instanceof Error ? error.message : String(error)
}
