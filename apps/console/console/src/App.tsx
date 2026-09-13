/**
 * 驾驶舱入口 —— 登录门 + 三形态外壳 + 页面路由。
 *
 * 三端（桌面端 / 网页版 / 手机端）
 * ------------------------------
 * 三端是**同一份代码**在三种断点下的呈现，共用同一套会话与同一批端点。这里不做
 * 任何"某端可以/不可以"的判断 —— 形态只在 `Shell` 里决定导航长什么样，权限判断
 * 始终在服务端完成。
 *
 * 登录门
 * ------
 * 没有会话就只渲染登录页，不挂载任何业务页面。这样"未登录"不会表现为一堆各自
 * 401 的卡片，而是一个明确的入口。会话失效（401 广播）时同样退回登录页。
 *
 * 单一真相
 * --------
 * 会话状态由本组件唯一持有；审批面板、设置页读的都是它。任何一处自己存一份令牌
 * 副本，都会立刻制造出"控制台说已登录、面板说未认证"的分裂。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import './App.css'
import { ApprovalCenter } from './components/ApprovalCenter'
import type { ApprovalActions, ApprovalUiState } from './components/ApprovalCenter'
import { Login } from './components/Login'
import { Shell } from './components/Shell'
import type { OsNotification } from './components/Shell'
import type { AuthConfig } from './lib/auth'
import {
  UNAUTHENTICATED_EVENT,
  clearSession,
  decodeToken,
  getSession,
  logout as performLogout,
  refreshIdentity,
  saveSession,
} from './lib/auth'
import type { Session } from './lib/auth'
import { useDeviceMode } from './lib/mode'
import { useApi } from './lib/api'
import type { HealthPayload, RosterPayload } from './lib/contracts'
import {
  PolicyAuthError,
  describeToken,
  isBuiltinMachinePrincipal,
  issueApproval,
  listApprovals,
  revokeApproval,
} from './lib/policyClient'
import type { ApprovalGrant, EnforcementSnapshot } from './lib/policyClient'
import { Overview } from './pages/Overview'
import { BusinessCenter, KnowledgeCenter, Roster } from './pages/Directory'
import { DataCenter, SystemStatus } from './pages/Operations'
import { Settings } from './pages/Settings'
import { Workbench } from './pages/Workbench'

/** 未认证时的诚实初值 —— 绝不假装"安全运行"。 */
const EMPTY_STATE: ApprovalUiState = {
  token: '',
  authed: false,
  snap: null,
  grants: [],
  busy: false,
  error: '',
  machineIdentity: '',
}

/** 页面标题。与 `lib/nav.ts` 的导航项一一对应，key 是唯一的关联方式。 */
const PAGE_META: Record<string, { title: string; sub: string }> = {
  overview: { title: '总览', sub: '真实运行态 —— 每一项都可追溯到后端数据源' },
  employees: { title: '我 AI 员工', sub: '14 内核 + 14 能力层 + provider 服务面' },
  business: { title: '业务中心', sub: '注册表中已登记的业务能力' },
  knowledge: { title: '知识中心', sub: '运行中服务的真实知识端点' },
  workbench: { title: '工作台', sub: 'JARVIS 流式对话与工具调用' },
  data: { title: '数据中心', sub: '审计存储的真实内容（已排除内核噪声）' },
  approval: { title: '审批中心', sub: '内核层拦截的人工授权 —— 需人类令牌' },
  status: { title: '系统状态', sub: '依赖探针与安全组件实况' },
  settings: { title: '系统设置', sub: '端形态 / 主题 / 会话 / 通知' },
}

/** 合法页面 key。用于校验深链参数，避免 `?p=<乱码>` 把界面带到一个空壳。 */
const KNOWN_PAGES = new Set(Object.keys(PAGE_META))

/**
 * 从 `?p=` 读初始页面。
 *
 * 存在的理由是 PWA：`manifest.webmanifest` 的 `shortcuts` 只能通过 URL 表达"直接
 * 打开某一页"，而装成独立窗口后没有地址栏，深链是唯一的入口方式。
 */
function readInitialPage(): string {
  try {
    const param = new URLSearchParams(window.location.search).get('p')
    if (param && KNOWN_PAGES.has(param)) return param
  } catch {
    // 非浏览器环境或受限 location：退回默认页。
  }
  return 'overview'
}

function App() {
  const device = useDeviceMode()
  const [session, setSession] = useState<Session | null>(() => getSession())
  const [active, setActive] = useState<string>(() => readInitialPage())
  const [query, setQuery] = useState('')

  // Policy 审批真实状态 —— 由 App 单一持有，因为侧栏与审批面板显示的是同一份真相。
  const [acl, setAcl] = useState<ApprovalUiState>(EMPTY_STATE)

  // 审批面板实际看到的视图。
  //
  // 令牌已换但数据尚未回来时标成"忙碌"，而不是"未认证" —— 后者会在每次登录后闪
  // 一下假状态。这里是**派生**的，不需要在 effect 里 setState（那会多跑一轮渲染）。
  const aclView = useMemo<ApprovalUiState>(() => {
    if (!session) return EMPTY_STATE
    if (acl.token === session.token) return acl
    return { ...EMPTY_STATE, token: session.token, busy: true }
  }, [acl, session])

  // ---------------------------------------------------------------------
  // 会话生命周期
  // ---------------------------------------------------------------------

  /**
   * 拉取审批状态。
   *
   * 刻意**不写成 async/await**，而是在 promise 回调里落状态：这样函数自身是同步的，
   * 在 effect 里调用它表达的是"发起一次外部同步"，而不是"在 effect 里同步 setState
   * 引发级联渲染"。对外行为完全一致 —— 状态本来就只在响应回来之后才写。
   */
  const loadPolicy = useCallback((token: string) => {
    const subject = describeToken(token)?.subject ?? ''
    listApprovals(token, { includeRevoked: false }).then(
      (data) => {
        // 一次 list 同时拿回凭据与拦截配置（后端在响应里带了 enforcement）。
        setAcl({
          token,
          authed: true,
          snap: data.enforcement as EnforcementSnapshot,
          grants: data.grants as ApprovalGrant[],
          busy: false,
          error: '',
          machineIdentity: isBuiltinMachinePrincipal(subject) ? subject : '',
        })
      },
      (err: unknown) => {
        const message =
          err instanceof PolicyAuthError
            ? err.message
            : err instanceof Error
              ? err.message
              : '读取失败'
        setAcl({
          token,
          authed: false,
          snap: null,
          grants: [],
          busy: false,
          error: message,
          machineIdentity: '',
        })
      },
    )
  }, [])

  // 会话一出现（登录 / 认领旧令牌 / 粘贴令牌）就把审批状态拉起来。
  useEffect(() => {
    if (session?.token) loadPolicy(session.token)
  }, [session?.token, loadPolicy])

  // 用真实接口复核"这个人还是不是人类"。令牌可能比主体的"人类性"活得久，
  // 因此令牌有效不等于还能用 —— 被停用时要当场退回登录页。
  useEffect(() => {
    if (!session) return
    let alive = true
    void (async () => {
      const info = await refreshIdentity()
      if (!alive) return
      if (!info) {
        if (!getSession()) setSession(null)
        return
      }
      if (!info.still_human) {
        await performLogout()
        if (alive) setSession(null)
        return
      }
      setSession((prev) =>
        prev
          ? {
              ...prev,
              principal: info.principal || prev.principal,
              displayName: info.display_name ?? prev.displayName,
              scope: info.scope ?? prev.scope,
              expiresAt: info.expires_at ?? prev.expiresAt,
            }
          : prev,
      )
    })()
    return () => {
      alive = false
    }
  }, [session])

  /** 切换页面，并把当前页反映到 URL（`?p=`），让深链与刷新都停在原处。 */
  const navigate = useCallback((key: string) => {
    setActive(key)
    try {
      const url = new URL(window.location.href)
      if (key === 'overview') url.searchParams.delete('p')
      else url.searchParams.set('p', key)
      window.history.replaceState(null, '', url.toString())
    } catch {
      // 受限环境（file:// 预览等）：只做内存内导航，不回写 URL。
    }
  }, [])

  const doLogout = useCallback(async () => {
    await performLogout()
    setSession(null)
    setQuery('')
    navigate('overview')
  }, [navigate])

  const onTokenChange = useCallback((token: string) => {
    const value = token.trim()
    if (!value) {
      clearSession()
      setSession(null)
      return
    }
    const decoded = decodeToken(value)
    if (!decoded) {
      // 无法解析的令牌一律当未认证 —— 留着一个永远失败的值只会让人以为已登录。
      clearSession()
      setSession(null)
      return
    }
    saveSession(
      {
        token: value,
        principal: decoded.subject,
        displayName: null,
        scope: null,
        permissions: [],
        expiresAt: decoded.expiresAt,
        client: decoded.client ?? 'web',
      },
      false,
    )
    setSession(getSession())
  }, [])

  useEffect(() => {
    const onExpired = () => setSession(null)
    window.addEventListener(UNAUTHENTICATED_EVENT, onExpired)
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onExpired)
  }, [])

  const onIssue = useCallback(
    async (action: string, reason: string, ttlSeconds: number) => {
      const token = aclView.token
      setAcl((prev) => ({ ...prev, busy: true, error: '' }))
      try {
        await issueApproval(token, [action], reason, ttlSeconds)
      } catch (err) {
        setAcl((prev) => ({
          ...prev,
          busy: false,
          error: err instanceof Error ? err.message : '签发失败',
        }))
        return
      }
      loadPolicy(token)
    },
    [aclView.token, loadPolicy],
  )

  const onRevoke = useCallback(
    async (grantId: string) => {
      const token = aclView.token
      setAcl((prev) => ({ ...prev, busy: true, error: '' }))
      try {
        await revokeApproval(token, grantId, '')
      } catch (err) {
        setAcl((prev) => ({
          ...prev,
          busy: false,
          error: err instanceof Error ? err.message : '撤销失败',
        }))
        return
      }
      loadPolicy(token)
    },
    [aclView.token, loadPolicy],
  )

  const aclActions: ApprovalActions = useMemo(
    () => ({
      onTokenChange,
      onRefresh: () => loadPolicy(aclView.token),
      onIssue,
      onRevoke,
    }),
    [aclView.token, loadPolicy, onIssue, onRevoke, onTokenChange],
  )

  // ---------------------------------------------------------------------
  // 通知聚合（全部来自真实端点）
  // ---------------------------------------------------------------------
  // 只在已登录时轮询：未登录时通知栏根本不可见，多发请求只会制造噪音。
  const authed = session !== null
  // 503 必须接受：降级响应带着完整的检查项，那正是最该显示真话的时刻。
  const ready = useApi<HealthPayload>(authed ? '/v1/ready' : null, 30_000, [503])
  const roster = useApi<RosterPayload>(authed ? '/v1/dashboard/roster' : null, 60_000)
  const authConfig = useApi<AuthConfig>(authed ? '/v1/auth/config' : null, 0)

  const notifications = useMemo<OsNotification[]>(() => {
    const items: OsNotification[] = []

    // 1) 登录通道是否可用（fail-closed 时没人能进来，这是最重要的一条）
    const config = authConfig.data
    if (config) {
      if (!config.secret_store.configured) {
        items.push({
          id: 'auth-no-secret',
          tone: 'danger',
          title: '尚未配置登录凭据',
          detail: '当前任何人都无法登录（fail-closed）。用 register_human_identity.py 设置凭据。',
        })
      } else if (config.login_eligible_humans === 0) {
        items.push({
          id: 'auth-no-eligible',
          tone: 'warn',
          title: '没有可登录的人类主体',
          detail: '凭据存储已就绪，但没有任何 ACTIVE 的人类身份设置了密码。',
        })
      }
    } else if (authConfig.error) {
      items.push({
        id: 'auth-config-error',
        tone: 'warn',
        title: '无法读取登录配置',
        detail: authConfig.error,
      })
    }

    // 2) 依赖健康：把不健康的检查项逐条报出来，而不是笼统说"降级"
    const checks = ready.data?.checks ?? {}
    const unhealthy = Object.entries(checks).filter(([, detail]) => detail?.status !== 'healthy')
    if (unhealthy.length > 0) {
      items.push({
        id: 'ready-degraded',
        tone: 'danger',
        title: `依赖不健康（${unhealthy.length} 项）`,
        detail: unhealthy
          .map(([name, detail]) => `${name}${detail?.error ? `：${detail.error}` : ''}`)
          .join('；'),
      })
    } else if (ready.error) {
      items.push({
        id: 'ready-error',
        tone: 'warn',
        title: '就绪探针不可用',
        detail: ready.error,
      })
    }

    // 3) provider 配置：未配置不是故障，但会直接决定对话/知识检索能不能用
    const providers = roster.data?.providers
    if (providers) {
      const unconfigured = providers.items.filter((item) => !item.configured)
      if (providers.registered_count === 0) {
        items.push({
          id: 'provider-none',
          tone: 'warn',
          title: '没有已注册的 LLM provider',
          detail: '对话与知识检索将无法工作。',
        })
      } else if (unconfigured.length === providers.registered_count) {
        items.push({
          id: 'provider-all-unconfigured',
          tone: 'warn',
          title: '所有 provider 都缺少密钥',
          detail: unconfigured
            .map((item) => `${item.label}（${item.required_env.join(' / ') || '无需密钥'}）`)
            .join('；'),
        })
      }
    } else if (roster.error) {
      items.push({
        id: 'provider-error',
        tone: 'warn',
        title: '无法读取 provider 状态',
        detail: roster.error,
      })
    }

    return items
  }, [authConfig.data, authConfig.error, ready.data, ready.error, roster.data, roster.error])

  // ---------------------------------------------------------------------
  // 渲染
  // ---------------------------------------------------------------------

  if (!session) {
    return <Login mode={device.mode} onSuccess={setSession} />
  }

  const meta = PAGE_META[active] ?? PAGE_META.overview

  return (
    <Shell
      mode={device.mode}
      theme={device.theme}
      session={session}
      active={active}
      onNavigate={navigate}
      onToggleTheme={() => device.setTheme(device.theme === 'dark' ? 'light' : 'dark')}
      onLogout={() => void doLogout()}
      query={query}
      onQueryChange={setQuery}
      notifications={notifications}
    >
      <div className="os-page-head">
        <h1 className="os-page-title">{meta.title}</h1>
        <p className="os-page-sub">{meta.sub}</p>
      </div>

      {active === 'overview' && <Overview query={query} />}
      {active === 'employees' && <Roster query={query} />}
      {active === 'business' && <BusinessCenter query={query} />}
      {active === 'knowledge' && <KnowledgeCenter query={query} />}
      {active === 'workbench' && <Workbench />}
      {active === 'data' && <DataCenter query={query} />}
      {active === 'approval' && (
        <div className="os-grid">
          <div className="os-span-12">
            <ApprovalCenter state={aclView} actions={aclActions} />
          </div>
        </div>
      )}
      {active === 'status' && <SystemStatus />}
      {active === 'settings' && (
        <Settings
          device={device}
          session={session}
          notifications={notifications}
          onLogout={() => void doLogout()}
        />
      )}
    </Shell>
  )
}

export default App
