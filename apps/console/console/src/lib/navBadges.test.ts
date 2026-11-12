/**
 * 侧栏徽标「数 → 徽标值」的纯判断：0 不显示、null 不显示、失败不显示。
 *
 * @vitest-environment jsdom
 */

import { describe, expect, test } from 'vitest'
import {
  badgeForCount,
  buildNavBadges,
  pendingApprovalsFrom,
  runningTasksFromLedger,
} from './navBadges'

describe('badgeForCount', () => {
  test('> 0 才成徽标', () => {
    expect(badgeForCount(3, 'danger')).toEqual({ value: 3, tone: 'danger' })
    expect(badgeForCount(1, 'accent')).toEqual({ value: 1, tone: 'accent' })
  })

  test('0、null、NaN、负数一律不显示（绝不显示 0 占位）', () => {
    expect(badgeForCount(0, 'danger')).toBeUndefined()
    expect(badgeForCount(null, 'danger')).toBeUndefined()
    expect(badgeForCount(undefined, 'danger')).toBeUndefined()
    expect(badgeForCount(Number.NaN, 'danger')).toBeUndefined()
    expect(badgeForCount(-2, 'danger')).toBeUndefined()
  })
})

describe('buildNavBadges', () => {
  test('待批 danger、在跑 accent，各自独立', () => {
    expect(buildNavBadges({ pendingApprovals: 2, runningTasks: 3 })).toEqual({
      'workflow-approvals': { value: 2, tone: 'danger' },
      'task-center': { value: 3, tone: 'accent' },
    })
  })

  test('一边为 0 只出另一边，两边都 0/空则空对象', () => {
    expect(buildNavBadges({ pendingApprovals: 0, runningTasks: 5 })).toEqual({
      'task-center': { value: 5, tone: 'accent' },
    })
    expect(buildNavBadges({ pendingApprovals: 0, runningTasks: 0 })).toEqual({})
    expect(buildNavBadges({ pendingApprovals: null, runningTasks: null })).toEqual({})
  })
})

describe('pendingApprovalsFrom（/v1/workflow/summary）', () => {
  test('正常取数，0 是真数', () => {
    expect(pendingApprovalsFrom({ pending_approvals: 7 }, null)).toBe(7)
    expect(pendingApprovalsFrom({ pending_approvals: 0 }, null)).toBe(0)
  })

  test('失败（含 403）时返回 null，不拿旧数据顶替', () => {
    expect(pendingApprovalsFrom({ pending_approvals: 7 }, '请求失败')).toBeNull()
  })

  test('形状不对返回 null：缺字段、非数字、非对象', () => {
    expect(pendingApprovalsFrom({}, null)).toBeNull()
    expect(pendingApprovalsFrom({ pending_approvals: '7' }, null)).toBeNull()
    expect(pendingApprovalsFrom(null, null)).toBeNull()
    expect(pendingApprovalsFrom([], null)).toBeNull()
  })
})

describe('runningTasksFromLedger（/v1/tasks/ledger/status）', () => {
  const ledger = (byStatus: Record<string, number>) => ({
    ledger: {
      db_path: '/tmp/x.db',
      schema_version: 1,
      counts: { tasks: 1, steps: 0, runs: 0 },
      by_status: byStatus,
      recent_migrations: [],
    },
    lag: {
      hot_goals: 0,
      ledger_tasks: 1,
      missing_in_ledger: [],
      stale: [],
      lag_total: 0,
      write_failures: 0,
      last_failure_at: null,
      last_failure_reason: null,
      default_owner: '',
    },
  })

  test('正常取 by_status.running', () => {
    expect(runningTasksFromLedger(ledger({ running: 4, completed: 9 }), null)).toBe(4)
  })

  test('缺 running 键即真 0（SQL GROUP BY 口径）', () => {
    expect(runningTasksFromLedger(ledger({ completed: 9 }), null)).toBe(0)
  })

  test('失败时返回 null；形状不对返回 null', () => {
    expect(runningTasksFromLedger(ledger({ running: 4 }), '请求失败')).toBeNull()
    expect(runningTasksFromLedger({}, null)).toBeNull()
    expect(runningTasksFromLedger(null, null)).toBeNull()
  })
})
