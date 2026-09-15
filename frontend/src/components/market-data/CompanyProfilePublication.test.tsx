import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MarketDataCenterApi, normalizeMarketDataContract, normalizeMarketDataCoverage } from './api'
import { CollectionJobsPanel } from './CollectionJobsPanel'
import { ConfirmBundleFreezeDialog } from './dialogs'
import { ReadinessSummary } from './ReadinessSummary'

const pending = {
  version: 'company-profile-dependency-check-v1', status: 'pending', can_freeze: false,
  input_basic_snapshot_id: 'new-basic', input_basic_snapshot_hash: 'a'.repeat(64),
  dependency_fingerprint: 'b'.repeat(64), required_dimensions: ['4_peers', '7_industry'],
  direct_dimensions: ['4_peers', '7_industry'], review_dimensions: [],
  pending_dimensions: ['4_peers', '7_industry'],
  issues: [{ dimension: '7_industry', reason: 'dependency_receipt_missing' }],
}
const contract = normalizeMarketDataContract({
  contract_version: 'fixture', expected_dimension_count: 22, asset_family: 'equity',
  dimensions: [{ dimension: '7_industry', ordinal: 7, label: '行业' }],
})
const rawCoverage = {
  symbol: '600519.SH', as_of_date: '2026-08-20', market: 'CN_EQUITY',
  report_ready: true, report_generatable: true, structurally_complete: true,
  uzi_integrity: { critical_count: 0, coverage_pct: 100 },
  company_profile_dependencies: pending,
}
const rawJob = {
  id: 'profile-job', status: 'succeeded', asset_family: 'equity',
  symbols: ['600519.SH'], start_date: '2026-08-20', end_date: '2026-08-20',
  dimensions: ['7_industry'], freeze_status: 'ready', can_confirm_freeze: true,
  publication_candidate: { proposal_hash: 'c'.repeat(64), can_publish: true },
  company_profile_dependencies: pending,
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function job(payload: unknown = rawJob) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload), {
    headers: { 'Content-Type': 'application/json' },
  })))
  return new MarketDataCenterApi({ baseUrl: 'http://offline.test/api/v1', token: '' }).getJob('profile-job')
}

describe('company profile input-version publication state', () => {
  it('retains the independent coverage result without changing native counts', () => {
    const coverage = normalizeMarketDataCoverage(rawCoverage)
    expect(coverage).toMatchObject({ company_profile_dependencies: pending, report_ready: true, report_generatable: true })
    expect(coverage.uzi_integrity?.critical_count).toBe(0)
  })

  it('shows pending input versions separately from the successful native check', () => {
    render(<ReadinessSummary contract={contract} coverage={normalizeMarketDataCoverage(rawCoverage)} />)
    expect(screen.getByText('输入版本待补齐')).toBeInTheDocument()
    expect(screen.getByText(/4_peers.*7_industry/)).toBeInTheDocument()
    expect(screen.getByText('通过 · 100%')).toBeInTheDocument()
  })

  it('does not infer a dependency block from a correctly bound source-quality gap', () => {
    const coverage = normalizeMarketDataCoverage({ ...rawCoverage, report_ready: false,
      company_profile_dependencies: { ...pending, status: 'ready', can_freeze: true, pending_dimensions: [], issues: [] },
    })
    render(<ReadinessSummary contract={contract} coverage={coverage} />)
    expect(screen.queryByText('输入版本待补齐')).not.toBeInTheDocument()
    expect(coverage.report_generatable).toBe(true)
  })

  it('preserves the job diagnostic and blocks a stale positive confirmation flag', async () => {
    const normalized = await job()
    expect(normalized).toMatchObject({ company_profile_dependencies: pending, can_confirm_freeze: false })
    render(<CollectionJobsPanel contract={contract}
      urlState={{ symbol: '600519.SH', market: 'CN_EQUITY', asset_family: 'equity', as_of_date: '2026-08-20', cutoff: 'market_close', tab: 'jobs', dimension: '' }}
      jobs={{ items: [normalized], total_count: 1, limit: 50, offset: 0 }}
      jobDetails={{ 'profile-job': normalized }} loading={false} actionLoading={false}
      onCreate={vi.fn()} onRefresh={vi.fn()} onLoadDetail={vi.fn()}
      onRetry={vi.fn()} onAgentRecovery={vi.fn()} onConfirmFreeze={vi.fn()} onCancel={vi.fn()} />)
    expect(screen.getAllByText('输入版本待补齐').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '确认冻结 Bundle' })).not.toBeInTheDocument()
  })

  it('keeps old payloads without the optional diagnostic compatible', async () => {
    const normalized = await job({ ...rawJob, company_profile_dependencies: undefined })
    expect(normalized.can_confirm_freeze).toBe(true)
    expect(normalized.freeze_status).toBe('awaiting_confirmation')
  })

  it('cannot submit a known-inconsistent candidate even from an already opened dialog', async () => {
    const onSubmit = vi.fn()
    render(<ConfirmBundleFreezeDialog job={await job()} busy={false} onClose={vi.fn()} onSubmit={onSubmit} />)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(screen.getByRole('button', { name: '确认冻结 Bundle' })).toBeDisabled()
    fireEvent.submit(screen.getByRole('button', { name: '确认冻结 Bundle' }).closest('form')!)
    expect(onSubmit).not.toHaveBeenCalled()
  })
})
