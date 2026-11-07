/**
 * 能力层 / 阶段标签的中性化映射。
 *
 * 名册接口（`/v1/dashboard/roster`）直接下发注册表里的展示名，其中
 * `domains[].label` / `bands[].label` / `band_label` 带着实现层术语
 * （「认知内核」「执行内核」…「内核闭环」）。这些词在总览页会被当成功能分组
 * 去读，对使用者没有意义。因此统一在展示层换成中性说法，并给未登记的标签
 * 兜底，保证界面上不会出现「内核」。
 *
 * 注意：前端只拿得到标签字符串（不是 key），所以这里按文案匹配，
 * 不依赖后端的 domain / band 枚举。
 */

/** 注册表已知取值 → 中性说法。 */
const NEUTRAL_LABELS: Record<string, string> = {
  // DOMAIN_LABELS
  认知内核: '能力',
  执行内核: '任务执行',
  治理内核: '规则',
  信任内核: '校验',
  // BAND_LABELS
  内核闭环: '运行闭环',
  组织与任务: '协作与任务',
  世界与治理: '外部协作',
  验证与进化: '验证与改进',
}

/**
 * 服务端下发的能力层 / 阶段标签 → 界面上可直接展示的中性文案。
 *
 * - 空值（`undefined` / 空串 / 纯空白）→「未分组」
 * - 已登记标签 → 固定中性说法
 * - 未登记但含「内核」→ 把「内核」替换成「能力」（「网络内核」→「网络能力」）
 * - 替换后只剩「能力」或为空 →「其他能力」
 * - 其余原样返回（只去掉首尾空白）
 */
export function neutralLayerLabel(label: string | undefined): string {
  const raw = (label ?? '').trim()
  if (!raw) return '未分组'

  const known = NEUTRAL_LABELS[raw]
  if (known) return known

  if (raw.includes('内核')) {
    const replaced = raw.replace(/内核/g, '能力').trim()
    // 「内核」单独成词时替换结果没有信息量，归入兜底分组。
    if (!replaced || replaced === '能力') return '其他能力'
    return replaced
  }

  return raw
}
