/**
 * 知识库客户端 —— 只调用后端**真实存在**的三个端点。
 *
 * 后端（`src/api/routes/knowledge.py`）目前只有：
 *   GET  /v1/knowledge/status       可用性 / 向量数 / embedding provider（永远 200）
 *   POST /v1/knowledge/documents    按纯文本入库（响应里给出真实分块数）
 *   POST /v1/knowledge/search       向量检索（响应里给出真实分块与余弦分数）
 *
 * **没有**的东西这里一个都不编：没有文档列表、没有按文档读分块、没有删除、
 * 没有文件上传（只收 JSON 文本）、没有异步任务/进度/取消接口。
 * 所以：
 *   * 文档列表来自**本机导入记录**（localStorage），并明确标注"本机记录"；
 *   * 分块预览不是伪造的切分，而是拿本机保存的开头片段再发一次**真实检索**，
 *     把服务端返回的、属于该文档的真实分块摊开；
 *   * 进度只有两段是真的：文件读取（FileReader 有真实字节进度）、入库结果；
 *     中间"服务端分块与向量化"没有进度接口，界面如实写"等待中"，不假装百分比。
 */

import { apiFetch } from './api'

/** 服务端 `IngestRequest.text` 的 `max_length`。 */
export const MAX_INGEST_CHARS = 200_000
/** 服务端 `SearchRequest.top_k` 的上限。 */
export const MAX_TOP_K = 20
/** 本机保存的开头片段长度：既够做一次有意义的检索，也不把 localStorage 撑爆。 */
export const SAMPLE_CHARS = 400

const LOG_VERSION = 1
const LOG_PREFIX = 'liuhao.knowledge.docs'
const MAX_LOG_ENTRIES = 200

export interface KnowledgeStatus {
  enabled: boolean
  provider: string | null
  model: string | null
  vectors: number
  reason: string | null
  message: string
}

export interface KnowledgeIngestResult {
  document_id: string
  chunks: number
  vectors: number
}

export interface KnowledgeHit {
  /** 向量库主键，形如 `<document_id>#<chunk_index>`。 */
  doc_id: string
  score: number
  text: string
  metadata: Record<string, unknown>
}

export interface KnowledgeSearchResult {
  query: string
  top_k: number
  hits: KnowledgeHit[]
  count: number
}

/** 一条本机导入记录的真实状态：只有"已入库"和"失败"两种是观测到的结果。 */
export type KnowledgeDocState = 'ok' | 'failed'

export interface KnowledgeDoc {
  /** 服务端返回的 document_id；失败记录用本机临时 id。 */
  id: string
  name: string
  /** UTF-8 字节数（本机计算）。 */
  bytes: number
  chars: number
  /** 服务端入库时上报的分块数；失败为 0。 */
  chunks: number
  /** 观测到结果的时间（epoch 秒）。 */
  at: number
  state: KnowledgeDocState
  error?: string
  /** 开头片段，仅用于"分块预览"时向服务端发起一次真实检索。 */
  sample: string
  source: 'file' | 'text'
}

/** 一条从服务端检索回来的真实分块。 */
export interface ChunkView {
  index: number
  score: number
  text: string
  docId: string
}

// ---------------------------------------------------------------------------
// 纯展示工具
// ---------------------------------------------------------------------------

export function byteLength(text: string): number {
  try {
    return new TextEncoder().encode(text).length
  } catch {
    // 极老环境没有 TextEncoder：按字符数近似，仅影响"大小"列。
    return text.length
  }
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return '—'
  if (bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 压平空白后取开头一段，作为检索探针（不是"本地切分预览"）。 */
export function makeSample(text: string, limit = SAMPLE_CHARS): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length <= limit ? flat : flat.slice(0, limit)
}

/** 入库前的本地校验：返回中文原因，`null` 表示可以发送。 */
export function validateIngestText(text: string): string | null {
  if (!text.trim()) return '内容为空：没有可入库的文本'
  if (text.length > MAX_INGEST_CHARS) {
    return `文本 ${text.length.toLocaleString()} 字符，超过服务端单次上限 ${MAX_INGEST_CHARS.toLocaleString()} 字符；请拆成多个文档分别导入`
  }
  return null
}

export function isAbortError(err: unknown): boolean {
  return err instanceof Error && err.name === 'AbortError'
}

// ---------------------------------------------------------------------------
// 本机导入记录
// ---------------------------------------------------------------------------

function logKey(principal: string): string {
  return `${LOG_PREFIX}.v${LOG_VERSION}.${principal || 'anonymous'}`
}

function isDocLike(value: unknown): value is KnowledgeDoc {
  if (!value || typeof value !== 'object') return false
  const row = value as Partial<KnowledgeDoc>
  return (
    typeof row.id === 'string' &&
    typeof row.name === 'string' &&
    (row.state === 'ok' || row.state === 'failed') &&
    typeof row.at === 'number'
  )
}

function normalizeDoc(row: KnowledgeDoc): KnowledgeDoc {
  return {
    id: row.id,
    name: row.name,
    bytes: Number.isFinite(row.bytes) ? row.bytes : 0,
    chars: Number.isFinite(row.chars) ? row.chars : 0,
    chunks: Number.isFinite(row.chunks) ? row.chunks : 0,
    at: row.at,
    state: row.state,
    error: typeof row.error === 'string' ? row.error : undefined,
    sample: typeof row.sample === 'string' ? row.sample : '',
    source: row.source === 'text' ? 'text' : 'file',
  }
}

export function loadDocs(principal: string): KnowledgeDoc[] {
  try {
    const raw = localStorage.getItem(logKey(principal))
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter(isDocLike).map(normalizeDoc).slice(0, MAX_LOG_ENTRIES)
  } catch {
    // 隐私模式 / 坏数据：退回空列表，不阻塞页面。
    return []
  }
}

export function saveDocs(principal: string, docs: KnowledgeDoc[]): void {
  try {
    localStorage.setItem(
      logKey(principal),
      JSON.stringify(docs.slice(0, MAX_LOG_ENTRIES)),
    )
  } catch {
    // 配额满 / 隐私模式：只是失去本机记录，不影响服务端已入库的事实。
  }
}

/** 写入一条记录并返回新列表（同 id 覆盖，最新的排在最前）。 */
export function upsertDoc(principal: string, doc: KnowledgeDoc): KnowledgeDoc[] {
  const next = [doc, ...loadDocs(principal).filter((item) => item.id !== doc.id)].slice(
    0,
    MAX_LOG_ENTRIES,
  )
  saveDocs(principal, next)
  return next
}

// ---------------------------------------------------------------------------
// 真实端点
// ---------------------------------------------------------------------------

export async function ingestKnowledge(
  text: string,
  options: {
    name: string
    source: 'file' | 'text'
    docId?: string
    signal?: AbortSignal
  },
): Promise<KnowledgeIngestResult> {
  return apiFetch<KnowledgeIngestResult>('/v1/knowledge/documents', {
    method: 'POST',
    body: JSON.stringify({
      text,
      ...(options.docId ? { doc_id: options.docId } : {}),
      metadata: { name: options.name, source: options.source },
    }),
    signal: options.signal,
  })
}

export async function searchKnowledge(
  query: string,
  topK = 8,
  signal?: AbortSignal,
  threshold?: number,
): Promise<KnowledgeSearchResult> {
  return apiFetch<KnowledgeSearchResult>('/v1/knowledge/search', {
    method: 'POST',
    body: JSON.stringify({
      query,
      top_k: Math.min(Math.max(1, topK), MAX_TOP_K),
      ...(threshold === undefined ? {} : { threshold }),
    }),
    signal,
  })
}

// ---------------------------------------------------------------------------
// 检索结果 → 分块视图
// ---------------------------------------------------------------------------

/** 命中归属的 document_id（优先 metadata，缺失时从 `<doc>#<index>` 主键里拆）。 */
export function hitDocumentId(hit: KnowledgeHit): string {
  const declared = hit.metadata?.document_id
  if (typeof declared === 'string' && declared) return declared
  const hash = hit.doc_id.lastIndexOf('#')
  return hash >= 0 ? hit.doc_id.slice(0, hash) : hit.doc_id
}

/** 命中属于哪个分块（同上，先 metadata.chunk_index，再从主键尾部取）。 */
export function hitChunkIndex(hit: KnowledgeHit): number {
  const declared = hit.metadata?.chunk_index
  if (typeof declared === 'number' && Number.isFinite(declared)) return declared
  const parsed = Number.parseInt(String(declared ?? ''), 10)
  if (Number.isFinite(parsed)) return parsed
  const hash = hit.doc_id.lastIndexOf('#')
  const tail = hash >= 0 ? Number.parseInt(hit.doc_id.slice(hash + 1), 10) : Number.NaN
  return Number.isFinite(tail) ? tail : -1
}

export function hitsForDocument(hits: KnowledgeHit[], documentId: string): KnowledgeHit[] {
  return hits.filter((hit) => hitDocumentId(hit) === documentId)
}

export function toChunkViews(hits: KnowledgeHit[]): ChunkView[] {
  return hits
    .map((hit) => ({
      index: hitChunkIndex(hit),
      score: Number.isFinite(hit.score) ? hit.score : 0,
      text: hit.text,
      docId: hit.doc_id,
    }))
    .sort((left, right) => left.index - right.index)
}

/**
 * 取回一篇文档的真实分块。
 *
 * 服务端没有"按文档读分块"的接口，所以这里用本机保存的开头片段做一次真实检索，
 * 再从响应里筛出属于该文档的命中。`top_k` 上限 20 是服务端约束 —— 因此界面
 * 必须如实写"最多 20 条"，不能让人误以为看到了完整切分。
 */
export async function previewDocumentChunks(
  doc: KnowledgeDoc,
  signal?: AbortSignal,
): Promise<{ chunks: ChunkView[]; examined: number }> {
  const probe = (doc.sample || doc.name).trim()
  if (!probe) {
    throw new Error('这篇文档没有可用于检索的开头片段，无法从服务端取回分块')
  }
  const result = await searchKnowledge(probe.slice(0, SAMPLE_CHARS), MAX_TOP_K, signal)
  const hits = Array.isArray(result.hits) ? result.hits : []
  return { chunks: toChunkViews(hitsForDocument(hits, doc.id)), examined: hits.length }
}

/** 文档 id → 展示名；用于把检索命中映射回"这是哪篇文档"。 */
export function docNameMap(docs: KnowledgeDoc[]): Map<string, string> {
  return new Map(docs.map((doc) => [doc.id, doc.name]))
}
