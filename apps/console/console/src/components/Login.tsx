/**
 * 登录页 —— 桌面端 / 网页版 / 手机端共用同一个组件。
 *
 * 三端的差别只在留白与卡片宽度（由 os.css 的 data-mode 处理），表单与校验逻辑
 * 完全相同。这样"三个端都能登录"是同一段代码在三种断点下的结果，而不是三份
 * 会各自走偏的实现。
 *
 * 诚实性（NO-FAKE）
 * ----------------
 * `/v1/auth/config` 会告诉我们是否已经配置了凭据。没有配置时不显示一个"看起来
 * 能用但一定失败"的表单，而是直接说明原因与修复命令 —— 否则用户只会看到
 * "凭据无效"，然后去猜密码。
 */

import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import {
  AuthError,
  CLIENT_MODES,
  type AuthConfig,
  type ClientMode,
  type Session,
  loadAuthConfig,
  login,
} from '../lib/auth'
import {
  IconAlert,
  IconDesktop,
  IconEye,
  IconEyeOff,
  IconGlobe,
  IconLock,
  IconMobile,
} from './icons'

export interface LoginProps {
  /** 当前端的形态，登录时随请求上报（令牌里会记下来源端）。 */
  mode: ClientMode
  onSuccess: (session: Session) => void
}

const CLIENT_ICON = {
  desktop: IconDesktop,
  web: IconGlobe,
  mobile: IconMobile,
} as const

const CLIENT_LABEL: Record<ClientMode, string> = {
  desktop: '桌面端',
  web: '网页版',
  mobile: '手机端',
}

export function Login({ mode, onSuccess }: LoginProps) {
  const [principal, setPrincipal] = useState('')
  const [secret, setSecret] = useState('')
  const [remember, setRemember] = useState(true)
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [config, setConfig] = useState<AuthConfig | null>(null)

  useEffect(() => {
    let alive = true
    void loadAuthConfig().then((value) => {
      if (alive) setConfig(value)
    })
    return () => {
      alive = false
    }
  }, [])

  const submit = useCallback(
    async (event: FormEvent) => {
      event.preventDefault()
      if (busy) return
      setBusy(true)
      setError('')
      try {
        const session = await login(principal.trim(), secret, { remember, client: mode })
        onSuccess(session)
      } catch (err) {
        if (err instanceof AuthError) {
          setError(err.status === 429 ? err.message : err.message || '登录失败')
        } else {
          setError(err instanceof Error ? err.message : '登录失败')
        }
        setSecret('')
      } finally {
        setBusy(false)
      }
    },
    [busy, mode, onSuccess, principal, remember, secret],
  )

  const notConfigured = config !== null && !config.secret_store.configured
  const noEligible =
    config !== null && config.secret_store.configured && config.login_eligible_humans === 0

  return (
    <div className="os-login">
      <form className="os-login-card" onSubmit={submit}>
        <div className="os-login-brand">
          <div className="os-login-mark">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 2.6l7.4 4.3v8.6L12 19.8 4.6 15.5V6.9z"
                stroke="#fff"
                strokeWidth="1.5"
                strokeLinejoin="round"
              />
              <circle cx="12" cy="12" r="2.6" fill="#fff" />
            </svg>
          </div>
          <div>
            <h1 className="os-login-title">LiuHao AI OS</h1>
            <p className="os-login-sub">全栈 AI 智能中枢 · 统一入口</p>
          </div>
        </div>

        {notConfigured && (
          <div className="os-note" role="status">
            <IconAlert size={15} />
            <span>
              {config?.message ||
                '尚未配置登录凭据，任何人都无法登录（fail-closed）。'}
              <br />
              <code>python scripts/register_human_identity.py --principal &lt;名&gt; --password-stdin</code>
            </span>
          </div>
        )}

        {noEligible && (
          <div className="os-note" role="status">
            <IconAlert size={15} />
            <span>
              {config?.message}
              <br />
              为要登录的主体设置凭据后即可登录。
            </span>
          </div>
        )}

        {error && (
          <div className="os-alert" role="alert">
            <IconAlert size={15} />
            <span>{error}</span>
          </div>
        )}

        <div className="os-field">
          <label htmlFor="os-principal">账号</label>
          <div className="os-input">
            <IconShieldMark />
            <input
              id="os-principal"
              name="username"
              autoComplete="username"
              placeholder="请输入账号"
              value={principal}
              onChange={(event) => setPrincipal(event.target.value)}
              required
              autoFocus
            />
          </div>
        </div>

        <div className="os-field">
          <label htmlFor="os-secret">密码</label>
          <div className="os-input">
            <IconLock size={16} />
            <input
              id="os-secret"
              name="password"
              type={reveal ? 'text' : 'password'}
              autoComplete="current-password"
              placeholder="请输入密码"
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              required
            />
            <button
              type="button"
              onClick={() => setReveal((value) => !value)}
              aria-label={reveal ? '隐藏密码' : '显示密码'}
              title={reveal ? '隐藏密码' : '显示密码'}
            >
              {reveal ? <IconEyeOff size={16} /> : <IconEye size={16} />}
            </button>
          </div>
        </div>

        <label className="os-check">
          <input
            type="checkbox"
            checked={remember}
            onChange={(event) => setRemember(event.target.checked)}
          />
          <span>记住我（在已安装的桌面端 / 手机端保持登录）</span>
        </label>

        <button
          type="submit"
          className="os-btn"
          disabled={busy || !principal.trim() || !secret}
        >
          {busy ? '正在登录…' : '登录'}
        </button>

        <div className="os-login-clients">
          {CLIENT_MODES.map((client) => {
            const Icon = CLIENT_ICON[client]
            return (
              <div
                key={client}
                className="os-client-chip"
                data-active={client === mode ? 'true' : 'false'}
                title={`当前以${CLIENT_LABEL[client]}形态打开`}
              >
                <Icon size={15} />
                <span>{CLIENT_LABEL[client]}</span>
              </div>
            )
          })}
        </div>

        <div className="os-login-foot">
          同意以同一账号在桌面端、网页版与手机端登录 —— 三端共用同一会话，
          权限判断始终在服务端完成。
          {config && (
            <>
              <br />
              已登记人类：{config.registered_humans} · 可登录：{config.login_eligible_humans} ·
              会话有效期：{Math.round(config.token.ttl_seconds / 3600)} 小时
            </>
          )}
        </div>
      </form>
    </div>
  )
}

/** 账号输入框里的小徽标（与品牌 mark 同形，弱化处理）。 */
function IconShieldMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 3.2l6.4 3.7v7.4L12 18.8l-6.4-3.7V6.9z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        opacity="0.7"
      />
    </svg>
  )
}
