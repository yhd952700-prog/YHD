/**
 * 控制台会话 —— 桌面端 / 网页版 / 手机端共用的唯一会话真相。
 *
 * 三端是同一份代码的三种呈现，**不是**三套鉴权。所以这里只有一份会话：
 * 登录一次，三个端各自持有自己的令牌副本，都用于同一批后端端点。
 *
 * 存储策略（对应设计图登录页的"记住我"）：
 * - 勾选"记住我" → localStorage，关掉浏览器/重开已安装的 App 仍保持登录；
 * - 不勾选       → sessionStorage，关标签页即失效。
 *
 * 两个 store 都**不是**安全边界：真正的授权判断始终在后端（JWT 签名校验 +
 * 身份内核的人类判定）。这里存的只是一个令牌字符串，前端不做任何权限决策。
 */

import { LOCAL_SERVICE_OFFLINE_MANUAL, readErrorMessage, readError } from './errorMessage'

export type ClientMode = 'desktop' | 'web' | 'mobile'

export const CLIENT_MODES: readonly ClientMode[] = ['desktop', 'web', 'mobile']

/** 一次已建立的会话。 */
export interface Session {
  token: string
  principal: string
  displayName: string | null
  scope: string | null
  permissions: string[]
  /** epoch 秒 */
  expiresAt: number
  client: ClientMode
  /** 账号在树中的位置。仅用于前端裁剪入口，不构成授权依据。 */
  parentAccountId: string | null
  path: string
  depth: number
}

/** `GET /v1/auth/config` 的响应：登录页据此解释当前状态。 */
export interface AuthConfig {
  auth_required: boolean
  methods: string[]
  oauth: Record<string, boolean>
  secret_store: { configured: boolean; source: string | null }
  registered_humans: number
  login_eligible_humans: number
  client_modes: string[]
  token: { ttl_seconds: number }
  message: string
  /** 是否已存在本机初始账号；决定登录页是否展示"首次使用"。 */
  root_account_exists?: boolean
  /** 自助创建初始账号是否开放（存在初始账号后恒为 false）。 */
  registration_open?: boolean
}

/** 登录/接口失败。`status` 直接来自后端，用于区分 401 与 429。 */
export class AuthError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const LOCAL_KEY = 'liuhao.session'
const TAB_KEY = 'liuhao.session.tab'
/** 旧版审批面板单独存的令牌键；读到它时认领过来，避免升级即登出。 */
const LEGACY_TOKEN_KEY = 'liuhao.approval_token'

/** 会话失效时广播，App 据此回到登录页。 */
export const UNAUTHENTICATED_EVENT = 'liuhao:unauthenticated'

function safeGet(storage: Storage | undefined, key: string): string | null {
  try {
    return storage ? storage.getItem(key) : null
  } catch {
    // sessionStorage/localStorage 在隐私模式下可能抛错。
    return null
  }
}

function safeSet(storage: Storage | undefined, key: string, value: string | null): void {
  try {
    if (!storage) return
    if (value === null) storage.removeItem(key)
    else storage.setItem(key, value)
  } catch {
    // 存储不可用只是失去"持久化"，不影响本次会话可用。
  }
}

function localStorageSafe(): Storage | undefined {
  try {
    return typeof window === 'undefined' ? undefined : window.localStorage
  } catch {
    return undefined
  }
}

function sessionStorageSafe(): Storage | undefined {
  try {
    return typeof window === 'undefined' ? undefined : window.sessionStorage
  } catch {
    return undefined
  }
}

/** JWT 载荷的**展示用**解析。绝不作为授权依据。 */
export function decodeToken(
  token: string,
): { subject: string; expiresAt: number; client: ClientMode | null } | null {
  const parts = (token || '').split('.')
  if (parts.length !== 3) return null
  try {
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const pad = b64.length % 4 ? '='.repeat(4 - (b64.length % 4)) : ''
    const payload = JSON.parse(atob(b64 + pad)) as {
      sub?: string
      exp?: number
      metadata?: { client?: string }
    }
    if (!payload.sub) return null
    const client = payload.metadata?.client
    return {
      subject: String(payload.sub),
      expiresAt: Number(payload.exp ?? 0),
      client: CLIENT_MODES.includes(client as ClientMode) ? (client as ClientMode) : null,
    }
  } catch {
    return null
  }
}

function parseSession(raw: string | null): Session | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<Session>
    if (!parsed || typeof parsed.token !== 'string' || !parsed.token) return null
    return {
      token: parsed.token,
      principal: String(parsed.principal ?? ''),
      displayName: (parsed.displayName as string | null) ?? null,
      scope: (parsed.scope as string | null) ?? null,
      permissions: Array.isArray(parsed.permissions) ? parsed.permissions.map(String) : [],
      expiresAt: Number(parsed.expiresAt ?? 0),
      client: CLIENT_MODES.includes(parsed.client as ClientMode)
        ? (parsed.client as ClientMode)
        : 'web',
      parentAccountId:
        typeof parsed.parentAccountId === 'string' ? parsed.parentAccountId : null,
      path: typeof parsed.path === 'string' ? parsed.path : '',
      depth: Number.isFinite(Number(parsed.depth)) ? Number(parsed.depth) : 0,
    }
  } catch {
    return null
  }
}

/** 令牌是否已过期（`expiresAt` 为 0 表示未知，按未过期处理让后端裁决）。 */
export function isExpired(session: Session | null): boolean {
  if (!session) return true
  if (!session.expiresAt) return false
  return session.expiresAt * 1000 <= Date.now()
}

/** 读取当前会话；已过期则视为无会话并清除。 */
export function getSession(): Session | null {
  const local = parseSession(safeGet(localStorageSafe(), LOCAL_KEY))
  const tab = parseSession(safeGet(sessionStorageSafe(), TAB_KEY))
  const found = local ?? tab

  if (found) {
    if (isExpired(found)) {
      clearSession()
      return null
    }
    return found
  }

  // 认领旧版的裸令牌：升级不该把人踢下线。
  const legacy = (safeGet(sessionStorageSafe(), LEGACY_TOKEN_KEY) || '').trim()
  if (legacy) {
    const decoded = decodeToken(legacy)
    if (decoded && decoded.expiresAt * 1000 > Date.now()) {
      const adopted: Session = {
        token: legacy,
        principal: decoded.subject,
        displayName: null,
        scope: null,
        permissions: [],
        expiresAt: decoded.expiresAt,
        client: decoded.client ?? 'web',
        parentAccountId: null,
        path: '',
        depth: 0,
      }
      saveSession(adopted, false)
      safeSet(sessionStorageSafe(), LEGACY_TOKEN_KEY, null)
      return adopted
    }
    safeSet(sessionStorageSafe(), LEGACY_TOKEN_KEY, null)
  }
  return null
}

/** 写入会话。`remember` 决定落在 localStorage 还是 sessionStorage。 */
export function saveSession(session: Session, remember: boolean): void {
  const payload = JSON.stringify(session)
  if (remember) {
    safeSet(localStorageSafe(), LOCAL_KEY, payload)
    safeSet(sessionStorageSafe(), TAB_KEY, null)
  } else {
    safeSet(sessionStorageSafe(), TAB_KEY, payload)
    safeSet(localStorageSafe(), LOCAL_KEY, null)
  }
}

/** 清除会话（两个 store 都清，避免残留一份仍可用的令牌）。 */
export function clearSession(): void {
  safeSet(localStorageSafe(), LOCAL_KEY, null)
  safeSet(sessionStorageSafe(), TAB_KEY, null)
  safeSet(sessionStorageSafe(), LEGACY_TOKEN_KEY, null)
}

/** 承载令牌的头名。
 *
 * 故意**不是** `Authorization`。生产托管的边缘网关（`CloudStudio Gateway`）会在
 * 请求到达本应用前把 `Authorization` 整个换成它自己的 JWT —— 实测：不带该头、
 * 带 17 字符假令牌、带 824 字符真令牌，应用收到的都是同一个 379 字符的 HS256
 * 令牌。只有网关不认识的头能原样穿过；而被基础设施改写的头本来也不该用来鉴权。
 * 后端 `require_bearer_payload` 优先读此头、其次才读 `Authorization`。
 */
export const TOKEN_HEADER = 'X-Liuhao-Token'

/** 带令牌的请求头。
 *
 * 两个头都写：私有头是托管环境下**唯一**可用通道，标准头让本机 / Docker 直连
 * 部署（无网关改写）行为不变。
 */
export function tokenHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init)
  const token = currentToken()
  if (token) {
    headers.set(TOKEN_HEADER, `Bearer ${token}`)
    headers.set('Authorization', `Bearer ${token}`)
  }
  return headers
}

/** 当前会话的令牌，供 bearer 头使用。 */
export function currentToken(): string {
  return getSession()?.token ?? ''
}

/** 登录页读取元信息的结果。失败时必须带一句能行动的中文，不能静默 null。 */
export interface AuthConfigResult {
  config: AuthConfig | null
  error: string | null
}

/**
 * 读取登录页需要的元信息。
 *
 * 失败不再静默降级成 ``null``：那会让登录页永远停在"读取中…"，用户既不知道
 * 后端没起来，也不知道该做什么。这里把统一错误文案带回给界面。
 */
export async function loadAuthConfig(): Promise<AuthConfigResult> {
  let response: Response
  try {
    response = await fetch('/v1/auth/config', { cache: 'no-store' })
  } catch {
    return {
      config: null,
      error: LOCAL_SERVICE_OFFLINE_MANUAL,
    }
  }
  if (!response.ok) {
    // 只起了静态站点时，``/v1/auth/config`` 会被静态服务器当成"文件不存在"
    // 返回 404。此时说"内容已删除"会把人带偏，直接指向"服务没起来"。
    if (response.status === 404) {
      return {
        config: null,
        error:
          '无法获取登录配置。请确认启动窗口仍在运行；如果服务刚启动，请稍等几秒后刷新本页重试。',
      }
    }
    const failure = await readError(response)
    return { config: null, error: failure.message }
  }
  try {
    return { config: (await response.json()) as AuthConfig, error: null }
  } catch {
    return {
      config: null,
      error: '服务没有返回正常的数据。请确认启动窗口仍在运行并完成初始化，然后刷新本页重试。',
    }
  }
}

export interface LoginOptions {
  remember: boolean
  client: ClientMode
}

/** 用凭据换一个会话。失败抛 `AuthError`（401 / 403 / 429）。 */
export async function login(
  principal: string,
  secret: string,
  options: LoginOptions,
): Promise<Session> {
  let response: Response
  try {
    response = await fetch('/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ principal, secret, client: options.client }),
    })
  } catch {
    throw new AuthError(0, LOCAL_SERVICE_OFFLINE_MANUAL)
  }
  if (!response.ok) {
    const message = await readErrorMessage(response)
    // 403 在本服务里表示"账号被暂停/不可用"，后端会带回实现细节，界面只给中性说明。
    throw new AuthError(
      response.status,
      response.status === 403 ? '该账号当前不可用，无法登录' : message,
    )
  }
  const body = (await response.json()) as {
    access_token: string
    principal: string
    display_name: string | null
    scope: string | null
    permissions: string[]
    expires_at: number
    client: string
    parent_account_id?: string | null
    path?: string
    depth?: number
  }
  const session: Session = {
    token: body.access_token,
    principal: body.principal,
    displayName: body.display_name ?? null,
    scope: body.scope ?? null,
    permissions: Array.isArray(body.permissions) ? body.permissions : [],
    expiresAt: Number(body.expires_at ?? 0),
    client: CLIENT_MODES.includes(body.client as ClientMode)
      ? (body.client as ClientMode)
      : options.client,
    parentAccountId: body.parent_account_id ?? null,
    path: body.path ?? '',
    depth: Number.isFinite(Number(body.depth)) ? Number(body.depth) : 0,
  }
  saveSession(session, options.remember)
  return session
}

/**
 * 登录页「创建账户」：只用用户名 + 密码自助创建，不发送邀请码或任何归属字段。
 *
 * 契约（后端实现中，前端先按此写）：`POST /v1/accounts/self-register`，
 * 请求 `{principal, secret, client}`，成功返回与激活接口相同的 `token`
 * （兼容 `access_token`）。层级与权限完全由服务端决定，前端不补字段。
 */
export async function registerSelf(
  principal: string,
  secret: string,
  options: LoginOptions,
): Promise<Session> {
  let response: Response
  try {
    response = await fetch('/v1/accounts/self-register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ principal, secret, client: options.client }),
    })
  } catch {
    throw new AuthError(0, LOCAL_SERVICE_OFFLINE_MANUAL)
  }
  if (!response.ok) {
    throw new AuthError(response.status, await selfRegistrationMessage(response))
  }
  const body = (await response.json()) as { token?: string; access_token?: string }
  const token = body.token || body.access_token || ''
  if (!token) throw new AuthError(0, '账户已创建，但未能取得登录状态，请重新登录')
  return sessionFromToken(token, options)
}

/** 自助注册失败文案：关闭/未启用一律中性，不透出层级或实现细节。 */
async function selfRegistrationMessage(response: Response): Promise<string> {
  const { code, message } = await readError(response)
  const closed = '当前不允许自行创建账户，请联系设备管理员'
  if (code && /CLOSED|SELF_REGISTRATION/i.test(code)) return closed
  if (response.status === 403) return closed
  if (response.status === 404 || response.status === 501) {
    return '当前暂时无法创建账户，请稍后再试'
  }
  if (response.status === 409) return '该用户名已被使用，请换一个'
  return message || '创建失败，请稍后再试'
}

/**
 * 用后端直接签发的令牌建立会话（激活 / 恢复码重置 / 迁移导入都走这条路）。
 *
 * 必须再取一次 `/v1/auth/me`：激活接口只返回 token + account，不返回
 * display_name/scope/permissions；缺了这些，界面会把"已登录"渲染成空白身份。
 */
export async function sessionFromToken(
  token: string,
  options: LoginOptions,
): Promise<Session> {
  const decoded = decodeToken(token)
  if (!decoded) throw new AuthError(401, '登录信息不正确。')
  const response = await fetch('/v1/auth/me', {
    headers: { [TOKEN_HEADER]: `Bearer ${token}`, Authorization: `Bearer ${token}` },
    cache: 'no-store',
  })
  if (!response.ok) {
    if (response.status === 401) throw new AuthError(401, '登录状态已失效，请重新登录')
    if (response.status === 403) throw new AuthError(403, '该账号当前不可用，无法登录')
    throw new AuthError(response.status, await readErrorMessage(response))
  }
  const body = (await response.json()) as {
    principal?: string
    display_name?: string | null
    scope?: string | null
    permissions?: string[]
    expires_at?: number | null
    parent_account_id?: string | null
    path?: string
    depth?: number
  }
  const session: Session = {
    token,
    principal: body.principal || decoded.subject,
    displayName: body.display_name ?? null,
    scope: body.scope ?? null,
    permissions: Array.isArray(body.permissions) ? body.permissions : [],
    expiresAt: Number(body.expires_at ?? decoded.expiresAt) || decoded.expiresAt,
    client: options.client,
    parentAccountId: body.parent_account_id ?? null,
    path: body.path ?? '',
    depth: Number.isFinite(Number(body.depth)) ? Number(body.depth) : 0,
  }
  saveSession(session, options.remember)
  return session
}

/** 登出：先请后端撤销令牌，再清本地（撤销失败也必须清，否则等于没登出）。 */
export async function logout(): Promise<void> {
  const token = currentToken()
  try {
    if (token) {
      await fetch('/v1/auth/logout', {
        method: 'POST',
        headers: tokenHeaders(),
      })
    }
  } catch {
    // 网络失败不阻塞本地登出。
  } finally {
    clearSession()
  }
}

/**
 * 身份复核没拿到结果时，要不要当成"会话已失效"。
 *
 * `refreshIdentity()` 对三种情况都返回 null：本地本来就没有令牌、服务端 401
 * 后令牌被清掉、网络或服务暂时不可达（令牌还在）。只有**令牌确实消失**才说明
 * 会话已失效；网络抖动不该把用户踢回登录页，更不该谎称"登录已过期"。
 */
export function sessionLossNotice(tokenStillPresent: boolean): 'expired' | null {
  return tokenStillPresent ? null : 'expired'
}

/** 用当前令牌向 `/v1/auth/me` 复核身份是否仍有效（可捕获"已被停用"）。 */
export async function refreshIdentity(): Promise<{
  still_human: boolean
  principal: string
  display_name: string | null
  scope: string | null
  expires_at: number | null
  parent_account_id?: string | null
  path?: string
  depth?: number
} | null> {
  const token = currentToken()
  if (!token) return null
  try {
    const response = await fetch('/v1/auth/me', {
      headers: tokenHeaders(),
      cache: 'no-store',
    })
    if (response.status === 401) {
      clearSession()
      return null
    }
    if (!response.ok) return null
    return await response.json()
  } catch {
    return null
  }
}
