/**
 * 目录类页面：我的员工 / 业务中心。
 *
 * 两者都是**同一份能力注册表**的不同切面：
 *   - 我的员工  → 14 内核 + 14 能力层 + provider 服务面（全量）
 *   - 业务中心  → 14 能力层（= 系统实际具备的业务能力），按阶段带分组
 *
 * 知识中心已迁到 `pages/Knowledge.tsx`：旧实现只把 OpenAPI 的 /knowledge/*
 * 路由列表摊开，用户看不到自己的文档，既不能导入也不能验收检索 —— 那不是
 * "知识库"，只是一份端点清单。
 *
 * 没有数据源的地方如实说明，不用占位条目撑满表格。
 */

import { useMemo, useState } from 'react'
import type { RosterPayload, EmployeesPayload, GoalsPayload } from '../lib/contracts'
import { useApi } from '../lib/api'
import { colorAt } from '../lib/chartUtils'
import {
  capabilityModuleLabel,
  capabilityStatusLabel,
  capabilityStatusTone,
  neutralLayerLabel,
  phaseLabel,
} from '../lib/layerLabels'
import { goalStateLabel } from '../lib/butler'
import { numberText } from '../lib/format'
import { BrainMap, NetworkGraph } from '../components/charts'
import {
  AgentActionButtons,
  CreateGoalForm,
  EmployeeActionButtons,
  GoalActionButtons,
  HireEmployeeForm,
} from '../components/OperatorControls'
import { Card, Dot, Empty, Guard } from '../components/ui'

const STATUS_TONE: Record<string, string> = {
  idle: 'ok',
  running: 'accent',
  paused: 'muted',
  stopped: 'danger',
  completed: 'ok',
  failed: 'danger',
}

/** 执行个体状态 → 中文；原始英文只进 `title`。 */
const AGENT_STATUS_LABELS: Record<string, string> = {
  idle: '空闲',
  running: '执行中',
  paused: '已暂停',
  stopped: '已停止',
  completed: '已完成',
  failed: '失败',
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
      <Card title="能力层分布" sub="能力层按阶段带分布" span={5}>
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
                  label: neutralLayerLabel(layer.name),
                  group: layer.band,
                }))}
              />
              <div className="os-legend">
                {data.bands.map((band, index) => (
                  <span className="os-legend-item" key={band.key}>
                    <span className="os-dot" style={{ background: colorAt(index) }} />
                    {neutralLayerLabel(band.label)}{' '}
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

      <Card title="大脑地图" sub="14 个能力模块按职责域分布" span={3}>
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
                  label: neutralLayerLabel(kernel.name),
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
            ? `共 ${roster.data.totals.employees} 项 · 来源：能力注册表`
            : '来源：能力注册表'
        }
        span={12}
        action={
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              className="os-badge"
              data-tone={tab === 'kernels' ? 'accent' : 'muted'}
              onClick={() => setTab('kernels')}
            >
              能力模块 {numberText(roster.data?.totals.kernels)}
            </button>
            <button
              className="os-badge"
              data-tone={tab === 'layers' ? 'accent' : 'muted'}
              onClick={() => setTab('layers')}
            >
              能力层 {numberText(roster.data?.totals.layers)}
            </button>
            <button
              className="os-badge"
              data-tone={tab === 'runtime' ? 'accent' : 'muted'}
              onClick={() => setTab('runtime')}
            >
              运行时 {numberText(employees.data?.count)}
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
            emptyTitle="还没有可用的执行个体"
            onRetry={employees.reload}
          >
            {(empData) => (<>
              <div className="os-operator-panel">
                <div className="os-operator-grid">
                  <div>
                    <div className="os-row-sub" style={{ fontWeight: 600, marginBottom: 8 }}>
                      雇佣员工
                    </div>
                    <HireEmployeeForm onDone={employees.reload} />
                  </div>
                  <div>
                    <div className="os-row-sub" style={{ fontWeight: 600, marginBottom: 8 }}>
                      创建目标
                    </div>
                    <CreateGoalForm onDone={goals.reload} />
                  </div>
                </div>
              </div>
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
                              {emp.agent_count} 个执行个体
                            </span>
                          </div>
                          <div className="os-row-sub">
                            {emp.agents
                              .map((a) => AGENT_STATUS_LABELS[a.status] ?? '状态未知')
                              .join(' · ')}
                          </div>
                        </div>
                        <EmployeeActionButtons name={emp.name} onDone={employees.reload} />
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
                        延迟 {numberText(agent.total_latency_ms)} 毫秒
                        {agent.current_task ? ` · 当前: ${agent.current_task}` : ''}
                      </div>
                    </div>
                        <span
                          className="os-badge"
                          data-tone={STATUS_TONE[agent.status] ?? 'muted'}
                          title={`原始状态：${agent.status}`}
                        >
                          {AGENT_STATUS_LABELS[agent.status] ?? '状态未知'}
                        </span>
                        <AgentActionButtons agentId={agent.id} status={agent.status} onDone={employees.reload} />
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
                            {goal.technical_detail ? (
                              <details style={{ marginTop: 4 }}>
                                <summary>技术详情</summary>
                                <code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                  {goal.technical_detail}
                                </code>
                              </details>
                            ) : null}
                          </div>
                        </div>
                        <span className="os-badge" data-tone={STATUS_TONE[goal.state] ?? 'muted'}>
                          {goalStateLabel(goal.state)}
                        </span>
                        <GoalActionButtons goalId={goal.goal_id} state={goal.state} onDone={goals.reload} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>)}
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
                  <div className="os-row" key={kernel.id} title={`编号：${kernel.id}`}>
                    <span className="os-dot" style={{ background: 'var(--os-accent)' }} />
                    <div className="os-row-main">
                      <div className="os-row-title">
                        {capabilityModuleLabel(kernel.kernel, kernel.name)}
                      </div>
                      <div className="os-row-sub">
                        {neutralLayerLabel(kernel.domain_label)}
                        {kernel.scope ? ` · ${kernel.scope}` : ''}
                      </div>
                    </div>
                    <span
                      className="os-badge"
                      data-tone={capabilityStatusTone(kernel.status)}
                      title={kernel.status ? `原始状态：${kernel.status}` : undefined}
                    >
                      {capabilityStatusLabel(kernel.status)}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="os-list">
                {layers.map((layer) => (
                  <div
                    className="os-row"
                    key={layer.id}
                    title={`编号：${layer.id}${layer.module ? ` · 模块：${layer.module}` : ''}`}
                  >
                    <span className="os-dot" style={{ background: 'var(--os-accent-2)' }} />
                    <div className="os-row-main">
                      <div className="os-row-title">{neutralLayerLabel(layer.name)}</div>
                      <div className="os-row-sub">
                        {neutralLayerLabel(layer.band_label)} · {phaseLabel(layer.phase)}
                      </div>
                    </div>
                    <span className="os-row-tail">{layer.tests} 用例</span>
                    <span
                      className="os-badge"
                      data-tone={capabilityStatusTone(layer.status)}
                      title={layer.status ? `原始状态：${layer.status}` : undefined}
                    >
                      {capabilityStatusLabel(layer.status)}
                    </span>
                  </div>
                ))}
              </div>
            )
          }
        </Guard>
        )}
      </Card>

      <Card title="服务面" sub="模型服务商的真实配置状态" span={12}>
        <Guard
          loading={roster.loading}
          error={roster.error}
          data={roster.data?.providers ?? null}
          isEmpty={(data) => data.items.length === 0}
          emptyTitle="还没有注册任何服务商"
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
                    <div
                      className="os-row-sub"
                      title={
                        item.required_env.length > 0
                          ? `需要配置：${item.required_env.join(' / ')}`
                          : undefined
                      }
                    >
                      {item.required_env.length > 0 ? '需要配置密钥后可用' : '无需密钥'}
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
                      {neutralLayerLabel(band.label)}{' '}
                      <span className="os-legend-value">
                        {data.layers.filter((layer) => layer.band === band.key).length}
                      </span>
                    </span>
                  ))}
                </div>
                <div className="os-list">
                  {rows.map((layer) => (
                    <div
                      className="os-row"
                      key={layer.id}
                      title={`编号：${layer.id}${layer.module ? ` · 模块：${layer.module}` : ''}`}
                    >
                      <span
                        className="os-dot"
                        style={{ background: colorAt(data.bands.findIndex((b) => b.key === layer.band)) }}
                      />
                      <div className="os-row-main">
                        <div className="os-row-title">{neutralLayerLabel(layer.name)}</div>
                        <div className="os-row-sub">
                          {neutralLayerLabel(layer.band_label)} · {phaseLabel(layer.phase)}
                        </div>
                      </div>
                      <span className="os-row-tail">{layer.tests} 用例</span>
                      <span
                        className="os-badge"
                        data-tone={capabilityStatusTone(layer.status)}
                        title={layer.status ? `原始状态：${layer.status}` : undefined}
                      >
                        {capabilityStatusLabel(layer.status)}
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
