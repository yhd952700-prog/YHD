/**
 * 管家展示语义的回归。重点只有三件事：
 *   1. 「待确认」不能从普通失败里长出来（误判比漏判更危险）；
 *   2. 后端 `str(time.time())` 的耗时必须解析成秒，而不是 Invalid Date；
 *   3. 进度只按已终结任务计数，运行中没有任务时绝不假装 100%。
 */

import { describe, expect, test } from 'vitest'
import {
  approvalTasks,
  buildGoalSummary,
  failedTasks,
  formatElapsed,
  formatTaskDuration,
  goalElapsedSeconds,
  goalStateLabel,
  goalStateTone,
  isGoalActive,
  looksLikeApproval,
  parseTaskTime,
  sortGoalSummaries,
  taskDurationSeconds,
  taskPhaseInfo,
  taskStats,
} from './butler'
import type { GoalTask } from './operator'

function task(overrides: Partial<GoalTask> = {}): GoalTask {
  return {
    id: 't1',
    name: '查资料',
    description: null,
    status: 'completed',
    capability_id: 'research',
    assigned_agent: 'agent-1',
    result: 'ok',
    error: null,
    started_at: '1780000000.1',
    completed_at: '1780000003.6',
    ...overrides,
  }
}

describe('任务状态映射', () => {
  test('待确认只来自策略拦截错误，普通失败保持失败', () => {
    const awaiting = taskPhaseInfo(
      task({ status: 'failed', error: 'policy_approval_required: action needs human approval' }),
    )
    expect(awaiting.label).toBe('待确认')
    expect(awaiting.tone).toBe('warn')

    const plain = taskPhaseInfo(task({ status: 'failed', error: 'connection reset' }))
    expect(plain.label).toBe('失败')
    expect(plain.tone).toBe('danger')
  })

  test('六种后端状态都有中文标签，未知状态原样保留', () => {
    expect(taskPhaseInfo(task({ status: 'pending' })).label).toBe('排队')
    expect(taskPhaseInfo(task({ status: 'ready' })).label).toBe('排队')
    expect(taskPhaseInfo(task({ status: 'running' })).label).toBe('进行中')
    expect(taskPhaseInfo(task({ status: 'completed' })).label).toBe('成功')
    expect(taskPhaseInfo(task({ status: 'failed' })).label).toBe('失败')
    expect(taskPhaseInfo(task({ status: 'skipped' })).label).toBe('已跳过')
    expect(taskPhaseInfo(task({ status: 'what_is_this' })).label).toBe('what_is_this')
  })

  test('completed_unattributed 计为成功（后端列表的既有口径）', () => {
    expect(taskPhaseInfo(task({ status: 'completed_unattributed' })).phase).toBe('succeeded')
  })

  test('审批识别不匹配裸的 approval', () => {
    expect(looksLikeApproval('approval token expired')).toBe(false)
    expect(looksLikeApproval('action requires human approval')).toBe(true)
    expect(looksLikeApproval(null)).toBe(false)
  })
})

describe('耗时', () => {
  test('数字字符串按 Unix 秒解析，毫秒数自动归一', () => {
    expect(parseTaskTime('1780000000.1')).toBeCloseTo(1780000000.1)
    expect(parseTaskTime('1780000000123')).toBeCloseTo(1780000000.123)
    expect(parseTaskTime('2026-11-06T12:00:00Z')).toBeTypeOf('number')
    expect(parseTaskTime('')).toBeNull()
    expect(parseTaskTime('not-a-time')).toBeNull()
  })

  test('已完成按真实起止，进行中按现在，未开始为 null', () => {
    expect(taskDurationSeconds(task())).toBeCloseTo(3.5)
    expect(taskDurationSeconds(task({ status: 'running', completed_at: null }), 1780000010)).toBeCloseTo(9.9)
    expect(taskDurationSeconds(task({ status: 'pending', started_at: null }))).toBeNull()
  })

  test('人话时长与运行计时', () => {
    expect(formatTaskDuration(null)).toBe('—')
    expect(formatTaskDuration(0.4)).toBe('400 毫秒')
    expect(formatTaskDuration(3.5)).toBe('3.5 秒')
    expect(formatTaskDuration(75)).toBe('1 分 15 秒')
    expect(formatElapsed(12)).toBe('00:12')
    expect(formatElapsed(3723)).toBe('1:02:03')
  })
})

describe('进度与汇总', () => {
  test('进度只按已终结任务计数，没有任务就是 0 且 total=0', () => {
    const stats = taskStats([])
    expect(stats.total).toBe(0)
    expect(stats.percent).toBe(0)

    const mixed = taskStats([
      task({ id: 'a', status: 'completed' }),
      task({ id: 'b', status: 'failed' }),
      task({ id: 'c', status: 'running' }),
      task({ id: 'd', status: 'pending' }),
    ])
    expect(mixed.total).toBe(4)
    expect(mixed.finished).toBe(2)
    expect(mixed.percent).toBe(50)
  })

  test('待确认计入已终结（它不会再自动推进）', () => {
    const stats = taskStats([
      task({ status: 'failed', error: 'policy_approval_required' }),
      task({ status: 'pending' }),
    ])
    expect(stats.awaiting).toBe(1)
    expect(stats.finished).toBe(1)
    expect(stats.percent).toBe(50)
  })

  test('总耗时取最早开始与最晚结束', () => {
    expect(goalElapsedSeconds([])).toBeNull()
    expect(
      goalElapsedSeconds([
        task({ id: 'a', started_at: '1780000000', completed_at: '1780000004' }),
        task({ id: 'b', started_at: '1780000002', completed_at: '1780000010' }),
      ]),
    ).toBeCloseTo(10)
  })

  test('汇总用真实计数与评估原文，不编造结论', () => {
    const detail = {
      goal_id: 'g1',
      state: 'failed',
      natural_language: '整理周报',
      scope: 'L1',
      error: '一个任务失败',
      correlation_id: null,
      created_at: 1780000000,
      artifacts: ['reports/weekly.md'],
      evaluation: {
        outcome: 'failed',
        summary: '未达到验收条件',
        replan_required: true,
        replan_triggered: false,
      },
      tasks: [
        task({ id: 'a', status: 'completed' }),
        task({ id: 'b', status: 'failed', error: '磁盘已满' }),
      ],
    }
    const text = buildGoalSummary(detail)
    expect(text).toContain('共 2 项任务')
    expect(text).toContain('成功 1')
    expect(text).toContain('失败 1')
    expect(text).toContain('1 个文件')
    expect(text).toContain('评估：未达到验收条件')
  })

  test('审批 / 失败任务筛选互不串台', () => {
    const tasks = [
      task({ id: 'a', status: 'failed', error: 'policy_approval_required' }),
      task({ id: 'b', status: 'failed', error: 'boom' }),
    ]
    expect(approvalTasks(tasks).map((t) => t.id)).toEqual(['a'])
    expect(failedTasks(tasks).map((t) => t.id)).toEqual(['b'])
  })

  test('目标状态中文化与活跃判定', () => {
    expect(goalStateLabel('completed')).toBe('成功')
    expect(goalStateLabel('cancelled')).toBe('已中止')
    expect(goalStateTone('failed')).toBe('danger')
    expect(isGoalActive('running')).toBe(true)
    expect(isGoalActive('completed')).toBe(false)
    expect(isGoalActive('tomato')).toBe(false)
  })

  test('历史倒序且缺失时间不冒充最新', () => {
    const sorted = sortGoalSummaries([
      { created_at: 1 },
      { created_at: null },
      { created_at: 3 },
    ])
    expect(sorted.map((g) => g.created_at)).toEqual([3, 1, null])
  })
})
