/**
 * 全部任务与审计（简约版合并页）。
 *
 * 并进来的 5 个旧入口在这里做标签：全部任务 / 审计 / 事件流 / 数据中心。
 * 页内身份分流全部复用现有组件的行为，不另写过滤逻辑：
 *   * 审计（Audit）：根看全量，非根只看自己的（profile.isRoot 决定 scope）；
 *   * 数据中心（DataCenter）：isRoot 决定是否显示维护数据块；
 *   * 全部任务（OwnerTasks）：只调 Owner 专属端点 `/v1/owner/tasks`，所以
 *     「全部任务」标签仅 Owner/根可见（照抄 MyAccountSecurity 里
 *     Recovery/Members 标签按 caps 隐藏的模式）—— 成员点开只会看到后端
 *     真实返回的拒绝，不如不渲染这个标签；
 *   * 事件流（Events）：本身不分流，按原样展示系统事件流。
 * initialTab 指向被隐藏的标签时，回落到第一个可见标签。
 * OwnerTasks 不接 query prop，所以不传。
 */
import { useState } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { OwnerTasks } from './OwnerTasks'
import { Audit } from './Audit'
import { Events } from './Events'
import { DataCenter } from './Operations'
import './MergedPages.css'

export type TasksAuditTab = 'tasks' | 'audit' | 'events' | 'data'

const ALL_TABS: { id: TasksAuditTab; label: string }[] = [
  { id: 'tasks', label: '全部任务' },
  { id: 'audit', label: '审计' },
  { id: 'events', label: '事件流' },
  { id: 'data', label: '数据中心' },
]

export function TasksAudit({
  initialTab = 'tasks',
  query = '',
  profile,
  isRoot,
  caps,
}: {
  initialTab?: TasksAuditTab
  query?: string
  profile: AccountProfile
  isRoot: boolean
  /** 决定「全部任务」标签是否渲染：仅 Owner/根可见。 */
  caps: AccountCaps
}) {
  // 无权看的标签不渲染：「全部任务」只调 Owner 专属端点，成员看到它只会
  // 得到后端真实拒绝，所以直接不渲染（同 MyAccountSecurity 的做法）。
  const tabs = ALL_TABS.filter(
    (item) => item.id !== 'tasks' || caps.isOwner || isRoot,
  )
  const [tab, setTab] = useState<TasksAuditTab>(() =>
    tabs.some((item) => item.id === initialTab) ? initialTab : tabs[0].id,
  )
  return (
    <div>
      <div className="mp-tabs-bar">
        <div className="mp-tabs" role="tablist" aria-label="全部任务与审计视图">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              className={`mp-tab${tab === item.id ? ' mp-tab-active' : ''}`}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
      {tab === 'tasks' && <OwnerTasks />}
      {tab === 'audit' && <Audit query={query} profile={profile} />}
      {tab === 'events' && <Events query={query} />}
      {tab === 'data' && <DataCenter query={query} isRoot={isRoot} />}
    </div>
  )
}
