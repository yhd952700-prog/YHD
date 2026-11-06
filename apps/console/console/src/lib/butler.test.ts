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
  mergeTaskSnapshots,
  parseTaskTime,
  riskTone,
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
    // 进程被杀后重启的收敛状态：不是 running，也不是普通失败
    const interrupted = taskPhaseInfo(task({ status: 'interrupted' }))
    expect(interrupted.phase).toBe('interrupted')
    expect(interrupted.label).toBe('已中断')
    expect(interrupted.tone).toBe('danger')
    expect(interrupted.note).toContain('可单独重试')
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

  test('风险级别只按后端原值给色调，未知保持中性', () => {
    expect(riskTone('HIGH')).toBe('danger')
    expect(riskTone('CRITICAL')).toBe('danger')
    expect(riskTone('MEDIUM')).toBe('warn')
    expect(riskTone('LOW')).toBe('ok')
    expect(riskTone('whatever')).toBe('muted')
  })
})

describe('增量任务快照', () => {
  test('按 id 合并且缺字段保留旧值，缺任务不清空', () => {
    const original = [
      task({ id: 't1', name: '查资料', status: 'pending', assigned_agent: 'agent-1' }),
      task({ id: 't2', name: '写报告', status: 'pending' }),
    ]

    // 快照只包含 t1 的变化，且只有 id / status。
    const partial = mergeTaskSnapshots(original, [
      { id: 't1', status: 'running' } as GoalTask,
    ])
    expect(partial).toHaveLength(2)
    expect(partial[0].status).toBe('running')
    expect(partial[0].name).toBe('查资料')
    expect(partial[0].assigned_agent).toBe('agent-1')
    expect(partial[1].name).toBe('写报告')
  })

  test('新任务追加；值为 null 是明确清空', () => {
    const merged = mergeTaskSnapshots(
      [task({ id: 't1', result: 'ok' })],
      [
        task({ id: 't2', name: '归档', status: 'ready' }),
        { id: 't1', result: null } as GoalTask,
      ],
    )
    expect(merged.map((item) => item.id)).toEqual(['t1', 't2'])
    expect(merged[0].result).toBeNull()
    expect(merged[1].name).toBe('归档')
  })

  test('不修改入参', () => {
    const original = [task({ id: 't1', status: 'pending' })]
    const merged = mergeTaskSnapshots(original, [{ id: 't1', status: 'completed' } as GoalTask])
    expect(original[0].status).toBe('pending')
    expect(merged[0].status).toBe('completed')
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

  test('审计的 blocked_at 是数字，也按 Unix 秒解析', () => {
    expect(parseTaskTime(1780000000.5)).toBeCloseTo(1780000000.5)
    expect(parseTaskTime(1780000000123)).toBeCloseTo(1780000000.123)
    expect(parseTaskTime(0)).toBeNull()
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

  test('已中断计为已终结且需要处理，不会被当成仍在进行', () => {
    const stats = taskStats([
      task({ id: 'a', status: 'interrupted' }),
      task({ id: 'b', status: 'running' }),
      task({ id: 'c', status: 'pending' }),
    ])
    expect(stats.finished).toBe(1)
    expect(stats.failed).toBe(1)
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
      task({ id: 'c', status: 'interrupted', error: '进程中断时该任务仍在执行' }),
    ]
    expect(approvalTasks(tasks).map((t) => t.id)).toEqual(['a'])
    // 中断任务结果未知，但同样要出现在"需要处理"里，才有点重试的入口
    expect(failedTasks(tasks).map((t) => t.id)).toEqual(['b', 'c'])
  })

  test('目标状态中文化与活跃判定', () => {
    expect(goalStateLabel('completed')).toBe('成功')
    expect(goalStateLabel('cancelled')).toBe('已中止')
    expect(goalStateLabel('interrupted')).toBe('已中断（进程重启）')
    expect(goalStateTone('failed')).toBe('danger')
    expect(goalStateTone('interrupted')).toBe('danger')
    expect(isGoalActive('running')).toBe(true)
    expect(isGoalActive('interrupted')).toBe(false)
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
