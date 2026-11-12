/**
 * 能力中心（简约版合并页）。
 *
 * 并进来的 5 个旧入口在这里做标签，不再单独占侧栏：
 * 技能中心 / 工具中心 / 能力注册表 / 业务中心 / 应用。
 * 每个标签直接复用现有页面组件，不重写它们的逻辑；query 只透传给
 * 接 query prop 的组件（下面 5 个全接）。
 */
import { useState } from 'react'
import { SkillCenter } from './SkillCenter'
import { ToolCenter } from './ToolCenter'
import { BusinessCenter, Roster } from './Directory'
import { Apps } from './Apps'
import './MergedPages.css'

export type CapabilityTab = 'skills' | 'tools' | 'roster' | 'business' | 'apps'

const TABS: { id: CapabilityTab; label: string }[] = [
  { id: 'skills', label: '技能中心' },
  { id: 'tools', label: '工具中心' },
  { id: 'roster', label: '能力注册表' },
  { id: 'business', label: '业务中心' },
  { id: 'apps', label: '应用' },
]

export function CapabilityCenter({
  initialTab = 'skills',
  query = '',
  canManageApps = false,
}: {
  initialTab?: CapabilityTab
  query?: string
  /** 应用页的 canManage：App.tsx 传入 caps.isRoot，与旧分支一致。 */
  canManageApps?: boolean
}) {
  const [tab, setTab] = useState<CapabilityTab>(initialTab)
  return (
    <div>
      <div className="mp-tabs-bar">
        <div className="mp-tabs" role="tablist" aria-label="能力中心视图">
          {TABS.map((item) => (
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
      {tab === 'skills' && <SkillCenter query={query} />}
      {tab === 'tools' && <ToolCenter query={query} />}
      {tab === 'roster' && <Roster query={query} />}
      {tab === 'business' && <BusinessCenter query={query} />}
      {tab === 'apps' && <Apps query={query} canManage={canManageApps} />}
    </div>
  )
}
