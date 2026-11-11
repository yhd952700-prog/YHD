/**
 * 手机端底栏契约 + 六组导航契约。
 *
 * 2026-11 Boss 定稿：手机首屏从运行看板换成**企业总览**，底栏六项与侧栏六组
 * 对齐（企业总览 / 办公室 / 任务 / 知识 / 能力 / 设置）。这是产品决策，不是
 * 为了让测试变绿；改断言时须在提交说明里写明。
 *
 * 历史缺陷（已修）：底栏曾只有四项，贾维斯与「目标与任务」在手机上无入口，
 * 窄屏只能看到派活回执、打不开结果列表。这两条继续钉死。
 */

import { describe, expect, test } from 'vitest'
import { ALL_NAV, MOBILE_NAV_KEYS, NAV_GROUPS, mobileNavKeysFor } from './nav'
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
  test('恰好六项，首项是企业总览（Boss 决定）', () => {
    expect(MOBILE_NAV_KEYS).toHaveLength(6)
    // 契约锚点：首项必须是企业总览，不能退回运行看板。
    expect(MOBILE_NAV_KEYS[0]).toBe('enterprise-overview')
    expect(MOBILE_NAV_KEYS).not.toContain('overview')
    expect(MOBILE_NAV_KEYS).not.toContain('workbench')
  })

  test('六项覆盖办公室、任务、知识、能力、设置', () => {
    expect(MOBILE_NAV_KEYS).toContain('my-employees')
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

  test('非根账号看不到设置时，第六项回退「我的」而不是死入口', () => {
    const keys = mobileNavKeysFor(memberCaps())
    expect(keys).toHaveLength(6)
    expect(keys[0]).toBe('enterprise-overview')
    expect(keys).toContain('me')
  })

  test('账号安全、我的数据、恢复码、关于与隐私仍可从设置组进入', () => {
    for (const key of ['account', 'my-data', 'recovery', 'about']) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })
})

describe('六组导航', () => {
  test('恰好六组，顺序固定', () => {
    expect(NAV_GROUPS.map((group) => group.label)).toEqual([
      '首页',
      '办公室',
      '任务',
      '知识',
      '能力',
      '设置',
    ])
  })

  test('每组都有主视图，且主视图在本组内', () => {
    for (const group of NAV_GROUPS) {
      expect(group.items.some((item) => item.key === group.defaultKey)).toBe(true)
    }
  })

  test('贾维斯在首页组，运行看板不再单独占入口', () => {
    const home = NAV_GROUPS.find((group) => group.key === 'home')
    expect(home?.items.map((item) => item.key)).toEqual(['enterprise-overview', 'butler'])
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
