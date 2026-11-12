/**
 * 深链门控与浏览器模式提示的集成测试。
 *
 * 核心要证明的事：成员用 `?p=preflight` / `?p=status` 打开时，目标页组件
 * **一次都不挂载**，因此 `/v1/production/preflight`、`/v1/alerts`、
 * `/v1/alerts/rules` 的调用次数必须是 0 —— 不是"请求失败后显示 403"。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'
import { savePendingRecovery, saveSession, type Session } from './lib/auth'

const PREFLIGHT = '/v1/production/preflight'
const ALERTS = '/v1/alerts'
const ALERT_RULES = '/v1/alerts/rules'

let currentIsRoot = false

function session(isRoot: boolean): Session {
  return {
    token: 'tok-test',
    principal: isRoot ? 'root' : 'member',
    displayName: null,
    scope: null,
    permissions: isRoot ? ['*'] : [],
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
    client: 'desktop',
    isRoot,
  }
}

function reply(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** App 挂载时会拉的一组真实端点；给最小可用形状，避免页面因缺字段崩掉。 */
function bodyFor(path: string): unknown {
  if (path === '/v1/auth/me') {
    return {
      still_human: true,
      principal: currentIsRoot ? 'root' : 'member',
      display_name: null,
      scope: null,
      expires_at: null,
      is_root: currentIsRoot,
    }
  }
  if (path === '/v1/accounts') return { members: [], count: 0 }
  if (path === '/v1/accounts/tree') {
    return {
      tree: {
        principal: 'root',
        permissions: [],
        status: 'active',
        children: [],
      },
    }
  }
  if (path === '/v1/policy/approvals') return { grants: [], enforcement: {} }
  if (path === '/v1/policy/enforcement') return {}
  if (path === '/v1/ready') return { status: 'ready', checks: {} }
  if (path === '/v1/metrics') return {}
  if (path === '/v1/health') {
    return { ai_provider: { configured: true, type: 'openai', model: 'gpt', simulated: false } }
  }
  if (path === '/v1/dashboard/roster') {
    return {
      totals: { employees: 0, kernels: 0, layers: 0, registry_entries: 0 },
      layers: [],
      kernels: [],
      bands: [],
      domains: [],
      items: [],
      registered_count: 0,
    }
  }
  if (path === '/v1/dashboard/summary') {
    return { sessions: { count: 0 }, audit: { total_events: 0 } }
  }
  if (path === '/v1/dashboard/activity') return { items: [] }
  if (path === '/v1/dashboard/analytics') {
    return {
      timeseries: { available: false, error: '当前没有数据' },
      breakdown: { available: false, items: [], error: '当前没有数据' },
    }
  }
  if (path === '/v1/auth/config') {
    return {
      auth_required: true,
      methods: ['password'],
      oauth: {},
      secret_store: { configured: true, source: 'keyring' },
      registered_humans: 1,
      login_eligible_humans: 1,
      client_modes: ['desktop'],
    }
  }
  if (path === '/v1/goals') return { goals: [] }
  if (path === PREFLIGHT) {
    return {
      ok: true,
      critical_count: 0,
      warning_count: 0,
      app_env: '',
      findings: [],
      checked_at: null,
    }
  }
  if (path === ALERTS) return { count: 0, firing_count: 0, alerts: [] }
  if (path === ALERT_RULES) return { count: 0, rules: [] }
  return {}
}

function installFetch() {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    return reply(bodyFor(url.split('?')[0]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

function count(calls: string[], path: string): number {
  return calls.filter((url) => url.split('?')[0] === path).length
}

function open(search: string, isRoot: boolean) {
  currentIsRoot = isRoot
  saveSession(session(isRoot), true)
  window.history.replaceState(null, '', `/${search}`)
  const harness = installFetch()
  render(<App />)
  return harness
}

function pageTitle(): string | null {
  return document.querySelector('.os-page-title')?.textContent ?? null
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 20))

describe('深链门控：成员不挂载管理员页', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    document.title = '鎏灏'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
  })

  test('成员 ?p=preflight：预检端点的调用次数为 0，并回落到企业总览', async () => {
    const { calls } = open('?p=preflight', false)

    await waitFor(() => expect(pageTitle()).toBe('企业总览'))
    await flush()

    expect(count(calls, PREFLIGHT)).toBe(0)
    expect(count(calls, ALERTS)).toBe(0)
    expect(count(calls, ALERT_RULES)).toBe(0)
  })

  test('成员 ?p=status：告警两个端点的调用次数为 0，并回落到企业总览', async () => {
    const { calls } = open('?p=status', false)

    await waitFor(() => expect(pageTitle()).toBe('企业总览'))
    await flush()

    expect(count(calls, ALERTS)).toBe(0)
    expect(count(calls, ALERT_RULES)).toBe(0)
    expect(count(calls, PREFLIGHT)).toBe(0)
  })

  test('管理员 ?p=preflight：页面正常渲染且请求发出', async () => {
    const { calls } = open('?p=preflight', true)

    await waitFor(() => expect(pageTitle()).toBe('生产预检'))
    await waitFor(() => expect(count(calls, PREFLIGHT)).toBeGreaterThan(0))
    expect(screen.getByText('预检结论')).toBeTruthy()
  })

  test('管理员 ?p=status：页面正常渲染且告警请求发出', async () => {
    const { calls } = open('?p=status', true)

    await waitFor(() => expect(pageTitle()).toBe('系统状态'))
    await waitFor(() => expect(count(calls, ALERTS)).toBeGreaterThan(0))
    expect(count(calls, ALERT_RULES)).toBeGreaterThan(0)
    expect(screen.getByText('运维告警')).toBeTruthy()
  })

  test('未知页面回落到默认页（企业总览），不白屏', async () => {
    open('?p=does-not-exist', false)

    await waitFor(() => expect(pageTitle()).toBe('企业总览'))
    expect(screen.getByText('企业运行概况')).toBeTruthy()
  })
})

describe('恢复码即时可见（PROD-006）', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.localStorage.clear()
  })

  test('注册响应后保存的恢复码无需刷新页面即显示', async () => {
    open('?p=overview', true)
    await flush()
    expect(document.body.textContent).not.toContain('下面是恢复码')

    // 模拟初始化完成后 Login 保存恢复码：App 早已挂载，必须靠事件同步。
    savePendingRecovery({ account: 'root', code: 'REC-INSTANT-2026', warning: null })

    await screen.findByText(/下面是恢复码/)
    expect(document.body.textContent).toContain('REC-INSTANT-2026')
    // 文案必须说清代价：只显示这一次，换机/忘密时用它。
    expect(document.body.textContent).toContain('只显示这一次')
    expect(document.body.textContent).toContain('换机或忘记密码时用它重置')
  })

  test('恢复码横幅可一键复制，且下载入口仍在', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    open('?p=overview', true)
    await flush()
    savePendingRecovery({ account: 'root', code: 'REC-COPY-2026', warning: null })

    fireEvent.click(await screen.findByRole('button', { name: '复制恢复码' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('REC-COPY-2026'))
    // CI 实测（run 43124949627）：writeText 已调用、handler 同步置 done，但满载
    // runner 上 React 提交可慢过 findByRole 默认 1s——放宽等待上限，断言不变：
    // 必须出现「已复制」。
    await screen.findByRole('button', { name: '已复制' }, { timeout: 5000 })
    expect(screen.getByRole('button', { name: '下载恢复材料' })).toBeTruthy()
  })
})

describe('?mode=web：浏览器模式标识', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.sessionStorage.clear()
    document.title = '鎏灏'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
    document.title = '鎏灏'
  })

  test('带 ?mode=web：标题有后缀，常驻提示可见且可关闭', async () => {
    open('?mode=web', false)

    await waitFor(() => expect(document.title).toBe('鎏灏 · 浏览器模式'))
    const notice = screen.getByTestId('browser-mode-notice')
    expect(notice.textContent).toContain('当前为浏览器模式')

    fireEvent.click(screen.getByText('知道了'))
    expect(screen.queryByTestId('browser-mode-notice')).toBeNull()
  })

  test('不带参数：打开工作台先看到企业总览，标题无后缀，提示不存在', async () => {
    open('', false)

    await waitFor(() => expect(pageTitle()).toBe('企业总览'))
    expect(document.title).toBe('鎏灏')
    expect(screen.queryByTestId('browser-mode-notice')).toBeNull()
  })
})
