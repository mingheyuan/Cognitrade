import { AlertTriangle, CheckCircle2, LoaderCircle, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import type { JsonValue } from './types'
import type { StatusTone } from './status'

export function StatusBadge({ label, tone = 'muted' }: { label: string; tone?: StatusTone }) {
  return <span className={`mdc-status mdc-status--${tone}`}>{label}</span>
}
export function LoadingState({ label = '正在读取数据库…' }: { label?: string }) {
  return (
    <div className="mdc-panel-state" role="status" aria-live="polite">
      <LoaderCircle className="is-spinning" size={20} aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return (
    <div className="mdc-panel-state mdc-panel-state--empty">
      <XCircle size={20} aria-hidden="true" />
      <strong>{title}</strong>
      <span>{detail}</span>
      {action}
    </div>
  )
}

export function InlineMessage({ tone, children }: { tone: 'success' | 'warning' | 'error'; children: ReactNode }) {
  const Icon = tone === 'success' ? CheckCircle2 : AlertTriangle
  return (
    <div className={`mdc-inline-message mdc-inline-message--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Icon size={16} aria-hidden="true" />
      <span>{children}</span>
    </div>
  )
}

export function DefinitionList({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="mdc-definition-list">
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value || '—'}</dd>
        </div>
      ))}
    </dl>
  )
}

export function JsonTree({ value, empty = '无结构化数据' }: { value?: JsonValue | Record<string, JsonValue>; empty?: string }) {
  if (value == null || (typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length)) {
    return <span className="mdc-json-empty">{empty}</span>
  }
  return <JsonNode value={value} depth={0} />
}

function JsonNode({ value, depth }: { value: JsonValue | Record<string, JsonValue>; depth: number }) {
  if (value == null) return <span className="mdc-json-null">null</span>
  if (typeof value === 'boolean') return <span className="mdc-json-boolean">{String(value)}</span>
  if (typeof value === 'number') return <span className="mdc-json-number">{String(value)}</span>
  if (typeof value === 'string') return <span className="mdc-json-string">{value}</span>
  const entries = Array.isArray(value) ? value.map((item, index) => [String(index), item] as const) : Object.entries(value)
  if (!entries.length) return <span className="mdc-json-empty">{Array.isArray(value) ? '[]' : '{}'}</span>
  return (
    <div className={`mdc-json-tree ${depth ? 'is-nested' : ''}`}>
      {entries.map(([key, child]) => (
        <div className="mdc-json-row" key={key}>
          <span className="mdc-json-key">{key}</span>
          <div><JsonNode value={child} depth={depth + 1} /></div>
        </div>
      ))}
    </div>
  )
}
