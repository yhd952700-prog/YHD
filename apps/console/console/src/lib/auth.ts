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

/** 当前会话的令牌，供 `Authorization: Bearer` 使用。 */
export function currentToken(): string {
  return getSession()?.token ?? ''
}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body?.detail === 'string') return body.detail
    if (body?.detail) return JSON.stringify(body.detail)
  } catch {
    // 非 JSON 响应：退回状态文本。
  }
  return response.statusText || `HTTP ${response.status}`
}

/** 登录页需要的元信息。任何失败都降级为一份"未配置"的诚实描述。 */
export async function loadAuthConfig(): Promise<AuthConfig | null> {
  try {
    const response = await fetch('/v1/auth/config', { cache: 'no-store' })
    if (!response.ok) return null
    return (await response.json()) as AuthConfig
  } catch {
    return null
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
    throw new AuthError(0, '无法连接到服务，请检查网络或服务是否在运行')
  }
  if (!response.ok) {
    throw new AuthError(response.status, await readError(response))
  }
  const body = (await response.json()) as {
    access_token: string
    principal: string
    display_name: string | null
    scope: string | null
    permissions: string[]
    expires_at: number
    client: string
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
        headers: { Authorization: `Bearer ${token}` },
      })
    }
  } catch {
    // 网络失败不阻塞本地登出。
  } finally {
    clearSession()
  }
}

/** 用当前令牌向 `/v1/auth/me` 复核身份是否仍有效（可捕获"已被停用"）。 */
export async function refreshIdentity(): Promise<{
  still_human: boolean
  principal: string
  display_name: string | null
  scope: string | null
  expires_at: number | null
} | null> {
  const token = currentToken()
  if (!token) return null
  try {
    const response = await fetch('/v1/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
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
