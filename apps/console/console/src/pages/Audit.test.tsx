/**
 * 审计链回归。
 *
 * 钉住五件最容易"看起来对但其实假"的事：
 *   1. 摘要数字与分布来自 `/v1/audit/summary` 的真实计数；
 *   2. 链校验 ok=false 必须原样展示为损坏态，列出具体失败项，绝不渲染成"正常"；
 *   3. 列表被 limit 截断时说"还有更多"，不假装这是全部；
 *   4. 非根账号看不到根专属的三张卡与范围切换，取数按令牌主体过滤；
 *   5. 点"查看同批操作"真的按 correlation_id 过滤；导出归档真的调导出接口，
 *      校验结论随回执如实回报。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Audit } from './Audit'
import type { AccountProfile } from '../lib/accountCaps'

function json(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

function profile(overrides: Partial<AccountProfile> = {}): AccountProfile {
  return {
    principal: 'root',
    displayName: '根账号',
    scope: 'L0',
    identityPermissions: [],
    accountPermissions: [],
    isRoot: true,
    canSelfRecover: true,
    members: [],
    memberCount: 2,
    source: 'live',
    loaded: true,
    error: null,
    ...overrides,
  }
}

function auditEvent(overrides: Record<string, unknown> = {}) {
  return {
    seq: 42,
    event_id: 'evt-42',
    event_type: 'goal',
    principal_id: 'root',
    scope: 'L0',
    timestamp: 1762833600,
    correlation_id: 'corr-1',
    outcome: 'ok',
    details: null,
    action: 'goal.create',
    target: '目标「上线准备」',
    display_title: '创建了目标「上线准备」',
    status_label: '成功',
    event_hash: 'abc',
    prev_event_hash: null,
    hash_alg: 'sha256',
    link_hash: null,
    ...overrides,
  }
}

function summaryBody(overrides: Record<string, unknown> = {}) {
  return {
    total_events: 5,
    store_total_events: 5,
    business_events: 4,
    tail_seq: 42,
    by_outcome: { ok: 4, denied: 1 },
    by_action: { 'goal.create': 3, login: 2 },
    distinct_correlation_ids: 3,
    generated_at: 1762833600,
    retention: {
      enabled: true,
      days: 90,
      max_rows: 100000,
      max_bytes: 104857600,
      min_keep: 1000,
      interval: 3600,
      max_delete: 1000,
    },
    chain_key: {
      status: 'available',
      mode: 'on',
      keyed_events: 5,
      degraded: false,
    },
    ...overrides,
  }
}

function eventsBody(events: unknown[], truncated = true) {
  return {
    events,
    count: events.length,
    truncated,
    filters: {
      principal: null,
      action: null,
      outcome: null,
      correlation_id: null,
      since: null,
      until: null,
      event_type: null,
      limit: 200,
      reverse: false,
    },
  }
}

function verifyBody(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    entries_checked: 5,
    first_failure: null,
    details: {
      failures: [],
      failure_count: 0,
      pinpoint_ran: false,
      mutation_performed: false,
      read_only: true,
    },
    ...overrides,
  }
}

let summaryResponse: { status: number; body: unknown }
let eventsResponse: { status: number; body: unknown }
let verifyResponse: { status: number; body: unknown }
let exportResponse: { status: number; body: unknown }
let fetchMock: ReturnType<typeof vi.fn>
let clickSpy: ReturnType<typeof vi.spyOn>
let originalCreateObjectURL: typeof URL.createObjectURL
let originalRevokeObjectURL: typeof URL.revokeObjectURL

beforeEach(() => {
  summaryResponse = { status: 200, body: summaryBody() }
  eventsResponse = { status: 200, body: eventsBody([auditEvent()]) }
  verifyResponse = { status: 200, body: verifyBody() }
  exportResponse = {
    status: 200,
    body: {
      schema: 1,
      exported_at: 1762833600,
      since_seq: 0,
      limit: 20000,
      count: 5,
      truncated: false,
      next_since_seq: 0,
      events: [auditEvent()],
      integrity: verifyBody(),
      notice: '归档不含校验密钥',
    },
  }

  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith('/v1/audit/summary')) {
      return json(summaryResponse.status, summaryResponse.body)
    }
    if (url.startsWith('/v1/audit/verify')) {
      return json(verifyResponse.status, verifyResponse.body)
    }
    if (url.startsWith('/v1/audit/export')) {
      return json(exportResponse.status, exportResponse.body)
    }
    if (url.startsWith('/v1/audit/events')) {
      const parsed = new URL(url, 'http://localhost')
      const correlation = parsed.searchParams.get('correlation_id')
      if (correlation) {
        return json(200, eventsBody([auditEvent({ seq: 43, event_id: 'evt-43' })], false))
      }
      return json(eventsResponse.status, eventsResponse.body)
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
  // jsdom 没有实现 URL.createObjectURL：直接替换静态方法，afterEach 恢复。
  originalCreateObjectURL = URL.createObjectURL
  originalRevokeObjectURL = URL.revokeObjectURL
  URL.createObjectURL = () => 'blob:fake-audit-archive'
  URL.revokeObjectURL = () => {}
  clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

afterEach(() => {
  clickSpy.mockRestore()
  URL.createObjectURL = originalCreateObjectURL
  URL.revokeObjectURL = originalRevokeObjectURL
  vi.unstubAllGlobals()
})

function renderPage(query = '', prof: AccountProfile = profile()) {
  return render(<Audit query={query} profile={prof} />)
}

describe('根账号视图', () => {
  test('摘要数字与分布来自 summary 接口', async () => {
    renderPage()
    expect(await screen.findByText('创建了目标「上线准备」')).toBeTruthy()

    // "你能看到的记录" 5 与"全库存储" 5；"最新编号" 42 与事件行编号 42；
    // "可追踪批次" 3 与 goal.create 3 条；业务事件 4 与 ok 结果 4 条。
    expect(screen.getAllByText('5').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('42').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('4').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('1').length).toBeGreaterThanOrEqual(1)
    // '保留期' 同时出现在摘要行与下方的保留期说明段落里。
    expect(screen.getAllByText(/保留期/).length).toBeGreaterThanOrEqual(2)
  })

  test('列表被截断时说还有更多，不假装这是全部', async () => {
    renderPage()
    await screen.findByText('创建了目标「上线准备」')
    expect(screen.getByText(/只显示最近 200 条/)).toBeTruthy()
  })

  test('链校验完好时显示"完好"与已检查条数', async () => {
    renderPage()
    await screen.findByText('创建了目标「上线准备」')

    fireEvent.click(screen.getByRole('button', { name: '检查记录完整性' }))
    expect(await screen.findByText('完好')).toBeTruthy()
    expect(screen.getAllByText('5').length).toBeGreaterThanOrEqual(1)
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).startsWith('/v1/audit/verify')),
    ).toBe(true)
  })

  test('链校验 ok=false 原样展示为损坏，不渲染成正常', async () => {
    verifyResponse = {
      status: 200,
      body: verifyBody({
        ok: false,
        entries_checked: 5,
        first_failure: 'seq=3 hash mismatch',
        details: {
          failures: ['seq=3 hash mismatch', 'seq=4 link broken'],
          failure_count: 2,
          pinpoint_ran: true,
          mutation_performed: false,
          read_only: true,
        },
      }),
    }
    renderPage()
    await screen.findByText('创建了目标「上线准备」')

    fireEvent.click(screen.getByRole('button', { name: '检查记录完整性' }))
    expect(await screen.findByText('已被修改 / 截断 / 分叉')).toBeTruthy()
    // 第一条失败同时出现在"第一处异常"行与失败清单里。
    expect(screen.getAllByText('seq=3 hash mismatch')).toHaveLength(2)
    expect(screen.getByText('seq=4 link broken')).toBeTruthy()
    expect(screen.queryByText('完好')).toBeNull()
  })

  test('导出归档调导出接口，回执如实报条数与校验结论', async () => {
    renderPage()
    await screen.findByText('创建了目标「上线准备」')

    fireEvent.click(screen.getByRole('button', { name: '导出归档' }))
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).startsWith('/v1/audit/export')),
      ).toBe(true)
    })
    const notice = await screen.findByRole('status')
    expect(notice.textContent).toContain('已导出 5 条记录')
    expect(notice.textContent).toContain('完好')
    expect(clickSpy).toHaveBeenCalled()
  })
})

describe('非根账号视图', () => {
  test('看不到根专属卡片与范围切换，取数按主体过滤', async () => {
    renderPage('', profile({ isRoot: false, principal: 'member-a' }))
    expect(await screen.findByText('创建了目标「上线准备」')).toBeTruthy()

    expect(screen.queryByText('记录完整性')).toBeNull()
    expect(screen.queryByText('操作类型分布')).toBeNull()
    expect(screen.queryByLabelText('查看范围')).toBeNull()
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes('/v1/audit/events?limit=200&principal=member-a'),
      ),
    ).toBe(true)
    // 非根不调 summary。
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).startsWith('/v1/audit/summary')),
    ).toBe(false)
  })
})

describe('关联目标', () => {
  test('点"查看同批操作"按 correlation_id 过滤，可返回全部', async () => {
    renderPage()
    await screen.findByText('创建了目标「上线准备」')

    fireEvent.click(screen.getByRole('button', { name: '查看同批操作' }))
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) =>
          String(url).includes('correlation_id=corr-1'),
        ),
      ).toBe(true)
    })
    expect(await screen.findByText('同一批操作')).toBeTruthy()
    expect(screen.getByRole('button', { name: '返回全部' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '返回全部' }))
    expect(await screen.findByText('操作记录')).toBeTruthy()
  })
})

describe('空态与搜索', () => {
  test('没有记录时如实说还没有操作记录', async () => {
    eventsResponse = { status: 200, body: eventsBody([], false) }
    summaryResponse = {
      status: 200,
      body: summaryBody({ total_events: 0, by_action: {}, by_outcome: {} }),
    }
    renderPage()
    // 摘要卡与事件列表各有一句"还没有操作记录"。
    expect((await screen.findAllByText('还没有操作记录')).length).toBeGreaterThanOrEqual(2)
  })

  test('搜索按标题过滤，不符合的不出现', async () => {
    const { unmount } = renderPage('上线准备')
    expect(await screen.findByText('创建了目标「上线准备」')).toBeTruthy()
    unmount()

    renderPage('不存在的操作')
    expect(await screen.findByText('还没有操作记录')).toBeTruthy()
  })
})
