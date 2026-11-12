/**
 * 系统状态与诊断（简约版合并页）。
 *
 * 并进来的 4 个旧入口在这里做标签：系统状态 / 信任 / 网络 / 身份与权限。
 * 每个标签直接复用现有页面组件，不重写逻辑；SystemStatus 不接 query
 * prop（只接 provider），其余三个接 query。
 */
import { useState } from 'react'
import type { AccountProfile } from '../lib/accountCaps'
import type { ActiveProviderStatus } from '../lib/providerStatus'
import { SystemStatus } from './Operations'
import { Trust } from './Trust'
import { Network } from './Network'
import { Identity } from './Identity'
import './MergedPages.css'

export type SystemDiagnosticsTab = 'status' | 'trust' | 'network' | 'identity'

const TABS: { id: SystemDiagnosticsTab; label: string }[] = [
  { id: 'status', label: '系统状态' },
  { id: 'trust', label: '信任' },
  { id: 'network', label: '网络' },
  { id: 'identity', label: '身份与权限' },
]

export function SystemDiagnostics({
  initialTab = 'status',
  query = '',
  profile,
  provider,
}: {
  initialTab?: SystemDiagnosticsTab
  query?: string
  profile: AccountProfile
  provider: ActiveProviderStatus
}) {
  const [tab, setTab] = useState<SystemDiagnosticsTab>(initialTab)
  return (
    <div>
      <div className="mp-tabs-bar">
        <div className="mp-tabs" role="tablist" aria-label="系统状态与诊断视图">
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
      {tab === 'status' && <SystemStatus provider={provider} />}
      {tab === 'trust' && <Trust query={query} />}
      {tab === 'network' && <Network query={query} />}
      {tab === 'identity' && <Identity query={query} profile={profile} />}
    </div>
  )
}
