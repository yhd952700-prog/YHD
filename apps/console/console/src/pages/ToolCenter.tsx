/**
 * 工具中心 —— MCP 服务、本地执行器、插件注册表、API 配置的真实状态。
 *
 * 事实边界
 * --------
 * 真实可用：`/v1/employees/capabilities`（本地执行器）、`/v1/plugins`（插件注册表）、
 * `/v1/models`（模型服务配置）。
 * 不存在：`/v1/mcp/servers` —— 当前本地服务只有进程内 MCP 适配器，没有 HTTP 面，
 * 因此 MCP 区块整块显示「本地服务还没提供」，不画假的服务卡片。
 *
 * 启用前检查：插件注册表没有权限与风险字段，这两项如实显示「未提供」，
 * 并在三项信息不齐时禁止在本页启用 —— 高风险必须有明确提示，未知风险不当作安全。
 *
 * 接线（本轮按要求不改公共文件）
 * -----------------------------
 * `TOOLS_CENTER_PAGE` 是给 `nav.ts` / `App.tsx` 统一接线用的导出常量。
 */

import { useMemo, useState } from 'react'
import './SkillsTools.css'
import { useApi } from '../lib/api'
import { Card, Empty, Failure, Loading } from '../components/ui'
import {
  FIELD_NOT_PROVIDED,
  NOT_PROVIDED,
  NOT_PROVIDED_DETAIL,
  RISK_LABELS,
  apiAvailability,
  probeState,
  readApiConfigs,
  readCapabilities,
  readMcpServers,
  readPlugins,
  riskLevel,
  riskTone,
  sourceLabel,
  type PluginToolRecord,
  type ProbeState,
  type RiskLevel,
} from '../lib/skillsTools'

/** 页面登记信息：等 `nav.ts` / `App.tsx` 统一接线时直接引用。 */
export const TOOLS_CENTER_PAGE = {
  key: 'tools-center',
  navLabel: '工具中心',
  title: '工具中心',
  sub: 'MCP 服务与 API 的配置状态、来源、可用性',
} as const

const STATE_LABELS: Record<ProbeState, string> = {
  loading: '读取中',
  ready: '可用',
  'not-provided': NOT_PROVIDED,
  forbidden: '无权查看',
  failed: '读取失败',
}

function Unavailable({ testId }: { testId?: string }) {
  return (
    <div className="st-unprovided" data-testid={testId} role="status">
      <strong>{NOT_PROVIDED}</strong>
      <span>{NOT_PROVIDED_DETAIL}</span>
    </div>
  )
}

function RiskChip({ risk }: { risk: RiskLevel }) {
  return (
    <span
      className={`st-chip st-tone-${riskTone(risk)}`}
      title={risk === 'unknown' ? '本地服务没有返回风险等级' : undefined}
    >
      {RISK_LABELS[risk]}
    </span>
  )
}

/**
 * 启用前检查：来源 / 需要权限 / 风险等级三件套。
 *
 * 三项必须齐全才允许启用；缺任何一项都保持禁用，并把缺项写明。
 */
function PreEnableCheck({
  source,
  permissions,
  risk,
  testId,
}: {
  source: string
  permissions: string[] | null
  risk: RiskLevel
  testId?: string
}) {
  const sourceKnown = source !== FIELD_NOT_PROVIDED
  const permissionsKnown = permissions !== null
  const riskKnown = risk !== 'unknown'
  const ready = sourceKnown && permissionsKnown && riskKnown && risk !== 'high'
  const reasons: string[] = []
  if (!sourceKnown) reasons.push('来源')
  if (!permissionsKnown) reasons.push('需要权限')
  if (!riskKnown) reasons.push('风险等级')
  return (
    <div className="st-precheck" data-testid={testId}>
      <dl className="st-kv">
        <dt>来源</dt>
        <dd>{sourceKnown ? source : FIELD_NOT_PROVIDED}</dd>
        <dt>需要权限</dt>
        <dd>
          {permissions === null
            ? FIELD_NOT_PROVIDED
            : permissions.length === 0
              ? '无需额外权限'
              : permissions.join('、')}
        </dd>
        <dt>风险等级</dt>
        <dd>
          <RiskChip risk={risk} />
        </dd>
      </dl>
      {risk === 'high' && (
        <p className="st-warning" role="alert">
          高风险：启用前请确认来源可信、权限范围可接受。高风险工具不会自动启用。
        </p>
      )}
      {reasons.length > 0 && (
        <p className="st-warning" role="alert">
          还缺「{reasons.join('、')}」，本页不提供启用入口；请等本地服务补齐后再操作。
        </p>
      )}
      <button className="os-btn os-btn-primary" type="button" disabled={!ready}>
        {ready ? '启用（需在服务端确认）' : '启用'}
      </button>
    </div>
  )
}

export function ToolCenter({ query = '' }: { query?: string }) {
  const capabilities = useApi<unknown>('/v1/employees/capabilities', 30000)
  // 插件与模型是易变清单：30s 轮询，避免安装/改配后页面一直显示旧表。
  // MCP 没有接口（恒为“本地服务还没提供”），保持单次。
  const plugins = useApi<unknown>('/v1/plugins', 30000)
  const models = useApi<unknown>('/v1/models', 30000)
  const mcp = useApi<unknown>('/v1/mcp/servers', 0)

  const capabilityRows = useMemo(
    () => readCapabilities(capabilities.data),
    [capabilities.data],
  )
  const pluginRows = useMemo(() => readPlugins(plugins.data), [plugins.data])
  const apiRows = useMemo(() => readApiConfigs(models.data), [models.data])
  const mcpRows = useMemo(() => readMcpServers(mcp.data), [mcp.data])

  const [inspecting, setInspecting] = useState<string | null>(null)

  const need = query.trim().toLowerCase()
  const match = (text: string) => !need || text.toLowerCase().includes(need)
  const shownCapabilities = capabilityRows.filter((row) =>
    match([row.name, row.tool_id, row.description ?? ''].join(' ')),
  )
  const shownPlugins = pluginRows.filter((row) =>
    match([row.name, row.id, row.kind, row.capabilities.join(' ')].join(' ')),
  )
  // GAP: 占位条目（source='placeholder'）是历史登记、无模块入口，后端补齐
  // activatable/builtin 字段后方可移入主表；此前一律折叠在下方分区，不可启用。
  const shownRealPlugins = shownPlugins.filter((row) => row.source !== 'placeholder')
  const shownPlaceholderPlugins = shownPlugins.filter((row) => row.source === 'placeholder')
  const shownApis = apiRows.filter((row) => match([row.name, row.source].join(' ')))

  const highRiskCapabilities = capabilityRows.filter(
    (row) => riskLevel(row.risk) === 'high',
  )

  const mcpState = probeState(mcp)
  const capabilityState = probeState(capabilities)
  const pluginState = probeState(plugins)
  const modelState = probeState(models)

  const sourceRows = [
    {
      path: 'GET /v1/employees/capabilities',
      purpose: '本地执行器能力（工具清单）',
      state: capabilityState,
    },
    { path: 'GET /v1/plugins', purpose: '插件与工具注册表', state: pluginState },
    { path: 'GET /v1/models', purpose: '模型服务（API）配置', state: modelState },
    { path: 'GET /v1/mcp/servers', purpose: 'MCP 服务配置', state: mcpState },
  ]

  const configuredApi = apiRows.filter((row) => apiAvailability(row).tone === 'ok').length

  return (
    <div className="os-grid">
      <Card title="说明" sub="只展示真实状态；没有的接口不画假窗口" span={12}>
        <ul className="os-list">
          <li>工具清单、插件注册表、模型服务配置都直接读本机服务，状态与可用性由本地服务字段决定。</li>
          <li>MCP 服务当前没有 HTTP 接口：整块显示「{NOT_PROVIDED}」，不会显示示例服务。</li>
          <li>启用外部工具/插件前必须看到来源、需要权限、风险等级；缺任何一项都保持禁用。</li>
          <li>高风险条目单独提示；风险未知不会被当成低风险。</li>
        </ul>
      </Card>

      <Card title="MCP 服务" sub="外部工具服务；只读展示配置状态" span={6}>
        {mcpState === 'loading' && <Loading />}
        {mcpState === 'not-provided' && <Unavailable testId="tools-mcp-unprovided" />}
        {(mcpState === 'failed' || mcpState === 'forbidden') && (
          <Failure message={mcp.error ?? NOT_PROVIDED} onRetry={mcp.reload} />
        )}
        {mcpState === 'ready' &&
          (mcpRows.length === 0 ? (
            <Empty
              title="还没有 MCP 服务"
              hint="本地服务接口可用，但当前没有配置任何 MCP 服务；新增入口需由本地服务提供。"
            />
          ) : (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>服务</th>
                    <th>传输</th>
                    <th>来源</th>
                    <th>风险</th>
                    <th>状态</th>
                    <th>启用前检查</th>
                  </tr>
                </thead>
                <tbody>
                  {mcpRows.map((row) => (
                    <tr key={row.name}>
                      <td>
                        <strong>{row.name}</strong>
                      </td>
                      <td>{row.transport || FIELD_NOT_PROVIDED}</td>
                      <td>{sourceLabel(row.source)}</td>
                      <td>
                        <RiskChip risk={row.risk} />
                      </td>
                      <td>{row.status || FIELD_NOT_PROVIDED}</td>
                      <td>
                        <button
                          className="os-btn"
                          type="button"
                          onClick={() =>
                            setInspecting((prev) =>
                              prev === `mcp:${row.name}` ? null : `mcp:${row.name}`,
                            )
                          }
                        >
                          {inspecting === `mcp:${row.name}` ? '收起' : '查看'}
                        </button>
                        {inspecting === `mcp:${row.name}` && (
                          <PreEnableCheck
                            source={sourceLabel(row.source)}
                            permissions={row.permissions}
                            risk={row.risk}
                            testId="tools-mcp-precheck"
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </Card>

      <Card title="本地工具" sub="随包内置的执行器能力（真实注册项）" span={6}>
        {capabilityState === 'loading' && <Loading />}
        {capabilityState === 'not-provided' && <Unavailable testId="tools-capabilities-unprovided" />}
        {(capabilityState === 'failed' || capabilityState === 'forbidden') && (
          <Failure message={capabilities.error ?? NOT_PROVIDED} onRetry={capabilities.reload} />
        )}
        {capabilityState === 'ready' &&
          (shownCapabilities.length === 0 ? (
            <Empty
              title={need ? '没有匹配的工具' : '本地没有已注册工具'}
              hint={
                need
                  ? `搜索「${query}」没有命中本地执行器能力。`
                  : '本地服务返回了空的能力清单；这通常意味着本机没有启用任何执行器。'
              }
            />
          ) : (
            <>
              {highRiskCapabilities.length > 0 && (
                <p className="st-warning" data-testid="tools-high-risk" role="alert">
                  有 {highRiskCapabilities.length} 个高风险工具。调用前请确认来源与权限范围。
                </p>
              )}
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>工具</th>
                      <th>工具 ID</th>
                      <th>说明</th>
                      <th>风险</th>
                      <th>可用性</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shownCapabilities.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <strong>{row.name}</strong>
                        </td>
                        <td>{row.tool_id || FIELD_NOT_PROVIDED}</td>
                        <td>{row.description || FIELD_NOT_PROVIDED}</td>
                        <td>
                          <RiskChip risk={riskLevel(row.risk)} />
                        </td>
                        <td>已注册，可调用</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="os-hint">
                来源：随包内置执行器；共 {shownCapabilities.length} 项
                {need ? `（过滤前 ${capabilityRows.length} 项）` : ''}。
              </p>
            </>
          ))}
      </Card>

      <Card title="插件与工具注册表" sub="含来源与状态；启用前逐项核对" span={12}>
        {pluginState === 'loading' && <Loading />}
        {pluginState === 'not-provided' && <Unavailable testId="tools-plugins-unprovided" />}
        {(pluginState === 'failed' || pluginState === 'forbidden') && (
          <Failure message={plugins.error ?? NOT_PROVIDED} onRetry={plugins.reload} />
        )}
        {pluginState === 'ready' &&
          (shownPlugins.length === 0 ? (
            <Empty
              title={need ? '没有匹配的条目' : '注册表为空'}
              hint={
                need
                  ? `搜索「${query}」没有命中插件或工具条目。`
                  : '本地服务返回了空的插件注册表。'
              }
            />
          ) : shownRealPlugins.length === 0 ? (
            <p className="os-hint">
              全部 {shownPlugins.length} 项均为历史登记（见下方折叠分区），没有可启用的真实条目。
            </p>
          ) : (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>类型</th>
                    <th>来源</th>
                    <th>状态</th>
                    <th>可用性</th>
                    <th>能力</th>
                    <th>启用前检查</th>
                  </tr>
                </thead>
                <tbody>
                  {shownRealPlugins.map((row: PluginToolRecord) => (
                    <tr key={row.id || row.name}>
                      <td>
                        <strong>{row.name}</strong>
                        <div className="st-sub">{row.id || FIELD_NOT_PROVIDED}</div>
                      </td>
                      <td>{row.kind || FIELD_NOT_PROVIDED}</td>
                      <td>{pluginSourceLabel(row.source)}</td>
                      <td>{row.error ? `异常：${row.error}` : row.availability === 'active' ? '已启用' : '未启用'}</td>
                      <td>{pluginAvailabilityLabel(row.availability)}</td>
                      <td>{row.capabilities.length === 0 ? '—' : row.capabilities.join('、')}</td>
                      <td>
                        <button
                          className="os-btn"
                          type="button"
                          onClick={() =>
                            setInspecting((prev) => (prev === row.id ? null : row.id))
                          }
                        >
                          {inspecting === row.id ? '收起' : '查看'}
                        </button>
                        {inspecting === row.id && (
                          <PreEnableCheck
                            source={pluginSourceLabel(row.source)}
                            permissions={row.permissions}
                            risk={row.risk}
                            testId="tools-plugin-precheck"
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        {pluginState === 'ready' && shownPlaceholderPlugins.length > 0 && (
          <details className="os-hint" style={{ marginTop: 12 }}>
            <summary>
              历史登记，不可启用（{shownPlaceholderPlugins.length} 项）
            </summary>
            <p>
              以下条目只有注册表登记，没有模块入口（既非随包内置、也不可加载），
              因此不计入上方主表、不提供启用入口；等本地服务补齐注册信息后再恢复。
            </p>
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>类型</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {shownPlaceholderPlugins.map((row: PluginToolRecord) => (
                    <tr key={row.id || row.name}>
                      <td>
                        <strong>{row.name}</strong>
                        <div className="st-sub">{row.id || FIELD_NOT_PROVIDED}</div>
                      </td>
                      <td>{row.kind || FIELD_NOT_PROVIDED}</td>
                      <td>历史登记，不可启用</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        )}
        {pluginState === 'ready' && shownPlugins.length > 0 && (
          <p className="os-hint">
            共 {shownRealPlugins.length} 项真实条目
            {shownPlaceholderPlugins.length > 0
              ? `（另有 ${shownPlaceholderPlugins.length} 项历史登记未计入）`
              : ''}
            ；插件注册表没有返回权限与风险字段，因此这些条目的「需要权限 / 风险等级」如实显示未提供，
            本页不会代替本地服务给它们评级。
          </p>
        )}
      </Card>

      <Card title="API 配置（模型服务）" sub="配置状态、来源、可用性" span={12}>
        {modelState === 'loading' && <Loading />}
        {modelState === 'not-provided' && <Unavailable testId="tools-api-unprovided" />}
        {(modelState === 'failed' || modelState === 'forbidden') && (
          <Failure message={models.error ?? NOT_PROVIDED} onRetry={models.reload} />
        )}
        {modelState === 'ready' &&
          (shownApis.length === 0 ? (
            <Empty
              title={need ? '没有匹配的配置' : '还没有模型服务配置'}
              hint={
                need
                  ? `搜索「${query}」没有命中模型服务配置。`
                  : '本地服务没有返回任何模型服务配置；请先在「系统设置 → 模型管理」里添加。'
              }
            />
          ) : (
            <>
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>配置</th>
                      <th>来源（厂商）</th>
                      <th>密钥状态</th>
                      <th>启用</th>
                      <th>连接验证</th>
                      <th>可用性</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shownApis.map((row) => {
                      const availability = apiAvailability(row)
                      return (
                        <tr key={row.id || row.name}>
                          <td>
                            <strong>{row.name}</strong>
                            <div className="st-sub">{row.id || FIELD_NOT_PROVIDED}</div>
                          </td>
                          <td>{row.source}</td>
                          <td>
                            {row.keyStatus === 'configured'
                              ? '已配置'
                              : row.keyStatus === 'missing'
                                ? '未配置'
                                : row.keyStatus === 'unreadable'
                                  ? '需要重新填写'
                                  : FIELD_NOT_PROVIDED}
                          </td>
                          <td>
                            {row.enabled === null ? FIELD_NOT_PROVIDED : row.enabled ? '已启用' : '已停用'}
                          </td>
                          <td>
                            {row.verified
                              ? '已验证'
                              : row.backendStatus || '尚未验证'}
                          </td>
                          <td>
                            <span className={`st-chip st-tone-${availability.tone}`}>
                              {availability.label}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <p className="os-hint">
                共 {shownApis.length} 项配置，其中 {configuredApi} 项可用（已配置密钥且连接已验证）。
              </p>
            </>
          ))}
      </Card>

      <Card title="数据来源" sub="本页每个区块实际调用过的接口" span={12}>
        <div className="os-table-wrap">
          <table className="os-table">
            <thead>
              <tr>
                <th>接口</th>
                <th>用途</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {sourceRows.map((row) => (
                <tr key={row.path}>
                  <td>
                    <code>{row.path}</code>
                  </td>
                  <td>{row.purpose}</td>
                  <td>
                    <span
                      className={`st-chip st-tone-${
                        row.state === 'ready'
                          ? 'ok'
                          : row.state === 'not-provided'
                            ? 'muted'
                            : row.state === 'loading'
                              ? 'muted'
                              : 'danger'
                      }`}
                    >
                      {STATE_LABELS[row.state]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

function pluginSourceLabel(source: PluginToolRecord['source']): string {
  if (source === 'builtin') return '随包内置'
  if (source === 'loadable') return '可加载插件'
  if (source === 'placeholder') return '历史登记（无模块入口）'
  return FIELD_NOT_PROVIDED
}

function pluginAvailabilityLabel(availability: PluginToolRecord['availability']): string {
  if (availability === 'active') return '运行中'
  if (availability === 'available') return '可启用'
  if (availability === 'unavailable') return '不可用'
  return FIELD_NOT_PROVIDED
}
