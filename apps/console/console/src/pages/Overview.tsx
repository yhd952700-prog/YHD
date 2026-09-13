/**
 * 总览 —— 设计图的主屏。
 *
 * NO-FAKE 说明：设计图里的四个 KPI 有一个是"今日接入（金额）"。本项目**没有**
 * 真实的资金/账本数据源（Economy 引擎是库不是实时账本，无全局单例），因此这里
 * 换成同样四个位置、但每一个都能追溯到真实来源的指标：
 *
 *   AI 员工总数  ← capability-registry.yaml（14 内核 + 14 能力层）
 *   运行中任务    ← 网关进程内活跃会话表
 *   审计事件      ← 审计存储真实计数
 *   系统健康度    ← /v1/ready 的五项依赖检查
 *
 * 同理，设计图里的"任务进度百分比"没有真实来源，这里不画进度条；"实时任务流"
 * 展示真实审计事件，"事件分布"用真实的事件类型分布。
 */

import { useMemo } from 'react'
import type {
  ActivityPayload,
  AnalyticsPayload,
  DashboardSummary,
  HealthPayload,
  RosterPayload,
} from '../lib/contracts'
import { useApi } from '../lib/api'
import { formatCompact } from '../lib/chartUtils'
import { eventLabel, outcomeLabel, outcomeTone, relativeTime } from '../lib/format'
import { BrainMap, DonutChart, LineChart, NetworkGraph } from '../components/charts'
import { Card, Dot, Guard } from '../components/ui'

const REFRESH_LIVE = 15_000
const REFRESH_SLOW = 60_000

export function Overview({ query }: { query: string }) {
  const summary = useApi<DashboardSummary>('/v1/dashboard/summary', REFRESH_LIVE)
  const activity = useApi<ActivityPayload>('/v1/dashboard/activity?limit=30', REFRESH_LIVE)
  const roster = useApi<RosterPayload>('/v1/dashboard/roster', REFRESH_SLOW)
  const analytics = useApi<AnalyticsPayload>('/v1/dashboard/analytics?days=14', REFRESH_SLOW)
  // 503 也要接受：降级时响应里带着完整的检查项，那正是健康度最该显示真话的时刻。
  const ready = useApi<HealthPayload>('/v1/ready', 30_000, [503])

  const need = query.trim().toLowerCase()

  const filteredActivity = useMemo(() => {
    const items = activity.data?.activity ?? []
    if (!need) return items.slice(0, 8)
    return items
      .filter(
        (item) =>
          item.principal.toLowerCase().includes(need) ||
          eventLabel(item.type).toLowerCase().includes(need) ||
          item.type.toLowerCase().includes(need) ||
          outcomeLabel(item.outcome).toLowerCase().includes(need),
      )
      .slice(0, 8)
  }, [activity.data, need])

  const health = useMemo(() => {
    const checks = ready.data?.checks ?? {}
    const names = Object.keys(checks)
    if (names.length === 0) return null
    const healthy = names.filter((name) => checks[name]?.status === 'healthy').length
    return { healthy, total: names.length, ratio: healthy / names.length }
  }, [ready.data])

  return (
    <div className="os-grid">
      {/* ---------- KPI ---------- */}
      <div className="os-span-12">
        <div className="os-kpis">
          <Kpi
            label="AI 员工总数"
            value={roster.data ? String(roster.data.totals.employees) : null}
            foot={
              roster.data
                ? `${roster.data.totals.kernels} 内核 + ${roster.data.totals.layers} 能力层`
                : '来自能力注册表'
            }
            error={roster.error}
          />
          <Kpi
            label="运行中任务"
            value={summary.data?.sessions ? String(summary.data.sessions.count ?? 0) : null}
            foot="网关进程内活跃会话"
            error={summary.error}
          />
          <Kpi
            label="审计事件"
            value={
              summary.data?.audit ? formatCompact(summary.data.audit.total_events ?? 0) : null
            }
            foot="真实审计存储累计"
            error={summary.error}
          />
          <Kpi
            label="系统健康度"
            value={health ? `${(health.ratio * 100).toFixed(0)}%` : null}
            foot={health ? `${health.healthy}/${health.total} 项依赖正常` : '来自就绪探针'}
            error={ready.error}
            tone={health && health.ratio < 1 ? 'warn' : undefined}
          />
        </div>
      </div>

      {/* ---------- AI 员工协作网络 ---------- */}
      <Card
        title="AI 员工协作网络"
        sub="真实能力层（capability-registry.yaml）按阶段带分布"
        span={5}
      >
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          isEmpty={(data) => data.layers.length === 0}
          emptyTitle="注册表不可用"
          emptyHint="capability-registry.yaml 未能解析"
          onRetry={roster.reload}
        >
          {(data) => (
            <>
              <NetworkGraph
                hubLabel="鎏灏 OS"
                nodes={data.layers.map((layer) => ({
                  id: layer.id,
                  label: layer.name,
                  group: layer.band,
                  groupLabel: layer.band_label,
                }))}
              />
              <div className="os-legend">
                {data.bands.map((band) => (
                  <Dot
                    key={band.key}
                    tone="accent"
                    label={
                      <>
                        {band.label}{' '}
                        <span className="os-legend-value">
                          {data.layers.filter((layer) => layer.band === band.key).length}
                        </span>
                      </>
                    }
                  />
                ))}
              </div>
            </>
          )}
        </Guard>
      </Card>

      {/* ---------- 实时任务流 ---------- */}
      <Card
        title="实时任务流"
        sub="真实审计事件（已过滤 kernel 噪声）"
        span={4}
        action={
          <button className="os-link" onClick={activity.reload}>
            刷新
          </button>
        }
      >
        <Guard
          loading={activity.loading}
          error={activity.error}
          data={activity.data}
          isEmpty={() => filteredActivity.length === 0}
          emptyTitle={need ? '没有匹配的事件' : '暂无事件'}
          emptyHint={need ? `没有事件匹配「${query}」` : undefined}
          onRetry={activity.reload}
        >
          {() => (
            <div className="os-list">
              {filteredActivity.map((item) => (
                <div className="os-row" key={`${item.correlation_id}-${item.timestamp}`}>
                  <span
                    className="os-dot"
                    style={{ background: `var(--os-${outcomeTone(item.outcome)})` }}
                  />
                  <div className="os-row-main">
                    <div className="os-row-title">{eventLabel(item.type)}</div>
                    <div className="os-row-sub">
                      {item.principal} · {outcomeLabel(item.outcome)}
                    </div>
                  </div>
                  <span className="os-row-tail">{relativeTime(item.timestamp)}</span>
                </div>
              ))}
            </div>
          )}
        </Guard>
      </Card>

      {/* ---------- AI 大脑地图 ---------- */}
      <Card title="AI 大脑地图" sub="14 内核按职责域分布" span={3}>
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          isEmpty={(data) => data.kernels.length === 0}
          emptyTitle="注册表不可用"
          onRetry={roster.reload}
        >
          {(data) => (
            <>
              <BrainMap
                regions={data.kernels.map((kernel) => ({
                  id: kernel.id,
                  label: kernel.name,
                  group: kernel.domain,
                }))}
              />
              <div className="os-legend">
                {data.domains.map((domain) => (
                  <Dot
                    key={domain.key}
                    tone="accent"
                    label={
                      <>
                        {domain.label}{' '}
                        <span className="os-legend-value">
                          {data.kernels.filter((kernel) => kernel.domain === domain.key).length}
                        </span>
                      </>
                    }
                  />
                ))}
              </div>
            </>
          )}
        </Guard>
      </Card>

      {/* ---------- 审计事件趋势 ---------- */}
      <Card
        title="审计事件趋势"
        sub="近 14 天真实事件数（按天，未做平滑）"
        span={8}
      >
        <Guard
          loading={analytics.loading}
          error={analytics.error}
          data={analytics.data}
          isEmpty={(data) => !data.timeseries.available}
          emptyTitle="时序数据不可用"
          emptyHint={analytics.data?.timeseries.error}
          onRetry={analytics.reload}
        >
          {(data) => {
            const points = data.timeseries.days
            const labels = points.map((point) => point.date.slice(5))
            return (
              <>
                <LineChart
                  labels={labels}
                  series={[
                    {
                      key: 'total',
                      label: '全部事件',
                      values: points.map((point) => point.total),
                      color: '#4d8dff',
                    },
                    {
                      key: 'allowed',
                      label: '放行',
                      values: points.map((point) => point.allowed),
                      color: '#2ee6a8',
                    },
                    {
                      key: 'denied',
                      label: '拒绝',
                      values: points.map((point) => point.denied),
                      color: '#ff5d6c',
                    },
                  ]}
                />
                <div className="os-legend">
                  <Dot tone="accent" label={<>全部事件</>} />
                  <Dot tone="ok" label={<>放行</>} />
                  <Dot tone="danger" label={<>拒绝</>} />
                  {data.timeseries.truncated && (
                    <span className="os-badge" data-tone="warn">
                      样本已截断（{formatCompact(data.timeseries.sampled_events ?? 0)} 条）
                    </span>
                  )}
                </div>
              </>
            )
          }}
        </Guard>
      </Card>

      {/* ---------- 事件类型分布 ---------- */}
      <Card title="事件类型分布" sub="真实审计事件构成" span={4}>
        <Guard
          loading={analytics.loading}
          error={analytics.error}
          data={analytics.data}
          isEmpty={(data) => !data.breakdown.available || data.breakdown.items.length === 0}
          emptyTitle="暂无审计事件"
          emptyHint={
            analytics.data?.breakdown.available
              ? `排除 kernel 噪声 ${formatCompact(analytics.data.breakdown.excluded_noise_events ?? 0)} 条后没有剩余事件`
              : analytics.data?.breakdown.error
          }
          onRetry={analytics.reload}
        >
          {(data) => {
            const top = data.breakdown.items.slice(0, 6)
            const rest = data.breakdown.items.slice(6)
            const restTotal = rest.reduce((sum, item) => sum + item.count, 0)
            const slices = [
              ...top.map((item) => ({
                label: `${eventLabel(item.type)}·${outcomeLabel(item.outcome)}`,
                value: item.count,
              })),
              ...(restTotal > 0 ? [{ label: '其他', value: restTotal }] : []),
            ]
            return (
              <>
                <DonutChart slices={slices} centerLabel="事件" />
                <div className="os-legend">
                  {slices.map((slice, index) => (
                    <span className="os-legend-item" key={slice.label}>
                      <span
                        className="os-dot"
                        style={{ background: chartColor(index) }}
                      />
                      {slice.label}{' '}
                      <span className="os-legend-value">{formatCompact(slice.value)}</span>
                    </span>
                  ))}
                </div>
              </>
            )
          }}
        </Guard>
      </Card>

      {/* ---------- 系统通知 ---------- */}
      <Card title="系统通知" sub="已接入服务面（provider 真实配置状态）" span={12}>
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data?.providers ?? null}
          isEmpty={(data) => data.items.length === 0}
          emptyTitle="没有已注册的 provider"
          onRetry={roster.reload}
        >
          {(data) => (
            <>
              <div className="os-list">
                {data.items.map((item) => (
                  <div className="os-row" key={item.type}>
                    <span
                      className="os-dot"
                      style={{
                        background: item.configured
                          ? 'var(--os-ok)'
                          : item.registered
                            ? 'var(--os-warn)'
                            : 'var(--os-text-faint)',
                      }}
                    />
                    <div className="os-row-main">
                      <div className="os-row-title">
                        {item.label}
                        {item.active && (
                          <>
                            {' '}
                            <span className="os-badge" data-tone="accent">
                              当前生效
                            </span>
                          </>
                        )}
                      </div>
                      <div className="os-row-sub">
                        {item.configured
                          ? '已配置，可用于真实调用'
                          : item.required_env.length > 0
                            ? `缺少密钥：${item.required_env.join(' / ')}`
                            : '无需密钥'}
                      </div>
                    </div>
                    <span className="os-badge" data-tone={item.configured ? 'ok' : 'warn'}>
                      {item.configured ? '就绪' : '未配置'}
                    </span>
                  </div>
                ))}
              </div>
              <div className="os-legend" style={{ marginTop: 10 }}>
                <Dot
                  tone="accent"
                  label={
                    <>
                      当前 provider{' '}
                      <span className="os-legend-value">
                        {roster.data?.providers.active.type ?? '—'}
                      </span>
                    </>
                  }
                />
                <Dot
                  tone="ok"
                  label={
                    <>
                      已配置{' '}
                      <span className="os-legend-value">
                        {data.configured_count}/{data.registered_count}
                      </span>
                    </>
                  }
                />
              </div>
            </>
          )}
        </Guard>
      </Card>
    </div>
  )
}

function chartColor(index: number): string {
  const palette = ['#4d8dff', '#9b6bff', '#2ee6a8', '#ffb74d', '#ff5d6c', '#41c7e0']
  return palette[index % palette.length]
}

function Kpi({
  label,
  value,
  foot,
  error,
  tone,
}: {
  label: string
  value: string | null
  foot: string
  error: string | null
  tone?: 'warn'
}) {
  return (
    <div className="os-kpi">
      <div className="os-kpi-label">{label}</div>
      {value === null ? (
        error ? (
          <div className="os-kpi-value" style={{ fontSize: 14, color: 'var(--os-danger)' }}>
            不可用
          </div>
        ) : (
          <div className="os-skeleton" style={{ height: 30, margin: '6px 0 4px' }} />
        )
      ) : (
        <div
          className="os-kpi-value"
          style={tone === 'warn' ? { color: 'var(--os-warn)' } : undefined}
        >
          {value}
        </div>
      )}
      <div className="os-kpi-foot">{error ? error : foot}</div>
    </div>
  )
}
