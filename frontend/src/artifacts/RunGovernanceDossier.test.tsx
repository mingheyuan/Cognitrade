import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { activityFromEvents, eventSummary } from '../live-data'
import type { RunDetail, RunEvent } from '../research-contracts'
import { RunGovernanceDossier } from './RunGovernanceDossier'

describe('RunGovernanceDossier', () => {
  afterEach(cleanup)

  it('renders every governed step and all returned metric groups', () => {
    const detail = {
      runId: 'complete-run-id',
      replay: { run_id: 'complete-run-id', turn: { turn_id: 'complete-turn-id', task_type: 'regular', execution_mode: 'graph', task_selection: { reason_codes: ['domain_match', 'capability_match'] } } },
      events: { run_id: 'complete-run-id', events: Array.from({ length: 3 }, (_, index) => ({ event_id: `e-${index}`, event_type: 'graph_node_completed' })), event_counts: { events: 3, distinct_event_types: 1 } },
      steps: {
        run_id: 'complete-run-id', graph_id: 'complete-graph-id', domain_id: 'finance_ashare', intent: 'stock_check', strategy: 'dynamic', revision: 7, status: 'completed', notes: ['完整执行图说明'], step_counts: { total: 2, completed: 2 }, status_counts: { completed: 2 },
        steps: [
          { node_id: 'collect', node_kind: 'subagent', capability: 'snapshot.collect', executor: 'collector', objective: '采集完整证据', status: 'completed', depends_on: [], tags: ['evidence'], input_artifact_types: ['normalized_query'], output_artifact_types: ['snapshot'] },
          { node_id: 'final-delivery-step', node_kind: 'subagent', capability: 'report.compose', executor: 'reporter', objective: '生成最终交付', status: 'completed', depends_on: ['collect'], tags: ['delivery'], input_artifact_types: ['snapshot'], output_artifact_types: ['research_report'] },
        ],
      },
      metrics: { run_id: 'complete-run-id', status: 'completed', tool_call_count: 9, counts: { nodes: 2, artifacts: 11 }, usage: { tokens: 4567, cost: .12, duration_seconds: 8.6 }, node_status_counts: { completed: 2 }, event_type_counts: { graph_node_completed: 2, assistant_message_ready: 1 }, graph_event_counts: { graph_node_completed: 2 }, degrade_reason_counts: { provider_unavailable: 1 } },
    } as unknown as RunDetail

    render(<RunGovernanceDossier detail={detail} />)
    for (const value of ['complete-run-id', 'complete-turn-id', 'complete-graph-id', '完整执行图说明', 'final-delivery-step', 'report.compose', 'research_report', '4,567', '数据源不可用', 'capability_match']) {
      expect(screen.getByText(value)).toBeInTheDocument()
    }
  })

  it('does not truncate a long user-visible event history', () => {
    const events = Array.from({ length: 140 }, (_, index) => ({ event_id: `event-${index}`, event_type: 'graph_node_completed', node_id: `node-${index}`, message: `done-${index}` })) as RunEvent[]
    expect(activityFromEvents(events)).toHaveLength(140)
  })

  it('keeps failure meaning without exposing backend exception internals', () => {
    const events = [{
      event_id: 'failed-model-call',
      event_type: 'main_agent_model_turn_failed',
      turn: 2,
      error: "LLMProviderError: InternalServerError: Error code: 502 - {'error': 'upstream'}",
    }] as RunEvent[]
    const [activity] = activityFromEvents(events)
    expect(activity.message).toContain('第 2 轮模型调用失败')
    expect(activity.message).toContain('HTTP 502')
    expect(activity.message).not.toContain('LLMProviderError')
    expect(activity.message).not.toContain('InternalServerError')
  })

  it('localizes raw failure progress and actions before rendering', () => {
    const event = {
      event_id: 'failed-provider-call',
      event_type: 'graph_node_failed',
      message: 'provider execution failed',
      current_action: 'Retry failed provider request',
    } as RunEvent

    expect(eventSummary(event)).toMatch(/[\u3400-\u9fff]/)
    expect(eventSummary(event)).not.toContain('provider execution failed')
    const [activity] = activityFromEvents([event])
    expect(activity.currentAction).toMatch(/[\u3400-\u9fff]/)
    expect(activity.currentAction).not.toContain('Retry failed provider request')
  })

  it('renders terminal and tool failure statuses in Chinese', () => {
    expect(eventSummary({ event_type: 'run_timed_out' } as RunEvent)).toBe('运行超时。')
    expect(eventSummary({ event_type: 'approval_rejected' } as RunEvent)).toBe('审批已拒绝。')
    const summary = eventSummary({
      event_type: 'main_agent_tool_loop_result',
      status: 'failed',
      output_summary: 'provider request failed',
    } as RunEvent)
    expect(summary).toContain('状态：失败')
    expect(summary).not.toContain('provider request failed')
    expect(eventSummary({
      event_type: 'plan_decision',
      status: 'failed',
      decision: { action: 'abort', reason: 'provider planning failed' },
    } as RunEvent)).not.toContain('provider planning failed')
    expect(eventSummary({
      event_type: 'tool_call_completed',
      output_summary: 'RemoteDisconnected while reading response',
    } as RunEvent)).not.toContain('RemoteDisconnected')
  })
})
