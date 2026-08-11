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
  BacktestRequest,
  BacktestResult,
  BacktestSummaryPage,
  MarketDataResponse,
  MarketQuote,
  PaperAccount,
  PaperOrder,
  PaperMatchResult,
  PaperOrderPage,
  PaperOrderInput,
  PaperPortfolio,
  QuantEngineStatus,
  SecuritySearchResponse,
} from './finance-contracts'
import { localizeErrorMessage } from './utils/error-message'

export const API_BASE_STORAGE_KEY = 'cognitrade.api.base-url'
export const API_TOKEN_STORAGE_KEY = 'cognitrade.api.token'
export const API_CONNECTION_CONFIGURED_STORAGE_KEY = 'cognitrade.api.connection-configured'

const DEFAULT_API_BASE = 'http://127.0.0.1:8000/api/v1'

export type ApiConnectionSettings = {
  baseUrl: string
  token: string
}

export type RunReportFormat = 'html' | 'pdf' | 'share-card' | 'war-report' | 'one-liner'

export type RunReportExport = {
  blob: Blob
  filename: string
}

export type HoldingReportProfile = 'lite' | 'medium' | 'deep'

export type FundHoldingReportItem = {
  rank: number
  symbol: string
  name: string
  market: string
  supported: boolean
  status: 'queued' | 'running' | 'completed' | 'completed_partial' | 'failed' | 'cancelled' | 'unsupported'
  fund_weight_pct: number | null
  target_security_weight_pct: number | null
  holding_source: string
  disclosure_date: string | null
  child_run_id: string | null
  attempt_run_ids: string[]
  report_available: boolean
  retry_count: number
  data_gap: string[]
  error: string | null
  started_at: string | null
  completed_at: string | null
}

export type FundHoldingReportBatch = {
  batch_id: string
  source_run_id: string
  source_artifact_id: string
  fund_code: string
  fund_name: string
  fund_type: string | null
  profile: HoldingReportProfile
  status: 'running' | 'completed' | 'completed_partial' | 'failed' | 'cancelled'
  concurrency: number
  holding_source: 'direct_equity_disclosure' | 'target_etf_lookthrough'
  is_fund_of_funds: boolean
  disclosure_date: string | null
  disclosure_age_days: number | null
  stale_level: 'current' | 'warning' | 'confirmation_required' | 'unknown'
  cancel_requested: boolean
  holdings: FundHoldingReportItem[]
  counts: {
    total: number
    supported: number
    queued: number
    running: number
    deliverable: number
    failed: number
    cancelled: number
    unsupported: number
  }
  data_gap: string[]
  created_at: string
  updated_at: string
  completed_at: string | null
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
    super(localizeErrorMessage(message, `API 请求失败${status ? `（HTTP ${status}）` : ''}。`))
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

  listRuns(limit = 50, offset = 0): Promise<RunHistoryRecord[]> {
    return this.request(`/runs?limit=${limit}&offset=${offset}`)
  }

  async listAllRuns(): Promise<RunHistoryRecord[]> {
    const pageSize = 100
    const rows: RunHistoryRecord[] = []
    let offset = 0
    while (true) {
      const page = await this.listRuns(pageSize, offset)
      rows.push(...page)
      if (page.length < pageSize) break
      offset += page.length
    }
    return Array.from(new Map(rows.map((run) => [run.run_id, run])).values())
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

  getRunReport(runId: string, format: RunReportFormat): Promise<RunReportExport> {
    const mediaType = format === 'pdf' ? 'application/pdf' : format === 'share-card' || format === 'war-report' ? 'image/png' : format === 'one-liner' ? 'text/plain' : 'text/html'
    const extension = format === 'share-card' || format === 'war-report' ? 'png' : format === 'one-liner' ? 'txt' : format
    return this.requestBlob(
      `/runs/${encodeURIComponent(runId)}/report?format=${encodeURIComponent(format)}`,
      mediaType,
      `uzi-report-${runId}-${format}.${extension}`,
    )
  }

  createFundHoldingReports(
    sourceRunId: string,
    payload: {
      fund_code: string
      profile: HoldingReportProfile
      source_artifact_id?: string
      confirm_stale?: boolean
      force?: boolean
    },
  ): Promise<FundHoldingReportBatch> {
    return this.request(`/runs/${encodeURIComponent(sourceRunId)}/holding-reports`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  listFundHoldingReportBatches(sourceRunId: string, fundCode?: string): Promise<FundHoldingReportBatch[]> {
    const query = fundCode ? `?fund_code=${encodeURIComponent(fundCode)}` : ''
    return this.request(`/runs/${encodeURIComponent(sourceRunId)}/holding-report-batches${query}`)
  }

  getFundHoldingReportBatch(batchId: string): Promise<FundHoldingReportBatch> {
    return this.request(`/holding-report-batches/${encodeURIComponent(batchId)}`)
  }

  cancelFundHoldingReportBatch(batchId: string): Promise<FundHoldingReportBatch> {
    return this.request(`/holding-report-batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' })
  }

  retryFundHoldingReportBatch(batchId: string, symbols: string[] = [], force = false): Promise<FundHoldingReportBatch> {
    return this.request(`/holding-report-batches/${encodeURIComponent(batchId)}/retry`, {
      method: 'POST',
      body: JSON.stringify({ symbols, force }),
    })
  }

  continueFundHoldingReportBatch(batchId: string, symbols: string[] = []): Promise<FundHoldingReportBatch> {
    return this.request(`/holding-report-batches/${encodeURIComponent(batchId)}/continue`, {
      method: 'POST',
      body: JSON.stringify({ symbols }),
    })
  }

  getFundHoldingReportZip(batchId: string): Promise<RunReportExport> {
    return this.requestBlob(
      `/holding-report-batches/${encodeURIComponent(batchId)}/reports.zip`,
      'application/zip',
      `fund-holding-reports-${batchId}.zip`,
    )
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
      throw new CognitradeApiError(localizeErrorMessage(error, '无法连接 Cognitrade API，请确认后端服务和网络连接正常。'), 0, 'network_error')
    }
    if (!response.ok) {
      const body = await response.text()
      throw new CognitradeApiError(
        apiErrorMessageFromText(body) || `API 请求失败（HTTP ${response.status}）`,
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

  listPaperOrders(limit = 50, offset = 0): Promise<PaperOrderPage> {
    return this.financeRequest(`/finance/paper/orders?limit=${limit}&offset=${offset}`)
  }

  placePaperOrder(order: PaperOrderInput): Promise<PaperOrder> {
    return this.financeRequest('/finance/paper/orders', {
      method: 'POST',
      body: JSON.stringify(order),
    })
  }

  getPaperEngine(): Promise<QuantEngineStatus> {
    return this.financeRequest('/finance/paper/engine')
  }

  matchPaperOrders(refresh = true): Promise<PaperMatchResult> {
    return this.financeRequest(`/finance/paper/orders/match?refresh=${refresh}`, {
      method: 'POST',
    })
  }

  cancelPaperOrder(orderId: string): Promise<PaperOrder> {
    return this.financeRequest(`/finance/paper/orders/${encodeURIComponent(orderId)}/cancel`, {
      method: 'POST',
    })
  }

  getBacktestEngine(): Promise<QuantEngineStatus> {
    return this.financeRequest('/finance/backtests/engine')
  }

  listBacktests(limit = 20, offset = 0): Promise<BacktestSummaryPage> {
    return this.financeRequest(`/finance/backtests?limit=${limit}&offset=${offset}`)
  }

  runBacktest(request: BacktestRequest): Promise<BacktestResult> {
    return this.financeRequest('/finance/backtests', {
      method: 'POST',
      body: JSON.stringify(request),
    })
  }

  getBacktest(backtestId: string): Promise<BacktestResult> {
    return this.financeRequest(`/finance/backtests/${encodeURIComponent(backtestId)}`)
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

  private async requestBlob(path: string, accept: string, fallbackFilename: string): Promise<RunReportExport> {
    const headers = new Headers({ Accept: accept })
    if (this.settings.token) headers.set('Authorization', `Bearer ${this.settings.token}`)
    let response: Response
    try {
      response = await fetch(`${this.settings.baseUrl}${path}`, { headers })
    } catch (error) {
      throw new CognitradeApiError(localizeErrorMessage(error, '无法连接 Cognitrade API，请确认后端服务和网络连接正常。'), 0, 'network_error')
    }
    if (!response.ok) {
      const contentType = response.headers.get('content-type') || ''
      const payload = contentType.includes('application/json')
        ? ((await response.json()) as ApiErrorBody)
        : null
      throw new CognitradeApiError(
        payload?.error?.message || `报告导出失败（HTTP ${response.status}）`,
        response.status,
        payload?.error?.code,
        payload?.error?.request_id || response.headers.get('X-Request-ID') || undefined,
      )
    }
    const disposition = response.headers.get('content-disposition') || ''
    const filenameMatch = disposition.match(/filename="?([^";]+)"?/i)
    return {
      blob: await response.blob(),
      filename: filenameMatch?.[1] || fallbackFilename,
    }
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
      throw new CognitradeApiError(localizeErrorMessage(error, '无法连接 Cognitrade API，请确认后端服务和网络连接正常。'), 0, 'network_error')
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

function apiErrorMessageFromText(value: string): string {
  const text = String(value || '').trim()
  if (!text) return ''
  try {
    const payload = JSON.parse(text) as ApiErrorBody
    return payload?.error?.message || (typeof payload?.detail === 'string' ? payload.detail : '')
  } catch {
    return localizeErrorMessage(text)
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
