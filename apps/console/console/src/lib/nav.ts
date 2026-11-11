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
 *
 * 2026-11 收敛（Boss 拍板）：39 个一级入口收成六组 —— 首页 / 办公室 / 任务 /
 * 知识 / 能力 / 设置。低频治理入口不再和日常动作混排，全部收进「设置」。
 * 收敛只改"入口在哪"，不改权限：每个页面的 `requires` 仍然逐项生效。
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

/** 六组之一。`defaultKey` 是点组名时落到的页，也是该组的主视图。 */
export interface NavGroup {
  key: string
  label: string
  icon: (props: IconProps) => ReactElement
  defaultKey: string
  items: NavItem[]
}

/**
 * 首页的规范 key。
 *
 * 旧地址 `?p=overview`（运行看板）与 `?p=runboard` 都重定向到这里；
 * 运行看板的内容已拆进首页状态带，不再单独占一页。
 */
export const HOME_KEY = ENTERPRISE_OVERVIEW_PAGE.key

/** ① 首页：企业总览 + 贾维斯。日常只有这两件事。 */
const HOME_NAV: NavItem[] = [
  {
    key: ENTERPRISE_OVERVIEW_PAGE.key,
    label: ENTERPRISE_OVERVIEW_PAGE.navLabel,
    icon: IconDashboard,
    hint: ENTERPRISE_OVERVIEW_PAGE.sub,
  },
  {
    key: 'butler',
    label: '贾维斯',
    icon: IconButler,
    hint: '你下目标，它拆解分派，给结论；也可以直接问',
  },
]

/** ② 办公室：看 AI 员工在干什么；组织与员工管理是这里的两种看法。 */
const OFFICE_NAV: NavItem[] = [
  {
    key: 'office',
    label: '办公室',
    icon: IconBusiness,
    hint: 'AI 员工的工位墙：谁在干活、干到哪、卡在哪；点工位直接派活',
  },
  {
    key: 'my-employees',
    label: '我的员工',
    icon: IconEmployees,
    hint: '创建、改名、停用，或直接把一件事派给它',
  },
  {
    key: ORG_PAGE.key,
    label: ORG_PAGE.title,
    icon: IconBusiness,
    hint: ORG_PAGE.sub,
    // 建企业、调部门与汇报关系是根专属动作；成员看不到这一项。
    requires: 'root',
  },
]

/** ③ 任务：台账、目标、项目、产出物，以及工作流与人工审批。 */
const TASKS_NAV: NavItem[] = [
  {
    key: TASK_CENTER_PAGE.key,
    label: TASK_CENTER_PAGE.navLabel,
    icon: IconGoals,
    hint: TASK_CENTER_PAGE.sub,
  },
  {
    key: 'workflow',
    label: '工作流',
    icon: IconProjects,
    hint: '画流程、点运行、看每一步的输入输出与耗时',
  },
  {
    key: 'workflow-approvals',
    label: '待办审批',
    icon: IconShield,
    hint: '工作流卡在人工审批时，在这里通过或拒绝',
  },
  { key: 'goals', label: '目标与任务', icon: IconGoals },
  { key: 'projects', label: '项目', icon: IconProjects },
  { key: 'files', label: '工作区文件', icon: IconFiles },
]

/** ④ 知识：喂进去的、记住的、带得走的。 */
const KNOWLEDGE_NAV: NavItem[] = [
  {
    key: 'knowledge',
    label: '知识中心',
    icon: IconKnowledge,
    hint: '文档上传、切块与检索；含入库进度和失败处理',
  },
  {
    key: 'knowledge-enterprise',
    label: '企业知识库',
    icon: IconKnowledge,
    hint: '分类归档、全文检索、版本对比、过期提醒与长期记忆',
  },
  {
    key: 'memory',
    label: '记忆',
    icon: IconData,
    hint: '按范围与员工查看记忆，可纠正、删除与导出',
  },
  {
    key: 'my-data',
    label: '我的数据',
    icon: IconData,
    hint: '一键导出人可读 ZIP；记忆/知识库可回导',
  },
]

/** ⑤ 能力：它能使什么、能接什么。 */
const CAPABILITIES_NAV: NavItem[] = [
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
    key: 'employees',
    label: '能力注册表',
    icon: IconEmployees,
    hint: '只读：系统预置了哪些能力模块',
  },
  { key: 'business', label: '业务中心', icon: IconBusiness },
  { key: 'apps', label: '应用', icon: IconApps },
]

/**
 * ⑥ 设置：低频治理与账号面。
 *
 * 组内仍按「账号与成员 / 安全与审计 / 系统与诊断」三段排列（`settingSection`），
 * 渲染时插入小标题即可；这里用扁平数组保证 key 唯一、深链白名单只有一份。
 */
const SETTINGS_NAV: NavItem[] = [
  // 账号与成员
  { key: 'settings', label: '系统设置', icon: IconSettings, requires: 'root' },
  { key: 'members', label: '成员与权限管理', icon: IconEmployees, requires: 'root' },
  { key: 'my-members', label: '我的账号', icon: IconEmployees, requires: 'manage' },
  { key: 'matrix', label: '权限矩阵', icon: IconLock, requires: 'root' },
  { key: 'templates', label: '角色模板', icon: IconShield, requires: 'root' },
  { key: 'my-keys', label: '我的 Key', icon: IconLock },
  {
    key: 'account',
    label: '账号安全',
    icon: IconShield,
    hint: '修改本人密码；需要当前密码',
  },
  {
    key: 'recovery',
    label: '恢复码',
    icon: IconTrust,
    requires: 'recovery',
  },
  { key: 'backup', label: '备份与迁移', icon: IconServer, requires: 'root' },
  { key: 'me', label: '我的', icon: IconShield, hint: '账号、数据与隐私入口' },
  // 安全与审计
  { key: 'approval', label: '审批中心', icon: IconShield, requires: 'grant' },
  { key: 'audit', label: '操作记录', icon: IconLock },
  { key: 'trust', label: '信任', icon: IconTrust },
  { key: 'network', label: '网络', icon: IconActivity },
  { key: 'identity', label: '身份与权限', icon: IconShield },
  // 系统与诊断
  { key: 'status', label: '系统状态', icon: IconActivity, requires: 'root' },
  { key: 'events', label: '事件流', icon: IconEvents },
  { key: 'data', label: '数据中心', icon: IconData },
  {
    key: 'notifications',
    label: '通知中心',
    icon: IconBell,
    hint: '只看未读、逐条标为已读；已读状态只记在这台设备上',
  },
  { key: 'preflight', label: '生产预检', icon: IconServer, requires: 'root' },
  {
    key: 'about',
    label: '关于与隐私',
    icon: IconShield,
    hint: '版本、数据目录、许可证与联网说明',
  },
]

/** 设置组内部的三个小标题；`startsAt` 指向该段第一个 key。 */
export const SETTINGS_SECTIONS: { label: string; startsAt: string }[] = [
  { label: '账号与成员', startsAt: 'settings' },
  { label: '安全与审计', startsAt: 'approval' },
  { label: '系统与诊断', startsAt: 'status' },
]

export const NAV_GROUPS: NavGroup[] = [
  { key: 'home', label: '首页', icon: IconDashboard, defaultKey: HOME_KEY, items: HOME_NAV },
  { key: 'office', label: '办公室', icon: IconEmployees, defaultKey: 'office', items: OFFICE_NAV },
  { key: 'tasks', label: '任务', icon: IconGoals, defaultKey: TASK_CENTER_PAGE.key, items: TASKS_NAV },
  { key: 'knowledge', label: '知识', icon: IconKnowledge, defaultKey: 'knowledge', items: KNOWLEDGE_NAV },
  {
    key: 'capabilities',
    label: '能力',
    icon: IconSparkle,
    defaultKey: SKILLS_CENTER_PAGE.key,
    items: CAPABILITIES_NAV,
  },
  { key: 'settings', label: '设置', icon: IconSettings, defaultKey: 'settings', items: SETTINGS_NAV },
]

export const ALL_NAV: NavItem[] = NAV_GROUPS.flatMap((group) => group.items)

/** 某个页面属于哪一组（用于侧栏展开当前组）。 */
export function groupForPage(key: string): NavGroup | undefined {
  return NAV_GROUPS.find((group) => group.items.some((item) => item.key === key))
}

function itemVisible(item: NavItem, caps: AccountCaps): boolean {
  if (!item.requires) return true
  if (item.requires === 'root') return caps.isRoot
  if (item.requires === 'grant') return caps.isRoot || caps.canGrant || caps.canApproveQueue
  if (item.requires === 'recovery') return caps.canRecovery
  // "我的成员"是成员面的入口；根已经有完整的"成员与权限管理"，不需要这一项。
  return !caps.isRoot && caps.canMyMembers
}

/** 按身份裁剪后的六组（未授权项直接不返回，调用方无从"置灰"）。 */
export function navGroupsFor(caps: AccountCaps): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items
      .filter((item) => itemVisible(item, caps))
      .map((item) => {
        if (item.key === 'my-keys' && caps.isRoot) return { ...item, label: '全部 Key' }
        // 非根账号的入口名不能出现"成员"这类层级词；内容仍是它自己创建的账号。
        if (item.key === 'my-members' && !caps.isRoot) return { ...item, label: '我的账号' }
        return item
      }),
  })).filter((group) => group.items.length > 0)
}

/**
 * 兼容旧调用点的三份清单。
 *
 * 六组是渲染用的真相；仍有人按 "业务 / 治理 / 设置" 的口径取列表，
 * 这里直接由组派生，避免再维护第二份数据。
 */
export function navFor(caps: AccountCaps): {
  primary: NavItem[]
  governance: NavItem[]
  footer: NavItem[]
  groups: NavGroup[]
} {
  const groups = navGroupsFor(caps)
  return {
    primary: groups.flatMap((group) => group.items),
    governance: [],
    footer: [],
    groups,
  }
}

/** 当前身份可见的全部页面 key —— 深链白名单。 */
export function visibleNavKeys(caps: AccountCaps): Set<string> {
  return new Set(ALL_NAV.filter((item) => itemVisible(item, caps)).map((item) => item.key))
}

/**
 * 手机端底部导航：七项，首项是「企业总览」，第二项是「贾维斯」。
 *
 * Boss 2026-11 定稿：手机首屏从运行看板换成企业总览（公司现在怎么样），
 * 并要求把贾维斯放进底栏第二项。底栏顺序 —— 企业总览 / 贾维斯 / 办公室 /
 * 任务 / 知识 / 能力 / 设置。这是 Boss 的产品决定，不是为了让测试变绿；
 * 改动契约测试时须在提交说明写明。
 *
 * `office` 是办公室工位墙；`settings` 承载账号与诊断。非根账号看不到
 * 系统设置时，第 7 项回退到「我的」，避免底栏出现死入口。
 */
export const MOBILE_NAV_KEYS = [
  ENTERPRISE_OVERVIEW_PAGE.key,
  'butler',
  'office',
  TASK_CENTER_PAGE.key,
  'knowledge',
  SKILLS_CENTER_PAGE.key,
  'settings',
]

export function mobileNavKeysFor(caps: AccountCaps): string[] {
  const visible = visibleNavKeys(caps)
  const last = visible.has('settings') ? 'settings' : 'me'
  const office = visible.has('office') ? 'office' : 'my-employees'
  const keys = [
    ENTERPRISE_OVERVIEW_PAGE.key,
    'butler',
    office,
    TASK_CENTER_PAGE.key,
    'knowledge',
    SKILLS_CENTER_PAGE.key,
    last,
  ]
  // 权限裁剪后可能少项：按可见性补齐到七项，仍保证首项是企业总览、第二项是贾维斯。
  for (const fallback of ['goals', 'files', 'memory', 'apps', 'me']) {
    if (keys.length >= 7) break
    if (visible.has(fallback) && !keys.includes(fallback)) keys.push(fallback)
  }
  return keys.slice(0, 7)
}
