/**
 * 三形态外壳：桌面端（全宽侧边栏）/ 网页版（紧凑）/ 手机端（底部导航）。
 *
 * 形态相关的显示/隐藏**全部由 CSS 依据 `<html data-mode>` 决定**（见 os.css），
 * 这里只负责渲染结构。组件里出现 `mode` 的唯一用途是给无障碍属性与搜索框
 * 快捷键提示选择文案 —— 一旦在 JS 里做布局判断，三端就会开始各自漂移。
 */

import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { DeviceMode, Theme } from '../lib/mode'
import type { Session } from '../lib/auth'
import { ALL_NAV, navGroupsFor, mobileNavKeysFor, SETTINGS_SECTIONS } from '../lib/nav'
import { BRAND_NAME_EN, BRAND_NAME_ZH } from '../brand'
import type { NavItem } from '../lib/nav'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { identityBarRole } from '../lib/accountCaps'
import { IconActivity, IconBell, IconLogout, IconSearch, IconSparkle } from './icons'
import { DesktopDownloadLink } from './DesktopDownloadLink'

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
  profile: AccountProfile
  caps: AccountCaps
  active: string
  /** 当前页的人类可读名称：用于切页时给读屏播报，而不是只换 DOM。 */
  pageTitle?: string
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
  profile,
  caps,
  active,
  pageTitle,
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
  const navigation = navGroupsFor(caps)
  const mobileKeys = mobileNavKeysFor(caps)
  const roleLabel = identityBarRole(profile)
  const searchRef = useRef<HTMLInputElement>(null)

  // 页内切换没有真实导航，读屏用户不会自动知道内容换了。这里用礼貌播报
  // 说明"已进入哪一页"，同时保留焦点在原导航项上（不抢焦点）。
  const [routeAnnouncement, setRouteAnnouncement] = useState('')
  useEffect(() => {
    if (!pageTitle) return
    setRouteAnnouncement(`已进入「${pageTitle}」`)
  }, [pageTitle])

  // 顶栏标注了 "/" 快捷键，就必须真的能用；在输入框/下拉内不劫持，保持正常输入。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.defaultPrevented) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target as HTMLElement | null
      const tag = target?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) {
        return
      }
      event.preventDefault()
      searchRef.current?.focus()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <>
      {/* 跳过导航：第一次 Tab 就能直达主内容或顶部搜索，不必先走完侧栏 29 项。 */}
      <div className="os-skip-links">
        <a className="os-skip-link" href="#os-main">
          跳到主内容
        </a>
        <a className="os-skip-link" href="#os-search">
          跳到搜索
        </a>
      </div>

      {/* 切换页面时播报落点；空元素常驻，文字变化才会触发读屏。 */}
      <p className="os-visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        {routeAnnouncement}
      </p>

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
            <div className="os-brand-name">{BRAND_NAME_EN}</div>
            <div className="os-brand-sub">LIVE · {BRAND_NAME_ZH}</div>
          </div>
        </div>

        <nav className="os-nav">
          {navigation.map((group) => {
            const GroupIcon = group.icon
            const containsActive = group.items.some((item) => item.key === active)
            return (
              <div className="os-nav-group" key={group.key}>
                <button
                  type="button"
                  className="os-nav-group-head"
                  data-active={containsActive || undefined}
                  title={group.label}
                  onClick={() => onNavigate(containsActive ? active : group.defaultKey)}
                >
                  <span className="os-nav-icon">
                    <GroupIcon size={17} />
                  </span>
                  <span className="os-nav-text">{group.label}</span>
                </button>
                <div className="os-nav-kids">
                  {group.items.map((item) => {
                    const section = SETTINGS_SECTIONS.find(
                      (candidate) => group.key === 'settings' && candidate.startsAt === item.key,
                    )
                    return (
                      <Fragment key={item.key}>
                        {section && <div className="os-nav-sublabel">{section.label}</div>}
                        <NavButton
                          item={item}
                          active={active === item.key}
                          onSelect={onNavigate}
                          compact
                        />
                      </Fragment>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </nav>

        <div className="os-sidebar-foot">
          <div className="os-user">
            <div className="os-avatar" aria-hidden="true">
              {initial}
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="os-user-name">{displayName}</div>
              <div className="os-user-role">{roleLabel}</div>
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
              id="os-search"
              ref={searchRef}
              type="search"
              placeholder="搜索任务 / 员工 / 数据…"
              aria-keyshortcuts="/"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
            />
            {mode !== 'mobile' && <span className="os-search-kbd">/</span>}
          </label>

          <div className="os-topbar-actions">
            {/* 只在网页端显示；CSS 按 data-mode/data-client 控制，桌面壳/手机端不出现。 */}
            <DesktopDownloadLink compact />
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

        <main className="os-content" id="os-main" tabIndex={-1}>
          {children}
        </main>
      </div>

      <nav className="os-bottom-nav" aria-label="底部导航">
        {mobileKeys.map((key) => {
          const item = ALL_NAV.find((candidate) => candidate.key === key)
          if (!item) return null
          const Icon = item.icon
          // 底栏用短标签：六项在 360px 下只有约 58px 一列，长名称会被截断。
          const short: Record<string, string> = {
            'enterprise-overview': '企业总览',
            'my-employees': '办公室',
            office: '办公室',
            'task-center': '任务',
            knowledge: '知识',
            'skills-center': '能力',
            settings: '设置',
            me: '我的',
          }
          const label = short[item.key] ?? item.label
          return (
            <button
              key={item.key}
              className="os-bottom-item"
              data-key={item.key}
              aria-current={active === item.key ? 'page' : undefined}
              onClick={() => onNavigate(item.key)}
            >
              <Icon size={item.key === 'butler' ? 22 : 19} />
              <span>{label}</span>
            </button>
          )
        })}
      </nav>
      </div>
    </>
  )
}

function NavButton({
  item,
  active,
  onSelect,
  compact,
}: {
  item: NavItem
  active: boolean
  onSelect: (key: string) => void
  /** 组内二级项：缩进、字号略小，不抢一级组名的视觉重量。 */
  compact?: boolean
}) {
  const Icon = item.icon
  const hasBadge = item.badge !== undefined && item.badge !== '' && item.badge !== 0
  return (
    <button
      className={compact ? 'os-nav-item os-nav-item-kid' : 'os-nav-item'}
      aria-current={active ? 'page' : undefined}
      title={item.hint}
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
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const urgent = items.filter((item) => item.tone === 'danger' || item.tone === 'warn').length

  /** 关闭并把焦点还给触发按钮：键盘用户不会掉到 body。 */
  const close = useCallback(() => {
    setOpen(false)
    triggerRef.current?.focus()
  }, [])

  // 打开后焦点进入弹层；Esc 关闭并归还焦点。弹层本身可聚焦，
  // 这样"暂无通知"（内部没有可聚焦项）时焦点也有落点。
  useEffect(() => {
    if (!open) return
    const panel = panelRef.current
    const firstFocusable = panel?.querySelector<HTMLElement>(
      'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    ;(firstFocusable ?? panel)?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      close()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, close])

  return (
    <div style={{ position: 'relative' }}>
      <button
        ref={triggerRef}
        className="os-icon-btn"
        onClick={() => (open ? close() : setOpen(true))}
        title="通知"
        aria-label={`通知，${items.length} 条`}
        aria-expanded={open}
        aria-controls="os-notifications-panel"
        aria-haspopup="dialog"
      >
        <IconBell size={16} />
        {urgent > 0 && <span className="os-icon-btn-dot" />}
      </button>

      {open && (
        <div
          ref={panelRef}
          id="os-notifications-panel"
          role="dialog"
          aria-label="通知"
          tabIndex={-1}
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
            <h3 className="os-card-title">通知</h3>
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
