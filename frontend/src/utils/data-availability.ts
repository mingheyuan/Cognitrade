type JsonRecord = Record<string, unknown>

const UNAVAILABLE_STATES = new Set([
  'degraded',
  'data_gap',
  'assumption_only',
  'no_data',
  'provider_unavailable',
  'rate_limited',
  'parse_failed',
])

export function isTechnicalErrorMessage(value: string): boolean {
  return /(?:[a-z0-9]+_){1,}[a-z0-9]*unavailable|connection aborted|remote.?disconnected|provider_failure|traceback|(?:network|connection|read) timeout|http\s*\d{3}/i.test(value)
}

export function dataUnavailableMessage(value: unknown): string | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const record = value as JsonRecord
  const availability = String(record.availability || record.status || '').toLowerCase()
  const detail = String(record.message || record.note || record.error || record.degrade_reason || '')
  if (!UNAVAILABLE_STATES.has(availability) && !isTechnicalErrorMessage(detail)) return null
  if (availability === 'no_data') return '当前查询范围内没有相关记录'
  if (availability === 'rate_limited') return '数据源请求频率受限，请稍后刷新'
  if (availability === 'parse_failed') return '数据格式暂时无法解析，请稍后刷新'
  if (/individual_info_unavailable/i.test(detail)) return '公司资料暂不可用，请稍后刷新'
  return '该项数据暂不可用，请稍后刷新'
}
