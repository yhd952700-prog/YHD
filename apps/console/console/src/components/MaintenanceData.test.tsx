/**
 * 历史数据处置面板的契约：根账号能看见原文与引用并逐条删除；成员不渲染入口；
 * 空态 / 加载态 / 失败态都有中文；删除打真实端点并刷新列表。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import { MaintenanceData } from './MaintenanceData'
import { DataCenter } from '../pages/Operations'

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

interface State {
  documents: Array<Record<string, unknown>>
  entries: Array<Record<string, unknown>>
  quarantined: Array<Record<string, unknown>>
  knowledgeStatus?: number
  memoryStatus?: number
  calls: Array<{ url: string; method: string }>
}

function baseState(): State {
  return {
    documents: [
      {
        document_id: '---.txt',
        display_name: '---.txt',
        chunks: 1,
        reason: 'missing_owner_metadata',
        detail: '缺少归属字段，旧 id 也无法反推原始账号',
        preview: '旧版单机数据：无归属历史正文。',
        preview_truncated: false,
        reference: 'knowledge://documents/---.txt#0',
        can_delete: true,
      },
    ],
    entries: [
      {
        id: 'm-1',
        account_id: '__unassigned__',
        text: '无法判定归属的记忆正文',
        source: 'legacy:memory_store',
        tags: ['legacy', 'memory_store'],
        created_at: '2026-11-07T00:00:00Z',
      },
    ],
    quarantined: [
      {
        id: 'q-telemetry',
        source: 'memory_store',
        source_ref: 't-1',
        reason: 'non_memory_telemetry',
        owner_hint: '',
        payload: { entry_id: 't-1', key: 'episodic:run:goal-1', value: '运行回执原文' },
        created_at: '2026-11-07T00:00:00Z',
      },
    ],
    calls: [],
  }
}

function stubFetch(state: State) {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    state.calls.push({ url, method })

    if (url.startsWith('/v1/knowledge/maintenance/unassigned')) {
      if (state.knowledgeStatus && state.knowledgeStatus >= 400) {
        return json({ detail: '知识库维护接口不可用' }, state.knowledgeStatus)
      }
      return json({
        documents: state.documents,
        count: state.documents.length,
        total: state.documents.length,
        limit: 50,
        offset: 0,
        has_more: false,
        lost: 0,
        note: '这些是无归属历史文档，主账号可查看并删除。',
      })
    }

    if (url.startsWith('/v1/memory/unassigned') && method === 'GET') {
      if (state.memoryStatus && state.memoryStatus >= 400) {
        return json({ detail: '记忆维护接口不可用' }, state.memoryStatus)
      }
      return json({
        entries: state.entries,
        count: state.entries.length,
        total: state.entries.length,
        limit: 50,
        offset: 0,
        has_more: false,
        quarantined: state.quarantined,
        quarantined_count: state.quarantined.length,
        quarantined_total: state.quarantined.length,
        quarantine_limit: 50,
        quarantine_offset: 0,
        note: '隔离区保留迁移时跳过的原始行。',
      })
    }

    if (url.startsWith('/v1/knowledge/documents/') && method === 'DELETE') {
      const id = decodeURIComponent(url.split('/').pop() ?? '')
      state.documents = state.documents.filter((doc) => doc.document_id !== id)
      return json({ document_id: id, deleted: true, chunks_removed: 1 })
    }

    if (url.startsWith('/v1/memory/unassigned/') && method === 'DELETE') {
      const id = decodeURIComponent(url.split('/').pop() ?? '')
      state.entries = state.entries.filter((entry) => entry.id !== id)
      return json({ id, deleted: true, removed: 1 })
    }

    if (url.startsWith('/v1/memory/quarantine/') && method === 'DELETE') {
      const id = decodeURIComponent(url.split('/').pop() ?? '')
      state.quarantined = state.quarantined.filter((row) => row.id !== id)
      return json({ id, deleted: true, removed: 1 })
    }

    // DataCenter 的仪表盘数据：只为验证"成员不渲染维护入口"。
    if (url.startsWith('/v1/dashboard/analytics')) {
      return json({
        timeseries: { available: false, days: [], error: null },
        breakdown: { items: [], excluded_noise_events: 0 },
      })
    }
    if (url.startsWith('/v1/dashboard/summary')) {
      return json({ sessions: { detail: [] } })
    }
    return json({})
  })
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MaintenanceData', () => {
  test('根账号能看到未归属文档与隔离原文，并能逐条删除', async () => {
    const state = baseState()
    stubFetch(state)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<MaintenanceData />)

    await screen.findByText('旧版单机数据：无归属历史正文。')
    expect(screen.getByText('缺少归属字段，旧 id 也无法反推账号')).toBeTruthy()
    expect(screen.getByText('knowledge://documents/---.txt#0')).toBeTruthy()
    expect(screen.getByText('无法判定归属的记忆正文')).toBeTruthy()
    expect(screen.getByText('运行时遥测（不是用户记忆）')).toBeTruthy()
    expect(screen.getByText(/运行回执原文/)).toBeTruthy()

    const deleteButtons = screen.getAllByRole('button', { name: '删除' })
    expect(deleteButtons).toHaveLength(3)

    // 删知识文档
    fireEvent.click(deleteButtons[0])
    await waitFor(() => {
      expect(state.documents).toHaveLength(0)
    })
    await screen.findByText('未归属知识文档已删除')
    expect(
      state.calls.some(
        (call) =>
          call.method === 'DELETE' &&
          call.url === '/v1/knowledge/documents/---.txt',
      ),
    ).toBe(true)
    await screen.findByText('没有未归属的知识文档')

    // 删未归属记忆
    const entryDelete = screen.getAllByRole('button', { name: '删除' })
    fireEvent.click(entryDelete[0])
    await waitFor(() => expect(state.entries).toHaveLength(0))
    await screen.findByText('未归属记忆已删除')
    // 列表刷新完成后再取按钮：否则拿到的还是上一轮残留的"删除"。
    await screen.findByText('没有未归属记忆')

    // 删隔离记录
    const quarantineDelete = screen.getAllByRole('button', { name: '删除' })
    expect(quarantineDelete).toHaveLength(1)
    fireEvent.click(quarantineDelete[0])
    await waitFor(() => expect(state.quarantined).toHaveLength(0))
    await screen.findByText('隔离记录已删除')
    // 两张表都空了之后整块切到空态，而不是留一张空表。
    await screen.findByText('没有未归属或隔离的记忆')

    expect(
      state.calls.some(
        (call) => call.method === 'DELETE' && call.url === '/v1/memory/unassigned/m-1',
      ),
    ).toBe(true)
    expect(
      state.calls.some(
        (call) =>
          call.method === 'DELETE' &&
          call.url === '/v1/memory/quarantine/q-telemetry',
      ),
    ).toBe(true)
  })

  test('空态：没有未归属文档 / 没有未归属或隔离记忆', async () => {
    const state = baseState()
    state.documents = []
    state.entries = []
    state.quarantined = []
    stubFetch(state)

    render(<MaintenanceData />)

    await screen.findByText('没有未归属的知识文档')
    await screen.findByText('没有未归属或隔离的记忆')
  })

  test('失败态：接口错误显示中文原因与重试入口', async () => {
    const state = baseState()
    state.knowledgeStatus = 500
    state.memoryStatus = 500
    stubFetch(state)

    render(<MaintenanceData />)

    await screen.findByText('知识库维护接口不可用')
    await screen.findByText('记忆维护接口不可用')
    expect(screen.getAllByRole('alert').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByRole('button', { name: /重试/ }).length).toBeGreaterThanOrEqual(2)
  })

  test('成员的数据中心不渲染历史数据处置入口，也不请求维护接口', async () => {
    const state = baseState()
    stubFetch(state)

    render(<DataCenter query="" isRoot={false} />)

    await waitFor(() => {
      expect(state.calls.some((call) => call.url.startsWith('/v1/dashboard/'))).toBe(true)
    })
    expect(screen.queryByText('未归属知识文档')).toBeNull()
    expect(screen.queryByText('未归属记忆与迁移隔离区')).toBeNull()
    expect(
      state.calls.some(
        (call) =>
          call.url.startsWith('/v1/knowledge/maintenance/unassigned') ||
          call.url.startsWith('/v1/memory/unassigned'),
      ),
    ).toBe(false)
  })
})
