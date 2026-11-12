/**
 * 对话面板的失败态回归。
 *
 * 后端在 HTTP 200 的流里用 `done{status:error}` 表示这一轮失败；
 * 前端必须渲染失败卡片，而不是把失败文案当成普通回复。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ChatPanel, greetingForHour } from './ChatPanel'
import { UNKNOWN_PROVIDER } from '../lib/providerStatus'

/** 测试环境没有 localStorage（Node 提示 --localstorage-file 未提供），补一个内存实现。 */
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() {
      return data.size
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => [...data.keys()][index] ?? null,
    removeItem: (key: string) => {
      data.delete(key)
    },
    setItem: (key: string, value: string) => {
      data.set(key, String(value))
    },
  } as Storage
}

beforeEach(() => {
  vi.stubGlobal('localStorage', memoryStorage())
})

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function sse(frames: string[]): Response {
  return new Response(frames.join(''), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChatPanel 首屏问候', () => {
  test('四个时段都称呼用户为 Boss', () => {
    for (const hour of [2, 8, 14, 20]) {
      expect(greetingForHour(hour)).toContain('Boss')
    }
  })

  test('首屏问候语渲染出来就带 Boss 称呼', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    expect(await screen.findByText(/Boss/)).toBeTruthy()
  })
})

describe('ChatPanel 结束帧', () => {
  test('done{status:error, reason:quota_exhausted} 渲染额度卡片，不把失败文案当普通回复', async () => {
    const failure = '这次没有拿到回复，请稍后再试。'
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return sse([
          `data: {"type":"token","content":"${failure}"}\n\n`,
          `data: {"type":"done","status":"error","reason":"quota_exhausted","error":"${failure}"}\n\n`,
        ])
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const card = await screen.findByText(/额度已用完/)
    expect(card.textContent).toContain('充值')
    // 失败文案已被 done.error 精确剔除，不再以普通回复的样子出现。
    expect(document.body.textContent).not.toContain(failure)
  })

  test('done{status:denied, reason:budget_exceeded} 渲染预算卡片与两个可点出口', async () => {
    const onOpenUsage = vi.fn()
    const onConfigureModel = vi.fn()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return sse([
          `data: {"type":"done","turn":1,"status":"denied","reason":"budget_exceeded","correlation_id":"156a7c18547a47bb"}\n\n`,
        ])
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <ChatPanel
        provider={UNKNOWN_PROVIDER}
        onConfigureModel={onConfigureModel}
        onOpenUsage={onOpenUsage}
      />,
    )

    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const text = await screen.findByText(/超出当前额度/)
    expect(text.textContent).toContain('更便宜的模型')
    // 只读用量页做不到调整额度：卡片上不能出现这类承诺。
    expect(text.textContent).not.toContain('去用量页调整额度')
    expect(text.textContent).not.toContain('请到「我的 Key → 用量」调整额度')
    fireEvent.click(screen.getByRole('button', { name: '查看用量' }))
    fireEvent.click(screen.getByRole('button', { name: '换更便宜的模型' }))
    expect(onOpenUsage).toHaveBeenCalledTimes(1)
    expect(onConfigureModel).toHaveBeenCalledTimes(1)
  })

  test('物理 402（建流前拒绝）同样渲染预算卡片与两个可点出口', async () => {
    const onOpenUsage = vi.fn()
    const onConfigureModel = vi.fn()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        // 建流前拒绝：不是 text/event-stream，而是 JSON 的 402。
        return new Response(
          JSON.stringify({
            code: 'budget_exceeded',
            message: '本次调用会超出可用预算（按保守 token 估算），请调整额度或换更便宜的模型。',
            correlation_id: '156a7c18547a47bb',
          }),
          { status: 402, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <ChatPanel
        provider={UNKNOWN_PROVIDER}
        onConfigureModel={onConfigureModel}
        onOpenUsage={onOpenUsage}
      />,
    )

    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const text = await screen.findByText(/超出当前额度/)
    expect(text.textContent).toContain('更便宜的模型')
    // 只读用量页做不到调整额度：卡片上不能出现这类承诺。
    expect(text.textContent).not.toContain('去用量页调整额度')
    expect(text.textContent).not.toContain('请到「我的 Key → 用量」调整额度')
    fireEvent.click(screen.getByRole('button', { name: '查看用量' }))
    fireEvent.click(screen.getByRole('button', { name: '换更便宜的模型' }))
    expect(onOpenUsage).toHaveBeenCalledTimes(1)
    expect(onConfigureModel).toHaveBeenCalledTimes(1)
    // 助手气泡不能是空的（曾用「…」占位）：只有错误卡，没有空气泡。
    const bubbles = Array.from(document.querySelectorAll('.chat-bubble'))
    expect(bubbles[bubbles.length - 1].textContent).not.toContain('…')
  })

  test('物理 403（权限拒绝）只说权限不足，不出现预算的两个按钮', async () => {
    const onOpenUsage = vi.fn()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return new Response(
          JSON.stringify({
            code: 'permission_denied',
            message: '权限不足：当前账号没有被授权执行这个操作。',
            correlation_id: 'deny-403',
          }),
          { status: 403, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} onOpenUsage={onOpenUsage} />,
    )

    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await screen.findByText(/权限不足/)
    expect(document.body.textContent).toContain('当前账号没有被授权执行这个操作')
    // 403 ≠ 402：既没有「查看用量」，也没有「换更便宜的模型」。
    expect(screen.queryByRole('button', { name: '查看用量' })).toBeNull()
    expect(screen.queryByRole('button', { name: '换更便宜的模型' })).toBeNull()
    expect(onOpenUsage).not.toHaveBeenCalled()
  })

  test('物理 429（限流）说请求过于频繁，不出现预算按钮', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return new Response(
          JSON.stringify({
            detail: '请求过于频繁，请稍后再试（rate limit exceeded）',
            code: 'RATE_LIMITED_PRINCIPAL',
          }),
          { status: 429, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} onOpenUsage={vi.fn()} />)

    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await screen.findByText('请求过于频繁，请稍后重试')
    expect(screen.queryByRole('button', { name: '查看用量' })).toBeNull()
    expect(screen.queryByRole('button', { name: '换更便宜的模型' })).toBeNull()
  })

  test('done{status:error, reason:key_revoked} 渲染换 Key 卡片', async () => {
    const failure = '这次没有拿到回复，请稍后再试。'
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return sse([
          `data: {"type":"done","status":"error","reason":"key_revoked","error":"${failure}"}\n\n`,
        ])
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const card = await screen.findByText(/吊销/)
    expect(card.textContent).toContain('Key')
    expect(document.body.textContent).not.toContain('请求失败')
  })

  test('done.error 带盘符路径与英文异常名：卡片只显示中文结论，不渲染 raw 原文', async () => {
    const leaky = 'Internal Server Error: C:\\srv\\gateway.py line 42'
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return sse([
          `data: ${JSON.stringify({ type: 'done', status: 'error', reason: 'upstream_error', error: leaky })}\n\n`,
        ])
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await screen.findByText(/服务端错误/)
    const body = document.body.textContent ?? ''
    // error.raw 只留在状态里，界面必须只渲染 text：路径与英文异常名都不能上屏。
    expect(body).not.toMatch(/[A-Za-z]:\\/)
    expect(body).not.toContain('gateway.py')
    expect(body).not.toContain('Internal Server Error')
  })
})

describe('ChatPanel 会话发现', () => {
  test('备份迁移后（本地无清单）按后端列表补全会话，并显示首条消息标题', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path.startsWith('/v1/chat/sessions')) {
        return json({ sessions: ['session-imported'], count: 1, users: {} })
      }
      if (path.includes('session_id=session-imported')) {
        return json({
          session_id: 'session-imported',
          count: 2,
          history: [
            { role: 'user', content: 'Q7紫罗兰-历史-8842：先给结论' },
            { role: 'assistant', content: '结论：边界判断有误' },
          ],
        })
      }
      if (path.startsWith('/v1/chat/history')) {
        return json({ session_id: '', count: 0, history: [] })
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    await screen.findByRole('option', { name: /Q7紫罗兰-历史-8842/ })
    // 清单已落到本地：下次刷新即使后端不可达也仍能看到这条会话。
    expect(JSON.parse(localStorage.getItem('liuhao.sessions') || '[]')).toContain(
      'session-imported',
    )
  })
})

describe('ChatPanel 工具卡与状态行', () => {
  function fillAndSend(text: string) {
    fireEvent.change(
      screen.getByPlaceholderText('未配置模型，请先配置模型密钥'),
      { target: { value: text } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
  }

  test('工具失败：摘要用人话、默认收起，原始 JSON 仍保留在折叠区', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return sse([
          'data: {"type":"tool","name":"manage_reminders","args":{"action":"create"},"output":{"status":"error","error":"时间已收到，但缺少提醒内容"}}\n\n',
          'data: {"type":"done","status":"completed"}\n\n',
        ])
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)
    fillAndSend('帮我设个提醒')

    const summary = await screen.findByText(/失败：时间已收到，但缺少提醒内容/)
    expect(summary.textContent).toContain('调用工具')
    const card = summary.closest('details') as HTMLDetailsElement | null
    expect(card).toBeTruthy()
    // 失败/需澄清的工具卡默认收起。
    expect(card!.open).toBe(false)
    expect(card!.hasAttribute('open')).toBe(false)
    // D：原始 JSON 不删，展开仍能看到。
    const pre = card!.querySelector('pre')
    expect(pre?.textContent).toContain('"status": "error"')
    expect(pre?.textContent).toContain('时间已收到，但缺少提醒内容')
  })

  test('notice 只更新状态行：不进正文、不算 token，token 到达后立即清除', async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | null = null
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c
      },
    })
    const encoder = new TextEncoder()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') {
        return new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)
    fillAndSend('你好')

    await act(async () => {
      controller!.enqueue(encoder.encode('data: {"type":"notice","message":"正在重试…"}\n\n'))
    })
    const note = await screen.findByText('正在重试…')
    expect(note.className).toContain('chat-status-note')
    expect(note.getAttribute('role')).toBe('status')

    await act(async () => {
      controller!.enqueue(encoder.encode('data: {"type":"token","content":"好"}\n\n'))
    })
    // token 一到，状态行立刻让位给正文。
    await waitFor(() => expect(screen.queryByText('正在重试…')).toBeNull())
    let bubbles = Array.from(document.querySelectorAll('.chat-bubble'))
    expect(bubbles[bubbles.length - 1].textContent).toBe('好')

    await act(async () => {
      controller!.enqueue(encoder.encode('data: {"type":"done","status":"completed"}\n\n'))
      controller!.close()
    })
    await waitFor(() => expect(screen.queryByText('生成中…')).toBeNull())
    bubbles = Array.from(document.querySelectorAll('.chat-bubble'))
    // notice 从未成为正文的一部分：最终正文仍只是模型产出的「好」。
    expect(bubbles[bubbles.length - 1].textContent).toBe('好')
  })

  test('流没有 done 就断开：渲染「连接中断」错误卡，且不留下空气泡', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/models') return json({ models: [], default_id: null })
      if (path === '/v1/chat/stream') return sse([])
      return json({})
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)
    fillAndSend('你好')

    await screen.findByText('连接中断，请重试。')
    const bubbles = Array.from(document.querySelectorAll('.chat-bubble'))
    const last = bubbles[bubbles.length - 1]
    // 正文为空时只能有错误卡，不能落「…」空气泡。
    expect(last.textContent).not.toContain('…')
    expect(last.querySelector('.chat-error')).toBeTruthy()
  })
})

/**
 * 中文输入法（组词中）按回车不能发送：拼音还在候选框里，回车是「选词」不是「发送」。
 * 这里用 compositionstart/end + keyCode 229 复现真实输入法事件序列；改前必须红。
 */
describe('ChatPanel 输入法回车', () => {
  const INPUT_PLACEHOLDER = '未配置模型，请先配置模型密钥'

  function stubStream() {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input)
        if (path === '/v1/models') return json({ models: [], default_id: null })
        if (path === '/v1/chat/stream') {
          calls.push(path)
          return sse(['data: {"type":"done","status":"completed"}\n\n'])
        }
        return json({})
      }),
    )
    return calls
  }

  const input = () => screen.getByPlaceholderText(INPUT_PLACEHOLDER) as HTMLTextAreaElement

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1024 })
  })

  test('组词中的回车（keyCode 229）不发送，拼音留在输入框', async () => {
    const calls = stubStream()
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    const box = input()
    fireEvent.change(box, { target: { value: 'nihao' } })
    fireEvent.compositionStart(box)
    fireEvent.keyDown(box, { key: 'Enter', keyCode: 229 })

    await waitFor(() => expect(calls).toHaveLength(0))
    expect(box.value).toBe('nihao')
  })

  test('compositionstart 后的普通回车也不发送；compositionend 之后回车才发送', async () => {
    const calls = stubStream()
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    const box = input()
    fireEvent.change(box, { target: { value: 'nihao' } })
    fireEvent.compositionStart(box)
    // 部分浏览器组合期间 keyCode 不是 229，只靠 composition 状态兜底。
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect(calls).toHaveLength(0))
    expect(box.value).toBe('nihao')

    fireEvent.compositionEnd(box)
    // 越过组合结束后的余波窗口（浏览器可能在 compositionend 之后补发一次 Enter）。
    await new Promise((resolve) => setTimeout(resolve, 60))
    fireEvent.change(box, { target: { value: '你好' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect(calls).toHaveLength(1))
    expect(box.value).toBe('')
  })

  test('手机上回车换行、点发送按钮才发送，键帽提示与行为一致', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 375 })
    const calls = stubStream()
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    const box = input()
    expect(box.getAttribute('enterkeyhint')).toBe('enter')

    fireEvent.change(box, { target: { value: '第一行' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() => expect(calls).toHaveLength(0))
    expect(box.value).toBe('第一行')

    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(calls).toHaveLength(1))
  })
})

describe('ChatPanel 历史与会话状态', () => {
  const EMPTY_PROMPT = '当前未配置真实模型：未配置模型，请先配置模型密钥。发送后只会收到未配置提示，不会生成模拟回答。'

  function stub(overrides: {
    history?: () => Promise<Response>
    sessions?: () => Promise<Response>
    models?: () => Promise<Response>
  }) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input)
        if (path === '/v1/models') {
          return overrides.models ? overrides.models() : json({ models: [], default_id: null })
        }
        if (path.startsWith('/v1/chat/sessions')) {
          return overrides.sessions
            ? overrides.sessions()
            : json({ sessions: [], total: 0, has_more: false })
        }
        if (path.startsWith('/v1/chat/history')) {
          return overrides.history
            ? overrides.history()
            : json({ history: [], has_more: false })
        }
        return json({})
      }),
    )
  }

  test('历史还没回来：显示“正在读取历史…”，不提前说“没有记录”', () => {
    stub({ history: () => new Promise<Response>(() => {}) })

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    expect(screen.getByText('正在读取历史…')).toBeTruthy()
    expect(screen.queryByText(EMPTY_PROMPT)).toBeNull()
  })

  test('历史读取失败：给出可点的重试，重试成功后回到正常空态', async () => {
    let attempts = 0
    stub({
      history: () => {
        attempts += 1
        if (attempts === 1) return Promise.resolve(new Response('', { status: 500 }))
        return Promise.resolve(json({ history: [], has_more: false }))
      },
    })

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    expect(await screen.findByText(/加载历史失败（500）/)).toBeTruthy()
    expect(screen.queryByText(EMPTY_PROMPT)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(screen.getByText(EMPTY_PROMPT)).toBeTruthy())
  })

  test('会话清单读不到：给一行提示与重试，不静默', async () => {
    stub({ sessions: () => Promise.resolve(new Response('', { status: 500 })) })

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    expect(await screen.findByText(/会话清单没读全/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  })

  test('模型清单读不到：给一行提示与重试，不静默', async () => {
    stub({ models: () => Promise.resolve(new Response('', { status: 500 })) })

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    expect(await screen.findByText(/模型清单没读到/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  })
})

/**
 * 长会话的两条硬约束：
 * - 600 条历史不一次性渲染（窗口化），按键不再让全部行参与协调；
 * - 超过 8000 字的粘贴只留摘要预览，大字符串不进 React 状态。
 * 以及流式时「用户上翻不被拉回底部」。
 */
describe('ChatPanel 长会话窗口化与超大输入', () => {
  const INPUT_PLACEHOLDER = '未配置模型，请先配置模型密钥'

  function stub(overrides: { history?: () => Promise<Response>; streamBody?: () => Response }) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input)
        if (path === '/v1/models') return json({ models: [], default_id: null })
        if (path.startsWith('/v1/chat/sessions')) {
          return json({ sessions: [], total: 0, has_more: false })
        }
        if (path.startsWith('/v1/chat/history')) {
          return overrides.history
            ? overrides.history()
            : json({ history: [], has_more: false })
        }
        if (path === '/v1/chat/stream' && overrides.streamBody) return overrides.streamBody()
        return json({})
      }),
    )
  }

  test('600 条历史只渲染视口附近窗口，最早的消息不参与挂载', async () => {
    const history = Array.from({ length: 600 }, (_, i) => ({
      role: i % 2 === 0 ? 'user' : 'assistant',
      content: `消息 ${i}`,
    }))
    stub({ history: () => Promise.resolve(json({ history, has_more: false })) })

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    await waitFor(() => expect(document.querySelectorAll('.chat-row').length).toBeGreaterThan(0))
    const rows = document.querySelectorAll('.chat-row')
    // 窗口上限 64 + 兜底余量：绝不能是 600。
    expect(rows.length).toBeLessThanOrEqual(80)
    // 最新一条在窗口里，最早一条不渲染（滚动上去时窗口会跟着换）。
    const log = document.querySelector('.chat-log') as HTMLElement
    expect(screen.getByText('消息 599')).toBeTruthy()
    expect(within(log).queryByText('消息 0')).toBeNull()
  })

  test('粘贴 9 万字：输入区只留摘要预览，提示「全文未发送」，且大字符串不参与后续按键', async () => {
    stub({})
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    const box = screen.getByPlaceholderText(INPUT_PLACEHOLDER) as HTMLTextAreaElement
    const big = 'A'.repeat(90000)
    const pasteEvent = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(pasteEvent, 'clipboardData', {
      value: { getData: () => big },
    })
    fireEvent(box, pasteEvent)

    // 输入区只显示摘要（约 160 字 + 说明），不是 9 万字。
    expect(box.value.length).toBeLessThan(400)
    expect(box.value).not.toContain('A'.repeat(1000))
    expect(screen.getByText(/全文未发送/)).toBeTruthy()
    expect((screen.getByRole('button', { name: '发送' }) as HTMLButtonElement).disabled).toBe(
      true,
    )

    // 在摘要上继续输入：全文引用作废，草稿退回小字符串，不再有「全文未发送」。
    fireEvent.change(box, { target: { value: `${box.value}q` } })
    expect(box.value.length).toBeLessThan(400)
    expect(screen.queryByText(/全文未发送/)).toBeNull()
  })

  test('流式时用户上翻不被强行拉回底部；「回到底部」带未读字数', async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | null = null
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c
      },
    })
    const encoder = new TextEncoder()
    stub({
      streamBody: () =>
        new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }),
    })

    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText(INPUT_PLACEHOLDER), {
      target: { value: '你好' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await act(async () => {
      controller!.enqueue(encoder.encode('data: {"type":"token","content":"好"}\n\n'))
    })
    // 等首个 token 真正落进气泡再上翻：否则它还在 rAF 缓冲里，会被算成未读。
    await waitFor(() => {
      const bubbles = Array.from(document.querySelectorAll('.chat-bubble'))
      expect(bubbles[bubbles.length - 1].textContent).toBe('好')
    })

    const log = document.querySelector('.chat-log') as HTMLDivElement
    Object.defineProperty(log, 'scrollHeight', { configurable: true, get: () => 1000 })
    Object.defineProperty(log, 'clientHeight', { configurable: true, get: () => 200 })
    log.scrollTop = 100
    fireEvent.scroll(log)

    // 用户向上翻读后，后续 token 不得把 scrollTop 拉回底部。
    await act(async () => {
      controller!.enqueue(encoder.encode('data: {"type":"token","content":"啊"}\n\n'))
    })
    await waitFor(() => expect(document.querySelector('.chat-jump-badge')?.textContent).toBe('1'))
    expect(log.scrollTop).toBe(100)

    // 点「回到底部」：视口落到内容底部（jsdom 不钳制 scrollTop，直接等于 scrollHeight）。
    fireEvent.click(screen.getByRole('button', { name: /回到底部/ }))
    expect(log.scrollTop).toBe(1000)
    await waitFor(() => expect(document.querySelector('.chat-jump-bottom')).toBeNull())

    await act(async () => {
      controller!.enqueue(encoder.encode('data: {"type":"done","status":"completed"}\n\n'))
      controller!.close()
    })
  })
})

/**
 * 工具箱与单助手结合版回归。
 *
 * 2026-11-12 用户纠正：不要两个可切换的助手（Muse / 小龙虾），只要结合版。
 * 因此这里钉住：界面上只有一个助手（贾维斯），没有切换器；快捷问题与工具箱
 * 都只有一套；工具箱点下去是把"指令模板 + 输入框内容"作为一条普通消息走
 * `POST /v1/chat/stream` 发给同一个助手，生成结果回到对话里。
 */
describe('ChatPanel 工具箱（单助手结合版）', () => {
  const INPUT_PLACEHOLDER = '未配置模型，请先配置模型密钥'

  function stubStream() {
    const bodies: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input)
        if (path === '/v1/models') return json({ models: [], default_id: null })
        if (path === '/v1/chat/stream') {
          bodies.push(String(init?.body ?? ''))
          return sse(['data: {"type":"done","status":"completed"}\n\n'])
        }
        return json({})
      }),
    )
    return bodies
  }

  const input = () => screen.getByPlaceholderText(INPUT_PLACEHOLDER) as HTMLTextAreaElement

  test('界面上只有一个助手，没有 Muse / 小龙虾切换器', async () => {
    stubStream()
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    expect(await screen.findByText('贾维斯')).toBeTruthy()
    expect(screen.queryByText('小龙虾')).toBeNull()
    expect(screen.queryByText('Muse')).toBeNull()
    // 工具箱只有一组：六个工具，不按人格分流。
    const toolbox = screen.getByRole('group', { name: '工具箱' })
    const buttons = Array.from(toolbox.querySelectorAll('button')).map((button) =>
      button.textContent?.trim(),
    )
    expect(buttons).toEqual(['总结', '写邮件', '润色', '翻译', '想点子', '代码示例'])
  })

  test('输入框有内容时点「总结」：模板 + 内容作为一条消息走 /v1/chat/stream', async () => {
    const bodies = stubStream()
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    fireEvent.change(input(), { target: { value: '本周做了 A、B 两件事' } })
    fireEvent.click(screen.getByRole('button', { name: '总结' }))

    await waitFor(() => expect(bodies).toHaveLength(1))
    const sent = JSON.parse(bodies[0]) as { message: string }
    expect(sent.message).toContain('总结')
    expect(sent.message).toContain('本周做了 A、B 两件事')
  })

  test('输入框是空的点「写邮件」：只填入指令，不发空消息', async () => {
    const bodies = stubStream()
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: '写邮件' }))

    await waitFor(() => expect(bodies).toHaveLength(0))
    expect(input().value).toContain('写一封得体的邮件')
    expect(screen.getByText(/已填入「写邮件」指令/)).toBeTruthy()
  })

  test('快捷问题只有一套：点一下直接发给唯一助手', async () => {
    const bodies = stubStream()
    render(<ChatPanel provider={UNKNOWN_PROVIDER} onConfigureModel={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: '帮我想几个新点子' }))

    await waitFor(() => expect(bodies).toHaveLength(1))
    const sent = JSON.parse(bodies[0]) as { message: string }
    expect(sent.message).toBe('帮我想几个新点子')
  })
})
