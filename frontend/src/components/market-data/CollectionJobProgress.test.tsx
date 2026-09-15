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
