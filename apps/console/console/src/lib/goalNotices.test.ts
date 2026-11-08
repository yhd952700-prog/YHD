/**
 * 通知栏里的"派活结果"契约。
 *
 * 用户关掉页面后回来，通知栏不能只显示"一切正常"。这里钉死：
 * 失败必须有醒目条目；进行中不制造噪音；历史失败不会一次性倒出来；
 * 文案不含内部状态码/异常。
 */

import { describe, expect, test } from 'vitest'
import { deriveGoalNotices, goalNotice } from './goalNotices'
import type { GoalSummary } from './operator'

function goal(overrides: Partial<GoalSummary> = {}): GoalSummary {
  return {
    goal_id: 'g1',
    state: 'completed',
    natural_language: '整理本周的项目进展',
    scope: 'project',
    created_at: 1_000_000,
    error: null,
    replan_suggested: false,
    task_count: 3,
    completed_tasks: 3,
    failed_tasks: 0,
    ...overrides,
  }
}

const NOW = 1_000_000 + 60

describe('派活结果通知', () => {
  test('完成 / 失败 / 停止都有中性中文文案，失败带下一步', () => {
    const done = goalNotice(
      goal({ state: 'completed', artifacts: ['reports/weekly.md'] }),
    )
    expect(done?.tone).toBe('ok')
    expect(done?.detail).toContain('已完成')
    expect(done?.title).toContain('产出')

    const failed = goalNotice(
      goal({ state: 'failed', failed_tasks: 2, error: 'RuntimeError: boom' }),
    )
    expect(failed?.tone).toBe('danger')
    expect(failed?.detail).toContain('没有完成')
    expect(failed?.detail).toContain('2 项没有成功')
    expect(failed?.detail).toContain('查看原因并重试')
    expect(failed?.detail).not.toMatch(/RuntimeError|failed/i)

    const stopped = goalNotice(goal({ state: 'cancelled' }))
    expect(stopped?.tone).toBe('muted')
    expect(stopped?.detail).toContain('已停止')
  })

  test('完成但没有写出文件时不能报绿色成功', () => {
    const empty = goalNotice(goal({ state: 'completed', artifacts: [] }))
    expect(empty?.tone).toBe('warn')
    expect(empty?.title).toContain('没有产出')
    expect(empty?.detail).toContain('没有写出任何文件')
  })

  test('running 不产生通知（避免轮询期间反复弹）', () => {
    expect(goalNotice(goal({ state: 'running' }))).toBeNull()
  })

  test('整体完成但有子任务失败时降级为警示，不谎报全部成功', () => {
    const partial = goalNotice(goal({ state: 'completed', failed_tasks: 2 }))
    expect(partial?.tone).toBe('warn')
    expect(partial?.detail).toContain('2 项没有成功')
  })

  test('中断与失败同样醒目', () => {
    const interrupted = goalNotice(goal({ state: 'interrupted' }))
    expect(interrupted?.tone).toBe('danger')
  })

  test('只报最近 24 小时，按时间倒序，最多 5 条', () => {
    const goals: GoalSummary[] = []
    for (let i = 0; i < 8; i += 1) {
      goals.push(
        goal({
          goal_id: `g${i}`,
          state: i % 2 === 0 ? 'failed' : 'completed',
          created_at: NOW - 3600 * i,
        }),
      )
    }
    goals.push(goal({ goal_id: 'old', state: 'failed', created_at: NOW - 3 * 86400 }))

    const notices = deriveGoalNotices(goals, NOW)
    expect(notices).toHaveLength(5)
    expect(notices.some((item) => item.id.includes('old'))).toBe(false)
    expect(notices[0].id).toContain('g0')
  })

  test('同一目标同一终态只出现一次', () => {
    const duplicated = [goal({ state: 'failed' }), goal({ state: 'failed' })]
    expect(deriveGoalNotices(duplicated, NOW)).toHaveLength(1)
  })
})
