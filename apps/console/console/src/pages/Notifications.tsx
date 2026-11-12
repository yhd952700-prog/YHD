/**
 * 通知中心 —— 站内通知的收件箱（只读事件 + 本机已读）。
 *
 * 为什么不是另起一套通知
 * ----------------------
 * 系统里唯一"真实发生什么"的出口是事件总线（`/v1/events`）。再建一份通知数据，
 * 就得回答"谁写、写什么、何时清理"三个问题，而单人工作台里没有这个必要。
 * 所以这里直接复用事件流，只补两件人真正需要的事：**只看未读**、**逐条标为已读**。
 *
 * 诚实边界
 * --------
 * * 已读状态只记在这台设备（见 `lib/notificationRead.ts`），界面明说，不假装云端同步。
 * * 事件本身只读：没有"忽略/删除"这类会改写事实的按钮。
 * * 真空就是真空：没有事件时显示"暂无通知"，不造占位通知。
 */

import { useMemo, useState } from 'react'
import { useApi } from '../lib/api'
import type { EventsResponse } from '../lib/operator'
import { Card, Guard } from '../components/ui'
import { eventLabel } from '../lib/format'
import { loadReadKeys, notificationKey, saveReadKeys } from '../lib/notificationRead'

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

export function Notifications() {
  // 与事件流同一端点、同一节奏；15 秒刷新一次。
  const listing = useApi<EventsResponse>('/v1/events?limit=100', 15_000)
  const [read, setRead] = useState<Set<string>>(() => loadReadKeys())
  const [onlyUnread, setOnlyUnread] = useState(false)

  const events = listing.data?.events ?? []
  const unread = useMemo(
    () => events.filter((event) => !read.has(notificationKey(event))),
    [events, read],
  )
  const shown = onlyUnread ? unread : events

  const persist = (next: Set<string>) => {
    setRead(saveReadKeys(next))
  }

  const markOne = (key: string) => {
    const next = new Set(read)
    next.add(key)
    persist(next)
  }

  const markAll = () => {
    const next = new Set(read)
    for (const event of events) next.add(notificationKey(event))
    persist(next)
  }

  return (
    <div className="os-grid">
      <Card
        title="通知中心"
        sub="系统里真实发生过、值得你留意的事件；已读只记在这台设备上"
        span={12}
        action={
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button
              className={onlyUnread ? 'os-btn' : 'os-btn os-btn-ghost'}
              onClick={() => setOnlyUnread((value) => !value)}
              aria-pressed={onlyUnread}
            >
              {onlyUnread ? '看全部' : `只看未读（${unread.length}）`}
            </button>
            <button className="os-btn os-btn-ghost" onClick={markAll} disabled={unread.length === 0}>
              全部标为已读
            </button>
            <button className="os-btn" onClick={() => listing.reload()}>
              立即刷新
            </button>
          </div>
        }
      >
        <Guard
          loading={listing.loading}
          error={listing.error}
          data={listing.data}
          isEmpty={(data) => data.count === 0}
          emptyTitle="暂无通知"
          emptyHint="系统还没有产生任何事件；完成一次真实动作（一次执行、一次登录、一次员工创建）后，这里会出现对应的通知。"
          onRetry={listing.reload}
        >
          {(data) => (
            <>
              <p className="os-hint">
                共 {data.count} 条 · 未读 {unread.length} 条
                {onlyUnread && unread.length === 0 ? ' —— 当前没有未读通知。' : ''}
              </p>
              {shown.length > 0 && (
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>状态</th>
                        <th>时间</th>
                        <th>事件</th>
                        <th>来源</th>
                        <th>摘要</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((event) => {
                        const key = notificationKey(event)
                        const isRead = read.has(key)
                        return (
                          <tr key={key}>
                            <td style={{ whiteSpace: 'nowrap' }}>{isRead ? '已读' : '未读'}</td>
                            <td style={{ whiteSpace: 'nowrap' }}>{formatTimestamp(event.timestamp)}</td>
                            <td style={{ whiteSpace: 'nowrap' }}>{eventLabel(event.type)}</td>
                            <td style={{ whiteSpace: 'nowrap' }}>{event.source || '—'}</td>
                            <td>{event.summary || '—'}</td>
                            <td>
                              {isRead ? (
                                '—'
                              ) : (
                                <button
                                  className="os-btn os-btn-ghost"
                                  onClick={() => markOne(key)}
                                  aria-label={`把这一条标为已读：${event.summary || event.type}`}
                                >
                                  标为已读
                                </button>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {data.truncated && (
                <p className="os-hint">
                  这里只列最近 {data.limit} 条；更早的事件请到「事件流」查看。
                </p>
              )}
            </>
          )}
        </Guard>
      </Card>

      <Card title="这个视图能证明什么" sub="边界写在这里，不用猜" span={12}>
        <ul className="os-list">
          <li>每一条都来自系统真实发生过的事件，实时读取，没有占位或模拟通知。</li>
          <li>已读状态只保存在这台设备上；换设备或清理浏览器数据后会重新显示为未读。</li>
          <li>只能标记阅读，不能改写或删除事件 —— 通知不是事实，事件流才是。</li>
        </ul>
      </Card>
    </div>
  )
}
