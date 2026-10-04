/**
 * 人类操作员控制层 —— 驾驶舱所有「写」操作的统一入口。
 *
 * 与 `api.ts` 的只读 `useApi` 互补：这里是真正的**主权动作**（雇佣 / 创建目标 /
 * 暂停 / 恢复 / 中止 / 重规划 / 解雇），全部走同一个带会话的 `apiFetch`，因此
 * 401 处理、错误透传与只读层完全一致。
 *
 * 端点命名与 `src/gateway/ai_management.py` 严格对齐；其中
 * ``POST /v1/goals/{id}/stop`` 是本次补齐的「中止运行中自主动作」主权端点。
 */

import { apiFetch } from './api'

export type Json = Record<string, unknown>

// ─── 雇佣 AI Employee ────────────────────────────────────────

export interface HireEmployeeBody {
  name: string
  agent_count?: number
  agent_types?: string[]
}

export function hireEmployee(body: HireEmployeeBody): Promise<Json> {
  return apiFetch<Json>('/v1/employees', {
    method: 'POST',
    body: JSON.stringify({
      name: body.name,
      agent_count: body.agent_count ?? 3,
      agent_types: body.agent_types ?? [],
    }),
  })
}

// ─── 创建并执行 Goal ─────────────────────────────────────────

export interface CreateGoalBody {
  natural_language: string
  scope?: string
  plan_mode?: string
  verification_criteria?: Json | null
  /**
   * 后台执行。``true`` 时网关立刻返回 ``state:"running"``，执行在后台线程继续，
   * 于是「中止」是一个**可观测的主权动作**：目标真的在飞，人真的能把它停下来。
   *
   * 默认 ``false`` 是网关的既有契约（同步跑完再返回）。驾驶舱此前从不发送这个
   * 字段，于是 UI 上创建的每一个 goal 都在请求里就跑完了 —— 「停止」按钮永远
   * 没有可停的东西，主权中止路径在界面上等于不存在。
   */
  background?: boolean
  /**
   * 归属的项目 id（来自 `Projects` 面）。必须是已存在的项目，否则网关返回 404。
   * 留空表示独立目标，不属于任何项目。
   */
  project_id?: string | null
}

export function createGoal(body: CreateGoalBody): Promise<Json> {
  return apiFetch<Json>('/v1/goals', {
    method: 'POST',
    body: JSON.stringify({
      natural_language: body.natural_language,
      scope: body.scope ?? 'L1',
      plan_mode: body.plan_mode ?? 'auto',
      verification_criteria: body.verification_criteria ?? null,
      background: body.background ?? false,
    }),
  })
}

// ─── 中止 Goal（主权：停下正在运行的自主动作） ──────────────────

export function stopGoal(goalId: string): Promise<Json> {
  return apiFetch<Json>(`/v1/goals/${encodeURIComponent(goalId)}/stop`, {
    method: 'POST',
  })
}

// ─── 重规划 Goal ─────────────────────────────────────────────

export function replanGoal(goalId: string): Promise<Json> {
  return apiFetch<Json>(`/v1/goals/${encodeURIComponent(goalId)}/replan`, {
    method: 'POST',
  })
}

// ─── 暂停 / 恢复单个 agent ───────────────────────────────────

export function pauseAgent(agentId: string): Promise<Json> {
  return apiFetch<Json>(`/v1/employees/${encodeURIComponent(agentId)}/pause`, {
    method: 'POST',
  })
}

export function resumeAgent(agentId: string): Promise<Json> {
  return apiFetch<Json>(`/v1/employees/${encodeURIComponent(agentId)}/resume`, {
    method: 'POST',
  })
}

// ─── 解雇（按名员工） ─────────────────────────────────────────

export function deleteEmployee(name: string): Promise<{ removed: string; ok: boolean }> {
  return apiFetch<{ removed: string; ok: boolean }>(
    `/v1/employees/${encodeURIComponent(name)}`,
    { method: 'DELETE' },
  )
}

// ─── 审计可解释性（只读查询面） ──────────────────────────────
//
// 端点与 `src/gateway/audit.py` 严格对齐。这一层**只有读**：没有写入 / 删除 /
// 修复链的能力 —— 一个能自愈的审计链等于一个可以被悄悄改写的审计链。
// `verifyAuditChain()` 返回 `ok=false` 就是链被改过/被截断/被分叉，前端必须
// 原样展示，不能美化成"正常"。

export interface AuditEvent {
  seq: number
  event_id: string
  event_type: string
  principal_id: string
  scope: string
  timestamp: number
  correlation_id: string
  outcome: string
  details: Record<string, unknown> | null
  /** 内核动作名（details.action 的顶层投影，便于按动作筛选） */
  action: string | null
  event_hash: string
  prev_event_hash: string | null
  hash_alg: string | null
  link_hash: string | null
}

export interface AuditEventsResponse {
  events: AuditEvent[]
  count: number
  /** 达到 limit 上限：还有更多，界面应提示而不是假装这是全部 */
  truncated: boolean
  filters: {
    principal: string | null
    action: string | null
    outcome: string | null
    correlation_id: string | null
    since: number | null
    until: number | null
    event_type: string | null
    limit: number
    reverse: boolean
  }
}

export interface AuditVerifyResult {
  /** 链完好为 true；被篡改 / 被截断 / 被分叉为 false */
  ok: boolean
  entries_checked: number
  first_failure: string | null
  details: {
    failures: string[]
    failure_count: number
    pinpoint_ran: boolean
    error?: string
    mutation_performed: boolean
    read_only: boolean
    db_path?: string
    duration_seconds?: number
  }
}

export interface AuditSummary {
  total_events: number
  tail_seq: number
  by_outcome: Record<string, number>
  by_action: Record<string, number>
  distinct_correlation_ids: number
  generated_at: number
}

export interface AuditEventsQuery {
  correlation_id?: string
  principal?: string
  action?: string
  outcome?: string
  /** Unix 秒 */
  since?: number
  until?: number
  event_type?: string
  limit?: number
  reverse?: boolean
}

export function fetchAuditEvents(query: AuditEventsQuery = {}): Promise<AuditEventsResponse> {
  const params = new URLSearchParams()
  if (query.correlation_id) params.set('correlation_id', query.correlation_id)
  if (query.principal) params.set('principal', query.principal)
  if (query.action) params.set('action', query.action)
  if (query.outcome) params.set('outcome', query.outcome)
  if (query.since !== undefined) params.set('since', String(query.since))
  if (query.until !== undefined) params.set('until', String(query.until))
  if (query.event_type) params.set('event_type', query.event_type)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.reverse !== undefined) params.set('reverse', String(query.reverse))
  const qs = params.toString()
  return apiFetch<AuditEventsResponse>(`/v1/audit/events${qs ? `?${qs}` : ''}`)
}

export function fetchAuditEventBySeq(seq: number): Promise<{ event: AuditEvent; duplicate_seq: boolean | null }> {
  return apiFetch<{ event: AuditEvent; duplicate_seq: boolean | null }>(
    `/v1/audit/events/${encodeURIComponent(String(seq))}`,
  )
}

/** 跑**真实**的链校验。后端只 SELECT，从不修复；ok=false 就是被改过。 */
export function verifyAuditChain(): Promise<AuditVerifyResult> {
  return apiFetch<AuditVerifyResult>('/v1/audit/verify')
}

export function fetchAuditSummary(): Promise<AuditSummary> {
  return apiFetch<AuditSummary>('/v1/audit/summary')
}

// ─── 工作区文件（只读可观测面） ──────────────────────────────
//
// 端点与 `src/gateway/files.py` 严格对齐。这一层**只有读**：没有写入 / 删除 /
// 上传能力 —— 写仍由执行内核的 `file_write` 工具负责，经执行围栏约束。一个能直接
// 落盘、不经审计的工作区侧门等于把 AI 员工的所有产物置于人类随心篡改之下。
// `listFiles` 返回 `count=0` 就是真空目录；`readFile` 在后端返回 404/413/415 时
// 原样抛错，前端必须如实展示，不能退化成"空内容"。

export interface FileEntry {
  name: string
  /** 相对工作区的路径（始终以 / 开头，或直接是文件名） */
  rel_path: string
  type: 'dir' | 'file'
  /** 目录为 null */
  size: number | null
  /** Unix 秒 */
  mtime: number
}

export interface FileListResponse {
  root: string
  path: string
  count: number
  entries: FileEntry[]
}

export interface FileContentResponse {
  path: string
  size: number
  encoding: string
  content: string
}

export function listFiles(path: string = ''): Promise<FileListResponse> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : ''
  return apiFetch<FileListResponse>(`/v1/files${qs}`)
}

export function readFile(path: string): Promise<FileContentResponse> {
  return apiFetch<FileContentResponse>(`/v1/files/content?path=${encodeURIComponent(path)}`)
}

// ─── 目标与任务（只读可观测面） ──────────────────────────────
//
// 端点与 `src/gateway/ai_management.py` 严格对齐（`GET /v1/goals`、
// `GET /v1/goals/{goal_id}`）。这一层**只有读**：没有任何写入 / 创建 / 中止 /
// 重规划能力 —— 写仍由本文件里既有的 `createGoal` / `stopGoal` / `replanGoal`
// 主权动作完成，这里只把 AI 员工真实执行过的目标与任务分解摊开给人看。
//
// 诚实优先：后端对不存在的 goal_id 返回真实的 404，前端原样抛错并展示
// "目标不存在或已被清除"，不退化成空列表；401 由 apiFetch 统一抛出并清会话。

export interface GoalSummary {
  goal_id: string
  state: string
  natural_language: string
  scope: string
  created_at: number | null
  error: string | null
  replan_suggested: boolean
  task_count: number
  completed_tasks: number
  failed_tasks: number
}

export interface GoalTask {
  id: string
  name: string
  description: string | null
  status: string
  capability_id: string | null
  assigned_agent: string | null
  result: string | null
  error: string | null
  started_at: string | null
  completed_at: string | null
}

export interface GoalDetail {
  goal_id: string
  state: string
  natural_language: string
  scope: string
  error: string | null
  correlation_id: string | null
  created_at: number | null
  evaluation: {
    outcome: string | null
    summary: string
    replan_required: boolean
    replan_triggered: boolean
  } | null
  tasks: GoalTask[]
}

export interface GoalsResponse {
  goals: GoalSummary[]
  count: number
}

/** 列表：随轮询自动刷新。 */
export function listGoals(): Promise<GoalsResponse> {
  return apiFetch<GoalsResponse>('/v1/goals')
}

/** 详情：手动触发（点开某条才取），不轮询。 */
export function getGoal(goalId: string): Promise<GoalDetail> {
  return apiFetch<GoalDetail>(`/v1/goals/${encodeURIComponent(goalId)}`)
}

// ─── 项目（产品面：把目标归类成人类可管理的容器） ───────────────
//
// 端点与 `src/gateway/projects.py` 严格对齐（`GET /v1/projects`、
// `GET /v1/projects/{id}`、`POST /v1/projects`、`DELETE /v1/projects/{id}`）。
// 这是人类主权创建 / 读取 / 删除的真实产物；目标在创建时可挂到某个项目之下
// （`createGoal` 的 `project_id` 字段，见上文），于是"项目"不是装饰，而是真实归类。
//
// 诚实优先：详情端点返回项目下的真实目标摘要，没有目标就 `goal_count=0`、
// `goals=[]`，不编造；删除会一致地把每个关联目标的 `project_id` 清回 None。

export interface ProjectGoalRef {
  goal_id: string
  state: string
  natural_language: string
  created_at: number | null
  task_count: number
  completed_tasks: number
  failed_tasks: number
}

export interface ProjectSummary {
  project_id: string
  name: string
  description: string
  created_at: number | null
  creator: string | null
  goal_ids: string[]
  goal_count: number
}

export interface ProjectDetail extends ProjectSummary {
  goals: ProjectGoalRef[]
}

export interface ProjectsResponse {
  projects: ProjectSummary[]
  count: number
}

export interface CreateProjectBody {
  name: string
  description?: string
}

/** 列表：每 30 秒轮询一次（项目会随目标归类而变化）。 */
export function listProjects(): Promise<ProjectsResponse> {
  return apiFetch<ProjectsResponse>('/v1/projects')
}

/** 详情：手动触发（点开某个项目才取），不轮询。 */
export function getProject(projectId: string): Promise<ProjectDetail> {
  return apiFetch<ProjectDetail>(`/v1/projects/${encodeURIComponent(projectId)}`)
}

export function createProject(body: CreateProjectBody): Promise<ProjectSummary> {
  return apiFetch<ProjectSummary>('/v1/projects', {
    method: 'POST',
    body: JSON.stringify({ name: body.name, description: body.description ?? '' }),
  })
}

export function deleteProject(projectId: string): Promise<{ removed: string; ok: boolean }> {
  return apiFetch<{ removed: string; ok: boolean }>(
    `/v1/projects/${encodeURIComponent(projectId)}`,
    { method: 'DELETE' },
  )
}
