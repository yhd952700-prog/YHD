/**
 * 首屏三行状态带回归。
 *
 * 钉住 S1 的六件事：
 *   1. 「正在做」只取 running/queued，失败步骤排前面并标「N 步失败」；
 *   2. 「等你确认」按 risk_level 降序、blocked_at 升序，且只有「查看」没有就地批准；
 *   3. 「刚完成」只认 completed + 产物非空，最多 3 条且总数如实；
 *   4. 三个区各自有空态，空态里不出现假数字；
 *   5. 403 说「无权查看」，不说成空列表；任一行失败只降级该行；
 *   6. 三行共用一次刷新：初始只发两个请求（/v1/goals 与 pending 各一次）。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { StatusBand } from './StatusBand'
import type { GoalSummary, PendingApprovalItem } from '../lib/operator'

const NOW_SECONDS = Date.now() / 1000

function ago(seconds: number): number {
  return NOW_SECONDS - seconds
}

function json(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

function goal(overrides: Partial<GoalSummary> & { goal_id: string }): GoalSummary {
  return {
    state: 'running',
    natural_language: '把本周会议记录整理成周报',
    scope: 'L1',
    created_at: ago(600),
    error: null,
    replan_suggested: false,
    task_count: 4,
    completed_tasks: 1,
    failed_tasks: 0,
    artifacts: [],
    assigned_employee: null,
    assigned_employees: [],
    ...overrides,
  }
}

function pending(overrides: Partial<PendingApprovalItem> & { pending_id: string }): PendingApprovalItem {
  return {
    action: 'file_write',
    risk_level: 'MEDIUM',
    policy_rule: null,
    correlation_id: null,
    blocked_at: ago(300),
    blocked_by: null,
    reason: '需要你确认的动作',
    summary: '需要你确认的动作',
    approve: { method: 'POST', path: '/v1/policy/approvals', body: {} },
    ...overrides,
  }
}

let goalsResponse: { status: number; body: unknown }
let approvalsResponse: { status: number; body: unknown }
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  goalsResponse = { status: 200, body: { goals: [], count: 0 } }
  approvalsResponse = {
    status: 200,
    body: {
      count: 0,
      items: [],
      as_of: NOW_SECONDS,
      lookback_seconds: 604800,
      enforcement: { enabled: true },
    },
  }

  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/v1/goals') return json(goalsResponse.status, goalsResponse.body)
    if (url === '/v1/policy/approvals/pending') {
      return json(approvalsResponse.status, approvalsResponse.body)
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderBand(onNavigate = vi.fn()) {
  return { onNavigate, ...render(<StatusBand onNavigate={onNavigate} />) }
}

describe('今天的状态 · 正在做', () => {
  test('失败步骤排前面，显示负责人、进度与失败步数', async () => {
    goalsResponse.body = {
      count: 3,
      goals: [
        goal({
          goal_id: 'g-ok',
          natural_language: '整理客户访谈',
          task_count: 5,
          completed_tasks: 2,
          created_at: ago(480),
          assigned_employees: ['资料员', '写作员'],
        }),
        goal({
          goal_id: 'g-failed',
          natural_language: '生成季度复盘',
          task_count: 4,
          completed_tasks: 1,
          failed_tasks: 2,
          created_at: ago(120),
          assigned_employee: '数据分析师',
        }),
        goal({
          goal_id: 'g-queued',
          state: 'queued',
          natural_language: '检查合同到期日',
          task_count: 0,
          completed_tasks: 0,
          created_at: ago(30),
        }),
      ],
    }

    renderBand()
    const region = await screen.findByRole('region', { name: /正在做/ })

    const titles = Array.from(region.querySelectorAll('.os-row-title')).map(
      (node) => node.textContent ?? '',
    )
    expect(titles).toHaveLength(3)
    // 有失败步骤的排最前，并在同一行标出来。
    expect(titles[0]).toContain('生成季度复盘')
    expect(titles[0]).toContain('2 步失败')
    expect(titles[1]).toContain('整理客户访谈')

    const subs = Array.from(region.querySelectorAll('.os-row-sub')).map(
      (node) => node.textContent ?? '',
    )
    expect(subs[0]).toContain('数据分析师')
    expect(subs[0]).toContain('执行中')
    expect(subs[0]).toContain('1/4 步')
    expect(subs[0]).toContain('已创建 2 分钟')
    expect(subs[1]).toContain('资料员、写作员')
    expect(subs[1]).toContain('2/5 步')
    // 没有任务时不写 0/0，也不画一条 0% 的假进度。
    expect(subs[2]).toContain('准备中')
    const queuedRow = region.querySelectorAll('.os-row')[2]
    expect(queuedRow.querySelector('.os-bar')).toBeNull()

    // 三行共用一次刷新：初始只有两个来源各一次请求。
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls.map(([url]) => String(url)).sort()).toEqual([
      '/v1/goals',
      '/v1/policy/approvals/pending',
    ])
  })
})

describe('今天的状态 · 等你确认', () => {
  test('按风险降序、拦截时间升序，只给查看不给就地批准', async () => {
    approvalsResponse.body = {
      count: 4,
      items: [
        pending({
          pending_id: 'p-low',
          action: 'model_key_read',
          risk_level: 'LOW',
          blocked_at: ago(30),
          reason: '读取本地日志',
        }),
        pending({
          pending_id: 'p-critical',
          action: 'account_export',
          risk_level: 'CRITICAL',
          blocked_at: ago(600),
          reason: '写入工作区外的文件',
        }),
        pending({
          pending_id: 'p-medium',
          action: 'file_read',
          risk_level: 'MEDIUM',
          blocked_at: ago(10),
          reason: '访问内部知识库',
        }),
        pending({
          pending_id: 'p-high',
          action: 'file_write',
          risk_level: 'HIGH',
          blocked_at: ago(120),
          reason: '调用外部网络',
        }),
      ],
      as_of: NOW_SECONDS,
      lookback_seconds: 604800,
      enforcement: { enabled: true },
    }

    const { onNavigate } = renderBand()
    const region = await screen.findByRole('region', { name: /等你确认/ })

    const titles = Array.from(region.querySelectorAll('.os-row-title')).map(
      (node) => node.textContent ?? '',
    )
    // 只显示前 3 条：极高 → 高 → 中；低危被截在后面。
    expect(titles).toEqual([
      '账号导出 被拦下，等你确认',
      '写入 被拦下，等你确认',
      '读取 被拦下，等你确认',
    ])

    const badges = Array.from(region.querySelectorAll('.os-band-entry .os-badge')).map(
      (node) => node.textContent ?? '',
    )
    expect(badges).toEqual(['风险 极高', '风险 高', '风险 中'])

    // 同一等级内按等待时间升序：高危那条已等待 2 分钟；原因照原文附在下面。
    const subs = Array.from(region.querySelectorAll('.os-row-sub')).map(
      (node) => node.textContent ?? '',
    )
    expect(subs[1]).toContain('已等待 2 分钟')
    expect(subs[1]).toContain('调用外部网络')

    // S1 明确不做就地批准：这一行只有查看入口。
    expect(within(region).queryByText(/批准/)).toBeNull()
    expect(within(region).queryByText('确认并允许')).toBeNull()

    fireEvent.click(within(region).getByRole('button', { name: /写入 被拦下/ }))
    expect(onNavigate).toHaveBeenCalledWith('approval')
    expect(region.textContent).toContain('还有 1 条')
  })
})

describe('今天的状态 · 刚完成', () => {
  test('只取 completed 且有产物的目标，最多 3 条，总数如实', async () => {
    goalsResponse.body = {
      count: 7,
      goals: [
        goal({
          goal_id: 'g-done-old',
          state: 'completed',
          natural_language: '整理老资料',
          artifacts: ['archive/old.md'],
          created_at: ago(4 * 86400),
        }),
        goal({
          goal_id: 'g-done-new',
          state: 'completed',
          natural_language: '生成第三季度复盘',
          artifacts: ['reports/q3.md', 'reports/q3.csv', 'reports/q3.xlsx'],
          created_at: ago(120),
        }),
        goal({
          goal_id: 'g-no-artifact',
          state: 'completed',
          natural_language: '跑完但没写出文件',
          artifacts: [],
          created_at: ago(60),
        }),
        goal({
          goal_id: 'g-failed-state',
          state: 'failed',
          natural_language: '失败但有旧产物',
          artifacts: ['legacy/x.md'],
          created_at: ago(30),
        }),
      ],
    }

    renderBand()
    const region = await screen.findByRole('region', { name: /刚完成/ })

    const titles = Array.from(region.querySelectorAll('.os-row-title')).map(
      (node) => node.textContent ?? '',
    )
    expect(titles).toEqual(['生成第三季度复盘', '整理老资料'])
    expect(region.textContent).not.toContain('跑完但没写出文件')
    expect(region.textContent).not.toContain('失败但有旧产物')

    const subs = Array.from(region.querySelectorAll('.os-row-sub')).map(
      (node) => node.textContent ?? '',
    )
    // 产物只显示文件名；多个产物说「另有 N 个」；时间只说是创建时间。
    expect(subs[0]).toContain('q3.md')
    expect(subs[0]).toContain('另有 2 个产物')
    expect(subs[0]).toContain('创建于 2 分钟前')
    expect(subs[1]).toContain('创建于 4 天前')
  })
})

describe('今天的状态 · 空态与失败态', () => {
  test('三行各自有空态，不显示假数字', async () => {
    approvalsResponse.body = {
      count: 0,
      items: [],
      as_of: NOW_SECONDS,
      lookback_seconds: 604800,
      enforcement: { enabled: false },
    }

    const { onNavigate } = renderBand()
    await screen.findByText('现在没有正在执行或排队的目标。')
    expect(screen.getByText('没有等你确认的事。')).toBeTruthy()
    // 只有响应明确说未启用，才补这句；不说「已启用」也不说「拦截中」。
    expect(screen.getByText(/当前没有启用策略拦截/)).toBeTruthy()
    expect(screen.getByText(/还没有「跑完并写出文件」的目标/)).toBeTruthy()
    expect(screen.queryByText('0 条')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '交办一件事 →' }))
    expect(onNavigate).toHaveBeenCalledWith('butler')
  })

  test('审批 403 说「无权查看」，不伪装成空列表', async () => {
    approvalsResponse = {
      status: 403,
      body: { detail: '需要管理员或 L0 人类主权' },
    }
    goalsResponse.body = {
      count: 1,
      goals: [
        goal({ goal_id: 'g1', natural_language: '整理客户访谈', task_count: 3, completed_tasks: 1 }),
      ],
    }

    renderBand()
    await screen.findByText(/当前账号无权查看审批待办/)
    expect(screen.queryByText('没有等你确认的事。')).toBeNull()
    // 其他两行不受影响。
    expect(screen.getByText('整理客户访谈')).toBeTruthy()
  })

  test('goals 失败只降级「正在做 / 刚完成」，审批行照常显示', async () => {
    goalsResponse = { status: 500, body: { detail: '服务内部出错了' } }
    approvalsResponse.body = {
      count: 1,
      items: [pending({ pending_id: 'p1', risk_level: 'HIGH', reason: '调用外部网络' })],
      as_of: NOW_SECONDS,
      lookback_seconds: 604800,
      enforcement: { enabled: true },
    }

    renderBand()
    await screen.findByText(/调用外部网络/)
    expect(screen.getAllByText(/这一行暂时读不到/)).toHaveLength(2)
    expect(screen.getByRole('region', { name: /等你确认/ }).textContent).toContain('已等待')
  })
})
