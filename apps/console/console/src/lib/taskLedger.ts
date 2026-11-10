/**
 * 任务台账（`/v1/tasks*`）的取数层与纯展示映射。
 *
 * 依据是真实已挂载的后端：`src/gateway/tasks_api.py`（由 `ai_management` 编进
 * `/v1` 路由）与 `src/gateway/task_ledger.py`。这里的所有字段名都对着
 * `_task_row_to_dict` / `_step_row_to_dict` / `summary()` 的真实返回值写，
 * 不额外发明字段。
 *
 * 诚实边界
 * --------
 * 后端**没有**优先级（priority）与截止时间（due）字段，列表也不返回子步骤，
 * 因此进度必须由 `/v1/tasks/{id}/steps` 单独取。缺什么就说「暂未提供」，
 * 绝不补占位数字 —— 一份看起来完整的假台账比空着更糟。
 */

import { apiFetch } from './api'
import { numberText } from './format'
import type { Tone } from './format'

export const TASK_STATUS_VALUES = [
  'pending_assignment',
  'pending',
  'running',
  'reviewing',
  'completed',
  'failed',
  'cancelled',
] as const

export type TaskStatus = (typeof TASK_STATUS_VALUES)[number]

export interface TaskStatusMeta {
  value: TaskStatus
  label: string
  tone: Tone
}

/** 七态与 `task_ledger.LedgerStatus` 一一对应（同一个状态机，不另造词）。 */
export const TASK_STATUSES: readonly TaskStatusMeta[] = [
  { value: 'pending_assignment', label: '待分配', tone: 'muted' },
  { value: 'pending', label: '待执行', tone: 'accent' },
  { value: 'running', label: '执行中', tone: 'warn' },
  { value: 'reviewing', label: '待审核', tone: 'accent' },
  { value: 'completed', label: '已完成', tone: 'ok' },
  { value: 'failed', label: '失败', tone: 'danger' },
  { value: 'cancelled', label: '已取消', tone: 'muted' },
]

export function taskStatusMeta(value: string): TaskStatusMeta | null {
  return TASK_STATUSES.find((item) => item.value === value) ?? null
}

/** 未知状态如实标注，不并入任何一个已知态（并入就等于替后端猜）。 */
export function taskStatusLabel(value: string): string {
  const meta = taskStatusMeta(value)
  if (meta) return meta.label
  const text = (value || '').trim()
  return text ? `未知状态（${text}）` : '未知状态'
}

export function taskStatusTone(value: string): Tone {
  return taskStatusMeta(value)?.tone ?? 'muted'
}

export interface LedgerAssignee {
  type: string
  id: string
  name: string
}

export interface LedgerTask {
  task_id: string
  goal_id: string
  title: string
  description: string
  status: string
  status_source: string
  assignee: LedgerAssignee | null
  owner_account_id: string
  project_id: string | null
  artifacts: string[]
  error: string | null
  technical_detail: string | null
  created_at: string | null
  started_at: string | null
  completed_at: string | null
  updated_at: string | null
  attempt_count: number
  archived: boolean
  human_hold: boolean
  source: string
}

export interface LedgerStep {
  step_id: string
  task_id: string
  step_key: string
  seq: number
  name: string
  description: string
  status: string
  status_source: string
  capability_id: string | null
  assignee: LedgerAssignee | null
  dependencies: string[]
  artifacts: string[]
  error: string | null
  technical_detail: string | null
  started_at: string | null
  completed_at: string | null
  attempt_count: number
  human_hold: boolean
}

export interface LedgerRun {
  run_id: string
  task_id: string
  step_key: string | null
  event: string
  status_from: string | null
  status_to: string | null
  actor: { type: string; id: string }
  reason: string | null
  error: string | null
  attempt: number
  at: string | null
}

export interface LedgerTaskDetail extends LedgerTask {
  steps: LedgerStep[]
  runs: LedgerRun[]
}

export interface TasksPayload {
  total: number
  limit: number
  offset: number
  items: LedgerTask[]
  /** 后端给的可见范围（self / subtree …）；缺失时不替它猜。 */
  visible_scope?: string
}

export interface MigrationRecord {
  migration_id: string
  source_path: string
  source_sha256: string
  started_at: string | null
  finished_at: string | null
  status: string
  entries_scanned: number
  tasks_imported: number
  steps_imported: number
  runs_imported: number
  skipped: number
  error: string | null
  /** 活动迁移的返回里有；历史记录的状态接口**不返回**这一项。 */
  skipped_reasons?: Record<string, number>
}

export interface LedgerStatusPayload {
  ledger: {
    db_path: string
    schema_version: number
    counts: { tasks: number; steps: number; runs: number }
    by_status: Record<string, number>
    recent_migrations: MigrationRecord[]
  }
  lag: {
    hot_goals: number
    ledger_tasks: number
    missing_in_ledger: string[]
    stale: Array<{ task_id: string; source_state: string; expected: string; ledger: string }>
    lag_total: number
    write_failures: number
    last_failure_at: string | null
    last_failure_reason: string | null
    default_owner: string
    projection_failures?: number
    last_projection_error?: string | null
  }
}

// ── 取数 ─────────────────────────────────────────────────────

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function strOrNull(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function normalizeAssignee(value: unknown): LedgerAssignee | null {
  if (!isRecord(value)) return null
  return {
    type: str(value.type),
    id: str(value.id),
    name: str(value.name),
  }
}

function normalizeTask(raw: unknown): LedgerTask | null {
  if (!isRecord(raw) || typeof raw.task_id !== 'string' || !raw.task_id) return null
  return {
    task_id: raw.task_id,
    goal_id: str(raw.goal_id),
    title: str(raw.title),
    description: str(raw.description),
    status: str(raw.status),
    status_source: str(raw.status_source),
    assignee: normalizeAssignee(raw.assignee),
    owner_account_id: str(raw.owner_account_id),
    project_id: strOrNull(raw.project_id),
    artifacts: Array.isArray(raw.artifacts)
      ? raw.artifacts.filter((item): item is string => typeof item === 'string')
      : [],
    error: strOrNull(raw.error),
    technical_detail: strOrNull(raw.technical_detail),
    created_at: strOrNull(raw.created_at),
    started_at: strOrNull(raw.started_at),
    completed_at: strOrNull(raw.completed_at),
    updated_at: strOrNull(raw.updated_at),
    attempt_count: num(raw.attempt_count),
    archived: raw.archived === true,
    human_hold: raw.human_hold === true,
    source: str(raw.source),
  }
}

function normalizeStep(raw: unknown): LedgerStep | null {
  if (!isRecord(raw) || typeof raw.step_key !== 'string') return null
  return {
    step_id: str(raw.step_id),
    task_id: str(raw.task_id),
    step_key: raw.step_key,
    seq: num(raw.seq),
    name: str(raw.name),
    description: str(raw.description),
    status: str(raw.status),
    status_source: str(raw.status_source),
    capability_id: strOrNull(raw.capability_id),
    assignee: normalizeAssignee(raw.assignee),
    dependencies: Array.isArray(raw.dependencies)
      ? raw.dependencies.filter((item): item is string => typeof item === 'string')
      : [],
    artifacts: Array.isArray(raw.artifacts)
      ? raw.artifacts.filter((item): item is string => typeof item === 'string')
      : [],
    error: strOrNull(raw.error),
    technical_detail: strOrNull(raw.technical_detail),
    started_at: strOrNull(raw.started_at),
    completed_at: strOrNull(raw.completed_at),
    attempt_count: num(raw.attempt_count),
    human_hold: raw.human_hold === true,
  }
}

/** 列表响应校验：缺 `items` 就返回 null，由页面说「暂未提供」而不是画空表。 */
export function parseTasksPayload(raw: unknown): TasksPayload | null {
  if (!isRecord(raw) || !Array.isArray(raw.items)) return null
  const items = raw.items
    .map(normalizeTask)
    .filter((item): item is LedgerTask => item !== null)
  return {
    total: num(raw.total, items.length),
    limit: num(raw.limit, items.length),
    offset: num(raw.offset),
    items,
    ...(typeof raw.visible_scope === 'string' ? { visible_scope: raw.visible_scope } : {}),
  }
}

export function parseTaskDetail(raw: unknown): LedgerTaskDetail | null {
  const task = normalizeTask(raw)
  if (!task || !isRecord(raw)) return null
  const steps = Array.isArray(raw.steps)
    ? raw.steps.map(normalizeStep).filter((item): item is LedgerStep => item !== null)
    : []
  const runs = Array.isArray(raw.runs)
    ? raw.runs.filter(isRecord).map((item) => ({
        run_id: str(item.run_id),
        task_id: str(item.task_id),
        step_key: strOrNull(item.step_key),
        event: str(item.event),
        status_from: strOrNull(item.status_from),
        status_to: strOrNull(item.status_to),
        actor: isRecord(item.actor)
          ? { type: str(item.actor.type), id: str(item.actor.id) }
          : { type: '', id: '' },
        reason: strOrNull(item.reason),
        error: strOrNull(item.error),
        attempt: num(item.attempt),
        at: strOrNull(item.at),
      }))
    : []
  return { ...task, steps, runs }
}

export function parseSteps(raw: unknown): LedgerStep[] | null {
  if (!isRecord(raw) || !Array.isArray(raw.steps)) return null
  return raw.steps.map(normalizeStep).filter((item): item is LedgerStep => item !== null)
}

export function parseLedgerStatus(raw: unknown): LedgerStatusPayload | null {
  if (!isRecord(raw) || !isRecord(raw.ledger) || !isRecord(raw.lag)) return null
  const ledger = raw.ledger
  const lag = raw.lag
  if (!isRecord(ledger.counts) || !Array.isArray(ledger.recent_migrations)) return null
  return {
    ledger: {
      db_path: str(ledger.db_path),
      schema_version: num(ledger.schema_version),
      counts: {
        tasks: num(ledger.counts.tasks),
        steps: num(ledger.counts.steps),
        runs: num(ledger.counts.runs),
      },
      by_status: isRecord(ledger.by_status)
        ? Object.fromEntries(
            Object.entries(ledger.by_status).filter(
              (entry): entry is [string, number] => typeof entry[1] === 'number',
            ),
          )
        : {},
      recent_migrations: ledger.recent_migrations.filter(isRecord).map((item) => ({
        migration_id: str(item.migration_id),
        source_path: str(item.source_path),
        source_sha256: str(item.source_sha256),
        started_at: strOrNull(item.started_at),
        finished_at: strOrNull(item.finished_at),
        status: str(item.status),
        entries_scanned: num(item.entries_scanned),
        tasks_imported: num(item.tasks_imported),
        steps_imported: num(item.steps_imported),
        runs_imported: num(item.runs_imported),
        skipped: num(item.skipped),
        error: strOrNull(item.error),
        ...(isRecord(item.skipped_reasons)
          ? {
              skipped_reasons: Object.fromEntries(
                Object.entries(item.skipped_reasons).filter(
                  (entry): entry is [string, number] => typeof entry[1] === 'number',
                ),
              ),
            }
          : {}),
      })),
    },
    lag: {
      hot_goals: num(lag.hot_goals),
      ledger_tasks: num(lag.ledger_tasks),
      missing_in_ledger: Array.isArray(lag.missing_in_ledger)
        ? lag.missing_in_ledger.filter((item): item is string => typeof item === 'string')
        : [],
      stale: Array.isArray(lag.stale)
        ? lag.stale.filter(isRecord).map((item) => ({
            task_id: str(item.task_id),
            source_state: str(item.source_state),
            expected: str(item.expected),
            ledger: str(item.ledger),
          }))
        : [],
      lag_total: num(lag.lag_total),
      write_failures: num(lag.write_failures),
      last_failure_at: strOrNull(lag.last_failure_at),
      last_failure_reason: strOrNull(lag.last_failure_reason),
      default_owner: str(lag.default_owner),
      ...(typeof lag.projection_failures === 'number'
        ? { projection_failures: lag.projection_failures }
        : {}),
      ...(typeof lag.last_projection_error === 'string' || lag.last_projection_error === null
        ? { last_projection_error: strOrNull(lag.last_projection_error) }
        : {}),
    },
  }
}

/** 列表请求路径（轮询由 `useApi` 负责，这里只拼真实查询参数）。 */
export function tasksPath(status: string, limit: number): string {
  const params = new URLSearchParams({
    limit: String(limit),
    sort_by: 'created_at',
    sort_order: 'desc',
  })
  if (status) params.set('status', status)
  return `/v1/tasks?${params.toString()}`
}

export async function getTaskDetail(taskId: string): Promise<LedgerTaskDetail | null> {
  const path = `/v1/tasks/${encodeURIComponent(taskId)}`
  return parseTaskDetail(await apiFetch<unknown>(path))
}

export async function getTaskSteps(taskId: string): Promise<LedgerStep[] | null> {
  const path = `/v1/tasks/${encodeURIComponent(taskId)}/steps`
  return parseSteps(await apiFetch<unknown>(path))
}

export async function retryTaskStep(
  taskId: string,
  stepKey: string | null,
  reason: string,
): Promise<{ goal_state: string | null; step_key: string | null }> {
  const raw = await apiFetch<unknown>(
    `/v1/tasks/${encodeURIComponent(taskId)}/retry`,
    {
      method: 'POST',
      body: JSON.stringify(stepKey ? { step_key: stepKey, reason } : { reason }),
    },
  )
  const record = isRecord(raw) ? raw : {}
  return {
    goal_state: strOrNull(record.goal_state),
    step_key: strOrNull(record.step_key),
  }
}

export async function takeoverTask(taskId: string, reason: string): Promise<void> {
  await apiFetch<unknown>(`/v1/tasks/${encodeURIComponent(taskId)}/takeover`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

// ── 纯展示映射 ───────────────────────────────────────────────

/** 负责人：`employee` 用名字，`agent` 用执行个体标识，`none` 如实说未分配。 */
export function assigneeText(assignee: LedgerAssignee | null | undefined): string {
  if (!assignee || assignee.type === 'none' || !assignee.type) return '未分配'
  const name = (assignee.name || assignee.id || '').trim()
  if (!name) return '未分配'
  return assignee.type === 'agent' ? `${name}（执行个体）` : name
}

const EMPTY = '—'

/** ISO 时间 → 本地时间；缺失或非法如实显示「—」，不拿 0 顶替。 */
export function formatStamp(value: string | null | undefined): string {
  if (!value) return EMPTY
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function toMs(value: string | null | undefined): number | null {
  if (!value) return null
  const ms = new Date(value).getTime()
  return Number.isFinite(ms) ? ms : null
}

/** 起止时间之间的真实耗时；任一缺失返回 null（由调用方决定怎么说）。 */
export function durationBetween(
  start: string | null | undefined,
  end: string | null | undefined,
): string | null {
  const from = toMs(start)
  const to = toMs(end)
  if (from === null || to === null) return null
  const ms = to - from
  if (ms < 0) return null
  if (ms < 1000) return `${Math.round(ms)} 毫秒`
  if (ms < 60_000) return `${numberText(ms / 1000, 1)} 秒`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes} 分 ${seconds} 秒`
}

export interface StepProgress {
  total: number
  done: number
  failed: number
  running: number
}

export function progressOf(steps: LedgerStep[]): StepProgress {
  return {
    total: steps.length,
    done: steps.filter((step) => step.status === 'completed').length,
    failed: steps.filter((step) => step.status === 'failed').length,
    running: steps.filter((step) => step.status === 'running').length,
  }
}

/** 进度文本：没有子步骤就说没有，不写 0/0 冒充"还没开始"。 */
export function progressText(progress: StepProgress | null): string {
  if (progress === null) return '暂未提供'
  if (progress.total === 0) return '没有子步骤'
  return `${progress.done}/${progress.total} 步`
}

/** 与后端 `TAKEOVER_STATUSES` 同口径：执行中的任务不能接管。 */
const TAKEOVER_STATUSES = new Set(['pending', 'pending_assignment', 'reviewing', 'failed'])

export function canRetryTask(task: LedgerTask): boolean {
  return task.status === 'failed' && !task.human_hold
}

export function canTakeoverTask(task: LedgerTask): boolean {
  return !task.human_hold && TAKEOVER_STATUSES.has(task.status)
}

/** 迁移跳过原因 → 人话（后端 code 是稳定的机器标识）。 */
const SKIP_REASON_LABELS: Record<string, string> = {
  missing_goal_id: '缺少目标 ID，无法建立台账行',
  already_in_ledger: '台账里已存在同 ID 任务，跳过不覆盖',
}

export function skipReasonLabel(reason: string): string {
  return SKIP_REASON_LABELS[reason] ?? `未登记的原因（${reason}）`
}
