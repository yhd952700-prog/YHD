/**
 * 成员与权限合并页：默认标签正确、点另一标签切换内容。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { MembersPermissions } from './MembersPermissions'

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

const rootCaps: AccountCaps = {
  isRoot: true,
  isOwner: true,
  canMembers: true,
  canMatrix: true,
  canTemplates: true,
  canBackup: true,
  canRecovery: true,
  canSettings: true,
  canSystemStatus: true,
  canAllKeys: true,
  canMyMembers: false,
  canSubtreeMemory: true,
  canViewAs: true,
  canGrant: false,
  canApproveQueue: true,
}

describe('成员与权限', () => {
  beforeEach(() => {
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('默认打开成员管理标签', async () => {
    render(
      <MembersPermissions
        profile={rootProfile}
        caps={rootCaps}
        readOnly={false}
        onViewAs={() => {}}
      />,
    )

    expect(screen.getByRole('tab', { name: '成员管理' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByRole('heading', { name: '成员与权限管理' })
  })

  test('点权限矩阵切换内容，成员管理内容消失', async () => {
    render(
      <MembersPermissions
        profile={rootProfile}
        caps={rootCaps}
        readOnly={false}
        onViewAs={() => {}}
      />,
    )
    await screen.findByRole('heading', { name: '成员与权限管理' })

    fireEvent.click(screen.getByRole('tab', { name: '权限矩阵' }))

    expect(screen.getByRole('tab', { name: '权限矩阵' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByRole('heading', { name: '权限矩阵' })
    expect(screen.queryByRole('heading', { name: '成员与权限管理' })).toBeNull()
  })

  test('点角色模板切换内容', async () => {
    render(
      <MembersPermissions
        profile={rootProfile}
        caps={rootCaps}
        readOnly={false}
        onViewAs={() => {}}
      />,
    )
    await screen.findByRole('heading', { name: '成员与权限管理' })

    fireEvent.click(screen.getByRole('tab', { name: '角色模板' }))

    await screen.findByRole('heading', { name: '角色模板' })
  })

  test('initialTab=matrix 直接打开权限矩阵标签', async () => {
    render(
      <MembersPermissions
        initialTab="matrix"
        profile={rootProfile}
        caps={rootCaps}
        readOnly={false}
        onViewAs={() => {}}
      />,
    )

    expect(screen.getByRole('tab', { name: '权限矩阵' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByRole('heading', { name: '权限矩阵' })
  })
})
