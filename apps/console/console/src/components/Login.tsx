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
  type AuthConfig,
  type ClientMode,
  type Session,
  loadAuthConfig,
  login,
  registerSelf,
  savePendingRecovery,
  sessionFromToken,
} from '../lib/auth'
import { apiFetch } from '../lib/api'
import { detectClient, isDesktopShell } from '../lib/mode'
import {
  clearLocalProof,
  localProofHeader,
  type LocalProofKind,
} from '../lib/localProof'
import { BRAND_NAME_EN, BRAND_NAME_ZH } from '../brand'
import { LOCAL_SERVICE_OFFLINE_MANUAL, readErrorMessage } from '../lib/errorMessage'
import {
  IconAlert,
  IconDesktop,
  IconEye,
  IconEyeOff,
  IconGlobe,
  IconLock,
  IconMobile,
} from './icons'
import { DesktopDownloadLink } from './DesktopDownloadLink'

export interface LoginProps {
  /** 当前端的形态，登录时随请求上报（令牌里会记下来源端）。 */
  mode: ClientMode
  /** 为什么回到登录页：过期或被登出都要明说，不静默跳转。 */
  notice?: 'expired' | 'logged-out' | null
  onSuccess: (session: Session) => void
}

type Stage =
  | 'login'
  | 'bootstrap'
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

/**
 * 网关还没就绪时登录页的自动重试节奏。
 *
 * 双击启动后浏览器可能比网关先到（用户手动刷新、或打开的是旧标签页），
 * 此时 ``/v1/auth/config`` 会连接失败。直接甩一句"服务没运行"会让人以为
 * 安装坏了；这里先显示"正在准备，请稍候"并自动重试，约 1 分钟后再给结论。
 */
const CONFIG_RETRY_DELAY_MS = 2000
const CONFIG_RETRY_LIMIT = 30

/**
 * 手机中继对被移除 / 未配对的设备返回 401（网关的 `/v1/auth/config` 本身是
 * 匿名可读的，401 只可能来自中继这一层）。
 *
 * 这种情况下重试没有意义：服务是活着的，只是这台设备不再被允许访问。
 * 所以这里停下重试，直接说明要重新配对。
 */
const DEVICE_REVOKED_MESSAGE =
  '这台设备已被移除，请重新配对。请在电脑上重新生成配对码，然后在手机上完成配对。'

/** 首次进入的页面：没有初始账号、或初始化停在半路，都直接进设置表单。 */
function initialStage(config: AuthConfig): Stage {
  if (config.root_account_exists === false) return 'bootstrap'
  if (config.initialization?.recoverable) return 'bootstrap'
  return 'login'
}

async function postJson<T>(
  path: string,
  body: unknown,
  proof?: LocalProofKind,
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(proof ? localProofHeader(proof) : {}),
      },
      body: JSON.stringify(body),
    })
  } catch {
    throw new AuthError(0, LOCAL_SERVICE_OFFLINE_MANUAL)
  }
  if (!response.ok) {
    throw new AuthError(response.status, await readErrorMessage(response))
  }
  return (await response.json()) as T
}

/** 登录失败必须给出下一步；后端文案已带指引的不重复追加。 */
function loginErrorText(message: string): string {
  if (!message) return '登录没有完成。请确认账号与密码，或联系设备管理员。'
  if (/请|联系|稍后|重试/.test(message)) return message
  return `${message}。请确认账号与密码是否正确，或联系设备管理员。`
}

export function Login({ mode, notice = null, onSuccess }: LoginProps) {
  const [principal, setPrincipal] = useState('')
  const [secret, setSecret] = useState('')
  const [remember, setRemember] = useState(true)
  const [reveal, setReveal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [configError, setConfigError] = useState('')
  const [deviceRevoked, setDeviceRevoked] = useState(false)
  const [stage, setStage] = useState<Stage>('login')

  useEffect(() => {
    let alive = true
    let timer: number | undefined
    let attempts = 0

    const load = () => {
      void loadAuthConfig().then((result) => {
        if (!alive) return
        if (result.config) {
          setConfig(result.config)
          setConfigError('')
          setStage((current) => (current === 'login' ? initialStage(result.config!) : current))
          return
        }
        if (result.status === 401) {
          // 设备已被移除（中继层 401）：服务在线，重试只会一直显示"正在准备"。
          setDeviceRevoked(true)
          setConfigError(DEVICE_REVOKED_MESSAGE)
          return
        }
        attempts += 1
        if (attempts <= CONFIG_RETRY_LIMIT) {
          // 服务还在启动/端口刚绑定：说清"正在准备"，并自动重试，不要求刷新。
          setConfigError('正在准备，请稍候…（本地服务启动后会自动进入设置页）')
          timer = window.setTimeout(load, CONFIG_RETRY_DELAY_MS)
          return
        }
        setConfigError(result.error ?? '')
      })
    }

    load()
    return () => {
      alive = false
      if (timer !== undefined) window.clearTimeout(timer)
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
        setError(loginErrorText(err instanceof AuthError ? err.message : '登录失败'))
        setSecret('')
        // 密码清空后提交按钮立刻变 disabled，浏览器会把它上面的焦点丢给 body，
        // 键盘用户得重新 Tab 一整圈。这里把焦点交回密码框（错误已由 role="alert" 播报）。
        document.getElementById('os-secret')?.focus()
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
            <div className="os-login-aside-title">{BRAND_NAME_ZH}</div>
            <div className="os-login-aside-en">{BRAND_NAME_EN}</div>
          </div>
        </div>

        <div className="os-login-status">
          <div className="os-login-status-row">
            <span>登录凭据</span>
            <span data-tone={config ? (notConfigured ? 'warn' : 'ok') : configError ? 'warn' : 'muted'}>
              {config ? (notConfigured ? '未配置（无法登录）' : '已配置') : configError ? '读取失败' : '读取中…'}
            </span>
          </div>
          <div className="os-login-status-row">
            <span>可登录账号</span>
            <span>{config ? config.login_eligible_humans : '—'}</span>
          </div>
          <div className="os-login-status-row">
            <span>当前形态</span>
            <span data-testid="client-kind">{CLIENT_LABEL[detectClient()]}</span>
          </div>
          <div className="os-login-status-row">
            <span>运行载体</span>
            <span data-testid="client-surface">
              {isDesktopShell() ? '桌面客户端（原生窗口）' : '浏览器'}
            </span>
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
            还没有设置登录凭据，此时任何人都无法登录。请按页面提示完成首次设置，再回到这里登录。
          </p>
        )}
        {/* 托盘文案只对桌面壳成立：浏览器里没有托盘，宽屏浏览器即使借用了
            桌面端栅格也不能显示这条说明，否则等于网页端自称桌面端。 */}
        {isDesktopShell() && (
          <p className="os-login-aside-note">
            桌面端只有一个鎏灏窗口；关闭窗口会退到系统托盘，本地服务继续运行。需要时从托盘重新打开即可。
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
            <h1 className="os-login-title">{BRAND_NAME_EN}</h1>
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

        {configError && (
          <div className="os-alert" role="alert">
            <IconAlert size={15} />
            <span>{configError}</span>
          </div>
        )}

        {deviceRevoked && (
          <button
            type="button"
            className="os-btn"
            data-testid="login-repair-link"
            onClick={() => window.location.assign('/')}
          >
            回到配对页，重新配对
          </button>
        )}

        {notice && !deviceRevoked && (
          <div className="os-alert" role="alert" data-testid="login-notice">
            <IconAlert size={15} />
            <span>
              {notice === 'expired'
                ? '登录已过期，需要重新登录。请在下方重新输入账号与密码。'
                : '你已退出登录，需要重新登录。请在下方重新输入账号与密码。'}
            </span>
          </div>
        )}

        {notConfigured && (
          <div className="os-note" role="status">
            <IconAlert size={15} />
            <span>
              {config?.message || '还没有设置登录凭据，此时任何人都无法登录。'}
              <br />
              请按页面提示完成首次设置：创建初始账号后即可登录。
            </span>
          </div>
        )}

        {noEligible && (
          <div className="os-note" role="status">
            <IconAlert size={15} />
            <span>
              {config?.message}
              <br />
              为账号设置密码后即可登录。
            </span>
          </div>
        )}

        {!noAccount && !deviceRevoked && (stage === 'login' || stage === 'register') && (
          <div
            className="os-login-tabs"
            role="tablist"
            aria-label="登录或创建账户"
            onKeyDown={(event) => {
              // 方向键在页签间切换（roving tabindex 的标准交互）。
              if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
              event.preventDefault()
              const next: Stage = stage === 'login' ? 'register' : 'login'
              setStage(next)
              setError('')
              // 等新面板挂载完再落焦：面板内表单的 autoFocus 会抢焦点，
              // 用宏任务排在它后面，保证焦点仍在被选中的页签上。
              window.setTimeout(() => {
                document
                  .getElementById(next === 'login' ? 'os-login-tab-login' : 'os-login-tab-register')
                  ?.focus()
              }, 0)
            }}
          >
            <button
              type="button"
              role="tab"
              id="os-login-tab-login"
              aria-selected={stage === 'login'}
              aria-controls="os-login-panel"
              tabIndex={stage === 'login' ? 0 : -1}
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
              id="os-login-tab-register"
              aria-selected={stage === 'register'}
              aria-controls="os-login-panel"
              tabIndex={stage === 'register' ? 0 : -1}
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

        {/* 登录失败提示只在登录表单里、紧贴密码字段（见下方 os-login-error）；
            其它阶段（创建账户/迁移/恢复）仍在顶部统一提示。 */}
        {error && stage !== 'login' && (
          <div className="os-alert" role="alert">
            <IconAlert size={15} />
            <span>{error}</span>
          </div>
        )}

        {stage === 'login' && !deviceRevoked && (
          <div role="tabpanel" id="os-login-panel" aria-labelledby="os-login-tab-login">
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
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? 'os-login-error' : undefined}
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
              {error && (
                <div className="os-alert" role="alert" id="os-login-error" style={{ marginTop: 8 }}>
                  <IconAlert size={15} />
                  <span>{error}</span>
                </div>
              )}
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
          </div>
        )}

        {stage === 'bootstrap' && (
          <BootstrapForm
            busy={busy}
            mode={mode}
            initialPrincipal={config?.initialization?.principal ?? ''}
            initializationIncomplete={config?.initialization?.state === 'incomplete'}
            onBusy={setBusy}
            onError={setError}
            onSuccess={onSuccess}
            onAlreadyInitialized={() => {
              // 已存在账号（含"初始化停在半路但名称不匹配"）：重新拉一次配置，
              // 再回到登录表单，避免把用户卡在一个必然 409 的表单上。
              void loadAuthConfig().then((result) => {
                if (!result.config) return
                setConfig(result.config)
                setConfigError('')
                setStage('login')
              })
            }}
            onCancel={() => {
              if (!noAccount) setStage('login')
            }}
            canCancel={!noAccount}
          />
        )}

        {stage === 'register' && (
          <div role="tabpanel" id="os-login-panel" aria-labelledby="os-login-tab-register">
            <SelfRegisterForm
              busy={busy}
              remember={remember}
              mode={mode}
              onBusy={setBusy}
              onError={setError}
              onCancel={() => setStage('login')}
              onSuccess={onSuccess}
            />
          </div>
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
            recovery={config?.recovery}
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

        {/* 只显示**当前**身份。以前这里把三种身份并排画出来，浏览器里就会
            出现一枚「桌面端」占位芯片；即使它是灰色未选中，也和"本页是网页版"
            矛盾。栅格预览的职责已交给设置页，登录页只保留一个真值。 */}
        <div className="os-login-clients">
          {(() => {
            const client = detectClient()
            const Icon = CLIENT_ICON[client]
            return (
              <div
                className="os-client-chip"
                data-active="true"
                title={`当前以${CLIENT_LABEL[client]}形态打开`}
              >
                <Icon size={15} />
                <span>{CLIENT_LABEL[client]}</span>
              </div>
            )
          })()}
        </div>

        {/* 只在网页端可见（CSS 按 data-mode/data-client 控制），桌面壳与手机端不出现。 */}
        <DesktopDownloadLink />

        <div className="os-login-foot">
          同意以同一账号在桌面端、网页版与手机端登录 —— 三端共用同一会话，
          权限判断始终在服务端完成。
          {config && (
            <>
              <br />
              已登记账号：{config.registered_humans} · 可登录：{config.login_eligible_humans} ·
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

function passwordStrength(password: string): { label: string; hint: string } {
  if (!password) return { label: '', hint: '至少 8 位，建议 12 位以上' }
  if (password.length < 8) return { label: '太短', hint: `还需要 ${8 - password.length} 位` }
  let score = 1
  if (password.length >= 12) score += 1
  if (/[A-Za-z]/.test(password) && /[0-9]/.test(password)) score += 1
  if (/[^A-Za-z0-9]/.test(password)) score += 1
  if (score >= 4) return { label: '强', hint: '长度与字符组合都不错' }
  if (score >= 3) return { label: '中', hint: '再长一点或换一种字符会更稳' }
  return { label: '弱', hint: '建议 12 位以上，并混合字母、数字或符号' }
}

const MODEL_PRESETS = {
  deepseek: { label: 'DeepSeek', provider: 'deepseek', baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  openai: { label: 'OpenAI', provider: 'openai', baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  custom: { label: '自定义（OpenAI 兼容）', provider: 'openai', baseUrl: '', model: '' },
} as const

type ModelPreset = keyof typeof MODEL_PRESETS

function BootstrapForm({
  busy,
  mode,
  initialPrincipal,
  initializationIncomplete,
  onBusy,
  onError,
  onSuccess,
  onAlreadyInitialized,
  onCancel,
  canCancel,
}: {
  busy: boolean
  mode: ClientMode
  initialPrincipal: string
  initializationIncomplete: boolean
  onBusy: (value: boolean) => void
  onError: (message: string) => void
  onSuccess: (session: Session) => void
  onAlreadyInitialized: () => void
  onCancel: () => void
  canCancel: boolean
}) {
  const [principal, setPrincipal] = useState(initialPrincipal)
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showModel, setShowModel] = useState(false)
  const [preset, setPreset] = useState<ModelPreset>('deepseek')
  const [modelKey, setModelKey] = useState('')
  const [baseUrl, setBaseUrl] = useState<string>(MODEL_PRESETS.deepseek.baseUrl)
  const [modelName, setModelName] = useState<string>(MODEL_PRESETS.deepseek.model)
  const [conflict, setConflict] = useState(false)

  const strength = passwordStrength(password)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    const name = principal.trim()
    if (!name) {
      onError('请输入管理员账号（英文，如 liuhao）')
      return
    }
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
    setConflict(false)
    try {
      const result = await postJson<{
        account: { principal: string }
        recovery_code: string
        notice: string
      }>('/v1/accounts/register', {
        principal: name,
        password,
        display_name: displayName.trim() || undefined,
      }, 'handoff')

      // 后端已单次消费这枚交接令牌；本地也不再保留，避免它继续躺在
      // sessionStorage 里被后续请求重复带走。
      clearLocalProof('handoff')

      // 提交即登录：不让用户再回头抄地址或手动登录一次。
      const session = await login(name, password, { remember: true, client: mode })

      // 模型 Key 是可选项：失败只记一条提示，绝不把已经完成的初始化卡住。
      let warning: string | null = null
      const key = modelKey.trim()
      if (showModel && key) {
        const chosen = MODEL_PRESETS[preset]
        const model = modelName.trim() || chosen.model
        try {
          await apiFetch('/v1/models', {
            method: 'POST',
            body: JSON.stringify({
              name: model,
              provider: chosen.provider,
              model,
              base_url: (baseUrl.trim() || chosen.baseUrl).replace(/\/+$/, '') || null,
              api_key: key,
              is_default: true,
              enabled: true,
            }),
          })
        } catch (err) {
          warning = `模型 Key 未能保存（${
            err instanceof Error ? err.message : '未知错误'
          }）。可以先进入系统，之后在「系统设置 → 模型管理」重新添加。`
        }
      }

      // 恢复码只在注册响应里出现这一次；暂存到主界面横幅，既不打断进入，
      // 也不会因为跳过中间页而丢失。
      savePendingRecovery({ account: result.account.principal, code: result.recovery_code, warning })
      onSuccess(session)
    } catch (err) {
      if (err instanceof AuthError && err.status === 409) setConflict(true)
      onError(err instanceof Error ? err.message : '初始化失败')
    } finally {
      onBusy(false)
    }
  }

  const applyPreset = (next: ModelPreset) => {
    setPreset(next)
    const chosen = MODEL_PRESETS[next]
    setBaseUrl(chosen.baseUrl)
    setModelName(chosen.model)
  }

  return (
    <form onSubmit={submit}>
      <p className="os-login-sub" style={{ marginBottom: 14 }}>
        {initializationIncomplete
          ? '上次初始化停在设置密码之前。请用首次注册时填写的账号名补全初始化，密码可以重新设置。'
          : '这台机器还没有账号。创建的第一个账号拥有本机全部管理范围，请务必妥善保存随后显示的恢复材料。'}
      </p>
      {initializationIncomplete && (
        <div className="os-note" role="status">
          {/* 必须包一层 <span>：.os-note 是 flex，裸文本与 <strong> 会各自成为
              flex item，被排成两列，读起来像乱码。 */}
          <span>
            为了不在匿名接口里泄露根账号名，这里不再预填名称。请填写
            <strong>首次注册时使用的名称</strong>（不是新名字），提交后会补全初始化并直接进入系统。
          </span>
        </div>
      )}
      <div className="os-field">
        <label htmlFor="boot-principal">账号</label>
        <input
          id="boot-principal"
          className="os-input-plain"
          value={principal}
          onChange={(event) => setPrincipal(event.target.value)}
          placeholder={
            initializationIncomplete
              ? '填写首次注册时使用的名称'
              : '字母或数字，如 liuhao'
          }
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
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="至少 8 位，建议 12 位以上"
          required
        />
        {password && (
          <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }} data-testid="password-strength">
            强度：{strength.label} · {strength.hint}
          </span>
        )}
      </div>
      <div className="os-field">
        <label htmlFor="boot-confirm">确认密码</label>
        <input
          id="boot-confirm"
          className="os-input-plain"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
          required
        />
      </div>

      <label className="os-check">
        <input
          type="checkbox"
          checked={showModel}
          onChange={(event) => setShowModel(event.target.checked)}
        />
        <span>同时配置模型 Key（可选，可跳过，之后也能补）</span>
      </label>
      {showModel && (
        <div className="os-field">
          <label htmlFor="boot-provider">服务商</label>
          <select
            id="boot-provider"
            className="os-input-plain"
            value={preset}
            onChange={(event) => applyPreset(event.target.value as ModelPreset)}
          >
            {(Object.keys(MODEL_PRESETS) as ModelPreset[]).map((key) => (
              <option key={key} value={key}>
                {MODEL_PRESETS[key].label}
              </option>
            ))}
          </select>
          {preset === 'custom' && (
            <>
              <label htmlFor="boot-base-url">API base_url</label>
              <input
                id="boot-base-url"
                className="os-input-plain"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.example.com/v1"
              />
              <label htmlFor="boot-model">模型名</label>
              <input
                id="boot-model"
                className="os-input-plain"
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                placeholder="例如 deepseek-chat"
              />
            </>
          )}
          <label htmlFor="boot-model-key">API Key（可留空跳过）</label>
          <input
            id="boot-model-key"
            className="os-input-plain"
            type="password"
            autoComplete="off"
            value={modelKey}
            onChange={(event) => setModelKey(event.target.value)}
            placeholder="粘贴 Key；留空则跳过模型配置"
          />
        </div>
      )}

      <button type="submit" className="os-btn" disabled={busy}>
        {busy ? '正在创建并登录…' : '创建并进入系统'}
      </button>
      {conflict && (
        <button type="button" className="os-link" onClick={onAlreadyInitialized}>
          这台机器已有账号，去登录
        </button>
      )}
      {canCancel && (
        <button type="button" className="os-link" onClick={onCancel}>
          返回登录
        </button>
      )}
    </form>
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
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    if (!principal.trim()) {
      onError('请输入用户名')
      return
    }
    if (password.length < 8) {
      onError('密码至少 8 位')
      return
    }
    onBusy(true)
    onError('')
    try {
      const session = await registerSelf(principal.trim(), password, { remember, client: mode })
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
            value={password}
            onChange={(event) => setPassword(event.target.value)}
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
  recovery,
}: {
  busy: boolean
  onBusy: (value: boolean) => void
  onError: (message: string) => void
  onCancel: () => void
  onLost: () => void
  onToken: (token: string) => Promise<void>
  recovery?: AuthConfig['recovery']
}) {
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [issuedCode, setIssuedCode] = useState<string | null>(null)
  const [issueFailed, setIssueFailed] = useState<string | null>(null)
  const [copyState, setCopyState] = useState<'idle' | 'done' | 'fail'>('idle')
  const [handoffBusy, setHandoffBusy] = useState(false)
  // 本机（含已配对手机中继）才能直接生成恢复码；直连手机端要给出真实路径。
  const canSelfServiceHere = recovery?.local ?? true
  const remoteAllowed = recovery?.remote_allowed === true

  /**
   * 本机自救：没有任何恢复码的账号，由本机生成一个新码并在页面直接显示。
   * 后端把码写进加密交接文件后立刻取回并删除，只在本响应里给出一次；
   * 因此必须把 `recovery_code` 直接呈现给用户，不能让他去找一个已被删掉的文件。
   */
  const issueLocal = async () => {
    if (handoffBusy) return
    setHandoffBusy(true)
    onError('')
    setCopyState('idle')
    try {
      const result = await postJson<{
        written?: boolean
        recovered?: boolean
        file?: string
        recovery_code?: string
      }>('/v1/accounts/recovery/local', {}, 'handoff')
      // 请求成功即说明交接令牌已被服务端消费；本页也移除，避免重放。
      clearLocalProof('handoff')
      const value = (result.recovery_code ?? '').trim()
      if (!value) {
        // 旧后端 / 降级路径可能不给码：不引导用户去开一个已经不存在的文件。
        setIssuedCode(null)
        setIssueFailed(
          '无法在本机生成恢复码。请改用你保存过的备份恢复，或联系设备管理员协助重置密码。',
        )
        return
      }
      setIssueFailed(null)
      setIssuedCode(value)
      // 顺手填进上方的“恢复码”，用户抄下后可直接重置。
      setCode(value)
    } catch (err) {
      setIssuedCode(null)
      setIssueFailed(null)
      onError(err instanceof Error ? err.message : '本机生成失败')
    } finally {
      setHandoffBusy(false)
    }
  }

  const copyIssued = async () => {
    if (!issuedCode) return
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(issuedCode)
      setCopyState('done')
    } catch {
      setCopyState('fail')
    }
  }

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
      const result = await postJson<{ token: string }>(
        '/v1/accounts/recovery/reset',
        {
          recovery_code: code.trim(),
          password,
        },
        'reset',
      )
      clearLocalProof('reset')
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
      {!canSelfServiceHere && (
        <div className="os-note" role="status" data-testid="recovery-path">
          <span>
            这台设备不是本机：重置请在本机打开控制台，或先在电脑上开启「手机访问」
            并完成配对后从手机端操作。
            {remoteAllowed
              ? '本机已开启远程恢复，也可以直接在这里重试。'
              : '若需要跨设备重置，请在电脑上开启远程恢复（环境变量 LIUHAO_ALLOW_REMOTE_RECOVERY）。'}
          </span>
        </div>
      )}
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
      {issuedCode && (
        <div className="os-note" role="status" data-testid="recovery-code">
          <span>
            <strong>新的恢复码只显示这一次</strong>，请立刻抄下来，或存进密码管理器。
          </span>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              margin: '8px 0',
              flexWrap: 'wrap',
            }}
          >
            <code
              data-testid="recovery-code-value"
              style={{
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                fontSize: 16,
                letterSpacing: '0.04em',
                padding: '4px 10px',
                borderRadius: 6,
                background: 'rgba(127, 127, 127, 0.14)',
                userSelect: 'all',
                wordBreak: 'break-all',
              }}
            >
              {issuedCode}
            </code>
            <button type="button" className="os-link" onClick={() => void copyIssued()}>
              {copyState === 'done' ? '已复制' : '复制'}
            </button>
          </div>
          {copyState === 'fail' && <div>复制失败，请手动选中上面的码复制。</div>}
          <span>
            这个码可用于本机或已配对的手机端，用一次就会失效；登录后建议到「恢复码」页重新生成一个新的。
          </span>
        </div>
      )}
      {issueFailed && (
        <div className="os-note" role="alert" data-testid="recovery-unavailable">
          <span>{issueFailed}</span>
        </div>
      )}
      <button type="submit" className="os-btn" disabled={busy}>
        {busy ? '正在重置…' : '重置并进入'}
      </button>
      <div className="os-login-links">
        <button type="button" className="os-link" onClick={onCancel}>
          返回登录
        </button>
        {canSelfServiceHere ? (
          <button
            type="button"
            className="os-link"
            onClick={() => void issueLocal()}
            disabled={handoffBusy}
          >
            {handoffBusy ? '正在生成…' : '没有恢复码？在本机生成'}
          </button>
        ) : (
          <span className="os-hint">
            没有恢复码？请在本机控制台生成，或通过已配对的「手机访问」操作。
          </span>
        )}
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
      <p className="os-note" role="note" style={{ marginBottom: 14 }}>
        审计记录与完整性校验密钥都不随包：新机的审计从头记录。若你把原机审计库单独带到新机，
        因为没有对应密钥，审计链只能做结构检查、无法用密钥证明内容未被改写；
        想保留可验证性，请在原机导出审计记录归档，并保留原机数据目录里的 audit_chain.key。
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
