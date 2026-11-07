/**
 * 一键诊断包入口。
 *
 * 放两个东西：一句明确的"不含密钥"说明，和一个"导出后告诉我文件在哪"的按钮。
 * 说明必须在点击之前就能看到 —— 用户要先知道发出去的东西安全，才愿意导出。
 */

import { useState } from 'react'
import { exportDiagnosticsBundle } from '../lib/diagnostics'

type Tone = 'ok' | 'danger' | 'muted'

interface ResultState {
  tone: Tone
  message: string
}

export function DiagnosticsExport() {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ResultState | null>(null)

  async function onExport() {
    setBusy(true)
    setResult(null)
    try {
      const saved = await exportDiagnosticsBundle()
      if (!saved) {
        setResult({ tone: 'muted', message: '已取消导出，没有生成文件。' })
        return
      }
      setResult({
        tone: 'ok',
        message:
          saved.location === 'chosen'
            ? `已保存：${saved.filename}（在你刚才选择的位置）`
            : `已开始下载：${saved.filename}。请到浏览器的下载位置查看。`,
      })
    } catch (err) {
      setResult({
        tone: 'danger',
        message: err instanceof Error && err.message
          ? `${err.message}\n可以重试一次；如果仍然失败，请改为手动提供日志。`
          : '导出没有完成。请重试一次；如果仍然失败，请改为手动提供日志。',
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="os-list">
      <div className="os-row">
        <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
        <div className="os-row-main">
          <div className="os-row-title">一键诊断包</div>
          <div className="os-row-sub">
            包含版本、系统信息、健康检查与最近一段日志。这个包不含你的密钥，
            路径中的用户名也已经隐去。
          </div>
        </div>
        <button className="os-btn" onClick={() => void onExport()} disabled={busy}>
          {busy ? '正在整理…' : '导出诊断包'}
        </button>
      </div>
      {result && (
        <div
          className={result.tone === 'danger' ? 'os-alert' : 'os-note'}
          role={result.tone === 'danger' ? 'alert' : 'status'}
          aria-live="polite"
          style={{ marginTop: 12, whiteSpace: 'pre-line' }}
        >
          {result.message}
        </div>
      )}
    </div>
  )
}
