/**
 * 首屏三行状态带的纯逻辑。
 *
 * 三行只有两个数据源：`/v1/goals`（「正在做」与「刚完成」是同一批目标的两种状态）
 * 与 `/v1/policy/approvals/pending`（「等你确认」）。这里只做挑选、排序与文案，
 * 不发请求 —— 组件用**一个** 30 秒轮询同时驱动两个来源，避免同一份数据被
 * 三个区块各取一遍。
 *
 * 三条取舍，写在代码里方便以后对照：
 * 1. 「正在做」把有失败步骤的目标排在前面：那是最需要人看一眼的一条。
 * 2. 「刚完成」只认「completed + 产物非空」，与任务页的 `delivered` 判定同一口径，
 *    否则会出现首页说「刚完成」、任务页说「没写出文件」的自相矛盾。
 * 3. 时间措辞跟着真实字段走：目标有 `completed_at` 才说「完成于」，
 *    没有就退回 `created_at` 并说「创建于」—— 不显示空值，也不拿创建时间冒充完成时间。
 */

import { parseTaskTime, riskTone } from './butler'
import type { ButlerTone } from './butler'
import { goalSummaryVerdict } from './goalVerdict'
import type { GoalSummary, GoalsResponse, PendingApprovalItem } from './operator'

/** 轮询间隔：与任务页、管家页保持同一节奏。 */
export const STATUS_BAND_REFRESH_MS = 30_000

/** 每行最多铺几条；剩余的进「还有 N 条」。 */
export const STATUS_BAND_LIMIT = 3

// ─── 风险等级 ────────────────────────────────────────────────────────────

const RISK_WEIGHT: Record<string, number> = {
  CRITICAL: 4,
  HIGH: 3,
  MEDIUM: 2,
  LOW: 1,
}

export function riskWeight(raw: string | null | undefined): number {
  const key = (raw ?? '').trim().toUpperCase()
  return RISK_WEIGHT[key] ?? 0
}

/** 风险等级 → 中文。原始英文只进 `title`，不上屏。 */
export function riskLabel(raw: string | null | undefined): string {
  switch ((raw ?? '').trim().toUpperCase()) {
    case 'CRITICAL':
      return '极高'
    case 'HIGH':
      return '高'
    case 'MEDIUM':
      return '中'
    case 'LOW':
      return '低'
    default:
      return '未知'
  }
}

/** 与管家页同一个色调函数，避免两处对「高危」的颜色判断分叉。 */
export function riskToneFor(raw: string | null | undefined): ButlerTone {
  return riskTone(raw ?? '')
}

// ─── 三个筛选器 ──────────────────────────────────────────────────────────

function createdSeconds(goal: GoalSummary): number {
  return parseTaskTime(goal.created_at) ?? Number.POSITIVE_INFINITY
}

function failedCount(goal: GoalSummary): number {
  const value = Number(goal.failed_tasks)
  return Number.isFinite(value) && value > 0 ? value : 0
}

/** 正在做：running / queued；失败过的排前面，其余按创建时间从早到晚。 */
export function pickRunningGoals(goals: GoalSummary[] | null | undefined): GoalSummary[] {
  if (!Array.isArray(goals)) return []
  return goals
    .filter((goal) => {
      const state = String(goal.state ?? '').toLowerCase()
      return state === 'running' || state === 'queued'
    })
    .slice()
    .sort((left, right) => {
      const failedOrder = (failedCount(left) > 0 ? 0 : 1) - (failedCount(right) > 0 ? 0 : 1)
      if (failedOrder !== 0) return failedOrder
      if (createdSeconds(left) !== createdSeconds(right)) {
        return createdSeconds(left) < createdSeconds(right) ? -1 : 1
      }
      return String(left.goal_id).localeCompare(String(right.goal_id))
    })
}

/** 完成时间：优先后端 `completed_at`，缺失时退回创建时间（并如实说明）。 */
export function completedSeconds(goal: GoalSummary): number {
  return parseTaskTime(goal.completed_at) ?? createdSeconds(goal)
}

/** 后端是否真的给了可解析的完成时间（决定文案说「完成于」还是「创建于」）。 */
export function hasCompletedAt(goal: GoalSummary): boolean {
  return parseTaskTime(goal.completed_at) !== null
}

/**
 * 刚完成：completed、产物非空、且判定为「已交付」；按完成时间从新到旧。
 *
 * 没有 `completed_at` 的历史目标按创建时间排序 —— 这是手上唯一的时间，
 * 但**不**把它当成完成时间展示（见 `completedAgoLabel`）。
 *
 * 返回完整列表，由组件截前 3 条并显示「还有 N 条」——在筛选器里截断会让
 * 「一共有几条」失去来源。
 */
export function pickCompletedGoals(goals: GoalSummary[] | null | undefined): GoalSummary[] {
  if (!Array.isArray(goals)) return []
  return goals
    .filter((goal) => {
      const state = String(goal.state ?? '').toLowerCase()
      return (
        state === 'completed' &&
        artifactPaths(goal).length > 0 &&
        goalSummaryVerdict(goal).kind === 'delivered'
      )
    })
    .slice()
    .sort((left, right) => {
      const leftDone = completedSeconds(left)
      const rightDone = completedSeconds(right)
      if (leftDone !== rightDone) return leftDone > rightDone ? -1 : 1
      return String(left.goal_id).localeCompare(String(right.goal_id))
    })
}

/**
 * 列表口径：`self` 只含自己的目标，`subtree` 含下属的。
 *
 * 字段来自后端；响应级缺失时看目标上有没有（两个位置后端都可能在用）。
 * 都没有就返回 null —— 不默认成 `subtree`，否则空态会多出一句没根据的解释。
 */
export function resolveVisibleScope(
  response: GoalsResponse | null | undefined,
): 'self' | 'subtree' | null {
  const direct = String(response?.visible_scope ?? '').trim().toLowerCase()
  if (direct === 'self' || direct === 'subtree') return direct
  const list = Array.isArray(response?.goals) ? response.goals : []
  const perGoal = list.find((goal) => String(goal.visible_scope ?? '').trim())?.visible_scope
  const value = String(perGoal ?? '').trim().toLowerCase()
  if (value === 'self' || value === 'subtree') return value
  return null
}

/**
 * 等你确认：按风险等级降序、拦截时间升序。
 *
 * 拦截时间缺失的排最后：把它当成「最早」会让一条没有时间的记录长期占据首位。
 */
export function pickPendingApprovals(
  items: PendingApprovalItem[] | null | undefined,
): PendingApprovalItem[] {
  if (!Array.isArray(items)) return []
  const blockedSeconds = (item: PendingApprovalItem): number =>
    parseTaskTime(item.blocked_at) ?? Number.POSITIVE_INFINITY
  return items.slice().sort((left, right) => {
    const riskOrder = riskWeight(right.risk_level) - riskWeight(left.risk_level)
    if (riskOrder !== 0) return riskOrder
    if (blockedSeconds(left) !== blockedSeconds(right)) {
      return blockedSeconds(left) < blockedSeconds(right) ? -1 : 1
    }
    return String(left.pending_id).localeCompare(String(right.pending_id))
  })
}

// ─── 展示文案 ────────────────────────────────────────────────────────────

/** 产物路径数组：兼容后端偶尔把 `{ path, exists }` 对象塞进 `artifacts`。 */
export function artifactPaths(goal: GoalSummary): string[] {
  const raw: unknown[] = Array.isArray(goal.artifacts) ? goal.artifacts : []
  return raw
    .map((item) => {
      if (typeof item === 'string') return item.trim()
      if (
        item &&
        typeof item === 'object' &&
        typeof (item as { path?: unknown }).path === 'string'
      ) {
        return (item as { path: string }).path.trim()
      }
      return ''
    })
    .filter(Boolean)
}

/** 路径 → 文件名；用于「刚完成」那一行，不把整条路径铺在首页。 */
export function artifactName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean)
  return parts[parts.length - 1] ?? path
}

/** 负责人：单人指派优先，其次任务里真实出现过的员工；都没有时如实说「未指派」。 */
export function goalOwnerLabel(goal: GoalSummary): string {
  if (goal.assigned_employee) return goal.assigned_employee
  if (Array.isArray(goal.assigned_employees) && goal.assigned_employees.length > 0) {
    return goal.assigned_employees.join('、')
  }
  return '未指派'
}

export function goalStateLabel(state: string | null | undefined): string {
  const value = (state ?? '').toLowerCase()
  if (value === 'running') return '执行中'
  if (value === 'queued') return '排队中'
  return ''
}

/** 进度：没有任务时是「准备中」，不写成 0/0 这种看起来像卡住的数字。 */
export function goalProgressLabel(goal: GoalSummary): string {
  const total = Number(goal.task_count)
  if (!Number.isFinite(total) || total <= 0) return '准备中'
  const completed = Number(goal.completed_tasks)
  return `${Number.isFinite(completed) ? completed : 0}/${total} 步`
}

/** 进度条比例；没有任务时返回 null（不画一条 0% 的假进度）。 */
export function goalProgressRatio(goal: GoalSummary): number | null {
  const total = Number(goal.task_count)
  if (!Number.isFinite(total) || total <= 0) return null
  const completed = Number(goal.completed_tasks)
  if (!Number.isFinite(completed) || completed < 0) return 0
  return Math.min(1, completed / total)
}

/** 秒 → 紧凑时长（分钟级以内不给「0 秒」）。 */
function compactElapsed(seconds: number): string {
  const value = Math.max(0, seconds)
  if (value < 60) return `${Math.round(value)} 秒`
  if (value < 3600) return `${Math.floor(value / 60)} 分钟`
  if (value < 86400) {
    const hours = Math.floor(value / 3600)
    const minutes = Math.floor((value % 3600) / 60)
    return minutes > 0 ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`
  }
  return `${Math.floor(value / 86400)} 天`
}

/** 「已创建 8 分钟」——说创建时间，不说运行时长。 */
export function createdAgoLabel(createdAt: number | null | undefined, nowMs: number): string {
  const seconds = parseTaskTime(createdAt)
  if (seconds === null) return '创建时间未记录'
  const elapsed = Math.max(0, nowMs / 1000 - seconds)
  if (elapsed < 60) return '刚创建'
  return `已创建 ${compactElapsed(elapsed)}`
}

/** 「已等待 12 分钟」——审批队列里最需要知道的就是等了多久。 */
export function waitingLabel(blockedAt: number | string | null | undefined, nowMs: number): string {
  const seconds = parseTaskTime(blockedAt)
  if (seconds === null) return '等待时间未记录'
  const elapsed = Math.max(0, nowMs / 1000 - seconds)
  if (elapsed < 60) return '刚被拦下'
  return `已等待 ${compactElapsed(elapsed)}`
}

/**
 * 「刚完成」的时间：有 `completed_at` 就说完成，没有就退回创建时间。
 *
 * 两种措辞必须能区分：
 *   * 有完成时间 →「刚完成 / 完成于 N 分钟前 …」；
 *   * 没有完成时间 →「刚创建 / 创建于 N 分钟前 …」，这是历史目标的真实口径。
 *
 * 绝不混用：一个三天前创建、刚刚跑完但后端没回 `completed_at` 的目标
 * 会显示「创建于 3 天前」，这是诚实的；写成「完成于 3 天前」就是假时间。
 */
export function completedAgoLabel(
  createdAt: number | null | undefined,
  nowMs: number,
  completedAt?: number | string | null,
): string {
  const finished = parseTaskTime(completedAt)
  if (finished !== null) {
    const elapsed = Math.max(0, nowMs / 1000 - finished)
    if (elapsed < 60) return '刚完成'
    if (elapsed < 3600) return `完成于 ${Math.floor(elapsed / 60)} 分钟前`
    if (elapsed < 86400) return `完成于 ${Math.floor(elapsed / 3600)} 小时前`
    if (elapsed < 7 * 86400) return `完成于 ${Math.floor(elapsed / 86400)} 天前`
    try {
      return `完成于 ${new Date(finished * 1000).toLocaleDateString('zh-CN')}`
    } catch {
      return '创建时间未记录'
    }
  }

  const seconds = parseTaskTime(createdAt)
  if (seconds === null) return '创建时间未记录'
  const elapsed = Math.max(0, nowMs / 1000 - seconds)
  if (elapsed < 60) return '刚创建'
  if (elapsed < 3600) return `创建于 ${Math.floor(elapsed / 60)} 分钟前`
  if (elapsed < 86400) return `创建于 ${Math.floor(elapsed / 3600)} 小时前`
  if (elapsed < 7 * 86400) return `创建于 ${Math.floor(elapsed / 86400)} 天前`
  try {
    return `创建于 ${new Date(seconds * 1000).toLocaleDateString('zh-CN')}`
  } catch {
    return '创建时间未记录'
  }
}

/** 待审批卡片的正文：优先用后端给的中文拦截原因，退到摘要，再退到中性说明。 */
export function pendingSummary(item: PendingApprovalItem): string {
  const reason = (item.reason ?? '').trim()
  if (reason) return reason
  const summary = (item.summary ?? '').trim()
  if (summary) return summary
  return '有一条动作被安全规则拦下，正在等你确认'
}
