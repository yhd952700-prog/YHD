/**
 * 登录页 —— 桌面端 / 网页版 / 手机端共用同一个组件。
 *
 * 三端的差别只在留白与卡片宽度（由 os.css 的 data-mode 处理），表单与校验逻辑
 * 完全相同。这样"三个端都能登录"是同一段代码在三种断点下的结果，而不是三份
 * 会各自走偏的实现。
 *
 * 账号体系（docs/account-hierarchy.md §9.2–§9.5）
 * ------------------------------------------------
 * 同一个入口按状态机切换，不区分"谁在登录"：
 *   S0 首次使用（本机还没有账号）→ 创建初始账号 → 一次性恢复材料
 *   S1 正常登录
 *   S2 用邀请码首次设置密码（后端一步完成校验与设密，见 IA C2）
 *   S4 忘记密码 → 恢复码重置
 *   S5 恢复码也丢失 → 中性引导，不承诺不存在的自动重建
 *   迁移 → 从备份迁移到本机（仅本机无账号时出现）
 * 文案里不出现任何层级/上下级/账号类别字样。
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
  registerSelf,
  sessionFromToken,
} from '../lib/auth'
import { readErrorMessage } from '../lib/errorMessage'
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

type Stage =
  | 'login'
  | 'bootstrap'
  | 'recovery-material'
  | 'register'
  | 'recovery'
  | 'migrate'
  | 'lost'

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

async function postJson<T>(path: string, body: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new AuthError(0, '无法连接到服务，请检查网络或服务是否在运行')
  }
  if (!response.ok) {
    throw new AuthError(response.status, await readErrorMessage(response))
  }
  return (await response.json()) as T
}

export function Login({ mode, onSuccess }: LoginProps) {
  const [principal, setPrincipal] = useState('')
  const [secret, setSecret] = useState('')
  const [remember, setRemember] = useState(true)
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [stage, setStage] = useState<Stage>('login')

  useEffect(() => {
    let alive = true
    void loadAuthConfig().then((value) => {
      if (!alive) return
      setConfig(value)
      if (value && value.root_account_exists === false) setStage('bootstrap')
    })
    return () => {
      alive = false
    }
  }, [])

  const finish = useCallback(
    async (token: string) => {
      const session = await sessionFromToken(token, { remember, client: mode })
      onSuccess(session)
    },
    [mode, onSuccess, remember],
  )

  const submitLogin = useCallback(
    async (event: FormEvent) => {
      event.preventDefault()
      if (busy) return
      setBusy(true)
      setError('')
      try {
        const session = await login(principal.trim(), secret, { remember, client: mode })
        onSuccess(session)
      } catch (err) {
        setError(err instanceof AuthError ? err.message : '登录失败')
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
  const noAccount = config !== null && config.root_account_exists === false
  const registrationOpen =
    config !== null &&
    (config.registration_open === true || config.root_account_exists === false)

  return (
    <div className="os-login">
      {/* 桌面端左栏：只放真实信息（凭据状态、可登录人数、当前形态），
          不放"安全运行 99.9%"这类编造的 HUD 数字。手机/网页端整列隐藏。 */}
      <aside className="os-login-aside">
        <div className="os-login-aside-brand">
          <div className="os-login-aside-mark">
            <svg width="44" height="44" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 2.6l7.4 4.3v8.6L12 19.8 4.6 15.5V6.9z"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinejoin="round"
              />
              <circle cx="12" cy="12" r="2.6" fill="currentColor" />
            </svg>
          </div>
          <div>
            <div className="os-login-aside-title">鎏灏智能中枢</div>
            <div className="os-login-aside-en">LiuHao AI OS</div>
          </div>
        </div>

        <div className="os-login-status">
          <div className="os-login-status-row">
            <span>登录凭据</span>
            <span data-tone={config ? (notConfigured ? 'warn' : 'ok') : 'muted'}>
              {config ? (notConfigured ? '未配置（无法登录）' : '已配置') : '读取中…'}
            </span>
          </div>
          <div className="os-login-status-row">
            <span>可登录人类</span>
            <span>{config ? config.login_eligible_humans : '—'}</span>
          </div>
          <div className="os-login-status-row">
            <span>当前形态</span>
            <span>{CLIENT_LABEL[mode]}</span>
          </div>
          <div className="os-login-status-row">
            <span>会话有效期</span>
            <span>
              {config ? `${Math.round(config.token.ttl_seconds / 3600)} 小时` : '—'}
            </span>
          </div>
        </div>

        {notConfigured && (
          <p className="os-login-aside-note">
            未配置凭据时登录会 fail-closed。先运行下方命令登记人类身份，再回到这里登录。
          </p>
        )}
      </aside>

      <div className="os-login-card">
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
            <p className="os-login-sub">
              {stage === 'bootstrap'
                ? '首次使用 · 初始化本机'
                : stage === 'register'
                  ? '创建账户 · 直接进入系统'
                  : stage === 'recovery'
                    ? '重置密码'
                    : stage === 'migrate'
                      ? '从备份迁移到本机'
                      : '全栈 AI 智能中枢 · 统一入口'}
            </p>
          </div>
        </div>

        {notConfigured && (
          <div className="os-note" role="status">
            <IconAlert size={15} />
            <span>
              {config?.message || '尚未配置登录凭据，任何人都无法登录（fail-closed）。'}
              <br />
              <code>python scripts/register_human_identity.py（按提示输入账号名与密码）</code>
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

        {!noAccount && (stage === 'login' || stage === 'register') && (
          <div className="os-login-tabs" role="tablist" aria-label="登录或创建账户">
            <button
              type="button"
              role="tab"
              aria-selected={stage === 'login'}
              data-active={stage === 'login' ? 'true' : 'false'}
              onClick={() => {
                setStage('login')
                setError('')
              }}
            >
              登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={stage === 'register'}
              data-active={stage === 'register' ? 'true' : 'false'}
              onClick={() => {
                setStage('register')
                setError('')
              }}
            >
              创建账户
            </button>
          </div>
        )}

        {error && (
          <div className="os-alert" role="alert">
            <IconAlert size={15} />
            <span>{error}</span>
          </div>
        )}

        {stage === 'login' && (
          <form onSubmit={submitLogin}>
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

            <button type="submit" className="os-btn" disabled={busy || !principal.trim() || !secret}>
              {busy ? '正在登录…' : '登录'}
            </button>

            <div className="os-login-links">
              {registrationOpen && (
                <button type="button" className="os-link" onClick={() => setStage('bootstrap')}>
                  首次使用，创建本机账号
                </button>
              )}
              <button type="button" className="os-link" onClick={() => setStage('recovery')}>
                忘记密码
              </button>
              {noAccount && (
                <button type="button" className="os-link" onClick={() => setStage('migrate')}>
                  从备份迁移到本机
                </button>
              )}
            </div>
          </form>
        )}

        {stage === 'bootstrap' && (
          <BootstrapForm
            busy={busy}
            onBusy={setBusy}
            onError={setError}
            onRecovery={(recoveryCode, account) => {
              setPrincipal(account)
              setStage('recovery-material')
              setError('')
              ;(window as { __osRecovery?: string }).__osRecovery = recoveryCode
            }}
            onCancel={() => {
              if (!noAccount) setStage('login')
            }}
            canCancel={!noAccount}
          />
        )}

        {stage === 'recovery-material' && (
          <RecoveryMaterial
            code={(window as { __osRecovery?: string }).__osRecovery ?? ''}
            account={principal}
            onDone={() => {
              ;(window as { __osRecovery?: string }).__osRecovery = ''
              setStage('login')
            }}
          />
        )}

        {stage === 'register' && (
          <SelfRegisterForm
            busy={busy}
            remember={remember}
            mode={mode}
            onBusy={setBusy}
            onError={setError}
            onCancel={() => setStage('login')}
            onSuccess={onSuccess}
          />
        )}

        {stage === 'recovery' && (
          <RecoveryForm
            busy={busy}
            onBusy={setBusy}
            onError={setError}
            onCancel={() => setStage('login')}
            onLost={() => setStage('lost')}
            onToken={async (token) => {
              await finish(token)
            }}
          />
        )}

        {stage === 'migrate' && (
          <MigrateForm
            busy={busy}
            onBusy={setBusy}
            onError={setError}
            onCancel={() => setStage(noAccount ? 'bootstrap' : 'login')}
            onImported={() => {
              setStage('login')
              setError('')
            }}
          />
        )}

        {stage === 'lost' && (
          <div className="os-note" role="status" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>
            <strong style={{ fontSize: 13 }}>恢复码也丢失时</strong>
            <span style={{ fontSize: 12, lineHeight: 1.7 }}>
              本机数据仍然保留，但无法用密码进入。可尝试：
              <br />1. 用此前导出的加密备份包，在本机重新执行迁移认领；
              <br />2. 若没有备份包，联系为你提供本机部署的服务方协助处理。
            </span>
            <button type="button" className="os-link" onClick={() => setStage('login')}>
              返回登录
            </button>
          </div>
        )}

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
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 分步表单
// ---------------------------------------------------------------------------

function BootstrapForm({
  busy,
  onBusy,
  onError,
  onRecovery,
  onCancel,
  canCancel,
}: {
  busy: boolean
  onBusy: (value: boolean) => void
  onError: (message: string) => void
  onRecovery: (code: string, account: string) => void
  onCancel: () => void
  canCancel: boolean
}) {
  const [principal, setPrincipal] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (password.length < 8) {
      onError('密码至少 8 位')
      return
    }
    if (password !== confirm) {
      onError('两次输入的密码不一致')
      return
    }
    onBusy(true)
    onError('')
    try {
      const result = await postJson<{
        account: { principal: string }
        recovery_code: string
        notice: string
      }>('/v1/accounts/register', {
        principal: principal.trim(),
        password,
        display_name: displayName.trim() || undefined,
      })
      onRecovery(result.recovery_code, result.account.principal)
    } catch (err) {
      onError(err instanceof Error ? err.message : '初始化失败')
    } finally {
      onBusy(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="os-login-sub" style={{ marginBottom: 14 }}>
        这台机器还没有账号。创建的第一个账号拥有本机全部管理范围，请务必妥善保存随后显示的恢复材料。
      </p>
      <div className="os-field">
        <label htmlFor="boot-principal">账号</label>
        <input
          id="boot-principal"
          className="os-input-plain"
          value={principal}
          onChange={(event) => setPrincipal(event.target.value)}
          placeholder="字母或数字，登录时使用"
          required
          autoFocus
        />
      </div>
      <div className="os-field">
        <label htmlFor="boot-name">显示名（可选）</label>
        <input
          id="boot-name"
          className="os-input-plain"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
          placeholder="界面上显示的名字"
        />
      </div>
      <div className="os-field">
        <label htmlFor="boot-password">密码</label>
        <input
          id="boot-password"
          className="os-input-plain"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="至少 8 位，建议 10 位以上"
          required
        />
      </div>
      <div className="os-field">
        <label htmlFor="boot-confirm">确认密码</label>
        <input
          id="boot-confirm"
          className="os-input-plain"
          type="password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          required
        />
      </div>
      <label className="os-check">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(event) => setAcknowledged(event.target.checked)}
        />
        <span>我知道这台机器只保留一个初始账号，创建后不再开放自助创建。</span>
      </label>
      <button type="submit" className="os-btn" disabled={busy || !acknowledged}>
        {busy ? '正在创建…' : '创建并继续'}
      </button>
      {canCancel && (
        <button type="button" className="os-link" onClick={onCancel}>
          返回登录
        </button>
      )}
    </form>
  )
}

function RecoveryMaterial({
  code,
  account,
  onDone,
}: {
  code: string
  account: string
  onDone: () => void
}) {
  const download = () => {
    const payload = {
      format: 'liuhao-recovery-material',
      account,
      recovery_code: code,
      generated_at: new Date().toISOString(),
      note: '此文件由本机界面生成，未加密。请保存到离线介质，不要放在同步盘。',
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `liuhao-recovery-${account}.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="os-note" style={{ flexDirection: 'column', alignItems: 'flex-start' }}>
        <strong style={{ fontSize: 13 }}>恢复码（只显示这一次）</strong>
        <code style={{ fontSize: 14, wordBreak: 'break-all' }}>{code || '（未返回恢复码）'}</code>
        <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }}>
          忘记密码时用它重置。请离线保存，不要截图发到聊天工具。
        </span>
      </div>
      <button type="button" className="os-btn" onClick={download}>
        下载恢复材料
      </button>
      <button type="button" className="os-btn os-btn-ghost" onClick={onDone}>
        我已妥善保存，去登录
      </button>
    </div>
  )
}

function SelfRegisterForm({
  busy,
  remember,
  mode,
  onBusy,
  onError,
  onCancel,
  onSuccess,
}: {
  busy: boolean
  remember: boolean
  mode: ClientMode
  onBusy: (value: boolean) => void
  onError: (message: string) => void
  onCancel: () => void
  onSuccess: (session: Session) => void
}) {
  const [principal, setPrincipal] = useState('')
  const [secret, setSecret] = useState('')
  const [reveal, setReveal] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (!principal.trim()) {
      onError('请输入用户名')
      return
    }
    if (secret.length < 8) {
      onError('密码至少 8 位')
      return
    }
    onBusy(true)
    onError('')
    try {
      const session = await registerSelf(principal.trim(), secret, { remember, client: mode })
      onSuccess(session)
    } catch (err) {
      onError(err instanceof AuthError ? err.message : '创建失败，请稍后再试')
    } finally {
      onBusy(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="os-login-sub" style={{ marginBottom: 14 }}>
        输入用户名和密码即可创建账户，创建后直接进入系统。
      </p>
      <div className="os-field">
        <label htmlFor="reg-principal">用户名</label>
        <input
          id="reg-principal"
          className="os-input-plain"
          name="username"
          autoComplete="username"
          value={principal}
          onChange={(event) => setPrincipal(event.target.value)}
          placeholder="字母或数字，登录时使用"
          required
          autoFocus
        />
      </div>
      <div className="os-field">
        <label htmlFor="reg-secret">密码</label>
        <div className="os-input">
          <IconLock size={16} />
          <input
            id="reg-secret"
            name="password"
            type={reveal ? 'text' : 'password'}
            autoComplete="new-password"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            placeholder="至少 8 位，建议 10 位以上"
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
        <input type="checkbox" checked={remember} readOnly />
        <span>在这台设备上保持登录</span>
      </label>
      <button type="submit" className="os-btn" disabled={busy}>
        {busy ? '正在创建…' : '创建并进入'}
      </button>
      <button type="button" className="os-link" onClick={onCancel}>
        返回登录
      </button>
    </form>
  )
}

function RecoveryForm({
  busy,
  onBusy,
  onError,
  onCancel,
  onLost,
  onToken,
}: {
  busy: boolean
  onBusy: (value: boolean) => void
  onError: (message: string) => void
  onCancel: () => void
  onLost: () => void
  onToken: (token: string) => Promise<void>
}) {
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (password.length < 8) {
      onError('密码至少 8 位')
      return
    }
    onBusy(true)
    onError('')
    try {
      const result = await postJson<{ token: string }>('/v1/accounts/recovery/reset', {
        recovery_code: code.trim(),
        password,
      })
      await onToken(result.token)
    } catch (err) {
      onError(err instanceof Error ? err.message : '重置失败')
    } finally {
      onBusy(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="os-login-sub" style={{ marginBottom: 14 }}>
        用初始化时保存的恢复码重置密码。恢复码使用一次后即失效。
      </p>
      <div className="os-field">
        <label htmlFor="rec-code">恢复码</label>
        <input
          id="rec-code"
          className="os-input-plain"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          required
          autoFocus
        />
      </div>
      <div className="os-field">
        <label htmlFor="rec-password">新密码</label>
        <input
          id="rec-password"
          className="os-input-plain"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="至少 8 位"
          required
        />
      </div>
      <button type="submit" className="os-btn" disabled={busy}>
        {busy ? '正在重置…' : '重置并进入'}
      </button>
      <div className="os-login-links">
        <button type="button" className="os-link" onClick={onCancel}>
          返回登录
        </button>
        <button type="button" className="os-link" onClick={onLost}>
          恢复码也丢失了
        </button>
      </div>
    </form>
  )
}

function MigrateForm({
  busy,
  onBusy,
  onError,
  onCancel,
  onImported,
}: {
  busy: boolean
  onBusy: (value: boolean) => void
  onError: (message: string) => void
  onCancel: () => void
  onImported: () => void
}) {
  const [bundle, setBundle] = useState<Record<string, unknown> | null>(null)
  const [fileName, setFileName] = useState('')
  const [passphrase, setPassphrase] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy || !bundle) return
    onBusy(true)
    onError('')
    try {
      await postJson('/v1/accounts/backup/import', { bundle, passphrase })
      onImported()
    } catch (err) {
      const message = err instanceof Error ? err.message : ''
      // 后端对已有账号的实例返回 409：把"已初始化"这个结果翻译成下一步该做什么。
      if (/已完成初始化|全新实例|ALREADY_INITIALIZED/i.test(message)) {
        onError('本机已有账号，备份包只能在全新实例上导入；请直接登录，或创建账户。')
      } else {
        onError(message || '迁移失败')
      }
    } finally {
      onBusy(false)
    }
  }

  return (
    <form onSubmit={submit}>
      <p className="os-login-sub" style={{ marginBottom: 14 }}>
        备份包只能在全新实例（本机还没有任何账号）首次启动时导入；导入不会覆盖已有账号，本机已有账号时不可导入。
      </p>
      <div className="os-field">
        <label htmlFor="mig-file">备份包</label>
        <input
          id="mig-file"
          type="file"
          accept="application/json,.json,.lhbak"
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (!file) return
            setFileName(file.name)
            void file.text().then(
              (text) => {
                try {
                  const parsed = JSON.parse(text) as Record<string, unknown>
                  setBundle(
                    parsed.bundle && typeof parsed.bundle === 'object'
                      ? (parsed.bundle as Record<string, unknown>)
                      : parsed,
                  )
                  onError('')
                } catch {
                  setBundle(null)
                  onError('备份包不是有效的 JSON 文件')
                }
              },
              () => onError('无法读取该文件'),
            )
          }}
        />
        {fileName && (
          <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }}>已选择：{fileName}</span>
        )}
      </div>
      <div className="os-field">
        <label htmlFor="mig-pass">口令</label>
        <input
          id="mig-pass"
          className="os-input-plain"
          type="password"
          value={passphrase}
          onChange={(event) => setPassphrase(event.target.value)}
          placeholder="导出时设置的口令"
          required
        />
      </div>
      <button type="submit" className="os-btn" disabled={busy || !bundle}>
        {busy ? '正在导入…' : '导入并继续'}
      </button>
      <button type="button" className="os-link" onClick={onCancel}>
        返回
      </button>
    </form>
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
