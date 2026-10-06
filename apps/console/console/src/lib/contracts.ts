/**
 * 后端响应契约 —— 与 `src/gateway/*.py` 的真实返回结构一一对应。
 *
 * 字段名**必须**与后端一致（后端不做驼峰转换）。这里只声明驾驶舱实际读取的
 * 字段，全部为可选或允许 null：后端在数据源不可用时会降级返回带 `error` 的
 * 空壳，前端必须能承受，而不是假设形状永远完整。
 */

/** 当前生效的 provider 的诚实性判据（后端 `ai_provider_status()` 的形状）。
 *
 * 这三个字段回答的是**"我看到的输出是真的吗"**，与 `ProviderItem.configured`
 * （"环境变量/密钥到位了吗"）是两个不同的问题，不可互相替代。
 *
 * 为什么它们必须在类型里：接口一开始只声明 `{type, model, name, error}`，
 * 于是 `mode` / `simulated` 在 TS 层面就不存在 —— 前端**想显示也显示不出来**。
 * 加上它们之前，控制台把内置 mock provider 渲染成"已配置，可用于真实调用"+
 * 绿色"就绪"（`Overview` 的 provider 卡片），而后端在这三个字段里一直如实
 * 区分 mock/real（`providers.py` 的注释："Never silently pretends a
 * simulation is real"）。缺这三个字段不是"少显示一点"，是**界面给假绿灯**。
 */
export interface ProviderHonesty {
  /** `mock` = 不调用真实模型，返回编造结果；`real` = 真实调用。 */
  mode?: 'mock' | 'real' | string
  /** 执行内核是否走模拟路径（`LIUHAO_ALLOW_SIMULATED_EXECUTION`）。 */
  simulated?: boolean
}

export interface ProviderInfo extends ProviderHonesty {
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

/** 名册 payload 中的真实员工条目（与后端 `build_roster_payload().real_employees` 一一对应）。
 *
 * 这是**真实持久化**员工（来自 `EmployeeStore`），与 `layers` / `kernels` 那种静态注册表
 * 条目是两回事——后者是"注册信息"，前者是"真有人/真有 agent 在跑"。
 *
 * Overview 的「能力层分布」卡片**有意不**把它渲染成"员工协作网络"——那曾是虚假宣称
 * （静态 capability-registry 分带 + 伪辐条，零员工、零协作、零边）。这里只把真实数据契约
 * 声明出来，供未来按需消费；绝不默认它已被画成一张"协作网络"图。
 */
export interface RosterEmployee {
  name: string
  agent_count: number
  agents: { id: string; agent_type: string; status: string }[]
  total_tasks_submitted: number
  total_tasks_completed: number
  total_tasks_failed: number
}

export interface ProviderItem {
  type: string
  label: string
  registered: boolean
  configured: boolean
  active: boolean
  required_env: string[]
  /** 接上去也不会产生真实调用的 provider（内置 mock / 空 / none）。 */
  is_mock?: boolean
  /** 这一项是不是**当前生效的那个**假 provider（"你正在被 mock 服务"）。 */
  is_active_provider_mock?: boolean
}

export interface RosterPayload {
  generated_at?: number
  source?: string
  available: boolean
  error?: string | null
  version?: string
  declared_totals?: { capabilities?: number; layers?: number }
  // Mirrors build_roster_payload() in src/ai/roster_payload.py exactly.
  // `declared_test_cases` used to be declared here and rendered by the
  // operations panel, but the backend has never produced it -- the payload
  // carries `declared_totals.{capabilities,layers}` instead. A field the server
  // does not send renders as undefined, so the panel was displaying a number
  // that did not exist. Do not add a field here without checking the payload
  // builder: this type is the only thing standing between the two.
  totals: {
    kernels: number
    layers: number
    /** kernel modules + capability layers. NOT an employee count. */
    registry_entries: number
    /** real persisted employees only -- see real_employees */
    employees: number
  }
  kernels: RegistryKernel[]
  layers: RegistryLayer[]
  domains: { key: string; label: string }[]
  bands: { key: string; label: string }[]
  /** 真实持久化员工（来自 EmployeeStore）。后端会发送；Overview 当前不把它渲染成网络图。 */
  real_employees?: RosterEmployee[]
  providers: {
    registered_count: number
    configured_count: number
    active: ProviderInfo
    items: ProviderItem[]
    error?: string
    /** 全系统级诚实性判据（不必翻 `active` 就能回答"现在是真跑还是模拟"）。 */
    mode?: 'mock' | 'real' | string
    simulated?: boolean
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
  /**
   * 审计证据链判据（`/v1/ready` 的 `checks.audit_store`）。
   *
   * 与 `status` 是**两个不同的问题**，不可互相替代：
   *   - `status` = 可用性：这份依赖能不能提供服务（决定 readiness 是否 503）；
   *   - `evidence_status` = 证据完整性：哈希链是否真被重新推导过。
   *
   * 曾经只有 `status` 且它被硬编码为 `healthy`，于是 `errors` 说"审计链不完整"
   * 而 `checks` 说"全好"—— 同一份 payload 自相矛盾。分字段后两者可以同时为真。
   */
  availability?: 'available' | 'unavailable'
  evidence_status?: 'verified' | 'unverified' | 'broken' | 'unknown'
  [key: string]: unknown
}

export interface HealthPayload {
  status?: string
  checks?: Record<string, HealthCheckEntry>
  errors?: string[]
  timestamp?: number
}

/** `/v1/ready` 的就绪载荷。
 *
 * 此前这里只声明了 `status?: string` —— 一个会让调用方以为"就绪载荷只有一句话"的
 * 契约：既没有 `checks`（机器/KPI 真正读的那份），也没有 `errors`（给人看的那份）。
 * 两个字段都补上，前端才有路径消费审计证据链判据。
 */
export interface ReadyPayload {
  status?: string
  checks?: Record<string, HealthCheckEntry>
  errors?: string[]
  timestamp?: number
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

/** `/v1/production/preflight` 的单条配置发现。
 *
 * 后端 `ConfigFinding.to_dict()`（`src/observability/production.py`）逐字对应。
 * `severity` 只有两种取值：`critical` / `warning` —— 这不是自由文本，后端用它
 * 统计 `critical_count` / `warning_count`，所以前端也按这两个值分色，不猜。
 */
export interface PreflightFinding {
  /** 被检查的环境变量名，例如 `SECRET_KEY`。 */
  key: string
  severity: 'critical' | 'warning' | string
  /** 机器可读判据，例如 `secret_key_missing`。 */
  code: string
  /** 给人看的中文说明，后端原文。 */
  message: string
}

/** `/v1/production/preflight` 的响应（`PreflightReport.to_dict()`）。
 *
 * 端点受 `require_human_principal` 保护：令牌必须带 `sub`，否则 401。
 * **报告只含键名 / 代码 / 严重级别，绝不含任何配置值** —— 前端因此不需要
 * （也不允许）在这里"顺手显示一下密钥"。
 */
export interface PreflightPayload {
  /** 无 critical 即通过。等价于 `critical_count === 0`，不是独立判据。 */
  ok: boolean
  /** `APP_ENV` 原值；空串表示未声明，**不等于** production。 */
  app_env: string
  critical_count: number
  warning_count: number
  findings: PreflightFinding[]
}
