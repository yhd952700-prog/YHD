import { describe, expect, test } from 'vitest'
import { describeFailure } from './errorMessage'

describe('后端错误文案可读化', () => {
  test('429 不把英文限流原文铺给用户', () => {
    // 后端真实返回：`请求过于频繁，请稍后再试（rate limit exceeded）`
    const failure = describeFailure(
      { detail: '请求过于频繁，请稍后再试（rate limit exceeded）' },
      429,
      'Too Many Requests',
      null,
    )
    expect(failure.message).toBe('请求过于频繁，请稍后再试')
  })

  test('429 仍保留追踪 ID，便于排障', () => {
    const failure = describeFailure({ detail: 'rate limit exceeded' }, 429, '', 'trace-123')
    expect(failure.message).toContain('追踪 ID：trace-123')
    expect(failure.message).not.toContain('rate limit exceeded')
  })

  test('非 429 保留服务端真实原因', () => {
    const failure = describeFailure({ detail: '该账号已存在' }, 409, '', null)
    expect(failure.message).toBe('该账号已存在')
  })
})
