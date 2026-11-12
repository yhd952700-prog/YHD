/**
 * 员工名册的规模控制：接口仍是一次返回全量，渲染层只切一页。
 * 这些断言只覆盖「1000 条不一次性进 DOM」与「搜索后回第 1 页」。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MyEmployees } from './MyEmployees'

const TOTAL = 120

function employee(i: number) {
  const name = `员工${String(i).padStart(4, '0')}`
  return {
    name,
    agent_count: 0,
    agents: [],
    role_description: '整理周报',
    default_capabilities: [],
    employment_status: 'active',
    read_only: false,
    stats: {
      name,
      agent_count: 0,
      total_tasks_submitted: 0,
      total_tasks_completed: 0,
      total_tasks_failed: 0,
      task_queue_length: 0,
    },
  }
}

const employees = Array.from({ length: TOTAL }, (_, i) => employee(i))

function json(data: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Headers(),
  } as unknown as Response
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/v1/employees/capabilities')) {
        return json({ capabilities: [], count: 0, ids: [] })
      }
      return json({
        agents: [],
        count: TOTAL,
        stats: {
          name: 'default',
          agent_count: 0,
          total_tasks_submitted: 0,
          total_tasks_completed: 0,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
        employees,
        employee_count: TOTAL,
      })
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('员工名册分页', () => {
  it('接口全量返回时只渲染 50 条一页，翻页与搜索回第一页', async () => {
    const view = render(<MyEmployees query="" />)

    await waitFor(() => expect(screen.getByText('员工0000')).toBeTruthy())
    expect(screen.getByText('员工0049')).toBeTruthy()
    expect(screen.queryByText('员工0050')).toBeNull()
    expect(screen.getByText(/第 1 \/ 3 页 · 共 120 位/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(screen.getByText('员工0050')).toBeTruthy())
    expect(screen.queryByText('员工0000')).toBeNull()

    // 搜索后必须回第 1 页：只匹配 1 个员工时停在旧页会渲染空白。
    view.rerender(<MyEmployees query="员工0119" />)
    await waitFor(() => expect(screen.getByText('员工0119')).toBeTruthy())
    expect(screen.queryByText('员工0050')).toBeNull()
    // 只剩 1 个命中时不足一页，分页条自然消失；关键是命中必须可见。
    expect(screen.queryByRole('button', { name: '下一页' })).toBeNull()
  })
})
