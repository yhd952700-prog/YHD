/**
 * 布局覆盖的回归：360px 下不能靠历史覆盖值切到桌面栅格。
 *
 * 报告实测：手机上点「宽屏布局」后 scrollWidth 725 对 360，侧栏 248px、
 * 正文只剩 112px、底栏 display:none，用户在手机上无法退回。
 */

import { describe, expect, test } from 'vitest'
import { DEVICE_MODES, layoutChoicesFor, resolveMode } from './mode'

describe('布局覆盖', () => {
  test('手机浏览器忽略历史覆盖，固定窄屏', () => {
    expect(resolveMode('mobile', 'desktop')).toBe('mobile')
    expect(resolveMode('mobile', 'web')).toBe('mobile')
    expect(resolveMode('mobile', null)).toBe('mobile')
  })

  test('桌面壳与宽屏浏览器仍按覆盖值', () => {
    expect(resolveMode('desktop', null)).toBe('desktop')
    expect(resolveMode('desktop', 'web')).toBe('web')
    expect(resolveMode('web', 'mobile')).toBe('mobile')
    expect(resolveMode('web', null)).toBe('web')
  })

  test('手机端只提供窄屏选项，其他端保留全部', () => {
    expect(layoutChoicesFor('mobile')).toEqual(['mobile'])
    expect(layoutChoicesFor('web')).toEqual(DEVICE_MODES)
    expect(layoutChoicesFor('desktop')).toEqual(DEVICE_MODES)
  })
})
