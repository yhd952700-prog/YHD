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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import { ApprovalCenter } from './components/ApprovalCenter'
import type { ApprovalActions, ApprovalUiState } from './components/ApprovalCenter'
import { Login } from './components/Login'
import { Shell } from './components/Shell'
import type { OsNotification } from './components/Shell'
import type { AuthConfig } from './lib/auth'
import { componentLabel } from './lib/format'
import {
  PENDING_RECOVERY_EVENT,
  UNAUTHENTICATED_EVENT,
  clearPendingRecovery,
  clearSession,
  decodeToken,
  getSession,
  logout as performLogout,
  readPendingRecovery,
  refreshIdentity,
  saveSession,
  sessionLossNotice,
} from './lib/auth'
import type { PendingRecovery, Session } from './lib/auth'
import { useDeviceMode } from './lib/mode'
import { useApi } from './lib/api'
import type { HealthPayload, RosterPayload } from './lib/contracts'
import { deriveGoalNotices } from './lib/goalNotices'
import type { GoalsResponse } from './lib/operator'
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
import { Notifications } from './pages/Notifications'
import { Trust } from './pages/Trust'
import { Network } from './pages/Network'
import { Identity } from './pages/Identity'
import { BusinessCenter, Roster } from './pages/Directory'
import { Knowledge } from './pages/Knowledge'
import { DataCenter, SystemStatus } from './pages/Operations'
import { Preflight } from './pages/Preflight'
import { Settings } from './pages/Settings'
import { About } from './pages/About'
import { EnterpriseOverview, ENTERPRISE_OVERVIEW_PAGE } from './pages/EnterpriseOverview'
import { Org, ORG_PAGE } from './pages/Org'
import { TaskCenter, TASK_CENTER_PAGE } from './pages/TaskCenter'
import { SkillCenter, SKILLS_CENTER_PAGE } from './pages/SkillCenter'
import { ToolCenter, TOOLS_CENTER_PAGE } from './pages/ToolCenter'
import { Backup } from './pages/Backup'
import { Members } from './pages/Members'
import { AccountSecurity } from './pages/AccountSecurity'
import { Memory } from './pages/Memory'
import { MyData } from './pages/MyData'
import { Me } from './pages/Me'
import { MyEmployees } from './pages/MyEmployees'
import { Office } from './pages/Office'
import { MyKeys } from './pages/MyKeys'
import { PermissionMatrix } from './pages/PermissionMatrix'
import { Recovery } from './pages/Recovery'
import { RoleTemplates } from './pages/RoleTemplates'
import { ReadOnlyBanner } from './components/AccountChrome'
import { buildProfile, deriveCaps } from './lib/accountCaps'
import { countTreeMembers, loadAccounts, loadTree, useAccountData } from './lib/accounts'
import type { AccountNode, AccountsPayload } from './lib/accounts'
import { visibleNavKeys, HOME_KEY } from './lib/nav'

/** 把恢复码导出成本地文件；内容与设置页原来提供的一致。 */
function downloadRecovery(recovery: PendingRecovery): void {
  const payload = {
    format: 'liuhao-recovery-material',
    account: recovery.account,
    recovery_code: recovery.code,
    generated_at: new Date().toISOString(),
    note: '此文件由本机界面生成，未加密。请保存到离线介质，不要放在同步盘。',
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `liuhao-recovery-${recovery.account || 'account'}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

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
  overview: { title: '运行看板', sub: '真实运行状态 —— 每一项都能追溯到来源' },
  [ENTERPRISE_OVERVIEW_PAGE.key]: {
    title: ENTERPRISE_OVERVIEW_PAGE.title,
    sub: ENTERPRISE_OVERVIEW_PAGE.sub,
  },
  [ORG_PAGE.key]: { title: ORG_PAGE.title, sub: ORG_PAGE.sub },
  [TASK_CENTER_PAGE.key]: { title: TASK_CENTER_PAGE.title, sub: TASK_CENTER_PAGE.sub },
  [SKILLS_CENTER_PAGE.key]: { title: SKILLS_CENTER_PAGE.title, sub: SKILLS_CENTER_PAGE.sub },
  [TOOLS_CENTER_PAGE.key]: { title: TOOLS_CENTER_PAGE.title, sub: TOOLS_CENTER_PAGE.sub },
  butler: { title: '贾维斯', sub: '你下目标，它拆解分派，给结论' },
  employees: { title: '能力注册表', sub: '14 个能力模块与 14 个执行层；只读' },
  'my-employees': { title: '我的员工', sub: '创建、调整、停用，或直接把一件事派给它' },
  office: { title: '办公室', sub: 'AI 员工的工位墙：谁在干活、干到哪、卡在哪' },
  business: { title: '业务中心', sub: '注册表中已登记的业务能力' },
  knowledge: { title: '知识中心', sub: '服务端文档账本 —— 上传 / 分块预览 / 检索测试' },
  data: { title: '数据中心', sub: '审计存储的真实内容（已排除系统内部记录）' },
  approval: { title: '审批中心', sub: '动作拦截与人工授权' },
  audit: { title: '审计链', sub: '真实审计事件与哈希链校验（只读）' },
  files: { title: '工作区文件', sub: 'AI 员工写出的真实产物（只读浏览 / 读取）' },
  goals: { title: '目标与任务', sub: 'AI 员工真实执行过的目标与任务分解（只读）' },
  projects: { title: '项目', sub: '把 AI 员工的目标分类到真实项目之下' },
  apps: { title: '应用', sub: '真实插件与应用 —— 由你激活' },
  events: { title: '事件流', sub: '真实系统事件流 —— 实时记录每一次动作' },
  notifications: {
    title: '通知中心',
    sub: '值得你留意的事件；已读只记在这台设备上',
  },
  trust: { title: '信任', sub: '真实信任状态（只读）—— 撤销标记 / 信任分 / 自信任链' },
  network: { title: '网络', sub: '网络总线真实状态（只读）—— 统计 / 消息历史' },
  identity: { title: '身份与权限', sub: '真实身份与权限清单（只读，可观测）' },
  status: { title: '系统状态', sub: '依赖探针与安全组件实况' },
  preflight: {
    title: '生产预检',
    sub: '真实配置预检结果 —— 只读，不含任何配置值',
  },
  settings: { title: '系统设置', sub: '端形态 / 主题 / 会话 / 通知' },
  me: { title: '我的', sub: '账号、数据与隐私入口；手机上也能直接走到' },
  about: { title: '关于与隐私', sub: '版本 / 数据目录 / 加密边界 / 联网说明 / 许可证' },
  // ── 账号面（§9.8）──────────────────────────────────────────────────
  members: { title: '成员与权限管理', sub: '成员、状态与 Key 授权；写操作都由服务端记录审计' },
  matrix: { title: '权限矩阵', sub: '按成员调整可执行范围' },
  templates: { title: '角色模板', sub: '预设模板只读；成员范围以权限矩阵为准' },
  backup: { title: '备份与迁移', sub: '导出加密包 / 从备份迁移到本机' },
  recovery: { title: '恢复码', sub: '忘记密码时重置；轮换后旧码立即失效' },
  'my-members': { title: '我的账号', sub: '你创建的账号及状态' },
  account: { title: '账号安全', sub: '修改本人密码；旧密码错误会被明确拒绝' },
  'my-keys': { title: '我的 Key', sub: 'Key 归属与用量' },
  'my-data': { title: '我的数据', sub: '导出人可读 ZIP；记忆与知识库可回导' },
  memory: { title: '记忆', sub: '检索、纠正、删除与导出' },
}

/** 合法页面 key。用于校验深链参数，避免 `?p=<乱码>` 把界面带到一个空壳。 */
const KNOWN_PAGES = new Set(Object.keys(PAGE_META))

/**
 * 旧路由 → 新落点（Boss 2026-11 收敛）。
 *
 * 自由对话并进贾维斯的「直接问」后，`workbench` 不再是一个页面；运行看板拆进
 * 首页状态带，`overview` 也不再单列。旧书签、桌面壳快捷方式、外链一律**重定向**，
 * 不做 404、不留白屏。
 */
export const PAGE_ALIASES: Record<string, string> = {
  workbench: 'butler',
  chat: 'butler',
  freechat: 'butler',
  'free-chat': 'butler',
  overview: HOME_KEY,
  runboard: HOME_KEY,
  // 手写地址 `#home` / `?p=home` 也落到企业总览，避免两种命名各说各话。
  home: HOME_KEY,
}

/** 把一个可能是旧名字的 key 解析成真实页面 key；不认识的返回 null。 */
export function resolvePageKey(raw: string): string | null {
  const key = PAGE_ALIASES[raw] ?? raw
  return KNOWN_PAGES.has(key) ? key : null
}

/**
 * 从 `?p=` 或 `#hash` 读初始页面。
 *
 * 存在的理由是 PWA：`manifest.webmanifest` 的 `shortcuts` 只能通过 URL 表达"直接
 * 打开某一页"，而装成独立窗口后没有地址栏，深链是唯一的入口方式。
 *
 * 同时接受 hash 形式（`#butler`）——桌面壳与旧文档里两种写法都出现过，
 * 这里统一解析、随后由 `canonicalizeUrl` 改写成规范的 `?p=`。
 */
function readInitialPage(fallback: string = HOME_KEY): string {
  try {
    const url = new URL(window.location.href)
    const fromHash = url.hash.replace(/^#\/?/, '')
    const candidate = fromHash || url.searchParams.get('p') || ''
    if (candidate) {
      const resolved = resolvePageKey(candidate)
      if (resolved) return resolved
    }
  } catch {
    // 非浏览器环境或受限 location：退回默认页。
  }
  return fallback
}

/**
 * 从 `?model=` 读深链目标模型。
 *
 * 桌面端的「去填写 Key」把模型的本地名称带进来，设置页据此自动展开并聚焦
 * Key 输入框。旧构建不认这个参数（只读 `?p=`），会停在设置页；那一侧由
 * 桌面端文案提示「请点编辑」，所以这里只负责"新构建能认"。
 */
export function readDeepLinkModel(): string {
  try {
    return (new URLSearchParams(window.location.search).get('model') ?? '').trim()
  } catch {
    return ''
  }
}

/**
 * 桌面壳用 `--web` 打开浏览器时会拼 `?mode=web`。
 *
 * 只用于标题后缀与常驻提示，**不改变**端形态判定（device.mode 仍按视口算）：
 * 浏览器模式下仍然要按窄屏/宽屏给出对应布局。
 */
export function readBrowserMode(): boolean {
  try {
    return new URLSearchParams(window.location.search).get('mode') === 'web'
  } catch {
    return false
  }
}

function App() {
  const device = useDeviceMode()

  // 动效偏好：系统 reduced-motion 默认生效，设置页可手动覆盖。
  useEffect(() => {
    applyReducedMotion(getReducedMotion())
  }, [])

  const [session, setSession] = useState<Session | null>(() => getSession())
  // 桌面端、网页版、手机端都先看到企业总览：它是六组里的「首页」。
  // （Boss 2026-11 定稿：手机首屏从运行看板换成企业总览。）
  const [active, setActive] = useState<string>(() => readInitialPage())
  /** 设置页要自动展开的模型（来自深链或控制台里的「去填写 Key」）。 */
  const [focusModel, setFocusModel] = useState<string>(() =>
    readInitialPage() === 'settings' ? readDeepLinkModel() : '',
  )
  const [query, setQuery] = useState('')
  /**
   * 为什么回到了登录页。
   *
   * 会话过期与被登出都会切回登录页，但"静默跳回"会让人以为页面坏了。
   * 这里记下原因，登录页据此给出「需要重新登录」的明确提示与入口。
   */
  const [loginNotice, setLoginNotice] = useState<'expired' | 'logged-out' | null>(null)

  /** `?mode=web`：桌面壳拉起的浏览器窗口。提示只在这里出现。 */
  const [browserMode] = useState(readBrowserMode)
  const [browserNoticeClosed, setBrowserNoticeClosed] = useState(false)

  // 浏览器模式下标题必须能一眼分清「这不是桌面应用」；卸载时还原。
  useEffect(() => {
    if (!browserMode) return
    const original = document.title
    document.title = `${original} · 浏览器模式`
    return () => {
      document.title = original
    }
  }, [browserMode])

  /**
   * 图形化首启刚创建的恢复码。
   *
   * 设置页提交后直接进入主界面，所以恢复码不能停在中间页；它被暂存在本机，
   * 在这里以横幅出现一次。用户下载或点"我已保存"后即清除。
   */
  const [pendingRecovery, setPendingRecovery] = useState<PendingRecovery | null>(() =>
    readPendingRecovery(),
  )
  /** 恢复码横幅的"复制"反馈：'done' 成功，'fail' 剪贴板不可用。 */
  const [recoveryCopy, setRecoveryCopy] = useState<'idle' | 'done' | 'fail'>('idle')

  useEffect(() => {
    if (pendingRecovery) setRecoveryCopy('idle')
  }, [pendingRecovery])

  /**
   * 注册响应可能晚于本组件挂载（登录页在同一个 App 里）。保存恢复码后
   * 立即同步一次，否则横幅要等用户手动刷新才会出现。
   */
  useEffect(() => {
    const sync = () => setPendingRecovery(readPendingRecovery())
    window.addEventListener(PENDING_RECOVERY_EVENT, sync)
    return () => window.removeEventListener(PENDING_RECOVERY_EVENT, sync)
  }, [])

  /**
   * 旧深链规范化：进来就把地址改写成规范形式。
   *
   * 三种情况：旧名字（workbench/chat/overview…）→ 新落点；hash 形式（#butler）→
   * `?p=butler`；首页不保留 `?p=`。只 replaceState，不产生历史记录。
   */
  useEffect(() => {
    try {
      const url = new URL(window.location.href)
      const rawHash = url.hash.replace(/^#\/?/, '')
      const raw = rawHash || url.searchParams.get('p') || ''
      if (!raw) return
      // 页内锚点（#os-main 等）不是路由，别动它。
      if (rawHash && !resolvePageKey(rawHash)) return
      const resolved = resolvePageKey(raw)
      if (!resolved) return
      url.hash = ''
      if (resolved === HOME_KEY) url.searchParams.delete('p')
      else url.searchParams.set('p', resolved)
      if (resolved !== raw || rawHash) {
        window.history.replaceState(null, '', url.toString())
      }
    } catch {
      // 受限环境：不改地址，只保留内存内导航。
    }
  }, [])
  /**
   * 只读查看：只禁用写操作，**不切换数据源**。
   *
   * 服务端没有"生效视角"端点（IA C1）；横幅会明确写出"当前仍是你自己的数据"，
   * 这样不会被误读成已经切到对方账号。
   */
  const [viewAs, setViewAs] = useState<string | null>(null)
  const readOnly = viewAs !== null

  // 是否账号树根只来自 `/auth/me` 的 is_root；不保留 parent/path/depth。
  const rootPosition = session !== null && session.isRoot
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

  /**
   * 深链门控：账号数据落定前，除总览外一律**不挂载**目标页。
   *
   * 子组件的 effect 会先于父级回退 effect 执行，所以只靠下面那个 redirect
   * 会出现"先渲染一帧、真发一次请求"的窗口：成员用 `?p=preflight` / `?p=status`
   * 打开时会打到管理员端点。这里在渲染层就挡住：加载中 → 占位；落定后不可见
   * → 总览。管理员不受影响（落定后正常挂载）。
   */
  const pagesSettled = !session || profile?.loaded === true || Boolean(profile?.error)
  const visiblePages = useMemo(() => visibleNavKeys(caps), [caps])
  const shown = !pagesSettled
    ? active === HOME_KEY
      ? active
      : null
    : visiblePages.has(active)
      ? active
      : HOME_KEY

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
    if (!session?.token) return
    let alive = true
    void (async () => {
      const info = await refreshIdentity()
      if (!alive) return
      if (!info) {
        // 令牌已被服务端判无效（401 时 clearSession）——必须明说"需要重新登录"，
        // 不能静默跳回登录页。网络抖动时令牌还在，不登出、不提示。
        const notice = sessionLossNotice(Boolean(getSession()))
        if (notice) {
          setLoginNotice(notice)
          setSession(null)
        }
        return
      }
      if (!info.still_human) {
        await performLogout()
        if (alive) {
          setLoginNotice('expired')
          setSession(null)
        }
        return
      }
      setSession((prev) => {
        if (!prev) return prev
        const next = {
          ...prev,
          principal: info.principal || prev.principal,
          displayName: info.display_name ?? prev.displayName,
          scope: info.scope ?? prev.scope,
          expiresAt: info.expires_at ?? prev.expiresAt,
          isRoot: typeof info.is_root === 'boolean' ? info.is_root : prev.isRoot,
        }
        // 服务端复核结果通常与本地一致；返回同一个对象引用，避免 effect 依赖
        // 因对象身份变化而再次触发，把自己变成 30rps 的 /v1/auth/me 轮询。
        const unchanged =
          next.principal === prev.principal &&
          next.displayName === prev.displayName &&
          next.scope === prev.scope &&
          next.expiresAt === prev.expiresAt &&
          next.isRoot === prev.isRoot
        return unchanged ? prev : next
      })
    })()
    return () => {
      alive = false
    }
  }, [session?.token])

  /** 切换页面，并把当前页反映到 URL（`?p=`），让深链与刷新都停在原处。 */
  const navigate = useCallback((key: string, options?: { model?: string | null }) => {
    setActive(key)
    const model = key === 'settings' ? (options?.model ?? '').trim() : ''
    setFocusModel(model)
    try {
      const url = new URL(window.location.href)
      // 首页（企业总览）回首页时不保留 `?p=`，保持干净地址；hash 深链也一并清掉。
      if (key === HOME_KEY) url.searchParams.delete('p')
      else url.searchParams.set('p', key)
      url.hash = ''
      // `model` 只对设置页有意义；离开设置页就清掉，避免下次进来莫名展开旧模型。
      if (model) url.searchParams.set('model', model)
      else url.searchParams.delete('model')
      window.history.replaceState(null, '', url.toString())
    } catch {
      // 受限环境（file:// 预览等）：只做内存内导航，不回写 URL。
    }
  }, [])

  /** 跳到设置页并定位到某个模型（控制台「去填写 Key」的统一出口）。 */
  const openModelSettings = useCallback(
    (model?: string | null) => navigate('settings', { model }),
    [navigate],
  )

  /**
   * 深链白名单：`?p=` 只接受当前身份可见的页面。
   *
   * 等账号数据落定后再校验，避免"成员权限还没回来就把 my-members 弹回总览"。
   * 不可见页一律回落总览，而不是渲染一个只有标题的空壳。
   */
  useEffect(() => {
    if (!profile?.loaded) return
    if (!visiblePages.has(active)) navigate(HOME_KEY)
  }, [profile?.loaded, visiblePages, active, navigate])

  const doLogout = useCallback(async () => {
    await performLogout()
    setLoginNotice('logged-out')
    setSession(null)
    setViewAs(null)
    setQuery('')
    navigate(HOME_KEY)
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
        isRoot: false,
      },
      false,
    )
    setSession(getSession())
  }, [])

  useEffect(() => {
    const onExpired = () => {
      // 令牌失效（过期 / 改密 / 被吊销 / 未带令牌）统一由请求层广播；
      // 记住"这是被登出"，登录页才能说清楚为什么回来了。
      setLoginNotice((prev) => prev ?? 'expired')
      setSession(null)
    }
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
  // 通知栏要展示最近 24 小时的目标终态；这里只读列表，不触发任何执行。
  const goals = useApi<GoalsResponse>(authed ? '/v1/goals' : null, 30_000)
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

    // 1) 登录通道是否可用（没有凭据时没人能进来，这是最重要的一条）
    const config = authConfig.data
    if (config) {
      if (!config.secret_store.configured) {
        items.push({
          id: 'auth-no-secret',
          tone: 'danger',
          title: '尚未配置登录凭据',
          detail: '当前没有任何账号可以登录。请先在登录页按提示完成初始化，再回到这里。',
        })
      } else if (config.login_eligible_humans === 0) {
        items.push({
          id: 'auth-no-eligible',
          tone: 'warn',
          title: '还没有可登录的账号',
          detail: '登录凭据已就绪，但还没有账号设置密码。请在登录页完成首次设置。',
        })
      }
    } else if (authConfig.error) {
      items.push({
        id: 'auth-config-error',
        tone: 'warn',
        title: '无法读取登录配置',
        detail: '鎏灏的本地服务没有运行。请打开鎏灏，然后刷新重试。',
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
          .map(([name, detail]) => {
            // 检查项名是 snake_case（api_key_manager），原始 error 是英文异常文本；
            // 两者都不能直接铺给用户。后端已给出 user_message（人话 + 下一步），
            // 缺失时只报"哪一项没通过"，不猜技术原因。
            const label = componentLabel(name)
            const message =
              typeof detail?.user_message === 'string' && detail.user_message.trim()
                ? detail.user_message
                : '这项检查没有通过，请到系统状态页查看'
            return `${label}：${message}`
          })
          .join('；'),
      })
    } else if (ready.httpStatus === 503) {
      // 刚启动时各项自检尚未跑完，探针会用 503 表示"还没好"。这不是故障，
      // 页面本来就会每 30 秒自动重试；这里只说清状态，不制造红色告警。
      items.push({
        id: 'ready-preparing',
        tone: 'warn',
        title: '服务正在准备',
        detail: '刚启动时自检需要一点时间，界面会自动重试，无需操作。',
      })
    } else if (ready.error) {
      items.push({
        id: 'ready-error',
        tone: 'warn',
        title: '就绪探针不可用',
        detail: '暂时读不到各项依赖的状态，请确认鎏灏仍在运行，然后刷新重试。',
      })
    }

    // 2b) 审计证据链判据。可用性(availability)与证据完整性(evidence_status)是两个
    // 不同的问题：store 打得开≠ 哈希链被重新推导过。以前只有 availability 这一档，
    // 于是"审计链不完整"这条致命事实被"依赖全绿 + 健康度 100%"盖住。
    // 这不是装饰：HC-01 的证据链不可信时，最需要人介入的时刻不能表现得最健康。
    const audit = checks.audit_store
    const evidence = audit?.evidence_status
    const uncovered = typeof audit?.uncovered_events === 'number' ? audit.uncovered_events : null
    if (evidence === 'pending') {
      // 两次检查点之间的正常尾差：历史有覆盖、只有新事件还没核对。等下一轮即可。
      items.push({
        id: 'ready-audit-pending',
        tone: 'warn',
        title: '正在核对新事件',
        detail: `有 ${uncovered ?? '?'} 条新事件等待下一轮自检核对（约 5 分钟内自动完成），不影响使用。`,
      })
    } else if (evidence && evidence !== 'verified' && evidence !== 'unknown') {
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
          detail: `${unconfigured
            .map((item) => item.label)
            .join('；')} 都还没有配置密钥。下一步：到「系统设置 → 模型管理」填入任意一家的密钥。`,
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

    // 4) 派活结果：只报最近 24 小时的终态，进行中的任务不是通知。
    items.push(...deriveGoalNotices(goals.data?.goals))

    return items
  }, [
    authConfig.data,
    authConfig.error,
    ready.data,
    ready.error,
    ready.httpStatus,
    roster.data,
    roster.error,
    provider,
    goals.data,
  ])

  // ---------------------------------------------------------------------
  // 渲染
  // ---------------------------------------------------------------------

  // 登录成功从登录页切到壳层时，焦点原本会落到 body；交到主内容上，
  // 键盘用户从"跳过导航"继续，读屏也会立刻读出当前页标题。
  const focusMainAfterLogin = useRef(false)
  useEffect(() => {
    if (!session || !focusMainAfterLogin.current) return
    let tries = 0
    let timer = 0
    const tick = () => {
      const main = document.getElementById('os-main')
      if (main) {
        focusMainAfterLogin.current = false
        main.focus()
        return
      }
      if (tries++ < 20) timer = window.setTimeout(tick, 50)
    }
    timer = window.setTimeout(tick, 0)
    return () => window.clearTimeout(timer)
    // profile 到位后壳层才会渲染出 main；账号信息比会话慢时也要能补上焦点。
  }, [session, profile])

  /** 浏览器模式提示：登录前也要可见（此时还没有壳层）。 */
  const browserNotice =
    browserMode && !browserNoticeClosed ? (
      <div
        className="os-note"
        role="status"
        data-testid="browser-mode-notice"
        style={{ justifyContent: 'space-between', alignItems: 'center' }}
      >
        <span>当前为浏览器模式；桌面端请用托盘菜单「打开鎏灏窗口」。</span>
        <button
          type="button"
          className="os-btn os-btn-ghost"
          onClick={() => setBrowserNoticeClosed(true)}
        >
          知道了
        </button>
      </div>
    ) : null

  if (!session || !profile) {
    return (
      <>
        {browserNotice}
        <Login
          mode={device.mode}
          notice={loginNotice}
          onSuccess={(next) => {
            setLoginNotice(null)
            focusMainAfterLogin.current = true
            setSession(next)
          }}
        />
      </>
    )
  }

  const meta =
    shown === null
      ? { title: '正在确认访问权限', sub: '账号数据就绪后自动打开' }
      : shown === 'my-keys' && caps.isRoot
        ? { title: '全部 Key', sub: '每人一条授权记录；标注可代用的成员其 Key 可在模型路由中被你使用' }
        : (PAGE_META[shown] ?? PAGE_META[HOME_KEY])

  return (
    <Shell
      mode={device.mode}
      theme={device.theme}
      session={session}
      profile={profile}
      caps={caps}
      active={shown ?? 'overview'}
      pageTitle={meta.title}
      onNavigate={navigate}
      onToggleTheme={() => device.setTheme(device.theme === 'dark' ? 'light' : 'dark')}
      onLogout={() => void doLogout()}
      query={query}
      onQueryChange={setQuery}
      notifications={notifications}
    >
      {browserNotice}

      {viewAs && <ReadOnlyBanner principal={viewAs} onExit={() => setViewAs(null)} />}

      {pendingRecovery && (
        <div
          className="os-note"
          role="status"
          style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 8 }}
        >
          <strong>账号已就绪。下面是恢复码（只显示这一次）</strong>
          <code style={{ fontSize: 14, wordBreak: 'break-all' }}>{pendingRecovery.code}</code>
          <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }}>
            换机或忘记密码时用它重置本账号的密码。请复制/下载后保存到离线介质，
            不要截图发到聊天工具。
          </span>
          {pendingRecovery.warning && (
            <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }}>
              {pendingRecovery.warning}
            </span>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              className="os-btn"
              onClick={async () => {
                try {
                  if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
                  await navigator.clipboard.writeText(pendingRecovery.code)
                  setRecoveryCopy('done')
                } catch {
                  setRecoveryCopy('fail')
                }
              }}
            >
              {recoveryCopy === 'done' ? '已复制' : '复制恢复码'}
            </button>
            <button type="button" className="os-btn" onClick={() => downloadRecovery(pendingRecovery)}>
              下载恢复材料
            </button>
            <button
              type="button"
              className="os-btn os-btn-ghost"
              onClick={() => {
                clearPendingRecovery()
                setPendingRecovery(null)
              }}
            >
              我已保存
            </button>
          </div>
          {recoveryCopy === 'fail' && (
            <span style={{ fontSize: 12, color: 'var(--os-text-dim)' }}>
              浏览器不允许自动复制，请手动选中上面的恢复码复制。
            </span>
          )}
        </div>
      )}

      {/* 企业总览自带页头，这里不再重复渲染同一组标题与副说明。 */}
      {shown !== ENTERPRISE_OVERVIEW_PAGE.key && (
        <div className="os-page-head">
          <h1 className="os-page-title">{meta.title}</h1>
          <p className="os-page-sub">{meta.sub}</p>
        </div>
      )}

      {shown === null && (
        <div className="os-grid">
          <div className="os-span-12">
            <div className="os-note" role="status">
              正在确认访问权限…账号数据就绪后自动打开。
            </div>
          </div>
        </div>
      )}

      {shown === 'overview' && (
        <Overview
          query={query}
          provider={provider}
          onNavigate={navigate}
          isRoot={caps.isRoot}
        />
      )}
      {shown === ENTERPRISE_OVERVIEW_PAGE.key && (
        <EnterpriseOverview principal={session.principal} onNavigate={navigate} />
      )}
      {shown === ORG_PAGE.key && <Org query={query} />}
      {shown === TASK_CENTER_PAGE.key && <TaskCenter query={query} />}
      {shown === SKILLS_CENTER_PAGE.key && <SkillCenter query={query} />}
      {shown === TOOLS_CENTER_PAGE.key && <ToolCenter query={query} />}
      {shown === 'butler' && (
        <Butler
          query={query}
          onOpenApproval={() => navigate('approval')}
          canApproveQueue={caps.canApproveQueue}
          provider={provider}
          onOpenUsage={() => navigate('my-keys')}
          onConfigureModel={() => openModelSettings(provider.provider?.model)}
        />
      )}
      {shown === 'office' && (
        <Office
          canApproveQueue={caps.canApproveQueue}
          onOpenEmployees={() => navigate('my-employees')}
        />
      )}
      {shown === 'employees' && <Roster query={query} />}
      {shown === 'my-employees' && (
        <MyEmployees
          query={query}
          onOpenUsage={() => navigate('my-keys')}
          onConfigureModel={() => openModelSettings(provider.provider?.model)}
        />
      )}
      {shown === 'business' && <BusinessCenter query={query} />}
      {shown === 'knowledge' && (
        <Knowledge
          query={query}
          onOpenSettings={() => navigate('settings')}
          onOpenUsage={() => navigate('my-keys')}
        />
      )}
      {shown === 'data' && <DataCenter query={query} isRoot={caps.isRoot} />}
      {shown === 'approval' && (
        <div className="os-grid">
          <div className="os-span-12">
            <ApprovalCenter state={aclView} actions={aclActions} />
          </div>
        </div>
      )}
      {shown === 'audit' && <Audit query={query} profile={profile} />}
      {shown === 'files' && <Files query={query} />}
      {shown === 'goals' && <Goals query={query} />}
      {shown === 'projects' && <Projects query={query} />}
      {shown === 'apps' && <Apps query={query} canManage={caps.isRoot} />}
      {shown === 'events' && <Events query={query} />}
      {shown === 'notifications' && <Notifications />}
      {shown === 'trust' && <Trust query={query} />}
      {shown === 'network' && <Network query={query} />}
      {shown === 'identity' && <Identity query={query} profile={profile} />}
      {shown === 'memory' && <Memory profile={profile} caps={caps} readOnly={readOnly} />}
      {shown === 'my-keys' && (
        <MyKeys profile={profile} caps={caps} readOnly={readOnly} session={session} />
      )}
      {shown === 'account' && <AccountSecurity profile={profile} readOnly={readOnly} />}
      {shown === 'my-data' && <MyData profile={profile} readOnly={readOnly} />}
      {shown === 'members' && (
        <Members
          profile={profile}
          caps={caps}
          variant="root"
          readOnly={readOnly}
          onViewAs={setViewAs}
        />
      )}
      {shown === 'my-members' && (
        <Members
          profile={profile}
          caps={caps}
          variant="member"
          readOnly={readOnly}
          onViewAs={setViewAs}
        />
      )}
      {shown === 'matrix' && (
        <PermissionMatrix profile={profile} caps={caps} readOnly={readOnly} />
      )}
      {shown === 'templates' && <RoleTemplates caps={caps} />}
      {shown === 'backup' && <Backup profile={profile} caps={caps} readOnly={readOnly} />}
      {shown === 'recovery' && <Recovery caps={caps} readOnly={readOnly} />}
      {shown === 'status' && <SystemStatus provider={provider} />}
      {shown === 'preflight' && <Preflight />}
      {shown === 'about' && <About />}
      {shown === 'me' && <Me caps={caps} onNavigate={navigate} />}
      {shown === 'settings' && (
        <Settings
          device={device}
          session={session}
          caps={caps}
          onNavigate={(key) => navigate(key)}
          notifications={notifications}
          onLogout={() => void doLogout()}
          focusModelId={focusModel}
        />
      )}
    </Shell>
  )
}

export default App
