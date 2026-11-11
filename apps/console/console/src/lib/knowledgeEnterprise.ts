/**
 * 企业知识库 / 长期记忆 API 客户端（0.3.0）。
 *
 * 后端契约：`src/gateway/knowledge_enterprise.py`，前缀 `/v1/knowledge/enterprise`。
 * 与旧的 `lib/knowledge.ts`（`/v1/knowledge` 向量链路）互不影响。
 *
 * 三条界面必须守住的诚实边界：
 *   1. 检索是**词法**的（FTS5 + 中文 bigram + BM25），界面写「词法相关度」，
 *      不冒充语义相似度；
 *   2. 图片不做 OCR，只按文件名/尺寸/说明检索，界面必须照实标注；
 *   3. 长期记忆与知识库分库存储，记忆越权读写成中性 404，界面不给「猜权限」开关。
 */

import { ApiError, apiFetch } from './api'
import { tokenHeaders } from './auth'

export const ENTERPRISE_BASE = '/v1/knowledge/enterprise'
export const UPLOAD_MAX_BYTES = 25 * 1024 * 1024
const UPLOAD_TIMEOUT_MS = 300_000

export interface EnterpriseStatus {
  documents: number
  chunks: number
  categories: number
  expired: number
  expiring: number
  store: string
  knowledge_store: string
  memory_store: string
  retrieval: string
  supported_types: string[]
  memory_scopes: string[]
}

export interface CategoryNode {
  id: string
  name: string
  parent_id: string | null
  visibility: 'private' | 'team' | 'enterprise'
  description: string
  created_by: string
  system: boolean
  document_count: number
  children: CategoryNode[]
}

export interface DocumentMatch {
  excerpt: string
  heading_path: string
  page: number | null
}

export interface DocumentSummary {
  id: string
  title: string
  doc_key: string
  owner_account_id: string
  project_id: string
  category_id: string | null
  category_name: string
  category_path: string[]
  source_type: string
  source_ref: string
  origin_filename: string
  mime: string
  visibility: 'private' | 'team' | 'enterprise'
  tags: string[]
  description: string
  current_version: number
  chunk_count: number
  size_bytes: number
  text_source: string
  expires_at: string | null
  expiry_notice_days: number
  expiry_state: 'none' | 'ok' | 'expiring' | 'expired'
  days_left: number | null
  created_at: string
  updated_at: string
  match?: DocumentMatch
  deduplicated?: boolean
  created?: boolean
  file_type?: string
}

export interface DocumentVersionMeta {
  id: string
  document_id: string
  version: number
  content_hash: string
  chunk_count: number
  file_path: string
  origin_filename: string
  mime: string
  size_bytes: number
  text_source: string
  note: string
  created_by: string
  created_at: string
}

export interface DocumentDetail extends DocumentSummary {
  versions: DocumentVersionMeta[]
  text?: string
  text_truncated?: boolean
  can_write: boolean
}

export interface DocumentVersionDetail extends DocumentVersionMeta {
  text: string
  text_truncated: boolean
  is_current: boolean
}

export interface DocumentDiff {
  document_id: string
  from_version: number
  to_version: number
  added_lines: number
  removed_lines: number
  changed: boolean
  unified: string
  truncated: boolean
}

export interface SearchHit {
  chunk_id: string
  document_id: string
  title: string
  score: number
  text: string
  excerpt: string
  heading_path: string
  page: number | null
  version: number
  owner_account_id: string
  category_id: string | null
  category_path: string[]
  source_type: string
  source_ref: string
  origin_filename: string
  expires_at: string | null
  expiry_state: 'none' | 'ok' | 'expiring' | 'expired'
  days_left: number | null
}

export interface SearchResult {
  query: string
  count: number
  hits: SearchHit[]
  retrieval: {
    mode: 'fts5_bm25' | 'like_fallback'
    status: 'ok' | 'no_relevant'
    reason: string | null
    top_score: number | null
    note: string
  }
}

export interface DocumentPage {
  documents: DocumentSummary[]
  total: number
  limit: number
  offset: number
  query: string
  retrieval: { mode: string } | null
}

export interface ExpiringResult {
  days: number
  counts: { expired: number; expiring: number }
  documents: DocumentSummary[]
  total: number
}

export interface MemoryEntry {
  id: string
  owner_account_id: string
  project_id: string
  employee_id: string
  scope: 'personal' | 'project' | 'employee' | 'enterprise'
  title: string
  content: string
  source: string
  source_ref: string
  tags: string[]
  confidence: number | null
  expires_at: string | null
  expiry_state: 'none' | 'ok' | 'expiring' | 'expired'
  days_left: number | null
  created_at: string
  updated_at: string
  revision: number
  can_write: boolean
}

export interface MemoryRevision {
  id: string
  memory_id: string
  revision: number
  title: string
  content: string
  source: string
  tags: string[]
  edited_by: string
  created_at: string
}

export interface MemoryDetail extends MemoryEntry {
  revisions: MemoryRevision[]
}

export interface MemoryPage {
  memories: MemoryEntry[]
  total: number
  limit: number
  offset: number
}

export interface MemoryViews {
  projects: { project_id: string; count: number; updated_at: string }[]
  employees: { employee_id: string; count: number; updated_at: string }[]
  scopes: Record<string, number>
  total: number
  store: string
  isolation: string
}

export interface UploadFields {
  title?: string
  category_id?: string
  doc_key?: string
  visibility?: string
  project_id?: string
  source_ref?: string
  description?: string
  expires_at?: string
  expiry_notice_days?: number
  tags?: string
  note?: string
  allow_low_quality?: boolean
}

export interface TextIngestFields extends Omit<UploadFields, 'tags'> {
  text: string
  /** 纯文本入库走 JSON，后端接收数组；上传文件走表单，合并成逗号分隔字符串。 */
  tags?: string[]
}

export interface DocumentUpdateFields {
  title?: string
  category_id?: string
  visibility?: string
  expires_at?: string
  clear_expiry?: boolean
  expiry_notice_days?: number
  tags?: string[]
  description?: string
  project_id?: string
  source_ref?: string
}

export interface SearchFields {
  query: string
  category_id?: string
  include_subcategories?: boolean
  project_id?: string
  top_k?: number
  include_expired?: boolean
}

function queryString(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function statusPath(): string {
  return `${ENTERPRISE_BASE}/status`
}

export async function fetchStatus(): Promise<EnterpriseStatus> {
  return apiFetch<EnterpriseStatus>(statusPath())
}

export async function fetchCategories(): Promise<CategoryNode[]> {
  const data = await apiFetch<{ categories: CategoryNode[] }>(`${ENTERPRISE_BASE}/categories`)
  return data.categories
}

export async function createCategory(input: {
  name: string
  parent_id?: string
  visibility?: string
  description?: string
}): Promise<CategoryNode> {
  return apiFetch<CategoryNode>(`${ENTERPRISE_BASE}/categories`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateCategory(
  categoryId: string,
  input: { name?: string; parent_id?: string; visibility?: string; description?: string },
): Promise<CategoryNode> {
  return apiFetch<CategoryNode>(`${ENTERPRISE_BASE}/categories/${encodeURIComponent(categoryId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export async function deleteCategory(categoryId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(
    `${ENTERPRISE_BASE}/categories/${encodeURIComponent(categoryId)}`,
    { method: 'DELETE' },
  )
}

export async function ingestText(fields: TextIngestFields): Promise<DocumentSummary> {
  return apiFetch<DocumentSummary>(`${ENTERPRISE_BASE}/documents/text`, {
    method: 'POST',
    body: JSON.stringify(fields),
  })
}

export async function fetchDocuments(params: {
  category_id?: string
  include_subcategories?: boolean
  project_id?: string
  include_expired?: boolean
  q?: string
  limit?: number
  offset?: number
}): Promise<DocumentPage> {
  return apiFetch<DocumentPage>(`${ENTERPRISE_BASE}/documents${queryString(params)}`)
}

export async function fetchDocument(documentId: string): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(
    `${ENTERPRISE_BASE}/documents/${encodeURIComponent(documentId)}`,
  )
}

export async function updateDocument(
  documentId: string,
  fields: DocumentUpdateFields,
): Promise<DocumentSummary> {
  return apiFetch<DocumentSummary>(
    `${ENTERPRISE_BASE}/documents/${encodeURIComponent(documentId)}`,
    { method: 'PATCH', body: JSON.stringify(fields) },
  )
}

export async function deleteDocument(documentId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(
    `${ENTERPRISE_BASE}/documents/${encodeURIComponent(documentId)}`,
    { method: 'DELETE' },
  )
}

export async function fetchVersion(
  documentId: string,
  version: number,
): Promise<DocumentVersionDetail> {
  return apiFetch<DocumentVersionDetail>(
    `${ENTERPRISE_BASE}/documents/${encodeURIComponent(documentId)}/versions/${version}`,
  )
}

export async function diffVersions(
  documentId: string,
  fromVersion: number,
  toVersion: number,
): Promise<DocumentDiff> {
  return apiFetch<DocumentDiff>(
    `${ENTERPRISE_BASE}/documents/${encodeURIComponent(documentId)}/diff${queryString({
      from_version: fromVersion,
      to_version: toVersion,
    })}`,
  )
}

export async function searchDocuments(fields: SearchFields): Promise<SearchResult> {
  return apiFetch<SearchResult>(`${ENTERPRISE_BASE}/search`, {
    method: 'POST',
    body: JSON.stringify(fields),
  })
}

export async function fetchExpiring(days = 30): Promise<ExpiringResult> {
  return apiFetch<ExpiringResult>(`${ENTERPRISE_BASE}/expiring${queryString({ days })}`)
}

function uploadFailure(xhr: XMLHttpRequest): ApiError {
  let message = `上传失败（HTTP ${xhr.status || 0}）`
  let code: string | null = null
  try {
    const body = JSON.parse(xhr.responseText) as {
      detail?: string | { code?: string; message?: string }
      message?: string
    }
    if (typeof body.detail === 'string') {
      message = body.detail
    } else if (body.detail && typeof body.detail === 'object') {
      message = body.detail.message || message
      code = body.detail.code ?? null
    } else if (typeof body.message === 'string') {
      message = body.message
    }
  } catch {
    // 非 JSON 响应：保留通用文案。
  }
  return new ApiError(xhr.status || 0, message, code)
}

/** 上传原件。大文件走 XHR 以复用上传进度条；解析失败原样透出后端 message。 */
export function uploadDocument(
  file: File,
  fields: UploadFields,
  onProgress?: (percent: number) => void,
): Promise<DocumentSummary> {
  const form = new FormData()
  form.append('file', file, file.name)
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null || value === '') continue
    form.append(key, String(value))
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${ENTERPRISE_BASE}/documents/upload`)
    xhr.timeout = UPLOAD_TIMEOUT_MS
    const headers = tokenHeaders()
    headers.forEach((value, key) => xhr.setRequestHeader(key, value))
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) {
        onProgress?.(Math.min(100, Math.round((event.loaded / event.total) * 100)))
      }
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as DocumentSummary)
        } catch {
          reject(new ApiError(xhr.status, '服务没有返回正常的上传结果，请重试。'))
        }
        return
      }
      reject(uploadFailure(xhr))
    }
    xhr.onerror = () => reject(new ApiError(0, '上传中断：请确认鎏灏仍在运行，然后重试。'))
    xhr.ontimeout = () =>
      reject(new ApiError(408, '上传超时。文件可能仍在处理，请刷新列表确认后再决定是否重传。'))
    xhr.send(form)
  })
}

/** 下载原件：需要带令牌，普通 <a href> 拿不到鉴权头，所以取 blob 再触发保存。 */
export async function downloadDocument(documentId: string, version?: number): Promise<void> {
  const url = `${ENTERPRISE_BASE}/documents/${encodeURIComponent(documentId)}/file${
    version ? `?version=${version}` : ''
  }`
  const response = await fetch(url, { headers: tokenHeaders(), cache: 'no-store' })
  if (!response.ok) {
    let message = `下载失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: { message?: string } | string }
      if (typeof body.detail === 'string') message = body.detail
      else if (body.detail?.message) message = body.detail.message
    } catch {
      // 保留通用文案。
    }
    throw new ApiError(response.status, message)
  }
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') || ''
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  const filename = utf8
    ? decodeURIComponent(utf8[1])
    : plain?.[1] || `knowledge-${documentId}`
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}

export async function fetchMemories(params: {
  project_id?: string
  employee_id?: string
  scope?: string
  q?: string
  include_expired?: boolean
  limit?: number
  offset?: number
}): Promise<MemoryPage> {
  return apiFetch<MemoryPage>(`${ENTERPRISE_BASE}/memory${queryString(params)}`)
}

export async function fetchMemory(memoryId: string): Promise<MemoryDetail> {
  return apiFetch<MemoryDetail>(`${ENTERPRISE_BASE}/memory/${encodeURIComponent(memoryId)}`)
}

export async function createMemory(input: {
  content: string
  title?: string
  project_id?: string
  employee_id?: string
  scope?: string
  source?: string
  source_ref?: string
  tags?: string[]
  confidence?: number | null
  expires_at?: string
}): Promise<MemoryEntry> {
  return apiFetch<MemoryEntry>(`${ENTERPRISE_BASE}/memory`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export async function updateMemory(
  memoryId: string,
  input: {
    content?: string
    title?: string
    project_id?: string
    employee_id?: string
    scope?: string
    source?: string
    source_ref?: string
    tags?: string[]
    confidence?: number | null
    expires_at?: string
    clear_expiry?: boolean
  },
): Promise<MemoryEntry> {
  return apiFetch<MemoryEntry>(`${ENTERPRISE_BASE}/memory/${encodeURIComponent(memoryId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export async function deleteMemory(memoryId: string): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(
    `${ENTERPRISE_BASE}/memory/${encodeURIComponent(memoryId)}`,
    { method: 'DELETE' },
  )
}

export async function fetchMemoryViews(): Promise<MemoryViews> {
  return apiFetch<MemoryViews>(`${ENTERPRISE_BASE}/memory/views`)
}
