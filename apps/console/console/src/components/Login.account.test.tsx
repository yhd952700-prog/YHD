/**
 * 登录页的账号面回归。
 *
 * 同一个入口服务所有身份（含自助创建的账户），因此登录页本身的文案也必须满足
 * "成员可见文字不含结构词"；同时钉住：
 * 正常登录 / 重新登录 / 恢复码重置 / 本机无账号时初始化 / 创建账户（不需要邀请码）。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Login } from './Login'
import type { Session } from '../lib/auth'
import { buildProfile, deriveCaps, findBannedTerms } from '../lib/accountCaps'
import { visibleNavKeys } from '../lib/nav'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function config(overrides: Record<string, unknown> = {}) {
  return {
    auth_required: true,
    methods: ['password'],
    oauth: {},
    secret_store: { configured: true, source: 'env' },
    registered_humans: 2,
    login_eligible_humans: 2,
    client_modes: ['desktop', 'web', 'mobile'],
    token: { ttl_seconds: 86_400 },
    message: '',
    root_account_exists: true,
    registration_open: false,
    ...overrides,
  }
}

let fetchMock: ReturnType<typeof vi.fn>

function mockRoutes(
  value: ReturnType<typeof config>,
  routes: Record<string, () => Response> = {},
) {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path === '/v1/auth/config') return json(200, value)
    const handler = routes[path]
    if (handler) return handler()
    return json(404, { detail: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetchMock)
}

beforeEach(() => {
  mockRoutes(config())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function expectNoBannedText() {
  expect(findBannedTerms(document.body.textContent ?? '')).toEqual([])
}

/** 登录页上不允许出现的账号类别/邀请语义（比全局禁用词更严格）。 */
function expectNoInviteWords() {
  const text = document.body.textContent ?? ''
  for (const word of ['邀请', '邀请码', '加入', '成员', '子账号', '上级']) {
    expect(text).not.toContain(word)
  }
}

/** 与 `/v1/auth/login` 同形的 JWT 载荷，仅用于前端展示解析。 */
function tokenFor(sub: string): string {
  const payload = btoa(
    JSON.stringify({ sub, exp: Math.floor(Date.now() / 1000) + 3600 }),
  )
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
  return `header.${payload}.signature`
}

describe('登录页状态机', () => {
  test('默认展示登录表单，并提供"登录 / 创建账户"两个平级入口', async () => {
    render(<Login mode="desktop" onSuccess={vi.fn()} />)
    expect(await screen.findByRole('tab', { name: '登录' })).toBeTruthy()
    expect(screen.getByRole('tab', { name: '创建账户' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '忘记密码' })).toBeTruthy()
    expect(document.body.textContent).toContain('全栈 AI 智能中枢')
    expectNoBannedText()
    expectNoInviteWords()
  })

  test('创建账户表单只有用户名和密码，没有邀请码字段', async () => {
    render(<Login mode="desktop" onSuccess={vi.fn()} />)
    fireEvent.click(await screen.findByRole('tab', { name: '创建账户' }))
    expect(document.body.textContent).toContain('输入用户名和密码即可创建账户')
    expect(screen.getByLabelText('用户名')).toBeTruthy()
    expect(screen.getByLabelText('密码')).toBeTruthy()
    expect(screen.queryByLabelText('邀请码')).toBeNull()
    expect(document.querySelector('input[name="invite"]')).toBeNull()
    expectNoInviteWords()
    expectNoBannedText()
  })

  test('忘记密码入口：可用恢复码，也可继续到"恢复码丢失"引导', async () => {
    render(<Login mode="desktop" onSuccess={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: '忘记密码' }))
    expect(document.body.textContent).toContain('恢复码使用一次后即失效')
    fireEvent.click(screen.getByRole('button', { name: '恢复码也丢失了' }))
    expect(document.body.textContent).toContain('恢复码也丢失时')
    expect(document.body.textContent).toContain('本机数据仍然保留')
    expectNoBannedText()
    expectNoInviteWords()
  })

  test('本机无账号 → 初始化表单，文案不出现账号类别词', async () => {
    mockRoutes(config({ root_account_exists: false, registration_open: true }))
    render(<Login mode="desktop" onSuccess={vi.fn()} />)
    expect(await screen.findByText('首次使用 · 初始化本机')).toBeTruthy()
    expect(document.body.textContent).toContain('这台机器还没有账号')
    expect(document.body.textContent).toContain('创建的第一个账号拥有本机全部管理范围')
    expectNoBannedText()
    expectNoInviteWords()
  })

  test('创建成功后直接进入系统，且以成员身份裁剪导航入口', async () => {
    const token = tokenFor('newcomer')
    const onSuccess = vi.fn()
    mockRoutes(config(), {
      '/v1/accounts/self-register': () => json(200, { token }),
      '/v1/auth/me': () =>
        json(200, {
          principal: 'newcomer',
          display_name: null,
          scope: 'L3',
          permissions: ['account:read'],
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          parent_account_id: 'root',
          path: '/root/newcomer',
          depth: 1,
        }),
    })

    render(<Login mode="web" onSuccess={onSuccess} />)
    fireEvent.click(await screen.findByRole('tab', { name: '创建账户' }))
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'newcomer' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: '创建并进入' }))

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
    const session = onSuccess.mock.calls[0][0] as Session
    expect(session.principal).toBe('newcomer')
    expect(session.parentAccountId).toBe('root')
    expect(session.depth).toBe(1)

    const caps = deriveCaps(
      buildProfile(session, { members: [], count: 0 }, 'live', null),
    )
    const visible = visibleNavKeys(caps)
    expect(caps.isRoot).toBe(false)
    for (const key of ['members', 'matrix', 'templates', 'backup', 'recovery', 'settings', 'status']) {
      expect(visible.has(key)).toBe(false)
    }
    expect(visible.has('overview')).toBe(true)
    expect(visible.has('my-keys')).toBe(true)
  })

  test('自助创建被关闭时只给中性文案，不透出层级或实现细节', async () => {
    mockRoutes(config(), {
      '/v1/accounts/self-register': () =>
        json(403, {
          detail: { code: 'ACCOUNT_SELF_REGISTRATION_CLOSED', message: 'self registration closed' },
        }),
    })

    render(<Login mode="web" onSuccess={vi.fn()} />)
    fireEvent.click(await screen.findByRole('tab', { name: '创建账户' }))
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'newcomer' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: '创建并进入' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('当前不允许自行创建账户，请联系设备管理员')
    expect(document.body.textContent).not.toContain('self registration closed')
    // 需求指定的中性文案本身含"设备管理员"（§3.1 的禁用词覆盖不到登录页这句），
    // 其余结构词仍然必须为 0。
    expect(findBannedTerms(document.body.textContent ?? '').filter((term) => term !== '管理员')).toEqual([])
    expectNoInviteWords()
  })
})
