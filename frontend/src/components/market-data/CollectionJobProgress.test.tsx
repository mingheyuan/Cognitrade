import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { CollectionJobProgress } from './CollectionJobProgress'

afterEach(cleanup)

it('distinguishes provider returns from persisted slot settlement', () => {
  render(<CollectionJobProgress progress={{ total: 22, queued: 0, running: 22, completed: 0, failed: 0, skipped: 0,
    provider: { phase: 'completed', completed: 22, total: 22 } }} />)
  expect(screen.getByText('0 / 22 已结算')).toBeInTheDocument()
  expect(screen.getByText(/上游返回 22\/22（不代表已落库）/)).toBeInTheDocument()
  expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
})

it('shows native postfetch separately after structured slots settle', () => {
  render(<CollectionJobProgress progress={{ total: 2, queued: 0, running: 0, completed: 2, failed: 0, skipped: 0,
    native_postfetch: { status: 'running', attempted: 2, succeeded: 1, failed: 1, persisted_snapshot_count: 0 } }} />)
  expect(screen.getByText(/原生补搜：进行中/)).toHaveTextContent('尝试 2 · 成功 1 · 缺口 1 · 新增快照 0')
  expect(screen.getByText(/不代表报告自审通过/)).toBeInTheDocument()
})

it('discloses why historical jobs did not run live postfetch', () => {
  render(<CollectionJobProgress progress={{ total: 2, queued: 0, running: 0, completed: 0, failed: 2, skipped: 0,
    native_postfetch: { status: 'not_run', attempted: 0, succeeded: 0, failed: 0, persisted_snapshot_count: 0,
      reason_code: 'no_current_day_postfetch_scope' } }} />)
  expect(screen.getByText(/历史日期不使用当前网页回填/)).toBeInTheDocument()
})

it('shows evidence enrichment after source settlement, not stale provider persistence', () => {
  render(<CollectionJobProgress progress={{ total: 2, queued: 0, running: 0, completed: 1, failed: 1, skipped: 0,
    provider: { phase: 'persisting', completed: 2, total: 2 },
    research_enrichment: { status: 'running', groups: [{ group: 'A', status: 'failed' }], revisions: [] } }} />)
  expect(screen.getByLabelText('研究证据整理进度')).toHaveTextContent('研究证据整理：进行中 · 已返回 1 组 · 已保存 0 个数据修订')
  expect(screen.getByText(/结构化槽位已结算，任务仍在继续/)).toBeInTheDocument()
  expect(screen.queryByText(/校验并落库/)).not.toBeInTheDocument()
  expect(screen.getByText(/不代表报告自审通过/)).toBeInTheDocument()
})

it('does not describe a terminal evidence stage as still running', () => {
  render(<CollectionJobProgress progress={{ total: 1, queued: 0, running: 0, completed: 1, failed: 0, skipped: 0,
    research_enrichment: { status: 'partial', groups: [], revisions: [{ snapshot_id: 'revision' }] } }} />)
  expect(screen.getByLabelText('研究证据整理进度')).toHaveTextContent('研究证据整理：仍有缺口')
  expect(screen.queryByText(/任务仍在继续/)).not.toBeInTheDocument()
})

it('separates overload, missing originals and cancellation from returned-group success', () => {
  render(<CollectionJobProgress progress={{ total: 1, queued: 0, running: 0, completed: 1, failed: 0, skipped: 0,
    research_enrichment: { status: 'partial', revisions: [], groups: [
      { symbol: '002371.SZ', group: 'B', status: 'model_failed', failure: { kind: 'llm_provider_unavailable', provider_error_code: 'serverOverloaded' } },
      { symbol: '300604.SZ', group: 'C', status: 'original_documents_missing' },
      { symbol: '688012.SH', group: 'A', status: 'completed' },
      { symbol: '688200.SH', group: 'A', status: 'cancelled' },
      { symbol: '688361.SH', group: 'A' },
    ] } }} />)
  expect(screen.getByLabelText('研究证据整理进度')).toHaveTextContent('已返回 5 组 · 已保存 0 个数据修订 · 异常 3 组')
  expect(screen.getByText('002371.SZ · B：模型上游过载（serverOverloaded）')).toBeInTheDocument()
  expect(screen.getByText('300604.SZ · C：缺少已归档原文')).toBeInTheDocument()
  expect(screen.getByText('688200.SH · A：已取消')).toBeInTheDocument()
  expect(screen.getAllByRole('listitem', { hidden: true })).toHaveLength(3)
})

it('does not show an issue disclosure for successful research groups', () => {
  render(<CollectionJobProgress progress={{ total: 1, queued: 0, running: 0, completed: 1, failed: 0, skipped: 0,
    research_enrichment: { status: 'completed', revisions: [], groups: [{ group: 'A', status: 'completed' }] } }} />)
  expect(screen.queryByText('查看异常分组')).not.toBeInTheDocument()
})

it.each([
  ['llm_transport_timeout', '模型请求超时'], ['llm_queue_timeout', '模型排队超时'],
  ['agent_content_invalid', '模型输出校验未通过'], ['llm_output_repetition', '检测到模型重复输出'],
])('keeps %s distinct from overload', (kind, label) => {
  render(<CollectionJobProgress progress={{ total: 1, queued: 0, running: 0, completed: 1, failed: 0, skipped: 0,
    research_enrichment: { status: 'partial', groups: [{ group: 'A', status: 'model_failed', failure: { kind } }] } }} />)
  expect(screen.getByText(`股票未标注 · A：${label}`)).toBeInTheDocument()
  expect(screen.queryByText(/模型上游过载/)).not.toBeInTheDocument()
})
