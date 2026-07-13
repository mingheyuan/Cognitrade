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
import { type Dispatch, type FormEvent, type ReactNode, type SetStateAction, useMemo } from 'react'
import { CognitradeApiClient, CognitradeApiError } from '../api-client'
import { useCognitrade } from '../CognitradeContext'
import type { JsonRecord, MarketDataResponse } from '../finance-contracts'

const EXAMPLES = [
  { symbol: '600519', label: '贵州茅台' },
  { symbol: '600036', label: '招商银行' },
  { symbol: '300750', label: '宁德时代' },
]

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
          <Metric label="PE · TTM" value={formatRatio(first(overview, 'pe_ttm'))} />
          <Metric label="PE · 静态" value={formatRatio(first(overview, 'pe_static'))} />
          <Metric label="PB" value={formatRatio(first(overview, 'pb'))} />
          <Metric label="PEG" value={formatRatio(first(overview, 'peg'))} />
          <Metric label="PCF" value={formatRatio(first(overview, 'pcf'))} />
          <Metric label="PS" value={formatRatio(first(overview, 'ps'))} />
          <Metric label="总市值" value={formatCompact(first(overview, 'market_cap'), '元')} />
          <Metric label="流通市值" value={formatCompact(first(overview, 'float_market_cap'), '元')} />
          <Metric label="估值日期" value={formatValue(first(overview, 'valuation_date'))} />
        </div>
      </DataSection>

      <DataSection icon={<BarChart3 size={18} />} kicker="SINGLE-DAY KLINE" title="K 线摘要" quality={data.quality.kline}>
        <div className="kline-summary-layout">
          <SingleCandle bar={latestBar} />
          <div className="kline-meta-grid">
            <Metric label="数据起始" value={formatDate(first(kline, 'start_date'))} />
            <Metric label="数据截止" value={formatDate(first(kline, 'end_date'))} />
            <Metric label="复权方式" value={adjustLabel(first(kline, 'adjust'))} />
            <Metric label="K 线条数" value={formatValue(first(kline, 'bar_count'))} />
            <Metric label="最后交易日" value={formatDate(first(latestBar, 'date'))} />
            <Metric label="换手率" value={formatTurnover(first(latestBar, 'turnover', '换手率'))} />
          </div>
          <div className="ohlc-strip" aria-label="最后交易日 OHLC">
            {(['open', 'high', 'low', 'close'] as const).map((key) => (
              <div key={key}><span>{key.toUpperCase()}</span><strong>{formatPrice(latestBar[key])}</strong></div>
            ))}
            <div><span>VOL</span><strong>{formatCompact(first(latestBar, 'volume'), '股')}</strong></div>
            <div><span>AMT</span><strong>{formatCompact(first(latestBar, 'amount'), '元')}</strong></div>
          </div>
          <p className="data-disclaimer">按需求仅展示最后一个交易日蜡烛与 OHLC 摘要，不生成历史价格趋势。</p>
        </div>
      </DataSection>

      <DataSection icon={<Building2 size={18} />} kicker="FUNDAMENTALS" title="基本面与财务" quality={data.quality.fundamental}>
        <div className="record-panel-grid">
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
      </DataSection>

      <div className="finance-two-column">
        <DataSection icon={<Activity size={18} />} kicker="CAPITAL FLOW" title="资金流与主力排名" quality={data.quality.capital_flow}>
          <RecordPanel title="最新资金流" value={flow.latest_flow} />
          <RecordPanel title="最近资金流" value={flow.recent_flows} wide />
          <RecordPanel title="主力资金排名" value={flow.main_flow_rank} wide />
        </DataSection>
        <DataSection icon={<TrendingUp size={18} />} kicker="SENTIMENT" title="热度与新闻样本" quality={data.quality.sentiment}>
          <RecordPanel title="热度排名趋势" value={sentiment.hot_rank_detail || sentiment.hot_rank_latest} wide />
          <TagList title="热门关键词" value={sentiment.hot_keywords} />
          <RecordPanel title="新闻样本" value={sentiment.news_samples} wide />
        </DataSection>
      </div>

      <div className="finance-two-column">
        <DataSection icon={<Newspaper size={18} />} kicker="DISCLOSURES" title="最近 30 天公告" quality={data.quality.notice}>
          <div className="notice-count"><strong>{formatValue(notices.notice_count)}</strong><span>条公告</span><small>{formatDate(notices.window_start)} — {formatDate(notices.window_end)}</small></div>
          <RecordPanel title="公告列表" value={notices.recent_notices} wide />
        </DataSection>
        <DataSection icon={<Clock3 size={18} />} kicker="EVENT TIMELINE" title="公司事件" quality={data.quality.events}>
          <TagList title="事件关键词" value={events.event_keywords} />
          <Timeline value={events.event_notices} />
        </DataSection>
      </div>

      <DataSection icon={<Landmark size={18} />} kicker="DRAGON & TIGER LIST" title="龙虎榜" quality={data.quality.lhb}>
        <div className="lhb-summary">
          <Metric label="最近上榜" value={formatDate(lhb.latest_lhb_date)} />
          <Metric label="买入席位合计" value={formatCompact(lhb.buy_top_total, '元')} tone="up" />
          <Metric label="卖出席位合计" value={formatCompact(lhb.sell_top_total, '元')} tone="down" />
        </div>
        <div className="finance-two-column">
          <RecordPanel title="买入席位" value={lhb.buy_detail} wide />
          <RecordPanel title="卖出席位" value={lhb.sell_detail} wide />
        </div>
        <RecordPanel title="统计记录" value={lhb.lhb_statistics} wide />
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

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'up' | 'down' }) {
  return <div className={`finance-metric ${tone ? `finance-metric--${tone}` : ''}`}><span>{label}</span><strong>{value}</strong></div>
}

function SingleCandle({ bar }: { bar: JsonRecord }) {
  const open = asNumber(bar.open)
  const close = asNumber(bar.close)
  const high = asNumber(bar.high)
  const low = asNumber(bar.low)
  if ([open, close, high, low].some((value) => value === null)) {
    return <div className="single-candle single-candle--empty"><BarChart3 size={26} /><span>暂无单日蜡烛数据</span></div>
  }
  const maximum = Math.max(high!, open!, close!)
  const minimum = Math.min(low!, open!, close!)
  const range = maximum - minimum || 1
  const bodyTop = ((maximum - Math.max(open!, close!)) / range) * 100
  const bodyHeight = Math.max((Math.abs(close! - open!) / range) * 100, 8)
  const rising = close! >= open!
  return (
    <div className="single-candle">
      <div className={`candle-visual ${rising ? 'is-up' : 'is-down'}`} aria-label={`${rising ? '上涨' : '下跌'}单日蜡烛`}>
        <span className="candle-wick" />
        <span className="candle-body" style={{ top: `${bodyTop}%`, height: `${bodyHeight}%` }} />
      </div>
      <div><span>单日方向</span><strong className={rising ? 'number-up' : 'number-down'}>{rising ? '收涨 / 平盘' : '收跌'}</strong><small>高 {formatPrice(high)} · 低 {formatPrice(low)}</small></div>
    </div>
  )
}

function RecordPanel({ title, value, wide = false }: { title: string; value: unknown; wide?: boolean }) {
  return <article className={`record-panel ${wide ? 'record-panel--wide' : ''}`}><h3>{title}</h3><RecordTable value={value} /></article>
}

function RecordTable({ value, limit = 12 }: { value: unknown; limit?: number }) {
  const records = normalizeRecords(value)
  if (!records.length) return <p className="data-empty">数据源暂未返回可展示记录</p>
  if (records.length === 1) {
    return <dl className="record-definition-list">{Object.entries(records[0]).slice(0, limit).map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{formatValue(item)}</dd></div>)}</dl>
  }
  const columns = Array.from(new Set(records.flatMap((row) => Object.keys(row)))).slice(0, 6)
  return (
    <div className="finance-table-scroll"><table className="finance-table"><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{records.slice(0, limit).map((row, index) => <tr key={`${index}-${columns.map((column) => String(row[column] ?? '')).join('-')}`}>{columns.map((column) => <td key={column}>{formatValue(row[column])}</td>)}</tr>)}</tbody></table></div>
  )
}

function TagList({ title, value }: { title: string; value: unknown }) {
  const tags = Array.isArray(value) ? value : []
  return <div className="tag-list"><h3>{title}</h3><div>{tags.length ? tags.slice(0, 16).map((tag, index) => <span key={`${tagLabel(tag)}-${index}`}>{tagLabel(tag)}</span>) : <p className="data-empty">暂无关键词</p>}</div></div>
}

function Timeline({ value }: { value: unknown }) {
  const records = normalizeRecords(value)
  if (!records.length) return <p className="data-empty">最近 30 天未返回已分类事件</p>
  return <ol className="event-timeline">{records.slice(0, 12).map((event, index) => <li key={`${index}-${formatValue(first(event, '公告日期', '公告时间', 'date', '时间'))}`}><span /><div><time>{formatDate(first(event, '公告日期', '公告时间', 'date', '时间'))}</time><strong>{formatValue(first(event, '公告标题', 'title', '名称'))}</strong><small>{formatValue(first(event, '事件类型', 'type', 'keyword', '关键词'))}</small></div></li>)}</ol>
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
  if (typeof value === 'object') {
    const record = asRecord(value)
    if (record && record.value !== undefined) {
      const primary = formatValue(record.value)
      const yoy = asNumber(record.yoy)
      return yoy === null ? primary : `${primary} · 同比 ${formatPercent(yoy * 100)}`
    }
    return JSON.stringify(value)
  }
  return String(value)
}

function formatDate(value: unknown): string {
  const text = formatValue(value)
  return text === '—' ? text : text.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')
}

function formatDateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('zh-CN', { hour12: false })
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
  return ({ available: '数据可用', partial: '部分可用', degraded: '数据降级', data_gap: '数据缺口', assumption_only: '仅假设' } as Record<string, string>)[value] || value
}

function apiErrorMessage(error: unknown): string {
  if (error instanceof CognitradeApiError) return `${error.message}${error.requestId ? ` · request ${error.requestId}` : ''}`
  return error instanceof Error ? error.message : String(error)
}
