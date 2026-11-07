/**
 * 鎏灏管家页面级回归。钉住这一版新增的真实能力：
 *   1. 一句话提交真的发 `POST /v1/goals` 且 `background: true`；
 *   2. 运行中显示增量任务快照，**缺字段/少任务不清空已有卡片**；
 *   3. 结束后任务卡显示员工、状态、耗时，汇总含评估与可点开的产物；
 *   4. 审批类失败给「待确认」；普通失败、审批失败的单任务重试都走真端点，
 *      409 `goal_running` / `task_not_retryable` 文案不同；
 *   5. 单独指派启用并真的发 `POST /v1/goals/assign`，404 / 409 文案不同；
 *   6. 待审批队列来自 `GET /v1/policy/approvals/pending`，批准采用队列返回的
 *      请求体；403 如实说「没有签发权限」而不是「队列为空」。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Butler } from './Butler'
import type { GoalDetail, GoalTask } from '../lib/operator'

const EMPLOYEES = {
  agents: [],
  count: 1,
  stats: {
    name: '小鎏',
    agent_count: 1,
    total_tasks_submitted: 0,
    total_tasks_completed: 0,
    total_tasks_failed: 0,
    task_queue_length: 0,
  },
  employees: [
    {
      name: '小鎏',
      agent_count: 1,
      role_description: '执行日常事务',
      default_capabilities: [],
      employment_status: 'active',
      read_only: false,
      agents: [
        {
          id: 'agent-1',
          agent_type: 'executor',
          name: '鎏灏执行体',
          status: 'idle',
          current_task: null,
          completed_tasks: 0,
          failed_tasks: 0,
          total_latency_ms: 0,
        },
      ],
      stats: {
        name: '小鎏',
        agent_count: 1,
        total_tasks_submitted: 0,
        total_tasks_completed: 0,
        total_tasks_failed: 0,
        task_queue_length: 0,
      },
    },
  ],
  employee_count: 1,
}

const RUNNING_DETAIL: GoalDetail = {
  goal_id: 'g1',
  state: 'running',
  natural_language: '把这周的会议记录整理成周报',
  scope: 'L1',
  error: null,
  correlation_id: null,
  created_at: 1780000000,
  artifacts: [],
  evaluation: null,
  tasks: [],
}

const FINAL_DETAIL: GoalDetail = {
  goal_id: 'g1',
  state: 'completed',
  natural_language: '把这周的会议记录整理成周报',
  scope: 'L1',
  error: null,
  correlation_id: 'corr-1',
  created_at: 1780000010,
  artifacts: ['reports/weekly.md'],
  evaluation: {
    outcome: 'completed',
    summary: '已整理 5 个项目并生成周报。',
    replan_required: false,
    replan_triggered: false,
  },
  tasks: [
    {
      id: 't1',
      name: '汇总会议记录',
      description: '读取本周会议记录',
      status: 'completed',
      capability_id: 'research',
      assigned_agent: 'agent-1',
      result: '已读取 5 份记录',
      error: null,
      started_at: '1780000000.1',
      completed_at: '1780000003.6',
    },
    {
      id: 't2',
      name: '生成周报',
      description: null,
      status: 'completed',
      capability_id: 'doc',
      assigned_agent: 'agent-1',
      result: 'reports/weekly.md',
      error: null,
      started_at: '1780000004',
      completed_at: '1780000009.5',
    },
  ],
}

const TASK_T1: GoalTask = {
  id: 't1',
  name: '汇总会议记录',
  description: '读取本周会议记录',
  status: 'pending',
  capability_id: 'research',
  assigned_agent: 'agent-1',
  result: null,
  error: null,
  started_at: null,
  completed_at: null,
}

const FIRST_SNAPSHOT: GoalDetail = {
  ...RUNNING_DETAIL,
  tasks: [TASK_T1],
}

const SECOND_SNAPSHOT: GoalDetail = {
  ...RUNNING_DETAIL,
  tasks: [{ id: 't1', status: 'running' } as GoalTask],
}

const APPROVAL_DETAIL: GoalDetail = {
  ...FINAL_DETAIL,
  state: 'failed',
  error: '一个任务需要人工审批',
  artifacts: [],
  evaluation: {
    outcome: 'failed',
    summary: '有动作被策略拦截。',
    replan_required: true,
    replan_triggered: false,
  },
  tasks: [
    {
      ...FINAL_DETAIL.tasks[0],
      id: 'a1',
      name: '发布到外部系统',
      status: 'failed',
      error: 'policy_approval_required: action publish requires human approval',
    },
    {
      ...FINAL_DETAIL.tasks[1],
      id: 'a2',
      name: '写本地缓存',
      status: 'failed',
      error: '磁盘已满',
    },
  ],
}

const TWO_FAILURES_DETAIL: GoalDetail = {
  ...APPROVAL_DETAIL,
  tasks: [
    {
      ...APPROVAL_DETAIL.tasks[1],
      id: 'f1',
      name: '写磁盘',
      error: '磁盘已满',
    },
    {
      ...APPROVAL_DETAIL.tasks[1],
      id: 'f2',
      name: '上传备份',
      error: '网络超时',
    },
  ],
}

const PENDING_ITEM = {
  pending_id: 'p1',
  action: 'publish',
  risk_level: 'HIGH',
  policy_rule: 'policy-1',
  correlation_id: 'corr-1',
  blocked_at: 1780000000,
  blocked_by: '小鎏',
  reason: '发布到外部系统需要人工确认',
  summary: '策略把 publish 拦下',
  approve: {
    method: 'POST',
    path: '/v1/policy/grants',
    body: {
      actions: ['publish'],
      target: '*',
      risk_level: 'HIGH',
      ttl_seconds: 900,
      reason: '人工确认',
    },
  },
}

let fetchMock: ReturnType<typeof vi.fn>
let detailPlan: GoalDetail[]
let lastDetail: GoalDetail = RUNNING_DETAIL
let assignResponses: Array<{ status: number; body: unknown }>
let assignBodies: Array<Record<string, unknown>>
let retryResponses: Record<string, { status: number; body: unknown }>
let pendingItems: unknown[]
let pendingForbidden: boolean

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function errorBody(code: string, message: string) {
  return { detail: { code, message } }
}

beforeEach(() => {
  detailPlan = []
  lastDetail = RUNNING_DETAIL
  assignResponses = []
  assignBodies = []
  retryResponses = {}
  pendingItems = []
  pendingForbidden = false

  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'

    if (url === '/v1/projects') return json(200, { projects: [], count: 0 })
    if (url === '/v1/employees') return json(200, EMPLOYEES)
    if (url === '/v1/goals' && method === 'GET') return json(200, { goals: [], count: 0 })
    if (url === '/v1/goals' && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
      return json(
        200,
        body.background === true
          ? { ...RUNNING_DETAIL, goal_id: 'g1' }
          : { detail: '页面必须用后台执行' },
      )
    }
    if (url === '/v1/goals/assign' && method === 'POST') {
      assignBodies.push(JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>)
      const next =
        assignResponses.shift() ?? {
          status: 200,
          body: { ...RUNNING_DETAIL, assigned_agent: 'agent-1' },
        }
      return json(next.status, next.body)
    }
    if (url === '/v1/goals/g1/stop' && method === 'POST') {
      return json(200, { goal_id: 'g1', state: 'cancelled' })
    }
    if (url.startsWith('/v1/goals/g1/tasks/') && url.endsWith('/retry') && method === 'POST') {
      const taskId = url.slice('/v1/goals/g1/tasks/'.length, -'/retry'.length)
      const next = retryResponses[taskId] ?? { status: 200, body: { ...lastDetail } }
      return json(next.status, next.body)
    }
    if (url === '/v1/goals/g1' && method === 'GET') {
      const body = detailPlan.length > 0 ? detailPlan.shift() ?? lastDetail : lastDetail
      lastDetail = body
      return json(200, body)
    }
    if (url === '/v1/policy/approvals/pending' && method === 'GET') {
      if (pendingForbidden) {
        return json(403, errorBody('forbidden', '需要管理员或 L0 人类主权'))
      }
      return json(200, {
        count: pendingItems.length,
        items: pendingItems,
        as_of: 1780000000,
        lookback_seconds: 604800,
        enforcement: {},
      })
    }
    if (url === '/v1/policy/approvals' && method === 'POST') {
      return json(201, { grant: { grant_id: 'grant-1' } })
    }
    if (url.startsWith('/v1/files/content')) {
      return json(200, {
        path: 'reports/weekly.md',
        size: 42,
        encoding: 'utf-8',
        content: '# 本周周报\n\n已整理 5 个项目。',
      })
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage(onOpenApproval = vi.fn()) {
  return render(<Butler query="" onOpenApproval={onOpenApproval} canApproveQueue />)
}

async function submitCommand(text: string) {
  fireEvent.change(screen.getByLabelText('要让管家做什么'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: '交给管家' }))
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/v1/goals')).toBe(true),
  )
}

describe('鎏灏管家 · 一句话与运行中快照', () => {
  test('一句话输入走后台目标，运行中显示可推进的计时与拆解状态', async () => {
    detailPlan = [RUNNING_DETAIL]
    renderPage()
    await submitCommand('把这周的会议记录整理成周报')

    const post = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === '/v1/goals' && init?.method === 'POST',
    )
    expect(post).toBeTruthy()
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      natural_language: '把这周的会议记录整理成周报',
      background: true,
    })

    await screen.findByText(/管家正在执行 · 已运行/, { exact: false })
    expect(screen.getByText('管家正在拆解并安排员工')).toBeTruthy()
    expect(
      screen.getAllByRole('progressbar').some((node) =>
        (node.getAttribute('aria-valuetext') ?? '').includes('总数以最终清单为准'),
      ),
    ).toBe(true)
    // 运行中绝不显示伪造的百分比。
    expect(
      screen.getAllByRole('progressbar').some((node) =>
        node.getAttribute('aria-valuenow') !== null,
      ),
    ).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '中止本次执行' }))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url) === '/v1/goals/g1/stop'),
      ).toBe(true),
    )
  })

  test('指定员工后，POST /v1/goals 带上 employee_name，汇总显示归属与员工汇总', async () => {
    detailPlan = [
      {
        ...FINAL_DETAIL,
        assigned_employee: '小鎏',
        employees: [
          { employee: '小鎏', tasks: 2, completed: 2, failed: 0, total_duration_ms: 9000 },
        ],
      },
    ]
    renderPage()
    await waitFor(() => expect(screen.getByText(/当前名册：小鎏/)).toBeTruthy())
    fireEvent.change(screen.getByLabelText('指定员工（可选）'), { target: { value: '小鎏' } })
    await submitCommand('把这周的会议记录整理成周报')

    const post = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === '/v1/goals' && init?.method === 'POST',
    )
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      natural_language: '把这周的会议记录整理成周报',
      employee_name: '小鎏',
      background: true,
    })

    await waitFor(() => expect(screen.getByText(/执行者：小鎏/)).toBeTruthy())
    expect(screen.getByText('员工汇总（按真实任务归属统计）')).toBeTruthy()
    expect(screen.getByText(/小鎏 · 做了 2 项 · 成功 2 · 失败 0 · 合计耗时 9\.0 秒/)).toBeTruthy()
  })

  test('增量快照只给变化字段时，已有任务卡保留旧字段与已出现任务', async () => {
    detailPlan = [FIRST_SNAPSHOT, SECOND_SNAPSHOT]
    renderPage()
    await submitCommand('把这周的会议记录整理成周报')

    await waitFor(() => expect(screen.getByText('汇总会议记录')).toBeTruthy())
    expect(screen.getByText('排队')).toBeTruthy()
    expect(screen.getByText('小鎏（鎏灏执行体）')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    await waitFor(() => expect(screen.getByText('进行中')).toBeTruthy())

    // 第二次快照只有 { id, status }：名称、指派、描述都必须保留，卡片不能消失。
    expect(screen.getByText('汇总会议记录')).toBeTruthy()
    expect(screen.getByText('小鎏（鎏灏执行体）')).toBeTruthy()
    expect(screen.getByText('读取本周会议记录')).toBeTruthy()
  })

  test('结束后任务卡显示员工 / 状态 / 耗时，汇总含评估与可点开的产物', async () => {
    detailPlan = [FINAL_DETAIL]
    renderPage()
    await submitCommand('把这周的会议记录整理成周报')
    await waitFor(() => screen.getByText('汇总会议记录'))

    const card = screen.getByText('汇总会议记录').closest('article') as HTMLElement
    expect(within(card).getByText('成功')).toBeTruthy()
    expect(within(card).getByText('小鎏（鎏灏执行体）')).toBeTruthy()
    expect(within(card).getByText('3.5 秒')).toBeTruthy()

    expect(screen.getByText(/共 2 项任务/)).toBeTruthy()
    expect(screen.getByText(/评估：已整理 5 个项目并生成周报。/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'reports/weekly.md' }))
    await waitFor(() => expect(screen.getByText(/已整理 5 个项目。/, { exact: false })).toBeTruthy())
  })
})

describe('鎏灏管家 · 审批与单任务重试', () => {
  test('审批类失败给「待确认」与「待我确认」入口；普通失败不被误标', async () => {
    const onOpenApproval = vi.fn()
    detailPlan = [APPROVAL_DETAIL]
    renderPage(onOpenApproval)
    await submitCommand('发布周报到外部系统')
    await waitFor(() => expect(screen.getAllByText('发布到外部系统').length).toBeGreaterThan(0))

    const approvalCard = screen
      .getAllByText('发布到外部系统')
      .map((node) => node.closest('article'))
      .find(Boolean) as HTMLElement
    expect(within(approvalCard).getByText('待确认')).toBeTruthy()

    const plainCard = screen
      .getAllByText('写本地缓存')
      .map((node) => node.closest('article'))
      .find(Boolean) as HTMLElement
    expect(within(plainCard).getByText('失败')).toBeTruthy()

    expect(screen.getByText('有 1 个动作需要你确认')).toBeTruthy()
    fireEvent.click(
      within(screen.getByRole('alert')).getByRole('button', { name: '查看已签发凭据' }),
    )
    expect(onOpenApproval).toHaveBeenCalledTimes(1)

    const retry = within(plainCard).getByRole('button', { name: '重试此任务' })
    expect((retry as HTMLButtonElement).disabled).toBe(false)
  })

  test('单任务重试真的发 POST；409 goal_running 与 task_not_retryable 文案不同', async () => {
    detailPlan = [TWO_FAILURES_DETAIL]
    retryResponses = {
      f1: { status: 409, body: errorBody('task_not_retryable', 'task is not failed') },
      f2: { status: 409, body: errorBody('goal_running', 'goal is still running') },
    }
    renderPage()
    await submitCommand('写磁盘并上传备份')
    await waitFor(() => expect(screen.getAllByText('写磁盘').length).toBeGreaterThan(0))

    const f1Card = screen
      .getAllByText('写磁盘')
      .map((node) => node.closest('article'))
      .find(Boolean) as HTMLElement
    fireEvent.click(within(f1Card).getByRole('button', { name: '重试此任务' }))
    await waitFor(() =>
      expect(
        within(f1Card).getByText(/只有失败任务能单独重试/),
      ).toBeTruthy(),
    )
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url) === '/v1/goals/g1/tasks/f1/retry',
      ),
    ).toBe(true)

    const f2Card = screen
      .getAllByText('上传备份')
      .map((node) => node.closest('article'))
      .find(Boolean) as HTMLElement
    fireEvent.click(within(f2Card).getByRole('button', { name: '重试此任务' }))
    await waitFor(() =>
      expect(within(f2Card).getByText(/这个目标还在运行/)).toBeTruthy(),
    )
  })
})

describe('鎏灏管家 · 单独指派', () => {
  test('选员工 + 写任务后发 POST /v1/goals/assign，汇总明确标为单独指派', async () => {
    detailPlan = [{ ...RUNNING_DETAIL, assigned_agent: 'agent-1' }]
    renderPage()
    await waitFor(() => expect(screen.getByText(/当前名册：小鎏/)).toBeTruthy())

    fireEvent.change(screen.getByLabelText('员工'), { target: { value: 'agent:agent-1' } })
    fireEvent.change(screen.getByLabelText('任务'), { target: { value: '把周报合并成一份' } })
    fireEvent.click(screen.getByRole('button', { name: /派给 小鎏 · 鎏灏执行体/ }))

    await waitFor(() => expect(assignBodies.length).toBe(1))
    expect(assignBodies[0]).toMatchObject({
      agent_id: 'agent-1',
      instruction: '把周报合并成一份',
      background: true,
    })

    await waitFor(() => expect(screen.getAllByText('单独指派').length).toBeGreaterThan(0))
    expect(screen.getByText(/你点名指派的，不是管家自动拆解/)).toBeTruthy()
  })

  test('按员工指派发 employee_name（不送 agent_id）', async () => {
    detailPlan = [{ ...RUNNING_DETAIL, assigned_employee: '小鎏' }]
    renderPage()
    await waitFor(() => expect(screen.getByText(/当前名册：小鎏/)).toBeTruthy())

    fireEvent.change(screen.getByLabelText('员工'), { target: { value: 'employee:小鎏' } })
    fireEvent.change(screen.getByLabelText('任务'), { target: { value: '把周报合并成一份' } })
    fireEvent.click(screen.getByRole('button', { name: /派给 小鎏（整位员工）/ }))
    await waitFor(() => expect(assignBodies.length).toBe(1))
    expect(assignBodies[0]).toMatchObject({
      employee_name: '小鎏',
      instruction: '把周报合并成一份',
      background: true,
    })
    expect(assignBodies[0].agent_id).toBeUndefined()

    await waitFor(() => expect(screen.getByText(/执行者：小鎏/)).toBeTruthy())
  })

  test('agent_not_found 与 agent_unavailable 给出不同下一步', async () => {
    assignResponses = [
      { status: 404, body: errorBody('agent_not_found', 'agent not found') },
      { status: 409, body: errorBody('agent_unavailable', 'agent paused') },
    ]
    renderPage()
    await waitFor(() => expect(screen.getByText(/当前名册：小鎏/)).toBeTruthy())

    fireEvent.change(screen.getByLabelText('员工'), { target: { value: 'agent:agent-1' } })
    fireEvent.change(screen.getByLabelText('任务'), { target: { value: '整理日志' } })

    fireEvent.click(screen.getByRole('button', { name: /派给/ }))
    await waitFor(() =>
      expect(screen.getByText(/指定的员工不存在/)).toBeTruthy(),
    )

    fireEvent.click(screen.getByRole('button', { name: /派给/ }))
    await waitFor(() =>
      expect(screen.getByText(/没有可用的执行个体/)).toBeTruthy(),
    )
    expect(assignBodies.length).toBe(2)
  })
})

describe('鎏灏管家 · 待我确认', () => {
  test('队列显示动作 / 风险 / 被拦时间，批准发送队列返回的请求体', async () => {
    pendingItems = [PENDING_ITEM]
    renderPage()

    await waitFor(() => expect(screen.getByText('publish')).toBeTruthy())
    expect(screen.getByText('风险 HIGH')).toBeTruthy()
    expect(screen.getByText(/被拦/)).toBeTruthy()

    const card = screen.getByText('publish').closest('article') as HTMLElement
    const approve = within(card).getByRole('button', { name: '采用此请求体批准' })
    // target=* 必须先显式确认覆盖所有目标。
    expect((approve as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(within(card).getByLabelText(/覆盖全部对象/))
    expect((approve as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(approve)
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url) === '/v1/policy/approvals' && init?.method === 'POST',
        ),
      ).toBe(true),
    )
    const post = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === '/v1/policy/approvals' && init?.method === 'POST',
    )
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      actions: ['publish'],
      target: '*',
      confirm_wildcard: true,
    })
    await waitFor(() => expect(screen.getByText(/已签发授权（grant-1）/)).toBeTruthy())
  })

  test('403 如实说没有签发权限，而不是说队列为空', async () => {
    pendingForbidden = true
    renderPage()

    await waitFor(() => expect(screen.getByText('你没有签发权限')).toBeTruthy())
    expect(screen.getByText(/这不是「队列为空」/)).toBeTruthy()
    expect(screen.queryByText('没有等待你确认的动作')).toBeNull()
  })

  test('没有签发范围的账号：整块不渲染，也不请求待审批端点', async () => {
    render(<Butler query="" onOpenApproval={vi.fn()} canApproveQueue={false} />)

    expect(screen.queryByText('待我确认')).toBeNull()
    expect(screen.queryByText(/签发权限/)).toBeNull()
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/v1/policy/approvals/pending')),
    ).toBe(false)
  })
})
