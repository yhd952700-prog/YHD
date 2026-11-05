/**
 * 知识中心 —— 接在真实知识库之上（后端 `79bd69c8` 补齐了文档账本）。
 *
 * 这一页现在做六件事，每一件都对应一个真实端点：
 *   1. 状态：`GET /v1/knowledge/status`（可用性、provider、向量总数）；
 *   2. 上传：`POST /v1/knowledge/documents/upload`（multipart，PDF/DOCX/HTML/TXT/MD）；
 *      大文件走 `POST /documents/upload/jobs`（按页进度 + 可取消 + 全回滚）；
 *   3. 文本入库：同步 `POST /documents` 或异步 `POST /ingest/jobs`（进度 + 取消）；
 *   4. 文档列表：`GET /documents?limit&offset`（服务端分页账本，不再是本机记录）；
 *   5. 分块预览：`GET /documents/{id}/chunks`（完整切分，带序号与标题路径，分页渲染）；
 *   6. 删除：`DELETE /documents/{id}`（管理员权限，二次确认，chunks + 向量一起删）。
 *
 * 三条不肯妥协的表达：
 *   * 上传失败按类型分话术，不一律「上传失败」；
 *   * 异步入库取消 = 全回滚，界面写「取消后本次不会留下任何数据」；
 *   * 能力边界只列真正还缺的，已经能用的不再写成做不到。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import { ApiError, useApi } from '../lib/api'
import {
  cancelIngestJob,
  classifyUploadFailure,
  deleteKnowledgeDocument,
  docDisplayName,
  docNameMap,
  DOC_PAGE_SIZE,
  documentsPath,
  fetchDocumentChunks,
  fetchIngestJob,
  hitChunkIndex,
  hitDocumentId,
  ingestKnowledge,
  isAbortError,
  isTerminalJobStatus,
  jobPhaseLabel,
  jobStatusLabel,
  MAX_INGEST_CHARS,
  normalizeDocPage,
  paginateChunks,
  scoreLabel,
  scoreNote,
  searchKnowledge,
  sortChunks,
  submitFileIngestJob,
  submitIngestJob,
  SYNC_UPLOAD_MAX_BYTES,
  UPLOAD_ACCEPT,
  UPLOAD_MAX_BYTES,
  uploadKnowledgeDocument,
  validateIngestText,
  validateUploadFile,
} from '../lib/knowledge'
import type {
  DocumentChunk,
  IngestJob,
  KnowledgeDocPage,
  KnowledgeDocSummary,
  KnowledgeSearchResult,
  KnowledgeStatus,
} from '../lib/knowledge'
import { formatBytes } from '../lib/knowledge'
import { relativeTime } from '../lib/format'
import { Card, Empty, Failure, Guard, Loading } from '../components/ui'

type UploadProgress = { loaded: number; total: number }
type ActionError = { title: string; message: string }

function jobTone(status: string): 'ok' | 'warn' | 'danger' | 'accent' | 'muted' {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed') return 'danger'
  if (status === 'cancelled') return 'warn'
  if (status === 'running') return 'accent'
  return 'muted'
}

export function Knowledge({
  query,
  onOpenSettings,
}: {
  query: string
  onOpenSettings: () => void
}) {
  const status = useApi<KnowledgeStatus>('/v1/knowledge/status', 30_000)
  const disabled = status.data ? !status.data.enabled : false

  // ── 文档账本（服务端分页） ────────────────────────────────────
  const [pageIndex, setPageIndex] = useState(0)
  const documents = useApi<KnowledgeDocPage>(
    documentsPath(DOC_PAGE_SIZE, pageIndex * DOC_PAGE_SIZE),
    30_000,
  )
  const normalizedPage = useMemo(
    () => (documents.data ? normalizeDocPage(documents.data) : null),
    [documents.data],
  )
  // 翻页后旧页数据可能还在（useApi 不重置 data）：offset 对不上就当没数据，
  // 否则会出现「页码已是第 2 页、表格还是第 1 页」的错位。
  const pageReady =
    normalizedPage && normalizedPage.offset === pageIndex * DOC_PAGE_SIZE
      ? normalizedPage
      : null
  const docs = pageReady?.documents ?? []
  const total = normalizedPage?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / DOC_PAGE_SIZE))

  useEffect(() => {
    if (!normalizedPage) return
    // 删除最后一页最后一条后，页码要退回来，否则会停在空白页。
    if (pageIndex > 0 && normalizedPage.documents.length === 0) {
      setPageIndex(pageIndex - 1)
    }
  }, [normalizedPage, pageIndex])

  // ── 上传（multipart） ─────────────────────────────────────────
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)
  const [dragging, setDragging] = useState(false)
  const uploadRef = useRef<AbortController | null>(null)
  /** 提取质量差被拦下的文件：保留 File 引用，用户确认后原样重试。 */
  const [lowQualityFile, setLowQualityFile] = useState<File | null>(null)
  const jobFileRef = useRef<File | null>(null)

  // ── 文本入库（同步 + 异步任务） ───────────────────────────────
  const [text, setText] = useState('')
  const [name, setName] = useState('')
  const [docId, setDocId] = useState('')
  const [importing, setImporting] = useState(false)
  const importRef = useRef<AbortController | null>(null)

  const [job, setJob] = useState<IngestJob | null>(null)
  const [jobSubmitting, setJobSubmitting] = useState(false)
  const [jobError, setJobError] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const handledJobRef = useRef('')

  // ── 通用反馈 ─────────────────────────────────────────────────
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<ActionError | null>(null)
  /** 删除后检索结果被清空时单独提示，避免和上传/入库的 notice 互相覆盖。 */
  const [searchNotice, setSearchNotice] = useState<string | null>(null)

  // ── 分块预览 ─────────────────────────────────────────────────
  const [selected, setSelected] = useState<KnowledgeDocSummary | null>(null)
  const [chunks, setChunks] = useState<DocumentChunk[] | null>(null)
  const [chunkPage, setChunkPage] = useState(0)
  const [chunkLoading, setChunkLoading] = useState(false)
  const [chunkError, setChunkError] = useState<string | null>(null)
  const chunkRef = useRef<AbortController | null>(null)

  // ── 删除（二次确认） ─────────────────────────────────────────
  const [pendingDelete, setPendingDelete] = useState<KnowledgeDocSummary | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const deleteRef = useRef<AbortController | null>(null)

  // ── 检索测试 ─────────────────────────────────────────────────
  const [searchInput, setSearchInput] = useState('')
  const [topK, setTopK] = useState(8)
  const [searchResult, setSearchResult] = useState<KnowledgeSearchResult | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const searchRef = useRef<AbortController | null>(null)

  useEffect(
    () => () => {
      uploadRef.current?.abort()
      importRef.current?.abort()
      chunkRef.current?.abort()
      deleteRef.current?.abort()
      searchRef.current?.abort()
    },
    [],
  )

  // ── 上传文件 ─────────────────────────────────────────────────
  const startUpload = useCallback(
    (file: File, allowLowQuality = false) => {
      setActionError(null)
      setNotice(null)
      setLowQualityFile(null)
      setUploading(true)
      setUploadProgress({ loaded: 0, total: file.size })
      const controller = new AbortController()
      uploadRef.current = controller

      void (async () => {
        try {
          const result = await uploadKnowledgeDocument(file, {
            docId,
            allowLowQuality,
            signal: controller.signal,
            onProgress: (loaded, progressTotal) =>
              setUploadProgress({ loaded, total: progressTotal }),
          })
          if (result.deduplicated) {
            setNotice(
              `「${file.name}」的内容与已有文档「${
                result.matched_document_id ?? '（未知 id）'
              }」完全相同，服务端未重复写入（本次新增 0 个分块）。`,
            )
          } else {
            setNotice(
              `已上传并入库「${result.source ?? file.name}」：${
                result.chunks
              } 个分块 · 当前向量总数 ${result.vectors}。`,
            )
            setPageIndex(0)
          }
          status.reload()
          documents.reload()
        } catch (err) {
          if (isAbortError(err)) {
            setNotice(
              '已取消上传：请求已中止。服务端可能已经完成本次入库 —— 已刷新文档列表，请以列表结果为准。',
            )
            status.reload()
            documents.reload()
          } else {
            const failure = classifyUploadFailure(err, file)
            setActionError(failure)
            // 低质量不是错误终点：把文件留着，用户可确认后加 allow_low_quality 重试。
            if (failure.kind === 'low-quality') setLowQualityFile(file)
          }
        } finally {
          uploadRef.current = null
          setUploading(false)
          setUploadProgress(null)
        }
      })()
    },
    [docId, documents.reload, status.reload],
  )

  /** 大文件走异步文件任务：立刻返回 job id，进度按真实页数推进且可取消。 */
  const startFileJob = useCallback(
    (file: File, allowLowQuality = false) => {
      setActionError(null)
      setNotice(null)
      setJobError(null)
      setLowQualityFile(null)
      setJobSubmitting(true)
      jobFileRef.current = file
      const controller = new AbortController()
      uploadRef.current = controller

      void (async () => {
        try {
          const snapshot = await submitFileIngestJob(file, {
            docId,
            allowLowQuality,
            signal: controller.signal,
          })
          setJob(snapshot)
        } catch (err) {
          setJobError(
            isAbortError(err)
              ? '已取消提交：任务尚未创建。'
              : classifyUploadFailure(err, file).message,
          )
        } finally {
          uploadRef.current = null
          setJobSubmitting(false)
        }
      })()
    },
    [docId],
  )

  /** 用户确认“乱码也要入库”后，用同一个文件重试（同步/异步取决于大小）。 */
  const retryLowQuality = useCallback(() => {
    if (!lowQualityFile) return
    if (lowQualityFile.size > SYNC_UPLOAD_MAX_BYTES) {
      startFileJob(lowQualityFile, true)
    } else {
      startUpload(lowQualityFile, true)
    }
  }, [lowQualityFile, startFileJob, startUpload])

  const acceptFile = useCallback(
    (file: File) => {
      setDragging(false)
      const local = validateUploadFile(file)
      if (local) {
        setNotice(null)
        setLowQualityFile(null)
        setActionError({ title: local.title, message: local.message })
        return
      }
      if (file.size > SYNC_UPLOAD_MAX_BYTES) {
        startFileJob(file)
      } else {
        startUpload(file)
      }
    },
    [startFileJob, startUpload],
  )

  const onDrop = useCallback(
    (event: DragEvent<HTMLLabelElement>) => {
      event.preventDefault()
      setDragging(false)
      const file = event.dataTransfer?.files?.[0]
      if (file && !uploading && !disabled) acceptFile(file)
    },
    [acceptFile, disabled, uploading],
  )

  const onPick = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      if (file) acceptFile(file)
      // 清空 input：同一个文件再次选择也要触发 change。
      event.target.value = ''
    },
    [acceptFile],
  )

  // ── 同步文本入库 ─────────────────────────────────────────────
  const submitSync = useCallback(() => {
    const local = validateIngestText(text)
    if (local) {
      setActionError({ title: '入库失败', message: local })
      return
    }
    setActionError(null)
    setNotice(null)
    setImporting(true)
    const controller = new AbortController()
    importRef.current = controller

    void (async () => {
      try {
        const result = await ingestKnowledge(text, {
          name: name.trim() || '粘贴文本',
          signal: controller.signal,
        })
        if (result.deduplicated) {
          setNotice(
            `内容与已有文档「${
              result.matched_document_id ?? '（未知 id）'
            }」完全相同，服务端未重复写入（本次新增 0 个分块）。`,
          )
        } else {
          setNotice(
            `已入库「${name.trim() || '粘贴文本'}」：${
              result.chunks
            } 个分块 · 当前向量总数 ${result.vectors}。`,
          )
          setText('')
          setPageIndex(0)
        }
        status.reload()
        documents.reload()
      } catch (err) {
        if (isAbortError(err)) {
          setNotice(
            '已取消同步入库请求。服务端可能已经完成写入 —— 已刷新文档列表，请以列表结果为准。',
          )
          status.reload()
          documents.reload()
        } else {
          setActionError({
            title: '入库失败',
            message: err instanceof Error ? err.message : '未知错误',
          })
        }
      } finally {
        importRef.current = null
        setImporting(false)
      }
    })()
  }, [documents.reload, name, status.reload, text])

  // ── 异步入库任务 ─────────────────────────────────────────────
  const startJob = useCallback(() => {
    const local = validateIngestText(text)
    if (local) {
      setActionError({ title: '入库失败', message: local })
      return
    }
    if (job && !isTerminalJobStatus(job.status)) {
      setActionError({
        title: '已有任务在跑',
        message: '上一个后台任务还没结束；先取消它，或等它完成后再提交新的。',
      })
      return
    }
    setActionError(null)
    setNotice(null)
    setJobError(null)
    setJobSubmitting(true)

    void (async () => {
      try {
        const snapshot = await submitIngestJob(text, {
          name: name.trim() || '粘贴文本',
          docId,
        })
        setJob(snapshot)
        // 不在这里清空文本：任务失败 / 取消时把原文留给用户重试。
      } catch (err) {
        setJobError(err instanceof Error ? err.message : '提交后台任务失败')
      } finally {
        setJobSubmitting(false)
      }
    })()
  }, [docId, job, name, text])

  // 轮询任务进度：只有非终态才轮询，结束就停。
  useEffect(() => {
    if (!job || isTerminalJobStatus(job.status)) return
    let alive = true
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          const next = await fetchIngestJob(job.jobId)
          if (alive) setJob(next)
        } catch (err) {
          if (alive) {
            setJobError(err instanceof Error ? err.message : '查询任务进度失败')
          }
        }
      })()
    }, 1000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [job?.jobId, job?.status])

  const jobActive = Boolean(job && !isTerminalJobStatus(job.status))

  // 终态只处理一次：刷新列表与状态，并把结果写成人话。
  useEffect(() => {
    if (!job || !isTerminalJobStatus(job.status)) return
    const key = `${job.jobId}:${job.status}`
    if (handledJobRef.current === key) return
    handledJobRef.current = key

    status.reload()
    documents.reload()

    if (job.status === 'succeeded') {
      setText('')
      if (job.result?.deduplicated) {
        setNotice(
          `内容与已有文档「${
            job.result.matched_document_id ?? '（未知 id）'
          }」完全相同，服务端未重复写入（本次新增 0 个分块）。`,
        )
      } else {
        setNotice(
          `后台入库完成：${job.documentId} · ${
            job.processedChunks || job.result?.chunks || 0
          } 个分块。文档列表已刷新。`,
        )
        setPageIndex(0)
      }
      setJobError(null)
    } else if (job.status === 'cancelled') {
      setNotice(
        '后台任务已取消：本次未写入任何数据（全回滚）。文档列表已刷新，不会出现该文档。',
      )
      setJobError(null)
    } else if (job.status === 'failed') {
      setJobError(job.error ?? '任务失败（服务端未给出原因）')
      // 乱码被拦住不是死路：保留文件，界面给出“确认继续入库”的重试按钮。
      if (job.errorCode === 'low_quality_extraction' && jobFileRef.current) {
        setLowQualityFile(jobFileRef.current)
      }
    }
    if (job.status === 'succeeded') jobFileRef.current = null
  }, [documents.reload, job, status.reload])

  const cancelJob = useCallback(() => {
    if (!job) return
    setCancelling(true)
    setJobError(null)
    void (async () => {
      try {
        const result = await cancelIngestJob(job.jobId)
        setJob(result.job)
        if (result.accepted) {
          setNotice(
            `${result.reason || '取消请求已受理'} —— 取消后本次不会留下任何数据（全回滚）。`,
          )
        } else {
          setJobError(result.reason || '任务已结束，取消请求未生效。')
        }
      } catch (err) {
        setJobError(err instanceof Error ? err.message : '取消任务失败')
      } finally {
        setCancelling(false)
      }
    })()
  }, [job])

  // ── 分块预览（读接口，不是检索拼凑） ─────────────────────────
  const openDoc = useCallback((doc: KnowledgeDocSummary) => {
    setSelected(doc)
    setChunks(null)
    setChunkPage(0)
    setChunkError(null)
    chunkRef.current?.abort()
    const controller = new AbortController()
    chunkRef.current = controller
    setChunkLoading(true)

    void (async () => {
      try {
        const result = await fetchDocumentChunks(doc.id, controller.signal)
        if (controller.signal.aborted) return
        setChunks(sortChunks(result.chunks))
      } catch (err) {
        if (isAbortError(err) || controller.signal.aborted) return
        if (err instanceof ApiError && err.status === 404) {
          setChunkError(`${err.message}（可能已被删除，或这篇文档还没有分块）`)
        } else {
          setChunkError(err instanceof Error ? err.message : '读取分块失败')
        }
      } finally {
        if (!controller.signal.aborted) setChunkLoading(false)
      }
    })()
  }, [])

  const chunkView = useMemo(
    () => paginateChunks(chunks ?? [], chunkPage),
    [chunks, chunkPage],
  )

  // ── 删除：二次确认 + 管理员权限处理 ──────────────────────────
  const confirmDelete = useCallback(() => {
    if (!pendingDelete) return
    setDeleting(true)
    setDeleteError(null)
    const controller = new AbortController()
    deleteRef.current = controller
    const target = pendingDelete

    void (async () => {
      try {
        const result = await deleteKnowledgeDocument(target.id, controller.signal)
        setNotice(
          `已删除「${docDisplayName(target)}」：服务端同时移除了 ${
            result.chunksRemoved
          } 个分块及其向量。列表、检索、分块预览都不会再返回它。`,
        )
        if (selected?.id === target.id) {
          setSelected(null)
          setChunks(null)
        }
        if (searchResult?.hits.some((hit) => hitDocumentId(hit) === target.id)) {
          setSearchResult(null)
          setSearchNotice('已删除的文档从检索结果中移除；请重新检索核对。')
        }
        setPendingDelete(null)
        status.reload()
        documents.reload()
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) {
          setDeleteError(
            '该操作需要管理员权限（admin 角色或 L0 人类主权）。当前账号不是管理员，无法删除。',
          )
        } else if (err instanceof ApiError && err.status === 404) {
          setDeleteError(`${err.message}（可能已被其他人删除）`)
          documents.reload()
        } else {
          setDeleteError(err instanceof Error ? err.message : '删除失败')
        }
      } finally {
        deleteRef.current = null
        setDeleting(false)
      }
    })()
  }, [documents.reload, pendingDelete, searchResult, selected, status.reload])

  // ── 检索测试 ─────────────────────────────────────────────────
  const runSearch = useCallback(() => {
    const probe = searchInput.trim()
    if (!probe) {
      setSearchError('请输入要检索的一句话')
      setSearchResult(null)
      return
    }
    searchRef.current?.abort()
    const controller = new AbortController()
    searchRef.current = controller
    setSearching(true)
    setSearchError(null)
    setSearchNotice(null)
    void (async () => {
      try {
        const result = await searchKnowledge(probe, topK, controller.signal)
        if (controller.signal.aborted) return
        setSearchResult(result)
      } catch (err) {
        if (!isAbortError(err)) {
          setSearchError(err instanceof Error ? err.message : '检索失败')
          setSearchResult(null)
        }
      } finally {
        if (!controller.signal.aborted) setSearching(false)
      }
    })()
  }, [searchInput, topK])

  // ── 展示 ─────────────────────────────────────────────────────
  const nameMap = useMemo(() => docNameMap(docs), [docs])
  const need = query.trim().toLowerCase()
  const shownDocs = useMemo(() => {
    if (!need) return docs
    return docs.filter((doc) =>
      [docDisplayName(doc), doc.id, doc.title ?? ''].some((value) =>
        value.toLowerCase().includes(need),
      ),
    )
  }, [docs, need])

  const missingNames = Boolean(
    searchResult?.hits.some((hit) => !nameMap.has(hitDocumentId(hit))),
  )

  return (
    <div className="os-grid">
      <Card title="知识库状态" sub="GET /v1/knowledge/status · 每 30 秒刷新" span={4}>
        <Guard
          loading={status.loading}
          error={status.error}
          data={status.data}
          onRetry={status.reload}
        >
          {(data) => (
            <>
              <dl className="os-kv">
                <dt>可用性</dt>
                <dd>
                  <span className="os-badge" data-tone={data.enabled ? 'ok' : 'danger'}>
                    {data.enabled ? '已启用' : '未启用'}
                  </span>
                </dd>
                <dt>文档数</dt>
                <dd>{normalizedPage ? normalizedPage.total : '—'}</dd>
                <dt>向量总数</dt>
                <dd>{data.vectors}</dd>
                <dt>Embedding</dt>
                <dd>
                  {data.provider ?? '—'}
                  {data.model ? ` · ${data.model}` : ''}
                </dd>
                <dt>不可用原因</dt>
                <dd className="os-model-error">{data.reason ?? '—'}</dd>
              </dl>
              {!data.enabled && (
                <div className="os-alert" style={{ marginTop: 12 }}>
                  <span>
                    {data.message}
                    <br />
                    未启用时不会有任何检索结果 —— 这里不会用模拟向量凑数。
                    <button className="os-link" onClick={onOpenSettings}>
                      去系统设置配置模型
                    </button>
                  </span>
                </div>
              )}
              <p className="os-hint">
                向量总数包含所有客户端与 AI 员工写入的分块；文档数来自服务端文档账本。
              </p>
            </>
          )}
        </Guard>
      </Card>

      <Card
        title="导入文档"
        sub="上传 PDF / Word / 网页 / 纯文本；大文件自动走后台任务（按页进度、可取消）"
        span={8}
        action={
          <button
            className="os-link"
            onClick={() => {
              status.reload()
              documents.reload()
            }}
            title="重新读取状态、文档账本与向量数"
          >
            刷新
          </button>
        }
      >
        {disabled && (
          <div className="os-alert">
            <span>知识库未启用，导入与检索都会失败。先配置 embedding provider 再来。</span>
          </div>
        )}

        <label
          className="os-kb-drop"
          data-active={dragging ? 'true' : 'false'}
          data-disabled={disabled || uploading ? 'true' : 'false'}
          onDragOver={(event) => {
            event.preventDefault()
            if (!disabled && !uploading) setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            type="file"
            className="os-visually-hidden"
            accept={UPLOAD_ACCEPT}
            onChange={onPick}
            disabled={disabled || uploading}
          />
          <strong>把 PDF / Word / 网页 / 纯文本拖到这里，或点击选择</strong>
          <span>
            支持 {UPLOAD_ACCEPT.replaceAll(',', ' / ')} · 单个文件 ≤{' '}
            {formatBytes(UPLOAD_MAX_BYTES)}；超过 {formatBytes(SYNC_UPLOAD_MAX_BYTES)}{' '}
            自动转后台任务，进度按真实页数推进、可取消。扫描版 PDF 会明确报“无文本层”，
            不会空着入库。
          </span>
        </label>

        {uploading && uploadProgress && (
          <div className="os-kb-stage" role="status" aria-live="polite">
            <span data-on="true">
              ① 上传文件
              {uploadProgress.total
                ? ` ${Math.round((uploadProgress.loaded / uploadProgress.total) * 100)}%`
                : ''}
              {uploadProgress.total
                ? `（${formatBytes(uploadProgress.loaded)} / ${formatBytes(uploadProgress.total)}）`
                : ''}
            </span>
            <span>② 服务端解析、分块与向量化（等待响应；大文件请改用后台任务）</span>
            <button className="os-link" onClick={() => uploadRef.current?.abort()}>
              取消上传
            </button>
          </div>
        )}

        {actionError && (
          <div className="os-alert" role="alert">
            <span>
              <strong>{actionError.title}：</strong>
              {actionError.message}
            </span>
          </div>
        )}
        {lowQualityFile && (
          <div className="os-kb-actions" style={{ marginTop: 8 }}>
            <button className="os-btn os-btn-ghost" onClick={retryLowQuality}>
              确认继续入库（接受低质量文本）
            </button>
            <button
              className="os-link"
              onClick={() => {
                setLowQualityFile(null)
                setActionError(null)
                setJobError(null)
              }}
            >
              不入库
            </button>
          </div>
        )}
        {notice && (
          <div className="os-note" role="status">
            <span>{notice}</span>
          </div>
        )}

        <div className="os-kb-grid2">
          <div className="os-field">
            <label htmlFor="kb-name">文档名（文本 / 后台入库在列表里的显示名）</label>
            <div className="os-input">
              <input
                id="kb-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：2026 Q3 复盘"
                disabled={importing || jobActive}
              />
            </div>
          </div>
          <div className="os-field">
            <label htmlFor="kb-docid">doc_id（可选；同名会整体替换旧分块）</label>
            <div className="os-input">
              <input
                id="kb-docid"
                value={docId}
                onChange={(event) => setDocId(event.target.value)}
                placeholder="例如：q3-retro"
                disabled={importing || jobActive}
              />
            </div>
          </div>
        </div>

        <div className="os-field">
          <label htmlFor="kb-text">或直接粘贴文本</label>
          <textarea
            id="kb-text"
            className="os-kb-textarea"
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={5}
            placeholder="把要入库的文档内容粘贴到这里…"
            disabled={importing || jobActive}
          />
        </div>
        <div className="os-model-form-foot">
          <span className="os-action-group" style={{ marginLeft: 0 }}>
            <button className="os-btn" disabled={disabled || importing || !text.trim()} onClick={submitSync}>
              {importing ? '入库中…' : '同步入库'}
            </button>
            <button
              className="os-btn os-btn-ghost"
              disabled={disabled || jobSubmitting || jobActive || !text.trim()}
              onClick={startJob}
            >
              {jobSubmitting ? '提交中…' : '后台入库（可取消）'}
            </button>
            {importing && (
              <button className="os-btn os-btn-ghost" onClick={() => importRef.current?.abort()}>
                取消
              </button>
            )}
          </span>
          <span className="os-hint">
            {text.trim() ? `${text.trim().length.toLocaleString()} 字符` : '尚未输入内容'}
            {` · 单次上限 ${MAX_INGEST_CHARS.toLocaleString()} 字符`}
          </span>
        </div>

        {(job || jobSubmitting || jobError) && (
          <div className="os-kb-job">
            <header className="os-kb-hit-head">
              <strong>后台入库任务</strong>
              {job && (
                <span className="os-badge" data-tone={jobTone(job.status)}>
                  {jobStatusLabel(job.status)}
                </span>
              )}
              {job && !isTerminalJobStatus(job.status) && (
                <span className="os-kb-score">
                  {job.totalPages > 0
                    ? `解析 ${job.extractedPages}/${job.totalPages} 页 · 嵌入 ${job.embeddedPages}/${job.totalPages} 页 · ${job.progressPercent}%`
                    : job.totalChunks
                      ? `${job.processedChunks} / ${job.totalChunks} 分块 · ${job.progressPercent}%`
                      : '等待服务端解析…'}
                </span>
              )}
              {job && isTerminalJobStatus(job.status) && (
                <button className="os-link" onClick={() => setJob(null)}>
                  关闭
                </button>
              )}
            </header>

            {jobSubmitting && <p className="os-hint">正在提交任务，等待服务端返回 job id…</p>}

            {job && (
              <>
                <p className="os-hint">
                  文档 id：{job.documentId || '—'} · 阶段：{jobPhaseLabel(job.phase)}
                  {job.startedAt ? ` · 开始于 ${job.startedAt}` : ''}
                </p>
                {!isTerminalJobStatus(job.status) && (
                  <div
                    className="os-kb-progress"
                    role="progressbar"
                    aria-label="后台入库进度"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(job.progressPercent)}
                  >
                    <span style={{ width: `${job.progressPercent}%` }} />
                  </div>
                )}
                {job.cancelRequested && !isTerminalJobStatus(job.status) && (
                  <p className="os-hint">
                    取消请求已记录：{job.source === 'file'
                      ? '会在下一个页边界生效'
                      : '会在下一个分块边界生效'}
                    ，尚未写入的内容不会落库。
                  </p>
                )}
                {job.status === 'succeeded' && (
                  <p className="os-hint">
                    {job.result?.deduplicated
                      ? `内容重复，未写入新分块（匹配到 ${job.result.matched_document_id ?? '已有文档'}）。`
                      : `已写入 ${job.result?.chunks ?? job.processedChunks} 个分块；当前向量总数 ${
                          job.result?.vectors ?? '—'
                        }。`}
                  </p>
                )}
                {job.status === 'cancelled' && (
                  <p className="os-hint">
                    已取消：本次未写入任何分块（全回滚），列表里不会出现该文档。
                  </p>
                )}
                {job.error && <p className="os-kb-error">失败原因：{job.error}</p>}
                {job.quality?.low_quality && (
                  <p className="os-hint">
                    提取质量：乱码 {job.quality.garbled_chars} 字符（
                    {(job.quality.garbled_ratio * 100).toFixed(1)}%）。
                    {job.errorCode === 'low_quality_extraction'
                      ? '服务端未入库；确认要用这份文本可点下面的“确认继续入库”。'
                      : '已按你的确认入库，检索时可能命中乱码段落。'}
                  </p>
                )}
                {job.note && <p className="os-hint">{job.note}</p>}
                <p className="os-hint">
                  取消策略：全回滚 —— 取消后本次不会留下任何数据（不会出现「部分入库」）。
                  {job.partialCommitPolicy ? ` 服务端策略：${job.partialCommitPolicy}` : ''}
                </p>
                {!isTerminalJobStatus(job.status) && (
                  <div className="os-kb-actions" style={{ marginTop: 8 }}>
                    <button
                      className="os-btn os-btn-ghost"
                      onClick={cancelJob}
                      disabled={cancelling || job.cancelRequested}
                    >
                      {cancelling
                        ? '取消中…'
                        : job.cancelRequested
                          ? '已请求取消（等待边界生效）'
                          : '取消任务'}
                    </button>
                  </div>
                )}
              </>
            )}

            {jobError && (
              <div className="os-alert" role="alert" style={{ marginTop: 10 }}>
                <span>{jobError}</span>
              </div>
            )}
          </div>
        )}
      </Card>

      <Card
        title="文档"
        sub={`服务端文档账本 · 共 ${total} 篇 · 第 ${pageIndex + 1} / ${pageCount} 页`}
        span={selected ? 7 : 12}
        action={
          <button
            className="os-link"
            onClick={() => {
              documents.reload()
              status.reload()
            }}
          >
            刷新
          </button>
        }
      >
        <Guard
          loading={documents.loading || (normalizedPage !== null && pageReady === null)}
          error={documents.error}
          data={pageReady}
          onRetry={documents.reload}
        >
          {(page) => (
            <>
              {pendingDelete && (
                <div className="os-alert" role="alertdialog" aria-label="删除确认">
                  <div>
                    <strong>确认删除「{docDisplayName(pendingDelete)}」？</strong>
                    <p style={{ margin: '4px 0 0' }}>
                      会连同分块与向量一起删除，不可恢复。删除后文档列表、检索与分块预览都将查不到该文档。
                    </p>
                    {deleteError && <p className="os-kb-error">{deleteError}</p>}
                    <div className="os-kb-actions" style={{ marginTop: 8 }}>
                      <button className="os-btn" onClick={confirmDelete} disabled={deleting}>
                        {deleting ? '删除中…' : '确认删除'}
                      </button>
                      <button
                        className="os-btn os-btn-ghost"
                        onClick={() => {
                          setPendingDelete(null)
                          setDeleteError(null)
                        }}
                        disabled={deleting}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {page.total === 0 ? (
                <Empty
                  title="还没有文档"
                  hint="把一份 .txt / .md 拖进上方导入区，或粘贴文本后点「同步入库」/「后台入库」；成功后这里会出现服务端的真实分块数。"
                />
              ) : shownDocs.length === 0 ? (
                <Empty
                  title={need ? `当前页没有文档匹配「${query}」` : '这一页没有文档'}
                  hint={
                    need
                      ? '搜索框只过滤当前页；换页或清空搜索框再试。'
                      : '列表可能已刷新或发生删除；翻到上一页或点刷新重试。'
                  }
                />
              ) : (
                <>
                  <div className="os-table-wrap">
                    <table className="os-table os-kb-docs-table">
                      <thead>
                        <tr>
                          <th>文件名</th>
                          <th>分块</th>
                          <th>入库时间</th>
                          <th>状态</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shownDocs.map((doc) => (
                          <tr
                            key={doc.id}
                            className="os-clickable"
                            onClick={() => openDoc(doc)}
                          >
                            <td data-label="文件名">
                              <button
                                className="os-link os-kb-name"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  openDoc(doc)
                                }}
                              >
                                {docDisplayName(doc)}
                              </button>
                              <span className="os-kb-meta">
                                {doc.id}
                                {doc.title && doc.source ? ` · ${doc.title}` : ''}
                              </span>
                            </td>
                            <td data-label="分块">{doc.chunks}</td>
                            <td
                              data-label="入库时间"
                              title={doc.ingestedAtRaw ?? undefined}
                            >
                              {relativeTime(doc.ingestedAt)}
                            </td>
                            <td data-label="状态">
                              <span className="os-badge" data-tone="ok">
                                已入库
                              </span>
                            </td>
                            <td data-label="操作">
                              <div className="os-kb-actions">
                                <button
                                  className="os-link"
                                  onClick={(event) => {
                                    event.stopPropagation()
                                    openDoc(doc)
                                  }}
                                >
                                  分块
                                </button>
                                <button
                                  className="os-link"
                                  onClick={(event) => {
                                    event.stopPropagation()
                                    setDeleteError(null)
                                    setPendingDelete(doc)
                                  }}
                                >
                                  删除
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {need && shownDocs.length !== page.documents.length && (
                    <p className="os-hint">
                      搜索「{query}」只过滤当前页：本页 {page.documents.length} 篇中{' '}
                      {shownDocs.length} 篇匹配。
                    </p>
                  )}
                </>
              )}

              {page.total > DOC_PAGE_SIZE && (
                <div className="os-kb-pager">
                  <button
                    className="os-btn os-btn-ghost"
                    disabled={pageIndex === 0}
                    onClick={() => setPageIndex(Math.max(0, pageIndex - 1))}
                  >
                    上一页
                  </button>
                  <span>
                    第 {pageIndex + 1} / {pageCount} 页 · 共 {page.total} 篇
                  </span>
                  <button
                    className="os-btn os-btn-ghost"
                    disabled={pageIndex + 1 >= pageCount}
                    onClick={() => setPageIndex(Math.min(pageCount - 1, pageIndex + 1))}
                  >
                    下一页
                  </button>
                </div>
              )}
            </>
          )}
        </Guard>
      </Card>

      {selected && (
        <Card
          title={`分块预览 · ${docDisplayName(selected)}`}
          sub="GET /documents/{id}/chunks · 服务端完整切分（不是检索拼凑）"
          span={5}
          action={
            <button className="os-link" onClick={() => setSelected(null)}>
              关闭
            </button>
          }
        >
          {chunkLoading ? (
            <Loading rows={4} />
          ) : chunkError ? (
            <Failure message={chunkError} onRetry={() => openDoc(selected)} />
          ) : chunks === null ? (
            <Empty title="还没有读取到分块" hint="点击重试重新读取。" />
          ) : chunks.length === 0 ? (
            <Empty
              title="这篇文档没有分块"
              hint="服务端没有返回任何分块 —— 可能仍在入库，或数据已被清理。"
            />
          ) : (
            <>
              <p className="os-hint">
                共 {chunks.length} 个分块；分块多时按页渲染，不会一次塞进几千条。
              </p>
              <div className="os-kb-chunks">
                {chunkView.items.map((chunk) => (
                  <article className="os-kb-chunk" key={chunk.chunkId || chunk.index}>
                    <header className="os-kb-hit-head">
                      <span className="os-badge" data-tone="muted">
                        分块 #{chunk.index}
                      </span>
                      {chunk.headingPath.length > 0 ? (
                        <span className="os-kb-doclabel">
                          {chunk.headingPath.join(' / ')}
                        </span>
                      ) : (
                        <span className="os-kb-score">（无标题路径）</span>
                      )}
                    </header>
                    <pre className="os-pre">{chunk.text}</pre>
                  </article>
                ))}
              </div>
              <div className="os-kb-pager">
                <button
                  className="os-btn os-btn-ghost"
                  disabled={chunkView.pageIndex === 0}
                  onClick={() => setChunkPage(Math.max(0, chunkView.pageIndex - 1))}
                >
                  上一页
                </button>
                <span>
                  第 {chunkView.pageIndex + 1} / {chunkView.pageCount} 页 · 第{' '}
                  {chunkView.start + 1}–{chunkView.end} 条 / 共 {chunkView.total} 条
                </span>
                <button
                  className="os-btn os-btn-ghost"
                  disabled={chunkView.pageIndex + 1 >= chunkView.pageCount}
                  onClick={() =>
                    setChunkPage(Math.min(chunkView.pageCount - 1, chunkView.pageIndex + 1))
                  }
                >
                  下一页
                </button>
              </div>
            </>
          )}
        </Card>
      )}

      <Card title="检索测试" sub="输入一句话，看命中哪些片段、分数多少" span={12}>
        {disabled ? (
          <Empty title="知识库未启用，无法检索" hint="先配置 embedding provider，再回到这里测试。" />
        ) : (
          <>
            <div className="os-kb-search">
              <div className="os-input">
                <input
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') runSearch()
                  }}
                  placeholder="例如：华东区 Q3 的续费率是多少？"
                  aria-label="检索测试输入"
                />
              </div>
              <select
                className="os-select"
                value={topK}
                onChange={(event) => setTopK(Number(event.target.value))}
                aria-label="返回条数"
              >
                {[4, 8, 12, 20].map((value) => (
                  <option key={value} value={value}>
                    返回 {value} 条
                  </option>
                ))}
              </select>
              <span className="os-action-group">
                <button className="os-btn" onClick={runSearch} disabled={searching}>
                  {searching ? '检索中…' : '检索'}
                </button>
                {searching && (
                  <button
                    className="os-btn os-btn-ghost"
                    onClick={() => searchRef.current?.abort()}
                  >
                    取消
                  </button>
                )}
              </span>
            </div>

            {searchError && (
              <div className="os-alert" role="alert" style={{ marginTop: 12 }}>
                <span>{searchError}</span>
              </div>
            )}
            {searchNotice && (
              <div className="os-note" role="status" style={{ marginTop: 12 }}>
                <span>{searchNotice}</span>
              </div>
            )}

            {searchResult && (
              <div style={{ marginTop: 12 }}>
                <p className="os-hint">
                  命中 {searchResult.hits.length} 条 · 库内向量总数 {searchResult.count} ·{' '}
                  {scoreNote(searchResult.retrieval?.mode)}
                </p>
                {searchResult.hits.length === 0 ? (
                  <Empty
                    title="没有命中任何片段"
                    hint="可能原因：库里还没有相关文档；查询与正文用词差异过大；或本身没有配置真实 embedding。"
                  />
                ) : (
                  <div className="os-kb-chunks">
                    {searchResult.hits.map((hit) => (
                      <article className="os-kb-chunk" key={hit.doc_id}>
                        <header className="os-kb-hit-head">
                          <span className="os-badge" data-tone="accent">
                            {scoreLabel(searchResult.retrieval?.mode)}
                          </span>
                          <span className="os-kb-doclabel">
                            {nameMap.get(hitDocumentId(hit)) ?? hitDocumentId(hit)}
                          </span>
                          <span className="os-kb-score">
                            {hit.score.toFixed(4)} · 分块 #{hitChunkIndex(hit)}
                          </span>
                        </header>
                        <pre className="os-pre">{hit.text}</pre>
                      </article>
                    ))}
                  </div>
                )}
                {missingNames && (
                  <p className="os-hint">
                    文档名来自当前页列表；不在本页的文档显示 document_id。翻页后再检索可看到更多名字。
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </Card>

      <Card title="能力边界" sub="只列真实还缺的；已经能用的不在这里" span={12}>
        <ul className="os-list">
          <li>
            <strong>文件类型</strong>：上传只接受 <code>.txt</code> /{' '}
            <code>.md</code>（UTF-8，≤5MB）。PDF / Word / 图片等没有解析器，
            <strong>不会假装解析</strong>；请先转换成纯文本。
          </li>
          <li>
            <strong>上传进度</strong>：multipart 上传走同步入库，只有上传字节进度；
            服务端分块与向量化的那一段没有进度接口，只能等响应。想要逐分块进度与可取消，
            请用「后台入库」。
          </li>
          <li>
            <strong>文档元数据</strong>：列表接口只返回 document_id、来源、分块数与入库时间，
            没有字节 / 字符数，也没有单文档详情接口 —— 所以这里不显示文件大小。
          </li>
          <li>
            <strong>异步任务记录</strong>：任务账本保存在服务端进程内存里，服务重启后历史任务的
            进度与结果无法再查；已经成功入库的文档本身在 SQLite 中，不受影响。
          </li>
          <li>
            <strong>删除权限与粒度</strong>：删除需要管理员权限（admin 角色或 L0 人类主权）；
            只能整篇删除，没有批量删除或单分块删除，也没有改名 / 编辑接口（同一 doc_id
            重新导入会整体替换）。
          </li>
          <li>
            <strong>检索局限</strong>：CJK 单字查询通常不命中（索引是二元组），不做同义词扩展；
            RRF 融合分不是相似度百分比。
          </li>
        </ul>
      </Card>
    </div>
  )
}
