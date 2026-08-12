import { describe, expect, it } from 'vitest'
import { pathForView, viewFromPath } from './useViewNavigation'

describe('workspace URL navigation', () => {
  it('maps every workspace view to a stable deep link', () => {
    expect(pathForView('market')).toBe('/market-data')
    expect(pathForView('funds')).toBe('/funds')
    expect(pathForView('paper')).toBe('/paper-trading')
    expect(pathForView('strategies')).toBe('/strategies')
    expect(viewFromPath('/runs')).toBe('runs')
    expect(viewFromPath('/paper-trading/')).toBe('paper')
    expect(viewFromPath('/backtesting/')).toBe('workbench')
    expect(viewFromPath('/funds/')).toBe('funds')
    expect(pathForView('strategies', '/Cognitrade/')).toBe('/Cognitrade/strategies')
    expect(viewFromPath('/Cognitrade/strategies', '/Cognitrade/')).toBe('strategies')
  })

  it('falls back to the workbench for unknown paths', () => {
    expect(viewFromPath('/unknown')).toBe('workbench')
  })
})
