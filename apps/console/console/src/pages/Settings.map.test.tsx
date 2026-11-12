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

    // 简约版：目录只列设置组自身的 8 项（除本页），被并掉的旧 key
    //（成员与权限管理 / 操作记录）已不在目录里，改从合并页进。
    fireEvent.click(screen.getByRole('button', { name: '我的账号与安全' }))
    expect(onNavigate).toHaveBeenCalledWith('my-account-security')
    fireEvent.click(screen.getByRole('button', { name: '审批签发' }))
    expect(onNavigate).toHaveBeenCalledWith('approval')
    fireEvent.click(screen.getByRole('button', { name: '系统状态与诊断' }))
    expect(onNavigate).toHaveBeenCalledWith('system-diagnostics')
  })

  test('三段归属：账号与成员 / 安全与审计 / 系统与诊断各收对条目', () => {
    renderSettings()

    // 设置组新顺序：settings、my-account-security、approval、backup、
    // notifications、preflight、about、system-diagnostics；SETTINGS_SECTIONS
    // 起点不变（settings / approval / system-diagnostics），所以分桶是：
    // 账号与成员收「我的账号与安全」；安全与审计收审批签发、备份与迁移、
    // 通知中心、生产预检、关于与隐私；系统与诊断收「系统状态与诊断」。
    const columns = document.querySelectorAll('.os-settings-map-col')
    const bySection: Record<string, string[]> = {}
    columns.forEach((column) => {
      const title = column.querySelector('.os-settings-map-title')?.textContent ?? ''
      bySection[title] = [...column.querySelectorAll('button')].map(
        (button) => button.textContent ?? '',
      )
    })
    expect(bySection['账号与成员']).toEqual(['我的账号与安全'])
    expect(bySection['安全与审计']).toEqual([
      '审批签发',
      '备份与迁移',
      '通知中心',
      '生产预检',
      '关于与隐私',
    ])
    expect(bySection['系统与诊断']).toEqual(['系统状态与诊断'])
  })

  test('根账号的目录里不出现成员面的「我的账号」', () => {
    renderSettings()
    expect(screen.queryByRole('button', { name: '我的账号' })).toBeNull()
  })
})
