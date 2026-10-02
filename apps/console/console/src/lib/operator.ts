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
