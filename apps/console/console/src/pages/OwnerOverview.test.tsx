/**
 * Owner 全局总览回归（F 组）。
 *
 * 钉住三件事：
 * 1. 数字与列表来自 `/v1/owner/*` 的真实响应（这里用 fetch mock 喂数，
 *    不直接测后端；后端隔离见 `tests/gateway/test_owner_model.py`）；
 * 2. 没有成员 / 没有任务 / 没有审计事件时显示空态，不编演示数据；
 * 3. 邀请成功后一次性激活码只显示这一次，并带出被邀请人的名字。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { OwnerOverview } from './OwnerOverview'

function json(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

function overviewBody(overrides: Record<string, unknown> = {}) {
  return {
    owner: {
      owner_principal: 'owner1',
      origin: 'first_registration',
      owner_created_at: '2026-11-12T00:00:00',
      migration_notes: [],
    },
    members: { total: 1, by_status: { active: 1 }, count: 1 },
    tasks: { goals: 0, tasks: 0, by_owner: {}, checked: true },
    usage: { checked: true, calls: 10, tokens_in: 100, tokens_out: 200 },
    audit: { events: 3, checked: true },
    ...overrides,
  }
}

function membersBody(members: unknown[]) {
  return { members, count: members.length }
}

function tasksBody(goals: unknown[]) {
  return {
    goals,
    goal_count: goals.length,
    task_count: goals.reduce(
      (sum, goal) => sum + ((goal as { tasks?: unknown[] }).tasks?.length ?? 0),
      0,
    ),
    scope: 'subtree',
    owners: [...new Set(goals.map((goal) => (goal as { owner_account_id: string }).owner_account_id))],
  }
}

function auditBody(events: unknown[]) {
  return {
    events,
    count: events.length,
    read_scope: { mode: 'subtree', principals: ['owner1'], root_view: true },
    truncated: false,
  }
}

let fetchMock: ReturnType<typeof vi.fn>
let overviewResponse: { status: number; body: unknown }
let membersResponse: { status: number; body: unknown }
let tasksResponse: { status: number; body: unknown }
let auditResponse: { status: number; body: unknown }
let inviteResponse: { status: number; body: unknown }

beforeEach(() => {
  overviewResponse = { status: 200, body: overviewBody() }
  membersResponse = {
    status: 200,
    body: membersBody([
      { principal: 'owner1', permissions: [], status: 'active', is_owner: true },
    ]),
  }
  tasksResponse = { status: 200, body: tasksBody([]) }
  auditResponse = { status: 200, body: auditBody([]) }
  inviteResponse = {
    status: 201,
    body: {
      account: { principal: 'member1', permissions: [], status: 'active', is_owner: false },
      activation_code: 'LH-AAAA-BBBB-CCCC-DDDD-EEEE-FFFF',
      notice: '把一次性激活码交给该成员',
    },
  }
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith('/v1/owner/overview')) return json(overviewResponse.status, overviewResponse.body)
    if (url.startsWith('/v1/owner/members') && init?.method === 'POST') {
      return json(inviteResponse.status, inviteResponse.body)
    }
    if (url.startsWith('/v1/owner/members')) return json(membersResponse.status, membersResponse.body)
    if (url.startsWith('/v1/owner/tasks')) return json(tasksResponse.status, tasksResponse.body)
    if (url.startsWith('/v1/owner/audit')) return json(auditResponse.status, auditResponse.body)
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Owner 全局总览', () => {
  test('空态：没有其他成员、没有任务、没有审计事件时如实显示空态', async () => {
    render(<OwnerOverview />)
    await waitFor(() => expect(screen.getByText('只有你自己，还没有邀请任何人')).toBeTruthy())
    expect(screen.getByText('还没有任务')).toBeTruthy()
    expect(screen.getByText('还没有审计事件')).toBeTruthy()
    expect(screen.getByText(/当前 Owner 是/)).toBeTruthy()
    expect(screen.getByText(/首次启动时注册/)).toBeTruthy()
  })

  test('有数据时按账号列出任务与审计，不编演示数据', async () => {
    tasksResponse = {
      status: 200,
      body: tasksBody([
        {
          goal_id: 'g1',
          owner_account_id: 'member1',
          state: 'running',
          title: '成员的目标',
          tasks: [{ task_id: 't1', status: 'running', assignees: [] }],
        },
      ]),
    }
    auditResponse = {
      status: 200,
      body: auditBody([
        {
          seq: 7,
          event_type: 'state_change',
          principal_id: 'owner1',
          timestamp: 1762833600,
          outcome: 'allowed',
          action: 'owner.member.create',
          target: 'member1',
        },
      ]),
    }
    render(<OwnerOverview />)
    await waitFor(() => expect(screen.getByText(/成员的目标/)).toBeTruthy())
    expect(screen.getByText(/owner\.member\.create/)).toBeTruthy()
    expect(screen.queryByText('还没有任务')).toBeNull()
  })

  test('邀请成功后一次性激活码只显示一次', async () => {
    render(<OwnerOverview />)
    await waitFor(() => expect(screen.getByPlaceholderText(/输入用户名/)).toBeTruthy())
    fireEvent.change(screen.getByPlaceholderText(/输入用户名/), {
      target: { value: 'member1' },
    })
    fireEvent.click(screen.getByRole('button', { name: '邀请' }))
    await waitFor(() =>
      expect(screen.getByText(/LH-AAAA-BBBB-CCCC-DDDD-EEEE-FFFF/)).toBeTruthy(),
    )
    expect(screen.getByText(/已邀请 member1/)).toBeTruthy()
    const inviteCall = fetchMock.mock.calls.find(([url, init]) =>
      String(url).startsWith('/v1/owner/members') && (init as RequestInit)?.method === 'POST',
    )
    expect(inviteCall).toBeTruthy()
    expect(JSON.parse(String((inviteCall?.[1] as RequestInit).body))).toMatchObject({
      principal: 'member1',
      role: 'viewer',
    })
  })
})
