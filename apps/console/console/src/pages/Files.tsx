/**
 * 工作区文件 —— 真实产物的只读可观测面。
 *
 * 为什么这个文件存在
 * ------------------
 * `lib/operator.ts` 早就实现了 `/v1/files`、`/v1/files/content` 两个客户端函数，
 * 对应后端 `src/gateway/files.py` 的严格只读面，但**没有任何页面调用过它们**。
 * AI 员工通过 `file_write` 工具把产物写进了工作区，却没有一个人类能浏览或读取
 * —— 一个"AI 替你干活但你看不到它干了什么"的系统谈不上可信、可审计、可运维。
 * 这个页面把那两个函数真正渲染出来。
 *
 * 诚实优先
 * --------
 * * 这是一个**只读**视图：没有任何写 / 删 / 上传入口。写仍由执行内核的
 *   `file_write` 工具负责，经执行围栏约束；这里只把已存在的产物摊开给人看。
 * * `count=0` 就是真空目录，如实显示"此目录为空"，不伪装。
 * * 后端对越界（400）/ 不存在（404）/ 过大（413）/ 二进制（415）返回**真实状态码
 *   与原因**，前端原样展示，不退化成"暂无数据"或"空内容"。
 * * 列出的条目来自后端实时 enum 工作区真实文件系统；没有缓存、没有模拟。
 */

import { useCallback, useMemo, useState } from 'react'
import { useApi } from '../lib/api'
import {
  readFile,
  type FileContentResponse,
  type FileEntry,
  type FileListResponse,
} from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

/** 路径面包屑：把 `a/b/c` 拆成 [{label, path}]，根单独成一项。 */
function breadcrumbs(path: string): { label: string; path: string }[] {
  const segments = path.split('/').filter(Boolean)
  const trail: { label: string; path: string }[] = [{ label: '工作区根', path: '' }]
  let acc = ''
  for (const segment of segments) {
    acc = acc ? `${acc}/${segment}` : segment
    trail.push({ label: segment, path: acc })
  }
  return trail
}

function formatMtime(mtime: number): string {
  if (!Number.isFinite(mtime) || mtime <= 0) return '—'
  try {
    return new Date(mtime * 1000).toLocaleString()
  } catch {
    return String(mtime)
  }
}

function formatSize(size: number | null): string {
  if (size === null) return '—'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

export function Files({ query }: { query: string }) {
  // 当前浏览目录（工作区相对路径；空字符串 = 根）。
  const [currentPath, setCurrentPath] = useState('')
  // 当前打开的文件（相对路径）；null = 没打开任何文件。
  const [selected, setSelected] = useState<string | null>(null)
  const [content, setContent] = useState<FileContentResponse | null>(null)
  const [contentError, setContentError] = useState<string | null>(null)
  const [loadingContent, setLoadingContent] = useState(false)

  // 目录列举：随 currentPath 变化自动重取；未登录时挂起（path=null）。
  const listingPath = `/v1/files?path=${encodeURIComponent(currentPath)}`
  const listing = useApi<FileListResponse>(listingPath, 30_000)

  const openEntry = useCallback(
    (entry: FileEntry) => {
      if (entry.type === 'dir') {
        // 进入目录：清空已打开的文件，面包屑跟着变。
        setSelected(null)
        setContent(null)
        setContentError(null)
        setCurrentPath(entry.rel_path)
      } else {
        // 打开文件：触发真实读取，loading 态独立于目录列举。
        setSelected(entry.rel_path)
        setContent(null)
        setContentError(null)
        setLoadingContent(true)
        void (async () => {
          try {
            setContent(await readFile(entry.rel_path))
          } catch (err) {
            setContent(null)
            setContentError(err instanceof Error ? err.message : '读取失败')
          } finally {
            setLoadingContent(false)
          }
        })()
      }
    },
    [],
  )

  const navigateTo = useCallback((path: string) => {
    setCurrentPath(path)
    setSelected(null)
    setContent(null)
    setContentError(null)
  }, [])

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    const entries = listing.data?.entries ?? []
    if (!need) return entries
    return entries.filter((entry) =>
      [entry.name, entry.rel_path, entry.type].some((value) => value.toLowerCase().includes(need)),
    )
  }, [listing.data, need])

  const crumbs = breadcrumbs(currentPath)

  return (
    <div className="os-grid">
      <Card
        title={selected ? `文件内容 · ${selected}` : `目录浏览 · ${currentPath || '工作区根'}`}
        sub="只读面 —— 写仍由 AI 员工的 file_write 工具经执行围栏完成"
        span={selected ? 8 : 12}
        action={
          selected ? (
            <button className="os-btn" onClick={() => setSelected(null)}>
              返回目录
            </button>
          ) : undefined
        }
      >
        {/* 面包屑：点任意一级都能跳回去 */}
        <div className="os-breadcrumbs">
          {crumbs.map((crumb, index) => (
            <span key={crumb.path || 'root'}>
              {index > 0 && <span className="os-breadcrumb-sep">/</span>}
              <button className="os-link" onClick={() => navigateTo(crumb.path)}>
                {crumb.label}
              </button>
            </span>
          ))}
        </div>

        {selected ? (
          // 文件内容区
          contentError ? (
            <p className="os-error">无法读取：{contentError}</p>
          ) : loadingContent || (content === null && !contentError) ? (
            <p>读取中…</p>
          ) : content ? (
            <div className="os-file-meta">
              <span>大小 {formatSize(content.size)}</span>
              <span>编码 {content.encoding}</span>
            </div>
          ) : null
        ) : (
          // 目录列举区
          <Guard
            loading={listing.loading}
            error={listing.error}
            data={listing.data}
            isEmpty={(data) => data.count === 0}
            emptyTitle="此目录为空"
            emptyHint="AI 员工尚未在此处写出任何文件；目录真实存在，只是没有条目。"
            onRetry={listing.reload}
          >
            {(data) => (
              <>
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>名称</th>
                        <th>类型</th>
                        <th>大小</th>
                        <th>修改时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((entry) => (
                        <tr
                          key={entry.rel_path}
                          className="os-clickable"
                          onClick={() => openEntry(entry)}
                        >
                          <td>
                            <span className={`os-dot os-tone-${entry.type === 'dir' ? 'accent' : 'muted'}`} />
                            {entry.name}
                          </td>
                          <td>{entry.type === 'dir' ? '目录' : '文件'}</td>
                          <td>{formatSize(entry.size)}</td>
                          <td>{formatMtime(entry.mtime)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {shown.length !== data.entries.length && (
                  <p className="os-hint">
                    搜索「{query}」过滤后显示 {shown.length} / {data.entries.length} 项。
                  </p>
                )}
              </>
            )}
          </Guard>
        )}
      </Card>

      {selected && content && !contentError && (
        <Card title="内容预览" sub="UTF-8 文本；二进制或超大文件由后端拒绝并说明原因" span={4}>
          <pre className="os-code-block">{content.content}</pre>
        </Card>
      )}

      {!selected && (
        <Card title="说明" sub="这个视图能证明什么" span={12}>
          <ul className="os-list">
            <li>列出的每一项都来自工作区真实文件系统，由后端实时 enum，无缓存、无模拟。</li>
            <li>点目录进入、点文件读取；右侧预览区只在真实读回文本后出现。</li>
            <li>
              越界路径（如 <code>../</code> 逃逸）会被后端 400 拒绝；文件不存在 404；过大 413；
              二进制 415 —— 这些都会如实显示，不会伪装成空内容。
            </li>
            <li>整个面只读：没有任何写入口，写仍由 AI 员工的 file_write 工具经执行围栏完成。</li>
          </ul>
          {listing.data && listing.data.count === 0 && (
            <Empty
              title="工作区尚未有产物"
              hint="让一个 AI 员工完成一次会写出文件的任务，再回到这里就能看到了。"
            />
          )}
        </Card>
      )}
    </div>
  )
}
