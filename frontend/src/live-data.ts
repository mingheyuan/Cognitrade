import { ACTIVE_RUN_STATUSES, INTENT_LABELS } from './data'
import type {
  AgentOSDashboardItem,
  AgentOSDashboardView,
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

export type DashboardCandidateItem = {
  rank: number
  code: string
  name: string
  sector: string
  score: number | null
  change: number | null
  tier: string
  reason: string
  risk: string
  deepChecked: boolean
  methodologyCoverage: number | null
  dataGapCount: number
  runId: string
}

export type SectorSignalItem = {
  name: string
  score: number
  scoreBasis: string
  sourceCount: number | null
  runId: string
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
  const artifacts = Array.isArray(detail?.replay.artifacts) ? detail.replay.artifacts : []
  const reportArtifact = [...artifacts].reverse().find((artifact) => (
    asString(asRecord(artifact).artifact_type) === 'research_report'
  ))
  const synthesisArtifact = [...artifacts].reverse().find((artifact) => (
    asString(asRecord(artifact).artifact_type) === 'main_agent_synthesis'
  ))
  const assistantArtifact = [...artifacts].reverse().find((artifact) => (
    asString(asRecord(artifact).artifact_type) === 'assistant_message'
  ))
  const reportContent = asRecord(asRecord(reportArtifact).content)
  const synthesisContent = asRecord(asRecord(synthesisArtifact).content)
  const assistantContent = asRecord(asRecord(assistantArtifact).content)
  const synthesisAvailable = Object.keys(synthesisContent).length > 0
  const synthesisUnavailable = asString(assistantContent.delivery_status) === 'synthesis_unavailable'
  const content = {
    ...reportContent,
    ...synthesisContent,
    ...(synthesisContent.conclusion ? {
      assessment: {
        ...asRecord(reportContent.assessment),
        summary: synthesisContent.conclusion,
      },
    } : {}),
  }
  if (Object.keys(content).length > 0) {
    const artifactId = asString(asRecord(reportArtifact).artifact_id)
    const synthesisId = asString(asRecord(synthesisArtifact).artifact_id)
    return {
      ...content,
      protocol: 'agent_os',
      conclusion_source: synthesisAvailable ? 'main_agent_synthesis' : 'pack_report',
      synthesis_available: synthesisAvailable,
      synthesis_unavailable: synthesisUnavailable,
      final_artifact_ids: [artifactId, synthesisId].filter(Boolean),
    } as ResearchReport
  }
  return asRecord(detail?.replay.result?.report) as ResearchReport
}

export function queryFromDetail(detail?: RunDetail | null): string {
  const request = asRecord(detail?.replay.request)
  return asString(request.user_query || asRecord(request.request).user_query)
}

export function intentFromDetail(detail?: RunDetail | null): string {
  const request = asRecord(detail?.replay.request)
  const graph = asRecord(asRecord(detail?.replay.snapshot).graph)
  const metadata = asRecord(graph.metadata)
  return asString(
    detail?.replay.result?.intent
      || request.intent
      || asRecord(request.request).intent
      || metadata.intent
      || detail?.steps.intent,
  )
}

export function statusFromDetail(detail?: RunDetail | null): string {
  return asString(
    detail?.replay.summary?.status
      || asRecord(detail?.replay.snapshot).status
      || detail?.metrics.status
      || detail?.events.status
      || detail?.metrics.summary?.status,
    'unknown',
  )
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
    waiting_approval: '等待审批',
    gated: '质量门禁',
    completed: '已完成',
    completed_partial: '部分完成',
    cancelled: '已取消',
    failed: '失败',
    timed_out: '超时',
    unknown: '未知',
  }
  return labels[String(status || 'unknown')] || String(status || '未知')
}

export type AgentProgressItem = {
  id: string
  actor: string
  nodeId: string
  message: string
  createdAt: string
}

export type AgentActivityItem = {
  id: string
  actor: string
  nodeId: string
  message: string
  currentAction?: string
  nextAction?: string
  kind: 'main_agent' | 'planner' | 'tool' | 'subagent' | 'system'
  turn: number | null
  status: string
  createdAt: string
}

export function assistantMessageFromDetail(detail?: RunDetail | null): string {
  const artifacts = Array.isArray(detail?.replay.artifacts) ? detail.replay.artifacts : []
  const assistantArtifact = [...artifacts].reverse().find((artifact) => (
    asString(asRecord(artifact).artifact_type) === 'assistant_message'
  ))
  const artifactContent = asRecord(asRecord(assistantArtifact).content)
  return asString(
    artifactContent.message
      || asRecord(detail?.replay.snapshot).assistant_message
      || asRecord(detail?.replay.result).message,
  )
}

export function hasFinalDelivery(detail?: RunDetail | null): boolean {
  const events = Array.isArray(detail?.events.events) ? detail.events.events : []
  if (events.some((event) => (
    asString(event.event_type) === 'assistant_message_ready'
      && asString(event.delivery_status) !== 'paused'
      && asString(event.delivery_status) !== 'synthesis_unavailable'
  ))) return true
  const artifacts = Array.isArray(detail?.replay.artifacts) ? detail.replay.artifacts : []
  return artifacts.some((artifact) => {
    if (asString(asRecord(artifact).artifact_type) !== 'assistant_message') return false
    if (asString(asRecord(asRecord(artifact).content).delivery_status) === 'synthesis_unavailable') return false
    return Boolean(asString(asRecord(asRecord(artifact).content).message))
  })
}

function eventId(event: RunEvent, index: number): string {
  return asString(event.event_id) || `${event.created_at || ''}:${event.event_type || ''}:${event.node_id || ''}:${index}`
}

function safeOperationalMessage(value: unknown): string {
  const message = asString(value)
  if (!message) return ''
  if (/(traceback|stack trace|internalservererror|llmprovidererror|remote.?disconnected|connection aborted|socket|econn|errno|httpx|requests\.|urllib|ssl|tcp)/i.test(message)) {
    const status = message.match(/(?:error code|status(?: code)?)\D{0,8}(\d{3})/i)?.[1]
    return `模型或外部数据服务暂时不可用${status ? `（HTTP ${status}）` : ''}，详细诊断已保留在服务端日志中。`
  }
  return message
}

function eventSummary(event: RunEvent): string {
  const eventType = asString(event.event_type)
  const turn = asNumber(event.turn)
  const toolName = asString(event.tool_name || event.tool)
  const status = asString(event.status)
  const decision = asRecord(event.decision)
  const reason = safeOperationalMessage(event.message || event.reason || decision.reason)
  const error = safeOperationalMessage(event.error)
  if (eventType === 'assistant_message_ready') return 'Main Agent 已生成最终交付产物。'
  if (eventType === 'assistant_message_status' && asString(event.delivery_status) === 'synthesis_unavailable') {
    return '研究产物已保存，但 Main Agent 最终答复暂不可用。'
  }
  if (eventType === 'agent_progress' && asString(event.progress_kind) === 'main_agent_graph') {
    return 'Main Agent 更新了受治理的执行计划。'
  }
  if (eventType === 'agent_progress' && asString(event.progress_kind) === 'main_agent_loop') {
    return `Main Agent 第 ${turn ?? '?'} 轮进度更新。`
  }
  if (eventType === 'main_agent_plan_started') return 'Main Agent 正在根据当前能力目录构建 ExecutionGraph。'
  if (eventType === 'graph_compound_expanded') return 'PlanAgent 已扩展动态任务图。'
  if (eventType === 'graph_compound_expansion_completed') return 'PlanAgent 已完成当前图扩展。'
  if (eventType === 'agent_progress' && reason.startsWith('research_report:')) return 'Report Composer 已生成最终研究报告产物。'
  if (eventType === 'main_agent_tool_loop_result') {
    return [
      `第 ${turn ?? '?'} 轮调用 Tool ${toolName || 'call'}，状态：${status || '已完成'}。`,
      asString(event.output_summary),
    ].filter(Boolean).join(' ')
  }
  if (eventType === 'main_agent_model_turn_failed') {
    return `Main Agent 第 ${turn ?? '?'} 轮模型调用失败${error || reason ? `：${error || reason}` : '。'}`
  }
  if (eventType === 'main_agent_failed') return `Main Agent 失败${error || reason ? `：${error || reason}` : '。'}`
  if (eventType === 'graph_node_failed') return `Subagent ${asString(event.node_id, 'node')} 失败${error || reason ? `：${error || reason}` : '。'}`
  if (eventType === 'tool_call_rejected' || eventType === 'tool_call_failed') {
    return `Tool ${toolName || 'call'} ${status || '失败'}${error || reason ? `：${error || reason}` : '。'}`
  }
  if (reason) return reason
  if (eventType === 'main_agent_started' || eventType === 'main_agent_resumed') {
    return `Main Agent 已${eventType === 'main_agent_resumed' ? '恢复' : '启动'}，当前模式：${asString(event.mode, 'core')}。`
  }
  if (eventType === 'main_agent_turn_started') {
    return `Main Agent 开始第 ${turn ?? '?'} 轮循环，正在评估下一步动作。`
  }
  if (eventType === 'main_agent_tool_calls_requested') {
    const names = asStringList(event.tool_names || event.tools)
    return `Main Agent 第 ${turn ?? '?'} 轮选择了 ${names.join('、') || '下一项 Tool'}。`
  }
  if (eventType === 'tool_call_started') return `Tool ${toolName || 'call'} 已开始。`
  if (eventType === 'tool_call_completed') {
    return `Tool ${toolName || 'call'} 已完成${asString(event.output_summary) ? `：${asString(event.output_summary)}` : '。'}`
  }
  if (eventType === 'plan_decision') {
    return `PlanAgent ${asString(decision.action, '继续执行')}：${asString(decision.reason, '已更新执行前沿。')}`
  }
  if (eventType === 'graph_run_started') return 'Main Agent 已启动受治理的 ExecutionGraph。'
  if (eventType === 'graph_frontier_ready') {
    const nodeIds = asStringList(event.node_ids)
    return `执行前沿已就绪${nodeIds.length ? `：${nodeIds.join('、')}` : '。'}`
  }
  if (eventType === 'graph_replan_applied' || eventType === 'graph_patch_applied') {
    return 'PlanAgent 已应用版本化图更新。'
  }
  if (eventType === 'graph_node_started') return `Subagent ${asString(event.node_id, 'node')} 已开始。`
  if (eventType === 'graph_node_completed') return `Subagent ${asString(event.node_id, 'node')} 已完成。`
  if (eventType === 'main_agent_completed') return 'Main Agent 已完成请求。'
  if (eventType === 'graph_run_finished') return `ExecutionGraph 已完成，状态：${asString(event.status, 'unknown')}。`
  return eventType ? `运行事件：${eventType.replaceAll('_', ' ')}` : ''
}

const USER_VISIBLE_EVENT_TYPES = new Set([
  'main_agent_started',
  'main_agent_resumed',
  'main_agent_plan_started',
  'main_agent_turn_started',
  'main_agent_tool_calls_requested',
  'main_agent_tool_loop_result',
  'main_agent_model_turn_failed',
  'main_agent_completed',
  'main_agent_failed',
  'main_agent_clarification_requested',
  'plan_decision',
  'graph_run_started',
  'graph_frontier_ready',
  'graph_compound_expanded',
  'graph_compound_expansion_completed',
  'graph_patch_applied',
  'graph_replan_applied',
  'graph_node_started',
  'graph_node_completed',
  'graph_node_failed',
  'graph_run_finished',
  'graph_run_gated_by_approval',
  'agent_progress',
  'tool_call_started',
  'tool_call_completed',
  'tool_call_failed',
  'tool_call_rejected',
  'assistant_message_ready',
  'assistant_message_status',
  'clarification_requested',
  'clarification_resolved',
  'approval_requested',
  'approval_granted',
  'approval_rejected',
  'run_failed',
  'run_cancelled',
  'run_timed_out',
])

function isUserVisibleEvent(event: RunEvent): boolean {
  return USER_VISIBLE_EVENT_TYPES.has(asString(event.event_type))
}

function displayActor(actor: string): string {
  if (actor === 'execution_graph_runner' || actor === 'graph_runner') return 'Agent OS'
  if (actor === 'main_agent.plan_agent') return 'PlanAgent'
  if (actor === 'main_agent') return 'Main Agent'
  if (actor.startsWith('subagent:')) return `Subagent ${actor.slice('subagent:'.length)}`
  return actor || 'Agent OS'
}

function activityKind(event: RunEvent): AgentActivityItem['kind'] {
  const eventType = asString(event.event_type)
  const actor = asString(event.actor)
  if (actor.includes('plan_agent') || eventType === 'plan_decision' || eventType.includes('replan')) return 'planner'
  if (eventType.includes('tool_call') || eventType.includes('tool_loop')) return 'tool'
  if (actor.includes('subagent') || eventType.includes('graph_node')) return 'subagent'
  if (actor.includes('main_agent') || eventType.startsWith('main_agent') || eventType === 'assistant_message_ready') return 'main_agent'
  return 'system'
}

function localizeAgentAction(value: string): string {
  const action = value.trim()
  if (!action) return ''
  const match = action.match(/^ExecutionGraph (\S+) started at revision (\d+)$/)
  if (match) return `ExecutionGraph ${match[1]} 已在第 ${match[2]} 个修订版本启动`
  if (action === 'Expand lazy compounds and calculate the ready frontier') return '展开延迟 compound 节点并计算可执行前沿'
  if (action === 'Recompute the ready frontier from the updated graph') return '重新计算更新后的可执行前沿'
  if (action === 'Persist the final Artifact and finish delivery') return '持久化最终 Artifact 并完成交付'
  if (action === 'Continue with the current graph frontier') return '继续当前图前沿'
  if (action === 'Process the remaining ready node result(s)') return '处理剩余可执行节点的结果'
  if (action === 'Evaluate the Board and the next graph decision') return '评估 Board 并决定下一步图操作'
  return action
    .replace(/^Ready frontier:\s*/, '当前可执行前沿：')
    .replace(/^Execute ready node\(s\):\s*/, '执行可执行节点：')
    .replace(/^Apply graph patch operations:\s*/, '应用图补丁操作：')
    .replace(/^PlanAgent decision:\s*/, 'PlanAgent 决策：')
    .replace(/^ExecutionGraph finished with status\s+/, 'ExecutionGraph 已以 ')
}

export function activityFromEvents(events: RunEvent[]): AgentActivityItem[] {
  const seen = new Set<string>()
  const rows: AgentActivityItem[] = []
  events.forEach((event, index) => {
    if (!isUserVisibleEvent(event)) return
    const rawMessage = asString(event.message)
    if (
      event.event_type === 'agent_progress'
      && (/^Subagent .* started\.?$/.test(rawMessage)
        || /^Subagent .* completed\.?$/.test(rawMessage)
        || /^Subagent .* completed and committed \d+ Artifact\(s\)\.?$/.test(rawMessage))
    ) return
    const message = eventSummary(event)
    if (!message) return
    const id = eventId(event, index)
    if (seen.has(id)) return
    seen.add(id)
    rows.push({
      id,
      actor: displayActor(asString(event.actor, 'Agent OS')),
      nodeId: asString(event.node_id, 'main_agent'),
      message,
      currentAction: localizeAgentAction(asString(event.current_action)),
      nextAction: localizeAgentAction(asString(event.next_action)),
      kind: activityKind(event),
      turn: asNumber(event.turn),
      status: asString(event.status),
      createdAt: asString(event.created_at),
    })
  })
  return rows
}

export function progressFromEvents(events: RunEvent[]): AgentProgressItem[] {
  const seen = new Set<string>()
  const rows: AgentProgressItem[] = []
  for (const event of events) {
    if (event.event_type !== 'agent_progress') continue
    const message = asString(event.message)
    if (!message) continue
    const id = asString(event.event_id) || `${event.created_at || ''}:${event.node_id || ''}:${message}`
    if (seen.has(id)) continue
    seen.add(id)
    rows.push({
      id,
      actor: asString(event.actor, 'Agent OS'),
      nodeId: asString(event.node_id, 'main_agent'),
      message,
      createdAt: asString(event.created_at),
    })
  }
  return rows.slice(-8)
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
    const reason = asString(row.why_it_made_the_list || row.why || row.reason || row.summary, '后端未提供候选理由')
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
      tier: asString(row.candidate_tier || row.tier, deepChecked ? '深度核验' : '候选'),
      reason,
      risk: asString(row.main_risk || row.risk, '未提供'),
      deepChecked,
    }
  })
}

export function dashboardItems(view: AgentOSDashboardView | null | undefined, resourceId: string): AgentOSDashboardItem[] {
  return view?.resources[resourceId]?.items || []
}

export function dashboardValue(view: AgentOSDashboardView | null | undefined, resourceId: string): Record<string, unknown> {
  const resource = view?.resources[resourceId]
  if (resource?.value !== undefined) return asRecord(resource.value)
  return asRecord(resource?.items?.[0]?.data)
}

export function dashboardCandidates(view: AgentOSDashboardView | null | undefined): DashboardCandidateItem[] {
  const rows = [
    ...dashboardItems(view, 'candidates'),
    ...dashboardItems(view, 'watchlist_candidates'),
  ]
  return rows.map((item, index) => {
    const row = asRecord(item.data)
    const methodology = asRecord(row.methodology)
    const gaps = Array.isArray(methodology.data_gaps)
      ? methodology.data_gaps
      : Array.isArray(row.missing_evidence) ? row.missing_evidence : []
    const tier = asString(row.candidate_tier, 'watchlist')
    const code = asString(row.symbol, `candidate-${index + 1}`)
    return {
      rank: index + 1,
      code,
      name: asString(row.name, code),
      sector: asString(row.sector, '行业未提供'),
      score: asNumber(row.score),
      change: asNumber(row.latest_pct_change),
      tier,
      reason: asString(row.why_it_made_the_list, '后端未提供入选理由'),
      risk: asString(row.main_risk, '未提供'),
      deepChecked: Boolean(row.deep_checked),
      methodologyCoverage: asNumber(methodology.pass_rate),
      dataGapCount: gaps.length,
      runId: asString(item.provenance.run_id),
    }
  })
}

export function dashboardSectorSignals(view: AgentOSDashboardView | null | undefined): SectorSignalItem[] {
  return dashboardItems(view, 'sector_heatmap').flatMap((item) => {
    const row = asRecord(item.data)
    const score = asNumber(row.score)
    if (score === null) return []
    return [{
      name: asString(row.name, '未命名板块'),
      score,
      scoreBasis: asString(row.score_basis, 'artifact_coverage'),
      sourceCount: asNumber(row.source_count),
      runId: asString(item.provenance.run_id),
    }]
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
  if (status === 'completed' || status === 'completed_partial') return 'complete'
  if (isActiveStatus(status)) return 'running'
  return 'review'
}
