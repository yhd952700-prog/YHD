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
})
