/**
 * 图表配色与数值格式 —— 从 `components/charts.tsx` 拆出来的非组件部分。
 *
 * 这些常量与函数在页面里也要用（图例的色点、卡片的副标题），若留在组件模块里，
 * 每个引用它的页面都要从"组件文件"导入一个函数，Fast Refresh 随之失效。
 */

export const CHART_COLORS = [
  '#4d8dff',
  '#9b6bff',
  '#2ee6a8',
  '#ffb74d',
  '#ff5d6c',
  '#41c7e0',
  '#f06292',
  '#8bc34a',
]

/**
 * 按序号取色，超出长度循环。
 *
 * 非有限索引退回到 0 号色：返回 `undefined` 会渲染成 `background: undefined`
 * （CSS 直接忽略），圆点变成透明 —— 一个「看起来能跑但信息丢了」的静默缺陷。
 */
export function colorAt(index: number): string {
  if (!Number.isInteger(index)) return CHART_COLORS[0]
  return CHART_COLORS[((index % CHART_COLORS.length) + CHART_COLORS.length) % CHART_COLORS.length]
}

/**
 * 大数字压缩为 k / M，避免图例把卡片撑破。
 *
 * **必须是总函数（对任何输入都不抛）。** 这不是风格偏好，是一条硬约束：
 *
 *   本函数曾因渲染一个后端从不发送的字段（`totals.declared_test_cases`）而收到
 *   `undefined`。旧实现在所有 `>=` 比较里都因 `Math.abs(undefined) === NaN` 落空，
 *   最后走到 `value.toFixed(1)` —— 抛 `TypeError`。后果不是「少一个数字」，而是
 *   **整页白屏**：一个人类主权主体在最需要观测的时刻看不到任何界面。
 *
 * 所以两条规则：
 *   1. 任何非有限输入（undefined / null / NaN / ±Infinity / 非数值）一律显示「—」。
 *      **不能显示 0** —— 那等于宣称「后端报告了 0」，而真相是「后端没发这个字段」。
 *   2. 真实的 0 必须仍然显示 0。缺失与零是两件事，混起来就是一个静默的谎。
 */
export function formatCompact(value: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return `${(value / 1000).toFixed(0)}k`
  if (abs >= 1000) return `${(value / 1000).toFixed(1)}k`
  if (!Number.isInteger(value)) return value.toFixed(1)
  return String(value)
}
