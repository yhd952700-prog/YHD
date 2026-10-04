/**
 * 目标与任务 —— AI 员工真实执行过的目标与任务分解（只读可观测面）。
 *
 * 为什么这个文件存在
 * ------------------
 * 后端 `src/gateway/ai_management.py` 早已暴露 `GET /v1/goals` 与
 * `GET /v1/goals/{goal_id}` 两个**只读**端点，但驾驶舱里没有任何页面渲染它们。
 * 人类因此看不到"我派出去的 AI 员工到底干了什么、拆成了哪些任务、成还是败"——
 * 一个对执行历史视而不见的系统谈不上可信。这个页面把这两个端点真正渲染出来。
 *
 * 诚实优先
 * --------
 * * 这是一个**只读**视图：没有任何写 / 中止 / 重规划入口。写仍由 `lib/operator.ts`
 *   里既有的主权动作完成；这里只把已存在的真实历史摊开给人看。
 * * 列表每 30 秒轮询一次（真实执行态会变）；详情是**点开才取**的手动请求，不轮询。
 * * 后端对不存在的 goal_id 返回真实 404，前端原样展示"目标不存在或已被清除"，
 *   不退化成空列表；401 由 apiFetch 统一抛出并清会话。
 * * 计时字段来自后端真实值：goal 的 `created_at` 是 Unix 秒，`started_at` /
 *   `completed_at` 是字符串时间戳。缺失就如实显示「—」，不伪装成时间。
 */

import { useCallback, useMemo, useState } from 'react'
import { ApiError, useApi } from '../lib/api'
import {
  getGoal,
  readFile,
  type GoalDetail,
  type GoalsResponse,
  type GoalSummary,
  type GoalTask,
} from '../lib/operator'
import { Card, Empty, Guard } from '../components/ui'

/** goal 状态 → 圆点色调后缀（与 Audit/Files 共用 `os-tone-*` 约定）。 */
function stateTone(state: string): string {
  const value = (state || '').toLowerCase()
  if (value === 'completed') return 'ok'
  if (value === 'failed' || value === 'cancelled') return 'danger'
  if (value === 'running') return 'warning'
  return 'muted'
}

/** task 状态 → 圆点色调后缀。 */
function taskStatusTone(status: string): string {
  const value = (status || '').toLowerCase()
  if (value === 'completed') return 'ok'
  if (value === 'failed') return 'danger'
  if (value === 'running') return 'warning'
  return 'muted'
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

/** 通用时间戳字符串（task 的 started_at / completed_at）→ 本地时间；空值显示「—」。 */
function formatTimestamp(value: string | null): string {
  if (!value) return '—'
  try {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return date.toLocaleString()
  } catch {
    return value
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

export function Goals({ query }: { query: string }) {
  // 列表：每 30 秒轮询一次真实执行态。
  const goals = useApi<GoalsResponse>('/v1/goals', 30_000)

  // 详情：手动触发，独立于列表轮询。
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<GoalDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // 产物查看：点开某个 artifact 后，直接调 `/v1/files/content` 读取真实内容。
  const [openArtifact, setOpenArtifact] = useState<string | null>(null)
  const [artifactContent, setArtifactContent] = useState<string | null>(null)
  const [artifactError, setArtifactError] = useState<string | null>(null)
  const [artifactLoading, setArtifactLoading] = useState(false)

  const openGoal = useCallback(async (id: string) => {
    setSelectedId(id)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    // 切换目标时收起上一个目标的产物视图，避免串台。
    setOpenArtifact(null)
    setArtifactContent(null)
    setArtifactError(null)
    setArtifactLoading(false)
    try {
      setDetail(await getGoal(id))
    } catch (err) {
      setDetail(null)
      // 后端对不存在的 goal_id 返回真实 404 —— 原样说明，不伪装成空。
      const status = err instanceof ApiError ? err.status : 0
      setDetailError(status === 404 ? '目标不存在或已被清除' : err instanceof Error ? err.message : '读取失败')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const openArtifactFile = useCallback(async (relPath: string) => {
    setOpenArtifact(relPath)
    setArtifactContent(null)
    setArtifactError(null)
    setArtifactLoading(true)
    try {
      // 直接调后端的文件内容端点读取真实产物（只读、经执行围栏约束）。
      const res = await readFile(relPath)
      setArtifactContent(res.content)
    } catch (err) {
      setArtifactContent(null)
      const status = err instanceof ApiError ? err.status : 0
      setArtifactError(
        status === 404
          ? '产物文件已不存在（可能已被清除）'
          : err instanceof Error
            ? err.message
            : '读取失败',
      )
    } finally {
      setArtifactLoading(false)
    }
  }, [])

  const need = query.trim().toLowerCase()
  const shown = useMemo(() => {
    const list = goals.data?.goals ?? []
    if (!need) return list
    return list.filter((goal) =>
      [goal.natural_language, goal.state, goal.scope]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [goals.data, need])

  return (
    <div className="os-grid">
      <Card title="说明" sub="这个视图能证明什么" span={12}>
        <ul className="os-list">
          <li>这里只读：每一项都来自后端真实的执行历史（`/v1/goals`），无缓存、无模拟。</li>
          <li>点列表里的某一行，会按 `goal_id` 拉取它的任务分解，右侧（下方）展示每个任务的真实状态、能力与代理。</li>
          <li>任务为空就是没有分解出任务，如实显示，不编造。</li>
          <li>目标不存在（已被清除）或被 401 拦截时，会显示后端给的真实原因，而不是假装没数据。</li>
        </ul>
      </Card>

      <Card
        title="目标列表"
        sub="AI 员工真实执行过的目标（每 30 秒刷新）"
        span={selectedId ? 8 : 12}
        action={
          selectedId ? (
            <button className="os-btn" onClick={() => setSelectedId(null)}>
              收起详情
            </button>
          ) : undefined
        }
      >
        {goals.error && !goals.loading && <p className="os-error">{goals.error}</p>}
        {goals.loading && !goals.data && <p>读取中…</p>}
        {shown.length === 0 && !goals.loading && !goals.error ? (
          <Empty
            title={need ? '没有匹配的目标' : '暂无目标'}
            hint="目标由 AI 员工执行后写入；还没有任何目标时这里就是空的。"
          />
        ) : (
          <Guard
            loading={goals.loading}
            error={goals.error}
            data={goals.data}
            isEmpty={(data) => data.goals.length === 0}
            emptyTitle="暂无目标"
            emptyHint="目标由 AI 员工执行后写入；还没有任何目标时这里就是空的。"
            onRetry={goals.reload}
          >
            {() => (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>目标</th>
                      <th>状态</th>
                      <th>进度</th>
                      <th>范围</th>
                      <th>创建时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((goal: GoalSummary) => (
                      <tr
                        key={goal.goal_id}
                        className="os-clickable"
                        onClick={() => void openGoal(goal.goal_id)}
                      >
                        <td>
                          <Truncated text={goal.natural_language} />
                        </td>
                        <td>
                          <span className={`os-dot os-tone-${stateTone(goal.state)}`} />
                          {goal.state || '—'}
                        </td>
                        <td>
                          {goal.completed_tasks}/{goal.task_count}
                          {goal.failed_tasks > 0 && (
                            <span className="os-error"> · {goal.failed_tasks} 失败</span>
                          )}
                        </td>
                        <td>{goal.scope || '—'}</td>
                        <td>{formatUnix(goal.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Guard>
        )}
        {shown.length !== (goals.data?.goals.length ?? 0) && (
          <p className="os-hint">
            搜索「{query}」过滤后显示 {shown.length} / {goals.data?.goals.length ?? 0} 项。
          </p>
        )}
      </Card>

      {selectedId && (
        <Card
          title={detail ? `任务分解 · ${detail.goal_id}` : `任务分解 · ${selectedId}`}
          sub="点击目标后手动拉取，不轮询"
          span={4}
        >
          {detailError && <p className="os-error">{detailError}</p>}
          {detailLoading && !detailError && <p>读取中…</p>}
          <Guard
            loading={detailLoading}
            error={detailError}
            data={detail}
            isEmpty={(data) => data.tasks.length === 0}
            emptyTitle="此目标没有分解出任务"
            emptyHint="后端没有为这个目标记录任何子任务。"
          >
            {(data) => (
              <>
                <div className="os-kv">
                  <div className="os-kv-row">
                    <span className="os-kv-key">状态</span>
                    <span className="os-kv-value">
                      <span className={`os-dot os-tone-${stateTone(data.state)}`} />
                      {data.state || '—'}
                    </span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">范围</span>
                    <span className="os-kv-value">{data.scope || '—'}</span>
                  </div>
                  {data.evaluation?.summary && (
                    <div className="os-kv-row">
                      <span className="os-kv-key">评估</span>
                      <span className="os-kv-value">{data.evaluation.summary}</span>
                    </div>
                  )}

                  {/* 产生的产物：目标真实写入工作区的文件（相对路径）。
                      空数组就是没产生任何文件，如实显示，不编造。 */}
                  <div className="os-kv-row os-kv-col">
                    <span className="os-kv-key">产生的产物</span>
                    <span className="os-kv-value">
                      {(!data.artifacts || data.artifacts.length === 0) ? (
                        <span className="os-hint">此目标没有向工作区写入任何文件。</span>
                      ) : (
                        <ul className="os-list">
                          {data.artifacts.map((relPath: string) => (
                            <li key={relPath}>
                              <button
                                className="os-link"
                                onClick={() => void openArtifactFile(relPath)}
                                title={relPath}
                              >
                                {relPath}
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </span>
                  </div>

                  {/* 点开后直接读取真实文件内容（只读）。 */}
                  {openArtifact && (
                    <div className="os-kv-row os-kv-col">
                      <span className="os-kv-key">产物内容 · {openArtifact}</span>
                      <span className="os-kv-value">
                        {artifactLoading && <span className="os-hint">读取中…</span>}
                        {artifactError && <span className="os-error">{artifactError}</span>}
                        {artifactContent !== null && !artifactLoading && (
                          <pre className="os-pre">{artifactContent}</pre>
                        )}
                      </span>
                    </div>
                  )}
                </div>
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>任务名</th>
                        <th>状态</th>
                        <th>能力</th>
                        <th>代理</th>
                        <th>结果 / 错误</th>
                        <th>起止时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.tasks.map((task: GoalTask) => (
                        <tr key={task.id}>
                          <td>
                            <Truncated text={task.name} max={200} />
                          </td>
                          <td>
                            <span className={`os-dot os-tone-${taskStatusTone(task.status)}`} />
                            {task.status || '—'}
                          </td>
                          <td>{task.capability_id || '—'}</td>
                          <td>{task.assigned_agent || '—'}</td>
                          <td>
                            {task.error ? (
                              <span className="os-error">{task.error}</span>
                            ) : (
                              <Truncated text={task.result ?? '—'} max={200} />
                            )}
                          </td>
                          <td>
                            {formatTimestamp(task.started_at)}
                            {task.completed_at ? ` → ${formatTimestamp(task.completed_at)}` : ''}
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
