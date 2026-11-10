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
  /** 后端真实字段：这位员工负责什么（`HireEmployeeRequest.role_description`）。 */
  role_description?: string
  /** 默认能力 id，只能从 `GET /v1/employees/capabilities` 的清单里选。 */
  default_capabilities?: string[]
}

export function hireEmployee(body: HireEmployeeBody): Promise<Json> {
  return apiFetch<Json>('/v1/employees', {
    method: 'POST',
    body: JSON.stringify({
      name: body.name,
      agent_count: body.agent_count ?? 3,
      agent_types: body.agent_types ?? [],
      role_description: body.role_description ?? '',
      default_capabilities: body.default_capabilities ?? [],
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
  /**
   * 指名派活（`GoalCreateRequest.employee_name`）：把整个目标钉给某位员工，
   * 后端解析成该员工的一个真实 agent。不存在/不可见 → 404，停用 → 409。
   * 留空 = 管家自己拆解、自己派活。
   */
  employee_name?: string | null
  /**
   * 幂等键（`GoalCreateRequest.client_request_id`）：同一次「提交目标」重发时保持
   * 不变，服务端在一小时窗口内返回同一个 goal_id。留空时由 `createGoal` 生成一次
   * 性 id —— 否则浏览器/代理层的重投会变成第二条目标。
   */
  client_request_id?: string | null
}

/**
 * 生成一次性的客户端请求 id。
 *
 * 形状与 `chatClient.newSessionId()` 一致（时间戳 base36 + 随机后缀）；后端
 * `_check_client_request_id` 只要求非空且 ≤128 字符。
 */
function newGoalRequestId(): string {
  return `goal-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

export function createGoal(body: CreateGoalBody): Promise<Json> {
  return apiFetch<Json>('/v1/goals', {
    method: 'POST',
    body: JSON.stringify({
      natural_language: body.natural_language,
      scope: body.scope ?? 'L1',
      plan_mode: body.plan_mode ?? 'auto',
      verification_criteria: body.verification_criteria ?? null,
      // 必须显式序列化：后端 `GoalCreateRequest.project_id` 是决定"目标归属哪个
      // 项目"的唯一字段。本字段此前**只声明在类型里、从未进过请求体**——于是哪怕
      // 调用方传了 project_id，也会被静默丢弃、目标落为"独立目标"，而类型检查
      // （tsc）全程是绿的。类型声明 ≠ 已接线；这里补上真正的接线。
      // null = 独立目标（后端语义），与后端 `Optional[str] = None` 一致。
      project_id: body.project_id ?? null,
      employee_name: body.employee_name ?? null,
      background: body.background ?? false,
      // 后端 `GoalCreateRequest.client_request_id` 是短窗口幂等键：同账号 + 同 id
      // 在一小时内返回同一个 goal_id。后端能收、前端从没发过 → 这条能力在控制台
      // 上不可达（与 project_id 同一种缺陷形状），所以这里必须真的序列化出去。
      client_request_id: body.client_request_id ?? newGoalRequestId(),
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

// ─── 单独指派 / 单任务重试 ───────────────────────────────────
//
// 与 `POST /v1/goals` 的关键差别：执行主体由调用方点名。不存在（404
// `agent_not_found`）或暂停（409 `agent_unavailable`）都必须原样报错，
// 绝不静默换成别的员工 —— 所以这里不做任何兜底重试。

export interface AssignTaskBody {
  /** 执行主体二选一：员工名（取其第一个可用 agent）。 */
  employee_name?: string
  /** 执行主体二选一：精确到某个 agent。 */
  agent_id?: string
  instruction: string
  scope?: string
  project_id?: string | null
  background?: boolean
}

export function assignTask(body: AssignTaskBody): Promise<Json> {
  return apiFetch<Json>('/v1/goals/assign', {
    method: 'POST',
    body: JSON.stringify({
      employee_name: body.employee_name,
      agent_id: body.agent_id,
      instruction: body.instruction,
      scope: body.scope ?? 'L1',
      project_id: body.project_id ?? null,
      background: body.background ?? true,
    }),
  })
}

/**
 * 只重跑一个失败任务。后端这个调用是**同步**的：请求返回时该任务已经跑完，
 * 其余任务的结果不变。409 `goal_running` / `task_not_retryable` 是两种不同
 * 拒绝，调用方必须分别提示。
 */
export function retryGoalTask(goalId: string, taskId: string): Promise<Json> {
  return apiFetch<Json>(
    `/v1/goals/${encodeURIComponent(goalId)}/tasks/${encodeURIComponent(taskId)}/retry`,
    { method: 'POST' },
  )
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
  /** "对什么做的"（details.target 的顶层投影） */
  target?: string | null
  /** 后端拼好的一句人话标题（谁 · 做了什么 · 对谁 · 结果） */
  display_title?: string
  /** 结果的中文标签 */
  status_label?: string
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

export interface AuditRetention {
  enabled: boolean
  /** 按时间保留的天数：超过就会被裁剪 */
  days: number
  max_rows: number
  max_bytes: number
  min_keep: number
  interval: number
  max_delete: number
}

export interface AuditChainKey {
  status: 'available' | 'missing' | 'unreadable' | string
  mode?: 'on' | 'off' | string
  /** 库里有带密钥（HMAC）事件的条数 */
  keyed_events: number
  /** true = 缺密钥，只能做结构检查 */
  degraded: boolean
  /** true = 旧库升级后的混合链（部分可证明） */
  partial?: boolean
  message?: string | null
  partial_message?: string | null
}

export interface AuditVerifyResult {
  /** 链完好为 true；被篡改 / 被截断 / 被分叉为 false */
  ok: boolean
  /** 第三种状态：缺校验密钥，只能结构检查（既不是完好，也不是被篡改） */
  degraded?: boolean
  /** 第四种状态：旧库升级后的混合链，尾巴可证明、前缀只能结构检查 */
  partial?: boolean
  message?: string | null
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
    verification_mode?: 'full' | 'incremental' | 'structural_only' | string
    degraded?: boolean
    degraded_reason?: string | null
    chain_key_status?: string
    keyed_events?: number
    structural_ok?: boolean
    /** 降级为无密钥校验时，这份无密钥链自身的校验结果（不能替代密钥证明） */
    unkeyed_integrity_ok?: boolean
    message?: string | null
    chain_key?: AuditChainKey
    retention?: AuditRetention
  }
}

export interface AuditSummary {
  /** 当前账号子树可读的事件数（本页"你能看到"口径）。 */
  total_events: number
  /** 全库存储事件数，与 /v1/ready、/v1/dashboard/summary 同源。 */
  store_total_events?: number
  /** 全库业务事件（已扣除 state_change 噪声）。 */
  business_events?: number
  noise_events?: number
  visible_events?: number
  definition?: Record<string, string>
  tail_seq: number
  by_outcome: Record<string, number>
  by_action: Record<string, number>
  distinct_correlation_ids: number
  generated_at: number
  retention?: AuditRetention
  chain_key?: AuditChainKey
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

/**
 * 跑**真实**的链校验。后端只 SELECT，从不修复；ok=false 就是被改过。
 *
 * 手动检查默认 ``full=true``：增量校验只看"上次校验点之后"，无法发现更早的
 * 历史行被改。慢一点，但用户点这个按钮要的是可信结论。
 */
export function verifyAuditChain(full = true): Promise<AuditVerifyResult> {
  return apiFetch<AuditVerifyResult>(`/v1/audit/verify?full=${full ? 'true' : 'false'}`)
}

export function fetchAuditSummary(): Promise<AuditSummary> {
  return apiFetch<AuditSummary>('/v1/audit/summary')
}

export interface AuditArchive {
  schema: number
  exported_at: number
  since_seq: number
  limit: number
  count: number
  truncated: boolean
  next_since_seq: number
  events: AuditEvent[]
  integrity: AuditVerifyResult
  retention?: AuditRetention
  notice: string
}

/**
 * 导出审计记录归档（仅根账号）。归档是给人留存/比对的副本，**不含校验密钥**；
 * 换机后没有密钥时只能查阅，不能替代原机上的带密钥校验。
 */
export function exportAuditArchive(
  sinceSeq = 0,
  limit = 20000,
): Promise<AuditArchive> {
  const params = new URLSearchParams({
    since_seq: String(sinceSeq),
    limit: String(limit),
  })
  return apiFetch<AuditArchive>(`/v1/audit/export?${params.toString()}`)
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
  /** 后端保留的原始失败原因，折叠展示；主位置只显示中文结论。 */
  technical_detail?: string | null
  replan_suggested: boolean
  task_count: number
  completed_tasks: number
  failed_tasks: number
  /** 后端 list_goals 已返回真实产物路径；空数组 = 这次没有写出文件。 */
  artifacts?: string[]
  /** 员工归属：列表里就能看出"这单是谁做的"（后端 list_goals 已返回）。 */
  assigned_employee?: string | null
  assigned_employees?: string[]
  employees?: EmployeeRollup[]
}

/** 后端 `_attach_employee_attribution` 的 goal 级汇总（每人一行）。 */
export interface EmployeeRollup {
  employee: string | null
  tasks: number
  completed: number
  failed: number
  total_duration_ms: number
}

export interface GoalTask {
  id: string
  name: string
  description: string | null
  status: string
  capability_id: string | null
  assigned_agent: string | null
  /** agent id → 真实员工名；解析不到时为 null（不假装是某人）。 */
  assigned_employee?: string | null
  /** 内核时间戳算出的真实耗时；拿不到为 null（不估算）。 */
  duration_ms?: number | null
  /** 后端判据：completed / completed_unattributed → true。 */
  success?: boolean
  /** 该任务真的写出的文件路径（工作区相对路径）；没有产物时为空数组。 */
  artifacts?: string[]
  /** 验证结论：success / partial / failed / unverified / …；没有验证时为 null。 */
  verification?: string | null
  /** 验证得分 0.0–1.0；unverified 固定为 0.0。 */
  score?: number | null
  result: string | null
  error: string | null
  /** 后端保留的原始失败原因（英文分类、任务 ID、依赖 ID），折叠展示。 */
  technical_detail?: string | null
  started_at: string | null
  completed_at: string | null
}

export interface GoalDetail {
  goal_id: string
  state: string
  natural_language: string
  scope: string
  error: string | null
  /** 后端保留的原始失败原因，折叠展示；主位置只显示中文结论。 */
  technical_detail?: string | null
  correlation_id: string | null
  created_at: number | null
  /**
   * 归属的项目 id；null = 独立目标（不属于任何项目）。后端 `GET /v1/goals/{id}`
   * 恒返回该键（`_run_bound_goal` 两条分支都写入）。设为可选是为了对**早于本字段
   * 存在的历史目标记录** fail-safe：缺失时界面显示「未记录归属」，不假装成 null。
   */
  project_id?: string | null
  /**
   * 单独指派入口的执行主体（agent id）。`POST /v1/goals/assign` 与后台执行
   * 都会写入；`POST /v1/goals` 的管家自动拆解目标为 null/缺失。
   * 界面据此区分「你点名派给某人」与「管家自己拆解」。
   */
  assigned_agent?: string | null
  /** 指名派活时钉住的那位员工（`POST /v1/goals` 的 employee_name / assign）。 */
  assigned_employee?: string | null
  /** 不是单人指派时，任务里真实出现过的员工集合。 */
  assigned_employees?: string[]
  /** 每人一行：做了几项、成几项、败几项、合计耗时。 */
  employees?: EmployeeRollup[]
  /** 真实工作区产物（相对路径）。空数组 = 该目标没有向工作区写入任何文件。 */
  artifacts: string[]
  /**
   * 本次计划由谁拆解：`model` / `model_retry` / `keyword`。
   * 早于该字段的历史记录会缺失；读侧必须显示「未知」，不能默认成 `model`。
   */
  decomposition_source?: string | null
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

/** 项目里真实写出的产物：路径 + 归属账号（后端 `_outputs_for_goals`）。 */
export interface ProjectOutput {
  path: string
  owner: string
  goal_id: string | null
}

export interface ProjectSummary {
  project_id: string
  name: string
  description: string
  created_at: number | null
  updated_at?: number | null
  archived_at: number | null
  creator: string | null
  owner_account_id?: string
  goal_ids: string[]
  goal_count: number
  /** 项目自身更新时间 ∪ 其下目标创建/更新时间的最晚值。 */
  last_activity_at: number | null
  output_count: number
  outputs: ProjectOutput[]
  outputs_truncated: boolean
}

export interface ProjectDetail extends ProjectSummary {
  goals: ProjectGoalRef[]
}

export interface ProjectsResponse {
  projects: ProjectSummary[]
  count: number
  /** 主账号切视角时回显被查看的账号；未切换为 null。 */
  view_account: string | null
}

export interface CreateProjectBody {
  name: string
  description?: string
}

export interface ListProjectsQuery {
  includeArchived?: boolean
  /** 仅主账号有效；成员传他人视角会被后端中性 404。 */
  viewAccount?: string | null
}

/** 列表：每 30 秒轮询一次（项目会随目标归类而变化）。 */
export function listProjects(query: ListProjectsQuery = {}): Promise<ProjectsResponse> {
  const params = new URLSearchParams()
  if (query.includeArchived) params.set('include_archived', 'true')
  if (query.viewAccount) params.set('view_account', query.viewAccount)
  const qs = params.toString()
  return apiFetch<ProjectsResponse>(`/v1/projects${qs ? `?${qs}` : ''}`)
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

/** 归档：仅从默认列表隐藏，数据与产出都保留；可恢复。 */
export function archiveProject(projectId: string): Promise<{
  project_id: string
  archived_at: number | null
  ok: boolean
}> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/archive`, {
    method: 'POST',
  })
}

/** 恢复归档项目。 */
export function restoreProject(projectId: string): Promise<{
  project_id: string
  archived_at: null
  ok: boolean
}> {
  return apiFetch(`/v1/projects/${encodeURIComponent(projectId)}/restore`, {
    method: 'POST',
  })
}

/**
 * 删除项目。有目标关联时后端先返回 409 `project_has_goals`（带影响面），
 * 调用方把 message 展示给用户，用户确认后再以 `force=true` 重发 —— 删除
 * 只解除归属，绝不删除目标本身。
 */
export function deleteProject(
  projectId: string,
  force = false,
): Promise<{ removed: string; ok: boolean; goal_count?: number }> {
  const qs = force ? '?force=true' : ''
  return apiFetch<{ removed: string; ok: boolean; goal_count?: number }>(
    `/v1/projects/${encodeURIComponent(projectId)}${qs}`,
    { method: 'DELETE' },
  )
}

// ─── 应用（产品面：把"插件"真实呈现给人类，并允许激活） ───────────────
//
// 端点与 `src/gateway/plugins.py` 严格对齐（`GET /v1/plugins`、
// `POST /v1/plugins/{id}/activate`）。这是内核层**真实存在**的插件注册项，
// 不是装饰：列表返回真实注册表（id/name/version/status/active），激活端点
// 真正加载插件并标记 ACTIVE（见后端 `load_plugin`）。诚实优先：未知 id 由
// 后端返回 404，激活失败会带真实 error，前端原样展示，不假装成功。

export interface AppSummary {
  plugin_id: string
  name: string
  version: string
  kernel_type: string
  status: string
  active: boolean
  activatable?: boolean
  builtin?: boolean
  capabilities: string[]
  scope: string
  error: string | null
}

export interface AppsResponse {
  plugins: AppSummary[]
  total: number
  active: number
  /** 历史登记但无模块入口、不可激活而被略过的条数（后端如实返回）。 */
  hidden?: number
}

/** 列表：每 30 秒轮询一次（激活态会变化）。 */
export function listApps(): Promise<AppsResponse> {
  return apiFetch<AppsResponse>('/v1/plugins')
}

/** 激活：人类主权动作（走带会话的 apiFetch，401 由它统一抛出并清会话）。 */
export function activateApp(pluginId: string): Promise<AppSummary> {
  return apiFetch<AppSummary>(
    `/v1/plugins/${encodeURIComponent(pluginId)}/activate`,
    { method: 'POST' },
  )
}

// ─── 事件流（只读可观测面） ─────────────────────────────────
//
// 端点与 `src/gateway/events.py` 严格对齐（`GET /v1/events`）。后端直接复用事件内核
// 的 `get_event_history`——内核已保真记录总线上的真实事件，这里只把真实发生的近期
// 事件摊开给人看。
//
// 诚实优先：后端返回的每一条都来自内核总线真实发生过的事件；真空就是真空，前端如实
// 展示"内核总线暂无事件"，不编造占位事件来假装系统正在活动。

export interface EventSummary {
  /** 事件关联 id（内核 Event.correlation_id） */
  id: string
  type: string
  source: string
  /** ISO 8601 时间戳 */
  timestamp: string
  scope: string
  priority: string
  /** 人类可读的载荷摘要（绝不声称更多信息） */
  summary: string
  /** 原始载荷（JSON 安全） */
  data: Record<string, unknown> | null
}

export interface EventsResponse {
  events: EventSummary[]
  count: number
  limit: number
  /** 缓冲区内还有更早的事件 */
  truncated: boolean
  /** 数据来源（诚实标注） */
  source: string
}

/** 近期真实事件；limit 缺省时后端取最近 50 条。 */
export function getEvents(limit?: number): Promise<EventsResponse> {
  const qs = limit !== undefined ? `?limit=${encodeURIComponent(String(limit))}` : ''
  return apiFetch<EventsResponse>(`/v1/events${qs}`)
}

// ─── 信任内核（只读可观测面） ─────────────────────────────────
//
// 端点与 `src/gateway/trust.py` 严格对齐（`GET /v1/trust/{entity_id}`）。后端直接复用
// 信任内核进程单例的真实状态——撤销标记、各作用域信任分、自信任链探针、管理器统计。
//
// 诚实优先：后端绝不编造分数；某作用域没有分数就直接省略，前端如实展示"该实体暂无信任分"；
// 401 由 apiFetch 统一抛出并清会话；其他错误原样展示，不退化成"暂无数据"。

export interface TrustScoreEntry {
  /** 信任等级（untrusted / low / medium / high / very_high） */
  level: string
  /** 0.0 - 1.0 */
  score: number
  /** ISO 8601 有效期上限；null 表示无过期 */
  expires_at: string | null
  /** 窗口是否已关闭 */
  is_expired: boolean
}

export interface TrustChainLinkEntry {
  from_entity: string
  to_entity: string
  trust_score: number
  scope: string
  active: boolean
  expires_at: string | null
  is_expired: boolean
}

export interface TrustChainSummary {
  source: string
  target: string
  composite_score: number
  scope: string
  valid: boolean
  computed_at: string
  links: TrustChainLinkEntry[]
}

export interface TrustSummary {
  /** 被查询的实体 id（原样回显） */
  entity_id: string
  /** 是否在信任撤销注册表中 */
  revoked: boolean
  /** scope -> 真实信任分；没有分数的 scope 不会出现 */
  scores: Record<string, TrustScoreEntry>
  /** 自信任链探针的真实结果；恒定存在（含自环） */
  chain_summary: TrustChainSummary | null
  /** 管理器统计 */
  stats: Record<string, unknown>
}

/** 读取某实体的真实信任状态；entityId 经 encodeURIComponent，避免把特殊字符当路径分叉。 */
export function getTrust(entityId: string): Promise<TrustSummary> {
  return apiFetch<TrustSummary>(`/v1/trust/${encodeURIComponent(entityId)}`)
}

// ─── 网络内核（只读可观测面） ─────────────────────────────────
//
// 端点与 `src/gateway/networks.py` 严格对齐（`GET /v1/network/messages`）。后端直接复用
// 网络内核进程单例的真实总线状态——stats() 统计与 get_message_history() 消息历史。
//
// 诚实优先：后端返回的 history 是总线真实发生过的消息（序列化为 to_dict）；全新进程里
// 总线没有任何消息时 history 为空数组，前端如实展示"总线暂无消息"，不编造占位消息。
// 严格只读：没有发送 / 写入入口（真实外部 send 是安全风险且无真实路径）。401 由 apiFetch
// 统一抛出并清会话；其他错误原样展示，不退化成"暂无数据"。

export interface NetworkMessageEntry {
  /** 消息 id（内核 Message.id） */
  id: string
  /** 消息类型 / 类别 */
  type: string
  /** 来源 */
  source: string
  /** 目标 */
  destination: string
  /** 实际使用的协议（route 后可能被改写） */
  protocol: string
  /** 关联 id */
  correlation_id: string
  /** 因果 id */
  causation_id: string
  /** 优先级 */
  priority: string
  /** 投递状态（pending/sent/delivered/failed/...） */
  status: string
  /** ISO 8601 创建时间；null 不出现 */
  created_at: string | null
  /** 其余字段（headers / metadata / 时间等）原样保留 */
  [key: string]: unknown
}

export interface NetworkMessagesResponse {
  /** bus.stats() 的真实统计 */
  stats: Record<string, unknown>
  /** 总线真实消息历史（最近 10000 条）；真空时为 [] */
  history: NetworkMessageEntry[]
}

/** 读取网络总线真实状态；严格只读，无任何写 / 发送入口。 */
export function getNetworkMessages(): Promise<NetworkMessagesResponse> {
  return apiFetch<NetworkMessagesResponse>('/v1/network/messages')
}

// ─── 身份与权限（只读可观测面） ───────────────────────────────
//
// 端点与 `src/gateway/identity.py` 严格对齐（**全部 GET**、全部 `require_human_principal`
// 闸门）：
//   GET /v1/identity/principals         主体清单（含注册表诚实度）
//   GET /v1/identity/principals/{id}    单个主体（未知 -> 404，绝不返回空对象）
//   GET /v1/identity/permissions        扁平 grant 清单（派生字段带 derived 标签）
//   GET /v1/identity/summary            按类型 / 状态 / 作用域上限计数 + 注册表诚实度
//
// 这一层**只有读**：身份内核把权限存成扁平 `Set[str]`，**不保留**每条 grant 的 scope，
// 所以响应里的 `scope` 永远是身份的作用域上限（scope_ceiling），不是单条授权的 scope；
// 凡是展示层派生出来的 `derived_action` / `derived_resource` 都带 `derived: true` 标签，
// 原始 `permission` 串永远是权威。后端对未知 id 返回**真实 404**，前端原样抛错并展示
// "主体不存在或已被清除"，不退化成空对象；`registry.rows_refused` / `warnings` 若非空
// 必须原样摊开（fail-closed 的诚实度 —— "零个人类"可能是注册表拒绝的结果，不是没有人类）。

/** 注册表自身真相（与 `_registry_report` 一一对应）。 */
export interface IdentityRegistry {
  backend: string | null
  location: string | null
  configured: boolean
  integrity_enforced: boolean
  integrity_state: string | null
  integrity_key_env: string
  rows_admitted: number | null
  /** fail-closed 时被拒收的行（真实内容，必须摊开给人看） */
  rows_refused: string[]
  rows_offered: number | null
}

/** 单个主体的真相视图（`_identity_view`，字段全部来自 `AgentIdentity`）。 */
export interface PrincipalView {
  id: string
  principal: string
  /** 内核 namespace 判定：human / agent / service */
  kind: string
  namespace: string
  /** kind 是内核显式标注的还是推断的（缺失 metadata["kind"] 时推断为 agent） */
  kind_marked: boolean
  status: string
  active: boolean
  /** 作用域上限 L0-L7，不是单条授权的 scope */
  scope: string
  trust_score: number | null
  permission_count: number
  fingerprint: string | null
  created_at: string | null
  last_modified: string | null
  display_name: string | null
}

export interface PrincipalsResponse {
  principals: PrincipalView[]
  count: number
  total: number
  truncated: boolean
  by_kind: Record<string, number>
  filters: Record<string, unknown>
  registry: IdentityRegistry
  warnings: string[]
  source: Record<string, unknown>
  read_only: boolean
}

/** 一条扁平 grant（谁 -> 被授予了什么 -> 受哪个作用域上限约束）。 */
export interface PermissionGrant {
  principal_id: string
  principal: string
  kind: string
  /** 权限原始串，权威字段 */
  permission: string
  /** 展示层派生（从 permission 按 ":" 拆分），非内核事实 */
  derived_action: string | null
  derived_resource: string | null
  derived: boolean
  scope: string
  identity_active: boolean
}

export interface PermissionsResponse {
  grants: PermissionGrant[]
  count: number
  truncated: boolean
  filters: Record<string, unknown>
  /** 两条「有意不说」的诚实声明（scope / 派生） */
  notes: string[]
  registry: IdentityRegistry
  warnings: string[]
  source: Record<string, unknown>
  read_only: boolean
}

export interface IdentitySummary {
  total_principals: number
  by_kind: Record<string, number>
  by_status: Record<string, number>
  by_scope: Record<string, number>
  total_grants: number
  individual_principals: Record<string, unknown>
  scope_note: string
  registry: IdentityRegistry
  warnings: string[]
  source: Record<string, unknown>
  read_only: boolean
}

export interface PrincipalDetail {
  principal: PrincipalView
  permissions: string[]
  grants: PermissionGrant[]
  permission_count: number
  scope_note: string
  derivation_note: string
  registry: IdentityRegistry
  warnings: string[]
  source: Record<string, unknown>
  read_only: boolean
}

/** 主体清单：随轮询自动刷新（身份会被创建 / 停用，状态会变）。 */
export function listPrincipals(): Promise<PrincipalsResponse> {
  return apiFetch<PrincipalsResponse>('/v1/identity/principals')
}

/** 单个主体：手动触发（输入 id / principal 才取），不轮询。未知 id 后端返回真实 404。 */
export function getPrincipal(id: string): Promise<PrincipalDetail> {
  return apiFetch<PrincipalDetail>(`/v1/identity/principals/${encodeURIComponent(id)}`)
}

/** 扁平权限清单：随轮询自动刷新（grant 会随授权变化）。 */
export function listPermissions(): Promise<PermissionsResponse> {
  return apiFetch<PermissionsResponse>('/v1/identity/permissions')
}

/** 身份总览：随轮询自动刷新。 */
export function getIdentitySummary(): Promise<IdentitySummary> {
  return apiFetch<IdentitySummary>('/v1/identity/summary')
}

// ─── 待审批队列（策略 defer 的真实拦截记录） ──────────────────
//
// 端点与 `src/gateway/policy.py` 严格对齐：
//   * GET  /v1/policy/approvals/pending —— 只对**能签发凭据**的主体可见；
//     非管理员是 403（不是空列表），跨主体不泄露；
//   * POST /v1/policy/approvals —— 签发凭据。请求体原样来自队列的
//     `approve.body`（页面提供「采用此请求体批准」）。
//
// 后端模板里的 `target` 是 `"*"`（审计事件没有记录具体目标）。签发通配授权
// 必须显式 `confirm_wildcard: true`，否则 400；页面据此要求一次明确勾选，
// 不把「覆盖所有目标」藏进一次无意识的点击。

export interface PendingApprovalItem {
  pending_id: string
  action: string
  risk_level: string
  /**
   * 这条动作是从哪触发的：`local`（电脑本机）/ `lan`（同一 Wi-Fi 的手机）。
   * 旧版本后端没有这个字段，按本机处理。
   */
  source_channel?: 'local' | 'lan'
  source_device?: string | null
  /**
   * 手机端能否直接批准。HIGH/CRITICAL（以及判定不出的档）为 false：
   * 批准必须回电脑本机，手机只能拒绝。旧版本后端没发这个字段时按 false 处理，
   * 宁可让用户回电脑，也不在手机上放行高危动作。
   */
  remote_approvable?: boolean
  policy_rule: string | null
  correlation_id: string | null
  /** 审计事件时间戳（Unix 秒） */
  blocked_at: number | string | null
  blocked_by: string | null
  reason: string
  summary: string
  approve: {
    method: string
    path: string
    body: Json
  }
}

export interface PendingApprovalsResponse {
  count: number
  items: PendingApprovalItem[]
  as_of: number | null
  lookback_seconds: number
  enforcement: Record<string, unknown>
}

/** 待审批队列：随轮询自动刷新（被拦动作会随执行出现）。 */
export function listPendingApprovals(): Promise<PendingApprovalsResponse> {
  return apiFetch<PendingApprovalsResponse>('/v1/policy/approvals/pending')
}

/**
 * 采用队列返回的请求体签发凭据。
 *
 * 调用方负责把 `approve.body` 原样递进来（页面只允许追加
 * `confirm_wildcard` 这一项显式确认）。403 = 没有签发权限，不是队列为空。
 */
export function approvePending(body: Json): Promise<Json> {
  return apiFetch<Json>('/v1/policy/approvals', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * 明确拒绝一条待确认动作。`reason` 可留空，但字段必传（后端写进审计）。
 *
 * 拒绝只做两件事：写一条拒绝审计、让卡片退出等待队列。它不签发凭据、
 * 不执行、也不重试原动作——拒绝之后同一请求仍然是被拦状态。
 * 403 = 没有审批权限；404 = 卡片已经处理过。
 */
export function rejectPending(pendingId: string, reason: string): Promise<Json> {
  return apiFetch<Json>('/v1/policy/approvals/reject', {
    method: 'POST',
    body: JSON.stringify({ pending_id: pendingId, reason }),
  })
}
