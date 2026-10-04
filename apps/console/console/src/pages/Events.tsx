/**
 * 事件流 —— 内核总线实况（只读产品面）。
 *
 * 为什么这个页面存在
 * ------------------
 * LHX-C-008 事件内核一直在总线里真实地发布/订阅事件，但此前唯一的出口是挂在
 * 非产品 app（``src/api/server.py``）上的一条 WebSocket，没有任何"人类可见"的页面。
 * 这个页面把 ``GET /v1/events``（后端直接复用 event kernel 的内存近期事件缓冲
 * ``get_event_history``）真实渲染出来。
 *
 * 诚实优先
 * --------
 * * 只读：没有任何发布/订阅写入入口。
 * * 后端返回的是总线里**真实发生**的事件；真空就是真空，如实显示"内核总线暂无事件"，
 *   不编造一条假装系统很忙的占位事件。
 * * 401 由 apiFetch 统一抛出并清会话；其他错误原样展示，不退化成"暂无数据"。
 * * 每 15 秒轮询一次，呈现内核总线实况。
 */

import { useMemo } from 'react'
import { useApi } from '../lib/api'
import type { EventsResponse } from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

function formatTimestamp(ts: string): string {
  if (!ts) return '—'
  try {
    const d = new Date(ts)
    if (!Number.isFinite(d.getTime())) return ts
    return d.toLocaleString()
  } catch {
    return ts
  }
}

export function Events({ query }: { query: string }) {
  // 随轮询自动刷新；未登录时挂起（path=null 由 useApi 处理）。
  const listing = useApi<EventsResponse>('/v1/events?limit=50', 15_000)

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    const events = listing.data?.events ?? []
    if (!need) return events
    return events.filter((e) =>
      [e.type, e.source, e.scope, e.priority, e.summary]
        .some((value) => (value ?? '').toLowerCase().includes(need)),
    )
  }, [listing.data, need])

  return (
    <div className="os-grid">
      <Card
        title="真实系统事件流"
        sub="内核总线实况 —— 来自事件内核的 get_event_history，每 15 秒刷新"
        span={12}
        action={
          <button className="os-btn" onClick={() => listing.reload()}>
            立即刷新
          </button>
        }
      >
        <Guard
          loading={listing.loading}
          error={listing.error}
          data={listing.data}
          isEmpty={(data) => data.count === 0}
          emptyTitle="内核总线暂无事件"
          emptyHint="总线里还没有任何事件流过；让系统跑一次真实动作（例如一次执行、一次登录、一次插件激活），再回到这里就能看到了。"
          onRetry={listing.reload}
        >
          {(data) => (
            <>
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>类型</th>
                      <th>来源</th>
                      <th>作用域</th>
                      <th>优先级</th>
                      <th>摘要</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((e) => (
                      <tr key={`${e.id}:${e.timestamp}:${e.type}`}>
                        <td>{formatTimestamp(e.timestamp)}</td>
                        <td>{e.type}</td>
                        <td>{e.source}</td>
                        <td>{e.scope}</td>
                        <td>{e.priority}</td>
                        <td>{e.summary || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.truncated && (
                <p className="os-hint">
                  缓冲区内还有更早的事件；当前显示最近 {data.limit} 条。
                </p>
              )}
              {shown.length !== data.events.length && (
                <p className="os-hint">
                  搜索「{query}」过滤后显示 {shown.length} / {data.events.length} 条。
                </p>
              )}
            </>
          )}
        </Guard>
      </Card>

      {listing.data && listing.data.count === 0 && (
        <Card title="说明" sub="这个视图能证明什么" span={12}>
          <ul className="os-list">
            <li>每一条都来自事件内核总线里真实发生过的事件，由后端 get_event_history 实时读取，无缓存、无模拟。</li>
            <li>只读：没有任何写入口，也不会伪造任何"心跳"事件来让界面显得热闹。</li>
            <li>真空就是真空 —— 内核总线没有事件时如实显示"暂无事件"，不假装系统正在活动。</li>
          </ul>
          <Empty
            title="内核总线暂无事件"
            hint="让系统执行一次真实动作后回来即可看到。"
          />
        </Card>
      )}
    </div>
  )
}
