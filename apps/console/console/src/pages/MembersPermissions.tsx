/**
 * 成员与权限（简约版合并页）。
 *
 * 并进来的 4 个旧入口在这里做标签：成员管理 / 成员与权限管理 /
 * 权限矩阵 / 角色模板。成员管理固定用 Members variant="root"
 *（与旧 owner-members 分支一致）；PermissionMatrix 与 RoleTemplates
 * 各自带原有的无权守卫（caps.canMatrix / caps.canTemplates 不满足时
 * 渲染它们自己的「无权执行该操作」卡片），这里不另写拒绝文案、不另
 * 外做权限判断。入口本身要求根或 Owner（nav.ts 的 'root-or-owner'）。
 */
import { useState } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { Members } from './Members'
import { PermissionMatrix } from './PermissionMatrix'
import { RoleTemplates } from './RoleTemplates'
import './MergedPages.css'

export type MembersPermissionsTab = 'members' | 'matrix' | 'templates'

const TABS: { id: MembersPermissionsTab; label: string }[] = [
  { id: 'members', label: '成员管理' },
  { id: 'matrix', label: '权限矩阵' },
  { id: 'templates', label: '角色模板' },
]

export function MembersPermissions({
  initialTab = 'members',
  profile,
  caps,
  readOnly,
  onViewAs,
}: {
  initialTab?: MembersPermissionsTab
  profile: AccountProfile
  caps: AccountCaps
  readOnly: boolean
  onViewAs: (principal: string) => void
}) {
  const [tab, setTab] = useState<MembersPermissionsTab>(initialTab)
  return (
    <div>
      <div className="mp-tabs-bar">
        <div className="mp-tabs" role="tablist" aria-label="成员与权限视图">
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
      {tab === 'members' && (
        <Members
          profile={profile}
          caps={caps}
          variant="root"
          readOnly={readOnly}
          onViewAs={onViewAs}
        />
      )}
      {tab === 'matrix' && (
        <PermissionMatrix profile={profile} caps={caps} readOnly={readOnly} />
      )}
      {tab === 'templates' && <RoleTemplates caps={caps} />}
    </div>
  )
}
