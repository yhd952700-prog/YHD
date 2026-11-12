/**
 * 侧栏入口徽标 —— 「数 → 徽标值」的纯判断（简约版第三组）。
 *
 * 数据口径（侦察已核实，不许换口径、不许混着数）：
 *   * 待批数：GET /v1/workflow/summary 的 pending_approvals（精确、无 200 上限）。
 *     不要用 /v1/approvals/summary.pending（超 200 少计），更不要把策略审批
 *     队列（/v1/policy/approvals/pending）加进来 —— 那是另一条队列，
 *     不在待办审批页里。
 *   * 在跑任务数：GET /v1/tasks/ledger/status 的 by_status.running（精确；
 *     后端由 SQL GROUP BY 生成，缺键即该状态 0 条，见 TaskCenter 的同口径注释）。
 *
 * 诚实规则（这里只管"数→徽标"，取数层在 App.tsx）：
 *   * 还在加载、请求失败、无权（403）时不显示徽标 —— 调用方传 null；
 *   * 数为 0 时隐藏徽标（0 不值得打扰人）；数 > 0 才显示。
 * 语气：待批用 danger（要人马上处理），任务中心用 accent（进行中是常态）。
 */

import { parseLedgerStatus } from './taskLedger'

export type NavBadgeTone = 'danger' | 'accent'

export interface NavBadge {
  value: number
  tone: NavBadgeTone
}

/**
 * 单个计数 → 徽标：只有有限且 > 0 的数才成徽标；0、null、undefined、
 * NaN 与负数一律不显示（绝不显示 0 占位）。
 */
export function badgeForCount(
  value: number | null | undefined,
  tone: NavBadgeTone,
): NavBadge | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return undefined
  return { value, tone }
}

/** 两处徽标一次算完；key 是导航 key（workflow-approvals / task-center）。 */
export function buildNavBadges(input: {
  pendingApprovals: number | null | undefined
  runningTasks: number | null | undefined
}): Record<string, NavBadge> {
  const badges: Record<string, NavBadge> = {}
  const pending = badgeForCount(input.pendingApprovals, 'danger')
  if (pending) badges['workflow-approvals'] = pending
  const running = badgeForCount(input.runningTasks, 'accent')
  if (running) badges['task-center'] = running
  return badges
}

/**
 * 从 /v1/workflow/summary 的原始响应里取待批数。
 *
 * `error` 非空（加载失败、403 无权等）时返回 null —— 调用方不要拿旧数据
 * 顶替；形状不对（缺 pending_approvals 或不是数字）同样返回 null。
 * 0 是真数（没有待批），照常返回 0，由 buildNavBadges 决定不显示。
 */
export function pendingApprovalsFrom(data: unknown, error: string | null): number | null {
  if (error !== null) return null
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return null
  const value = (data as Record<string, unknown>).pending_approvals
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * 从 /v1/tasks/ledger/status 的原始响应里取在跑任务数。
 *
 * 失败/无权时返回 null；形状校验走 parseLedgerStatus；by_status 缺 running
 * 键即真 0（SQL GROUP BY 口径，见 TaskCenter）。
 */
export function runningTasksFromLedger(data: unknown, error: string | null): number | null {
  if (error !== null) return null
  const parsed = parseLedgerStatus(data)
  if (!parsed) return null
  return parsed.ledger.by_status.running ?? 0
}
