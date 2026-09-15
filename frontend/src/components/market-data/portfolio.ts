import { isActiveCollectionJob } from './status'
import type { CollectionJob, FundPortfolioSummary } from './types'

/** Prefer the server's per-slot assessment; an empty gap list proves nothing. */
export function fundPortfolioSummary(job: CollectionJob, childJobs: CollectionJob[] = []): FundPortfolioSummary {
  if (job.portfolio_summary?.by_holding) return job.portfolio_summary
  const totalHoldings = Math.max(job.top_n || 10, job.progress.child_total || 0, job.expanded_holdings.length)
  const totalSlots = totalHoldings * 22
  const assessed = job.can_confirm_freeze || ['frozen', 'ready', 'ready_degraded', 'awaiting_confirmation'].includes(job.freeze_status || '')
  const gaps = job.expanded_holdings.reduce((sum, holding) => sum + (holding.missing_dimensions?.length || 0), 0)
  const summary: FundPortfolioSummary = job.portfolio_summary || {
    total_holdings: totalHoldings, ready_holdings: assessed ? job.expanded_holdings.filter((holding) => !holding.missing_dimensions?.length).length : 0,
    dimensions_per_holding: 22, total_slots: totalSlots, ready_slots: assessed ? totalSlots - gaps : 0,
    missing_slots: gaps, retryable_slots: 0, blocked_slots: gaps, active_slots: 0,
    unassessed_slots: assessed ? 0 : totalSlots - gaps,
    attempt: 0, current_child_job_ids: [], by_error_code: [], by_dimension: [],
  }
  const activeSlots = Math.max(summary.active_slots, childJobs.filter((child) => isActiveCollectionJob(child.status)).reduce((sum, child) => {
    const states = child.task_summary?.by_status || {}
    return sum + Math.max(child.progress.queued + child.progress.running + (child.progress.cancelling || 0),
      Number(states.queued || 0) + Number(states.running || 0) + Number(states.cancelling || 0))
  }, 0))
  const readySlots = !assessed && summary.ready_slots === summary.total_slots && !summary.ready_holdings
    ? 0 : Math.min(summary.ready_slots, Math.max(0, summary.total_slots - Math.max(summary.missing_slots, activeSlots)))
  return {
    ...summary, ready_slots: readySlots, active_slots: activeSlots,
    ready_holdings: activeSlots ? Math.min(summary.ready_holdings, job.progress.child_complete || 0) : summary.ready_holdings,
    unassessed_slots: Math.max(0, summary.total_slots - readySlots - Math.max(summary.missing_slots, activeSlots)),
  }
}
