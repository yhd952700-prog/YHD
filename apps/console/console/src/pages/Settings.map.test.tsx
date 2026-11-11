/**
 * 系统设置 · 设置分组目录。
 *
 * 手机端没有侧栏，账号 / 安全 / 诊断只能从设置页这张目录进去；
 * 这条用例钉住三件事：三组都在、条目走宿主路由、根账号看不到「我的账号」
 * 这类成员面入口（目录与侧栏同源，不能各维护一份）。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Settings } from './Settings'
import { buildProfile, deriveCaps } from '../lib/accountCaps'
import type { Session } from '../lib/auth'
import type { DeviceModeState } from '../lib/mode'

vi.mock('../components/ModelManager', () => ({
  ModelManager: () => null,
  providerLabel: (provider: string) => provider,
}))
vi.mock('../components/DiagnosticsExport', () => ({
  DiagnosticsExport: () => null,
}))

const SESSION: Session = {
  token: 'token',
  principal: 'boss',
  displayName: 'Boss',
  scope: 'L0',
  permissions: [],
  expiresAt: 0,
  client: 'web',
  isRoot: true,
}

const DEVICE: DeviceModeState = {
  mode: 'desktop',
  detected: 'desktop',
  override: null,
  theme: 'dark',
  setOverride: () => {},
  setTheme: () => {},
}

function renderSettings() {
  const onNavigate = vi.fn()
  render(
    <Settings
      device={DEVICE}
      session={SESSION}
      caps={deriveCaps(buildProfile(SESSION, null, 'api', null))}
      onNavigate={onNavigate}
      notifications={[]}
      onLogout={() => {}}
    />,
  )
  return onNavigate
}

beforeEach(() => {
  // 这张卡不依赖任何接口；其余卡片一律 404，让它们走各自的失败态，不干扰断言。
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'not found' }), {
          status: 404,
          headers: { 'Content-Type': 'application/json' },
        }),
    ),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('系统设置 · 设置分组目录', () => {
  test('账号 / 安全 / 诊断三组都在，点条目走宿主路由', () => {
    const onNavigate = renderSettings()

    expect(screen.getByText('账号与成员')).toBeTruthy()
    expect(screen.getByText('安全与审计')).toBeTruthy()
    expect(screen.getByText('系统与诊断')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '成员与权限管理' }))
    expect(onNavigate).toHaveBeenCalledWith('members')
    fireEvent.click(screen.getByRole('button', { name: '操作记录' }))
    expect(onNavigate).toHaveBeenCalledWith('audit')
    fireEvent.click(screen.getByRole('button', { name: '系统状态' }))
    expect(onNavigate).toHaveBeenCalledWith('status')
  })

  test('根账号的目录里不出现成员面的「我的账号」', () => {
    renderSettings()
    expect(screen.queryByRole('button', { name: '我的账号' })).toBeNull()
  })
})
