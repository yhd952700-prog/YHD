/**
 * 技能中心 / 工具中心的取数与归一 —— 只映射后端**真实存在**的端点。
 *
 * 核对结果（2026-11 当前主仓）
 * ---------------------------
 * * ``/v1/employees/capabilities``  真实存在：已注册且 ACTIVE 的本地执行器
 *   （id / name / tool_id / description / risk）。
 * * ``/v1/plugins``                 真实存在：插件注册表（status / active / builtin /
 *   activatable / capabilities）。
 * * ``/v1/models``                  真实存在：模型服务配置（密钥状态 / 启用 / 验证）。
 * * ``/v1/skills``                   **不存在**（后端没有技能注册表路由）。
 * * ``/v1/mcp/servers``              **不存在**（MCP 只有进程内适配器，无 HTTP 面）。
 *
 * 后两个路径不做任何客户端假造：`probeState` 把 404/405/501 统一判成
 * 「本地服务还没提供」，页面据此渲染空态并禁用写入动作。字段级缺失同样如实
 * 标注（`null` 表示后端没返回，和「空列表」严格区分）。
 */

import type { ApiState } from './api'
import type { EmployeeCapability } from './contracts'
import type { AppSummary } from './operator'
import { safeArray } from './format'

/** 一整块数据拿不到时的统一说法（用户要求逐字出现）。 */
export const NOT_PROVIDED = '本地服务还没提供'
/** 紧随其后的解释：说清是"这个版本没有"，而不是"这里本来就空"。 */
export const NOT_PROVIDED_DETAIL =
  '当前版本的本地服务没有提供这个接口，本页不会用占位数字或示例数据代替。'

/** 单个字段没返回时的说法；与整块未提供分开，避免说错原因。 */
export const FIELD_NOT_PROVIDED = '未提供'

export type ProbeState = 'loading' | 'ready' | 'not-provided' | 'forbidden' | 'failed'

/**
 * 把 `useApi` 的真实结果归类。
 *
 * 只有 404/405/501 才算"接口不存在"；403 是"你看不到"；其余错误原样展示。
 * 绝不用 data === null 推断"未提供" —— 那会把网络失败也说成后端没做。
 */
export function probeState<T>(api: ApiState<T>): ProbeState {
  if (api.data !== null) return 'ready'
  const status = api.status
  if (status === 404 || status === 405 || status === 501) return 'not-provided'
  if (status === 403) return 'forbidden'
  if (api.error) return 'failed'
  return 'loading'
}

// ---------------------------------------------------------------------------
// 风险等级
// ---------------------------------------------------------------------------

export type RiskLevel = 'low' | 'medium' | 'high' | 'unknown'

const RISK_HIGH = new Set(['high', 'critical', 'severe', '高', '高风险'])
const RISK_MEDIUM = new Set(['medium', 'moderate', '中', '中风险'])
const RISK_LOW = new Set(['low', 'minimal', '低', '低风险'])

/** 后端 `Tool.risk` 是 LOW / MEDIUM / HIGH 这类字符串；无法识别时不猜。 */
export function riskLevel(raw: unknown): RiskLevel {
  const text = String(raw ?? '')
    .trim()
    .toLowerCase()
  if (!text) return 'unknown'
  if (RISK_HIGH.has(text)) return 'high'
  if (RISK_MEDIUM.has(text)) return 'medium'
  if (RISK_LOW.has(text)) return 'low'
  return 'unknown'
}

export const RISK_LABELS: Record<RiskLevel, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  unknown: '风险未提供',
}

export function riskTone(level: RiskLevel): 'ok' | 'warn' | 'danger' | 'muted' {
  if (level === 'low') return 'ok'
  if (level === 'medium') return 'warn'
  if (level === 'high') return 'danger'
  return 'muted'
}

/** 来源字段的人话；只翻译确定的枚举，未知值不假装知道。 */
export function sourceLabel(raw: unknown): string {
  const text = String(raw ?? '').trim()
  if (!text) return FIELD_NOT_PROVIDED
  const known: Record<string, string> = {
    builtin: '随包内置',
    local: '本地',
    imported: '外部导入',
    external: '外部来源',
    market: '外部市场',
    user: '你自己创建',
    system: '系统预置',
    plugin: '插件提供',
    mcp: 'MCP 服务',
  }
  const mapped = known[text.toLowerCase()]
  if (mapped) return mapped
  return /[\u4e00-\u9fa5]/.test(text) ? text : '来源未标明'
}

/** 字符串数组字段：null 表示后端没给，[] 表示后端明确给了空列表。 */
function stringListOrNull(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null
  return value.map((item) => String(item ?? '').trim()).filter(Boolean)
}

// ---------------------------------------------------------------------------
// 技能
// ---------------------------------------------------------------------------

export interface SkillRecord {
  id: string
  name: string
  description: string
  source: string
  version: string
  risk: RiskLevel
  /** null = 后端没有返回权限清单（≠ 无需权限）。 */
  permissions: string[] | null
  /** null = 后端没有返回启用状态。 */
  enabled: boolean | null
  /** null = 后端没有返回"被哪些员工使用"。 */
  usedBy: string[] | null
}

function firstString(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number') return String(value)
  }
  return ''
}

function firstBoolean(
  record: Record<string, unknown>,
  keys: string[],
): boolean | null {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'boolean') return value
  }
  return null
}

/**
 * 读取技能列表。
 *
 * 兼容 `{skills: []}` 与裸数组两种形态，其余一律视为空列表 —— 不会凭空补条目。
 * 每条的 id 缺失时用名称兜底，仅用于 React key 与展示，不代表后端有稳定 id。
 */
export function readSkills(payload: unknown): SkillRecord[] {
  if (!payload) return []
  const list = Array.isArray(payload)
    ? payload
    : Array.isArray((payload as { skills?: unknown }).skills)
      ? ((payload as { skills: unknown[] }).skills ?? [])
      : []
  return list
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item) => {
      const name = firstString(item, ['name', 'title', 'label'])
      const id = firstString(item, ['id', 'skill_id', 'slug']) || name
      const permissions =
        stringListOrNull(item.permissions) ??
        stringListOrNull(item.required_permissions) ??
        stringListOrNull(item.permission_scope)
      const usedBy =
        stringListOrNull(item.used_by) ??
        stringListOrNull(item.used_by_employees) ??
        stringListOrNull(item.employees)
      return {
        id,
        name: name || id || '未命名技能',
        description: firstString(item, ['description', 'summary']),
        source: firstString(item, ['source', 'origin', 'provider', 'author']),
        version: firstString(item, ['version']),
        risk: riskLevel(item.risk ?? item.risk_level),
        permissions,
        enabled: firstBoolean(item, ['enabled', 'active', 'installed']),
        usedBy,
      }
    })
}

// ---------------------------------------------------------------------------
// MCP 服务
// ---------------------------------------------------------------------------

export interface McpServerRecord {
  name: string
  transport: string
  status: string
  source: string
  risk: RiskLevel
  permissions: string[] | null
}

export function readMcpServers(payload: unknown): McpServerRecord[] {
  if (!payload) return []
  const list = Array.isArray(payload)
    ? payload
    : Array.isArray((payload as { servers?: unknown }).servers)
      ? ((payload as { servers: unknown[] }).servers ?? [])
      : []
  return list
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item) => ({
      name: firstString(item, ['name', 'server', 'id']) || '未命名服务',
      transport: firstString(item, ['transport', 'type']),
      status: firstString(item, ['status', 'state']),
      source: firstString(item, ['source', 'origin']),
      risk: riskLevel(item.risk ?? item.risk_level),
      permissions:
        stringListOrNull(item.permissions) ??
        stringListOrNull(item.required_permissions),
    }))
}

// ---------------------------------------------------------------------------
// 工具（本地执行器 / 插件）
// ---------------------------------------------------------------------------

/** 本地执行器：直接沿用 `/v1/employees/capabilities` 的真实契约。 */
export function readCapabilities(payload: unknown): EmployeeCapability[] {
  const list = (payload as { capabilities?: unknown } | null)?.capabilities
  return safeArray<EmployeeCapability>(list).filter(
    (item) => Boolean(item) && typeof item === 'object',
  )
}

export type ToolSource = 'builtin' | 'loadable' | 'placeholder' | 'unknown'
export type ToolAvailability = 'active' | 'available' | 'unavailable' | 'unknown'

export interface PluginToolRecord {
  id: string
  name: string
  kind: string
  source: ToolSource
  availability: ToolAvailability
  capabilities: string[]
  /** 插件注册表没有风险字段 —— 恒为 unknown，页面据此阻止启用。 */
  risk: RiskLevel
  /** 插件注册表没有权限字段 —— 恒为 null。 */
  permissions: string[] | null
  error: string | null
}

/**
 * 插件注册表 → 工具条目。
 *
 * `activatable` / `builtin` 是后端真实字段；两者都缺时只能算历史占位。
 * 风险与权限**不由前端推断**：注册表没有这两项，就保持 unknown / null。
 */
export function readPlugins(payload: unknown): PluginToolRecord[] {
  return safeArray<AppSummary>((payload as { plugins?: unknown } | null)?.plugins).map(
    (item) => {
      const builtin = Boolean(item.builtin)
      const activatable = Boolean(item.activatable)
      const source: ToolSource = builtin ? 'builtin' : activatable ? 'loadable' : 'placeholder'
      const status = String(item.status ?? '')
      const availability: ToolAvailability = item.active
        ? 'active'
        : status === 'failed' || status === 'error'
          ? 'unavailable'
          : activatable
            ? 'available'
            : 'unknown'
      return {
        id: String(item.plugin_id ?? ''),
        name: String(item.name ?? item.plugin_id ?? '未命名条目'),
        kind: String(item.kernel_type ?? ''),
        source,
        availability,
        capabilities: safeArray<string>(item.capabilities),
        risk: 'unknown',
        permissions: null,
        error: typeof item.error === 'string' && item.error.trim() ? item.error.trim() : null,
      }
    },
  )
}

// ---------------------------------------------------------------------------
// API 配置（模型服务）
// ---------------------------------------------------------------------------

export type ApiKeyStatus = 'configured' | 'missing' | 'unreadable' | 'unknown'

export interface ApiConfigRecord {
  id: string
  name: string
  /** 真实厂商 / 协议展示名（后端 `provider_display_name` / `vendor`）。 */
  source: string
  keyStatus: ApiKeyStatus
  enabled: boolean | null
  verified: boolean
  /** 后端自己给的展示状态（`display_status` / `status_label`）。 */
  backendStatus: string
}

function apiKeyStatus(raw: unknown, hasKey: unknown): ApiKeyStatus {
  const text = String(raw ?? '').trim().toLowerCase()
  if (text === 'ok') return 'configured'
  if (text === 'missing') return 'missing'
  if (text === 'needs_reentry' || text === 'unreadable') return 'unreadable'
  if (typeof hasKey === 'boolean') return hasKey ? 'configured' : 'missing'
  return 'unknown'
}

export function readApiConfigs(payload: unknown): ApiConfigRecord[] {
  const list = (payload as { models?: unknown } | null)?.models
  const rows = safeArray<Record<string, unknown>>(list)
  return rows
    .filter((item) => Boolean(item) && typeof item === 'object')
    .map((item) => ({
      id: firstString(item, ['id', 'model_id']),
      name:
        firstString(item, ['display_name', 'name', 'model']) ||
        firstString(item, ['id', 'model_id']) ||
        '未命名配置',
      source:
        firstString(item, ['provider_display_name', 'vendor', 'provider']) ||
        FIELD_NOT_PROVIDED,
      keyStatus: apiKeyStatus(item.api_key_status, item.api_key_set),
      enabled: firstBoolean(item, ['enabled']),
      verified: item.verified === true,
      backendStatus: firstString(item, ['display_status', 'status_label']),
    }))
}

/** 可用性由真实字段推导：缺密钥 / 密钥解不开 / 已停用，都不算可用。 */
export function apiAvailability(record: ApiConfigRecord): {
  tone: 'ok' | 'warn' | 'danger' | 'muted'
  label: string
} {
  if (record.keyStatus === 'missing') return { tone: 'danger', label: '不可用（未配置密钥）' }
  if (record.keyStatus === 'unreadable') {
    return { tone: 'danger', label: '不可用（密钥需要重新填写）' }
  }
  if (record.enabled === false) return { tone: 'muted', label: '已停用' }
  if (record.keyStatus === 'configured') {
    return record.verified
      ? { tone: 'ok', label: '可用（连接已验证）' }
      : { tone: 'warn', label: '已配置，尚未验证连接' }
  }
  return { tone: 'muted', label: '配置状态未知' }
}
