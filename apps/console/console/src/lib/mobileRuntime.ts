/**
 * 手机端运行时兜底（纯增量，不依赖 App.tsx）。
 *
 * 两件事：
 * 1. **键盘遮挡**：`interactive-widget=resizes-content` 只在 Android Chrome 生效，
 *    iOS Safari 不会收缩布局视口。这里用 `visualViewport` 监听 + 聚焦元素滚入
 *    可视区兜底；样式一行不改。
 * 2. **断网提示**：`useApi` 的自动重连是"请求失败之后"才发生，用户先看到的是
 *    转圈或旧数据。这里在 `offline` 事件立刻显示中文说明，`online` 后提示恢复。
 *
 * 两个安装函数都是幂等的：重复调用不会叠加监听或 banner。
 */

const BANNER_ID = 'liuhao-network-banner'

let installed = false
let bannerInstalled = false

function isTextField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

/** 键盘弹起时把正在输入的控件带回可视区（不改布局、不改 App）。 */
function keepFocusedVisible(): void {
  const viewport = window.visualViewport
  const active = document.activeElement
  if (!viewport || !isTextField(active)) return
  // 布局视口没变、可视视口明显变矮，才判定为软键盘遮挡。
  if (viewport.height >= window.innerHeight - 120) return
  if (!(active instanceof HTMLElement)) return
  active.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' })
}

export function installMobileViewportGuard(): void {
  if (installed || typeof window === 'undefined') return
  installed = true

  const viewport = window.visualViewport
  let timer: number | null = null
  const schedule = () => {
    if (timer !== null) window.clearTimeout(timer)
    // 键盘动画有两个阶段（开始 / 结束），各补一次。
    timer = window.setTimeout(() => {
      keepFocusedVisible()
      timer = window.setTimeout(keepFocusedVisible, 260)
    }, 120)
  }

  document.addEventListener('focusin', (event) => {
    if (isTextField(event.target)) schedule()
  })
  viewport?.addEventListener('resize', schedule)
  window.addEventListener('orientationchange', schedule)
}

function banner(): HTMLElement | null {
  return document.getElementById(BANNER_ID)
}

function ensureBanner(): HTMLElement {
  const existing = banner()
  if (existing) return existing
  const element = document.createElement('div')
  element.id = BANNER_ID
  element.setAttribute('role', 'status')
  element.setAttribute('aria-live', 'polite')
  element.style.cssText = [
    'position:fixed',
    'left:0',
    'right:0',
    'top:0',
    'z-index:2147483000',
    'box-sizing:border-box',
    'padding:calc(10px + env(safe-area-inset-top)) 16px 10px',
    'font:14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    'color:#fff',
    'background:#b42318',
    'box-shadow:0 2px 8px rgba(0,0,0,.28)',
    'transform:translateY(-100%)',
    'transition:transform .18s ease-out',
    'pointer-events:none',
  ].join(';')
  document.body.appendChild(element)
  // 下一帧滑入，避免一出现就"砸"在屏幕上。
  requestAnimationFrame(() => {
    element.style.transform = 'translateY(0)'
  })
  return element
}

function showBanner(text: string, tone: 'offline' | 'online'): void {
  const element = ensureBanner()
  element.textContent = text
  element.style.background = tone === 'offline' ? '#b42318' : '#157f3b'
  element.style.transform = 'translateY(0)'
}

function hideBanner(): void {
  const element = banner()
  if (!element) return
  element.style.transform = 'translateY(-100%)'
  window.setTimeout(() => element.remove(), 220)
}

export function installNetworkStatusBanner(): void {
  if (bannerInstalled || typeof window === 'undefined') return
  bannerInstalled = true

  const goOffline = () => {
    showBanner(
      '网络已断开：请检查 Wi-Fi，并确认手机和电脑在同一个网络。恢复后会自动重连。',
      'offline',
    )
  }
  const goOnline = () => {
    showBanner('网络已恢复，正在自动重连…', 'online')
    window.setTimeout(() => {
      if (navigator.onLine) hideBanner()
    }, 2500)
  }

  window.addEventListener('offline', goOffline)
  window.addEventListener('online', goOnline)
  if (!navigator.onLine) goOffline()
}

/** 入口一次性安装；返回是否可用（供测试断言）。 */
export function installMobileRuntime(): void {
  installMobileViewportGuard()
  installNetworkStatusBanner()
}
