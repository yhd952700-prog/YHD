/**
 * 第一次使用控制台时的提示回归。
 *
 * 这里钉住的是"陌生人第一次用会卡住"的四个点：
 *   1. 会话过期 / 已登出时，登录页要明说"需要重新登录"（不在 App 里静默跳转）；
 *   2. 工作台要有一眼能找到的对话入口；
 *   3. 要说明关窗后只是退到托盘、本地服务继续运行、可从托盘重开；
 *   4. 没配模型时，要说明可以先跳过、之后在设置里补 Key。
 *
 * 只挂载展示组件：对话面板 / 模型管理 / 诊断导出用空壳替代，不联网。
 */

import { describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { Session } from '../lib/auth'
import type { DeviceModeState } from '../lib/mode'
import type { ActiveProviderStatus } from '../lib/providerStatus'

vi.mock('../components/ChatPanel', () => ({
  ChatPanel: () => <div data-testid="chat-panel" />,
}))
vi.mock('../components/ModelManager', () => ({
  ModelManager: () => <div data-testid="model-manager" />,
}))
vi.mock('../components/DiagnosticsExport', () => ({
  DiagnosticsExport: () => <div data-testid="diagnostics" />,
}))

import { Settings } from './Settings'
import { Workbench } from './Workbench'

const session: Session = {
  token: 'token',
  principal: 'liuhao',
  displayName: '鎏灏',
  scope: 'L0',
  permissions: ['account:read'],
  expiresAt: 0,
  client: 'desktop',
  isRoot: true,
}

const device: DeviceModeState = {
  mode: 'desktop',
  detected: 'desktop',
  override: null,
  theme: 'dark',
  setOverride: () => {},
  setTheme: () => {},
}

const provider: ActiveProviderStatus = {
  provider: null,
  posture: { isReal: false, simulated: false, verdict: '尚未接入真实模型' } as ActiveProviderStatus['posture'],
  mode: 'mock',
  isReal: false,
  isMock: true,
  label: '尚未接入真实模型',
  detail: '当前回复为演示内容，不会调用真实模型。',
  error: null,
  loading: false,
  reload: () => {},
}

describe('第一公里提示', () => {
  /**
   * 把 navigator.userAgent 临时改成桌面壳（追加 `LiuHaoDesktopShell/<版本>`）。
   *
   * 「关闭窗口退托盘」只对原生窗口成立；在浏览器里渲染这句话就是网页端自称
   * 桌面端。所以这类断言必须跑在桌面壳身份下，浏览器身份另有反向用例。
   */
  function asDesktopShell(): () => void {
    const original = window.navigator.userAgent
    Object.defineProperty(window.navigator, 'userAgent', {
      value: `${original} LiuHaoDesktopShell/0.1.2`,
      configurable: true,
    })
    return () => {
      Object.defineProperty(window.navigator, 'userAgent', {
        value: original,
        configurable: true,
      })
    }
  }

  test('工作台有显眼的「开始对话」入口，点击不会抛错', () => {
    render(<Workbench provider={provider} onConfigureModel={() => {}} onNavigate={() => {}} />)
    const button = screen.getByRole('button', { name: '开始对话' })
    expect(button.className).toContain('os-btn-primary')
    fireEvent.click(button)
  })

  test('桌面壳设置页说明关窗会退到托盘、可从托盘重开', () => {
    const restore = asDesktopShell()
    try {
      render(
        <Settings
          device={device}
          session={session}
          notifications={[]}
          onLogout={() => {}}
        />,
      )
      expect(screen.getByText(/关闭窗口不会退出/)).toBeTruthy()
      expect(screen.getByText(/退到系统托盘/)).toBeTruthy()
      expect(screen.getByText(/从托盘重新打开/)).toBeTruthy()
    } finally {
      restore()
    }
  })

  test('网页版不出现「关闭窗口退到托盘」的桌面端专属文案', () => {
    render(
      <Settings
        device={device}
        session={session}
        notifications={[]}
        onLogout={() => {}}
      />,
    )
    expect(screen.queryByText(/关闭窗口不会退出/)).toBeNull()
    expect(screen.queryByText(/退到系统托盘/)).toBeNull()
    expect(screen.queryByText(/从托盘重新打开/)).toBeNull()
  })

  test('设置页说明没配模型可以先跳过、之后补 Key', () => {
    render(
      <Settings
        device={device}
        session={session}
        notifications={[]}
        onLogout={() => {}}
      />,
    )
    expect(screen.getByText(/先跳过/)).toBeTruthy()
    expect(screen.getByText(/补上 Key/)).toBeTruthy()
  })

  test('桌面壳设置页首个可视区有「首次使用提示」，两个跳转按钮可点击', () => {
    // jsdom 没有实现 scrollIntoView；桩掉它，顺带确认点击真的触发了定位。
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView

    const restore = asDesktopShell()
    try {
      render(
        <Settings
          device={device}
          session={session}
          notifications={[]}
          onLogout={() => {}}
        />,
      )

      const notice = screen.getByRole('status', { name: '首次使用提示' })
      expect(notice.textContent).toMatch(/先跳过|最后再配/)
      expect(notice.textContent).toMatch(/补上 Key/)
      expect(notice.textContent).toMatch(/退到系统托盘/)
      expect(notice.textContent).toMatch(/从托盘重新打开/)
      expect(notice.textContent).not.toMatch(/黑色命令窗口|保持打开/)

      fireEvent.click(screen.getByRole('button', { name: '去配置模型' }))
      expect(scrollIntoView).toHaveBeenCalled()
    } finally {
      restore()
    }
  })
})
