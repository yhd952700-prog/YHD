/**
 * 网络内核 —— 真实总线状态（只读产品面）。
 *
 * 为什么这个页面存在
 * ------------------
 * LHX-C-009 网络内核此前长期 PRIMITIVE-ONLY：总线只对内核内部 / 测试可见，从未走上真实
 * 产品路径（A2A/MCP/gRPC 适配器根本不存在）。这个页面把 ``GET /v1/network/messages``
 * （后端直接复用网络内核进程单例的真实总线状态）渲染出来。
 *
 * 诚实优先
 * --------
 * * 只读：没有任何写入口 / 发送入口，也不会伪造任何总线状态。
 * * 后端返回的是总线**真实**状态（stats 统计 / 消息历史）；全新进程里总线没有任何消息
 *   时 history 为空数组，前端如实展示"总线暂无消息"。
 * * 401 由 apiFetch 统一抛出并清会话；其他错误原样展示，不退化成"暂无数据"。
 */

import { useState } from 'react'
import { getNetworkMessages } from '../lib/operator'
import type { NetworkMessagesResponse } from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

export function Network({ query: _query }: { query: string }) {
  const [data, setData] = useState<NetworkMessagesResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    getNetworkMessages()
      .then((res) => {
        setData(res)
        setLoading(false)
      })
      .catch((err: unknown) => {
        setData(null)
        setError(err instanceof Error ? err.message : '读取失败')
        setLoading(false)
      })
  }

  return (
    <div className="os-grid">
      <Card title="网络总线状态" sub="网络内核真实状态（只读）—— 总线统计与消息历史" span={12}>
        <div className="os-form">
          <button className="os-btn os-btn-primary" onClick={load} disabled={loading}>
            {loading ? '刷新中…' : '刷新'}
          </button>
          {data && <p className="os-hint">已载入：{data.history.length} 条消息</p>}
        </div>
      </Card>

      <Card title="总线统计" sub="bus.stats() 的真实统计" span={12}>
        <Guard
          loading={loading}
          error={error}
          data={data}
          isEmpty={(d) => d === null}
          emptyTitle="尚未载入"
          emptyHint="点击上方「刷新」读取网络总线的真实状态。"
        >
          {(d) => <pre className="os-pre">{JSON.stringify(d.stats, null, 2)}</pre>}
        </Guard>
      </Card>

      <Card title="消息历史" sub="总线真实发生过的消息（最近 10000 条）" span={12}>
        <Guard
          loading={loading}
          error={error}
          data={data}
          isEmpty={(d) => d === null}
          emptyTitle="尚未载入"
          emptyHint="点击上方「刷新」读取消息历史。"
        >
          {(d) =>
            d.history.length === 0 ? (
              <Empty title="总线暂无消息" hint="网络内核进程启动后还没有任何消息经过总线。" />
            ) : (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>id</th>
                      <th>类型</th>
                      <th>来源</th>
                      <th>目标</th>
                      <th>协议</th>
                      <th>状态</th>
                      <th>创建时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.history.map((m) => (
                      <tr key={m.id}>
                        <td>{m.id}</td>
                        <td>{m.type}</td>
                        <td>{m.source}</td>
                        <td>{m.destination}</td>
                        <td>{m.protocol}</td>
                        <td>{m.status}</td>
                        <td>{m.created_at ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </Guard>
      </Card>
    </div>
  )
}
