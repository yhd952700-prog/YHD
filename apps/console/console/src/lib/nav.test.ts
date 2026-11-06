/**
 * 手机端底栏契约。
 *
 * 曾经的缺陷：`MOBILE_NAV_KEYS` 只有四项（overview / employees / approval /
 * settings），对话页（workbench）在手机上没有任何一键入口。这条断言把「五项且
 * 含管家」钉死，避免以后再被"精简"掉。
 */

import { describe, expect, test } from 'vitest'
import { ALL_NAV, MOBILE_NAV_KEYS } from './nav'

describe('手机端底部导航', () => {
  test('恰好五项，且包含对话入口 workbench', () => {
    expect(MOBILE_NAV_KEYS).toHaveLength(5)
    expect(MOBILE_NAV_KEYS).toContain('workbench')
  })

  test('每个 key 都能在 ALL_NAV 里找到（否则底栏会渲染出空洞）', () => {
    for (const key of MOBILE_NAV_KEYS) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })

  test('保留 default 页与"我的"入口', () => {
    expect(MOBILE_NAV_KEYS[0]).toBe('overview')
    expect(MOBILE_NAV_KEYS.at(-1)).toBe('settings')
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
    // PRIMARY / GOVERNANCE / FOOTER 三组曾经各自维护一份清单，同名入口叠在一起
    // 就是截图里"应用 ×5、数据中心 ×3"的来源。现在同一 key 只允许存在一次。
    const overlaps = ALL_NAV.filter(
      (item, index) =>
        ALL_NAV.findIndex((other) => other.key === item.key) !== index,
    )
    expect(overlaps).toEqual([])
  })
})

describe('管家 / 工作台分工', () => {
  test('两个入口各有一句定位，且不互相抄动作', () => {
    const butler = ALL_NAV.find((item) => item.key === 'butler')
    const workbench = ALL_NAV.find((item) => item.key === 'workbench')
    expect(butler?.hint).toContain('你下目标')
    expect(butler?.hint).toContain('拆解分派')
    expect(workbench?.hint).toContain('你自己看')
    expect(workbench?.hint).not.toContain('拆解分派')
  })

  test('员工管理入口独立于只读的能力注册表', () => {
    const mine = ALL_NAV.find((item) => item.key === 'my-employees')
    const registry = ALL_NAV.find((item) => item.key === 'employees')
    expect(mine?.label).toBe('我的员工')
    expect(registry?.label).toBe('能力注册表')
  })
})
