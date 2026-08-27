import type { DimensionStatus, EvidenceGrade, FreshnessStatus } from './types'

export type StatusTone = 'good' | 'info' | 'warning' | 'danger' | 'muted'

const DIMENSION_STATUS: Record<DimensionStatus, { label: string; tone: StatusTone }> = {
  available: { label: '可用', tone: 'good' },
  verified_empty: { label: '经验证为空', tone: 'info' },
  data_gap: { label: '数据缺口', tone: 'warning' },
  conflict: { label: '待解决冲突', tone: 'danger' },
  revoked: { label: '已撤销', tone: 'danger' },
  unknown: { label: '未知', tone: 'muted' },
}
const FRESHNESS_STATUS: Record<FreshnessStatus, { label: string; tone: StatusTone }> = {
  fresh: { label: '新鲜', tone: 'good' },
  stale: { label: '已过期', tone: 'warning' },
  not_applicable: { label: '不适用', tone: 'muted' },
  unknown: { label: '时效未知', tone: 'muted' },
}

export function dimensionStatusMeta(status: string | null | undefined) {
  return DIMENSION_STATUS[(status || 'unknown') as DimensionStatus] || { label: status || '未知', tone: 'muted' as const }
}

export function nativeQualityMeta(quality: string | null | undefined, usable?: boolean) {
  const normalized = String(quality || '').toLowerCase()
  if (usable && ['partial', 'degraded'].includes(normalized)) return { label: 'UZI 部分可用', tone: 'warning' as const }
  if (usable) return { label: 'UZI 可用', tone: 'good' as const }
  if (['error', 'failed', 'parse_failed', 'provider_unavailable', 'rate_limited', 'timeout'].includes(normalized)) return { label: 'UZI 获取失败', tone: 'danger' as const }
  if (['missing', 'missing_result', 'no_data', 'data_gap', 'assumption_only'].includes(normalized)) return { label: 'UZI 数据缺口', tone: 'warning' as const }
  return { label: quality || 'UZI 未判定', tone: 'muted' as const }
}

export function freshnessStatusMeta(status: string | null | undefined) {
  return FRESHNESS_STATUS[(status || 'unknown') as FreshnessStatus] || { label: status || '未知', tone: 'muted' as const }
}

export function evidenceGradeLabel(grade: EvidenceGrade | undefined) {
  if (grade === 'A') return 'A级 · 截止前观测'
  if (grade === 'B') return 'B级 · 可证明历史可见'
  if (grade === 'C') return 'C级 · 仅可作排除评分的降级披露'
  return '无合格证据'
}

export function jobStatusMeta(status: string | null | undefined) {
  const normalized = String(status || 'unknown').toLowerCase()
  if (['completed', 'succeeded', 'published'].includes(normalized)) return { label: '已完成', tone: 'good' as const }
  if (['running', 'processing', 'leased'].includes(normalized)) return { label: '运行中', tone: 'info' as const }
  if (['queued', 'pending', 'draft'].includes(normalized)) return { label: '等待中', tone: 'muted' as const }
  if (['failed', 'cancelled', 'revoked', 'invalid'].includes(normalized)) return { label: normalized === 'cancelled' ? '已取消' : '失败', tone: 'danger' as const }
  if (['partial', 'completed_partial'].includes(normalized)) return { label: '部分完成', tone: 'warning' as const }
  return { label: status || '未知', tone: 'muted' as const }
}

export function isActiveCollectionJob(status: string | null | undefined) {
  return ['queued', 'pending', 'running', 'processing', 'leased'].includes(
    String(status || '').toLowerCase(),
  )
}

export function formatDateTime(value?: string | null) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(parsed)
}

export function formatDuration(milliseconds: number) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—'
  const totalSeconds = Math.round(milliseconds / 1000)
  if (totalSeconds < 60) return `${totalSeconds} 秒`
  const totalMinutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (totalMinutes < 60) return `${totalMinutes} 分${seconds ? ` ${seconds} 秒` : ''}`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return `${hours} 小时${minutes ? ` ${minutes} 分` : ''}`
}

export function collectionJobDurations(job: { created_at?: string | null; started_at?: string | null; completed_at?: string | null }) {
  const createdAt = job.created_at ? new Date(job.created_at).getTime() : Number.NaN
  const startedAt = job.started_at ? new Date(job.started_at).getTime() : Number.NaN
  const completedAt = job.completed_at ? new Date(job.completed_at).getTime() : Date.now()
  return {
    queue: Number.isFinite(createdAt) && Number.isFinite(startedAt) ? formatDuration(startedAt - createdAt) : '—',
    run: Number.isFinite(startedAt) ? formatDuration(completedAt - startedAt) : '—',
  }
}

export function formatFreshness(seconds?: number | null) {
  if (seconds == null) return '冻结后不限时效'
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} 小时`
  return `${Math.round(seconds / 86_400)} 天`
}

export function shortHash(value?: string | null) {
  if (!value) return '—'
  return value.length > 16 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value
}

export function progressValue(progress: { total: number; completed: number; failed: number; skipped: number }) {
  if (!progress.total) return 0
  return Math.min(100, Math.round(((progress.completed + progress.failed + progress.skipped) / progress.total) * 100))
}
