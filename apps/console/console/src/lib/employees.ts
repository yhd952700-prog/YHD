/**
 * 「我的员工」前端契约 —— 全部走 `src/gateway/ai_management.py` 的真实端点。
 *
 *   GET    /v1/employees                  当前账号可见的真实员工（按 owner 过滤）
 *   GET    /v1/employees/capabilities     可选的默认能力清单（只列已注册执行器）
 *   POST   /v1/employees                  创建 { name, role_description, default_capabilities, ... }
 *   PATCH  /v1/employees/{name}           改名 / 改职责 / 改能力 / 停用启用
 *   DELETE /v1/employees/{name}           删除（seed 默认员工 403）
 *   POST   /v1/goals/assign               指名派活 { employee_name 或 agent_id }
 *
 * 不再有任何「前端提案 / 待联调」端点：这里每一个 URL 都能在后端找到实现。
 * 归属语义由后端强制（别人的员工一律 404，停用后指派 409）；前端只负责把
 * `read_only` 如实渲染成只读，不渲染编辑/删除按钮。
 */

import type {
  AgentInfo,
  EmployeeCapabilitiesPayload,
  EmployeeCapability,
  EmployeeSummary,
  EmployeesPayload,
} from './contracts'
import { ApiError, apiFetch } from './api'
import {
  BUDGET_DENIAL_MESSAGE,
  BUDGET_DENIAL_NEXT_STEP,
  PERMISSION_DENIAL_MESSAGE,
} from './errorMessage'
import { capabilityLabel } from './format'

/** 后端 GET /v1/employees 的 employees[] 形状（字段见 contracts.ts）。
 *
 * 状态与"最后一次活动"由后端算好（`status` / `status_label` / `last_activity`），
 * 前端只负责展示，不自己拼内部标识。老后端没有这些字段时按旧逻辑兜底。
 */
export type MyEmployee = EmployeeSummary & {
  display_name?: string
  employee_id?: string
  status?: string
  status_label?: string
  last_activity?: {
    description?: string
    outcome?: string | null
    outcome_label?: string
    at?: string | null
  } | null
}

/** seed 默认员工：网关依赖它，后端拒绝改/删（403）；界面据此不渲染动作。 */
export const SEED_EMPLOYEE_NAME = 'liuhao-default'

/** 只保留数组里像"员工"的条目；缺失字段由展示层兜底。 */
export function employeesOf(payload: EmployeesPayload | null | undefined): MyEmployee[] {
  const list = payload?.employees
  if (!Array.isArray(list)) return []
  return list.map((item) => ({
    name: String(item?.name ?? ''),
    agent_count: Number(item?.agent_count ?? (item?.agents?.length ?? 0)),
    agents: Array.isArray(item?.agents) ? item.agents : [],
    role_description: String(item?.role_description ?? ''),
    default_capabilities: Array.isArray(item?.default_capabilities)
      ? item.default_capabilities
      : [],
    owner_account_id: item?.owner_account_id,
    employment_status: String(item?.employment_status ?? 'active'),
    read_only: Boolean(item?.read_only),
    display_name: (item as MyEmployee)?.display_name,
    employee_id: (item as MyEmployee)?.employee_id,
    status: (item as MyEmployee)?.status,
    status_label: (item as MyEmployee)?.status_label,
    last_activity: (item as MyEmployee)?.last_activity ?? null,
    stats: item?.stats ?? {
      name: String(item?.name ?? ''),
      agent_count: Number(item?.agent_count ?? 0),
      total_tasks_submitted: 0,
      total_tasks_completed: 0,
      total_tasks_failed: 0,
      task_queue_length: 0,
    },
  }))
}

export function employeeStatus(employee: MyEmployee): {
  label: string
  tone: 'accent' | 'ok' | 'warn' | 'danger' | 'muted'
} {
  if (String(employee.employment_status).toLowerCase() === 'disabled') {
    return { label: '已停用', tone: 'muted' }
  }
  // 后端给出真实状态（空闲/执行中/出错/已完成）时优先采用。
  const status = String(employee.status ?? '').toLowerCase()
  if (status === 'idle') return { label: '空闲', tone: 'ok' }
  if (status === 'running') return { label: '执行中', tone: 'accent' }
  if (status === 'error') return { label: '出错', tone: 'danger' }
  if (status === 'completed') return { label: '已完成', tone: 'ok' }
  const agents = employee.agents ?? []
  if (agents.some((agent) => String(agent.status).toLowerCase() === 'running')) {
    return { label: '执行中', tone: 'accent' }
  }
  const queue = Number(employee.stats?.task_queue_length ?? 0)
  if (queue > 0) return { label: '有任务排队', tone: 'warn' }
  if (agents.length > 0) return { label: '空闲', tone: 'ok' }
  return { label: '未指派能力', tone: 'muted' }
}

/** "最后一次做了什么、什么时候"——只展示后端给的人话描述，不含内部标识。 */
export function employeeLastActivity(employee: MyEmployee): string {
  const activity = employee.last_activity
  const description = String(activity?.description ?? '').trim()
  if (!description) return ''
  const label = String(activity?.outcome_label ?? '').trim()
  const at = activity?.at ? new Date(String(activity.at)) : null
  const when =
    at && !Number.isNaN(at.getTime())
      ? at.toLocaleString('zh-CN', { hour12: false })
      : ''
  const parts = [label, description].filter(Boolean)
  const text = parts.join('：')
  return when ? `${text}（${when}）` : text
}

export function employeeRole(employee: MyEmployee): string {
  const role = String(employee.role_description ?? '').trim()
  return role || '未填写职责'
}

/** 默认能力 id → 中文标签；未知 id 由 capabilityLabel 兜底成「其他能力」。 */
export function employeeCapabilities(
  employee: MyEmployee,
  catalog?: Array<Pick<EmployeeCapability, 'id' | 'name'> | { id: string; name?: string }>,
): string {
  const ids = Array.isArray(employee.default_capabilities)
    ? employee.default_capabilities
    : []
  if (ids.length === 0) return '未指定'
  return ids
    .map((id) => {
      const item = catalog?.find((entry) => entry.id === id)
      if (item?.name && /[\u4e00-\u9fa5]/.test(item.name)) return item.name
      return capabilityLabel(id)
    })
    .join('、')
}

/** 能力风险级别 → 人话标签；未知级别如实说「未标注」，不替服务端猜。 */
export function capabilityRisk(risk?: string): {
  label: string
  tone: 'ok' | 'warn' | 'danger' | 'muted'
} | null {
  const level = String(risk ?? '').trim().toUpperCase()
  if (!level) return null
  if (level === 'LOW') return { label: '低风险', tone: 'ok' }
  if (level === 'MEDIUM') return { label: '中风险', tone: 'warn' }
  if (level === 'HIGH') return { label: '高风险', tone: 'danger' }
  if (level === 'CRITICAL') return { label: '极高风险', tone: 'danger' }
  return { label: '风险未标注', tone: 'muted' }
}

/**
 * 一位员工的默认能力 + 各自风险级别，供列表行一眼判断
 * 「它能不能删文件 / 能不能联网」。
 */
export function employeeCapabilityRisks(
  employee: MyEmployee,
  catalog?: Array<Pick<EmployeeCapability, 'id' | 'name' | 'risk'>>,
): string {
  const ids = Array.isArray(employee.default_capabilities)
    ? employee.default_capabilities
    : []
  if (ids.length === 0) return ''
  return ids
    .map((id) => {
      const item = catalog?.find((entry) => entry.id === id)
      const name =
        item?.name && /[\u4e00-\u9fa5]/.test(item.name)
          ? item.name
          : capabilityLabel(id)
      const risk = capabilityRisk(item?.risk)
      return risk ? `${name}（${risk.label}）` : name
    })
    .join('、')
}

/**
 * 能力目录里是否**确实没有**高风险项。
 *
 * 判定由数据算，不写死：目录为空、任一项是 HIGH/CRITICAL、或任一项没有标注
 * 风险级别，都返回 false —— 这些情况下说「目录里没有高风险能力」都没有依据。
 * 将来目录里真的出现高风险能力，界面上那句提示会自动消失。
 */
export function catalogHasNoHighRisk(
  catalog?: Array<Pick<EmployeeCapability, 'risk'>>,
): boolean {
  const items = catalog ?? []
  if (items.length === 0) return false
  return items.every((item) => {
    const level = String(item.risk ?? '').trim().toUpperCase()
    return level === 'LOW' || level === 'MEDIUM'
  })
}

export function employeeAgentId(employee: MyEmployee): string {
  const usable = (employee.agents ?? []).find((agent) => agent.status !== 'paused')
  return String((usable ?? employee.agents?.[0])?.id ?? '')
}

/** 能否接活：在职 + 至少一个未暂停的 agent。 */
export function employeeAssignable(employee: MyEmployee): boolean {
  if (String(employee.employment_status).toLowerCase() === 'disabled') return false
  return (employee.agents ?? []).some((agent) => agent.status !== 'paused')
}

/** seed 默认员工：后端明确拒绝改名/停用/删除，界面不渲染这些按钮。 */
export function isSeedEmployee(employee: MyEmployee): boolean {
  return employee.name === SEED_EMPLOYEE_NAME
}

/** 系统默认员工对外的称呼：`liuhao-default` 是内部标识，不上屏。 */
export const SEED_EMPLOYEE_LABEL = '贾维斯'

/**
 * 员工在界面上显示的名字。
 *
 * 只有系统默认员工会被换成人话称呼 —— 它由服务端内置创建，名字是内部标识；
 * 用户自己创建的员工保留原名，那是用户自己起的，换掉反而认不出。
 */
export function employeeDisplayName(employee: MyEmployee): string {
  if (isSeedEmployee(employee)) return SEED_EMPLOYEE_LABEL
  const display = String(employee.display_name ?? '').trim()
  return display || employee.name
}

export function readableError(error: unknown): string {
  if (error instanceof ApiError) {
    // 员工执行也会触发模型调用：402 是额度不够，不是没权限。
    if (error.code === 'budget_exceeded' || error.status === 402) {
      return `${BUDGET_DENIAL_MESSAGE}${BUDGET_DENIAL_NEXT_STEP}`
    }
    if (error.code === 'permission_denied') return PERMISSION_DENIAL_MESSAGE
    // 能力 id、agent id 是执行器/内部标识：只在能力清单里出现，错误文案里不上屏。
    if (error.code === 'unknown_capability') {
      return '所选默认能力里有未注册项。请刷新能力清单后重新选择，或先不选默认能力。'
    }
    if (
      error.code === 'agent_unavailable' ||
      error.code === 'employee_unavailable'
    ) {
      return '这个员工的执行个体处于暂停状态，无法接受指派。请先恢复后再派活。'
    }
    if (error.code === 'agent_not_found' || error.code === 'employee_not_found') {
      return '员工不存在或不在当前可见范围。请刷新员工列表后重新选择。'
    }
    return error.message
  }
  if (error instanceof Error) return error.message
  return '操作失败，请稍后重试'
}

export async function fetchEmployeeCapabilities(): Promise<EmployeeCapabilitiesPayload> {
  return apiFetch<EmployeeCapabilitiesPayload>('/v1/employees/capabilities')
}

export interface CreateEmployeeInput {
  name: string
  role_description?: string
  default_capabilities?: string[]
  agent_count?: number
  agent_types?: string[]
}

/** 创建成功 → 201 + 员工摘要；同名已存在 → 409。 */
export async function createEmployee(input: CreateEmployeeInput): Promise<MyEmployee> {
  return apiFetch<MyEmployee>('/v1/employees', {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      role_description: input.role_description ?? '',
      default_capabilities: input.default_capabilities ?? [],
      agent_count: input.agent_count ?? 3,
      agent_types: input.agent_types ?? [],
    }),
  })
}

export interface EmployeePatch {
  name?: string
  role_description?: string
  default_capabilities?: string[]
  employment_status?: 'active' | 'disabled'
}

/** 只作用于自己的员工；别人的/不存在 → 404，改名撞名 → 409。 */
export async function updateEmployee(
  name: string,
  patch: EmployeePatch,
): Promise<MyEmployee> {
  const body: Record<string, unknown> = {}
  if (patch.name !== undefined) body.name = patch.name
  if (patch.role_description !== undefined) body.role_description = patch.role_description
  if (patch.default_capabilities !== undefined) {
    body.default_capabilities = patch.default_capabilities
  }
  if (patch.employment_status !== undefined) body.employment_status = patch.employment_status
  return apiFetch<MyEmployee>(`/v1/employees/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export async function deleteEmployee(name: string): Promise<void> {
  await apiFetch<unknown>(`/v1/employees/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export interface AssignEmployeeGoal {
  goal_id?: string
  assigned_employee?: string | null
  assigned_agent?: string | null
  state?: string
}

/**
 * 点名派活：`POST /v1/goals/assign`，执行主体二选一。
 *
 * 传 `employee_name` 时后端取该员工第一个可用 agent；传 `agent_id` 时精确到
 * 个体。两者都不给或都给由后端 400 拒绝，前端这里只序列化实际选择的那一种。
 */
export async function assignEmployeeTask(input: {
  employee_name?: string
  agent_id?: string
  instruction: string
  scope?: string
  project_id?: string | null
  background?: boolean
}): Promise<AssignEmployeeGoal> {
  return apiFetch<AssignEmployeeGoal>('/v1/goals/assign', {
    method: 'POST',
    body: JSON.stringify({
      employee_name: input.employee_name,
      agent_id: input.agent_id,
      instruction: input.instruction,
      scope: input.scope ?? 'L1',
      project_id: input.project_id ?? null,
      background: input.background ?? true,
    }),
  })
}

/** 员工级计数（任务 + 目标 + 队列）：汇总行用这一份真实数字。 */
export function employeeStatsText(employee: MyEmployee): string {
  const stats = employee.stats
  if (!stats) return '还没有执行记录'
  const parts = [
    `提交 ${stats.total_tasks_submitted} 项`,
    `完成 ${stats.total_tasks_completed}`,
    `失败 ${stats.total_tasks_failed}`,
  ]
  if (Number(stats.task_queue_length ?? 0) > 0) {
    parts.push(`排队 ${stats.task_queue_length}`)
  }
  const goals = Number(stats.total_goals_submitted ?? 0)
  if (goals > 0) {
    parts.push(
      `目标 ${stats.total_goals_completed ?? 0}/${goals} 完成`,
    )
  }
  return parts.join(' · ')
}

/** agent 状态到人话（员工页一行里展示每位 agent 的真实状态）。 */
export function agentStatusLabel(agent: AgentInfo): string {
  const raw = String(agent.status ?? '').toLowerCase()
  if (raw === 'running' || raw === 'busy') return '执行中'
  if (raw === 'paused') return '暂停'
  if (raw === 'error' || raw === 'failed') return '异常'
  if (raw === 'idle' || raw === 'ready' || raw === 'available') return '空闲'
  return '状态未知'
}

// ─── 批量创建 ────────────────────────────────────────────────

export interface BatchEmployeeLine {
  name: string
  role: string
  capabilities: string[]
}

export interface ParsedEmployeeBatch {
  items: BatchEmployeeLine[]
  /** 名字为空的行数：如实计数，不静默丢掉。 */
  skipped: number
}

/**
 * 解析批量粘贴：一行一位员工，`名字 | 职责 | 能力,能力`，后两段可省略。
 *
 * 只做切分不做校验 —— 名字是否合法由服务端判定，前端不替它猜。
 */
export function parseEmployeeBatch(text: string): ParsedEmployeeBatch {
  const items: BatchEmployeeLine[] = []
  let skipped = 0
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const [namePart, rolePart, capsPart] = line.split('|')
    const name = (namePart ?? '').trim()
    if (!name) {
      skipped += 1
      continue
    }
    items.push({
      name,
      role: (rolePart ?? '').trim(),
      capabilities: (capsPart ?? '')
        .split(/[,，、\s]+/)
        .map((value) => value.trim())
        .filter(Boolean),
    })
  }
  return { items, skipped }
}

export interface BatchHireFailure {
  name?: string
  code?: string
  message?: string
}

export interface BatchHireResult {
  created: MyEmployee[]
  existing: MyEmployee[]
  failed: BatchHireFailure[]
  created_count: number
  existing_count: number
  failed_count: number
  requested_count: number
}

/** 单批上限与后端一致：超过就得自己分批，否则整单被 422 拒绝。 */
export const BATCH_HIRE_LIMIT = 50

/** 一次提交多位员工；重名不覆盖，由服务端归到 existing。 */
export async function createEmployeesBatch(
  items: BatchEmployeeLine[],
): Promise<BatchHireResult> {
  const payload =
    (await apiFetch<Partial<BatchHireResult>>('/v1/employees/batch', {
      method: 'POST',
      body: JSON.stringify({
        employees: items.map((item) => ({
          name: item.name,
          role_description: item.role,
          default_capabilities: item.capabilities,
          agent_count: 3,
          agent_types: [],
        })),
      }),
    })) ?? {}
  return {
    created: Array.isArray(payload.created) ? payload.created : [],
    existing: Array.isArray(payload.existing) ? payload.existing : [],
    failed: Array.isArray(payload.failed) ? payload.failed : [],
    created_count: Number(payload.created_count ?? 0),
    existing_count: Number(payload.existing_count ?? 0),
    failed_count: Number(payload.failed_count ?? 0),
    requested_count: Number(payload.requested_count ?? items.length),
  }
}

// ─── 员工级记忆（只读） ─────────────────────────────────────

export interface EmployeeMemoryEntry {
  id: string
  text: string
  source?: string
  tags?: string[]
  account_id?: string
  created_at?: string
}

export interface EmployeeMemoriesPayload {
  employee?: { name?: string; display_name?: string }
  owner_account_id?: string
  memories: EmployeeMemoryEntry[]
  count?: number
}

/** 某位员工自己记住的内容；只读，纠错仍回「记忆」总表。 */
export async function fetchEmployeeMemories(
  name: string,
): Promise<EmployeeMemoriesPayload> {
  const payload = await apiFetch<EmployeeMemoriesPayload>(
    `/v1/employees/${encodeURIComponent(name)}/memories`,
  )
  const memories = Array.isArray(payload?.memories) ? payload.memories : []
  return {
    employee: payload?.employee,
    owner_account_id: payload?.owner_account_id,
    memories,
    count: Number(payload?.count ?? memories.length),
  }
}

// ─── 复制 ──────────────────────────────────────────────────────

export interface EmployeeCopyResult {
  created: boolean
  source: string
  name: string
  employment_status: string
  copied: { skills: string[]; capabilities: string[]; prompts: string[] }
  warnings: string[]
  notes: string[]
}

/**
 * 复制员工配置：提示词、agent 类型/数量、默认能力、技能绑定一起带走。
 *
 * 副本由后端强制停用（不会一复制就开跑），任务/记忆/运行历史不复制。
 */
export async function copyEmployee(
  name: string,
  options?: { name?: string; copySkills?: boolean; copyCapabilities?: boolean; copyPrompts?: boolean },
): Promise<EmployeeCopyResult> {
  const body: Record<string, unknown> = {}
  if (options?.name) body.name = options.name
  if (options?.copySkills !== undefined) body.copy_skills = options.copySkills
  if (options?.copyCapabilities !== undefined) body.copy_capabilities = options.copyCapabilities
  if (options?.copyPrompts !== undefined) body.copy_prompts = options.copyPrompts
  return apiFetch<EmployeeCopyResult>(`/v1/employees/${encodeURIComponent(name)}/copy`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ─── 导入 / 导出 ───────────────────────────────────────────────

export interface EmployeeConfigDocument {
  format: string
  format_version: number
  exported_at: string
  source?: { employee_name?: string; owner_account_id?: string }
  employee: Record<string, unknown>
  notes: string[]
}

/** 导出为可读 JSON；后端只给配置白名单字段，不含任何密钥。 */
export async function exportEmployeeConfig(name: string): Promise<EmployeeConfigDocument> {
  return apiFetch<EmployeeConfigDocument>(`/v1/employees/${encodeURIComponent(name)}/export`)
}

export type ImportConflictMode = 'skip' | 'rename'

export interface ImportPlanRow {
  index: number
  requested_name: string
  final_name: string
  action: 'create' | 'rename' | 'skip' | 'error'
  reason: string
  errors: string[]
  warnings: string[]
  will_be_disabled: boolean
}

export interface ImportPlan {
  dry_run: boolean
  on_conflict: string
  plan: ImportPlanRow[]
  summary: { create: number; rename: number; skip: number; error: number }
  notes: string[]
}

export interface ImportApplyResult {
  dry_run: boolean
  results: Array<{ name: string; status: string; message: string; skills?: string[] }>
  created_count: number
  skipped_count: number
  failed_count: number
  notes: string[]
}

function importBody(input: {
  config: unknown
  onConflict?: ImportConflictMode
  asName?: string
  confirm?: boolean
}): string {
  return JSON.stringify({
    config: input.config,
    on_conflict: input.onConflict ?? 'skip',
    as_name: input.asName,
    confirm: input.confirm ?? false,
  })
}

/** dry-run：校验 + 冲突处理，返回"将会做什么"，不落盘。 */
export async function previewEmployeeImport(input: {
  config: unknown
  onConflict?: ImportConflictMode
  asName?: string
}): Promise<ImportPlan> {
  return apiFetch<ImportPlan>('/v1/employees/import/preview', {
    method: 'POST',
    body: importBody(input),
  })
}

/** 确认导入：服务端会重新校验；未带 confirm 一律 400。 */
export async function applyEmployeeImport(input: {
  config: unknown
  onConflict?: ImportConflictMode
  asName?: string
}): Promise<ImportApplyResult> {
  return apiFetch<ImportApplyResult>('/v1/employees/import', {
    method: 'POST',
    body: importBody({ ...input, confirm: true }),
  })
}

// ─── 工作质量评估 ──────────────────────────────────────────────

/** 全部来自台账真实行；算不出的字段是 null，不是 0。 */
export interface EmployeeQuality {
  employee: string
  available: boolean
  source: string
  generated_at: string
  definitions: Record<string, string>
  sample: number
  status_counts: Record<string, number>
  completed: number
  failed: number
  cancelled: number
  in_progress: number
  terminal: number
  completion_rate: number | null
  failure_rate: number | null
  avg_duration_seconds: number | null
  duration_sample: number
  duration_missing: number
  manual_rework_count: number | null
  manual_retry_count: number | null
  takeover_count: number | null
  handed_off_count: number | null
  auto_retry_attempts: number | null
  last_task_at: string | null
  notes: string[]
}

export async function fetchEmployeeQuality(name: string): Promise<EmployeeQuality> {
  return apiFetch<EmployeeQuality>(`/v1/employees/${encodeURIComponent(name)}/quality`)
}

// ─── 预算 ──────────────────────────────────────────────────────

export type BudgetPeriod = 'day' | 'week' | 'month'

export interface EmployeeBudgetPolicy {
  enabled: boolean
  calls_limit: number | null
  tokens_limit: number | null
  cost_limit: number | null
  period: BudgetPeriod
}

export interface EmployeeBudgetView {
  employee: string
  configured?: boolean
  policy: EmployeeBudgetPolicy
  period_label: string
  window: string
  period_start: string
  next_reset_at: string
  usage: {
    calls: number
    tokens: number
    cost: number
    cost_complete: boolean
    cost_unknown_calls: number
    last_source: string
    updated_at: string
  }
  remaining: { calls: number | null; tokens: number | null; cost: number | null }
  exceeded: boolean
  enforced: boolean
  violations: Array<{ kind: string; limit?: number | null; used?: number | null; message: string }>
  notes: string[]
  store_error?: string
}

export interface EmployeeBudgetPatch {
  enabled?: boolean
  /** 传 null 表示取消该项上限。 */
  calls_limit?: number | null
  tokens_limit?: number | null
  cost_limit?: number | null
  period?: BudgetPeriod
  reset_usage?: boolean
}

export async function fetchEmployeeBudget(name: string): Promise<EmployeeBudgetView> {
  return apiFetch<EmployeeBudgetView>(`/v1/employees/${encodeURIComponent(name)}/budget`)
}

export async function saveEmployeeBudget(
  name: string,
  patch: EmployeeBudgetPatch,
): Promise<EmployeeBudgetView> {
  return apiFetch<EmployeeBudgetView>(`/v1/employees/${encodeURIComponent(name)}/budget`, {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

export async function clearEmployeeBudget(name: string): Promise<void> {
  await apiFetch<unknown>(`/v1/employees/${encodeURIComponent(name)}/budget`, {
    method: 'DELETE',
  })
}

/** 百分比文案：上限未设置时返回 null（不显示"0%"假装有额度）。 */
export function budgetPercent(used: number, limit: number | null): number | null {
  if (limit === null || limit <= 0) return null
  return Math.min(100, Math.round((used / limit) * 100))
}

