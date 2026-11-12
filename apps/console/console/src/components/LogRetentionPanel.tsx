/**
 * 日志占用与保留设置：让用户看到「占了多少、保留多久」，并能立即清理。
 *
 * 与 `src/gateway/log_settings.py` 的三个端点对应：
 *   GET  /v1/settings/logs        当前占用 + 生效中的保留值
 *   PUT  /v1/settings/logs        保存天数 / 上限（持久化，重启仍生效）
 *   POST /v1/settings/logs/prune  立即清理一轮
 *
 * 清理只删旧的日志备份（*.log.N），正在写入的活动日志不会被删除；界面上必须
 * 把这句话说清楚，避免用户以为「清理」会把当前日志也抹掉。
 */
import { useCallback, useEffect, useState } from 'react'
import { apiFetch, useApi } from '../lib/api'
import { formatBytes } from '../lib/knowledge'

const MIB = 1024 * 1024

interface LogState {
  total_bytes: number
  active_bytes: number
  backup_bytes: number
  active_count: number
  backup_count: number
  retention_days: number
  max_total_bytes: number
  env_override: { retention_days: boolean; max_total_bytes: boolean }
  note: string
  last_report: {
    generated_at?: string
    status?: string
    notice?: string
    deleted?: unknown[]
    failed?: { name: string; reason: string }[]
  } | null
}

interface PruneResult {
  state: LogState
  report: {
    status: string
    notice?: string
    deleted?: unknown[]
    failed?: { name: string; reason: string }[]
  }
}

type Tone = 'ok' | 'warn' | 'danger' | 'muted'

interface Message {
  tone: Tone
  text: string
}

function toneFor(message: PruneResult['report']): Tone {
  if ((message.failed?.length ?? 0) > 0) return 'warn'
  return 'ok'
}

export interface LogRetentionPanelProps {
  /** 成员视角只读：能看占用与策略，保存/清理仍只由根账号执行。 */
  canManage?: boolean
}

export function LogRetentionPanel({ canManage = true }: LogRetentionPanelProps) {
  const state = useApi<LogState>('/v1/settings/logs', 0)
  const [days, setDays] = useState('')
  const [mib, setMib] = useState('')
  const [busy, setBusy] = useState<'save' | 'prune' | null>(null)
  const [message, setMessage] = useState<Message | null>(null)
  const reload = state.reload

  useEffect(() => {
    if (!state.data) return
    setDays(String(state.data.retention_days))
    setMib(String(Math.round(state.data.max_total_bytes / MIB)))
  }, [state.data])

  const save = useCallback(async () => {
    const parsedDays = Number(days)
    const parsedMib = Number(mib)
    if (!Number.isInteger(parsedDays) || parsedDays < 0) {
      setMessage({ tone: 'danger', text: '保留天数需要是 0 或正整数（0 表示不按天清理）。' })
      return
    }
    if (!Number.isFinite(parsedMib) || parsedMib < 0) {
      setMessage({ tone: 'danger', text: '上限需要是 0 或正数（0 表示不按量清理）。' })
      return
    }
    setBusy('save')
    setMessage(null)
    try {
      await apiFetch<LogState>('/v1/settings/logs', {
        method: 'PUT',
        body: JSON.stringify({
          retention_days: parsedDays,
          max_total_bytes: Math.round(parsedMib * MIB),
        }),
      })
      reload()
      setMessage({ tone: 'ok', text: '已保存，重启后仍然生效。' })
    } catch (err) {
      setMessage({
        tone: 'danger',
        text: err instanceof Error ? err.message : '保存失败，请稍后重试。',
      })
    } finally {
      setBusy(null)
    }
  }, [days, mib, reload])

  const prune = useCallback(async () => {
    setBusy('prune')
    setMessage(null)
    try {
      const result = await apiFetch<PruneResult>('/v1/settings/logs/prune', { method: 'POST' })
      reload()
      const deleted = result.report.deleted?.length ?? 0
      setMessage({
        tone: toneFor(result.report),
        text:
          result.report.notice ??
          (deleted > 0
            ? `已清理 ${deleted} 份日志备份，正在写入的日志未受影响。`
            : '没有需要清理的日志备份。'),
      })
    } catch (err) {
      setMessage({
        tone: 'danger',
        text: err instanceof Error ? err.message : '清理失败，请稍后重试。',
      })
    } finally {
      setBusy(null)
    }
  }, [reload])

  if (state.error && !state.data) {
    return (
      <p className="os-alert" role="alert">
        读取日志占用失败：{state.error}
      </p>
    )
  }

  const data = state.data
  const effectiveDays = data ? data.retention_days : '—'
  const effectiveMib = data ? Math.round(data.max_total_bytes / MIB) : '—'
  const report = data?.last_report

  return (
    <div>
      {!canManage && (
        <p className="os-note" role="note">
          该设置由系统统一管理，当前账号只读。
        </p>
      )}
      <p>
        {data ? (
          <>
            日志占用 <strong>{formatBytes(data.total_bytes)}</strong>，保留 {effectiveDays} 天 / 上限{' '}
            {effectiveMib} MiB。
          </>
        ) : (
          '正在读取日志占用…'
        )}
      </p>
      {data && data.backup_count > 0 ? (
        <p className="os-note">
          其中正在写入的日志 {formatBytes(data.active_bytes)}，可清理的旧备份{' '}
          {formatBytes(data.backup_bytes)}（{data.backup_count} 份）。
        </p>
      ) : null}

      <div className="os-form">
        <label className="os-field">
          <span>保留天数</span>
          <input
            className="os-input"
            type="number"
            min={0}
            max={3650}
            value={days}
            disabled={!canManage || busy !== null || state.loading}
            onChange={(event) => setDays(event.target.value)}
          />
        </label>
        <label className="os-field">
          <span>总量上限（MiB）</span>
          <input
            className="os-input"
            type="number"
            min={0}
            value={mib}
            disabled={!canManage || busy !== null || state.loading}
            onChange={(event) => setMib(event.target.value)}
          />
        </label>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="os-btn os-btn-primary"
            onClick={save}
            disabled={!canManage || busy !== null || state.loading}
          >
            {busy === 'save' ? '正在保存…' : '保存设置'}
          </button>
          <button className="os-btn os-btn-ghost" onClick={prune} disabled={!canManage || busy !== null}>
            {busy === 'prune' ? '正在清理…' : '立即清理'}
          </button>
        </div>
      </div>

      {data?.env_override.retention_days || data?.env_override.max_total_bytes ? (
        <p className="os-alert" role="alert">
          {data.env_override.retention_days ? '保留天数' : ''}
          {data.env_override.retention_days && data.env_override.max_total_bytes ? '与' : ''}
          {data.env_override.max_total_bytes ? '总量上限' : ''}
          正被环境变量覆盖，界面保存的值不会生效；要改请调整启动环境。
        </p>
      ) : null}

      <p className="os-note">{data?.note ?? '只清理旧的日志备份（*.log.N），正在写入的日志不会删除。'}</p>

      {message ? (
        <p className="os-note" role="status" data-tone={message.tone}>
          {message.text}
        </p>
      ) : null}

      {report ? (
        <p className="os-note" data-tone={report.status === '部分完成' ? 'warn' : 'ok'}>
          上次清理{report.generated_at ? `（${report.generated_at}）` : ''}：
          {report.failed && report.failed.length > 0
            ? `${report.failed.length} 份未能删除，可稍后重试。`
            : (report.notice ?? '已完成。')}
        </p>
      ) : null}
    </div>
  )
}
