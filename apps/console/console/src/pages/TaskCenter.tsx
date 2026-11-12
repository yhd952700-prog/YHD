/**
 * 任务中心 —— 让人真正能用的任务台账界面（只读浏览 + 两个受控动作）。
 *
 * 数据全部来自已挂载的 `/v1/tasks*`（见 `src/gateway/tasks_api.py`）：
 * 七态筛选、逐步时间线、失败重试、人工接管、台账迁移状态。
 *
 * 诚实边界
 * --------
 * * 后端没有优先级（priority）与截止时间（due）字段，列表就显示「暂未提供」，
 *   不填占位数字；接口没上线（404）或响应缺 `items` 时，整块说
 *   「暂未提供 —— 本地服务的任务台账还没上线」，不画一张空表冒充"没有任务"。
 * * 列表进度来自真实子步骤（`/v1/tasks/{id}/steps`），取不到就是「暂未提供」，
 *   不按状态猜百分比。
 * * 重试与接管都必须先确认再发请求；结果只报后端真实返回，失败原样显示原因。
 *
 * 路由登记
 * --------
 * 本轮不改 `lib/nav.ts` 与 `App.tsx`（工作树里有别的会话的未提交改动），
 * 入口信息以导出常量 `TASK_CENTER_PAGE` 的形式留在这里，最后由统一接线的人接。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './TaskCenter.css'
import { ApiError, useApi } from '../lib/api'
import { capabilityLabel, taskNameLabel } from '../lib/format'
import {
  TASK_STATUSES,
  assigneeText,
  canRetryTask,
  canTakeoverTask,
  durationBetween,
  formatStamp,
  getTaskDetail,
  getTaskSteps,
  parseLedgerStatus,
  parseTasksPayload,
  progressOf,
  progressText,
  retryTaskStep,
  skipReasonLabel,
  takeoverTask,
  taskStatusLabel,
  taskStatusTone,
  tasksPath,
  type LedgerStep,
  type LedgerTask,
  type LedgerTaskDetail,
  type MigrationRecord,
  type StepProgress,
} from '../lib/taskLedger'
import { Card, Empty, Guard } from '../components/ui'

/** 页面登记信息：等 `nav.ts` / `App.tsx` 统一接线时直接引用，避免两处各写一份。 */
export const TASK_CENTER_PAGE = {
  key: 'task-center',
  navLabel: '任务中心',
  title: '任务中心',
  sub: '七态任务、逐步时间线与失败处理；失败可重试或人工接管',
} as const

/** 接口未上线 / 响应字段缺失时的统一说法（不造假数据）。 */
const LEDGER_OFFLINE = '暂未提供 —— 本地服务的任务台账还没上线'
/** 单个字段没上线时的说法；与整块未上线分开，避免说了不真实的原因。 */
const FIELD_MISSING = '暂未提供'
const FIELD_MISSING_HINT = '任务台账接口当前没有返回这个字段'

const PAGE_SIZE = 20
const MAX_PAGE_SIZE = 200
const PROGRESS_BATCH = 20

type PendingAction =
  | { kind: 'retry'; stepKey: string | null }
  | { kind: 'takeover' }

interface Feedback {
  tone: 'ok' | 'danger'
  text: string
}

function progressKey(task: LedgerTask): string {
  return `${task.updated_at ?? ''}|${task.status}`
}

function errorText(error: unknown): string {
  if (error instanceof Error && error.message) return error.message
  return '操作没有完成，请稍后重试。'
}

function actionErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '当前账号无权执行这个操作，请换用有权限的账号。'
    if (error.status === 404) return '任务或步骤已不存在，可能已被清理；刷新后再看。'
    if (error.status === 409) return error.message || '这个操作与任务当前状态冲突，未执行。'
    return error.message || '操作没有完成，请稍后重试。'
  }
  return errorText(error)
}

export function TaskCenter({ query }: { query: string }) {
  const [statusFilter, setStatusFilter] = useState('')
  const [limit, setLimit] = useState(PAGE_SIZE)
  const path = useMemo(() => tasksPath(statusFilter, limit), [statusFilter, limit])
  const raw = useApi<unknown>(path, 30_000)
  const payload = useMemo(() => parseTasksPayload(raw.data), [raw.data])
  // 端点没上线（404/405）或响应结构不对：整块如实说未上线，不画空表。
  const offline =
    raw.status === 404 || raw.status === 405 || (raw.data !== null && payload === null)

  // 详情：点开才取，不轮询（与「目标与任务」页同一口径）。
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<LedgerTaskDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  // 两个动作：先确认（可填原因），再发请求；结果只报后端真实返回。
  const [pending, setPending] = useState<PendingAction | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<Feedback | null>(null)

  const openTask = useCallback(async (id: string) => {
    setSelectedId(id)
    setDetail(null)
    setDetailError(null)
    setDetailLoading(true)
    setPending(null)
    setReason('')
    setFeedback(null)
    try {
      setDetail(await getTaskDetail(id))
    } catch (err) {
      setDetail(null)
      const status = err instanceof ApiError ? err.status : 0
      setDetailError(
        status === 404 ? '任务不存在或已被清理' : actionErrorText(err),
      )
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const reloadDetail = useCallback(async (id: string) => {
    try {
      setDetail(await getTaskDetail(id))
      setDetailError(null)
    } catch (err) {
      setDetailError(actionErrorText(err))
    }
  }, [])

  const submitAction = useCallback(async () => {
    if (!pending || !selectedId || busy) return
    setBusy(true)
    setFeedback(null)
    try {
      if (pending.kind === 'retry') {
        const result = await retryTaskStep(selectedId, pending.stepKey, reason.trim())
        setFeedback({
          tone: 'ok',
          text: `已提交重试：子步骤 ${result.step_key ?? pending.stepKey ?? '（本地服务未返回）'}；目标当前状态 ${result.goal_state ?? '（本地服务未返回）'}。下方时间线会按最新记录刷新。`,
        })
      } else {
        await takeoverTask(selectedId, reason.trim())
        setFeedback({
          tone: 'ok',
          text: '已转由人工接手：任务状态落为「待执行」，系统不再自动执行它。',
        })
      }
      setPending(null)
      setReason('')
      await reloadDetail(selectedId)
      raw.reload()
    } catch (err) {
      setFeedback({ tone: 'danger', text: actionErrorText(err) })
    } finally {
      setBusy(false)
    }
  }, [busy, pending, raw, reason, reloadDetail, selectedId])

  const need = query.trim().toLowerCase()
  const tasks = useMemo(() => {
    const list = payload?.items ?? []
    if (!need) return list
    return list.filter((task) =>
      [
        task.title,
        task.goal_id,
        task.task_id,
        assigneeText(task.assignee),
        taskStatusLabel(task.status),
      ]
        .filter(Boolean)
        .some((value) => value.toLowerCase().includes(need)),
    )
  }, [payload, need])

  // 列表进度：真实子步骤 → 「已完成/总数」。按 (task_id, updated_at) 缓存，
  // 只有任务确实变了才重新取，避免每 30 秒把同一批步骤再拉一遍。
  const cacheRef = useRef<Record<string, { key: string; value: StepProgress | null }>>({})
  const [progress, setProgress] = useState<
    Record<string, { key: string; value: StepProgress | null }>
  >({})
  const runningRef = useRef(false)
  useEffect(() => {
    const targets = tasks
      .filter((task) => cacheRef.current[task.task_id]?.key !== progressKey(task))
      .slice(0, PROGRESS_BATCH)
    if (targets.length === 0 || runningRef.current) return
    runningRef.current = true
    let alive = true
    void (async () => {
      for (const task of targets) {
        let value: StepProgress | null = null
        try {
          const steps = await getTaskSteps(task.task_id)
          value = steps === null ? null : progressOf(steps)
        } catch {
          // 取不到就说「暂未提供」，不按状态猜一个百分比。
          value = null
        }
        cacheRef.current[task.task_id] = { key: progressKey(task), value }
      }
      runningRef.current = false
      if (alive) setProgress({ ...cacheRef.current })
    })()
    return () => {
      alive = false
    }
  }, [tasks])

  const pickStatus = useCallback((value: string) => {
    setStatusFilter(value)
    setLimit(PAGE_SIZE)
  }, [])

  const total = payload?.total ?? 0
  const canLoadMore = payload !== null && limit < MAX_PAGE_SIZE && total > (payload?.items.length ?? 0)

  return (
    <div className="os-grid">
      <Card title="说明" sub="这个页面能看到什么、看不到什么" span={12}>
        <ul className="os-list">
          <li>任务来自本地台账（`/v1/tasks`），七态齐全：待分配、待执行、执行中、待审核、已完成、失败、已取消。</li>
          <li>优先级与截止时间：台账接口当前没有这两个字段，如实显示「暂未提供」，不填占位值。</li>
          <li>进度按真实子步骤计算；取不到子步骤时同样显示「暂未提供」。</li>
          <li>失败任务可重试或转人工接管，两者都会先请你确认，结果按本地服务真实返回展示。</li>
        </ul>
      </Card>

      <Card
        title="任务列表"
        sub="每 30 秒刷新；点一行看逐步时间线"
        span={selectedId ? 7 : 12}
        action={
          selectedId ? (
            <button className="os-btn" type="button" onClick={() => setSelectedId(null)}>
              收起详情
            </button>
          ) : undefined
        }
      >
        <div className="tc-tabs" role="group" aria-label="按状态筛选任务">
          <button
            type="button"
            className={`tc-tab${statusFilter === '' ? ' tc-tab-active' : ''}`}
            aria-pressed={statusFilter === ''}
            onClick={() => pickStatus('')}
          >
            全部
          </button>
          {TASK_STATUSES.map((status) => (
            <button
              key={status.value}
              type="button"
              className={`tc-tab${statusFilter === status.value ? ' tc-tab-active' : ''}`}
              aria-pressed={statusFilter === status.value}
              onClick={() => pickStatus(status.value)}
            >
              {status.label}
            </button>
          ))}
        </div>

        {offline ? (
          <p className="tc-missing-block" role="status">
            {LEDGER_OFFLINE}
            {raw.error ? <span className="tc-sub">{raw.error}</span> : null}
          </p>
        ) : (
          <>
            {shownSummary(payload, tasks.length, need, total)}
            {tasks.length === 0 && !raw.loading && !raw.error ? (
              <Empty
                title={need ? '没有匹配的任务' : '当前筛选下没有任务'}
                hint={
                  need
                    ? '换个关键词，或点「全部」看所有状态。'
                    : '台账里这个状态确实还没有任务；换一个状态看看，或者等执行产生记录。'
                }
              />
            ) : (
              <Guard
                loading={raw.loading}
                error={raw.error}
                data={payload}
                onRetry={raw.reload}
              >
                {() => (
                  <div className="os-table-wrap">
                    <table className="os-table">
                      <thead>
                        <tr>
                          <th>任务</th>
                          <th>状态</th>
                          <th>负责人</th>
                          <th>优先级</th>
                          <th>截止时间</th>
                          <th>进度</th>
                          <th>创建时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tasks.map((task) => (
                          <tr
                            key={task.task_id}
                            className="os-clickable"
                            onClick={() => void openTask(task.task_id)}
                          >
                            <td>
                              <button
                                type="button"
                                className="os-link tc-task-open"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  void openTask(task.task_id)
                                }}
                              >
                                <span className="tc-task-title">
                                  {task.title || '未命名任务'}
                                </span>
                              </button>
                              {task.human_hold && <span className="tc-flag">人工接管中</span>}
                            </td>
                            <td>
                              <span className="os-badge" data-tone={taskStatusTone(task.status)}>
                                {taskStatusLabel(task.status)}
                              </span>
                            </td>
                            <td>{assigneeText(task.assignee)}</td>
                            <td>
                              <MissingField />
                            </td>
                            <td>
                              <MissingField />
                            </td>
                            <td>
                              <ProgressCell
                                entry={progress[task.task_id]}
                                fresh={progressKey(task)}
                              />
                            </td>
                            <td>{formatStamp(task.created_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Guard>
            )}
            {canLoadMore && (
              <button
                type="button"
                className="os-btn tc-load-more"
                onClick={() => setLimit((value) => Math.min(value + PAGE_SIZE, MAX_PAGE_SIZE))}
              >
                再多看 {Math.min(PAGE_SIZE, MAX_PAGE_SIZE - limit)} 条
              </button>
            )}
          </>
        )}
      </Card>

      {selectedId && (
        <Card
          title={detail ? `任务详情 · ${detail.title || detail.task_id}` : `任务详情 · ${selectedId}`}
          sub="逐步时间线来自台账真实记录；失败步骤整卡标红并单列原因"
          span={5}
        >
          {detailError && (
            <p className="tc-error" role="alert">
              {detailError}
            </p>
          )}
          {detailLoading && !detailError && <p>读取中…</p>}

          {feedback && (
            <p
              className={feedback.tone === 'ok' ? 'tc-feedback tc-feedback-ok' : 'tc-feedback tc-feedback-error'}
              role={feedback.tone === 'ok' ? 'status' : 'alert'}
            >
              {feedback.text}
            </p>
          )}

          {pending && (
            <div
              className="tc-confirm"
              role="dialog"
              aria-modal="false"
              aria-label={pending.kind === 'retry' ? '确认重试' : '确认人工接管'}
            >
              <strong>
                {pending.kind === 'retry'
                  ? `确认重试子步骤 ${pending.stepKey ?? '（由本地服务选择第一个失败步骤）'}？`
                  : '确认转由人工接管？'}
              </strong>
              <p className="tc-sub">
                {pending.kind === 'retry'
                  ? '重试会把这一步交回执行链重新跑一次，可能再次失败。'
                  : '接管后任务不再自动执行，等待你人工处理。'}
              </p>
              <label className="tc-reason-label">
                原因（可留空）
                <textarea
                  className="tc-reason"
                  value={reason}
                  rows={2}
                  maxLength={500}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <div className="tc-confirm-actions">
                <button
                  type="button"
                  className="os-btn"
                  disabled={busy}
                  onClick={() => void submitAction()}
                >
                  {busy ? '提交中…' : pending.kind === 'retry' ? '确认重试' : '确认接管'}
                </button>
                <button
                  type="button"
                  className="os-btn os-btn-ghost"
                  disabled={busy}
                  onClick={() => {
                    setPending(null)
                    setReason('')
                  }}
                >
                  取消
                </button>
              </div>
            </div>
          )}

          <Guard loading={detailLoading} error={detailError} data={detail}>
            {(data) => (
              <>
                <dl className="os-kv">
                  <dt>状态</dt>
                  <dd>
                    <span className="os-badge" data-tone={taskStatusTone(data.status)}>
                      {taskStatusLabel(data.status)}
                    </span>
                    {data.human_hold && <span className="tc-flag">人工接管中</span>}
                    {data.archived && <span className="tc-flag">已归档</span>}
                  </dd>
                  <dt>负责人</dt>
                  <dd>{assigneeText(data.assignee)}</dd>
                  <dt>目标</dt>
                  <dd>{data.goal_id || '—'}</dd>
                  <dt>尝试次数</dt>
                  <dd>{data.attempt_count}</dd>
                  <dt>创建 / 开始 / 完成</dt>
                  <dd>
                    {formatStamp(data.created_at)} · {formatStamp(data.started_at)} ·{' '}
                    {formatStamp(data.completed_at)}
                  </dd>
                  <dt>优先级 / 截止时间</dt>
                  <dd className="tc-missing" title={FIELD_MISSING_HINT}>
                    {FIELD_MISSING}
                  </dd>
                  {data.error && (
                    <>
                      <dt>失败原因</dt>
                      <dd className="tc-error">{data.error}</dd>
                    </>
                  )}
                </dl>

                <div className="tc-actions">
                  {canRetryTask(data) && (
                    <button
                      type="button"
                      className="os-btn"
                      onClick={() => {
                        setFeedback(null)
                        setReason('')
                        setPending({ kind: 'retry', stepKey: null })
                      }}
                    >
                      重试失败步骤
                    </button>
                  )}
                  {canTakeoverTask(data) && (
                    <button
                      type="button"
                      className="os-btn os-btn-ghost"
                      onClick={() => {
                        setFeedback(null)
                        setReason('')
                        setPending({ kind: 'takeover' })
                      }}
                    >
                      人工接管
                    </button>
                  )}
                  {data.status === 'running' && (
                    <span className="tc-sub">执行中的任务不能接管，等它结束或失败后再操作。</span>
                  )}
                  {data.human_hold && (
                    <span className="tc-sub">已被人工接管：自动重试与自动执行都不会覆盖你。</span>
                  )}
                  {data.status !== 'failed' && !data.human_hold && data.status !== 'running' && (
                    <span className="tc-sub">只有失败任务能重试；当前状态没有可重试内容。</span>
                  )}
                </div>

                <h3 className="os-detail-title">逐步时间线</h3>
                {data.steps.length === 0 ? (
                  <Empty
                    title="这个任务没有子步骤"
                    hint="台账里这条任务没有拆出子步骤；任务级失败原因在上面，重试仍可用。"
                  />
                ) : (
                  <ol className="tc-timeline">
                    {data.steps.map((step, index) => (
                      <StepCard
                        key={step.step_key || step.step_id || index}
                        step={step}
                        index={index}
                        onRetry={
                          step.status === 'failed' && !data.human_hold
                            ? () => {
                                setFeedback(null)
                                setReason('')
                                setPending({ kind: 'retry', stepKey: step.step_key })
                              }
                            : undefined
                        }
                      />
                    ))}
                  </ol>
                )}
              </>
            )}
          </Guard>
        </Card>
      )}

      <LedgerStatusCard />
    </div>
  )
}

/** 列表说明行：总数 / 可见范围 / 只加载了多少条，全部用真实数字。 */
function shownSummary(
  payload: { total: number; items: LedgerTask[]; visible_scope?: string } | null,
  shown: number,
  searched: string,
  total: number,
) {
  if (!payload) return null
  const scope =
    payload.visible_scope === 'self'
      ? '只包含你自己的任务'
      : payload.visible_scope
        ? `可见范围：${payload.visible_scope}`
        : ''
  return (
    <p className="os-hint tc-summary">
      共 {total} 条
      {payload.items.length < total
        ? `，当前加载 ${payload.items.length} 条（按创建时间倒序）`
        : ''}
      {searched ? `；搜索「${searched}」命中 ${shown} 条` : ''}
      {scope ? `；${scope}` : ''}
    </p>
  )
}

function MissingField() {
  return (
    <span className="tc-missing" title={FIELD_MISSING_HINT}>
      {FIELD_MISSING}
    </span>
  )
}

/** 进度单元格：只画真实子步骤数；缓存过期或取数失败都说「暂未提供」。 */
function ProgressCell({
  entry,
  fresh,
}: {
  entry: { key: string; value: StepProgress | null } | undefined
  fresh: string
}) {
  if (!entry || entry.key !== fresh) {
    return (
      <span className="tc-missing" title="子步骤进度还在读取，或这次没有取到">
        {FIELD_MISSING}
      </span>
    )
  }
  const value = entry.value
  if (value === null) {
    return (
      <span className="tc-missing" title="取子步骤失败；不代表这一步没有进度">
        {FIELD_MISSING}
      </span>
    )
  }
  const label = progressText(value)
  if (value.total === 0) return <span className="tc-sub">{label}</span>
  const percent = Math.round((value.done / value.total) * 100)
  return (
    <span className="tc-progress">
      <span className="tc-progress-text">
        {label}
        {value.failed > 0 && <span className="tc-error"> · {value.failed} 步失败</span>}
        {value.running > 0 && <span className="tc-sub"> · 执行中</span>}
      </span>
      <span className="tc-bar" aria-hidden="true">
        <span
          className={`tc-bar-fill${value.failed > 0 ? ' tc-bar-fill-danger' : ''}`}
          style={{ width: `${percent}%` }}
        />
      </span>
    </span>
  )
}

/** 一步 = 一张卡；失败步骤整卡标红并把原因单列出来。 */
function StepCard({
  step,
  index,
  onRetry,
}: {
  step: LedgerStep
  index: number
  onRetry?: () => void
}) {
  const failed = step.status === 'failed'
  const running = step.status === 'running'
  const seq = step.seq > 0 ? step.seq : index + 1
  const duration = durationBetween(step.started_at, step.completed_at)
  return (
    <li className={`tc-step${failed ? ' tc-step-failed' : ''}${running ? ' tc-step-running' : ''}`}>
      <div className="tc-step-head">
        <span className="tc-step-index">第 {seq} 步</span>
        <span className="tc-step-name">{taskNameLabel(step.name)}</span>
        <span className="os-badge" data-tone={taskStatusTone(step.status)}>
          {taskStatusLabel(step.status)}
        </span>
        {step.human_hold && <span className="tc-flag">人工接管</span>}
      </div>

      <div className="tc-step-meta">
        <span>负责人：{assigneeText(step.assignee)}</span>
        <span>
          能力：
          {step.capability_id ? (
            <span title={step.capability_id}>{capabilityLabel(step.capability_id)}</span>
          ) : (
            '—'
          )}
        </span>
        <span>耗时：{duration ?? (running ? '进行中' : '—')}</span>
        <span>尝试：{step.attempt_count}</span>
      </div>

      <div className="tc-step-time">
        开始：{formatStamp(step.started_at)} · 结束：
        {step.completed_at ? formatStamp(step.completed_at) : running ? '进行中' : '—'}
      </div>

      {failed && (
        <div className="tc-step-error">
          <strong>失败在这一步</strong>
          <div>{step.error || '本地服务没有给出失败原因'}</div>
          {step.technical_detail && (
            <details>
              <summary>技术详情</summary>
              <code>{step.technical_detail}</code>
            </details>
          )}
          {onRetry && (
            <button type="button" className="os-btn tc-step-retry" onClick={onRetry}>
              重试这一步
            </button>
          )}
        </div>
      )}

      {!failed && step.error && <p className="tc-error">{step.error}</p>}
    </li>
  )
}

/** 台账迁移状态：导入了多少、跳过多少、落后多少；没有原因就说没有。 */
function LedgerStatusCard() {
  const raw = useApi<unknown>('/v1/tasks/ledger/status', 60_000)
  const data = useMemo(() => parseLedgerStatus(raw.data), [raw.data])
  const forbidden = raw.status === 403
  const offline = raw.status === 404 || raw.status === 405 || (raw.data !== null && data === null)

  return (
    <Card
      title="台账迁移状态"
      sub="导入了多少目标/步骤、跳过多少，以及台账相对目标记录落后多少（每 60 秒刷新）"
      span={12}
    >
      {forbidden ? (
        <p className="os-hint" role="status">
          仅主账号可查看台账导入与落后自检；当前账号无权查看这一块（不是「没有数据」）。
        </p>
      ) : offline ? (
        <p className="tc-missing-block" role="status">
          {LEDGER_OFFLINE}
          {raw.error ? <span className="tc-sub">{raw.error}</span> : null}
        </p>
      ) : raw.error ? (
        <p className="tc-error" role="alert">
          {raw.error}
        </p>
      ) : raw.loading && !data ? (
        <p>读取中…</p>
      ) : data ? (
        <>
          <div className="tc-counts">
            <CountItem label="台账任务" value={data.ledger.counts.tasks} />
            <CountItem label="子步骤" value={data.ledger.counts.steps} />
            <CountItem label="执行记录" value={data.ledger.counts.runs} />
            <CountItem label="落后项" value={data.lag.lag_total} warn={data.lag.lag_total > 0} />
            <CountItem
              label="写穿透失败"
              value={data.lag.write_failures}
              warn={data.lag.write_failures > 0}
            />
          </div>

          <div className="tc-status-grid">
            {TASK_STATUSES.map((status) => (
              <span key={status.value} className="tc-status-item">
                <span className="tc-sub">{status.label}</span>
                {/* 该状态计数是真 0：后端 by_status 由 SQL GROUP BY 生成，缺键即该状态
                    0 条台账任务；?? 0 只把“无此状态行”读成 0，不是缺失冒充。 */}
                <strong>{data.ledger.by_status[status.value] ?? 0}</strong>
              </span>
            ))}
          </div>

          <h3 className="os-detail-title">最近迁移</h3>
          {data.ledger.recent_migrations.length === 0 ? (
            <p className="os-hint">
              台账里还没有迁移记录：本机从未有过目标时这是空的，不是读取失败。
            </p>
          ) : (
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>结果</th>
                    <th>扫描目标</th>
                    <th>导入任务</th>
                    <th>导入步骤</th>
                    <th>跳过</th>
                  </tr>
                </thead>
                <tbody>
                  {data.ledger.recent_migrations.map((record) => (
                    <tr key={record.migration_id}>
                      <td>{formatStamp(record.finished_at ?? record.started_at)}</td>
                      <td>{migrationStatusLabel(record)}</td>
                      <td>{record.entries_scanned}</td>
                      <td>{record.tasks_imported}</td>
                      <td>{record.steps_imported}</td>
                      <td>{record.skipped}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <SkippedReasons records={data.ledger.recent_migrations} />

          <h3 className="os-detail-title">落后自检</h3>
          <dl className="os-kv">
            <dt>热文件目标 / 台账任务</dt>
            <dd>
              {data.lag.hot_goals} / {data.lag.ledger_tasks}
            </dd>
            <dt>缺失 / 状态偏差</dt>
            <dd className={data.lag.lag_total > 0 ? 'tc-error' : undefined}>
              {data.lag.missing_in_ledger.length} / {data.lag.stale.length}
            </dd>
            {data.lag.missing_in_ledger.length > 0 && (
              <>
                <dt>缺失的目标</dt>
                <dd>{data.lag.missing_in_ledger.join('、')}</dd>
              </>
            )}
            {data.lag.stale.length > 0 && (
              <>
                <dt>状态偏差</dt>
                <dd>
                  <ul className="os-list">
                    {data.lag.stale.slice(0, 5).map((item) => (
                      <li key={item.task_id}>
                        {item.task_id}：源 {item.source_state || '—'} → 应为{' '}
                        {taskStatusLabel(item.expected)}，台账是 {taskStatusLabel(item.ledger)}
                      </li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
            <dt>上次写穿透失败</dt>
            <dd>
              {formatStamp(data.lag.last_failure_at)}
              {data.lag.last_failure_reason ? ` · ${data.lag.last_failure_reason}` : ''}
            </dd>
            {typeof data.lag.projection_failures === 'number' && data.lag.projection_failures > 0 && (
              <>
                <dt>投影失败</dt>
                <dd className="tc-error">
                  {data.lag.projection_failures} 次
                  {data.lag.last_projection_error ? ` · ${data.lag.last_projection_error}` : ''}
                </dd>
              </>
            )}
          </dl>
          <p className="os-hint">
            落后自检只读活跃目标文件（热文件），不含归档分片；归档是否已导入看上面的迁移记录。
          </p>
        </>
      ) : null}
    </Card>
  )
}

function CountItem({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <span className={`tc-count${warn ? ' tc-count-warn' : ''}`}>
      <span className="tc-sub">{label}</span>
      <strong>{value}</strong>
    </span>
  )
}

function migrationStatusLabel(record: MigrationRecord): string {
  if (record.status === 'completed') return '已完成'
  if (record.status === 'failed') return record.error ? `失败：${record.error}` : '失败'
  if (record.status === 'rolled_back') return '已回滚'
  if (record.status === 'running') return '进行中'
  return record.status || '—'
}

/**
 * 跳过原因。
 *
 * 状态接口的历史迁移记录只返回跳过条数，不返回原因分布（原因只在执行迁移的
 * 返回里）。这里如实说明，不把条数反推成原因。
 */
function SkippedReasons({ records }: { records: MigrationRecord[] }) {
  const withSkips = records.filter((record) => record.skipped > 0)
  if (withSkips.length === 0) return null
  return (
    <div className="tc-skip">
      {withSkips.map((record) => (
        <div key={record.migration_id} className="tc-skip-item">
          <span className="tc-sub">
            跳过 {record.skipped} 条（{formatStamp(record.finished_at ?? record.started_at)}）
          </span>
          {record.skipped_reasons && Object.keys(record.skipped_reasons).length > 0 ? (
            <ul className="os-list">
              {Object.entries(record.skipped_reasons).map(([reason, count]) => (
                <li key={reason}>
                  {skipReasonLabel(reason)}：{count} 条
                </li>
              ))}
            </ul>
          ) : (
            <span className="tc-missing">
              暂未提供 —— 台账状态接口只返回跳过条数，不返回原因；原因只在执行迁移的返回里给出。
            </span>
          )}
        </div>
      ))}
    </div>
  )
}
