/**
 * 恢复码（根专属）。
 *
 * 后端只提供一个动作：`POST /v1/accounts/recovery/rotate`。它没有"读取当前恢复码"
 * 的端点，明文也只在轮换响应里出现一次。因此本页**不展示一个并不存在的当前值**，
 * 只提供轮换 + 一次性展示，并把"旧码立即失效"写在按钮之前。
 *
 * 与文档的差异见 `docs/account-ui-ia.md` C6/C8：没有加密文件端点，
 * 下载内容仅为明文恢复码，界面如实标注。
 */

import { useState } from 'react'
import type { AccountCaps } from '../lib/accountCaps'
import type { DataSource } from '../lib/accounts'
import { rotateRecovery } from '../lib/accounts'
import { Card } from '../components/ui'
import { OneTimeSecret, StubBadge } from '../components/AccountChrome'

export function Recovery({ caps, readOnly }: { caps: AccountCaps; readOnly: boolean }) {
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [issued, setIssued] = useState<{
    code: string
    notice?: string
  } | null>(null)
  const [source, setSource] = useState<DataSource>('live')
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  if (!caps.canRecovery) {
    return (
      <Card title="恢复码" sub="当前账号没有该入口" span={12}>
        <div className="os-empty">无权执行该操作</div>
      </Card>
    )
  }

  const rotate = async () => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const result = await rotateRecovery()
      setSource(result.source)
      setIssued({
        code: result.data.recovery_code,
        notice: result.data.notice,
      })
      setNotice({ tone: 'ok', text: '已生成新的恢复码，旧码已失效' })
      setConfirmed(false)
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '轮换失败',
      })
    } finally {
      setBusy(false)
    }
  }

  const download = () => {
    if (!issued) return
    const payload = {
      format: 'liuhao-recovery-material',
      recovery_code: issued.code,
      generated_at: new Date().toISOString(),
      note: '此文件由本机界面生成，未加密。请保存到离线介质，不要放在同步盘。',
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `liuhao-recovery-${Date.now()}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="os-grid">
      <Card
        title="恢复码"
        sub="忘记密码时用它重置。轮换后旧码立即失效，明文只显示一次。"
        span={7}
        action={<StubBadge source={source} />}
      >
        {notice && (
          <div
            className={notice.tone === 'ok' ? 'os-note' : 'os-alert'}
            role={notice.tone === 'ok' ? 'status' : 'alert'}
          >
            {notice.text}
          </div>
        )}

        {issued ? (
          <>
            <OneTimeSecret
              label="新的恢复码"
              value={issued.code}
              notice={issued.notice ?? '恢复码没有有效期；使用一次后即失效。'}
              onDone={() => setIssued(null)}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button
                className="os-btn os-btn-ghost"
                style={{ width: 'auto' }}
                onClick={download}
              >
                下载恢复材料（未加密）
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">用途</span>
                <span className="os-kv-value">登录页"忘记密码"重置</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">次数</span>
                <span className="os-kv-value">每次轮换生成一个，使用一次后失效</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">保管</span>
                <span className="os-kv-value">离线介质；不要放在同步盘或聊天工具</span>
              </div>
            </div>

            <label className="os-check">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
                disabled={readOnly}
              />
              <span>我明白轮换后旧恢复码立即失效，需要重新保存。</span>
            </label>

            <button
              className="os-btn"
              disabled={readOnly || busy || !confirmed}
              onClick={() => void rotate()}
            >
              {busy ? '正在生成…' : '生成新的恢复码'}
            </button>
          </>
        )}
      </Card>

      <Card title="恢复码丢失时" sub="界面不承诺不存在的自动重建" span={5}>
        <ul className="os-list">
          <li>本机数据仍然保留，但无法用密码进入。</li>
          <li>其他仍能登录的账号不受影响，可继续处理日常事务。</li>
          <li>备份包只能在全新实例（本机还没有任何账号）首次启动时导入；本机已有账号时不可导入，也不会覆盖现有数据。</li>
          <li>老数据升级上来的账号可能从未有过恢复码；这种情况下登录页"忘记密码"里提供"在本机生成恢复码"，码只写入本机数据目录、不出现在网络响应里。</li>
          <li>既没有恢复码、也无法访问本机数据目录时，只能进入"重建身份体系"或联系为你提供本机部署的服务方。</li>
        </ul>
        <p className="os-hint">
          这里不画"忘记密码自动找回"按钮：恢复码没有 TTL，本机生成路径也必须以"能读到本机数据目录"为前提。
        </p>
      </Card>
    </div>
  )
}
