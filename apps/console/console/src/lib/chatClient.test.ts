import { afterEach, describe, expect, test, vi } from 'vitest'
import {
  ChatStreamError,
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
              message: '本次调用会超出可用预算（按保守 token 估算），请调整额度或换更便宜的模型。',
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
    // 证书失败三类动作不同：换网络 / 核对地址 / 核对时间，不能都叫"连不上"。
    expect(chatFailureText('certificate_untrusted')).toContain('信任来源不同')
    expect(chatFailureText('certificate_untrusted')).toContain('网络管理员')
    expect(chatFailureText('certificate_untrusted')).not.toContain('让系统信任该证书')
    expect(chatFailureText('certificate_hostname')).toContain('不匹配')
    expect(chatFailureText('certificate_expired')).toContain('已过期')
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
  test('402 budget_exceeded 说清额度来源与查看用量入口，不误报权限', () => {
    const err = friendlyChatError(new ApiError(402, '', 'budget_exceeded'))
    expect(err.text).toContain('超出当前额度')
    expect(err.text).toContain('额度由管理员在启动配置中设定')
    expect(err.text).toContain('重启鎏灏')
    expect(err.text).toContain('我的 Key')
    expect(err.text).toContain('更便宜的模型')
    // 页面只做只读用量，不能承诺「去那里调整额度」。
    expect(err.text).not.toContain('请到「我的 Key → 用量」调整额度')
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

  test('聊天失败文案说明额度来源，并给查看用量/换模型的出口', () => {
    const text = chatFailureText('budget_exceeded')
    expect(text).toContain('超出当前额度')
    expect(text).toContain('额度由管理员在启动配置中设定')
    expect(text).toContain('我的 Key')
    expect(text).toContain('更便宜的模型')
    expect(text).not.toContain('请到「我的 Key → 用量」调整额度')
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
      '鎏灏的本地服务没有运行。请打开鎏灏后重试；刚启动的话请等几秒。',
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

describe('streamChat notice 与中断兜底', () => {
  test('notice 帧原样透出：状态行可见，且不混进正文 token', async () => {
    const frames =
      'data: {"type":"notice","code":"retrying","message":"正在重试…"}\n\n' +
      'data: {"type":"token","content":"好"}\n\n' +
      'data: {"type":"done","status":"completed"}\n\n'
    vi.stubGlobal('fetch', vi.fn(async () => new Response(frames, { status: 200 })))

    const events: ChatEvent[] = []
    for await (const evt of streamChat('你好', 's1')) events.push(evt)

    expect(events[0]).toEqual({
      type: 'notice',
      code: 'retrying',
      message: '正在重试…',
    })
    // notice 只更新状态行：正文里只应有模型真正产出的那一个字。
    expect(events.filter((evt) => evt.type === 'token')).toEqual([
      { type: 'token', content: '好' },
    ])
  })

  test('流结束但没有 done 帧：抛 stream_interrupted，不能当成成功', async () => {
    // 后端异常没补 done（或被中间层掐断）：只有半截 token 就 EOF。
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response('data: {"type":"token","content":"半截"}\n\n', { status: 200 }),
      ),
    )

    let caught: unknown
    try {
      for await (const _evt of streamChat('你好', 's1')) {
        // 收到半截 token 是允许的；错的是把它当完整回答。
      }
    } catch (err) {
      caught = err
    }
    expect(caught).toBeInstanceOf(ChatStreamError)
    expect((caught as ChatStreamError).code).toBe('stream_interrupted')
    // 文案仍然只从 chatFailureText 出。
    expect(friendlyChatError(caught).text).toBe('连接中断，请重试。')
  })

  test('chatFailureText 覆盖客户端新增的两个中断分类，且不改 402 既有文案', () => {
    expect(chatFailureText('stream_timeout')).toBe('等待模型响应超时，请重试。')
    expect(chatFailureText('stream_interrupted')).toBe('连接中断，请重试。')
    expect(chatFailureText('budget_exceeded')).toContain('超出当前额度')
  })
})

/**
 * 防回归：上游原文（盘符路径 / 英文异常名）只允许进折叠的技术详情，
 * 绝不能出现在聊天失败卡片的主文案里。输入统一使用带
 * `C:\srv\gateway.py line 42` 的假上游响应。
 */
describe('失败主文案永不回显上游原文', () => {
  const LEAKY_UPSTREAM = 'Internal Server Error: C:\\srv\\gateway.py line 42'

  function expectCleanMainText(text: string) {
    expect(text).not.toMatch(/[A-Za-z]:\\/)
    expect(text).not.toContain('gateway.py')
    expect(text).not.toContain('Internal Server Error')
  }

  test('friendlyChatError 5xx：带盘符路径的上游原文不进主文案', () => {
    const raw = `请求失败（502）：{"detail":"${LEAKY_UPSTREAM}"}`
    const err = friendlyChatError(new Error(raw))
    expect(err.text).toBe('AI 服务暂时不可用，请稍后重试')
    expectCleanMainText(err.text)
    // 原文仍保留在 raw 里，供「详情」折叠使用，但界面不渲染它。
    expect(err.raw).toContain('gateway.py')
  })

  test('friendlyChatError 未分类失败：只给中文兜底，路径留在 raw', () => {
    const raw = '请求失败（400）：读取 C:\\srv\\gateway.py line 42 时失败'
    const err = friendlyChatError(new Error(raw))
    expect(err.text).toBe('对话失败，请稍后重试')
    expectCleanMainText(err.text)
    expect(err.raw).toContain('gateway.py')
  })

  test('chatFailureText 链式失败：剥掉「服务端返回」段与上游原文', () => {
    const fallback = `所有已配置的模型都没有成功返回（HTTP 500）；服务端返回：{"error":{"message":"${LEAKY_UPSTREAM}"}}`
    for (const reason of ['unreachable', undefined]) {
      const text = chatFailureText(reason, fallback)
      expect(text).toContain('HTTP 500')
      expect(text).not.toContain('服务端返回')
      expectCleanMainText(text)
    }
  })
})
