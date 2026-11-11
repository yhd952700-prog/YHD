import { afterEach, describe, expect, test, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { EnterpriseOverview } from './EnterpriseOverview'

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

const today = new Date()
const todayIso = new Date(
  today.getFullYear(),
  today.getMonth(),
  today.getDate(),
  9,
  30,
  0,
).toISOString()
const yesterdayIso = new Date(
  today.getFullYear(),
  today.getMonth(),
  today.getDate() - 1,
  22,
  0,
  0,
).toISOString()

const EMPLOYEES = {
  employees: [
    { name: 'Alice', employment_status: 'active', agents: [{ status: 'running' }, { status: 'idle' }] },
    { name: 'Bob', employment_status: 'active', agents: [{ status: 'idle' }] },
    { name: 'Carol', employment_status: 'disabled', agents: [] },
  ],
  employee_count: 3,
  agents: [],
}

const ORG = {
  roots: [
    {
      node_id: 'rd',
      name: '研发部',
      kind: 'department',
      assignments: [
        { subject_type: 'employee', subject_id: 'Alice', subject_label: 'Alice' },
        { subject_type: 'employee', subject_id: 'Bob', subject_label: 'Bob' },
      ],
      children: [
        {
          node_id: 'ops',
          name: '运营组',
          kind: 'team',
          assignments: [
            { subject_type: 'employee', subject_id: 'Carol', subject_label: 'Carol' },
          ],
          children: [],
        },
      ],
    },
  ],
  node_count: 2,
  assignment_count: 3,
}

function task(
  id: string,
  title: string,
  status: string,
  assignee: { type: string; name: string } | null,
  extra: Record<string, unknown> = {},
) {
  return {
    task_id: id,
    goal_id: 'g1',
    title,
    status,
    assignee,
    created_at: todayIso,
    started_at: todayIso,
    completed_at: null,
    updated_at: todayIso,
    human_hold: false,
    error: null,
    ...extra,
  }
}

const RUNNING = {
  total: 1,
  limit: 5,
  offset: 0,
  items: [task('t-run', '整理本周客户回访', 'running', { type: 'employee', name: 'Alice' })],
}

const COMPLETED = {
  total: 3,
  limit: 200,
  offset: 0,
  items: [
    task('c1', '整理本周客户回访', 'completed', { type: 'employee', name: 'Alice' }, {
      completed_at: todayIso,
    }),
    task('c2', '生成月度销售报表', 'completed', { type: 'employee', name: 'Bob' }, {
      completed_at: yesterdayIso,
    }),
    task('c3', '旧任务缺少完成时间', 'completed', { type: 'employee', name: 'Carol' }, {
      completed_at: null,
    }),
  ],
}

const FAILED = {
  total: 1,
  limit: 5,
  offset: 0,
  items: [
    task('f1', '抓取竞品价格', 'failed', { type: 'employee', name: 'Bob' }, {
      error: '模型调用超时',
      human_hold: true,
    }),
  ],
}

const RECENT = {
  total: 5,
  limit: 200,
  offset: 0,
  items: [
    task('c1', '整理本周客户回访', 'completed', { type: 'employee', name: 'Alice' }, {
      completed_at: todayIso,
    }),
    task('c-split', '联合拜访客户', 'running', { type: 'employee', name: 'Alice、Carol' }),
    task('f1', '抓取竞品价格', 'failed', { type: 'employee', name: 'Bob' }, {
      error: '模型调用超时',
      human_hold: true,
    }),
    task('r1', '整理本周客户回访', 'running', { type: 'employee', name: 'Alice' }),
    task('u1', '未派单的待办', 'pending', null),
  ],
}

const APPROVALS = {
  count: 1,
  items: [
    {
      pending_id: 'p1',
      action: 'shell.exec',
      risk_level: 'HIGH',
      source_channel: 'local',
      blocked_at: Math.floor(Date.now() / 1000) - 120,
      blocked_by: 'Alice',
      reason: '命令包含删除参数',
      summary: '执行高危命令等待确认',
    },
  ],
}

const USAGE = {
  principal: 'owner',
  scope: 'self',
  callers_visible: true,
  calls: 12,
  tokens_in: 1000,
  tokens_out: 2456,
  total_tokens: 3456,
  by_key_owner: [],
  cost: 0.12,
  cost_complete: true,
  by_model: [
    {
      model: 'demo-model',
      tokens_in: 1000,
      tokens_out: 2456,
      total_tokens: 3456,
      cost: 0.12,
      pricing_status: 'priced',
      message: '',
    },
  ],
}

type Override = { status?: number; body?: unknown }

function stubApi(overrides: Record<string, Override> = {}) {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = new URL(String(input), 'http://localhost')
    const status = url.searchParams.get('status')
    const key =
      url.pathname === '/v1/tasks'
        ? `tasks:${status ?? 'recent'}`
        : url.pathname === '/v1/policy/approvals/pending'
          ? 'approvals'
          : url.pathname === '/v1/employees'
            ? 'employees'
            : url.pathname === '/v1/org/tree'
              ? 'org'
              : url.pathname.startsWith('/v1/accounts/') && url.pathname.endsWith('/usage')
                ? 'usage'
                : `${url.pathname}${url.search}`
    const override = overrides[key]
    if (override) return jsonResponse(override.body ?? {}, override.status ?? 200)
    switch (key) {
      case 'employees':
        return jsonResponse(EMPLOYEES)
      case 'org':
        return jsonResponse(ORG)
      case 'tasks:running':
        return jsonResponse(RUNNING)
      case 'tasks:completed':
        return jsonResponse(COMPLETED)
      case 'tasks:failed':
        return jsonResponse(FAILED)
      case 'tasks:recent':
        return jsonResponse(RECENT)
      case 'approvals':
        return jsonResponse(APPROVALS)
      case 'usage':
        return jsonResponse(USAGE)
      default:
        return jsonResponse({ detail: 'not found' }, 404)
    }
  })
}

function metric(label: string): HTMLElement {
  const element = screen.getByText(label).closest('.os-kpi')
  if (!(element instanceof HTMLElement)) throw new Error(`找不到指标卡：${label}`)
  return element
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('EnterpriseOverview', () => {
  test('用真实接口字段渲染企业概况、任务、审批、部门与用量', async () => {
    stubApi()
    render(<EnterpriseOverview principal="owner" />)

    expect(await screen.findByText('研发部')).toBeTruthy()

    expect(metric('组织节点').textContent).toContain('2')
    expect(metric('员工总数').textContent).toContain('3')
    expect(metric('在线 / 暂停').textContent).toContain('2 / 1')
    expect(metric('执行中执行器').textContent).toContain('1')

    expect(metric('执行中任务').textContent).toContain('1')
    expect(metric('今日完成').textContent).toContain('1')
    expect(metric('今日完成').textContent).toContain('缺少完成时间')
    expect(metric('失败任务').textContent).toContain('1')
    expect(metric('人工挂起').textContent).toContain('1')

    expect(screen.getByText('执行高危命令等待确认')).toBeTruthy()

    const rd = screen.getByText('研发部').closest('.eov-org-row')
    const ops = screen.getByText('运营组').closest('.eov-org-row')
    expect(rd?.textContent).toContain('本节点 4 项')
    expect(ops?.textContent).toContain('本节点 1 项')

    expect(metric('调用次数').textContent).toContain('12')
    expect(metric('总词元').textContent).toContain('3456')
    expect(metric('估算成本').textContent).toContain('0.12')
    expect(screen.getByText('demo-model')).toBeTruthy()
  })

  test('待审批 403 时显示「你无权查看」而不是空列表', async () => {
    stubApi({ approvals: { status: 403, body: { detail: 'forbidden' } } })
    render(<EnterpriseOverview principal="owner" />)

    expect(await screen.findByText('你无权查看')).toBeTruthy()
    expect(screen.queryByText('当前没有待审批事项')).toBeNull()
  })

  test('任务台账不可用时明确写「暂未提供」并带原因', async () => {
    stubApi({
      'tasks:running': { status: 404, body: { detail: '任务台账未挂载' } },
      'tasks:completed': { status: 404, body: { detail: '任务台账未挂载' } },
      'tasks:failed': { status: 404, body: { detail: '任务台账未挂载' } },
      'tasks:recent': { status: 404, body: { detail: '任务台账未挂载' } },
    })
    render(<EnterpriseOverview principal="owner" />)

    expect(await screen.findByText('任务数据暂未提供')).toBeTruthy()
    expect(screen.getAllByText(/任务台账读取失败/).length).toBeGreaterThan(0)
  })

  test('用量接口未挂载时写「暂未接入」并说明原因', async () => {
    stubApi({ usage: { status: 404, body: { detail: 'not found' } } })
    render(<EnterpriseOverview principal="owner" />)

    expect(await screen.findByText('暂未接入')).toBeTruthy()
    expect(screen.getByText(/没有挂载用量接口/)).toBeTruthy()
  })

  test('概况卡给出「进入办公室」「问贾维斯」两个入口（手机端没有侧栏）', () => {
    stubApi({})
    const onNavigate = vi.fn()
    render(<EnterpriseOverview principal="owner" onNavigate={onNavigate} />)

    fireEvent.click(screen.getByRole('button', { name: '问贾维斯' }))
    expect(onNavigate).toHaveBeenCalledWith('butler')
    fireEvent.click(screen.getByRole('button', { name: '进入办公室' }))
    expect(onNavigate).toHaveBeenCalledWith('office')
  })
})
