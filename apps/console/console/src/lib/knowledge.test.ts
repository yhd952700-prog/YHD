import { beforeEach, describe, expect, test } from 'vitest'
import {
  MAX_INGEST_CHARS,
  formatBytes,
  hitsForDocument,
  hitChunkIndex,
  hitDocumentId,
  loadDocs,
  makeSample,
  saveDocs,
  scoreLabel,
  scoreNote,
  toChunkViews,
  upsertDoc,
  validateIngestText,
} from './knowledge'
import type { KnowledgeDoc, KnowledgeHit } from './knowledge'

function doc(overrides: Partial<KnowledgeDoc> = {}): KnowledgeDoc {
  return {
    id: 'd1',
    name: 'a.md',
    bytes: 1024,
    chars: 900,
    chunks: 3,
    at: 1_700_000_000,
    state: 'ok',
    sample: 'hello',
    source: 'file',
    ...overrides,
  }
}

function hit(overrides: Partial<KnowledgeHit> = {}): KnowledgeHit {
  return {
    doc_id: 'd1#2',
    score: 0.81,
    text: 'chunk text',
    metadata: { document_id: 'd1', chunk_index: 2 },
    ...overrides,
  }
}

/**
 * 本仓库的 vitest 环境没有暴露 `localStorage`（`typeof localStorage === 'undefined'`），
 * 而浏览器里一定有。这里用一份内存实现顶上，测的是我们自己的读写/隔离逻辑，
 * 不是 jsdom 的存储实现。
 */
class MemoryStorage implements Storage {
  private data = new Map<string, string>()

  get length(): number {
    return this.data.size
  }

  clear(): void {
    this.data.clear()
  }

  getItem(key: string): string | null {
    const value = this.data.get(key)
    return value === undefined ? null : value
  }

  key(index: number): string | null {
    return Array.from(this.data.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.data.delete(key)
  }

  setItem(key: string, value: string): void {
    this.data.set(key, String(value))
  }
}

beforeEach(() => {
  Object.defineProperty(globalThis, 'localStorage', {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  })
})

describe('validateIngestText', () => {
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

describe('makeSample', () => {
  test('压平空白并截断到探针长度', () => {
    expect(makeSample('  a\n\n b   c ')).toBe('a b c')
    expect(makeSample('x'.repeat(500)).length).toBe(400)
  })

  test('空文本得到空探针（上层据此拒绝预览）', () => {
    expect(makeSample('   ')).toBe('')
  })
})

describe('formatBytes', () => {
  test('按字节 / KB / MB 展示', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(3 * 1024 * 1024)).toBe('3.0 MB')
  })
})

describe('本机导入记录', () => {
  test('按 principal 隔离：换账号不会看到别人的文档', () => {
    saveDocs('alice', [doc()])
    expect(loadDocs('alice')).toHaveLength(1)
    expect(loadDocs('bob')).toHaveLength(0)
  })

  test('同 id 覆盖而不是重复追加', () => {
    upsertDoc('alice', doc())
    upsertDoc('alice', doc({ chunks: 5 }))
    const rows = loadDocs('alice')
    expect(rows).toHaveLength(1)
    expect(rows[0].chunks).toBe(5)
  })

  test('损坏的 localStorage 不会让页面崩', () => {
    localStorage.setItem('liuhao.knowledge.docs.v1.alice', '{not json')
    expect(loadDocs('alice')).toEqual([])
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

describe('检索命中 → 分块', () => {
  test('优先按 metadata.document_id 归属', () => {
    expect(hitDocumentId(hit())).toBe('d1')
    expect(hitsForDocument([hit(), hit({ doc_id: 'd2#1', metadata: { document_id: 'd2' } })], 'd1')).toHaveLength(1)
  })

  test('metadata 缺失时从 `<id>#<index>` 主键兜底', () => {
    expect(hitDocumentId(hit({ doc_id: 'd9#4', metadata: {} }))).toBe('d9')
    expect(hitChunkIndex(hit({ doc_id: 'd9#4', metadata: {} }))).toBe(4)
  })

  test('分块按 chunk_index 升序，而不是按相似度', () => {
    const views = toChunkViews([
      hit({ doc_id: 'd1#7', metadata: { document_id: 'd1', chunk_index: 7 }, score: 0.9 }),
      hit({ doc_id: 'd1#1', metadata: { document_id: 'd1', chunk_index: 1 }, score: 0.4 }),
    ])
    expect(views.map((view) => view.index)).toEqual([1, 7])
  })
})
