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
