/**
 * 信任内核 —— 真实信任状态（只读产品面）。
 *
 * 为什么这个页面存在
 * ------------------
 * LHX-C-010 信任内核此前长期 PRIMITIVE-ONLY：决策函数从未走上真实产品路径。这个页面
 * 把 ``GET /v1/trust/{entity_id}``（后端直接复用信任内核进程单例的真实状态）渲染出来。
 *
 * 诚实优先
 * --------
 * * 只读：没有任何写入口 / 撤销入口，也不会伪造任何信任状态。
 * * 后端返回的是信任内核**真实**状态（撤销标记 / 各作用域分数 / 自信任链 / 统计）；
 *   某作用域没有分数就直接省略，前端如实展示"该实体暂无信任分"。
 * * 401 由 apiFetch 统一抛出并清会话；其他错误原样展示，不退化成"暂无数据"。
 */

import { useState } from 'react'
import { useImeComposition } from '../lib/ime'
import { getTrust } from '../lib/operator'
import type { TrustSummary } from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

function formatTimestamp(ts: string | null): string {
  if (!ts) return '—'
  try {
    const d = new Date(ts)
    if (!Number.isFinite(d.getTime())) return ts
    return d.toLocaleString()
  } catch {
    return ts
  }
}

/** 信任统计的字段名 → 人话；不在表里的字段统一归入「其他统计」，不露英文键名。 */
const STAT_LABELS: Record<string, string> = {
  total_entities: '已登记实体',
  total_chain_links: '信任链条数',
  total_events: '记录事件数',
  revoked_entities: '已撤销实体',
  by_trust_level: '按信任等级分布',
}

function statLabel(name: string): string {
  return STAT_LABELS[name] ?? '其他统计'
}

/** 统计值 → 可读文本：嵌套分布展开成「L0 3 · L1 2」，不打印 JSON。 */
function statValue(value: unknown): string {
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return '—'
    return entries
      .map(([key, inner]) => `${key} ${typeof inner === 'number' ? inner : '—'}`)
      .join(' · ')
  }
  return '—'
}

export function Trust({ query }: { query: string }) {
  const ime = useImeComposition<HTMLInputElement>()
  const [entityId, setEntityId] = useState('')
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [data, setData] = useState<TrustSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = (id: string) => {
    const target = id.trim()
    if (!target) return
    setSubmitted(target)
    setLoading(true)
    setError(null)
    getTrust(target)
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

  // 外部 query（顶栏搜索）若非空且当前还没提交过，用它直接探一次。
  const effectiveQuery = query.trim()
  const resolvedId = submitted ?? (effectiveQuery || '')

  return (
    <div className="os-grid">
      <Card title="信任状态查询" sub="真实信任状态（只读）—— 输入实体标识查询" span={12}>
        <div className="os-form">
          <label className="os-field">
            <span className="os-field-label">实体标识</span>
            <input
              className="os-input"
              placeholder="实体标识（例如员工名）"
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              onKeyDown={(e) => {
                if (e.key !== 'Enter') return
                // 组词中的回车是选词，别拿半截拼音去查实体。
                if (ime.isImeKey(e)) return
                run(entityId)
              }}
              onCompositionStart={ime.onCompositionStart}
              onCompositionEnd={ime.onCompositionEnd}
            />
          </label>
          <button className="os-btn os-btn-primary" onClick={() => run(entityId)} disabled={!entityId.trim()}>
            查询
          </button>
        </div>
        {resolvedId && <p className="os-hint">当前查询：{resolvedId}</p>}
      </Card>

      <Card title="实体概览" sub="真实撤销标记与查询的实体标识" span={12}>
        <Guard
          loading={loading}
          error={error}
          data={data}
          isEmpty={(d) => d.entity_id !== resolvedId}
          emptyTitle="尚未查询"
          emptyHint="在上方输入一个实体标识并点击「查询」，即可看到它在系统里的真实信任状态。"
        >
          {(d) => (
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">实体标识</span>
                <span className="os-kv-value">{d.entity_id}</span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">撤销状态</span>
                <span className="os-kv-value">
                  {d.revoked ? (
                    <span className="os-badge" data-tone="danger">
                      已撤销
                    </span>
                  ) : (
                    <span className="os-badge" data-tone="ok">
                      未撤销
                    </span>
                  )}
                </span>
              </div>
            </div>
          )}
        </Guard>
      </Card>

      <Card title="信任分" sub="按作用域展开的真实分数（无分数的作用域不出现）" span={12}>
        <Guard
          loading={loading}
          error={error}
          data={data}
          isEmpty={(d) => d.entity_id !== resolvedId}
          emptyTitle="尚未查询"
          emptyHint="在上方输入一个实体标识并点击「查询」。"
        >
          {(d) =>
            Object.keys(d.scores).length === 0 ? (
              <Empty title="该实体暂无信任分" hint="系统没有为它登记任何层级的信任分。" />
            ) : (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>信任层级</th>
                      <th>等级</th>
                      <th>分数</th>
                      <th>有效期至</th>
                      <th>已过期</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(d.scores).map(([scope, s]) => (
                      <tr key={scope}>
                        <td>{scope}</td>
                        <td>{s.level}</td>
                        <td>{s.score}</td>
                        <td>{formatTimestamp(s.expires_at)}</td>
                        <td>{s.is_expired ? '是' : '否'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </Guard>
      </Card>

      <Card title="自信任链探针" sub="以自身为起点与终点的真实链结果" span={12}>
        <Guard
          loading={loading}
          error={error}
          data={data}
          isEmpty={(d) => d.entity_id !== resolvedId}
          emptyTitle="尚未查询"
          emptyHint="在上方输入一个实体标识并点击「查询」。"
        >
          {(d) =>
            d.chain_summary ? (
              <div className="os-kv">
                <div className="os-kv-row">
                  <span className="os-kv-key">来源</span>
                  <span className="os-kv-value">{d.chain_summary.source}</span>
                </div>
                <div className="os-kv-row">
                  <span className="os-kv-key">目标</span>
                  <span className="os-kv-value">{d.chain_summary.target}</span>
                </div>
                <div className="os-kv-row">
                  <span className="os-kv-key">综合得分</span>
                  <span className="os-kv-value">{d.chain_summary.composite_score}</span>
                </div>
                <div className="os-kv-row">
                  <span className="os-kv-key">是否有效</span>
                  <span className="os-kv-value">{d.chain_summary.valid ? '是' : '否'}</span>
                </div>
                <div className="os-kv-row">
                  <span className="os-kv-key">计算时间</span>
                  <span className="os-kv-value">{formatTimestamp(d.chain_summary.computed_at)}</span>
                </div>
              </div>
            ) : (
              <Empty title="无链可查" hint="系统没有该实体的任何信任链。" />
            )
          }
        </Guard>
      </Card>

      <Card title="运行统计" sub="真实的信任状态统计" span={12}>
        <Guard
          loading={loading}
          error={error}
          data={data}
          isEmpty={(d) => d.entity_id !== resolvedId}
          emptyTitle="尚未查询"
          emptyHint="在上方输入一个实体标识并点击「查询」。"
        >
          {(d) => (
            <div className="os-kv">
              {Object.entries(d.stats ?? {}).map(([key, value]) => (
                <div className="os-kv-row" key={key}>
                  <span className="os-kv-key">{statLabel(key)}</span>
                  <span className="os-kv-value">{statValue(value)}</span>
                </div>
              ))}
            </div>
          )}
        </Guard>
      </Card>
    </div>
  )
}
