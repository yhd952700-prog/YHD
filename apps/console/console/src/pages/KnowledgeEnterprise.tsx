/**
 * 企业知识库中心（0.3.0）。
 *
 * 覆盖：分类树、上传（PDF/DOCX/XLSX/图片/TXT/MD/CSV）、全文检索、按分类与
 * 过期过滤、详情、版本对比、过期标记，以及同页的长期记忆视图。
 *
 * 页面只呈现后端已经给出的字段，不按文件名猜分类、不把词法分数写成「相似度」，
 * 图片如实标注「仅元数据可检索」。导航与路由由模板线统一接入，本文件只导出
 * 页面组件与建议的 route 常量。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'
import { Card, Empty, Failure, Loading } from '../components/ui'
import { numberText } from '../lib/format'
import {
  UPLOAD_MAX_BYTES,
  createCategory,
  createMemory,
  deleteCategory,
  deleteDocument,
  deleteMemory,
  diffVersions,
  downloadDocument,
  fetchCategories,
  fetchDocument,
  fetchDocuments,
  fetchExpiring,
  fetchMemories,
  fetchMemory,
  fetchMemoryViews,
  fetchStatus,
  fetchVersion,
  ingestText,
  searchDocuments,
  updateDocument,
  updateMemory,
  uploadDocument,
} from '../lib/knowledgeEnterprise'
import type {
  CategoryNode,
  DocumentDetail,
  DocumentDiff,
  DocumentSummary,
  DocumentVersionDetail,
  EnterpriseStatus,
  ExpiringResult,
  MemoryDetail,
  MemoryEntry,
  MemoryPage,
  MemoryViews,
  SearchHit,
  SearchResult,
} from '../lib/knowledgeEnterprise'
import './KnowledgeEnterprise.css'

/** 供 nav.ts 接入。 */
export const KNOWLEDGE_ENTERPRISE_ROUTE = {
  key: 'knowledge-enterprise',
  label: '企业知识库',
  hint: '分类归档、全文检索、版本对比、过期提醒与长期记忆',
} as const

type Tab = 'documents' | 'memory'

interface UploadFormState {
  title: string
  doc_key: string
  category_id: string
  project_id: string
  visibility: string
  source_ref: string
  description: string
  expires_at: string
  expiry_notice_days: string
  tags: string
}

interface TextFormState extends UploadFormState {
  text: string
}

const EMPTY_UPLOAD: UploadFormState = {
  title: '',
  doc_key: '',
  category_id: '',
  project_id: '',
  visibility: '',
  source_ref: '',
  description: '',
  expires_at: '',
  expiry_notice_days: '30',
  tags: '',
}

const EMPTY_TEXT: TextFormState = { ...EMPTY_UPLOAD, text: '' }

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，原因未知'
}

function dateText(value?: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 16)
}

function sizeText(bytes: number): string {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${numberText(bytes / 1024, 1)} KB`
  return `${numberText(bytes / 1024 / 1024, 1)} MB`
}

function flattenCategories(
  nodes: CategoryNode[],
  depth = 0,
): { node: CategoryNode; depth: number }[] {
  const result: { node: CategoryNode; depth: number }[] = []
  for (const node of nodes) {
    result.push({ node, depth })
    result.push(...flattenCategories(node.children, depth + 1))
  }
  return result
}

function findCategory(nodes: CategoryNode[], id: string): CategoryNode | null {
  for (const node of nodes) {
    if (node.id === id) return node
    const hit = findCategory(node.children, id)
    if (hit) return hit
  }
  return null
}

function Notice({ notice }: { notice: { ok: boolean; text: string } | null }) {
  if (!notice) return null
  return (
    <p className={`ke-notice${notice.ok ? ' ke-notice-ok' : ' ke-notice-bad'}`} role="status">
      {notice.text}
    </p>
  )
}

function ExpiryBadge({
  state,
  days,
}: {
  state: DocumentSummary['expiry_state']
  days: number | null
}) {
  if (state === 'none') return <span className="ke-muted">长期</span>
  if (state === 'expired') return <span className="os-badge" data-tone="danger">已过期</span>
  if (state === 'expiring') {
    return (
      <span className="os-badge" data-tone="warn">
        {days ?? 0} 天后到期
      </span>
    )
  }
  return <span className="os-badge" data-tone="ok">{days ?? '—'} 天后到期</span>
}

function CategoryTree({
  nodes,
  activeId,
  onPick,
  depth = 0,
}: {
  nodes: CategoryNode[]
  activeId: string | null
  onPick: (id: string | null) => void
  depth?: number
}) {
  return (
    <>
      {nodes.map((node) => (
        <div key={node.id}>
          <button
            type="button"
            className={`ke-cat${activeId === node.id ? ' ke-cat-active' : ''}`}
            style={{ paddingLeft: 10 + depth * 14 }}
            aria-pressed={activeId === node.id}
            onClick={() => onPick(node.id)}
            title={`${node.name} · ${node.visibility}`}
          >
            <span className="ke-cat-name">
              {node.name}
              {node.system && <em className="ke-cat-sys">系统</em>}
            </span>
            <span className="ke-cat-count">{node.document_count}</span>
          </button>
          {node.children.length > 0 && (
            <CategoryTree
              nodes={node.children}
              activeId={activeId}
              onPick={onPick}
              depth={depth + 1}
            />
          )}
        </div>
      ))}
    </>
  )
}

function DiffBlock({ diff }: { diff: DocumentDiff }) {
  return (
    <div className="ke-diff">
      <p className="ke-hint">
        v{diff.from_version} → v{diff.to_version} · 新增 {diff.added_lines} 行 / 删除{' '}
        {diff.removed_lines} 行
        {diff.truncated && '（差异过长，已截断）'}
      </p>
      {diff.changed ? (
        <pre className="ke-diff-body">
          {diff.unified.split('\n').map((line, index) => {
            const tone = line.startsWith('+')
              ? 'add'
              : line.startsWith('-')
                ? 'del'
                : line.startsWith('@@')
                  ? 'meta'
                  : ''
            return (
              <span key={`${index}-${line.slice(0, 8)}`} className={`ke-diff-line${tone ? ` ke-diff-${tone}` : ''}`}>
                {line || ' '}
                {'\n'}
              </span>
            )
          })}
        </pre>
      ) : (
        <p className="ke-hint">两个版本正文一致。</p>
      )}
    </div>
  )
}

export function KnowledgeEnterprise({ query = '' }: { query?: string }) {
  const [tab, setTab] = useState<Tab>('documents')
  const [status, setStatus] = useState<EnterpriseStatus | null>(null)
  const [categories, setCategories] = useState<CategoryNode[]>([])
  const [expiring, setExpiring] = useState<ExpiringResult | null>(null)
  const [overviewError, setOverviewError] = useState<string | null>(null)
  const [categoryId, setCategoryId] = useState<string | null>(null)
  const [includeSub, setIncludeSub] = useState(true)
  const [searchInput, setSearchInput] = useState(query)
  const [activeQuery, setActiveQuery] = useState(query)
  const [onlyExpiring, setOnlyExpiring] = useState(false)
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [listBusy, setListBusy] = useState(false)
  const [listError, setListError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)

  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [detailBusy, setDetailBusy] = useState(false)
  const [versionView, setVersionView] = useState<DocumentVersionDetail | null>(null)
  const [diffFrom, setDiffFrom] = useState('1')
  const [diffTo, setDiffTo] = useState('1')
  const [diff, setDiff] = useState<DocumentDiff | null>(null)
  const [expiryDraft, setExpiryDraft] = useState('')
  const [moveCategory, setMoveCategory] = useState('')

  const [showUpload, setShowUpload] = useState(false)
  const [showText, setShowText] = useState(false)
  const [uploadForm, setUploadForm] = useState<UploadFormState>(EMPTY_UPLOAD)
  const [textForm, setTextForm] = useState<TextFormState>(EMPTY_TEXT)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadPct, setUploadPct] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [newCategoryName, setNewCategoryName] = useState('')
  const [newCategoryVisibility, setNewCategoryVisibility] = useState('team')

  const listSeq = useRef(0)
  const overviewSeq = useRef(0)
  const detailSeq = useRef(0)
  const flatCategories = useMemo(() => flattenCategories(categories), [categories])

  const loadOverview = useCallback(async () => {
    const current = ++overviewSeq.current
    setOverviewError(null)
    try {
      const [nextStatus, nextCategories, nextExpiring] = await Promise.all([
        fetchStatus(),
        fetchCategories(),
        fetchExpiring(30),
      ])
      if (current !== overviewSeq.current) return
      setStatus(nextStatus)
      setCategories(nextCategories)
      setExpiring(nextExpiring)
    } catch (error) {
      if (current === overviewSeq.current) setOverviewError(errorText(error))
    }
  }, [])

  const loadList = useCallback(async () => {
    const current = ++listSeq.current
    setListBusy(true)
    setListError(null)
    try {
      if (onlyExpiring) {
        const data = await fetchExpiring(30)
        if (current !== listSeq.current) return
        setDocuments(data.documents)
        setHits(null)
        setSearchResult(null)
        setTotal(data.total)
        return
      }
      if (activeQuery.trim()) {
        const data = await searchDocuments({
          query: activeQuery.trim(),
          category_id: categoryId ?? '',
          include_subcategories: includeSub,
          top_k: 50,
        })
        if (current !== listSeq.current) return
        setHits(data.hits)
        setSearchResult(data)
        setDocuments([])
        setTotal(data.count)
        return
      }
      const data = await fetchDocuments({
        category_id: categoryId ?? '',
        include_subcategories: includeSub,
        limit: 20,
        offset,
      })
      if (current !== listSeq.current) return
      setDocuments(data.documents)
      setHits(null)
      setSearchResult(null)
      setTotal(data.total)
    } catch (error) {
      if (current === listSeq.current) setListError(errorText(error))
    } finally {
      if (current === listSeq.current) setListBusy(false)
    }
  }, [activeQuery, categoryId, includeSub, offset, onlyExpiring, tick])

  useEffect(() => {
    void loadOverview()
  }, [loadOverview])

  useEffect(() => {
    void loadList()
  }, [loadList])

  const openDocument = useCallback(async (documentId: string) => {
    const current = ++detailSeq.current
    setDetailBusy(true)
    setVersionView(null)
    setDiff(null)
    try {
      const data = await fetchDocument(documentId)
      if (current !== detailSeq.current) return
      setDetail(data)
      setExpiryDraft(data.expires_at ? data.expires_at.slice(0, 10) : '')
      setMoveCategory(data.category_id ?? '')
      const versions = data.versions.map((item) => item.version)
      const currentVersion = data.current_version
      setDiffTo(String(currentVersion))
      setDiffFrom(String(versions.find((value) => value < currentVersion) ?? currentVersion))
    } catch (error) {
      if (current === detailSeq.current) {
        setDetail(null)
        setNotice({ ok: false, text: errorText(error) })
      }
    } finally {
      if (current === detailSeq.current) setDetailBusy(false)
    }
  }, [])

  const refreshAll = useCallback(() => {
    setTick((value) => value + 1)
    void loadOverview()
  }, [loadOverview])

  const pickCategory = (id: string | null) => {
    setCategoryId(id)
    setOffset(0)
    setOnlyExpiring(false)
  }

  const runSearch = () => {
    setOffset(0)
    setOnlyExpiring(false)
    setActiveQuery(searchInput.trim())
    setTick((value) => value + 1)
  }

  const clearSearch = () => {
    setSearchInput('')
    setActiveQuery('')
    setOffset(0)
    setTick((value) => value + 1)
  }

  const submitCategory = async (event: FormEvent) => {
    event.preventDefault()
    const name = newCategoryName.trim()
    if (!name) {
      setNotice({ ok: false, text: '分类名不能为空' })
      return
    }
    setSubmitting(true)
    try {
      await createCategory({
        name,
        parent_id: categoryId ?? '',
        visibility: newCategoryVisibility,
      })
      setNewCategoryName('')
      setNotice({ ok: true, text: `已创建分类：${name}` })
      await loadOverview()
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    } finally {
      setSubmitting(false)
    }
  }

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault()
    if (!uploadFile) {
      setNotice({ ok: false, text: '请先选择要上传的文件' })
      return
    }
    if (uploadFile.size > UPLOAD_MAX_BYTES) {
      setNotice({
        ok: false,
        text: `文件超过 ${Math.round(UPLOAD_MAX_BYTES / 1024 / 1024)}MB 上限`,
      })
      return
    }
    setSubmitting(true)
    setUploadPct(0)
    setNotice(null)
    try {
      const result = await uploadDocument(
        uploadFile,
        {
          title: uploadForm.title,
          doc_key: uploadForm.doc_key,
          category_id: uploadForm.category_id || categoryId || '',
          project_id: uploadForm.project_id,
          visibility: uploadForm.visibility,
          source_ref: uploadForm.source_ref,
          description: uploadForm.description,
          expires_at: uploadForm.expires_at,
          expiry_notice_days: Number(uploadForm.expiry_notice_days) || 30,
          tags: uploadForm.tags,
        },
        setUploadPct,
      )
      setNotice({
        ok: true,
        text: result.deduplicated
          ? `内容与当前版本一致，未新增版本：${result.title}`
          : `已入库：${result.title}（v${result.current_version}，${result.chunk_count} 个分块）`,
      })
      setUploadFile(null)
      setShowUpload(false)
      setUploadForm(EMPTY_UPLOAD)
      refreshAll()
      await openDocument(result.id)
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    } finally {
      setSubmitting(false)
      setUploadPct(0)
    }
  }

  const submitText = async (event: FormEvent) => {
    event.preventDefault()
    if (!textForm.text.trim()) {
      setNotice({ ok: false, text: '正文不能为空' })
      return
    }
    setSubmitting(true)
    setNotice(null)
    try {
      const result = await ingestText({
        text: textForm.text,
        title: textForm.title,
        doc_key: textForm.doc_key,
        category_id: textForm.category_id || categoryId || '',
        project_id: textForm.project_id,
        visibility: textForm.visibility,
        source_ref: textForm.source_ref,
        description: textForm.description,
        expires_at: textForm.expires_at,
        expiry_notice_days: Number(textForm.expiry_notice_days) || 30,
        tags: textForm.tags ? textForm.tags.split(',').map((item) => item.trim()) : [],
      })
      setNotice({
        ok: true,
        text: result.deduplicated
          ? `内容与当前版本一致，未新增版本：${result.title}`
          : `已入库：${result.title}（v${result.current_version}）`,
      })
      setShowText(false)
      setTextForm(EMPTY_TEXT)
      refreshAll()
      await openDocument(result.id)
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    } finally {
      setSubmitting(false)
    }
  }

  const saveExpiry = async () => {
    if (!detail) return
    setSubmitting(true)
    try {
      await updateDocument(detail.id, {
        expires_at: expiryDraft || undefined,
        clear_expiry: !expiryDraft,
      })
      setNotice({ ok: true, text: '过期时间已更新' })
      await openDocument(detail.id)
      refreshAll()
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    } finally {
      setSubmitting(false)
    }
  }

  const saveCategory = async () => {
    if (!detail) return
    setSubmitting(true)
    try {
      await updateDocument(detail.id, { category_id: moveCategory })
      setNotice({ ok: true, text: '分类已更新' })
      await openDocument(detail.id)
      refreshAll()
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    } finally {
      setSubmitting(false)
    }
  }

  const removeDocument = async () => {
    if (!detail) return
    if (!window.confirm(`确认删除「${detail.title}」？删除后检索与列表立即不可见，历史版本仍保留在库内。`)) {
      return
    }
    setSubmitting(true)
    try {
      await deleteDocument(detail.id)
      setNotice({ ok: true, text: `已删除：${detail.title}` })
      setDetail(null)
      refreshAll()
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    } finally {
      setSubmitting(false)
    }
  }

  const runDiff = async () => {
    if (!detail) return
    const from = Number(diffFrom)
    const to = Number(diffTo)
    if (!Number.isFinite(from) || !Number.isFinite(to)) {
      setNotice({ ok: false, text: '请选择要对比的两个版本' })
      return
    }
    try {
      setDiff(await diffVersions(detail.id, from, to))
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    }
  }

  const viewVersion = async (version: number) => {
    if (!detail) return
    try {
      setVersionView(await fetchVersion(detail.id, version))
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    }
  }

  const saveDownload = async (documentId: string) => {
    try {
      await downloadDocument(documentId)
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    }
  }

  const removeCategory = async (category: CategoryNode) => {
    if (!window.confirm(`确认删除分类「${category.name}」？分类下必须没有文档与子分类。`)) return
    try {
      await deleteCategory(category.id)
      setNotice({ ok: true, text: `已删除分类：${category.name}` })
      if (categoryId === category.id) setCategoryId(null)
      await loadOverview()
    } catch (error) {
      setNotice({ ok: false, text: errorText(error) })
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / 20))
  const pageIndex = Math.floor(offset / 20)
  const activeCategory = categoryId ? findCategory(categories, categoryId) : null

  return (
    <div className="os-grid ke-page">
      <Card
        title="企业知识库"
        sub="分类归档、词法全文检索、版本对比、过期提醒；长期记忆独立库存储"
        span={12}
        action={
          <div className="ke-tabs" role="tablist" aria-label="企业知识库视图">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'documents'}
              className={`ke-tab${tab === 'documents' ? ' ke-tab-active' : ''}`}
              onClick={() => setTab('documents')}
            >
              文档库
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'memory'}
              className={`ke-tab${tab === 'memory' ? ' ke-tab-active' : ''}`}
              onClick={() => setTab('memory')}
            >
              长期记忆
            </button>
          </div>
        }
      >
        {overviewError ? (
          <Failure message={overviewError} onRetry={() => void loadOverview()} />
        ) : !status ? (
          <Loading rows={2} />
        ) : (
          <>
            <div className="ke-metrics">
              <span className="ke-metric">
                <strong>{status.documents}</strong> 篇文档
              </span>
              <span className="ke-metric">
                <strong>{status.chunks}</strong> 个分块
              </span>
              <span className="ke-metric">
                <strong>{status.categories}</strong> 个分类
              </span>
              {/* 到期计数是真 0：ExpiringResult.counts 两项为契约必填；expiring 为 null
                  时本块不渲染（上方 Loading / Failure 已接管），?? 0 只是兜底。 */}
              <span className="ke-metric">
                <strong>{expiring?.counts.expiring ?? 0}</strong> 即将过期
              </span>
              <span className="ke-metric">
                <strong>{expiring?.counts.expired ?? 0}</strong> 已过期
              </span>
            </div>
            <p className="ke-hint">
              检索方式：词法检索（FTS5 + 中文 bigram + BM25），按标题加权，不是语义相似度。
              支持 {status.supported_types.join(' / ')}；图片不做 OCR，只按文件名、尺寸与说明检索。
            </p>
            <Notice notice={notice} />
          </>
        )}
      </Card>

      {tab === 'documents' ? (
        <>
          <Card
            title="分类"
            sub={`${flatCategories.length} 个分类；可见性 private / team / enterprise`}
            span={4}
          >
            <nav className="ke-cats" aria-label="知识库分类">
              <button
                type="button"
                className={`ke-cat${categoryId === null ? ' ke-cat-active' : ''}`}
                aria-pressed={categoryId === null}
                onClick={() => pickCategory(null)}
              >
                <span className="ke-cat-name">全部文档</span>
              </button>
              <CategoryTree nodes={categories} activeId={categoryId} onPick={pickCategory} />
            </nav>
            <label className="ke-check">
              <input
                type="checkbox"
                checked={includeSub}
                onChange={(event) => {
                  setIncludeSub(event.target.checked)
                  setOffset(0)
                }}
              />
              包含子分类
            </label>
            {activeCategory && (
              <div className="ke-cat-meta">
                <p>
                  可见性：<strong>{activeCategory.visibility}</strong>
                  {activeCategory.created_by === '__system__' ? ' · 系统预置' : ''}
                </p>
                {activeCategory.description && <p>{activeCategory.description}</p>}
                <button type="button" className="os-link" onClick={() => void removeCategory(activeCategory)}>
                  删除该分类
                </button>
              </div>
            )}
            <form className="ke-mini-form" onSubmit={submitCategory}>
              <strong>新建子分类</strong>
              <input
                className="os-input-plain"
                value={newCategoryName}
                onChange={(event) => setNewCategoryName(event.target.value)}
                placeholder={activeCategory ? `在「${activeCategory.name}」下新建` : '新建顶级分类'}
                aria-label="新分类名称"
              />
              <select
                className="os-input-plain"
                value={newCategoryVisibility}
                onChange={(event) => setNewCategoryVisibility(event.target.value)}
                aria-label="新分类可见性"
              >
                <option value="private">private · 仅自己</option>
                <option value="team">team · 团队</option>
                <option value="enterprise">enterprise · 全员</option>
              </select>
              <button type="submit" className="os-btn" disabled={submitting}>
                创建
              </button>
            </form>
          </Card>

          <Card
            title={onlyExpiring ? '过期提醒' : activeQuery ? '检索结果' : activeCategory ? `${activeCategory.name} · 文档` : '全部文档'}
            sub={
              onlyExpiring
                ? '含已过期与 30 天内到期；按全部可见文档统计'
                : activeQuery
                  ? `${total} 条命中，按词法相关度排序`
                  : `共 ${total} 篇，点击任一行查看详情与版本`
            }
            span={8}
            action={
              <div className="ke-actions">
                <button type="button" className="os-btn" onClick={() => setShowUpload((value) => !value)}>
                  {showUpload ? '收起上传' : '上传文件'}
                </button>
                <button type="button" className="os-btn" onClick={() => setShowText((value) => !value)}>
                  {showText ? '收起文本' : '录入文本'}
                </button>
              </div>
            }
          >
            <div className="ke-toolbar">
              <input
                className="os-input-plain"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') runSearch()
                }}
                placeholder="输入关键词，检索可见文档片段"
                aria-label="知识库检索"
              />
              <button type="button" className="os-btn" onClick={runSearch} disabled={listBusy}>
                检索
              </button>
              {activeQuery && (
                <button type="button" className="os-link" onClick={clearSearch}>
                  清除检索
                </button>
              )}
              <label className="ke-check">
                <input
                  type="checkbox"
                  checked={onlyExpiring}
                  onChange={(event) => {
                    setOnlyExpiring(event.target.checked)
                    setOffset(0)
                    setActiveQuery('')
                    setSearchInput('')
                  }}
                />
                只看过期/即将过期
              </label>
            </div>

            {showUpload && (
              <form className="ke-form" onSubmit={submitUpload}>
                <div className="ke-form-grid">
                  <label>
                    文件
                    <input
                      type="file"
                      accept={status ? status.supported_types.map((item) => `.${item}`).join(',') : undefined}
                      onChange={(event: ChangeEvent<HTMLInputElement>) => {
                        setUploadFile(event.target.files?.[0] ?? null)
                        event.target.value = ''
                      }}
                    />
                  </label>
                  <label>
                    标题
                    <input
                      className="os-input-plain"
                      value={uploadForm.title}
                      onChange={(event) => setUploadForm({ ...uploadForm, title: event.target.value })}
                      placeholder="留空则用文件名"
                    />
                  </label>
                  <label>
                    分类
                    <select
                      className="os-input-plain"
                      value={uploadForm.category_id || categoryId || ''}
                      onChange={(event) => setUploadForm({ ...uploadForm, category_id: event.target.value })}
                    >
                      <option value="">未分类</option>
                      {flatCategories.map(({ node, depth }) => (
                        <option key={node.id} value={node.id}>
                          {'　'.repeat(depth)}
                          {node.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    文档标识（同名再传=新版本）
                    <input
                      className="os-input-plain"
                      value={uploadForm.doc_key}
                      onChange={(event) => setUploadForm({ ...uploadForm, doc_key: event.target.value })}
                      placeholder="留空则用文件名"
                    />
                  </label>
                  <label>
                    可见性
                    <select
                      className="os-input-plain"
                      value={uploadForm.visibility}
                      onChange={(event) => setUploadForm({ ...uploadForm, visibility: event.target.value })}
                    >
                      <option value="">跟随分类</option>
                      <option value="private">private · 仅自己</option>
                      <option value="team">team · 团队</option>
                      <option value="enterprise">enterprise · 全员</option>
                    </select>
                  </label>
                  <label>
                    项目标识
                    <input
                      className="os-input-plain"
                      value={uploadForm.project_id}
                      onChange={(event) => setUploadForm({ ...uploadForm, project_id: event.target.value })}
                      placeholder="可留空"
                    />
                  </label>
                  <label>
                    过期日
                    <input
                      type="date"
                      className="os-input-plain"
                      value={uploadForm.expires_at}
                      onChange={(event) => setUploadForm({ ...uploadForm, expires_at: event.target.value })}
                    />
                  </label>
                  <label>
                    提前提醒（天）
                    <input
                      type="number"
                      min={0}
                      max={3650}
                      className="os-input-plain"
                      value={uploadForm.expiry_notice_days}
                      onChange={(event) =>
                        setUploadForm({ ...uploadForm, expiry_notice_days: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    标签（逗号分隔）
                    <input
                      className="os-input-plain"
                      value={uploadForm.tags}
                      onChange={(event) => setUploadForm({ ...uploadForm, tags: event.target.value })}
                    />
                  </label>
                  <label>
                    来源说明
                    <input
                      className="os-input-plain"
                      value={uploadForm.source_ref}
                      onChange={(event) => setUploadForm({ ...uploadForm, source_ref: event.target.value })}
                      placeholder="例如：2026 制度汇编 / 客户提供"
                    />
                  </label>
                  <label className="ke-form-wide">
                    说明（图片会作为可检索文本）
                    <textarea
                      className="os-input-plain"
                      rows={2}
                      value={uploadForm.description}
                      onChange={(event) => setUploadForm({ ...uploadForm, description: event.target.value })}
                    />
                  </label>
                </div>
                <div className="ke-form-actions">
                  <button type="submit" className="os-btn" disabled={submitting}>
                    {submitting ? `上传中 ${uploadPct}%` : '上传并入库'}
                  </button>
                  {submitting && <progress value={uploadPct} max={100} aria-label="上传进度" />}
                </div>
              </form>
            )}

            {showText && (
              <form className="ke-form" onSubmit={submitText}>
                <div className="ke-form-grid">
                  <label>
                    标题
                    <input
                      className="os-input-plain"
                      value={textForm.title}
                      onChange={(event) => setTextForm({ ...textForm, title: event.target.value })}
                    />
                  </label>
                  <label>
                    分类
                    <select
                      className="os-input-plain"
                      value={textForm.category_id || categoryId || ''}
                      onChange={(event) => setTextForm({ ...textForm, category_id: event.target.value })}
                    >
                      <option value="">未分类</option>
                      {flatCategories.map(({ node, depth }) => (
                        <option key={node.id} value={node.id}>
                          {'　'.repeat(depth)}
                          {node.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    文档标识
                    <input
                      className="os-input-plain"
                      value={textForm.doc_key}
                      onChange={(event) => setTextForm({ ...textForm, doc_key: event.target.value })}
                    />
                  </label>
                  <label>
                    过期日
                    <input
                      type="date"
                      className="os-input-plain"
                      value={textForm.expires_at}
                      onChange={(event) => setTextForm({ ...textForm, expires_at: event.target.value })}
                    />
                  </label>
                  <label className="ke-form-wide">
                    正文
                    <textarea
                      className="os-input-plain"
                      rows={6}
                      value={textForm.text}
                      onChange={(event) => setTextForm({ ...textForm, text: event.target.value })}
                      placeholder="粘贴制度、流程、会议结论等纯文本"
                    />
                  </label>
                </div>
                <div className="ke-form-actions">
                  <button type="submit" className="os-btn" disabled={submitting}>
                    入库
                  </button>
                </div>
              </form>
            )}

            {listError ? (
              <Failure message={listError} onRetry={() => setTick((value) => value + 1)} />
            ) : listBusy && documents.length === 0 && !hits ? (
              <Loading rows={4} />
            ) : hits ? (
              hits.length === 0 ? (
                <Empty
                  title="没有命中"
                  hint={
                    searchResult?.retrieval.note ??
                    '换个关键词试试；词法检索不会把「意思相近但用词不同」的内容算作命中。'
                  }
                />
              ) : (
                <ul className="ke-hits">
                  {hits.map((hit) => (
                    <li key={hit.chunk_id}>
                      <button type="button" className="ke-hit" onClick={() => void openDocument(hit.document_id)}>
                        <div className="ke-hit-head">
                          <strong>{hit.title}</strong>
                          <span className="os-badge" data-tone="ok">
                            相关度 {numberText(hit.score, 2)}
                          </span>
                          <ExpiryBadge state={hit.expiry_state} days={hit.days_left} />
                        </div>
                        <p className="ke-hit-excerpt">{hit.excerpt}</p>
                        <p className="ke-hit-meta">
                          {hit.category_path.join(' / ') || '未分类'} · v{hit.version}
                          {hit.heading_path ? ` · ${hit.heading_path}` : ''}
                          {hit.page ? ` · 第 ${hit.page} 页` : ''}
                          {hit.source_ref ? ` · 来源：${hit.source_ref}` : ''}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )
            ) : documents.length === 0 ? (
              <Empty
                title="还没有文档"
                hint="上传 PDF / Word / Excel / 图片 / 文本，或直接录入一段纯文本。"
              />
            ) : (
              <ul className="ke-docs">
                {documents.map((doc) => (
                  <li key={doc.id}>
                    <button type="button" className="ke-doc" onClick={() => void openDocument(doc.id)}>
                      <div className="ke-doc-main">
                        <strong>{doc.title}</strong>
                        <span className="ke-doc-meta">
                          {doc.category_path.join(' / ') || '未分类'} · v{doc.current_version} ·{' '}
                          {doc.chunk_count} 个分块 · {sizeText(doc.size_bytes)}
                        </span>
                        {doc.match?.excerpt && <span className="ke-doc-excerpt">{doc.match.excerpt}</span>}
                        {doc.source_ref && <span className="ke-doc-meta">来源：{doc.source_ref}</span>}
                      </div>
                      <div className="ke-doc-side">
                        <ExpiryBadge state={doc.expiry_state} days={doc.days_left} />
                        <span className="ke-muted">{dateText(doc.updated_at)}</span>
                        <span className="ke-muted">
                          {doc.visibility} · {doc.owner_account_id}
                        </span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {!hits && !onlyExpiring && total > 20 && (
              <div className="ke-pager">
                <button
                  type="button"
                  className="os-btn"
                  disabled={pageIndex === 0 || listBusy}
                  onClick={() => setOffset(Math.max(0, offset - 20))}
                >
                  上一页
                </button>
                <span className="ke-muted">
                  第 {pageIndex + 1} / {pageCount} 页
                </span>
                <button
                  type="button"
                  className="os-btn"
                  disabled={pageIndex + 1 >= pageCount || listBusy}
                  onClick={() => setOffset(offset + 20)}
                >
                  下一页
                </button>
              </div>
            )}
          </Card>

          {(detail || detailBusy) && (
            <Card
              title={detail ? detail.title : '文档详情'}
              sub={detail ? `文档标识 ${detail.doc_key} · 上传者 ${detail.owner_account_id}` : '正在读取…'}
              span={12}
              action={
                detail ? (
                  <div className="ke-actions">
                    {detail.origin_filename && (
                      <button type="button" className="os-btn" onClick={() => void saveDownload(detail.id)}>
                        下载原件
                      </button>
                    )}
                    <button type="button" className="os-btn" onClick={() => setDetail(null)}>
                      关闭
                    </button>
                  </div>
                ) : undefined
              }
            >
              {detailBusy && !detail ? (
                <Loading rows={3} />
              ) : detail ? (
                <>
                  <div className="ke-detail-grid">
                    <p>
                      <span className="ke-label">分类</span>
                      {detail.category_path.join(' / ') || '未分类'}
                    </p>
                    <p>
                      <span className="ke-label">可见性</span>
                      {detail.visibility}
                    </p>
                    <p>
                      <span className="ke-label">来源</span>
                      {detail.source_type}
                      {detail.source_ref ? ` · ${detail.source_ref}` : ''}
                    </p>
                    <p>
                      <span className="ke-label">原件</span>
                      {detail.origin_filename || '纯文本入库'}
                    </p>
                    <p>
                      <span className="ke-label">大小</span>
                      {sizeText(detail.size_bytes)}
                    </p>
                    <p>
                      <span className="ke-label">过期</span>
                      <ExpiryBadge state={detail.expiry_state} days={detail.days_left} />
                      {detail.expires_at ? ` · ${dateText(detail.expires_at)}` : ''}
                    </p>
                    <p>
                      <span className="ke-label">项目</span>
                      {detail.project_id || '—'}
                    </p>
                    <p>
                      <span className="ke-label">标签</span>
                      {detail.tags.length ? detail.tags.join(' / ') : '—'}
                    </p>
                    <p>
                      <span className="ke-label">更新时间</span>
                      {dateText(detail.updated_at)}
                    </p>
                  </div>
                  {detail.text_source === 'image_metadata' && (
                    <p className="ke-hint">
                      图片未做 OCR：当前可检索内容只有文件名、尺寸与人工说明。
                    </p>
                  )}

                  {detail.can_write && (
                    <div className="ke-edit-row">
                      <label>
                        过期日
                        <input
                          type="date"
                          className="os-input-plain"
                          value={expiryDraft}
                          onChange={(event) => setExpiryDraft(event.target.value)}
                        />
                      </label>
                      <button type="button" className="os-btn" onClick={() => void saveExpiry()} disabled={submitting}>
                        保存过期
                      </button>
                      <label>
                        移动到分类
                        <select
                          className="os-input-plain"
                          value={moveCategory}
                          onChange={(event) => setMoveCategory(event.target.value)}
                        >
                          <option value="">未分类</option>
                          {flatCategories.map(({ node, depth }) => (
                            <option key={node.id} value={node.id}>
                              {'　'.repeat(depth)}
                              {node.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button type="button" className="os-btn" onClick={() => void saveCategory()} disabled={submitting}>
                        保存分类
                      </button>
                      <button type="button" className="os-link ke-danger" onClick={() => void removeDocument()}>
                        删除文档
                      </button>
                    </div>
                  )}

                  <h3 className="ke-subtitle">版本（{detail.versions.length}）</h3>
                  <ul className="ke-versions">
                    {detail.versions.map((version) => (
                      <li key={version.id}>
                        <span>
                          v{version.version}
                          {version.version === detail.current_version ? ' · 当前' : ''} ·{' '}
                          {version.chunk_count} 个分块 · {sizeText(version.size_bytes)}
                        </span>
                        <span className="ke-muted">{dateText(version.created_at)}</span>
                        <button type="button" className="os-link" onClick={() => void viewVersion(version.version)}>
                          查看正文
                        </button>
                      </li>
                    ))}
                  </ul>

                  <div className="ke-diff-controls">
                    <label>
                      对比
                      <select
                        className="os-input-plain"
                        value={diffFrom}
                        onChange={(event) => setDiffFrom(event.target.value)}
                      >
                        {detail.versions.map((version) => (
                          <option key={version.id} value={version.version}>
                            v{version.version}
                          </option>
                        ))}
                      </select>
                    </label>
                    <span>→</span>
                    <select
                      className="os-input-plain"
                      value={diffTo}
                      onChange={(event) => setDiffTo(event.target.value)}
                    >
                      {detail.versions.map((version) => (
                        <option key={version.id} value={version.version}>
                          v{version.version}
                        </option>
                      ))}
                    </select>
                    <button type="button" className="os-btn" onClick={() => void runDiff()}>
                      对比版本
                    </button>
                  </div>
                  {diff && <DiffBlock diff={diff} />}
                  {versionView && (
                    <div className="ke-version-text">
                      <div className="ke-version-head">
                        <strong>v{versionView.version} 正文</strong>
                        <button type="button" className="os-link" onClick={() => setVersionView(null)}>
                          收起
                        </button>
                      </div>
                      <pre>{versionView.text || '（空）'}</pre>
                      {versionView.text_truncated && (
                        <p className="ke-hint">正文过长，这里只显示前 20000 字。</p>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <Empty title="文档不可用" hint="文档可能已被删除，或当前账号无权访问。" />
              )}
            </Card>
          )}
        </>
      ) : (
        <MemoryPanel span={12} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 长期记忆
// ---------------------------------------------------------------------------

interface MemoryFormState {
  title: string
  content: string
  project_id: string
  employee_id: string
  scope: string
  source: string
}

const EMPTY_MEMORY: MemoryFormState = {
  title: '',
  content: '',
  project_id: '',
  employee_id: '',
  scope: 'personal',
  source: '',
}

function MemoryPanel({ span }: { span: 12 }) {
  const [views, setViews] = useState<MemoryViews | null>(null)
  const [page, setPage] = useState<MemoryPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [filters, setFilters] = useState({ project_id: '', employee_id: '', scope: '', q: '' })
  const [form, setForm] = useState<MemoryFormState>(EMPTY_MEMORY)
  const [detail, setDetail] = useState<MemoryDetail | null>(null)
  const [editing, setEditing] = useState<{ id: string; content: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [tick, setTick] = useState(0)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [nextViews, nextPage] = await Promise.all([
        fetchMemoryViews(),
        fetchMemories({
          project_id: filters.project_id,
          employee_id: filters.employee_id,
          scope: filters.scope,
          q: filters.q,
          limit: 50,
        }),
      ])
      setViews(nextViews)
      setPage(nextPage)
    } catch (err) {
      setError(errorText(err))
    }
  }, [filters, tick])

  useEffect(() => {
    void load()
  }, [load])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!form.content.trim()) {
      setNotice({ ok: false, text: '记忆内容不能为空' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      await createMemory({
        content: form.content,
        title: form.title,
        project_id: form.project_id,
        employee_id: form.employee_id,
        scope: form.scope,
        source: form.source,
      })
      setNotice({ ok: true, text: '记忆已写入独立记忆库' })
      setForm(EMPTY_MEMORY)
      setTick((value) => value + 1)
    } catch (err) {
      setNotice({ ok: false, text: errorText(err) })
    } finally {
      setBusy(false)
    }
  }

  const openDetail = async (memory: MemoryEntry) => {
    try {
      setDetail(await fetchMemory(memory.id))
    } catch (err) {
      setNotice({ ok: false, text: errorText(err) })
    }
  }

  const saveEdit = async () => {
    if (!editing) return
    setBusy(true)
    try {
      await updateMemory(editing.id, { content: editing.content })
      setNotice({ ok: true, text: '记忆已更新，旧版本留在修订历史里' })
      setEditing(null)
      setTick((value) => value + 1)
      if (detail?.id === editing.id) setDetail(await fetchMemory(editing.id))
    } catch (err) {
      setNotice({ ok: false, text: errorText(err) })
    } finally {
      setBusy(false)
    }
  }

  const remove = async (memory: MemoryEntry) => {
    if (!window.confirm(`确认删除记忆「${memory.title || memory.id}」？删除后列表与视图立即不可见。`)) {
      return
    }
    try {
      await deleteMemory(memory.id)
      setNotice({ ok: true, text: '记忆已删除' })
      setDetail(null)
      setTick((value) => value + 1)
    } catch (err) {
      setNotice({ ok: false, text: errorText(err) })
    }
  }

  return (
    <Card
      title="长期记忆"
      sub="独立数据库存储；按项目、按员工隔离视图。个人记忆仅本人可见，写入只属于记录者"
      span={span}
    >
      {error ? (
        <Failure message={error} onRetry={() => setTick((value) => value + 1)} />
      ) : !page || !views ? (
        <Loading rows={3} />
      ) : (
        <>
          <div className="ke-metrics">
            <span className="ke-metric">
              <strong>{views.total}</strong> 条可见记忆
            </span>
            {/* 各 scope 计数是真 0：后端 memory.py 固定输出全部 MEMORY_SCOPES，
                缺失 scope 按 0 补齐；?? 0 只防旧版本响应缺键。 */}
            <span className="ke-metric">
              <strong>{views.scopes.project ?? 0}</strong> 条项目记忆
            </span>
            <span className="ke-metric">
              <strong>{views.scopes.employee ?? 0}</strong> 条员工记忆
            </span>
            <span className="ke-metric">
              <strong>{views.scopes.personal ?? 0}</strong> 条个人记忆
            </span>
          </div>
          <p className="ke-hint">
            存储隔离：{views.isolation}。知识库与记忆库是两个 SQLite 文件，记忆接口没有文档写入口。
          </p>
          <div className="ke-memory-chips">
            {views.projects.map((item) => (
              <button
                key={item.project_id}
                type="button"
                className="ke-chip"
                onClick={() => setFilters({ ...filters, project_id: item.project_id, employee_id: '' })}
              >
                项目 {item.project_id} · {item.count}
              </button>
            ))}
            {views.employees.map((item) => (
              <button
                key={item.employee_id}
                type="button"
                className="ke-chip"
                onClick={() => setFilters({ ...filters, employee_id: item.employee_id, project_id: '' })}
              >
                员工 {item.employee_id} · {item.count}
              </button>
            ))}
          </div>
          <Notice notice={notice} />

          <form className="ke-form" onSubmit={submit}>
            <div className="ke-form-grid">
              <label>
                标题
                <input
                  className="os-input-plain"
                  value={form.title}
                  onChange={(event) => setForm({ ...form, title: event.target.value })}
                  placeholder="可留空，自动取首行"
                />
              </label>
              <label>
                范围
                <select
                  className="os-input-plain"
                  value={form.scope}
                  onChange={(event) => setForm({ ...form, scope: event.target.value })}
                >
                  <option value="personal">personal · 仅自己</option>
                  <option value="project">project · 按项目</option>
                  <option value="employee">employee · 按员工</option>
                  <option value="enterprise">enterprise · 全员</option>
                </select>
              </label>
              <label>
                项目
                <input
                  className="os-input-plain"
                  value={form.project_id}
                  onChange={(event) => setForm({ ...form, project_id: event.target.value })}
                  placeholder={form.scope === 'project' ? 'project 范围必填' : '可留空'}
                />
              </label>
              <label>
                员工
                <input
                  className="os-input-plain"
                  value={form.employee_id}
                  onChange={(event) => setForm({ ...form, employee_id: event.target.value })}
                  placeholder={form.scope === 'employee' ? 'employee 范围必填' : '可留空'}
                />
              </label>
              <label>
                来源
                <input
                  className="os-input-plain"
                  value={form.source}
                  onChange={(event) => setForm({ ...form, source: event.target.value })}
                  placeholder="例如：会话沉淀 / 复盘会"
                />
              </label>
              <label className="ke-form-wide">
                内容
                <textarea
                  className="os-input-plain"
                  rows={3}
                  value={form.content}
                  onChange={(event) => setForm({ ...form, content: event.target.value })}
                />
              </label>
            </div>
            <div className="ke-form-actions">
              <button type="submit" className="os-btn" disabled={busy}>
                写入记忆
              </button>
            </div>
          </form>

          <div className="ke-toolbar">
            <input
              className="os-input-plain"
              value={filters.project_id}
              onChange={(event) => setFilters({ ...filters, project_id: event.target.value })}
              placeholder="按项目过滤"
              aria-label="按项目过滤记忆"
            />
            <input
              className="os-input-plain"
              value={filters.employee_id}
              onChange={(event) => setFilters({ ...filters, employee_id: event.target.value })}
              placeholder="按员工过滤"
              aria-label="按员工过滤记忆"
            />
            <select
              className="os-input-plain"
              value={filters.scope}
              onChange={(event) => setFilters({ ...filters, scope: event.target.value })}
              aria-label="按范围过滤记忆"
            >
              <option value="">全部范围</option>
              <option value="personal">personal</option>
              <option value="project">project</option>
              <option value="employee">employee</option>
              <option value="enterprise">enterprise</option>
            </select>
            <input
              className="os-input-plain"
              value={filters.q}
              onChange={(event) => setFilters({ ...filters, q: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === 'Enter') setTick((value) => value + 1)
              }}
              placeholder="搜索标题/内容"
            />
            <button type="button" className="os-btn" onClick={() => setTick((value) => value + 1)}>
              应用
            </button>
          </div>

          {page.memories.length === 0 ? (
            <Empty title="还没有记忆" hint="把项目结论、客户偏好、员工经验写进来；它们不会进入正式业务库。" />
          ) : (
            <ul className="ke-docs">
              {page.memories.map((memory) => (
                <li key={memory.id}>
                  <button type="button" className="ke-doc" onClick={() => void openDetail(memory)}>
                    <div className="ke-doc-main">
                      <strong>{memory.title || '(无标题)'}</strong>
                      <span className="ke-doc-excerpt">{memory.content.slice(0, 160)}</span>
                      <span className="ke-doc-meta">
                        {memory.scope} · {memory.project_id || '无项目'} ·{' '}
                        {memory.employee_id || '无员工'} · 修订 {memory.revision} ·{' '}
                        {memory.source || '来源未标注'}
                      </span>
                    </div>
                    <div className="ke-doc-side">
                      <span className="os-badge" data-tone="ok">{memory.scope}</span>
                      <span className="ke-muted">{dateText(memory.updated_at)}</span>
                      <span className="ke-muted">{memory.owner_account_id}</span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {detail && (
            <div className="ke-memory-detail">
              <div className="ke-version-head">
                <strong>{detail.title || '(无标题)'}</strong>
                <span className="ke-muted">
                  {detail.scope} · 修订 {detail.revision}
                </span>
                <button type="button" className="os-link" onClick={() => setDetail(null)}>
                  关闭
                </button>
              </div>
              {editing?.id === detail.id ? (
                <>
                  <textarea
                    className="os-input-plain"
                    rows={4}
                    value={editing.content}
                    onChange={(event) => setEditing({ id: editing.id, content: event.target.value })}
                  />
                  <div className="ke-form-actions">
                    <button type="button" className="os-btn" onClick={() => void saveEdit()} disabled={busy}>
                      保存
                    </button>
                    <button type="button" className="os-link" onClick={() => setEditing(null)}>
                      取消
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <p className="ke-memory-content">{detail.content}</p>
                  <div className="ke-actions">
                    {detail.can_write && (
                      <>
                        <button
                          type="button"
                          className="os-btn"
                          onClick={() => setEditing({ id: detail.id, content: detail.content })}
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          className="os-link ke-danger"
                          onClick={() => void remove(detail)}
                        >
                          删除
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
              <details className="ke-revisions">
                <summary>修订历史（{detail.revisions.length}）</summary>
                <ul>
                  {detail.revisions.map((revision) => (
                    <li key={revision.id}>
                      <strong>r{revision.revision}</strong>
                      <span className="ke-muted">{dateText(revision.created_at)}</span>
                      <p>{revision.content}</p>
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          )}
        </>
      )}
    </Card>
  )
}
