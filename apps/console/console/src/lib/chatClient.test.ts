import { describe, expect, test } from 'vitest'
import { friendlyChatError, makeSessionTitle } from './chatClient'

describe('friendlyChatError', () => {
  test('Ollama 404 翻译为"模型不存在"，原始 JSON 保留在详情', () => {
    const raw = '请求失败（404）：{"detail":"Ollama error 404: model not found"}'
    const err = friendlyChatError(new Error(raw))
    expect(err.text).toBe('模型不存在，请在模型管理中确认模型名')
    expect(err.raw).toContain('Ollama error 404')
  })

  test('401 会话失效', () => {
    expect(friendlyChatError(new Error('请求失败（401）：Unauthorized')).text).toBe(
      '会话已失效，请重新登录',
    )
  })

  test('网络不可达', () => {
    expect(friendlyChatError(new TypeError('Failed to fetch')).text).toBe(
      '无法连接到本地服务，请确认服务已启动',
    )
  })

  test('5xx 服务端错误', () => {
    expect(friendlyChatError(new Error('请求失败（502）：bad gateway')).text).toBe(
      'AI 服务暂时不可用，请稍后重试',
    )
  })

  test('未知错误也有中文兜底，绝不直出原始英文', () => {
    const err = friendlyChatError(new Error('boom'))
    expect(err.text).toBe('对话失败，请稍后重试')
  })
})

describe('makeSessionTitle', () => {
  test('压平空白并截断到 16 字', () => {
    expect(makeSessionTitle('  帮我\n\n分析   华东区销售 ')).toBe('帮我 分析 华东区销售')
    expect(makeSessionTitle('a'.repeat(30))).toHaveLength(17)
    expect(makeSessionTitle('a'.repeat(30)).endsWith('…')).toBe(true)
  })

  test('空消息不产生标题（界面回退到"未命名会话"）', () => {
    expect(makeSessionTitle('   ')).toBe('')
  })
})
