import type {
  AgentOSReplay,
  AgentOSRunSummary,
  ResearchIntent,
  ResearchRequest,
  ResearchResponse,
} from './research-contracts'

const apiBaseUrl = (import.meta.env.VITE_COGNITRADE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1').replace(/\/$/, '')
const apiToken = import.meta.env.VITE_COGNITRADE_API_TOKEN ?? ''

function requestHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    ...(apiToken ? { Authorization: `Bearer ${apiToken}` } : {}),
  }
}

async function readError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { error?: { message?: string } }
    return payload.error?.message ?? `Request failed with HTTP ${response.status}`
  } catch {
    return `Request failed with HTTP ${response.status}`
  }
}

export async function createAgentOSRun(request: ResearchRequest): Promise<ResearchResponse> {
  const response = await fetch(`${apiBaseUrl}/runs`, {
    method: 'POST',
    headers: requestHeaders(),
    body: JSON.stringify(request),
  })
  if (!response.ok) throw new Error(await readError(response))
  return response.json() as Promise<ResearchResponse>
}

export async function listAgentOSRuns(): Promise<AgentOSRunSummary[]> {
  const response = await fetch(`${apiBaseUrl}/runs`, { headers: requestHeaders() })
  if (!response.ok) throw new Error(await readError(response))
  return response.json() as Promise<AgentOSRunSummary[]>
}

export async function getAgentOSReplay(runId: string): Promise<AgentOSReplay> {
  const response = await fetch(`${apiBaseUrl}/runs/${encodeURIComponent(runId)}/replay`, {
    headers: requestHeaders(),
  })
  if (!response.ok) throw new Error(await readError(response))
  return response.json() as Promise<AgentOSReplay>
}

export function subscribeToExecutionEvents(
  runId: string,
  onEvent: (event: Record<string, unknown>) => void,
  onError: (error: Error) => void,
): () => void {
  const controller = new AbortController()
  void (async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/runs/${encodeURIComponent(runId)}/events/stream`, {
        headers: apiToken ? { Authorization: `Bearer ${apiToken}` } : {},
        signal: controller.signal,
      })
      if (!response.ok || !response.body) throw new Error(await readError(response))
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let pending = ''
      while (!controller.signal.aborted) {
        const { value, done } = await reader.read()
        if (done) return
        pending += decoder.decode(value, { stream: true })
        const frames = pending.split('\n\n')
        pending = frames.pop() ?? ''
        for (const frame of frames) {
          const data = frame.split('\n').find((line) => line.startsWith('data: '))?.slice(6)
          if (!data) continue
          onEvent(JSON.parse(data) as Record<string, unknown>)
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        onError(error instanceof Error ? error : new Error('Event stream failed'))
      }
    }
  })()
  return () => controller.abort()
}

export function defaultIntent(intent: ResearchIntent | 'auto'): ResearchIntent {
  return intent === 'auto' ? 'stock_check' : intent
}
