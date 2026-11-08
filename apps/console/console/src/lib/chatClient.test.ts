import { afterEach, describe, expect, test, vi } from 'vitest'
import {
  chatFailureText,
  friendlyChatError,
  makeSessionTitle,
  streamChat,
} from './chatClient'
import { ApiError } from './api'
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

  test('物理 402（建流前拒绝）抛 ApiError，并保留 code 供文案分流', async () => {
    // 后端这次在建流前就返回 402：响应体不是 SSE，fetch 拿到的是 JSON。
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              code: 'budget_exceeded',
              message: '本次调用会超出你设定的预算，请调整额度或换更便宜的模型。',
              correlation_id: '156a7c18547a47bb',
            }),
            { status: 402, headers: { 'Content-Type': 'application/json' } },
          ),
      ),
    )
    let caught: unknown
    try {
      for await (const _evt of streamChat('你好', 's1')) {
        // 不应产出任何事件：402 在建流前就把请求拒掉了。
        expect.fail('物理 402 不应建立事件流')
      }
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).status).toBe(402)
    expect((caught as ApiError).code).toBe('budget_exceeded')
    expect(friendlyChatError(caught).action).toBe('budget')
  })
})

describe('chatFailureText', () => {
  test('按后端 reason 给出可行动文案', () => {
    expect(chatFailureText('quota_exhausted')).toContain('额度已用完')
    expect(chatFailureText('key_revoked')).toContain('吊销')
    expect(chatFailureText('rate_limited')).toContain('稍后重试')
    expect(chatFailureText('unreachable')).toContain('网络')
    // 后端给了带模型名的说明时，卡片必须显示它，而不是丢掉模型名。
    expect(
      chatFailureText('unreachable', '连不上模型服务，请确认本地服务已启动或网络可用。（模型：qwen2.5:7b）'),
    ).toContain('模型：qwen2.5:7b')
  })

  test('没有分类时用后端中性说明兜底，再没有才给通用文案', () => {
    expect(chatFailureText(undefined, '后端的中性说明')).toBe('后端的中性说明')
    expect(chatFailureText('')).toBe('这一轮没有成功，请稍后重试。')
  })
})

describe('friendlyChatError 402/403 口径', () => {
  test('402 budget_exceeded 说预算并给出可点的下一步，不误报权限', () => {
    const err = friendlyChatError(new ApiError(402, '', 'budget_exceeded'))
    expect(err.text).toContain('超出你设定的预算')
    expect(err.text).toContain('我的 Key')
    expect(err.text).toContain('模型管理')
    expect(err.text).not.toContain('权限')
    expect(err.action).toBe('budget')
  })

  test('403 permission_denied 才说权限，且不提预算', () => {
    const err = friendlyChatError(new ApiError(403, '', 'permission_denied'))
    expect(err.text).toContain('权限不足')
    expect(err.text).not.toContain('预算')
    expect(err.action).toBeUndefined()
  })

  test('403 ACCOUNT_KEY_NOT_OWNED 说「无权使用该模型配置」，且不是预算问题', () => {
    const err = friendlyChatError(
      new ApiError(403, '无权使用该模型配置', 'ACCOUNT_KEY_NOT_OWNED'),
    )
    expect(err.text).toContain('无权使用该模型配置')
    expect(err.text).toContain('我的 Key')
    expect(err.text).not.toContain('预算')
    expect(err.action).toBeUndefined()
  })

  test('物理 429 的 ApiError 只带中文说明，也能落到限流文案而不是通用兜底', () => {
    const err = friendlyChatError(
      new ApiError(429, '请求过于频繁，请稍后再试（rate limit exceeded）', 'RATE_LIMITED_PRINCIPAL'),
    )
    expect(err.text).toBe('请求过于频繁，请稍后重试')
    expect(err.action).toBeUndefined()
  })

  test('409 ACCOUNT_KEY_NOT_CONFIGURED 直接指路「我的 Key」，不暴露英文 code', () => {
    const err = friendlyChatError(
      new ApiError(
        409,
        '当前工作区暂未配置可用模型，请先添加自己的模型',
        'ACCOUNT_KEY_NOT_CONFIGURED',
      ),
    )
    expect(err.text).toContain('自己的模型 Key')
    expect(err.text).toContain('我的 Key')
    expect(err.text).toContain('添加')
    expect(err.text).not.toContain('ACCOUNT_KEY_NOT_CONFIGURED')
  })

  test('聊天失败文案同步带上调额度/换模型的出口', () => {
    const text = chatFailureText('budget_exceeded')
    expect(text).toContain('预算')
    expect(text).toContain('我的 Key')
    expect(text).toContain('更便宜的模型')
  })
})

describe('friendlyChatError', () => {
  test('Ollama 404 翻译为"模型不存在"，原始 JSON 保留在详情', () => {
    const raw = '请求失败（404）：{"detail":"Ollama error 404: model not found"}'
    const err = friendlyChatError(new Error(raw))
    expect(err.text).toBe('模型不存在，请在模型管理中确认模型名')
    expect(err.raw).toContain('Ollama error 404')
  })

  test('422 超长消息给出上限说明，且不把 5 万字原文塞进详情', () => {
    const raw = `请求失败（422）：{"detail":[{"type":"string_too_long","loc":["body","message"],"msg":"String should have at most 8000 characters","input":"${'A'.repeat(50000)}"}]}`
    const err = friendlyChatError(new Error(raw))
    expect(err.text).toContain('最多 8000 字')
    expect(err.raw.length).toBeLessThan(700)
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
