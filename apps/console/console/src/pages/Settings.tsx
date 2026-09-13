/**
 * 系统设置 —— 形态覆盖 / 主题 / 会话 / 通知。
 *
 * 这一页存在的意义是让"三端"从一句口号变成一个可验证的事实：同一个账号、同一个
 * 会话，在这里可以立刻把界面切到桌面端 / 网页版 / 手机端，直接看到三种布局。
 *
 * 形态覆盖是**显示偏好**，不是权限开关 —— 切成手机端不会少任何一个可执行动作，
 * 只是把侧边栏换成底部导航、把多列网格收成单列。任何"某端不能做某事"的规则都
 * 必须落在服务端，不在这里。
 */

import { useCallback, useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { DeviceMode, DeviceModeState, Theme } from '../lib/mode'
import { DEVICE_MODES, MODE_HINTS, MODE_LABELS } from '../lib/mode'
import type { Session } from '../lib/auth'
import { humanDuration, toneVar, useNow } from '../lib/format'
import type { OsNotification } from '../components/Shell'
import { Card, Dot } from '../components/ui'
import {
  IconCheck,
  IconDesktop,
  IconGlobe,
  IconMobile,
  IconRefresh,
  IconServer,
} from '../components/icons'

export interface SettingsProps {
  device: DeviceModeState
  session: Session
  notifications: OsNotification[]
  onLogout: () => void
}

const MODE_ICON: Record<DeviceMode, (props: { size?: number }) => ReactElement> = {
  desktop: IconDesktop,
  web: IconGlobe,
  mobile: IconMobile,
}

/** `beforeinstallprompt` 事件（Chromium 专有，TS 的 DOM 类型里没有）。 */
interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice?: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

export function Settings({ device, session, notifications, onLogout }: SettingsProps) {
  const { mode, detected, override, theme, setOverride, setTheme } = device

  // 安装提示：桌面端会触发 beforeinstallprompt，手机端（iOS Safari）不会 —— 后者
  // 只能手动"添加到主屏幕"。拿不到事件时如实提示手动方式，而不是放一个按了没反应的按钮。
  const [installEvent, setInstallEvent] = useState<InstallPromptEvent | null>(null)
  // 初始值用惰性初始化器取：在 effect 里 setState 会多跑一轮渲染，而这里根本不需要
  // 订阅变化 —— `appinstalled` 事件已经覆盖了"装完之后变成 standalone"这一种变化。
  const [standalone, setStandalone] = useState(() => {
    try {
      return window.matchMedia('(display-mode: standalone)').matches
    } catch {
      return false
    }
  })

  useEffect(() => {
    const onPrompt = (event: Event) => {
      event.preventDefault()
      setInstallEvent(event as InstallPromptEvent)
    }
    const onInstalled = () => {
      setInstallEvent(null)
      setStandalone(true)
    }
    window.addEventListener('beforeinstallprompt', onPrompt)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])

  const install = useCallback(async () => {
    if (!installEvent) return
    await installEvent.prompt()
    const choice = await installEvent.userChoice
    if (choice?.outcome === 'accepted') setStandalone(true)
    setInstallEvent(null)
  }, [installEvent])

  // 剩余时长必须来自"状态化的时间"：在渲染里直接调 `Date.now()` 是不纯的
  // （同样的 props 会渲染出不同结果），React 的纯度检查会拦下来。
  const now = useNow()
  const remaining = session.expiresAt ? Math.max(0, session.expiresAt - now / 1000) : 0

  return (
    <div className="os-grid">
      <Card title="端形态" sub="同一份代码的三种呈现 —— 切换只影响布局，不影响权限" span={12}>
        <div className="os-choice">
          <button
            className="os-choice-option"
            data-selected={override === null ? 'true' : 'false'}
            onClick={() => setOverride(null)}
          >
            <span className="os-choice-title">
              <IconRefresh size={15} /> 跟随窗口
            </span>
            <span className="os-choice-desc">
              按视口宽度自动判定（当前 {MODE_LABELS[detected]}）
            </span>
          </button>

          {DEVICE_MODES.map((candidate) => {
            const Icon = MODE_ICON[candidate]
            return (
              <button
                key={candidate}
                className="os-choice-option"
                data-selected={override === candidate ? 'true' : 'false'}
                onClick={() => setOverride(candidate)}
              >
                <span className="os-choice-title">
                  <Icon size={15} /> {MODE_LABELS[candidate]}
                </span>
                <span className="os-choice-desc">{MODE_HINTS[candidate]}</span>
              </button>
            )
          })}
        </div>
        <div className="os-note" style={{ marginTop: 12 }}>
          <span>
            当前生效形态：<strong>{MODE_LABELS[mode]}</strong>
            {override ? '（已手动覆盖）' : '（跟随窗口）'}。
            三端共用同一批端点与同一套鉴权 —— 断点里没有、也不该有权限边界。
          </span>
        </div>
      </Card>

      <Card title="外观主题" sub="可跟随形态，也可手动指定" span={6}>
        <div className="os-choice">
          {(['dark', 'light'] as Theme[]).map((candidate) => (
            <button
              key={candidate}
              className="os-choice-option"
              data-selected={theme === candidate ? 'true' : 'false'}
              onClick={() => setTheme(candidate)}
            >
              <span className="os-choice-title">
                {theme === candidate && <IconCheck size={15} />}
                {candidate === 'dark' ? '深色' : '浅色'}
              </span>
              <span className="os-choice-desc">
                {candidate === 'dark' ? '桌面端 / 手机端默认' : '网页版默认'}
              </span>
            </button>
          ))}
          <button className="os-choice-option" data-selected="false" onClick={() => setTheme(null)}>
            <span className="os-choice-title">
              <IconRefresh size={15} /> 跟随形态
            </span>
            <span className="os-choice-desc">清除手动覆盖，回到形态默认</span>
          </button>
        </div>
      </Card>

      <Card title="当前会话" sub="令牌由服务端签发与校验，前端只保存不决策" span={6}>
        <dl className="os-kv">
          <dt>主体</dt>
          <dd>{session.principal || '—'}</dd>
          <dt>显示名</dt>
          <dd>{session.displayName || '—'}</dd>
          <dt>身份范围</dt>
          <dd>{session.scope ? `${session.scope} · 人类主权` : '—'}</dd>
          <dt>登录来源端</dt>
          <dd>{MODE_LABELS[session.client] ?? session.client}</dd>
          <dt>会话剩余</dt>
          <dd>{session.expiresAt ? humanDuration(remaining) : '由服务端裁决'}</dd>
          <dt>过期时刻</dt>
          <dd>
            {session.expiresAt ? new Date(session.expiresAt * 1000).toLocaleString('zh-CN') : '—'}
          </dd>
          <dt>凭据权限</dt>
          <dd>
            {session.permissions.length > 0
              ? session.permissions.join(' / ')
              : '（无附加 scope）'}
          </dd>
        </dl>
        <div style={{ marginTop: 14, display: 'flex', gap: 8 }}>
          <button className="os-btn os-btn-ghost" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </Card>

      <Card title="安装到设备" sub="PWA —— 桌面端独立窗口 / 手机端主屏图标" span={6}>
        {standalone ? (
          <div className="os-list">
            <div className="os-row">
              <span className="os-dot" style={{ background: 'var(--os-ok)' }} />
              <div className="os-row-main">
                <div className="os-row-title">已在独立窗口中运行</div>
                <div className="os-row-sub">当前就是从已安装的应用打开的</div>
              </div>
              <span className="os-badge" data-tone="ok">
                已安装
              </span>
            </div>
          </div>
        ) : (
          <>
            <div className="os-list">
              <div className="os-row">
                <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                <div className="os-row-main">
                  <div className="os-row-title">桌面端（Chrome / Edge）</div>
                  <div className="os-row-sub">
                    地址栏右侧的「安装」图标，或浏览器菜单 → 安装应用
                  </div>
                </div>
              </div>
              <div className="os-row">
                <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                <div className="os-row-main">
                  <div className="os-row-title">手机端（iOS Safari / Android Chrome）</div>
                  <div className="os-row-sub">
                    分享 → 添加到主屏幕。iOS 不派发安装事件，只能用这个手动方式。
                  </div>
                </div>
              </div>
            </div>
            {installEvent && (
              <div style={{ marginTop: 14 }}>
                <button className="os-btn" onClick={() => void install()}>
                  立即安装
                </button>
              </div>
            )}
          </>
        )}
      </Card>

      <Card title="系统通知" sub="与顶栏铃铛同一份数据" span={12}>
        {notifications.length === 0 ? (
          <div className="os-empty">
            <strong>暂无通知</strong>
            <span>所有已接入项状态正常</span>
          </div>
        ) : (
          <div className="os-list">
            {notifications.map((item) => (
              <div className="os-row" key={item.id}>
                <span
                  className="os-dot"
                  style={{ background: `var(--os-${toneVar(item.tone)})` }}
                />
                <div className="os-row-main">
                  <div className="os-row-title">{item.title}</div>
                  <div className="os-row-sub">{item.detail}</div>
                </div>
                <span className="os-badge" data-tone={item.tone === 'muted' ? 'muted' : item.tone}>
                  {item.tone === 'danger' ? '严重' : item.tone === 'warn' ? '注意' : '正常'}
                </span>
              </div>
            ))}
          </div>
        )}
        <div className="os-legend" style={{ marginTop: 12 }}>
          <Dot tone="muted" label={<>共 {notifications.length} 条</>} />
          <Dot tone="accent" label={<>当前形态 {MODE_LABELS[mode]}</>} />
          <span className="os-legend-item">
            <IconServer size={13} /> 数据来自真实端点
          </span>
        </div>
      </Card>
    </div>
  )
}
