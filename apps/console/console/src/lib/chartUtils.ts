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

/** 按序号取色，超出长度循环。 */
export function colorAt(index: number): string {
  return CHART_COLORS[index % CHART_COLORS.length]
}

/** 大数字压缩为 k / M，避免图例把卡片撑破。 */
export function formatCompact(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return `${(value / 1000).toFixed(0)}k`
  if (abs >= 1000) return `${(value / 1000).toFixed(1)}k`
  if (!Number.isInteger(value)) return value.toFixed(1)
  return String(value)
}
