/**
 * 总览 —— 设计图的主屏。
 *
 * NO-FAKE 说明：设计图里的四个 KPI 有一个是"今日接入（金额）"。本项目**没有**
 * 真实的资金/账本数据源（Economy 引擎是库不是实时账本，无全局单例），因此这里
 * 换成同样四个位置、但每一个都能追溯到真实来源的指标：
 *
 *   AI 员工总数  ← EmployeeStore 真实持久化员工（totals.employees）；foot 附带内核/能力层注册表说明
 *   运行中任务    ← 网关进程内活跃会话表
 *   审计事件      ← 审计存储真实计数
 *   系统健康度    ← /v1/ready 的全部依赖检查（auth、审计、围栏、排程器等）
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
  PreflightPayload,
  RosterPayload,
} from '../lib/contracts'
import { useApi } from '../lib/api'
import { formatCompact } from '../lib/chartUtils'
import {
  eventLabel,
  numberText,
  outcomeLabel,
  outcomeTone,
  principalLabel,
  relativeTime,
  safeText,
} from '../lib/format'
import { providerRowSubtitle } from '../lib/honesty'
import { capabilityModuleLabel, neutralLayerLabel } from '../lib/layerLabels'
import {
  partitionProviders,
  providerSummary,
  recentRunTitle,
  trendView,
} from '../lib/overviewModel'
import type { ActiveProviderStatus } from '../lib/providerStatus'
import { BrainMap, DonutChart, LineChart, NetworkGraph } from '../components/charts'
import { StatusBand } from '../components/StatusBand'
import { Card, Dot, Guard } from '../components/ui'

const REFRESH_LIVE = 15_000
const REFRESH_SLOW = 60_000

export function Overview({
  query,
  provider,
  onNavigate,
  isRoot = false,
}: {
  query: string
  provider: ActiveProviderStatus
  onNavigate: (key: string) => void
  /** 预检与告警是根账号可见的运维面；成员不请求，也不显示假数字。 */
  isRoot?: boolean
}) {
  const summary = useApi<DashboardSummary>('/v1/dashboard/summary', REFRESH_LIVE)
  const activity = useApi<ActivityPayload>('/v1/dashboard/activity?limit=30', REFRESH_LIVE)
  const roster = useApi<RosterPayload>('/v1/dashboard/roster', REFRESH_SLOW)
  const analytics = useApi<AnalyticsPayload>('/v1/dashboard/analytics?days=14', REFRESH_SLOW)
  // 503 也要接受：降级时响应里带着完整的检查项，那正是健康度最该显示真话的时刻。
  const ready = useApi<HealthPayload>('/v1/ready', 30_000, [503])
  // 健康度必须和「预检」「告警」说同一套话：只读就绪探针会出现
  // “总览 100% 健康、预检 SECRET_KEY critical、告警 74 条正在触发”。
  const preflight = useApi<PreflightPayload>(
    isRoot ? '/v1/production/preflight' : null,
    REFRESH_SLOW,
  )
  const alerts = useApi<{ firing_count?: number; count?: number }>(
    isRoot ? '/v1/alerts' : null,
    REFRESH_SLOW,
  )

  const need = query.trim().toLowerCase()

  const filteredActivity = useMemo(() => {
    const items = activity.data?.activity ?? []
    if (!need) return items.slice(0, 8)
    return items
      .filter(
        (item) =>
          safeText(item.principal).toLowerCase().includes(need) ||
          eventLabel(item.type).toLowerCase().includes(need) ||
          safeText(item.type).toLowerCase().includes(need) ||
          outcomeLabel(item.outcome).toLowerCase().includes(need),
      )
      .slice(0, 8)
  }, [activity.data, need])

  const health = useMemo(() => {
    const checks = ready.data?.checks ?? {}
    const names = Object.keys(checks)
    if (names.length === 0) return null
    // 告警级检查（``blocks_readiness=false``，如指标链路、非关键内核）不参与
    // “依赖正常”的分子/分母，否则会把监控失明算成业务不就绪；它们单独展示。
    const blockingNames = names.filter(
      (name) => checks[name]?.blocks_readiness !== false,
    )
    const healthy = blockingNames.filter((name) => checks[name]?.status === 'healthy').length
    const advisories = names.filter(
      (name) =>
        checks[name]?.blocks_readiness === false &&
        checks[name]?.status !== 'healthy',
    )
    // 预检与告警各算一项“子系统”：critical>0 / firing>0 就是异常。
    // 没读到的来源（成员无权限、请求失败）不计入分母，避免把未知当成健康。
    const critical = preflight.data?.critical_count
    const warning = preflight.data?.warning_count
    const firing = alerts.data?.firing_count
    const preflightKnown = typeof critical === 'number'
    const alertsKnown = typeof firing === 'number'
    const extraTotal = (preflightKnown ? 1 : 0) + (alertsKnown ? 1 : 0)
    const extraHealthy =
      (preflightKnown && critical === 0 ? 1 : 0) + (alertsKnown && firing === 0 ? 1 : 0)
    const total = blockingNames.length + extraTotal
    const healthyTotal = healthy + extraHealthy
    return {
      healthy: healthyTotal,
      readyHealthy: healthy,
      readyTotal: blockingNames.length,
      total,
      ratio: healthyTotal / total,
      critical: preflightKnown ? critical : null,
      warning: preflightKnown ? warning ?? 0 : null,
      firing: alertsKnown ? firing : null,
      preflightError: preflight.error,
      alertsError: alerts.error,
      // 告警级问题单独列在 foot 里，不能因为不参与百分比就彻底隐身。
      advisories: advisories.map((name) =>
        safeText(checks[name]?.display_status) || name,
      ),
      // 审计证据链的判据独立于可用性。分母计入它，这样"依赖全绿但证据链从未被
      // 重新推导"不会显示成 100%——那正是本缺陷在控制台上的原样呈现。
      auditEvidence: checks.audit_store?.evidence_status as
        | 'verified'
        | 'unverified'
        | 'pending'
        | 'broken'
        | 'unknown'
        | undefined,
      auditUncovered: checks.audit_store?.uncovered_events as number | undefined,
    }
  }, [ready.data, preflight.data, preflight.error, alerts.data, alerts.error])

  const attention = useMemo(() => {
    const checks = ready.data?.checks ?? {}
    const degraded = Object.keys(checks).filter(
      (name) => checks[name]?.status !== 'healthy',
    ).length
    const recent = activity.data?.activity ?? []
    const failed = recent.filter(
      (item) =>
        item.outcome === 'error' ||
        item.outcome === 'failure' ||
        item.outcome === 'failed' ||
        item.outcome === 'deny',
    ).length
    return {
      degraded,
      failed,
      recent: recent.length,
      modelReady: provider.isReal,
      // 预检/告警与就绪探针是三条独立证据；总览必须同时展示，不能只报一条。
      critical: health?.critical ?? null,
      firing: health?.firing ?? null,
      warning: health?.warning ?? null,
      preflightError: health?.preflightError ?? '',
      alertsError: health?.alertsError ?? '',
    }
  }, [ready.data, activity.data, provider.isReal, health])

  return (
    <div className="os-grid">
      {/* ---------- 今天的状态 ----------
          首屏只回答三个问题：现在在跑什么、什么卡在等我、最近跑成了什么。
          数据只来自 /v1/goals 与 /v1/policy/approvals/pending，30 秒统一刷新；
          三行各自有空态与失败态，任何一行读不到都不会连累另外两行。 */}
      <StatusBand onNavigate={onNavigate} />

      {/* ---------- 需要我 ----------
          只用可追溯的真实信号：/v1/ready 的异常检查、近 30 条审计里的失败/拒绝、
          /v1/health 的模型真实性。没有"待审批"数据源就不编造它。 */}
      <div className="os-span-12">
        <div className="os-attention" role="status" aria-label="需要我关注">
          <span className="os-attention-lead">需要我</span>
          <span
            className="os-attention-item"
            data-tone={attention.degraded > 0 ? 'warn' : 'ok'}
          >
            <strong>{ready.data ? attention.degraded : '—'}</strong>
            项依赖异常
          </span>
          <span
            className="os-attention-item"
            data-tone={attention.failed > 0 ? 'warn' : 'ok'}
          >
            <strong>{activity.data ? attention.failed : '—'}</strong>
            条失败/拒绝（
            {attention.recent > 0 ? `近 ${attention.recent} 条事件` : '暂无事件'}）
          </span>
          {isRoot && (
            <span
              className="os-attention-item"
              data-tone={attention.critical ? 'warn' : 'ok'}
            >
              <strong>{attention.critical === null ? '—' : attention.critical}</strong>
              条预检严重项
            </span>
          )}
          {isRoot && (
            <span
              className="os-attention-item"
              data-tone={attention.firing ? 'warn' : 'ok'}
            >
              <strong>{attention.firing === null ? '—' : attention.firing}</strong>
              条告警正在触发
            </span>
          )}
          <span
            className="os-attention-item"
            data-tone={attention.modelReady ? 'ok' : 'warn'}
          >
            <strong>{attention.modelReady ? '真实' : '模拟'}</strong>
            模型状态
          </span>
          {ready.error || activity.error || attention.preflightError || attention.alertsError ? (
            <span className="os-attention-clear">
              状态没读全，暂时不能判断是否无异常；看下方对应卡片的重试。
            </span>
          ) : ready.data === null || activity.data === null ? (
            <span className="os-attention-clear" role="status">
              正在检查依赖与近期失败…
            </span>
          ) : (
            attention.degraded === 0 &&
            attention.failed === 0 &&
            attention.modelReady &&
            attention.critical === 0 &&
            attention.firing === 0 && (
              <span className="os-attention-clear">
                当前无异常
                {attention.recent === 0
                  ? ' · 下一步：到「自由对话」问一句，或到「系统设置 → 模型管理」确认服务连通。'
                  : ' · 下一步：可在事件流查看明细。'}
              </span>
            )
          )}
        </div>
      </div>
      {/* ---------- 第一次用：首屏直接给"下一步点哪里" ----------
          只在确实缺东西时出现（没有真实模型 / 还没有员工），不打扰日常使用。 */}
      {(() => {
        const employees = roster.data?.totals.employees ?? null
        const firstRun = !provider.isReal || employees === 0
        if (!firstRun) return null
        return (
          <div className="os-span-12">
            <div className="os-attention" role="status" aria-label="第一次使用">
              <span className="os-attention-lead">第一次用</span>
              <span className="os-attention-item">
                ①{' '}
                {provider.isReal ? (
                  '模型已就绪'
                ) : (
                  <button type="button" className="os-link" onClick={() => onNavigate('settings')}>
                    配置模型
                  </button>
                )}
              </span>
              <span className="os-attention-item">
                ②{' '}
                {employees === 0 ? (
                  <button
                    type="button"
                    className="os-link"
                    onClick={() => onNavigate('my-employees')}
                  >
                    创建第一位员工
                  </button>
                ) : employees === null ? (
                  '检查员工名册'
                ) : (
                  '员工已就绪'
                )}
              </span>
              <span className="os-attention-item">
                ③{' '}
                <button type="button" className="os-link" onClick={() => onNavigate('knowledge')}>
                  导入第一份知识
                </button>
              </span>
              <span className="os-attention-item">
                也可以直接去
                <button type="button" className="os-link" onClick={() => onNavigate('butler')}>
                  「贾维斯」
                </button>
                说一句话派活
              </span>
            </div>
          </div>
        )
      })()}
      {/* ---------- KPI ---------- */}
      <div className="os-span-12">
        <div className="os-kpis">
          <Kpi
            label="AI 员工总数"
            value={roster.data ? String(roster.data.totals.employees) : null}
            foot={
              roster.data
                ? `${roster.data.totals.kernels} 个能力模块 + ${roster.data.totals.layers} 个能力层`
                : '来自能力注册表'
            }
            error={roster.error}
          />
          <Kpi
            label="运行中任务"
            value={summary.data?.sessions ? numberText(summary.data.sessions.count) : null}
            foot="当前活跃会话"
            error={summary.error}
          />
          <Kpi
            label="审计事件"
            value={
              summary.data?.audit ? formatCompact(summary.data.audit.total_events) : null
            }
            foot={
              summary.data?.audit
                ? `全部存储 ${numberText(summary.data.audit.total_events)} · 业务事件 ${numberText(
                    summary.data.audit.business_events,
                  )} · 你可读 ${numberText(summary.data.audit.visible_events)}`
                : '审计存储累计'
            }
            error={summary.error}
          />
          <Kpi
            label="系统健康度"
            value={
              health && Number.isFinite(health.ratio)
                ? `${numberText(health.ratio * 100)}%`
                : null
            }
            foot={
              health
                ? [
                    `${health.readyHealthy}/${health.readyTotal} 项依赖正常`,
                    health.critical === null ? null : `预检严重 ${health.critical}`,
                    health.firing === null ? null : `告警触发 ${health.firing}`,
                    health.advisories.length > 0
                      ? `告警：${health.advisories.join('、')}`
                      : null,
                    health.auditEvidence === 'broken'
                      ? `证据链已断裂（${health.auditUncovered ?? '?'} 条未覆盖）`
                      : health.auditEvidence === 'pending'
                        ? `${health.auditUncovered ?? '?'} 条新事件待核对`
                        : health.auditEvidence && health.auditEvidence !== 'verified'
                          ? `证据链未重新推导（${health.auditUncovered ?? '?'} 条未覆盖）`
                          : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')
                : ready.httpStatus === 503
                  ? '服务正在准备，自检完成后自动更新'
                  : '来自就绪探针'
            }
            error={ready.error || preflight.error || alerts.error}
            tone={
              health &&
              (health.ratio < 1 ||
                health.advisories.length > 0 ||
                health.auditEvidence === 'broken' ||
                (health.critical ?? 0) > 0 ||
                (health.firing ?? 0) > 0)
                ? 'warn'
                : undefined
            }
          />
        </div>
      </div>

      {/* ---------- 能力层分布 ---------- */}
      <Card
        title="能力层分布"
        sub="能力模块按阶段分组；悬停查看名称"
        span={5}
      >
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          isEmpty={(data) => data.layers.length === 0}
          emptyTitle="注册表不可用"
          emptyHint="能力注册表未能解析"
          onRetry={roster.reload}
        >
          {(data) => (
            <>
              <NetworkGraph
                hubLabel="鎏灏 OS"
                nodes={data.layers.map((layer) => ({
                  id: layer.id,
                  label: neutralLayerLabel(layer.name),
                  group: layer.band,
                  groupLabel: neutralLayerLabel(layer.band_label),
                }))}
              />
              <div className="os-legend">
                {data.bands.map((band) => (
                  <Dot
                    key={band.key}
                    tone="accent"
                    label={
                      <>
                        {neutralLayerLabel(band.label)}{' '}
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
        title={recentRunTitle(filteredActivity)}
        sub="最近的真实运行记录（已隐藏系统内部名称）"
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
          emptyTitle={need ? '没有匹配的事件' : '暂无运行记录'}
          emptyHint={
            need
              ? `没有事件匹配「${query}」`
              : '这里会显示员工最近做过的事。下一步：到「自由对话」问一句。'
          }
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
                      {principalLabel(item.principal)} · {outcomeLabel(item.outcome)}
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
      <Card title="AI 能力全景" sub="按职责分组的 14 个能力模块；悬停查看名称" span={3}>
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
                  label: capabilityModuleLabel(kernel.kernel, kernel.name),
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
                        {neutralLayerLabel(domain.label)}{' '}
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
            const view = trendView(data.timeseries.days)
            // 稀疏数据不画贴地线：一条几乎为 0 的线会被读成"系统很安静"，
            // 而事实是"还没有足够证据"。空状态必须把下一步说出来。
            if (view.sparse) {
              return (
                <div className="os-empty" role="status">
                  <div className="os-empty-title">{view.emptyTitle}</div>
                  <div className="os-empty-hint">{view.emptyHint}</div>
                </div>
              )
            }
            return (
              <>
                <div className="os-hud">
                  <LineChart labels={view.labels} series={view.series} />
                </div>
                <div className="os-legend">
                  {view.series.map((series) => (
                    <span className="os-legend-item" key={series.key}>
                      <span className="os-dot" style={{ background: series.color }} />
                      {series.label}
                    </span>
                  ))}
                  {data.timeseries.truncated && (
                    <span className="os-badge" data-tone="warn">
                      样本已截断（{formatCompact(data.timeseries.sampled_events)} 条）
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
              ? `排除系统内部记录 ${formatCompact(analytics.data.breakdown.excluded_noise_events)} 条后没有剩余事件`
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

      {/* ---------- 模型服务商连通状态 ----------
          标题必须如实描述卡片内容：这里列出的是"哪些服务商配了、哪家正在生效"，
          与"系统通知"无关。未配置项默认折叠，避免把真正需要注意的两条挤下去。 */}
      <Card
        title="模型服务商连通状态"
        sub="已配置优先；未配置的服务商折叠在下方"
        span={12}
      >
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data?.providers ?? null}
          isEmpty={(data) => data.items.length === 0}
          emptyTitle="没有登记的模型服务商"
          emptyHint="到「系统设置 → 模型管理」添加服务商并测试连通后，这里会显示连通状态。"
          onRetry={roster.reload}
        >
          {(data) => {
            // 真实性判据**只**来自 /v1/health（见 lib/providerStatus.ts）。名册这里
            // 只负责"每一项配没配密钥"以及"哪一项是当前生效的"两份事实。
            const sections = partitionProviders(data.items, provider.provider?.type)
            const prominent = [
              ...(sections.active ? [sections.active] : []),
              ...sections.configured,
            ]
            const renderRow = (item: (typeof data.items)[number], isActive: boolean) => {
              const dotTone = !item.configured
                ? item.registered
                  ? 'var(--os-warn)'
                  : 'var(--os-text-faint)'
                : isActive
                  ? provider.isReal
                    ? 'var(--os-ok)'
                    : 'var(--os-danger)'
                  : 'var(--os-text-dim)'
              const badgeTone = !item.configured
                ? 'warn'
                : isActive
                  ? provider.posture.tone
                  : 'muted'
              return (
                <div className="os-row" key={item.type}>
                  <span className="os-dot" style={{ background: dotTone }} />
                  <div className="os-row-main">
                    <div className="os-row-title">
                      {item.label}
                      {isActive && (
                        <>
                          {' '}
                          <span className="os-badge" data-tone="accent">
                            当前生效
                          </span>
                        </>
                      )}
                      {item.is_mock && (
                        <>
                          {' '}
                          <span className="os-badge" data-tone="danger">
                            模拟
                          </span>
                        </>
                      )}
                    </div>
                    <div className="os-row-sub">
                      {item.configured && !isActive
                        ? '已配置，但当前未生效'
                        : providerRowSubtitle({
                            configured: item.configured,
                            requiredEnv: item.required_env,
                            // 真实性只说一次、且只说当前生效服务商的那份。
                            posture: provider.posture,
                          })}
                    </div>
                  </div>
                  <span className="os-badge" data-tone={badgeTone}>
                    {!item.configured
                      ? '未配置'
                      : isActive
                        ? provider.posture.badge
                        : '已配置'}
                  </span>
                </div>
              )
            }
            return (
              <>
                <div className="os-list">
                  {prominent.map((item) =>
                    renderRow(item, item.type === provider.provider?.type),
                  )}
                </div>
                {sections.unconfigured.length > 0 && (
                  <details className="os-fold">
                    <summary>
                      未配置 {sections.unconfigured.length} 家 · 展开
                    </summary>
                    <div className="os-list">
                      {sections.unconfigured.map((item) => renderRow(item, false))}
                    </div>
                  </details>
                )}
                <div className="os-legend" style={{ marginTop: 10 }}>
                  <Dot
                    tone="accent"
                    label={
                      <>
                        当前服务商{' '}
                        <span className="os-legend-value">
                          {provider.provider?.type ?? '—'}
                        </span>
                      </>
                    }
                  />
                  <Dot
                    tone={provider.posture.tone}
                    label={
                      <>
                        {provider.isReal ? '真实调用' : '非真实调用'} ·{' '}
                        <span className="os-legend-value">{provider.posture.badge}</span>
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
                <p className="os-note" style={{ marginTop: 8 }}>
                  <span>{providerSummary(sections)}。{provider.detail}</span>
                </p>
              </>
            )
          }}
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
