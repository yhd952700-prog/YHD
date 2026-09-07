import { useEffect, useState } from 'react'
import { NAV_MAIN } from '../lib/dashboardData'

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
export function Sidebar({ active, onSelect }: SidebarProps) {
  return (
    <aside className="sidebar">
      <nav className="nav">
        {NAV_MAIN.map((n) => (
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
            {n.badge && <span className="nav-badge">{n.badge}</span>}
          </button>
        ))}
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
            <li className="warn">
              <span>External Action</span>
              <em>PENDING APPROVAL</em>
            </li>
          </ul>
        </div>
      </div>

      <div className="sidebar-foot">© 2026 LIUHAO AI Team</div>
    </aside>
  )
}
