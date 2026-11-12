/**
 * 系统状态与诊断合并页：默认标签正确、点另一标签切换内容。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { AccountProfile } from '../lib/accountCaps'
import type { ActiveProviderStatus } from '../lib/providerStatus'
import { SystemDiagnostics } from './SystemDiagnostics'

function reply(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).split('?')[0]
      if (path === '/v1/ready') return reply({ status: 'ready', checks: {} })
      if (path === '/v1/dashboard/roster') {
        return reply({
          totals: { employees: 0, kernels: 0, layers: 0, registry_entries: 0 },
          layers: [],
          kernels: [],
          bands: [],
          domains: [],
          items: [],
          registered_count: 0,
        })
      }
      if (path === '/v1/auth/config') {
        return reply({
          auth_required: true,
          methods: ['password'],
          oauth: {},
          secret_store: { configured: true, source: 'keyring' },
          registered_humans: 1,
          login_eligible_humans: 1,
          client_modes: ['desktop'],
        })
      }
      if (path === '/v1/identity/summary') {
        return reply({
          total_principals: 0,
          by_kind: {},
          by_status: {},
          by_scope: {},
          total_grants: 0,
          individual_principals: {},
          scope_note: '',
          registry: {},
          warnings: [],
          source: {},
        })
      }
      if (path === '/v1/identity/principals') {
        return reply({
          principals: [],
          count: 0,
          total: 0,
          truncated: false,
          by_kind: {},
          filters: {},
          warnings: [],
        })
      }
      if (path === '/v1/identity/permissions') {
        return reply({ grants: [], count: 0, truncated: false, filters: {}, notes: [] })
      }
      return reply({})
    }),
  )
}

const rootProfile: AccountProfile = {
  principal: 'root',
  displayName: null,
  scope: null,
  identityPermissions: [],
  accountPermissions: [],
  isRoot: true,
  isOwner: true,
  canSelfRecover: true,
  members: [],
  memberCount: 0,
  source: 'live',
  loaded: true,
  error: null,
}

const provider: ActiveProviderStatus = {
  provider: null,
  posture: {
    isMock: true,
    simulated: true,
    isReal: false,
    verdict: '未配置真实模型',
    tone: 'warn',
    badge: '未配置',
  },
  mode: 'mock',
  isReal: false,
  isMock: true,
  label: '未配置真实模型',
  detail: '',
  error: null,
}

describe('系统状态与诊断', () => {
  beforeEach(() => {
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('默认打开系统状态标签', async () => {
    render(
      <SystemDiagnostics query="" profile={rootProfile} provider={provider} />,
    )

    expect(screen.getByRole('tab', { name: '系统状态' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('依赖健康')
  })

  test('点信任切换内容，系统状态内容消失', async () => {
    render(
      <SystemDiagnostics query="" profile={rootProfile} provider={provider} />,
    )
    await screen.findByText('依赖健康')

    fireEvent.click(screen.getByRole('tab', { name: '信任' }))

    expect(screen.getByRole('tab', { name: '信任' }).getAttribute('aria-selected')).toBe('true')
    await screen.findByText('信任状态查询')
    expect(screen.queryByText('依赖健康')).toBeNull()
  })

  test('点网络、身份与权限各切换到对应内容', async () => {
    render(
      <SystemDiagnostics query="" profile={rootProfile} provider={provider} />,
    )
    await screen.findByText('依赖健康')

    fireEvent.click(screen.getByRole('tab', { name: '网络' }))
    await screen.findByText('网络总线状态')

    fireEvent.click(screen.getByRole('tab', { name: '身份与权限' }))
    // 「我的范围」只对非根渲染；根看到的是身份总览。
    await screen.findByText('身份总览 / Identity Summary')
  })

  test('initialTab=identity 直接打开身份与权限标签', async () => {
    render(
      <SystemDiagnostics
        query=""
        profile={rootProfile}
        provider={provider}
        initialTab="identity"
      />,
    )

    expect(screen.getByRole('tab', { name: '身份与权限' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('身份总览 / Identity Summary')
  })
})
