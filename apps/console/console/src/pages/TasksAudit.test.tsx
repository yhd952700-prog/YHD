/**
 * 全部任务与审计合并页：默认标签正确、点另一标签切换内容。
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { AccountProfile } from '../lib/accountCaps'
import { deriveCaps } from '../lib/accountCaps'
import { TasksAudit } from './TasksAudit'

function reply(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).split('?')[0]
      if (path === '/v1/owner/tasks') {
        return reply({ goal_count: 0, task_count: 0, owners: [], goals: [] })
      }
      if (path === '/v1/audit/summary') return reply({ total_events: 0 })
      if (path === '/v1/audit/events') return reply({ events: [], truncated: false })
      if (path === '/v1/events') return reply({ events: [], count: 0, limit: 50 })
      if (path === '/v1/dashboard/analytics') {
        return reply({
          timeseries: { available: false, error: '当前没有数据' },
          breakdown: { available: false, items: [], error: '当前没有数据' },
        })
      }
      if (path === '/v1/dashboard/summary') {
        return reply({ sessions: { count: 0 }, audit: { total_events: 0 } })
      }
      return reply({})
    }),
  )
}

const rootProfile: AccountProfile = {
  principal: 'root',
  displayName: null,
  scope: null,
  identityPermissions: [],
  accountPermissions: [],
  isRoot: true,
  isOwner: true,
  canSelfRecover: true,
  members: [],
  memberCount: 0,
  source: 'live',
  loaded: true,
  error: null,
}

const memberProfile: AccountProfile = {
  ...rootProfile,
  principal: 'member',
  isRoot: false,
  isOwner: false,
}

describe('全部任务与审计', () => {
  beforeEach(() => {
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('默认打开全部任务标签', async () => {
    render(<TasksAudit query="" profile={rootProfile} isRoot caps={deriveCaps(rootProfile)} />)

    expect(screen.getByRole('tab', { name: '全部任务' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    await screen.findByText('还没有任务')
  })

  test('成员看不到「全部任务」标签，默认落在审计；initialTab=tasks 也回落到审计', async () => {
    render(
      <TasksAudit
        query=""
        profile={memberProfile}
        isRoot={false}
        caps={deriveCaps(memberProfile)}
        initialTab="tasks"
      />,
    )

    expect(screen.queryByRole('tab', { name: '全部任务' })).toBeNull()
    expect(screen.getByRole('tab', { name: '审计' }).getAttribute('aria-selected')).toBe('true')
    // 成员的审计页没有「记录完整性」卡（仅根可见），空态是「还没有操作记录」。
    await screen.findByText('还没有操作记录')
    // 成员根本不会调 Owner 专属端点 /v1/owner/tasks。
    expect(
      (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(([url]) =>
        String(url).includes('/v1/owner/tasks'),
      ),
    ).toBe(false)
  })

  test('点审计切换内容，全部任务内容消失', async () => {
    render(<TasksAudit query="" profile={rootProfile} isRoot caps={deriveCaps(rootProfile)} />)
    await screen.findByText('还没有任务')

    fireEvent.click(screen.getByRole('tab', { name: '审计' }))

    expect(screen.getByRole('tab', { name: '审计' }).getAttribute('aria-selected')).toBe('true')
    await screen.findByText('记录完整性')
    expect(screen.queryByText('还没有任务')).toBeNull()
  })

  test('点事件流、数据中心各切换到对应内容', async () => {
    render(<TasksAudit query="" profile={rootProfile} isRoot caps={deriveCaps(rootProfile)} />)
    await screen.findByText('还没有任务')

    fireEvent.click(screen.getByRole('tab', { name: '事件流' }))
    expect((await screen.findAllByText('暂无事件')).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('tab', { name: '数据中心' }))
    await screen.findByText('审计事件趋势')
  })

  test('initialTab=events 直接打开事件流标签', async () => {
    render(
      <TasksAudit
        query=""
        profile={rootProfile}
        isRoot
        caps={deriveCaps(rootProfile)}
        initialTab="events"
      />,
    )

    expect(screen.getByRole('tab', { name: '事件流' }).getAttribute('aria-selected')).toBe(
      'true',
    )
    expect((await screen.findAllByText('暂无事件')).length).toBeGreaterThan(0)
  })
})
