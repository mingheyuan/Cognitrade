import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { IntentDeliveryDossier } from './IntentDeliveryDossier'

describe('IntentDeliveryDossier', () => {
  afterEach(cleanup)

  it('renders every screening candidate, note and enrichment target without truncation', () => {
    const candidates = Array.from({ length: 11 }, (_, index) => ({
      symbol: `600${String(index).padStart(3, '0')}`,
      name: `完整候选-${index + 1}`,
      score: 10 - index / 10,
      deep_checked: index % 2 === 0,
      why_it_made_the_list: `候选理由-${index + 1}`,
    }))
    render(<IntentDeliveryDossier artifacts={[{
      artifact_id: 'screening-1',
      artifact_type: 'screening_result',
      content: {
        screened_candidates: candidates,
        watchlist_candidates: [{ symbol: '999999', name: '完整观察候选', score: 1 }],
        enrichment_targets: ['600000', '600010'],
        scorecard: { candidate_count: 12, eligible_candidate_count: 11, deep_checked_count: 6 },
        methodology: { screen_style: 'quality' },
        screening_notes: ['完整筛选说明-不得遗漏'],
      },
    }]} />)

    expect(screen.getByText('完整候选-11')).toBeInTheDocument()
    expect(screen.getByText('完整观察候选')).toBeInTheDocument()
    expect(screen.getByText('完整筛选说明-不得遗漏')).toBeInTheDocument()
    expect(screen.getAllByText('600010').length).toBeGreaterThan(0)
    expect(document.body.textContent).not.toContain('{"')
  })

  it('renders all sector, thesis and distillation evidence', () => {
    render(<IntentDeliveryDossier artifacts={[
      {
        artifact_id: 'sector-1',
        artifact_type: 'sector_analysis',
        content: {
          sector: '半导体', theme: '国产替代', source_count: 2,
          sector_heatmap: [{ name: '先进封装', score: 88, raw_value: 7.6 }],
          constituents: [{ symbol: '688001', name: '成分样本-完整' }],
          findings: [{ snapshot_type: 'sector', facts: { full_fact: '板块完整事实' } }],
          related_snapshots: [{ snapshot_type: 'events', facts: { event: '完整事件证据' } }],
          breadth: { advancers: 18 }, relative_strength: { month_1: 6.5 }, signal_scorecard: { quality: 0.8 },
          catalysts: { theme_news: ['全部主题新闻'], policy_signals: ['全部政策信号'], regulatory_signals: [], samples: [] },
          policy_snapshots: [{ snapshot_type: 'policy', facts: { title: '完整政策快照' } }],
          risks: ['完整板块风险'], analysis_notes: ['完整分析说明'],
        },
      },
      {
        artifact_id: 'thesis-1',
        artifact_type: 'thesis_review',
        content: {
          verdict: 'inconclusive', overall_verdict: 'inconclusive', review_target_run_id: 'run-old', review_target_found: true,
          review_target_artifact_ids: ['artifact-old-1'], target_thesis: [{ claim: '完整历史投资逻辑' }],
          evidence_comparison: { target_artifact_count: 1, fresh_evidence_count: 2, fresh_snapshot_types: ['market'], fresh_symbols: ['600519'] },
          findings: [{ claim: '完整新增发现' }], strengths: [{ summary: '完整增强证据' }], weaknesses: ['完整削弱证据'], follow_up: ['完整后续动作'],
        },
      },
      {
        artifact_id: 'source-1', artifact_type: 'source_record_set',
        content: { query: '蒸馏问题', records: [{ record_id: 'record-1', text: '完整原始观点', author: '研究员甲' }] },
      },
      {
        artifact_id: 'result-1', artifact_type: 'distillation_result',
        content: { summary: { source_record_count: 1, dimension_count: 1 }, dimensions: [{ dimension_name: '竞争优势', claim: '完整蒸馏结论' }] },
      },
      {
        artifact_id: 'review-1', artifact_type: 'distillation_review',
        content: { verdict: 'needs_review', findings: [{ reason: '完整审核意见' }], distillation: { dimensions: [{ claim: '审核内完整蒸馏' }] } },
      },
    ]} />)

    for (const value of [
      '板块完整事实', '完整事件证据', '完整政策快照', '完整板块风险', '完整历史投资逻辑',
      '完整新增发现', '完整增强证据', '完整削弱证据', '完整后续动作', '完整原始观点',
      '完整蒸馏结论', '完整审核意见', '审核内完整蒸馏',
    ]) expect(screen.getByText(value)).toBeInTheDocument()
  })
})
