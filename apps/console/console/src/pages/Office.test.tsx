/**
 * 办公室工位墙：真实字段怎么渲染、缺字段怎么说话。
 *
 * 钉住三件事：
 *   1. 状态与"最近活动"用后端字段，不自己编；
 *   2. 没有归属的任务不硬塞给员工，显示「暂未提供」；
 *   3. 点工位打开抽屉，派活走 POST /v1/goals/assign。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Office } from './Office'

function json(data: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Headers(),
  } as unknown as Response
}

function employee(overrides: Record<string, unknown>) {
  const name = String(overrides.name)
  return {
    name,
    agent_count: 1,
    agents: [{ id: `${name}-agent`, name, status: 'idle' }],
    role_description: '整理周报',
    default_capabilities: [],
    employment_status: 'active',
    read_only: false,
    status: 'idle',
    status_label: '空闲',
    last_activity: null,
    stats: {
      name,
      agent_count: 1,
      total_tasks_submitted: 0,
      total_tasks_completed: 0,
      total_tasks_failed: 0,
      task_queue_length: 0,
    },
    ...overrides,
  }
}

const EMPLOYEES = [
  employee({
    name: '小市',
    display_name: '小市',
    status: 'running',
    last_activity: { description: '完成竞品周报', at: '2026-11-10T09:00:00Z' },
  }),
  employee({ name: '小文', display_name: '小文', status: 'idle' }),
  employee({
    name: '小财',
    display_name: '小财',
    status: 'error',
    last_activity: { description: '对账失败', outcome_label: '失败', at: '2026-11-10T08:00:00Z' },
  }),
]

const RUNNING_TASK = {
  task_id: 't1',
  goal_id: 'g1',
  title: '整理上周销售数据',
  description: '',
  status: 'running',
  status_source: 'agent',
  assignee: { type: 'agent', id: '小市-agent', name: '小市' },
  owner_account_id: 'acc',
  project_id: null,
  artifacts: [],
  error: null,
  technical_detail: null,
  created_at: null,
  started_at: null,
  completed_at: null,
  updated_at: null,
  attempt_count: 1,
  archived: false,
  human_hold: false,
  source: 'butler',
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/v1/employees')) {
        return json({ agents: [], count: 3, employees: EMPLOYEES, employee_count: 3 })
      }
      if (url.includes('/v1/tasks') && url.includes('status=running')) {
        return json({ total: 1, limit: 50, offset: 0, items: [RUNNING_TASK] })
      }
      if (url.includes('/v1/tasks')) {
        return json({ total: 0, limit: 20, offset: 0, items: [] })
      }
      if (url.includes('/v1/policy/approvals/pending')) {
        return json({ count: 2, items: [], as_of: null, lookback_seconds: 0, enforcement: {} })
      }
      if (url.includes('/v1/goals/assign') && init?.method === 'POST') {
        return json({ goal_id: 'g2', task_ids: [], employee_name: '小市' })
      }
      return json({})
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('办公室工位墙', () => {
  it('渲染员工状态、当前任务与整体计数；没有归属的任务不硬塞', async () => {
    render(<Office canApproveQueue />)

    await waitFor(() => expect(screen.getByText('小市')).toBeTruthy())
    expect(screen.getByText('小文')).toBeTruthy()
    expect(screen.getByText('小财')).toBeTruthy()

    // 小市有一条 running 任务，assignee 指向它的 agent。
    expect(screen.getByText('整理上周销售数据')).toBeTruthy()
    // 小财在出错，但没有分配到的任务 → 如实说未提供，而不是随便挑一条。
    expect(screen.getAllByText('暂未提供').length).toBeGreaterThan(0)

    // 整体状态：1 位在干活、2 件等你确认。按统计项取值，避免和工位卡里的数字撞。
    expect(screen.getByText('位在干活').closest('.os-office-stat')?.textContent).toContain('1')
    expect(screen.getByText(/件等你确认/).closest('.os-office-stat')?.textContent).toContain('2')
    // 暂停语义写在界面上，避免被理解成"删员工"。
    expect(screen.getByText('「暂停」是停接活，不是删员工')).toBeTruthy()
  })

  it('点工位打开抽屉，派活走指名接口', async () => {
    render(<Office canApproveQueue />)
    await waitFor(() => expect(screen.getByText('小文')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('打开 小文 的派活面板'))
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('小文')

    fireEvent.change(screen.getByLabelText('派一件事给它'), {
      target: { value: '把本周会议纪要归档' },
    })
    fireEvent.click(screen.getByRole('button', { name: '派活' }))

    await waitFor(() => {
      const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(([url]) =>
        String(url).includes('/v1/goals/assign'),
      )
      expect(call).toBeTruthy()
      expect(JSON.parse(String(call![1]?.body))).toMatchObject({
        employee_name: '小文',
        instruction: '把本周会议纪要归档',
      })
    })
  })
})
