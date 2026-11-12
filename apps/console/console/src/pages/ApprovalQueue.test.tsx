/**
 * 待办审批回归。
 *
 * 钉住五件最容易"看起来对但其实假"的事：
 *   1. 概览数字来自 `/v1/approvals/summary` 的真实计数，不是写死的；
 *   2. 状态切换真的按 status 重新取数；
 *   3. 搜索按标题/操作/理由/提交人过滤，不符合的不会出现；
 *   4. 通过/拒绝都要先确认，确认后才发 POST，决定写进审计的意见随请求提交，
 *      结果只报后端真实返回（409 原样显示，不伪装成成功）；
 *   5. 没有审批人资格时只看不能决，不出现通过/拒绝按钮。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ApprovalQueue } from './ApprovalQueue'

function json(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

const NOW_SECONDS = Math.floor(Date.now() / 1000)

function approvalItem(overrides: Record<string, unknown> = {}) {
  return {
    approval_id: 'ap-1',
    run_id: 'run-1',
    step_id: 'step-1',
    node_id: 'node-approve',
    def_id: 'def-weekly',
    title: '发布周报',
    operation: 'publish_report',
    reason: '对外发布前需人工确认',
    approvers: ['root'],
    status: 'pending',
    submitted_by: 'alice',
    owner_account_id: 'acct-1',
    decided_by: null,
    comment: null,
    created_at: NOW_SECONDS - 3600,
    created_at_text: '2026-11-11 10:00',
    expires_at: NOW_SECONDS + 3600,
    expires_at_text: '2026-11-11 12:00',
    decided_at_text: null,
    remaining_seconds: 3600,
    run: {
      run_id: 'run-1',
      name: '周报流程',
      status: 'waiting_approval',
      def_id: 'def-weekly',
      current_node_id: 'node-approve',
      submitted_by: 'alice',
    },
    ...overrides,
  }
}

function approvalDetail(overrides: Record<string, unknown> = {}) {
  return {
    approval: {
      ...approvalItem(),
      can_decide: true,
      steps: [
        {
          step_id: 's1',
          node_id: 'node-collect',
          node_type: 'task',
          name: '收集数据',
          seq: 1,
          status: 'completed',
          attempt: 1,
          error: null,
          duration_ms: 12000,
        },
        {
          step_id: 's2',
          node_id: 'node-approve',
          node_type: 'approval',
          name: '人工审批',
          seq: 2,
          status: 'waiting_approval',
          attempt: 0,
          error: null,
          duration_ms: null,
        },
      ],
      audit: [],
      ...overrides,
    },
  }
}

let summaryResponse: { status: number; body: unknown }
let listResponse: { status: number; body: unknown }
let detailResponse: { status: number; body: unknown }
let decideResponse: { status: number; body: unknown }
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  summaryResponse = {
    status: 200,
    body: { pending: 2, overdue: 1, due_within_5m: 1 },
  }
  listResponse = { status: 200, body: { approvals: [approvalItem()] } }
  detailResponse = { status: 200, body: approvalDetail() }
  decideResponse = {
    status: 200,
    body: approvalDetail({
      status: 'approved',
      decided_by: 'root',
      run: {
        run_id: 'run-1',
        name: '周报流程',
        status: 'running',
        def_id: 'def-weekly',
        current_node_id: 'node-approve',
        submitted_by: 'alice',
      },
    }),
  }

  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.startsWith('/v1/approvals/summary')) {
      return json(summaryResponse.status, summaryResponse.body)
    }
    if (method === 'POST' && url.endsWith('/approve')) {
      return json(decideResponse.status, decideResponse.body)
    }
    if (method === 'POST' && url.endsWith('/reject')) {
      return json(decideResponse.status, decideResponse.body)
    }
    const detailMatch = url.match(/^\/v1\/approvals\/([^/?]+)$/)
    if (detailMatch) {
      return json(detailResponse.status, detailResponse.body)
    }
    if (url.startsWith('/v1/approvals?')) {
      return json(listResponse.status, listResponse.body)
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage(query = '') {
  return render(<ApprovalQueue query={query} />)
}

async function openDetail() {
  fireEvent.click(await screen.findByRole('button', { name: '发布周报' }))
  await screen.findByText(/审批详情 · 发布周报/)
}

describe('概览与队列', () => {
  test('概览数字来自 summary 接口，不是写死的', async () => {
    const { container } = renderPage()
    await screen.findByText('发布周报')

    const counts = container.querySelectorAll('.wf-count strong')
    expect(counts).toHaveLength(3)
    expect(counts[0].textContent).toBe('2')
    expect(counts[1].textContent).toBe('1')
    expect(counts[2].textContent).toBe('1')
  })

  test('空队列如实说没有待处理，不画空表', async () => {
    listResponse = { status: 200, body: { approvals: [] } }
    renderPage()
    expect(await screen.findByText('没有待处理的审批')).toBeTruthy()
  })

  test('状态切换真的按 status 重新取数', async () => {
    renderPage()
    await screen.findByText('发布周报')

    fireEvent.click(screen.getByRole('button', { name: '已通过' }))
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes('status=approved')),
      ).toBe(true)
    })

    fireEvent.click(screen.getByRole('button', { name: '全部' }))
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes('status=&')),
      ).toBe(true)
    })
  })

  test('搜索按标题/操作/理由过滤，不符合的不出现', async () => {
    const { unmount } = renderPage('周报')
    expect(await screen.findByText('发布周报')).toBeTruthy()
    unmount()

    renderPage('不存在的审批')
    expect(await screen.findByText('没有待处理的审批')).toBeTruthy()
    expect(screen.queryByText('发布周报')).toBeNull()
  })
})

describe('审批决定', () => {
  test('通过必须先确认；确认后才发 POST，意见随请求提交', async () => {
    renderPage()
    await openDetail()

    fireEvent.click(screen.getByRole('button', { name: '通过' }))
    expect(
      screen.getByText('确认通过：流程会从这个审批节点继续往下走。'),
    ).toBeTruthy()
    // 还没确认，绝不能已经发出写请求。
    expect(
      fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST'),
    ).toHaveLength(0)

    fireEvent.change(screen.getByRole('textbox', { name: /处理意见/ }), {
      target: { value: '数据已核对' },
    })
    fireEvent.click(screen.getByRole('button', { name: '确认通过' }))

    const feedback = await screen.findByRole('status')
    expect(feedback.textContent).toContain('已通过「发布周报」')
    expect(feedback.textContent).toContain('执行中')

    const post = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/approve'))
    expect(post).toBeTruthy()
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({
      comment: '数据已核对',
    })
  })

  test('拒绝确认文案说明流程会被终止', async () => {
    renderPage()
    await openDetail()

    fireEvent.click(screen.getByRole('button', { name: '拒绝' }))
    expect(screen.getByText('确认拒绝：这条流程运行会被立即终止。')).toBeTruthy()
    expect(screen.getByRole('button', { name: '确认拒绝' })).toBeTruthy()
  })

  test('决定冲突（409）原样显示后端原因，不伪装成成功', async () => {
    decideResponse = {
      status: 409,
      body: { detail: { code: 'already_decided', message: '这条审批已经处理过' } },
    }
    renderPage()
    await openDetail()

    fireEvent.click(screen.getByRole('button', { name: '通过' }))
    fireEvent.click(screen.getByRole('button', { name: '确认通过' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('这条审批已经处理过')
    expect(screen.queryByText(/已通过「发布周报」/)).toBeNull()
  })

  test('不在审批人范围内：只看不能决，不出现通过/拒绝按钮', async () => {
    detailResponse = { status: 200, body: approvalDetail({ can_decide: false }) }
    renderPage()
    await openDetail()

    expect(screen.getByText(/你不在本审批的审批人范围内/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '通过' })).toBeNull()
    expect(screen.queryByRole('button', { name: '拒绝' })).toBeNull()
  })
})

describe('详情内容', () => {
  test('流程步骤与审批人来自详情接口', async () => {
    renderPage()
    await openDetail()

    expect(screen.getByText('第 1 步')).toBeTruthy()
    expect(screen.getByText('第 2 步')).toBeTruthy()
    expect(screen.getByText('收集数据')).toBeTruthy()
    expect(screen.getByText('人工审批')).toBeTruthy()
    expect(screen.getByText('root')).toBeTruthy()
  })
})
