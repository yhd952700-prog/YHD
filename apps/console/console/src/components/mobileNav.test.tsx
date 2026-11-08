/**
 * 手机端底栏的渲染契约：目标与任务入口必须真实存在且可点。
 *
 * 只测关键行为，不测像素：窄屏下派活的结果落在目标列表里，底栏没有入口
 * 就等于"回执能看到、结果打不开"。这里断言 DOM 里有一个原生 button，
 * 点它能触发导航，并且当前页有 aria-current 让读屏知道自己在哪。
 */

import { describe, expect, test, vi } from 'vitest'
import { render } from '@testing-library/react'
import { Shell } from './Shell'
import type { Session } from '../lib/auth'
import type { AccountsPayload } from '../lib/accounts'
import { buildProfile, deriveCaps } from '../lib/accountCaps'

const PAYLOAD: AccountsPayload = {
  members: [{ principal: 'root', permissions: ['*'], status: 'active' }],
  count: 1,
}

const SESSION: Session = {
  token: 't',
  principal: 'root',
  displayName: '根',
  scope: 'human',
  permissions: [],
  expiresAt: 0,
  client: 'mobile',
  isRoot: true,
}

const PROFILE = buildProfile(SESSION, PAYLOAD, 'live', null)

function renderMobile(active: string, onNavigate = vi.fn()) {
  render(
    <Shell
      pageTitle="手机底栏测试"
      mode="mobile"
      theme="dark"
      session={SESSION}
      profile={PROFILE}
      caps={deriveCaps(PROFILE)}
      active={active}
      onNavigate={onNavigate}
      onToggleTheme={vi.fn()}
      onLogout={vi.fn()}
      query=""
      onQueryChange={vi.fn()}
      notifications={[]}
    >
      <div>内容</div>
    </Shell>,
  )
  return { onNavigate }
}

describe('手机端底部导航渲染', () => {
  test('底栏有目标与任务入口，是可用按钮且点击触发导航', () => {
    const { onNavigate } = renderMobile('overview')
    const nav = document.querySelector('.os-bottom-nav')
    expect(nav).toBeTruthy()

    const buttons = nav!.querySelectorAll<HTMLButtonElement>('.os-bottom-item')
    expect(buttons).toHaveLength(6)

    const goal = nav!.querySelector<HTMLButtonElement>('[data-key="goals"]')
    expect(goal).toBeTruthy()
    expect(goal!.textContent).toContain('目标')
    expect(goal!.disabled).toBe(false)

    goal!.click()
    expect(onNavigate).toHaveBeenCalledWith('goals')
  })

  test('当前页是目标与任务时，入口带 aria-current=page', () => {
    renderMobile('goals')
    // 侧栏也有「目标与任务」，必须只查底栏那个（手机端侧栏是隐藏的）。
    const goal = document.querySelector<HTMLButtonElement>(
      '.os-bottom-nav [data-key="goals"]',
    )
    expect(goal).toBeTruthy()
    expect(goal!.getAttribute('aria-current')).toBe('page')
  })
})
