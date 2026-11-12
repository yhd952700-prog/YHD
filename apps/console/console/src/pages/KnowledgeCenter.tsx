/**
 * 知识库中心。
 *
 * 数据来源只有本地服务真实存在的三条接口：
 *   - GET  /v1/knowledge/status     启用状态、向量模型、向量条数
 *   - GET  /v1/knowledge/documents  文档账本（来源、分块数、入库时间、内容哈希）
 *   - POST /v1/knowledge/search     检索（命中片段 + 原始 metadata）
 *   - POST /v1/knowledge/documents/upload  上传（XHR 带进度）
 *
 * 产品要求的 9 个分类，本地服务**没有**对应字段/接口：文档账本不返回 category，
 * 上传与检索也不接收 category。分类视图因此只显示「本地服务还没提供」并列出
 * 缺什么，绝不按文件名猜一个分类来把页面填满。
 *
 * 路由信息先以导出常量放在本模块，等公共文件（nav.ts / App.tsx）由主线统一接。
 */
import { useMemo, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { ApiError, useApi } from '../lib/api'
import type { ApiState } from '../lib/api'
import {
  DOC_PAGE_SIZE,
  UPLOAD_ACCEPT,
  classifyUploadFailure,
  docDisplayName,
  documentsPath,
  fetchDocumentChunks,
  hitDocumentId,
  knowledgeStatusView,
  normalizeDocPage,
  scoreLabel,
  scoreNote,
  searchKnowledge,
  sortChunks,
  uploadKnowledgeDocument,
  validateUploadFile,
} from '../lib/knowledge'
import type {
  DocumentChunk,
  KnowledgeDocSummary,
  KnowledgeHit,
  KnowledgeSearchResult,
  KnowledgeStatus,
} from '../lib/knowledge'
import { Card, Empty, Failure, Guard, Loading } from '../components/ui'
import { AccessDenied, LocalGap } from '../components/LocalGap'
import { numberText, relativeTime } from '../lib/format'
import './KnowledgeCenter.css'

/** 产品要求的知识库分类（信息架构），与本地服务是否支持无关。 */
export const KNOWLEDGE_CATEGORIES = [
  '企业知识',
  '产品资料',
  '客户资料',
  '项目资料',
  '制度',
  '流程',
  '历史决策',
  'Agent 经验',
  'Skills 资料',
] as const

/** 供 nav.ts 后续统一接入；本轮不改公共文件。 */
export const KNOWLEDGE_CENTER_ROUTE = {
  key: 'knowledge-center',
  label: '知识库中心',
  hint: '分类归档、上传、检索，来源与入库时间可追溯',
} as const

const CATEGORY_MISSING = [
  '文档账本没有分类字段：GET /v1/knowledge/documents 只返回文档标识、来源、标题、分块数、内容哈希与入库时间。',
  '上传没有分类入参：POST /v1/knowledge/documents/upload 只接收文件、文档标识与低质量内容开关。',
  '检索没有分类过滤：POST /v1/knowledge/search 只接收检索词、命中数量与相似度阈值。',
  '没有分类管理接口：本地服务里不存在新建 / 重命名 / 删除分类的端点。',
] as const

function metadataText(hit: KnowledgeHit, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = hit.metadata?.[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  }
  return null
}

function hitLocation(hit: KnowledgeHit): string {
  const parts: string[] = []
  const source = metadataText(hit, ['source', 'filename', 'document_source'])
  if (source) parts.push(source)
  const page = metadataText(hit, ['page'])
  const pages = metadataText(hit, ['pages'])
  const location = metadataText(hit, ['location'])
  if (page) parts.push(`第 ${page} 页`)
  else if (pages) parts.push(`页码 ${pages}`)
  else if (location) parts.push(location)
  return parts.length ? parts.join(' · ') : hitDocumentId(hit)
}

function StatusBlock({ state }: { state: ApiState<KnowledgeStatus> }) {
  if (state.status === 403 || state.status === 404) {
    return (
      <AccessDenied
        what="知识库状态"
        detail="本地服务对越权与不存在返回同一个中性响应，因此这里不显示「未启用」。"
      />
    )
  }
  if (state.error) return <Failure message={state.error} onRetry={state.reload} />
  if (!state.data) return <Loading rows={1} />

  const status = state.data
  const view = knowledgeStatusView(status)
  if (view.enabled) {
    return (
      <p className="kc-status kc-status-ok" role="status">
        <span className="os-badge" data-tone="ok">
          已启用
        </span>
        <span>
          向量模型：{status.provider ?? '未知'}
          {status.model ? ` / ${status.model}` : ''} · 向量 {status.vectors} 条
        </span>
      </p>
    )
  }
  return (
    <div className="kc-status kc-status-off" role="status">
      <span className="os-badge" data-tone="warn">
        未启用
      </span>
      <div className="kc-status-text">
        <strong>{view.title}</strong>
        <p>{view.detail}</p>
        {view.command && <code className="kc-command">{view.command}</code>}
      </div>
    </div>
  )
}

export function KnowledgeCenter({ query = '' }: { query?: string }) {
  const [category, setCategory] = useState<string | null>(null)
  const [page, setPage] = useState(0)
  const [searchInput, setSearchInput] = useState(query)
  const [searchResult, setSearchResult] = useState<KnowledgeSearchResult | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [searchDenied, setSearchDenied] = useState(false)
  const [searching, setSearching] = useState(false)
  const [selectedDoc, setSelectedDoc] = useState<KnowledgeDocSummary | null>(null)
  const [chunks, setChunks] = useState<DocumentChunk[] | null>(null)
  const [chunksError, setChunksError] = useState<string | null>(null)
  const [chunksLoading, setChunksLoading] = useState(false)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [uploadPct, setUploadPct] = useState(0)
  const [uploadNotice, setUploadNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const statusState = useApi<KnowledgeStatus>('/v1/knowledge/status', 30_000)
  const docsState = useApi<unknown>(documentsPath(DOC_PAGE_SIZE, page * DOC_PAGE_SIZE), 0)
  const docPage = useMemo(
    () => (docsState.data ? normalizeDocPage(docsState.data) : null),
    [docsState.data],
  )
  const ready = statusState.data?.enabled === true

  const runSearch = async () => {
    const text = searchInput.trim()
    setSearchDenied(false)
    setSearchError(null)
    setSearchResult(null)
    if (!text) {
      setSearchError('请输入要检索的内容。')
      return
    }
    setSearching(true)
    try {
      setSearchResult(await searchKnowledge(text, 8))
    } catch (error) {
      if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
        setSearchDenied(true)
      } else {
        setSearchError(error instanceof Error ? error.message : '检索失败，原因未知')
      }
    } finally {
      setSearching(false)
    }
  }

  const openChunks = async (doc: KnowledgeDocSummary) => {
    setSelectedDoc(doc)
    setChunks(null)
    setChunksError(null)
    setChunksLoading(true)
    try {
      const result = await fetchDocumentChunks(doc.id)
      setChunks(sortChunks(result.chunks))
    } catch (error) {
      setChunksError(error instanceof Error ? error.message : '分块读取失败，原因未知')
    } finally {
      setChunksLoading(false)
    }
  }

  const onPickFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setUploadNotice(null)
    const rejection = validateUploadFile(file)
    if (rejection) {
      setUploadNotice({ ok: false, text: `${rejection.title}：${rejection.message}` })
      return
    }
    setUploadBusy(true)
    setUploadPct(0)
    try {
      const result = await uploadKnowledgeDocument(file, {
        onProgress: (loaded, total) => {
          if (total > 0) setUploadPct(Math.min(100, Math.round((loaded / total) * 100)))
        },
      })
      setUploadNotice({
        ok: true,
        text: `已入库：${file.name}，${result.chunks} 个分块${
          result.deduplicated ? '（内容重复，未新增分块）' : ''
        }`,
      })
      setPage(0)
      docsState.reload()
      statusState.reload()
    } catch (error) {
      const failure = classifyUploadFailure(error, file)
      setUploadNotice({ ok: false, text: `${failure.title}：${failure.message}` })
    } finally {
      setUploadBusy(false)
      setUploadPct(0)
    }
  }

  const totalPages = docPage ? Math.max(1, Math.ceil(docPage.total / Math.max(1, docPage.limit))) : 1

  return (
    <div className="os-grid kc-page">
      <Card
        title="知识库中心"
        sub="状态、上传与文档账本都直接来自本地服务，不预填任何示例数据"
        span={12}
      >
        <StatusBlock state={statusState} />

        <div className="kc-upload">
          <div className="kc-upload-copy">
            <strong>上传文档</strong>
            <p>
              支持 {UPLOAD_ACCEPT.split(',').join(' / ')}。上传后按内容哈希去重，重复内容不会新增分块。
            </p>
          </div>
          <div className="kc-upload-action">
            <input
              ref={fileInput}
              type="file"
              accept={UPLOAD_ACCEPT}
              aria-label="选择要上传的文档"
              onChange={onPickFile}
              disabled={uploadBusy || !ready}
            />
            {uploadBusy && (
              <progress className="kc-progress" value={uploadPct} max={100} aria-label="上传进度">
                {uploadPct}%
              </progress>
            )}
          </div>
        </div>
        {!ready && statusState.data && (
          <p className="kc-hint">向量模型未启用，上传入口暂不可用；先按上面的提示恢复服务。</p>
        )}
        {uploadNotice && (
          <p className={`kc-notice${uploadNotice.ok ? ' kc-notice-ok' : ' kc-notice-bad'}`} role="status">
            {uploadNotice.text}
          </p>
        )}
      </Card>

      <Card
        title="分类"
        sub="9 个分类是产品要求的信息架构；本地服务还没有分类字段"
        span={4}
      >
        <nav className="kc-cats" aria-label="知识库分类">
          <button
            type="button"
            className={`kc-cat${category === null ? ' kc-cat-active' : ''}`}
            aria-pressed={category === null}
            onClick={() => setCategory(null)}
          >
            全部文档
          </button>
          {KNOWLEDGE_CATEGORIES.map((name) => (
            <button
              key={name}
              type="button"
              className={`kc-cat${category === name ? ' kc-cat-active' : ''}`}
              aria-pressed={category === name}
              onClick={() => setCategory(name)}
            >
              {name}
            </button>
          ))}
        </nav>
        <p className="kc-hint">
          分类按钮不显示数量：本地服务没有分类字段，任何数量都会是编的。当前所有文档只出现在「全部文档」。
        </p>
      </Card>

      {category !== null ? (
        <Card title={`${category} · 分类视图`} span={8}>
          <LocalGap
            title={`「${category}」分类暂时无法归档`}
            summary="文档账本没有分类字段，上传与检索也不接收分类。为了不把猜测当成分类，这里不展示任何按文件名推断的归类结果。"
            missing={CATEGORY_MISSING}
            note="仍然可用的部分：全部文档的列表、上传与检索。点左侧「全部文档」即可回到这些能力。"
          />
        </Card>
      ) : (
        <>
          <Card
            title="检索"
            sub="命中片段、分数语义与来源都取自本地服务返回，不做二次推断"
            span={8}
          >
            <div className="kc-search">
              <input
                className="os-input-plain"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void runSearch()
                }}
                placeholder="输入一句话，检索知识库里的片段"
                aria-label="知识库检索输入"
              />
              <button
                type="button"
                className="os-btn"
                onClick={() => void runSearch()}
                disabled={searching}
              >
                {searching ? '检索中…' : '检索'}
              </button>
            </div>

            {searchDenied && (
              <AccessDenied
                what="知识库检索结果"
                detail="本地服务没有返回检索结果，也没有说明原因；这里不显示「0 条命中」。"
              />
            )}
            {searchError && <Failure message={searchError} onRetry={() => void runSearch()} />}

            {searchResult && searchResult.enabled === false && (
              <LocalGap
                title="检索这次没有执行"
                summary={
                  searchResult.message ||
                  searchResult.reason ||
                  '本地服务表示索引当前不可用，但没有给出原因。'
                }
                missing={[
                  '索引可用性原因：POST /v1/knowledge/search 的 reason / message 字段。',
                  '恢复方式：由运维调用 POST /v1/knowledge/maintenance/reindex 重建索引。',
                ]}
                note="上传与文档列表不受这条检索失败影响。"
              />
            )}

            {searchResult && searchResult.enabled !== false && (
              <div className="kc-hits">
                <p className="kc-hint">
                  {searchResult.count} 条命中 · {scoreLabel(searchResult.retrieval?.mode)} ·{' '}
                  {scoreNote(searchResult.retrieval?.mode)}
                </p>
                {searchResult.hits.length === 0 ? (
                  <Empty
                    title="没有命中相关片段"
                    hint="换一个说法再试；如果知识库还是空的，先在上面上传文档。"
                  />
                ) : (
                  <ol className="kc-hit-list">
                    {searchResult.hits.map((hit) => (
                      <li key={hit.doc_id} className="kc-hit">
                        <div className="kc-hit-head">
                          <span className="os-badge" data-tone="accent">
                            分数 {numberText(hit.score, 3)}
                          </span>
                          <span className="kc-hit-source">{hitLocation(hit)}</span>
                        </div>
                        <p className="kc-hit-text">{hit.text}</p>
                        <code className="kc-hit-id">{hit.doc_id}</code>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}
          </Card>

          <Card
            title="文档列表"
            sub="来源、分块数、入库时间与内容哈希均取自文档账本"
            span={12}
          >
            {docsState.status === 403 || docsState.status === 404 ? (
              <AccessDenied
                what="知识库文档"
                detail="本地服务对越权与不存在返回同一个中性响应，因此这里不显示空列表。"
              />
            ) : (
              <Guard
                loading={docsState.loading}
                error={docsState.error}
                data={docPage}
                isEmpty={(value) => value.documents.length === 0}
                emptyTitle="还没有文档"
                emptyHint="在上面上传第一个文件后，这里会显示它的来源、分块数与入库时间。"
                onRetry={docsState.reload}
              >
                {(value) => (
                  <>
                    <div className="os-table-wrap">
                      <table className="os-table kc-table">
                        <thead>
                          <tr>
                            <th>文档</th>
                            <th>来源</th>
                            <th>分块</th>
                            <th>入库时间</th>
                            <th>内容哈希</th>
                            <th>操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          {value.documents.map((doc) => (
                            <tr key={doc.id}>
                              <td>
                                <strong>{docDisplayName(doc)}</strong>
                                <code className="kc-doc-id">{doc.id}</code>
                              </td>
                              <td>{doc.source ?? '未标注（文本入库）'}</td>
                              <td>{doc.chunks}</td>
                              <td title={doc.ingestedAtRaw ?? undefined}>
                                {relativeTime(doc.ingestedAt)}
                              </td>
                              <td>
                                <code>{doc.contentHash ? doc.contentHash.slice(0, 12) : '—'}</code>
                              </td>
                              <td>
                                <button
                                  type="button"
                                  className="os-btn os-btn-ghost"
                                  onClick={() => void openChunks(doc)}
                                >
                                  查看分块
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="kc-pager">
                      <span className="kc-hint">
                        共 {value.total} 份文档 · 第 {value.offset + 1}–
                        {Math.min(value.offset + value.documents.length, value.total)} 条
                      </span>
                      <span className="kc-pager-actions">
                        <button
                          type="button"
                          className="os-btn os-btn-ghost"
                          onClick={() => setPage((current) => Math.max(0, current - 1))}
                          disabled={page === 0}
                        >
                          上一页
                        </button>
                        <button
                          type="button"
                          className="os-btn os-btn-ghost"
                          onClick={() => setPage((current) => current + 1)}
                          disabled={page + 1 >= totalPages}
                        >
                          下一页
                        </button>
                      </span>
                    </div>
                    <p className="kc-hint">
                      时间取自文档账本的入库记录；本地服务没有单独的「最后修改时间」字段，所以这里不另造一个。
                    </p>
                  </>
                )}
              </Guard>
            )}
          </Card>

          {selectedDoc && (
            <Card
              title={`分块 · ${docDisplayName(selectedDoc)}`}
              sub="按服务端切分顺序展示，用于核对来源与上下文"
              span={12}
            >
              {chunksLoading && <Loading rows={3} />}
              {chunksError && <Failure message={chunksError} />}
              {chunks && chunks.length === 0 && (
                <Empty title="这份文档没有分块" hint="可能是入库失败或内容为空，可重新上传。" />
              )}
              {chunks && chunks.length > 0 && (
                <ol className="kc-chunks">
                  {chunks.map((chunk) => (
                    <li key={chunk.chunkId}>
                      <div className="kc-chunk-head">
                        <span className="os-badge">#{chunk.index}</span>
                        <span className="kc-chunk-path">
                          {chunk.headingPath.length ? chunk.headingPath.join(' / ') : '（无标题路径）'}
                        </span>
                      </div>
                      <p className="kc-chunk-text">{chunk.text}</p>
                      <code className="kc-hit-id">{chunk.chunkId}</code>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          )}
        </>
      )}
    </div>
  )
}
