/**
 * 一键诊断包的下载 / 另存为。
 *
 * 为什么不用 ``apiFetch``：它按 JSON 解析响应，而诊断包是 zip 二进制。
 * 这里只复用令牌头与 ``readError``（失败文案与全站一致）。
 *
 * "保存到用户选的位置"分两条路：
 * * 支持文件选择器的浏览器（Chrome / Edge）→ 让用户选目录，写到选中的位置；
 * * 其余浏览器 → 触发下载，落到浏览器的下载目录。
 * 两条路都要把**文件名**回给界面，用户才知道去哪里找。
 */

import { tokenHeaders } from './auth'
import { readError } from './errorMessage'

export interface DiagnosticsSaveResult {
  filename: string
  /** ``chosen`` = 用户刚选的位置；``download`` = 浏览器下载目录。 */
  location: 'chosen' | 'download'
}

interface WritableLike {
  write: (data: Blob) => Promise<void>
  close: () => Promise<void>
}

interface FileHandleLike {
  name: string
  createWritable: () => Promise<WritableLike>
}

type SaveFilePicker = (options: {
  suggestedName?: string
  types?: Array<{ description: string; accept: Record<string, string[]> }>
}) => Promise<FileHandleLike>

function picker(): SaveFilePicker | null {
  const candidate = (window as unknown as { showSaveFilePicker?: SaveFilePicker })
    .showSaveFilePicker
  return typeof candidate === 'function' ? candidate : null
}

function filenameFrom(response: Response): string {
  const header = response.headers.get('X-LiuHao-Diagnostics-Filename')
  if (header) return header
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename="?([^";]+)"?/i.exec(disposition)
  if (match?.[1]) return match[1]
  return `liuhao-diagnostics-${Date.now()}.zip`
}

/**
 * 导出诊断包。用户主动取消另存为时返回 ``null``（不是失败）。
 */
export async function exportDiagnosticsBundle(): Promise<DiagnosticsSaveResult | null> {
  let response: Response
  try {
    response = await fetch('/v1/diagnostics/bundle', {
      headers: tokenHeaders(),
      cache: 'no-store',
    })
  } catch {
    throw new Error(
      '无法连接到鎏灏服务。请确认服务已经启动、网络连接正常，然后重试。',
    )
  }

  if (!response.ok) {
    const failure = await readError(response)
    throw new Error(failure.message)
  }

  const blob = await response.blob()
  const filename = filenameFrom(response)

  const saveAs = picker()
  if (saveAs) {
    try {
      const handle = await saveAs({
        suggestedName: filename,
        types: [{ description: '诊断包压缩文件', accept: { 'application/zip': ['.zip'] } }],
      })
      const writable = await handle.createWritable()
      await writable.write(blob)
      await writable.close()
      return { filename: handle.name || filename, location: 'chosen' }
    } catch (err) {
      // 用户在系统对话框里点了取消：不生成文件，也不报错。
      if (err instanceof DOMException && err.name === 'AbortError') return null
      throw err
    }
  }

  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => {
    // jsdom / 老浏览器可能没有 revokeObjectURL；清理失败不影响"已开始下载"。
    if (typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url)
  }, 0)
  return { filename, location: 'download' }
}
