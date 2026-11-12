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
 * * ``/v1/skills``                  真实存在：技能注册表（导入 / 启停 / 绑定）。
 * * ``/v1/mcp/servers``             不存在；MCP 配置在导入时会返回 415，指向工具中心。
 *
 * 缺口不做任何客户端假造：`probeState` 把 404/405/501 统一判成
 * 「本地服务还没提供」，页面据此渲染空态并禁用写入动作。字段级缺失同样如实
 * 标注（`null` 表示后端没返回，和「空列表」严格区分）。
 */

import { ApiError, apiFetch, type ApiState } from './api'
import { tokenHeaders } from './auth'
import { readError } from './errorMessage'
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

/** 技能来源格式的人话；未知值不假装知道。 */
export function formatLabel(raw: string): string {
  const known: Record<string, string> = {
    manual: '手工创建',
    'agent-skill': 'Agent Skills（SKILL.md）',
    'liuhao-json': '本机 JSON 清单',
  }
  return known[raw] ?? (raw ? raw : FIELD_NOT_PROVIDED)
}

/** 字符串数组字段：null 表示后端没给，[] 表示后端明确给了空列表。 */
function stringListOrNull(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null
  return value.map((item) => String(item ?? '').trim()).filter(Boolean)
}

// ---------------------------------------------------------------------------
// 技能
// ---------------------------------------------------------------------------

export interface SkillImportReportRow {
  field: string
  value: unknown
  /** mapped / stored_raw / unsupported / defaulted / absent / empty */
  status: string
  note: string
}

/** 技能声明的可执行入口（来自 SKILL.md 的 ``runtime.entrypoints``）。 */
export interface SkillRuntimeEntrypoint {
  id: string
  label: string
  /** python / node / powershell；未知值原样保留。 */
  interpreter: string
  script: string
  /** 脚本声明了联网意图。注意：本机拦不住网络，这里只是声明，不是强制。 */
  network: boolean
  /** 该入口当前有"记住的授权"。 */
  approved: boolean
}

/**
 * 运行时真实状态 —— 后端按包目录 + 授权表现算，前端不推导。
 *
 * ``state`` 取值：``no_runtime``（只作为资料注入）/ ``disabled``（未启用）/
 * ``pending_approval``（首次运行待确认）/ ``approved``（已有授权）/
 * ``unavailable``（运行时读不出来）。未知值原样展示，不硬翻译。
 */
export interface SkillRuntimeView {
  declared: boolean
  /** 包目录真实存在（存在 ≠ 可运行）。 */
  installed: boolean
  state: string
  allowed: boolean
  entrypoints: SkillRuntimeEntrypoint[]
  approvedEntrypoints: string[]
  /** 后端声明的资源上限（memory_mb / cpu_seconds / timeout_seconds / max_processes…）。 */
  limits: Record<string, number>
  packageHash: string
  installedAt: string
  detail: string
}

export function emptySkillRuntime(): SkillRuntimeView {
  return {
    declared: false,
    installed: false,
    state: 'no_runtime',
    allowed: false,
    entrypoints: [],
    approvedEntrypoints: [],
    limits: {},
    packageHash: '',
    installedAt: '',
    detail: '',
  }
}

/** 后端没返回 runtime 时按"没有运行时"处理，绝不假装可执行。 */
export function readSkillRuntime(value: unknown): SkillRuntimeView {
  if (!value || typeof value !== 'object') return emptySkillRuntime()
  const raw = value as Record<string, unknown>
  const list = Array.isArray(raw.entrypoints) ? raw.entrypoints : []
  const entrypoints = list
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item) => ({
      id: String(item.id ?? ''),
      label: String(item.label ?? item.id ?? ''),
      interpreter: String(item.interpreter ?? ''),
      script: String(item.script ?? ''),
      network: item.network === true,
      approved: item.approved === true,
    }))
    .filter((item) => Boolean(item.id))
  const limitsRaw = raw.limits && typeof raw.limits === 'object' ? raw.limits : {}
  const limits: Record<string, number> = {}
  for (const [key, item] of Object.entries(limitsRaw as Record<string, unknown>)) {
    const numeric = typeof item === 'number' ? item : Number(item)
    if (Number.isFinite(numeric)) limits[key] = numeric
  }
  return {
    declared: raw.declared === true,
    installed: raw.installed === true,
    state: typeof raw.state === 'string' && raw.state.trim() ? raw.state.trim() : 'no_runtime',
    allowed: raw.allowed === true,
    entrypoints,
    approvedEntrypoints: stringListOrNull(raw.approved_entrypoints) ?? [],
    limits,
    packageHash: String(raw.package_hash ?? ''),
    installedAt: String(raw.installed_at ?? ''),
    detail: String(raw.detail ?? ''),
  }
}

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
  /** 真实绑定（员工 ↔ 技能表）；null = 后端没返回。 */
  usedBy: string[] | null
  /** 由能力交集推导的「可能相关」，不是绑定。 */
  relatedEmployees: string[] | null
  bindingsError: string | null
  /** 来源格式：manual / agent-skill / liuhao-json。 */
  format: string
  license: string
  allowedTools: string[]
  nameSource: string
  /** 正文引用了脚本（只能证明"提到了"，不代表已校验）。 */
  hasScripts: boolean
  scriptRefs: string[]
  /** 后端按"已装包 + 已启用 + 已授权"现算；未声明入口的技能恒为 false。 */
  executionAllowed: boolean
  /** 运行时真实状态；后端没返回时是"没有运行时"。 */
  runtime: SkillRuntimeView
  /** 技能声明了但本机能力目录里没有的能力。 */
  missingCapabilities: string[]
  importReport: SkillImportReportRow[]
  createdAt: string
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
      const relatedEmployees =
        stringListOrNull(item.related_employees) ??
        stringListOrNull(item.possibly_related_employees)
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
        relatedEmployees,
        bindingsError:
          typeof item.bindings_error === 'string' && item.bindings_error.trim()
            ? item.bindings_error.trim()
            : null,
        format: firstString(item, ['format']),
        license: firstString(item, ['license']),
        allowedTools: stringListOrNull(item.allowed_tools) ?? [],
        nameSource: firstString(item, ['name_source']),
        hasScripts: item.has_scripts === true,
        scriptRefs: stringListOrNull(item.script_refs) ?? [],
        executionAllowed: item.execution_allowed === true,
        runtime: readSkillRuntime(item.runtime),
        missingCapabilities: stringListOrNull(item.missing_capabilities) ?? [],
        importReport: Array.isArray(item.import_report)
          ? (item.import_report as SkillImportReportRow[]).filter(
              (row) => Boolean(row) && typeof row === 'object',
            )
          : [],
        createdAt: firstString(item, ['created_at']),
      }
    })
}

// ---------------------------------------------------------------------------
// 技能安装 / 运行 / 授权（``/v1/skills`` 的真实端点）
// ---------------------------------------------------------------------------

export interface SkillInstallResult {
  id: string
  name: string
  enabled: boolean | null
  note: string
  runtime: SkillRuntimeView
  report: SkillImportReportRow[]
  /** 后端为升级返回的说明；普通安装为空。 */
  upgradeNote: string
}

/** ``POST /v1/skills/import``：上传 .zip / .md / .json 技能包。 */
export async function uploadSkillPackage(
  file: File,
  filename: string,
): Promise<SkillInstallResult> {
  const body = new FormData()
  body.append('file', file, filename)
  return readInstallResult(await apiFetch<unknown>('/v1/skills/import', { method: 'POST', body }))
}

/** ``POST /v1/skills/install/local``：从本机目录 / 文件安装（目录只能走这条）。 */
export async function installSkillFromPath(path: string): Promise<SkillInstallResult> {
  return readInstallResult(
    await apiFetch<unknown>('/v1/skills/install/local', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  )
}

/** ``POST /v1/skills/{id}/upgrade``：同 id 覆盖，保留员工绑定；旧授权自动失效。 */
export async function upgradeSkillPackage(
  skillId: string,
  file: File,
  filename: string,
): Promise<SkillInstallResult> {
  const body = new FormData()
  body.append('file', file, filename)
  return readInstallResult(
    await apiFetch<unknown>(`/v1/skills/${encodeURIComponent(skillId)}/upgrade`, {
      method: 'POST',
      body,
    }),
  )
}

function readInstallResult(payload: unknown): SkillInstallResult {
  const record = (payload ?? {}) as Record<string, unknown>
  return {
    id: String(record.id ?? ''),
    name: String(record.name ?? ''),
    enabled: typeof record.enabled === 'boolean' ? record.enabled : null,
    note: String(record.import_note ?? ''),
    runtime: readSkillRuntime(record.runtime),
    report: Array.isArray(record.import_report)
      ? (record.import_report as SkillImportReportRow[]).filter(
          (row) => Boolean(row) && typeof row === 'object',
        )
      : [],
    upgradeNote: String(record.upgrade_note ?? ''),
  }
}

/** 一次运行的公开结果（不含脚本原文与参数值）。 */
export interface SkillRunView {
  runId: string
  skillId: string
  entrypoint: string
  status: string
  exitCode: number | null
  timedOut: boolean
  durationMs: number | null
  peakMemoryBytes: number | null
  cpuMs: number | null
  /** ``low_integrity+job`` 或 ``job_only``；界面据此说「受限执行」。 */
  isolation: string
  limits: Record<string, number>
  summary: string
  result: unknown
  stdout: string
  stderr: string
  truncatedBytes: number
  finishedAt: string
}

/** 待确认的首次运行申请。 */
export interface SkillApprovalTicket {
  approvalId: string
  skillName: string
  entrypoint: string
  interpreter: string
  script: string
  network: boolean
  limits: Record<string, number>
  requestedAt: string
}

export interface SkillRunResponse {
  /** ``pending_approval``（未执行）或 ``finished``（已执行）。 */
  state: string
  skillId: string
  approval: SkillApprovalTicket | null
  notice: string
  run: SkillRunView | null
}

/** ``POST /v1/skills/{id}/run``：未授权时只登记申请，不启动任何进程。 */
export async function runSkillEntrypoint(
  skillId: string,
  entrypoint: string,
  params: Record<string, unknown>,
  employee?: string,
): Promise<SkillRunResponse> {
  return readRunResponse(
    await apiFetch<unknown>(`/v1/skills/${encodeURIComponent(skillId)}/run`, {
      method: 'POST',
      body: JSON.stringify({ entrypoint, params, employee: employee || null }),
    }),
  )
}

export function readRunResponse(payload: unknown): SkillRunResponse {
  const record = (payload ?? {}) as Record<string, unknown>
  const approvalRaw =
    record.approval && typeof record.approval === 'object'
      ? (record.approval as Record<string, unknown>)
      : null
  const runRaw =
    record.run && typeof record.run === 'object' ? (record.run as Record<string, unknown>) : null
  return {
    state: String(record.state ?? ''),
    skillId: String(record.skill_id ?? ''),
    approval: approvalRaw
      ? {
          approvalId: String(approvalRaw.approval_id ?? ''),
          skillName: String(approvalRaw.skill_name ?? ''),
          entrypoint: String(approvalRaw.entrypoint ?? ''),
          interpreter: String(approvalRaw.interpreter ?? ''),
          script: String(approvalRaw.script ?? ''),
          network: approvalRaw.network === true,
          limits: numericRecord(approvalRaw.limits),
          requestedAt: String(approvalRaw.requested_at ?? ''),
        }
      : null,
    notice: String(record.notice ?? ''),
    run: runRaw
      ? {
          runId: String(runRaw.run_id ?? ''),
          skillId: String(runRaw.skill_id ?? ''),
          entrypoint: String(runRaw.entrypoint_id ?? ''),
          status: String(runRaw.status ?? ''),
          exitCode: typeof runRaw.exit_code === 'number' ? runRaw.exit_code : null,
          timedOut: runRaw.timed_out === true,
          durationMs: typeof runRaw.duration_ms === 'number' ? runRaw.duration_ms : null,
          peakMemoryBytes:
            typeof runRaw.peak_memory_bytes === 'number' ? runRaw.peak_memory_bytes : null,
          cpuMs: typeof runRaw.cpu_ms === 'number' ? runRaw.cpu_ms : null,
          isolation: String(runRaw.isolation ?? ''),
          limits: numericRecord(runRaw.limits),
          summary: String(runRaw.summary ?? ''),
          result: runRaw.result ?? null,
          stdout: String(runRaw.stdout ?? ''),
          stderr: String(runRaw.stderr ?? ''),
          truncatedBytes:
            typeof runRaw.truncated_bytes === 'number' ? runRaw.truncated_bytes : 0,
          finishedAt: String(runRaw.finished_at ?? ''),
        }
      : null,
  }
}

function numericRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== 'object') return {}
  const out: Record<string, number> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    const numeric = typeof item === 'number' ? item : Number(item)
    if (Number.isFinite(numeric)) out[key] = numeric
  }
  return out
}

export interface SkillRememberedApproval {
  approvalId: string
  entrypoint: string
  script: string
  mode: string
  approvedBy: string
  approvedAt: string
  expiresAt: string
}

export interface SkillPendingApproval {
  approvalId: string
  entrypoint: string
  script: string
  requestedBy: string
  requestedAt: string
}

export interface SkillApprovalsView {
  remembered: SkillRememberedApproval[]
  pending: SkillPendingApproval[]
}

/** ``GET /v1/skills/{id}/approvals``：待确认申请 + 已记住的授权。 */
export async function readSkillApprovals(skillId: string): Promise<SkillApprovalsView> {
  return parseSkillApprovals(
    await apiFetch<unknown>(`/v1/skills/${encodeURIComponent(skillId)}/approvals`),
  )
}

export function parseSkillApprovals(payload: unknown): SkillApprovalsView {
  const record = (payload ?? {}) as Record<string, unknown>
  const rows = <T extends Record<string, unknown>>(value: unknown): T[] =>
    Array.isArray(value)
      ? value.filter((item): item is T => Boolean(item) && typeof item === 'object')
      : []
  return {
    remembered: rows<Record<string, unknown>>(record.remembered).map((item) => ({
      approvalId: String(item.approval_id ?? ''),
      entrypoint: String(item.entrypoint ?? ''),
      script: String(item.script ?? ''),
      mode: String(item.mode ?? ''),
      approvedBy: String(item.approved_by ?? ''),
      approvedAt: String(item.approved_at ?? ''),
      expiresAt: String(item.expires_at ?? ''),
    })),
    pending: rows<Record<string, unknown>>(record.pending).map((item) => ({
      approvalId: String(item.approval_id ?? ''),
      entrypoint: String(item.entrypoint ?? ''),
      script: String(item.script ?? ''),
      requestedBy: String(item.requested_by ?? ''),
      requestedAt: String(item.requested_at ?? ''),
    })),
  }
}

/** ``POST /v1/skills/approvals/{id}/approve``：``once`` 只放行一次，``always`` 记住 90 天。 */
export async function approveSkillRun(
  approvalId: string,
  mode: 'once' | 'always',
): Promise<void> {
  await apiFetch<unknown>(
    `/v1/skills/approvals/${encodeURIComponent(approvalId)}/approve`,
    { method: 'POST', body: JSON.stringify({ mode }) },
  )
}

export async function rejectSkillRun(approvalId: string): Promise<void> {
  await apiFetch<unknown>(
    `/v1/skills/approvals/${encodeURIComponent(approvalId)}/reject`,
    { method: 'POST' },
  )
}

/** ``DELETE /v1/skills/approvals/{id}``：清掉一条记住的授权。 */
export async function revokeSkillApproval(approvalId: string): Promise<void> {
  await apiFetch<unknown>(`/v1/skills/approvals/${encodeURIComponent(approvalId)}`, {
    method: 'DELETE',
  })
}

/** ``DELETE /v1/skills/{id}/approvals``：清掉该技能全部记住的授权。 */
export async function revokeSkillApprovals(skillId: string): Promise<number> {
  const payload = (await apiFetch<unknown>(
    `/v1/skills/${encodeURIComponent(skillId)}/approvals`,
    { method: 'DELETE' },
  )) as Record<string, unknown>
  const cleared = payload?.revoked
  return typeof cleared === 'number' ? cleared : 0
}

export interface SkillFileRow {
  path: string
  sha256: string
}

export interface SkillFilesView {
  installed: boolean
  packageHash: string
  installedAt: string
  origin: string
  files: SkillFileRow[]
}

/** ``GET /v1/skills/{id}/files``：已安装包的真实文件清单与哈希。 */
export async function readSkillFiles(skillId: string): Promise<SkillFilesView> {
  const payload = (await apiFetch<unknown>(
    `/v1/skills/${encodeURIComponent(skillId)}/files`,
  )) as Record<string, unknown>
  const files = Array.isArray(payload?.files) ? payload.files : []
  return {
    installed: payload?.installed === true,
    packageHash: String(payload?.package_hash ?? ''),
    installedAt: String(payload?.installed_at ?? ''),
    origin: String(payload?.origin ?? ''),
    files: files
      .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      .map((item) => ({
        path: String(item.path ?? ''),
        sha256: String(item.sha256 ?? ''),
      })),
  }
}

export interface SkillAuditEvent {
  event: string
  at: string
  runId: string
  entrypoint: string
  exitCode: number | null
  timedOut: boolean
  durationMs: number | null
  isolation: string
  detail: Record<string, unknown>
}

/** ``GET /v1/skills/{id}/audit``：start / finish 成对的运行审计。 */
export async function readSkillAudit(skillId: string, limit = 50): Promise<SkillAuditEvent[]> {
  const payload = (await apiFetch<unknown>(
    `/v1/skills/${encodeURIComponent(skillId)}/audit?limit=${limit}`,
  )) as Record<string, unknown>
  const events = Array.isArray(payload?.events) ? payload.events : []
  return events
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
    .map((item) => ({
      event: String(item.event ?? ''),
      at: String(item.ts ?? item.at ?? ''),
      runId: String(item.run_id ?? ''),
      entrypoint: String(item.entrypoint_id ?? ''),
      exitCode: typeof item.exit_code === 'number' ? item.exit_code : null,
      timedOut: item.timed_out === true,
      durationMs: typeof item.duration_ms === 'number' ? item.duration_ms : null,
      isolation: String(item.isolation ?? ''),
      detail: item,
    }))
}

/**
 * ``GET /v1/skills/{id}/export``：下载 zip。
 *
 * 导出接口返回二进制，`apiFetch` 只会按 JSON 解析，所以这里自己发请求；令牌仍走
 * `tokenHeaders`，与其它请求同一套鉴权。
 */
export async function downloadSkillPackage(skillId: string): Promise<void> {
  const response = await fetch(`/v1/skills/${encodeURIComponent(skillId)}/export`, {
    headers: tokenHeaders(),
    cache: 'no-store',
    signal: AbortSignal.timeout(60_000),
  })
  if (!response.ok) {
    const parts = await readError(response)
    throw new ApiError(response.status, parts.message, parts.code)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = `${skillId}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally {
    URL.revokeObjectURL(url)
  }
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
