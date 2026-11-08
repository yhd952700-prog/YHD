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
export const SEED_EMPLOYEE_LABEL = '默认管家'

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
