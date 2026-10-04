/**
 * 项目 —— 把 AI 员工真实执行过的目标归类成人类可管理的容器（产品面）。
 *
 * 为什么这个文件存在
 * ------------------
 * 后端 `src/gateway/projects.py` 暴露了 `GET /v1/projects`、`GET /v1/projects/{id}`、
 * `POST /v1/projects`、`DELETE /v1/projects/{id}` 四个端点，但驾驶舱里没有任何页面
 * 渲染它们。人类因此看不到"我派出去的目标被组织成了哪些项目"——一个对执行历史
 * 没有组织视图的系统谈不上可管理、可交付。这个页面把这四个端点真正渲染出来。
 *
 * 诚实优先
 * --------
 * * 创建 / 删除是**人类主权动作**（走带会话的 `apiFetch`，401 由它统一抛出并清会话）；
 *   读取是只读：列表每 30 秒轮询一次，详情点开才取，不轮询。
 * * 目标在创建时可挂到某个项目（`createGoal` 的 `project_id`），于是"项目"不是装饰，
 *   而是真实归类：详情端点返回项目下的真实目标摘要。没有目标就 `goal_count=0`、
 *   `goals=[]`，不编造。
 * * 后端对不存在的 project_id 返回真实 404，前端原样展示"项目不存在或已被清除"，
 *   不退化成空列表。
 */

import { useCallback, useMemo, useState } from 'react'
import { ApiError, useApi } from '../lib/api'
import {
  createProject,
  deleteProject,
  getProject,
  type CreateProjectBody,
  type ProjectDetail,
  type ProjectGoalRef,
  type ProjectsResponse,
  type ProjectSummary,
} from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

/** 项目状态（这里的"状态"指目标的聚合，沿用 goals 的色调约定）。 */
function projectStateTone(): string {
  return 'accent'
}

/** Unix 秒 → 本地时间；null / 0 / 非法值如实显示「—」。 */
function formatUnix(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds <= 0) return '—'
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

export function Projects({ query }: { query: string }) {
  // 列表：每 30 秒轮询一次真实归类态。
  const projects = useApi<ProjectsResponse>('/v1/projects', 30_000)

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
      void projects.reload()
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }, [name, description, projects])

  const handleDelete = useCallback(
    async (id: string) => {
      try {
        await deleteProject(id)
        if (selectedId === id) {
          setSelectedId(null)
          setDetail(null)
        }
        void projects.reload()
      } catch (err) {
        setCreateError(err instanceof Error ? err.message : '删除失败')
      }
    },
    [selectedId, projects],
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
          <li>这里是人类主权面：项目由你创建、删除，并把 AI 员工的目标归类到项目之下。</li>
          <li>目标在「目标与任务」里创建时可选择归属某个项目；这里如实显示每个项目下的真实目标数量与摘要。</li>
          <li>没有目标是真没有，显示「该项目下还没有目标」，不编造进展。</li>
        </ul>
      </Card>

      <Card title="新建项目" sub="人类主权：由你命名与描述" span={selectedId ? 8 : 12}>
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
        sub="你创建的真实项目（每 30 秒刷新）"
        span={selectedId ? 8 : 12}
        action={
          selectedId ? (
            <button className="os-btn" onClick={() => setSelectedId(null)}>
              收起详情
            </button>
          ) : undefined
        }
      >
        {projects.error && !projects.loading && <p className="os-error">{projects.error}</p>}
        {projects.loading && !projects.data && <p>读取中…</p>}
        {shown.length === 0 && !projects.loading && !projects.error ? (
          <Empty
            title={need ? '没有匹配的项目' : '暂无项目'}
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
                      <th>目标数</th>
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
                          <div style={{ fontWeight: 600 }}>{project.name}</div>
                          {project.description && (
                            <Truncated text={project.description} max={300} />
                          )}
                        </td>
                        <td>
                          <span className={`os-dot os-tone-${projectStateTone()}`} />
                          {project.goal_count}
                        </td>
                        <td>{formatUnix(project.created_at)}</td>
                        <td>
                          <button
                            className="os-btn os-btn-danger"
                            onClick={(e) => {
                              e.stopPropagation()
                              void handleDelete(project.project_id)
                            }}
                          >
                            删除
                          </button>
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
            emptyHint="在「目标与任务」里创建目标时选择归属本项目，这里就会出现真实的目标。"
          >
            {(data) => (
              <>
                <div className="os-kv">
                  <div className="os-kv-row">
                    <span className="os-kv-key">目标数</span>
                    <span className="os-kv-value">{data.goal_count}</span>
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
