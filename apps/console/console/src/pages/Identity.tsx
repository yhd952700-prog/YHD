/**
 * 身份与权限 —— 真实身份内核的可解释性视图（只读）。
 *
 * 为什么这个页面存在
 * ------------------
 * `src/gateway/identity.py` 把**已经存在**的身份内核（生命周期 / 作用域上限 / 主体
 * 唯一性 / human-agent 命名空间不相交 / 注册表 fail-closed）暴露成了四个真实的
 * GET 端点，但此前没有任何页面渲染它们。于是「用户能看见并控制每个主体/权限」在
 * 运行时无从验证。这个页面把那四个端点真正摊开给人看。
 *
 * 诚实优先
 * --------
 * * 严格只读：没有任何写 / 授权入口 —— 赋权面（grant_permission）尚没有人类复核闸门，
 *   先把账本摊开，写操作留到它有真实闸门之后再谈。
 * * 后端对未知 id 返回**真实 404**，前端原样抛错并展示「主体不存在或已被清除」，
 *   不退化成空对象（空对象会被读成「这个主体存在但没有权限」）。
 * * `registry.rows_refused` / `warnings` 若非空必须原样摊开：fail-closed 时「零个人类」
 *   是注册表拒绝的结果，不是人类不存在，这两种答案不能混为一谈。
 * * `derived_action` / `derived_resource` 是展示层派生的，一律带「派生」标签，原始
 *   `permission` 串才是权威；绝不把派生当内核事实。
 * * 401 由 apiFetch 统一抛出并清会话；其他错误原样展示，不退化成「暂无数据」。
 */

import { useCallback, useMemo, useState } from 'react'
import { useApi } from '../lib/api'
import {
  getPrincipal,
  type IdentityRegistry,
  type IdentitySummary,
  type PermissionGrant,
  type PermissionsResponse,
  type PrincipalDetail,
  type PrincipalView,
  type PrincipalsResponse,
} from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

/** 状态 -> 徽标色调。active 是正常态，suspended / deactivated 是危险态。 */
function statusTone(status: string): 'ok' | 'danger' | undefined {
  const value = (status || '').toLowerCase()
  if (value === 'active') return 'ok'
  if (value === 'suspended' || value === 'deactivated') return 'danger'
  return undefined
}

/** 把注册表诚实度原样摊开（fail-closed 的真相）。 */
function RegistryReport({ registry }: { registry: IdentityRegistry }) {
  const refused = registry.rows_refused ?? []
  return (
    <div className="os-kv-col">
      <span className="os-kv-key">注册表诚实度</span>
      <ul className="os-list">
        <li>
          <span>后端</span>
          <span className="os-legend-value">{registry.backend ?? '—'}</span>
        </li>
        <li>
          <span>完整性强制</span>
          <span className="os-legend-value">{registry.integrity_enforced ? '已启用' : '未启用'}</span>
        </li>
        <li>
          <span>已接纳行数</span>
          <span className="os-legend-value">{registry.rows_admitted ?? '—'}</span>
        </li>
        <li>
          <span>被拒收行数</span>
          <span className="os-legend-value">{refused.length}</span>
        </li>
      </ul>
      {refused.length > 0 && (
        <div className="os-alert">
          注册表在加载时拒收了 {refused.length} 行，因此未出现在清单中：{refused.join('、')}。
          设置 {registry.integrity_key_env} 并重新注册这些人类才能接纳它们。
        </div>
      )}
    </div>
  )
}

/** 一组警告（fail-closed / 派生声明等）逐条诚实展示，不吞成「暂无数据」。 */
function Warnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {warnings.map((warning, index) => (
        <div className="os-alert" key={index}>
          {warning}
        </div>
      ))}
    </div>
  )
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    const date = new Date(iso)
    if (!Number.isFinite(date.getTime())) return iso
    return date.toLocaleString()
  } catch {
    return iso
  }
}

export function Identity({ query }: { query: string }) {
  const summary = useApi<IdentitySummary>('/v1/identity/summary', 30_000)
  const principals = useApi<PrincipalsResponse>('/v1/identity/principals', 30_000)
  const permissions = useApi<PermissionsResponse>('/v1/identity/permissions', 30_000)

  // 单个主体查询 —— 手动触发（输入 id / principal 才取），不轮询。
  // 404 原样展示为「主体不存在或已被清除」，不退化成空对象。
  const [lookupId, setLookupId] = useState('')
  const [detail, setDetail] = useState<PrincipalDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const runLookup = useCallback((id: string) => {
    const target = id.trim()
    if (!target) return
    setDetailLoading(true)
    setDetailError(null)
    setDetail(null)
    getPrincipal(target)
      .then((response) => {
        setDetail(response)
        setDetailLoading(false)
      })
      .catch((err: unknown) => {
        setDetail(null)
        setDetailError(err instanceof Error ? err.message : '读取失败')
        setDetailLoading(false)
      })
  }, [])

  const need = query.trim().toLowerCase()
  const shownPrincipals = useMemo<PrincipalView[]>(() => {
    const source = principals.data?.principals ?? []
    if (!need) return source
    return source.filter((principal) =>
      [principal.id, principal.principal, principal.kind, principal.status, principal.scope, principal.display_name]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [principals.data, need])

  const shownGrants = useMemo<PermissionGrant[]>(() => {
    const source = permissions.data?.grants ?? []
    if (!need) return source
    return source.filter((grant) =>
      [grant.principal_id, grant.principal, grant.permission, grant.derived_action, grant.derived_resource, grant.scope]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [permissions.data, need])

  return (
    <div className="os-grid">
      {/* ─── 身份总览 ─── */}
      <Card
        title="身份总览 / Identity Summary"
        sub="真实身份计数 + 注册表诚实度（只读）"
        span={12}
      >
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={(data) => data.total_principals === 0}
          emptyTitle="没有任何已登记主体"
          emptyHint="身份内核里当前一个主体都没有；这不是读取失败，而是真实为空。"
          onRetry={summary.reload}
        >
          {(data) => (
            <>
              <div className="os-kv">
                <div className="os-kv-row">
                  <span className="os-kv-key">主体总数</span>
                  <span className="os-kv-value">{data.total_principals}</span>
                </div>
                <div className="os-kv-row">
                  <span className="os-kv-key">授权总数</span>
                  <span className="os-kv-value">{data.total_grants}</span>
                </div>
              </div>

              <div className="os-kv-col">
                <span className="os-kv-key">按类型</span>
                <ul className="os-list">
                  {Object.entries(data.by_kind).map(([kind, value]) => (
                    <li key={kind}>
                      <span>{kind}</span>
                      <span className="os-legend-value">{value}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="os-kv-col">
                <span className="os-kv-key">按状态</span>
                <ul className="os-list">
                  {Object.entries(data.by_status).map(([status, value]) => (
                    <li key={status}>
                      <span>{status}</span>
                      <span className="os-legend-value">{value}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="os-kv-col">
                <span className="os-kv-key">按作用域上限</span>
                <ul className="os-list">
                  {Object.entries(data.by_scope).map(([scope, value]) => (
                    <li key={scope}>
                      <span>{scope}</span>
                      <span className="os-legend-value">{value}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <RegistryReport registry={data.registry} />
              <Warnings warnings={data.warnings} />
            </>
          )}
        </Guard>
      </Card>

      {/* ─── 主体清单 ─── */}
      <Card
        title="主体清单 / Principals"
        sub="身份内核真实主体（只读）"
        span={12}
      >
        <Guard
          loading={principals.loading}
          error={principals.error}
          data={principals.data}
          isEmpty={(data) => data.principals.length === 0}
          emptyTitle="没有任何已登记主体"
          emptyHint="身份内核当前没有登记任何主体。读取失败会显示在上方，不会退化成这个空态。"
          onRetry={principals.reload}
        >
          {(data) => (
            <>
              <Warnings warnings={data.warnings} />
              {shownPrincipals.length === 0 ? (
                <Empty title="没有匹配的主体" hint="当前搜索条件没有命中任何主体。" />
              ) : (
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>主体 id</th>
                        <th>principal</th>
                        <th>类型</th>
                        <th>作用域上限</th>
                        <th>是否人</th>
                        <th>状态</th>
                        <th>权限数</th>
                        <th>创建于</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shownPrincipals.map((principal) => (
                        <tr key={principal.id}>
                          <td>{principal.id}</td>
                          <td>{principal.principal}</td>
                          <td>
                            {principal.kind}
                            {principal.kind_marked ? '' : '（推断）'}
                          </td>
                          <td>{principal.scope}</td>
                          <td>{principal.kind === 'human' ? '是' : '否'}</td>
                          <td>
                            {statusTone(principal.status) ? (
                              <span className="os-badge" data-tone={statusTone(principal.status)}>
                                {principal.status}
                              </span>
                            ) : (
                              principal.status
                            )}
                          </td>
                          <td>{principal.permission_count}</td>
                          <td>{formatTime(principal.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {data.truncated && (
                <p className="os-hint">
                  仅显示前 {data.count} 条（命中 {data.total} 条）—— 还有更多，这里不是全部。
                </p>
              )}
            </>
          )}
        </Guard>
      </Card>

      {/* ─── 权限（只读） ─── */}
      <Card
        title="权限（只读） / Permissions"
        sub="扁平授权清单 —— 派生字段均带「派生」标签"
        span={12}
      >
        <Guard
          loading={permissions.loading}
          error={permissions.error}
          data={permissions.data}
          isEmpty={(data) => data.grants.length === 0}
          emptyTitle="没有任何授权"
          emptyHint="没有任何主体被授予任何权限；这不是读取失败，而是真实为空。"
          onRetry={permissions.reload}
        >
          {(data) => (
            <>
              <Warnings warnings={data.warnings} />
              {data.notes.map((note, index) => (
                <div className="os-note" key={index}>
                  {note}
                </div>
              ))}
              {shownGrants.length === 0 ? (
                <Empty title="没有匹配的授权" hint="当前搜索条件没有命中任何授权。" />
              ) : (
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>主体</th>
                        <th>权限原串</th>
                        <th>派生动作</th>
                        <th>派生资源</th>
                        <th>作用域上限</th>
                        <th>身份有效</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shownGrants.map((grant, index) => (
                        <tr key={`${grant.principal_id}-${grant.permission}-${index}`}>
                          <td>{grant.principal}</td>
                          <td>{grant.permission}</td>
                          <td>
                            {grant.derived_action ?? '—'}
                            {grant.derived ? '（派生）' : ''}
                          </td>
                          <td>
                            {grant.derived_resource ?? '—'}
                            {grant.derived ? '（派生）' : ''}
                          </td>
                          <td>{grant.scope}</td>
                          <td>{grant.identity_active ? '是' : '否'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {data.truncated && (
                <p className="os-hint">仅显示前 {data.count} 条授权，不是全部。</p>
              )}
            </>
          )}
        </Guard>
      </Card>

      {/* ─── 单个主体查询（手动触发，演示 404 诚实态） ─── */}
      <Card
        title="主体详情查询"
        sub="按 id / principal 查询单个主体（未知 -> 404）"
        span={12}
      >
        <div className="os-form">
          <label className="os-field">
            <span className="os-field-label">主体 id 或 principal</span>
            <input
              className="os-input"
              placeholder="例如 system / human:xxx / agent 的 hex id"
              value={lookupId}
              onChange={(event) => setLookupId(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') runLookup(lookupId)
              }}
            />
          </label>
          <button
            className="os-btn os-btn-primary"
            onClick={() => runLookup(lookupId)}
            disabled={!lookupId.trim()}
          >
            查询
          </button>
        </div>

        {detailError && <p className="os-error">主体不存在或已被清除：{detailError}</p>}
        {detailLoading && <p>读取中…</p>}
        {detail && (
          <>
            <Warnings warnings={detail.warnings} />
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">id</span>
                <span className="os-kv-value">{detail.principal.id}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">principal</span>
                <span className="os-kv-value">{detail.principal.principal}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">类型</span>
                <span className="os-kv-value">
                  {detail.principal.kind}
                  {detail.principal.kind_marked ? '' : '（推断）'}
                </span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">状态</span>
                <span className="os-kv-value">{detail.principal.status}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">作用域上限</span>
                <span className="os-kv-value">{detail.principal.scope}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">权限数</span>
                <span className="os-kv-value">{detail.permission_count}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">创建于</span>
                <span className="os-kv-value">{formatTime(detail.principal.created_at)}</span>
              </div>
            </div>
            {detail.grants.length > 0 && (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>权限原串</th>
                      <th>派生动作</th>
                      <th>派生资源</th>
                      <th>作用域上限</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.grants.map((grant, index) => (
                      <tr key={`${grant.permission}-${index}`}>
                        <td>{grant.permission}</td>
                        <td>
                          {grant.derived_action ?? '—'}
                          {grant.derived ? '（派生）' : ''}
                        </td>
                        <td>
                          {grant.derived_resource ?? '—'}
                          {grant.derived ? '（派生）' : ''}
                        </td>
                        <td>{grant.scope}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
