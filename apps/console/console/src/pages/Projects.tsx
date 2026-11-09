/**
 * 项目 —— 把 AI 员工真实执行过的目标归类成人类可管理的容器（产品面）。
 *
 * 为什么这个文件存在
 * ------------------
 * 后端 `src/gateway/projects.py` 暴露了项目的完整生命周期：
 * `GET /v1/projects`（列表，可按账号视角过滤 / 含归档）、
 * `GET /v1/projects/{id}`（详情 + 目标 + 产出）、`POST /v1/projects`、
 * `POST /v1/projects/{id}/archive`、`POST /v1/projects/{id}/restore`、
 * `DELETE /v1/projects/{id}`（有目标时先 409 报影响面，`force=true` 才删）。
 * 这个页面把这条真实路径完整渲染出来 —— 否则人类能建项目却管理不了它。
 *
 * 诚实优先
 * --------
 * * 每行显示的后端真实字段：关联目标数、最近活动时间、产出路径（含归属账号）。
 *   没有目标 / 产出就显示 0 与「—」，不编造进展。
 * * 删除有目标的项目时，后端先 409 返回影响面；界面把这段中文说明原样展示，
 *   用户二次确认后才带 `force=true` 重发 —— 不会一键清空装满历史的容器。
 * * 归档只是从默认列表隐藏，详情和产出都保留；「显示已归档」可随时恢复。
 * * 主账号可切到某个成员视角（服务端 `view_account` 过滤）；成员传他人视角
 *   会被后端中性 404，界面不制造这种注定被拒的调用。
 */

import { useCallback, useMemo, useState } from 'react'
import { ApiError, useApi } from '../lib/api'
import {
  archiveProject,
  createProject,
  deleteProject,
  getProject,
  restoreProject,
  type CreateProjectBody,
  type ProjectDetail,
  type ProjectGoalRef,
  type ProjectsResponse,
  type ProjectSummary,
} from '../lib/operator'
import { flattenTree, loadTree, useAccountData } from '../lib/accounts'
import { getSession } from '../lib/auth'
import { Card, Empty, Guard } from '../components/ui'

/** Unix 秒 → 本地时间；null / 0 / 非法值如实显示「—」。 */
function formatUnix(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return '—'
  }
  try {
    return new Date(seconds * 1000).toLocaleString()
  } catch {
    return String(seconds)
  }
}

/** 把超长文本截断成单行省略号，并保留完整内容做 tooltip。 */
function Truncated({ text, max = 360 }: { text: string; max?: number }) {
  return (
    <span
      title={text}
      style={{
        display: 'block',
        maxWidth: max,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {text || '—'}
    </span>
  )
}

function ArchivedBadge() {
  return (
    <span className="os-dot" style={{ background: '#8a8f98' }} title="已归档：仅从默认列表隐藏，数据与产出都保留" />
  )
}

export function Projects({ query }: { query: string }) {
  const session = useMemo(() => getSession(), [])
  const isRoot = Boolean(session?.isRoot)

  // 列表筛选：是否显示归档、主账号切到哪个成员视角。
  const [showArchived, setShowArchived] = useState(false)
  const [viewAccount, setViewAccount] = useState<string | null>(null)
  const listPath = useMemo(() => {
    const params = new URLSearchParams()
    if (showArchived) params.set('include_archived', 'true')
    if (viewAccount) params.set('view_account', viewAccount)
    const qs = params.toString()
    return `/v1/projects${qs ? `?${qs}` : ''}`
  }, [showArchived, viewAccount])
  const projects = useApi<ProjectsResponse>(listPath, 30_000)

  // 主账号的视角选择器：只取子树成员（含自己）；成员不请求树。
  const tree = useAccountData(
    () =>
      isRoot && session
        ? loadTree({ principal: session.principal, isRoot: true })
        : null,
    session?.principal ?? null,
  )
  const viewOptions = useMemo(() => {
    const root = tree.data?.tree
    if (!root) return []
    return flattenTree(root)
      .filter((node) => node.principal !== session?.principal)
      .map((node) => node.principal)
  }, [tree.data, session?.principal])

  // 详情：手动触发，独立于列表轮询。
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // 创建表单。
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  // 行内动作（归档 / 恢复 / 删除）的错误。
  const [actionError, setActionError] = useState<string | null>(null)

  const refresh = useCallback(() => {
    void projects.reload()
  }, [projects])

  const openProject = useCallback(async (id: string) => {
    setSelectedId(id)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    try {
      setDetail(await getProject(id))
    } catch (err) {
      setDetail(null)
      const status = err instanceof ApiError ? err.status : 0
      setDetailError(status === 404 ? '项目不存在或已被清除' : err instanceof Error ? err.message : '读取失败')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleCreate = useCallback(async () => {
    const trimmed = name.trim()
    if (!trimmed) {
      setCreateError('项目名称不能为空')
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      await createProject({ name: trimmed, description: description.trim() } satisfies CreateProjectBody)
      setName('')
      setDescription('')
      setSelectedId(null)
      setDetail(null)
      refresh()
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }, [name, description, refresh])

  const handleArchive = useCallback(
    async (id: string, restore: boolean) => {
      setActionError(null)
      try {
        if (restore) await restoreProject(id)
        else await archiveProject(id)
        if (selectedId === id) void openProject(id)
        refresh()
      } catch (err) {
        setActionError(err instanceof Error ? err.message : '操作失败')
      }
    },
    [selectedId, openProject, refresh],
  )

  const handleDelete = useCallback(
    async (id: string, label: string) => {
      setActionError(null)
      try {
        await deleteProject(id)
      } catch (err) {
        // 有目标关联：后端先给 409 + 影响面。把真实说明摆给用户，确认后再强制删除。
        if (err instanceof ApiError && err.status === 409 && err.code === 'project_has_goals') {
          const ok = window.confirm(`删除「${label}」前请确认影响面：\n\n${err.message}`)
          if (!ok) return
          try {
            await deleteProject(id, true)
          } catch (forceErr) {
            setActionError(forceErr instanceof Error ? forceErr.message : '删除失败')
            return
          }
        } else {
          setActionError(err instanceof Error ? err.message : '删除失败')
          return
        }
      }
      if (selectedId === id) {
        setSelectedId(null)
        setDetail(null)
      }
      refresh()
    },
    [selectedId, refresh],
  )

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    const list = projects.data?.projects ?? []
    if (!need) return list
    return list.filter((project) =>
      [project.name, project.description]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [projects.data, need])

  return (
    <div className="os-grid">
      <Card title="说明" sub="这个视图能证明什么" span={12}>
        <ul className="os-list">
          <li>这里是项目：由你创建、归档、恢复、删除，并把 AI 员工的目标归类到项目之下。</li>
          <li>「关联目标」是真实挂在该项目下的目标数；「最近活动」取项目与目标时间的最晚值；「产出」是目标真实写出的产物路径与归属账号。</li>
          <li>没有目标是真没有，显示「该项目下还没有目标」，不编造进展。</li>
          <li>删除有目标的项目会先提示影响面：目标本身与产出不会被删除，只解除项目归属。</li>
        </ul>
      </Card>

      <Card title="新建项目" sub="由你命名与描述" span={selectedId ? 8 : 12}>
        <div className="os-form">
          <label className="os-field">
            <span className="os-field-label">项目名称</span>
            <input
              className="os-input"
              value={name}
              maxLength={200}
              placeholder="例如：Q4 客户交付"
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="os-field">
            <span className="os-field-label">描述（可选）</span>
            <textarea
              className="os-input"
              value={description}
              maxLength={2000}
              rows={2}
              placeholder="这个项目的目标是什么"
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          {createError && <p className="os-error">{createError}</p>}
          <div>
            <button className="os-btn os-btn-primary" onClick={() => void handleCreate()} disabled={creating}>
              {creating ? '创建中…' : '创建项目'}
            </button>
          </div>
        </div>
      </Card>

      <Card
        title="项目列表"
        sub={viewAccount ? `以 ${viewAccount} 的视角查看（服务端按归属过滤）` : '你可见的真实项目（每 30 秒刷新）'}
        span={selectedId ? 8 : 12}
        action={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {isRoot && viewOptions.length > 0 && (
              <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
                <span>视角</span>
                <select
                  className="os-input"
                  value={viewAccount ?? ''}
                  onChange={(e) => {
                    setViewAccount(e.target.value || null)
                    setSelectedId(null)
                    setDetail(null)
                  }}
                  style={{ minWidth: 120 }}
                >
                  <option value="">主账号（全部）</option>
                  {viewOptions.map((principal) => (
                    <option key={principal} value={principal}>
                      {principal}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="os-check-hit" style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(e) => setShowArchived(e.target.checked)}
              />
              <span>显示已归档</span>
            </label>
            {selectedId ? (
              <button className="os-btn" onClick={() => setSelectedId(null)}>
                收起详情
              </button>
            ) : undefined}
          </div>
        }
      >
        {actionError && <p className="os-error">{actionError}</p>}
        {shown.length === 0 && !projects.loading && !projects.error ? (
          <Empty
            title={need ? '没有匹配的项目' : showArchived ? '暂无项目' : '暂无项目'}
            hint="用上方「新建项目」创建一个；还没有任何项目时这里就是空的。"
          />
        ) : (
          <Guard
            loading={projects.loading}
            error={projects.error}
            data={projects.data}
            isEmpty={(data) => data.projects.length === 0}
            emptyTitle="暂无项目"
            emptyHint="用上方「新建项目」创建一个；还没有任何项目时这里就是空的。"
            onRetry={projects.reload}
          >
            {() => (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>项目名称</th>
                      <th>关联目标</th>
                      <th>最近活动</th>
                      <th>产出</th>
                      <th>创建时间</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((project: ProjectSummary) => (
                      <tr
                        key={project.project_id}
                        className="os-clickable"
                        onClick={() => void openProject(project.project_id)}
                      >
                        <td>
                          <div style={{ fontWeight: 600, display: 'flex', gap: 6, alignItems: 'center' }}>
                            {project.archived_at ? <ArchivedBadge /> : null}
                            {project.name}
                          </div>
                          {project.description && (
                            <Truncated text={project.description} max={300} />
                          )}
                        </td>
                        <td>{project.goal_count}</td>
                        <td>{formatUnix(project.last_activity_at)}</td>
                        <td>
                          {project.output_count === 0 ? (
                            '—'
                          ) : (
                            <div>
                              <div>{project.output_count} 个</div>
                              {project.outputs[0] && (
                                <Truncated
                                  text={`${project.outputs[0].path}（${project.outputs[0].owner}）`}
                                  max={220}
                                />
                              )}
                              {project.outputs_truncated && (
                                <span className="os-hint">还有更多，点开详情查看</span>
                              )}
                            </div>
                          )}
                        </td>
                        <td>{formatUnix(project.created_at)}</td>
                        <td>
                          <div style={{ display: 'flex', gap: 6 }} onClick={(e) => e.stopPropagation()}>
                            {project.archived_at ? (
                              <button
                                className="os-btn"
                                onClick={() => void handleArchive(project.project_id, true)}
                              >
                                恢复
                              </button>
                            ) : (
                              <button
                                className="os-btn"
                                onClick={() => void handleArchive(project.project_id, false)}
                              >
                                归档
                              </button>
                            )}
                            <button
                              className="os-btn os-btn-danger"
                              onClick={() => void handleDelete(project.project_id, project.name)}
                            >
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Guard>
        )}
        {shown.length !== (projects.data?.projects.length ?? 0) && (
          <p className="os-hint">
            搜索「{query}」过滤后显示 {shown.length} / {projects.data?.projects.length ?? 0} 项。
          </p>
        )}
      </Card>

      {selectedId && (
        <Card
          title={detail ? `项目详情 · ${detail.name}` : `项目详情 · ${selectedId}`}
          sub="点击项目后手动拉取，不轮询"
          span={4}
        >
          {detailError && <p className="os-error">{detailError}</p>}
          {detailLoading && !detailError && <p>读取中…</p>}
          <Guard
            loading={detailLoading}
            error={detailError}
            data={detail}
            isEmpty={(data) => data.goals.length === 0}
            emptyTitle="该项目下还没有目标"
            emptyHint="在「我的员工 → 运行时 → 创建目标」里，用「归属项目」选择本项目后创建目标，这里就会出现真实的目标。"
          >
            {(data) => (
              <>
                <div className="os-kv">
                  <div className="os-kv-row">
                    <span className="os-kv-key">状态</span>
                    <span className="os-kv-value">
                      {data.archived_at ? `已归档（${formatUnix(data.archived_at)}）` : '进行中'}
                    </span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">关联目标</span>
                    <span className="os-kv-value">{data.goal_count}</span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">最近活动</span>
                    <span className="os-kv-value">{formatUnix(data.last_activity_at)}</span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">创建时间</span>
                    <span className="os-kv-value">{formatUnix(data.created_at)}</span>
                  </div>
                  {data.description && (
                    <div className="os-kv-row">
                      <span className="os-kv-key">描述</span>
                      <span className="os-kv-value">{data.description}</span>
                    </div>
                  )}
                </div>
                <div style={{ margin: '10px 0 6px', fontWeight: 600 }}>
                  产出（{data.output_count}）
                </div>
                {data.output_count === 0 ? (
                  <p className="os-hint">该项目下的目标还没有写出任何产物。</p>
                ) : (
                  <div className="os-table-wrap">
                    <table className="os-table">
                      <thead>
                        <tr>
                          <th>产出路径</th>
                          <th>归属账号</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.outputs.map((output) => (
                          <tr key={`${output.goal_id ?? 'na'}:${output.path}`}>
                            <td>
                              <Truncated text={output.path} max={260} />
                            </td>
                            <td>{output.owner || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <div style={{ margin: '10px 0 6px', fontWeight: 600 }}>目标</div>
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>目标</th>
                        <th>状态</th>
                        <th>进度</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.goals.map((goal: ProjectGoalRef) => (
                        <tr key={goal.goal_id}>
                          <td>
                            <Truncated text={goal.natural_language} max={220} />
                          </td>
                          <td>{goal.state || '—'}</td>
                          <td>
                            {goal.completed_tasks}/{goal.task_count}
                            {goal.failed_tasks > 0 && (
                              <span className="os-error"> · {goal.failed_tasks} 失败</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Guard>
        </Card>
      )}
    </div>
  )
}
