import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MethodDeliveryDossier } from './MethodDeliveryDossier'

describe('MethodDeliveryDossier', () => {
  afterEach(cleanup)

  it('renders every panel vote, model result, portfolio row, gap and assumption', () => {
    const votes = Array.from({ length: 13 }, (_, index) => ({
      perspective_id: `seat-${index + 1}`,
      perspective_label: `完整席位-${index + 1}`,
      signal: 'neutral', score: index, reasoning: `完整评审理由-${index + 1}`,
    }))
    render(<MethodDeliveryDossier artifacts={[
      {
        artifact_id: 'valuation-1', artifact_type: 'valuation_model', confidence: 0.8,
        content: { methodology_version: '3.9.2', quality_status: 'available', models: [{ symbol: '600519', fair_value: 1800, sensitivity: [{ fair_value: 1700 }, { fair_value: 1900 }], assumptions: { wacc: 0.09 } }] },
      },
      {
        artifact_id: 'panel-1', artifact_type: 'investor_panel_review',
        content: { quality_status: 'available', votes, distribution: { bullish: 2 }, disagreement: { score_std: 3 } },
      },
      {
        artifact_id: 'portfolio-1', artifact_type: 'holding_rebalance',
        content: { quality_status: 'assumption_only', drift_table: [{ ticker: '600000', action: 'reduce' }, { ticker: '000001', action: 'add' }], trades: [{ ticker: '000001', delta_weight: 2 }], data_gap: ['完整组合缺口'], assumption_flags: ['完整组合假设'] },
      },
      {
        artifact_id: 'deep-1', artifact_type: 'deep_analysis',
        content: { results: Array.from({ length: 8 }, (_, index) => ({ symbol: `完整深度标的-${index + 1}` })), summary: '完整 Deep 摘要' },
      },
    ]} />)

    for (const value of ['完整席位-13', '完整评审理由-13', '1,900', '完整组合缺口', '完整组合假设', '完整深度标的-8', '完整 Deep 摘要']) {
      expect(screen.getByText(value)).toBeInTheDocument()
    }
    expect(document.body.textContent).not.toContain('{"')
  })
})
