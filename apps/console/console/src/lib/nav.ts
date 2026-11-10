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
  IconBell,
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
  IconSparkle,
  IconTrust,
  IconWorkbench,
  IconServer,
} from '../components/icons'
import type { AccountCaps } from './accountCaps'
// 页面登记信息由页面自己导出，导航只引用，避免标题/副说明两处各写一份。
import { ENTERPRISE_OVERVIEW_PAGE } from '../pages/EnterpriseOverview'
import { ORG_PAGE } from '../pages/Org'
import { TASK_CENTER_PAGE } from '../pages/TaskCenter'
import { SKILLS_CENTER_PAGE } from '../pages/SkillCenter'
import { TOOLS_CENTER_PAGE } from '../pages/ToolCenter'

export type NavRequirement = 'root' | 'manage' | 'grant' | 'recovery'

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
  {
    key: ENTERPRISE_OVERVIEW_PAGE.key,
    label: ENTERPRISE_OVERVIEW_PAGE.navLabel,
    icon: IconDashboard,
    hint: ENTERPRISE_OVERVIEW_PAGE.sub,
  },
  {
    key: 'overview',
    label: '运行看板',
    icon: IconDashboard,
    hint: '系统健康、告警与实时活动；企业规模与任务进度看企业总览',
  },
  {
    key: ORG_PAGE.key,
    label: ORG_PAGE.title,
    icon: IconBusiness,
    hint: ORG_PAGE.sub,
    // 建企业、调部门与汇报关系是根专属动作；成员看不到这一项。
    requires: 'root',
  },
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
    key: TASK_CENTER_PAGE.key,
    label: TASK_CENTER_PAGE.navLabel,
    icon: IconGoals,
    hint: TASK_CENTER_PAGE.sub,
  },
  {
    key: 'employees',
    label: '能力注册表',
    icon: IconEmployees,
    hint: '只读：系统预置了哪些能力模块',
  },
  { key: 'business', label: '业务中心', icon: IconBusiness },
  {
    key: 'knowledge',
    label: '知识中心',
    icon: IconKnowledge,
    hint: '文档上传、切块与检索；含入库进度和失败处理',
  },
  {
    key: 'memory',
    label: '记忆',
    icon: IconData,
    hint: '按范围与员工查看记忆，可纠正、删除与导出',
  },
  {
    key: SKILLS_CENTER_PAGE.key,
    label: SKILLS_CENTER_PAGE.navLabel,
    icon: IconSparkle,
    hint: SKILLS_CENTER_PAGE.sub,
  },
  {
    key: TOOLS_CENTER_PAGE.key,
    label: TOOLS_CENTER_PAGE.navLabel,
    icon: IconServer,
    hint: TOOLS_CENTER_PAGE.sub,
  },
  {
    key: 'workbench',
    label: '自由对话',
    icon: IconWorkbench,
    hint: '你自己问、自己翻，不经过管家拆解',
  },
  { key: 'goals', label: '目标与任务', icon: IconGoals },
  { key: 'projects', label: '项目', icon: IconProjects },
  { key: 'apps', label: '应用', icon: IconApps },
  { key: 'data', label: '数据中心', icon: IconData },
  { key: 'events', label: '事件流', icon: IconEvents },
  {
    key: 'notifications',
    label: '通知中心',
    icon: IconBell,
    hint: '只看未读、逐条标为已读；已读状态只记在这台设备上',
  },
]

/** 独立于主导航的治理入口。 */
export const GOVERNANCE_NAV: NavItem[] = [
  // 审批面要能签发凭据才成立；没有签发范围的账号连入口都不出现（不是点了 403）。
  { key: 'approval', label: '审批中心', icon: IconShield, requires: 'grant' },
  // 操作记录：后端 `/v1/audit/*` 一直有真实数据，此前没有任何页面渲染它。
  { key: 'audit', label: '操作记录', icon: IconLock },
  // 信任内核只读面：后端 `/v1/trust/{entity_id}` 返回真实信任状态，此前没有任何页面渲染它。
  { key: 'trust', label: '信任', icon: IconTrust },
  // 网络内核只读面：后端 `/v1/network/messages` 返回总线真实状态（stats + 消息历史），此前没有任何页面渲染它。
  { key: 'network', label: '网络', icon: IconActivity },
  // 身份与权限只读面：后端 `/v1/identity/*` 返回真实身份内核状态（主体 / 权限 / 注册表诚实度），此前没有任何页面渲染它。
  { key: 'identity', label: '身份与权限', icon: IconShield },
  // 工作区文件：AI 员工经 file_write 写出的产物，此前没有任何页面让人浏览/读取。
  { key: 'files', label: '工作区文件', icon: IconFiles },
  // 数据可携带性：每个人都能把自己的会话/记忆/员工/知识库/用量带走，
  // 也能把记忆与知识库导回来。不属于根专属的加密备份，故不设 requires。
  { key: 'my-data', label: '我的数据', icon: IconData, hint: '一键导出人可读 ZIP；记忆/知识库可回导' },
  { key: 'status', label: '系统状态', icon: IconActivity, requires: 'root' },
  // 生产预检：`/v1/production/preflight` 此前真实可用但界面上无入口，
  // 一份算好的"能不能上生产"从未递到人眼前。只读，不含任何配置值。
  { key: 'preflight', label: '生产预检', icon: IconServer, requires: 'root' },

  // ── 账号面（§9.8）────────────────────────────────────────────────────
  { key: 'members', label: '成员与权限管理', icon: IconEmployees, requires: 'root' },
  { key: 'matrix', label: '权限矩阵', icon: IconLock, requires: 'root' },
  { key: 'templates', label: '角色模板', icon: IconShield, requires: 'root' },
  { key: 'backup', label: '备份与迁移', icon: IconServer, requires: 'root' },
  // 根账号 + 登录页自助注册的账号：后者没有管理员可代发激活码，必须有
  // 自己补发恢复码的入口；管理员创建的成员不出现（由管理员重置密码）。
  { key: 'recovery', label: '恢复码', icon: IconTrust, requires: 'recovery' },
  // 成员面：只有含管理范围的账号才出现；更深层默认没有该范围。
  { key: 'my-members', label: '我的账号', icon: IconEmployees, requires: 'manage' },
  { key: 'my-keys', label: '我的 Key', icon: IconLock },
  // 账号安全：所有人可见。此前后端 `/v1/accounts/{principal}/password` 只被
  // 管理员重置成员调用，普通用户没有"改自己密码"的入口。
  { key: 'account', label: '账号安全', icon: IconShield, hint: '修改本人密码；需要当前密码' },
]

export const FOOTER_NAV: NavItem[] = [
  // 「我的」：手机端账号、数据与隐私入口的收敛页。桌面端侧栏也有同一入口，
  // 这样三端对"账号面在哪里"只有一个答案，不再靠深链或猜路径。
  { key: 'me', label: '我的', icon: IconShield, hint: '账号、数据与隐私入口' },
  // 关于与隐私：所有人可见。首次披露不能只藏在安装前的 txt 里。
  {
    key: 'about',
    label: '关于与隐私',
    icon: IconShield,
    hint: '版本、数据目录、许可证与联网说明',
  },
  { key: 'settings', label: '系统设置', icon: IconSettings, requires: 'root' },
]

export const ALL_NAV: NavItem[] = [...PRIMARY_NAV, ...GOVERNANCE_NAV, ...FOOTER_NAV]

function itemVisible(item: NavItem, caps: AccountCaps): boolean {
  if (!item.requires) return true
  if (item.requires === 'root') return caps.isRoot
  if (item.requires === 'grant') return caps.isRoot || caps.canGrant || caps.canApproveQueue
  if (item.requires === 'recovery') return caps.canRecovery
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
 * 手机端底部导航：六项，含"目标与任务"与"我的"。
 *
 * `butler`（管家 / 对话）必须在这里 —— 此前手机端四项里没有它，
 * 于是"进入对话"在手机上没有任何一键入口，只能靠深层 URL 或外链。
 *
 * `goals`（目标与任务）也必须在这里：窄屏下派活的结果只落在目标列表里，
 * 没有入口时用户能看到"已派活"的回执，却永远打不开结果页。
 *
 * 六项在 360px 宽下仍是一行（os.css 在 ≤420px 收紧字号与内边距），
 * 不换行、不横向溢出；每项仍是原生 <button>，可 Tab 聚焦。
 *
 * 第 6 项固定为「我的」：账号安全、我的数据、恢复码、关于与隐私都在这一页分组，
 * 不再按身份切换成设置或我的 Key —— 两种身份在手机上都要能走到账号面。
 */
export const MOBILE_NAV_KEYS = [
  'overview',
  'my-employees',
  'butler',
  'goals',
  'approval',
  'me',
]

export function mobileNavKeysFor(caps: AccountCaps): string[] {
  // 没有签发范围时底栏不留「审批中心」的死入口，换成同样可读的「工作区文件」。
  const fourth = visibleNavKeys(caps).has('approval') ? 'approval' : 'files'
  return ['overview', 'my-employees', 'butler', 'goals', fourth, 'me']
}
