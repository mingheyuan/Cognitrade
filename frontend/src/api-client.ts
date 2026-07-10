import type {
  AgentOSDashboardView,
  ApiErrorBody,
  ResearchRequest,
  ResearchResponse,
  RunDetail,
  RunEventsPayload,
  RunHistoryRecord,
  RunMetricsPayload,
  RunReplayPayload,
  RunStepsPayload,
  SystemStatus,
} from './research-contracts'

export const API_BASE_STORAGE_KEY = 'cognitrade.api.base-url'
export const API_TOKEN_STORAGE_KEY = 'cognitrade.api.token'

const DEFAULT_API_BASE = 'http://127.0.0.1:8000/api/v1'

export type ApiConnectionSettings = {
  baseUrl: string
  token: string
}

export class CognitradeApiError extends Error {
  readonly status: number
  readonly code?: string
  readonly requestId?: string

  constructor(message: string, status: number, code?: string, requestId?: string) {
    super(message)
    this.name = 'CognitradeApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
  }
}

export function normalizeApiBase(value: string): string {
  const normalized = value.trim().replace(/\/+$/, '')
  if (!normalized) return DEFAULT_API_BASE
  return normalized.endsWith('/api/v1') ? normalized : `${normalized}/api/v1`
}

export function loadApiConnection(): ApiConnectionSettings {
  const configuredBase = import.meta.env.VITE_COGNITRADE_API_BASE_URL as string | undefined
  return {
    baseUrl: normalizeApiBase(localStorage.getItem(API_BASE_STORAGE_KEY) || configuredBase || DEFAULT_API_BASE),
    token: sessionStorage.getItem(API_TOKEN_STORAGE_KEY) || '',
  }
}

export function saveApiConnection(settings: ApiConnectionSettings): ApiConnectionSettings {
  const normalized = { baseUrl: normalizeApiBase(settings.baseUrl), token: settings.token.trim() }
  localStorage.setItem(API_BASE_STORAGE_KEY, normalized.baseUrl)
  if (normalized.token) sessionStorage.setItem(API_TOKEN_STORAGE_KEY, normalized.token)
  else sessionStorage.removeItem(API_TOKEN_STORAGE_KEY)
  return normalized
}

export class CognitradeApiClient {
  readonly settings: ApiConnectionSettings

  constructor(settings: ApiConnectionSettings) {
    this.settings = { ...settings, baseUrl: normalizeApiBase(settings.baseUrl) }
  }

  getSystemStatus(): Promise<SystemStatus> {
    return this.request('/system/status')
  }

  listRuns(limit = 50): Promise<RunHistoryRecord[]> {
    return this.request(`/runs?limit=${limit}`)
  }

  createRun(payload: ResearchRequest): Promise<ResearchResponse> {
    return this.request('/runs', { method: 'POST', body: JSON.stringify(payload) })
  }

  cancelRun(runId: string): Promise<Record<string, unknown>> {
    return this.request(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
  }

  rerunRun(runId: string, fromNode: string): Promise<ResearchResponse> {
    return this.request(`/runs/${encodeURIComponent(runId)}/reruns`, {
      method: 'POST',
      body: JSON.stringify({ from_node: fromNode }),
    })
  }

  getReplay(runId: string): Promise<RunReplayPayload> {
    return this.request(`/runs/${encodeURIComponent(runId)}/replay`)
  }

  getEvents(runId: string): Promise<RunEventsPayload> {
    return this.request(`/runs/${encodeURIComponent(runId)}/events`)
  }

  getSteps(runId: string): Promise<RunStepsPayload> {
    return this.request(`/runs/${encodeURIComponent(runId)}/steps`)
  }

  getMetrics(runId: string): Promise<RunMetricsPayload> {
    return this.request(`/runs/${encodeURIComponent(runId)}/metrics`)
  }

  getDashboard(domainId: string, viewId: string): Promise<AgentOSDashboardView> {
    return this.request(
      `/dashboard/${encodeURIComponent(domainId)}/${encodeURIComponent(viewId)}`,
    )
  }

  async getRunDetail(runId: string): Promise<RunDetail> {
    const [replay, events, steps, metrics] = await Promise.all([
      this.getReplay(runId),
      this.getEvents(runId),
      this.getSteps(runId),
      this.getMetrics(runId),
    ])
    return { runId, replay, events, steps, metrics }
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    if (init.body) headers.set('Content-Type', 'application/json')
    if (this.settings.token) headers.set('Authorization', `Bearer ${this.settings.token}`)

    let response: Response
    try {
      response = await fetch(`${this.settings.baseUrl}${path}`, { ...init, headers })
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      throw new CognitradeApiError(`无法连接 Cognitrade API：${reason}`, 0, 'network_error')
    }

    const contentType = response.headers.get('content-type') || ''
    const payload = contentType.includes('application/json')
      ? ((await response.json()) as T | ApiErrorBody)
      : ((await response.text()) as T)
    if (response.ok) return payload as T

    const body = typeof payload === 'object' && payload !== null ? (payload as ApiErrorBody) : undefined
    const detail = typeof body?.detail === 'string' ? body.detail : undefined
    throw new CognitradeApiError(
      body?.error?.message || detail || `API 请求失败（HTTP ${response.status}）`,
      response.status,
      body?.error?.code,
      body?.error?.request_id || response.headers.get('X-Request-ID') || undefined,
    )
  }
}
