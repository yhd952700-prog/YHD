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

/**
 * 把被剔除的成员按"日志 / 诊断文件"归类，别让提示只剩一个光秃秃的数字。
 * 网关与桌面返回的名字形如 ``logs/gateway.log.txt``、``summary.json``。
 */
function omittedMessage(names: string[]): string {
  const logs = names.filter((name) => name.startsWith('logs/')).length
  const others = names.length - logs
  const parts = [
    logs > 0 ? `${logs} 个日志文件` : '',
    others > 0 ? `${others} 个诊断文件` : '',
  ].filter(Boolean)
  return `有 ${parts.join('和 ')}因含密钥未包含，清单见包内 REDACTED-MEMBERS.txt。`
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
      const where =
        saved.location === 'chosen'
          ? `已保存：${saved.filename}（在你刚才选择的位置）`
          : `已开始下载：${saved.filename}。请到浏览器的下载位置查看。`
      const omitted =
        saved.omitted.length > 0 ? `\n${omittedMessage(saved.omitted)}` : ''
      setResult({ tone: 'ok', message: `${where}${omitted}` })
    } catch (err) {
      setResult({
        tone: 'danger',
        message: err instanceof Error && err.message
          ? err.message
          : '导出没有完成。请把这条提示截图发给技术支持。',
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
            路径中的用户名也已经隐去；若某个文件仍含明文密钥，导出时会自动剔除它，
            并在包内 REDACTED-MEMBERS.txt 列出少了哪些（日志或诊断文件），
            其余文件照常发给技术支持即可。
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
