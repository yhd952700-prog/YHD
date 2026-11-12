/**
 * 我的数据 —— 数据可携带性的唯一入口。
 *
 * 设计取舍：
 * 1. **一键导出是明文 ZIP**：会话是可直接阅读的 Markdown，记忆/知识库/用量是
 *    JSON/CSV；它不是加密备份的替代品，换机恢复完整身份仍走「备份与迁移」。
 * 2. **只导出自己的数据**：即使是根账号，这里也不会打包成员会话、记忆、员工
 *    或知识库 —— 那是加密备份的职责，且有二次确认。
 * 3. **回导只做记忆与知识库**：这两类是"内容"，可以按正文/哈希去重；会话与
 *    员工没有安全的重放语义，界面不提供假的"导入成功"。
 */

import { useRef, useState } from 'react'
import type { AccountProfile } from '../lib/accountCaps'
import { neutralDenial } from '../lib/accounts'
import { exportAllData, importKnowledge, importMemories } from '../lib/accounts'
import { Card } from '../components/ui'

type Notice = { tone: 'ok' | 'error'; text: string }

function parseEntries(raw: string, key: 'entries' | 'documents'): unknown[] {
  const parsed = JSON.parse(raw) as unknown
  if (Array.isArray(parsed)) return parsed
  if (parsed && typeof parsed === 'object') {
    const value = (parsed as Record<string, unknown>)[key]
    if (Array.isArray(value)) return value
  }
  throw new Error(`文件里没有 \`${key}\` 数组`)
}

export function MyData({
  profile,
  readOnly,
}: {
  profile: AccountProfile
  readOnly: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [importing, setImporting] = useState<'memory' | 'knowledge' | null>(null)
  const memoryInput = useRef<HTMLInputElement>(null)
  const knowledgeInput = useRef<HTMLInputElement>(null)

  const doExport = async () => {
    setBusy(true)
    setNotice(null)
    try {
      const { blob, filename } = await exportAllData()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      URL.revokeObjectURL(url)
      setNotice({
        tone: 'ok',
        text:
          `已生成 ${filename}，解压后从 README.md 开始看。` +
          '注意：这个 ZIP 是阅读归档，会话 / 目标 / 员工 / 模型 Key 不会随它迁移；' +
          '想完整搬到另一台机器请用命令行加密备份（backup create --encrypt）。',
      })
    } catch (error) {
      setNotice({ tone: 'error', text: neutralDenial(error) })
    } finally {
      setBusy(false)
    }
  }

  const doImport = async (kind: 'memory' | 'knowledge', file: File | undefined) => {
    if (!file) return
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setImporting(kind)
    setNotice(null)
    try {
      const text = await file.text()
      if (kind === 'memory') {
        const result = await importMemories(parseEntries(text, 'entries'))
        setNotice({
          tone: 'ok',
          text: `记忆回导完成：新增 ${result.data.imported} 条，已存在/跳过 ${result.data.skipped} 条。`,
        })
      } else {
        const result = await importKnowledge(parseEntries(text, 'documents'))
        const extra = result.data.deduplicated ? `，去重 ${result.data.deduplicated} 篇` : ''
        setNotice({
          tone: 'ok',
          text: `知识库回导完成：新增 ${result.data.imported} 篇${extra}，跳过 ${result.data.skipped} 篇。`,
        })
      }
    } catch (error) {
      setNotice({ tone: 'error', text: neutralDenial(error) })
    } finally {
      setImporting(null)
      if (memoryInput.current) memoryInput.current.value = ''
      if (knowledgeInput.current) knowledgeInput.current.value = ''
    }
  }

  return (
    <div className="os-grid">
      <Card
        title="一键导出我的全部数据"
        sub={`账号 ${profile.principal}：导出一个普通人能直接打开的 ZIP，不需要口令、不需要本系统。`}
        span={12}
      >
        {notice && (
          <div className={notice.tone === 'ok' ? 'os-note' : 'os-alert'} role="status">
            {notice.text}
          </div>
        )}
        <p className="os-muted">
          包含：会话（Markdown 对话全文，带时间）、目标（Markdown / JSON）、
          记忆（Markdown / JSON，带记录时间）、员工清单（Markdown，
          只含你自己的）、知识库（JSON 可回导 + 可阅读的 Markdown）、
          用量（usage.csv）、你自己名下的工作区文件（files/），以及逐份说明文件的
          INDEX.md 与清单文件。
        </p>
        <p className="os-muted">
          不包含：其他成员的会话、记忆、员工、知识库与文件；模型 Key、登录令牌、恢复码等任何凭据；
          本机安装路径、内部数据库与系统配置密文。主题等界面偏好保存在浏览器本地，不在服务端。
        </p>
        <div className="os-note">
          <strong>想完整搬到另一台机器？请用命令行加密备份，而不是这个 ZIP。</strong>
          <br />
          这个 ZIP 只用于阅读与回导记忆 / 知识库；会话、目标、员工清单、模型 Key 都不会随它迁移。
          完整换机请执行 <code>python -m src.cli backup create --encrypt --password "你的口令"</code>，
          再把生成的备份文件拷到新机器导入（图形界面：系统设置 → 备份与迁移）。
        </div>
        <button className="os-btn" onClick={doExport} disabled={busy}>
          {busy ? '正在打包…' : '导出 ZIP'}
        </button>
      </Card>

      <Card
        title="只回导记忆与知识库"
        sub="其余内容（会话、目标、员工清单、工作区文件）是只读归档，不提供回导；完整换机请用命令行加密备份（backup create --encrypt）。"
        span={12}
      >
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <label className="os-btn" style={{ cursor: readOnly ? 'not-allowed' : 'pointer' }}>
            {importing === 'memory' ? '正在导入记忆…' : '回导记忆（JSON）'}
            <input
              ref={memoryInput}
              type="file"
              accept="application/json,.json"
              hidden
              disabled={readOnly || importing !== null}
              onChange={(event) => doImport('memory', event.target.files?.[0])}
            />
          </label>
          <label className="os-btn" style={{ cursor: readOnly ? 'not-allowed' : 'pointer' }}>
            {importing === 'knowledge' ? '正在导入知识库…' : '回导知识库（JSON）'}
            <input
              ref={knowledgeInput}
              type="file"
              accept="application/json,.json"
              hidden
              disabled={readOnly || importing !== null}
              onChange={(event) => doImport('knowledge', event.target.files?.[0])}
            />
          </label>
        </div>
        <p className="os-muted">
          知识库回导要求向量化可用：向量模型未配置、本机 Ollama 未运行或缺少模型、或向量库不可用都会失败。
          原因以知识中心「知识库状态」卡为准，本地服务不会假装成功。
          记忆按（正文 / 来源 / 时间）去重，知识库按内容哈希去重。
        </p>
      </Card>
    </div>
  )
}
