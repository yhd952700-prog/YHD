/**
 * 账号面客户端 —— 只调用 `src/gateway/accounts.py` 已定义的契约。
 *
 * 两条纪律：
 * 1. **不伪造"已完成"**：开发环境下若路由尚未挂载（404 且不是业务错误体），
 *    允许落到契约桩，但调用方会拿到 `source: 'stub'`，界面必须显示
 *    "本地契约桩 · 待联调" 徽标；生产构建永不启用桩。
 * 2. **形状与后端一致**：成员条目刻意不含 parent/depth/root 字段
 *    （见 `_accounts.public_member`），前端不得自行补结构字段。
 */

import { useEffect, useRef, useState } from 'react'
import { LOCAL_SERVICE_OFFLINE_MANUAL, describeDetail, readErrorMessage } from './errorMessage'
import { clearSession, tokenHeaders, UNAUTHENTICATED_EVENT } from './auth'

export type DataSource = 'live' | 'stub'

export interface AccountMember {
  principal: string
  permissions: string[]
  status: string
  created_at?: string
  /** 列表接口只返回直接成员时带 `true`（`accounts.py` 直连视图）。 */
  is_direct?: boolean
  /** 仅根账号视图出现（`public_member(show_key_authorization=True)`）。 */
  allow_superior_key_use?: boolean
  key_auth_source?: string
}

export interface AccountNode extends AccountMember {
  children: AccountNode[]
}

export interface AccountsPayload {
  members: AccountMember[]
  count: number
}

export interface RolePreset {
  id: string
  permissions: string[]
}

export interface RolesPayload {
  roles: RolePreset[]
  scopes: string[]
}

/** `/v1/accounts/{p}/usage` 的真实响应（见 `src/gateway/account_usage.py`）。 */
export interface UsageEntry {
  calls: number
  tokens_in: number
  tokens_out: number
  total_tokens: number
}

export interface UsageModelEntry {
  model: string
  tokens_in: number
  tokens_out: number
  total_tokens: number
  /** 未定价模型为 `null`（不是 0 元）。 */
  cost: number | null
  pricing_status: 'priced' | 'unknown'
  message: string
}

export interface UsagePayload {
  principal: string
  scope?: 'subtree' | 'self'
  callers_visible?: boolean
  calls: number
  tokens_in: number
  tokens_out: number
  total_tokens: number
  by_key_owner: Array<UsageEntry & { key_owner: string }>
  /** 仅根可见；成员响应里连这个键都不出现。 */
  by_actor?: Array<UsageEntry & { actor: string }>
  /** 已定价模型的成本合计（未定价模型不计入）。 */
  cost?: number
  /** false 表示有未定价模型，`cost` 不是全部。 */
  cost_complete?: boolean
  unknown_models?: string[]
  cost_message?: string
  budget_note?: string
  by_model?: UsageModelEntry[]
}

export interface BackupManifest {
  members: string[]
  member_count: number
  files: number
  bytes: number
  models: number
  credentials: number
  skipped: string[]
}

export interface BackupExported {
  bundle: Record<string, unknown>
  scope?: string
  exported_at?: string
  members?: number
  /** `account_backup.py` 生成的真实统计（账号数 / 文件数 / 字节数）。 */
  manifest?: BackupManifest
}

/** `/v1/memory/entries` 的真实条目（见 `account_memory._row_to_entry`）。 */
export interface MemoryEntry {
  id: string
  account_id: string
  text: string
  source: string
  tags: string[]
  created_at: string
}

export interface MemoryPayload {
  entries: MemoryEntry[]
  scope: 'self' | 'subtree'
  count: number
}

/** `/v1/memory/custody`：代管开关只由本人显式变更，变更写审计。 */
export interface MemoryCustodyPayload {
  enabled: boolean
  updated_at: string
}

export interface AccountResult<T> {
  data: T
  source: DataSource
}

export interface AccountViewer {
  principal: string
  isRoot: boolean
}

export class AccountApiError extends Error {
  status: number
  code: string | null
  constructor(status: number, code: string | null, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

/** 开发环境才允许启用契约桩；生产构建恒为 false。 */
export function stubEnabled(): boolean {
  const env = (import.meta as ImportMeta & { env?: { DEV?: boolean } }).env
  return Boolean(env?.DEV)
}

/**
 * 没有可读 detail 时的兜底：不许把「请求失败（500）」这种只报状态码的句子
 * 直接丢给用户。按状态给出「发生了什么 + 下一步」，与 api.ts 同一口径。
 */
function fallbackStatusMessage(status: number): string {
  if (status === 403 || status === 404) {
    return '当前账号没有执行这个操作的权限；如需开通，请联系管理员。'
  }
  if (status === 408) {
    return '这次请求超时，操作可能没有完成。请重试；不要重复提交同一个操作。'
  }
  if (status === 429) {
    return '请求过于频繁，请稍后再试。'
  }
  if (status === 502 || status === 503 || status === 504) {
    return '本地服务暂时不可用，可能是正在启动或重启。请稍后重试。'
  }
  if (status >= 500) {
    return '本地服务这次没有完成操作，请稍后重试；持续失败可导出诊断包。'
  }
  return '操作没有完成，请稍后重试；持续出现可导出诊断包。'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = tokenHeaders(init?.headers)
  if (init?.body) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    // 与 api.ts 相同：没有超时的 fetch 会在服务卡住时永远悬着，界面一直转圈。
    response = await fetch(path, {
      ...init,
      headers,
      signal: init?.signal ?? AbortSignal.timeout(15_000),
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'TimeoutError') {
      throw new AccountApiError(
        408,
        null,
        '这次读取超时（15 秒）。本地服务可能正忙，请重试；如果一直超时，请重启鎏灏后再试。',
      )
    }
    throw new AccountApiError(0, null, LOCAL_SERVICE_OFFLINE_MANUAL)
  }
  if (!response.ok) {
    let code: string | null = null
    let message = ''
    try {
      const body = (await response.json()) as {
        code?: string
        detail?: unknown
        message?: unknown
      }
      code = typeof body.code === 'string' ? body.code : null
      // 与 api.ts 同一条规则：只保留含中文的可读说明，英文原文/机器串不上屏。
      message = describeDetail(body.detail || body.message)
    } catch {
      message = await readErrorMessage(response)
    }
    // 令牌失效（过期 / 被改密或授权变更吊销）与"未带令牌"都必须回到登录页，
    // 不能把 `missing bearer token` 这类协议细节直接摊给用户。
    if (response.status === 401) {
      clearSession()
      try {
        window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
      } catch {
        // 非浏览器环境：忽略。
      }
      throw new AccountApiError(401, code, '登录状态已失效，请重新登录')
    }
    throw new AccountApiError(
      response.status,
      code,
      message || fallbackStatusMessage(response.status),
    )
  }
  return (await response.json()) as T
}

/** 路由未挂载（而非业务拒绝）——只有这种 404 才能落到桩。 */
export function isRouteMissing(error: unknown): boolean {
  return error instanceof AccountApiError && error.status === 404 && error.code === null
}

/** 账号面的拒绝一律中性化，不把服务端结构信息透给界面。 */
export function neutralDenial(error: unknown): string {
  if (error instanceof AccountApiError && error.status === 401) {
    return '登录状态已失效，请重新登录'
  }
  if (error instanceof AccountApiError && (error.status === 403 || error.status === 404)) {
    return '无权执行该操作'
  }
  const fallback = error instanceof Error ? error.message : ''
  return fallback || '操作没有完成，请稍后重试；持续出现可导出诊断包。'
}

async function withStub<T>(
  live: () => Promise<T>,
  stub: () => T,
): Promise<AccountResult<T>> {
  try {
    return { data: await live(), source: 'live' }
  } catch (error) {
    if (stubEnabled() && isRouteMissing(error)) {
      return { data: stub(), source: 'stub' }
    }
    throw error
  }
}

function seedMembers(): AccountMember[] {
  return [
    { principal: 'member-a', permissions: ['account:read', 'model:use'], status: 'active', created_at: '2026-01-02', allow_superior_key_use: false, key_auth_source: 'not_granted' },
    { principal: 'member-b', permissions: ['account:read', 'account:manage_children', 'model:use'], status: 'active', created_at: '2026-01-03', allow_superior_key_use: true, key_auth_source: 'explicit_toggle' },
  ]
}

let stubStore: AccountMember[] = seedMembers()

function stubAccounts(viewer: AccountViewer): AccountsPayload {
  const visible = viewer.isRoot ? stubStore : stubStore.slice(0, 1)
  return { members: visible, count: visible.length }
}

function stubTree(viewer: AccountViewer): { tree: AccountNode } {
  const root: AccountNode = {
    principal: viewer.principal,
    permissions: ['account:read', 'account:manage_children'],
    status: 'active',
    allow_superior_key_use: false,
    children: stubStore.map((member) => ({ ...member, children: [] })),
  }
  return { tree: root }
}

/** 含自己在内的节点总数（`/v1/accounts/tree` 只返回给根）。 */
export function countTreeMembers(node: AccountNode): number {
  return 1 + node.children.reduce((sum, child) => sum + countTreeMembers(child), 0)
}

/** 把树拍平成列表（含传入节点）；页面按需再排除根自己。 */
export function flattenTree(node: AccountNode): AccountNode[] {
  return [node, ...node.children.flatMap((child) => flattenTree(child))]
}

/** 某个节点的全部后代（不含它自己）。 */
export function descendantsOf(node: AccountNode): AccountNode[] {
  return node.children.flatMap((child) => flattenTree(child))
}

function stubRoles(): RolesPayload {
  return {
    roles: [
      { id: 'viewer', permissions: ['account:read', 'account:audit_read', 'account:usage_read', 'model:use'] },
      { id: 'operator', permissions: ['account:read', 'account:manage_children', 'account:audit_read', 'account:usage_read', 'model:use'] },
      { id: 'manager', permissions: ['account:read', 'account:manage_children', 'account:audit_read', 'account:usage_read', 'model:use', 'account:grant', 'model:manage'] },
    ],
    scopes: [
      'account:read',
      'account:manage_children',
      'account:audit_read',
      'account:usage_read',
      'account:grant',
      'model:use',
      'model:manage',
    ],
  }
}

export function loadAccounts(viewer: AccountViewer): Promise<AccountResult<AccountsPayload>> {
  return withStub(
    () => request<AccountsPayload>('/v1/accounts'),
    () => stubAccounts(viewer),
  )
}

export function loadTree(viewer: AccountViewer): Promise<AccountResult<{ tree: AccountNode }>> {
  return withStub(
    () => request<{ tree: AccountNode }>('/v1/accounts/tree'),
    () => stubTree(viewer),
  )
}

export function loadRoles(): Promise<AccountResult<RolesPayload>> {
  return withStub(
    () => request<RolesPayload>('/v1/accounts/roles'),
    () => stubRoles(),
  )
}

export function createMember(input: {
  principal: string
  display_name?: string
  role?: string
  allow_key_sharing: boolean
}): Promise<AccountResult<{ account: AccountMember; activation_code: string; notice: string }>> {
  return withStub(
    () =>
      request<{ account: AccountMember; activation_code: string; notice: string }>(
        '/v1/accounts',
        { method: 'POST', body: JSON.stringify(input) },
      ),
    () => {
      const account: AccountMember = {
        principal: input.principal,
        permissions: stubRoles().roles.find((role) => role.id === (input.role || 'viewer'))?.permissions ?? [],
        status: 'pending_activation',
        allow_superior_key_use: input.allow_key_sharing,
        key_auth_source: input.allow_key_sharing ? 'explicit_toggle' : 'not_granted',
      }
      stubStore = [...stubStore, account]
      return { account, activation_code: 'STUB-CODE', notice: '契约桩：激活码未由服务端生成。' }
    },
  )
}

export function reissueActivation(
  principal: string,
): Promise<AccountResult<{ activation_code: string; expires_at: string }>> {
  return withStub(
    () =>
      request<{ activation_code: string; expires_at: string }>(
        `/v1/accounts/${encodeURIComponent(principal)}/activation-code`,
        { method: 'POST' },
      ),
    () => ({ activation_code: 'STUB-CODE', expires_at: '契约桩' }),
  )
}

export function updateStatus(
  principal: string,
  status: 'active' | 'suspended' | 'deleted',
  reason = '',
): Promise<AccountResult<Record<string, unknown>>> {
  return withStub(
    () =>
      request<Record<string, unknown>>(`/v1/accounts/${encodeURIComponent(principal)}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status, reason }),
      }),
    () => {
      stubStore = stubStore.map((member) =>
        member.principal === principal ? { ...member, status } : member,
      )
      return { ok: true }
    },
  )
}

export function resetMemberPassword(
  principal: string,
  password: string,
): Promise<AccountResult<{ ok: boolean }>> {
  return withStub(
    () =>
      request<{ ok: boolean }>(`/v1/accounts/${encodeURIComponent(principal)}/password`, {
        method: 'POST',
        body: JSON.stringify({ password }),
      }),
    () => ({ ok: true }),
  )
}

/**
 * 本人改密（自助）。必须带当前密码：只拿到令牌不足以静默换密。
 * 成功后服务端会吊销本人旧令牌，调用方必须提示重新登录。
 */
export function changeOwnPassword(
  principal: string,
  currentPassword: string,
  password: string,
): Promise<
  AccountResult<{ ok: boolean; requires_reauth?: boolean; notice?: string }>
> {
  return withStub(
    () =>
      request<{ ok: boolean; requires_reauth?: boolean; notice?: string }>(
        `/v1/accounts/${encodeURIComponent(principal)}/password`,
        {
          method: 'POST',
          body: JSON.stringify({ current_password: currentPassword, password }),
        },
      ),
    () => ({ ok: true, requires_reauth: true, notice: '密码已修改，请用新密码重新登录。' }),
  )
}

export function grantPermissions(
  principal: string,
  grant: string[],
  revoke: string[],
): Promise<AccountResult<{ account: AccountMember }>> {
  return withStub(
    () =>
      request<{ account: AccountMember }>(
        `/v1/accounts/${encodeURIComponent(principal)}/permissions`,
        { method: 'PATCH', body: JSON.stringify({ grant, revoke }) },
      ),
    () => {
      const current = stubStore.find((member) => member.principal === principal)
      const permissions = new Set(current?.permissions ?? [])
      grant.forEach((scope) => permissions.add(scope))
      revoke.forEach((scope) => permissions.delete(scope))
      const account: AccountMember = {
        ...(current ?? { principal, status: 'active' }),
        permissions: [...permissions].sort(),
      }
      stubStore = stubStore.map((member) => (member.principal === principal ? account : member))
      return { account }
    },
  )
}

export function setKeySharing(
  principal: string,
  allow: boolean,
): Promise<AccountResult<{ account: AccountMember }>> {
  return withStub(
    () =>
      request<{ account: AccountMember }>(
        `/v1/accounts/${encodeURIComponent(principal)}/key-sharing`,
        { method: 'PATCH', body: JSON.stringify({ allow }) },
      ),
    () => {
      const current = stubStore.find((member) => member.principal === principal)
      const account: AccountMember = {
        ...(current ?? { principal, permissions: [], status: 'active' }),
        allow_superior_key_use: allow,
        key_auth_source: 'explicit_toggle',
      }
      stubStore = stubStore.map((member) => (member.principal === principal ? account : member))
      return { account }
    },
  )
}

export function loadUsage(principal: string): Promise<AccountResult<UsagePayload>> {
  return withStub(
    () => request<UsagePayload>(`/v1/accounts/${encodeURIComponent(principal)}/usage`),
    () => ({
      principal,
      scope: 'self',
      callers_visible: false,
      calls: 12,
      tokens_in: 1200,
      tokens_out: 2200,
      total_tokens: 3400,
      cost: 0.008,
      cost_complete: true,
      unknown_models: [],
      cost_message: '',
      budget_note: '未定价模型只统计用量、不参与预算拦截：成本未知，既不会被当成 0 元放行，也不会因为缺少价格而被拒绝。',
      by_model: [],
      by_key_owner: [
        { key_owner: principal, calls: 12, tokens_in: 1200, tokens_out: 2200, total_tokens: 3400 },
      ],
    }),
  )
}

export async function exportBackup(
  scope: 'all' | 'self',
  passphrase: string,
): Promise<AccountResult<BackupExported>> {
  // 备份包必须由后端加密生成；前端不造假的包（IA §6：此接口返回 501 时如实报错）。
  const data = await request<BackupExported>('/v1/accounts/backup/export', {
    method: 'POST',
    body: JSON.stringify({ scope, passphrase }),
  })
  return { data, source: 'live' }
}

export async function importBackup(
  bundle: Record<string, unknown>,
  passphrase: string,
): Promise<AccountResult<Record<string, unknown>>> {
  // 认领备份会改写本机身份状态，绝不能用桩假装成功。
  const data = await request<Record<string, unknown>>('/v1/accounts/backup/import', {
    method: 'POST',
    body: JSON.stringify({ bundle, passphrase }),
  })
  return { data, source: 'live' }
}

export async function loadMemory(
  viewer: AccountViewer,
  scope: 'self' | 'subtree',
  query: string,
): Promise<AccountResult<MemoryPayload>> {
  // 非根恒为 self；子树范围只对根有效，前端不制造一次注定被拒的调用。
  const effectiveScope: 'self' | 'subtree' = viewer.isRoot ? scope : 'self'
  const data = await request<MemoryPayload>(
    `/v1/memory/entries?scope=${effectiveScope}&q=${encodeURIComponent(query)}`,
  )
  return { data, source: 'live' }
}

export async function exportMemory(
  scope: 'self' | 'subtree',
  format: 'json' | 'md' = 'json',
): Promise<AccountResult<{ content: string; filename: string; entries: number }>> {
  const data = await request<{ content: string; filename: string; entries: number }>(
    '/v1/memory/export',
    { method: 'POST', body: JSON.stringify({ scope, format }) },
  )
  return { data, source: 'live' }
}

export async function loadMemoryCustody(): Promise<AccountResult<MemoryCustodyPayload>> {
  const data = await request<MemoryCustodyPayload>('/v1/memory/custody')
  return { data, source: 'live' }
}

/** 纠正一条属于自己的记忆（`PATCH /v1/memory/entries/{id}`）。 */
export async function updateMemoryEntry(
  entryId: string,
  patch: { text?: string; source?: string; tags?: string[] },
): Promise<AccountResult<{ entry: MemoryEntry }>> {
  const data = await request<{ entry: MemoryEntry }>(
    `/v1/memory/entries/${encodeURIComponent(entryId)}`,
    { method: 'PATCH', body: JSON.stringify(patch) },
  )
  return { data, source: 'live' }
}

/** 删除一条属于自己的记忆（`DELETE /v1/memory/entries/{id}`，不可恢复）。 */
export async function deleteMemoryEntry(
  entryId: string,
): Promise<AccountResult<{ id: string; deleted: boolean; removed: number }>> {
  const data = await request<{ id: string; deleted: boolean; removed: number }>(
    `/v1/memory/entries/${encodeURIComponent(entryId)}`,
    { method: 'DELETE' },
  )
  return { data, source: 'live' }
}

/** 迁移隔离区一行（`account_memory_quarantine`，只有根账号能读）。 */
export interface QuarantinedMemoryRow {
  id: string
  source: string
  source_ref: string
  reason: string
  owner_hint: string
  payload: Record<string, unknown>
  created_at: string
}

/** `GET /v1/memory/unassigned`：无归属记忆 + 隔离区（根账号专属）。 */
export interface UnassignedMemoryPayload {
  entries: MemoryEntry[]
  count: number
  total: number
  limit: number
  offset: number
  has_more: boolean
  quarantined: QuarantinedMemoryRow[]
  quarantined_count: number
  quarantined_total: number
  quarantine_limit: number
  quarantine_offset: number
  note: string
}

export async function loadUnassignedMemory(
  options: {
    limit?: number
    offset?: number
    quarantineLimit?: number
    quarantineOffset?: number
  } = {},
): Promise<AccountResult<UnassignedMemoryPayload>> {
  const limit = Math.min(200, Math.max(1, Math.trunc(options.limit ?? 50) || 50))
  const offset = Math.max(0, Math.trunc(options.offset ?? 0) || 0)
  const quarantineLimit = Math.min(
    500,
    Math.max(1, Math.trunc(options.quarantineLimit ?? 50) || 50),
  )
  const quarantineOffset = Math.max(0, Math.trunc(options.quarantineOffset ?? 0) || 0)
  const data = await request<UnassignedMemoryPayload>(
    `/v1/memory/unassigned?limit=${limit}&offset=${offset}` +
      `&quarantine_limit=${quarantineLimit}&quarantine_offset=${quarantineOffset}`,
  )
  return { data, source: 'live' }
}

/** 删除一条无归属记忆（根专属，`DELETE /v1/memory/unassigned/{id}`）。 */
export async function deleteUnassignedMemory(
  entryId: string,
): Promise<AccountResult<{ id: string; deleted: boolean; removed: number }>> {
  const data = await request<{ id: string; deleted: boolean; removed: number }>(
    `/v1/memory/unassigned/${encodeURIComponent(entryId)}`,
    { method: 'DELETE' },
  )
  return { data, source: 'live' }
}

/** 删除一条迁移隔离记录（根专属，`DELETE /v1/memory/quarantine/{id}`）。 */
export async function deleteQuarantinedMemory(
  quarantineId: string,
): Promise<AccountResult<{ id: string; deleted: boolean; removed: number }>> {
  const data = await request<{ id: string; deleted: boolean; removed: number }>(
    `/v1/memory/quarantine/${encodeURIComponent(quarantineId)}`,
    { method: 'DELETE' },
  )
  return { data, source: 'live' }
}

export async function setMemoryCustody(
  enabled: boolean,
): Promise<AccountResult<MemoryCustodyPayload>> {
  const data = await request<MemoryCustodyPayload>('/v1/memory/custody', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  })
  return { data, source: 'live' }
}

/**
 * 轮换恢复码（根专属，`POST /v1/accounts/recovery/rotate`）。
 *
 * 旧码在服务端立即失效，明文只在本次响应里出现一次 —— 界面必须一次性展示。
 * 响应**没有** `expires_at`：恢复码没有 TTL，展示"有效期至"会骗用户。
 */
export function rotateRecovery(): Promise<
  AccountResult<{ recovery_code: string; notice?: string }>
> {
  return withStub(
    () =>
      request<{ recovery_code: string; notice?: string }>(
        '/v1/accounts/recovery/rotate',
        { method: 'POST' },
      ),
    () => ({
      recovery_code: 'STUB-RECOVERY',
      notice: '契约桩：恢复码未由服务端生成。',
    }),
  )
}

export interface DataImportResult {
  ok: boolean
  submitted: number
  imported: number
  deduplicated?: number
  skipped: number
  errors?: string[]
}

/**
 * 一键导出「我的全部数据」：后端返回真正生成的 ZIP（人可读 Markdown/CSV/JSON）。
 *
 * 不能用 `request`：它假定 JSON 响应，而这里要拿二进制。错误分支仍沿用
 * 同一套中性文案与 401 回登录页规则，避免多一条不一致的失败体验。
 */
export async function exportAllData(): Promise<{ blob: Blob; filename: string }> {
  const headers = tokenHeaders()
  headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch('/v1/accounts/export/all', {
      method: 'POST',
      headers,
      body: JSON.stringify({ include_files: true }),
    })
  } catch {
    throw new AccountApiError(0, null, LOCAL_SERVICE_OFFLINE_MANUAL)
  }
  if (!response.ok) {
    let code: string | null = null
    let message = ''
    try {
      const body = (await response.json()) as { code?: string; detail?: unknown }
      code = typeof body.code === 'string' ? body.code : null
      message = describeDetail(body.detail)
    } catch {
      message = await readErrorMessage(response)
    }
    if (response.status === 401) {
      clearSession()
      try {
        window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
      } catch {
        // 非浏览器环境：忽略。
      }
      throw new AccountApiError(401, code, '登录状态已失效，请重新登录')
    }
    throw new AccountApiError(response.status, code, message || `导出失败（${response.status}）`)
  }
  const disposition = response.headers.get('Content-Disposition') || ''
  const match = /filename="?([^";]+)"?/i.exec(disposition)
  return {
    blob: await response.blob(),
    filename: match?.[1] || 'liuhao-my-data.zip',
  }
}

/** 回导记忆：输入是 `memories.json` 的 `entries` 数组。 */
export async function importMemories(
  entries: unknown[],
): Promise<AccountResult<DataImportResult>> {
  const data = await request<DataImportResult>('/v1/memory/import', {
    method: 'POST',
    body: JSON.stringify({ entries }),
  })
  return { data, source: 'live' }
}

/** 回导知识库：输入是 `knowledge.json` 的 `documents` 数组。 */
export async function importKnowledge(
  documents: unknown[],
): Promise<AccountResult<DataImportResult>> {
  const data = await request<DataImportResult>('/v1/knowledge/import', {
    method: 'POST',
    body: JSON.stringify({ documents }),
  })
  return { data, source: 'live' }
}

/** 账号面统一的异步读取：失败原样暴露，不吞成空数据。
 *
 * `load` 返回 `null` 表示"当前身份不该发这个请求"（例如非根不取树），
 * 此时保持空态且不制造一次注定被拒的调用。
 */
export function useAccountData<T>(
  load: () => Promise<AccountResult<T>> | null,
  /**
   * 触发重新拉取的标识（例如登录主体）。登录发生在组件挂载之后时，
   * 仅靠 tick 不会自动再取数 —— 那会让"可管理 N 位成员"永远停在 0。
   */
  key?: string | null,
) {
  const loader = useRef(load)
  loader.current = load
  const [data, setData] = useState<T | null>(null)
  const [source, setSource] = useState<DataSource>('live')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    const pending = loader.current()
    if (!pending) {
      setLoading(false)
      setData(null)
      return
    }
    pending.then(
      (result) => {
        if (!alive) return
        setData(result.data)
        setSource(result.source)
        setLoading(false)
      },
      (failure: unknown) => {
        if (!alive) return
        setData(null)
        setError(neutralDenial(failure))
        setLoading(false)
      },
    )
    return () => {
      alive = false
    }
  }, [tick, key])

  return {
    data,
    source,
    error,
    loading,
    reload: () => setTick((value) => value + 1),
  }
}
