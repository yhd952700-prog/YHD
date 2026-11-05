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
