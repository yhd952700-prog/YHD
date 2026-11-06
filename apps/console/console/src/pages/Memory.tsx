/**
 * 记忆（所有账号可见，范围不同）。
 *
 * 根：自己的 + 整棵范围（只读检索、可导出整棵范围）。
 * 非根：只有自己的；不出现范围切换。
 *
 * 端点全部是真实实现（`src/gateway/account_memory.py`）：
 * `GET /v1/memory/entries`、`POST /v1/memory/export`、
 * `GET|POST /v1/memory/custody`。没有契约桩，也没有"待联调"分支。
 * 无数据与无权限都只给中性文案，不出现任何层级措辞。
 */

import { useState } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import {
  exportMemory,
  loadMemory,
  loadMemoryCustody,
  neutralDenial,
  setMemoryCustody,
  useAccountData,
} from '../lib/accounts'
import { Card, Guard } from '../components/ui'

export function Memory({
  profile,
  caps,
  readOnly,
}: {
  profile: AccountProfile
  caps: AccountCaps
  readOnly: boolean
}) {
  const [query, setQuery] = useState('')
  const [applied, setApplied] = useState('')
  const [scope, setScope] = useState<'self' | 'subtree'>('self')
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [custodyBusy, setCustodyBusy] = useState(false)

  const memory = useAccountData(() =>
    loadMemory({ principal: profile.principal, isRoot: profile.isRoot }, scope, applied),
  )
  const custody = useAccountData(() => loadMemoryCustody())

  const doExport = async () => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const result = await exportMemory(scope, 'json')
      const blob = new Blob([result.data.content], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = result.data.filename
      anchor.click()
      URL.revokeObjectURL(url)
      setNotice({ tone: 'ok', text: `导出完成，共 ${result.data.entries} 条` })
    } catch (error) {
      setNotice({ tone: 'error', text: neutralDenial(error) })
    } finally {
      setBusy(false)
    }
  }

  const toggleCustody = async (next: boolean) => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setCustodyBusy(true)
    setNotice(null)
    try {
      await setMemoryCustody(next)
      custody.reload()
      setNotice({ tone: 'ok', text: next ? '已开启记忆代管' : '已关闭记忆代管' })
    } catch (error) {
      setNotice({ tone: 'error', text: neutralDenial(error) })
    } finally {
      setCustodyBusy(false)
    }
  }

  return (
    <div className="os-grid">
      <Card
        title="记忆"
        sub={
          caps.canSubtreeMemory
            ? '可检索自己的记忆，也可只读检索整棵范围；导出范围随检索范围。'
            : '这里只显示属于你自己的记忆。'
        }
        span={12}
      >
        {notice && (
          <div className={notice.tone === 'ok' ? 'os-note' : 'os-alert'} role="status">
            {notice.text}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
          <input
            className="os-input-plain"
            style={{ flex: 1, minWidth: 200 }}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索记忆内容、来源或标签"
            aria-label="搜索记忆"
          />
          {caps.canSubtreeMemory && (
            <select
              aria-label="检索范围"
              className="os-input-plain"
              style={{ width: 'auto' }}
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as 'self' | 'subtree')
                memory.reload()
              }}
            >
              <option value="self">仅自己</option>
              <option value="subtree">整棵范围</option>
            </select>
          )}
          <button
            className="os-btn"
            style={{ width: 'auto' }}
            onClick={() => {
              setApplied(query.trim())
              memory.reload()
            }}
          >
            检索
          </button>
          <button
            className="os-btn os-btn-ghost"
            style={{ width: 'auto' }}
            disabled={readOnly || busy}
            onClick={() => void doExport()}
          >
            导出{scope === 'subtree' ? '整棵范围' : '我的记忆'}
          </button>
        </div>

        <Guard
          loading={memory.loading}
          error={memory.error}
          data={memory.data}
          isEmpty={(data) => data.entries.length === 0}
          emptyTitle={applied ? '没有命中的记忆' : '还没有记忆'}
          emptyHint={
            applied
              ? '换个关键词试试。'
              : '对话与任务产生的记忆会出现在这里。'
          }
          onRetry={memory.reload}
        >
          {(data) => (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>来源账号</th>
                    <th>来源</th>
                    <th>标签</th>
                    <th>内容</th>
                  </tr>
                </thead>
                <tbody>
                  {data.entries.map((entry) => (
                    <tr key={entry.id}>
                      <td>{entry.created_at || '—'}</td>
                      <td>{entry.account_id}</td>
                      <td>{entry.source || '—'}</td>
                      <td>{entry.tags.length > 0 ? entry.tags.join('、') : '—'}</td>
                      <td>{entry.text}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Guard>

        {caps.canSubtreeMemory && (
          <p className="os-hint">整棵范围只读检索；导出结果会带上来源账号。</p>
        )}
      </Card>

      <Card title="记忆代管" span={12}>
        <Guard
          loading={custody.loading}
          error={custody.error}
          data={custody.data}
          onRetry={custody.reload}
        >
          {(data) => (
            <label className="os-check">
              <input
                type="checkbox"
                checked={data.enabled}
                disabled={readOnly || custodyBusy}
                onChange={(event) => void toggleCustody(event.target.checked)}
              />
              <span>允许协作处理我的记忆</span>
            </label>
          )}
        </Guard>
        <p className="os-hint">
          只有你本人可以开启或关闭；变更会记入审计，关闭后立即停止。
        </p>
      </Card>
    </div>
  )
}
