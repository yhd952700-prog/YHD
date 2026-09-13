/**
 * 三形态外壳：桌面端（全宽侧边栏）/ 网页版（紧凑）/ 手机端（底部导航）。
 *
 * 形态相关的显示/隐藏**全部由 CSS 依据 `<html data-mode>` 决定**（见 os.css），
 * 这里只负责渲染结构。组件里出现 `mode` 的唯一用途是给无障碍属性与搜索框
 * 快捷键提示选择文案 —— 一旦在 JS 里做布局判断，三端就会开始各自漂移。
 */

import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { DeviceMode, Theme } from '../lib/mode'
import type { Session } from '../lib/auth'
import { ALL_NAV, FOOTER_NAV, GOVERNANCE_NAV, MOBILE_NAV_KEYS, PRIMARY_NAV } from '../lib/nav'
import type { NavItem } from '../lib/nav'
import { IconActivity, IconBell, IconLogout, IconSearch, IconSparkle } from './icons'

export interface OsNotification {
  id: string
  tone: 'ok' | 'warn' | 'danger' | 'accent' | 'muted'
  title: string
  detail: string
}

export interface ShellProps {
  mode: DeviceMode
  theme: Theme
  session: Session
  active: string
  onNavigate: (key: string) => void
  onToggleTheme: () => void
  onLogout: () => void
  query: string
  onQueryChange: (value: string) => void
  notifications: OsNotification[]
  children: ReactNode
}

export function Shell({
  mode,
  theme,
  session,
  active,
  onNavigate,
  onToggleTheme,
  onLogout,
  query,
  onQueryChange,
  notifications,
  children,
}: ShellProps) {
  const initial = (session.displayName || session.principal || '?').trim().charAt(0).toUpperCase()
  const displayName = session.displayName || session.principal

  return (
    <div className="os-app">
      <aside className="os-sidebar" aria-label="主导航">
        <div className="os-brand">
          <div className="os-brand-mark">
            <svg width="19" height="19" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 2.6l7.4 4.3v8.6L12 19.8 4.6 15.5V6.9z"
                stroke="#fff"
                strokeWidth="1.6"
                strokeLinejoin="round"
              />
              <circle cx="12" cy="12" r="2.4" fill="#fff" />
            </svg>
          </div>
          <div>
            <div className="os-brand-name">LiuHao AI OS</div>
            <div className="os-brand-sub">LIVE · 鎏灏智能中枢</div>
          </div>
        </div>

        <nav className="os-nav">
          <div className="os-nav-label">业务</div>
          {PRIMARY_NAV.map((item) => (
            <NavButton
              key={item.key}
              item={item}
              active={active === item.key}
              onSelect={onNavigate}
            />
          ))}

          <div className="os-nav-label">治理</div>
          {GOVERNANCE_NAV.map((item) => (
            <NavButton
              key={item.key}
              item={item}
              active={active === item.key}
              onSelect={onNavigate}
            />
          ))}
        </nav>

        <div className="os-sidebar-foot">
          <nav className="os-nav" aria-label="设置">
            {FOOTER_NAV.map((item) => (
              <NavButton
                key={item.key}
                item={item}
                active={active === item.key}
                onSelect={onNavigate}
              />
            ))}
          </nav>
          <div className="os-user">
            <div className="os-avatar" aria-hidden="true">
              {initial}
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="os-user-name">{displayName}</div>
              <div className="os-user-role">
                {session.scope ? `${session.scope} · 人类主权` : '人类身份'}
              </div>
            </div>
            <button className="os-logout" onClick={onLogout} title="退出登录" aria-label="退出登录">
              <IconLogout size={16} />
            </button>
          </div>
        </div>
      </aside>

      <div className="os-main">
        <header className="os-topbar">
          <label className="os-search">
            <IconSearch size={16} />
            <span className="os-visually-hidden">搜索当前页面</span>
            <input
              type="search"
              placeholder="搜索任务 / 员工 / 数据…"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
            />
            {mode !== 'mobile' && <span className="os-search-kbd">/</span>}
          </label>

          <div className="os-topbar-actions">
            <Clock />
            <button
              className="os-icon-btn"
              onClick={onToggleTheme}
              title={theme === 'dark' ? '切换到浅色' : '切换到深色'}
              aria-label="切换主题"
            >
              {theme === 'dark' ? <IconSparkle size={16} /> : <IconActivity size={16} />}
            </button>
            <Notifications items={notifications} />
          </div>
        </header>

        <main className="os-content">{children}</main>
      </div>

      <nav className="os-bottom-nav" aria-label="底部导航">
        {MOBILE_NAV_KEYS.map((key) => {
          const item = ALL_NAV.find((candidate) => candidate.key === key)
          if (!item) return null
          const Icon = item.icon
          const label = item.key === 'settings' ? '我的' : item.label.replace('我AI', '')
          return (
            <button
              key={item.key}
              className="os-bottom-item"
              aria-current={active === item.key ? 'page' : undefined}
              onClick={() => onNavigate(item.key)}
            >
              <Icon size={19} />
              <span>{label}</span>
            </button>
          )
        })}
      </nav>
    </div>
  )
}

function NavButton({
  item,
  active,
  onSelect,
}: {
  item: NavItem
  active: boolean
  onSelect: (key: string) => void
}) {
  const Icon = item.icon
  const hasBadge = item.badge !== undefined && item.badge !== '' && item.badge !== 0
  return (
    <button
      className="os-nav-item"
      aria-current={active ? 'page' : undefined}
      onClick={() => onSelect(item.key)}
    >
      <span className="os-nav-icon">
        <Icon size={17} />
      </span>
      <span className="os-nav-text">{item.label}</span>
      {hasBadge && (
        <span className="os-nav-badge" data-tone={item.badgeTone ?? 'accent'}>
          {item.badge}
        </span>
      )}
    </button>
  )
}

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <span className="os-clock">
      {now.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}{' '}
      {now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
    </span>
  )
}

function Notifications({ items }: { items: OsNotification[] }) {
  const [open, setOpen] = useState(false)
  const urgent = items.filter((item) => item.tone === 'danger' || item.tone === 'warn').length

  return (
    <div style={{ position: 'relative' }}>
      <button
        className="os-icon-btn"
        onClick={() => setOpen((value) => !value)}
        title="系统通知"
        aria-label={`系统通知，${items.length} 条`}
        aria-expanded={open}
      >
        <IconBell size={16} />
        {urgent > 0 && <span className="os-icon-btn-dot" />}
      </button>

      {open && (
        <div
          className="os-card"
          style={{
            position: 'absolute',
            right: 0,
            top: 42,
            width: 320,
            zIndex: 40,
            maxHeight: 380,
            overflowY: 'auto',
          }}
        >
          <div className="os-card-head">
            <h3 className="os-card-title">系统通知</h3>
            <span className="os-badge" data-tone="muted">
              {items.length}
            </span>
          </div>
          {items.length === 0 ? (
            <div className="os-empty">
              <strong>暂无通知</strong>
              <span>所有已接入项状态正常</span>
            </div>
          ) : (
            <div className="os-list">
              {items.map((item) => (
                <div className="os-row" key={item.id}>
                  <span className="os-dot" style={{ background: toneColor(item.tone) }} />
                  <div className="os-row-main">
                    <div className="os-row-title">{item.title}</div>
                    <div className="os-row-sub">{item.detail}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function toneColor(tone: OsNotification['tone']): string {
  switch (tone) {
    case 'ok':
      return 'var(--os-ok)'
    case 'warn':
      return 'var(--os-warn)'
    case 'danger':
      return 'var(--os-danger)'
    case 'accent':
      return 'var(--os-accent)'
    default:
      return 'var(--os-text-faint)'
  }
}
