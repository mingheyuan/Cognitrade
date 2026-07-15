import { describe, expect, it } from 'vitest'
import { pathForView, viewFromPath } from './useViewNavigation'

describe('workspace URL navigation', () => {
  it('maps every workspace view to a stable deep link', () => {
    expect(pathForView('market')).toBe('/market-data')
    expect(pathForView('paper')).toBe('/paper-trading')
    expect(viewFromPath('/runs')).toBe('runs')
    expect(viewFromPath('/paper-trading/')).toBe('paper')
  })

  it('falls back to the workbench for unknown paths', () => {
    expect(viewFromPath('/unknown')).toBe('workbench')
  })
})
