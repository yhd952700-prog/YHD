import { describe, expect, test } from 'vitest'
import { ApiError } from './api'
import {
  CHUNK_PAGE_SIZE,
  MAX_INGEST_CHARS,
  classifyUploadFailure,
  docDisplayName,
  documentsPath,
  formatBytes,
  hitChunkIndex,
  hitDocumentId,
  isTerminalJobStatus,
  jobStatusLabel,
  paginateChunks,
  parseServerTime,
  scoreLabel,
  scoreNote,
  sortChunks,
  toDocSummary,
  toIngestJob,
  UPLOAD_MAX_BYTES,
  validateIngestText,
  validateUploadFile,
} from './knowledge'
import type { DocumentChunk, KnowledgeHit } from './knowledge'

function hit(overrides: Partial<KnowledgeHit> = {}): KnowledgeHit {
  return {
    doc_id: 'd1#2',
    score: 0.81,
    text: 'chunk text',
    metadata: { document_id: 'd1', chunk_index: 2 },
    ...overrides,
  }
}

function chunk(index: number): DocumentChunk {
  return {
    chunkId: `d1#${index}`,
    index,
    text: `正文 ${index}`,
    headingPath: ['第一章'],
    contextHeader: '第一章',
  }
}

function abortError(): Error {
  const error = new Error('请求已取消')
  error.name = 'AbortError'
  return error
}

describe('服务端时间解析', () => {
  test('SQLite datetime 是 UTC 且没有时区标记，不能当成本地时间', () => {
    // 假如按本地时间解析，东八区会得到 08:00Z 而不是 12:00Z。
    expect(parseServerTime('2026-11-06 12:00:00')).toBe(Date.UTC(2026, 10, 6, 12) / 1000)
  })

  test('带时区的 ISO 串（任务快照）按原样解析', () => {
    expect(parseServerTime('2026-11-06T12:00:00+00:00')).toBe(
      Date.UTC(2026, 10, 6, 12) / 1000,
    )
  })

  test('数字秒 / 毫秒都能识别；坏数据返回 0 而不是 NaN', () => {
    expect(parseServerTime(1_700_000_000)).toBe(1_700_000_000)
    expect(parseServerTime(1_700_000_000_000)).toBe(1_700_000_000)
    expect(parseServerTime('')).toBe(0)
    expect(parseServerTime('not a time')).toBe(0)
    expect(parseServerTime(null)).toBe(0)
  })
})

describe('文档账本行 → 展示模型', () => {
  test('真实字段全部保留', () => {
    const doc = toDocSummary({
      document_id: 'q3-retro',
      chunks: 12,
      source: 'q3-retro.md',
      title: null,
      content_hash: 'abc',
      ingested_at: '2026-11-06 12:00:00',
    })
    expect(doc).not.toBeNull()
    expect(doc?.id).toBe('q3-retro')
    expect(doc?.chunks).toBe(12)
    expect(doc?.source).toBe('q3-retro.md')
    expect(doc?.ingestedAt).toBe(Date.UTC(2026, 10, 6, 12) / 1000)
  })

  test('没有 document_id 的行直接丢弃，不生成假文档', () => {
    expect(toDocSummary({ chunks: 3 })).toBeNull()
    expect(toDocSummary(null)).toBeNull()
  })

  test('展示名按 来源 > 标题 > id 兜底，绝不返回空串', () => {
    const base = toDocSummary({ document_id: 'd1', chunks: 1 })
    expect(base).not.toBeNull()
    if (!base) return
    expect(docDisplayName(base)).toBe('d1')
    expect(docDisplayName({ ...base, title: '标题', source: null })).toBe('标题')
    expect(docDisplayName({ ...base, source: 'a.md' })).toBe('a.md')
  })
})

describe('文档列表请求参数', () => {
  test('limit 夹在 1..200，offset 不为负', () => {
    expect(documentsPath(20, 40)).toBe('/v1/knowledge/documents?limit=20&offset=40')
    expect(documentsPath(999, -5)).toBe('/v1/knowledge/documents?limit=200&offset=0')
    expect(documentsPath(0, 0)).toBe('/v1/knowledge/documents?limit=20&offset=0')
  })
})

describe('上传预检与失败话术', () => {
  test('PDF / Word / 网页 / 纯文本都放行（真解析，不假装）', () => {
    expect(validateUploadFile({ name: 'report.pdf', size: 10 })).toBeNull()
    expect(validateUploadFile({ name: 'slides.docx', size: 10 })).toBeNull()
    expect(validateUploadFile({ name: 'page.html', size: 10 })).toBeNull()
    expect(validateUploadFile({ name: 'page.htm', size: 10 })).toBeNull()
    expect(validateUploadFile({ name: 'notes.txt', size: 10 })).toBeNull()
    expect(validateUploadFile({ name: 'notes.md', size: 10 })).toBeNull()
  })

  test('真正不支持的类型才在本地拦下，并列出支持范围', () => {
    const failure = validateUploadFile({ name: 'archive.zip', size: 10 })
    expect(failure?.kind).toBe('unsupported-type')
    expect(failure?.title).toBe('类型不支持')
    expect(failure?.message).toContain('.pdf')
    expect(failure?.message).toContain('.docx')
  })

  test('超过 20MB 与空文件在本地就拦下', () => {
    const tooBig = validateUploadFile({ name: 'a.pdf', size: UPLOAD_MAX_BYTES + 1 })
    expect(tooBig?.kind).toBe('too-large')
    expect(tooBig?.title).toBe('文件太大')
    expect(tooBig?.message).toContain('20.0 MB')

    expect(validateUploadFile({ name: 'a.txt', size: 0 })?.kind).toBe('empty')
  })

  test('提取质量差的 422 有单独话术，保留服务端原话', () => {
    const failure = classifyUploadFailure(
      new ApiError(
        422,
        '该文件可能是扫描件或字体嵌入异常，提取质量差（乱码 40 字符，占比 61.5%）；如确认仍需入库，请重试并设置 allow_low_quality=true',
      ),
      { name: 'broken.txt', size: 100 },
    )
    expect(failure.kind).toBe('low-quality')
    expect(failure.title).toBe('提取质量差')
    expect(failure.message).toContain('allow_low_quality=true')
  })

  test('四类失败给四套不同的话术', () => {
    const file = { name: 'a.md', size: 100 }
    const unsupported = classifyUploadFailure(new ApiError(415, ''), {
      name: 'a.pdf',
      size: 100,
    })
    const tooLarge = classifyUploadFailure(new ApiError(413, ''), {
      name: 'a.txt',
      size: UPLOAD_MAX_BYTES + 1,
    })
    const badEncoding = classifyUploadFailure(
      new ApiError(400, '文件不是 UTF-8 编码：invalid start byte'),
      file,
    )
    const server = classifyUploadFailure(new ApiError(503, '知识库未启用：未配置 provider'), file)

    expect(unsupported.kind).toBe('unsupported-type')
    expect(tooLarge.kind).toBe('too-large')
    expect(badEncoding.kind).toBe('bad-encoding')
    expect(server.kind).toBe('unavailable')

    expect(unsupported.title).toBe('类型不支持')
    expect(tooLarge.title).toBe('文件太大')
    expect(badEncoding.title).toBe('编码不对')
    expect(server.title).toBe('服务端不可用')

    // 四个标题不同 —— 不能一律「上传失败」。
    expect(
      new Set([unsupported.title, tooLarge.title, badEncoding.title, server.title]).size,
    ).toBe(4)
    expect(badEncoding.message).toContain('UTF-8')
  })

  test('5xx / 网络中断 / 取消各有独立话术', () => {
    expect(classifyUploadFailure(new ApiError(500, 'boom'), { name: 'a.md', size: 1 }).title).toBe(
      '服务端错误',
    )
    expect(classifyUploadFailure(new ApiError(0, ''), { name: 'a.md', size: 1 }).kind).toBe(
      'network',
    )
    const cancelled = classifyUploadFailure(abortError(), { name: 'a.md', size: 1 })
    expect(cancelled.kind).toBe('cancelled')
    expect(cancelled.message).toContain('可能已经完成')
  })

  test('401 / 403 不与「文件问题」混为一谈', () => {
    expect(classifyUploadFailure(new ApiError(401, ''), { name: 'a.md', size: 1 }).title).toBe(
      '登录已过期',
    )
    expect(classifyUploadFailure(new ApiError(403, '没有权限'), { name: 'a.md', size: 1 }).kind).toBe(
      'forbidden',
    )
  })
})

describe('分块读取与分页', () => {
  test('服务端分块按 chunk_index 兜底排序', () => {
    const sorted = sortChunks([chunk(7), chunk(1), chunk(4)])
    expect(sorted.map((item) => item.index)).toEqual([1, 4, 7])
  })

  test('45 个分块分成 3 页，最后一页不越界', () => {
    const chunks = Array.from({ length: 45 }, (_, index) => chunk(index))
    const page = paginateChunks(chunks, 2, CHUNK_PAGE_SIZE)
    expect(page.pageCount).toBe(3)
    expect(page.pageIndex).toBe(2)
    expect(page.start).toBe(40)
    expect(page.end).toBe(45)
    expect(page.items).toHaveLength(5)
  })

  test('页码越界自动夹到最后一页，不会出现空白页', () => {
    const chunks = Array.from({ length: 45 }, (_, index) => chunk(index))
    const page = paginateChunks(chunks, 99, CHUNK_PAGE_SIZE)
    expect(page.pageIndex).toBe(2)
    expect(paginateChunks([], 5).pageIndex).toBe(0)
    expect(paginateChunks([], 5).start).toBe(0)
  })
})

describe('异步任务快照', () => {
  test('终态识别：queued / running 不算结束', () => {
    expect(isTerminalJobStatus('succeeded')).toBe(true)
    expect(isTerminalJobStatus('failed')).toBe(true)
    expect(isTerminalJobStatus('cancelled')).toBe(true)
    expect(isTerminalJobStatus('running')).toBe(false)
    expect(isTerminalJobStatus('queued')).toBe(false)
  })

  test('未知状态原样显示，不冒充「已完成」', () => {
    expect(jobStatusLabel('succeeded')).toBe('已完成')
    expect(jobStatusLabel('some-new-status')).toBe('some-new-status')
  })

  test('进度百分比夹在 0..100，全回滚策略原样带出', () => {
    const job = toIngestJob({
      job_id: 'j1',
      document_id: 'd1',
      status: 'running',
      phase: 'embedding',
      total_chunks: 10,
      processed_chunks: 4,
      progress_percent: 1200,
      cancel_requested: false,
      partial_commit_policy: 'all-or-nothing（取消后不写入任何分块，不会留下半成品）',
    })
    expect(job.progressPercent).toBe(100)
    expect(job.processedChunks).toBe(4)
    expect(job.partialCommitPolicy).toContain('all-or-nothing')
    expect(job.result).toBeNull()
    expect(job.error).toBeNull()
  })
})

describe('同步入库参数校验', () => {
  test('空内容给出中文原因', () => {
    expect(validateIngestText('   \n  ')).toContain('内容为空')
  })

  test('超过服务端字符上限时拒绝，并给出具体数量', () => {
    const tooLong = 'x'.repeat(MAX_INGEST_CHARS + 1)
    const message = validateIngestText(tooLong)
    expect(message).not.toBeNull()
    expect(message).toContain('200,000')
  })

  test('正常文本放行', () => {
    expect(validateIngestText('一段可以入库的文本')).toBeNull()
  })
})

describe('展示工具', () => {
  test('字节格式化', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB')
  })
})

describe('分数语义跟随检索模式', () => {
  test('hybrid 是 RRF 融合分，不能说成余弦相似度', () => {
    expect(scoreLabel('hybrid')).toContain('RRF')
    expect(scoreNote('hybrid')).toContain('不是相似度百分比')
  })

  test('vector 模式才是余弦相似度', () => {
    expect(scoreLabel('vector')).toBe('余弦相似度')
    expect(scoreNote('vector')).toContain('余弦相似度')
  })

  test('未知模式给中性说法，不冒充相似度', () => {
    expect(scoreLabel(undefined)).toBe('排序分')
  })
})

describe('检索命中归属', () => {
  test('优先按 metadata.document_id / chunk_index', () => {
    expect(hitDocumentId(hit())).toBe('d1')
    expect(hitChunkIndex(hit())).toBe(2)
  })

  test('metadata 缺失时从 `<id>#<index>` 主键兜底', () => {
    expect(hitDocumentId(hit({ doc_id: 'd9#4', metadata: {} }))).toBe('d9')
    expect(hitChunkIndex(hit({ doc_id: 'd9#4', metadata: {} }))).toBe(4)
  })
})
