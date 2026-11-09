/**
 * 软件更新面板 —— 检查更新 / 下载并安装 / 回滚。
 *
 * 三条硬规则：
 * 1. **每个按钮都必须有结果**。没有配置更新源时，检查按钮直接禁用并把原因写在
 *    旁边，而不是让用户点了以后只看到一句通用报错。
 * 2. **安装完成不等于已生效**。升级写的是磁盘文件，当前进程仍是旧代码，所以
 *    成功后必须明确要求重启，不能显示成"已是最新"。
 * 3. **失败要能被照着做**。回滚尝试、校验失败、断网各自给出不同的下一步；
 *    统一成"升级失败，请重试"等于什么都没说。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../lib/api'
import { detectClient } from '../lib/mode'
import type { BackupView, UpgradeStatus } from '../lib/upgrade'
import {
  applyUpgrade,
  checkForUpdate,
  fetchUpgradeStatus,
  rollbackUpgrade,
} from '../lib/upgrade'

type Busy = 'check' | 'apply' | 'rollback' | null
type Tone = 'ok' | 'warn' | 'danger' | 'muted'
interface Notice {
  tone: Tone
  message: string
}

const STATE_LABEL: Record<string, string> = {
  idle: '待检查',
  checking: '检查中',
  applying: '安装中',
  available: '有新版本',
  applied: '已安装（重启后生效）',
  rolled_back: '已回滚',
  failed: '上次未成功',
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN')
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return err.message
  return err instanceof Error ? err.message : '操作没有完成，请稍后重试。'
}

/** 备份与数据是否同盘：同盘必须直说"会一起丢"，不能让用户误以为已经安全。
 *  手机端不展示本机的盘符与绝对路径，只保留结论。 */
function backupVolumeText(backup: BackupView, hideMachinePaths = false): string {
  if (backup.same_volume) {
    const drive = hideMachinePaths ? '' : backup.backup_drive || backup.data_drive
    return `与数据目录在同一块盘${drive ? `（${drive}）` : ''}：这块盘坏了，数据和备份会一起丢。`
  }
  if (hideMachinePaths) {
    return '与数据目录不在同一块盘：单块盘损坏不会同时丢掉数据和备份。'
  }
  const drives = [
    backup.data_drive ? `数据在 ${backup.data_drive}` : '',
    backup.backup_drive ? `备份在 ${backup.backup_drive}` : '',
  ]
    .filter(Boolean)
    .join(' · ')
  return `与数据目录不在同一块盘${drives ? `（${drives}）` : ''}：单块盘损坏不会同时丢掉数据和备份。`
}

export function UpgradePanel() {
  const [status, setStatus] = useState<UpgradeStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<Busy>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const next = await fetchUpgradeStatus()
      if (alive.current) setStatus(next)
    } catch (err) {
      if (alive.current) setNotice({ tone: 'danger', message: errorText(err) })
    } finally {
      if (alive.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    alive.current = true
    void refresh()
    return () => {
      alive.current = false
    }
  }, [refresh])

  // 安装期间同步服务端进度：同步接口要等几十秒到几分钟，界面不能只有"转圈"。
  useEffect(() => {
    if (busy !== 'apply' && busy !== 'rollback') return
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(timer)
  }, [busy, refresh])

  async function onCheck() {
    setBusy('check')
    setNotice(null)
    try {
      const result = await checkForUpdate()
      setStatus(result.status)
      const check = result.check
      if (check.status === 'update_available' && check.latest_version) {
        setNotice({
          tone: 'ok',
          message: `发现新版本 ${check.latest_version}。安装在后台进行，期间请不要关闭软件。`,
        })
      } else if (check.status === 'up_to_date') {
        setNotice({ tone: 'muted', message: '当前已是最新版本。' })
      } else {
        setNotice({ tone: 'warn', message: check.message || '这次没有检查成功，请稍后重试。' })
      }
    } catch (err) {
      setNotice({ tone: 'danger', message: errorText(err) })
      await refresh()
    } finally {
      setBusy(null)
    }
  }

  async function onApply(version: string) {
    if (!window.confirm(`确认安装 ${version}？安装期间请不要关闭软件；安装完成后需要重启才会生效，失败会尝试回滚，但可能需要手动修复。`)) return
    setBusy('apply')
    setNotice({ tone: 'muted', message: `正在安装 ${version}，可能需要几分钟，请不要关闭软件。` })
    try {
      const result = await applyUpgrade(version)
      setStatus(result.status)
      setNotice({
        tone: 'ok',
        message: `已安装 ${result.apply.to_version}（备份编号 ${result.apply.backup_id}）。请关闭并重新启动鎏灏，重启后版本才会变成新版本；如果新版本有问题，可在下面一键回滚。`,
      })
    } catch (err) {
      // 超时/断开不代表服务端没做完：重新取一次状态，按真实结果说话。
      await refresh()
      setNotice({ tone: 'danger', message: errorText(err) })
    } finally {
      setBusy(null)
    }
  }

  async function onRollback() {
    if (!window.confirm('确认回滚到升级前的版本？回滚会恢复升级前的程序文件，数据目录保持不变。')) {
      return
    }
    setBusy('rollback')
    setNotice({ tone: 'muted', message: '正在回滚，请不要关闭软件。' })
    try {
      const result = await rollbackUpgrade(status?.last_apply?.backup_id ?? null)
      setStatus(result.status)
      setNotice({
        tone: 'ok',
        message: `已回滚到 ${result.rollback.to_version}。请关闭并重新启动鎏灏，重启后生效。`,
      })
    } catch (err) {
      await refresh()
      setNotice({ tone: 'danger', message: errorText(err) })
    } finally {
      setBusy(null)
    }
  }

  if (loading && !status) {
    return <div className="os-note">正在读取当前版本…</div>
  }

  if (!status) {
    return (
      <div className="os-alert" role="alert">
        没有读取到版本信息。请确认鎏灏仍在运行后刷新页面。
      </div>
    )
  }

  const sourceReady = status.source_configured
  const latest = status.latest_version
  const canApply = status.update_available && Boolean(latest)
  const canRollback =
    status.last_apply !== null &&
    status.last_operation?.kind === 'apply' &&
    status.last_operation?.status === 'completed'
  const stateLabel = STATE_LABEL[status.state] ?? status.state
  const backup = status.backup ?? null
  // 手机端不该看到主机的盘符与绝对路径；也不能因为长路径把卡片撑成逐字竖排。
  const hideMachinePaths = detectClient() === 'mobile'

  return (
    <div className="os-list" style={{ minWidth: 0 }}>
      <div className="os-row">
        <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
        <div className="os-row-main" style={{ minWidth: 0 }}>
          <div className="os-row-title">当前版本 {status.current_version ?? '未知'}</div>
          <div className="os-row-sub" style={{ overflowWrap: 'anywhere' }}>
            更新源：{sourceReady ? status.source || '已配置' : '未配置'} · 最近检查：
            {formatTime(status.last_check?.checked_at)}
          </div>
        </div>
        <span className="os-badge" data-tone={status.state === 'failed' ? 'warn' : 'muted'}>
          {stateLabel}
        </span>
      </div>

      {backup && (
        <div
          className={backup.same_volume ? 'os-alert' : 'os-note'}
          role={backup.same_volume ? 'alert' : undefined}
          data-testid="upgrade-backup-info"
          style={{ marginTop: 12, minWidth: 0, overflowWrap: 'anywhere' }}
        >
          <div>
            <strong>备份位置</strong>：
            {hideMachinePaths ? '本机磁盘上的备份目录（手机端不显示本机路径）' : backup.root}
          </div>
          <div style={{ marginTop: 6 }}>{backupVolumeText(backup, hideMachinePaths)}</div>
          <div style={{ marginTop: 6 }}>
            为什么放在数据目录外：数据目录被删、或它所在的盘坏了，放在里面的备份会一起消失——那正是最需要备份的时候。
          </div>
          <div style={{ marginTop: 6 }}>
            换机时带走：
            {hideMachinePaths
              ? '在电脑上把整个备份目录复制走'
              : `把 ${backup.root} 整个目录复制走`}
            （只带数据目录会丢掉升级前的回滚备份）。
          </div>
          <div style={{ marginTop: 6 }}>
            最近一次备份：
            {backup.last_backup_at
              ? `${formatTime(backup.last_backup_at)}（共 ${backup.backup_count} 份）`
              : '还没有升级备份（首次升级前会自动生成）。'}
          </div>
          {backup.migration && backup.migration.moved > 0 && (
            <div style={{ marginTop: 6 }}>
              {hideMachinePaths
                ? `旧位置的 ${backup.migration.moved} 份备份已自动搬到新位置。`
                : `旧位置 ${backup.migration.from} 的 ${backup.migration.moved} 份备份已自动搬到新位置。`}
            </div>
          )}
        </div>
      )}

      {!sourceReady && (
        <div className="os-note" style={{ marginTop: 12 }}>
          <div>
            安装者还没有为本机配置更新源，所以自动检查暂不可用；这里的「检查更新」按钮会保持禁用，不会出现点了没反应的情况。
          </div>
          <div style={{ marginTop: 8 }}>
            <strong>手动更新（覆盖安装）</strong>：关闭鎏灏后运行新版本的安装包，按提示覆盖安装即可。程序文件会更新，数据目录（会话、知识库、记忆等）不会被覆盖。
          </div>
        </div>
      )}

      {sourceReady && canApply && latest && (
        <div className="os-note" style={{ marginTop: 12 }}>
          可升级到 <strong>{latest}</strong>。
          {status.last_check?.release?.notes ? ` ${status.last_check.release.notes}` : ''}
        </div>
      )}

      {sourceReady && !canApply && status.last_check && (
        <div className="os-note" style={{ marginTop: 12 }}>
          {status.last_check.message || '上次检查没有发现可用更新。'}
          {status.last_check.offline ? '（本机当前连不上更新源，这次结果不代表真的没有新版本。）' : ''}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
        <button
          className="os-btn"
          onClick={() => void onCheck()}
          disabled={busy !== null || !sourceReady}
          title={sourceReady ? '检查更新源上是否有新版本' : '未配置更新源，请使用手动覆盖安装'}
        >
          {busy === 'check' ? '正在检查…' : '检查更新'}
        </button>
        {canApply && latest && (
          <button
            className="os-btn"
            onClick={() => void onApply(latest)}
            disabled={busy !== null}
          >
            {busy === 'apply' ? '正在下载并安装…' : `下载并安装 ${latest}`}
          </button>
        )}
        {canRollback && (
          <button
            className="os-btn os-btn-ghost"
            onClick={() => void onRollback()}
            disabled={busy !== null}
          >
            {busy === 'rollback' ? '正在回滚…' : '回滚到升级前版本'}
          </button>
        )}
      </div>

      {sourceReady && (
        <div className="os-note" style={{ marginTop: 10 }}>
          也可以手动更新（覆盖安装）：关闭鎏灏后运行新版本安装包；数据目录不受影响。
        </div>
      )}

      {status.error?.message && !notice && (
        <div className="os-alert" role="alert" style={{ marginTop: 12 }}>
          {status.error.message}
          {status.error.technical_detail ? (
            <details style={{ marginTop: 8 }}>
              <summary>技术详情</summary>
              <code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {status.error.technical_detail}
              </code>
            </details>
          ) : null}
        </div>
      )}

      {notice && (
        <div
          className={notice.tone === 'danger' ? 'os-alert' : 'os-note'}
          role={notice.tone === 'danger' ? 'alert' : 'status'}
          aria-live="polite"
          style={{ marginTop: 12, whiteSpace: 'pre-line' }}
        >
          {notice.message}
        </div>
      )}
    </div>
  )
}
