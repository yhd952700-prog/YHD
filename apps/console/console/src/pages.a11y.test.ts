/**
 * 五页可访问性契约（我的 Key / 知识库 / 运维 / 设置 / 目标与任务）。
 *
 * 读源码而不是渲染：这些断言盯的是"读屏与键盘能不能用"的固定结构
 * （role / 焦点转移 / Esc 关闭），jsdom 既不算焦点顺序也不跑 CSS，
 * 用字符串把结构钉死更直接、也更抗重构噪音。
 */

import { readFileSync } from 'node:fs'
import { describe, expect, test } from 'vitest'

const read = (name: string) =>
  readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n')

const MY_KEYS = read('./pages/MyKeys.tsx')
const KNOWLEDGE = read('./pages/Knowledge.tsx')
const OPERATIONS = read('./pages/Operations.tsx')
const GOALS = read('./pages/Goals.tsx')
const MODEL_MANAGER = read('./components/ModelManager.tsx')

describe('我的 Key', () => {
  test('失败提示用 role="alert"，成功才用 status', () => {
    expect(MY_KEYS).toMatch(/role=\{notice\.tone === 'ok' \? 'status' : 'alert'\}/)
  })

  test('没有纯图标无名称按钮', () => {
    expect(MY_KEYS).not.toContain('os-icon-btn')
  })
})

describe('知识库', () => {
  test('删除确认是 alertdialog，且错误单独 role="alert"', () => {
    expect(KNOWLEDGE).toContain('role="alertdialog"')
    expect(KNOWLEDGE).toMatch(/className="os-kb-error" role="alert"/)
  })

  test('确认框打开时焦点进入取消按钮，Esc 可关闭', () => {
    expect(KNOWLEDGE).toContain('deleteCancelRef.current?.focus()')
    expect(KNOWLEDGE).toContain("event.key === 'Escape'")
    expect(KNOWLEDGE).toContain('document.removeEventListener(\'keydown\', onKeyDown)')
  })

  test('关闭确认框（取消 / Esc）后焦点回到触发它的删除按钮', () => {
    expect(KNOWLEDGE).toContain('deleteTriggerRef.current = event.currentTarget')
    expect(KNOWLEDGE).toContain('deleteTriggerRef.current?.focus()')
  })

  test('分块预览抽屉打开时焦点进入关闭按钮，关闭后回到原处', () => {
    expect(KNOWLEDGE).toContain('previewCloseRef.current?.focus()')
    expect(KNOWLEDGE).toContain('previewTriggerRef.current?.focus()')
  })

  test('没有纯图标无名称按钮', () => {
    expect(KNOWLEDGE).not.toContain('os-icon-btn')
  })
})

describe('运维', () => {
  test('降级告警用 role="alert"', () => {
    const index = OPERATIONS.indexOf("data.status === 'degraded'")
    expect(index).toBeGreaterThanOrEqual(0)
    const block = OPERATIONS.slice(index, index + 300)
    expect(block).toContain('role="alert"')
  })

  test('没有纯图标无名称按钮', () => {
    expect(OPERATIONS).not.toContain('os-icon-btn')
  })
})

describe('设置（模型管理）', () => {
  test('模型按钮保留按钮语义，不被 role="listitem" 覆盖', () => {
    expect(MODEL_MANAGER).not.toContain('role="listitem"')
    expect(MODEL_MANAGER).toContain('role="group" aria-label="可用模型"')
  })

  test('保存失败用 role="alert"，成功才用 status', () => {
    expect(MODEL_MANAGER).toMatch(/className="os-alert" role="alert"/)
    expect(MODEL_MANAGER).toMatch(/className="os-note" role="status"/)
  })
})

describe('目标与任务', () => {
  test('目标名是可聚焦的按钮，键盘能打开详情', () => {
    expect(GOALS).toContain('className="os-link os-goal-open"')
    const index = GOALS.indexOf('os-goal-open')
    const block = GOALS.slice(index, index + 320)
    expect(block).toContain('event.stopPropagation()')
    expect(block).toContain('void openGoal(goal.goal_id)')
  })

  test('详情与产物读取失败都用 role="alert"', () => {
    expect(GOALS).toMatch(/className="os-error" role="alert">\s*\{detailError\}/)
    expect(GOALS).toMatch(/className="os-error" role="alert">\s*\{artifactError\}/)
  })

  test('没有纯图标无名称按钮', () => {
    expect(GOALS).not.toContain('os-icon-btn')
  })
})
