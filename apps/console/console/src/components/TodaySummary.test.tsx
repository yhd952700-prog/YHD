/**
 * 操作台顶部「今天小结」条：有数时三项都出现，失败时显示空态。
 *
 * @vitest-environment jsdom
 */

import { describe, expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TodaySummary } from './TodaySummary'

describe('今天小结', () => {
  test('有数时三项都出现，连成一句', () => {
    render(
      <TodaySummary
        data={{
          completed: { count: 5, scanned: 5, total: 5, exact: true, missingTime: 0 },
          running: 3,
          pending: 2,
          pendingForbidden: false,
        }}
      />,
    )
    expect(screen.getByRole('status').textContent).toContain(
      '今天完成 5 个、正在跑 3 个、待批 2 件',
    )
  })

  test('非精确时写「至少」并注明只扫描了最近 N 条', () => {
    render(
      <TodaySummary
        data={{
          completed: { count: 12, scanned: 200, total: 350, exact: false, missingTime: 0 },
          running: 1,
          pending: 0,
          pendingForbidden: false,
        }}
      />,
    )
    const text = screen.getByRole('status').textContent ?? ''
    expect(text).toContain('今天完成至少 12 个')
    expect(text).toContain('仅扫描最近 200 条已完成任务')
  })

  test('三项全无时显示空态，不画假数字', () => {
    render(
      <TodaySummary
        data={{ completed: null, running: null, pending: null, pendingForbidden: false }}
      />,
    )
    expect(screen.getByRole('status').textContent).toBe('今日数据暂未取到')
  })

  test('403 导致待批拿不到：只写两项并注明待批数未取到', () => {
    render(
      <TodaySummary
        data={{
          completed: { count: 5, scanned: 5, total: 5, exact: true, missingTime: 0 },
          running: 2,
          pending: null,
          pendingForbidden: true,
        }}
      />,
    )
    const text = screen.getByRole('status').textContent ?? ''
    expect(text).toContain('今天完成 5 个、正在跑 2 个')
    // 第三项不能出现：小结正文里没有「、待批 … 件」这一项
    // （小字里的「待批数未取到」是缺口说明，不是第三项）。
    expect(text).not.toContain('、待批')
    expect(text).toContain('待批数未取到')
  })
})
