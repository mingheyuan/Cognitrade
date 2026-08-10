const CHINESE_TEXT = /[\u3400-\u9fff]/

export function localizeErrorMessage(
  value: unknown,
  fallback = '操作失败，请稍后重试。',
): string {
  const message = value instanceof Error ? value.message.trim() : String(value || '').trim()
  if (!message) return fallback
  const containsTechnicalDetail = /(?:traceback|stack trace|[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b|remote.?disconnected|connection aborted|socket|econn|errno|httpx|requests\.|urllib|ssl|tcp)/i.test(message)
  if (CHINESE_TEXT.test(message) && !containsTechnicalDetail) return message

  const status = message.match(/(?:error code|status(?: code)?|http)\D{0,8}(\d{3})/i)?.[1]
  const suffix = status ? `（HTTP ${status}）` : ''
  if (/(failed to fetch|networkerror|network request failed|load failed)/i.test(message)) {
    return `无法连接 Cognitrade API${suffix}，请确认后端服务和网络连接正常。`
  }
  if (/(traceback|stack trace|internalservererror|llmprovidererror|remote.?disconnected|connection aborted|socket|econn|errno|httpx|requests\.|urllib|ssl|tcp)/i.test(message)) {
    return `模型或外部数据服务暂时不可用${suffix}，请稍后重试。`
  }
  if (/(timeout|timed out)/i.test(message)) return `请求超时${suffix}，请稍后重试。`
  if (/(not found|does not exist|absent)/i.test(message)) return '未找到请求的资源。'
  if (/(unauthorized|authentication|bearer token)/i.test(message)) return '身份认证失败，请检查 Bearer token。'
  if (/(forbidden|permission denied|access denied)/i.test(message)) return '当前账号没有执行该操作的权限。'
  if (/(invalid|required|missing|must |unsupported|empty)/i.test(message)) return '请求参数或依赖不符合要求。'
  if (/(failed|failure|error|exception)/i.test(message)) return `${fallback.replace(/[。.]$/, '')}${suffix}。`
  return fallback
}

export function errorMessageWithRequestId(
  value: unknown,
  requestId?: string,
  fallback?: string,
): string {
  const message = localizeErrorMessage(value, fallback)
  return `${message}${requestId ? ` · request_id ${requestId}` : ''}`
}
