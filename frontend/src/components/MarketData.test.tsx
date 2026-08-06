import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MarketDataResponse } from '../finance-contracts'
import { MarketData, type MarketDataViewState } from './MarketData'

vi.mock('../CognitradeContext', () => ({
  useCognitrade: () => ({
    settings: { baseUrl: 'http://127.0.0.1:8000/api/v1', token: '' },
  }),
}))

const data: MarketDataResponse = {
  symbol: '600519',
  name: '贵州茅台',
  retrieved_at: '2026-08-05T10:00:00Z',
  quality_status: 'partial',
  quality: {
    market: 'available', kline: 'available', fundamental: 'available', capital_flow: 'available',
    sentiment: 'available', notice: 'available', events: 'available', lhb: 'data_gap',
  },
  sources: {
    market: { provider_id: 'akshare.market', vendor: 'eastmoney' },
    kline: { provider_id: 'akshare.kline', vendor: 'eastmoney' },
    fundamental: { provider_id: 'akshare.fundamental', vendor: 'eastmoney' },
    capital_flow: { provider_id: 'akshare.capital_flow', vendor: 'eastmoney' },
    sentiment: { provider_id: 'akshare.sentiment', vendor: 'eastmoney' },
    notice: { provider_id: 'cninfo.notice', vendor: 'cninfo' },
    events: { provider_id: 'cninfo.events', vendor: 'cninfo' },
    lhb: {},
  },
  sections: {
    market: {
      overview: { latest_price: 1500, pct_change: 1.2, custom_business_metric: '行情补充值' },
      history_summary: { latest_bar: { date: '2026-08-05', open: 1490, high: 1510, low: 1480, close: 1500, volume: 10 } },
    },
    kline: { bars: [{ date: '2026-08-05', open: 1490, high: 1510, low: 1480, close: 1500, volume: 10 }] },
    fundamental: { info: { name: '贵州茅台' }, extra_block: { hidden_metric: '基本面补充值' } },
    capital_flow: { latest_flow: { main_net_inflow: 100 } },
    sentiment: {
      hot_keywords: Array.from({ length: 17 }, (_, index) => `关键词${index + 1}`),
      event_note: '热度补充值',
    },
    notices: { notice_count: 13, window_start: '2026-07-01', window_end: '2026-08-05', recent_notices: [] },
    events: {
      event_keywords: ['分红'],
      event_notices: Array.from({ length: 13 }, (_, index) => ({
        date: `2026-08-${String(index + 1).padStart(2, '0')}`,
        title: `事件${index + 1}`,
        type: '公告',
      })),
    },
    lhb: {},
  },
}

describe('MarketData', () => {
  afterEach(cleanup)

  it('exposes every returned business section without raw JSON or silent slicing', () => {
    const state: MarketDataViewState = { query: '600519', data, loading: false, error: '' }
    render(<MarketData state={state} setState={vi.fn()} />)

    expect(screen.getByText('行情补充值')).toBeInTheDocument()
    expect(screen.getByText('基本面补充值')).toBeInTheDocument()
    expect(screen.getByText('关键词17')).toBeInTheDocument()
    expect(screen.getByText('数据来源与完整性')).toBeInTheDocument()
    expect(screen.getByText('实时行情与估值')).toBeInTheDocument()
    expect(screen.queryByText(/\{"latest_price"/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '查看全部 13 条事件' }))
    expect(screen.getByText('事件13')).toBeInTheDocument()
  })
})
