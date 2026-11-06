/**
 * 记忆页的行为契约（对齐 `src/gateway/account_memory.py` 的真实端点）。
 *
 * 钉死六件事：
 * 1. 列表/检索打到 `/v1/memory/entries`，按真实字段（source / tags）展示；
 * 2. 非根只发 `scope=self`，不制造注定被拒的子树请求；
 * 3. 无数据时是中性空态；
 * 4. 路由缺失也不会伪造条目，更不会出现"待联调 / 契约桩"；
 * 5. 代管开关读到 `/v1/memory/custody`，点击后 POST 真实 `{enabled}`；
 * 6. 整页文案不含层级词。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import type { Session } from '../lib/auth'
import { buildProfile, deriveCaps, findBannedTerms } from '../lib/accountCaps'
import type { AccountsPayload, MemoryEntry } from '../lib/accounts'
import { Memory } from './Memory'

const PAYLOAD: AccountsPayload = { members: [], count: 0 }

function session(overrides: Partial<Session>): Session {
  return {
    token: 't',
    principal: 'leaf-a',
    displayName: '账号 A',
    scope: null,
    permissions: [],
    expiresAt: 0,
    client: 'web',
    parentAccountId: 'parent-a',
    path: 'root/parent-a/leaf-a',
    depth: 2,
    ...overrides,
  }
}

const MEMBER_SESSION = session({})
const ROOT_SESSION = session({
  principal: 'root',
  displayName: '根',
  scope: 'L0',
  parentAccountId: null,
  path: 'root',
  depth: 0,
  permissions: ['account:grant', 'account:manage_children'],
})

const MEMBER_PROFILE = buildProfile(MEMBER_SESSION, PAYLOAD, 'live', null)
const MEMBER_CAPS = deriveCaps(MEMBER_PROFILE)
const ROOT_PROFILE = buildProfile(ROOT_SESSION, PAYLOAD, 'live', null)
const ROOT_CAPS = deriveCaps(ROOT_PROFILE)

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

interface FetchState {
  entries: MemoryEntry[]
  custodyEnabled: boolean
  entriesStatus?: number
}

function stubFetch(state: FetchState) {
  const calls: Array<{ url: string; method: string; body: unknown }> = []
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    let body: unknown = null
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    calls.push({ url, method, body })

    if (url.startsWith('/v1/memory/entries')) {
      if (state.entriesStatus && state.entriesStatus >= 400) {
        return json({ detail: 'Not Found' }, state.entriesStatus)
      }
      return json({ entries: state.entries, scope: 'self', count: state.entries.length })
    }
    if (url === '/v1/memory/custody') {
      if (method === 'POST') {
        state.custodyEnabled = Boolean((body as { enabled?: boolean })?.enabled)
        return json({ enabled: state.custodyEnabled, updated_at: '2026-11-07T00:00:00Z' })
      }
      return json({
        enabled: state.custodyEnabled,
        updated_at: state.custodyEnabled ? '2026-11-07T00:00:00Z' : '',
      })
    }
    return json({ detail: 'Not Found' }, 404)
  })
  return calls
}

afterEach(() => {
  vi.restoreAllMocks()
})

const ENTRY: MemoryEntry = {
  id: 'mem-1',
  account_id: 'leaf-a',
  text: '把周报模板固定到每周五',
  source: 'chat',
  tags: ['偏好', '周报'],
  created_at: '2026-11-06T10:00:00Z',
}

describe('记忆页（真实端点）', () => {
  test('按真实字段展示来源、标签与内容，且没有待联调徽标', async () => {
    stubFetch({ entries: [ENTRY], custodyEnabled: false })
    render(<Memory profile={MEMBER_PROFILE} caps={MEMBER_CAPS} readOnly={false} />)

    await waitFor(() => expect(screen.getByText(ENTRY.text)).toBeTruthy())
    expect(screen.getByText('chat')).toBeTruthy()
    expect(screen.getByText('偏好、周报')).toBeTruthy()
    expect(screen.getByText('leaf-a')).toBeTruthy()

    const text = document.body.textContent ?? ''
    expect(text).not.toContain('待联调')
    expect(text).not.toContain('契约桩')
  })

  test('非根只请求 scope=self', async () => {
    const calls = stubFetch({ entries: [], custodyEnabled: false })
    render(<Memory profile={MEMBER_PROFILE} caps={MEMBER_CAPS} readOnly={false} />)

    await waitFor(() => expect(calls.some((call) => call.url.startsWith('/v1/memory/entries'))).toBe(true))
    const listCall = calls.find((call) => call.url.startsWith('/v1/memory/entries'))
    expect(listCall?.url).toContain('scope=self')
    expect(listCall?.url).not.toContain('scope=subtree')
  })

  test('无数据是中性空态，且整页不含层级词', async () => {
    stubFetch({ entries: [], custodyEnabled: false })
    render(<Memory profile={MEMBER_PROFILE} caps={MEMBER_CAPS} readOnly={false} />)

    await waitFor(() => expect(screen.getByText('还没有记忆')).toBeTruthy())
    expect(screen.getByText('对话与任务产生的记忆会出现在这里。')).toBeTruthy()
    expect(findBannedTerms(document.body.textContent ?? '')).toEqual([])
  })

  test('路由缺失也不伪造条目，更不会出现桩徽标', async () => {
    stubFetch({ entries: [], custodyEnabled: false, entriesStatus: 404 })
    render(<Memory profile={MEMBER_PROFILE} caps={MEMBER_CAPS} readOnly={false} />)

    await waitFor(() => expect(screen.getByText('数据不可用')).toBeTruthy())
    expect(document.body.textContent).not.toContain('待联调')
    expect(document.body.textContent).not.toContain('契约桩')
  })

  test('根账号可切到整棵范围，导出按钮随范围变化', async () => {
    const calls = stubFetch({ entries: [ENTRY], custodyEnabled: false })
    render(<Memory profile={ROOT_PROFILE} caps={ROOT_CAPS} readOnly={false} />)

    await waitFor(() => expect(screen.getByText(ENTRY.text)).toBeTruthy())
    fireEvent.change(screen.getByLabelText('检索范围'), { target: { value: 'subtree' } })

    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('scope=subtree'))).toBe(true),
    )
    expect(screen.getByRole('button', { name: '导出整棵范围' })).toBeTruthy()
  })

  test('代管开关读到真实状态，点击后 POST 真实 enabled', async () => {
    const calls = stubFetch({ entries: [], custodyEnabled: false })
    render(<Memory profile={MEMBER_PROFILE} caps={MEMBER_CAPS} readOnly={false} />)

    const checkbox = (await screen.findByLabelText('允许协作处理我的记忆')) as HTMLInputElement
    expect(checkbox.checked).toBe(false)

    fireEvent.click(checkbox)
    await waitFor(() => expect(screen.getByText('已开启记忆代管')).toBeTruthy())

    const post = calls.find((call) => call.url === '/v1/memory/custody' && call.method === 'POST')
    expect(post?.body).toEqual({ enabled: true })
  })

  test('只读查看时禁用代管开关与导出', async () => {
    stubFetch({ entries: [ENTRY], custodyEnabled: true })
    render(<Memory profile={ROOT_PROFILE} caps={ROOT_CAPS} readOnly />)

    const checkbox = (await screen.findByLabelText('允许协作处理我的记忆')) as HTMLInputElement
    expect(checkbox.disabled).toBe(true)
    expect(
      (screen.getByRole('button', { name: '导出我的记忆' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })
})
