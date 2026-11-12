/**
 * 操作台顶部「今天小结」的文案拼装 —— 纯函数（简约版第三组）。
 *
 * 文案固定为三项，全部有真实来源，缺一不可编：
 *   * 今天完成 X 个 —— lib/enterpriseOverview.ts 的 completedToday()
 *     （取最近已完成任务按本地自然日数）；它返回 exact 标志 —— 全量已完成
 *     ≤ 200 时精确，否则只是下限，此时文案如实写「今天完成至少 X 个」
 *     （另加小字注明仅扫描最近 N 条）。
 *   * 正在跑 N 个 —— 与徽标同一来源：/v1/tasks/ledger/status 的 by_status.running。
 *   * 待批 Y 件 —— 与徽标同一来源：/v1/workflow/summary 的 pending_approvals。
 *
 * 缺口处理（不编数，只说缺了哪一项）：
 *   * 三项全无（还在加载或都取失败）→ 返回 null，组件显示空态
 *     「今日数据暂未取到」；
 *   * 403 导致 Y 拿不到 → 只写 X 和 N 两项，小字注明「待批数未取到」；
 *   * X 或 N 单项未取到时同样只写能写出的项，并在小字里注明缺了哪一项。
 */

import type { CompletedToday } from './enterpriseOverview'

export interface TodaySummaryInput {
  /** completedToday() 的结果；null = 还没回来或取失败。 */
  completed: CompletedToday | null
  /** 在跑任务数（与徽标同一来源）；null = 还没回来或取失败。 */
  running: number | null
  /** 待批数（与徽标同一来源）；null = 还没回来、取失败或无权。 */
  pending: number | null
  /** 待批端点返回 403：Y 不是 0，只是当前账号看不到。 */
  pendingForbidden: boolean
}

export interface TodaySummaryView {
  /** 三项里能写出的项，按「完成、在跑、待批」顺序，用「、」连起来。 */
  items: string[]
  /** 小字说明：非精确、缺完成时间、某项未取到。 */
  notes: string[]
}

/** 拼装小结；三项全无时返回 null（组件据此显示空态，不画假数字）。 */
export function buildTodaySummary(input: TodaySummaryInput): TodaySummaryView | null {
  const items: string[] = []
  const notes: string[] = []

  if (input.completed) {
    const count = input.completed.count
    items.push(
      input.completed.exact ? `今天完成 ${count} 个` : `今天完成至少 ${count} 个`,
    )
    if (!input.completed.exact) {
      notes.push(
        `仅扫描最近 ${input.completed.scanned} 条已完成任务（全量 ${input.completed.total} 条），今日数可能偏低`,
      )
    }
    if (input.completed.missingTime > 0) {
      notes.push(`${input.completed.missingTime} 条已完成任务缺少完成时间，未计入`)
    }
  } else {
    notes.push('今日完成数暂未取到')
  }

  if (typeof input.running === 'number') {
    items.push(`正在跑 ${input.running} 个`)
  } else {
    notes.push('在跑数暂未取到')
  }

  if (typeof input.pending === 'number') {
    items.push(`待批 ${input.pending} 件`)
  } else if (input.pendingForbidden) {
    notes.push('待批数未取到：当前账号无权查看审批汇总')
  } else {
    notes.push('待批数暂未取到')
  }

  if (items.length === 0) return null
  // 全无时不留"缺了哪项"的注脚 —— 空态只有一句话，避免三条未取到叠在一起。
  return { items, notes }
}
