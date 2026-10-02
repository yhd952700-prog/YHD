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
  fetchAuditEvents,
  verifyAuditChain,
  type AuditEvent,
  type AuditEventsResponse,
  type AuditSummary,
  type AuditVerifyResult,
} from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

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

export function Audit({ query }: { query: string }) {
  const summary = useApi<AuditSummary>('/v1/audit/summary', 30_000)
  const events = useApi<AuditEventsResponse>(`/v1/audit/events?limit=${EVENT_LIMIT}`, 30_000)

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
      setFilterError(err instanceof Error ? err.message : '读取失败')
    }
  }, [])

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    const source = correlation ? (filtered ?? []) : (events.data?.events ?? [])
    if (!need) return source
    return source.filter((event) =>
      [event.event_type, event.principal_id, event.action, event.outcome, event.correlation_id]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [correlation, filtered, events.data, need])

  const truncated = !correlation && (events.data?.truncated ?? false)

  return (
    <div className="os-grid">
      <Card
        title="审计链完整性"
        sub="只读校验 —— 后端只 SELECT，从不修复"
        span={4}
        action={
          <button className="os-btn" onClick={() => void runVerify()} disabled={verifying}>
            {verifying ? '校验中…' : '校验哈希链'}
          </button>
        }
      >
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={(data) => data.total_events === 0}
          emptyTitle="审计库为空"
          onRetry={summary.reload}
        >
          {(data) => (
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">事件总数</span>
                <span className="os-kv-value">{data.total_events}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">尾部 seq</span>
                <span className="os-kv-value">{data.tail_seq}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">关联 ID 数</span>
                <span className="os-kv-value">{data.distinct_correlation_ids}</span>
              </div>
            </div>
          )}
        </Guard>

        {/* 校验结果 —— ok=false 必须看起来就是坏了 */}
        {verifyError && <p className="os-error">校验请求失败：{verifyError}</p>}
        {verify && (
          <div className="os-kv">
            <div className="os-kv-row">
              <span className="os-kv-key">链状态</span>
              <span className="os-kv-value">
                <span className={`os-dot os-tone-${verify.ok ? 'ok' : 'danger'}`} />
                {verify.ok ? '完好' : '已被修改 / 截断 / 分叉'}
              </span>
            </div>
            <div className="os-kv-row">
              <span className="os-kv-key">已校验条目</span>
              <span className="os-kv-value">{verify.entries_checked}</span>
            </div>
            {verify.first_failure && (
              <div className="os-kv-row">
                <span className="os-kv-key">首个失败</span>
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
        title="动作分布"
        sub="按内核动作名统计（真实计数）"
        span={4}
      >
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={(data) => Object.keys(data.by_action).length === 0}
          emptyTitle="尚无已记录动作"
          onRetry={summary.reload}
        >
          {(data) => (
            <ul className="os-list">
              {Object.entries(data.by_action)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 12)
                .map(([action, count]) => (
                  <li key={action}>
                    <span>{action}</span>
                    <span className="os-legend-value">{count}</span>
                  </li>
                ))}
            </ul>
          )}
        </Guard>
      </Card>

      <Card title="结果分布" sub="真实 outcome 计数" span={4}>
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={(data) => Object.keys(data.by_outcome).length === 0}
          emptyTitle="尚无已记录结果"
          onRetry={summary.reload}
        >
          {(data) => (
            <ul className="os-list">
              {Object.entries(data.by_outcome)
                .sort((a, b) => b[1] - a[1])
                .map(([outcome, count]) => (
                  <li key={outcome}>
                    <span className={`os-dot os-tone-${outcomeTone(outcome)}`} />
                    <span>{outcome || '(空)'}</span>
                    <span className="os-legend-value">{count}</span>
                  </li>
                ))}
            </ul>
          )}
        </Guard>
      </Card>

      <Card
        title={correlation ? `关联事件 · ${correlation}` : '审计事件'}
        sub="点击关联 ID 可只看这一条链路"
        span={12}
        action={
          correlation ? (
            <button className="os-btn" onClick={() => setCorrelation(null)}>
              返回全部
            </button>
          ) : undefined
        }
      >
        {filterError && <p className="os-error">{filterError}</p>}
        {correlation && filtered === null && !filterError && <p>读取中…</p>}
        {shown.length === 0 && !filterError ? (
          <Empty
            title={correlation ? '该关联 ID 下没有事件' : '审计库为空'}
            hint="审计事件由内核在动作发生时写入；没有任何动作就没有事件。"
          />
        ) : (
          <>
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>seq</th>
                    <th>时间</th>
                    <th>主体</th>
                    <th>类型 / 动作</th>
                    <th>结果</th>
                    <th>关联 ID</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((event) => (
                    <tr key={event.seq}>
                      <td>{event.seq}</td>
                      <td>{formatTime(event.timestamp)}</td>
                      <td>{event.principal_id || '—'}</td>
                      <td>
                        {event.event_type}
                        {event.action ? ` · ${event.action}` : ''}
                      </td>
                      <td>
                        <span className={`os-dot os-tone-${outcomeTone(event.outcome)}`} />
                        {event.outcome || '—'}
                      </td>
                      <td>
                        {event.correlation_id ? (
                          <button
                            className="os-link"
                            onClick={() => void openCorrelation(event.correlation_id)}
                          >
                            {event.correlation_id}
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
