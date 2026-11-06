/**
 * 导航定义 —— 三个形态共用同一份"页面清单"。
 *
 * 单独成文件的原因有两条，都不是洁癖：
 * 1. Fast Refresh：常量与组件混在一个模块里，改常量会让整个 Shell 重新挂载。
 * 2. **单一真相**：历史上这个项目同时存在 `dashboardData.ts` 的 `NAV_MAIN` 与
 *    Shell 里的导航表两份清单，两边一旦分叉，就会出现"菜单里有、点进去是空白"
 *    或反过来的情况。现在只有这一份。
 *
 * 账号体系（`docs/account-ui-ia.md`）：入口裁剪在这里完成。**不渲染**而不是置灰
 * —— 未授权入口在 DOM 里根本不存在。裁剪只看 `AccountCaps`，不做授权判定。
 */

import type { ReactElement } from 'react'
import type { IconProps } from '../components/icons'
import {
  IconActivity,
  IconBusiness,
  IconButler,
  IconDashboard,
  IconData,
  IconEmployees,
  IconEvents,
  IconFiles,
  IconGoals,
  IconKnowledge,
  IconLock,
  IconProjects,
  IconApps,
  IconSettings,
  IconShield,
  IconTrust,
  IconWorkbench,
  IconServer,
} from '../components/icons'
import type { AccountCaps } from './accountCaps'

export type NavRequirement = 'root' | 'manage' | 'grant'

export interface NavItem {
  key: string
  label: string
  icon: (props: IconProps) => ReactElement
  /** 悬停时出现的一句话定位；导航栏放不下长文案，但不能让人猜。 */
  hint?: string
  /** 侧边栏徽标（真实计数） */
  badge?: string | number
  badgeTone?: 'accent' | 'danger' | 'muted'
  /** 入口条件；缺省 = 所有人可见。 */
  requires?: NavRequirement
}

export const PRIMARY_NAV: NavItem[] = [
  { key: 'overview', label: '总览', icon: IconDashboard },
  {
    key: 'butler',
    label: '鎏灏管家',
    icon: IconButler,
    hint: '你下目标，它拆解分派，给结论',
  },
  {
    key: 'my-employees',
    label: '我的员工',
    icon: IconEmployees,
    hint: '创建、改名、停用，或直接把一件事派给它',
  },
  {
    key: 'employees',
    label: '能力注册表',
    icon: IconEmployees,
    hint: '只读：系统预置了哪些能力模块',
  },
  { key: 'business', label: '业务中心', icon: IconBusiness },
  { key: 'knowledge', label: '知识中心', icon: IconKnowledge },
  { key: 'memory', label: '记忆', icon: IconData },
  {
    key: 'workbench',
    label: '工作台',
    icon: IconWorkbench,
    hint: '你自己看任务、看文件、看产出，也可以直接对话',
  },
  { key: 'goals', label: '目标与任务', icon: IconGoals },
  { key: 'projects', label: '项目', icon: IconProjects },
  { key: 'apps', label: '应用', icon: IconApps },
  { key: 'data', label: '数据中心', icon: IconData },
  { key: 'events', label: '事件流', icon: IconEvents },
]

/** 独立于主导航的治理入口。 */
export const GOVERNANCE_NAV: NavItem[] = [
  // 审批面要能签发凭据才成立；没有签发范围的账号连入口都不出现（不是点了 403）。
  { key: 'approval', label: '审批中心', icon: IconShield, requires: 'grant' },
  // 审计链：后端 `/v1/audit/*` 一直有真实数据，此前没有任何页面渲染它。
  { key: 'audit', label: '审计链', icon: IconLock },
  // 信任内核只读面：后端 `/v1/trust/{entity_id}` 返回真实信任状态，此前没有任何页面渲染它。
  { key: 'trust', label: '信任', icon: IconTrust },
  // 网络内核只读面：后端 `/v1/network/messages` 返回总线真实状态（stats + 消息历史），此前没有任何页面渲染它。
  { key: 'network', label: '网络', icon: IconActivity },
  // 身份与权限只读面：后端 `/v1/identity/*` 返回真实身份内核状态（主体 / 权限 / 注册表诚实度），此前没有任何页面渲染它。
  { key: 'identity', label: '身份与权限', icon: IconShield },
  // 工作区文件：AI 员工经 file_write 写出的产物，此前没有任何页面让人浏览/读取。
  { key: 'files', label: '工作区文件', icon: IconFiles },
  { key: 'status', label: '系统状态', icon: IconActivity, requires: 'root' },
  // 生产预检：`/v1/production/preflight` 此前真实可用但界面上无入口，
  // 一份算好的"能不能上生产"从未递到人眼前。只读，不含任何配置值。
  { key: 'preflight', label: '生产预检', icon: IconServer, requires: 'root' },

  // ── 账号面（§9.8）────────────────────────────────────────────────────
  { key: 'members', label: '成员与权限管理', icon: IconEmployees, requires: 'root' },
  { key: 'matrix', label: '权限矩阵', icon: IconLock, requires: 'root' },
  { key: 'templates', label: '角色模板', icon: IconShield, requires: 'root' },
  { key: 'backup', label: '备份与迁移', icon: IconServer, requires: 'root' },
  { key: 'recovery', label: '恢复码', icon: IconTrust, requires: 'root' },
  // 成员面：只有含管理范围的账号才出现；更深层默认没有该范围。
  { key: 'my-members', label: '我的账号', icon: IconEmployees, requires: 'manage' },
  { key: 'my-keys', label: '我的 Key', icon: IconLock },
]

export const FOOTER_NAV: NavItem[] = [
  { key: 'settings', label: '系统设置', icon: IconSettings, requires: 'root' },
]

export const ALL_NAV: NavItem[] = [...PRIMARY_NAV, ...GOVERNANCE_NAV, ...FOOTER_NAV]

function itemVisible(item: NavItem, caps: AccountCaps): boolean {
  if (!item.requires) return true
  if (item.requires === 'root') return caps.isRoot
  if (item.requires === 'grant') return caps.isRoot || caps.canGrant || caps.canApproveQueue
  // "我的成员"是成员面的入口；根已经有完整的"成员与权限管理"，不需要这一项。
  return !caps.isRoot && caps.canMyMembers
}

/** 按身份裁剪后的导航（未授权项直接不返回，调用方无从"置灰"）。 */
export function navFor(caps: AccountCaps): {
  primary: NavItem[]
  governance: NavItem[]
  footer: NavItem[]
} {
  const governance = GOVERNANCE_NAV.filter((item) => itemVisible(item, caps)).map((item) => {
    if (item.key === 'my-keys' && caps.isRoot) return { ...item, label: '全部 Key' }
    // 非根账号的入口名不能出现"成员"这类层级词；内容仍是它自己创建的账号。
    if (item.key === 'my-members' && !caps.isRoot) return { ...item, label: '我的账号' }
    return item
  })
  return {
    primary: PRIMARY_NAV.filter((item) => itemVisible(item, caps)),
    governance,
    footer: FOOTER_NAV.filter((item) => itemVisible(item, caps)),
  }
}

/** 当前身份可见的全部页面 key —— 深链白名单。 */
export function visibleNavKeys(caps: AccountCaps): Set<string> {
  const { primary, governance, footer } = navFor(caps)
  return new Set([...primary, ...governance, ...footer].map((item) => item.key))
}

/**
 * 手机端底部导航：五项，含"我的"。
 *
 * `workbench`（管家 / 对话）必须在这里 —— 此前手机端四项里没有它，
 * 于是"进入对话"在手机上没有任何一键入口，只能靠深层 URL 或外链。
 * 五项是 390×844 下不换行的上限（每项一列，每项 ≥44px 热区）。
 *
 * 第 5 项按身份切换：设置入口只对根出现；其他人用"我的 Key"作为账户面入口。
 */
export const MOBILE_NAV_KEYS = ['overview', 'my-employees', 'workbench', 'approval', 'settings']

export function mobileNavKeysFor(caps: AccountCaps): string[] {
  const last = caps.canSettings ? 'settings' : 'my-keys'
  // 没有签发范围时底栏不留「审批中心」的死入口，换成同样可读的「工作区文件」。
  const fourth = visibleNavKeys(caps).has('approval') ? 'approval' : 'files'
  return ['overview', 'my-employees', 'workbench', fourth, last]
}
