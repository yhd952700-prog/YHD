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

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
/**
 * 以下页面保持静态 import（不懒加载），原因各不相同，但都不动：
 * - 导出 `*_PAGE` / `*_ROUTE` 常量，PAGE_META 与深链解析在模块层同步要用
 *   （OperatorConsole、EnterpriseOverview、Org、TaskCenter、ApprovalQueue、
 *   OwnerOverview）；SkillCenter、ToolCenter、WorkflowCenter、OwnerTasks、
 *   KnowledgeEnterprise 只用常量，不在 App 里直接渲染，仍按 0.4.3 只引常量；
 * - Preflight 保持静态：App.deepLink.test.tsx 里 ?p=preflight 用同步
 *   getByText 断言「预检结论」，标题 waitFor 等不到懒加载 chunk（页头在
 *   Suspense 外面）；
 * - Knowledge、CapabilityCenter、SystemDiagnostics 在 0.4.4 Phase 2 改成
 *   懒加载：?p=memory 与 ?p=capability-center 两个深链测试已改成等 chunk
 *   加载后再断言（findBy*），?p=status 的 waitFor 等的是告警请求数，请求
 *   只在组件挂载后才发，所以天然等到了 chunk，不用改；
 * - OperatorConsole / EnterpriseOverview 是默认落点与回落页，首屏就要在。
 */
import { OperatorConsole, OPERATOR_CONSOLE_PAGE } from './pages/OperatorConsole'
import { Preflight } from './pages/Preflight'
import { EnterpriseOverview, ENTERPRISE_OVERVIEW_PAGE } from './pages/EnterpriseOverview'
import { Org, ORG_PAGE } from './pages/Org'
import { TaskCenter, TASK_CENTER_PAGE } from './pages/TaskCenter'
import { ApprovalQueue, WORKFLOW_APPROVALS_PAGE } from './pages/ApprovalQueue'
import { OwnerOverview, OWNER_OVERVIEW_PAGE } from './pages/OwnerOverview'
import type { CapabilityTab } from './pages/CapabilityCenter'
import type { SystemDiagnosticsTab } from './pages/SystemDiagnostics'
import { SKILLS_CENTER_PAGE } from './pages/SkillCenter'
import { TOOLS_CENTER_PAGE } from './pages/ToolCenter'
import { OWNER_TASKS_PAGE } from './pages/OwnerTasks'
import { WORKFLOW_CENTER_PAGE } from './pages/WorkflowCenter'
import { KNOWLEDGE_ENTERPRISE_ROUTE } from './pages/KnowledgeEnterprise'

/**
 * 其余页面全部懒加载：首屏只下外壳 + 默认操作台，点开某页时才下那一页的
 * chunk。只改加载方式，不改组件、props、key 与渲染条件；导航、权限、
 * 深链逻辑一律不动。
 * 合并页的标签类型只以 `import type` 引入，编译时擦除，不进首屏 chunk。
 */
const Overview = lazy(() => import('./pages/Overview').then((m) => ({ default: m.Overview })))
const Butler = lazy(() => import('./pages/Butler').then((m) => ({ default: m.Butler })))
const Files = lazy(() => import('./pages/Files').then((m) => ({ default: m.Files })))
const Notifications = lazy(() =>
  import('./pages/Notifications').then((m) => ({ default: m.Notifications })),
)
const Settings = lazy(() => import('./pages/Settings').then((m) => ({ default: m.Settings })))
const About = lazy(() => import('./pages/About').then((m) => ({ default: m.About })))
const Backup = lazy(() => import('./pages/Backup').then((m) => ({ default: m.Backup })))
const MyData = lazy(() => import('./pages/MyData').then((m) => ({ default: m.MyData })))
const MyEmployees = lazy(() =>
  import('./pages/MyEmployees').then((m) => ({ default: m.MyEmployees })),
)
const Office = lazy(() => import('./pages/Office').then((m) => ({ default: m.Office })))
const Knowledge = lazy(() => import('./pages/Knowledge').then((m) => ({ default: m.Knowledge })))
const CapabilityCenter = lazy(() =>
  import('./pages/CapabilityCenter').then((m) => ({ default: m.CapabilityCenter })),
)
const SystemDiagnostics = lazy(() =>
  import('./pages/SystemDiagnostics').then((m) => ({ default: m.SystemDiagnostics })),
)
const MembersPermissions = lazy(() =>
  import('./pages/MembersPermissions').then((m) => ({ default: m.MembersPermissions })),
)
const TasksAudit = lazy(() =>
  import('./pages/TasksAudit').then((m) => ({ default: m.TasksAudit })),
)
const MyAccountSecurity = lazy(() =>
  import('./pages/MyAccountSecurity').then((m) => ({ default: m.MyAccountSecurity })),
)
import type { MembersPermissionsTab } from './pages/MembersPermissions'
import type { TasksAuditTab } from './pages/TasksAudit'
import type { MyAccountSecurityTab } from './pages/MyAccountSecurity'
import { ReadOnlyBanner } from './components/AccountChrome'
import { refreshWorkspaceName, useWorkspaceName } from './lib/workspaceName'
import { buildProfile, deriveCaps } from './lib/accountCaps'
import { countTreeMembers, loadAccounts, loadTree, useAccountData } from './lib/accounts'
import type { AccountNode, AccountsPayload } from './lib/accounts'
import { visibleNavKeys, resolveMergedTarget, HOME_KEY } from './lib/nav'
import { buildNavBadges, pendingApprovalsFrom, runningTasksFromLedger } from './lib/navBadges'
import { completedToday } from './lib/enterpriseOverview'
import { parseTasksPayload } from './lib/taskLedger'

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
  [OPERATOR_CONSOLE_PAGE.key]: {
    title: OPERATOR_CONSOLE_PAGE.title,
    sub: OPERATOR_CONSOLE_PAGE.sub,
  },
  // Owner 专属入口（只对 Owner 渲染；普通账号的导航里根本没有这四项）。
  // 全局总览与全部任务用 F 组 Owner 页自带的标题/副说明（单一来源）；
  // 成员管理与审计复用已有真实组件，无真实用户/任务/事件时各页自带空态。
  [OWNER_OVERVIEW_PAGE.key]: {
    title: OWNER_OVERVIEW_PAGE.title,
    sub: OWNER_OVERVIEW_PAGE.sub,
  },
  'owner-members': { title: '成员管理', sub: '全部成员、状态与 Key 授权' },
  [OWNER_TASKS_PAGE.key]: { title: OWNER_TASKS_PAGE.title, sub: OWNER_TASKS_PAGE.sub },
  'owner-audit': { title: '审计', sub: '全量审计事件与哈希链校验（只读）' },
  // 简约版合并页（5 个）：标题与导航一致，sub 一句话说清并进了什么。
  'capability-center': { title: '能力中心', sub: '技能、工具、能力注册表、业务与应用' },
  'members-permissions': { title: '成员与权限', sub: '成员管理、权限矩阵与角色模板' },
  'tasks-audit': { title: '全部任务与审计', sub: '全部任务、审计、事件流与数据中心' },
  'my-account-security': { title: '我的账号与安全', sub: '我的 Key、账号安全、恢复码与我的账号' },
  'system-diagnostics': { title: '系统状态与诊断', sub: '系统状态、信任、网络与身份' },
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
  [KNOWLEDGE_ENTERPRISE_ROUTE.key]: {
    title: KNOWLEDGE_ENTERPRISE_ROUTE.label,
    sub: KNOWLEDGE_ENTERPRISE_ROUTE.hint,
  },
  workflow: { title: '工作流', sub: '画流程、点运行、看每一步的输入输出与耗时' },
  'workflow-approvals': {
    title: '待办审批',
    sub: '工作流卡在人工审批时，在这里通过或拒绝',
  },
  data: { title: '数据中心', sub: '审计存储的真实内容（已排除系统内部记录）' },
  approval: { title: '审批签发', sub: '动作拦截与人工授权' },
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
 * 旧路由 → 新落点（Boss 2026-11 收敛；2026-11-12 起默认落点是聊天操作台）。
 *
 * `workbench` / `chat` / `freechat` 都是旧的"对话"叫法，现在统一落到聊天
 * 操作台；运行看板拆进首页状态带，`overview` 也不再单列。旧书签、桌面壳
 * 快捷方式、外链一律**重定向**，不做 404、不留白屏。
 */
export const PAGE_ALIASES: Record<string, string> = {
  workbench: OPERATOR_CONSOLE_PAGE.key,
  chat: OPERATOR_CONSOLE_PAGE.key,
  freechat: OPERATOR_CONSOLE_PAGE.key,
  'free-chat': OPERATOR_CONSOLE_PAGE.key,
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
    // 不带任何深链参数时默认进聊天操作台（不是企业总览）；带了不认识的
    // 参数才回落到企业总览（见深链测试：未知页面回落总览，不白屏）。
    if (!candidate) return OPERATOR_CONSOLE_PAGE.key
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
  // 桌面端、网页版、手机端登录后都先看到聊天操作台：中间是与唯一助手的
  // 对话区，底部输入框，工具箱在输入框上方；左边导航保留原来的全部入口。
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

  // 浏览器标签标题只从工作台名字配置读（`lib/workspaceName.ts` 的 store，
  // 出厂默认 Jarvis · 贾维斯；设置页改了名，这里跟着变）；静态 `index.html`
  // 的标题由 G 组随改名统一改，这里不碰那个文件。必须在浏览器模式的后缀
  // effect 之前跑，这样后者读到的 original 已经是新名字，标题变成
  // "Jarvis · 贾维斯 · 浏览器模式"。挂载时向后端取一次当前名字。
  const workspaceName = useWorkspaceName()
  useEffect(() => {
    void refreshWorkspaceName()
  }, [])
  useEffect(() => {
    document.title = workspaceName.lockup
  }, [workspaceName.lockup])

  // 浏览器模式下标题必须能一眼分清「这不是桌面应用」；改名后后缀跟着新名字走。
  useEffect(() => {
    if (!browserMode) return
    document.title = `${workspaceName.lockup} · 浏览器模式`
    return () => {
      document.title = workspaceName.lockup
    }
  }, [browserMode, workspaceName.lockup])

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
          // 与登录一致：is_owner 为真，或 is_root 为真；后端没下发 is_owner
          // 时保留登录时算好的值，不把 Owner 入口抖掉。
          isOwner:
            info.is_owner === true ||
            (typeof info.is_root === 'boolean'
              ? info.is_root
              : (prev.isOwner ?? prev.isRoot)),
        }
        // 服务端复核结果通常与本地一致；返回同一个对象引用，避免 effect 依赖
        // 因对象身份变化而再次触发，把自己变成 30rps 的 /v1/auth/me 轮询。
        const unchanged =
          next.principal === prev.principal &&
          next.displayName === prev.displayName &&
          next.scope === prev.scope &&
          next.expiresAt === prev.expiresAt &&
          next.isRoot === prev.isRoot &&
          next.isOwner === prev.isOwner
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
    navigate(OPERATOR_CONSOLE_PAGE.key)
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

  // ---------------------------------------------------------------------
  // 侧栏徽标 + 今天小结的取数（App 层一次取完，两处共用，不发两遍）
  // ---------------------------------------------------------------------
  // 口径（不许换）：待批数取 /v1/workflow/summary.pending_approvals（精确，
  // 无 200 上限）；在跑数取 /v1/tasks/ledger/status.by_status.running（精确）。
  // 轮询 30 秒一档：与 EnterpriseOverview 的任务轮询一致（WorkflowCenter
  // 对 summary 用 15 秒、TaskCenter 对 ledger 用 60 秒，30 秒取中间）。
  // 今日完成数取 /v1/tasks?status=completed&limit=200（completedToday 的
  // 口径，与 EnterpriseOverview 同一条查询）。
  const workflowSummary = useApi<unknown>(authed ? '/v1/workflow/summary' : null, 30_000)
  const ledgerStatus = useApi<unknown>(authed ? '/v1/tasks/ledger/status' : null, 30_000)
  const completedTasks = useApi<unknown>(
    authed
      ? '/v1/tasks?status=completed&limit=200&sort_by=completed_at&sort_order=desc'
      : null,
    30_000,
  )
  // 失败/无权（403）/形状不对时均为 null —— 徽标与小结都不画假数字。
  const pendingApprovals = pendingApprovalsFrom(workflowSummary.data, workflowSummary.error)
  const runningTasks = runningTasksFromLedger(ledgerStatus.data, ledgerStatus.error)
  const navBadges = useMemo(
    () => buildNavBadges({ pendingApprovals, runningTasks }),
    [pendingApprovals, runningTasks],
  )
  const todaySummary = useMemo(
    () => ({
      completed: completedToday(parseTasksPayload(completedTasks.data), Date.now()),
      running: runningTasks,
      pending: pendingApprovals,
      pendingForbidden: workflowSummary.status === 403,
    }),
    [completedTasks.data, runningTasks, pendingApprovals, workflowSummary.status],
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
        detail: '贾维斯的本地服务没有运行。请打开贾维斯，然后刷新重试。',
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
        detail: '暂时读不到各项依赖的状态，请确认贾维斯仍在运行，然后刷新重试。',
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
        <span>当前为浏览器模式；桌面端请用托盘菜单「打开贾维斯窗口」。</span>
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
      navBadges={navBadges}
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

      {/* 企业总览与操作台自带页头，这里不再重复渲染同一组标题与副说明。 */}
      {shown !== ENTERPRISE_OVERVIEW_PAGE.key &&
        shown !== OPERATOR_CONSOLE_PAGE.key && (
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

      {/* 懒加载页面的加载态：只在切页瞬间出现一句话，不动标题与导航。 */}
      <Suspense
        fallback={
          <div className="os-note" role="status">
            正在打开页面…
          </div>
        }
      >
        {shown === OPERATOR_CONSOLE_PAGE.key && (
        <OperatorConsole
          provider={provider}
          onConfigureModel={() => openModelSettings(provider.provider?.model)}
          onOpenUsage={() => navigate('my-keys')}
          today={todaySummary}
        />
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
      {shown === 'owner-overview' && <OwnerOverview onNavigate={navigate} />}
      {shown === ORG_PAGE.key && <Org query={query} />}
      {/* 任务中心：旧 key workflow / goals / projects 落到对应标签。
          key={shown} 强制按 active 换 tab 时重挂载，initialTab 只做初始值。 */}
      {shown === TASK_CENTER_PAGE.key && <TaskCenter key={shown} query={query} />}
      {shown === WORKFLOW_CENTER_PAGE.key && (
        <TaskCenter key={shown} query={query} initialTab="workflow" />
      )}
      {shown === 'goals' && <TaskCenter key={shown} query={query} initialTab="goals" />}
      {shown === 'projects' && (
        <TaskCenter key={shown} query={query} initialTab="projects" />
      )}
      {shown === WORKFLOW_APPROVALS_PAGE.key && <ApprovalQueue query={query} />}
      {/* 能力中心：旧 key skills-center / tools-center / employees /
          business / apps 落到对应标签，tab 从 MERGE_MAP 查。 */}
      {shown === 'capability-center' && (
        <CapabilityCenter key={shown} query={query} canManageApps={caps.isRoot} />
      )}
      {shown === SKILLS_CENTER_PAGE.key && (
        <CapabilityCenter
          key={shown}
          query={query}
          canManageApps={caps.isRoot}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'skills') as CapabilityTab}
        />
      )}
      {shown === TOOLS_CENTER_PAGE.key && (
        <CapabilityCenter
          key={shown}
          query={query}
          canManageApps={caps.isRoot}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'tools') as CapabilityTab}
        />
      )}
      {shown === 'employees' && (
        <CapabilityCenter
          key={shown}
          query={query}
          canManageApps={caps.isRoot}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'roster') as CapabilityTab}
        />
      )}
      {shown === 'business' && (
        <CapabilityCenter
          key={shown}
          query={query}
          canManageApps={caps.isRoot}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'business') as CapabilityTab}
        />
      )}
      {shown === 'apps' && (
        <CapabilityCenter
          key={shown}
          query={query}
          canManageApps={caps.isRoot}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'apps') as CapabilityTab}
        />
      )}
      {/* 成员与权限：旧 key owner-members / members / matrix / templates。 */}
      {shown === 'members-permissions' && (
        <MembersPermissions
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          onViewAs={setViewAs}
        />
      )}
      {shown === 'owner-members' && (
        <MembersPermissions
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          onViewAs={setViewAs}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'members') as MembersPermissionsTab
          }
        />
      )}
      {shown === 'members' && (
        <MembersPermissions
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          onViewAs={setViewAs}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'members') as MembersPermissionsTab
          }
        />
      )}
      {shown === 'matrix' && (
        <MembersPermissions
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          onViewAs={setViewAs}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'matrix') as MembersPermissionsTab
          }
        />
      )}
      {shown === 'templates' && (
        <MembersPermissions
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          onViewAs={setViewAs}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'templates') as MembersPermissionsTab
          }
        />
      )}
      {/* 全部任务与审计：旧 key owner-tasks / owner-audit / audit /
          events / data。 */}
      {shown === 'tasks-audit' && (
        <TasksAudit key={shown} query={query} profile={profile} isRoot={caps.isRoot} caps={caps} />
      )}
      {shown === 'owner-tasks' && (
        <TasksAudit
          key={shown}
          query={query}
          profile={profile}
          isRoot={caps.isRoot} caps={caps}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'tasks') as TasksAuditTab}
        />
      )}
      {shown === 'owner-audit' && (
        <TasksAudit
          key={shown}
          query={query}
          profile={profile}
          isRoot={caps.isRoot} caps={caps}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'audit') as TasksAuditTab}
        />
      )}
      {shown === 'audit' && (
        <TasksAudit
          key={shown}
          query={query}
          profile={profile}
          isRoot={caps.isRoot} caps={caps}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'audit') as TasksAuditTab}
        />
      )}
      {shown === 'events' && (
        <TasksAudit
          key={shown}
          query={query}
          profile={profile}
          isRoot={caps.isRoot} caps={caps}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'events') as TasksAuditTab}
        />
      )}
      {shown === 'data' && (
        <TasksAudit
          key={shown}
          query={query}
          profile={profile}
          isRoot={caps.isRoot} caps={caps}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'data') as TasksAuditTab}
        />
      )}
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
      {shown === 'my-employees' && (
        <MyEmployees
          query={query}
          onOpenUsage={() => navigate('my-keys')}
          onConfigureModel={() => openModelSettings(provider.provider?.model)}
        />
      )}
      {shown === 'knowledge' && (
        <Knowledge
          key={shown}
          query={query}
          onOpenSettings={() => navigate('settings')}
          onOpenUsage={() => navigate('my-keys')}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
        />
      )}
      {shown === KNOWLEDGE_ENTERPRISE_ROUTE.key && (
        <Knowledge
          key={shown}
          query={query}
          onOpenSettings={() => navigate('settings')}
          onOpenUsage={() => navigate('my-keys')}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          initialTab="enterprise"
        />
      )}
      {shown === 'memory' && (
        <Knowledge
          key={shown}
          query={query}
          onOpenSettings={() => navigate('settings')}
          onOpenUsage={() => navigate('my-keys')}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          initialTab="memory"
        />
      )}
      {shown === 'approval' && (
        <div className="os-grid">
          <div className="os-span-12">
            <ApprovalCenter state={aclView} actions={aclActions} />
          </div>
        </div>
      )}
      {shown === 'files' && <Files query={query} />}
      {shown === 'notifications' && <Notifications />}
      {shown === 'my-data' && <MyData profile={profile} readOnly={readOnly} />}
      {/* 我的账号与安全：旧 key my-keys / account / recovery /
          my-members / me。 */}
      {shown === 'my-account-security' && (
        <MyAccountSecurity
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          session={session}
          onNavigate={navigate}
          onViewAs={setViewAs}
        />
      )}
      {shown === 'my-keys' && (
        <MyAccountSecurity
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          session={session}
          onNavigate={navigate}
          onViewAs={setViewAs}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'keys') as MyAccountSecurityTab}
        />
      )}
      {shown === 'account' && (
        <MyAccountSecurity
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          session={session}
          onNavigate={navigate}
          onViewAs={setViewAs}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'account') as MyAccountSecurityTab
          }
        />
      )}
      {shown === 'recovery' && (
        <MyAccountSecurity
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          session={session}
          onNavigate={navigate}
          onViewAs={setViewAs}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'recovery') as MyAccountSecurityTab
          }
        />
      )}
      {shown === 'my-members' && (
        <MyAccountSecurity
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          session={session}
          onNavigate={navigate}
          onViewAs={setViewAs}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'members') as MyAccountSecurityTab
          }
        />
      )}
      {shown === 'me' && (
        <MyAccountSecurity
          key={shown}
          profile={profile}
          caps={caps}
          readOnly={readOnly}
          session={session}
          onNavigate={navigate}
          onViewAs={setViewAs}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'me') as MyAccountSecurityTab}
        />
      )}
      {/* 系统状态与诊断：旧 key status / trust / network / identity。 */}
      {shown === 'system-diagnostics' && (
        <SystemDiagnostics
          key={shown}
          query={query}
          profile={profile}
          provider={provider}
        />
      )}
      {shown === 'status' && (
        <SystemDiagnostics
          key={shown}
          query={query}
          profile={profile}
          provider={provider}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'status') as SystemDiagnosticsTab}
        />
      )}
      {shown === 'trust' && (
        <SystemDiagnostics
          key={shown}
          query={query}
          profile={profile}
          provider={provider}
          initialTab={(resolveMergedTarget(shown)?.tab ?? 'trust') as SystemDiagnosticsTab}
        />
      )}
      {shown === 'network' && (
        <SystemDiagnostics
          key={shown}
          query={query}
          profile={profile}
          provider={provider}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'network') as SystemDiagnosticsTab
          }
        />
      )}
      {shown === 'identity' && (
        <SystemDiagnostics
          key={shown}
          query={query}
          profile={profile}
          provider={provider}
          initialTab={
            (resolveMergedTarget(shown)?.tab ?? 'identity') as SystemDiagnosticsTab
          }
        />
      )}
      {shown === 'backup' && <Backup profile={profile} caps={caps} readOnly={readOnly} />}
      {shown === 'preflight' && <Preflight />}
      {shown === 'about' && <About />}
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
      </Suspense>
    </Shell>
  )
}

export default App
