/**
 * 运维页的"字段缺失"回归。
 *
 * 后端降级响应会漏掉数值字段。旧代码对这些值直接 `toFixed()` / 直接拼进 JSX，
 * 结果是 `TypeError: Cannot read properties of undefined (reading "toFixed")`
 * —— 整页白屏，而不是少一个数字。
 *
 * 这里的契约有两条：
 * 1. 字段缺失时卡片仍然渲染，数值位显示「—」；
 * 2. 真实的 0 仍然显示 0（缺失与零不能混为一谈）。
 */

import { afterEach, describe, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { SystemStatus } from './Operations'
import { UNKNOWN_PROVIDER } from '../lib/providerStatus'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

interface Overrides {
  metrics?: unknown
  roster?: unknown
  alerts?: unknown
  auth?: unknown
}

function mockOperations(overrides: Overrides = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/v1/ready') return json({ status: 'ok', checks: {} })
      if (path.startsWith('/v1/metrics')) return json(overrides.metrics ?? {})
      if (path.startsWith('/v1/policy/enforcement')) {
        return json({ enabled: false, env_var: null, enforced_actions: [] })
      }
      if (path.startsWith('/v1/dashboard/roster')) {
        return json(
          overrides.roster ?? {
            available: true,
            kernels: [],
            layers: [],
            domains: [],
            bands: [],
            providers: {},
          },
        )
      }
      if (path.startsWith('/v1/auth/config')) return json(overrides.auth ?? {})
      if (path.startsWith('/v1/alerts/rules')) return json({ count: 0, rules: [] })
      if (path.startsWith('/v1/alerts')) return json(overrides.alerts ?? { alerts: [] })
      return json({})
    }),
  )
}

/** `<dt>` 的兄弟节点 `<dd>` 文本（运行指标 / 注册表快照用的是 os-kv 定义列表）。 */
function valueOf(label: string): string | null {
  return screen.getByText(label).nextElementSibling?.textContent ?? null
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('运维页在后端漏发数值字段时不白屏', () => {
  test('运行指标 / 注册表快照 / 运维告警 / 身份与会话都渲染，数值位显示「—」', async () => {
    mockOperations()
    render(<SystemStatus provider={UNKNOWN_PROVIDER} />)

    // 卡片标题是静态的：先证明整页已经渲染，而不是抛异常后只剩错误边界。
    expect(screen.getByText('运行指标')).toBeTruthy()
    expect(screen.getByText('注册表快照')).toBeTruthy()
    expect(screen.getByText('运维告警')).toBeTruthy()

    await waitFor(() => expect(valueOf('API 密钥总数')).toBe('—'))
    expect(valueOf('运行时长')).toBe('—')
    expect(valueOf('已过期密钥')).toBe('—')
    expect(valueOf('RBAC 角色数')).toBe('—')
    expect(valueOf('限流桶')).toBe('—')

    await waitFor(() => expect(valueOf('能力模块 / 能力层')).toBe('— / —'))
    expect(valueOf('注册表条目')).toBe('—')

    await waitFor(() => expect(valueOf('已登记账号')).toBe('—'))
    expect(valueOf('可登录账号')).toBe('—')
    expect(valueOf('会话有效期')).toBe('—')

    const firing = screen.getByText('正在触发').parentElement?.textContent ?? ''
    expect(firing).toContain('—')
    const history = screen.getByText('历史告警总数').parentElement?.textContent ?? ''
    expect(history).toContain('—')
  })

  test('有值时显示真实数字；真实的 0 依然是 0（不是「—」）', async () => {
    mockOperations({
      metrics: {
        uptime_seconds: 3725,
        components: {
          api_key_manager: { total_keys: 7, expired_count: 1 },
          rbac_manager: { total_roles: 3 },
          rate_limiter: { active_buckets: 2 },
        },
      },
      roster: {
        available: true,
        source: 'capabilities.yaml',
        version: '3',
        totals: { kernels: 355, layers: 11, registry_entries: 12345 },
        kernels: [],
        layers: [],
        domains: [],
        bands: [],
        providers: {},
      },
      alerts: { count: 0, firing_count: 0, alerts: [] },
      auth: {
        secret_store: { configured: false },
        registered_humans: 12,
        login_eligible_humans: 11,
        token: { ttl_seconds: 28800 },
        oauth: {},
      },
    })
    render(<SystemStatus provider={UNKNOWN_PROVIDER} />)

    await waitFor(() => expect(valueOf('API 密钥总数')).toBe('7'))
    expect(valueOf('已过期密钥')).toBe('1')
    expect(valueOf('RBAC 角色数')).toBe('3')
    expect(valueOf('限流桶')).toBe('2')
    expect(valueOf('运行时长')).not.toBe('—')

    await waitFor(() => expect(valueOf('能力模块 / 能力层')).toBe('355 / 11'))
    expect(valueOf('注册表条目')).toBe('12k')
    expect(valueOf('已登记账号')).toBe('12')
    expect(valueOf('可登录账号')).toBe('11')
    expect(valueOf('会话有效期')).not.toBe('—')

    await waitFor(() => {
      const firing = screen.getByText('正在触发').parentElement?.textContent ?? ''
      expect(firing).toContain('0')
      expect(firing).not.toContain('—')
    })
  })
})
