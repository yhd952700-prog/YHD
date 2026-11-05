/**
 * 知识库客户端 —— 只调用后端**真实存在**的端点（`src/api/routes/knowledge.py`）。
 *
 * 本文件对应的后端契约（`79bd69c8` 起）：
 *   GET    /v1/knowledge/status                      可用性 / 向量数 / provider（永远 200）
 *   POST   /v1/knowledge/documents                   按纯文本同步入库
 *   POST   /v1/knowledge/search                      混合检索（RRF 融合 / 纯向量）
 *   GET    /v1/knowledge/documents?limit&offset      服务端文档账本（分页）
 *   GET    /v1/knowledge/documents/{id}/chunks       按文档读**全部**分块（带序号 + 标题路径）
 *   DELETE /v1/knowledge/documents/{id}              整篇删除（管理员权限，chunks + 向量 + 哈希一起删）
 *   POST   /v1/knowledge/documents/upload            multipart 上传（PDF/DOCX/HTML/TXT/MD，≤20MB）
 *   POST   /v1/knowledge/documents/upload/jobs       异步收文件（大文件：按页进度 + 可取消 + 全回滚）
 *   POST   /v1/knowledge/ingest/jobs                 异步纯文本入库（202，返回 job id）
 *   GET    /v1/knowledge/ingest/jobs/{job_id}        任务快照（进度 / 阶段 / 结果）
 *   POST   /v1/knowledge/ingest/jobs/{job_id}/cancel 请求取消（下一个页/分块边界生效，全回滚）
 *
 * 三条必须守住的诚实边界：
 *   1. 文档列表是**服务端真相**，不再是本机 localStorage 记录；
 *   2. 取消异步入库 = 全回滚，界面必须写"取消后本次不会留下任何数据"，
 *      不能写成"可能部分入库"；
 *   3. 上传失败按类型分话术（类型不支持 / 太大 / 编码不对 / 服务端错误），
 *      后端给了原因就原样透出，后端没给才用本地兜底文案。
 */

import { ApiError, apiFetch } from './api'
import { clearSession, tokenHeaders, UNAUTHENTICATED_EVENT } from './auth'
import { describeDetail } from './errorMessage'

/** 服务端 `IngestRequest.text` 的 `max_length`。 */
export const MAX_INGEST_CHARS = 200_000
/** 服务端 `SearchRequest.top_k` 的上限。 */
export const MAX_TOP_K = 20
/** 文档列表默认每页条数（服务端 `limit` 上限 200）。 */
export const DOC_PAGE_SIZE = 20
/** 分块预览默认每页条数：几千条分块一次进 DOM 会卡死，必须分页渲染。 */
export const CHUNK_PAGE_SIZE = 20
/** 上传大小上限，与后端 `_MAX_UPLOAD_BYTES` 一致。 */
export const UPLOAD_MAX_BYTES = 20 * 1024 * 1024
/** 超过这个大小走异步文件任务：小文件同步快进快出，大文件要进度和取消。 */
export const SYNC_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
/** 允许上传的后缀，与后端 `SUPPORTED_SUFFIXES` 一致。 */
export const UPLOAD_SUFFIXES = ['.pdf', '.docx', '.html', '.htm', '.txt', '.md'] as const
export const UPLOAD_ACCEPT = UPLOAD_SUFFIXES.join(',')
/** 上传 + 同步入库可能很慢，超时必须比普通请求宽。 */
const UPLOAD_TIMEOUT_MS = 180_000

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
  /** 服务端按内容哈希去重：重复导入不会写入新分块。 */
  deduplicated?: boolean
  matched_document_id?: string | null
  /** multipart 上传回填的原始文件名。 */
  source?: string
}

/** 服务端检索模式：`hybrid` 是向量 + BM25 的 RRF 融合，`vector` 才是纯余弦。 */
export interface RetrievalInfo {
  mode?: string
  fts_available?: boolean
  rrf_k?: number
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
  retrieval?: RetrievalInfo
}

// ---------------------------------------------------------------------------
// 文档账本（服务端真相）
// ---------------------------------------------------------------------------

export interface KnowledgeDocSummary {
  id: string
  /** 服务端 `source`（上传时是原始文件名，文本入库时是我们写进去的展示名）。 */
  source: string | null
  title: string | null
  chunks: number
  contentHash: string | null
  /** 入库时间（epoch 秒）；服务端时间格式异常时为 0，界面显示「未知时间」。 */
  ingestedAt: number
  /** 服务端原始时间串，用于 tooltip 展示。 */
  ingestedAtRaw: string | null
}

export interface KnowledgeDocPage {
  documents: KnowledgeDocSummary[]
  total: number
  limit: number
  offset: number
}

export interface DocumentChunk {
  chunkId: string
  index: number
  text: string
  headingPath: string[]
  contextHeader: string
}

export interface DocumentChunks {
  documentId: string
  chunks: DocumentChunk[]
  count: number
}

/**
 * SQLite `datetime('now')` 返回 `YYYY-MM-DD HH:MM:SS`（UTC，但**没有时区标记**）。
 * 直接交给 `Date.parse` 会被当成本地时间，东八区就会偏 8 小时；任务快照用的是
 * 带 `+00:00` 的 ISO 串。两种都要能正确解析。
 */
export function parseServerTime(raw: unknown): number {
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return raw > 1e12 ? raw / 1000 : raw
  }
  if (typeof raw !== 'string') return 0
  const text = raw.trim()
  if (!text) return 0
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text)
    ? `${text.replace(' ', 'T')}Z`
    : text
  const ms = Date.parse(normalized)
  return Number.isFinite(ms) && ms > 0 ? ms / 1000 : 0
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function asCount(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0
}

/** 服务端一行文档 → 展示模型；字段缺失时给中性值，不编造。 */
export function toDocSummary(raw: unknown): KnowledgeDocSummary | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  const id = asText(row.document_id)
  if (!id) return null
  return {
    id,
    source: asText(row.source),
    title: asText(row.title),
    chunks: asCount(row.chunks),
    contentHash: asText(row.content_hash),
    ingestedAt: parseServerTime(row.ingested_at),
    ingestedAtRaw: typeof row.ingested_at === 'string' ? row.ingested_at : null,
  }
}

/** 文档展示名：服务端来源 > 标题 > document_id（绝不显示空白）。 */
export function docDisplayName(doc: KnowledgeDocSummary): string {
  return doc.source ?? doc.title ?? doc.id
}

export function documentsPath(limit: number, offset: number): string {
  const safeLimit = Math.min(200, Math.max(1, Math.trunc(limit) || DOC_PAGE_SIZE))
  const safeOffset = Math.max(0, Math.trunc(offset) || 0)
  return `/v1/knowledge/documents?limit=${safeLimit}&offset=${safeOffset}`
}

export async function fetchDocuments(
  limit = DOC_PAGE_SIZE,
  offset = 0,
  signal?: AbortSignal,
): Promise<KnowledgeDocPage> {
  const raw = await apiFetch<unknown>(documentsPath(limit, offset), { signal })
  return normalizeDocPage(raw)
}

/**
 * 把服务端账本响应转成展示模型。`useApi` 只做原样 JSON，所以页面也必须过一遍
 * 这里 —— 否则表格拿到的是 `document_id` 而不是 `id`，删除按钮会发出
 * `/documents/undefined`。
 */
export function normalizeDocPage(raw: unknown): KnowledgeDocPage {
  const page = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const documents = Array.isArray(page.documents)
    ? page.documents
        .map(toDocSummary)
        .filter((doc): doc is KnowledgeDocSummary => doc !== null)
    : []
  return {
    documents,
    total: asCount(page.total),
    limit: asCount(page.limit) || DOC_PAGE_SIZE,
    offset: Math.max(0, Number(page.offset) || 0),
  }
}

/** 按文档读完整分块（`chunks` 是服务端切分结果，不是检索 top-k）。 */
export async function fetchDocumentChunks(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentChunks> {
  const raw = await apiFetch<{
    document_id?: unknown
    chunks?: unknown
    count?: unknown
  }>(`/v1/knowledge/documents/${encodeURIComponent(documentId)}/chunks`, { signal })
  const chunks: DocumentChunk[] = Array.isArray(raw.chunks)
    ? raw.chunks
        .map((item) => {
          if (!item || typeof item !== 'object') return null
          const row = item as Record<string, unknown>
          const index = Number(row.chunk_index)
          return {
            chunkId: String(row.chunk_id ?? ''),
            index: Number.isFinite(index) ? index : 0,
            text: typeof row.text === 'string' ? row.text : '',
            headingPath: Array.isArray(row.heading_path)
              ? row.heading_path.filter(
                  (part): part is string => typeof part === 'string' && part.length > 0,
                )
              : [],
            contextHeader: typeof row.context_header === 'string' ? row.context_header : '',
          }
        })
        .filter((chunk): chunk is DocumentChunk => chunk !== null)
    : []
  return {
    documentId: typeof raw.document_id === 'string' ? raw.document_id : documentId,
    chunks,
    count: asCount(raw.count) || chunks.length,
  }
}

/** 服务端返回的分块已经有序，但老数据 / 异常响应仍要在这里兜底排序。 */
export function sortChunks(chunks: DocumentChunk[]): DocumentChunk[] {
  return [...chunks].sort((left, right) => left.index - right.index)
}

export interface ChunkPageView {
  items: DocumentChunk[]
  pageIndex: number
  pageCount: number
  start: number
  end: number
  total: number
}

/** 分块分页视图：页面索引越界时自动夹到最后一页，避免出现空白页。 */
export function paginateChunks(
  chunks: DocumentChunk[],
  pageIndex: number,
  pageSize = CHUNK_PAGE_SIZE,
): ChunkPageView {
  const size = Math.max(1, Math.trunc(pageSize) || CHUNK_PAGE_SIZE)
  const total = chunks.length
  const pageCount = Math.max(1, Math.ceil(total / size))
  const index = Math.min(pageCount - 1, Math.max(0, Math.trunc(pageIndex) || 0))
  const start = index * size
  const end = Math.min(total, start + size)
  return { items: chunks.slice(start, end), pageIndex: index, pageCount, start, end, total }
}

export async function deleteKnowledgeDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<{ documentId: string; deleted: boolean; chunksRemoved: number }> {
  const raw = await apiFetch<{
    document_id?: unknown
    deleted?: unknown
    chunks_removed?: unknown
  }>(`/v1/knowledge/documents/${encodeURIComponent(documentId)}`, {
    method: 'DELETE',
    signal,
  })
  return {
    documentId: typeof raw.document_id === 'string' ? raw.document_id : documentId,
    deleted: raw.deleted === true,
    chunksRemoved: asCount(raw.chunks_removed),
  }
}

// ---------------------------------------------------------------------------
// multipart 上传：按失败类型给不同话术
// ---------------------------------------------------------------------------

export type UploadFailureKind =
  | 'unsupported-type'
  | 'too-large'
  | 'empty'
  | 'bad-encoding'
  | 'low-quality'
  | 'unauthorized'
  | 'forbidden'
  | 'unavailable'
  | 'server'
  | 'timeout'
  | 'network'
  | 'cancelled'
  | 'unknown'

export interface UploadFailure {
  kind: UploadFailureKind
  /** 一句话标题，例如「类型不支持」。 */
  title: string
  /** 完整原因 + 下一步怎么做。 */
  message: string
}

export function fileSuffix(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

/** 本地预检：能在发请求前拦下的，就不要让服务端白跑一趟。 */
export function validateUploadFile(file: { name: string; size: number }): UploadFailure | null {
  const suffix = fileSuffix(file.name)
  if (!UPLOAD_SUFFIXES.includes(suffix as (typeof UPLOAD_SUFFIXES)[number])) {
    return {
      kind: 'unsupported-type',
      title: '类型不支持',
      message: `不支持的文件类型：${
        suffix || '未知后缀'
      }。支持 ${UPLOAD_SUFFIXES.join(' / ')}；自扫描 PDF 与加密文件会另外单独报错。`,
    }
  }
  if (file.size > UPLOAD_MAX_BYTES) {
    return {
      kind: 'too-large',
      title: '文件太大',
      message: `文件过大（上限 ${formatBytes(UPLOAD_MAX_BYTES)}）：当前 ${formatBytes(
        file.size,
      )}。请压缩或拆分后再上传。`,
    }
  }
  if (file.size === 0) {
    return { kind: 'empty', title: '文件为空', message: '文件为空：没有可入库的文本。' }
  }
  return null
}

/** 把上传异常翻译成给人类看的话术；同一类失败永远同一套说法。 */
export function classifyUploadFailure(
  error: unknown,
  file: { name: string; size: number },
): UploadFailure {
  if (isAbortError(error)) {
    return {
      kind: 'cancelled',
      title: '已取消',
      message: '上传请求已中止 —— 服务端可能已经完成了本次入库，请刷新文档列表核对。',
    }
  }
  const status = error instanceof ApiError ? error.status : 0
  const detail = error instanceof Error ? error.message.trim() : String(error || '').trim()
  const suffix = fileSuffix(file.name)

  if (status === 415) {
    return {
      kind: 'unsupported-type',
      title: '类型不支持',
      message:
        detail ||
        `不支持的文件类型：${
          suffix || '未知后缀'
        }。支持 ${UPLOAD_SUFFIXES.join(' / ')}，不会假装解析。`,
    }
  }
  if (status === 422 && detail.includes('提取质量差')) {
    return {
      kind: 'low-quality',
      title: '提取质量差',
      message: detail,
    }
  }
  if (status === 413) {
    return {
      kind: 'too-large',
      title: '文件太大',
      message:
        detail ||
        `文件过大（上限 ${formatBytes(UPLOAD_MAX_BYTES)}）：当前 ${formatBytes(
          file.size,
        )}，请压缩或拆分后再上传。`,
    }
  }
  if (status === 400) {
    if (detail.includes('UTF-8') || detail.includes('编码')) {
      return {
        kind: 'bad-encoding',
        title: '编码不对',
        message: `${detail || '文件不是 UTF-8 编码'}。请用 UTF-8 另存后再上传（Windows 记事本可在「另存为」里选择 UTF-8）。`,
      }
    }
    if (detail.includes('为空')) {
      return { kind: 'empty', title: '文件为空', message: detail || '文件为空：没有可入库的文本。' }
    }
    return { kind: 'unknown', title: '请求被拒绝', message: detail || '服务端拒绝了这次上传（HTTP 400）。' }
  }
  if (status === 401) {
    return {
      kind: 'unauthorized',
      title: '登录已过期',
      message: '会话已失效，请重新登录后再上传。',
    }
  }
  if (status === 403) {
    return {
      kind: 'forbidden',
      title: '没有权限',
      message: detail || '当前账号没有上传知识库文档的权限。',
    }
  }
  if (status === 408) {
    return {
      kind: 'timeout',
      title: '请求超时',
      message: detail || '上传或服务端解析超时，请稍后重试；大文件建议拆分。',
    }
  }
  if (status === 503) {
    return {
      kind: 'unavailable',
      title: '服务端不可用',
      message: `${detail || '知识库暂时不可用'}。这与文件格式无关，请先确认知识库 / embedding provider 的状态。`,
    }
  }
  if (status >= 500) {
    return {
      kind: 'server',
      title: '服务端错误',
      message: `${detail || `服务端返回 HTTP ${status}`}。请稍后重试；反复出现时把这段原因（含追踪 ID）交给运维。`,
    }
  }
  if (status === 0) {
    return {
      kind: 'network',
      title: '服务端错误',
      message: detail
        ? `${detail}。网络中断或网关不可达，请检查连接后重试。`
        : '无法连接服务端：网络中断或网关不可达，请检查连接后重试。',
    }
  }
  return { kind: 'unknown', title: '上传失败', message: detail || '未知原因，请稍后重试。' }
}

function makeAbortError(): Error {
  const error = new Error('请求已取消')
  error.name = 'AbortError'
  return error
}

/**
 * multipart 上传（XHR 而不是 fetch：需要真实的上传字节进度）。
 * 不手动设置 Content-Type —— 浏览器必须自己写 multipart boundary。
 */
export function uploadKnowledgeDocument(
  file: File,
  options: {
    docId?: string
    /** 用户确认“乱码也要入库”时才传 true，服务端才会放行低质量文本。 */
    allowLowQuality?: boolean
    signal?: AbortSignal
    onProgress?: (loaded: number, total: number) => void
  } = {},
): Promise<KnowledgeIngestResult> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(makeAbortError())
      return
    }
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/v1/knowledge/documents/upload')
    xhr.timeout = UPLOAD_TIMEOUT_MS
    tokenHeaders().forEach((value, key) => xhr.setRequestHeader(key, value))

    let settled = false
    const finish = (fn: () => void) => {
      if (settled) return
      settled = true
      options.signal?.removeEventListener('abort', onAbort)
      fn()
    }
    const onAbort = () => {
      xhr.abort()
      finish(() => reject(makeAbortError()))
    }
    options.signal?.addEventListener('abort', onAbort, { once: true })

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) options.onProgress?.(event.loaded, event.total)
    }
    xhr.onerror = () =>
      finish(() => reject(new ApiError(0, '无法连接服务端（网络中断或网关不可达）')))
    xhr.ontimeout = () =>
      finish(() => reject(new ApiError(408, '上传或服务端解析超时，请稍后重试')))
    xhr.onabort = () => finish(() => reject(makeAbortError()))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          finish(() => resolve(JSON.parse(xhr.responseText) as KnowledgeIngestResult))
        } catch {
          finish(() => reject(new ApiError(0, '服务端返回了无法解析的响应')))
        }
        return
      }
      if (xhr.status === 401) {
        clearSession()
        try {
          window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT))
        } catch {
          // 非浏览器环境：忽略。
        }
      }
      let detail = ''
      let traceId = ''
      try {
        const body = JSON.parse(xhr.responseText) as { detail?: unknown; trace_id?: unknown }
        detail = describeDetail(body?.detail)
        traceId = typeof body?.trace_id === 'string' ? body.trace_id : ''
      } catch {
        // 非 JSON 响应（例如边缘网关的纯文本 502）：由状态码兜底。
      }
      if (!traceId) traceId = xhr.getResponseHeader('X-Trace-ID') ?? ''
      const message = detail || xhr.statusText || `HTTP ${xhr.status}`
      finish(() =>
        reject(new ApiError(xhr.status, traceId ? `${message}\n追踪 ID：${traceId}` : message)),
      )
    }

    const form = new FormData()
    form.append('file', file, file.name)
    const docId = options.docId?.trim()
    if (docId) form.append('doc_id', docId)
    if (options.allowLowQuality) form.append('allow_low_quality', 'true')
    xhr.send(form)
  })
}

// ---------------------------------------------------------------------------
// 异步入库任务（进度 / 取消 / 全回滚）
// ---------------------------------------------------------------------------

export interface IngestJobResult {
  document_id?: string
  chunks?: number
  vectors?: number
  deduplicated?: boolean
  matched_document_id?: string | null
  committed_chunks?: number
  extraction?: {
    file_type?: string
    page_count?: number
    text_chars?: number
    paged?: boolean
    quality?: IngestQuality | null
  }
}

export interface IngestQuality {
  garbled_chars: number
  total_chars: number
  garbled_ratio: number
  low_quality: boolean
}

export interface IngestJob {
  jobId: string
  documentId: string
  /** `text`（粘贴文本）或 `file`（上传文件）。 */
  source: string
  fileName: string | null
  fileType: string | null
  /** 原样保留服务端状态串：新增状态不会被误标成"完成"。 */
  status: string
  phase: string
  totalChunks: number
  processedChunks: number
  /** 文件任务才有页数；纯文本任务为 0。 */
  totalPages: number
  extractedPages: number
  embeddedPages: number
  processedPages: number
  progressPercent: number
  quality: IngestQuality | null
  errorCode: string | null
  cancelRequested: boolean
  createdAt: string | null
  startedAt: string | null
  finishedAt: string | null
  error: string | null
  result: IngestJobResult | null
  note: string | null
  partialCommitPolicy: string | null
}

export function isTerminalJobStatus(status: string): boolean {
  return status === 'succeeded' || status === 'failed' || status === 'cancelled'
}

const JOB_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  running: '进行中',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export function jobStatusLabel(status: string): string {
  return JOB_STATUS_LABELS[status] ?? status
}

const PHASE_LABELS: Record<string, string> = {
  queued: '排队',
  extracting: '解析文件',
  embedding: '向量化',
  committing: '写入分块',
  done: '完成',
  failed: '失败',
  cancelled: '已取消',
}

export function jobPhaseLabel(phase: string): string {
  return PHASE_LABELS[phase] ?? phase
}

function asQuality(raw: unknown): IngestQuality | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Record<string, unknown>
  const garbledChars = Number(row.garbled_chars)
  const totalChars = Number(row.total_chars)
  const ratio = Number(row.garbled_ratio)
  return {
    garbled_chars: Number.isFinite(garbledChars) ? garbledChars : 0,
    total_chars: Number.isFinite(totalChars) ? totalChars : 0,
    garbled_ratio: Number.isFinite(ratio) ? ratio : 0,
    low_quality: row.low_quality === true,
  }
}

export function toIngestJob(raw: unknown): IngestJob {
  const row = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const percent = Number(row.progress_percent)
  return {
    jobId: String(row.job_id ?? ''),
    documentId: String(row.document_id ?? ''),
    source: String(row.source ?? 'text'),
    fileName: asText(row.file_name),
    fileType: asText(row.file_type),
    status: String(row.status ?? ''),
    phase: String(row.phase ?? ''),
    totalChunks: asCount(row.total_chunks),
    processedChunks: asCount(row.processed_chunks),
    totalPages: asCount(row.total_pages),
    extractedPages: asCount(row.extracted_pages),
    embeddedPages: asCount(row.embedded_pages),
    processedPages: asCount(row.processed_pages),
    progressPercent: Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : 0,
    quality: asQuality(row.quality),
    errorCode: asText(row.error_code),
    cancelRequested: row.cancel_requested === true,
    createdAt: asText(row.created_at),
    startedAt: asText(row.started_at),
    finishedAt: asText(row.finished_at),
    error: asText(row.error),
    result:
      row.result && typeof row.result === 'object'
        ? (row.result as IngestJobResult)
        : null,
    note: asText(row.note),
    partialCommitPolicy: asText(row.partial_commit_policy),
  }
}

/** 大文件：立刻拿到 job id，进度按服务端解析/嵌入的真实页数推进。 */
export async function submitFileIngestJob(
  file: File,
  options: {
    docId?: string
    allowLowQuality?: boolean
    signal?: AbortSignal
  } = {},
): Promise<IngestJob> {
  const form = new FormData()
  form.append('file', file, file.name)
  const docId = options.docId?.trim()
  if (docId) form.append('doc_id', docId)
  if (options.allowLowQuality) form.append('allow_low_quality', 'true')
  const raw = await apiFetch<unknown>('/v1/knowledge/documents/upload/jobs', {
    method: 'POST',
    body: form,
    signal: options.signal,
  })
  return toIngestJob(raw)
}

export async function submitIngestJob(
  text: string,
  options: { name: string; docId?: string; signal?: AbortSignal },
): Promise<IngestJob> {
  const raw = await apiFetch<unknown>('/v1/knowledge/ingest/jobs', {
    method: 'POST',
    body: JSON.stringify({
      text,
      ...(options.docId?.trim() ? { doc_id: options.docId.trim() } : {}),
      // 列表接口展示的是 `metadata.source`：把用户给的名字写到 source，
      // 否则文本入库的文档在服务端列表里只能看到一串 id。
      metadata: { name: options.name, source: options.name },
    }),
    signal: options.signal,
  })
  return toIngestJob(raw)
}

export async function fetchIngestJob(jobId: string, signal?: AbortSignal): Promise<IngestJob> {
  const raw = await apiFetch<unknown>(
    `/v1/knowledge/ingest/jobs/${encodeURIComponent(jobId)}`,
    { signal },
  )
  return toIngestJob(raw)
}

export interface IngestJobCancelResult {
  job: IngestJob
  accepted: boolean
  reason: string
}

export async function cancelIngestJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<IngestJobCancelResult> {
  const raw = await apiFetch<Record<string, unknown>>(
    `/v1/knowledge/ingest/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: 'POST', signal },
  )
  return {
    job: toIngestJob(raw),
    accepted: raw.cancel_accepted === true,
    reason: typeof raw.reason === 'string' ? raw.reason : '',
  }
}

// ---------------------------------------------------------------------------
// 纯展示工具
// ---------------------------------------------------------------------------

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return '—'
  if (bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
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
// 真实端点（同步文本入库 / 检索）
// ---------------------------------------------------------------------------

export async function ingestKnowledge(
  text: string,
  options: {
    name: string
    signal?: AbortSignal
  },
): Promise<KnowledgeIngestResult> {
  return apiFetch<KnowledgeIngestResult>('/v1/knowledge/documents', {
    method: 'POST',
    body: JSON.stringify({
      text,
      metadata: { name: options.name, source: options.name },
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
// 检索命中 → 分块归属 / 分数语义
// ---------------------------------------------------------------------------

/**
 * 分数的含义随检索模式变化，界面必须跟着变 —— 否则会把 RRF 的 0.016 误标成
 * "相似度"，让人以为检索质量很差。
 */
export function scoreLabel(mode?: string): string {
  if (mode === 'hybrid') return '融合排序分（RRF）'
  if (mode === 'vector') return '余弦相似度'
  return '排序分'
}

export function scoreNote(mode?: string): string {
  if (mode === 'hybrid') {
    return '分数为向量 + 全文检索的 RRF 融合排序分，数值很小（约为 1/(60+名次)），只用于本次结果内排序，不是相似度百分比。'
  }
  return '分数为余弦相似度（-1~1），只用于本次结果内排序，不宜跨库比较。'
}

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

export function docNameMap(docs: KnowledgeDocSummary[]): Map<string, string> {
  return new Map(docs.map((doc) => [doc.id, docDisplayName(doc)]))
}
