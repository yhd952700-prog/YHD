/**
 * 鎏灏管家 —— 一句话吩咐 → 自动拆解 → 员工执行 → 人话汇总。
 *
 * 这一页**只用真实端点**：
 *   * POST /v1/goals（background=true）     一句话交给管家，立刻进入 running；
 *   * GET  /v1/goals/{id}                    轮询目标真实状态与任务清单；
 *   * POST /v1/goals/{id}/stop               人类主权中止；
 *   * POST /v1/goals/{id}/replan             整目标重新规划（失败后的重试路径）；
 *   * GET  /v1/goals                          历史执行；
 *   * GET  /v1/employees                      员工名册（单独指派的选择器数据源）；
 *   * GET  /v1/projects                       可选的归属项目；
 *   * GET  /v1/files/content                  点开看产物正文。
 *
 * 后端没有的三件事，在界面上**显式禁用并标注「等待后端接口」**，不编造：
 *   1. 把一条任务直接指派给指定员工；
 *   2. 运行中逐任务进度（后台执行期间 tasks 恒为空，结束时才落盘）；
 *   3. 单任务重试（只有整目标 replan）。
 * 另外，审批中心只有「已签发凭据」，没有「待审批请求」队列；被策略拦截的动作
 * 只能在任务错误里看到，入口指到审批中心，但不能在这里代为批准。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from '../lib/api'
import type { EmployeesPayload } from '../lib/contracts'
import { relativeTime, useNow, safeText } from '../lib/format'
import {
  approvalTasks,
  buildGoalSummary,
  BUTLER_DETAIL_POLL_MS,
  BUTLER_LIST_POLL_MS,
  failedTasks,
  formatElapsed,
  formatTaskDuration,
  goalElapsedSeconds,
  goalStateLabel,
  goalStateTone,
  isGoalActive,
  sortGoalSummaries,
  taskDurationSeconds,
  taskPhaseInfo,
  taskStats,
} from '../lib/butler'
import {
  createGoal,
  readFile,
  replanGoal,
  stopGoal,
} from '../lib/operator'
import type {
  GoalDetail,
  GoalSummary,
  GoalsResponse,
  Json,
  ProjectsResponse,
} from '../lib/operator'
import { Card, Empty, Failure, Guard, Loading } from '../components/ui'

type ArtifactPreview = {
  path: string
  loading: boolean
  content: string | null
  size: number | null
  error: string | null
}

function goalIdOf(raw: Json): string {
  const value = raw.goal_id
  return typeof value === 'string' ? value : ''
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '未知错误'
}

function acceptanceTone(state: string): 'ok' | 'warn' | 'danger' | 'accent' | 'muted' {
  return goalStateTone(state)
}

export function Butler({
  query,
  onOpenApproval,
}: {
  query: string
  onOpenApproval: () => void
}) {
  // ── 一句话输入 ───────────────────────────────────────────────
  const [prompt, setPrompt] = useState('')
  const [scope, setScope] = useState('L1')
  const [projectId, setProjectId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // ── 当前目标 ─────────────────────────────────────────────────
  const [activeId, setActiveId] = useState<string | null>(null)
  const [activePrompt, setActivePrompt] = useState('')
  const [activeStartedAt, setActiveStartedAt] = useState<number | null>(null)
  const [activeRunning, setActiveRunning] = useState(false)
  const autoAttached = useRef(false)

  // ── 主权动作 ─────────────────────────────────────────────────
  const [stopping, setStopping] = useState(false)
  const [stopNotice, setStopNotice] = useState<string | null>(null)
  const [stopError, setStopError] = useState<string | null>(null)
  const [replanning, setReplanning] = useState(false)
  const [replanStartedAt, setReplanStartedAt] = useState<number | null>(null)
  const [replanNotice, setReplanNotice] = useState<string | null>(null)
  const [replanError, setReplanError] = useState<string | null>(null)

  // ── 产物预览 ─────────────────────────────────────────────────
  const [artifact, setArtifact] = useState<ArtifactPreview | null>(null)

  const now = useNow(activeRunning || replanning ? 1_000 : 30_000)

  const projects = useApi<ProjectsResponse>('/v1/projects', 60_000)
  const employees = useApi<EmployeesPayload>('/v1/employees', 30_000)
  const goals = useApi<GoalsResponse>('/v1/goals', BUTLER_LIST_POLL_MS)

  const detailApi = useApi<GoalDetail>(
    activeId ? `/v1/goals/${encodeURIComponent(activeId)}` : null,
    activeRunning ? BUTLER_DETAIL_POLL_MS : 0,
  )
  // useApi 在 path 变化时不会清空旧 data：必须用 goal_id 校验，否则会短暂把
  // 上一个目标的任务卡显示在新目标下面。
  const detail = detailApi.data && detailApi.data.goal_id === activeId ? detailApi.data : null
  // 最终态与 `activeRunning` 之间有一帧的时差（effect 还没来得及清零）。
  // 界面一律以**后端真实 state** 为准，否则会出现「徽标写成功、却还在转圈」。
  const live = activeRunning && (!detail || isGoalActive(detail.state))
  const tasks = useMemo(() => (detail && Array.isArray(detail.tasks) ? detail.tasks : []), [detail])
  const stats = useMemo(() => taskStats(tasks), [tasks])
  const awaiting = useMemo(() => approvalTasks(tasks), [tasks])
  const failed = useMemo(() => failedTasks(tasks), [tasks])
  const artifacts = useMemo(
    () => (detail && Array.isArray(detail.artifacts) ? detail.artifacts : []),
    [detail],
  )

  const list = goals.data && Array.isArray(goals.data.goals) ? goals.data.goals : []
  const activeSummary = list.find((item) => item.goal_id === activeId) ?? null
  const state = detail?.state ?? activeSummary?.state ?? (activeRunning ? 'running' : '')
  const promptText = detail?.natural_language ?? activePrompt ?? activeSummary?.natural_language ?? ''

  /** 运行中：以后端 running 记录里的 created_at 为起点；没有就退回本地提交时刻。 */
  const runningElapsed = useMemo(() => {
    if (!live) return null
    const start = detail?.created_at ?? activeStartedAt
    if (!start || !Number.isFinite(start)) return null
    return Math.max(0, now / 1000 - start)
  }, [live, activeStartedAt, detail?.created_at, now])

  const terminalElapsed = useMemo(() => (detail ? goalElapsedSeconds(tasks, now / 1000) : null), [detail, tasks, now])

  // ── 首个仍在运行的目标：刷新页面后自动接上，不用人去历史里翻 ──────
  useEffect(() => {
    if (autoAttached.current || activeId) return
    if (list.length === 0) return
    const running = sortGoalSummaries(list).find((item) => isGoalActive(item.state))
    if (!running) return
    autoAttached.current = true
    setActiveId(running.goal_id)
    setActivePrompt(running.natural_language)
    setActiveStartedAt(running.created_at ?? null)
    setActiveRunning(true)
  }, [activeId, list])

  // 目标进入终态：停止 2 秒轮询，并刷新历史列表（真实状态）。
  useEffect(() => {
    if (!detail) return
    if (!isGoalActive(detail.state) && activeRunning) {
      setActiveRunning(false)
      goals.reload()
    }
  }, [detail, activeRunning, goals.reload])

  useEffect(() => {
    // 切换目标时清掉上一个目标遗留的产物预览与动作提示。
    setArtifact(null)
    setStopNotice(null)
    setStopError(null)
    setReplanNotice(null)
    setReplanError(null)
  }, [activeId])

  const submit = useCallback(async () => {
    const text = prompt.trim()
    if (!text) {
      setSubmitError('先写一句要让管家做的事，再发送。')
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const raw = await createGoal({
        natural_language: text,
        scope,
        project_id: projectId || null,
        background: true,
      })
      const id = goalIdOf(raw)
      if (!id) {
        setSubmitError('后端返回了目标但没有 goal_id：无法追踪这次执行，请检查网关版本。')
        return
      }
      setActiveId(id)
      setActivePrompt(text)
      setActiveStartedAt(Date.now() / 1000)
      setActiveRunning(true)
      setPrompt('')
      goals.reload()
    } catch (error) {
      setSubmitError(errorText(error))
    } finally {
      setSubmitting(false)
    }
  }, [goals.reload, projectId, prompt, scope])

  const openFromHistory = useCallback((item: GoalSummary) => {
    setActiveId(item.goal_id)
    setActivePrompt(item.natural_language)
    setActiveStartedAt(item.created_at ?? null)
    setActiveRunning(isGoalActive(item.state))
  }, [])

  const stop = useCallback(async () => {
    if (!activeId) return
    setStopping(true)
    setStopError(null)
    setStopNotice(null)
    try {
      await stopGoal(activeId)
      setStopNotice(
        '已发送中止请求。下面显示的是后端真实终态；已经结束的目标不会被改写。',
      )
      setActiveRunning(true)
      detailApi.reload()
      goals.reload()
    } catch (error) {
      setStopError(errorText(error))
    } finally {
      setStopping(false)
    }
  }, [activeId, detailApi.reload, goals.reload])

  const retryWholeGoal = useCallback(async () => {
    if (!activeId) return
    setReplanning(true)
    setReplanStartedAt(Date.now())
    setReplanError(null)
    setReplanNotice(null)
    try {
      await replanGoal(activeId)
      setReplanNotice('管家已重新规划并重跑；下面显示后端返回的最新结果。')
      setActiveRunning(true)
      detailApi.reload()
      goals.reload()
    } catch (error) {
      setReplanError(errorText(error))
    } finally {
      setReplanning(false)
      setReplanStartedAt(null)
    }
  }, [activeId, detailApi.reload, goals.reload])

  const openArtifact = useCallback(async (path: string) => {
    setArtifact({ path, loading: true, content: null, size: null, error: null })
    try {
      const file = await readFile(path)
      setArtifact((current) =>
        current?.path === path
          ? { path, loading: false, content: file.content, size: file.size, error: null }
          : current,
      )
    } catch (error) {
      setArtifact((current) =>
        current?.path === path
          ? { path, loading: false, content: null, size: null, error: errorText(error) }
          : current,
      )
    }
  }, [])

  const filteredHistory = useMemo(() => {
    const needle = safeText(query).trim().toLowerCase()
    const sorted = sortGoalSummaries(list)
    if (!needle) return sorted
    return sorted.filter((item) =>
      `${item.natural_language} ${item.goal_id} ${item.state}`.toLowerCase().includes(needle),
    )
  }, [list, query])

  const employeeNames = (employees.data?.employees ?? []).map((item) => item.name)
  const projectOptions = projects.data?.projects ?? []
  const replanElapsed = replanStartedAt
    ? formatElapsed(Math.max(0, (now - replanStartedAt) / 1000))
    : null

  // 任务上的 `assigned_agent` 是 **agent id**（后端 `goal_task_graph` 的赋值口径），
  // 不是员工名。能通过名册反查时显示「员工（agent）」；查不到就原样显示 id，
  // 不假装它是员工名。
  const agentOwners = useMemo(() => {
    const map = new Map<string, string>()
    for (const employee of employees.data?.employees ?? []) {
      for (const agent of employee.agents ?? []) {
        map.set(agent.id, `${employee.name}（${agent.name || agent.id}）`)
      }
    }
    return map
  }, [employees.data])

  return (
    <div className="os-grid">
      {/* ── 一句话吩咐 ───────────────────────────────────────── */}
      <Card
        title="一句话吩咐"
        sub="直接说你要什么；管家负责拆解、派给员工，再把结果汇总回来"
        span={12}
      >
        <form
          className="os-butler-command"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <label className="os-visually-hidden" htmlFor="butler-prompt">
            要让管家做什么
          </label>
          <input
            id="butler-prompt"
            className="os-input os-butler-command-input"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="比如：把这周的会议记录整理成周报，按项目归档，再给我一份 300 字摘要"
            disabled={submitting}
            autoComplete="off"
          />
          <button className="os-btn os-butler-command-send" type="submit" disabled={submitting}>
            {submitting ? '提交中…' : '交给管家'}
          </button>
        </form>

        <div className="os-butler-options">
          <div className="os-field">
            <label htmlFor="butler-scope">范围</label>
            <select
              id="butler-scope"
              className="os-input"
              value={scope}
              onChange={(event) => setScope(event.target.value)}
            >
              {['L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7'].map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </div>
          <div className="os-field">
            <label htmlFor="butler-project">归属项目（可选）</label>
            <select
              id="butler-project"
              className="os-input"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              disabled={projects.error !== null || projectOptions.length === 0}
            >
              <option value="">独立目标（不挂项目）</option>
              {projectOptions.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.name}
                </option>
              ))}
            </select>
          </div>
          <p className="os-hint os-butler-options-hint">
            {projects.error
              ? `项目列表不可用（${projects.error}）；本次按独立目标提交。`
              : projectOptions.length === 0
                ? '还没有项目；本次按独立目标提交。'
                : '挂到项目后，项目页会同步看到这条目标。'}
          </p>
        </div>

        {submitError && (
          <div className="os-alert" role="alert" style={{ marginTop: 10 }}>
            <span>{submitError}</span>
          </div>
        )}

        <p className="os-hint" style={{ marginTop: 10 }}>
          提交后管家在后台执行，页面每 2 秒向 GET /v1/goals/&#123;goal_id&#125; 确认一次状态；
          不会用假的分块进度冒充实时进度。
        </p>
      </Card>

      {/* ── 当前目标 / 汇总 ─────────────────────────────────── */}
      {activeId && (
        <Card
          title="管家汇总"
          sub={
            state
              ? `${goalStateLabel(state)} · 目标 ${activeId}`
              : `目标 ${activeId}`
          }
          span={12}
          action={
            <div className="os-kb-actions">
              {live && (
                <button
                  className="os-btn os-btn-ghost"
                  onClick={() => void stop()}
                  disabled={stopping}
                >
                  {stopping ? '中止中…' : '中止本次执行'}
                </button>
              )}
              {state === 'failed' && !live && (
                <button
                  className="os-btn os-btn-ghost"
                  onClick={() => void retryWholeGoal()}
                  disabled={replanning}
                >
                  {replanning ? `重新规划中… ${replanElapsed ?? ''}` : '重新规划并重跑'}
                </button>
              )}
              <button className="os-link" onClick={() => detailApi.reload()}>
                刷新
              </button>
            </div>
          }
        >
          {detailApi.error && !detail ? (
            <Failure message={detailApi.error} onRetry={detailApi.reload} />
          ) : !detail ? (
            <Loading rows={3} />
          ) : (
            <>
              <div className="os-butler-run-head">
                <span
                  className="os-butler-pulse"
                  data-running={live ? 'true' : 'false'}
                  aria-hidden="true"
                />
                <div className="os-butler-run-text">
                  <strong>{promptText || '（目标没有自然语言描述）'}</strong>
                  <p className="os-hint">
                    {live
                      ? `管家正在拆解并安排员工执行 · 已运行 ${formatElapsed(runningElapsed)}`
                      : `总耗时 ${formatTaskDuration(terminalElapsed)} · ${stats.total} 项任务`}
                  </p>
                </div>
                <span className="os-badge" data-tone={acceptanceTone(state)}>
                  {goalStateLabel(state)}
                </span>
              </div>

              {live ? (
                <>
                  <div
                    className="os-butler-progress"
                    data-indeterminate={tasks.length === 0 ? 'true' : 'false'}
                    role="progressbar"
                    aria-valuetext={
                      tasks.length === 0
                        ? '进度未知：后端运行期间不返回任务清单'
                        : `已结束 ${stats.finished} / ${stats.total} 项`
                    }
                    aria-valuenow={tasks.length === 0 ? undefined : stats.percent}
                  >
                    <span style={{ width: tasks.length === 0 ? undefined : `${stats.percent}%` }} />
                  </div>
                  {tasks.length === 0 && (
                    <div className="os-note" role="status" style={{ marginTop: 10 }}>
                      <strong>为什么现在看不到任务卡？</strong>
                      <p style={{ margin: '4px 0 0' }}>
                        后端在后台执行期间不落盘任务清单：GET /v1/goals/&#123;goal_id&#125; 会返回
                        tasks: []，直到执行结束才一次性写入。所以这里只能显示「仍在运行」
                        和计时；不是页面卡死，也不会伪造逐任务进度。
                      </p>
                      <p className="os-hint" style={{ marginTop: 4 }}>
                        如果你不想等了，可以「中止本次执行」；中止后下面会显示后端真实终态。
                      </p>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div
                    className="os-butler-progress"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={stats.percent}
                    aria-valuetext={`已结束 ${stats.finished} / ${stats.total} 项`}
                  >
                    <span style={{ width: `${stats.percent}%` }} />
                  </div>
                  <p className="os-hint" style={{ marginTop: 6 }}>
                    已结束 {stats.finished} / {stats.total} 项 · 成功 {stats.succeeded} · 失败{' '}
                    {stats.failed} · 待确认 {stats.awaiting}
                    {stats.skipped > 0 ? ` · 跳过 ${stats.skipped}` : ''}
                  </p>
                  <p className="os-butler-summary">{buildGoalSummary(detail)}</p>

                  {awaiting.length > 0 && (
                    <div className="os-alert" role="alert" style={{ marginTop: 12 }}>
                      <div>
                        <strong>有 {awaiting.length} 个动作需要你确认</strong>
                        <p style={{ margin: '4px 0 0' }}>
                          这些任务是被策略拦截后失败的，不是普通报错。授权入口在审批中心。
                          当前网关没有「待审批请求」队列接口，所以这里只列出执行时被拦下的
                          动作，不能在这里代为批准。
                        </p>
                        <button
                          className="os-btn"
                          style={{ marginTop: 8 }}
                          onClick={onOpenApproval}
                        >
                          去审批中心授权
                        </button>
                      </div>
                    </div>
                  )}

                  {failed.length > 0 && (
                    <div className="os-butler-failures">
                      <strong>失败项（可整目标重跑，单任务重试等待后端接口）</strong>
                      <ul>
                        {failed.map((task) => (
                          <li key={task.id}>
                            <span>{task.name || '（未命名任务）'}</span>
                            <span className="os-kb-error">{task.error ?? '后端未给出原因'}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {artifacts.length > 0 && (
                    <div className="os-butler-artifacts">
                      <strong>产出的文件（点开预览）</strong>
                      <div className="os-kb-actions">
                        {artifacts.map((path) => (
                          <button
                            key={path}
                            className="os-link"
                            onClick={() => void openArtifact(path)}
                          >
                            {path}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {artifact && (
                    <div className="os-butler-preview">
                      <div className="os-butler-preview-head">
                        <strong>{artifact.path}</strong>
                        {artifact.size !== null && (
                          <span className="os-hint">{artifact.size} 字节</span>
                        )}
                        <button className="os-link" onClick={() => setArtifact(null)}>
                          关闭
                        </button>
                      </div>
                      {artifact.loading && <Loading rows={2} />}
                      {artifact.error && (
                        <p className="os-kb-error" role="alert">
                          读取失败：{artifact.error}
                        </p>
                      )}
                      {artifact.content !== null && (
                        <pre className="os-pre os-butler-preview-body">{artifact.content}</pre>
                      )}
                    </div>
                  )}
                </>
              )}

              {stopNotice && (
                <p className="os-note" role="status" style={{ marginTop: 10 }}>
                  {stopNotice}
                </p>
              )}
              {stopError && <p className="os-kb-error">中止失败：{stopError}</p>}
              {replanNotice && (
                <p className="os-note" role="status" style={{ marginTop: 10 }}>
                  {replanNotice}
                </p>
              )}
              {replanError && <p className="os-kb-error">重新规划失败：{replanError}</p>}
              {replanning && (
                <p className="os-hint">
                  后端这个重规划调用是同步的：完成前不会返回中间进度，页面会一直显示
                  「重新规划中… {replanElapsed ?? ''}」。
                </p>
              )}
            </>
          )}
        </Card>
      )}

      {/* ── 任务分解 ─────────────────────────────────────────── */}
      {activeId && (
        <Card
          title="任务分解"
          sub={
            detail
              ? `总进度 ${stats.percent}% · 已结束 ${stats.finished} / ${stats.total} 项`
              : '等待管家的任务清单'
          }
          span={12}
        >
          {!detail ? (
            detailApi.error ? (
              <Failure message={detailApi.error} onRetry={detailApi.reload} />
            ) : detailApi.loading ? (
              <Loading rows={3} />
            ) : (
              <Empty
                title="还没有任务清单"
                hint="运行中的目标后端不返回任务；等管家执行结束，这里会一次性出现完整的任务卡。"
              />
            )
          ) : tasks.length === 0 ? (
            <Empty
              title={live ? '管家仍在执行，任务清单尚未落盘' : '本次没有分解出任务'}
              hint={
                live
                  ? '后端在运行期间返回 tasks: []；结束后才会写入任务卡。这是后端当前的能力边界，不是页面故障。'
                  : '目标已结束但没有任何任务：可能需要换一种更具体的说法，或检查员工是否可用。'
              }
            />
          ) : (
            <div className="os-butler-tasks">
              {tasks.map((task) => {
                const info = taskPhaseInfo(task)
                return (
                  <article key={task.id} className="os-butler-task" data-tone={info.tone}>
                    <header className="os-butler-task-head">
                      <span className="os-badge" data-tone={info.tone}>
                        {info.label}
                      </span>
                      <strong className="os-butler-task-name">
                        {task.name || '（未命名任务）'}
                      </strong>
                      <span className="os-butler-task-time" title={`原始状态：${info.raw || '缺失'}`}>
                        {formatTaskDuration(taskDurationSeconds(task, now / 1000))}
                      </span>
                    </header>
                    <p className="os-butler-task-meta">
                      指派给{' '}
                      <strong>
                        {task.assigned_agent
                          ? agentOwners.get(task.assigned_agent) ?? task.assigned_agent
                          : '后端未记录'}
                      </strong>
                      {task.capability_id ? ` · 能力 ${task.capability_id}` : ''}
                    </p>
                    {info.note && <p className="os-hint">{info.note}</p>}
                    {task.description && <p className="os-hint">{task.description}</p>}
                    {task.result && (
                      <details className="os-butler-task-result">
                        <summary>查看任务结果</summary>
                        <pre className="os-pre">{task.result}</pre>
                      </details>
                    )}
                    {task.error && <p className="os-kb-error">失败原因：{task.error}</p>}
                    {info.phase === 'awaiting' && (
                      <button className="os-btn os-btn-ghost" onClick={onOpenApproval}>
                        去审批中心授权
                      </button>
                    )}
                    {info.phase === 'failed' && (
                      <button
                        className="os-btn os-btn-ghost"
                        disabled
                        title="后端没有「只重试一个任务」的端点；目前只能 POST /v1/goals/{goal_id}/replan 重跑整个目标"
                      >
                        重试此任务（等待后端接口）
                      </button>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </Card>
      )}

      {/* ── 单独指派（后端缺失，显式禁用） ──────────────────────── */}
      <Card
        title="单独指派"
        sub="绕过管家的自动拆解，直接点一名员工做一件事"
        span={5}
      >
        <fieldset className="os-butler-assign" disabled aria-describedby="butler-assign-gap">
          <div className="os-field">
            <label htmlFor="butler-assign-employee">员工</label>
            <select id="butler-assign-employee" className="os-input" defaultValue="">
              <option value="">
                {employees.loading
                  ? '员工名册加载中…'
                  : employeeNames.length === 0
                    ? '没有可用员工'
                    : '选择员工'}
              </option>
              {employeeNames.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
          <div className="os-field">
            <label htmlFor="butler-assign-task">任务</label>
            <input
              id="butler-assign-task"
              className="os-input"
              placeholder="比如：把 reports/ 下的周报合并成一份"
            />
          </div>
          <button className="os-btn" type="button">
            指派并开始
          </button>
        </fieldset>
        <div className="os-alert" id="butler-assign-gap" style={{ marginTop: 10 }}>
          <div>
            <strong>等待后端接口</strong>
            <p style={{ margin: '4px 0 0' }}>
              当前网关没有「把一条任务直接指派给指定员工」的端点。可用的写入口只有
              `POST /v1/goals`（由管家自动拆解、交给默认员工执行）。所以本节保持禁用，
              不做假数据演示。
            </p>
          </div>
        </div>
        {/* 名册只做展示，让空选择器不至于看起来像坏了。 */}
        {employeeNames.length > 0 && (
          <p className="os-hint" style={{ marginTop: 8 }}>
            当前名册：{employeeNames.join('、')}
          </p>
        )}
      </Card>

      {/* ── 历史 ─────────────────────────────────────────────── */}
      <Card
        title="历史执行"
        sub="最近的目标 · 点一条查看它的任务分解与汇总"
        span={7}
        action={
          <button className="os-link" onClick={goals.reload}>
            刷新
          </button>
        }
      >
        <Guard
          loading={goals.loading}
          error={goals.error}
          data={goals.data}
          onRetry={goals.reload}
        >
          {() =>
            filteredHistory.length === 0 ? (
              <Empty
                title={query.trim() ? `没有匹配「${query.trim()}」的目标` : '还没有执行过目标'}
                hint={
                  query.trim()
                    ? '搜索只匹配目标描述、id 与状态；清空搜索框可看到全部。'
                    : '在上面输入一句话并交给管家；执行记录会出现在这里。'
                }
              />
            ) : (
              <div className="os-butler-history">
                {filteredHistory.map((item) => (
                  <button
                    key={item.goal_id}
                    className="os-butler-history-row"
                    data-active={item.goal_id === activeId ? 'true' : 'false'}
                    onClick={() => openFromHistory(item)}
                  >
                    <span className="os-badge" data-tone={goalStateTone(item.state)}>
                      {goalStateLabel(item.state)}
                    </span>
                    <span className="os-butler-history-text">{item.natural_language}</span>
                    <span className="os-hint os-butler-history-meta">
                      {relativeTime(item.created_at ?? 0)} · 完成 {item.completed_tasks}/
                      {item.task_count}
                      {item.failed_tasks > 0 ? ` · 失败 ${item.failed_tasks}` : ''}
                    </span>
                  </button>
                ))}
              </div>
            )
          }
        </Guard>
      </Card>

      {/* ── 能力边界（只写真缺的） ─────────────────────────────── */}
      <Card
        title="能力边界"
        sub="这一版只列真正还缺的能力；已经能用的不再写「做不到」"
        span={12}
      >
        <dl className="os-kv os-butler-gaps">
          <dt>单独指派</dt>
          <dd>
            缺端点：没有「把一条任务直接指派给某个员工」的接口。现在只能发
            `POST /v1/goals`，由管家自动拆解并交给默认员工。本节已禁用，等后端补口。
          </dd>
          <dt>运行中逐任务进度</dt>
          <dd>
            缺数据：后台目标在运行期间 `GET /v1/goals/{'{goal_id}'}` 的 `tasks` 恒为空，
            结束才写入。页面用计时与不定进度条证明任务还活着，但不伪造任务级百分比。
          </dd>
          <dt>单任务重试</dt>
          <dd>
            缺端点：只有整目标 `POST /v1/goals/{'{goal_id}'}/replan`。失败任务卡上的
            「重试此任务」因此禁用，并标注等待后端接口。
          </dd>
          <dt>待审批队列</dt>
          <dd>
            缺队列：审批中心展示的是**已签发**的授权凭据，没有「待审批请求」列表。
            被策略拦截的动作只能在任务错误里看到；可以在任务卡与汇总里看到
            「待确认」与去审批中心的入口，但无法在这里代为批准。
          </dd>
          <dt>产物</dt>
          <dd>
            已可用：汇总里点文件名会读 `GET /v1/files/content` 显示正文；二进制或超大文件
            会显示后端的真实拒绝原因，不会伪装成空内容。
          </dd>
        </dl>
      </Card>
    </div>
  )
}

export default Butler
