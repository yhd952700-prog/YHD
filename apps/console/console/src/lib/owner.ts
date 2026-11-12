/**
 * Owner（超级管理员）数据层 —— `/v1/owner/*` 的真实响应。
 *
 * 只给 Owner 用：后端 `require_owner` 对非 Owner 一律 403，前端只做入口
 * 裁剪（`nav.ts` 里 `requires: 'root'` 的项），不做任何授权判定。
 * 没有真实数据时后端返回空列表 / 零计数，这里不补任何演示数据。
 */

import { apiFetch } from './api'

export interface OwnerMember {
  principal: string
  permissions: string[]
  status: string
  created_at?: string
  is_owner: boolean
  allow_superior_key_use?: boolean
  key_auth_source?: string
}

export interface OwnerOrigin {
  owner_principal: string | null
  origin: 'first_registration' | 'legacy_migration' | 'standalone_single_user' | 'none'
  owner_created_at?: string | null
  migration_notes: string[]
}

export interface OwnerMe extends OwnerOrigin {
  is_owner: boolean
  principal: string
  standalone: boolean
  member_count: number
}

export interface OwnerOverview {
  owner: OwnerOrigin
  members: { total: number; by_status: Record<string, number>; count: number }
  tasks: { goals: number; tasks: number; by_owner: Record<string, number>; checked: boolean }
  usage: Record<string, unknown> & { checked: boolean; reason?: string }
  audit: { events: number | null; checked: boolean }
}

export interface OwnerMembersPayload {
  members: OwnerMember[]
  count: number
}

export interface OwnerTaskItem {
  task_id?: string
  status: string
  assignees: string[]
}

export interface OwnerGoal {
  goal_id?: string
  owner_account_id: string
  state: string
  title: string
  tasks: OwnerTaskItem[]
}

export interface OwnerTasksPayload {
  goals: OwnerGoal[]
  goal_count: number
  task_count: number
  scope: string
  owners: string[]
}

export interface OwnerAuditEvent {
  seq: number
  event_id?: string
  event_type: string
  principal_id: string
  timestamp: number
  outcome: string
  action?: string | null
  target?: string | null
  details?: Record<string, unknown>
  is_self?: boolean
}

export interface OwnerAuditPayload {
  events: OwnerAuditEvent[]
  count: number
  read_scope: { mode: string; principals: string[] | 'all'; root_view: boolean }
  truncated: boolean
}

export async function loadOwnerMe(): Promise<OwnerMe> {
  return apiFetch<OwnerMe>('/v1/owner/me')
}

export async function loadOwnerOverview(): Promise<OwnerOverview> {
  return apiFetch<OwnerOverview>('/v1/owner/overview')
}

export async function loadOwnerMembers(): Promise<OwnerMembersPayload> {
  return apiFetch<OwnerMembersPayload>('/v1/owner/members')
}

export async function loadOwnerTasks(): Promise<OwnerTasksPayload> {
  return apiFetch<OwnerTasksPayload>('/v1/owner/tasks')
}

export async function loadOwnerAudit(limit = 100): Promise<OwnerAuditPayload> {
  return apiFetch<OwnerAuditPayload>(`/v1/owner/audit?limit=${limit}&reverse=true`)
}

/** Owner 邀请成员：成功返回一次性激活码（只出现这一次）。 */
export async function inviteOwnerMember(input: {
  principal: string
  display_name?: string
  role?: string
  permissions?: string[]
}): Promise<{ account: OwnerMember; activation_code: string; notice: string }> {
  return apiFetch<{ account: OwnerMember; activation_code: string; notice: string }>(
    '/v1/owner/members',
    {
      method: 'POST',
      body: JSON.stringify({
        principal: input.principal,
        display_name: input.display_name || undefined,
        role: input.role || undefined,
        permissions: input.permissions,
      }),
    },
  )
}

/** Owner 改成员权限 / 状态（目标是 Owner 本人时后端 403）。 */
export async function updateOwnerMemberPermissions(
  principal: string,
  grant: string[],
  revoke: string[],
): Promise<{ account: OwnerMember }> {
  return apiFetch<{ account: OwnerMember }>(
    `/v1/owner/members/${encodeURIComponent(principal)}/permissions`,
    { method: 'PATCH', body: JSON.stringify({ grant, revoke }) },
  )
}

export async function updateOwnerMemberStatus(
  principal: string,
  status: 'active' | 'suspended' | 'deleted',
  reason = '',
): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(
    `/v1/owner/members/${encodeURIComponent(principal)}/status`,
    { method: 'PATCH', body: JSON.stringify({ status, reason }) },
  )
}
