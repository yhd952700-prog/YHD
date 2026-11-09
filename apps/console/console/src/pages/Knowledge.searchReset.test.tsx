/**
 * 文档账本搜索必须回第 1 页：服务端分页 + 当前页过滤时，
 * 停在第 N 页会让用户看见「当前页没有文档匹配」而以为没搜到。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Knowledge } from './Knowledge'

const TOTAL = 1000

function doc(i: number) {
  return {
    document_id: `doc-${String(i).padStart(4, '0')}`,
    source: `文档${String(i).padStart(4, '0')}.md`,
    title: `文档${String(i).padStart(4, '0')}`,
    chunk_count: 1,
    content_hash: `hash-${i}`,
    ingested_at: '2026-11-09 10:00:00',
  }
}

function page(offset: number) {
  return {
    documents: Array.from({ length: 20 }, (_, i) => doc(offset + i)),
    total: TOTAL,
    limit: 20,
    offset,
  }
}

function json(data: unknown) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
    text: async () => JSON.stringify(data),
    headers: new Headers(),
  } as unknown as Response
}

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  if (url.includes('/v1/knowledge/status')) {
    return json({
      enabled: true,
      provider: 'local',
      model: 'bge',
      vectors: 0,
      reason: null,
      message: 'ok',
    })
  }
  if (url.includes('/v1/knowledge/documents')) {
    const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset') ?? 0)
    return json(page(offset))
  }
  return json({})
})

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  fetchMock.mockClear()
})

describe('知识库文档搜索', () => {
  it('在第 2 页搜索时回到第 1 页，并显示第 1 页的命中', async () => {
    const view = render(<Knowledge query="" onOpenSettings={() => undefined} />)
    await waitFor(() => expect(screen.getByText('文档0000.md')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(screen.getByText('文档0020.md')).toBeTruthy())
    expect(screen.queryByText('文档0000.md')).toBeNull()

    const zeroOffsetBefore = fetchMock.mock.calls.filter(([url]) =>
      String(url).includes('/documents?limit=20&offset=0'),
    ).length
    view.rerender(<Knowledge query="文档0005" onOpenSettings={() => undefined} />)

    await waitFor(() => expect(screen.getByText('文档0005.md')).toBeTruthy())
    expect(screen.queryByText('当前页没有文档匹配')).toBeNull()
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).includes('/documents?limit=20&offset=0'),
        ),
      ).toHaveLength(zeroOffsetBefore + 1),
    )
  })
})
