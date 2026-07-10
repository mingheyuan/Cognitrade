import { ACTIVE_RUN_STATUSES, INTENT_LABELS } from './data'
import type {
  EvidenceRecord,
  ResearchReport,
  RunDetail,
  RunEvent,
  RunHistoryRecord,
  RunStatus,
  RunStep,
} from './research-contracts'

export type RunListItem = {
  id: string
  title: string
  summary: string
  intent: string
  intentLabel: string
  status: string
  statusLabel: string
  time: string
  childThreadCount: number
}

export type EvidenceItem = {
  id: string
  source: string
  title: string
  meta: string
  quality: number
  type: 'market' | 'filing' | 'methodology' | 'news'
}

export type CandidateItem = {
  rank: number
  code: string
  name: string
  sector: string
  score: number | null
  change: number | null
  valuation: string
  cashflow: number | null
  momentum: number | null
  tier: string
  reason: string
  risk: string
  deepChecked: boolean
}

const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

export function asString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}

export function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

export function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => asString(item)).filter(Boolean)
  const text = asString(value)
  return text ? [text] : []
}

export function reportFromDetail(detail?: RunDetail | null): ResearchReport {
  return asRecord(detail?.replay.result?.report) as ResearchReport
}

export function queryFromDetail(detail?: RunDetail | null): string {
  return asString(detail?.replay.request?.user_query)
}

export function intentFromDetail(detail?: RunDetail | null): string {
  return asString(detail?.replay.result?.intent || detail?.replay.request?.intent || detail?.steps.intent)
}

export function statusFromDetail(detail?: RunDetail | null): string {
  return asString(detail?.replay.summary?.status || detail?.metrics.summary?.status, 'unknown')
}

export function isActiveStatus(status?: string | null): boolean {
  return ACTIVE_RUN_STATUSES.has(String(status || ''))
}

export function statusLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    received: '已接收',
    identifying_intent: '识别意图',
    needs_clarification: '待补充',
    ready: '已就绪',
    running: '运行中',
    waiting_external: '等待外部',
    gated: '质量门禁',
    completed: '已完成',
    cancelled: '已取消',
    failed: '失败',
    timed_out: '超时',
    unknown: '未知',
  }
  return labels[String(status || 'unknown')] || String(status || '未知')
}

export function runListItem(row: RunHistoryRecord): RunListItem {
  const intent = asString(row.intent, 'unknown')
  const status = asString(row.status, 'unknown')
  const summary = asString(row.display_summary || row.message, '暂无运行摘要')
  return {
    id: row.run_id,
    title: summary,
    summary,
    intent,
    intentLabel: INTENT_LABELS[intent] || intent,
    status,
    statusLabel: statusLabel(status),
    time: formatRunTime(row),
    childThreadCount: asNumber(row.child_thread_count) || 0,
  }
}

export function evidenceFromReport(report: ResearchReport): EvidenceItem[] {
  const rows = Array.isArray(report.evidence) ? report.evidence : []
  return rows.map((row, index) => evidenceItem(row, index))
}

function evidenceItem(row: EvidenceRecord, index: number): EvidenceItem {
  const metadata = asRecord(row.metadata)
  const sourceType = asString(row.source_type || metadata.source_type, 'evidence')
  const source = asString(row.source_name || metadata.source_name || sourceType, '研究证据')
  const qualityValue = asNumber(row.quality ?? row.confidence ?? metadata.confidence)
  const quality = qualityValue === null ? 0 : Math.round(qualityValue <= 1 ? qualityValue * 100 : qualityValue)
  return {
    id: asString(row.evidence_id || row.id, `E${String(index + 1).padStart(2, '0')}`),
    source,
    title: asString(row.title || row.summary, '未命名证据'),
    meta: [asString(row.published_at), sourceType].filter(Boolean).join(' · ') || '后端未提供时间',
    quality: Math.max(0, Math.min(100, quality)),
    type: evidenceType(sourceType),
  }
}

function evidenceType(sourceType: string): EvidenceItem['type'] {
  const normalized = sourceType.toLowerCase()
  if (normalized.includes('market') || normalized.includes('price')) return 'market'
  if (normalized.includes('filing') || normalized.includes('notice') || normalized.includes('fundamental')) return 'filing'
  if (normalized.includes('method') || normalized.includes('valuation')) return 'methodology'
  return 'news'
}

export function candidatesFromReport(report: ResearchReport): CandidateItem[] {
  const candidates = Array.isArray(report.candidates) ? report.candidates : []
  return candidates.map((raw, index) => {
    const row = asRecord(raw)
    const code = asString(row.symbol || row.code || row.ticker, `candidate-${index + 1}`)
    const score = asNumber(row.score ?? row.composite_score ?? row.total_score)
    const change = asNumber(row.latest_pct_change ?? row.change_pct ?? row.change)
    const cashflow = asNumber(row.cashflow_score ?? row.cash_flow_score)
    const momentum = asNumber(row.momentum_score ?? row.trend_score)
    const reason = asString(row.why || row.reason || row.summary, '后端未提供候选理由')
    const deepChecked = Boolean(row.deep_checked ?? row.verified)
    return {
      rank: asNumber(row.rank) || index + 1,
      code,
      name: asString(row.name || row.company_name, code),
      sector: asString(row.sector || row.industry, '行业未提供'),
      score,
      change,
      valuation: asString(row.valuation || row.valuation_state, '未提供'),
      cashflow,
      momentum,
      tier: asString(row.tier, deepChecked ? '深度核验' : '候选'),
      reason,
      risk: asString(row.risk, '未提供'),
      deepChecked,
    }
  })
}

export function reportRisks(report: ResearchReport): string[] {
  return asStringList(report.risk)
}

export function reportFollowUps(report: ResearchReport): string[] {
  return asStringList(report.follow_up)
}

export function latestEvent(events: RunEvent[]): RunEvent | undefined {
  return events.length ? events[events.length - 1] : undefined
}

export function stepProgress(steps: RunStep[]): number {
  if (!steps.length) return 0
  const complete = steps.filter((step) => ['completed', 'skipped'].includes(asString(step.status))).length
  return Math.round((complete / steps.length) * 100)
}

export function formatDate(value?: string | null): string {
  if (!value) return '时间未提供'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function formatRunTime(row: RunHistoryRecord): string {
  const explicit = asString(row.updated_at || row.created_at)
  if (explicit) return formatDate(explicit)
  const decoded = dateFromUlid(row.run_id)
  return decoded ? formatDate(decoded.toISOString()) : '时间未提供'
}

function dateFromUlid(value: string): Date | null {
  const prefix = value.slice(0, 10).toUpperCase()
  if (prefix.length !== 10) return null
  let timestamp = 0
  for (const character of prefix) {
    const digit = CROCKFORD.indexOf(character)
    if (digit < 0) return null
    timestamp = timestamp * 32 + digit
  }
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? null : date
}

export function statusTone(status?: RunStatus | string): 'complete' | 'running' | 'review' {
  if (status === 'completed') return 'complete'
  if (isActiveStatus(status)) return 'running'
  return 'review'
}
