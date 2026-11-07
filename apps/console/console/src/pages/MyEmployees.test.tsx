/**
 * 「我的员工」页的行为契约（对齐后端真实端点）。
 *
 * 四件事必须钉死：
 * 1. 没有员工时给引导，不是空白；
 * 2. 列表把真实字段（role_description / default_capabilities / employment_status）
 *    翻成人话；
 * 3. `read_only=true` 的员工不渲染改名/停用/删除/派活按钮；
 * 4. 创建与停用打到真实 URL + 真实字段，失败不谎报成功。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { MyEmployees } from './MyEmployees'

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

const CAPABILITIES = {
  capabilities: [{ id: 'writer', name: '写作', tool_id: 'write_file', description: '', risk: 'low' }],
  count: 1,
  ids: ['writer'],
}

function stubFetch(
  employees: unknown[],
  options: { createStatus?: number; patchStatus?: number } = {},
) {
  const calls: Array<{ url: string; method: string; body: unknown }> = []
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    let body: unknown = null
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    calls.push({ url, method, body })
    if (url.includes('/v1/employees/capabilities')) return jsonResponse(CAPABILITIES)
    if (method === 'POST') {
      return jsonResponse(
        options.createStatus && options.createStatus >= 400
          ? { detail: { code: 'employee_exists', message: '同名员工已存在' } }
          : { name: '小周' },
        options.createStatus ?? 201,
      )
    }
    if (method === 'PATCH') {
      return jsonResponse(
        options.patchStatus && options.patchStatus >= 400
          ? { detail: { code: 'employee_disabled', message: '员工已停用' } }
          : { name: '小周' },
        options.patchStatus ?? 200,
      )
    }
    return jsonResponse({ employees, count: employees.length })
  })
  return calls
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('我的员工', () => {
  test('按真实字段列出职责、默认能力、状态与计数', async () => {
    stubFetch([
      {
        name: '小周',
        agent_count: 1,
        role_description: '整理会议纪要与周报',
        default_capabilities: ['writer'],
        employment_status: 'active',
        read_only: false,
        agents: [
          {
            id: 'agent-1',
            agent_type: 'executor',
            name: '小周的执行体',
            status: 'idle',
            current_task: null,
            completed_tasks: 2,
            failed_tasks: 0,
            total_latency_ms: 0,
          },
        ],
        stats: {
          name: '小周',
          agent_count: 1,
          total_tasks_submitted: 2,
          total_tasks_completed: 2,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      },
    ])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('小周')).toBeTruthy())
    expect(screen.getByText(/职责：整理会议纪要与周报/)).toBeTruthy()
    expect(screen.getByText(/默认能力：写作/)).toBeTruthy()
    expect(screen.getByText(/提交 2 项 · 完成 2 · 失败 0/)).toBeTruthy()
    expect(screen.getAllByText('空闲').length).toBeGreaterThan(0)
  })

  test('执行个体行只展示序号与状态，原始标识只留在 title', async () => {
    stubFetch([
      {
        name: '小周',
        agent_count: 2,
        role_description: '整理纪要',
        default_capabilities: [],
        employment_status: 'active',
        read_only: false,
        agents: [
          {
            id: 'agent-1',
            agent_type: 'planner',
            name: 'liuhao-default·planner-0',
            status: 'idle',
            current_task: null,
            completed_tasks: 0,
            failed_tasks: 0,
            total_latency_ms: 0,
          },
          {
            id: 'agent-2',
            agent_type: 'executor',
            name: 'liuhao-default·executor-1',
            status: 'idle',
            current_task: null,
            completed_tasks: 0,
            failed_tasks: 0,
            total_latency_ms: 0,
          },
        ],
        stats: {
          name: '小周',
          agent_count: 2,
          total_tasks_submitted: 0,
          total_tasks_completed: 0,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      },
    ])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('小周')).toBeTruthy())

    const text = document.body.textContent ?? ''
    expect(text).toContain('执行个体 1')
    expect(text).toContain('执行个体 2')
    expect(text).not.toContain('planner-0')
    expect(text).not.toContain('executor-1')
    expect(text).not.toContain('liuhao-default')

    expect(screen.getByText('执行个体 1·空闲').textContent).toBe('执行个体 1·空闲')
    expect(screen.getByText('执行个体 2·空闲').textContent).toBe('执行个体 2·空闲')
    // 内部标识连 title/aria 线索都不留：界面上完全不出现。
    expect(document.querySelector('[title*="planner"]')).toBeNull()
    expect(document.querySelector('[title*="liuhao-default"]')).toBeNull()
  })

  test('系统默认员工不显示内部标识，只显示人话称呼与系统默认徽标', async () => {
    stubFetch([
      {
        name: 'liuhao-default',
        agent_count: 3,
        role_description: '本机默认管家员工：拆解目标并调度本地能力',
        default_capabilities: [],
        employment_status: 'active',
        read_only: false,
        agents: [
          {
            id: 'a1',
            agent_type: 'planner',
            name: 'liuhao-default-planner-0',
            status: 'idle',
          },
        ],
        stats: {
          name: 'liuhao-default',
          agent_count: 3,
          total_tasks_submitted: 0,
          total_tasks_completed: 0,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      },
    ])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('默认管家')).toBeTruthy())
    expect(screen.getByText('系统默认')).toBeTruthy()
    const text = document.body.textContent ?? ''
    expect(text).not.toContain('liuhao-default')
    // 内部标识不再作为 title 排查线索暴露在界面上。
    expect(screen.getByText('默认管家').getAttribute('title')).toBeNull()
    expect(document.querySelector('[title*="liuhao-default"]')).toBeNull()
  })

  test('展示后端给的状态与"最近一次做了什么、什么时候"', async () => {
    stubFetch([
      {
        name: '小周',
        display_name: '小周',
        agent_count: 1,
        role_description: '整理客户反馈',
        default_capabilities: [],
        employment_status: 'active',
        read_only: false,
        status: 'completed',
        status_label: '已完成',
        last_activity: {
          description: '整理本周客户反馈',
          outcome: 'completed',
          outcome_label: '已完成',
          at: '2026-11-08T10:00:00+00:00',
        },
        agents: [
          { id: 'a1', agent_type: 'general', name: 'raw-agent-0', status: 'idle' },
        ],
        stats: {
          name: '小周',
          agent_count: 1,
          total_tasks_submitted: 1,
          total_tasks_completed: 1,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      },
    ])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('小周')).toBeTruthy())
    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0)
    const text = document.body.textContent ?? ''
    expect(text).toContain('最近一次：已完成：整理本周客户反馈')
    expect(text).not.toContain('raw-agent-0')
  })

  test('没有员工时给出创建引导，而不是空白页', async () => {
    stubFetch([])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('还没有员工')).toBeTruthy())
    expect(screen.getByText(/先在上面创建第一个员工/)).toBeTruthy()
  })

  test('read_only 员工只展示，不渲染改名/停用/删除/派活按钮', async () => {
    stubFetch([
      {
        name: '别人家的员工',
        agent_count: 1,
        role_description: '整理纪要',
        default_capabilities: ['writer'],
        employment_status: 'active',
        read_only: true,
        agents: [],
        stats: {
          name: '别人家的员工',
          agent_count: 1,
          total_tasks_submitted: 0,
          total_tasks_completed: 0,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      },
    ])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('别人家的员工')).toBeTruthy())
    expect(screen.getByText('只读')).toBeTruthy()
    expect(screen.queryByText('改名')).toBeNull()
    expect(screen.queryByText('停用')).toBeNull()
    expect(screen.queryByText('删除')).toBeNull()
    expect(screen.queryByText('单独指派一个任务')).toBeNull()
  })

  test('创建打到真实 URL 与真实字段', async () => {
    const calls = stubFetch([])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('还没有员工')).toBeTruthy())
    const nameInput = screen.getByLabelText(/名字（必填）/) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: '小周' } })
    fireEvent.change(screen.getByLabelText('职责'), {
      target: { value: '整理会议纪要' },
    })
    await waitFor(() => expect(screen.getByLabelText('写作')).toBeTruthy())
    fireEvent.click(screen.getByLabelText('写作'))
    screen.getByText('创建员工', { selector: 'button' }).click()

    await waitFor(() => {
      const create = calls.find(
        (call) => call.method === 'POST' && call.url === '/v1/employees',
      )
      expect(create).toBeTruthy()
      expect(create?.body).toMatchObject({
        name: '小周',
        role_description: '整理会议纪要',
        default_capabilities: ['writer'],
      })
    })
  })

  test('停用打到真实字段 employment_status=disabled', async () => {
    const calls = stubFetch([
      {
        name: '小周',
        agent_count: 1,
        role_description: '整理纪要',
        default_capabilities: [],
        employment_status: 'active',
        read_only: false,
        agents: [{ id: 'a1', agent_type: 'executor', name: '执行体', status: 'idle' }],
        stats: {
          name: '小周',
          agent_count: 1,
          total_tasks_submitted: 0,
          total_tasks_completed: 0,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      },
    ])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('小周')).toBeTruthy())
    screen.getByText('停用', { selector: 'button' }).click()
    await waitFor(() => {
      const patch = calls.find(
        (call) => call.method === 'PATCH' && call.url === '/v1/employees/%E5%B0%8F%E5%91%A8',
      )
      expect(patch?.body).toMatchObject({ employment_status: 'disabled' })
    })
  })

  test('文案不含层级与归属词（子账号视角同样干净）', async () => {
    stubFetch([
      {
        name: '小周',
        agent_count: 1,
        role_description: '整理纪要',
        default_capabilities: ['writer'],
        employment_status: 'active',
        read_only: false,
        agents: [],
        stats: {
          name: '小周',
          agent_count: 1,
          total_tasks_submitted: 0,
          total_tasks_completed: 0,
          total_tasks_failed: 0,
          task_queue_length: 0,
        },
      },
    ])
    render(<MyEmployees query="" />)
    await waitFor(() => expect(screen.getByText('小周')).toBeTruthy())
    const text = document.body.textContent ?? ''
    for (const term of ['子账号', '上级', '下级', '层级', '主账号', '成员', '邀请', '团队', '组织']) {
      expect(text).not.toContain(term)
    }
  })
})
