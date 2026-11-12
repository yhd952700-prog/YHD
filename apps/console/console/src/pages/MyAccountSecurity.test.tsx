/**
 * 我的账号与安全合并页：默认标签正确、点另一标签切换内容；
 * 无权看的标签（恢复码 / 我的账号）不渲染出来。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import type { Session } from '../lib/auth'
import { MyAccountSecurity } from './MyAccountSecurity'

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
      if (path === '/v1/accounts') return reply({ members: [], count: 0 })
      if (path === '/v1/accounts/tree') {
        return reply({
          tree: { principal: 'root', permissions: [], status: 'active', children: [] },
        })
      }
      if (path === '/v1/accounts/roles') return reply({ roles: [] })
      if (path.endsWith('/usage')) {
        return reply({
          principal: 'root',
          scope: 'self',
          callers_visible: false,
          calls: 0,
          tokens_in: 0,
          tokens_out: 0,
          total_tokens: 0,
          by_key_owner: [],
          by_actor: [],
        })
      }
      return reply({})
    }),
  )
}

function profile(isRoot: boolean): AccountProfile {
  return {
    principal: isRoot ? 'root' : 'member',
    displayName: null,
    scope: null,
    identityPermissions: [],
    accountPermissions: [],
    isRoot,
    isOwner: isRoot,
    canSelfRecover: isRoot,
    members: [],
    memberCount: 0,
    source: 'live',
    loaded: true,
    error: null,
  }
}

function caps(isRoot: boolean): AccountCaps {
  return {
    isRoot,
    isOwner: isRoot,
    canMembers: isRoot,
    canMatrix: isRoot,
    canTemplates: isRoot,
    canBackup: isRoot,
    canRecovery: isRoot,
    canSettings: isRoot,
    canSystemStatus: isRoot,
    canAllKeys: isRoot,
    canMyMembers: !isRoot,
    canSubtreeMemory: isRoot,
    canViewAs: isRoot,
    canGrant: false,
    canApproveQueue: isRoot,
  }
}

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
    isOwner: isRoot,
  }
}

describe('我的账号与安全（根账号）', () => {
  beforeEach(() => {
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('默认打开我的 Key 标签；根没有“我的账号”标签', async () => {
    render(
      <MyAccountSecurity
        profile={profile(true)}
        caps={caps(true)}
        readOnly={false}
        session={session(true)}
        onNavigate={() => {}}
        onViewAs={() => {}}
      />,
    )

    expect(screen.getByRole('tab', { name: '我的 Key' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByRole('heading', { name: '全部 Key' })
    expect(screen.queryByRole('tab', { name: '我的账号' })).toBeNull()
  })

  test('点账号安全、恢复码、我的各切换到对应内容', async () => {
    render(
      <MyAccountSecurity
        profile={profile(true)}
        caps={caps(true)}
        readOnly={false}
        session={session(true)}
        onNavigate={() => {}}
        onViewAs={() => {}}
      />,
    )
    await screen.findByRole('heading', { name: '全部 Key' })

    fireEvent.click(screen.getByRole('tab', { name: '账号安全' }))
    await screen.findByRole('heading', { name: '修改我的密码' })

    fireEvent.click(screen.getByRole('tab', { name: '恢复码' }))
    await screen.findByRole('heading', { name: '恢复码' })

    fireEvent.click(screen.getByRole('tab', { name: '我的' }))
    await screen.findByRole('heading', { name: '我的' })
  })
})

describe('我的账号与安全（成员）', () => {
  beforeEach(() => {
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('没有恢复码标签，有“我的账号”标签', async () => {
    render(
      <MyAccountSecurity
        profile={profile(false)}
        caps={caps(false)}
        readOnly={false}
        session={session(false)}
        onNavigate={() => {}}
        onViewAs={() => {}}
      />,
    )

    expect(screen.queryByRole('tab', { name: '恢复码' })).toBeNull()
    expect(screen.getByRole('tab', { name: '我的账号' })).toBeTruthy()
    await screen.findByRole('heading', { name: '我的 Key' })
  })

  test('点“我的账号”切换到成员视图', async () => {
    render(
      <MyAccountSecurity
        profile={profile(false)}
        caps={caps(false)}
        readOnly={false}
        session={session(false)}
        onNavigate={() => {}}
        onViewAs={() => {}}
      />,
    )
    await screen.findByRole('heading', { name: '我的 Key' })

    fireEvent.click(screen.getByRole('tab', { name: '我的账号' }))

    await screen.findByRole('heading', { name: '我的账号' })
  })

  test('initialTab 指向被隐藏的恢复码时回落到第一个可见标签', () => {
    render(
      <MyAccountSecurity
        initialTab="recovery"
        profile={profile(false)}
        caps={caps(false)}
        readOnly={false}
        session={session(false)}
        onNavigate={() => {}}
        onViewAs={() => {}}
      />,
    )

    expect(screen.getByRole('tab', { name: '我的 Key' }).getAttribute('aria-selected')).toBe(
      'true',
    )
  })
})
