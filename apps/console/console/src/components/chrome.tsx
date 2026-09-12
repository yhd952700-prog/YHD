import { useEffect, useState } from 'react'
import { NAV_MAIN } from '../lib/dashboardData'
import type { ApprovalUiState } from './ApprovalCenter'

/** 当前时间（参考图格式 2025-08-30 16:36 (PST)）。 */
function useClock(): string {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  const pad = (n: number) => String(n).padStart(2, '0')
  const d = now
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())} (PST)`
}

interface SidebarProps {
  active: string
  onSelect: (key: string) => void
  /** Policy 审批真实状态。未认证时如实显示，不假装 SAFE MODE。 */
  approval?: ApprovalUiState
}

/** 顶栏。 */
export function TopBar() {
  const clock = useClock()
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-logo">
          <span className="brand-logo-glyph">鎏</span>
        </div>
        <div className="brand-text">
          <div className="brand-line1">
            <span className="brand-cn">鎏灏 AI</span>
            <span className="brand-tag">AI核心控制中枢</span>
          </div>
          <div className="brand-line2">Advanced Automatic Finance</div>
        </div>
      </div>

      <div className="topbar-center">
        <div className="cc-title">
          <span className="cc-title-main">CEO COMMAND CENTER</span>
          <span className="cc-title-sub">老板驾驶舱</span>
        </div>
        <div className="cc-tags">
          <span>Intelligent</span> · <span>Autonomous</span> · <span>Global</span>
        </div>
      </div>

      <div className="topbar-meta">
        <span className="meta-time">{clock}</span>
        <span className="meta-weather">🌤 26°C 北京</span>
        <span className="meta-user">
          <span className="meta-avatar">辛</span> 辛宏达
        </span>
      </div>
    </header>
  )
}

/** 左侧导航 + 安全运行模式。 */
export function Sidebar({ active, onSelect, approval }: SidebarProps) {
  // 审批徽标：只显示**真实**的待批/有效凭据数；未认证时不显示数字，
  // 而不是像以前那样写死一个 '1'。
  const acl = approval
  const badge = acl?.authed && acl.grants.length > 0 ? String(acl.grants.length) : undefined

  return (
    <aside className="sidebar">
      <nav className="nav">
        {NAV_MAIN.map((n) => {
          const isAcl = n.key === 'approval'
          const shown = isAcl ? badge : n.badge
          return (
            <button
              key={n.key}
              className={`nav-item ${active === n.key ? 'active' : ''}`}
              onClick={() => onSelect(n.key)}
            >
              <span className="nav-glyph">{n.icon}</span>
              <span className="nav-label">
                {n.label}
                <span className="nav-en">{n.en}</span>
              </span>
              {shown && <span className="nav-badge">{shown}</span>}
            </button>
          )
        })}
      </nav>

      <div className="sidebar-section">
        <div className="sidebar-sec-title">安全运行模式</div>
        <div className="safe-mode">
          <div className="safe-mode-badge">
            <span className="safe-shield">🛡</span> SAFE MODE
          </div>
          <ul>
            <li className="ok">
              <span>Executive Lock</span>
              <em>LOCKED</em>
            </li>
            <li className="ok">
              <span>Browser Guard</span>
              <em>GUARDED</em>
            </li>
            {/* 内核层拦截：真实状态来自 /v1/policy/enforcement（需令牌）。 */}
            <li
              className={
                acl?.snap?.config_error ? 'warn' : acl?.snap?.enabled ? 'warn' : 'ok'
              }
            >
              <span>Kernel Policy</span>
              <em>
                {acl?.snap?.config_error
                  ? 'CONFIG ERROR'
                  : acl?.snap?.enabled
                    ? `ENFORCED · ${acl.snap.count}`
                    : acl?.authed
                      ? 'RECORD-ONLY'
                      : 'UNVERIFIED'}
              </em>
            </li>
            {/* 外部动作审批：真实凭据数，未认证时不假装有待批项。 */}
            <li className={acl?.authed && acl.grants.length > 0 ? 'warn' : 'ok'}>
              <span>External Action</span>
              <em>
                {!acl?.authed
                  ? 'UNVERIFIED'
                  : acl.grants.length > 0
                    ? `${acl.grants.length} APPROVED`
                    : 'NONE PENDING'}
              </em>
            </li>
          </ul>
        </div>
      </div>

      <div className="sidebar-foot">© 2026 LIUHAO AI Team</div>
    </aside>
  )
}
