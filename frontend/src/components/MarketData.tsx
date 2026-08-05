import {
  Activity,
  AlertCircle,
  BarChart3,
  Building2,
  Clock3,
  Database,
  Landmark,
  Newspaper,
  RefreshCw,
  Search,
  TrendingUp,
} from 'lucide-react'
import { type Dispatch, type FormEvent, type ReactNode, type SetStateAction, useMemo, useState } from 'react'
import { BusinessDataView } from '../artifacts/BusinessDataView'
import { CognitradeApiClient, CognitradeApiError } from '../api-client'
import { useCognitrade } from '../CognitradeContext'
import type { JsonRecord, MarketDataResponse } from '../finance-contracts'
import { dataUnavailableMessage, isTechnicalErrorMessage } from '../utils/data-availability'

const EXAMPLES = [
  { symbol: '600519', label: '贵州茅台' },
  { symbol: '600036', label: '招商银行' },
  { symbol: '300750', label: '宁德时代' },
]

type KlineBar = {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number | null
  turnover: number | null
}

type KlineRange = 20 | 60 | 120 | 250 | 750 | 'all'

export interface MarketDataViewState {
  query: string
  data: MarketDataResponse | null
  loading: boolean
  error: string
}

interface MarketDataProps {
  state: MarketDataViewState
  setState: Dispatch<SetStateAction<MarketDataViewState>>
}

export function MarketData({ state, setState }: MarketDataProps) {
  const { settings } = useCognitrade()
  const client = useMemo(() => new CognitradeApiClient(settings), [settings])
  const { query, data, loading, error } = state
  const setQuery = (nextQuery: string) => setState((current) => ({ ...current, query: nextQuery }))

  const load = async (value = query, refresh = false) => {
    const normalized = value.trim()
    if (!normalized) return
    setState((current) => ({ ...current, loading: true, error: '' }))
    try {
      const response = await client.getMarketData(normalized, refresh)
      setState((current) => ({ ...current, data: response, query: response.symbol }))
    } catch (loadError) {
      setState((current) => ({ ...current, error: apiErrorMessage(loadError) }))
    } finally {
      setState((current) => ({ ...current, loading: false }))
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void load()
  }

  return (
    <main className="finance-page market-data-page">
      <section className="finance-hero">
        <div>
          <span className="section-kicker">FINANCE PACK · VERIFIED SNAPSHOTS</span>
          <h1>行情与研究数据</h1>
          <p>从真实数据源读取行情、财务、资金情绪、公告事件与龙虎榜；缺失项会明确标记，不做推测补全。</p>
        </div>
        <form className="security-search" onSubmit={submit}>
          <Search size={17} aria-hidden="true" />
          <label className="sr-only" htmlFor="market-security-search">股票代码或名称</label>
          <input
            id="market-security-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入股票代码或名称，例如 600519"
            autoComplete="off"
          />
          <button className="primary-button" type="submit" disabled={loading || !query.trim()}>
            {loading ? <RefreshCw className="is-spinning" size={15} /> : <Search size={15} />}
            {loading ? '采集中' : '查询'}
          </button>
        </form>
        <div className="security-examples" aria-label="示例股票">
          {EXAMPLES.map((example) => (
            <button key={example.symbol} onClick={() => { setQuery(example.symbol); void load(example.symbol) }} disabled={loading}>
              <strong>{example.label}</strong><span>{example.symbol}</span>
            </button>
          ))}
        </div>
      </section>

      {error && (
        <div className="finance-alert" role="alert">
          <AlertCircle size={17} />
          <span>{error}</span>
          <button onClick={() => void load(query, true)}>重试并刷新数据源</button>
        </div>
      )}

      {!data && !loading && !error && (
        <section className="finance-empty-state">
          <Database size={28} />
          <h2>选择一只 A 股开始读取</h2>
          <p>一次请求会并行收集八类 Finance Pack 快照。首次读取取决于上游数据源响应速度，后续使用分区缓存。</p>
          <button className="primary-button" onClick={() => void load('600519')}>查看示例数据</button>
        </section>
      )}

      {loading && <MarketDataSkeleton />}
      {data && !loading && <MarketDataContent data={data} onRefresh={() => void load(data.symbol, true)} />}
    </main>
  )
}

function MarketDataContent({ data, onRefresh }: { data: MarketDataResponse; onRefresh: () => void }) {
  const overview = data.sections.market.overview
  const kline = data.sections.kline
  const latestBar = asRecord(kline.latest_bar) || asRecord(data.sections.market.history_summary.latest_bar) || {}
  const pctChange = asNumber(first(overview, 'pct_change', '涨跌幅'))
  const positive = (pctChange || 0) >= 0
  const fundamental = data.sections.fundamental
  const flow = data.sections.capital_flow
  const sentiment = data.sections.sentiment
  const notices = data.sections.notices
  const events = data.sections.events
  const lhb = data.sections.lhb
  const source = data.sources.market || {}
  const isBank = isBankSecurity(fundamental.info, data.name)

  return (
    <div className="finance-data-stack">
      <section className="market-identity-card">
        <div className="market-security-name">
          <span className={`quote-direction ${positive ? 'is-up' : 'is-down'}`}>{positive ? '涨' : '跌'}</span>
          <div><span>{data.symbol}</span><h2>{data.name}</h2></div>
        </div>
        <div className="market-price-block">
          <strong>{formatPrice(first(overview, 'latest_price', '最新价'))}</strong>
          <span className={positive ? 'number-up' : 'number-down'}>{formatPercent(pctChange)}</span>
        </div>
        <div className="market-source-block">
          <span className={`quality-pill quality-pill--${data.quality_status}`}>{qualityLabel(data.quality_status)}</span>
          <small><Database size={12} />{String(source.provider_id || source.vendor || 'Finance Pack')}</small>
          <small><Clock3 size={12} />{formatDateTime(data.retrieved_at)}</small>
        </div>
        <button className="secondary-button" onClick={onRefresh}><RefreshCw size={14} />刷新源数据</button>
      </section>

      <section className="metric-grid metric-grid--market">
        <Metric label="今开" value={formatPrice(first(overview.spot as JsonRecord, '今开', 'open') ?? latestBar.open)} />
        <Metric label="最高" value={formatPrice(first(overview.spot as JsonRecord, '最高', 'high') ?? latestBar.high)} />
        <Metric label="最低" value={formatPrice(first(overview.spot as JsonRecord, '最低', 'low') ?? latestBar.low)} />
        <Metric label="收盘" value={formatPrice(latestBar.close ?? first(overview, 'latest_price'))} />
        <Metric label="成交量" value={formatCompact(first(overview.spot as JsonRecord, '成交量', 'volume') ?? latestBar.volume, '股')} />
        <Metric label="成交额" value={formatCompact(first(overview, 'amount') ?? latestBar.amount, '元')} />
      </section>

      <DataSection icon={<TrendingUp size={18} />} kicker="VALUATION" title="估值概览" quality={data.quality.market}>
        <div className="metric-grid metric-grid--valuation">
          <Metric label="市盈率（TTM）" value={formatRatio(first(overview, 'pe_ttm'))} />
          <Metric label="静态市盈率" value={formatRatio(first(overview, 'pe_static'))} />
          <Metric label="市净率" value={formatRatio(first(overview, 'pb'))} />
          <Metric label="市盈增长比" value={formatRatio(first(overview, 'peg'))} />
          <Metric label="市现率" value={formatRatio(first(overview, 'pcf'))} />
          <Metric label="市销率" value={formatRatio(first(overview, 'ps'))} />
          <Metric label="总市值" value={formatCompact(first(overview, 'market_cap'), '元')} />
          <Metric label="流通市值" value={formatCompact(first(overview, 'float_market_cap'), '元')} />
          <Metric label="估值日期" value={formatValue(first(overview, 'valuation_date'))} />
        </div>
        <RecordPanel title="行情与估值补充字段" value={remainingFields(overview, [
          'latest_price', 'pct_change', 'amount', 'pe_ttm', 'pe_static', 'pb', 'peg', 'pcf', 'ps',
          'market_cap', 'float_market_cap', 'valuation_date', 'spot',
        ])} wide hideWhenEmpty />
        <RecordPanel title="行情历史摘要" value={data.sections.market.history_summary} wide />
      </DataSection>

      <DataSection icon={<BarChart3 size={18} />} kicker="HISTORICAL KLINE" title="历史 K 线 · 日线" quality={data.quality.kline}>
        <KlineHistory kline={kline} fallbackBar={latestBar} />
      </DataSection>

      <DataSection icon={<Building2 size={18} />} kicker="FUNDAMENTALS" title="基本面与财务" quality={data.quality.fundamental}>
        <div className="record-panel-columns">
          <RecordPanel title="公司资料" value={fundamental.info} />
          <RecordPanel title="财务摘要" value={fundamental.financial_abstract_new || fundamental.financial_abstract} />
          <RecordPanel title="盈利能力" value={fundamental.financial_benefit_new} />
          <RecordPanel title="资产负债" value={fundamental.financial_debt_new} />
          <RecordPanel title="现金流" value={fundamental.financial_cash_new} />
          <RecordPanel title="财务分析" value={fundamental.financial_analysis} />
        </div>
        {isBank && hasData(fundamental.banking_metrics) && (
          <div className="banking-block">
            <div className="subsection-title"><Landmark size={16} /><strong>银行专项指标</strong></div>
            <RecordTable value={fundamental.banking_metrics} limit={20} />
            <RecordPanel title="同行比较" value={fundamental.bank_peer_comparison} wide />
          </div>
        )}
        <RecordPanel title="基本面补充字段" value={remainingFields(fundamental, [
          'info', 'financial_abstract_new', 'financial_abstract', 'financial_benefit_new',
          'financial_debt_new', 'financial_cash_new', 'financial_analysis', 'banking_metrics',
          'bank_peer_comparison',
        ])} wide hideWhenEmpty />
      </DataSection>

      <DataSection icon={<Activity size={18} />} kicker="CAPITAL FLOW" title="资金流与主力排名" quality={data.quality.capital_flow}>
        <RecordPanel title="最新资金流" value={flow.latest_flow} wide />
        <RecordPanel title="最近资金流" value={flow.recent_flows} wide />
        <RecordPanel title="主力资金排名" value={flow.main_flow_rank} wide />
        <RecordPanel title="资金流补充字段" value={remainingFields(flow, ['latest_flow', 'recent_flows', 'main_flow_rank'])} wide hideWhenEmpty />
      </DataSection>

      <DataSection icon={<TrendingUp size={18} />} kicker="MARKET SIGNALS" title="市场热度与新闻" quality={data.quality.sentiment}>
        <div className="market-signal-summary-grid">
          <RecordPanel title="热度排名趋势" value={sentiment.hot_rank_detail || sentiment.hot_rank_latest} />
          <article className="record-panel market-keyword-panel">
            <TagList title="热门关键词" value={sentiment.hot_keywords} />
          </article>
        </div>
        <RecordPanel title="新闻样本" value={sentiment.news_samples} wide />
        <RecordPanel title="市场热度补充字段" value={remainingFields(sentiment, ['hot_rank_detail', 'hot_rank_latest', 'hot_keywords', 'news_samples'])} wide hideWhenEmpty />
      </DataSection>

      <DataSection icon={<Newspaper size={18} />} kicker="DISCLOSURES & EVENTS" title="公司事件与公告">
        <div className="disclosure-overview">
          <div className="notice-count">
            <strong>{formatValue(notices.notice_count)}</strong>
            <span>条公告</span>
            <small>{formatDate(notices.window_start)} — {formatDate(notices.window_end)}</small>
          </div>
          <div className="disclosure-quality-pills" aria-label="信息披露数据状态">
            {data.quality.events && <span className={`quality-pill quality-pill--${data.quality.events}`}>事件 · {qualityLabel(data.quality.events)}</span>}
            {data.quality.notice && <span className={`quality-pill quality-pill--${data.quality.notice}`}>公告 · {qualityLabel(data.quality.notice)}</span>}
          </div>
        </div>
        <div className="disclosure-event-summary">
          <div className="disclosure-keyword-panel">
            <TagList title="事件关键词" value={events.event_keywords} />
          </div>
          <div className="disclosure-timeline-panel">
            <h3><Clock3 size={14} aria-hidden="true" />事件时间线</h3>
            <Timeline value={events.event_notices} />
          </div>
        </div>
        <RecordPanel title="最近 30 天公告" value={notices.recent_notices} wide />
        <RecordPanel title="公告补充字段" value={remainingFields(notices, ['notice_count', 'window_start', 'window_end', 'recent_notices'])} wide hideWhenEmpty />
        <RecordPanel title="事件补充字段" value={remainingFields(events, ['event_keywords', 'event_notices'])} wide hideWhenEmpty />
      </DataSection>

      <DataSection icon={<Landmark size={18} />} kicker="DRAGON & TIGER LIST" title="龙虎榜" quality={data.quality.lhb}>
        <div className="lhb-summary">
          <Metric label="最近上榜" value={formatDate(lhb.latest_lhb_date)} />
          <Metric label="买入席位合计" value={formatCompact(lhb.buy_top_total, '元')} tone="up" />
          <Metric label="卖出席位合计" value={formatCompact(lhb.sell_top_total, '元')} tone="down" />
        </div>
        <div className="record-panel-grid">
          <RecordPanel title="买入席位" value={lhb.buy_detail} />
          <RecordPanel title="卖出席位" value={lhb.sell_detail} />
        </div>
        <RecordPanel title="统计记录" value={lhb.lhb_statistics} wide />
        <RecordPanel title="龙虎榜补充字段" value={remainingFields(lhb, ['latest_lhb_date', 'buy_top_total', 'sell_top_total', 'buy_detail', 'sell_detail', 'lhb_statistics'])} wide hideWhenEmpty />
      </DataSection>

      <DataSection icon={<Database size={18} />} kicker="SOURCE COVERAGE" title="数据来源与完整性" quality={data.quality_status}>
        <SourceCoverage quality={data.quality} sources={data.sources} retrievedAt={data.retrieved_at} />
      </DataSection>
    </div>
  )
}

function DataSection({ icon, kicker, title, quality, children }: { icon: ReactNode; kicker: string; title: string; quality?: string; children: ReactNode }) {
  return (
    <section className="finance-section-card">
      <header>
        <span className="finance-section-icon">{icon}</span>
        <div><span className="section-kicker">{kicker}</span><h2>{title}</h2></div>
        {quality && <span className={`quality-pill quality-pill--${quality}`}>{qualityLabel(quality)}</span>}
      </header>
      <div className="finance-section-body">{children}</div>
    </section>
  )
}

const SOURCE_LABELS: Record<string, string> = {
  market: '实时行情与估值',
  kline: '历史 K 线',
  fundamental: '基本面与财务',
  capital_flow: '资金流',
  sentiment: '市场热度与新闻',
  notice: '公司公告',
  events: '公司事件',
  lhb: '龙虎榜',
}

function SourceCoverage({ quality, sources, retrievedAt }: { quality: Record<string, string>; sources: Record<string, JsonRecord>; retrievedAt: string }) {
  return (
    <div className="source-coverage-list">
      {Object.keys(SOURCE_LABELS).map((sourceType) => {
        const source = sources[sourceType] || {}
        const state = quality[sourceType] || 'data_gap'
        return (
          <details key={sourceType} className="source-coverage-row">
            <summary>
              <span className={`source-coverage-dot is-${state}`} />
              <span><strong>{SOURCE_LABELS[sourceType]}</strong><small>{String(source.provider_id || source.vendor || '来源未返回')}</small></span>
              <span className={`quality-pill quality-pill--${state}`}>{qualityLabel(state)}</span>
            </summary>
            <div>
              {Object.keys(source).length ? <BusinessDataView value={source} path={`market_sources.${sourceType}`} compact /> : <p className="data-empty">后端未返回该分区的来源元数据。</p>}
            </div>
          </details>
        )
      })}
      <footer><Clock3 size={12} />本页数据汇总时间：{formatDateTime(retrievedAt)}</footer>
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return <div className={`finance-metric ${tone ? `finance-metric--${tone}` : ''}`}><span>{label}</span><strong>{value}</strong></div>
}

function remainingFields(value: unknown, consumed: string[]): JsonRecord {
  const record = asRecord(value)
  if (!record) return {}
  const omitted = new Set(consumed)
  return Object.fromEntries(Object.entries(record).filter(([key]) => !omitted.has(key)))
}

function KlineHistory({ kline, fallbackBar }: { kline: JsonRecord; fallbackBar: JsonRecord }) {
  const allBars = normalizeKlineBars(kline.bars)
  const fallbackBars = allBars.length ? allBars : normalizeKlineBars([fallbackBar])
  const [range, setRange] = useState<KlineRange>('all')
  const [selectedDate, setSelectedDate] = useState('')
  const bars = range === 'all' ? fallbackBars : fallbackBars.slice(-range)
  const selected = bars.find((bar) => bar.date === selectedDate) || bars.at(-1)
  const hasHistory = allBars.length > 1

  if (!selected) {
    return <div className="kline-empty"><BarChart3 size={25} /><strong>暂无历史 K 线</strong><span>数据源未返回可绘制的开、高、低、收价格。</span></div>
  }

  const width = 1000
  const priceTop = 18
  const priceHeight = 250
  const volumeTop = 296
  const volumeHeight = 58
  const plotLeft = 18
  const plotRight = 944
  const plotWidth = plotRight - plotLeft
  const priceLow = Math.min(...bars.map((bar) => bar.low))
  const priceHigh = Math.max(...bars.map((bar) => bar.high))
  const pricePadding = Math.max((priceHigh - priceLow) * 0.08, priceHigh * 0.002, 0.01)
  const scaleLow = priceLow - pricePadding
  const scaleHigh = priceHigh + pricePadding
  const priceSpan = scaleHigh - scaleLow || 1
  const maxVolume = Math.max(...bars.map((bar) => bar.volume), 1)
  const slotWidth = plotWidth / Math.max(bars.length, 1)
  const candleWidth = Math.max(2, Math.min(slotWidth * 0.58, 12))
  const priceY = (value: number) => priceTop + ((scaleHigh - value) / priceSpan) * priceHeight
  const volumeY = (value: number) => volumeTop + volumeHeight - (value / maxVolume) * volumeHeight
  const gridTicks = Array.from({ length: 5 }, (_, index) => scaleHigh - (priceSpan * index) / 4)
  const dateTickIndexes = Array.from(new Set([0, Math.floor((bars.length - 1) / 4), Math.floor((bars.length - 1) / 2), Math.floor(((bars.length - 1) * 3) / 4), bars.length - 1]))
  const rangeOptions: Array<{ value: KlineRange; label: string }> = [
    { value: 20, label: '20 日' },
    { value: 60, label: '60 日' },
    { value: 120, label: '120 日' },
    { value: 250, label: '近 1 年' },
    { value: 750, label: '近 3 年' },
    { value: 'all', label: '全部' },
  ]

  return (
    <div className="kline-history">
      <div className="kline-toolbar">
        <div className="kline-selected-summary" aria-live="polite">
          <strong>{formatDate(selected.date)}</strong>
          <span>开 {formatPrice(selected.open)}</span>
          <span>高 {formatPrice(selected.high)}</span>
          <span>低 {formatPrice(selected.low)}</span>
          <span>收 {formatPrice(selected.close)}</span>
          <span className={selected.close >= selected.open ? 'number-up' : 'number-down'}>{selected.close >= selected.open ? '收涨' : '收跌'}</span>
        </div>
        <div className="kline-range-switch" aria-label="K 线时间范围">
          {rangeOptions.map((option) => (
            <button
              key={String(option.value)}
              type="button"
              className={range === option.value ? 'is-active' : ''}
              onClick={() => setRange(option.value)}
              disabled={option.value !== 'all' && fallbackBars.length <= option.value && range !== option.value}
              aria-pressed={range === option.value}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {hasHistory ? (
        <div className="kline-chart-shell">
          <svg className="kline-chart" viewBox={`0 0 ${width} 382`} role="img" aria-label={`${formatDate(bars[0].date)}至${formatDate(bars.at(-1)?.date)}日 K 线与成交量，共 ${bars.length} 个交易日`}>
            {gridTicks.map((tick) => {
              const y = priceY(tick)
              return <g key={tick}><line className="kline-grid-line" x1={plotLeft} x2={plotRight} y1={y} y2={y} /><text className="kline-axis-label" x={plotRight + 12} y={y + 4}>{tick.toFixed(2)}</text></g>
            })}
            <line className="kline-volume-divider" x1={plotLeft} x2={plotRight} y1={volumeTop - 11} y2={volumeTop - 11} />
            {bars.map((bar, index) => {
              const x = plotLeft + index * slotWidth + slotWidth / 2
              const rising = bar.close >= bar.open
              const bodyTop = priceY(Math.max(bar.open, bar.close))
              const bodyBottom = priceY(Math.min(bar.open, bar.close))
              const bodyHeight = Math.max(bodyBottom - bodyTop, 1.8)
              const volumeBarY = volumeY(bar.volume)
              return (
                <g
                  key={`${bar.date}-${index}`}
                  className={`kline-candle ${rising ? 'is-up' : 'is-down'} ${bar.date === selected.date ? 'is-selected' : ''}`}
                  tabIndex={0}
                  role="button"
                  aria-label={`${formatDate(bar.date)}，开盘${bar.open}，最高${bar.high}，最低${bar.low}，收盘${bar.close}`}
                  onMouseEnter={() => setSelectedDate(bar.date)}
                  onFocus={() => setSelectedDate(bar.date)}
                >
                  <rect className="kline-hit-area" x={x - slotWidth / 2} y={priceTop} width={slotWidth} height={priceHeight + volumeHeight + 36} />
                  <line className="kline-wick" x1={x} x2={x} y1={priceY(bar.high)} y2={priceY(bar.low)} />
                  <rect className="kline-body" x={x - candleWidth / 2} y={bodyTop} width={candleWidth} height={bodyHeight} rx="0.7" />
                  <rect className="kline-volume" x={x - candleWidth / 2} y={volumeBarY} width={candleWidth} height={volumeTop + volumeHeight - volumeBarY} rx="0.7" />
                </g>
              )
            })}
            {dateTickIndexes.map((index) => {
              const bar = bars[index]
              const x = plotLeft + index * slotWidth + slotWidth / 2
              return <text key={`${bar.date}-${index}`} className="kline-date-label" x={x} y={377} textAnchor={index === 0 ? 'start' : index === bars.length - 1 ? 'end' : 'middle'}>{shortDate(bar.date)}</text>
            })}
            <text className="kline-volume-label" x={plotLeft} y={volumeTop - 17}>成交量</text>
          </svg>
        </div>
      ) : (
        <div className="kline-cache-note"><AlertCircle size={16} /><span>当前缓存只包含最后一个交易日，请点击“刷新源数据”获取完整历史序列。</span></div>
      )}

      <div className="kline-meta-grid">
        <Metric label="历史起始" value={formatDate(first(kline, 'start_date') || fallbackBars[0]?.date)} />
        <Metric label="历史截止" value={formatDate(first(kline, 'end_date') || fallbackBars.at(-1)?.date)} />
        <Metric label="复权方式" value={adjustLabel(first(kline, 'adjust'))} />
        <Metric label="交易日数量" value={`${formatValue(first(kline, 'bar_count') || fallbackBars.length)} 日`} />
        <Metric label="最后交易日" value={formatDate(selected.date)} />
        <Metric label="换手率" value={formatTurnover(selected.turnover)} />
      </div>

      <div className="ohlc-strip" aria-label="选中交易日行情摘要">
        <div><span>开盘价</span><strong>{formatPrice(selected.open)}</strong></div>
        <div><span>最高价</span><strong>{formatPrice(selected.high)}</strong></div>
        <div><span>最低价</span><strong>{formatPrice(selected.low)}</strong></div>
        <div><span>收盘价</span><strong>{formatPrice(selected.close)}</strong></div>
        <div><span>成交量</span><strong>{formatCompact(selected.volume, '股')}</strong></div>
        <div><span>成交额</span><strong>{formatCompact(selected.amount, '元')}</strong></div>
      </div>

      {hasHistory && <p className="kline-history-note">历史序列已全部进入上方趋势图；为避免用大表淹没研究页面，这里不重复铺开逐日原始明细。</p>}
    </div>
  )
}

function RecordPanel({ title, value, wide = false, hideWhenEmpty = false }: { title: string; value: unknown; wide?: boolean; hideWhenEmpty?: boolean }) {
  if (hideWhenEmpty && !dataUnavailableMessage(value) && !hasData(value)) return null
  return <article className={`record-panel ${wide ? 'record-panel--wide' : ''}`}><h3>{title}</h3><RecordTable value={value} /></article>
}

function RecordTable({ value, limit = 12 }: { value: unknown; limit?: number }) {
  const [expanded, setExpanded] = useState(false)
  const unavailableMessage = dataUnavailableMessage(value)
  if (unavailableMessage) {
    return <div className="data-unavailable"><AlertCircle size={14} aria-hidden="true" /><span>{unavailableMessage}</span></div>
  }
  const records = normalizeRecords(value)
  if (!records.length) return <p className="data-empty">数据源暂未返回可展示记录</p>
  const hasNestedValue = records.some((row) => Object.values(row).some((item) => item != null && typeof item === 'object'))
  if (hasNestedValue) {
    return <BusinessDataView value={value} path="market_data" compact />
  }
  if (records.length === 1) {
    const entries = Object.entries(records[0])
    const visibleEntries = expanded ? entries : entries.slice(0, limit)
    return <><dl className="record-definition-list">{visibleEntries.map(([key, item]) => <div key={key}><dt>{fieldLabel(key)}</dt><dd>{formatValue(item)}</dd></div>)}</dl>{entries.length > limit && <button className="record-table-more" type="button" onClick={() => setExpanded((current) => !current)}>{expanded ? '收起字段' : `查看全部 ${entries.length} 个字段`}</button>}</>
  }
  const allColumns = Array.from(new Set(records.flatMap((row) => Object.keys(row))))
  const columns = expanded ? allColumns : allColumns.slice(0, 6)
  const visibleRecords = expanded ? records : records.slice(0, limit)
  const hasMore = records.length > limit || allColumns.length > 6
  return (
    <><div className="finance-table-scroll"><table className="finance-table"><thead><tr>{columns.map((column) => <th key={column}>{fieldLabel(column)}</th>)}</tr></thead><tbody>{visibleRecords.map((row, index) => <tr key={`${index}-${columns.map((column) => String(row[column] ?? '')).join('-')}`}>{columns.map((column) => <td key={column}>{formatValue(row[column])}</td>)}</tr>)}</tbody></table></div>{hasMore && <button className="record-table-more" type="button" onClick={() => setExpanded((current) => !current)}>{expanded ? '收起明细' : `查看全部 ${records.length} 条 · ${allColumns.length} 列`}</button>}</>
  )
}

function TagList({ title, value }: { title: string; value: unknown }) {
  const tags = Array.isArray(value) ? value : []
  return <div className="tag-list"><h3>{title}</h3><div>{tags.length ? tags.map((tag, index) => <span key={`${tagLabel(tag)}-${index}`}>{tagLabel(tag)}</span>) : <p className="data-empty">暂无关键词</p>}</div></div>
}

function Timeline({ value }: { value: unknown }) {
  const records = normalizeRecords(value)
  const [expanded, setExpanded] = useState(false)
  if (!records.length) return <p className="data-empty">最近 30 天未返回已分类事件</p>
  const visible = expanded ? records : records.slice(0, 12)
  return <><ol className="event-timeline">{visible.map((event, index) => <li key={`${index}-${formatValue(first(event, '公告日期', '公告时间', 'date', '时间'))}`}><span /><div><time>{formatDate(first(event, '公告日期', '公告时间', 'date', '时间'))}</time><strong>{formatValue(first(event, '公告标题', 'title', '名称'))}</strong><small>{formatValue(first(event, '事件类型', 'type', 'keyword', '关键词'))}</small></div></li>)}</ol>{records.length > 12 && <button className="record-table-more" type="button" onClick={() => setExpanded((current) => !current)}>{expanded ? '收起事件' : `查看全部 ${records.length} 条事件`}</button>}</>
}

function MarketDataSkeleton() {
  return <div className="finance-data-stack" aria-label="正在读取行情数据"><div className="market-identity-card finance-skeleton" /><div className="metric-grid metric-grid--market">{Array.from({ length: 6 }, (_, index) => <div className="finance-metric finance-skeleton" key={index} />)}</div><div className="finance-section-card finance-skeleton finance-skeleton--section" /></div>
}

function normalizeRecords(value: unknown): JsonRecord[] {
  if (Array.isArray(value)) return value.filter((item): item is JsonRecord => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
  const record = asRecord(value)
  if (!record || !Object.keys(record).length) return []
  return [record]
}

function normalizeKlineBars(value: unknown): KlineBar[] {
  return normalizeRecords(value).flatMap((row) => {
    const date = String(first(row, 'date', '日期', '交易日期') || '').trim()
    const open = asNumber(first(row, 'open', '开盘', '开盘价'))
    const high = asNumber(first(row, 'high', '最高', '最高价'))
    const low = asNumber(first(row, 'low', '最低', '最低价'))
    const close = asNumber(first(row, 'close', '收盘', '收盘价'))
    if (!date || open === null || high === null || low === null || close === null) return []
    return [{
      date,
      open,
      high,
      low,
      close,
      volume: asNumber(first(row, 'volume', '成交量')) || 0,
      amount: asNumber(first(row, 'amount', '成交额')),
      turnover: asNumber(first(row, 'turnover', '换手率')),
    }]
  })
}

function asRecord(value: unknown): JsonRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : null
}

function first(record: JsonRecord | null | undefined, ...keys: string[]): unknown {
  if (!record) return null
  for (const key of keys) if (record[key] !== undefined && record[key] !== null && record[key] !== '') return record[key]
  return null
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const parsed = Number(value)
  return value !== null && value !== '' && Number.isFinite(parsed) ? parsed : null
}

function formatPrice(value: unknown): string {
  const number = asNumber(value)
  return number === null ? '—' : `¥${number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 3 })}`
}

function formatRatio(value: unknown): string {
  const number = asNumber(value)
  return number === null ? '—' : number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function formatPercent(value: number | null): string {
  return value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatTurnover(value: unknown): string {
  const number = asNumber(value)
  if (number === null) return '—'
  const percentage = Math.abs(number) <= 1 ? number * 100 : number
  return `${percentage.toFixed(2)}%`
}

function formatCompact(value: unknown, unit: string): string {
  const number = asNumber(value)
  if (number === null) return '—'
  const absolute = Math.abs(number)
  if (absolute >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿${unit}`
  if (absolute >= 10_000) return `${(number / 10_000).toFixed(2)} 万${unit}`
  return `${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${unit}`
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) {
    if (!value.length) return '暂无数据'
    const preview = value.slice(0, 4).map((item) => formatNestedValue(item, 1)).filter(Boolean)
    return `${preview.join('；')}${value.length > preview.length ? `；另有 ${value.length - preview.length} 项` : ''}`
  }
  if (typeof value === 'object') {
    const record = asRecord(value)
    if (record && record.value !== undefined) {
      const primary = formatValue(record.value)
      const yoy = asNumber(record.yoy)
      return yoy === null ? primary : `${primary} · 同比 ${formatPercent(yoy * 100)}`
    }
    return formatNestedValue(value, 0)
  }
  const text = String(value)
  if (isTechnicalErrorMessage(text)) return '数据暂不可用，请稍后刷新'
  return VALUE_LABELS[text.toLowerCase()] || text
}

function formatNestedValue(value: unknown, depth: number): string {
  if (value === null || value === undefined || value === '') return '—'
  if (depth >= 2) return Array.isArray(value) ? `${value.length} 项明细` : '结构化明细'
  if (Array.isArray(value)) {
    if (!value.length) return '暂无数据'
    return value.slice(0, 3).map((item) => formatNestedValue(item, depth + 1)).join('；') + (value.length > 3 ? `；另有 ${value.length - 3} 项` : '')
  }
  const record = asRecord(value)
  if (record) {
    const entries = Object.entries(record).filter(([, item]) => item !== null && item !== undefined && item !== '').slice(0, 5)
    if (!entries.length) return '暂无数据'
    return entries.map(([key, item]) => `${fieldLabel(key)}：${formatNestedValue(item, depth + 1)}`).join(' · ')
  }
  if (typeof value === 'number') return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  if (typeof value === 'boolean') return value ? '是' : '否'
  const text = String(value)
  if (isTechnicalErrorMessage(text)) return '数据暂不可用，请稍后刷新'
  return VALUE_LABELS[text.toLowerCase()] || text
}

const VALUE_LABELS: Record<string, string> = {
  available: '数据可用', partial: '部分可用', degraded: '数据降级', data_gap: '数据缺口', assumption_only: '仅有假设',
  no_data: '暂无记录', provider_unavailable: '数据源不可用', rate_limited: '请求受限', parse_failed: '解析失败',
  qfq: '前复权', hfq: '后复权', true: '是', false: '否',
}

const FIELD_LABELS: Record<string, string> = {
  symbol: '股票代码', normalized_symbol: '市场代码', code: '代码', name: '名称', title: '标题', type: '类型', keyword: '关键词',
  date: '日期', time: '时间', publish_time: '发布时间', report_date: '报告日期', start_date: '起始日期', end_date: '截止日期',
  open: '开盘价', high: '最高价', low: '最低价', close: '收盘价', pre_close: '前收盘价', latest_price: '最新价', pct_change: '涨跌幅', change: '涨跌额',
  volume: '成交量', amount: '成交额', turnover: '换手率', outstanding_share: '流通股本', adjust: '复权方式', bar_count: '交易日数量', latest_bar: '最新交易日', bars: '历史行情',
  pe_ttm: '市盈率（TTM）', pe_static: '静态市盈率', pb: '市净率', peg: '市盈增长比', pcf: '市现率', ps: '市销率',
  market_cap: '总市值', float_market_cap: '流通市值', total_shares: '总股本', float_shares: '流通股本', valuation_date: '估值日期', valuation: '估值明细',
  spot: '实时行情', spot_found: '实时行情可用', valuation_found: '估值数据可用',
  value: '数值', yoy: '同比变化', ratio: '比例', rank: '排名', score: '评分', count: '数量', total: '合计',
  availability: '数据状态', data_gap: '数据缺口', degrade_reason: '降级原因', error: '错误说明', note: '数据说明',
  provider_id: '数据提供方', vendor: '数据供应商', source_id: '来源标识', source: '数据来源', skill: '采集能力', from_cache: '使用缓存', source_confidence: '来源置信度',
  url: '详情链接', link: '详情链接', industry: '所属行业', concept: '所属概念', category: '分类', description: '说明', content: '内容',
  main_net_inflow: '主力净流入', net_inflow: '净流入', buy_amount: '买入金额', sell_amount: '卖出金额', buy_ratio: '买入占比', sell_ratio: '卖出占比',
  basic_eps: '基本每股收益', diluted_eps: '稀释每股收益', assets_debt_ratio: '资产负债率', business_cycle: '营业周期', calc_per_net_assets: '每股净资产',
  operating_income_total: '营业总收入', parent_holder_net_profit: '归母净利润', sale_gross_margin: '销售毛利率', sale_net_interest_ratio: '销售净利率',
  current_ratio: '流动比率', quick_ratio: '速动比率', conservative_quick_ratio: '保守速动比率', equity_ratio: '股东权益比率',
  accounts_payable: '应付账款', accounts_receivable: '应收账款', accrued_wages: '应付职工薪酬', assets_total: '资产总额', capital_reserve: '资本公积',
  act_cash_flow_net: '经营活动现金流净额', act_cash_inflow_total: '经营活动现金流入合计', act_cash_outflow_total: '经营活动现金流出合计',
  cash_net_addition: '现金净增加额', financing_cash_flow_net: '筹资活动现金流净额', invest_cash_flow_net: '投资活动现金流净额',
  common_profit_total: '综合收益总额', net_profit: '净利润', profit_total: '利润总额', operating_profit: '营业利润', operating_income: '营业收入',
}

const FIELD_WORDS: Record<string, string> = {
  latest: '最新', recent: '近期', main: '主力', net: '净额', inflow: '流入', outflow: '流出', flow: '资金流', hot: '热度', news: '新闻',
  buy: '买入', sell: '卖出', price: '价格', avg: '平均', average: '平均', max: '最高', min: '最低', current: '当前', previous: '前期',
  company: '公司', stock: '股票', security: '证券', market: '市场', financial: '财务', revenue: '营收', profit: '利润', cash: '现金', debt: '负债',
  date: '日期', time: '时间', year: '年度', quarter: '季度', rate: '比率', ratio: '占比', amount: '金额', volume: '数量', total: '合计', count: '数量',
  id: '标识', status: '状态', source: '来源', provider: '提供方', data: '数据', detail: '明细', info: '资料', metric: '指标', value: '数值',
  calculate: '', calc: '', index: '', basic: '基本', diluted: '稀释', eps: '每股收益', assets: '资产', asset: '资产', equity: '权益',
  business: '营业', cycle: '周期', per: '每股', operating: '经营', activity: '活动', income: '收入', cost: '成本', costs: '成本',
  parent: '归属母公司', holder: '股东', deduct: '扣非', yoy: '同比', growth: '增长', full: '全面摊薄', weighted: '加权平均', roe: '净资产收益率',
  inventory: '存货', days: '天数', receive: '应收', accounts: '账款', sale: '销售', gross: '毛利', margin: '率', interest: '利息',
  conservative: '保守', quick: '速动', common: '综合', continuing: '持续经营', minority: '少数股东', comprehensive: '综合收益',
  payable: '应付', receivable: '应收', accrued: '应计', wages: '职工薪酬', reserve: '准备金', borrow: '借款', borrowing: '借款',
  construction: '在建工程', contract: '合同', deferred: '递延', tax: '税费', fixed: '固定', intangible: '无形', goodwill: '商誉',
  long: '长期', term: '期', short: '短期', loans: '借款', loan: '借款', other: '其他', capital: '资本', surplus: '盈余', undistributed: '未分配',
  act: '经营活动', invest: '投资', financing: '筹资活动', addition: '增加额', ending: '期末',
  equivalents: '等价物', amortization: '摊销', impairment: '减值', depreciation: '折旧', employees: '职工', payments: '支付', received: '收到',
  expense: '费用', expenses: '费用', fee: '费用', manage: '管理', sales: '销售', research: '研发', development: '开发', taxes: '税费', surcharges: '附加',
  associate: '联营企业', disposal: '处置', fair: '公允价值', changes: '变动', exchange: '汇兑', commission: '佣金', commissions: '佣金',
}

function fieldLabel(key: string): string {
  const normalized = String(key || '').trim()
  if (!normalized) return '数据项'
  const direct = FIELD_LABELS[normalized] || FIELD_LABELS[normalized.toLowerCase()]
  if (direct) return direct
  if (Array.from(normalized).some((character) => character.charCodeAt(0) > 127)) return normalized
  const words = normalized.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase().split(/[_\s.-]+/).filter(Boolean)
  const translated = words.map((word) => FIELD_WORDS[word] || '').filter(Boolean)
  return translated.length ? translated.join('') : '扩展指标'
}

function formatDate(value: unknown): string {
  const text = formatValue(value)
  return text === '—' ? text : text.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function shortDate(value: string): string {
  const normalized = formatDate(value)
  const match = normalized.match(/^(\d{4})-(\d{2})-(\d{2})/)
  return match ? `${match[2]}-${match[3]}` : normalized
}

function adjustLabel(value: unknown): string {
  const normalized = String(value || '').toLowerCase()
  if (normalized === 'qfq') return '前复权'
  if (normalized === 'hfq') return '后复权'
  return '不复权'
}

function hasData(value: unknown): boolean {
  return normalizeRecords(value).length > 0
}

function isBankSecurity(info: unknown, name: string): boolean {
  if (name.includes('银行')) return true
  const record = asRecord(info)
  if (!record) return false
  return Object.entries(record).some(([key, value]) => {
    const normalizedKey = key.toLowerCase()
    return ['行业', '所属行业', 'industry', '主营业务'].some((label) => normalizedKey.includes(label.toLowerCase()))
      && String(value || '').includes('银行')
  })
}

function tagLabel(value: unknown): string {
  const record = asRecord(value)
  if (!record) return formatValue(value)
  const preferred = first(record, '关键词', '概念名称', '热词', 'keyword', '名称', '新闻标题')
  if (preferred !== null) return formatValue(preferred)
  const fallback = Object.values(record).find((item) => {
    const text = String(item || '')
    return text && !/^\d{4}[-/]\d{2}[-/]\d{2}/.test(text) && !/^[A-Z]{0,2}\d{6}$/.test(text)
  })
  return formatValue(fallback)
}

function qualityLabel(value: string): string {
  return ({ available: '数据可用', partial: '部分可用', degraded: '数据降级', data_gap: '数据缺口', assumption_only: '仅假设', no_data: '暂无记录', provider_unavailable: '数据源不可用', rate_limited: '请求受限', parse_failed: '解析失败' } as Record<string, string>)[value] || value
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof CognitradeApiError) return `${error.message}${error.requestId ? ` · request ${error.requestId}` : ''}`
  return error instanceof Error ? error.message : String(error)
}
