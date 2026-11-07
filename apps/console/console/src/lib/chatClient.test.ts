import { afterEach, describe, expect, test, vi } from 'vitest'
import {
  chatFailureText,
  friendlyChatError,
  makeSessionTitle,
  streamChat,
} from './chatClient'
import type { ChatEvent } from './chatClient'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamChat 结束帧', () => {
  test('done{status:error, reason:quota_exhausted} 必须透出，不能当成成功', async () => {
    const frames =
      'data: {"type":"token","content":"这次没有拿到回复"}\n\n' +
      'data: {"type":"done","status":"error","reason":"quota_exhausted","error":"这次没有拿到回复"}\n\n'
    vi.stubGlobal('fetch', vi.fn(async () => new Response(frames, { status: 200 })))

    const events: ChatEvent[] = []
    for await (const evt of streamChat('你好', 's1')) events.push(evt)

    expect(events[0]).toEqual({ type: 'token', content: '这次没有拿到回复' })
    const done = events.find((evt) => evt.type === 'done')
    expect(done).toBeTruthy()
    if (done?.type === 'done') {
      expect(done.status).toBe('error')
      expect(done.reason).toBe('quota_exhausted')
      expect(done.error).toBe('这次没有拿到回复')
    }
  })

  test('成功结束帧同样透出 status=completed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('data: {"type":"done","status":"completed"}\n\n')),
    )
    const events: ChatEvent[] = []
    for await (const evt of streamChat('你好', 's1')) events.push(evt)
    expect(events[0]).toMatchObject({ type: 'done', status: 'completed' })
  })
})

describe('chatFailureText', () => {
  test('按后端 reason 给出可行动文案', () => {
    expect(chatFailureText('quota_exhausted')).toContain('额度已用完')
    expect(chatFailureText('key_revoked')).toContain('吊销')
    expect(chatFailureText('rate_limited')).toContain('稍后重试')
    expect(chatFailureText('unreachable')).toContain('网络')
  })

  test('没有分类时用后端中性说明兜底，再没有才给通用文案', () => {
    expect(chatFailureText(undefined, '后端的中性说明')).toBe('后端的中性说明')
    expect(chatFailureText('')).toBe('这一轮没有成功，请稍后重试。')
  })
})

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
      '鎏灏的本地服务没有运行。请打开鎏灏（启动窗口应保持打开）后重试；刚启动的话请等几秒。',
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
