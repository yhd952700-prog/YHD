/**
 * 运维类页面：数据中心 / 系统状态。
 *
 * 这两页的定位是"可核对"：数据中心展开的是审计存储的真实内容，系统状态展开的是
 * 依赖探针与安全组件的真实状态。任何一项拿不到数据都如实显示原因，不用 0 或
 * "正常"占位。
 */

import { useMemo } from 'react'
import type {
  AnalyticsPayload,
  DashboardSummary,
  EnforcementPayload,
  HealthPayload,
  RosterPayload,
} from '../lib/contracts'
import type { AuthConfig } from '../lib/auth'
import { useApi } from '../lib/api'
import { formatCompact } from '../lib/chartUtils'
import { eventLabel, humanDuration, outcomeLabel, outcomeTone } from '../lib/format'
import { DonutChart, LineChart } from '../components/charts'
import { Card, Dot, Guard } from '../components/ui'

const REFRESH = 30_000

// ---------------------------------------------------------------------------
// 数据中心
// ---------------------------------------------------------------------------

export function DataCenter({ query }: { query: string }) {
  const analytics = useApi<AnalyticsPayload>('/v1/dashboard/analytics?days=30', 60_000)
  const summary = useApi<DashboardSummary>('/v1/dashboard/summary', REFRESH)
  const need = query.trim().toLowerCase()

  const points = analytics.data?.timeseries.days ?? []

  const breakdownRows = useMemo(() => {
    const items = analytics.data?.breakdown.items ?? []
    if (!need) return items
    return items.filter(
      (item) =>
        item.type.toLowerCase().includes(need) ||
        eventLabel(item.type).toLowerCase().includes(need) ||
        item.outcome.toLowerCase().includes(need),
    )
  }, [analytics.data, need])

  const sessions = summary.data?.sessions?.detail ?? []
  const filteredSessions = need
    ? sessions.filter((session) => session.id.toLowerCase().includes(need))
    : sessions

  return (
    <div className="os-grid">
      <Card title="审计事件趋势" sub="近 30 天，按天，未做平滑或补齐" span={12}>
        <Guard
          loading={analytics.loading}
          error={analytics.error}
          data={analytics.data}
          isEmpty={(data) => !data.timeseries.available || data.timeseries.days.length === 0}
          emptyTitle="时序数据不可用"
          emptyHint={analytics.data?.timeseries.error}
          onRetry={analytics.reload}
        >
          {() => (
            <>
              <LineChart
                height={210}
                labels={points.map((point) => point.date.slice(5))}
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
                <Dot tone="accent" label="全部事件" />
                <Dot tone="ok" label="放行" />
                <Dot tone="danger" label="拒绝" />
                <Dot
                  tone="muted"
                  label={
                    <>
                      内核噪声（未计入）{' '}
                      <span className="os-legend-value">
                        {formatCompact(points.reduce((sum, point) => sum + point.noise, 0))}
                      </span>
                    </>
                  }
                />
              </div>
            </>
          )}
        </Guard>
      </Card>

      <Card title="事件构成" sub="真实 event_type × outcome 分布" span={4}>
        <Guard
          loading={analytics.loading}
          error={analytics.error}
          data={analytics.data}
          isEmpty={(data) => !data.breakdown.available || data.breakdown.items.length === 0}
          emptyTitle="暂无事件"
          emptyHint={analytics.data?.breakdown.error}
          onRetry={analytics.reload}
        >
          {(data) => {
            const top = data.breakdown.items.slice(0, 7)
            return (
              <>
                <DonutChart
                  slices={top.map((item) => ({
                    label: eventLabel(item.type),
                    value: item.count,
                  }))}
                  centerValue={formatCompact(data.breakdown.total_events ?? 0)}
                  centerLabel="总事件"
                />
                <div className="os-legend">
                  {top.map((item, index) => (
                    <span className="os-legend-item" key={`${item.type}-${item.outcome}`}>
                      <span
                        className="os-dot"
                        style={{ background: palette(index) }}
                      />
                      {eventLabel(item.type)}·{outcomeLabel(item.outcome)}{' '}
                      <span className="os-legend-value">{formatCompact(item.count)}</span>
                    </span>
                  ))}
                </div>
                <div className="os-note" style={{ marginTop: 12 }}>
                  <span>
                    已排除 kernel 噪声 <strong>{formatCompact(data.breakdown.excluded_noise_events ?? 0)}</strong> 条
                    （state_change，占比通常 &gt;99%，对业务无意义）。
                  </span>
                </div>
              </>
            )
          }}
        </Guard>
      </Card>

      <Card title="事件明细" sub="按真实条数排序" span={8}>
        <Guard
          loading={analytics.loading}
          error={analytics.error}
          data={analytics.data}
          isEmpty={() => breakdownRows.length === 0}
          emptyTitle={need ? '没有匹配的事件类型' : '暂无事件'}
          onRetry={analytics.reload}
        >
          {() => (
            <div className="os-list">
              {breakdownRows.map((item) => {
                const max = breakdownRows[0]?.count || 1
                return (
                  <div className="os-row" key={`${item.type}-${item.outcome}`}>
                    <span
                      className="os-dot"
                      style={{ background: `var(--os-${outcomeTone(item.outcome)})` }}
                    />
                    <div className="os-row-main">
                      <div className="os-row-title">{eventLabel(item.type)}</div>
                      <div className="os-row-sub">{outcomeLabel(item.outcome)}</div>
                      <div className="os-bar">
                        <div
                          className="os-bar-fill"
                          style={{ width: `${Math.max(2, (item.count / max) * 100)}%` }}
                        />
                      </div>
                    </div>
                    <span className="os-row-tail">{formatCompact(item.count)}</span>
                  </div>
                )
              })}
            </div>
          )}
        </Guard>
      </Card>

      <Card title="会话轮次" sub="真实对话会话及其轮次" span={12}>
        <Guard
          loading={summary.loading}
          error={summary.error}
          data={summary.data}
          isEmpty={() => filteredSessions.length === 0}
          emptyTitle={need ? '没有匹配的会话' : '当前没有活跃会话'}
          emptyHint={need ? undefined : '发起一次对话后这里会出现真实会话'}
          onRetry={summary.reload}
        >
          {() => (
            <div className="os-list">
              {filteredSessions.map((session) => (
                <div className="os-row" key={session.id}>
                  <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                  <div className="os-row-main">
                    <div className="os-row-title">{session.id}</div>
                    <div className="os-row-sub">会话标识</div>
                  </div>
                  <span className="os-row-tail">{session.turns} 轮</span>
                </div>
              ))}
            </div>
          )}
        </Guard>
      </Card>
    </div>
  )
}

function palette(index: number): string {
  const colors = ['#4d8dff', '#9b6bff', '#2ee6a8', '#ffb74d', '#ff5d6c', '#41c7e0', '#f06292']
  return colors[index % colors.length]
}

// ---------------------------------------------------------------------------
// 系统状态
// ---------------------------------------------------------------------------

interface MetricsPayload {
  uptime_seconds?: number
  components?: {
    api_key_manager?: {
      total_keys?: number
      by_status?: Record<string, number>
      expired_count?: number
    }
    rbac_manager?: { total_roles?: number }
    rate_limiter?: { active_buckets?: number }
  }
}

export function SystemStatus() {
  // 503 必须接受：降级响应用的是同一个 body，丢掉它就等于把最关键的信息丢了。
  const ready = useApi<HealthPayload>('/v1/ready', REFRESH, [503])
  const metrics = useApi<MetricsPayload>('/v1/metrics', 60_000)
  const enforcement = useApi<EnforcementPayload>('/v1/policy/enforcement', 60_000)
  const roster = useApi<RosterPayload>('/v1/dashboard/roster', 60_000)
  const authConfig = useApi<AuthConfig>('/v1/auth/config', 0)

  return (
    <div className="os-grid">
      <Card title="依赖健康" sub="来自就绪探针的真实五项检查" span={7}>
        <Guard
          loading={ready.loading}
          error={ready.error}
          data={ready.data}
          isEmpty={(data) => Object.keys(data.checks ?? {}).length === 0}
          emptyTitle="探针没有返回检查项"
          onRetry={ready.reload}
        >
          {(data) => (
            <>
              <div className="os-list">
                {Object.entries(data.checks ?? {}).map(([name, detail]) => {
                  const healthy = detail?.status === 'healthy'
                  return (
                    <div className="os-row" key={name}>
                      <span
                        className="os-dot"
                        style={{ background: healthy ? 'var(--os-ok)' : 'var(--os-danger)' }}
                      />
                      <div className="os-row-main">
                        <div className="os-row-title">{name}</div>
                        {!healthy && detail?.error && (
                          <div className="os-row-sub">{detail.error}</div>
                        )}
                      </div>
                      <span className="os-badge" data-tone={healthy ? 'ok' : 'danger'}>
                        {detail?.status ?? '未知'}
                      </span>
                    </div>
                  )
                })}
              </div>
              {data.status === 'degraded' && (
                <div className="os-alert" style={{ marginTop: 12, marginBottom: 0 }}>
                  <span>
                    服务处于降级状态，就绪探针返回 503。上面标红的就是原因。
                  </span>
                </div>
              )}
            </>
          )}
        </Guard>
      </Card>

      <Card title="运行指标" sub="真实组件计数与进程时长" span={5}>
        <Guard
          loading={metrics.loading}
          error={metrics.error}
          data={metrics.data}
          emptyTitle="指标不可用"
          onRetry={metrics.reload}
        >
          {(data) => (
            <dl className="os-kv">
              <dt>运行时长</dt>
              <dd>{humanDuration(data.uptime_seconds ?? 0)}</dd>
              <dt>API 密钥总数</dt>
              <dd>{data.components?.api_key_manager?.total_keys ?? 0}</dd>
              <dt>已过期密钥</dt>
              <dd>{data.components?.api_key_manager?.expired_count ?? 0}</dd>
              <dt>RBAC 角色数</dt>
              <dd>{data.components?.rbac_manager?.total_roles ?? 0}</dd>
              <dt>限流桶</dt>
              <dd>{data.components?.rate_limiter?.active_buckets ?? 0}</dd>
            </dl>
          )}
        </Guard>
      </Card>

      <Card title="拦截配置" sub="Policy 内核动作拦截（需人类令牌）" span={6}>
        <Guard
          loading={enforcement.loading}
          error={enforcement.error}
          data={enforcement.data}
          emptyTitle="无法读取拦截配置"
          emptyHint="该端点要求有效的登录令牌"
          onRetry={enforcement.reload}
        >
          {(data) => (
            <dl className="os-kv">
              <dt>拦截开关</dt>
              <dd>
                <span className="os-badge" data-tone={data.enabled ? 'danger' : 'muted'}>
                  {data.enabled ? '已开启' : '未开启（记录型）'}
                </span>
              </dd>
              <dt>环境变量</dt>
              <dd>
                <code>{data.env_var ?? '—'}</code>
              </dd>
              <dt>生效等级</dt>
              <dd>
                {Array.isArray(data.enforced_tiers) && data.enforced_tiers.length > 0
                  ? (data.enforced_tiers as string[]).join(' / ')
                  : '—'}
              </dd>
            </dl>
          )}
        </Guard>
      </Card>

      <Card title="身份与会话" sub="登录通道的真实状态" span={6}>
        <Guard
          loading={authConfig.loading}
          error={authConfig.error}
          data={authConfig.data}
          emptyTitle="无法读取登录配置"
          onRetry={authConfig.reload}
        >
          {(data) => (
            <dl className="os-kv">
              <dt>凭据存储</dt>
              <dd>
                <span className="os-badge" data-tone={data.secret_store.configured ? 'ok' : 'warn'}>
                  {data.secret_store.configured ? '已配置' : '未配置'}
                </span>
              </dd>
              <dt>已登记人类</dt>
              <dd>{data.registered_humans}</dd>
              <dt>可登录主体</dt>
              <dd>{data.login_eligible_humans}</dd>
              <dt>会话有效期</dt>
              <dd>{humanDuration(data.token.ttl_seconds)}</dd>
              <dt>第三方登录</dt>
              <dd>
                {Object.entries(data.oauth).every(([, enabled]) => !enabled)
                  ? '未接入（仅账号密码）'
                  : Object.entries(data.oauth)
                      .filter(([, enabled]) => enabled)
                      .map(([name]) => name)
                      .join(' / ')}
              </dd>
            </dl>
          )}
        </Guard>
      </Card>

      <Card title="注册表快照" sub="当前生效的能力注册表" span={12}>
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          emptyTitle="注册表不可用"
          onRetry={roster.reload}
        >
          {(data) => (
            <dl className="os-kv">
              <dt>来源文件</dt>
              <dd>
                <code>{data.source ?? '—'}</code>
              </dd>
              <dt>版本</dt>
              <dd>{data.version ?? '—'}</dd>
              <dt>内核 / 能力层</dt>
              <dd>
                {data.totals.kernels} / {data.totals.layers}
              </dd>
              <dt>声明总数</dt>
              <dd>
                {data.declared_totals?.capabilities ?? '—'} / {data.declared_totals?.layers ?? '—'}
              </dd>
              <dt>声明测试用例</dt>
              <dd>{formatCompact(data.totals.declared_test_cases)}</dd>
              <dt>当前 provider</dt>
              <dd>{data.providers.active?.type ?? '—'}</dd>
            </dl>
          )}
        </Guard>
      </Card>

      <SystemAlerts />
    </div>
  )
}

// ─── 运维告警（只读读取面） ─────────────────────────────────
//
// 端点 `/v1/alerts` 与 `/v1/alerts/rules` 由 `src/gateway/observability.py` 暴露，
// 只读，需人类令牌。这是把"只写不读"的告警库变成操作员可见的关键一步：
// 执行失败告警（真实路径）此前只落日志/JSON，没有任何 API 或 UI 能读取。
//
// 诚实优先：
// * firing_count=0 时如实显示"当前没有正在触发的告警"，不伪装成"一切正常"的绿勾。
// * 规则注册表如实展示：系统健康规则当前全部停用（对应指标发射器尚未接线，启用即
//   装饰性失效）；执行失败告警规则已启用（真实可用路径）。不美化这一分裂。

interface AlertItem {
  id: string
  name: string
  severity: string
  state: string
  source: string
  message: string
  fired_at: number | null
}

interface AlertsResponse {
  count: number
  firing_count: number
  alerts: AlertItem[]
}

interface AlertRuleItem {
  id: string
  name: string
  enabled: boolean
  severity: string
  metric_name: string | null
}

interface AlertRulesResponse {
  count: number
  rules: AlertRuleItem[]
}

function SystemAlerts() {
  const alerts = useApi<AlertsResponse>('/v1/alerts', REFRESH)
  const rules = useApi<AlertRulesResponse>('/v1/alerts/rules', REFRESH)

  const firing = (alerts.data?.alerts ?? []).filter((a) => a.state === 'FIRING')
  const disabledHealth = (rules.data?.rules ?? []).filter((r) => !r.enabled)
  const enabledExec = (rules.data?.rules ?? []).filter((r) => r.enabled)

  return (
    <Card
      title="运维告警"
      sub="真实告警库只读视图（执行失败告警为真实可用路径）"
      span={12}
    >
      <Guard
        loading={alerts.loading}
        error={alerts.error}
        data={alerts.data}
        emptyTitle="告警库为空"
        emptyHint="还没有任何告警被记录；执行失败时这里会出现条目。"
        onRetry={alerts.reload}
      >
        {(data) => (
          <>
            <div className="os-kv">
              <div className="os-kv-row">
                <span className="os-kv-key">正在触发</span>
                <span className="os-kv-value">
                  <span
                    className={`os-dot os-tone-${data.firing_count > 0 ? 'danger' : 'ok'}`}
                  />
                  {data.firing_count}
                </span>
              </div>
              <div className="os-kv-row">
                <span className="os-kv-key">历史告警总数</span>
                <span className="os-kv-value">{data.count}</span>
              </div>
            </div>

            {firing.length === 0 ? (
              <p className="os-hint">当前没有正在触发的告警。</p>
            ) : (
              <div className="os-table-wrap" style={{ marginTop: 12 }}>
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>严重度</th>
                      <th>名称</th>
                      <th>来源</th>
                      <th>信息</th>
                    </tr>
                  </thead>
                  <tbody>
                    {firing.map((a) => (
                      <tr key={a.id}>
                        <td>
                          <span className={`os-dot os-tone-${a.severity === 'CRITICAL' || a.severity === 'HIGH' ? 'danger' : 'warn'}`} />
                          {a.severity}
                        </td>
                        <td>{a.name}</td>
                        <td>{a.source || '—'}</td>
                        <td>{a.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {rules.data && (
              <p className="os-hint" style={{ marginTop: 12 }}>
                规则注册表：{disabledHealth.length} 条系统健康规则全部停用（指标发射器未接线，启用即失效）；
                {enabledExec.length} 条执行失败告警规则已启用（真实可用）。
              </p>
            )}
          </>
        )}
      </Guard>
    </Card>
  )
}
