/**
 * 产物预览抽屉 —— 点开一个文件，就地看内容，不跳页、不开新窗口。
 *
 * 边界（与后端一致，不粉饰）
 * ------------------------
 * * 只读：后端只有 `GET /v1/files/content`，没有写接口、也**没有二进制下载接口**。
 * * 文本（UTF-8）就地渲染；`.md` 走 Markdown，其它按等宽文本。
 * * 二进制（415）/ 过大（413）/ 越界（400）/ 不存在（404）原样展示后端原因，
 *   并给出工作区路径，请到文件系统打开 —— 不伪造「下载成功」。
 * * 关闭只收起抽屉，页面位置不变；焦点交还给打开它的按钮（由调用方负责）。
 */

import { useEffect, useRef, useState } from 'react'
import { readFile, type FileContentResponse } from '../lib/operator'
import { Markdown } from './Markdown'

function fileName(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.length > 0 ? parts[parts.length - 1] : path
}

function isMarkdown(path: string): boolean {
  return /\.(md|markdown|mdx)$/i.test(path)
}

/** 把已经读回来的文本按原文件名下载（二进制没有下载接口，不会走到这里）。 */
function downloadText(path: string, text: string) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName(path)
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export function ArtifactPreview({ path, onClose }: { path: string; onClose: () => void }) {
  const [content, setContent] = useState<FileContentResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)
  const closeRef = useRef<HTMLButtonElement | null>(null)
  // 调用方的 onClose 常是内联函数；放进 ref，避免每次父组件渲染都重设焦点 / 锁滚动。
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    setContent(null)
    setCopied(false)
    void (async () => {
      try {
        const data = await readFile(path)
        if (alive) setContent(data)
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : '读取失败')
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => {
      alive = false
    }
  }, [path])

  // 打开即把焦点放进抽屉；Esc 关闭；背后页面不跟着滚。
  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current()
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
    }
  }, [path])

  const copyPath = () => {
    void (async () => {
      try {
        await navigator.clipboard.writeText(path)
        setCopied(true)
      } catch {
        setCopied(false)
      }
    })()
  }

  return (
    <>
      <div className="os-drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside
        className="os-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`产物预览 · ${fileName(path)}`}
      >
        <header className="os-drawer-head">
          <div>
            <div className="os-drawer-title">产物预览 · {fileName(path)}</div>
            <div className="os-drawer-sub">只读；按 Esc 或「关闭」回到刚才的位置</div>
          </div>
          <div className="os-drawer-actions">
            {content && !error && (
              <button className="os-btn" onClick={() => downloadText(path, content.content)}>
                下载
              </button>
            )}
            <button
              className="os-btn"
              ref={closeRef}
              onClick={onClose}
              aria-label="关闭产物预览"
            >
              关闭
            </button>
          </div>
        </header>

        <div className="os-drawer-body">
          <div className="os-file-meta">
            <span>
              路径 <code>{path}</code>
            </span>
            <button className="os-link" onClick={copyPath}>
              {copied ? '已复制' : '复制路径'}
            </button>
            {content && <span>大小 {content.size} 字节</span>}
            {content && <span>编码 {content.encoding}</span>}
          </div>

          {loading && <p>读取中…</p>}

          {!loading && error && (
            <div>
              <p className="os-error" role="alert">
                {error}
              </p>
              <p className="os-hint">
                这个文件不能在界面里预览（不是 UTF-8 文本，或超过读取上限）。当前本地服务只开放
                文本读取，没有文件下载接口，所以这里不提供下载；请按上面的路径在文件系统中打开。
              </p>
            </div>
          )}

          {!loading && content && (
            <>
              {isMarkdown(path) ? (
                <Markdown text={content.content} />
              ) : (
                <pre className="os-pre">{content.content}</pre>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  )
}
