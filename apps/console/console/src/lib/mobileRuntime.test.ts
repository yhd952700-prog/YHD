import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('手机端运行时兜底', () => {
  beforeEach(() => {
    vi.resetModules()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('断网时立刻显示中文提示，恢复后自动收起', async () => {
    vi.useFakeTimers()
    const { installNetworkStatusBanner } = await import('./mobileRuntime')
    installNetworkStatusBanner()

    window.dispatchEvent(new Event('offline'))
    const banner = document.getElementById('liuhao-network-banner')
    expect(banner).not.toBeNull()
    expect(banner?.textContent).toContain('网络已断开')
    expect(banner?.getAttribute('role')).toBe('status')

    window.dispatchEvent(new Event('online'))
    expect(banner?.textContent).toContain('网络已恢复')
    vi.advanceTimersByTime(3000)
    expect(document.getElementById('liuhao-network-banner')).toBeNull()
  })

  it('重复安装不会叠加 banner 或监听', async () => {
    const { installNetworkStatusBanner } = await import('./mobileRuntime')
    installNetworkStatusBanner()
    installNetworkStatusBanner()
    window.dispatchEvent(new Event('offline'))
    expect(document.querySelectorAll('#liuhao-network-banner')).toHaveLength(1)
  })

  it('没有 visualViewport 时视口兜底不报错', async () => {
    const { installMobileViewportGuard } = await import('./mobileRuntime')
    expect(() => installMobileViewportGuard()).not.toThrow()
  })
})
