/**
 * 目录类页面：我AI员工 / 业务中心 / 知识中心。
 *
 * 三者都是**同一份能力注册表**的不同切面：
 *   - 我AI员工  → 14 内核 + 14 能力层 + provider 服务面（全量）
 *   - 业务中心  → 14 能力层（= 系统实际具备的业务能力），按阶段带分组
 *   - 知识中心  → 与知识/记忆相关的内核 + OpenAPI 里真实的 /knowledge/* 端点
 *
 * 没有数据源的地方如实说明，不用占位条目撑满表格。
 */

import { useMemo, useState } from 'react'
import type { RosterPayload, EmployeesPayload, GoalsPayload } from '../lib/contracts'
import { useApi } from '../lib/api'
import { colorAt } from '../lib/chartUtils'
import { BrainMap, NetworkGraph } from '../components/charts'
import { Card, Dot, Empty, Guard } from '../components/ui'

type OpenApiPaths = { paths?: Record<string, Record<string, unknown>> }

const STATUS_TONE: Record<string, string> = {
  idle: 'ok',
  running: 'accent',
  paused: 'muted',
  stopped: 'danger',
  completed: 'ok',
  failed: 'danger',
}

export function Roster({ query }: { query: string }) {
  const roster = useApi<RosterPayload>('/v1/dashboard/roster', 60_000)
  const employees = useApi<EmployeesPayload>('/v1/employees', 30_000)
  const goals = useApi<GoalsPayload>('/v1/goals', 30_000)
  const [tab, setTab] = useState<'kernels' | 'layers' | 'runtime'>('kernels')

  const need = query.trim().toLowerCase()

  const kernels = useMemo(() => {
    const hit = (text: string) => !need || text.toLowerCase().includes(need)
    return (roster.data?.kernels ?? []).filter(
      (kernel) =>
        hit(kernel.name) || hit(kernel.id) || hit(kernel.kernel) || hit(kernel.domain_label),
    )
  }, [roster.data, need])

  const layers = useMemo(() => {
    const hit = (text: string) => !need || text.toLowerCase().includes(need)
    return (roster.data?.layers ?? []).filter(
      (layer) =>
        hit(layer.name) || hit(layer.id) || hit(layer.module ?? '') || hit(layer.band_label),
    )
  }, [roster.data, need])

  return (
    <div className="os-grid">
      <Card title="协作网络" sub="能力层按阶段带分布" span={5}>
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          isEmpty={(data) => data.layers.length === 0}
          emptyTitle="注册表不可用"
          onRetry={roster.reload}
        >
          {(data) => (
            <>
              <NetworkGraph
                hubLabel="鎏灏 OS"
                height={260}
                nodes={data.layers.map((layer) => ({
                  id: layer.id,
                  label: layer.name,
                  group: layer.band,
                }))}
              />
              <div className="os-legend">
                {data.bands.map((band, index) => (
                  <span className="os-legend-item" key={band.key}>
                    <span className="os-dot" style={{ background: colorAt(index) }} />
                    {band.label}{' '}
                    <span className="os-legend-value">
                      {data.layers.filter((layer) => layer.band === band.key).length}
                    </span>
                  </span>
                ))}
              </div>
            </>
          )}
        </Guard>
      </Card>

      <Card title="大脑地图" sub="14 内核按职责域分布" span={3}>
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
                height={260}
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
                          {data.kernels.filter((k) => k.domain === domain.key).length}
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

      <Card
        title="AI 员工名册"
        sub={
          roster.data
            ? `共 ${roster.data.totals.employees} 项 · 来源 ${roster.data.source}`
            : '来源 capability-registry.yaml'
        }
        span={12}
        action={
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className="os-badge"
              data-tone={tab === 'kernels' ? 'accent' : 'muted'}
              onClick={() => setTab('kernels')}
            >
              内核 {roster.data?.totals.kernels ?? 0}
            </button>
            <button
              className="os-badge"
              data-tone={tab === 'layers' ? 'accent' : 'muted'}
              onClick={() => setTab('layers')}
            >
              能力层 {roster.data?.totals.layers ?? 0}
            </button>
            <button
              className="os-badge"
              data-tone={tab === 'runtime' ? 'accent' : 'muted'}
              onClick={() => setTab('runtime')}
            >
              运行时 {employees.data?.count ?? 0}
            </button>
          </div>
        }
      >
        {tab === 'runtime' ? (
          <Guard
            loading={employees.loading}
            error={employees.error}
            data={employees.data}
            isEmpty={(data) => data.agents.length === 0}
            emptyTitle="Agent pool 未初始化"
            onRetry={employees.reload}
          >
            {(empData) => (
              <div className="os-list">
                <div className="os-row" style={{ marginBottom: 8, borderBottom: '1px solid var(--os-border)' }}>
                  <div className="os-row-main">
                    <div className="os-row-sub">
                      提交 {empData.stats.total_tasks_submitted} ·
                      完成 {empData.stats.total_tasks_completed} ·
                      失败 {empData.stats.total_tasks_failed} ·
                      队列 {empData.stats.task_queue_length}
                    </div>
                  </div>
                </div>
                {empData.employees && empData.employees.length > 0 && (
                  <div style={{ marginTop: 12 }}>
                    <div className="os-row-sub" style={{ fontWeight: 600, marginBottom: 4 }}>
                      按名员工（{empData.employees.length}）
                    </div>
                    {empData.employees.map((emp) => (
                      <div className="os-row" key={emp.name}>
                        <span
                          className="os-dot"
                          style={{
                            background:
                              emp.name === 'liuhao-default'
                                ? 'var(--os-accent)'
                                : 'var(--os-ok)',
                          }}
                        />
                        <div className="os-row-main">
                          <div className="os-row-title">
                            {emp.name}{' '}
                            <span className="os-badge" data-tone="muted">
                              {emp.agent_count} agents
                            </span>
                          </div>
                          <div className="os-row-sub">
                            {emp.agents.map((a) => a.status).join(' · ')}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {empData.agents.map((agent) => (
                  <div className="os-row" key={agent.id}>
                    <span className="os-dot" style={{ background: `var(--os-${STATUS_TONE[agent.status] ?? 'muted'})` }} />
                    <div className="os-row-main">
                      <div className="os-row-title">
                        {agent.name}{' '}
                        <span className="os-badge" data-tone="muted">
                          {agent.id}
                        </span>
                      </div>
                      <div className="os-row-sub">
                        {agent.agent_type} ·
                        完成 {agent.completed_tasks} ·
                        失败 {agent.failed_tasks} ·
                        延迟 {agent.total_latency_ms.toFixed(0)}ms
                        {agent.current_task ? ` · 当前: ${agent.current_task}` : ''}
                      </div>
                    </div>
                    <span className="os-badge" data-tone={STATUS_TONE[agent.status] ?? 'muted'}>
                      {agent.status}
                    </span>
                  </div>
                ))}
                {goals.data && goals.data.goals.length > 0 && (
                  <div style={{ marginTop: 12 }}>
                    <div className="os-row-sub" style={{ fontWeight: 600, marginBottom: 4 }}>最近 Goal 执行</div>
                    {goals.data.goals.slice(0, 5).map((goal) => (
                      <div className="os-row" key={goal.goal_id}>
                        <span className="os-dot" style={{ background: `var(--os-${STATUS_TONE[goal.state] ?? 'muted'})` }} />
                        <div className="os-row-main">
                          <div className="os-row-title">
                            <code>{goal.goal_id}</code>{' '}
                            <span className="os-badge" data-tone="muted">{goal.scope}</span>
                          </div>
                          <div className="os-row-sub">
                            {goal.natural_language.length > 60
                              ? goal.natural_language.slice(0, 60) + '…'
                              : goal.natural_language}
                            {goal.error ? ` · ${goal.error}` : ''}
                          </div>
                        </div>
                        <span className="os-badge" data-tone={STATUS_TONE[goal.state] ?? 'muted'}>
                          {goal.state}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </Guard>
        ) : (
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          isEmpty={() => (tab === 'kernels' ? kernels.length : layers.length) === 0}
          emptyTitle={need ? '没有匹配的条目' : '注册表为空'}
          onRetry={roster.reload}
        >
          {() =>
            tab === 'kernels' ? (
              <div className="os-list">
                {kernels.map((kernel) => (
                  <div className="os-row" key={kernel.id}>
                    <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                    <div className="os-row-main">
                      <div className="os-row-title">
                        {kernel.name}{' '}
                        <span className="os-badge" data-tone="muted">
                          {kernel.id}
                        </span>
                      </div>
                      <div className="os-row-sub">
                        {kernel.domain_label} · kernel={kernel.kernel}
                        {kernel.scope ? ` · ${kernel.scope}` : ''}
                      </div>
                    </div>
                    <span className="os-badge" data-tone={kernel.status === 'implemented' ? 'ok' : 'muted'}>
                      {kernel.status ?? '未知'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="os-list">
                {layers.map((layer) => (
                  <div className="os-row" key={layer.id}>
                    <span className="os-dot" style={{ background: 'var(--os-accent-2)' }} />
                    <div className="os-row-main">
                      <div className="os-row-title">
                        {layer.name}{' '}
                        <span className="os-badge" data-tone="muted">
                          {layer.id}
                        </span>
                      </div>
                      <div className="os-row-sub">
                        {layer.band_label} · {layer.phase ?? '—'} ·{' '}
                        <code>{layer.module ?? '—'}</code>
                      </div>
                    </div>
                    <span className="os-row-tail">{layer.tests} 用例</span>
                  </div>
                ))}
              </div>
            )
          }
        </Guard>
        )}
      </Card>

      <Card title="服务面" sub="LLM provider 真实配置状态" span={12}>
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data?.providers ?? null}
          isEmpty={(data) => data.items.length === 0}
          emptyTitle="没有已注册的 provider"
          onRetry={roster.reload}
        >
          {(data) => (
            <div className="os-list">
              {data.items.map((item) => (
                <div className="os-row" key={item.type}>
                  <span
                    className="os-dot"
                    style={{
                      background: item.configured ? 'var(--os-ok)' : 'var(--os-warn)',
                    }}
                  />
                  <div className="os-row-main">
                    <div className="os-row-title">{item.label}</div>
                    <div className="os-row-sub">
                      {item.required_env.length > 0
                        ? `需要：${item.required_env.join(' / ')}`
                        : '无需密钥'}
                    </div>
                  </div>
                  {item.active && (
                    <span className="os-badge" data-tone="accent">
                      当前生效
                    </span>
                  )}
                  <span className="os-badge" data-tone={item.configured ? 'ok' : 'warn'}>
                    {item.configured ? '就绪' : '未配置'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Guard>
      </Card>
    </div>
  )
}

export function BusinessCenter({ query }: { query: string }) {
  const roster = useApi<RosterPayload>('/v1/dashboard/roster', 60_000)
  const need = query.trim().toLowerCase()

  return (
    <div className="os-grid">
      <Card
        title="业务能力清单"
        sub="能力层即系统实际具备的业务能力（全部来自注册表）"
        span={12}
      >
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          isEmpty={(data) => data.layers.length === 0}
          emptyTitle="注册表不可用"
          onRetry={roster.reload}
        >
          {(data) => {
            const rows = data.layers.filter(
              (layer) =>
                !need ||
                layer.name.toLowerCase().includes(need) ||
                layer.id.toLowerCase().includes(need) ||
                layer.band_label.toLowerCase().includes(need),
            )
            if (rows.length === 0) {
              return <Empty title="没有匹配的能力" hint={`没有条目匹配「${query}」`} />
            }
            return (
              <>
                <div className="os-legend" style={{ marginTop: 0, marginBottom: 12 }}>
                  {data.bands.map((band, index) => (
                    <span className="os-legend-item" key={band.key}>
                      <span className="os-dot" style={{ background: colorAt(index) }} />
                      {band.label}{' '}
                      <span className="os-legend-value">
                        {data.layers.filter((layer) => layer.band === band.key).length}
                      </span>
                    </span>
                  ))}
                </div>
                <div className="os-list">
                  {rows.map((layer) => (
                    <div className="os-row" key={layer.id}>
                      <span
                        className="os-dot"
                        style={{ background: colorAt(data.bands.findIndex((b) => b.key === layer.band)) }}
                      />
                      <div className="os-row-main">
                        <div className="os-row-title">{layer.name}</div>
                        <div className="os-row-sub">
                          {layer.band_label} · {layer.phase ?? '—'} ·{' '}
                          <code>{layer.module ?? '—'}</code>
                        </div>
                      </div>
                      <span className="os-row-tail">{layer.tests} 用例</span>
                      <span
                        className="os-badge"
                        data-tone={layer.status === 'implemented' ? 'ok' : 'muted'}
                      >
                        {layer.status ?? '未知'}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )
          }}
        </Guard>
      </Card>

      <Card title="业务数据来源" span={12}>
        <div className="os-note">
          <span>
            本页只呈现注册表中**已登记**的业务能力。金额类指标（营收、成本、预算）当前
            <strong>没有</strong>接入真实账本数据源 —— Economy 引擎是可供调用的库，不是
            实时账本，因此这里不显示任何金额，也不做估算。
            <br />
            已接入的真实业务数据在 <strong>数据中心</strong> 与 <strong>总览</strong>：
            会话、审计事件、事件分布、依赖健康度。
          </span>
        </div>
      </Card>
    </div>
  )
}

export function KnowledgeCenter({ query }: { query: string }) {
  const roster = useApi<RosterPayload>('/v1/dashboard/roster', 60_000)
  const spec = useApi<OpenApiPaths>('/openapi.json', 0)
  const need = query.trim().toLowerCase()

  const knowledgePaths = useMemo(() => {
    const paths = Object.keys(spec.data?.paths ?? {})
    return paths.filter((path) => path.startsWith('/knowledge')).sort()
  }, [spec.data])

  const relatedKernels = useMemo(() => {
    const wanted = new Set(['context', 'memory', 'capability', 'evaluation'])
    return (roster.data?.kernels ?? []).filter((kernel) => wanted.has(kernel.kernel))
  }, [roster.data])

  const filtered = need
    ? knowledgePaths.filter((path) => path.toLowerCase().includes(need))
    : knowledgePaths

  return (
    <div className="os-grid">
      <Card title="知识检索 API" sub="来自运行中服务的 OpenAPI 定义（真实路由）" span={7}>
        <Guard
          loading={spec.loading}
          error={spec.error}
          data={spec.data}
          isEmpty={() => filtered.length === 0}
          emptyTitle="没有知识相关端点"
          emptyHint={need ? `没有端点匹配「${query}」` : undefined}
          onRetry={spec.reload}
        >
          {() => (
            <div className="os-list">
              {filtered.map((path) => {
                const methods = Object.keys(spec.data?.paths?.[path] ?? {}).filter((key) =>
                  ['get', 'post', 'put', 'delete', 'patch'].includes(key),
                )
                const detail = spec.data?.paths?.[path] ?? {}
                const summary =
                  typeof detail.get === 'object' &&
                  detail.get !== null &&
                  'summary' in (detail.get as Record<string, unknown>)
                    ? String((detail.get as Record<string, unknown>).summary)
                    : ''
                return (
                  <div className="os-row" key={path}>
                    <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                    <div className="os-row-main">
                      <div className="os-row-title">
                        <code>{path}</code>
                      </div>
                      {summary && <div className="os-row-sub">{summary}</div>}
                    </div>
                    {methods.map((method) => (
                      <span className="os-badge" data-tone="accent" key={method}>
                        {method.toUpperCase()}
                      </span>
                    ))}
                  </div>
                )
              })}
            </div>
          )}
        </Guard>
      </Card>

      <Card title="相关知识内核" sub="与知识/记忆/能力直接相关的内核" span={5}>
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data}
          isEmpty={() => relatedKernels.length === 0}
          emptyTitle="注册表不可用"
          onRetry={roster.reload}
        >
          {() => (
            <div className="os-list">
              {relatedKernels.map((kernel) => (
                <div className="os-row" key={kernel.id}>
                  <span className="os-dot" style={{ background: 'var(--os-accent-2)' }} />
                  <div className="os-row-main">
                    <div className="os-row-title">{kernel.name}</div>
                    <div className="os-row-sub">
                      {kernel.id} · {kernel.domain_label}
                    </div>
                  </div>
                  <span
                    className="os-badge"
                    data-tone={kernel.status === 'implemented' ? 'ok' : 'muted'}
                  >
                    {kernel.status ?? '未知'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Guard>
      </Card>

      <Card title="说明" span={12}>
        <div className="os-note">
          <span>
            知识检索端点是真实的（直接读自运行中服务的 OpenAPI 定义），但**能否返回结果**
            取决于是否配置了可用的 LLM provider。provider 的配置状态见
            <strong> 我AI员工 → 服务面 </strong>或 <strong>总览 → 系统通知</strong>。
            未配置时端点会如实报错，而不是返回伪造的答案。
          </span>
        </div>
      </Card>
    </div>
  )
}
