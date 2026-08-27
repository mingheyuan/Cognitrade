import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FundCollectionOverview, FundCollectionProgress } from './FundCollectionOverview'
import type { CollectionJob } from './types'

const holdings = Array.from({ length: 10 }, (_, index) => ({
  rank: index + 1,
  symbol: `${600000 + index}.SH`,
  code: String(600000 + index),
  name: `持仓股票 ${index + 1}`,
  market: 'CN_EQUITY',
  fund_weight_pct: 10 - index * 0.5,
  disclosed_weight_pct: 10 - index * 0.5,
  missing_dimensions: [],
}))

const parentJob: CollectionJob = {
  job_id: 'fund-parent',
  status: 'running',
  mode: 'missing_or_stale',
  asset_family: 'fund',
  top_n: 10,
  symbols: ['020464'],
  dimensions: ['fund_top_holdings'],
  start_date: '2026-08-26',
  end_date: '2026-08-26',
  max_concurrency: 5,
  publish_when_complete: true,
  scoped_retry_supported: true,
  progress: { total: 1, queued: 0, running: 1, completed: 0, failed: 0, skipped: 0 },
  created_at: '2026-08-26T06:07:21Z',
  parent_bundle_id: 'fund-bundle',
  expanded_holdings: holdings,
  child_collection_job_ids: ['equity-child'],
  portfolio_summary: {
    total_holdings: 10,
    ready_holdings: 0,
    dimensions_per_holding: 22,
    total_slots: 220,
    ready_slots: 220,
    missing_slots: 0,
    retryable_slots: 0,
    blocked_slots: 0,
    active_slots: 0,
    attempt: 0,
    current_child_job_ids: [],
    by_error_code: [],
    by_dimension: [],
  },
}

const childJob: CollectionJob = {
  ...parentJob,
  job_id: 'equity-child',
  parent_job_id: 'fund-parent',
  asset_family: 'equity',
  top_n: null,
  symbols: holdings.map((holding) => holding.symbol),
  dimensions: Array.from({ length: 22 }, (_, index) => `dimension_${index}`),
  progress: { total: 220, queued: 0, running: 220, completed: 0, failed: 0, skipped: 0 },
  expanded_holdings: [],
  child_collection_job_ids: [],
  portfolio_summary: undefined,
  task_summary: {
    total: 220,
    by_status: { running: 220 },
    active: 220,
    succeeded: 0,
    skipped: 0,
    retryable_failed: 0,
    blocked_failed: 0,
    by_error_code: [],
    by_dimension: [],
  },
}

afterEach(cleanup)

describe('FundCollectionOverview', () => {
  it('uses live child progress instead of showing a false blocked warning', () => {
    render(<>
      <FundCollectionProgress job={parentJob} childJobs={[childJob]} />
      <FundCollectionOverview
        job={parentJob}
        childJobs={[childJob]}
        jobDetails={{ 'equity-child': childJob }}
        actionLoading={false}
        onLoadDetail={vi.fn()}
        onRetry={vi.fn()}
      />
    </>)

    expect(screen.getByText('220 个槽正在处理')).toBeInTheDocument()
    expect(screen.getByText('合格 0/220 槽')).toBeInTheDocument()
    expect(screen.getAllByText('采集中')).toHaveLength(10)
    expect(screen.queryByText('当前缺口不能靠重复采集解决')).not.toBeInTheDocument()
  })

  it('expands one holding and retries only its retryable dimensions', async () => {
    const retry = vi.fn().mockResolvedValue(undefined)
    const partialParent: CollectionJob = {
      ...parentJob,
      status: 'partial',
      expanded_holdings: holdings.map((holding, index) => ({
        ...holding,
        missing_dimensions: index === 0 ? ['0_basic'] : [],
      })),
      portfolio_summary: {
        ...parentJob.portfolio_summary!,
        ready_holdings: 9,
        ready_slots: 219,
        missing_slots: 1,
        retryable_slots: 1,
      },
    }
    const failedChild: CollectionJob = {
      ...childJob,
      status: 'partial',
      progress: { total: 1, queued: 0, running: 0, completed: 0, failed: 1, skipped: 0 },
      task_summary: {
        ...childJob.task_summary!,
        total: 1,
        by_status: { failed: 1 },
        active: 0,
        retryable_failed: 1,
      },
      tasks: [{
        task_id: 'task-basic',
        symbol: holdings[0].symbol,
        dimension_id: '0_basic',
        status: 'failed',
        attempt: 1,
        max_attempts: 3,
        retryable: true,
        error_code: 'provider_data_gap',
        error_message: 'provider returned no usable result',
        failure_kind: 'source_unavailable',
        failure_stage: 'acquisition',
        failure_label: '数据源未返回',
        raw_data_present: false,
      }],
    }

    render(<FundCollectionOverview
      job={partialParent}
      childJobs={[failedChild]}
      jobDetails={{ 'equity-child': failedChild }}
      actionLoading={false}
      onLoadDetail={vi.fn().mockResolvedValue(failedChild)}
      onRetry={retry}
    />)

    screen.getAllByRole('button', { name: /查看 22 维/ })[0].click()
    const dimensionRetry = await screen.findByRole('button', { name: `重试 ${holdings[0].symbol} 0_basic` })
    dimensionRetry.click()
    expect(await screen.findByText(`${holdings[0].symbol} · 0_basic 已入队；运行状态将在 3 秒内自动刷新。`)).toBeInTheDocument()
    const retryButton = await screen.findByRole('button', { name: '重试该股票 1 个失败槽' })
    retryButton.click()

    expect(retry).toHaveBeenNthCalledWith(1, 'fund-parent', {
      symbols: [holdings[0].symbol],
      dimensions: ['0_basic'],
    })
    expect(retry).toHaveBeenNthCalledWith(2, 'fund-parent', {
      symbols: [holdings[0].symbol],
      dimensions: ['0_basic'],
    })
    expect(screen.getByText('数据源未返回')).toBeInTheDocument()
    expect(screen.getByText('本次查询没有拿到可用原始记录，可在数据源恢复后重试。')).toBeInTheDocument()
    expect(screen.getByText('provider returned no usable result')).toBeInTheDocument()
  })

  it('reloads task detail when a child retry reaches a terminal state', async () => {
    const onLoadDetail = vi.fn().mockResolvedValue(null)
    const runningChild: CollectionJob = {
      ...childJob,
      symbols: [holdings[0].symbol],
      dimensions: ['12_capital_flow'],
      progress: { total: 1, queued: 0, running: 1, completed: 0, failed: 0, skipped: 0 },
    }
    const { rerender } = render(<FundCollectionOverview
      job={parentJob}
      childJobs={[runningChild]}
      jobDetails={{}}
      actionLoading={false}
      onLoadDetail={onLoadDetail}
      onRetry={vi.fn()}
    />)

    screen.getAllByRole('button', { name: /查看 22 维/ })[0].click()
    await waitFor(() => expect(onLoadDetail).toHaveBeenCalledWith('equity-child'))
    onLoadDetail.mockClear()

    rerender(<FundCollectionOverview
      job={parentJob}
      childJobs={[{
        ...runningChild,
        status: 'failed',
        progress: { total: 1, queued: 0, running: 0, completed: 0, failed: 1, skipped: 0 },
      }]}
      jobDetails={{}}
      actionLoading={false}
      onLoadDetail={onLoadDetail}
      onRetry={vi.fn()}
    />)

    await waitFor(() => expect(onLoadDetail).toHaveBeenCalledWith('equity-child'))
  })

  it('explains the concrete capital-flow provider blockers', async () => {
    const failedCapitalChild: CollectionJob = {
      ...childJob,
      status: 'failed',
      symbols: [holdings[0].symbol],
      dimensions: ['12_capital_flow'],
      progress: { total: 1, queued: 0, running: 0, completed: 0, failed: 1, skipped: 0 },
      tasks: [{
        task_id: 'capital-task',
        symbol: holdings[0].symbol,
        dimension_id: '12_capital_flow',
        status: 'failed',
        attempt: 1,
        max_attempts: 3,
        retryable: true,
        failure_kind: 'source_unavailable',
        failure_label: '数据源未返回',
        error_message: 'provider efinance_capital_flow: provider_unavailable (ConnectionError); provider tushare_capital_flow: provider_unavailable (PermissionDenied)',
      }],
    }
    render(<FundCollectionOverview
      job={parentJob}
      childJobs={[failedCapitalChild]}
      jobDetails={{ 'equity-child': failedCapitalChild }}
      actionLoading={false}
      onLoadDetail={vi.fn().mockResolvedValue(failedCapitalChild)}
      onRetry={vi.fn()}
    />)

    screen.getAllByRole('button', { name: /查看 22 维/ })[0].click()
    expect(await screen.findByText('eFinance / 东财连接被上游中断；Tushare 账户没有 moneyflow 接口权限。')).toBeInTheDocument()
  })
})
