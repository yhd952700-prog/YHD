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
import { deriveProviderStatus } from './lib/providerStatus'
import { applyReducedMotion, getReducedMotion } from './lib/reducedMotion'
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
import { Butler } from './pages/Butler'
import { Audit } from './pages/Audit'
import { Files } from './pages/Files'
import { Goals } from './pages/Goals'
import { Projects } from './pages/Projects'
import { Apps } from './pages/Apps'
import { Events } from './pages/Events'
import { Trust } from './pages/Trust'
import { Network } from './pages/Network'
import { Identity } from './pages/Identity'
import { BusinessCenter, Roster } from './pages/Directory'
import { Knowledge } from './pages/Knowledge'
import { DataCenter, SystemStatus } from './pages/Operations'
import { Preflight } from './pages/Preflight'
import { Settings } from './pages/Settings'
import { Workbench } from './pages/Workbench'
import { Backup } from './pages/Backup'
import { Members } from './pages/Members'
import { Memory } from './pages/Memory'
import { MyEmployees } from './pages/MyEmployees'
import { MyKeys } from './pages/MyKeys'
import { PermissionMatrix } from './pages/PermissionMatrix'
import { Recovery } from './pages/Recovery'
import { RoleTemplates } from './pages/RoleTemplates'
import { ReadOnlyBanner } from './components/AccountChrome'
import { buildProfile, deriveCaps } from './lib/accountCaps'
import { countTreeMembers, loadAccounts, loadTree, useAccountData } from './lib/accounts'
import type { AccountNode, AccountsPayload } from './lib/accounts'
import { visibleNavKeys } from './lib/nav'

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
  butler: { title: '鎏灏管家', sub: '你下目标，它拆解分派，给结论' },
  employees: { title: '能力注册表', sub: '14 个能力模块与 14 个执行层；只读' },
  'my-employees': { title: '我的员工', sub: '创建、调整、停用，或直接把一件事派给它' },
  business: { title: '业务中心', sub: '注册表中已登记的业务能力' },
  knowledge: { title: '知识中心', sub: '服务端文档账本 —— 上传 / 分块预览 / 检索测试' },
  workbench: { title: '工作台', sub: '你自己看任务、看文件、看产出，也可以直接对话' },
  data: { title: '数据中心', sub: '审计存储的真实内容（已排除系统内部记录）' },
  approval: { title: '审批中心', sub: '内核层拦截的人工授权 —— 需人类令牌' },
  audit: { title: '审计链', sub: '真实审计事件与哈希链校验（只读）' },
  files: { title: '工作区文件', sub: 'AI 员工写出的真实产物（只读浏览 / 读取）' },
  goals: { title: '目标与任务', sub: 'AI 员工真实执行过的目标与任务分解（只读）' },
  projects: { title: '项目', sub: '把 AI 员工的目标分类到真实项目之下' },
  apps: { title: '应用', sub: '真实插件与应用 —— 由你激活' },
  events: { title: '事件流', sub: '真实系统事件流 —— 内核总线实况' },
  trust: { title: '信任', sub: '信任内核真实状态（只读）—— 撤销标记 / 信任分 / 自信任链' },
  network: { title: '网络', sub: '网络总线真实状态（只读）—— 统计 / 消息历史' },
  identity: { title: '身份与权限', sub: '真实身份与权限清单（只读，可观测）' },
  status: { title: '系统状态', sub: '依赖探针与安全组件实况' },
  preflight: {
    title: '生产预检',
    sub: '真实配置预检结果 —— 只读，不含任何配置值',
  },
  settings: { title: '系统设置', sub: '端形态 / 主题 / 会话 / 通知' },
  // ── 账号面（§9.8）──────────────────────────────────────────────────
  members: { title: '成员与权限管理', sub: '成员、状态与 Key 授权；写操作都由服务端记录审计' },
  matrix: { title: '权限矩阵', sub: '按成员调整可执行范围' },
  templates: { title: '角色模板', sub: '预设模板只读；成员范围以权限矩阵为准' },
  backup: { title: '备份与迁移', sub: '导出加密包 / 从备份迁移到本机' },
  recovery: { title: '恢复码', sub: '忘记密码时重置；轮换后旧码立即失效' },
  'my-members': { title: '我的账号', sub: '你创建的账号及状态' },
  'my-keys': { title: '我的 Key', sub: 'Key 归属与用量' },
  memory: { title: '记忆', sub: '只读检索与导出' },
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

  // 动效偏好：系统 reduced-motion 默认生效，设置页可手动覆盖。
  useEffect(() => {
    applyReducedMotion(getReducedMotion())
  }, [])

  const [session, setSession] = useState<Session | null>(() => getSession())
  const [active, setActive] = useState<string>(() => readInitialPage())
  const [query, setQuery] = useState('')

  /**
   * 只读查看：只禁用写操作，**不切换数据源**。
   *
   * 服务端没有"生效视角"端点（IA C1）；横幅会明确写出"当前仍是你自己的数据"，
   * 这样不会被误读成已经切到对方账号。
   */
  const [viewAs, setViewAs] = useState<string | null>(null)
  const readOnly = viewAs !== null

  // 账号位置来自 `/auth/me` 的 parent/path/depth；这里只用于裁剪入口。
  const rootPosition =
    session !== null && session.parentAccountId === null && session.depth === 0
  const accounts = useAccountData<AccountsPayload>(
    () =>
      session
        ? loadAccounts({ principal: session.principal, isRoot: rootPosition })
        : Promise.resolve({ data: { members: [], count: 0 }, source: 'live' as const }),
    session?.principal ?? null,
  )
  // `/v1/accounts` 只返回直接成员；根账号的"可管理 N 位成员"必须按整棵树计。
  const tree = useAccountData<{ tree: AccountNode }>(
    () => (session && rootPosition ? loadTree({ principal: session.principal, isRoot: true }) : null),
    session?.principal ?? null,
  )
  const subtreeMemberCount =
    tree.data?.tree !== undefined ? countTreeMembers(tree.data.tree) - 1 : undefined
  const profile = useMemo(
    () =>
      session
        ? buildProfile(session, accounts.data, accounts.source, accounts.error, subtreeMemberCount)
        : null,
    [session, accounts.data, accounts.source, accounts.error, subtreeMemberCount],
  )
  const caps = useMemo(() => deriveCaps(profile), [profile])

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
              parentAccountId: info.parent_account_id ?? prev.parentAccountId,
              path: info.path ?? prev.path,
              depth: Number.isFinite(Number(info.depth)) ? Number(info.depth) : prev.depth,
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

  /**
   * 深链白名单：`?p=` 只接受当前身份可见的页面。
   *
   * 等账号数据落定后再校验，避免"成员权限还没回来就把 my-members 弹回总览"。
   * 不可见页一律回落总览，而不是渲染一个只有标题的空壳。
   */
  useEffect(() => {
    if (!profile?.loaded) return
    if (!visibleNavKeys(caps).has(active)) navigate('overview')
  }, [profile?.loaded, caps, active, navigate])

  const doLogout = useCallback(async () => {
    await performLogout()
    setSession(null)
    setViewAs(null)
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
        parentAccountId: null,
        path: '',
        depth: 0,
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
    async (
      action: string,
      target: string,
      reason: string,
      ttlSeconds: number,
      confirmWildcard: boolean,
    ) => {
      const token = aclView.token
      setAcl((prev) => ({ ...prev, busy: true, error: '' }))
      try {
        // target 必填无默认：授权单位是 (principal, action, target)，见 policyClient.issueApproval。
        await issueApproval(token, [action], target, reason, ttlSeconds, confirmWildcard)
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
  // 模型状态的单一事实源：只读 /v1/health 的 ai_provider。
  // 名册仍负责"各项有没有密钥"，但**不再**提供"是不是真实模型"的判据 ——
  // 两个问题分开回答，界面才不会出现三份互相矛盾的模型状态。
  const health = useApi<HealthPayload>(authed ? '/v1/health' : null, 15_000)
  const provider = useMemo(
    () =>
      deriveProviderStatus({
        provider: health.data?.ai_provider,
        error: health.error,
        loading: health.loading,
        reload: health.reload,
      }),
    [health.data, health.error, health.loading, health.reload],
  )

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

    // 2b) 审计证据链判据。可用性(availability)与证据完整性(evidence_status)是两个
    // 不同的问题：store 打得开≠ 哈希链被重新推导过。以前只有 availability 这一档，
    // 于是"审计链不完整"这条致命事实被"依赖全绿 + 健康度 100%"盖住。
    // 这不是装饰：HC-01 的证据链不可信时，最需要人介入的时刻不能表现得最健康。
    const audit = checks.audit_store
    const evidence = audit?.evidence_status
    if (evidence && evidence !== 'verified' && evidence !== 'unknown') {
      const uncovered = typeof audit?.uncovered_events === 'number' ? audit.uncovered_events : null
      items.push({
        id: 'ready-audit-evidence',
        tone: evidence === 'broken' ? 'danger' : 'warn',
        title:
          evidence === 'broken'
            ? '审计证据链覆盖已断裂'
            : '审计证据链尚未被重新推导',
        detail: [
          evidence === 'broken'
            ? `曾有 checkpoint，但覆盖不再完整（${uncovered ?? '?'} 条事件未被覆盖）——这是完整性回归。`
            : `有 ${uncovered ?? '?'} 条审计事件，但没有任何 checkpoint 覆盖过它们（生产尚未接入周期性验证器）。`,
          '服务本身仍可正常受理流量，此项不影响就绪探针。',
        ].join(''),
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
          title: '还没有可用的模型服务商',
          detail: '对话与知识检索将无法工作。',
        })
      } else if (unconfigured.length === providers.registered_count) {
        items.push({
          id: 'provider-all-unconfigured',
          tone: 'warn',
          title: '所有服务商都缺少密钥',
          detail: unconfigured
            .map((item) => `${item.label}（${item.required_env.join(' / ') || '无需密钥'}）`)
            .join('；'),
        })
      }
    } else if (roster.error) {
      items.push({
        id: 'provider-error',
        tone: 'warn',
        title: '无法读取模型服务配置',
        detail: roster.error,
      })
    }

    // 3b) 诚实性 **只** 来自 /v1/health 的 ai_provider（见 lib/providerStatus.ts）。
    // 这里不再从名册 mode/simulated 推导 —— 那正是三处模型状态互相矛盾的来源。
    if (!provider.provider) {
      items.push({
        id: 'provider-health-unknown',
        tone: 'warn',
        title: '暂时无法读取模型服务状态',
        detail: provider.detail,
      })
    } else if (!provider.isReal) {
      items.push({
        id: 'provider-not-real',
        tone: provider.posture.tone === 'danger' ? 'danger' : 'warn',
        title: provider.isMock ? '当前尚未接入真实模型' : provider.label,
        detail: provider.isMock
          ? provider.detail
          : `${provider.detail}（服务商：${provider.provider.type ?? '—'}）`,
      })
    }

    return items
  }, [
    authConfig.data,
    authConfig.error,
    ready.data,
    ready.error,
    roster.data,
    roster.error,
    provider,
  ])

  // ---------------------------------------------------------------------
  // 渲染
  // ---------------------------------------------------------------------

  if (!session || !profile) {
    return <Login mode={device.mode} onSuccess={setSession} />
  }

  const meta =
    active === 'my-keys' && caps.isRoot
      ? { title: '全部 Key', sub: '每人一条授权记录；标注可代用的成员其 Key 可在模型路由中被你使用' }
      : (PAGE_META[active] ?? PAGE_META.overview)

  return (
    <Shell
      mode={device.mode}
      theme={device.theme}
      session={session}
      profile={profile}
      caps={caps}
      active={active}
      onNavigate={navigate}
      onToggleTheme={() => device.setTheme(device.theme === 'dark' ? 'light' : 'dark')}
      onLogout={() => void doLogout()}
      query={query}
      onQueryChange={setQuery}
      notifications={notifications}
    >
      {viewAs && <ReadOnlyBanner principal={viewAs} onExit={() => setViewAs(null)} />}

      <div className="os-page-head">
        <h1 className="os-page-title">{meta.title}</h1>
        <p className="os-page-sub">{meta.sub}</p>
      </div>

      {active === 'overview' && <Overview query={query} provider={provider} />}
      {active === 'butler' && (
        <Butler
          query={query}
          onOpenApproval={() => navigate('approval')}
          canApproveQueue={caps.canApproveQueue}
        />
      )}
      {active === 'employees' && <Roster query={query} />}
      {active === 'my-employees' && <MyEmployees query={query} />}
      {active === 'business' && <BusinessCenter query={query} />}
      {active === 'knowledge' && (
        <Knowledge query={query} onOpenSettings={() => navigate('settings')} />
      )}
      {active === 'workbench' && (
        <Workbench
          provider={provider}
          onNavigate={(key) => navigate(key)}
          onConfigureModel={() => navigate('settings')}
        />
      )}
      {active === 'data' && <DataCenter query={query} />}
      {active === 'approval' && (
        <div className="os-grid">
          <div className="os-span-12">
            <ApprovalCenter state={aclView} actions={aclActions} />
          </div>
        </div>
      )}
      {active === 'audit' && <Audit query={query} profile={profile} />}
      {active === 'files' && <Files query={query} />}
      {active === 'goals' && <Goals query={query} />}
      {active === 'projects' && <Projects query={query} />}
      {active === 'apps' && <Apps query={query} />}
      {active === 'events' && <Events query={query} />}
      {active === 'trust' && <Trust query={query} />}
      {active === 'network' && <Network query={query} />}
      {active === 'identity' && <Identity query={query} profile={profile} />}
      {active === 'memory' && <Memory profile={profile} caps={caps} readOnly={readOnly} />}
      {active === 'my-keys' && <MyKeys profile={profile} caps={caps} readOnly={readOnly} />}
      {active === 'members' && (
        <Members
          profile={profile}
          caps={caps}
          variant="root"
          readOnly={readOnly}
          onViewAs={setViewAs}
        />
      )}
      {active === 'my-members' && (
        <Members
          profile={profile}
          caps={caps}
          variant="member"
          readOnly={readOnly}
          onViewAs={setViewAs}
        />
      )}
      {active === 'matrix' && (
        <PermissionMatrix profile={profile} caps={caps} readOnly={readOnly} />
      )}
      {active === 'templates' && <RoleTemplates caps={caps} />}
      {active === 'backup' && <Backup profile={profile} caps={caps} readOnly={readOnly} />}
      {active === 'recovery' && <Recovery caps={caps} readOnly={readOnly} />}
      {active === 'status' && <SystemStatus provider={provider} />}
      {active === 'preflight' && <Preflight />}
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
