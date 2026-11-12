/**
 * 今天小结文案拼装：三项齐全、非精确、缺项、403、全无。
 *
 * @vitest-environment jsdom
 */

import { describe, expect, test } from 'vitest'
import { buildTodaySummary } from './todaySummary'
import type { CompletedToday } from './enterpriseOverview'

function completed(overrides: Partial<CompletedToday> = {}): CompletedToday {
  return { count: 5, scanned: 5, total: 5, exact: true, missingTime: 0, ...overrides }
}

describe('buildTodaySummary', () => {
  test('三项齐全时按固定文案拼成一句', () => {
    const view = buildTodaySummary({
      completed: completed(),
      running: 3,
      pending: 2,
      pendingForbidden: false,
    })
    expect(view?.items).toEqual(['今天完成 5 个', '正在跑 3 个', '待批 2 件'])
    expect(view?.notes).toEqual([])
  })

  test('0 也是真数：三项都是 0 照样写出来', () => {
    const view = buildTodaySummary({
      completed: completed({ count: 0 }),
      running: 0,
      pending: 0,
      pendingForbidden: false,
    })
    expect(view?.items).toEqual(['今天完成 0 个', '正在跑 0 个', '待批 0 件'])
  })

  test('非精确时写「至少」，并小字注明只扫描了最近 N 条', () => {
    const view = buildTodaySummary({
      completed: completed({ count: 12, scanned: 200, total: 350, exact: false }),
      running: 1,
      pending: 0,
      pendingForbidden: false,
    })
    expect(view?.items[0]).toBe('今天完成至少 12 个')
    expect(view?.notes[0]).toContain('仅扫描最近 200 条已完成任务（全量 350 条）')
  })

  test('缺完成时间的条数如实注在小字里，不混进计数', () => {
    const view = buildTodaySummary({
      completed: completed({ count: 4, missingTime: 2 }),
      running: 0,
      pending: 0,
      pendingForbidden: false,
    })
    expect(view?.items[0]).toBe('今天完成 4 个')
    expect(view?.notes).toContain('2 条已完成任务缺少完成时间，未计入')
  })

  test('403 导致待批拿不到：只写 X 和 N，小字注明待批数未取到', () => {
    const view = buildTodaySummary({
      completed: completed(),
      running: 2,
      pending: null,
      pendingForbidden: true,
    })
    expect(view?.items).toEqual(['今天完成 5 个', '正在跑 2 个'])
    expect(view?.notes).toContain('待批数未取到：当前账号无权查看审批汇总')
  })

  test('单项未取到：只写能写出的项，小字注明缺了哪一项', () => {
    const view = buildTodaySummary({
      completed: null,
      running: 2,
      pending: 1,
      pendingForbidden: false,
    })
    expect(view?.items).toEqual(['正在跑 2 个', '待批 1 件'])
    expect(view?.notes).toContain('今日完成数暂未取到')
  })

  test('三项全无时返回 null（组件显示空态，不画假数字）', () => {
    expect(
      buildTodaySummary({
        completed: null,
        running: null,
        pending: null,
        pendingForbidden: false,
      }),
    ).toBeNull()
  })
})
