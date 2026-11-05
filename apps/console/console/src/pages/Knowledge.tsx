/**
 * 知识中心 —— 真正能用的知识库界面。
 *
 * 之前这一页只是"端点清单"：用户点进来看到 `/knowledge/*` 路由列表，看不到
 * 自己有哪些文档、也不知道检索到底有没有用。现在它做四件事：
 *
 *   1. 状态：`/v1/knowledge/status` 的真实可用性、embedding provider、向量总数；
 *   2. 导入：拖入 / 选择纯文本文件或粘贴文本，服务端真实入库，失败给真实原因；
 *   3. 文档：本机导入记录（文件名、大小、分块数、入库时间、状态）。**本机记录
 *      不是服务端真相** —— 服务端没有文档列表接口，卡片标题里写清楚；
 *   4. 分块预览 + 检索测试：全部结果来自 `/v1/knowledge/search` 的真实响应。
 *
 * 进度为什么这么显示：文件读取有真实字节进度；"服务端分块与向量化"没有进度
 * 接口，所以如实标"等待中"，不伪造百分比。取消会真正 abort HTTP 请求，并提示
 * "服务端可能仍完成了本次入库" —— 中止请求不等于撤销服务端工作。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import { useApi } from '../lib/api'
import {
  byteLength,
  docNameMap,
  formatBytes,
  hitChunkIndex,
  hitDocumentId,
  ingestKnowledge,
  isAbortError,
  loadDocs,
  makeSample,
  MAX_INGEST_CHARS,
  MAX_TOP_K,
  previewDocumentChunks,
  scoreLabel,
  scoreNote,
  searchKnowledge,
  upsertDoc,
  validateIngestText,
} from '../lib/knowledge'
import type {
  ChunkView,
  KnowledgeDoc,
  KnowledgeSearchResult,
  KnowledgeStatus,
} from '../lib/knowledge'
import { relativeTime } from '../lib/format'
import { Card, Empty, Failure, Guard, Loading } from '../components/ui'

type IngestStage = 'idle' | 'reading' | 'sending'

export function Knowledge({
  query,
  principal,
  onOpenSettings,
}: {
  query: string
  principal: string
  onOpenSettings: () => void
}) {
  const status = useApi<KnowledgeStatus>('/v1/knowledge/status', 30_000)
  const disabled = status.data ? !status.data.enabled : false

  // ── 本机导入记录 ─────────────────────────────────────────────
  const [docs, setDocs] = useState<KnowledgeDoc[]>(() => loadDocs(principal))
  useEffect(() => {
    // 换账号就换一份记录：知识库写入是按人类主体授权的，不该串号。
    setDocs(loadDocs(principal))
  }, [principal])

  // ── 导入 ─────────────────────────────────────────────────────
  const [text, setText] = useState('')
  const [name, setName] = useState('')
  const [docId, setDocId] = useState('')
  const [dragging, setDragging] = useState(false)
  const [stage, setStage] = useState<IngestStage>('idle')
  const [readProgress, setReadProgress] = useState<{ loaded: number; total: number } | null>(null)
  const [ingestError, setIngestError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const readerRef = useRef<FileReader | null>(null)
  const ingestRef = useRef<AbortController | null>(null)

  // ── 分块预览 ─────────────────────────────────────────────────
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [chunks, setChunks] = useState<ChunkView[] | null>(null)
  const [examined, setExamined] = useState(0)
  const [previewMode, setPreviewMode] = useState<string | undefined>(undefined)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const previewRef = useRef<AbortController | null>(null)

  // ── 检索测试 ─────────────────────────────────────────────────
  const [searchInput, setSearchInput] = useState('')
  const [topK, setTopK] = useState(8)
  const [searchResult, setSearchResult] = useState<KnowledgeSearchResult | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const searchRef = useRef<AbortController | null>(null)

  useEffect(
    () => () => {
      readerRef.current?.abort()
      ingestRef.current?.abort()
      previewRef.current?.abort()
      searchRef.current?.abort()
    },
    [],
  )

  const submit = useCallback(
    async (content: string, displayName: string, source: 'file' | 'text') => {
      const localError = validateIngestText(content)
      if (localError) {
        setStage('idle')
        setIngestError(localError)
        return
      }

      ingestRef.current?.abort()
      const controller = new AbortController()
      ingestRef.current = controller
      setStage('sending')
      setIngestError(null)
      setNotice(null)

      try {
        const result = await ingestKnowledge(content, {
          name: displayName,
          source,
          docId: docId.trim(),
          signal: controller.signal,
        })
        if (result.deduplicated) {
          // 服务端按内容哈希去重：没有写入新分块，返回的 document_id 并不存在于库中。
          // 这种情况下不能再造一条"已入库 0 分块"的假记录。
          const matched = result.matched_document_id ?? ''
          const matchedName = docs.find((doc) => doc.id === matched)?.name
          setNotice(
            `内容与已有文档「${matchedName ?? (matched || '（未知 id）')}」完全相同，服务端未重复写入（本次新增 0 个分块）。`,
          )
          setText('')
          setName('')
          setDocId('')
          status.reload()
          return
        }
        const record: KnowledgeDoc = {
          id: result.document_id,
          name: displayName,
          bytes: byteLength(content),
          chars: content.length,
          chunks: Number.isFinite(result.chunks) ? result.chunks : 0,
          at: Date.now() / 1000,
          state: 'ok',
          sample: makeSample(content),
          source,
        }
        setDocs(upsertDoc(principal, record))
        setNotice(
          `已入库：${displayName} · 服务端分成 ${record.chunks} 个分块 · 当前向量总数 ${result.vectors}`,
        )
        setText('')
        setName('')
        setDocId('')
        status.reload()
      } catch (err) {
        if (isAbortError(err)) {
          setNotice('已取消。请求已中止 —— 服务端可能仍完成了本次入库，请点「刷新」核对向量数。')
        } else {
          const message = err instanceof Error ? err.message : '未知错误'
          // 失败也留一条记录：要求就是"失败要显示真实原因"，刷新后不能凭空消失。
          setDocs(
            upsertDoc(principal, {
              id: `local-${Date.now()}`,
              name: displayName,
              bytes: byteLength(content),
              chars: content.length,
              chunks: 0,
              at: Date.now() / 1000,
              state: 'failed',
              error: message,
              sample: makeSample(content),
              source,
            }),
          )
          setIngestError(`入库失败：${message}`)
        }
      } finally {
        ingestRef.current = null
        setReadProgress(null)
        setStage('idle')
      }
    },
    // 只依赖 reload（useCallback 稳定引用）：把整个 status 放进依赖会让 submit
    // 每次渲染都换新函数，进而让 readFile / onDrop 也全部重建。
    [docId, docs, principal, status.reload],
  )

  const readFile = useCallback(
    (file: File) => {
      setDragging(false)
      setIngestError(null)
      setNotice(null)
      setSelectedId(null)
      setText('')
      setName(file.name)
      setReadProgress({ loaded: 0, total: file.size || 0 })
      setStage('reading')

      const reader = new FileReader()
      readerRef.current = reader
      reader.onprogress = (event) => {
        if (event.lengthComputable) {
          setReadProgress({ loaded: event.loaded, total: event.total })
        }
      }
      reader.onerror = () => {
        readerRef.current = null
        setReadProgress(null)
        setStage('idle')
        setIngestError(`读取文件失败：${reader.error?.message ?? '未知原因'}`)
      }
      reader.onload = () => {
        readerRef.current = null
        setReadProgress(null)
        const content = typeof reader.result === 'string' ? reader.result : ''
        setText(content)
        void submit(content, file.name, 'file')
      }
      reader.readAsText(file, 'utf-8')
    },
    [submit],
  )

  const cancelIngest = useCallback(() => {
    readerRef.current?.abort()
    readerRef.current = null
    ingestRef.current?.abort()
    ingestRef.current = null
    setReadProgress(null)
    setStage('idle')
    setNotice('已取消本次导入。文件读取会立即停止；已发出的服务端请求被中止，但服务端可能仍完成了入库。')
  }, [])

  const onDrop = useCallback(
    (event: DragEvent<HTMLLabelElement>) => {
      event.preventDefault()
      setDragging(false)
      const file = event.dataTransfer?.files?.[0]
      if (file) readFile(file)
    },
    [readFile],
  )

  const onPick = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      if (file) readFile(file)
      // 清空 input：同一个文件再次选择也要触发 change。
      event.target.value = ''
    },
    [readFile],
  )

  // ── 打开文档 → 取真实分块 ─────────────────────────────────────
  const openDoc = useCallback((doc: KnowledgeDoc) => {
    setSelectedId(doc.id)
    setChunks(null)
    setExamined(0)
    setPreviewMode(undefined)
    setPreviewError(null)
    if (doc.state === 'failed') return

    previewRef.current?.abort()
    const controller = new AbortController()
    previewRef.current = controller
    setPreviewLoading(true)
    void (async () => {
      try {
        const result = await previewDocumentChunks(doc, controller.signal)
        if (controller.signal.aborted) return
        setChunks(result.chunks)
        setExamined(result.examined)
        setPreviewMode(result.mode)
      } catch (err) {
        if (!isAbortError(err)) {
          setPreviewError(err instanceof Error ? err.message : '取回分块失败')
        }
      } finally {
        if (!controller.signal.aborted) setPreviewLoading(false)
      }
    })()
  }, [])

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

  const nameMap = useMemo(() => docNameMap(docs), [docs])
  const need = query.trim().toLowerCase()
  const shownDocs = useMemo(() => {
    if (!need) return docs
    return docs.filter((doc) =>
      [doc.name, doc.id, doc.error ?? ''].some((value) => value.toLowerCase().includes(need)),
    )
  }, [docs, need])

  const selected = selectedId ? docs.find((doc) => doc.id === selectedId) ?? null : null
  const localChunks = docs
    .filter((doc) => doc.state === 'ok')
    .reduce((sum, doc) => sum + doc.chunks, 0)
  const serverVectors = status.data?.vectors ?? 0
  const otherVectors = Math.max(0, serverVectors - localChunks)

  return (
    <div className="os-grid">
      <Card title="知识库状态" sub="真实端点 /v1/knowledge/status" span={4}>
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
              {data.enabled && otherVectors > 0 && (
                <p className="os-hint" style={{ marginTop: 10 }}>
                  服务端共有 {serverVectors} 个向量；本机记录中已入库文档合计 {localChunks} 个分块，
                  另有 {otherVectors} 个由其他客户端 / AI 员工写入，不在本页列表中。
                </p>
              )}
            </>
          )}
        </Guard>
      </Card>

      <Card
        title="导入文档"
        sub="纯文本入库 → 服务端分块 + 向量化（真实写入）"
        span={8}
        action={
          <button className="os-link" onClick={status.reload} title="重新读取状态与向量数">
            刷新
          </button>
        }
      >
        {disabled && (
          <div className="os-alert">
            <span>知识库未启用，导入会直接失败。先配置 embedding provider 再来。</span>
          </div>
        )}

        <label
          className="os-kb-drop"
          data-active={dragging ? 'true' : 'false'}
          data-disabled={disabled || stage !== 'idle' ? 'true' : 'false'}
          onDragOver={(event) => {
            event.preventDefault()
            if (!disabled && stage === 'idle') setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            type="file"
            className="os-visually-hidden"
            accept=".txt,.md,.markdown,.csv,.json,.log,.yaml,.yml,.xml,.html,.htm,.rst,.ts,.tsx,.js,.jsx,.py,.java,.go,.rs,.sql,.ini,.conf"
            onChange={onPick}
            disabled={disabled || stage !== 'idle'}
          />
          <strong>把文本文件拖到这里，或点击选择</strong>
          <span>
            支持 .txt / .md / .csv / .json / .log 等纯文本；单次上限{' '}
            {MAX_INGEST_CHARS.toLocaleString()} 字符（服务端限制）
          </span>
        </label>

        {stage !== 'idle' && (
          <div className="os-kb-stage" role="status" aria-live="polite">
            <span data-on={stage === 'reading' ? 'true' : undefined}>
              ① 读取文件
              {stage === 'reading' && readProgress
                ? ` ${readProgress.total ? Math.round((readProgress.loaded / readProgress.total) * 100) : 0}%`
                : ' 完成'}
            </span>
            <span data-on={stage === 'sending' ? 'true' : undefined}>
              ② 服务端分块与向量化（无进度接口，等待中）
            </span>
            <span>③ 返回结果</span>
            <button className="os-link" onClick={cancelIngest}>
              取消
            </button>
          </div>
        )}

        {ingestError && (
          <div className="os-alert" role="alert">
            <span>{ingestError}</span>
          </div>
        )}
        {notice && (
          <div className="os-note" role="status">
            <span>{notice}</span>
          </div>
        )}

        <div className="os-kb-grid2">
          <div className="os-field">
            <label htmlFor="kb-name">文档名（本机记录用，可留空）</label>
            <div className="os-input">
              <input
                id="kb-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：2026 Q3 复盘.md"
                disabled={stage !== 'idle'}
              />
            </div>
          </div>
          <div className="os-field">
            <label htmlFor="kb-docid">doc_id（可选，重名会整体替换旧分块）</label>
            <div className="os-input">
              <input
                id="kb-docid"
                value={docId}
                onChange={(event) => setDocId(event.target.value)}
                placeholder="例如：q3-retro"
                disabled={stage !== 'idle'}
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
            disabled={stage !== 'idle'}
          />
        </div>
        <div className="os-model-form-foot">
          <button
            className="os-btn"
            disabled={disabled || stage !== 'idle' || !text.trim()}
            onClick={() => void submit(text, name.trim() || '粘贴文本', 'text')}
          >
            导入这段文本
          </button>
          <span className="os-hint">
            {text.trim() ? `${text.trim().length.toLocaleString()} 字符` : '尚未输入内容'}
          </span>
        </div>
      </Card>

      <Card
        title="文档"
        sub="本机导入记录 —— 服务端目前没有文档列表接口"
        span={selected ? 7 : 12}
      >
        {shownDocs.length === 0 ? (
          <Empty
            title={need ? `没有文档匹配「${query}」` : docs.length === 0 ? '还没有文档' : '没有可显示的文档'}
            hint={
              need
                ? undefined
                : '把一份 .txt / .md / .csv 拖进上方导入区，或粘贴一段文本，入库成功后就会出现在这里。'
            }
          />
        ) : (
          <>
            <div className="os-table-wrap">
              <table className="os-table os-kb-docs-table">
                <thead>
                  <tr>
                    <th>文件名</th>
                    <th>大小</th>
                    <th>分块</th>
                    <th>入库时间</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {shownDocs.map((doc) => (
                    <tr
                      key={doc.id}
                      className={doc.state === 'ok' ? 'os-clickable' : undefined}
                      onClick={() => {
                        if (doc.state === 'ok') openDoc(doc)
                      }}
                    >
                      <td data-label="文件名">
                        {doc.state === 'ok' ? (
                          <button
                            className="os-link os-kb-name"
                            onClick={(event) => {
                              event.stopPropagation()
                              openDoc(doc)
                            }}
                          >
                            {doc.name}
                          </button>
                        ) : (
                          <span className="os-kb-name">{doc.name}</span>
                        )}
                        <span className="os-kb-meta">{doc.id}</span>
                      </td>
                      <td data-label="大小">{formatBytes(doc.bytes)}</td>
                      <td data-label="分块">
                        {doc.state === 'ok' ? `${doc.chunks}` : '—'}
                      </td>
                      <td data-label="入库时间" title={new Date(doc.at * 1000).toLocaleString()}>
                        {relativeTime(doc.at)}
                      </td>
                      <td data-label="状态">
                        <span
                          className="os-badge"
                          data-tone={doc.state === 'ok' ? 'ok' : 'danger'}
                        >
                          {doc.state === 'ok' ? '已入库' : '失败'}
                        </span>
                        {doc.error && <span className="os-kb-error">{doc.error}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {need && shownDocs.length !== docs.length && (
              <p className="os-hint">
                搜索「{query}」过滤后显示 {shownDocs.length} / {docs.length} 条本机记录。
              </p>
            )}
          </>
        )}
      </Card>

      {selected && (
        <Card
          title={`分块预览 · ${selected.name}`}
          sub="来自服务端真实检索 —— 不是本机推测的切分"
          span={5}
          action={
            <button className="os-link" onClick={() => setSelectedId(null)}>
              关闭
            </button>
          }
        >
          {selected.state === 'failed' ? (
            <Empty
              title="该文档入库失败，没有可检索的分块"
              hint={selected.error ? `失败原因：${selected.error}` : undefined}
            />
          ) : previewLoading ? (
            <Loading rows={3} />
          ) : previewError ? (
            <Failure message={previewError} onRetry={() => openDoc(selected)} />
          ) : chunks === null || chunks.length === 0 ? (
            <Empty
              title="这次没有取回该文档的分块"
              hint={`服务端返回的 ${examined} 条最相关分块中没有本文档。入库时服务端上报 ${selected.chunks} 个分块；换一个更贴近正文的检索词可提高命中率。`}
            />
          ) : (
            <>
              <p className="os-hint">
                服务端最多返回 {MAX_TOP_K} 条最相关分块（top_k 硬上限），因此这里不是完整切分：
                本次命中 {examined} 条，其中本文档 {chunks.length} 条。
              </p>
              <div className="os-kb-chunks">
                {chunks.map((chunk) => (
                  <article className="os-kb-chunk" key={chunk.docId}>
                    <header className="os-kb-hit-head">
                      <span className="os-badge" data-tone="muted">
                        分块 #{chunk.index}
                      </span>
                      <span className="os-kb-score">
                        {scoreLabel(previewMode)} {chunk.score.toFixed(4)}
                      </span>
                    </header>
                    <pre className="os-pre">{chunk.text}</pre>
                  </article>
                ))}
              </div>
            </>
          )}
        </Card>
      )}

      <Card title="检索测试" sub="输入一句话，看命中哪些片段、相似度多少" span={12}>
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
              </div>
            )}
          </>
        )}
      </Card>

      <Card title="能力边界" sub="这个页面依赖的真实接口，以及目前确实缺少的接口" span={12}>
        <ul className="os-list">
          <li>
            已接入：<code>GET /v1/knowledge/status</code>、<code>POST /v1/knowledge/documents</code>、
            <code>POST /v1/knowledge/search</code> —— 状态、入库、检索都是真实调用，失败会原样显示后端原因。
          </li>
          <li>
            <strong>缺少</strong>（因此本页不提供，也不会伪造）：文档列表 <code>GET /v1/knowledge/documents</code>、
            单文档分块 <code>GET /v1/knowledge/documents/{'{id}'}/chunks</code>、删除
            <code>DELETE /v1/knowledge/documents/{'{id}'}</code>、文件上传（multipart）、
            异步入库任务（进度 / 取消）。
          </li>
          <li>
            结果：文档列表只能是<strong>本机导入记录</strong>（换设备/换账号就看不到）；
            分块预览只能拿到服务端 top_k ≤ {MAX_TOP_K} 的最相关分块；取消只是中止请求，
            不能撤销服务端已开始的向量化。
          </li>
        </ul>
      </Card>
    </div>
  )
}
