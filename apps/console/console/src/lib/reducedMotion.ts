/**
 * "减弱动效"偏好。
 *
 * 两级尊重：
 * 1. 系统 `prefers-reduced-motion` 默认生效（用户不必额外设置）；
 * 2. 控制台内可手动覆盖（设置页开关），覆盖值持久化在 localStorage。
 *
 * 视觉层只做一件事：把 html 上的 data-reduced-motion 置为 true，
 * 由 CSS 统一停掉动画与过渡（见 App.css 末尾）。
 */

const KEY = 'liuhao.reduced-motion'

export function getReducedMotion(): boolean {
  try {
    const stored = localStorage.getItem(KEY)
    if (stored === 'true') return true
    if (stored === 'false') return false
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

export function applyReducedMotion(on: boolean): void {
  document.documentElement.setAttribute('data-reduced-motion', on ? 'true' : 'false')
}

export function setReducedMotion(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? 'true' : 'false')
  } catch {
    // 隐私模式：忽略持久化，本次会话仍然生效。
  }
  applyReducedMotion(on)
}
