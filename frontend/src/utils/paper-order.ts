export function estimateOrder(price: number | undefined, side: 'buy' | 'sell', quantity: number) {
  if (!price || quantity <= 0) return { gross: 0, fees: 0, total: 0 }
  const fill = price * (side === 'buy' ? 1.0005 : 0.9995)
  const gross = fill * quantity
  const commission = Math.max(gross * 0.00025, 5)
  const transfer = gross * 0.00001
  const stamp = side === 'sell' ? gross * 0.0005 : 0
  const fees = commission + transfer + stamp
  return { gross, fees, total: side === 'buy' ? gross + fees : gross - fees }
}
