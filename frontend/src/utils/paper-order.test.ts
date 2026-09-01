import { describe, expect, it } from 'vitest'
import { estimateOrder } from './paper-order'

describe('paper order estimate', () => {
  it('includes slippage and all buy-side fees in occupied cash', () => {
    const estimate = estimateOrder(10, 'buy', 100)

    expect(estimate.gross).toBeCloseTo(1000.5, 6)
    expect(estimate.fees).toBeCloseTo(5.010005, 6)
    expect(estimate.total).toBeCloseTo(1005.510005, 6)
  })

  it('subtracts sell fees from returned cash', () => {
    const estimate = estimateOrder(10, 'sell', 100)

    expect(estimate.total).toBeLessThan(estimate.gross)
    expect(estimate.fees).toBeGreaterThanOrEqual(5)
  })
})
