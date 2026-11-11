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
import { clearPendingRecovery, readPendingRecovery } from '../lib/auth'
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
  routes: Record<string, (init?: RequestInit) => Response> = {},
) {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path === '/v1/auth/config') return json(200, value)
    const handler = routes[path]
    if (handler) return handler(init)
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
  test('会话过期：明确提示需要重新登录，并指出入口就在下方', async () => {
    render(<Login mode="desktop" notice="expired" onSuccess={vi.fn()} />)
    const alert = await screen.findByTestId('login-notice')
    expect(alert.textContent).toContain('登录已过期')
    expect(alert.textContent).toContain('需要重新登录')
    expect(alert.textContent).toContain('下方')
    // 入口就是同一页的表单，不需要用户自己找。
    expect(await screen.findByLabelText('账号')).toBeTruthy()
    expectNoBannedText()
  })

  test('主动登出：同样说明需要重新登录，不静默跳回', async () => {
    render(<Login mode="desktop" notice="logged-out" onSuccess={vi.fn()} />)
    const alert = await screen.findByTestId('login-notice')
    expect(alert.textContent).toContain('已退出登录')
    expect(alert.textContent).toContain('需要重新登录')
    expectNoBannedText()
  })

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

  test('手机直连（非本机）：只指路到桌面端，不渲染重置表单、不暴露内部开关名', async () => {
    mockRoutes(
      config({
        recovery: {
          available: true,
          self_service: false,
          rotate_recommended: false,
          local: false,
          remote_allowed: false,
        },
      }),
    )
    render(<Login mode="mobile" onSuccess={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: '忘记密码' }))
    const note = await screen.findByTestId('recovery-path')
    expect(note.textContent).toContain('鎏灏桌面端')
    expect(note.textContent).not.toContain('手机访问')
    expect(note.textContent).not.toContain('LIUHAO_ALLOW_REMOTE_RECOVERY')
    // 真能走通的只有桌面端：恢复码表单与重置按钮都不该出现。
    expect(screen.queryByLabelText('恢复码')).toBeNull()
    expect(screen.queryByLabelText('新密码')).toBeNull()
    expect(screen.queryByRole('button', { name: '重置并进入' })).toBeNull()
    expect(screen.queryByRole('button', { name: '没有恢复码？在本机生成' })).toBeNull()
    expectNoBannedText()
  })

  test('本机：可直接在本机生成恢复码', async () => {
    mockRoutes(
      config({
        recovery: {
          available: false,
          self_service: true,
          rotate_recommended: false,
          local: true,
          remote_allowed: false,
        },
      }),
    )
    render(<Login mode="web" onSuccess={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: '忘记密码' }))
    expect(await screen.findByRole('button', { name: '没有恢复码？在本机生成' })).toBeTruthy()
    expect(screen.queryByTestId('recovery-path')).toBeNull()
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

  test('宽屏浏览器仍以网页版身份登录：布局 mode 不再冒充客户端', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1440 })
    const token = tokenFor('person')
    let loginBody: Record<string, unknown> = {}
    mockRoutes(config(), {
      '/v1/auth/login': (init) => {
        loginBody = JSON.parse(String(init?.body ?? '{}'))
        return json(200, {
          access_token: token,
          principal: 'person',
          display_name: null,
          scope: 'L3',
          permissions: [],
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          client: 'web',
        })
      },
    })
    try {
      render(<Login mode="desktop" onSuccess={vi.fn()} />)
      fireEvent.change(await screen.findByLabelText('账号'), { target: { value: 'person' } })
      fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } })
      fireEvent.click(screen.getByRole('button', { name: '登录' }))
      await waitFor(() => expect(loginBody.client).toBe('web'))
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1024 })
    }
  })

  test('手机端首次初始化：不渲染必失败的表单，指路到这台电脑的桌面端', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 390 })
    mockRoutes(config({ root_account_exists: false, registration_open: true }))
    try {
      render(<Login mode="mobile" onSuccess={vi.fn()} />)
      const guide = await screen.findByTestId('bootstrap-remote-guide')
      expect(guide.textContent).toContain('鎏灏桌面端')
      expect(screen.queryByLabelText('账号')).toBeNull()
      expect(screen.queryByRole('button', { name: '创建并进入系统' })).toBeNull()
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1024 })
    }
  })

  test('初始化被网关拒绝（403）：文案指到桌面端，不再要求手机打开控制台/命令行', async () => {
    mockRoutes(config({ root_account_exists: false, registration_open: true }), {
      '/v1/accounts/register': () =>
        json(403, { detail: '首次初始化只允许在本机控制台或命令行执行' }),
    })
    render(<Login mode="web" onSuccess={vi.fn()} />)
    // 等状态机真正切到初始化表单，避免把内容填进登录表单。
    expect(await screen.findByText('首次使用 · 初始化本机')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('账号'), { target: { value: 'owner' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } })
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: '创建并进入系统' }))
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/v1/accounts/register', expect.anything()),
    )
    await waitFor(() =>
      expect(document.body.textContent).toContain('首次初始化需要在这台电脑上的鎏灏桌面端完成'),
    )
    expect(document.body.textContent).not.toContain('命令行')
  })

  test('创建成功后直接进入系统，且以成员身份裁剪导航入口', async () => {
    const token = tokenFor('newcomer')
    const onSuccess = vi.fn()
    let registerBody: Record<string, unknown> = {}
    mockRoutes(config(), {
      '/v1/accounts/self-register': (init) => {
        registerBody = JSON.parse(String(init?.body ?? '{}'))
        return json(200, { token })
      },
      '/v1/auth/me': () =>
        json(200, {
          principal: 'newcomer',
          display_name: null,
          scope: 'L3',
          permissions: ['account:read'],
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          is_root: false,
        }),
    })

    render(<Login mode="web" onSuccess={onSuccess} />)
    fireEvent.click(await screen.findByRole('tab', { name: '创建账户' }))
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'newcomer' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: '创建并进入' }))

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
    // 字段名必须与后端 `SelfRegisterRequest` 一致：`password`，不是 `secret`。
    expect(registerBody).toMatchObject({
      principal: 'newcomer',
      password: 'password123',
      client: 'web',
    })
    expect(registerBody).not.toHaveProperty('secret')
    const session = onSuccess.mock.calls[0][0] as Session
    expect(session.principal).toBe('newcomer')
    expect(session.isRoot).toBe(false)

    const caps = deriveCaps(
      buildProfile(session, { members: [], count: 0 }, 'live', null),
    )
    const visible = visibleNavKeys(caps)
    expect(caps.isRoot).toBe(false)
    for (const key of ['members', 'matrix', 'templates', 'backup', 'recovery', 'settings', 'status']) {
      expect(visible.has(key)).toBe(false)
    }
    expect(visible.has('enterprise-overview')).toBe(true)
    expect(visible.has('my-keys')).toBe(true)
  })

  test('自助注册当场拿到一次恢复码，并开放「我的 → 恢复码」补发入口', async () => {
    clearPendingRecovery()
    const token = tokenFor('newcomer')
    const code = 'RC-4N7K-2QX9-8M3D'
    const onSuccess = vi.fn()
    mockRoutes(config(), {
      '/v1/accounts/self-register': () =>
        json(200, {
          token,
          recovery_code: code,
          notice: '账号已创建并自动登录。恢复码只显示这一次，请离线保存；换机或忘记密码时用它重置。',
        }),
      '/v1/auth/me': () =>
        json(200, {
          principal: 'newcomer',
          display_name: null,
          scope: 'L3',
          permissions: ['account:read'],
          expires_at: Math.floor(Date.now() / 1000) + 3600,
          is_root: false,
          can_rotate_recovery: true,
        }),
    })

    render(<Login mode="web" onSuccess={onSuccess} />)
    fireEvent.click(await screen.findByRole('tab', { name: '创建账户' }))
    // 建号前就告知"会显示一次恢复码"，避免用户建完才第一次听说。
    expect(document.body.textContent).toContain('创建成功会显示一次恢复码')
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'newcomer' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'password123' } })
    fireEvent.click(screen.getByRole('button', { name: '创建并进入' }))

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
    // 响应里的恢复码必须立刻落进本机待展示位（主界面横幅只展示这一次）。
    expect(readPendingRecovery()).toMatchObject({ account: 'newcomer', code })
    const session = onSuccess.mock.calls[0][0] as Session
    expect(session.isRoot).toBe(false)
    expect(session.canRotateRecovery).toBe(true)

    const caps = deriveCaps(buildProfile(session, { members: [], count: 0 }, 'live', null))
    expect(caps.canRecovery).toBe(true)
    const visible = visibleNavKeys(caps)
    expect(visible.has('recovery')).toBe(true)
    // 恢复码入口之外，成员仍然看不到主账号专属页面。
    for (const key of ['members', 'matrix', 'templates', 'backup', 'settings', 'status']) {
      expect(visible.has(key)).toBe(false)
    }
    clearPendingRecovery()
  })

  test('本机生成恢复码：码直接显示在页面上，并自动填入输入框', async () => {
    mockRoutes(config(), {
      '/v1/accounts/recovery/local': () =>
        json(200, {
          written: true,
          recovered: false,
          file: 'recovery-handoff.bin',
          recovery_code: 'HAND-OFF-2026',
        }),
    })

    render(<Login mode="desktop" onSuccess={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: '忘记密码' }))
    fireEvent.click(screen.getByRole('button', { name: '没有恢复码？在本机生成' }))

    const panel = await screen.findByTestId('recovery-code')
    expect(panel.textContent).toContain('HAND-OFF-2026')
    expect(panel.textContent).toContain('只显示这一次')
    expect(panel.textContent).toContain('抄下来')
    expect(panel.textContent).not.toContain('已配对的手机端')
    expect(panel.textContent).toContain('用一次就会失效')
    expect(panel.textContent).toContain('重新生成')
    expect(screen.getByRole('button', { name: '复制' })).toBeTruthy()
    // 码同时被填进“恢复码”，用户不必再手抄一遍才能继续。
    expect((screen.getByLabelText('恢复码') as HTMLInputElement).value).toBe('HAND-OFF-2026')
    // 不能再引导用户去打开一个后端已经删掉的文件。
    expect(document.body.textContent).not.toContain('打开该文件')
    expect(document.body.textContent).not.toContain('recovery-handoff.bin')
    expectNoBannedText()
  })

  test('本机生成拿不到码时不指向文件，改为说明可用的替代路径', async () => {
    mockRoutes(config(), {
      '/v1/accounts/recovery/local': () =>
        json(200, { written: true, recovered: false, file: 'recovery-handoff.bin' }),
    })

    render(<Login mode="desktop" onSuccess={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: '忘记密码' }))
    fireEvent.click(screen.getByRole('button', { name: '没有恢复码？在本机生成' }))

    const alert = await screen.findByTestId('recovery-unavailable')
    expect(alert.textContent).toContain('无法在本机生成恢复码')
    expect(alert.textContent).toContain('备份')
    expect(alert.textContent).toContain('联系设备管理员')
    expect(screen.queryByTestId('recovery-code')).toBeNull()
    expect(document.body.textContent).not.toContain('打开该文件')
    expect(document.body.textContent).not.toContain('recovery-handoff.bin')
    // 兜底文案按要求点名“设备管理员”，其余禁用词仍必须为 0。
    expect(findBannedTerms(document.body.textContent ?? '').filter((term) => term !== '管理员')).toEqual([])
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

  test('访问层拒绝（401）：停止重试，指路桌面端且不再提配对/手机访问', async () => {
    let calls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === '/v1/auth/config') {
          calls += 1
          return json(401, { detail: 'Unauthorized' })
        }
        return json(404, { detail: 'Not Found' })
      }),
    )

    render(<Login mode="mobile" notice="expired" onSuccess={vi.fn()} />)

    const message = await screen.findByText(/无法读取登录配置（401）/)
    expect(message.textContent).toContain('鎏灏桌面端')
    expect(message.textContent).not.toContain('配对')
    expect(message.textContent).not.toContain('手机访问')
    expect(message.textContent).not.toContain('LIUHAO_ALLOW_REMOTE_RECOVERY')
    // 已经没有"重新配对"这种能力，页面上也不该再有对应入口。
    expect(screen.queryByTestId('login-repair-link')).toBeNull()
    // 不再显示会误导人的"正在准备"，也不显示与 401 矛盾的"登录已过期"。
    expect(document.body.textContent).not.toContain('正在准备')
    expect(screen.queryByTestId('login-notice')).toBeNull()
    // 读不到配置时表单必然失败，不再摆出来。
    expect(document.querySelector('#os-principal')).toBeNull()

    // 401 不安排重试：等过一个完整的重试周期后仍然只有 1 次请求。
    await new Promise((resolve) => setTimeout(resolve, 2500))
    expect(calls).toBe(1)
  })

  test('服务真不可用（5xx）：保持"正在准备"并继续自动重试', async () => {
    let calls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === '/v1/auth/config') {
          calls += 1
          return json(503, { detail: '暂时不可用' })
        }
        return json(404, { detail: 'Not Found' })
      }),
    )

    render(<Login mode="mobile" onSuccess={vi.fn()} />)

    expect(await screen.findByText(/正在准备/)).toBeTruthy()
    await new Promise((resolve) => setTimeout(resolve, 2400))
    expect(calls).toBeGreaterThanOrEqual(2)
    expect(document.body.textContent).not.toContain('无法读取登录配置')
  })

  test('连不上（服务还没起来）：同样保持"正在准备"并继续自动重试', async () => {
    let calls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        if (String(input) === '/v1/auth/config') calls += 1
        return Promise.reject(new TypeError('Failed to fetch'))
      }),
    )

    render(<Login mode="mobile" onSuccess={vi.fn()} />)

    expect(await screen.findByText(/正在准备/)).toBeTruthy()
    await new Promise((resolve) => setTimeout(resolve, 2400))
    expect(calls).toBeGreaterThanOrEqual(2)
  })
})
