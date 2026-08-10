import { describe, expect, it } from 'vitest'

import { errorMessageWithRequestId, localizeErrorMessage } from './error-message'

describe('localizeErrorMessage', () => {
  it('keeps Chinese messages and proprietary field names', () => {
    expect(localizeErrorMessage('research_profile 为必填字段')).toBe('research_profile 为必填字段')
  })

  it('does not expose raw browser or provider errors', () => {
    expect(localizeErrorMessage('TypeError: Failed to fetch')).toContain('无法连接 Cognitrade API')
    expect(localizeErrorMessage("LLMProviderError: InternalServerError: Error code: 502")).toBe(
      '模型或外部数据服务暂时不可用（HTTP 502），请稍后重试。',
    )
    expect(localizeErrorMessage('行情加载失败：RemoteDisconnected')).not.toContain('RemoteDisconnected')
  })

  it('uses the request_id field name in diagnostic references', () => {
    expect(errorMessageWithRequestId('操作失败', 'req-1')).toBe('操作失败 · request_id req-1')
  })
})
