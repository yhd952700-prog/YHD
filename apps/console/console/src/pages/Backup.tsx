/**
 * 备份导出 / 迁移认领（根专属）。
 *
 * 导出前必须二次确认，确认框列出范围与人数；口令只用于本次加密，不落盘、
 * 不写日志。导入只能在**全新实例**（本机还没有任何账号）首次启动时执行；
 * 本机已有账号时不提供导入入口，也不做任何覆盖。
 *
 * 后端 `account_backup.py` 已落地：导出返回真实 `manifest`（账号数 / 文件数 /
 * 字节数），前端按实际值回报；不再有契约桩，也没有成功态占位。
 */

import { useState } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { exportBackup } from '../lib/accounts'
import { Card } from '../components/ui'

export function Backup({
  profile,
  caps,
  readOnly,
}: {
  profile: AccountProfile
  caps: AccountCaps
  readOnly: boolean
}) {
  const [scope, setScope] = useState<'all' | 'self'>('all')
  const [passphrase, setPassphrase] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  if (!caps.canBackup) {
    return (
      <Card title="备份与迁移" sub="当前账号没有该入口" span={12}>
        <div className="os-empty">无权执行该操作</div>
      </Card>
    )
  }

  const doExport = async () => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    if (passphrase.length < 8) {
      setNotice({ tone: 'error', text: '口令至少 8 位' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const result = await exportBackup(scope, passphrase)
      const blob = new Blob([JSON.stringify(result.data, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `liuhao-backup-${scope}-${Date.now()}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      const manifest = result.data.manifest
      setNotice({
        tone: 'ok',
        text: manifest
          ? `导出完成：${manifest.member_count} 个账号 / ${manifest.files} 个文件 / ${_bytes(manifest.bytes)}，请把文件保存到离线介质`
          : '导出完成，请把文件保存到离线介质',
      })
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '导出失败',
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="os-grid">
      <Card
        title="导出备份"
        sub="整棵树 = 成员身份、设置、Key 密文与全部成员数据；口令只用于本次加密。"
        span={6}
      >
        {notice && (
          <div className={notice.tone === 'ok' ? 'os-note' : 'os-alert'} role="status">
            {notice.text}
          </div>
        )}
        <div className="os-field">
          <label htmlFor="backup-scope">导出范围</label>
          <select
            id="backup-scope"
            className="os-input-plain"
            value={scope}
            onChange={(event) => setScope(event.target.value as 'all' | 'self')}
          >
            <option value="all">整棵树</option>
            <option value="self">仅我的账号</option>
          </select>
        </div>
        <div className="os-field">
          <label htmlFor="backup-pass">口令</label>
          <input
            id="backup-pass"
            className="os-input-plain"
            type="password"
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
            placeholder="至少 8 位；不落盘、不写日志"
          />
        </div>

        {/* 二次确认：把范围与代价写在按钮之前，而不是导出后再解释 */}
        <div className="os-note" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>
          <strong style={{ fontSize: 13 }}>这次导出会包含</strong>
          <span style={{ fontSize: 12, lineHeight: 1.7 }}>
            · {scope === 'all' ? `整棵树共 ${profile.memberCount + 1} 个账号` : '仅你本人的账号'}
            <br />· 全部会话、记忆、文件、任务与 Key 密文
            <br />· 文件数与体积：以导出包内真实统计为准（完成后再列出）
          </span>
        </div>

        <label className="os-check">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            disabled={readOnly}
          />
          <span>我知道导出包包含他人的个人数据，会妥善保管。</span>
        </label>

        <button
          className="os-btn"
          disabled={readOnly || busy || passphrase.length < 8 || !confirmed}
          onClick={() => void doExport()}
        >
          {busy ? '正在导出…' : '导出并下载'}
        </button>
      </Card>

      <Card
        title="从备份迁移到本机"
        sub="只在全新实例（本机还没有任何账号）首次启动时可用。"
        span={6}
      >
        <div className="os-note" role="status">
          本机已有账号，备份包无法在这里导入，现有数据也不会被覆盖。若要整树恢复，请在一台全新的、
          还没有任何账号的实例上启动，在登录页选择「从备份迁移到本机」。
        </div>
        <div className="os-kv-col" style={{ marginTop: 10 }}>
          <span className="os-kv-key">为什么不能导入</span>
          <span>
            导入会整树改写身份与数据，因此后端只允许在没有账号的新实例上执行一次；已有账号时会直接拒绝。
          </span>
        </div>
      </Card>
    </div>
  )
}

function _bytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
