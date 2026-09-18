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
  it('shows research still running for a settled stock collection child', () => {
    const child = { ...childJob, progress: { ...childJob.progress, running: 0, completed: 220,
      research_enrichment: { status: 'running', groups: [{ group: 'A' }], revisions: [] } } }
    render(<FundCollectionOverview job={parentJob} childJobs={[child]} jobDetails={{}}
      actionLoading={false} onLoadDetail={vi.fn()} onRetry={vi.fn()} />)
    expect(screen.getByLabelText('研究证据整理进度')).toHaveTextContent('研究证据整理：进行中')
    expect(screen.getByText(/结构化槽位已结算，任务仍在继续/)).toBeInTheDocument()
  })

  it('does not mark unscheduled holdings or absent portfolio assessments ready', () => {
    const job = { ...parentJob, portfolio_summary: undefined, child_collection_job_ids: [] }
    render(<>
      <FundCollectionProgress job={job} />
      <FundCollectionOverview job={job} childJobs={[]} jobDetails={{}} actionLoading={false} onLoadDetail={vi.fn()} onRetry={vi.fn()} />
    </>)
    expect(screen.getByText('严格可用 0/220 槽')).toBeInTheDocument()
    expect(screen.getByText('未评估 220 槽')).toBeInTheDocument()
    expect(screen.getAllByText('未评估')).toHaveLength(10)
    expect(screen.queryByText('已就绪')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

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
    expect(screen.getByText('严格可用 0/220 槽')).toBeInTheDocument()
    expect(screen.getAllByText('采集中')).toHaveLength(10)
    expect(screen.queryByText('当前缺口不能靠重复采集解决')).not.toBeInTheDocument()
  })

  it.each([
    { status: 'running', cancelling: 0, active: 0 },
    { status: 'cancelling', cancelling: 10, active: 10 },
  ])('does not infer activity from legacy gaps or deduct existing gaps twice ($status)', ({ status, cancelling, active }) => {
    const parent = {
      ...parentJob,
      portfolio_summary: { ...parentJob.portfolio_summary!, ready_slots: 130, missing_slots: 90, retryable_slots: 60, blocked_slots: 30 },
    }
    const child = {
      ...childJob, status, task_summary: undefined,
      progress: { total: 220, queued: 0, running: 0, cancelling, completed: 0, failed: 0, skipped: 0 },
    }
    render(<>
      <FundCollectionProgress job={parent} childJobs={[child]} />
      <FundCollectionOverview job={parent} childJobs={[child]} jobDetails={{}} actionLoading={false} onLoadDetail={vi.fn()} onRetry={vi.fn()} />
    </>)
    expect(screen.getByText('严格可用 130/220 槽')).toBeInTheDocument()
    if (active) expect(screen.getByText(`${active} 个槽正在处理`)).toBeInTheDocument()
    else expect(screen.queryByText(/个槽正在处理/)).not.toBeInTheDocument()
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
      tasks: [
        {
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
        },
        {
          task_id: 'task-governance-audit',
          symbol: holdings[0].symbol,
          dimension_id: '11_governance',
          status: 'partial',
          attempt: 1,
          max_attempts: 3,
          retryable: true,
          error_code: 'historical_partial',
          error_message: 'optional governance fields are incomplete',
          failure_kind: 'audit_only',
          failure_stage: 'audit',
          failure_label: 'UZI 可用 · 扩展审计提示',
          raw_data_present: true,
        },
      ],
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
    expect(screen.getByText('该槽保留原生数据检查结果；研究缺口仅作 Agent 输入提示，PIT 与冻结数据安全校验独立执行，不代表最终报告自审通过。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `重试 ${holdings[0].symbol} 11_governance` })).not.toBeInTheDocument()
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

  it('does not treat a saved error response as usable UZI data', async () => {
    const failedMacroChild: CollectionJob = {
      ...childJob,
      status: 'partial',
      tasks: [{
        task_id: 'macro-task',
        symbol: holdings[0].symbol,
        dimension_id: '3_macro',
        status: 'failed',
        attempt: 1,
        max_attempts: 3,
        retryable: true,
        error_code: 'historical_provider_failure',
        error_message: 'em_push2: provider_unavailable (ConnectionError)',
        collection_outcome: 'retryable_failure',
        failure_kind: 'source_unavailable',
        failure_stage: 'acquisition',
        failure_label: '数据源未返回',
        raw_data_present: true,
      }],
    }
    render(<FundCollectionOverview
      job={parentJob}
      childJobs={[failedMacroChild]}
      jobDetails={{ 'equity-child': failedMacroChild }}
      actionLoading={false}
      onLoadDetail={vi.fn().mockResolvedValue(failedMacroChild)}
      onRetry={vi.fn()}
    />)

    screen.getAllByRole('button', { name: /查看 22 维/ })[0].click()
    expect(await screen.findByText('本次上游请求失败，没有把失败误判为历史不可恢复；可定向重试。')).toBeInTheDocument()
    expect(screen.queryByText('UZI 可用 · 字段审计提示')).not.toBeInTheDocument()
    expect(screen.queryByText(/字段缺口作为提示交给完整 Agent/)).not.toBeInTheDocument()
    expect(screen.getByText('em_push2: provider_unavailable (ConnectionError)')).toBeInTheDocument()
  })

  it('distinguishes partial, verified-empty, retryable and unavailable historical outcomes', async () => {
    const outcomeParent: CollectionJob = {
      ...parentJob,
      status: 'partial',
      expanded_holdings: holdings.map((holding, index) => ({
        ...holding,
        missing_dimensions: index === 0
          ? ['12_capital_flow', '19_contests']
          : [],
      })),
      portfolio_summary: {
        ...parentJob.portfolio_summary!,
        ready_holdings: 9,
        ready_slots: 218,
        missing_slots: 2,
        retryable_slots: 1,
        blocked_slots: 1,
      },
    }
    const outcomeChild: CollectionJob = {
      ...childJob,
      status: 'partial',
      symbols: [holdings[0].symbol],
      dimensions: ['10_valuation', '16_lhb', '12_capital_flow', '19_contests'],
      progress: { total: 4, queued: 0, running: 0, completed: 4, failed: 1, skipped: 0 },
      tasks: [
        {
          task_id: 'valuation-partial',
          symbol: holdings[0].symbol,
          dimension_id: '10_valuation',
          status: 'partial',
          attempt: 1,
          max_attempts: 3,
          retryable: false,
          collection_outcome: 'partial',
          result_message: '估值倍数可用，历史分位样本不足。',
          raw_data_present: true,
        },
        {
          task_id: 'lhb-empty',
          symbol: holdings[0].symbol,
          dimension_id: '16_lhb',
          status: 'succeeded',
          attempt: 1,
          collection_outcome: 'verified_empty',
        },
        {
          task_id: 'capital-retryable',
          symbol: holdings[0].symbol,
          dimension_id: '12_capital_flow',
          status: 'failed',
          attempt: 1,
          max_attempts: 3,
          retryable: true,
          collection_outcome: 'retryable_failure',
        },
        {
          task_id: 'contest-unavailable',
          symbol: holdings[0].symbol,
          dimension_id: '19_contests',
          status: 'partial',
          attempt: 1,
          retryable: false,
          collection_outcome: 'unavailable',
        },
      ],
    }

    render(<FundCollectionOverview
      job={outcomeParent}
      childJobs={[outcomeChild]}
      jobDetails={{ 'equity-child': outcomeChild }}
      actionLoading={false}
      onLoadDetail={vi.fn().mockResolvedValue(outcomeChild)}
      onRetry={vi.fn()}
    />)

    screen.getAllByRole('button', { name: /查看 22 维/ })[0].click()
    expect(await screen.findByText('部分恢复')).toBeInTheDocument()
    expect(screen.getByText('估值倍数可用，历史分位样本不足。')).toBeInTheDocument()
    expect(screen.getByText('查证为空')).toBeInTheDocument()
    expect(screen.getByText('数据源失败')).toBeInTheDocument()
    expect(screen.getByText('无历史档案')).toBeInTheDocument()
    expect(screen.getByText('该字段没有可证明的历史归档；不以当前值回填历史，保留缺口披露，评分按 UZI 原生结果。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `重试 ${holdings[0].symbol} 12_capital_flow` })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `重试 ${holdings[0].symbol} 19_contests` })).not.toBeInTheDocument()
  })
})
