import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MethodDeliveryDossier } from './MethodDeliveryDossier'

describe('MethodDeliveryDossier', () => {
  afterEach(cleanup)

  it('renders complete UZI native method and command artifacts', () => {
    render(<MethodDeliveryDossier artifacts={[
      {
        artifact_id: 'native-method-1', artifact_type: 'uzi_native_method_result', confidence: 0.8,
        content: {
          method_id: 'returns_attribution', quality_status: 'available',
          results: [{ symbol: 'portfolio', result: { holdings: [{ ticker: '600519', contribution_pct: 8.6 }], total_return_pct: 8.6 } }],
          data_gap: [], assumptions: [], source_commit: '1b5cdcb', implementation: 'vendored_uzi_3_9_2_pure_function',
        },
      },
      {
        artifact_id: 'native-command-1', artifact_type: 'uzi_native_command_result',
        content: { command_id: 'panel-only', phase: 'complete', quality_status: 'available', results: [{ symbol: '600519', panel_consensus: 62 }] },
      },
    ]} />)

    for (const value of ['returns_attribution', '600519', '8.6%', 'panel-only', '62']) {
      expect(screen.getAllByText(value).length).toBeGreaterThan(0)
    }
    expect(document.body.textContent).not.toContain('{"')
  })
})
