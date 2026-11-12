/**
 * 总览页的展示规则 —— 抽成纯函数，便于钉死三条"卡片名与数据必须自洽"的约定。
 *
 * 三条规则都来自真实的界面事故：
 * 1. 卡片叫「失败沙箱」，但里面每一条都是 success：名字在说假话。
 *    现在标题由数据算出来，数据里没有失败就不许出现「失败」二字。
 * 2. 图例四类、线只有一条：图例与序列必须来自同一份数组。
 *    这里给出唯一的序列定义，页面只负责渲染。
 * 3. 近 14 天只有个位数事件时，画一条贴地线会让人以为"系统很安静"。
 *    稀疏数据一律走明确的空状态，并给出下一步建议。
 */

import { eventLabel, outcomeLabel } from './format'

export interface TrendPoint {
  date: string
  total: number
  allowed: number
  denied: number
}

export interface TrendSeries {
  key: string
  label: string
  color: string
  values: number[]
}

export interface TrendView {
  sparse: boolean
  total: number
  activeDays: number
  labels: string[]
  series: TrendSeries[]
  emptyTitle: string
  emptyHint: string
}

/** 14 天内少于这个条数就不足以构成趋势线。 */
export const SPARSE_EVENT_THRESHOLD = 10

/** 图例与折线共用这一份定义 —— 二者不可能再对不上。 */
const TREND_COLORS = {
  total: '#4d8dff',
  allowed: '#2ee6a8',
  denied: '#ff5d6c',
} as const

export function trendView(points: TrendPoint[]): TrendView {
  const safe = Array.isArray(points) ? points : []
  const total = safe.reduce((sum, point) => sum + (Number(point.total) || 0), 0)
  const activeDays = safe.filter((point) => (Number(point.total) || 0) > 0).length
  const sparse = total < SPARSE_EVENT_THRESHOLD || activeDays <= 1

  return {
    sparse,
    total,
    activeDays,
    labels: safe.map((point) => String(point.date ?? '').slice(5)),
    series: [
      {
        key: 'total',
        label: '全部事件',
        color: TREND_COLORS.total,
        values: safe.map((point) => Number(point.total) || 0),
      },
      {
        key: 'allowed',
        label: '放行',
        color: TREND_COLORS.allowed,
        values: safe.map((point) => Number(point.allowed) || 0),
      },
      {
        key: 'denied',
        label: '拒绝',
        color: TREND_COLORS.denied,
        values: safe.map((point) => Number(point.denied) || 0),
      },
    ],
    emptyTitle: '当前无异常',
    emptyHint:
      total === 0
        ? '近 14 天没有审计事件。下一步：到「自由对话」问一句，或到「系统设置 → 模型管理」确认服务是否连通。'
        : `近 14 天共 ${total} 条事件，不足以形成趋势。下一步：再观察一天，或到「事件流」查看明细。`,
  }
}

export interface RunLike {
  outcome?: unknown
}

const FAIL_OUTCOMES = new Set(['error', 'failure', 'failed', 'deny', 'denied', 'interrupted'])

export function isFailedOutcome(outcome: unknown): boolean {
  return typeof outcome === 'string' && FAIL_OUTCOMES.has(outcome.toLowerCase())
}

/**
 * 「最近运行」卡片的标题。
 *
 * 全部成功时标题只能说「最近运行」；只有真的存在失败，标题才允许出现「失败」。
 * 反过来也成立：数据里有失败时，标题必须把条数说出来，不能继续若无其事。
 */
export function recentRunTitle(items: RunLike[]): string {
  const failed = (Array.isArray(items) ? items : []).filter((item) =>
    isFailedOutcome(item?.outcome),
  ).length
  if (failed === 0) return '最近运行'
  return `最近运行（含 ${failed} 条失败）`
}

/** 运行条目的一句可读描述：任务名 + 结果。内部标识在这里被翻译掉。 */
export function runLine(item: { type?: unknown; outcome?: unknown; principal?: unknown }): string {
  const name = eventLabel(typeof item?.type === 'string' ? item.type : '')
  const result = outcomeLabel(typeof item?.outcome === 'string' ? item.outcome : '')
  return `${name} · ${result}`
}

export interface ProviderLike {
  type: string
  label: string
  configured: boolean
  is_mock?: boolean
  registered?: boolean
  required_env?: string[]
}

export interface ProviderSections<T extends ProviderLike> {
  active: T | null
  configured: T[]
  unconfigured: T[]
  total: number
}

/**
 * 服务商分区：当前生效 → 已配置 → 未配置。
 * 未配置项默认折叠，避免一屏里全是"未配置"把真正重要的两条挤下去。
 */
export function partitionProviders<T extends ProviderLike>(
  items: T[],
  activeType: string | undefined,
): ProviderSections<T> {
  const safe = Array.isArray(items) ? items : []
  const active = safe.find((item) => item.type === activeType) ?? null
  const configured = safe
    .filter((item) => item.configured && item.type !== activeType)
    .sort((a, b) => a.label.localeCompare(b.label, 'zh-Hans-CN'))
  const unconfigured = safe
    .filter((item) => !item.configured)
    .sort((a, b) => a.label.localeCompare(b.label, 'zh-Hans-CN'))
  return { active, configured, unconfigured, total: safe.length }
}

export function providerSummary(sections: ProviderSections<ProviderLike>): string {
  const configuredCount = sections.configured.length + (sections.active?.configured ? 1 : 0)
  if (sections.unconfigured.length === 0) return `已登记 ${sections.total} 家服务商，全部已配置`
  return `已配置 ${configuredCount} 家 · 未配置 ${sections.unconfigured.length} 家`
}
