/**
 * 历史数据处置：无归属知识文档 + 无归属记忆 + 迁移隔离区。
 *
 * 这三类数据只在根账号 / 老部署的维护视图里出现，成员既看不到入口，后端也固定
 * 404。这里必须"看得见、能处置"：给出原文预览与引用，并支持逐条删除；本轮不
 * 提供自动归属 / 重新入库（归属无法判定正是它们留在这里的原因）。
 */

import { useState } from 'react'
import {
  deleteQuarantinedMemory,
  deleteUnassignedMemory,
  loadUnassignedMemory,
  useAccountData,
  type MemoryEntry,
  type QuarantinedMemoryRow,
  type UnassignedMemoryPayload,
} from '../lib/accounts'
import {
  deleteKnowledgeDocument,
  fetchUnassignedDocuments,
  type UnassignedKnowledgeDoc,
  type UnassignedKnowledgePage,
} from '../lib/knowledge'
import { Card, Guard } from './ui'

/** 迁移隔离原因 → 中文（未知原因不猜，原样显示标识）。 */
const QUARANTINE_REASON: Record<string, string> = {
  non_memory_telemetry: '运行时遥测（不是用户记忆）',
  empty_text: '正文为空',
  missing_entry_id: '缺少条目 id',
  missing_key: '缺少键名',
}

const DOC_REASON: Record<string, string> = {
  missing_owner_metadata: '缺少归属字段，旧 id 也无法反推账号',
}

function reasonText(reason: string): string {
  return QUARANTINE_REASON[reason] ?? reason
}

function docReasonText(reason: string): string {
  return DOC_REASON[reason] ?? reason
}

function whenText(raw: string): string {
  const ms = Date.parse(raw)
  if (!Number.isFinite(ms) || ms <= 0) return raw || '—'
  return new Date(ms).toLocaleString('zh-CN', { hour12: false })
}

/** 隔离区 payload 的原文预览：优先 key/value，缺失时退回整段 JSON。 */
function quarantinePreview(row: QuarantinedMemoryRow): string {
  const payload = row.payload ?? {}
  const parts: string[] = []
  const key = typeof payload.key === 'string' ? payload.key : ''
  if (key) parts.push(key)
  const value = payload.value
  if (typeof value === 'string') parts.push(value)
  else if (value !== null && value !== undefined) parts.push(JSON.stringify(value))
  const text = parts.join(' · ').trim()
  if (text) return text.length > 260 ? `${text.slice(0, 260)}…` : text
  try {
    const raw = JSON.stringify(payload)
    return raw.length > 260 ? `${raw.slice(0, 260)}…` : raw
  } catch {
    return '（原文无法解析）'
  }
}

function actionErrorText(failure: unknown): string {
  if (failure instanceof Error && failure.message.trim()) return failure.message
  return '操作没有完成，请稍后重试。'
}

export function MaintenanceData() {
  const memory = useAccountData<UnassignedMemoryPayload>(
    () => loadUnassignedMemory(),
    'maintenance-memory',
  )
  const knowledge = useAccountData<UnassignedKnowledgePage>(
    () =>
      fetchUnassignedDocuments().then((data) => ({ data, source: 'live' as const })),
    'maintenance-knowledge',
  )
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  async function runDelete(
    key: string,
    doneText: string,
    action: () => Promise<unknown>,
    reload: () => void,
  ) {
    setBusy(key)
    setNotice(null)
    setActionError(null)
    try {
      await action()
      setNotice(doneText)
      reload()
    } catch (failure) {
      setActionError(actionErrorText(failure))
    } finally {
      setBusy(null)
    }
  }

  function removeMemoryEntry(entry: MemoryEntry) {
    if (
      !window.confirm(
        `删除这条未归属记忆？删除后不可恢复。\n\n${entry.text}`,
      )
    ) {
      return
    }
    void runDelete(
      `entry:${entry.id}`,
      '未归属记忆已删除',
      () => deleteUnassignedMemory(entry.id),
      memory.reload,
    )
  }

  function removeQuarantine(row: QuarantinedMemoryRow) {
    if (
      !window.confirm(
        `删除这条隔离记录？隔离区只保留原始行，删除后不可恢复。\n\n${quarantinePreview(row)}`,
      )
    ) {
      return
    }
    void runDelete(
      `quarantine:${row.id}`,
      '隔离记录已删除',
      () => deleteQuarantinedMemory(row.id),
      memory.reload,
    )
  }

  function removeKnowledgeDoc(doc: UnassignedKnowledgeDoc) {
    if (
      !window.confirm(
        `删除这篇未归属知识文档？分块与向量会一起删除，且不可恢复。\n\n${doc.displayName}（${doc.chunks} 个分块）`,
      )
    ) {
      return
    }
    void runDelete(
      `doc:${doc.documentId}`,
      '未归属知识文档已删除',
      () => deleteKnowledgeDocument(doc.documentId),
      knowledge.reload,
    )
  }

  return (
    <>
      {(notice || actionError) && (
        <div style={{ gridColumn: '1 / -1' }}>
          {actionError && (
            <div className="os-alert" role="alert">
              <span>{actionError}</span>
            </div>
          )}
          {notice && (
            <div className="os-note" role="status">
              <span>{notice}</span>
            </div>
          )}
        </div>
      )}

      <Card
        title="未归属知识文档"
        sub="历史数据里无法判定归属的文档已保留原文：可查看预览与引用，也可逐条删除。本轮暂不支持自动归属或重新入库。"
        span={12}
      >
        {/* // GAP: 自动归属 / 重新入库未上线——归属无法判定正是这些文档留在这里的原因；
            卡片副标题已写明“暂不支持”，此处不画归属/入库按钮。 */}
        <Guard
          loading={knowledge.loading}
          error={knowledge.error}
          data={knowledge.data}
          isEmpty={(data) => data.documents.length === 0}
          emptyTitle="没有未归属的知识文档"
          emptyHint="迁移后无法判定归属的文档会出现在这里，直到人工删除或重新入库。"
          onRetry={knowledge.reload}
        >
          {(data) => (
            <>
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>文档</th>
                      <th>原因</th>
                      <th>分块</th>
                      <th>原文预览</th>
                      <th>引用</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.documents.map((doc) => (
                      <tr key={doc.documentId}>
                        <td>
                          <div>{doc.displayName}</div>
                          <div className="os-hint">{doc.documentId}</div>
                        </td>
                        <td>
                          <div>{docReasonText(doc.reason)}</div>
                          {doc.detail && <div className="os-hint">{doc.detail}</div>}
                        </td>
                        <td>{doc.chunks}</td>
                        <td
                          style={{
                            maxWidth: 360,
                            whiteSpace: 'pre-wrap',
                            wordBreak: 'break-word',
                          }}
                        >
                          {doc.preview || '（没有可显示的原文）'}
                          {doc.previewTruncated && <div className="os-hint">（已截断）</div>}
                        </td>
                        <td>
                          <code>{doc.reference}</code>
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>
                          <button
                            className="os-btn os-btn-ghost"
                            style={{ width: 'auto' }}
                            disabled={!doc.canDelete || busy !== null}
                            onClick={() => removeKnowledgeDoc(doc)}
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="os-note" style={{ marginTop: 12 }}>
                <span>
                  共 <strong>{data.total}</strong> 篇；迁移丢失{' '}
                  <strong>{data.lost}</strong> 篇。成员账号看不到这些文档，删除只影响
                  无归属历史数据。
                </span>
              </div>
            </>
          )}
        </Guard>
      </Card>

      <Card
        title="未归属记忆与迁移隔离区"
        sub="迁移时无法判定归属、但仍保留原文的记忆。可查看内容并逐条删除；成员账号看不到这些内容，相关操作只对本机主账号开放。"
        span={12}
      >
        <Guard
          loading={memory.loading}
          error={memory.error}
          data={memory.data}
          isEmpty={(data) => data.entries.length === 0 && data.quarantined.length === 0}
          emptyTitle="没有未归属或隔离的记忆"
          emptyHint="迁移时无法判定归属、或格式异常被隔离的原始行会出现在这里。"
          onRetry={memory.reload}
        >
          {(data) => (
            <>
              <h3 className="os-card-title" style={{ fontSize: 14 }}>
                未归属记忆（{data.total} 条）
              </h3>
              {data.entries.length === 0 ? (
                <div className="os-empty" role="status">
                  <strong>没有未归属记忆</strong>
                </div>
              ) : (
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>来源</th>
                        <th>内容</th>
                        <th>标签</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.entries.map((entry) => (
                        <tr key={entry.id}>
                          <td>{whenText(entry.created_at)}</td>
                          <td>{entry.source || '—'}</td>
                          <td
                            style={{
                              maxWidth: 380,
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                            }}
                          >
                            {entry.text}
                          </td>
                          <td>{entry.tags.length > 0 ? entry.tags.join('、') : '—'}</td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <button
                              className="os-btn os-btn-ghost"
                              style={{ width: 'auto' }}
                              disabled={busy !== null}
                              onClick={() => removeMemoryEntry(entry)}
                            >
                              删除
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <h3 className="os-card-title" style={{ fontSize: 14, marginTop: 16 }}>
                隔离区（{data.quarantined_total} 条）
              </h3>
              {data.quarantined.length === 0 ? (
                <div className="os-empty" role="status">
                  <strong>没有隔离记录</strong>
                </div>
              ) : (
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>来源引用</th>
                        <th>原因</th>
                        <th>原文</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.quarantined.map((row) => (
                        <tr key={row.id}>
                          <td>{whenText(row.created_at)}</td>
                          <td>
                            <div>{row.source || '—'}</div>
                            <div className="os-hint">{row.source_ref || '—'}</div>
                          </td>
                          <td>{reasonText(row.reason)}</td>
                          <td
                            style={{
                              maxWidth: 380,
                              whiteSpace: 'pre-wrap',
                              wordBreak: 'break-word',
                            }}
                          >
                            {quarantinePreview(row)}
                          </td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <button
                              className="os-btn os-btn-ghost"
                              style={{ width: 'auto' }}
                              disabled={busy !== null}
                              onClick={() => removeQuarantine(row)}
                            >
                              删除
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {data.note && (
                <div className="os-note" style={{ marginTop: 12 }}>
                  <span>{data.note}</span>
                </div>
              )}
            </>
          )}
        </Guard>
      </Card>
    </>
  )
}
