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
import { capabilityLabel, numberText, taskNameLabel } from '../lib/format'
import { goalSummaryVerdict, goalVerdict } from '../lib/goalVerdict'
import { GoalStatus } from '../components/GoalStatus'
import { Card, Empty, Guard } from '../components/ui'

/** 任务状态 → 人话（成 / 败 / 进行中）。 */
function taskStatusLabel(status: string): string {
  const value = (status || '').toLowerCase()
  if (value === 'completed') return '成功'
  if (value === 'failed') return '失败'
  if (value === 'running') return '执行中'
  if (value === 'pending') return '等待'
  if (value === 'skipped') return '跳过'
  if (value === 'cancelled' || value === 'interrupted') return '中断'
  return status || '—'
}

/** 真实耗时（毫秒）→ 人话；缺失显示「—」，不估算。 */
function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  const value = Number(ms)
  if (!Number.isFinite(value) || value < 0) return '—'
  if (value < 1000) return `${Math.round(value)} 毫秒`
  if (value < 60_000) return `${numberText(value / 1000, 1)} 秒`
  return `${Math.floor(value / 60_000)} 分 ${Math.round((value % 60_000) / 1000)} 秒`
}

/** goal 级"谁做的"：单人指派优先，其次任务里真实出现过的员工集合。 */
function goalEmployees(goal: GoalSummary | GoalDetail): string {
  if (goal.assigned_employee) return goal.assigned_employee
  if (goal.assigned_employees && goal.assigned_employees.length > 0) {
    return goal.assigned_employees.join('、')
  }
  return '—'
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
      [goal.natural_language, goal.state, goalSummaryVerdict(goal).label, goal.scope,
        goal.assigned_employee ?? '',
        ...(goal.assigned_employees ?? [])]
        .filter((value): value is string => typeof value === 'string')
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [goals.data, need])

  return (
    <div className="os-grid">
      <Card title="说明" sub="这个视图能证明什么" span={12}>
        <ul className="os-list">
          <li>这里只读：每一项都来自真实的执行历史，无缓存、无模拟。</li>
          <li>点列表里的某一行，会拉取它的任务分解，右侧（下方）展示每个任务的真实状态、能力与执行者。</li>
          <li>任务为空就是没有分解出任务，如实显示，不编造。</li>
          <li>目标不存在（已被清除）或被 401 拦截时，会显示真实原因，而不是假装没数据。</li>
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
                      <th>员工</th>
                      <th>进度</th>
                      <th>范围</th>
                      <th>创建时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((goal: GoalSummary) => {
                      return (
                        <tr
                          key={goal.goal_id}
                          className="os-clickable"
                          onClick={() => void openGoal(goal.goal_id)}
                        >
                          <td>
                            {/* 行点击是鼠标便利；键盘用户需要一个真正的按钮，
                                否则目标列表在纯键盘下完全打不开详情。 */}
                            <button
                              className="os-link os-goal-open"
                              onClick={(event) => {
                                event.stopPropagation()
                                void openGoal(goal.goal_id)
                              }}
                            >
                              <Truncated text={goal.natural_language} />
                            </button>
                          </td>
                          <td>
                            {/* 三种终态必须一眼分得开：有产出=绿、无产出=黄、未完成=红。
                                不能把"completed 但没写文件"也涂成绿色。 */}
                            <GoalStatus goal={goal} />
                          </td>
                          <td>
                            <Truncated text={goalEmployees(goal)} max={160} />
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
                      )
                    })}
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
          {detailError && (
            <p className="os-error" role="alert">
              {detailError}
            </p>
          )}
          {detailLoading && !detailError && <p>读取中…</p>}
          <Guard
            loading={detailLoading}
            error={detailError}
            data={detail}
            isEmpty={(data) => data.tasks.length === 0}
            emptyTitle="此目标没有分解出任务"
            emptyHint="这个目标还没有记录任何子任务。"
          >
            {(data) => {
              const verdict = goalVerdict({
                state: data.state,
                artifacts: data.artifacts,
                failed_tasks: data.tasks.filter(
                  (task) => task.success === false || task.status === 'failed',
                ).length,
              })
              return (
              <>
                <div className="os-kv">
                  <div className="os-kv-row">
                    <span className="os-kv-key">状态</span>
                    <span className="os-kv-value">
                      <GoalStatus
                        goal={{
                          state: data.state,
                          artifacts: data.artifacts,
                          failed_tasks: data.tasks.filter(
                            (task) => task.success === false || task.status === 'failed',
                          ).length,
                        }}
                      />
                    </span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">下一步</span>
                    <span className={verdict.retryable ? 'os-error' : 'os-hint'}>
                      {verdict.hint}
                    </span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">范围</span>
                    <span className="os-kv-value">{data.scope || '—'}</span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">谁做的</span>
                    <span className="os-kv-value">{goalEmployees(data)}</span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">成功 / 失败</span>
                    <span className="os-kv-value">
                      成功{' '}
                      {
                        data.tasks.filter(
                          (task) => task.success === true || task.status === 'completed',
                        ).length
                      }{' '}
                      · 失败{' '}
                      {
                        data.tasks.filter(
                          (task) => task.success === false || task.status === 'failed',
                        ).length
                      }{' '}
                      · 共 {data.tasks.length} 项
                    </span>
                  </div>
                  {/* 归属项目：我的新创建入口能把目标挂到项目下，这里必须让归属**可见**，
                      否则"归属了但看不到"= 又一次 surface 与真相不一致。
                      undefined（历史目标）与 null（独立目标）刻意分开表述，不糊成一个"—"。 */}
                  <div className="os-kv-row">
                    <span className="os-kv-key">归属项目</span>
                    <span className="os-kv-value">
                      {data.project_id === undefined
                        ? '未记录归属'
                        : data.project_id === null
                          ? '独立目标（不属于任何项目）'
                          : data.project_id}
                    </span>
                  </div>
                  {data.evaluation?.summary && (
                    <div className="os-kv-row">
                      <span className="os-kv-key">评估</span>
                      <span className="os-kv-value">{data.evaluation.summary}</span>
                    </div>
                  )}

                  {/* 员工汇总：每人做了几项、成几项、败几项、合计多久。
                      这是后端按真实任务归属聚合出来的数字；没有归属就如实说没有。 */}
                  {data.employees && data.employees.length > 0 ? (
                    <div className="os-kv-row os-kv-col">
                      <span className="os-kv-key">员工汇总</span>
                      <span className="os-kv-value">
                        <ul className="os-list">
                          {data.employees.map((row) => (
                            <li key={row.employee ?? 'unattributed'}>
                              {row.employee ?? '未归属'} · 做了 {row.tasks} 项 · 成功{' '}
                              {row.completed} · 失败 {row.failed} · 合计耗时{' '}
                              {formatDuration(row.total_duration_ms)}
                            </li>
                          ))}
                        </ul>
                      </span>
                    </div>
                  ) : (
                    <div className="os-kv-row">
                      <span className="os-kv-key">员工汇总</span>
                      <span className="os-kv-value os-hint">
                        此目标没有可归属到员工的已完成任务。
                      </span>
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
                        {artifactError && (
                          <span className="os-error" role="alert">
                            {artifactError}
                          </span>
                        )}
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
                        <th>成 / 败</th>
                        <th>谁做的</th>
                        <th>耗时</th>
                        <th>能力</th>
                        <th>结果 / 错误</th>
                        <th>起止时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.tasks.map((task: GoalTask) => (
                        <tr key={task.id}>
                          <td>
                            <Truncated text={taskNameLabel(task.name)} max={200} />
                          </td>
                          <td>
                            <span className={`os-dot os-tone-${taskStatusTone(task.status)}`} />
                            {taskStatusLabel(task.status)}
                          </td>
                          <td>{task.assigned_employee || task.assigned_agent || '—'}</td>
                          <td>{formatDuration(task.duration_ms)}</td>
                          <td>{task.capability_id ? capabilityLabel(task.capability_id) : '—'}</td>
                          <td>
                            {task.error ? (
                              <>
                                <span className="os-error">{task.error}</span>
                                {task.technical_detail ? (
                                  <details style={{ marginTop: 4 }}>
                                    <summary>技术详情</summary>
                                    <code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                                      {task.technical_detail}
                                    </code>
                                  </details>
                                ) : null}
                              </>
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
              )
            }}
          </Guard>
        </Card>
      )}
    </div>
  )
}
