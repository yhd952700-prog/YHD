/**
 * 回归测试：`formatCompact` 必须是**总函数** —— 任何输入都不抛异常。
 *
 * 缺陷本体
 * --------
 * `Operations.tsx` 曾渲染后端从不发送的字段 `totals.declared_test_cases`。
 * 于是 `formatCompact(undefined)` 被调用，而旧实现是：
 *
 *     if (abs >= 1_000_000) ...      // Math.abs(undefined) === NaN，全部为 false
 *     if (!Number.isInteger(value)) return value.toFixed(1)   // ← 抛 TypeError
 *
 * `undefined.toFixed` 抛 `TypeError`。这不是「少显示一个数字」，而是**整页崩成白屏** ——
 * 人类主权主体在最需要观测的时候反而看不到界面。这正是把格式化函数写成总函数的理由。
 */

import { describe, expect, test } from 'vitest'
import { CHART_COLORS, colorAt, formatCompact } from './chartUtils'

/** 曾经崩掉整页的那一类输入：字段不存在 → undefined / null / NaN。 */
const HOSTILE_NUMBERS: unknown[] = [
  undefined,
  null,
  NaN,
  Number.POSITIVE_INFINITY,
  Number.NEGATIVE_INFINITY,
  '',
  'not a number',
  {},
  [],
  true,
  false,
]

describe('formatCompact 是总函数（永不抛）', () => {
  test('任何非有限输入都不抛异常', () => {
    for (const value of HOSTILE_NUMBERS) {
      expect(() => formatCompact(value as number)).not.toThrow()
    }
  })

  test('缺失值显示「—」，而不是显示空白或编造一个 0', () => {
    // 显示 0 是撒谎：那等于宣称「后端报告了 0 条事件」，而真相是「后端没发这个字段」。
    expect(formatCompact(undefined as unknown as number)).toBe('—')
    expect(formatCompact(null as unknown as number)).toBe('—')
    expect(formatCompact(NaN)).toBe('—')
  })

  test('真实的 0 仍然是 0（不能和「缺失」混为一谈）', () => {
    // 这是本文件最关键的一条：把缺失显示成 0 会制造一个静默的谎。
    expect(formatCompact(0)).toBe('0')
  })

  test('正常数值的压缩格式保持不变', () => {
    expect(formatCompact(999)).toBe('999')
    expect(formatCompact(1000)).toBe('1.0k')
    expect(formatCompact(1500)).toBe('1.5k')
    expect(formatCompact(15_500)).toBe('16k')
    expect(formatCompact(2_500_000)).toBe('2.5M')
    expect(formatCompact(-1500)).toBe('-1.5k')
    expect(formatCompact(1.25)).toBe('1.3')
  })
})

describe('colorAt', () => {
  test('越界索引循环取色而不是返回 undefined', () => {
    // 未定义的颜色会渲染成 `background: undefined`（CSS 忽略它），
    // 于是圆点变成透明 —— 一个「看起来能跑但信息丢了」的静默缺陷。
    expect(colorAt(0)).toBe(CHART_COLORS[0])
    expect(colorAt(CHART_COLORS.length)).toBe(CHART_COLORS[0])
    expect(CHART_COLORS).toContain(colorAt(999))
  })

  test('非有限索引不抛异常', () => {
    for (const value of HOSTILE_NUMBERS) {
      expect(() => colorAt(value as number)).not.toThrow()
    }
  })
})
