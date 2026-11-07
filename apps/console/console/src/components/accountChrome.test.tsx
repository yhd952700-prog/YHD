/**
 * 外壳（导航 + 身份条）的账号面断言。
 *
 * 这是"不渲染而不是置灰"的渲染层证据：成员的 DOM 里根本没有治理入口，
 * 身份条也只有"工作空间"，整段可见文字不含任何结构词。
 */

import { describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Shell } from './Shell'
import type { Session } from '../lib/auth'
import type { AccountsPayload } from '../lib/accounts'
import { buildProfile, deriveCaps, findBannedTerms } from '../lib/accountCaps'

const PAYLOAD: AccountsPayload = {
  members: [
    { principal: 'root', permissions: ['*'], status: 'active' },
    { principal: 'member-a', permissions: ['account:read'], status: 'active' },
    {
      principal: 'member-b',
      permissions: ['account:read', 'account:manage_children'],
      status: 'active',
    },
  ],
  count: 3,
}

const ROOT = buildProfile(
  {
    token: 't',
    principal: 'root',
    displayName: '根',
    scope: 'human',
    permissions: [],
    expiresAt: 0,
    client: 'web',
    parentAccountId: null,
    path: '',
    depth: 0,
  } satisfies Session,
  PAYLOAD,
  'live',
  null,
)

const MEMBER = buildProfile(
  {
    token: 't',
    principal: 'member-b',
    displayName: '账号 B',
    scope: null,
    permissions: [],
    expiresAt: 0,
    client: 'web',
    parentAccountId: 'root',
    path: 'root/member-b',
    depth: 1,
  } satisfies Session,
  PAYLOAD,
  'live',
  null,
)

function renderShell(profile: typeof ROOT) {
  return render(
    <Shell
      mode="desktop"
      theme="dark"
      session={{
        token: 't',
        principal: profile.principal,
        displayName: profile.displayName,
        scope: profile.scope,
        permissions: profile.identityPermissions,
        expiresAt: 0,
        client: 'web',
        parentAccountId: profile.parentAccountId,
        path: profile.path,
        depth: profile.depth,
      }}
      profile={profile}
      caps={deriveCaps(profile)}
      active="overview"
      onNavigate={vi.fn()}
      onToggleTheme={vi.fn()}
      onLogout={vi.fn()}
      query=""
      onQueryChange={vi.fn()}
      notifications={[]}
    >
      <div>内容</div>
    </Shell>,
  )
}

describe('根身份条与导航', () => {
  test('顶部身份区使用中性文案，管理入口照常可见', () => {
    renderShell(ROOT)
    expect(screen.getByText('我的工作空间')).toBeTruthy()
    expect(screen.queryByText('主账号 · 可管理 2 位成员')).toBeNull()
    expect(screen.getByText('成员与权限管理')).toBeTruthy()
    expect(screen.getByText('权限矩阵')).toBeTruthy()
    expect(screen.getByText('全部 Key')).toBeTruthy()
  })
})

describe('成员身份条与导航', () => {
  test('身份条只写"我的工作空间"，治理入口在 DOM 里不存在', () => {
    renderShell(MEMBER)
    expect(screen.getByText('我的工作空间')).toBeTruthy()
    for (const label of ['成员与权限管理', '权限矩阵', '角色模板', '备份与迁移', '恢复码', '系统设置']) {
      expect(screen.queryByText(label)).toBeNull()
    }
    expect(screen.getByText('我的 Key')).toBeTruthy()
    expect(screen.getByText('我的账号')).toBeTruthy()
  })

  test('成员可见文字不含任何结构词', () => {
    renderShell(MEMBER)
    const text = document.body.textContent ?? ''
    expect(findBannedTerms(text)).toEqual([])
  })
})

describe('顶部身份区不可区分账号层级', () => {
  test('根与成员的顶部身份文字完全一致且不含层级词', () => {
    const rootView = renderShell(ROOT)
    const rootRole = rootView.container.querySelector('.os-user-role')?.textContent ?? ''
    rootView.unmount()

    const memberView = renderShell(MEMBER)
    const memberRole = memberView.container.querySelector('.os-user-role')?.textContent ?? ''

    expect(rootRole).toBe('我的工作空间')
    expect(memberRole).toBe(rootRole)
    expect(findBannedTerms(rootRole)).toEqual([])
  })
})
