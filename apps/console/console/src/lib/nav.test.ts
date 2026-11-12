/**
 * 手机端底栏契约 + 七组导航契约。
 *
 * 2026-11 Boss 定稿：手机首屏从运行看板换成**企业总览**，并要求底栏加入
 * **贾维斯**且放在第二项 —— 企业总览 / 贾维斯 / 办公室 / 任务 / 知识 / 能力 /
 * 设置，共七项。这是产品决策，不是为了让测试变绿；改断言时须在提交说明里写明。
 *
 * 历史缺陷（已修）：底栏曾只有四项，贾维斯与「目标与任务」在手机上无入口，
 * 窄屏只能看到派活回执、打不开结果列表。这两条继续钉死。
 */

import { describe, expect, test } from 'vitest'
import { ALL_NAV, MOBILE_NAV_KEYS, NAV_GROUPS, mobileNavKeysFor, visibleNavKeys } from './nav'
import { buildProfile, deriveCaps } from './accountCaps'
import type { Session } from './auth'

/** 一个最普通的成员会话：没有系统设置、没有成员管理。 */
function memberCaps() {
  const session: Session = {
    token: 't',
    principal: 'member',
    displayName: '成员',
    scope: 'human',
    permissions: [],
    expiresAt: 0,
    client: 'web',
    isRoot: false,
  }
  return deriveCaps(
    buildProfile(
      session,
      { members: [{ principal: 'member', permissions: [], status: 'active' }], count: 1 },
      'live',
      null,
    ),
  )
}

describe('手机端底部导航', () => {
  test('恰好七项，首项是企业总览、第二项是贾维斯（Boss 决定）', () => {
    expect(MOBILE_NAV_KEYS).toHaveLength(7)
    // 契约锚点：首项必须是企业总览，不能退回运行看板。
    expect(MOBILE_NAV_KEYS[0]).toBe('enterprise-overview')
    // 契约锚点：贾维斯固定在第二项，不能被权限裁剪或排序挤走。
    expect(MOBILE_NAV_KEYS[1]).toBe('butler')
    expect(MOBILE_NAV_KEYS).not.toContain('overview')
    expect(MOBILE_NAV_KEYS).not.toContain('workbench')
  })

  test('七项覆盖办公室、任务、知识、能力、设置', () => {
    expect(MOBILE_NAV_KEYS).toContain('office')
    expect(MOBILE_NAV_KEYS).toContain('task-center')
    expect(MOBILE_NAV_KEYS).toContain('knowledge')
    expect(MOBILE_NAV_KEYS).toContain('skills-center')
    expect(MOBILE_NAV_KEYS).toContain('settings')
  })

  test('每个 key 都能在 ALL_NAV 里找到（否则底栏会渲染出空洞）', () => {
    for (const key of MOBILE_NAV_KEYS) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })

  test('非根账号看不到设置时，第七项回退「我的」而不是死入口', () => {
    const keys = mobileNavKeysFor(memberCaps())
    expect(keys).toHaveLength(7)
    expect(keys[0]).toBe('enterprise-overview')
    expect(keys[1]).toBe('butler')
    expect(keys).toContain('me')
  })

  test('账号安全、我的数据、恢复码、关于与隐私仍可从设置组进入', () => {
    for (const key of ['account', 'my-data', 'recovery', 'about']) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })
})

describe('六组导航', () => {
  test('恰好七组，顺序固定', () => {
    expect(NAV_GROUPS.map((group) => group.label)).toEqual([
      '首页',
      'Owner',
      '办公室',
      '任务',
      '知识',
      '能力',
      '设置',
    ])
  })

  test('Owner 组只有四项，且只对 Owner 渲染', () => {
    const owner = NAV_GROUPS.find((group) => group.key === 'owner')
    expect(owner?.items.map((item) => item.key)).toEqual([
      'owner-overview',
      'owner-members',
      'owner-tasks',
      'owner-audit',
    ])
    expect(owner?.items.map((item) => item.label)).toEqual([
      '全局总览',
      '成员管理',
      '全部任务',
      '审计',
    ])
  })

  test('每组都有主视图，且主视图在本组内', () => {
    for (const group of NAV_GROUPS) {
      expect(group.items.some((item) => item.key === group.defaultKey)).toBe(true)
    }
  })

  test('操作台在首页组首位，运行看板不再单独占入口', () => {
    const home = NAV_GROUPS.find((group) => group.key === 'home')
    expect(home?.items.map((item) => item.key)).toEqual(['console', 'enterprise-overview', 'butler'])
    // 点"首页"组名落到操作台（登录后的默认页），不是企业总览。
    expect(home?.defaultKey).toBe('console')
    expect(ALL_NAV.some((item) => item.key === 'overview')).toBe(false)
    expect(ALL_NAV.some((item) => item.key === 'workbench')).toBe(false)
  })

  test('员工管理入口独立于只读的能力注册表', () => {
    const mine = ALL_NAV.find((item) => item.key === 'my-employees')
    const registry = ALL_NAV.find((item) => item.key === 'employees')
    expect(mine?.label).toBe('我的员工')
    expect(registry?.label).toBe('能力注册表')
    // 一个在办公室（要动手），一个在能力（只读）。
    expect(NAV_GROUPS.find((g) => g.key === 'office')?.items.some((i) => i.key === 'my-employees')).toBe(true)
    expect(NAV_GROUPS.find((g) => g.key === 'capabilities')?.items.some((i) => i.key === 'employees')).toBe(true)
  })
})

describe('导航去重（截图事故回归）', () => {
  test('每个 key 只出现一次', () => {
    const keys = ALL_NAV.map((item) => item.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  test('每个显示名只出现一次（防止分组拼接时重复渲染）', () => {
    const labels = ALL_NAV.map((item) => item.label)
    expect(new Set(labels).size).toBe(labels.length)
  })

  test('分组之间没有相同入口', () => {
    const overlaps = ALL_NAV.filter(
      (item, index) =>
        ALL_NAV.findIndex((other) => other.key === item.key) !== index,
    )
    expect(overlaps).toEqual([])
  })
})

describe('Owner 专属入口（F 组）', () => {
  /** Owner 会话：首次注册的账号，后端下发 is_owner。 */
  function ownerCaps() {
    const session: Session = {
      token: 't',
      principal: 'owner',
      displayName: 'Owner',
      scope: 'human',
      permissions: [],
      expiresAt: 0,
      client: 'web',
      isRoot: true,
      isOwner: true,
    }
    return deriveCaps(
      buildProfile(
        session,
        { members: [{ principal: 'owner', permissions: [], status: 'active' }], count: 1 },
        'live',
        null,
      ),
    )
  }

  test('Owner 看得到全部四项 Owner 入口，普通成员一项都看不到', () => {
    const ownerKeys = visibleNavKeys(ownerCaps())
    for (const key of ['owner-overview', 'owner-members', 'owner-tasks', 'owner-audit']) {
      expect(ownerKeys.has(key)).toBe(true)
    }
    const memberKeys = visibleNavKeys(memberCaps())
    for (const key of ['owner-overview', 'owner-members', 'owner-tasks', 'owner-audit']) {
      expect(memberKeys.has(key)).toBe(false)
    }
  })

  test('Owner 入口在 Owner 组（不是设置组），不另开第八组', () => {
    expect(NAV_GROUPS).toHaveLength(7)
    const owner = NAV_GROUPS.find((group) => group.key === 'owner')
    expect(owner?.items.map((item) => item.key)).toEqual([
      'owner-overview',
      'owner-members',
      'owner-tasks',
      'owner-audit',
    ])
    const settings = NAV_GROUPS.find((group) => group.key === 'settings')
    expect(settings?.items.some((item) => item.key === 'owner-overview')).toBe(false)
    expect(settings?.items.some((item) => item.key === 'owner-tasks')).toBe(false)
  })

  test('旧会话没有 is_owner 时回落到 isRoot，Owner 入口不消失', () => {
    const session: Session = {
      token: 't',
      principal: 'owner',
      displayName: 'Owner',
      scope: 'human',
      permissions: [],
      expiresAt: 0,
      client: 'web',
      isRoot: true,
    }
    const caps = deriveCaps(
      buildProfile(
        session,
        { members: [{ principal: 'owner', permissions: [], status: 'active' }], count: 1 },
        'live',
        null,
      ),
    )
    expect(caps.isOwner).toBe(true)
    expect(visibleNavKeys(caps).has('owner-overview')).toBe(true)
  })
})
