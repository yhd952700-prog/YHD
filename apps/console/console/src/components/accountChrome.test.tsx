/**
 * 外壳（导航 + 身份条）的账号面断言。
 *
 * 这是"不渲染而不是置灰"的渲染层证据：成员的 DOM 里根本没有治理入口，
 * 身份条也只有"工作空间"，整段可见文字不含任何结构词。
 */

import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
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
    isRoot: true,
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
    isRoot: false,
  } satisfies Session,
  PAYLOAD,
  'live',
  null,
)

/**
 * 导航范围的测试隔离：Shell 默认读本地记住的「日常 / 全部」与 URL 参数，
 * 上一个测试点的「更多」会通过 replaceState 留在 URL 里，所以每个测试前
 * 都把本地值与 URL 参数清掉；需要看全部入口的测试再显式记成「全部」。
 */
beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState(null, '', window.location.pathname)
})

/** 把导航范围记成「全部」再渲染：权限可见性测试看的是全部入口，不是日常默认。 */
function renderShellAll(profile: typeof ROOT) {
  window.localStorage.setItem('jarvis.nav_scope', 'all')
  return renderShell(profile)
}

function renderShell(profile: typeof ROOT) {
  return render(
    <Shell
      pageTitle="账号面测试"
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
        isRoot: profile.isRoot,
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
    // 权限可见性在「全部」范围下断言；日常默认只显示 8 个入口，见下方的收小测试。
    renderShellAll(ROOT)
    expect(screen.getByText('我的工作空间')).toBeTruthy()
    expect(screen.queryByText('主账号 · 可管理 2 位成员')).toBeNull()
    // 简约版：旧的「成员与权限管理 / 权限矩阵 / 全部 Key」已并进合并页，
    // 侧栏上只剩「成员与权限」「全部任务与审计」「我的账号与安全」「系统状态与诊断」。
    expect(screen.getByText('成员与权限')).toBeTruthy()
    expect(screen.getByText('全部任务与审计')).toBeTruthy()
    expect(screen.getByText('我的账号与安全')).toBeTruthy()
    expect(screen.getByText('系统状态与诊断')).toBeTruthy()
  })
})

describe('成员身份条与导航', () => {
  test('身份条只写"我的工作空间"，治理入口在 DOM 里不存在', () => {
    renderShellAll(MEMBER)
    expect(screen.getByText('我的工作空间')).toBeTruthy()
    for (const label of ['成员与权限', '权限矩阵', '角色模板', '备份与迁移', '恢复码', '系统设置']) {
      expect(screen.queryByText(label)).toBeNull()
    }
    // 「我的 Key / 我的账号」已并进「我的账号与安全」；成员的侧栏与底栏各有一处。
    expect(screen.getAllByText('我的账号与安全').length).toBe(2)
    expect(screen.getByText('系统状态与诊断')).toBeTruthy()
  })

  test('成员可见文字不含任何结构词', () => {
    renderShellAll(MEMBER)
    const text = document.body.textContent ?? ''
    expect(findBannedTerms(text)).toEqual([])
  })
})

describe('顶部身份区不可区分账号层级', () => {
  test('根与成员的顶部身份文字完全一致且不含层级词', () => {
    const rootView = renderShellAll(ROOT)
    const rootRole = rootView.container.querySelector('.os-user-role')?.textContent ?? ''
    rootView.unmount()

    const memberView = renderShellAll(MEMBER)
    const memberRole = memberView.container.querySelector('.os-user-role')?.textContent ?? ''

    expect(rootRole).toBe('我的工作空间')
    expect(memberRole).toBe(rootRole)
    expect(findBannedTerms(rootRole)).toEqual([])
  })
})

describe('日常 / 全部切换（2026-11 收小）', () => {
  test('默认是日常：根只看到 8 个常用入口，治理入口不在 DOM 里，但有「更多」', () => {
    renderShell(ROOT)
    for (const label of ['操作台', '待办审批', '工作区文件', '知识中心', '系统设置']) {
      expect(screen.getByText(label)).toBeTruthy()
    }
    // 「办公室」不在此列：手机底栏（始终渲染、靠 CSS 隐藏）里也有这个短标签。
    // 「能力中心」也不在此列：它在底栏第六项，日常模式下也在 DOM 里。
    for (const label of ['成员与权限', '全部任务与审计', '我的账号与安全', '系统状态与诊断']) {
      expect(screen.queryByText(label)).toBeNull()
    }
    expect(screen.getByRole('button', { name: /更多/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /日常/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /全部/ })).toBeTruthy()
  })

  test('点「更多」展开全部入口，再点「日常」收回去；选择会记下来', () => {
    renderShell(ROOT)
    fireEvent.click(screen.getByRole('button', { name: /更多/ }))
    expect(screen.getByText('成员与权限')).toBeTruthy()
    expect(screen.getByText('全部任务与审计')).toBeTruthy()
    expect(screen.getByText('我的账号与安全')).toBeTruthy()
    expect(screen.getByText('系统状态与诊断')).toBeTruthy()
    expect(screen.queryByText(/更多/)).toBeNull()
    // 选择记在本地与 URL 参数里，刷新后不丢。
    expect(window.localStorage.getItem('jarvis.nav_scope')).toBe('all')
    expect(window.location.search).toContain('nav=all')

    fireEvent.click(screen.getByRole('button', { name: /日常/ }))
    expect(screen.queryByText('成员与权限')).toBeNull()
    expect(screen.getByRole('button', { name: /更多/ })).toBeTruthy()
    expect(window.localStorage.getItem('jarvis.nav_scope')).toBe('daily')
  })

  test('成员的日常没有系统设置，点「全部」也不会多出治理入口', () => {
    renderShell(MEMBER)
    expect(screen.queryByText('系统设置')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /全部/ }))
    for (const label of ['成员与权限', '系统设置']) {
      expect(screen.queryByText(label)).toBeNull()
    }
    // 「我的 Key / 我的账号」已并进「我的账号与安全」；成员的侧栏与底栏各有一处。
    expect(screen.getAllByText('我的账号与安全').length).toBe(2)
    expect(screen.getByText('系统状态与诊断')).toBeTruthy()
  })
})
