/**
 * 鎏灏管家页面级回归。钉住四件事：
 *   1. 一句话提交真的发 `POST /v1/goals` 且 `background: true`；
 *   2. 运行中明确写「后端不落盘任务清单」，不伪造任务进度；
 *   3. 结束后任务卡显示员工、状态、耗时，汇总显示评估与可点开的产物；
 *   4. 单独指派因后端缺端点而禁用，审批类失败给出审批中心入口。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { Butler } from './Butler'
import type { GoalDetail } from '../lib/operator'

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

let fetchMock: ReturnType<typeof vi.fn>
let detailPlan: GoalDetail[]

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  detailPlan = []
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
    if (url === '/v1/goals/g1/stop' && method === 'POST') {
      return json(200, { goal_id: 'g1', state: 'cancelled' })
    }
    if (url === '/v1/goals/g1' && method === 'GET') {
      const body = detailPlan.length > 0 ? detailPlan.shift() ?? RUNNING_DETAIL : RUNNING_DETAIL
      return json(200, body)
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
  return render(<Butler query="" onOpenApproval={onOpenApproval} />)
}

async function submitCommand(text: string) {
  fireEvent.change(screen.getByLabelText('要让管家做什么'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: '交给管家' }))
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([url]) => String(url) === '/v1/goals')).toBe(true),
  )
}

describe('鎏灏管家', () => {
  test('一句话输入走后台目标，运行中如实说明任务清单尚未落盘', async () => {
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

    await screen.findByText('管家正在拆解并安排员工执行 · 已运行', { exact: false })
    expect(screen.getByText('为什么现在看不到任务卡？')).toBeTruthy()
    expect(
      screen.getAllByRole('progressbar').some((node) =>
        (node.getAttribute('aria-valuetext') ?? '').includes('进度未知'),
      ),
    ).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '中止本次执行' }))
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url) === '/v1/goals/g1/stop'),
      ).toBe(true),
    )
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

  test('审批类失败给「待确认」与审批中心入口；普通失败不被误标', async () => {
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
      within(screen.getByRole('alert')).getByRole('button', { name: '去审批中心授权' }),
    )
    expect(onOpenApproval).toHaveBeenCalledTimes(1)

    // 单任务重试后端没有端点：禁用并写明等待后端接口。
    const retry = within(plainCard).getByRole('button', { name: /重试此任务/ })
    expect((retry as HTMLButtonElement).disabled).toBe(true)
    expect(retry.getAttribute('title')).toContain('没有「只重试一个任务」的端点')
  })

  test('单独指派因后端缺端点而整节禁用，并写清原因', async () => {
    const { container } = renderPage()
    const assign = container.querySelector('fieldset') as HTMLFieldSetElement
    expect(assign.disabled).toBe(true)
    expect(screen.getByText('等待后端接口')).toBeTruthy()
    expect(screen.getByText(/当前网关没有「把一条任务直接指派给指定员工」的端点/)).toBeTruthy()
  })
})
