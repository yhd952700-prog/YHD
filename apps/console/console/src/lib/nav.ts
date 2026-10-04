/**
 * 导航定义 —— 三个形态共用同一份"页面清单"。
 *
 * 单独成文件的原因有两条，都不是洁癖：
 * 1. Fast Refresh：常量与组件混在一个模块里，改常量会让整个 Shell 重新挂载。
 * 2. **单一真相**：历史上这个项目同时存在 `dashboardData.ts` 的 `NAV_MAIN` 与
 *    Shell 里的导航表两份清单，两边一旦分叉，就会出现"菜单里有、点进去是空白"
 *    或反过来的情况。现在只有这一份。
 */

import type { ReactElement } from 'react'
import type { IconProps } from '../components/icons'
import {
  IconActivity,
  IconBusiness,
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
} from '../components/icons'

export interface NavItem {
  key: string
  label: string
  icon: (props: IconProps) => ReactElement
  /** 侧边栏徽标（真实计数） */
  badge?: string | number
  badgeTone?: 'accent' | 'danger' | 'muted'
}

export const PRIMARY_NAV: NavItem[] = [
  { key: 'overview', label: '总览', icon: IconDashboard },
  { key: 'employees', label: '我AI员工', icon: IconEmployees },
  { key: 'business', label: '业务中心', icon: IconBusiness },
  { key: 'knowledge', label: '知识中心', icon: IconKnowledge },
  { key: 'workbench', label: '工作台', icon: IconWorkbench },
  { key: 'goals', label: '目标与任务', icon: IconGoals },
  { key: 'projects', label: '项目', icon: IconProjects },
  { key: 'apps', label: '应用', icon: IconApps },
  { key: 'data', label: '数据中心', icon: IconData },
  { key: 'events', label: '事件流', icon: IconEvents },
]

/** 独立于主导航的治理入口。 */
export const GOVERNANCE_NAV: NavItem[] = [
  { key: 'approval', label: '审批中心', icon: IconShield },
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
  { key: 'status', label: '系统状态', icon: IconActivity },
]

export const FOOTER_NAV: NavItem[] = [{ key: 'settings', label: '系统设置', icon: IconSettings }]

export const ALL_NAV: NavItem[] = [...PRIMARY_NAV, ...GOVERNANCE_NAV, ...FOOTER_NAV]

/** 手机端底部导航：只放最常用的四个（含"我的"）。 */
export const MOBILE_NAV_KEYS = ['overview', 'employees', 'approval', 'settings']
