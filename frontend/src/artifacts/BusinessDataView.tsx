import { AlertCircle, CheckCircle2, LineChart as LineChartIcon } from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  businessFieldLabel,
  businessValueLabel,
  dataGapLabel,
  isSensitiveBusinessKey,
} from './labels'
import { localizeErrorMessage } from '../utils/error-message'
import { asRecord } from '../utils/value'

type BusinessDataViewProps = {
  value: unknown
  fieldKey?: string
  path?: string
  compact?: boolean
}

type HistoryRange = '1m' | '3m' | '6m' | '1y' | '3y' | 'all'

const RANGE_OPTIONS: Array<{ id: HistoryRange; label: string; days: number | null }> = [
  { id: '1m', label: '近 1 月', days: 31 },
  { id: '3m', label: '近 3 月', days: 93 },
  { id: '6m', label: '近 6 月', days: 186 },
  { id: '1y', label: '近 1 年', days: 366 },
  { id: '3y', label: '近 3 年', days: 1096 },
  { id: 'all', label: '全部', days: null },
]

const HISTORY_FIELD_MARKERS = [
  'history', 'kline', 'curve', 'trend', 'time_series', 'price_path', 'debt_path',
]
const DATE_KEYS = ['date', 'datetime', 'timestamp', 'time', 'report_date', 'period_end', 'year']
const CHART_COLORS = ['#70e0bd', '#e6bb63', '#8d7ff0', '#6fb6ff', '#ef8a8a', '#78d27d', '#dc8feb', '#a9b7c0']

function isPrimitive(value: unknown): boolean {
  return value == null || ['string', 'number', 'boolean'].includes(typeof value)
}

function safeEntries(value: Record<string, unknown>): Array<[string, unknown]> {
  return Object.entries(value).filter(([key]) => !isSensitiveBusinessKey(key))
}

function numberValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value.replaceAll(',', '').replace('%', ''))
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function scalarText(value: unknown, key = ''): string {
  if (value == null || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    const formatted = value.toLocaleString('zh-CN', { maximumFractionDigits: 6 })
    const normalized = key.toLowerCase()
    return normalized.includes('pct') || normalized.endsWith('_rate') ? `${formatted}%` : formatted
  }
  const text = String(value)
  if (key === 'error_type') {
    return ({
      RemoteDisconnected: '上游数据源主动断开连接',
      ConnectionError: '上游数据源连接失败',
      Timeout: '上游数据源响应超时',
      ReadTimeout: '上游数据源读取超时',
      HTTPError: '上游数据源返回 HTTP 错误',
    } as Record<string, string>)[text] || text
  }
  if (
    /(^|_)(error|errors|exception|failure)(_|$)/i.test(key)
    || ['degrade_reason', 'diagnostic', 'diagnostics', 'stderr', 'stdout'].includes(key.toLowerCase())
  ) {
    return localizeErrorMessage(text, '处理过程中发生错误，请稍后重试。')
  }
  if (key === 'data_gap' || key === 'risk_flags') return dataGapLabel(text)
  return businessValueLabel(text)
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function PrimitiveValue({ value, fieldKey = '' }: { value: unknown; fieldKey?: string }) {
  const text = scalarText(value, fieldKey)
  if (typeof value === 'string' && isHttpUrl(value)) {
    return <a className="artifact-data-link" href={value} target="_blank" rel="noreferrer">{text}</a>
  }
  return <span>{text}</span>
}

function historyDescriptor(path: string, rows: Array<Record<string, unknown>>) {
  const normalizedPath = path.toLowerCase()
  if (!HISTORY_FIELD_MARKERS.some((marker) => normalizedPath.includes(marker))) return null
  if (rows.length < 2) return null
  const dateKey = DATE_KEYS.find((key) => rows.some((row) => row[key] != null))
  if (!dateKey) return null
  const numericKeys = Array.from(new Set(rows.flatMap((row) => Object.keys(row))))
    .filter((key) => key !== dateKey && rows.some((row) => numberValue(row[key]) != null))
  return numericKeys.length ? { dateKey, numericKeys } : null
}

function dateMillis(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const parsed = Date.parse(String(value || ''))
  return Number.isFinite(parsed) ? parsed : null
}

function BusinessTimeSeries({ rows, path }: { rows: Array<Record<string, unknown>>; path: string }) {
  const descriptor = historyDescriptor(path, rows)
  const [range, setRange] = useState<HistoryRange>('all')
  const filteredRows = useMemo(() => {
    if (!descriptor || range === 'all') return rows
    const latest = Math.max(...rows.map((row) => dateMillis(row[descriptor.dateKey]) ?? Number.NEGATIVE_INFINITY))
    const option = RANGE_OPTIONS.find((item) => item.id === range)
    if (!Number.isFinite(latest) || !option?.days) return rows
    const cutoff = latest - option.days * 86_400_000
    return rows.filter((row) => (dateMillis(row[descriptor.dateKey]) ?? latest) >= cutoff)
  }, [descriptor, range, rows])
  if (!descriptor) return null

  const first = filteredRows[0]
  const last = filteredRows.at(-1)
  return (
    <div className="artifact-history-view">
      <div className="artifact-history-toolbar">
        <div><LineChartIcon size={15} /><span>完整历史趋势</span><small>{rows.length} 个观测点</small></div>
        <div role="group" aria-label="历史趋势区间">
          {RANGE_OPTIONS.map((option) => (
            <button key={option.id} type="button" className={range === option.id ? 'is-active' : ''} onClick={() => setRange(option.id)}>{option.label}</button>
          ))}
        </div>
      </div>
      <div className="artifact-history-chart" role="img" aria-label={`${businessFieldLabel(path.split('.').at(-1) || path)}完整历史趋势`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={filteredRows} margin={{ top: 12, right: 18, bottom: 2, left: 0 }}>
            <CartesianGrid stroke="rgba(126, 160, 148, .12)" vertical={false} />
            <XAxis dataKey={descriptor.dateKey} minTickGap={42} tick={{ fill: '#78948a', fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: '#78948a', fontSize: 10 }} axisLine={false} tickLine={false} width={58} domain={['auto', 'auto']} />
            <Tooltip contentStyle={{ background: '#101c19', border: '1px solid rgba(148,180,169,.18)', borderRadius: 8, color: '#eaf4f0', fontSize: 11 }} />
            <Legend wrapperStyle={{ fontSize: 10, color: '#8ca69d' }} />
            {descriptor.numericKeys.map((key, index) => (
              <Line key={key} dataKey={key} name={businessFieldLabel(key)} type="monotone" stroke={CHART_COLORS[index % CHART_COLORS.length]} strokeWidth={1.6} dot={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="artifact-history-summary">
        <span><small>覆盖起点</small><strong>{scalarText(first?.[descriptor.dateKey])}</strong></span>
        <span><small>覆盖终点</small><strong>{scalarText(last?.[descriptor.dateKey])}</strong></span>
        <span><small>显示点数</small><strong>{filteredRows.length}</strong></span>
        <span><small>完整点数</small><strong>{rows.length}</strong></span>
      </div>
    </div>
  )
}

function PrimitiveList({ values, fieldKey }: { values: unknown[]; fieldKey: string }) {
  if (!values.length) return <div className="artifact-empty-value">后端返回空列表</div>
  const isGap = fieldKey === 'data_gap' || fieldKey === 'risk_flags' || fieldKey.includes('missing')
  return (
    <ul className={`artifact-value-list ${isGap ? 'is-warning' : ''}`}>
      {values.map((value, index) => (
        <li key={`${String(value)}-${index}`}>{isGap ? <AlertCircle size={12} /> : <CheckCircle2 size={12} />}<PrimitiveValue value={value} fieldKey={fieldKey} /></li>
      ))}
    </ul>
  )
}

function ObjectTable({ rows, path }: { rows: Array<Record<string, unknown>>; path: string }) {
  const columns = Array.from(new Set(rows.flatMap((row) => safeEntries(row).map(([key]) => key))))
  if (!rows.length) return <div className="artifact-empty-value">后端返回空列表</div>
  const nested = rows.some((row) => columns.some((column) => !isPrimitive(row[column])))
  if (nested) {
    return (
      <div className="artifact-record-list">
        {rows.map((row, index) => (
          <article key={`${path}-${index}`}>
            <span className="artifact-record-index">{String(index + 1).padStart(2, '0')}</span>
            <BusinessDataView value={row} path={`${path}[]`} compact />
          </article>
        ))}
      </div>
    )
  }
  return (
    <div className="artifact-table-wrap">
      <table className="artifact-data-table">
        <thead><tr>{columns.map((column) => <th key={column}>{businessFieldLabel(column)}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${path}-${index}`}>{columns.map((column) => <td key={column}><PrimitiveValue value={row[column]} fieldKey={column} /></td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function BusinessDataView({ value, fieldKey = '', path = fieldKey || 'artifact', compact = false }: BusinessDataViewProps) {
  if (isPrimitive(value)) return <PrimitiveValue value={value} fieldKey={fieldKey} />
  if (Array.isArray(value)) {
    if (!value.length) return <div className="artifact-empty-value">后端返回空列表</div>
    if (value.every(isPrimitive)) return <PrimitiveList values={value} fieldKey={fieldKey} />
    const rows = value.map(asRecord)
    const history = historyDescriptor(path, rows)
    return history ? <BusinessTimeSeries rows={rows} path={path} /> : <ObjectTable rows={rows} path={path} />
  }

  const entries = safeEntries(asRecord(value))
  if (!entries.length) return <div className="artifact-empty-value">后端返回空对象</div>
  const primitiveEntries = entries.filter(([, item]) => isPrimitive(item))
  const nestedEntries = entries.filter(([, item]) => !isPrimitive(item))
  return (
    <div className={`artifact-structured-view ${compact ? 'is-compact' : ''}`}>
      {primitiveEntries.length > 0 && (
        <dl className="artifact-definition-grid">
          {primitiveEntries.map(([key, item]) => (
            <div key={`${path}.${key}`}><dt>{businessFieldLabel(key)}</dt><dd><PrimitiveValue value={item} fieldKey={key} /></dd></div>
          ))}
        </dl>
      )}
      {nestedEntries.map(([key, item]) => (
        <section className="artifact-data-section" key={`${path}.${key}`}>
          <header><h4>{businessFieldLabel(key)}</h4>{Array.isArray(item) && <span>{item.length} 项</span>}</header>
          <BusinessDataView value={item} fieldKey={key} path={`${path}.${key}`} compact={compact} />
        </section>
      ))}
    </div>
  )
}
