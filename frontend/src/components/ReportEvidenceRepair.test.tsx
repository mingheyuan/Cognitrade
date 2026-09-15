import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CognitradeApiClient } from '../api-client'
import { ReportEvidenceRepair } from './ReportEvidenceRepair'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const request = { symbols: ['688256.SH'], asset_family: 'equity', start_date: '2026-09-02',
  end_date: '2026-09-02', dimensions: ['7_industry'], mode: 'missing_or_stale', max_concurrency: 5,
  publish_when_complete: false, include_research_archive: true,
  research_document_requests: [{ dimension: '7_industry', url: 'https://www.cninfo.com.cn/report.htm' }] }

function setup() {
  const client = {
    getReportRepairPlan: vi.fn().mockResolvedValue({ job_id: 'report', automatic_collection: false, items: [{
      symbol: '688256', as_of_date: '2026-09-02', recovery: { kind: 'evidence_pending', action: 'verify_sources' },
      dimensions: [{ dimension: '7_industry', reason: 'new_source_requires_verification', retryable: true }],
      pending_evidence: request.research_document_requests, collection_request: request, notes: [],
    }] }),
    collectReportEvidence: vi.fn().mockResolvedValue({ id: 'collection', status: 'running' }),
    getReportEvidenceCollection: vi.fn().mockResolvedValue({ id: 'collection', status: 'partial',
      can_confirm_freeze: true, publication_candidate: { proposal_hash: 'fixed-proposal', degraded: true },
      research_archive: { succeeded: 1, failed: 0, persisted_snapshot_count: 1 } }),
    confirmReportEvidenceCollection: vi.fn().mockResolvedValue({}),
    retryReportJob: vi.fn().mockResolvedValue({}),
  }
  const onRevised = vi.fn().mockResolvedValue(undefined)
  render(<ReportEvidenceRepair client={client as unknown as CognitradeApiClient} jobId="report" onRevised={onRevised} />)
  return { client, onRevised }
}

describe('explicit report evidence repair', () => {
  it('requires explicit acquisition, then publishes and revises only the selected stock', async () => {
    const { client, onRevised } = setup()
    expect(client.getReportRepairPlan).not.toHaveBeenCalled()
    expect(client.collectReportEvidence).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '查看补证与修订方案' }))
    await screen.findByText('https://www.cninfo.com.cn/report.htm')
    expect(client.collectReportEvidence).not.toHaveBeenCalled()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: '启动该股票补证' }))
    await waitFor(() => expect(client.collectReportEvidence).toHaveBeenCalledWith(request))
    await screen.findByText('数据中心任务 collection · running')
    expect(client.retryReportJob).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '刷新补证进度' }))
    fireEvent.click(await screen.findByRole('button', { name: '使用补证数据修订该股票报告' }))
    await waitFor(() => expect(client.confirmReportEvidenceCollection).toHaveBeenCalledWith('collection', 'fixed-proposal', true))
    await waitFor(() => expect(client.retryReportJob).toHaveBeenCalledWith('report', {
      symbols: ['688256'], refresh_inputs: true, confirmed: true,
    }))
    expect(onRevised).toHaveBeenCalledOnce()
  })

  it('does not acquire when confirmation is declined', async () => {
    const { client } = setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    fireEvent.click(screen.getByRole('button', { name: '查看补证与修订方案' }))
    fireEvent.click(await screen.findByRole('button', { name: '启动该股票补证' }))
    expect(client.collectReportEvidence).not.toHaveBeenCalled()
    expect(client.retryReportJob).not.toHaveBeenCalled()
  })

  it('does not start a report when publication fails', async () => {
    const { client } = setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    client.confirmReportEvidenceCollection.mockRejectedValue(new Error('候选已变化，请刷新'))
    fireEvent.click(screen.getByRole('button', { name: '查看补证与修订方案' }))
    fireEvent.click(await screen.findByRole('button', { name: '启动该股票补证' }))
    fireEvent.click(await screen.findByRole('button', { name: '刷新补证进度' }))
    fireEvent.click(await screen.findByRole('button', { name: '使用补证数据修订该股票报告' }))
    await screen.findByRole('alert')
    expect(client.retryReportJob).not.toHaveBeenCalled()
  })

  it('allows another explicit collection but not a report revision when no new source was archived', async () => {
    const { client } = setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    client.getReportEvidenceCollection.mockResolvedValue({ id: 'collection', status: 'partial',
      can_confirm_freeze: true, publication_candidate: { proposal_hash: 'unchanged-proposal', degraded: true },
      research_archive: { succeeded: 0, failed: 1, persisted_snapshot_count: 0 } })
    fireEvent.click(screen.getByRole('button', { name: '查看补证与修订方案' }))
    fireEvent.click(await screen.findByRole('button', { name: '启动该股票补证' }))
    fireEvent.click(await screen.findByRole('button', { name: '刷新补证进度' }))
    await screen.findByText(/本次尚未归档新的原文/)
    expect(screen.queryByRole('button', { name: '使用补证数据修订该股票报告' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '再次手动补证' }))
    await waitFor(() => expect(client.collectReportEvidence).toHaveBeenCalledTimes(2))
    expect(client.confirmReportEvidenceCollection).not.toHaveBeenCalled()
    expect(client.retryReportJob).not.toHaveBeenCalled()
  })
})
