/**
 * 回归测试：`lib/format.ts` 里的三个展示函数必须是**总函数**。
 *
 * 为什么这是一件独立的事（而不是 `chartUtils` 的重复）
 * --------------------------------------------------
 * 全仓的展示函数此前各有各的守卫策略，强度不一致：
 *
 *   relativeTime   —— 有 `if (!timestamp)`，能挡住 0/undefined/NaN
 *   humanDuration  —— 有 `!Number.isFinite()`，但**只对 number 有效**
 *   formatCompact  —— 没有任何守卫 ⇒ 崩页（已在 chartUtils.test.ts 锁定）
 *
 * 于是同一个「后端没发这个字段」的现实，在三个函数里分别表现为「正常」「显示 —」
 * 和「整页白屏」。这正是需要一次性钉死的不变量：**展示层永不抛**。
 */

import { describe, expect, test } from 'vitest'
import {
  eventLabel,
  humanDuration,
  outcomeLabel,
  outcomeTone,
  relativeTime,
  safeArray,
  safeText,
} from './format'

/** 后端字段缺失 / 类型不符时的真实取值范围。 */
const HOSTILE: unknown[] = [
  undefined,
  null,
  NaN,
  Number.POSITIVE_INFINITY,
  Number.NEGATIVE_INFINITY,
  -1,
  0,
  '',
  'not a timestamp',
  {},
  [],
]

describe('relativeTime 是总函数', () => {
  test('任何输入都不抛', () => {
    for (const value of HOSTILE) {
      expect(() => relativeTime(value as number)).not.toThrow()
    }
  })

  test('缺失时间如实说「未知时间」，不显示 1970 或空白', () => {
    // 显示成一个具体时间（哪怕是 1970）比显示「未知」危险得多：
    // 它会让人以为这是一条很老的事件，而不是一个缺失的字段。
    expect(relativeTime(undefined as unknown as number)).toBe('未知时间')
    expect(relativeTime(0)).toBe('未知时间')
    expect(relativeTime(NaN)).toBe('未知时间')
  })

  test('正常时间仍然给出相对描述', () => {
    const now = Date.now() / 1000
    expect(relativeTime(now - 5)).toBe('刚刚')
    expect(relativeTime(now - 300)).toContain('分钟前')
    expect(relativeTime(now - 7200)).toContain('小时前')
  })

  test('未来时间戳显示「刚刚」而不是负数分钟', () => {
    // 时钟漂移或写入端时钟超前都会产生未来时间戳。
    // 负数会显示成「-3 分钟前」，那是一个会被当成数据错误来追查的假信号。
    expect(relativeTime(Date.now() / 1000 + 600)).toBe('刚刚')
  })
})

describe('humanDuration 是总函数', () => {
  test('任何输入都不抛', () => {
    for (const value of HOSTILE) {
      expect(() => humanDuration(value as number)).not.toThrow()
    }
  })

  test('缺失时长显示「—」，不是「0 秒」', () => {
    expect(humanDuration(undefined as unknown as number)).toBe('—')
    expect(humanDuration(NaN)).toBe('—')
    expect(humanDuration(Number.POSITIVE_INFINITY)).toBe('—')
    // 负数时长不存在，显示「—」而不是「-5 秒」
    expect(humanDuration(-5)).toBe('—')
  })

  test('真实的 0 秒仍然是 0 秒', () => {
    // 与 formatCompact 同一条不变量：缺失 ≠ 零。
    expect(humanDuration(0)).toBe('0 秒')
  })

  test('正常时长换算不变', () => {
    expect(humanDuration(45)).toBe('45 秒')
    expect(humanDuration(90)).toBe('1 分 30 秒')
    expect(humanDuration(3700)).toBe('1 小时 1 分')
    expect(humanDuration(90_000)).toBe('1 天 1 小时')
  })
})

describe('事件与结果标签', () => {
  test('未知类型原样显示，绝不显示 undefined', () => {
    // 旧实现 `EVENT_LABELS[type] ?? type`：type 为 undefined 时返回 undefined，
    // React 渲染 undefined 会静默变成空白 —— 一个没人看得出异常的字段缺失。
    expect(eventLabel(undefined as unknown as string)).toBe('未知')
    expect(eventLabel('')).toBe('未知')
    expect(outcomeLabel(undefined as unknown as string)).toBe('未知')
  })

  test('已知类型照旧翻译', () => {
    expect(eventLabel('access_denied')).toBe('访问拒绝')
    expect(outcomeLabel('allow')).toBe('通过')
    expect(outcomeTone('denied')).toBe('danger')
    expect(outcomeTone('allow')).toBe('ok')
  })

  test('未知 outcome 的色调是 muted，而不是崩溃', () => {
    for (const value of HOSTILE) {
      expect(() => outcomeTone(value as string)).not.toThrow()
      expect(() => outcomeLabel(value as string)).not.toThrow()
    }
  })
})

/**
 * safeText / safeArray —— 契约落差兜底。
 *
 * 后端契约有时声明非空（如 `ActivityItem.principal: string`、
 * `AppSummary.capabilities: string[]`），但运行时真会发 null / 缺字段。
 * 直接 `value.toLowerCase()` 或 `[...value]` 在这些值上会抛 `TypeError` 整页白屏。
 * 这两个总函数把「缺失」降级成「空串 / 空数组」，让页面不崩。
 */
describe('safeText 是契约落差兜底', () => {
  test('null / undefined 返回空串（不是崩溃，也不是 "null" 字面量）', () => {
    expect(safeText(null)).toBe('')
    expect(safeText(undefined)).toBe('')
  })

  test('字符串原样返回，数字 / 布尔转成字符串', () => {
    expect(safeText('hello')).toBe('hello')
    expect(safeText(123)).toBe('123')
    expect(safeText(true)).toBe('true')
  })

  test('对象 / 数组等非标量返回空串，绝不抛', () => {
    expect(safeText({})).toBe('')
    expect(safeText([])).toBe('')
    expect(() => safeText({ a: 1 })).not.toThrow()
  })

  test('safeText 后接 toLowerCase 永不抛', () => {
    // 这正是 Overview / 各页面搜索过滤里的调用形态。
    for (const value of [null, undefined, 123, 'AbC', '']) {
      expect(() => safeText(value).toLowerCase().includes('x')).not.toThrow()
    }
  })
})

describe('safeArray 是契约落差兜底', () => {
  test('null / undefined / 非数组一律返回空数组', () => {
    expect(safeArray(undefined)).toEqual([])
    expect(safeArray(null)).toEqual([])
    expect(safeArray('not-array')).toEqual([])
    expect(safeArray(42)).toEqual([])
  })

  test('真数组原样返回', () => {
    expect(safeArray(['a', 'b'])).toEqual(['a', 'b'])
  })

  test('safeArray 消除 [..x] 与 x.length 的崩溃路径', () => {
    // 这正是 Apps.tsx 里 `app.capabilities` 缺失时会崩的两个点。
    expect(() => [...safeArray<string>(undefined)]).not.toThrow()
    expect(safeArray(undefined).length).toBe(0)
    expect(safeArray(undefined).join(', ')).toBe('')
  })
})
