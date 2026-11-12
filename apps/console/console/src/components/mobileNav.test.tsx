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
  test('底栏七项，首项是企业总览、第二项是贾维斯且可点（Boss 决定）', () => {
    const { onNavigate } = renderMobile('enterprise-overview')
    const nav = document.querySelector('.os-bottom-nav')
    expect(nav).toBeTruthy()

    const buttons = nav!.querySelectorAll<HTMLButtonElement>('.os-bottom-item')
    expect(buttons).toHaveLength(7)
    expect(buttons[0].getAttribute('data-key')).toBe('enterprise-overview')
    expect(buttons[0].textContent).toContain('企业总览')

    // 贾维斯固定在第二项，且是真实可点的入口。
    expect(buttons[1].getAttribute('data-key')).toBe('butler')
    expect(buttons[1].textContent).toContain('贾维斯')
    expect(buttons[1].disabled).toBe(false)
    buttons[1].click()
    expect(onNavigate).toHaveBeenCalledWith('butler')

    // 办公室、任务也要有真实入口，避免"回执能看到、结果打不开"。
    const office = nav!.querySelector<HTMLButtonElement>('[data-key="office"]')
    const task = nav!.querySelector<HTMLButtonElement>('[data-key="task-center"]')
    expect(office).toBeTruthy()
    expect(task).toBeTruthy()
    expect(task!.disabled).toBe(false)

    // 第六项是能力中心（简约版合并页），第七项是系统设置。
    expect(buttons[5].getAttribute('data-key')).toBe('capability-center')
    expect(buttons[5].textContent).toContain('能力中心')
    expect(buttons[5].disabled).toBe(false)
    expect(buttons[6].getAttribute('data-key')).toBe('settings')

    task!.click()
    expect(onNavigate).toHaveBeenCalledWith('task-center')
  })

  test('当前页是任务中心时，入口带 aria-current=page', () => {
    renderMobile('task-center')
    const task = document.querySelector<HTMLButtonElement>(
      '.os-bottom-nav [data-key="task-center"]',
    )
    expect(task).toBeTruthy()
    expect(task!.getAttribute('aria-current')).toBe('page')
  })
})
