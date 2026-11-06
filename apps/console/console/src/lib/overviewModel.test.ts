/**
 * 总览页三条"卡片名必须说真话"的规则。
 *
 * 对应截图上真实出现过的三类事故：
 * - 卡片叫"失败沙箱"，但每一条都是 success；
 * - 图例 4 类、折线只有 1 条；
 * - 近 14 天几乎没事件，却画出一条贴地线。
 */

import { describe, expect, test } from 'vitest'
import {
  partitionProviders,
  providerSummary,
  recentRunTitle,
  trendView,
} from './overviewModel'

describe('最近运行：标题与数据必须一致', () => {
  test('全部成功时，标题不得出现"失败"二字', () => {
    const title = recentRunTitle([{ outcome: 'success' }, { outcome: 'ok' }])
    expect(title).toBe('最近运行')
    expect(title).not.toContain('失败')
  })

  test('存在失败时，标题必须把失败条数说出来', () => {
    const title = recentRunTitle([
      { outcome: 'success' },
      { outcome: 'failed' },
      { outcome: 'error' },
    ])
    expect(title).toBe('最近运行（含 2 条失败）')
  })

  test('空数据不暗示失败', () => {
    expect(recentRunTitle([])).toBe('最近运行')
  })
})

describe('事件趋势：稀疏数据走空状态', () => {
  const days = (values: number[]) =>
    values.map((total, index) => ({
      date: `2026-11-${String(index + 1).padStart(2, '0')}`,
      total,
      allowed: Math.max(0, total - 1),
      denied: Math.min(1, total),
    }))

  test('14 天只有 5 条 → 不画贴地线，给出"当前无异常 + 下一步"', () => {
    const view = trendView(days([5, ...Array(13).fill(0)]))
    expect(view.sparse).toBe(true)
    expect(view.emptyTitle).toBe('当前无异常')
    expect(view.emptyHint).toContain('下一步')
    expect(view.total).toBe(5)
  })

  test('事件只在一天出现也算稀疏（无法形成趋势）', () => {
    const view = trendView(days([12, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]))
    expect(view.sparse).toBe(true)
  })

  test('14 天稳定有量 → 允许画线，且图例与序列来自同一份数组', () => {
    const view = trendView(days([3, 4, 2, 5, 3, 6, 4, 3, 5, 2, 4, 3, 6, 4]))
    expect(view.sparse).toBe(false)
    expect(view.series).toHaveLength(3)
    expect(view.series.map((series) => series.label)).toEqual(['全部事件', '放行', '拒绝'])
    for (const series of view.series) {
      expect(series.values).toHaveLength(view.labels.length)
    }
  })

  test('完全空数据：文案必须给出下一步，而不是让人自己判断', () => {
    const view = trendView(days(Array(14).fill(0)))
    expect(view.sparse).toBe(true)
    expect(view.emptyHint).toContain('下一步')
  })
})

describe('服务商清单：已配置优先、未配置折叠', () => {
  const items = [
    { type: 'ollama', label: '本地 Ollama', configured: true },
    { type: 'openai', label: 'OpenAI', configured: true },
    { type: 'anthropic', label: 'Anthropic', configured: false },
    { type: 'deepseek', label: 'DeepSeek', configured: false },
  ]

  test('当前生效排第一，其余已配置在前，未配置归入折叠区', () => {
    const sections = partitionProviders(items, 'ollama')
    expect(sections.active?.type).toBe('ollama')
    expect(sections.configured.map((item) => item.type)).toEqual(['openai'])
    expect(sections.unconfigured.map((item) => item.type)).toEqual(['anthropic', 'deepseek'])
  })

  test('汇总句不出现"provider"，并写明未配置数量', () => {
    const sections = partitionProviders(items, 'ollama')
    const summary = providerSummary(sections)
    expect(summary).not.toContain('provider')
    expect(summary).toContain('未配置 2 家')
  })
})
