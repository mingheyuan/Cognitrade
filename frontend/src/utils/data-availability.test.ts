import { describe, expect, it } from 'vitest'
import { dataUnavailableMessage, isTechnicalErrorMessage } from './data-availability'

describe('data availability presentation', () => {
  it('renders stable product messages for provider statuses', () => {
    expect(dataUnavailableMessage({ availability: 'no_data' })).toBe('当前查询范围内没有相关记录')
    expect(dataUnavailableMessage({ availability: 'rate_limited' })).toContain('请求频率受限')
    expect(dataUnavailableMessage({ availability: 'parse_failed' })).toContain('无法解析')
  })

  it('does not expose raw upstream connection details', () => {
    const raw = "individual_info_unavailable: Connection aborted RemoteDisconnected"
    expect(isTechnicalErrorMessage(raw)).toBe(true)
    expect(dataUnavailableMessage({ availability: 'provider_unavailable', note: raw })).toBe('公司资料暂不可用，请稍后刷新')
  })
})
