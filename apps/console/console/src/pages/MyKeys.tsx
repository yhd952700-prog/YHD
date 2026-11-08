/**
 * 我的 Key / 全部 Key。
 *
 * 非根：只出现"Key 归属：我的账号"、授权开关与用量，不出现任何来源或结构字段。
 * 根：看到每个成员的 Key 授权与真实归属，可代用者标注为"主账号可代用"。
 *
 * 后端目前只有 `key-sharing` 开关，没有 Key 的新增 / 轮换 / 吊销端点；
 * 页面不伪造这些动作（IA C3）。
 */

import { useState } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { keyAuthorizationLabel, selfKeyLabel } from '../lib/accountCaps'
import {
  descendantsOf,
  loadAccounts,
  loadTree,
  loadUsage,
  setKeySharing,
  useAccountData,
} from '../lib/accounts'
import type { AccountNode } from '../lib/accounts'
import { numberText } from '../lib/format'
import { Card, Empty, Guard } from '../components/ui'
import { StubBadge } from '../components/AccountChrome'

/** 成本统一按"元"展示；极小金额保留 6 位，避免显示成 ¥0.00 被误读为免费。 */
function formatCost(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '未计入成本'
  if (value === 0) return '¥0.00'
  return value < 0.01 ? `¥${numberText(value, 6)}` : `¥${numberText(value, 2)}`
}

export function MyKeys({
  profile,
  caps,
  readOnly,
}: {
  profile: AccountProfile
  caps: AccountCaps
  readOnly: boolean
}) {
  const accounts = useAccountData(() =>
    loadAccounts({ principal: profile.principal, isRoot: profile.isRoot }),
  )
  // 列表接口只给直接成员；"全部 Key"要覆盖整棵树，故根账号另取树。
  const tree = useAccountData<{ tree: AccountNode }>(() =>
    caps.canAllKeys ? loadTree({ principal: profile.principal, isRoot: true }) : null,
  )
  const keyRows = tree.data?.tree ? descendantsOf(tree.data.tree) : accounts.data?.members ?? []
  const [selected, setSelected] = useState<string>(profile.principal)
  const usage = useAccountData(() => loadUsage(selected))
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const changeSharing = async (allow: boolean, principal: string) => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      await setKeySharing(principal, allow)
      const self = principal === profile.principal
      setNotice({
        tone: 'ok',
        text: self
          ? allow
            ? '已允许授权使用我的 Key'
            : '已停止授权使用我的 Key'
          : allow
            ? `${principal} 的 Key 已允许代用；对方需要重新登录后生效`
            : `已停止对 ${principal} 的 Key 代用；对方需要重新登录后生效`,
      })
      accounts.reload()
      tree.reload()
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '授权变更失败',
      })
    } finally {
      setBusy(false)
    }
  }

  const usingStub =
    accounts.source === 'stub' || usage.source === 'stub' || tree.source === 'stub'
      ? ('stub' as const)
      : ('live' as const)

  return (
    <div className="os-grid">
      <Card
        title={caps.canAllKeys ? '全部 Key' : '我的 Key'}
        sub={
          caps.canAllKeys
            ? '每人一条授权记录。标注"主账号可代用"的成员，其 Key 可在模型路由中被本账号使用。'
            : '这里只显示你自己账号的 Key 归属与用量。'
        }
        span={12}
        action={<StubBadge source={usingStub} />}
      >
        {notice && (
          <div
            className={notice.tone === 'ok' ? 'os-note' : 'os-alert'}
            // 成功只是状态更新，失败必须打断式播报；此前一律 role="status"，
            // 读屏在错误发生时不会主动读出（polite 可能被后续内容盖掉）。
            role={notice.tone === 'ok' ? 'status' : 'alert'}
          >
            {notice.text}
          </div>
        )}

        {caps.canAllKeys ? (
          <Guard
            loading={accounts.loading}
            error={accounts.error}
            data={accounts.data}
            isEmpty={(data) => data.members.length === 0}
            emptyTitle="还没有成员"
            onRetry={accounts.reload}
          >
            {() => (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>账号</th>
                      <th>Key 归属</th>
                      <th>状态</th>
                      <th>用量</th>
                      <th>切换</th>
                    </tr>
                  </thead>
                  <tbody>
                    {keyRows.map((member) => (
                      <tr key={member.principal}>
                        <td>{member.principal}</td>
                        <td>{keyAuthorizationLabel(member, true)}</td>
                        <td>{member.status}</td>
                        <td>
                          <button className="os-link" onClick={() => setSelected(member.principal)}>
                            查看用量
                          </button>
                        </td>
                        <td>
                          <button
                            className="os-link"
                            disabled={readOnly || busy}
                            onClick={() =>
                              void changeSharing(!member.allow_superior_key_use, member.principal)
                            }
                          >
                            {member.allow_superior_key_use ? '取消代用' : '允许代用'}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Guard>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">账号</span>
                <span className="os-kv-value">{profile.principal}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">归属</span>
                <span className="os-kv-value">{selfKeyLabel().replace('Key 归属：', '')}</span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                className="os-btn"
                style={{ width: 'auto' }}
                disabled={readOnly || busy}
                onClick={() => void changeSharing(true, profile.principal)}
              >
                允许授权使用我的 Key
              </button>
              <button
                className="os-btn os-btn-ghost"
                style={{ width: 'auto' }}
                disabled={readOnly || busy}
                onClick={() => void changeSharing(false, profile.principal)}
              >
                停止共享
              </button>
            </div>
            <p className="os-hint">
              授权只影响模型调用走哪把 Key；你自己的对话、记忆与文件始终只属于你。
              用量会如实计入这把 Key。
            </p>
          </div>
        )}
      </Card>

      <Card
        title={`用量 · ${selected}`}
        sub="调用次数与用量（真实计数，按 Key 归属拆开）"
        span={12}
        action={<StubBadge source={usage.source} />}
      >
        <Guard
          loading={usage.loading}
          error={usage.error}
          data={usage.data}
          isEmpty={(data) => data.calls === 0}
          emptyTitle="暂无调用记录"
          onRetry={usage.reload}
        >
          {(data) => (
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">调用次数</span>
                <span className="os-kv-value">{data.calls}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">用量（输入 / 输出）</span>
                <span className="os-kv-value">
                  {data.tokens_in} / {data.tokens_out} · 合计 {data.total_tokens}
                </span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">成本（估算）</span>
                <span className="os-kv-value">
                  {data.cost_complete
                    ? formatCost(data.cost)
                    : data.cost
                      ? `已计入 ${formatCost(data.cost)}，其余未计入`
                      : '未计入成本'}
                </span>
              </div>
              {!data.cost_complete && (
                <p className="os-hint" role="status">
                  {data.cost_message || '该模型暂无价格数据，未计入成本'}
                  {data.unknown_models && data.unknown_models.length > 0
                    ? `（${data.unknown_models.join('、')}）`
                    : ''}
                </p>
              )}
              {data.budget_note && <p className="os-hint">{data.budget_note}</p>}
              {caps.canAllKeys &&
                data.by_key_owner.map((entry) => (
                  <div className="os-kv-row" key={entry.key_owner}>
                    <span className="os-kv-key">Key 归属 · {entry.key_owner}</span>
                    <span className="os-kv-value">
                      {entry.calls} 次 · 用量 {entry.total_tokens}
                    </span>
                  </div>
                ))}
              {data.by_actor && data.by_actor.length > 0 && (
                <>
                  <div className="os-kv-row">
                    <span className="os-kv-key">实际调用者</span>
                    <span className="os-kv-value">仅本账号可见</span>
                  </div>
                  {data.by_actor.map((entry) => (
                    <div className="os-kv-row" key={entry.actor}>
                      <span className="os-kv-key">调用者 · {entry.actor}</span>
                      <span className="os-kv-value">
                        {entry.calls} 次 · 用量 {entry.total_tokens}
                      </span>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </Guard>
        <p className="os-hint">
          额度由启动配置决定，调整后需要重启鎏灏才会生效；本页只显示已用量。
        </p>
        {!caps.canAllKeys && (
          <Empty
            title="Key 新增与修改暂未开放"
            hint="暂不支持创建 / 轮换 / 吊销；这里不做假按钮。"
          />
        )}
      </Card>
    </div>
  )
}
