/**
 * 自我升级的取数层（检查 → 下载 → 应用 → 回滚）。
 *
 * 服务端 `POST /v1/upgrade/apply` 与 `/rollback` 是**同步**执行：升级包含备份、
 * 落盘与启动自检，正常在数十秒到数分钟之间。这里必须显式放宽超时，否则前端
 * 15 秒就判超时，用户会以为失败并重复点击 —— 而服务端可能还在写入。
 *
 * 失败时不自己编原因：后端会把「更新源未配置 / 发布包校验失败 / 自检未通过」
 * 等真实情况放在 `detail.message` 里，由 `apiFetch` 转成可读文案。
 */

import { apiFetch } from './api'

/** 安装/回滚的最长等待：备份 + 写文件 + 启动自检 + 失败回滚。 */
const LONG_RUNNING_TIMEOUT_MS = 20 * 60 * 1000

export type UpgradeState =
  | 'idle'
  | 'checking'
  | 'applying'
  | 'available'
  | 'applied'
  | 'rolled_back'
  | 'failed'

export type CheckStatus =
  | 'update_available'
  | 'up_to_date'
  | 'unreachable'
  | 'invalid_manifest'
  | 'version_unknown'

export interface ReleaseView {
  version: string
  artifact: string
  sha256: string
  size: number
  notes?: string | null
  published_at?: string | null
}

export interface CheckView {
  status: CheckStatus
  message: string
  current_version: string | null
  latest_version: string | null
  release: ReleaseView | null
  source: string
  offline: boolean
  checked_at: string
}

export interface OperationView {
  id: string
  kind: 'apply' | 'rollback'
  status: string
  created_at: string
  from_version: string | null
  to_version: string | null
  backup_id: string | null
  reason: string
  error_category: string | null
}

export interface UpgradeStatus {
  ok: boolean
  source_configured: boolean
  source: string
  state: UpgradeState
  phase: string
  message: string
  current_version: string | null
  latest_version: string | null
  update_available: boolean
  last_check: CheckView | null
  last_operation: OperationView | null
  last_apply: OperationView | null
  last_rollback: OperationView | null
  error: { category: string; message: string } | null
  updated_at: string
}

export interface CheckResponse {
  ok: boolean
  mode: string
  source_configured: boolean
  check: CheckView
  status: UpgradeStatus
}

export interface ApplyResponse {
  ok: boolean
  mode: string
  warning: string
  apply: {
    record_id: string
    backup_id: string
    from_version: string
    to_version: string
    installed_files: number
    data_checked_files: number
    message: string
    self_check_passed: boolean
    self_check_issues: number
  }
  status: UpgradeStatus
}

export interface RollbackResponse {
  ok: boolean
  mode: string
  warning: string
  notice: string | null
  source_configured: boolean
  rollback: {
    record_id: string
    backup_id: string
    from_version: string
    to_version: string
    restored_files: number
    message: string
  }
  status: UpgradeStatus
}

export function fetchUpgradeStatus(): Promise<UpgradeStatus> {
  return apiFetch<UpgradeStatus>('/v1/upgrade/status')
}

export function checkForUpdate(): Promise<CheckResponse> {
  return apiFetch<CheckResponse>('/v1/upgrade/check', {
    method: 'POST',
    body: '{}',
  })
}

export function applyUpgrade(version: string): Promise<ApplyResponse> {
  return apiFetch<ApplyResponse>('/v1/upgrade/apply', {
    method: 'POST',
    body: JSON.stringify({ confirm: true, version }),
    signal: AbortSignal.timeout(LONG_RUNNING_TIMEOUT_MS),
  })
}

export function rollbackUpgrade(backupId?: string | null): Promise<RollbackResponse> {
  return apiFetch<RollbackResponse>('/v1/upgrade/rollback', {
    method: 'POST',
    body: JSON.stringify({ confirm: true, ...(backupId ? { backup_id: backupId } : {}) }),
    signal: AbortSignal.timeout(LONG_RUNNING_TIMEOUT_MS),
  })
}
