/**
 * 三种终态必须在界面上分得开：有产出 / 无产出 / 未完成。
 *
 * 这里断言的是渲染层真正用的数据（标签、色调、下一步提示），
 * 不是后端状态码：后端仍返回 `completed`，但界面不能都涂成绿色。
 */

import { describe, expect, test } from 'vitest'
import { goalVerdict } from './goalVerdict'

describe('目标终态展示', () => {
  test('完成且有产物：绿色，并说明产物数量', () => {
    const verdict = goalVerdict({
      state: 'completed',
      artifacts: ['reports/weekly.md', 'site/index.html'],
    })
    expect(verdict.kind).toBe('delivered')
    expect(verdict.label).toBe('已完成并产出')
    expect(verdict.tone).toBe('ok')
    expect(verdict.hint).toContain('2 个文件')
  })

  test('完成但没有产物：黄色，不显示成绿色成功', () => {
    const verdict = goalVerdict({ state: 'completed', artifacts: [] })
    expect(verdict.kind).toBe('empty')
    expect(verdict.label).toBe('已完成但无产出')
    expect(verdict.tone).toBe('warning')
    expect(verdict.hint).toContain('没有写出任何文件')
    expect(verdict.retryable).toBe(true)
  })

  test('未完成：红色，并给出重试 / 改需求的下一步', () => {
    const verdict = goalVerdict({ state: 'failed', failed_tasks: 2 })
    expect(verdict.kind).toBe('failed')
    expect(verdict.label).toBe('未完成')
    expect(verdict.tone).toBe('danger')
    expect(verdict.hint).toContain('重试')
    expect(verdict.hint).toContain('补充需求')
    expect(verdict.retryable).toBe(true)
  })

  test('三种终态的色调互不相同', () => {
    const tones = [
      goalVerdict({ state: 'completed', artifacts: ['a.md'] }).tone,
      goalVerdict({ state: 'completed', artifacts: [] }).tone,
      goalVerdict({ state: 'failed' }).tone,
    ]
    expect(new Set(tones).size).toBe(3)
  })

  test('进行中不冒充终态', () => {
    const verdict = goalVerdict({ state: 'running' })
    expect(verdict.label).toBe('进行中')
    expect(verdict.retryable).toBe(false)
  })
})
