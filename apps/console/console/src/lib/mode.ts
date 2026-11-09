/**
 * 三端形态（桌面端 / 网页版 / 手机端）与主题。
 *
 * **形态是布局，不是权限。** 三个端共用同一份代码、同一套会话、同一批接口；
 * 差别只在断点、导航位置与信息密度。把形态做成权限会立刻制造出"手机端不能
 * 审批"这类假边界 —— 那是产品决策，不该藏在断点里。
 *
 * 身份先于尺寸：桌面客户端（原生窗口）由自定义 User-Agent 标记
 * （``LiuHaoDesktopShell/...``，见 ``src/desktop/native_window.py``）识别，
 * 一旦命中就**固定**为 desktop，不因窗口被拖窄而变成网页版/手机端。
 * 普通浏览器才按视口宽度判定：
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

/**
 * 布局称呼：只描述排版，不冒充客户端身份。
 *
 * 浏览器在宽屏下也走 ``data-mode=desktop`` 的栅格，但它的身份是「网页版」。
 * 设置页里描述"当前生效的是什么"时必须用这一组词，不能拿 ``MODE_LABELS``
 * 说成「桌面端（PC 客户端）」——那会让网页端自称桌面端。身份只由
 * ``CLIENT_LABEL[detectClient()]`` 表达。
 */
export const LAYOUT_LABELS: Record<DeviceMode, string> = {
  desktop: '宽屏布局',
  web: '紧凑布局',
  mobile: '窄屏布局',
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

/**
 * 桌面壳 UA 身份标记。必须与 `src/desktop/native_window.py` 的
 * `DESKTOP_UA_TOKEN` 完全一致（`tests/desktop` 有用例钉住两边同步）。
 */
export const DESKTOP_SHELL_UA_TOKEN = 'LiuHaoDesktopShell/'

/** 当前页面是否运行在桌面壳的原生窗口里（读 UA，不靠窗口尺寸）。 */
export function isDesktopShell(ua?: string): boolean {
  const value =
    ua ?? (typeof navigator === 'undefined' ? '' : navigator.userAgent ?? '')
  return value.includes(DESKTOP_SHELL_UA_TOKEN)
}

/** 按身份 + 视口判定形态：桌面壳恒为 desktop，浏览器按宽度。 */
export function detectMode(width?: number, ua?: string): DeviceMode {
  if (isDesktopShell(ua)) return 'desktop'
  const w = width ?? (typeof window === 'undefined' ? WEB_MAX_WIDTH : window.innerWidth)
  if (w < MOBILE_MAX_WIDTH) return 'mobile'
  if (w < WEB_MAX_WIDTH) return 'web'
  return 'desktop'
}

/** 客户端身份（与**布局**分开）：桌面壳 / 浏览器网页 / 手机浏览器。 */
export type ClientKind = 'desktop' | 'web' | 'mobile'

/**
 * 判定客户端身份：桌面壳读 UA；浏览器按宽度区分手机与网页。
 *
 * 浏览器即使窗口很宽，身份仍是 `web`（不是 desktop）——布局可以借用宽屏
 * 栅格，但「当前形态」与客户端的自我描述不能冒充桌面客户端。
 */
export function detectClient(width?: number, ua?: string): ClientKind {
  if (isDesktopShell(ua)) return 'desktop'
  const w = width ?? (typeof window === 'undefined' ? WEB_MAX_WIDTH : window.innerWidth)
  return w < MOBILE_MAX_WIDTH ? 'mobile' : 'web'
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
  // 身份与形态分开标记：桌面壳即使被用户覆盖成网页版，`data-client` 仍是
  // `desktop-shell`，便于排查"到底在哪个壳里"。
  root.dataset.client = isDesktopShell() ? 'desktop-shell' : 'browser'

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
