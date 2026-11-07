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

  test.each([
    [502, 'Bad Gateway'],
    [503, 'Service Unavailable'],
    [504, 'Gateway Timeout'],
  ])('非 JSON 的 %i 不回显浏览器英文状态文本', (status, statusText) => {
    const failure = describeFailure(null, status, statusText, null)
    expect(failure.message).not.toContain(statusText)
    expect(failure.message).toContain('服务暂时不可用')
    expect(failure.message).toContain('重试')
  })

  test('500 没有可读文案时给中性中文与下一步动作', () => {
    const failure = describeFailure(null, 500, 'Internal Server Error', null)
    expect(failure.message).not.toContain('Internal Server Error')
    expect(failure.message).toContain('没有完成')
    expect(failure.message).toContain('诊断包')
  })

  test('连不上服务（status 0）明确告诉用户先启动服务', () => {
    const failure = describeFailure(null, 0, '', null)
    expect(failure.message).toContain('无法连接到鎏灏服务')
    expect(failure.message).toContain('重试')
  })

  test.each([
    'FileNotFoundError: [Errno 2] No such file or directory',
    'ECONNREFUSED 127.0.0.1:8787',
    'Internal Server Error',
    'unknown_kernel',
  ])('英文异常与机器串不上屏：%s', (detail) => {
    const failure = describeFailure({ detail }, 400, '', null)
    // 400 没有专用兜底，走中性中文；正文里不允许出现英文异常原文。
    expect(failure.message).not.toContain(detail)
    expect(failure.message).toMatch(/[\u4e00-\u9fff]/)
  })

  test('对象形式的 detail 只取中文说明，不拼英文键名', () => {
    const failure = describeFailure(
      { detail: { code: 'agent_unavailable', message: '员工已停用，无法接受指派' } },
      409,
      '',
      null,
    )
    expect(failure.message).toBe('员工已停用，无法接受指派')
    expect(failure.code).toBe('agent_unavailable')
    expect(failure.message).not.toContain('agent_unavailable')
  })
})
