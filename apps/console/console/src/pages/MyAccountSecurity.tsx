/**
 * 我的账号与安全（简约版合并页）。
 *
 * 并进来的 5 个旧入口在这里做标签：我的 Key / 账号安全 / 恢复码 /
 * 我的账号 / 我的。原有守卫条件一个不动：
 *   * 恢复码标签只在 caps.canRecovery 时渲染（Recovery 自己也有同样
 *     的守卫，这里不渲染标签、而不是渲染一张"无权"页）；
 *   * 我的账号标签只在 caps.canMyMembers 时渲染（Members variant="member"
 *     自己也有同样的守卫，这里同样不渲染标签）；
 *   * 我的 Key、账号安全、我的三个标签人人可见（对应组件本身无入口守卫）。
 * initialTab 指向被隐藏的标签时，回落到第一个可见标签。
 * MyKeys / AccountSecurity / Recovery / Members / Me 都不接 query prop，
 * 所以 query 不往里传。
 */
import { useState } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import type { Session } from '../lib/auth'
import { MyKeys } from './MyKeys'
import { AccountSecurity } from './AccountSecurity'
import { Recovery } from './Recovery'
import { Members } from './Members'
import { Me } from './Me'
import './MergedPages.css'

export type MyAccountSecurityTab = 'keys' | 'account' | 'recovery' | 'members' | 'me'

const ALL_TABS: { id: MyAccountSecurityTab; label: string }[] = [
  { id: 'keys', label: '我的 Key' },
  { id: 'account', label: '账号安全' },
  { id: 'recovery', label: '恢复码' },
  { id: 'members', label: '我的账号' },
  { id: 'me', label: '我的' },
]

export function MyAccountSecurity({
  initialTab = 'keys',
  profile,
  caps,
  readOnly,
  session,
  onNavigate,
  onViewAs,
}: {
  initialTab?: MyAccountSecurityTab
  profile: AccountProfile
  caps: AccountCaps
  readOnly: boolean
  session: Session
  onNavigate: (key: string) => void
  onViewAs: (principal: string) => void
}) {
  // 无权看的标签不渲染：恢复码看 canRecovery，我的账号看 canMyMembers。
  const tabs = ALL_TABS.filter((item) => {
    if (item.id === 'recovery') return caps.canRecovery
    if (item.id === 'members') return caps.canMyMembers
    return true
  })
  const [tab, setTab] = useState<MyAccountSecurityTab>(() =>
    tabs.some((item) => item.id === initialTab) ? initialTab : tabs[0].id,
  )
  return (
    <div>
      <div className="mp-tabs-bar">
        <div className="mp-tabs" role="tablist" aria-label="我的账号与安全视图">
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
      {tab === 'keys' && (
        <MyKeys profile={profile} caps={caps} readOnly={readOnly} session={session} />
      )}
      {tab === 'account' && <AccountSecurity profile={profile} readOnly={readOnly} />}
      {tab === 'recovery' && <Recovery caps={caps} readOnly={readOnly} />}
      {tab === 'members' && (
        <Members
          profile={profile}
          caps={caps}
          variant="member"
          readOnly={readOnly}
          onViewAs={onViewAs}
        />
      )}
      {tab === 'me' && <Me caps={caps} onNavigate={onNavigate} />}
    </div>
  )
}
