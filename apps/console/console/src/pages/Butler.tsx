/**
 * 鎏灏管家 —— 一句话吩咐 → 自动拆解 → 员工执行 → 人话汇总；
 * 也支持点名把一件事直接派给某个员工。
 *
 * 这一页**只用真实端点**：
 *   * POST /v1/goals（background=true）     一句话交给管家，立刻进入 running；
 *   * POST /v1/goals/assign                 点名派给指定员工（不走管家拆解）；
 *   * GET  /v1/goals/{id}                    轮询目标真实状态与**增量**任务快照；
 *   * POST /v1/goals/{id}/stop               人类主权中止；
 *   * POST /v1/goals/{id}/replan             整目标重新规划；
 *   * POST /v1/goals/{goal_id}/tasks/{task_id}/retry  只重跑一个失败任务（同步）；
 *   * GET  /v1/policy/approvals/pending      待审批队列（只对能签发凭据的主体可见）；
 *   * POST /v1/policy/approvals              采用队列返回的请求体签发凭据；
 *   * GET  /v1/goals                          历史执行；
 *   * GET  /v1/employees                      员工名册（指派选择器）；
 *   * GET  /v1/projects                       可选的归属项目；
 *   * GET  /v1/files/content                  点开看产物正文。
 *
 * 诚实约束：
 *   * 运行中的任务快照可能是**增量**的（只包含刚变化的任务）。按 id 合并，
 *     缺字段保留旧值，已出现的卡片绝不会因为某次快照不完整而被清空；
 *   * 只有全部终结才给总进度百分比；运行中只报「已出现 N 项 / 已结束 M 项」；
 *   * 待审批队列的授权模板 `target` 是 `*`，后端要求 `confirm_wildcard` 显式
 *     二次确认；页面把这件事摆到明面上，不藏在一次无意识点击里。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, useApi } from '../lib/api'
import type { EmployeesPayload } from '../lib/contracts'
import { capabilityLabel, numberText, relativeTime, safeText, taskDescriptionLabel, taskNameLabel, useNow } from '../lib/format'
import {
  approvalTasks,
  buildGoalSummary,
  BUTLER_DETAIL_POLL_MS,
  BUTLER_LIST_POLL_MS,
  decompositionNotice,
  failedTasks,
  formatElapsed,
  formatTaskDuration,
  goalElapsedSeconds,
  goalStateLabel,
  isGoalActive,
  mergeTaskSnapshots,
  parseTaskTime,
  riskTone,
  sortGoalSummaries,
  splitLinks,
  taskDurationSeconds,
  taskPhaseInfo,
  taskStats,
} from '../lib/butler'
import {
  approvePending,
  assignTask,
  createGoal,
  readFile,
  replanGoal,
  retryGoalTask,
  stopGoal,
} from '../lib/operator'
import type {
  GoalDetail,
  GoalSummary,
  GoalTask,
  GoalsResponse,
  Json,
  PendingApprovalItem,
  PendingApprovalsResponse,
  ProjectsResponse,
} from '../lib/operator'
import { BudgetDenialActions, Card, Empty, Failure, Guard, Loading } from '../components/ui'
import { GoalStatus } from '../components/GoalStatus'
import {
  BUDGET_DENIAL_MESSAGE,
  BUDGET_DENIAL_NEXT_STEP,
  isBudgetDenialMessage,
  PERMISSION_DENIAL_MESSAGE,
} from '../lib/errorMessage'
import { goalWorkMessage } from '../lib/goalWork'
import { SEED_EMPLOYEE_LABEL, SEED_EMPLOYEE_NAME } from '../lib/employees'

/** 系统默认员工用对外称呼，内部标识不上屏。 */
function displayName(name: string): string {
  return name === SEED_EMPLOYEE_NAME ? SEED_EMPLOYEE_LABEL : name
}

type ArtifactPreview = {
  path: string
  loading: boolean
  content: string | null
  size: number | null
  error: string | null
}

type GoalMode = 'butler' | 'assigned'

const PENDING_POLL_MS = 30_000

/** 空态示例：点一下填入输入框，用户再改成自己的事。 */
const EXAMPLE_TASKS = [
  '把这周的会议记录整理成周报，按项目归档，再给我一份 300 字摘要',
  '检查本周新增的文档，列出缺少负责人的条目，并给出补齐建议',
  '把最近三次失败的运行整理成一页复盘，写清原因和下一步',
]

/** 拦截风险等级 → 中文；原始英文只进 `title`。 */
const RISK_LABELS: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

function riskLabel(raw: string | null | undefined): string {
  const key = (raw ?? '').trim().toLowerCase()
  return RISK_LABELS[key] ?? '未知'
}

/** 后端 `duration_ms` 是真实时间戳差；没有就退回由时间戳推算的口径。 */
function durationLabel(task: GoalTask, nowSeconds: number): string {
  const ms = Number(task.duration_ms)
  if (task.duration_ms !== undefined && task.duration_ms !== null && Number.isFinite(ms) && ms >= 0) {
    if (ms < 1000) return `${Math.round(ms)} 毫秒`
    if (ms < 60_000) return `${numberText(ms / 1000, 1)} 秒`
    const minutes = Math.floor(ms / 60_000)
    const seconds = Math.round((ms % 60_000) / 1000)
    return `${minutes} 分 ${seconds} 秒`
  }
  return formatTaskDuration(taskDurationSeconds(task, nowSeconds))
}

/** 员工汇总行里的合计耗时（真实相加，不估算）。 */
function totalDurationLabel(ms: number | null | undefined): string {
  const value = Number(ms)
  if (!Number.isFinite(value) || value <= 0) return '—'
  if (value < 1000) return `${Math.round(value)} 毫秒`
  if (value < 60_000) return `${numberText(value / 1000, 1)} 秒`
  return `${Math.floor(value / 60_000)} 分 ${Math.round((value % 60_000) / 1000)} 秒`
}

function goalIdOf(raw: Json): string {
  const value = raw.goal_id
  return typeof value === 'string' ? value : ''
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    // 管家派活 / 重新规划都会触发模型调用：402 说预算并给出口，403 才说权限。
    if (error.code === 'budget_exceeded' || error.status === 402) {
      return `${BUDGET_DENIAL_MESSAGE}${BUDGET_DENIAL_NEXT_STEP}`
    }
    if (error.code === 'permission_denied') return PERMISSION_DENIAL_MESSAGE
    return error.message || '操作没有完成，请稍后重试。'
  }
  if (error instanceof Error && error.message) {
    // 英文异常不上屏；已经是中文的说明原样保留。
    return /[\u4e00-\u9fff]/.test(error.message)
      ? error.message
      : '操作没有完成，请稍后重试。'
  }
  return '操作没有完成，请稍后重试。'
}

function approveFailureMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return '你没有批准权限：只有具备批准权限的账号才能在这里批准。这不代表列表是空的。'
  }
  return errorText(error)
}

function approvalBodyOf(item: PendingApprovalItem): Json {
  const body = item.approve?.body
  return body && typeof body === 'object' && !Array.isArray(body) ? body : {}
}

/** 队列模板的 target 是 `"*"`（审计事件没有记录具体目标）。 */
function isWildcardBody(body: Json): boolean {
  return typeof body.target === 'string' && body.target === '*'
}

function blockedAtText(value: number | string | null | undefined): string {
  const seconds = parseTaskTime(value)
  return seconds === null ? '拦截时间未记录' : `${relativeTime(seconds)}被拦`
}

export function Butler({
  query,
  onOpenApproval,
  canApproveQueue,
  onOpenUsage,
  onConfigureModel,
}: {
  query: string
  onOpenApproval: () => void
  canApproveQueue: boolean
  /** 402 预算拒绝的可点出口：用量页 / 模型管理。 */
  onOpenUsage?: () => void
  onConfigureModel?: () => void
}) {
  // ── 一句话输入 ───────────────────────────────────────────────
  const [prompt, setPrompt] = useState('')
  const [scope, setScope] = useState('L1')
  const [projectId, setProjectId] = useState('')
  /** 可选：把整个目标指名交给某位员工（`POST /v1/goals` 的 employee_name）。 */
  const [promptEmployee, setPromptEmployee] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  // ── 当前目标 ─────────────────────────────────────────────────
  const [activeId, setActiveId] = useState<string | null>(null)
  const [activePrompt, setActivePrompt] = useState('')
  const [activeStartedAt, setActiveStartedAt] = useState<number | null>(null)
  const [activeRunning, setActiveRunning] = useState(false)
  const [activeMode, setActiveMode] = useState<GoalMode>('butler')
  const [activeEmployee, setActiveEmployee] = useState<string | null>(null)
  const autoAttached = useRef(false)

  // ── 任务快照缓存（增量合并，不允许越刷越少） ─────────────────
  const [taskCache, setTaskCache] = useState<{ goalId: string; tasks: GoalTask[] } | null>(null)

  // ── 单独指派 ─────────────────────────────────────────────────
  /** 执行主体：`employee:<名字>`（整位员工）或 `agent:<id>`（精确到个体）。 */
  const [assignTarget, setAssignTarget] = useState('')
  const [assignInstruction, setAssignInstruction] = useState('')
  const [assigning, setAssigning] = useState(false)
  const [assignError, setAssignError] = useState<string | null>(null)
  const [assignNotice, setAssignNotice] = useState<string | null>(null)

  // ── 单任务重试 ───────────────────────────────────────────────
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const [retryErrors, setRetryErrors] = useState<Record<string, string>>({})

  // ── 待审批 ───────────────────────────────────────────────────
  const [approvingId, setApprovingId] = useState<string | null>(null)
  const [approveErrors, setApproveErrors] = useState<Record<string, string>>({})
  const [approveNotices, setApproveNotices] = useState<Record<string, string>>({})
  const [wildcardConfirmed, setWildcardConfirmed] = useState<Record<string, boolean>>({})

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
  const pendingApi = useApi<PendingApprovalsResponse>(
    canApproveQueue ? '/v1/policy/approvals/pending' : null,
    PENDING_POLL_MS,
  )

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

  // 增量快照合并。注意：某一帧快照只包含刚变化的任务、甚至缺字段，
  // 这里按 id 合并并保留旧值；`detail` 自己仍原样保留，供汇总使用。
  useEffect(() => {
    if (!detail) return
    const incoming = Array.isArray(detail.tasks) ? detail.tasks : []
    setTaskCache((previous) => {
      if (!previous || previous.goalId !== detail.goal_id) {
        return { goalId: detail.goal_id, tasks: mergeTaskSnapshots([], incoming) }
      }
      return {
        goalId: detail.goal_id,
        tasks: mergeTaskSnapshots(previous.tasks, incoming),
      }
    })
  }, [detail])

  const tasks = useMemo(
    () => (taskCache && taskCache.goalId === activeId ? taskCache.tasks : []),
    [taskCache, activeId],
  )
  const stats = useMemo(() => taskStats(tasks), [tasks])
  // 拆解来源必须显式上屏：keyword = 不是 AI 拆的，老记录 = 未知。
  const decompositionHint = useMemo(() => {
    if (!detail) return null
    const notice = decompositionNotice(detail, tasks.length || (detail.tasks?.length ?? 0))
    // 运行中的快照还没有拆解来源；此时显示"未知"会把新目标说成老记录。
    if (live && notice?.tone === 'muted') return null
    return notice
  }, [detail, tasks.length, live])
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
  const pendingItems =
    pendingApi.data && Array.isArray(pendingApi.data.items) ? pendingApi.data.items : []

  /** 运行中：以后端 running 记录里的 created_at 为起点；没有就退回本地提交时刻。 */
  const runningElapsed = useMemo(() => {
    if (!live) return null
    const start = detail?.created_at ?? activeStartedAt
    if (!start || !Number.isFinite(start)) return null
    return Math.max(0, now / 1000 - start)
  }, [live, activeStartedAt, detail?.created_at, now])

  const terminalElapsed = useMemo(() => (detail ? goalElapsedSeconds(tasks, now / 1000) : null), [detail, tasks, now])

  // 任务上的 `assigned_agent` 是 **agent id**（后端 `goal_task_graph` 的赋值口径），
  // 不是员工名。能通过名册反查时显示「员工（agent）」；查不到就原样显示 id，
  // 不假装它是员工名。
  const agentOwners = useMemo(() => {
    const map = new Map<string, string>()
    for (const employee of employees.data?.employees ?? []) {
      for (const agent of employee.agents ?? []) {
        map.set(agent.id, `${displayName(employee.name)}（${agent.name || agent.id}）`)
      }
    }
    return map
  }, [employees.data])

  /** 这个目标是不是「点名派给某人」而不是管家自动拆解。 */
  const assignedEmployeeName =
    detail?.assigned_employee || (activeMode === 'assigned' ? activeEmployee : null) || ''
  const assignedAgentId = detail?.assigned_agent ?? ''
  const assignedGoal = Boolean(assignedEmployeeName || assignedAgentId)
  const assignedLabel = assignedEmployeeName
    ? assignedEmployeeName
    : assignedAgentId
      ? agentOwners.get(assignedAgentId) ?? assignedAgentId
      : ''

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
    setActiveMode('butler')
    setActiveEmployee(null)
  }, [activeId, list])

  // 目标进入终态：停止 2 秒轮询，并刷新历史列表与待审批队列（真实状态）。
  useEffect(() => {
    if (!detail) return
    if (!isGoalActive(detail.state) && activeRunning) {
      setActiveRunning(false)
      goals.reload()
      pendingApi.reload()
    }
  }, [detail, activeRunning, goals.reload, pendingApi.reload])

  useEffect(() => {
    // 切换目标时清掉上一个目标遗留的产物预览与动作提示。
    setArtifact(null)
    setStopNotice(null)
    setStopError(null)
    setReplanNotice(null)
    setReplanError(null)
    setAssignError(null)
    setAssignNotice(null)
    setRetryErrors({})
    setRetryingId(null)
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
        employee_name: promptEmployee || null,
        background: true,
      })
      if (promptEmployee) setPromptEmployee('')
      const id = goalIdOf(raw)
      if (!id) {
        setSubmitError('目标已提交，但没有返回可追踪的编号：这次执行无法在页面上跟进，请稍后刷新目标列表查看。')
        return
      }
      setActiveId(id)
      setActivePrompt(text)
      setActiveStartedAt(Date.now() / 1000)
      setActiveRunning(true)
      setActiveMode('butler')
      setActiveEmployee(null)
      setPrompt('')
      goals.reload()
      pendingApi.reload()
    } catch (error) {
      setSubmitError(errorText(error))
    } finally {
      setSubmitting(false)
    }
  }, [goals.reload, pendingApi.reload, projectId, prompt, promptEmployee, scope])

  const assign = useCallback(async () => {
    const target = assignTarget
    const instruction = assignInstruction.trim()
    if (!target) {
      setAssignError('先选一位员工或一个执行个体，再派活。')
      return
    }
    if (!instruction) {
      setAssignError('写一句要这位员工做的事。')
      return
    }
    setAssigning(true)
    setAssignError(null)
    setAssignNotice(null)
    try {
      const employeeTarget = target.startsWith('employee:')
        ? target.slice('employee:'.length)
        : ''
      const agentId = target.startsWith('agent:') ? target.slice('agent:'.length) : ''
      const raw = await assignTask(
        employeeTarget
          ? {
              employee_name: employeeTarget,
              instruction,
              scope,
              project_id: projectId || null,
              background: true,
            }
          : {
              agent_id: agentId,
              instruction,
              scope,
              project_id: projectId || null,
              background: true,
            },
      )
      const id = goalIdOf(raw)
      if (!id) {
        setAssignError('指派已提交，但没有返回可追踪的编号：这次执行无法在页面上跟进。')
        return
      }
      let label = employeeTarget ? displayName(employeeTarget) : ''
      if (!label && agentId) {
        const roster = employees.data?.employees ?? []
        for (const employee of roster) {
          const agent = (employee.agents ?? []).find((item) => item.id === agentId)
          if (agent) {
            label = `${displayName(employee.name)} · ${agent.name || agent.id}`
            break
          }
        }
        if (!label) label = agentId
      }
      setActiveId(id)
      setActivePrompt(instruction)
      setActiveStartedAt(Date.now() / 1000)
      setActiveRunning(true)
      setActiveMode('assigned')
      setActiveEmployee(label)
      setAssignInstruction('')
      setAssignNotice(`已派给 ${label}。这件事只由这位员工做，下面只显示这位员工的任务。`)
      goals.reload()
      pendingApi.reload()
    } catch (error) {
      setAssignError(goalWorkMessage(error, '指派失败'))
    } finally {
      setAssigning(false)
    }
  }, [
    assignInstruction,
    assignTarget,
    employees.data,
    goals.reload,
    pendingApi.reload,
    projectId,
    scope,
  ])

  const openFromHistory = useCallback((item: GoalSummary) => {
    setActiveId(item.goal_id)
    setActivePrompt(item.natural_language)
    setActiveStartedAt(item.created_at ?? null)
    setActiveRunning(isGoalActive(item.state))
    setActiveMode('butler')
    setActiveEmployee(null)
    setAssignNotice(null)
  }, [])

  const stop = useCallback(async () => {
    if (!activeId) return
    setStopping(true)
    setStopError(null)
    setStopNotice(null)
    try {
      await stopGoal(activeId)
      setStopNotice(
        '已发送中止请求。下面显示的是真实终态；已经结束的目标不会被改写。',
      )
      setActiveRunning(true)
      detailApi.reload()
      goals.reload()
      pendingApi.reload()
    } catch (error) {
      setStopError(errorText(error))
    } finally {
      setStopping(false)
    }
  }, [activeId, detailApi.reload, goals.reload, pendingApi.reload])

  const retryWholeGoal = useCallback(async () => {
    if (!activeId) return
    setReplanning(true)
    setReplanStartedAt(Date.now())
    setReplanError(null)
    setReplanNotice(null)
    try {
      await replanGoal(activeId)
      setReplanNotice('管家已重新规划并重跑；下面显示最新的结果。')
      setActiveRunning(true)
      detailApi.reload()
      goals.reload()
      pendingApi.reload()
    } catch (error) {
      setReplanError(errorText(error))
    } finally {
      setReplanning(false)
      setReplanStartedAt(null)
    }
  }, [activeId, detailApi.reload, goals.reload, pendingApi.reload])

  const retryTask = useCallback(
    async (task: GoalTask) => {
      if (!activeId) return
      setRetryingId(task.id)
      setRetryErrors((current) => {
        if (!(task.id in current)) return current
        const next = { ...current }
        delete next[task.id]
        return next
      })
      try {
        // 后端这个调用是同步的：返回时这个任务已经跑完，其余任务不受影响。
        await retryGoalTask(activeId, task.id)
        setActiveRunning(true)
        setReplanNotice('这个任务已重试完成；下面刷新为最新任务清单。')
        detailApi.reload()
        goals.reload()
        pendingApi.reload()
      } catch (error) {
        setRetryErrors((current) => ({
          ...current,
          [task.id]: goalWorkMessage(error, '任务重试失败'),
        }))
      } finally {
        setRetryingId(null)
      }
    },
    [activeId, detailApi.reload, goals.reload, pendingApi.reload],
  )

  const approve = useCallback(
    async (item: PendingApprovalItem, body: Json, wildcard: boolean) => {
      setApprovingId(item.pending_id)
      setApproveErrors((current) => {
        const next = { ...current }
        delete next[item.pending_id]
        return next
      })
      setApproveNotices((current) => {
        const next = { ...current }
        delete next[item.pending_id]
        return next
      })
      try {
        // 「采用队列返回的请求体」：只追加后端要求的通配二次确认，不改其它字段。
        const requestBody = wildcard ? { ...body, confirm_wildcard: true } : body
        await approvePending(requestBody)
        setApproveNotices((current) => ({
          ...current,
          [item.pending_id]: '已批准。批准后不会自动继续执行：回到失败任务点「重试此任务」，或「重新规划并重跑」。',
        }))
        setWildcardConfirmed((current) => ({ ...current, [item.pending_id]: false }))
        pendingApi.reload()
        detailApi.reload()
      } catch (error) {
        setApproveErrors((current) => ({
          ...current,
          [item.pending_id]: approveFailureMessage(error),
        }))
      } finally {
        setApprovingId(null)
      }
    },
    [detailApi.reload, pendingApi.reload],
  )

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
      `${item.natural_language} ${item.goal_id} ${item.state} ${item.assigned_employee ?? ''}`
        .toLowerCase()
        .includes(needle),
    )
  }, [list, query])

  /** 可接活的员工：在职、非只读、至少一个执行个体没暂停。 */
  const assignableEmployees = useMemo(
    () =>
      (employees.data?.employees ?? []).filter(
        (employee) =>
          !employee.read_only &&
          String(employee.employment_status).toLowerCase() !== 'disabled' &&
          (employee.agents ?? []).some((agent) => agent.status !== 'paused'),
      ),
    [employees.data],
  )
  /** 单独指派的下拉：整位员工（employee_name）与个体（agent_id）都能选。 */
  const assignOptions = useMemo(() => {
    const employeeOptions: Array<{ value: string; label: string; paused: boolean }> = []
    const agentOptions: Array<{ value: string; label: string; paused: boolean }> = []
    for (const employee of employees.data?.employees ?? []) {
      if (employee.read_only) continue
      const disabled = String(employee.employment_status).toLowerCase() === 'disabled'
      employeeOptions.push({
        value: `employee:${employee.name}`,
        label: `${displayName(employee.name)}（整位员工）`,
        paused: disabled || !(employee.agents ?? []).some((agent) => agent.status !== 'paused'),
      })
      for (const agent of employee.agents ?? []) {
        agentOptions.push({
          value: `agent:${agent.id}`,
          label: `${displayName(employee.name)} · ${agent.name || agent.id}`,
          paused: disabled || agent.status === 'paused',
        })
      }
    }
    return { employees: employeeOptions, agents: agentOptions }
  }, [employees.data])
  const selectedAssignOption =
    [...assignOptions.employees, ...assignOptions.agents].find(
      (option) => option.value === assignTarget,
    ) ?? null
  const employeeNames = (employees.data?.employees ?? []).map((item) => displayName(item.name))
  const projectOptions = projects.data?.projects ?? []
  const replanElapsed = replanStartedAt
    ? formatElapsed(Math.max(0, (now - replanStartedAt) / 1000))
    : null

  const scrollToPending = useCallback(() => {
    document.getElementById('butler-pending')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }, [])

  return (
    <div className="os-grid">
      <div className="os-span-12">
        <div className="os-position" role="note">
          <span>
            <strong>鎏灏管家</strong>：你下目标，它拆解分派，给结论。
            想自己看任务、看文件、看产出，去「工作台」。
          </span>
        </div>
      </div>
      {/* ── 一句话吩咐 ───────────────────────────────────────── */}
      <Card
        title="让管家安排（可分给多位员工）"
        sub="说清你要什么，管家拆解后分给合适的员工；不指定员工时，可以多人协作"
        span={activeId ? 7 : 6}
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

        {!prompt && !submitting && (
          <div style={{ marginTop: 10 }}>
            <span className="os-hint">不知道第一句怎么写？点一条示例，再改成你的事：</span>
            <div className="os-kb-actions">
              {EXAMPLE_TASKS.map((task) => (
                <button
                  key={task}
                  type="button"
                  className="os-link"
                  title="点一下填入上面的输入框，可以再修改"
                  onClick={() => {
                    setPrompt(task)
                    document.getElementById('butler-prompt')?.focus()
                  }}
                >
                  {task}
                </button>
              ))}
            </div>
          </div>
        )}

        <details className="os-butler-advanced" style={{ marginTop: 8 }}>
          <summary className="os-hint">
            高级选项：执行范围、归属项目、指定员工（不填也能派活）
          </summary>
          <div className="os-butler-options">
          <div className="os-field">
            <label htmlFor="butler-scope">执行范围</label>
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
          <div className="os-field">
            <label htmlFor="butler-employee">指定员工（可选）</label>
            <select
              id="butler-employee"
              className="os-input"
              value={promptEmployee}
              onChange={(event) => setPromptEmployee(event.target.value)}
              disabled={employees.loading || assignableEmployees.length === 0}
            >
              <option value="">
                {employees.loading
                  ? '员工名册加载中…'
                  : assignableEmployees.length === 0
                    ? '没有可接活的员工'
                    : '不指定（由管家分给合适的员工）'}
              </option>
              {assignableEmployees.map((employee) => (
                <option key={employee.name} value={employee.name}>
                  {displayName(employee.name)}
                </option>
              ))}
            </select>
          </div>
          <p className="os-hint os-butler-options-hint">
            {promptEmployee
              ? `整个目标只交给「${displayName(promptEmployee)}」做，不再由管家改派；它的子任务都归这位员工。`
              : projects.error
                ? `项目列表不可用（${projects.error}）；本次按独立目标提交。`
                : projectOptions.length === 0
                  ? '还没有项目；本次按独立目标提交。'
                  : '挂到项目后，项目页会同步看到这条目标。'}
          </p>
          </div>
        </details>

        {submitError && (
          <div className="os-alert" role="alert" style={{ marginTop: 10 }}>
            <span>{submitError}</span>
            {isBudgetDenialMessage(submitError) && onOpenUsage && onConfigureModel && (
              <BudgetDenialActions
                onOpenUsage={onOpenUsage}
                onConfigureModel={onConfigureModel}
              />
            )}
          </div>
        )}

        <p className="os-hint" style={{ marginTop: 10 }}>
          提交后管家在后台执行，页面每 2 秒自动确认一次状态。
          任务会一项项出现；全部结束后才显示总进度，期间不编造百分比。
        </p>
      </Card>

      {/* ── 只派给一个人（点名执行） ─────────────────────────── */}
      <Card
        title="只派给一个人"
        sub="不拆解、不换人：你选一位员工，这件事只由他做"
        span={activeId ? 5 : 6}
      >
        <div className="os-butler-assign-flag" aria-label="只派给一个人，不经过自动拆解">
          <span className="os-badge" data-tone="accent">只派一人</span>
          <span>这件事不会交给管家拆解，也不会换人执行。</span>
        </div>

        <div className="os-field">
          <label htmlFor="butler-assign-employee">派给谁</label>
          <select
            id="butler-assign-employee"
            className="os-input"
            value={assignTarget}
            onChange={(event) => setAssignTarget(event.target.value)}
            disabled={
              assigning ||
              employees.loading ||
              assignOptions.employees.length + assignOptions.agents.length === 0
            }
          >
            <option value="">
              {employees.loading
                ? '员工名册加载中…'
                : assignOptions.employees.length + assignOptions.agents.length === 0
                  ? '没有可用员工'
                  : '选择员工'}
            </option>
            {assignOptions.employees.length > 0 && (
              <optgroup label="派给整位员工">
                {assignOptions.employees.map((option) => (
                  <option key={option.value} value={option.value} disabled={option.paused}>
                    {option.label}
                    {option.paused ? '（停用或无可执行个体）' : ''}
                  </option>
                ))}
              </optgroup>
            )}
            {assignOptions.agents.length > 0 && (
              <optgroup label="派给某个执行个体（可选）">
                {assignOptions.agents.map((option) => (
                  <option key={option.value} value={option.value} disabled={option.paused}>
                    {option.label}
                    {option.paused ? '（暂停中）' : ''}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>

        <div className="os-field">
          <label htmlFor="butler-assign-task">任务</label>
          <input
            id="butler-assign-task"
            className="os-input"
            value={assignInstruction}
            onChange={(event) => setAssignInstruction(event.target.value)}
            placeholder="比如：把 reports/ 下的周报合并成一份"
            disabled={assigning}
            autoComplete="off"
          />
        </div>

        {selectedAssignOption && (
          <p className="os-hint" style={{ marginTop: 6 }}>
            执行者：{selectedAssignOption.label}
            {' · '}
            范围 {scope}
            {' · '}
            {projectId
              ? `项目 ${projectOptions.find((project) => project.project_id === projectId)?.name ?? projectId}`
              : '独立目标（不挂项目）'}
          </p>
        )}

        <div className="os-kb-actions">
          <button
            className="os-btn"
            type="button"
            onClick={() => void assign()}
            disabled={assigning || !assignTarget || !assignInstruction.trim()}
          >
            {assigning ? '指派中…' : `派给 ${selectedAssignOption ? selectedAssignOption.label : '所选员工'}`}
          </button>
          {employees.error && (
            <span className="os-kb-error">员工名册不可用：{employees.error}</span>
          )}
        </div>

        {assignError && (
          <p className="os-kb-error" role="alert" style={{ marginTop: 8 }}>
            {assignError}
            {isBudgetDenialMessage(assignError) && onOpenUsage && onConfigureModel && (
              <BudgetDenialActions
                onOpenUsage={onOpenUsage}
                onConfigureModel={onConfigureModel}
              />
            )}
          </p>
        )}
        {assignNotice && (
          <p className="os-note" role="status" style={{ marginTop: 8 }}>
            {assignNotice}
          </p>
        )}

        {employeeNames.length > 0 && (
          <p className="os-hint" style={{ marginTop: 8 }}>
            当前名册：{employeeNames.join('、')}
          </p>
        )}
      </Card>

      {/* ── 当前目标 / 汇总 ─────────────────────────────────── */}
      {activeId && (
        <Card
          title="管家汇总"
          sub={
            state
              ? `${assignedGoal ? '只派给一个人' : '管家安排'} · ${goalStateLabel(state)}`
              : '正在读取这次执行'
          }
          span={7}
          className={live ? 'sc-panel-active sc-hud' : undefined}
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
                刷新状态
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
                  className="sc-status"
                  data-state={live ? 'active' : state === 'failed' ? 'failed' : 'idle'}
                  aria-hidden="true"
                />
                <div className="os-butler-run-text">
                  <strong>{promptText || '（目标没有自然语言描述）'}</strong>
                  <p className="os-hint">
                    {live
                      ? `管家正在执行 · 已运行 ${formatElapsed(runningElapsed)} · 已出现 ${stats.total} 项任务`
                      : `总耗时 ${formatTaskDuration(terminalElapsed)} · ${stats.total} 项任务`}
                  </p>
                  {assignedGoal && (
                    <p className="os-hint">
                      执行者：{assignedLabel}（这件事只由这位员工做）
                    </p>
                  )}
                </div>
                <div className="os-butler-run-badges">
                  <span className="os-badge" data-tone={assignedGoal ? 'accent' : 'muted'}>
                    {assignedGoal ? '只派给一个人' : '管家安排'}
                  </span>
                  <GoalStatus
                    goal={{
                      state,
                      artifacts: detail?.artifacts ?? activeSummary?.artifacts ?? [],
                      completed_tasks: stats.succeeded,
                      failed_tasks: stats.failed,
                    }}
                    showHint
                  />
                </div>
              </div>

              {live && <div className="sc-flow-line" aria-hidden="true" />}

              {live ? (
                <>
                  <div
                    className="os-butler-progress"
                    data-indeterminate="true"
                    role="progressbar"
                    aria-valuetext={`已出现 ${stats.total} 项任务，其中已结束 ${stats.finished} 项；总数以最终清单为准`}
                  >
                    <span />
                  </div>
                  <p className="os-hint" style={{ marginTop: 6 }}>
                    已出现 {stats.total} 项 · 已结束 {stats.finished} 项 · 进行中 {stats.running} 项
                    {stats.awaiting > 0 ? ` · 待确认 ${stats.awaiting}` : ''}
                    {stats.skipped > 0 ? ` · 跳过 ${stats.skipped}` : ''}
                  </p>
                  {tasks.length === 0 ? (
                    <div className="os-note" role="status" style={{ marginTop: 10 }}>
                      <strong>管家正在拆解并安排员工</strong>
                      <p style={{ margin: '4px 0 0' }}>
                        任务会一项项出现，不用等整次执行结束；在此之前不编造任何任务级进度。
                      </p>
                      <p className="os-hint" style={{ marginTop: 4 }}>
                        不想等了可以点「中止本次执行」，页面会显示真实结果。
                      </p>
                    </div>
                  ) : (
                    <p className="os-hint" style={{ marginTop: 8 }}>
                      任务会陆续更新；已经出现的卡不会消失，也不会因为个别字段暂时缺失被清空。
                    </p>
                  )}
                </>
              ) : (
                <>
                  <p className="os-hint" style={{ marginTop: 6 }}>
                    已结束 {stats.finished} / {stats.total} 项 · 可交付 {stats.delivered} 项
                    {stats.failed > 0 ? ` · 失败 ${stats.failed}` : ''}
                    {stats.awaiting > 0 ? ` · 待确认 ${stats.awaiting}` : ''}
                    {stats.skipped > 0 ? ` · 跳过 ${stats.skipped}` : ''}
                  </p>
                  <p className="os-butler-summary">
                    <LinkifiedText text={buildGoalSummary(detail)} />
                  </p>

                  {detail.employees && detail.employees.length > 0 && (
                    <div className="os-note" role="status" style={{ marginTop: 10 }}>
                      <strong>员工汇总（按真实任务归属统计）</strong>
                      <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                        {detail.employees.map((row) => (
                          <li key={row.employee ?? 'unattributed'}>
                            {displayName(row.employee ?? '') || '未归属'} · 做了 {row.tasks} 项 · 完成 {row.completed} · 失败{' '}
                            {row.failed} · 合计耗时 {totalDurationLabel(row.total_duration_ms)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {awaiting.length > 0 && (
                    <div className="os-alert" role="alert" style={{ marginTop: 12 }}>
                      <div>
                        <strong>有 {awaiting.length} 个动作需要你确认</strong>
                        <p style={{ margin: '4px 0 0' }}>
                          {canApproveQueue
                            ? '这些任务被安全规则拦下了，需要你批准后才能继续。到下方「待我确认」批准，再回到任务卡点「重试此任务」。'
                            : '这些任务被安全规则拦下，需要批准后才能继续。批准完再回到任务卡点「重试此任务」。'}
                        </p>
                        {canApproveQueue && (
                          <div className="os-kb-actions" style={{ marginTop: 8 }}>
                            <button className="os-btn" onClick={scrollToPending}>
                              去「待我确认」
                            </button>
                            <button className="os-link" onClick={onOpenApproval}>
                              查看已批准的记录
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {failed.length > 0 && (
                    <div className="os-butler-failures">
                      <strong>失败项（可单独重试，或重新规划整目标）</strong>
                      <ul>
                        {failed.map((task) => (
                          <li key={task.id}>
                            <span>{taskNameLabel(task.name)}</span>
                            <span className="os-kb-error">{task.error ?? '未给出原因'}</span>
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
              {replanError && (
                <p className="os-kb-error">
                  重新规划失败：{replanError}
                  {isBudgetDenialMessage(replanError) && onOpenUsage && onConfigureModel && (
                    <BudgetDenialActions
                      onOpenUsage={onOpenUsage}
                      onConfigureModel={onConfigureModel}
                    />
                  )}
                </p>
              )}
              {replanning && (
                <p className="os-hint">
                  重新规划要跑完才返回，期间页面会一直显示「重新规划中…」。
                </p>
              )}
            </>
          )}
        </Card>
      )}

      {/* ── 历史 ─────────────────────────────────────────────── */}
      <Card
        title="历史执行"
        sub="最近的目标 · 点一条查看它的任务分解与汇总"
        span={12}
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
                    ? '搜索只匹配目标描述与状态；清空搜索框可看到全部。'
                    : '在上面输入一句话并点「交给管家」，或点一条示例填入；也可以点名只派给一个人。执行记录会出现在这里。'
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
                    <GoalStatus
                      goal={{
                        state: item.state,
                        artifacts: item.artifacts,
                        completed_tasks: item.completed_tasks,
                        failed_tasks: item.failed_tasks,
                      }}
                    />
                    <span className="os-butler-history-text">{item.natural_language}</span>
                    <span className="os-hint os-butler-history-meta">
                      {relativeTime(item.created_at ?? 0)} · 完成 {item.completed_tasks}/
                      {item.task_count}
                      {item.failed_tasks > 0 ? ` · 失败 ${item.failed_tasks}` : ''}
                      {item.assigned_employee ? ` · ${displayName(item.assigned_employee)}` : ''}
                    </span>
                  </button>
                ))}
              </div>
            )
          }
        </Guard>
      </Card>

      {/* ── 待我确认（真实待审批队列；无签发范围时整块不渲染） ── */}
      {canApproveQueue && (
      <Card
        title="待我确认"
        sub="被安全规则拦下、需要你点头才能继续的动作"
        span={12}
        action={
          <button className="os-link" onClick={pendingApi.reload}>
            刷新
          </button>
        }
      >
        <div id="butler-pending" />
        {pendingApi.status === 403 ? (
          <div className="os-alert" role="alert">
            <div>
              <strong>当前账号不能在这里批准</strong>
              <p style={{ margin: '4px 0 0' }}>
                这个列表只对有批准权限的账号开放。它不代表「没有待确认动作」，
                只是当前账号看不到。请换管理员账号登录后再来。
              </p>
              {pendingApi.error && <p className="os-hint">{pendingApi.error}</p>}
            </div>
          </div>
        ) : pendingApi.error && !pendingApi.data ? (
          <Failure message={pendingApi.error} onRetry={pendingApi.reload} />
        ) : pendingApi.loading ? (
          <Loading rows={2} />
        ) : pendingItems.length === 0 ? (
          <Empty
            title="现在没有等你确认的动作"
            hint="被安全规则拦下、需要你批准的动作才会出现在这里；普通失败不会显示在这。"
          />
        ) : (
          <div className="os-butler-pending">
            {pendingItems.map((item) => {
              const body = approvalBodyOf(item)
              const wildcard = isWildcardBody(body)
              const confirmed = Boolean(wildcardConfirmed[item.pending_id])
              const busy = approvingId === item.pending_id
              return (
                <article
                  key={item.pending_id}
                  className="os-butler-pending-item"
                  data-tone={riskTone(item.risk_level)}
                >
                  <header className="os-butler-pending-head">
                    <span
                      className="os-badge"
                      data-tone={riskTone(item.risk_level)}
                      title={item.risk_level ? `原始等级：${item.risk_level}` : undefined}
                    >
                      风险 {riskLabel(item.risk_level)}
                    </span>
                    <strong className="os-butler-pending-action">{item.action}</strong>
                    <span className="os-hint">{blockedAtText(item.blocked_at)}</span>
                  </header>
                  <p className="os-butler-pending-reason">{item.reason || item.summary || '暂未给出拦截原因'}</p>
                  <p
                    className="os-hint"
                    title={
                      [
                        item.policy_rule ? `规则编号：${item.policy_rule}` : '',
                        item.correlation_id ? `关联编号：${item.correlation_id}` : '',
                      ]
                        .filter(Boolean)
                        .join(' · ') || undefined
                    }
                  >
                    这条动作触发了安全规则，需要你确认。
                    {item.blocked_by ? `（拦截账号：${item.blocked_by}）` : ''}
                  </p>
                  {wildcard && (
                    <label className="os-butler-pending-confirm">
                      <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(event) =>
                          setWildcardConfirmed((current) => ({
                            ...current,
                            [item.pending_id]: event.target.checked,
                          }))
                        }
                      />
                      我确认：这次批准对所有对象生效
                    </label>
                  )}
                  <div className="os-kb-actions">
                    <button
                      className="os-btn"
                      onClick={() => void approve(item, body, wildcard)}
                      disabled={busy || (wildcard && !confirmed)}
                    >
                      {busy ? '批准中…' : '确认并允许'}
                    </button>
                    <span className="os-hint">
                      {wildcard
                        ? '当前只能整类放行，不能只批一个文件或一个项目；需要你再次确认。'
                        : '按列表里的动作批准，参数不改。'}
                    </span>
                  </div>
                  {approveErrors[item.pending_id] && (
                    <p className="os-kb-error" role="alert">
                      {approveErrors[item.pending_id]}
                    </p>
                  )}
                  {approveNotices[item.pending_id] && (
                    <p className="os-note" role="status">
                      {approveNotices[item.pending_id]}
                    </p>
                  )}
                </article>
              )
            })}
          </div>
        )}
      </Card>
      )}
      {activeId && (
        <Card
          title="任务分解"
          sub={
            detail
              ? live
                ? `执行中 · 已出现 ${stats.total} 项 · 已结束 ${stats.finished} 项`
                : `已结束 ${stats.finished} / ${stats.total} 项 · 可交付 ${stats.delivered} 项`
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
                hint="管家开始拆解后，任务会一项项出现；第一项到达前这里保持为空。"
              />
            )
          ) : tasks.length === 0 ? (
            <Empty
              title={live ? '管家正在拆解，第一张任务卡还没到' : '本次没有分解出任务'}
              hint={
                live
                  ? '运行期间会持续返回任务快照；已收到的任务卡会保留，不会因为某次快照不完整而被清空。'
                  : '目标已结束但没有任何任务：可能需要换一种更具体的说法，或检查员工是否可用。'
              }
            />
          ) : (
            <>
              {decompositionHint && (
                <p
                  className={decompositionHint.tone === 'warn' ? 'os-note' : 'os-hint'}
                  role={decompositionHint.tone === 'warn' ? 'status' : undefined}
                >
                  {decompositionHint.text}
                </p>
              )}
              <div className="os-butler-tasks">
              {tasks.map((task) => {
                const info = taskPhaseInfo(task)
                const retryable =
                  info.phase === 'failed' || info.phase === 'awaiting' || info.phase === 'interrupted'
                const busy = retryingId === task.id
                return (
                  <article
                    key={task.id}
                    className="os-butler-task"
                    data-tone={info.tone}
                    data-live={info.phase === 'running' ? 'true' : 'false'}
                  >
                    <header className="os-butler-task-head">
                      <span className="os-badge" data-tone={info.tone}>
                        {info.phase === 'running' && (
                          <span className="os-butler-task-dot" aria-hidden="true" />
                        )}
                        {info.label}
                      </span>
                      <strong className="os-butler-task-name">
                        {taskNameLabel(task.name)}
                      </strong>
                      <span className="os-butler-task-time" title={`原始状态：${info.raw || '缺失'}`}>
                        {durationLabel(task, now / 1000)}
                      </span>
                    </header>
                    <p className="os-butler-task-meta">
                      指派给{' '}
                      <strong>
                        {task.assigned_employee
                          ? displayName(task.assigned_employee)
                          : task.assigned_agent
                            ? agentOwners.get(task.assigned_agent) ?? task.assigned_agent
                            : '未记录'}
                      </strong>
                      {task.capability_id ? ` · 能力 ${capabilityLabel(task.capability_id)}` : ''}
                    </p>
                    {info.note && <p className="os-hint">{info.note}</p>}
                    {task.verification && (
                      <p className="os-hint">
                        验证：
                        {task.verification === 'unverified'
                          ? '未验证（没有独立证据证明结果正确）'
                          : task.verification === 'success'
                            ? '已通过'
                            : task.verification === 'partial'
                              ? '部分通过'
                              : task.verification === 'failed'
                                ? '未通过'
                                : task.verification}
                        {typeof task.score === 'number'
                          ? ` · 得分 ${numberText(task.score, 2)}`
                          : ''}
                      </p>
                    )}
                    {task.description && (
                      <p className="os-hint">{taskDescriptionLabel(task.description)}</p>
                    )}
                    {task.result && (
                      <details className="os-butler-task-result">
                        <summary>查看任务结果</summary>
                        <pre className="os-pre">
                          <LinkifiedText text={task.result} />
                        </pre>
                      </details>
                    )}
                    {task.error && (
                      <p className="os-kb-error">
                        {info.phase === 'interrupted' ? '中断说明：' : '失败原因：'}
                        {task.error}
                      </p>
                    )}
                    {info.phase === 'awaiting' && (
                      <div className="os-kb-actions">
                        <button className="os-btn os-btn-ghost" onClick={scrollToPending}>
                          去「待我确认」授权
                        </button>
                        <button
                          className="os-btn os-btn-ghost"
                          onClick={() => void retryTask(task)}
                          disabled={busy}
                          title="如果已经批准，可在这里重新执行这个任务"
                        >
                          {busy ? '重试中…' : '重试此任务'}
                        </button>
                      </div>
                    )}
                    {retryable && retryErrors[task.id] && (
                      <p className="os-kb-error" role="alert">
                        {retryErrors[task.id]}
                      </p>
                    )}
                    {(info.phase === 'failed' || info.phase === 'interrupted') && (
                      <button
                        className="os-btn os-btn-ghost"
                        onClick={() => void retryTask(task)}
                        disabled={busy}
                        title="只重跑这一个任务；其余任务的结果不变。重试是同步的，完成才返回。"
                      >
                        {busy ? '重试中…（同步执行）' : '重试此任务'}
                      </button>
                    )}
                  </article>
                )
              })}
              </div>
            </>
          )}
        </Card>
      )}

      {/* ── 能力边界（折叠成一行，不占首屏） ─────────────────── */}
      <Card title="能力边界" span={12}>
        <details className="os-butler-gaps">
          <summary className="os-hint">
            当前有一项限制：批准只能整类生效，不能只批一个文件或一个项目（点开查看）
          </summary>
          <div className="os-hint" style={{ marginTop: 8 }}>
            <p style={{ margin: '0 0 6px' }}>
              需要你批准的动作，目前只能对所有对象一起放行；页面会请你再次确认。
              其余能力不受影响。
            </p>
            <p style={{ margin: 0 }}>
              文本产物可以在汇总里点开看正文；二进制或超大文件会被明确拒绝并说明原因。
            </p>
          </div>
        </details>
      </Card>
    </div>
  )
}

/**
 * 结果里的真实来源必须可点击。只把 http(s) 文本变成 <a>，其余原样输出，
 * 不解析 markdown、不注入 HTML，避免把模型输出当成可执行内容。
 */
function LinkifiedText({ text }: { text: string }) {
  const segments = splitLinks(text)
  return (
    <>
      {segments.map((segment, index) =>
        segment.href ? (
          <a
            key={`${index}-${segment.href}`}
            href={segment.href}
            target="_blank"
            rel="noreferrer noopener"
          >
            {segment.value}
          </a>
        ) : (
          <span key={`${index}-text`}>{segment.value}</span>
        ),
      )}
    </>
  )
}

export default Butler
