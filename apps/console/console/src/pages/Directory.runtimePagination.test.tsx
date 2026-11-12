/**
 * 能力注册表「运行时」Tab 的规模控制：员工与执行体各按 50 条一页渲染。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Roster } from './Directory'

const TOTAL = 120

function employee(i: number) {
  return {
    name: `员工${String(i).padStart(4, '0')}`,
    agent_count: 1,
    agents: [
      {
        id: `emp-agent-${i}`,
        agent_type: 'executor',
        name: `员工执行体${i}`,
        status: 'idle',
        current_task: null,
        completed_tasks: 0,
        failed_tasks: 0,
        total_latency_ms: 0,
      },
    ],
    role_description: '',
    default_capabilities: [],
    employment_status: 'active',
    read_only: false,
    stats: {
      name: `员工${String(i).padStart(4, '0')}`,
      agent_count: 1,
      total_tasks_submitted: 0,
      total_tasks_completed: 0,
      total_tasks_failed: 0,
      task_queue_length: 0,
    },
  }
}

const employees = Array.from({ length: TOTAL }, (_, i) => employee(i))
const agents = Array.from({ length: TOTAL }, (_, i) => ({
  id: `agent-${i}`,
  agent_type: 'executor',
  name: `执行体${String(i).padStart(4, '0')}`,
  status: 'idle',
  current_task: null,
  completed_tasks: 0,
  failed_tasks: 0,
  total_latency_ms: 0,
}))

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
      if (url.includes('/v1/dashboard/roster')) {
        return json({
          available: true,
          totals: { kernels: 1, layers: 1, registry_entries: 2, employees: TOTAL },
          kernels: [],
          layers: [],
          domains: [],
          bands: [],
          providers: {
            registered_count: 0,
            configured_count: 0,
            active: null,
            items: [],
          },
        })
      }
      if (url.includes('/v1/goals')) return json({ goals: [], count: 0 })
      return json({
        agents,
        count: TOTAL,
        stats: {
          name: 'default',
          agent_count: TOTAL,
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

describe('运行时 Tab 分页', () => {
  it('员工与执行体各只渲染 50 条，翻页后继续', async () => {
    render(<Roster query="" />)
    fireEvent.click(await screen.findByRole('button', { name: /运行时/ }))

    await waitFor(() => expect(screen.getByText('员工0000')).toBeTruthy())
    expect(screen.getByText('员工0049')).toBeTruthy()
    expect(screen.queryByText('员工0050')).toBeNull()
    expect(screen.getByText(/第 1 \/ 3 页 · 共 120 位/)).toBeTruthy()

    expect(screen.getByText('执行体0000')).toBeTruthy()
    expect(screen.queryByText('执行体0050')).toBeNull()
    expect(screen.getByText(/第 1 \/ 3 页 · 共 120 个执行个体/)).toBeTruthy()

    fireEvent.click(screen.getAllByRole('button', { name: '下一页' })[0])
    await waitFor(() => expect(screen.getByText('员工0050')).toBeTruthy())
    expect(screen.queryByText('员工0000')).toBeNull()

    fireEvent.click(screen.getAllByRole('button', { name: '下一页' })[1])
    await waitFor(() => expect(screen.getByText('执行体0050')).toBeTruthy())
    expect(screen.queryByText('执行体0000')).toBeNull()
  })
})
