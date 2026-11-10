/**
 * 记忆中心：长期记忆条目可定位、按员工可查、按项目如实说明缺口、
 * 无权限不显示成空、长期记忆与正式业务数据在页面上分开。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AccountProfile } from '../lib/accountCaps'
import { MemoryCenter } from './MemoryCenter'

const PROFILE: AccountProfile = {
  principal: 'owner',
  displayName: '我',
  scope: null,
  identityPermissions: [],
  accountPermissions: [],
  isRoot: true,
  canSelfRecover: false,
  members: [],
  memberCount: 0,
  source: 'live',
  loaded: true,
  error: null,
}

const EMPLOYEES = {
  agents: [],
  count: 2,
  stats: {},
  employees: [
    { name: 'Alice', display_name: '爱丽丝', agent_count: 1, agents: [] },
    { name: 'Bob', display_name: '鲍勃', agent_count: 0, agents: [] },
  ],
  employee_count: 2,
}

function json(data: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Headers(),
  } as unknown as Response
}

function fail(status: number, detail: string) {
  return {
    ok: false,
    status,
    json: async () => ({ detail }),
    text: async () => JSON.stringify({ detail }),
    headers: new Headers(),
  } as unknown as Response
}

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  if (url.includes('/v1/memory/entries')) {
    return json({
      entries: [
        {
          id: 'm-1',
          account_id: 'owner',
          text: '季度复盘放在每月最后一个周五。',
          source: 'user',
          tags: ['节奏'],
          created_at: '2026-11-10T03:00:00+00:00',
        },
      ],
      scope: 'self',
      count: 1,
      has_more: false,
    })
  }
  if (url.includes('/v1/employees/Alice/memories')) {
    return json({
      employee: { name: 'Alice', display_name: '爱丽丝' },
      owner_account_id: 'owner',
      memories: [
        {
          id: 'm-alice-1',
          account_id: 'owner',
          text: '偏好先看结论，再看过程。',
          source: 'employee',
          tags: [],
          created_at: '2026-11-10T04:00:00+00:00',
        },
      ],
      count: 1,
    })
  }
  if (url.includes('/v1/employees/Bob/memories')) {
    return fail(404, '员工不存在或不在当前可见范围')
  }
  if (url.endsWith('/v1/employees')) return json(EMPLOYEES)
  if (url.includes('/v1/projects')) {
    return json({
      projects: [
        {
          project_id: 'p-1',
          name: '秋季发布',
          description: '',
          created_at: 1762400000,
          archived_at: null,
          creator: 'owner',
          goal_ids: [],
          goal_count: 2,
          last_activity_at: 1762400000,
          output_count: 0,
          outputs: [],
          outputs_truncated: false,
        },
      ],
      count: 1,
      view_account: null,
    })
  }
  return json({})
})

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  fetchMock.mockClear()
})

describe('记忆中心', () => {
  it('长期记忆显示条目正文、来源、时间与条目 id，并与正式业务数据分开陈述', async () => {
    render(<MemoryCenter profile={PROFILE} />)
    await waitFor(() => expect(screen.getByText('季度复盘放在每月最后一个周五。')).toBeTruthy())
    expect(screen.getByText('来源：用户自述')).toBeTruthy()
    expect(screen.getByText('条目 m-1')).toBeTruthy()

    expect(screen.getByText('数据边界：长期记忆 ≠ 正式业务数据')).toBeTruthy()
    expect(screen.getByText('正式业务数据')).toBeTruthy()
    expect(screen.getByText('account_memory_entries')).toBeTruthy()
  })

  it('按员工查看记忆；无权查看的员工显示无权限而不是空列表', async () => {
    render(<MemoryCenter profile={PROFILE} />)

    fireEvent.click(screen.getByRole('tab', { name: '按员工' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '爱丽丝' })).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '爱丽丝' }))
    await waitFor(() => expect(screen.getByText('偏好先看结论，再看过程。')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '鲍勃' }))
    await waitFor(() => expect(screen.getByText('无权限查看「Bob」的记忆')).toBeTruthy())
    expect(screen.queryByText('这位员工还没有记忆条目')).toBeNull()
  })

  it('按项目视图如实说明缺接口，并保留真实项目列表', async () => {
    render(<MemoryCenter profile={PROFILE} />)

    fireEvent.click(screen.getByRole('tab', { name: '按项目' }))
    await waitFor(() => expect(screen.getByText('秋季发布')).toBeTruthy())

    expect(screen.getByText('「按项目看记忆」还没有接口')).toBeTruthy()
    expect(screen.getAllByText('本地服务还没提供').length).toBeGreaterThan(0)
    expect(screen.getByText('项目记忆')).toBeTruthy()
    expect(screen.getByText(/按项目返回记忆的接口/)).toBeTruthy()
  })
})
