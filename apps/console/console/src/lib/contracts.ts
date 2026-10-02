/**
 * 后端响应契约 —— 与 `src/gateway/*.py` 的真实返回结构一一对应。
 *
 * 字段名**必须**与后端一致（后端不做驼峰转换）。这里只声明驾驶舱实际读取的
 * 字段，全部为可选或允许 null：后端在数据源不可用时会降级返回带 `error` 的
 * 空壳，前端必须能承受，而不是假设形状永远完整。
 */

export interface ProviderInfo {
  type?: string
  model?: string | null
  name?: string | null
  error?: string
}

export interface SessionDetail {
  id: string
  turns: number
}

export interface DashboardSummary {
  status?: string
  uptime_seconds?: number
  provider?: ProviderInfo
  timestamp?: number
  sessions?: {
    count?: number
    ids?: string[]
    detail?: SessionDetail[]
    error?: string
  }
  audit?: {
    total_events?: number
    breakdown?: Record<string, number>
    db_path?: string
    error?: string
  }
}

export interface ActivityItem {
  type: string
  outcome: string
  principal: string
  timestamp: number
  correlation_id?: string
}

export interface ActivityPayload {
  activity?: ActivityItem[]
  count?: number
  limit?: number
}

export interface RegistryKernel {
  id: string
  legacy_id?: string | null
  name: string
  kernel: string
  kind?: string | null
  status?: string | null
  scope?: string | null
  source?: string | null
  /** 展示分组（后端按 kernel 归域，不是注册表原始字段） */
  domain: string
  domain_label: string
}

export interface RegistryLayer {
  id: string
  name: string
  module?: string | null
  phase?: string | null
  status?: string | null
  tests: number
  band: string
  band_label: string
}

export interface ProviderItem {
  type: string
  label: string
  registered: boolean
  configured: boolean
  active: boolean
  required_env: string[]
}

export interface RosterPayload {
  generated_at?: number
  source?: string
  available: boolean
  error?: string | null
  version?: string
  declared_totals?: { capabilities?: number; layers?: number }
  totals: {
    kernels: number
    layers: number
    employees: number
    declared_test_cases: number
  }
  kernels: RegistryKernel[]
  layers: RegistryLayer[]
  domains: { key: string; label: string }[]
  bands: { key: string; label: string }[]
  providers: {
    registered_count: number
    configured_count: number
    active: ProviderInfo
    items: ProviderItem[]
    error?: string
  }
}

export interface TimeseriesPoint {
  date: string
  total: number
  allowed: number
  denied: number
  noise: number
}

export interface BreakdownItem {
  type: string
  outcome: string
  count: number
}

export interface AnalyticsPayload {
  generated_at?: number
  timeseries: {
    available: boolean
    error?: string
    days: TimeseriesPoint[]
    sampled_events?: number
    truncated?: boolean
  }
  breakdown: {
    available: boolean
    error?: string
    items: BreakdownItem[]
    excluded_noise_events?: number
    total_events?: number
  }
}

export interface HealthCheckEntry {
  status?: string
  error?: string
  [key: string]: unknown
}

export interface HealthPayload {
  status?: string
  checks?: Record<string, HealthCheckEntry>
  errors?: string[]
  timestamp?: number
}

export interface ReadyPayload {
  status?: string
}

export interface EnforcementPayload {
  enabled?: boolean
  env_var?: string
  intercepting?: boolean
  enforced_tiers?: string[]
  [key: string]: unknown
}

/** 审批凭据统计（审批中心用到的两个数字，这里只声明读到的部分）。 */
export interface ApprovalSummary {
  count: number
  grants: { id: string; remaining_seconds?: number; is_active?: boolean }[]
}

// ─── AI Employee / Goal / Workflow 契约 ─────────────────────

export interface AgentInfo {
  id: string
  agent_type: string
  name: string
  status: string
  current_task: string | null
  completed_tasks: number
  failed_tasks: number
  total_latency_ms: number
}

export interface EmployeeStats {
  name: string
  agent_count: number
  total_tasks_submitted: number
  total_tasks_completed: number
  total_tasks_failed: number
  task_queue_length: number
}

/** 按名员工名册中的单条摘要（与后端 GET /v1/employees 的 employees[] 对应）。 */
export interface EmployeeSummary {
  name: string
  agent_count: number
  agents: AgentInfo[]
  stats: EmployeeStats
}

export interface EmployeesPayload {
  /** 默认员工的 agent pool（向后兼容运行时 Tab）。 */
  agents: AgentInfo[]
  count: number
  stats: EmployeeStats
  /** 全员真实员工列表（P1 按名生命周期的真实契约）。 */
  employees?: EmployeeSummary[]
  employee_count?: number
  error?: string
}

export interface GoalSummary {
  goal_id: string
  state: string
  natural_language: string
  scope: string
  created_at?: number | null
  error?: string | null
  replan_suggested: boolean
  task_count: number
  completed_tasks: number
  failed_tasks: number
}

export interface GoalTraceEntry {
  task_id: string | null
  event_type: string
  ts?: string | null
  capability?: string | null
  decision: string
  output?: unknown
  error?: string | null
  correlation_id?: string
}

export interface GoalDetail {
  goal_id: string
  state: string
  natural_language: string
  scope: string
  correlation_id: string
  error?: string | null
  replan_count: number
  replan_suggested: boolean
  trace: GoalTraceEntry[]
  tasks: Record<string, unknown>[]
  evaluation?: {
    outcome?: string
    replan_required?: boolean
    replan_triggered?: boolean
    summary?: string
  } | null
  created_at?: number
}

export interface GoalsPayload {
  goals: GoalSummary[]
  count: number
}

export interface WorkflowSummary {
  goal_id: string
  state: string
  task_count: number
  completed_tasks: number
  failed_tasks: number
}

export interface WorkflowsPayload {
  workflows: WorkflowSummary[]
  count: number
}
