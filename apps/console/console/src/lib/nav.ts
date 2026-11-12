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
 *
 * 2026-11 再收小（Joseph 定稿）：默认只显示「日常」8 个入口（`DAILY_NAV_KEYS`），
 * 点「日常 / 全部」切换或「更多」展开全部。切换状态记在本地与 URL 参数里。
 *
 * 2026-11 简约版（Joseph 拍板）：可见导航收成 24 个入口（仍分七组），28 个
 * 旧 key 并进 5 个合并页（另加任务中心、知识中心的两组标签），不再单独占
 * 入口；但旧 key 一个不删 —— `ALL_NAV` 从此是「可路由页面注册表」
 * （52 个 = 24 个可见入口 + 28 个被并掉的旧 key），不再是「可见导航清单」。
 * 可见清单只看 `NAV_GROUPS`（`visibleNavItemKeys`）；深链白名单
 * `visibleNavKeys` 按 `ALL_NAV` 过滤，必须仍含被并掉的旧 key，否则
 * `?p=workflow` 这类旧深链会被 App 踢回总览。旧 key → 合并页的映射见
 * `MERGE_MAP`；合并页本身下一组建，本组只定 key 与映射。
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
import { OPERATOR_CONSOLE_PAGE } from '../pages/OperatorConsole'
import { TASK_CENTER_PAGE } from '../pages/TaskCenter'
import { SKILLS_CENTER_PAGE } from '../pages/SkillCenter'
import { TOOLS_CENTER_PAGE } from '../pages/ToolCenter'

/**
 * 入口条件；缺省 = 所有人可见。
 *
 * `'root-or-owner'`：根或 Owner 任一即可见。成员与权限合并页需要它 ——
 * 合并前 owner-members 要求 owner，members / matrix / templates 要求
 * root，合并页对两者都可见，页内再按身份分流（分流是建页组的事）。
 * 不要用"无 requires + navGroupsFor 里特判"：条件写在数据里，测试才能
 * 直接断言，调用方也不用猜。
 */
export type NavRequirement = 'root' | 'manage' | 'grant' | 'recovery' | 'owner' | 'root-or-owner'

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

/** 七组之一。`defaultKey` 是点组名时落到的页，也是该组的主视图。 */
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

/** ① 首页：聊天操作台 + 企业总览 + 管家。日常只有这三件事，操作台是默认落点。 */
const HOME_NAV: NavItem[] = [
  {
    key: OPERATOR_CONSOLE_PAGE.key,
    label: '操作台',
    icon: IconSparkle,
    hint: '聊天操作台：跟贾维斯直接对话，工具箱在输入框上方',
  },
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

/**
 * Owner 专属入口（简约版：3 项）。
 *
 *   * 全局总览 → Owner 总览页（全部成员/任务/用量/审计的汇总）；
 *   * 成员与权限 → 合并页：旧的成员管理（owner-members）+ 成员与权限管理
 *     （members）+ 权限矩阵（matrix）+ 角色模板（templates），对根与
 *     Owner 都可见，页内按身份分流（建页组的事）；
 *   * 全部任务与审计 → 合并页：旧的全部任务（owner-tasks）+ 审计
 *     （owner-audit）+ 操作记录（audit）+ 事件流（events）+ 数据中心
 *     （data），人人可见，页内按身份分流。
 *
 * 后端权限过滤与 Owner 产生、审计日志由 F 组做；这里只按登录身份
 * 显示/隐藏（`caps.isOwner`，来自后端 `is_owner`，未下发时回落根账号）。
 */
const OWNER_NAV: NavItem[] = [
  {
    key: 'owner-overview',
    label: '全局总览',
    icon: IconDashboard,
    hint: '全部成员、任务与用量的汇总（Owner 专属）',
    requires: 'owner',
  },
  {
    key: 'members-permissions',
    label: '成员与权限',
    icon: IconEmployees,
    hint: '成员、权限矩阵与角色模板（根与 Owner 可见）',
    requires: 'root-or-owner',
  },
  {
    key: 'tasks-audit',
    label: '全部任务与审计',
    icon: IconGoals,
    hint: '全部任务、审计链与事件流；按身份看全量或自己',
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

/** ③ 任务：台账、人工审批与工作区文件。旧的工作流 / 目标与任务 / 项目
 *  并进任务中心做标签（MERGE_MAP），不再单独占入口。 */
const TASKS_NAV: NavItem[] = [
  {
    key: TASK_CENTER_PAGE.key,
    label: TASK_CENTER_PAGE.navLabel,
    icon: IconGoals,
    hint: TASK_CENTER_PAGE.sub,
  },
  {
    key: 'workflow-approvals',
    label: '待办审批',
    icon: IconShield,
    hint: '工作流卡在人工审批时，在这里通过或拒绝',
  },
  { key: 'files', label: '工作区文件', icon: IconFiles },
]

/** ④ 知识：喂进去的、记住的、带得走的。旧的企业知识库 / 记忆并进
 *  知识中心做标签（MERGE_MAP），不再单独占入口。 */
const KNOWLEDGE_NAV: NavItem[] = [
  {
    key: 'knowledge',
    label: '知识中心',
    icon: IconKnowledge,
    hint: '文档上传、切块与检索；含入库进度和失败处理',
  },
  {
    key: 'my-data',
    label: '我的数据',
    icon: IconData,
    hint: '一键导出人可读 ZIP；记忆/知识库可回导',
  },
]

/** ⑤ 能力：一个合并页。旧的技能中心 / 工具中心 / 能力注册表 / 业务中心 /
 *  应用并进来做标签（MERGE_MAP），不再单独占入口。 */
const CAPABILITIES_NAV: NavItem[] = [
  {
    key: 'capability-center',
    label: '能力中心',
    icon: IconSparkle,
    hint: '技能、工具、能力注册表、业务与应用',
  },
]

/**
 * ⑥ 设置：低频治理与账号面（简约版：8 项）。
 *
 * 组内仍按「账号与成员 / 安全与审计 / 系统与诊断」三段排列（`settingSection`），
 * 渲染时插入小标题即可；这里用扁平数组保证 key 唯一、深链白名单只有一份。
 *
 * 合并页（人人可见，页内按身份分流，建页组的事）：
 *   * 我的账号与安全 ← 我的 Key / 账号安全 / 恢复码 / 我的账号 / 我的；
 *   * 系统状态与诊断 ← 系统状态 / 信任 / 网络 / 身份与权限。
 * 「审批签发」只改显示名，key 仍是 approval（App.tsx 的标题是建页组的事）。
 */
const SETTINGS_NAV: NavItem[] = [
  // 账号与成员
  { key: 'settings', label: '系统设置', icon: IconSettings, requires: 'root' },
  {
    key: 'my-account-security',
    label: '我的账号与安全',
    icon: IconShield,
    hint: '我的 Key、账号安全、恢复码与我的账号',
  },
  // 安全与审计
  { key: 'approval', label: '审批签发', icon: IconShield, requires: 'grant' },
  { key: 'backup', label: '备份与迁移', icon: IconServer, requires: 'root' },
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
  // 系统与诊断
  {
    key: 'system-diagnostics',
    label: '系统状态与诊断',
    icon: IconActivity,
    hint: '系统状态、信任、网络与身份',
  },
]

/** 设置组内部的三个小标题；`startsAt` 指向该段第一个 key。 */
export const SETTINGS_SECTIONS: { label: string; startsAt: string }[] = [
  { label: '账号与成员', startsAt: 'settings' },
  { label: '安全与审计', startsAt: 'approval' },
  { label: '系统与诊断', startsAt: 'system-diagnostics' },
]

export const NAV_GROUPS: NavGroup[] = [
  { key: 'home', label: '首页', icon: IconDashboard, defaultKey: OPERATOR_CONSOLE_PAGE.key, items: HOME_NAV },
  { key: 'owner', label: 'Owner', icon: IconShield, defaultKey: 'owner-overview', items: OWNER_NAV },
  { key: 'office', label: '办公室', icon: IconEmployees, defaultKey: 'office', items: OFFICE_NAV },
  { key: 'tasks', label: '任务', icon: IconGoals, defaultKey: TASK_CENTER_PAGE.key, items: TASKS_NAV },
  { key: 'knowledge', label: '知识', icon: IconKnowledge, defaultKey: 'knowledge', items: KNOWLEDGE_NAV },
  {
    key: 'capabilities',
    label: '能力',
    icon: IconSparkle,
    defaultKey: 'capability-center',
    items: CAPABILITIES_NAV,
  },
  { key: 'settings', label: '设置', icon: IconSettings, defaultKey: 'settings', items: SETTINGS_NAV },
]

/**
 * 被并掉的 28 个旧 key（简约版）。
 *
 * 它们不再出现在可见导航里，但页面本身还在、深链还在，所以必须留在
 * 可路由注册表里：`visibleNavKeys`（App.tsx 深链白名单）与
 * `navFor().primary` 都按它们过滤。`requires` 一律沿用合并前的原值，
 * 一个不放宽 —— 旧深链的权限语义与合并前完全一致。
 * 标签/提示也沿用原值：合并页建好之前，旧页面仍按原名渲染。
 */
const MERGED_AWAY_NAV: NavItem[] = [
  // 并进任务中心（标签）
  {
    key: 'workflow',
    label: '工作流',
    icon: IconProjects,
    hint: '画流程、点运行、看每一步的输入输出与耗时',
  },
  { key: 'goals', label: '目标与任务', icon: IconGoals },
  { key: 'projects', label: '项目', icon: IconProjects },
  // 并进知识中心（标签）
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
  // 并进能力中心（标签）
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
  // 并进成员与权限（标签）
  {
    key: 'owner-members',
    label: '成员管理',
    icon: IconEmployees,
    hint: '全部成员、状态与 Key 授权（Owner 专属）',
    requires: 'owner',
  },
  { key: 'members', label: '成员与权限管理', icon: IconEmployees, requires: 'root' },
  { key: 'matrix', label: '权限矩阵', icon: IconLock, requires: 'root' },
  { key: 'templates', label: '角色模板', icon: IconShield, requires: 'root' },
  // 并进全部任务与审计（标签）
  {
    key: 'owner-tasks',
    label: '全部任务',
    icon: IconGoals,
    hint: '本地台账里的全部任务（Owner 专属）',
    requires: 'owner',
  },
  {
    key: 'owner-audit',
    label: '审计',
    icon: IconLock,
    hint: '全量审计事件与哈希链校验（Owner 专属）',
    requires: 'owner',
  },
  { key: 'audit', label: '操作记录', icon: IconLock },
  { key: 'events', label: '事件流', icon: IconEvents },
  { key: 'data', label: '数据中心', icon: IconData },
  // 并进我的账号与安全（标签）
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
  { key: 'my-members', label: '我的账号', icon: IconEmployees, requires: 'manage' },
  { key: 'me', label: '我的', icon: IconShield, hint: '账号、数据与隐私入口' },
  // 并进系统状态与诊断（标签）
  { key: 'status', label: '系统状态', icon: IconActivity, requires: 'root' },
  { key: 'trust', label: '信任', icon: IconTrust },
  { key: 'network', label: '网络', icon: IconActivity },
  { key: 'identity', label: '身份与权限', icon: IconShield },
]

/** 被并掉的旧 key 落到哪个合并页、默认打开哪个标签。 */
export interface MergedTarget {
  /** 合并页 key（一定在 24 个可见入口里）。 */
  page: string
  /** 合并页内的标签 id（建页组按 KnowledgeEnterprise 的 Tab 模式实现）。 */
  tab: string
}

export const MERGE_MAP: Record<string, MergedTarget> = {
  // 任务中心
  workflow: { page: 'task-center', tab: 'workflow' },
  goals: { page: 'task-center', tab: 'goals' },
  projects: { page: 'task-center', tab: 'projects' },
  // 知识中心
  'knowledge-enterprise': { page: 'knowledge', tab: 'enterprise' },
  memory: { page: 'knowledge', tab: 'memory' },
  // 能力中心
  'skills-center': { page: 'capability-center', tab: 'skills' },
  'tools-center': { page: 'capability-center', tab: 'tools' },
  employees: { page: 'capability-center', tab: 'roster' },
  business: { page: 'capability-center', tab: 'business' },
  apps: { page: 'capability-center', tab: 'apps' },
  // 成员与权限
  'owner-members': { page: 'members-permissions', tab: 'members' },
  members: { page: 'members-permissions', tab: 'members' },
  matrix: { page: 'members-permissions', tab: 'matrix' },
  templates: { page: 'members-permissions', tab: 'templates' },
  // 全部任务与审计
  'owner-tasks': { page: 'tasks-audit', tab: 'tasks' },
  'owner-audit': { page: 'tasks-audit', tab: 'audit' },
  audit: { page: 'tasks-audit', tab: 'audit' },
  events: { page: 'tasks-audit', tab: 'events' },
  data: { page: 'tasks-audit', tab: 'data' },
  // 我的账号与安全
  'my-keys': { page: 'my-account-security', tab: 'keys' },
  account: { page: 'my-account-security', tab: 'account' },
  recovery: { page: 'my-account-security', tab: 'recovery' },
  'my-members': { page: 'my-account-security', tab: 'members' },
  me: { page: 'my-account-security', tab: 'me' },
  // 系统状态与诊断
  status: { page: 'system-diagnostics', tab: 'status' },
  trust: { page: 'system-diagnostics', tab: 'trust' },
  network: { page: 'system-diagnostics', tab: 'network' },
  identity: { page: 'system-diagnostics', tab: 'identity' },
}

/** 旧 key 被并进哪个合并页；可见入口返回 undefined（它自己就是落点）。 */
export function resolveMergedTarget(key: string): MergedTarget | undefined {
  return MERGE_MAP[key]
}

/** 该 key 是否是被并掉的旧 key（只剩深链/注册表意义，不再占侧栏入口）。 */
export function isMergedAwayKey(key: string): boolean {
  return key in MERGE_MAP
}

/**
 * 可路由页面注册表（52 个 = 24 个可见入口 + 28 个被并掉的旧 key）。
 *
 * 名字沿用 `ALL_NAV`，减少调用方改动，但语义变了：它不再是「可见导航
 * 清单」，而是「这个账号能打开的页面清单」。深链白名单（`visibleNavKeys`）、
 * 手机底栏查表（Shell 的 `ALL_NAV.find`）、`navFor().primary` 都以它为准。
 * 想数"侧栏有几项"，看 `NAV_GROUPS` / `visibleNavItemKeys`，别数这个。
 */
export const ALL_NAV: NavItem[] = [
  ...NAV_GROUPS.flatMap((group) => group.items),
  ...MERGED_AWAY_NAV,
]

/**
 * 导航范围：日常 / 全部（2026-11 收小，Joseph 定稿）。
 *
 * 默认只显示「日常」8 个入口 —— 主线是聊天→任务→审批→结果：
 * 说一句话（操作台）、看全局（总览）、下目标（贾维斯）、看任务
 * （任务中心）、点审批（待办审批）；files 是任务干完的产出物，
 * knowledge 是聊天和任务要用的资料，settings 是配模型和改名字的地方。
 * 其余 15 个可见入口不是删了，点「全部」/「更多」就展开，全在 NAV_GROUPS 里；
 * 被并掉的 28 个旧 key 也没删，仍在 ALL_NAV 注册表里，深链照常用。
 * 收小只改默认显示，不改权限：`settings` 仍 requires root，非根账号的
 * 日常只有 7 个（没有系统设置），其余权限规则一处不动。
 */
export type NavScope = 'daily' | 'all'

export const DAILY_NAV_KEYS: string[] = [
  OPERATOR_CONSOLE_PAGE.key,
  ENTERPRISE_OVERVIEW_PAGE.key,
  'butler',
  TASK_CENTER_PAGE.key,
  'workflow-approvals',
  'files',
  'knowledge',
  'settings',
]

/** 记住「日常 / 全部」的本地 key；URL 参数 `?nav=daily|all` 与它同名。 */
export const NAV_SCOPE_STORAGE_KEY = 'jarvis.nav_scope'
export const NAV_SCOPE_PARAM = 'nav'

/**
 * 纯函数：URL 参数优先于本地记住的值，两者都没有或写错了都回落到「日常」。
 * 单独抽出来是为了能不依赖 window 直接测。
 */
export function resolveNavScope(
  searchValue: string | null,
  storedValue: string | null,
): NavScope {
  if (searchValue === 'all' || searchValue === 'daily') return searchValue
  if (storedValue === 'all' || storedValue === 'daily') return storedValue
  return 'daily'
}

/** 读当前范围：先看 URL 参数，再看本地记住的值，都没有就是「日常」。 */
export function readNavScope(): NavScope {
  try {
    if (typeof window === 'undefined') return 'daily'
    const search = new URLSearchParams(window.location.search).get(NAV_SCOPE_PARAM)
    const stored = window.localStorage.getItem(NAV_SCOPE_STORAGE_KEY)
    return resolveNavScope(search, stored)
  } catch {
    return 'daily'
  }
}

/**
 * 记住当前范围：写本地 + 写进 URL 参数（刷新、分享链接都不丢）。
 * 隐私模式等写失败时只影响"记住"，不影响当次切换，所以吞掉异常。
 */
export function persistNavScope(scope: NavScope): void {
  try {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(NAV_SCOPE_STORAGE_KEY, scope)
    const url = new URL(window.location.href)
    url.searchParams.set(NAV_SCOPE_PARAM, scope)
    window.history.replaceState(null, '', url)
  } catch {
    // 记住失败不阻断切换。
  }
}

/** 当前身份可见的日常入口 key（保持 DAILY_NAV_KEYS 的顺序）。 */
export function dailyNavKeysFor(caps: AccountCaps): string[] {
  const visible = visibleNavKeys(caps)
  return DAILY_NAV_KEYS.filter((key) => visible.has(key))
}

/**
 * 按范围裁剪后的分组。`'all'` 与 `navGroupsFor` 完全一致（全部 24 个可见入口）；
 * `'daily'` 只留日常 8 个（仍按账号权限过滤，非根账号没有系统设置）。
 * 分组结构不变，只是空组被去掉，所以设置组的小标题逻辑照常工作。
 *
 * 例外：`activeKey` 指定的当前页即使不在日常里也会一并保留 —— 深链、
 * 搜索跳进来的页面在侧栏里要找得到自己；但必须是该账号本来就看得见
 * 的页（无权看的页不会因此被"偷渡"进来），切换状态本身也不因此改变。
 */
export function navGroupsForScope(
  caps: AccountCaps,
  scope: NavScope,
  activeKey?: string,
): NavGroup[] {
  const groups = navGroupsFor(caps)
  if (scope === 'all') return groups
  const daily = new Set(dailyNavKeysFor(caps))
  if (activeKey && visibleNavKeys(caps).has(activeKey)) daily.add(activeKey)
  return groups
    .map((group) => ({ ...group, items: group.items.filter((item) => daily.has(item.key)) }))
    .filter((group) => group.items.length > 0)
}

/**
 * 某个页面属于哪一组（用于侧栏展开当前组）。
 *
 * 被并掉的旧 key 返回它合并页所在的组（例如 `workflow` → 任务组，因为
 * 它现在是任务中心的标签），这样深链跳进旧页面时侧栏仍能展开到正确位置。
 */
export function groupForPage(key: string): NavGroup | undefined {
  const merged = MERGE_MAP[key]
  if (merged) {
    return NAV_GROUPS.find((group) => group.items.some((item) => item.key === merged.page))
  }
  return NAV_GROUPS.find((group) => group.items.some((item) => item.key === key))
}

function itemVisible(item: NavItem, caps: AccountCaps): boolean {
  if (!item.requires) return true
  if (item.requires === 'root') return caps.isRoot
  if (item.requires === 'owner') return caps.isOwner
  if (item.requires === 'root-or-owner') return caps.isRoot || caps.isOwner
  if (item.requires === 'grant') return caps.isRoot || caps.canGrant || caps.canApproveQueue
  if (item.requires === 'recovery') return caps.canRecovery
  // "我的成员"是成员面的入口；根已经有完整的"成员与权限管理"，不需要这一项。
  return !caps.isRoot && caps.canMyMembers
}

/**
 * 按身份改显示名：根的"我的 Key"其实是全部 Key；非根账号的入口名不能
 * 出现"成员"这类层级词；内容仍是它自己创建的账号。只改文案，不改 key。
 */
function displayItem(item: NavItem, caps: AccountCaps): NavItem {
  if (item.key === 'my-keys' && caps.isRoot) return { ...item, label: '全部 Key' }
  if (item.key === 'my-members' && !caps.isRoot) return { ...item, label: '我的账号' }
  return item
}

/** 按身份裁剪后的七组可见导航（未授权项直接不返回，调用方无从"置灰"）。 */
export function navGroupsFor(caps: AccountCaps): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => itemVisible(item, caps)).map((item) => displayItem(item, caps)),
  })).filter((group) => group.items.length > 0)
}

/** 当前身份可见的全部可路由页面（含被并掉的旧 key，`displayItem` 改名照常生效）。 */
function visibleRegistryItems(caps: AccountCaps): NavItem[] {
  return ALL_NAV.filter((item) => itemVisible(item, caps)).map((item) => displayItem(item, caps))
}

/**
 * 兼容旧调用点的三份清单。
 *
 * 七组是渲染用的真相；仍有人按 "业务 / 治理 / 设置" 的口径取列表，
 * 这里直接由组派生，避免再维护第二份数据。
 *
 * `primary` 的语义（调用方定的，别按字面猜）：当前身份**可路由的全部
 * 页面**，即 `ALL_NAV` 按权限过滤后的清单 —— 不是可见侧栏。
 * Settings.tsx 只用 `groups`；accountCaps.test.ts 用 `primary` 查
 * `my-keys` 的根改名（"全部 Key"），而 `my-keys` 是被并掉的旧 key，
 * 不在可见侧栏里，只在注册表里。所以 `primary` 必须按注册表出，
 * 按可见清单出会把调用方要查的旧 key 漏掉。
 */
export function navFor(caps: AccountCaps): {
  primary: NavItem[]
  governance: NavItem[]
  footer: NavItem[]
  groups: NavGroup[]
} {
  return {
    primary: visibleRegistryItems(caps),
    governance: [],
    footer: [],
    groups: navGroupsFor(caps),
  }
}

/**
 * 当前身份可见的全部可路由页面 key —— 深链白名单（App.tsx 用它）。
 *
 * 按 `ALL_NAV`（52 个）过滤：被并掉的 28 个旧 key 必须仍在里面，否则
 * `?p=workflow` / `?p=memory` 这类旧深链会被 App 踢回总览。
 * 5 个新合并页 key 也在里面（它们在 ALL_NAV 里），点侧栏新入口不会
 * 被当成"不可见"弹回首页；但 App.tsx 的 PAGE_META 与渲染分支还没
 * 有这 5 个 key（建页组补），深链 `?p=capability-center` 在建页组
 * 补完之前仍会在入口处回落到操作台 —— 不是这里的问题。
 */
export function visibleNavKeys(caps: AccountCaps): Set<string> {
  return new Set(ALL_NAV.filter((item) => itemVisible(item, caps)).map((item) => item.key))
}

/**
 * 当前身份可见的侧栏入口 key（24 个可见清单过滤后）。
 *
 * 与 `visibleNavKeys` 的区别：这个只数侧栏上真实出现的入口（供侧栏
 * 计数与"全部"切换用），那个是深链白名单（含被并掉的旧 key）。
 * 两个别混用：拿白名单数侧栏会多算 28 个，拿侧栏清单做白名单会断旧深链。
 */
export function visibleNavItemKeys(caps: AccountCaps): Set<string> {
  return new Set(
    NAV_GROUPS.flatMap((group) => group.items)
      .filter((item) => itemVisible(item, caps))
      .map((item) => item.key),
  )
}

/**
 * 手机端底部导航：七项，首项是「企业总览」，第二项是「贾维斯」。
 *
 * Boss 2026-11 定稿：手机首屏从运行看板换成企业总览（公司现在怎么样），
 * 并要求把贾维斯放进底栏第二项。底栏顺序 —— 企业总览 / 贾维斯 / 办公室 /
 * 任务 / 知识 / 能力 / 设置。这是 Boss 的产品决定，不是为了让测试变绿；
 * 改动契约测试时须在提交说明写明。
 *
 * 简约版：第 6 项从技能中心换成能力中心（合并页）；第 7 项仍是系统设置，
 * 非根账号看不到系统设置时回退到「我的账号与安全」（合并页），不再是
 * 被并掉的「我的」。底栏的 key 必须在 ALL_NAV 注册表里，否则 Shell
 * 的 `ALL_NAV.find` 查不到、那一项直接不渲染（见 nav.test.ts 的断言）。
 *
 * `office` 是办公室工位墙；`settings` 承载账号与诊断。非根账号看不到
 * 系统设置时，第 7 项回退到「我的账号与安全」，避免底栏出现死入口。
 */
export const MOBILE_NAV_KEYS = [
  ENTERPRISE_OVERVIEW_PAGE.key,
  'butler',
  'office',
  TASK_CENTER_PAGE.key,
  'knowledge',
  'capability-center',
  'settings',
]

export function mobileNavKeysFor(caps: AccountCaps): string[] {
  const visible = visibleNavKeys(caps)
  const last = visible.has('settings') ? 'settings' : 'my-account-security'
  const office = visible.has('office') ? 'office' : 'my-employees'
  const keys = [
    ENTERPRISE_OVERVIEW_PAGE.key,
    'butler',
    office,
    TASK_CENTER_PAGE.key,
    'knowledge',
    'capability-center',
    last,
  ]
  // 权限裁剪后可能少项：按可见性补齐到七项，仍保证首项是企业总览、第二项是贾维斯。
  // 补位只用可见入口 key（被并掉的旧 key 不能进底栏，Shell 按 ALL_NAV 查表
  // 虽然能查到，但点进去是旧页面、与"底栏只放可见入口"的约定不符）。
  for (const fallback of ['files', 'my-data', 'notifications', 'about']) {
    if (keys.length >= 7) break
    if (visible.has(fallback) && !keys.includes(fallback)) keys.push(fallback)
  }
  return keys.slice(0, 7)
}
