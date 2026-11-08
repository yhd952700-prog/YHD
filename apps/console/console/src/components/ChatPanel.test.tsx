/**
 * 对话面板的失败态回归。
 *
 * 后端在 HTTP 200 的流里用 `done{status:error}` 表示这一轮失败；
 * 前端必须渲染失败卡片，而不是把失败文案当成普通回复。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ChatPanel } from './ChatPanel'
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
      screen.getByPlaceholderText('未配置真实模型：当前发送将得到模拟回复'),
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
      screen.getByPlaceholderText('未配置真实模型：当前发送将得到模拟回复'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const text = await screen.findByText(/超出你设定的预算/)
    expect(text.textContent).toContain('更便宜的模型')
    fireEvent.click(screen.getByRole('button', { name: '去用量页调整额度' }))
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
            message: '本次调用会超出你设定的预算，请调整额度或换更便宜的模型。',
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
      screen.getByPlaceholderText('未配置真实模型：当前发送将得到模拟回复'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const text = await screen.findByText(/超出你设定的预算/)
    expect(text.textContent).toContain('更便宜的模型')
    fireEvent.click(screen.getByRole('button', { name: '去用量页调整额度' }))
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
      screen.getByPlaceholderText('未配置真实模型：当前发送将得到模拟回复'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await screen.findByText(/权限不足/)
    expect(document.body.textContent).toContain('当前账号没有被授权执行这个操作')
    // 403 ≠ 402：既没有「去调整额度」，也没有「换更便宜的模型」。
    expect(screen.queryByRole('button', { name: '去用量页调整额度' })).toBeNull()
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
      screen.getByPlaceholderText('未配置真实模型：当前发送将得到模拟回复'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await screen.findByText('请求过于频繁，请稍后重试')
    expect(screen.queryByRole('button', { name: '去用量页调整额度' })).toBeNull()
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
      screen.getByPlaceholderText('未配置真实模型：当前发送将得到模拟回复'),
      { target: { value: '你好' } },
    )
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    const card = await screen.findByText(/吊销/)
    expect(card.textContent).toContain('Key')
    expect(document.body.textContent).not.toContain('请求失败')
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
