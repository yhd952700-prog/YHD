/**
 * 任务中心回归。
 *
 * 钉住五件在需求里写明、也最容易"看起来对但其实假"的事：
 *   1. 七态筛选齐全，切换时真的按 status 重新取数；
 *   2. 负责人/进度用真实数据，优先级与截止时间后端没有就写「暂未提供」；
 *   3. 失败步骤整卡标红、原因单列，正常步骤不红；
 *   4. 重试/接管都必须先确认再发请求，结果只报后端真实返回（409 原样显示）；
 *   5. 台账接口 404 与 403 各说各的话，不画空表也不伪造数字。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TaskCenter } from './TaskCenter'

function json(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

function task(overrides: Record<string, unknown> = {}) {
  return {
    task_id: 't1',
    goal_id: 'g1',
    title: '整理客户访谈',
    description: '',
    status: 'failed',
    status_source: 'ledger',
    assignee: { type: 'employee', id: 'e1', name: '资料员' },
    owner_account_id: 'acct-1',
    project_id: null,
    artifacts: [],
    error: '第二步写入被拦截',
    technical_detail: null,
    created_at: '2026-11-11T02:00:00+00:00',
    started_at: '2026-11-11T02:00:10+00:00',
    completed_at: '2026-11-11T02:01:40+00:00',
    updated_at: '2026-11-11T02:01:40+00:00',
    attempt_count: 1,
    archived: false,
    human_hold: false,
    source: 'goal',
    ...overrides,
  }
}

function step(overrides: Record<string, unknown> = {}) {
  return {
    step_id: 's1',
    task_id: 't1',
    step_key: 'collect_interview',
    seq: 1,
    name: '访谈整理',
    description: '',
    status: 'completed',
    status_source: 'ledger',
    capability_id: 'file_read',
    assignee: { type: 'employee', id: 'e1', name: '资料员' },
    dependencies: [],
    artifacts: [],
    error: null,
    technical_detail: null,
    started_at: '2026-11-11T02:00:10+00:00',
    completed_at: '2026-11-11T02:00:40+00:00',
    attempt_count: 1,
    human_hold: false,
    ...overrides,
  }
}

const FAILED_STEP = step({
  step_id: 's2',
  step_key: 'write_report',
  seq: 2,
  name: '写周报',
  status: 'failed',
  capability_id: 'file_write',
  error: '写入被策略拦截：目标路径不在工作区',
  technical_detail: 'PermissionError: outside workspace',
  started_at: '2026-11-11T02:00:40+00:00',
  completed_at: '2026-11-11T02:01:30+00:00',
})

const PENDING_STEP = step({
  step_id: 's3',
  step_key: 'notify',
  seq: 3,
  name: '通知结果',
  status: 'pending',
  capability_id: null,
  assignee: { type: 'none', id: '', name: '' },
  started_at: null,
  completed_at: null,
})

let tasksResponse: { status: number; body: unknown }
let detailResponse: { status: number; body: unknown }
let stepsByTask: Record<string, { status: number; body: unknown }>
let retryResponse: { status: number; body: unknown }
let takeoverResponse: { status: number; body: unknown }
let ledgerResponse: { status: number; body: unknown }
let fetchMock: ReturnType<typeof vi.fn>

function listPayload(items: unknown[], total = items.length) {
  return { total, limit: 20, offset: 0, items, visible_scope: 'self' }
}

function detailPayload(taskOverrides: Record<string, unknown> = {}, steps: unknown[] = []) {
  return {
    ...task(taskOverrides),
    steps,
    runs: [],
    ...taskOverrides,
  }
}

beforeEach(() => {
  tasksResponse = {
    status: 200,
    body: listPayload([task({ task_id: 't1' }), task({ task_id: 't2', status: 'pending', title: '生成季度复盘', assignee: null, error: null, updated_at: '2026-11-11T03:00:00+00:00' })]),
  }
  detailResponse = { status: 200, body: detailPayload({}, [step(), FAILED_STEP, PENDING_STEP]) }
  stepsByTask = {
    t1: { status: 200, body: { steps: [step(), FAILED_STEP, PENDING_STEP] } },
    t2: { status: 200, body: { steps: [] } },
  }
  retryResponse = {
    status: 200,
    body: { task_id: 't1', step_key: 'write_report', goal_state: 'running', task: task({ status: 'running' }) },
  }
  takeoverResponse = { status: 200, body: { task: task({ status: 'pending', human_hold: true }) } }
  ledgerResponse = { status: 403, body: { detail: '需要管理员或 L0 人类主权' } }

  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.startsWith('/v1/tasks/ledger/status')) {
      return json(ledgerResponse.status, ledgerResponse.body)
    }
    if (method === 'POST' && url.endsWith('/retry')) {
      return json(retryResponse.status, retryResponse.body)
    }
    if (method === 'POST' && url.endsWith('/takeover')) {
      return json(takeoverResponse.status, takeoverResponse.body)
    }
    const stepsMatch = url.match(/^\/v1\/tasks\/([^/?]+)\/steps$/)
    if (stepsMatch) {
      const entry = stepsByTask[stepsMatch[1]] ?? { status: 404, body: { detail: 'not found' } }
      return json(entry.status, entry.body)
    }
    const detailMatch = url.match(/^\/v1\/tasks\/([^/?]+)$/)
    if (detailMatch && detailMatch[1] !== 'ledger') {
      return json(detailResponse.status, detailResponse.body)
    }
    if (url.startsWith('/v1/tasks?')) {
      return json(tasksResponse.status, tasksResponse.body)
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage(query = '') {
  return render(<TaskCenter query={query} />)
}

async function openDetail() {
  const title = await screen.findByRole('button', { name: /整理客户访谈/ })
  fireEvent.click(title)
  await screen.findByText('逐步时间线')
}

describe('任务列表', () => {
  test('七态筛选齐全，切换真的按 status 重新取数', async () => {
    renderPage()
    await screen.findByText('整理客户访谈')

    for (const label of ['全部', '待分配', '待执行', '执行中', '待审核', '已完成', '失败', '已取消']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }

    fireEvent.click(screen.getByRole('button', { name: '失败' }))
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes('status=failed')),
      ).toBe(true)
    })
  })

  test('负责人与进度用真实数据，优先级/截止时间如实说暂未提供', async () => {
    renderPage()

    // 负责人来自 assignee.name；没有负责人就说未分配。
    expect(await screen.findByText('资料员')).toBeTruthy()
    expect(await screen.findByText('生成季度复盘')).toBeTruthy()
    expect(screen.getAllByText('未分配').length).toBeGreaterThanOrEqual(1)

    // 进度来自真实子步骤：t1 是 2/3 步（done=1? 见 fixture：completed 1 + pending 1 + failed 1）。
    expect(await screen.findByText(/1\/3 步/)).toBeTruthy()
    expect(screen.getByText(/1 步失败/)).toBeTruthy()

    // 后端没有这两个字段：不填数字，明确说暂未提供。
    expect(screen.getAllByText('暂未提供').length).toBeGreaterThanOrEqual(2)
  })
})

describe('任务详情', () => {
  test('逐步时间线含序号/负责人/能力/耗时/起止；失败步骤整卡标红并单列原因', async () => {
    const { container } = renderPage()
    await openDetail()

    expect(screen.getByText('第 1 步')).toBeTruthy()
    expect(screen.getByText('第 2 步')).toBeTruthy()
    expect(screen.getByText('第 3 步')).toBeTruthy()

    const cards = container.querySelectorAll('.tc-step')
    expect(cards).toHaveLength(3)
    expect(container.querySelectorAll('.tc-step-failed')).toHaveLength(1)
    // 正常步骤不能被误标红。
    expect(cards[0].className).not.toContain('tc-step-failed')
    expect(cards[2].className).not.toContain('tc-step-failed')

    const failedCard = cards[1] as HTMLElement
    expect(failedCard.textContent).toContain('失败在这一步')
    expect(failedCard.textContent).toContain('写入被策略拦截：目标路径不在工作区')
    expect(failedCard.textContent).toContain('负责人：资料员')
    expect(failedCard.textContent).toContain('能力：')
    // 10:00:40 → 10:01:30 是真实 50 秒，不是猜的。
    expect(failedCard.textContent).toContain('耗时：50.0 秒')

    // 没有负责人的步骤如实说未分配，不编一个名字。
    expect(cards[2].textContent).toContain('负责人：未分配')
  })
})

describe('失败处理', () => {
  test('重试必须先确认；确认后按真实 step_key 提交并显示后端结果', async () => {
    renderPage()
    await openDetail()

    fireEvent.click(screen.getByRole('button', { name: '重试这一步' }))
    const dialog = screen.getByRole('dialog', { name: '确认重试' })
    expect(dialog.textContent).toContain('write_report')
    // 还没确认，绝不能已经发出写请求。
    expect(fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: '确认重试' }))

    const feedback = await screen.findByText(/已提交重试/)
    expect(feedback.textContent).toContain('write_report')
    expect(feedback.textContent).toContain('running')

    const post = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/retry'))
    expect(post).toBeTruthy()
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({
      step_key: 'write_report',
      reason: '',
    })
  })

  test('重试冲突（409）原样显示后端原因，不伪装成成功', async () => {
    retryResponse = {
      status: 409,
      body: { detail: { code: 'human_takeover_active', message: '任务已被人工接管，自动重试不可用' } },
    }
    renderPage()
    await openDetail()

    fireEvent.click(screen.getByRole('button', { name: '重试这一步' }))
    fireEvent.click(screen.getByRole('button', { name: '确认重试' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('任务已被人工接管，自动重试不可用')
    expect(screen.queryByText(/已提交重试/)).toBeNull()
  })

  test('人工接管同样先确认，原因随请求真实提交', async () => {
    renderPage()
    await openDetail()

    fireEvent.click(screen.getByRole('button', { name: '人工接管' }))
    const dialog = screen.getByRole('dialog', { name: '确认人工接管' })
    fireEvent.change(dialog.querySelector('textarea') as HTMLTextAreaElement, {
      target: { value: '我来处理' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确认接管' }))

    const feedback = await screen.findByText(/已转由人工接手/)
    expect(feedback.textContent).toContain('待执行')

    const post = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/takeover'))
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({ reason: '我来处理' })
  })
})

describe('诚实降级', () => {
  test('列表接口 404：整块说台账未上线，不画空表', async () => {
    tasksResponse = { status: 404, body: { detail: { code: 'not_found', message: '未找到该路由' } } }
    renderPage()

    const notes = await screen.findAllByText(/暂未提供 —— 本地服务的任务台账还没上线/)
    expect(notes.length).toBeGreaterThanOrEqual(1)
    // 不能被误说成「当前筛选下没有任务」。
    expect(screen.queryByText('当前筛选下没有任务')).toBeNull()
  })

  test('台账状态：403 说无权查看；200 用真实数字并说明跳过原因不返回', async () => {
    const { unmount } = renderPage()
    await screen.findByText(/仅主账号可查看/)
    unmount()

    ledgerResponse = {
      status: 200,
      body: {
        ledger: {
          db_path: 'D:/LiuHaoData/tasks.sqlite3',
          schema_version: 1,
          counts: { tasks: 12, steps: 30, runs: 45 },
          by_status: { failed: 2, completed: 10 },
          recent_migrations: [
            {
              migration_id: 'm1',
              source_path: 'goals.jsonl',
              source_sha256: 'abc',
              started_at: '2026-11-11T02:00:00+00:00',
              finished_at: '2026-11-11T02:00:05+00:00',
              status: 'completed',
              entries_scanned: 14,
              tasks_imported: 12,
              steps_imported: 30,
              runs_imported: 45,
              skipped: 2,
              error: null,
            },
          ],
        },
        lag: {
          hot_goals: 12,
          ledger_tasks: 12,
          missing_in_ledger: [],
          stale: [],
          lag_total: 0,
          write_failures: 0,
          last_failure_at: null,
          last_failure_reason: null,
          default_owner: 'employee:none',
        },
      },
    }

    renderPage()
    expect((await screen.findAllByText('12')).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('30').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('45').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('已完成').length).toBeGreaterThanOrEqual(1)
    // 历史迁移只给条数、不给原因：如实说明，不反推原因。
    expect(screen.getByText(/只返回跳过条数，不返回原因/)).toBeTruthy()
  })
})
