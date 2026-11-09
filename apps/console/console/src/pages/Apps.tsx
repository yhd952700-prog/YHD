/**
 * 应用 —— 把后端"插件"真实呈现给人类，并允许激活（产品面，Top-3 #2 缺口）。
 *
 * 为什么这个文件存在
 * ------------------
 * 后端 `src/gateway/plugins.py` 暴露了 `GET /v1/plugins`、`POST /v1/plugins/{id}/activate`
 * 两个端点，但驾驶舱里没有任何页面渲染它们。人类因此看不到"系统里有哪些插件、
 * 哪些已被我激活"——一个声称"AI OS"却把插件能力藏在内核里不摊开的产品，谈不上
 * 可管理、可激活、可交付。这个页面把这两个端点真正渲染出来。
 *
 * 诚实优先
 * --------
 * * 读取是只读：列表每 30 秒轮询一次，如实显示每个插件的真实 status / active。
 * * 激活是**人类主权动作**（走带会话的 `apiFetch`，401 由它统一抛出并清会话）；
 *   未知 id 由后端返回真实 404，激活失败会带真实 error，前端原样展示，不假装成功。
 * * 没有插件就如实显示「暂无应用」，不编造；后端对不存在的 plugin_id 返回真实 404，
 *   前端说明"插件已不在列表里"并给出下一步。
 */

import { useCallback, useMemo, useState } from 'react'
import { ApiError, useApi } from '../lib/api'
import {
  activateApp,
  type AppSummary,
  type AppsResponse,
} from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'
import { capabilityLabel, friendlyPluginError, safeArray } from '../lib/format'

/** 状态色：与 goals / projects 的色调约定一致。 */
function statusTone(status: string): 'accent' | 'danger' | 'muted' {
  if (status === 'active') return 'accent'
  if (status === 'failed') return 'danger'
  return 'muted'
}

const APP_STATUS_LABELS: Record<string, string> = {
  active: '已启用',
  registered: '未启用',
  discovered: '未启用',
  inactive: '未启用',
  unloaded: '未启用',
  failed: '异常',
  error: '异常',
  loading: '加载中',
  disabled: '已停用',
}

const KERNEL_TYPE_LABELS: Record<string, string> = {
  agent: '员工',
  service: '服务',
  tool: '工具',
  plugin: '插件',
  kernel: '系统组件',
  identity: '身份系统',
  memory: '记忆系统',
  security: '安全系统',
  builtin: '内置能力',
}

export function Apps({
  query,
  canManage = true,
}: {
  query: string
  /** 插件激活是全局动作：成员可看列表，按钮在点击前就说明需要管理权限。 */
  canManage?: boolean
}) {
  // 列表：每 30 秒轮询一次真实归类态。
  const apps = useApi<AppsResponse>('/v1/plugins', 30_000)

  // 激活态：逐行独立，避免一次失败污染整张表。
  const [busyId, setBusyId] = useState<string | null>(null)
  const [rowError, setRowError] = useState<Record<string, string>>({})

  const handleActivate = useCallback(
    async (pluginId: string) => {
      if (!canManage) return
      setBusyId(pluginId)
      setRowError((prev) => {
        const next = { ...prev }
        delete next[pluginId]
        return next
      })
      try {
        await activateApp(pluginId)
        void apps.reload()
      } catch (err) {
        const status = err instanceof ApiError ? err.status : 0
        setRowError((prev) => ({
          ...prev,
          [pluginId]:
            status === 404
              ? '列表里已经没有这个插件，可能已被移除；列表每 30 秒自动刷新，请稍后再试。'
              : err instanceof Error
                ? err.message
                : '激活没有完成，请重试；持续失败请刷新页面后查看该插件状态。',
        }))
        // 失败也是真实状态：刷新一次，让该行按 failed 渲染且不再显示可点击按钮。
        void apps.reload()
      } finally {
        setBusyId(null)
      }
    },
    [apps, canManage],
  )

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    const list = apps.data?.plugins ?? []
    if (!need) return list
    return list.filter((app: AppSummary) =>
      [app.plugin_id, app.name, app.kernel_type, ...safeArray<string>(app.capabilities)]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [apps.data, need])

  return (
    <div className="os-grid">
      <Card title="说明" sub="这个视图能证明什么" span={12}>
        <ul className="os-list">
          <li>这里是插件与工具：已注册且有真实模块入口的插件由系统列出，激活动作由你触发（真实加载，非占位）。</li>
          <li>本版本暂不提供第三方插件市场：随包提供的只有内置能力与已登记的可加载插件，不承诺安装外部插件。</li>
          <li>列表如实显示每个插件的真实状态（未启用 / 已启用 / 异常）；激活后状态会变为已启用。</li>
          <li>没有入口模块的历史占位条目不会出现在启用列表；加载失败的条目只显示中文原因，不再保留永远失败的激活按钮。</li>
          <li>没有插件是真没有，显示「暂无需要激活的插件」；激活失败会带真实原因，不假装成功。</li>
          {!canManage && <li>激活需要管理权限；当前账号可以查看，按钮会保持禁用。</li>}
        </ul>
      </Card>

      {(apps.data?.plugins.length ?? 0) > 0 && (apps.data?.plugins.length ?? 0) <= 2 && (
        <Card title="为什么这里条目很少" sub="内置能力与可加载插件不是一回事" span={12}>
          <ul className="os-list">
            <li>身份、记忆、安全与多租户是系统内置能力，随系统启动即生效，不需要在这里激活。</li>
            <li>这里只列出可以真正加载的插件；没有入口的历史登记不会显示，也不会留下点不动的按钮。</li>
            <li>下一步：想确认系统已经具备哪些内置能力，打开左侧「能力注册表」；如需接入自己的插件，请按《插件开发指南》实现后重启，再回到本页激活。</li>
          </ul>
        </Card>
      )}

      <Card
        title="应用列表"
        sub="系统里真实注册的插件（每 30 秒刷新）"
        span={12}
      >
        {shown.length === 0 && !apps.loading && !apps.error ? (
          <Empty
            title={need ? '没有匹配的应用' : '暂无需要激活的插件'}
            hint="身份、记忆、安全与多租户是系统内置能力，随系统启动生效，不需要激活。想查看内置能力清单，打开左侧「能力注册表」；如需接入自有插件，请按《插件开发指南》实现后重启。"
          />
        ) : (
          <Guard
            loading={apps.loading}
            error={apps.error}
            data={apps.data}
            isEmpty={(data) => data.plugins.length === 0}
            emptyTitle="暂无需要激活的插件"
            emptyHint="身份、记忆、安全与多租户是系统内置能力，随系统启动生效，不需要激活。想查看内置能力清单，打开左侧「能力注册表」；如需接入自有插件，请按《插件开发指南》实现后重启。"
            onRetry={apps.reload}
          >
            {() => (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>插件 ID</th>
                      <th>名称</th>
                      <th>版本</th>
                      <th>类型</th>
                      <th>状态</th>
                      <th>能力</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((app: AppSummary) => {
                      const caps = safeArray(app.capabilities)
                      return (
                      <tr key={app.plugin_id}>
                        <td>{app.plugin_id}</td>
                        <td>{app.name}</td>
                        <td>{app.version}</td>
                        <td>{KERNEL_TYPE_LABELS[app.kernel_type] ?? '其他'}</td>
                        <td>
                          <span className={`os-dot os-tone-${statusTone(app.status)}`} />
                          {APP_STATUS_LABELS[app.status] ?? '未知状态'}
                          {app.error && (
                            <span className="os-error"> · {friendlyPluginError(app.error)}</span>
                          )}
                        </td>
                        <td>
                          {caps.length === 0
                            ? '—'
                            : caps.map((cap) => capabilityLabel(cap)).join('、')}
                        </td>
                        <td>
                          {app.active ? (
                            <button className="os-btn" disabled>
                              已激活
                            </button>
                          ) : app.activatable === false ? (
                            <span className="os-hint">
                              {app.builtin ? '内置能力，无需激活' : '不可激活（缺少模块入口）'}
                            </span>
                          ) : app.status === 'failed' ? (
                            <span
                              className="os-hint"
                              title={app.error ? friendlyPluginError(app.error) : undefined}
                            >
                              加载失败，请更新插件后重启
                            </span>
                          ) : (
                            <button
                              className="os-btn os-btn-primary"
                              disabled={!canManage || busyId === app.plugin_id}
                              title={canManage ? undefined : '当前账号暂无此权限'}
                              onClick={() => void handleActivate(app.plugin_id)}
                            >
                              {!canManage
                                ? '当前账号暂无此权限'
                                : busyId === app.plugin_id
                                  ? '激活中…'
                                  : '激活'}
                            </button>
                          )}
                          {rowError[app.plugin_id] && (
                            <p className="os-error">{rowError[app.plugin_id]}</p>
                          )}
                        </td>
                      </tr>
                    )})}
                  </tbody>
                </table>
              </div>
            )}
          </Guard>
        )}
        {shown.length !== (apps.data?.plugins.length ?? 0) && (
          <p className="os-hint">
            搜索「{query}」过滤后显示 {shown.length} / {apps.data?.plugins.length ?? 0} 项。
          </p>
        )}
        {typeof apps.data?.hidden === 'number' && apps.data.hidden > 0 && (
          <p className="os-hint" data-testid="apps-hidden-note">
            另有 {apps.data.hidden} 个历史登记条目未列出（没有可加载的模块入口，无法激活）。
          </p>
        )}
      </Card>
    </div>
  )
}
