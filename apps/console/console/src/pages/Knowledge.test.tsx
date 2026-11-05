/**
 * 知识中心页面级回归：把「界面必须说人话」的几件事钉在渲染层。
 *
 * 覆盖：
 *   1. 文档列表来自服务端账本（不再读 localStorage）；
 *   2. 删除必须先出现二次确认，并写明「会连同分块与向量一起删除，不可恢复」；
 *   3. 分块预览走 `/documents/{id}/chunks`（不是检索），且几千条分块要分页；
 *   4. 异步入库显示进度 / 取消，并明确「取消后本次不会留下任何数据」；
 *   5. PDF 在本地就被拦下，原话包含「不会假装解析」。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Knowledge } from './Knowledge'

const STATUS = {
  enabled: true,
  provider: 'openai-compatible',
  model: 'embed-3',
  vectors: 12,
  reason: null,
  message: '知识库已启用',
}

const DOC = {
  document_id: 'd1',
  source: 'q3-retro.md',
  title: null,
  chunks: 45,
  content_hash: 'abc',
  ingested_at: '2026-11-06 12:00:00',
}

const PAGE = { documents: [DOC], total: 1, limit: 20, offset: 0 }

const CHUNKS = {
  document_id: 'd1',
  chunks: Array.from({ length: 45 }, (_, index) => ({
    chunk_id: `d1#${index}`,
    chunk_index: index,
    text: `第 ${index} 块正文`,
    heading_path: ['第一章', '细节'],
    context_header: '第一章 · 细节',
  })),
  count: 45,
}

const RUNNING_JOB = {
  job_id: 'j1',
  document_id: 'q3-retro',
  status: 'running',
  phase: 'embedding',
  total_chunks: 10,
  processed_chunks: 3,
  progress_percent: 30,
  cancel_requested: false,
  created_at: '2026-11-06T12:00:00+00:00',
  started_at: '2026-11-06T12:00:00+00:00',
  finished_at: null,
  error: null,
  result: null,
  note: null,
  partial_commit_policy: 'all-or-nothing（取消后不写入任何分块，不会留下半成品）',
}

const CANCELLED_JOB = {
  ...RUNNING_JOB,
  status: 'cancelled',
  phase: 'cancelled',
  finished_at: '2026-11-06T12:00:05+00:00',
  result: { committed_chunks: 0 },
  note: '已取消；未写入任何分块（全回滚）',
  cancel_accepted: true,
  reason: '取消将在下一个分块边界生效；已入库内容按全回滚处理',
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url.startsWith('/v1/knowledge/status')) return json(200, STATUS)
    if (url.startsWith('/v1/knowledge/documents?') && method === 'GET') return json(200, PAGE)
    if (url === '/v1/knowledge/ingest/jobs' && method === 'POST') return json(202, RUNNING_JOB)
    if (url.endsWith('/cancel') && method === 'POST') return json(200, CANCELLED_JOB)
    if (url.startsWith('/v1/knowledge/ingest/jobs/') && method === 'GET') {
      return json(200, RUNNING_JOB)
    }
    if (url === '/v1/knowledge/documents/d1/chunks' && method === 'GET') {
      return json(200, CHUNKS)
    }
    if (url === '/v1/knowledge/documents/d1' && method === 'DELETE') {
      return json(200, { document_id: 'd1', deleted: true, chunks_removed: 45 })
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage() {
  return render(<Knowledge query="" onOpenSettings={() => {}} />)
}

async function waitForDocs() {
  await waitFor(() => expect(screen.getByText('q3-retro.md')).toBeTruthy())
}

describe('知识中心 · 服务端文档账本', () => {
  test('列表来自服务端接口，显示真实分块数', async () => {
    renderPage()
    await waitForDocs()
    expect(screen.getByText('45')).toBeTruthy()
    expect(screen.getAllByText('已入库').length).toBeGreaterThan(0)
    const listCall = fetchMock.mock.calls.find(([url]) =>
      String(url).startsWith('/v1/knowledge/documents?'),
    )
    expect(listCall).toBeTruthy()
  })

  test('PDF 在本地被拦下，原话包含「不会假装解析」', async () => {
    const { container } = renderPage()
    await waitForDocs()
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    expect(input).toBeTruthy()
    fireEvent.change(input, {
      target: { files: [new File(['%PDF'], 'report.pdf', { type: 'application/pdf' })] },
    })
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('类型不支持')
    expect(alert.textContent).toContain('不会假装解析')
    // 本地预检失败不应该发起上传请求。
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/documents/upload')),
    ).toBe(false)
  })
})

describe('知识中心 · 删除', () => {
  test('先二次确认并写明不可恢复，确认后才发 DELETE', async () => {
    renderPage()
    await waitForDocs()
    fireEvent.click(screen.getByRole('button', { name: '删除' }))

    expect(screen.getByText(/确认删除「q3-retro.md」？/)).toBeTruthy()
    expect(screen.getByText(/会连同分块与向量一起删除，不可恢复/)).toBeTruthy()
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE'),
    ).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() =>
      expect(screen.getByText(/已删除「q3-retro.md」/)).toBeTruthy(),
    )
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === '/v1/knowledge/documents/d1' && init?.method === 'DELETE',
      ),
    ).toBe(true)
  })
})

describe('知识中心 · 分块预览', () => {
  test('读的是 chunks 接口而不是检索，且按页渲染', async () => {
    renderPage()
    await waitForDocs()
    fireEvent.click(screen.getByRole('button', { name: '分块' }))

    await waitFor(() => expect(screen.getByText('分块 #0')).toBeTruthy())
    expect(screen.getByText(/第 1 \/ 3 页/)).toBeTruthy()
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes('/v1/knowledge/search')),
    ).toBe(false)
    expect(
      fetchMock.mock.calls.some(([url]) => String(url) === '/v1/knowledge/documents/d1/chunks'),
    ).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('分块 #20')).toBeTruthy()
  })
})

describe('知识中心 · 异步入库取消', () => {
  test('显示进度、可取消，并写明取消后不留任何数据', async () => {
    renderPage()
    await waitForDocs()

    fireEvent.change(screen.getByLabelText('或直接粘贴文本'), {
      target: { value: '一段要后台入库的文本' },
    })
    fireEvent.click(screen.getByRole('button', { name: '后台入库（可取消）' }))

    await waitFor(() => expect(screen.getByText('后台入库任务')).toBeTruthy())
    expect(screen.getByText(/3 \/ 10 分块 · 30%/)).toBeTruthy()
    expect(screen.getByText(/取消后本次不会留下任何数据/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '取消任务' }))
    await waitFor(() =>
      expect(screen.getByText(/后台任务已取消：本次未写入任何数据（全回滚）/)).toBeTruthy(),
    )
    // 取消后要刷新列表，让用户看到真实状态。
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).startsWith('/v1/knowledge/documents?'),
        ),
      ).toHaveLength(2),
    )
  })
})
