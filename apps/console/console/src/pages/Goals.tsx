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

import { useCallback, useMemo, useRef, useState } from 'react'
import { ApiError, useApi } from '../lib/api'
import {
  getGoal,
  type GoalDetail,
  type GoalsResponse,
  type GoalSummary,
  type GoalTask,
} from '../lib/operator'
import { capabilityLabel, numberText, taskNameLabel } from '../lib/format'
import { goalSummaryVerdict, goalVerdict } from '../lib/goalVerdict'
import { GoalStatus } from '../components/GoalStatus'
import { ArtifactPreview } from '../components/ArtifactPreview'
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

  // 产物预览抽屉：只记路径，读取与渲染在组件里做；关闭后焦点还给"打开它的按钮"。
  const [previewPath, setPreviewPath] = useState<string | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  const openGoal = useCallback(async (id: string) => {
    setSelectedId(id)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    // 切换目标时收起上一个目标的产物预览，避免串台。
    setPreviewPath(null)
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

  const openPreview = useCallback((relPath: string, trigger: HTMLElement | null) => {
    returnFocusRef.current = trigger
    setPreviewPath(relPath)
  }, [])

  const closePreview = useCallback(() => {
    setPreviewPath(null)
    const target = returnFocusRef.current
    returnFocusRef.current = null
    // 等抽屉卸载后再还焦点，避免焦点落在已移除的节点上。
    window.setTimeout(() => target?.focus(), 0)
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
          <li>
            点列表里的某一行，打开这件任务的详情：谁在负责、当前第几步、每一步的起止与失败原因，
            以及它写出的产物；产物点开在右侧抽屉里看内容。
          </li>
          <li>任务为空就是没有分解出任务，如实显示，不编造。</li>
          <li>目标不存在（已被清除）或被 401 拦截时，会显示真实原因，而不是假装没数据。</li>
        </ul>
      </Card>

      <Card
        title="目标列表"
        sub="AI 员工真实执行过的目标（每 30 秒刷新）"
        span={selectedId ? 7 : 12}
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
          title={detail ? `任务详情 · ${detail.goal_id}` : `任务详情 · ${selectedId}`}
          sub="点开才拉取，不轮询；每一步的开始 / 结束 / 失败原因都来自本地服务的执行记录"
          span={5}
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
              // 后端字段缺失时按空数组处理：界面说「没有产物」，不崩、不编。
              const artifacts = data.artifacts ?? []
              const verdict = goalVerdict({
                state: data.state,
                artifacts,
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
                          artifacts,
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
                    <span className="os-kv-key">谁在负责</span>
                    <span className="os-kv-value">{goalEmployees(data)}</span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">当前第几步</span>
                    <span className="os-kv-value">{stepProgressText(data.tasks)}</span>
                  </div>
                  <div className="os-kv-row">
                    <span className="os-kv-key">用了哪些工具</span>
                    <span className="os-kv-value os-hint">
                      暂未提供 —— 本地服务的逐步运行记录还没上线；现在的目标详情只有任务级记录。
                    </span>
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

                </div>

                <h3 className="os-detail-title">每一步</h3>
                <ol className="os-steps">
                  {data.tasks.map((task, index) => (
                    <StepCard
                      key={task.id}
                      task={task}
                      index={index}
                      onOpenArtifact={openPreview}
                    />
                  ))}
                </ol>

                <h3 className="os-detail-title">产物</h3>
                {artifacts.length === 0 ? (
                  <p className="os-hint">此目标没有向工作区写入任何文件。</p>
                ) : (
                  <ul className="os-list">
                    {artifacts.map((relPath: string) => (
                      <li key={relPath}>
                        <button
                          className="os-link"
                          onClick={(event) => openPreview(relPath, event.currentTarget)}
                          title={relPath}
                        >
                          {relPath}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
              )
            }}
          </Guard>
        </Card>
      )}

      {previewPath && <ArtifactPreview path={previewPath} onClose={closePreview} />}
    </div>
  )
}

/**
 * 「当前第几步」：只按后端记录说话。
 *
 * 优先找 running（正在跑的那一步）；没有 running 但有 failed，就说停在哪一步；
 * 全部走完说走了几步；一条都没有就如实说没有分解。顺序 = 任务数组顺序，
 * 不另外排序 —— 那已经是后端的分解顺序。
 */
function stepProgressText(tasks: GoalTask[]): string {
  const total = tasks.length
  if (total === 0) return '暂未提供（没有分解出任务）'
  const status = (task: GoalTask) => (task.status || '').toLowerCase()
  const runningIndex = tasks.findIndex((task) => status(task) === 'running')
  if (runningIndex >= 0) return `第 ${runningIndex + 1} 步 / 共 ${total} 步（进行中）`
  const failedIndex = tasks.findIndex(
    (task) => task.success === false || status(task) === 'failed',
  )
  if (failedIndex >= 0) return `停在第 ${failedIndex + 1} 步 / 共 ${total} 步（失败）`
  const settled = tasks.filter((task) =>
    ['completed', 'failed', 'skipped', 'cancelled', 'interrupted'].includes(status(task)),
  ).length
  if (settled === total) return `已走完 ${total} 步`
  return `尚未开始（共 ${total} 步）`
}

/** 一步 = 一张卡：第几步、谁负责、起止时间、失败原因（标红）、这一步的产物。 */
function StepCard({
  task,
  index,
  onOpenArtifact,
}: {
  task: GoalTask
  index: number
  onOpenArtifact: (path: string, trigger: HTMLElement | null) => void
}) {
  const status = (task.status || '').toLowerCase()
  const failed = task.success === false || status === 'failed'
  const running = status === 'running'
  const artifacts = task.artifacts ?? []
  return (
    <li
      className={`os-step${failed ? ' os-step-failed' : ''}${running ? ' os-step-current' : ''}`}
    >
      <div className="os-step-head">
        <span className="os-step-index">第 {index + 1} 步</span>
        <span className="os-step-name">{taskNameLabel(task.name)}</span>
        <span className="os-step-status">
          <span className={`os-dot os-tone-${taskStatusTone(task.status)}`} />
          {taskStatusLabel(task.status)}
        </span>
      </div>

      <div className="os-step-meta">
        <span>负责：{task.assigned_employee || task.assigned_agent || '—'}</span>
        <span>能力：{task.capability_id ? capabilityLabel(task.capability_id) : '暂未提供'}</span>
        <span>耗时：{formatDuration(task.duration_ms)}</span>
      </div>

      <div className="os-step-time">
        开始：{formatTimestamp(task.started_at)} · 结束：
        {task.completed_at ? formatTimestamp(task.completed_at) : running ? '进行中' : '—'}
      </div>

      {failed && (
        <div className="os-step-error">
          <strong>失败在这一步</strong>
          <div>{task.error || '暂未提供'}</div>
          {task.technical_detail && (
            <details>
              <summary>技术详情</summary>
              <code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                {task.technical_detail}
              </code>
            </details>
          )}
        </div>
      )}

      {!failed && task.error && <p className="os-error">{task.error}</p>}

      {!failed && task.result && (
        <div className="os-step-result">
          <span className="os-step-label">结果</span>
          <span>{task.result}</span>
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="os-step-result">
          <span className="os-step-label">产物</span>
          <ul className="os-list">
            {artifacts.map((relPath) => (
              <li key={relPath}>
                <button
                  className="os-link"
                  onClick={(event) => onOpenArtifact(relPath, event.currentTarget)}
                  title={relPath}
                >
                  {relPath}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  )
}
