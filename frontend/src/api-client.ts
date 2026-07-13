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
import type {
  MarketDataResponse,
  MarketQuote,
  PaperAccount,
  PaperOrder,
  PaperOrderInput,
  PaperPortfolio,
  SecuritySearchResponse,
} from './finance-contracts'

export const API_BASE_STORAGE_KEY = 'cognitrade.api.base-url'
export const API_TOKEN_STORAGE_KEY = 'cognitrade.api.token'
export const API_CONNECTION_CONFIGURED_STORAGE_KEY = 'cognitrade.api.connection-configured'

const DEFAULT_API_BASE = 'http://127.0.0.1:8000/api/v1'

export type ApiConnectionSettings = {
  baseUrl: string
  token: string
}

export type ChatThreadRecord = {
  thread_id: string
  [key: string]: unknown
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
  const storedBase = localStorage.getItem(API_BASE_STORAGE_KEY)
  const userConfigured = localStorage.getItem(API_CONNECTION_CONFIGURED_STORAGE_KEY) === 'true'
  return {
    baseUrl: normalizeApiBase((userConfigured && storedBase) || configuredBase || storedBase || DEFAULT_API_BASE),
    token: sessionStorage.getItem(API_TOKEN_STORAGE_KEY) || '',
  }
}

export function saveApiConnection(settings: ApiConnectionSettings): ApiConnectionSettings {
  const normalized = { baseUrl: normalizeApiBase(settings.baseUrl), token: settings.token.trim() }
  localStorage.setItem(API_BASE_STORAGE_KEY, normalized.baseUrl)
  localStorage.setItem(API_CONNECTION_CONFIGURED_STORAGE_KEY, 'true')
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

  createChatThread(): Promise<ChatThreadRecord> {
    return this.request('/chat/threads', { method: 'POST' })
  }

  submitChatRun(threadId: string, payload: ResearchRequest): Promise<ResearchResponse> {
    return this.request(`/chat/threads/${encodeURIComponent(threadId)}/runs`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
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

  async streamRunEvents(
    runId: string,
    onEvent: (event: RunEventsPayload['events'][number]) => void,
    signal?: AbortSignal,
    lastEventId?: string,
  ): Promise<void> {
    const headers = new Headers({ Accept: 'text/event-stream' })
    if (this.settings.token) headers.set('Authorization', `Bearer ${this.settings.token}`)
    if (lastEventId) headers.set('Last-Event-ID', lastEventId)
    let response: Response
    try {
      response = await fetch(
        `${this.settings.baseUrl}/runs/${encodeURIComponent(runId)}/events/stream`,
        { headers, signal },
      )
    } catch (error) {
      if (signal?.aborted) return
      const reason = error instanceof Error ? error.message : String(error)
      throw new CognitradeApiError(`无法连接 Cognitrade API：${reason}`, 0, 'network_error')
    }
    if (!response.ok) {
      const body = await response.text()
      throw new CognitradeApiError(
        body || `API 请求失败（HTTP ${response.status}）`,
        response.status,
        undefined,
      )
    }
    if (!response.body) return
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      while (true) {
        const chunk = await reader.read()
        buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop() || ''
        for (const frame of frames) {
          const event = parseSseFrame(frame)
          if (event) onEvent(event)
        }
        if (chunk.done) break
      }
      const finalEvent = parseSseFrame(buffer)
      if (finalEvent) onEvent(finalEvent)
    } finally {
      reader.releaseLock()
    }
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

  searchSecurities(query: string, limit = 8): Promise<SecuritySearchResponse> {
    return this.financeRequest(
      `/finance/securities?query=${encodeURIComponent(query)}&limit=${limit}`,
    )
  }

  getMarketData(symbol: string, refresh = false): Promise<MarketDataResponse> {
    return this.financeRequest(
      `/finance/market-data/${encodeURIComponent(symbol)}?refresh=${refresh}`,
    )
  }

  getMarketQuote(symbol: string, refresh = false): Promise<MarketQuote> {
    return this.financeRequest(
      `/finance/quotes/${encodeURIComponent(symbol)}?refresh=${refresh}`,
    )
  }

  getPaperAccount(): Promise<PaperAccount> {
    return this.financeRequest('/finance/paper/account')
  }

  resetPaperAccount(initialCash = 1_000_000): Promise<PaperAccount> {
    return this.financeRequest('/finance/paper/account/reset', {
      method: 'POST',
      body: JSON.stringify({ initial_cash: initialCash }),
    })
  }

  getPaperPortfolio(): Promise<PaperPortfolio> {
    return this.financeRequest('/finance/paper/portfolio')
  }

  listPaperOrders(limit = 50): Promise<{ orders: PaperOrder[] }> {
    return this.financeRequest(`/finance/paper/orders?limit=${limit}`)
  }

  placePaperOrder(order: PaperOrderInput): Promise<PaperOrder> {
    return this.financeRequest('/finance/paper/orders', {
      method: 'POST',
      body: JSON.stringify(order),
    })
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

  private financeRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
    const financeBase = this.settings.baseUrl.replace(/\/api\/v1$/, '/api/v2')
    return this.request(path, init, financeBase)
  }

  private async request<T>(path: string, init: RequestInit = {}, baseUrl = this.settings.baseUrl): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    if (init.body) headers.set('Content-Type', 'application/json')
    if (this.settings.token) headers.set('Authorization', `Bearer ${this.settings.token}`)

    let response: Response
    try {
      response = await fetch(`${baseUrl}${path}`, { ...init, headers })
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

function parseSseFrame(frame: string): RunEventsPayload['events'][number] | null {
  let eventType = 'message'
  let eventId = ''
  const data: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) eventType = line.slice(6).trim()
    else if (line.startsWith('id:')) eventId = line.slice(3).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  try {
    const payload = JSON.parse(data.join('\n')) as Record<string, unknown>
    return { ...payload, event_id: eventId || payload.event_id, event_type: eventType } as RunEventsPayload['events'][number]
  } catch {
    return { event_id: eventId || undefined, event_type: eventType, message: data.join('\n') }
  }
}
