/**
 * 三端形态（桌面端 / 网页版 / 手机端）与主题。
 *
 * **形态是布局，不是权限。** 三个端共用同一份代码、同一套会话、同一批接口；
 * 差别只在断点、导航位置与信息密度。把形态做成权限会立刻制造出"手机端不能
 * 审批"这类假边界 —— 那是产品决策，不该藏在断点里。
 *
 * 判定以视口宽度为准（CSS 断点与之一致）：
 *   < 768px   → mobile   （底部导航、单列卡片）
 *   < 1280px  → web      （紧凑布局、浅色）
 *   >= 1280px → desktop  （全宽侧边栏、深色、多列网格）
 *
 * 用户可在系统设置里覆盖（覆盖值存 localStorage，刷新后保持）。
 */

import { useCallback, useEffect, useState } from 'react'

export type DeviceMode = 'desktop' | 'web' | 'mobile'
export type Theme = 'dark' | 'light'

export const DEVICE_MODES: readonly DeviceMode[] = ['desktop', 'web', 'mobile']

/** 断点与 os.css 中的媒体查询保持一致；改这里必须同步改样式。 */
export const MOBILE_MAX_WIDTH = 768
export const WEB_MAX_WIDTH = 1280

const MODE_KEY = 'liuhao.mode'
const THEME_KEY = 'liuhao.theme'

/** 设计约定：桌面端与手机端用深色，网页版用浅色。 */
export const DEFAULT_THEME: Record<DeviceMode, Theme> = {
  desktop: 'dark',
  web: 'light',
  mobile: 'dark',
}

export const MODE_LABELS: Record<DeviceMode, string> = {
  desktop: '桌面端（PC 客户端）',
  web: '网页版（Web）',
  mobile: '手机端（iOS / Android）',
}

export const MODE_HINTS: Record<DeviceMode, string> = {
  desktop: '全宽侧边栏 · 多列网格 · 深色',
  web: '紧凑布局 · 浅色 · 适合浏览器标签页',
  mobile: '底部导航 · 单列卡片 · 适配安全区',
}

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStored(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // 存储不可用只是失去持久化。
  }
}

/** 按当前视口宽度判定形态。 */
export function detectMode(width?: number): DeviceMode {
  const w = width ?? (typeof window === 'undefined' ? WEB_MAX_WIDTH : window.innerWidth)
  if (w < MOBILE_MAX_WIDTH) return 'mobile'
  if (w < WEB_MAX_WIDTH) return 'web'
  return 'desktop'
}

/** 用户手动覆盖的形态；未覆盖返回 null。 */
export function getModeOverride(): DeviceMode | null {
  const stored = readStored(MODE_KEY)
  return DEVICE_MODES.includes(stored as DeviceMode) ? (stored as DeviceMode) : null
}

export function setModeOverride(mode: DeviceMode | null): void {
  writeStored(MODE_KEY, mode)
}

/** 用户手动选择的主题；未选择返回 null。 */
export function getThemeOverride(): Theme | null {
  const stored = readStored(THEME_KEY)
  return stored === 'dark' || stored === 'light' ? stored : null
}

export function setThemeOverride(theme: Theme | null): void {
  writeStored(THEME_KEY, theme)
}

/** 浏览器 UI（地址栏 / 状态栏）配色。与 os.css 的 --os-bg 取值保持一致。 */
const THEME_COLOR: Record<Theme, string> = {
  dark: '#050912',
  light: '#f4f7fd',
}

/** 把形态与主题写到 `<html>` 上，CSS 只认这两个属性。 */
export function applyDocumentFlags(mode: DeviceMode, theme: Theme): void {
  const root = document.documentElement
  root.dataset.mode = mode
  root.dataset.theme = theme

  // 让手机端状态栏 / 桌面端标题栏跟着主题走 —— 只有 `[data-theme]` 能驱动 CSS，
  // 但浏览器 UI 只认 `<meta name="theme-color">`，两者必须手动对齐。
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_COLOR[theme])
}

export interface DeviceModeState {
  /** 实际生效的形态（覆盖优先，否则按视口） */
  mode: DeviceMode
  /** 视口自动判定的形态（用于在设置里显示"跟随窗口"） */
  detected: DeviceMode
  /** 用户覆盖值，null 表示跟随窗口 */
  override: DeviceMode | null
  theme: Theme
  setOverride: (mode: DeviceMode | null) => void
  setTheme: (theme: Theme | null) => void
}

/** 订阅窗口尺寸变化 + 把形态/主题同步到 `<html>`。 */
export function useDeviceMode(): DeviceModeState {
  const [detected, setDetected] = useState<DeviceMode>(() => detectMode())
  const [override, setOverrideState] = useState<DeviceMode | null>(() => getModeOverride())
  // 主题覆盖值必须是 state：直接从 storage 读不会触发重渲染，切换主题会看起来"没反应"。
  const [themeOverride, setThemeState] = useState<Theme | null>(() => getThemeOverride())

  useEffect(() => {
    const onResize = () => setDetected(detectMode())
    window.addEventListener('resize', onResize)
    window.addEventListener('orientationchange', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      window.removeEventListener('orientationchange', onResize)
    }
  }, [])

  const mode = override ?? detected
  // 未手动指定主题时，主题跟随形态（桌面/手机深色，网页浅色）。
  const effectiveTheme = themeOverride ?? DEFAULT_THEME[mode]

  useEffect(() => {
    applyDocumentFlags(mode, effectiveTheme)
  }, [mode, effectiveTheme])

  const setOverride = useCallback((next: DeviceMode | null) => {
    setModeOverride(next)
    setOverrideState(next)
  }, [])

  const setTheme = useCallback((next: Theme | null) => {
    setThemeOverride(next)
    setThemeState(next)
  }, [])

  return {
    mode,
    detected,
    override,
    theme: effectiveTheme,
    setOverride,
    setTheme,
  }
}
