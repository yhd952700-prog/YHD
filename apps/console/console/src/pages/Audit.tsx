/**
 * 审计链 —— 真实审计数据的可解释性视图。
 *
 * 为什么这个文件存在
 * ------------------
 * `lib/operator.ts` 早就实现了 `/v1/audit/events`、`/v1/audit/verify`、
 * `/v1/audit/summary` 三个客户端函数，但**没有任何页面调用过它们**。于是后端
 * 有完整、真实、可校验的审计链，驾驶舱里却看不到 —— 一个"有数据但没视图"的
 * 空洞。这个页面把那三个函数真正渲染出来。
 *
 * 诚实优先
 * --------
 * * `/v1/audit/verify` 返回 `ok=false` 时**原样展示为危险态**，列出具体失败的
 *   seq。一个把"链坏了"渲染成"正常"的界面比没有界面更有害。
 * * 事件列表被 `limit` 截断时显示"还有更多"，而不是假装这是全部。
 * * 后端不可读 / 401 时显示真实错误，不退化成"暂无数据"。
 *
 * 只读：这个页面没有任何写入能力。能自愈的审计链等于可以被悄悄改写的审计链。
 */

import { useCallback, useMemo, useState } from 'react'
import { useApi } from '../lib/api'
import {
  exportAuditArchive,
  fetchAuditEvents,
  verifyAuditChain,
  type AuditEvent,
  type AuditEventsResponse,
  type AuditSummary,
  type AuditVerifyResult,
} from '../lib/operator'
import type { AccountProfile } from '../lib/accountCaps'
import { Card, Empty, Failure, Guard, Loading } from '../components/ui'
import { actionLabel, eventLabel, outcomeLabel, principalLabel } from '../lib/format'

/** 事件条数上限。后端默认 100、硬上限 1000；这里取 200 足够人读。 */
const EVENT_LIMIT = 200

function formatTime(timestamp: number): string {
  // 内核写的是 Unix 秒。非法/缺失值是事实，如实显示，不要伪装成时间。
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '—'
  try {
    return new Date(timestamp * 1000).toLocaleString()
  } catch {
    return String(timestamp)
  }
}

function outcomeTone(outcome: string): string {
  const value = (outcome || '').toLowerCase()
  if (value === 'denied' || value === 'failed' || value === 'error') return 'danger'
  if (value === 'ok' || value === 'success' || value === 'allowed' || value === 'granted') return 'ok'
  return 'muted'
}

export function Audit({ query, profile }: { query: string; profile: AccountProfile }) {
  /**
   * 范围筛选：根可选"全部 / 仅自己"；非根只保留"仅自己"。
   * 后端对非 L0 主体强制按令牌主体过滤（audit.py:270-280），
   * 因此界面不提供一个按不动的按钮 —— 只显示真正生效的那一档。
   */
  const [scope, setScope] = useState<'all' | 'self'>('all')
  const effectiveScope = profile.isRoot ? scope : 'self'
  const eventsPath =
    effectiveScope === 'all'
      ? `/v1/audit/events?limit=${EVENT_LIMIT}`
      : `/v1/audit/events?limit=${EVENT_LIMIT}&principal=${encodeURIComponent(profile.principal)}`

  const summary = useApi<AuditSummary>(profile.isRoot ? '/v1/audit/summary' : null, 30_000)
  const events = useApi<AuditEventsResponse>(eventsPath, 30_000)

  // 链校验刻意是**手动触发**的：它要扫全链，不该每 30 秒自动跑一次。
  const [verify, setVerify] = useState<AuditVerifyResult | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [verifyError, setVerifyError] = useState<string | null>(null)

  const runVerify = useCallback(async () => {
    setVerifying(true)
    setVerifyError(null)
    try {
      setVerify(await verifyAuditChain())
    } catch (err) {
      setVerify(null)
      setVerifyError(err instanceof Error ? err.message : '校验失败')
    } finally {
      setVerifying(false)
    }
  }, [])

  // 导出归档：密钥不出机，但记录本身要能带走。归档里带导出时刻的校验结果，
  // 换机后即使没有密钥也还能查阅、与原机的校验结论比对。
  const [exporting, setExporting] = useState(false)
  const [exportNotice, setExportNotice] = useState<string | null>(null)

  const runExport = useCallback(async () => {
    setExporting(true)
    setExportNotice(null)
    try {
      const archive = await exportAuditArchive()
      const blob = new Blob([JSON.stringify(archive, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `liuhao-audit-archive-${Date.now()}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      const mode = archive.integrity?.degraded
        ? '只能结构检查'
        : archive.integrity?.partial
          ? '部分可证明'
          : archive.integrity?.ok
            ? '完好'
            : '发现异常'
      setExportNotice(
        `已导出 ${archive.count} 条记录（导出时校验：${mode}）${
          archive.truncated ? '；记录较多，请用导出回执里的续传序号继续导出' : ''
        }。归档不含校验密钥，离开原机后无法重新做密钥校验。`,
      )
    } catch (err) {
      setExportNotice(
        err instanceof Error
          ? err.message
          : '导出没有完成，请重试；持续失败可在「设置 → 遇到问题」导出诊断包后再试。',
      )
    } finally {
      setExporting(false)
    }
  }, [])

  // 关联目标：点一下就按 correlation_id 过滤，用来回答"这件事的审计在哪"。
  const [correlation, setCorrelation] = useState<string | null>(null)
  const [filtered, setFiltered] = useState<AuditEvent[] | null>(null)
  const [filterError, setFilterError] = useState<string | null>(null)

  const openCorrelation = useCallback(async (id: string) => {
    setCorrelation(id)
    setFiltered(null)
    setFilterError(null)
    try {
      const response = await fetchAuditEvents({
        correlation_id: id,
        limit: EVENT_LIMIT,
      })
      setFiltered(response.events)
    } catch (err) {
      setFilterError(
        err instanceof Error ? err.message : '这一批操作没有读出来，请重试。',
      )
    }
  }, [])

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    const source = correlation ? (filtered ?? []) : (events.data?.events ?? [])
    if (!need) return source
    return source.filter((event) =>
      [
        event.display_title,
        event.target,
        event.event_type,
        event.principal_id,
        event.action,
        event.outcome,
        event.correlation_id,
      ]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [correlation, filtered, events.data, need])

  const truncated = !correlation && (events.data?.truncated ?? false)

  return (
    <div className="os-grid">
      {profile.isRoot && (
        <>
      <Card
        title="记录完整性"
        sub="只读校验 —— 只检查，从不修改"
        span={4}
        action={
          <>
            <button className="os-btn" onClick={() => void runVerify()} disabled={verifying}>
              {verifying ? '检查中…' : '检查记录完整性'}
            </button>
            <button className="os-btn" onClick={() => void runExport()} disabled={exporting}>
              {exporting ? '导出中…' : '导出归档'}
            </button>
          </>
        }
      >
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={(data) => data.total_events === 0}
          emptyTitle="还没有操作记录"
          emptyHint="先做一次会被记录的操作（登录、发消息、激活插件或修改成员），再回到本页刷新即可看到。"
          onRetry={summary.reload}
        >
          {(data) => (
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">你能看到的记录</span>
                <span className="os-kv-value">{data.total_events}</span>
              </div>
              {typeof data.store_total_events === 'number' && (
                <div className="os-kv-row">
                  <span className="os-kv-key">全库存储</span>
                  <span className="os-kv-value">{data.store_total_events}</span>
                </div>
              )}
              {typeof data.business_events === 'number' && (
                <div className="os-kv-row">
                  <span className="os-kv-key">业务事件（不含内部状态变更）</span>
                  <span className="os-kv-value">{data.business_events}</span>
                </div>
              )}
              <div className="os-kv-row">
                <span className="os-kv-key">最新编号</span>
                <span className="os-kv-value">{data.tail_seq}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">可追踪批次</span>
                <span className="os-kv-value">{data.distinct_correlation_ids}</span>
              </div>
              {data.retention?.enabled && (
                <div className="os-kv-row">
                  <span className="os-kv-key">保留期</span>
                  <span className="os-kv-value">{data.retention.days} 天</span>
                </div>
              )}
            </div>
          )}
        </Guard>
        {summary.data?.retention?.enabled && (
          <p className="os-hint">
            超过保留期（{summary.data.retention.days} 天）的记录会被自动删除，
            不是永久留存；极端写入下还会按上限（
            {summary.data.retention.max_rows.toLocaleString()} 行 /{' '}
            {Math.round(summary.data.retention.max_bytes / 1048576)} MB）
            提前裁剪。如需长期留档请定期导出。
          </p>
        )}
        {summary.data?.chain_key?.degraded && (
          <p className="os-note" role="status">
            {summary.data.chain_key.message || '完整性无法用校验密钥证明'}
          </p>
        )}
        {summary.data?.chain_key?.partial && !summary.data.chain_key.degraded && (
          <p className="os-hint">
            {summary.data.chain_key.partial_message ||
              '部分可证明：旧记录只能做结构检查'}
          </p>
        )}
        {exportNotice && (
          <p className="os-note" role="status">
            {exportNotice}
          </p>
        )}

        {/* 校验结果 —— ok=false 必须看起来就是坏了；缺密钥 / 混合链是另外两种状态 */}
        {verifyError && (
          <p className="os-error">
            校验请求失败：{verifyError}。可点右上角「检查记录完整性」重试。
          </p>
        )}
        {verify && (
          <div className="os-kv">
            <div className="os-kv-row">
              <span className="os-kv-key">记录状态</span>
              <span className="os-kv-value">
                <span
                  className={`os-dot os-tone-${
                    verify.degraded || verify.partial
                      ? 'warn'
                      : verify.ok
                        ? 'ok'
                        : 'danger'
                  }`}
                />
                {verify.degraded
                  ? verify.details.degraded_reason === 'chain_key_downgraded'
                    ? '无法用密钥证明（库内无带密钥记录）'
                    : '只能结构检查（缺少校验密钥）'
                  : verify.partial
                    ? '部分可证明（旧记录只能结构检查）'
                    : verify.ok
                      ? '完好'
                      : '已被修改 / 截断 / 分叉'}
              </span>
            </div>
            <div className="os-kv-row">
              <span className="os-kv-key">已检查条数</span>
              <span className="os-kv-value">{verify.entries_checked}</span>
            </div>
            {verify.degraded && (
              <p className="os-note" role="status">
                {verify.message || '缺少校验密钥，完整性只能做结构检查'}
                （{verify.details.degraded_reason === 'chain_key_downgraded'
                  ? `无密钥校验${verify.details.structural_ok ? '一致' : '不一致'}，但这不能证明链没有被整体重算`
                  : `结构${verify.details.structural_ok ? '一致' : '不一致'}`}；
                密钥状态：{verify.details.chain_key_status || 'missing'}）。
              </p>
            )}
            {verify.partial && !verify.degraded && (
              <p className="os-note" role="status">
                {verify.message ||
                  '部分可证明：升级前的旧记录只能做结构检查，升级后的记录可用密钥证明'}
                （这不是“完好”，也不是“损坏”：旧前缀无法用密钥证明未被重写）。
              </p>
            )}
            {verify.first_failure && (
              <div className="os-kv-row">
                <span className="os-kv-key">第一处异常</span>
                <span className="os-kv-value">{verify.first_failure}</span>
              </div>
            )}
            {verify.details.failures.length > 0 && (
              <ul className="os-list">
                {verify.details.failures.map((failure) => (
                  <li key={failure}>{failure}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      <Card
        title="操作类型分布"
        sub="按操作类型统计（真实计数）"
        span={4}
      >
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={(data) => Object.keys(data.by_action).length === 0}
          emptyTitle="还没有操作记录"
          emptyHint="先做一次会被记录的操作（登录、发消息、激活插件或修改成员），再回到本页刷新即可看到。"
          onRetry={summary.reload}
        >
          {(data) => (
            <ul className="os-list">
              {Object.entries(data.by_action)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 12)
                .map(([action, count]) => (
                  <li key={action}>
                    <span>{actionLabel(action)}</span>
                    <span className="os-legend-value">{count}</span>
                  </li>
                ))}
            </ul>
          )}
        </Guard>
      </Card>

      <Card title="结果分布" sub="操作结果计数" span={4}>
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={(data) => Object.keys(data.by_outcome).length === 0}
          emptyTitle="还没有操作结果"
          onRetry={summary.reload}
        >
          {(data) => (
            <ul className="os-list">
              {Object.entries(data.by_outcome)
                .sort((a, b) => b[1] - a[1])
                .map(([outcome, count]) => (
                  <li key={outcome}>
                    <span className={`os-dot os-tone-${outcomeTone(outcome)}`} />
                    <span>{outcomeLabel(outcome)}</span>
                    <span className="os-legend-value">{count}</span>
                  </li>
                ))}
            </ul>
          )}
        </Guard>
      </Card>
        </>
      )}

      <Card
        title={correlation ? '同一批操作' : '操作记录'}
        sub="每条都说明：谁、什么时候、做了什么、对谁、结果如何"
        span={12}
        action={
          correlation ? (
            <button className="os-btn" onClick={() => setCorrelation(null)}>
              返回全部
            </button>
          ) : profile.isRoot ? (
            <select
              aria-label="查看范围"
              value={scope}
              onChange={(event) => setScope(event.target.value as 'all' | 'self')}
            >
              <option value="all">全部</option>
              <option value="self">仅自己</option>
            </select>
          ) : undefined
        }
      >
        {/* 错误与加载必须显式分支：events 轮询失败时 data 仍是 null，
            若直接走到空态，会把"读不到审计记录"渲染成"还没有操作记录"。 */}
        {filterError ? (
          <Failure
            message={filterError}
            onRetry={correlation ? () => void openCorrelation(correlation) : undefined}
          />
        ) : correlation && filtered === null ? (
          <Loading rows={5} />
        ) : !correlation && events.error ? (
          <Failure message={events.error} onRetry={events.reload} />
        ) : !correlation && events.loading && events.data === null ? (
          <Loading rows={5} />
        ) : shown.length === 0 ? (
          <Empty
            title={correlation ? '这一批没有其它操作' : '还没有操作记录'}
            hint="有人登录、改设置、建员工或派活时，这里会自动出现一条记录。"
          />
        ) : (
          <>
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>编号</th>
                    <th>时间</th>
                    <th>账号</th>
                    <th>做了什么</th>
                    <th>结果</th>
                    <th>同一批</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((event) => (
                    <tr key={event.seq}>
                      <td>{event.seq}</td>
                      <td>{formatTime(event.timestamp)}</td>
                      <td>{principalLabel(event.principal_id) || '—'}</td>
                      <td>
                        {/* 后端已拼好一句人话（动作 + 对象 + 结果）；旧后端退回中文标签。 */}
                        {event.display_title ||
                          [
                            event.action ? actionLabel(event.action) : eventLabel(event.event_type),
                            event.target,
                          ]
                            .filter(Boolean)
                            .join('：') ||
                          '系统操作'}
                      </td>
                      <td>
                        <span className={`os-dot os-tone-${outcomeTone(event.outcome)}`} />
                        {event.status_label || outcomeLabel(event.outcome)}
                      </td>
                      <td>
                        {event.correlation_id ? (
                          <button
                            className="os-link"
                            onClick={() => void openCorrelation(event.correlation_id)}
                          >
                            查看同批操作
                          </button>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {truncated && (
              <p className="os-hint">
                只显示最近 {EVENT_LIMIT} 条 —— 链上还有更多，这里不是全部。
              </p>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
