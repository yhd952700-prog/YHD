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
}

export function hireEmployee(body: HireEmployeeBody): Promise<Json> {
  return apiFetch<Json>('/v1/employees', {
    method: 'POST',
    body: JSON.stringify({
      name: body.name,
      agent_count: body.agent_count ?? 3,
      agent_types: body.agent_types ?? [],
    }),
  })
}

// ─── 创建并执行 Goal ─────────────────────────────────────────

export interface CreateGoalBody {
  natural_language: string
  scope?: string
  plan_mode?: string
  verification_criteria?: Json | null
}

export function createGoal(body: CreateGoalBody): Promise<Json> {
  return apiFetch<Json>('/v1/goals', {
    method: 'POST',
    body: JSON.stringify({
      natural_language: body.natural_language,
      scope: body.scope ?? 'L1',
      plan_mode: body.plan_mode ?? 'auto',
      verification_criteria: body.verification_criteria ?? null,
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

export interface AuditVerifyResult {
  /** 链完好为 true；被篡改 / 被截断 / 被分叉为 false */
  ok: boolean
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
  }
}

export interface AuditSummary {
  total_events: number
  tail_seq: number
  by_outcome: Record<string, number>
  by_action: Record<string, number>
  distinct_correlation_ids: number
  generated_at: number
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

/** 跑**真实**的链校验。后端只 SELECT，从不修复；ok=false 就是被改过。 */
export function verifyAuditChain(): Promise<AuditVerifyResult> {
  return apiFetch<AuditVerifyResult>('/v1/audit/verify')
}

export function fetchAuditSummary(): Promise<AuditSummary> {
  return apiFetch<AuditSummary>('/v1/audit/summary')
}
