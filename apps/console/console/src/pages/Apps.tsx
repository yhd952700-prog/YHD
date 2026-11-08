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
 *   前端原样展示「未知插件」。
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
  inactive: '未启用',
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
}

export function Apps({
  query,
  canManage = true,
}: {
  query: string
  /** 插件激活是全局动作：成员可看列表，按钮在点击前就说明只能主账号启用。 */
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
              ? '未知插件'
              : err instanceof Error
                ? err.message
                : '激活失败',
        }))
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
          <li>这里是插件与工具：已注册插件由系统列出，激活动作由你触发（真实加载，非占位）。</li>
          <li>列表如实显示每个插件的真实状态（未启用 / 运行中 / 异常）；激活后状态会变为运行中。</li>
          <li>没有插件是真没有，显示「暂无应用」；激活失败会带真实原因，不假装成功。</li>
          {!canManage && <li>激活由主账号统一管理；当前账号可以查看，按钮会保持禁用。</li>}
        </ul>
      </Card>

      <Card
        title="应用列表"
        sub="系统里真实注册的插件（每 30 秒刷新）"
        span={12}
      >
        {shown.length === 0 && !apps.loading && !apps.error ? (
          <Empty
            title={need ? '没有匹配的应用' : '暂无应用'}
            hint="系统启动时若没有登记任何内置应用，这里就是空的；登记完成后再刷新即可。"
          />
        ) : (
          <Guard
            loading={apps.loading}
            error={apps.error}
            data={apps.data}
            isEmpty={(data) => data.plugins.length === 0}
            emptyTitle="暂无应用"
            emptyHint="系统启动时若未注册任何内置插件，这里就是空的。"
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
                          ) : (
                            <button
                              className="os-btn os-btn-primary"
                              disabled={!canManage || busyId === app.plugin_id}
                              title={canManage ? undefined : '由主账号统一管理'}
                              onClick={() => void handleActivate(app.plugin_id)}
                            >
                              {!canManage
                                ? '仅主账号可激活'
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
      </Card>
    </div>
  )
}
