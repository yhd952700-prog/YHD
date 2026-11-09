/**
 * 账号安全 —— 本人自助改密。
 *
 * 为什么这个页面存在
 * ------------------
 * 后端 `POST /v1/accounts/{principal}/password` 早已可用，但此前只被"管理员重置
 * 成员密码"调用；没有任何界面让**普通用户改自己的密码**。忘记密码只能走恢复码，
 * 知道密码却不能主动换掉它。
 *
 * 安全边界
 * --------
 * * 自助改密必须同时提交当前密码：只拿到令牌不足以静默换密。
 * * 改完服务端立即吊销本人旧令牌（bump epoch），所以这里如实提示"请用新密码
 *   重新登录"，不假装当前会话还能继续。
 * * 只读查看模式下禁用，与成员页的写操作规则一致。
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import type { AccountProfile } from '../lib/accountCaps'
import { changeOwnPassword } from '../lib/accounts'
import { Card } from '../components/ui'

export function AccountSecurity({
  profile,
  readOnly,
}: {
  profile: AccountProfile
  readOnly: boolean
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    if (!current.trim()) {
      setNotice({ tone: 'error', text: '请输入当前密码' })
      return
    }
    if (next.length < 8) {
      setNotice({ tone: 'error', text: '新密码至少 8 位' })
      return
    }
    if (!next.trim()) {
      setNotice({ tone: 'error', text: '密码不能为空白' })
      return
    }
    if (current === next) {
      setNotice({ tone: 'error', text: '新密码必须与当前密码不同' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const result = await changeOwnPassword(profile.principal, current, next)
      setCurrent('')
      setNext('')
      setNotice({
        tone: 'ok',
        text: result.data.notice ?? '密码已修改，当前登录凭据已失效，请用新密码重新登录。',
      })
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '改密失败',
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="os-grid">
      <Card
        title="修改我的密码"
        sub={`当前账号：${profile.principal}。修改后旧密码立即失效，当前登录也会退出。`}
        span={12}
      >
        {notice && (
          <div
            className={notice.tone === 'ok' ? 'os-note' : 'os-alert'}
            role={notice.tone === 'ok' ? 'status' : 'alert'}
          >
            {notice.text}
          </div>
        )}
        <form className="os-field" onSubmit={(event) => void submit(event)}>
          <label htmlFor="self-current-password">当前密码</label>
          <input
            id="self-current-password"
            className="os-input-plain"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            placeholder="用于证明这是你本人"
            disabled={readOnly || busy}
          />
          <label htmlFor="self-new-password">新密码</label>
          <input
            id="self-new-password"
            className="os-input-plain"
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(event) => setNext(event.target.value)}
            placeholder="至少 8 位，不能全是空格"
            disabled={readOnly || busy}
          />
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="os-btn"
              style={{ width: 'auto' }}
              type="submit"
              disabled={readOnly || busy}
            >
              {busy ? '正在修改…' : '修改密码'}
            </button>
          </div>
        </form>
        <div className="os-note">
          忘记密码时请用「恢复码」重置；管理员重置其他成员密码请到「成员与权限管理」。
        </div>
      </Card>
    </div>
  )
}
