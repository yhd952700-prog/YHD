/**
 * 管家派活：错误码 → 用户该做什么。
 *
 * 后端 10 个稳定错误码此前全靠 `ApiError.code` 分支，而 `code` 一直读到的是
 * `detail.code`（后端早已摊平到顶层），于是分支全部落空、统一显示成
 * 「提交失败：…」。这里逐条钉住「码 → 下一步动作」，任何一个分支失效都会红。
 */

import { describe, expect, test } from 'vitest'
import { ApiError } from '../lib/api'
import { goalWorkMessage } from '../lib/goalWork'

const CASES: Array<[code: string, expected: string]> = [
  ['agent_not_found', '重新选择'],
  ['employee_not_found', '重新选择'],
  ['employee_disabled', '启用'],
  ['agent_unavailable', '恢复'],
  ['employee_unavailable', '恢复'],
  ['assign_target_required', '指定一个执行者'],
  ['task_not_retryable', '不是失败态'],
  ['goal_running', '等它结束'],
  ['task_not_found', '刷新目标'],
  ['goal_not_found', '已经不存在'],
]

describe('管家派活的错误指引', () => {
  test.each(CASES)('错误码 %s 命中专属指引', (code, expected) => {
    const text = goalWorkMessage(new ApiError(409, '服务端原文', code), '提交失败')
    expect(text).toContain(expected)
    // 专属指引不能退化成「提交失败：服务端原文」。
    expect(text).not.toContain('提交失败')
  })

  test('未登记的错误码才退回兜底文案（并保留原始说明）', () => {
    const text = goalWorkMessage(new ApiError(500, '服务端原文', 'unknown_code'), '提交失败')
    expect(text).toBe('提交失败：服务端原文')
  })

  test('402 说额度并给出口，403 才说权限（两类拒绝不混）', () => {
    const budget = goalWorkMessage(
      new ApiError(402, '', 'budget_exceeded'),
      '提交失败',
    )
    expect(budget).toContain('超出当前额度')
    expect(budget).toContain('我的 Key')
    expect(budget).toContain('更便宜的模型')
    expect(budget).not.toContain('权限')

    const permission = goalWorkMessage(
      new ApiError(403, '', 'permission_denied'),
      '提交失败',
    )
    expect(permission).toContain('权限不足')
    expect(permission).not.toContain('预算')
  })

  test('非接口错误（例如网络异常对象）走兜底，不崩溃', () => {
    expect(goalWorkMessage(new Error('网络断了'), '提交失败')).toBe('提交失败：网络断了')
    expect(goalWorkMessage('不是错误对象', '提交失败')).toBe(
      '提交失败：操作没有完成，请稍后重试。',
    )
  })
})
