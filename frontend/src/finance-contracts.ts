export type JsonRecord = Record<string, unknown>

export type SecuritySearchResult = {
  symbol: string
  name: string
  latest_price?: number | null
  pct_change?: number | null
  source?: string
}

export type SecuritySearchResponse = {
  query: string
  results: SecuritySearchResult[]
}

export type MarketQuote = {
  symbol: string
  name: string
  latest_price: number
  pct_change?: number | null
  as_of?: string | null
  source: JsonRecord
  quality_status: string
}

export type MarketDataResponse = {
  symbol: string
  name: string
  retrieved_at: string
  quality_status: string
  quality: Record<string, string>
  sources: Record<string, JsonRecord>
  sections: {
    market: {
      overview: JsonRecord
      history_summary: JsonRecord
    }
    kline: JsonRecord
    fundamental: JsonRecord
    capital_flow: JsonRecord
    sentiment: JsonRecord
    notices: JsonRecord
    events: JsonRecord
    lhb: JsonRecord
  }
}

export type PaperAccount = {
  account_id: string
  generation: number
  status: string
  currency: 'CNY'
  initial_cash: number
  cash: number
  created_at: string
  updated_at: string
  rules: {
    market: string
    board_lot: number
    settlement: string
    short_selling: boolean
    price_source: string
    market_hours_enforced: boolean
    calendar: string
    timezone: string
    sessions: string[]
  }
}

export type PaperPosition = {
  symbol: string
  name: string
  quantity: number
  available_quantity: number
  average_cost: number
  latest_price: number
  market_value: number
  cost_basis: number
  unrealized_pnl: number
  unrealized_pnl_pct?: number | null
  quote_as_of?: string | null
  quote_stale: boolean
}

export type PaperPortfolio = {
  account: PaperAccount
  summary: {
    cash: number
    market_value: number
    equity: number
    cost_basis: number
    total_pnl: number
    total_return_pct?: number | null
    position_count: number
  }
  positions: PaperPosition[]
  as_of: string
}

export type PaperOrder = {
  order_id: string
  client_order_id: string
  symbol: string
  name: string
  side: 'buy' | 'sell'
  quantity: number
  quote_price: number
  fill_price: number
  gross_amount: number
  fees: {
    commission: number
    transfer_fee: number
    stamp_duty: number
    total: number
  }
  cash_effect: number
  realized_pnl?: number | null
  quote_as_of?: string
  quote_source?: string
  trade_date?: string
  status: string
  created_at: string
  idempotent_replay: boolean
}

export type PaperOrderInput = {
  symbol: string
  side: 'buy' | 'sell'
  quantity: number
  client_order_id: string
}
