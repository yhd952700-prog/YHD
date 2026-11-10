/**
 * 知识库中心：分类缺口必须如实说明、列表来源与入库时间可见、检索命中带来源、
 * 无权限时不能显示成空列表。
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { KnowledgeCenter } from './KnowledgeCenter'

const STATUS = {
  enabled: true,
  provider: 'local',
  model: 'bge-m3',
  vectors: 12,
  reason: null,
  message: 'ok',
}

const DOC = {
  document_id: 'doc-1',
  source: '员工手册.md',
  title: '员工手册',
  chunk_count: 3,
  content_hash: 'abcdef1234567890',
  ingested_at: '2026-11-09 10:00:00',
}

const SEARCH = {
  query: '报销',
  top_k: 8,
  hits: [
    {
      doc_id: 'doc-1#0',
      score: 0.87,
      text: '报销制度见第 3 节。',
      metadata: { source: '制度.pdf', page: 3 },
    },
  ],
  count: 1,
  enabled: true,
  retrieval: { mode: 'vector', status: 'ok' },
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

function fail(status: number, detail: string) {
  return {
    ok: false,
    status,
    json: async () => ({ detail }),
    text: async () => JSON.stringify({ detail }),
    headers: new Headers(),
  } as unknown as Response
}

let documentsStatus = 200
let documentsBody: unknown = { documents: [DOC], total: 1, limit: 20, offset: 0 }

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input)
  if (url.includes('/v1/knowledge/status')) return json(STATUS)
  if (url.includes('/chunks')) {
    return json({
      document_id: 'doc-1',
      chunks: [
        {
          chunk_id: 'doc-1#0',
          chunk_index: 0,
          text: '第一章 总则',
          heading_path: ['第一章'],
          context_header: '员工手册',
        },
      ],
      count: 1,
    })
  }
  if (url.includes('/v1/knowledge/documents?')) {
    if (documentsStatus !== 200) return fail(documentsStatus, 'not found')
    return json(documentsBody)
  }
  if (url.includes('/v1/knowledge/search')) return json(SEARCH)
  return json({})
})

beforeEach(() => {
  documentsStatus = 200
  documentsBody = { documents: [DOC], total: 1, limit: 20, offset: 0 }
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  fetchMock.mockClear()
})

describe('知识库中心', () => {
  it('文档列表显示来源与入库时间，分类视图如实说明本地服务缺口', async () => {
    render(<KnowledgeCenter />)
    await waitFor(() =>
      expect(screen.getAllByText('员工手册.md').length).toBeGreaterThan(0),
    )
    expect(screen.getByText('入库时间')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '客户资料' }))
    expect(screen.getByText('「客户资料」分类暂时无法归档')).toBeTruthy()
    expect(screen.getByText('本地服务还没提供')).toBeTruthy()
    expect(screen.getByText(/GET \/v1\/knowledge\/documents 只返回/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '全部文档' }))
    await waitFor(() => expect(screen.getByLabelText('知识库检索输入')).toBeTruthy())
  })

  it('检索命中显示分数、来源与页码', async () => {
    render(<KnowledgeCenter />)
    await waitFor(() => expect(screen.getByLabelText('知识库检索输入')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('知识库检索输入'), { target: { value: '报销' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))

    await waitFor(() => expect(screen.getByText('报销制度见第 3 节。')).toBeTruthy())
    expect(screen.getByText('制度.pdf · 第 3 页')).toBeTruthy()
    expect(screen.getByText('分数 0.870')).toBeTruthy()
  })

  it('文档接口无权限时显示「无权限查看」，不显示空列表', async () => {
    documentsStatus = 404
    render(<KnowledgeCenter />)
    await waitFor(() => expect(screen.getByText('无权限查看知识库文档')).toBeTruthy())
    expect(screen.queryByText('还没有文档')).toBeNull()
  })

  it('上传前做本地类型预检，不支持的类型不发请求', async () => {
    render(<KnowledgeCenter />)
    await waitFor(() => expect(screen.getByLabelText('选择要上传的文档')).toBeTruthy())

    const input = screen.getByLabelText('选择要上传的文档') as HTMLInputElement
    const file = new File(['x'], 'bad.exe', { type: 'application/octet-stream' })
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByText(/类型不支持/)).toBeTruthy())
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/upload'))).toBe(false)
  })
})
