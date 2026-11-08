/**
 * 鎏灏管家 —— 目标执行面的展示语义。
 *
 * 这一层只做**解释**，不造数据：所有字段都来自 `GET /v1/goals/{id}` 与
 * `GET /v1/goals` 的真实响应。三件容易说假话的事情在这里被钉死：
 *
 * 1. 状态映射。后端任务状态只有 `pending/ready/running/completed/failed/skipped`
 *    六种，没有「待确认」。界面上的「待确认」**只在**任务失败且错误串确实提到
 *    policy / approval / deferred 时才出现，并且保留原始错误，绝不把普通失败
 *    美化成一个需要人类盖章的动作。
 * 2. 耗时。`started_at` / `completed_at` 是后端 `str(time.time())` 的**数字字符串**
 *    （不是 ISO 时间）。直接 `new Date("1780000000.1")` 会得到 Invalid Date，旧页面
 *    因此把原始数字当时间显示。这里按 Unix 秒解析，并区分「未开始 / 进行中 /
 *    已结束」。
 * 3. 进度。只按**已经终结**的任务计数。运行中的目标会给出**增量**任务快照
 *    （可能只包含刚变化的任务），`mergeTaskSnapshots` 按 id 合并，绝不因为
 *    某次快照缺字段或少了某些任务就把已有卡片清空；没有任务时进度未知，
 *    不拿「已运行时长」冒充完成百分比。
 */

import type { GoalDetail, GoalTask } from './operator'
import { numberText } from './format'

/** 目标详情轮询间隔（运行中）。 */
export const BUTLER_DETAIL_POLL_MS = 2_000
/** 历史目标列表轮询间隔。 */
export const BUTLER_LIST_POLL_MS = 15_000

export type ButlerTone = 'ok' | 'warn' | 'danger' | 'accent' | 'muted'

export interface TextSegment {
  value: string
  href?: string
}

/**
 * 结论里出现的真实来源必须能点开，不能只让用户看一串死文本。
 * 同时识别模型常见的 markdown 形式 `[标题](url)`：只显示标题，
 * 不把 `[标题](` 和 `)` 这些语法符号残留在页面上。
 */
const LINK_PATTERN =
  /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|https?:\/\/[^\s，。；、）」』"'<>]+/g

/**
 * 把一段文本拆成「普通文本 / 链接」片段，供页面渲染 <a>。
 * 句末的英文标点（,.;:!?）不属于 URL，留在后面的普通文本里。
 */
export function splitLinks(text: string): TextSegment[] {
  if (!text) return []
  const segments: TextSegment[] = []
  let cursor = 0
  for (const match of text.matchAll(LINK_PATTERN)) {
    const index = match.index ?? 0
    if (match[2]) {
      if (index > cursor) segments.push({ value: text.slice(cursor, index) })
      segments.push({ value: match[1], href: match[2] })
      cursor = index + match[0].length
      continue
    }
    const value = match[0].replace(/[.,;:!?]+$/, '')
    if (!value) continue
    if (index > cursor) segments.push({ value: text.slice(cursor, index) })
    segments.push({ value, href: value })
    cursor = index + value.length
  }
  if (cursor < text.length) segments.push({ value: text.slice(cursor) })
  return segments
}

export type TaskPhase =
  | 'queued'
  | 'running'
  | 'awaiting'
  | 'succeeded'
  | 'failed'
  | 'interrupted'
  | 'skipped'
  | 'unknown'

export interface TaskPhaseInfo {
  phase: TaskPhase
  label: string
  tone: ButlerTone
  /** 保留后端的原始状态串，供 tooltip 显示；界面不隐藏它。 */
  raw: string
  note?: string
}

/**
 * 只在错误串确实指向「策略拦截 / 等人类审批」时返回 true。
 *
 * 刻意不匹配裸的 `approval` —— 很多普通报错里也会出现这个词，那会把失败误判成
 * 「待确认」，让操作者以为点一下就能继续。宁可漏判（显示普通失败）也不误判。
 */
const APPROVAL_PATTERN =
  /policy_approval_required|approval[_ -]?required|requires? human|waiting for (a )?human|deferred|policydeferred|待人工审批|需要人工审批|人工授权|人类审批/i

export function looksLikeApproval(value: unknown): boolean {
  return typeof value === 'string' && value.length > 0 && APPROVAL_PATTERN.test(value)
}

/** 后端任务状态 → 界面语义。未知状态原样显示，不折叠成「进行中」。 */
export function taskPhaseInfo(task: GoalTask): TaskPhaseInfo {
  const raw = (task?.status ?? '').toString()
  const value = raw.toLowerCase()
  if (value === 'completed' || value === 'completed_unattributed') {
    // 「执行结束」不是「成功」；是否真的交付由 goalVerdict 按 artifacts 判定。
    return { phase: 'succeeded', label: '已完成', tone: 'ok', raw }
  }
  if (value === 'failed') {
    if (looksLikeApproval(task.error)) {
      return {
        phase: 'awaiting',
        label: '待确认',
        tone: 'warn',
        raw,
        note: '执行被策略拦截，可能需要你在审批中心授权后再重试',
      }
    }
    return { phase: 'failed', label: '失败', tone: 'danger', raw }
  }
  if (value === 'running') {
    return { phase: 'running', label: '进行中', tone: 'accent', raw }
  }
  if (value === 'interrupted') {
    // 进程被杀时仍在执行的任务：结果未知，但可以单独重试。
    return {
      phase: 'interrupted',
      label: '已中断',
      tone: 'danger',
      raw,
      note: '进程中断时该任务仍在执行，结果未知；可单独重试',
    }
  }
  if (value === 'skipped') {
    return { phase: 'skipped', label: '已跳过', tone: 'muted', raw }
  }
  if (value === 'pending' || value === 'ready' || value === 'planned') {
    return { phase: 'queued', label: '排队', tone: 'muted', raw }
  }
  return {
    phase: 'unknown',
    label: raw || '未知',
    tone: 'muted',
    raw,
    note: raw ? `出现了未登记的任务状态「${raw}」` : '未给出任务状态',
  }
}

/**
 * 解析任务 / 审计时间。后端把 `time.time()` 直接 `str()` 成 `"1780000000.123"`；
 * 审计事件的 `blocked_at` 则通常是数字。两种都按 Unix 秒解析，同时兼容 ISO 串
 * 与毫秒数；无法解析返回 null。
 */
export function parseTaskTime(value: string | number | null | undefined): number | null {
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return null
    return value > 1e12 ? value / 1000 : value
  }
  if (typeof value !== 'string') return null
  const text = value.trim()
  if (!text) return null
  if (/^\d+(\.\d+)?$/.test(text)) {
    const numeric = Number(text)
    if (!Number.isFinite(numeric) || numeric <= 0) return null
    return numeric > 1e12 ? numeric / 1000 : numeric
  }
  const ms = Date.parse(text)
  if (!Number.isFinite(ms) || ms <= 0) return null
  return ms / 1000
}

/**
 * 把增量任务快照合并进已有卡片。
 *
 * 后端在运行期间只**按 task id 更新槽位**，某次快照可能只包含刚变化的任务，
 * 甚至某个字段暂时缺失。规则：
 *   * 以 task id 为键；快照里没出现的任务原样保留（不清空）；
 *   * 快照里值为 `undefined` 的字段保留旧值（缺字段 ≠ 该字段被清空）；
 *   * 值为 `null` 的字段照常覆盖（后端明确说过「现在是空的」）；
 *   * 新任务按到达顺序追加。
 * 返回新数组，不修改入参。
 */
export function mergeTaskSnapshots(
  previous: GoalTask[],
  incoming: GoalTask[],
): GoalTask[] {
  const result = previous.map((task) => ({ ...task }))
  const positions = new Map<string, number>()
  result.forEach((task, index) => {
    if (task?.id) positions.set(task.id, index)
  })

  for (const snapshot of incoming) {
    if (!snapshot || typeof snapshot.id !== 'string' || !snapshot.id) continue
    const fields = Object.fromEntries(
      Object.entries(snapshot).filter(([, value]) => value !== undefined),
    ) as Partial<GoalTask>
    const position = positions.get(snapshot.id)
    if (position === undefined) {
      positions.set(snapshot.id, result.length)
      result.push({ ...snapshot })
      continue
    }
    result[position] = { ...result[position], ...fields }
  }
  return result
}

/** 风险级别 → 语调。未知级别保持中性，不猜成高危。 */
export function riskTone(risk: string): ButlerTone {
  switch ((risk ?? '').toUpperCase()) {
    case 'CRITICAL':
    case 'HIGH':
      return 'danger'
    case 'MEDIUM':
      return 'warn'
    case 'LOW':
      return 'ok'
    default:
      return 'muted'
  }
}

/** 单任务耗时（秒）。已结束按真实起止；进行中按「开始到现在」；未开始返回 null。 */
export function taskDurationSeconds(task: GoalTask, nowSeconds = Date.now() / 1000): number | null {
  const started = parseTaskTime(task.started_at)
  if (started === null) return null
  const completed = parseTaskTime(task.completed_at)
  if (completed !== null) return Math.max(0, completed - started)
  if ((task.status ?? '').toLowerCase() === 'running') {
    return Math.max(0, nowSeconds - started)
  }
  return null
}

/** 秒 → 人话。null 是「没有数据」，不是 0 秒。 */
export function formatTaskDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return '—'
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))} 毫秒`
  if (seconds < 60) return `${seconds < 10 ? numberText(seconds, 1) : Math.round(seconds)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 时 ${minutes % 60} 分`
}

/** 运行中的已运行时长 → `00:12` / `01:02:03`。非法值返回「—」。 */
export function formatElapsed(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export interface TaskStats {
  total: number
  queued: number
  running: number
  awaiting: number
  succeeded: number
  failed: number
  skipped: number
  unknown: number
  /** 已终结（成功 / 失败 / 待确认 / 跳过）的任务数。 */
  finished: number
  /** 真正写出文件的已完成任务数（只有真实产物路径才算交付）。 */
  delivered: number
  /** 已终结占比 0..100；没有任务时为 0（页面必须显示「进度未知」而不是 0%）。 */
  percent: number
}

export function taskStats(tasks: GoalTask[]): TaskStats {
  const stats: TaskStats = {
    total: 0,
    queued: 0,
    running: 0,
    awaiting: 0,
    succeeded: 0,
    failed: 0,
    skipped: 0,
    unknown: 0,
    finished: 0,
    delivered: 0,
    percent: 0,
  }
  for (const task of tasks) {
    stats.total += 1
    switch (taskPhaseInfo(task).phase) {
      case 'queued':
        stats.queued += 1
        break
      case 'running':
        stats.running += 1
        break
      case 'awaiting':
        stats.awaiting += 1
        break
      case 'succeeded':
        stats.succeeded += 1
        if (Array.isArray(task.artifacts) && task.artifacts.length > 0) {
          stats.delivered += 1
        }
        break
      case 'failed':
        stats.failed += 1
        break
      case 'interrupted':
        // 中断 = 未成功且可重试，与失败一起计入"已终结/需处理"。
        stats.failed += 1
        break
      case 'skipped':
        stats.skipped += 1
        break
      default:
        stats.unknown += 1
    }
  }
  stats.finished = stats.succeeded + stats.failed + stats.skipped + stats.awaiting
  stats.percent = stats.total > 0 ? Math.round((stats.finished / stats.total) * 100) : 0
  return stats
}

/** 目标状态 → 中文标签。未知原样显示。 */
export function goalStateLabel(state: string): string {
  switch ((state ?? '').toLowerCase()) {
    case 'running':
      return '执行中'
    case 'created':
      return '已创建'
    case 'planning':
    case 'decomposing':
      return '拆解中'
    case 'decomposed':
      return '已拆解'
    case 'executing':
      return '执行中'
    case 'waiting':
      return '等待依赖'
    case 'verifying':
      return '汇总校验中'
    case 'completed':
      // 生命周期终态，不是「成功」；成功与否由 goalVerdict 判定。
      return '已结束'
    case 'partial':
      return '部分完成'
    case 'failed':
      return '失败'
    case 'interrupted':
      return '已中断（进程重启）'
    case 'cancelled':
      return '已中止'
    case 'replan_suggested':
      return '建议重试'
    default:
      return state || '未知'
  }
}

export function goalStateTone(state: string): ButlerTone {
  const value = (state ?? '').toLowerCase()
  if (value === 'completed') return 'ok'
  if (value === 'partial' || value === 'replan_suggested') return 'warn'
  if (value === 'failed' || value === 'cancelled' || value === 'interrupted') return 'danger'
  if (value === 'running' || value === 'planning' || value === 'decomposing' || value === 'executing') {
    return 'accent'
  }
  return 'muted'
}

/** 是否仍在推进。未知状态按「不在推进」处理，避免无休止轮询。 */
export function isGoalActive(state: string): boolean {
  return ['created', 'planning', 'decomposing', 'decomposed', 'executing', 'waiting', 'verifying', 'running'].includes(
    (state ?? '').toLowerCase(),
  )
}

/** 目标的真实总耗时：最早任务开始 → 最晚任务结束 / 现在。没有任务返回 null。 */
export function goalElapsedSeconds(tasks: GoalTask[], nowSeconds = Date.now() / 1000): number | null {
  const starts: number[] = []
  const ends: number[] = []
  let live = false
  for (const task of tasks) {
    const start = parseTaskTime(task.started_at)
    if (start !== null) starts.push(start)
    const end = parseTaskTime(task.completed_at)
    if (end !== null) ends.push(end)
    if (end === null && (task.status ?? '').toLowerCase() === 'running') live = true
  }
  if (starts.length === 0) return null
  const start = Math.min(...starts)
  const end = live ? nowSeconds : ends.length > 0 ? Math.max(...ends) : nowSeconds
  return Math.max(0, end - start)
}

/** 需要人类确认（策略拦截）的任务。没有就是空数组，不编造。 */
export function approvalTasks(tasks: GoalTask[]): GoalTask[] {
  return tasks.filter((task) => taskPhaseInfo(task).phase === 'awaiting')
}

/** 失败/中断任务（不含待确认）。中断任务结果未知，同样允许单独重试。 */
export function failedTasks(tasks: GoalTask[]): GoalTask[] {
  return tasks.filter((task) => {
    const phase = taskPhaseInfo(task).phase
    return phase === 'failed' || phase === 'interrupted'
  })
}

/**
 * 人话汇总。优先用后端 `evaluation.summary`（它是真实的评估器输出），
 * 缺失时只用**可数事实**拼一句，不替后端编造结论。
 */
export function buildGoalSummary(detail: GoalDetail): string {
  if (isGoalActive(detail.state)) {
    return '管家还在执行；任务会随增量快照逐条出现，结束后再给出一段完整汇总。'
  }
  const stats = taskStats(detail.tasks)
  const artifacts = Array.isArray(detail.artifacts) ? detail.artifacts : []
  const parts: string[] = []
  // The conclusion is the deliverable: lead with the synthesized evaluation
  // summary, then the factual counts/artifacts as supporting detail. Before
  // this change the summary was always empty, so users only ever saw "成功 N 项".
  const evaluation = detail.evaluation?.summary?.trim()
  if (evaluation) parts.push(`评估：${evaluation}`)
  if (stats.total === 0) {
    parts.push('本次没有分解出任务。')
  } else {
    const tail: string[] = []
    // 「完成」只数执行结束；「可交付」只数真的写出了文件的任务，两者分开报。
    if (stats.succeeded > 0) tail.push(`完成 ${stats.succeeded}（可交付 ${stats.delivered}）`)
    if (stats.failed > 0) tail.push(`失败 ${stats.failed}`)
    if (stats.awaiting > 0) tail.push(`待确认 ${stats.awaiting}`)
    if (stats.skipped > 0) tail.push(`跳过 ${stats.skipped}`)
    if (stats.queued + stats.running > 0) tail.push(`未结束 ${stats.queued + stats.running}`)
    parts.push(`共 ${stats.total} 项任务${tail.length > 0 ? `：${tail.join('，')}` : ''}。`)
  }
  parts.push(
    artifacts.length > 0
      ? `向工作区写入 ${artifacts.length} 个文件。`
      : '没有向工作区写入任何文件。',
  )
  if (detail.error) parts.push(`错误：${detail.error}`)
  return parts.join(' ')
}

/** 历史列表按创建时间倒序；缺失时间排在后面（不伪造 0 为最新）。 */
export function sortGoalSummaries<T extends { created_at: number | null }>(list: T[]): T[] {
  return [...list].sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0))
}
