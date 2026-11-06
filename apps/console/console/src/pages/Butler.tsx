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
import { capabilityLabel, relativeTime, safeText, useNow } from '../lib/format'
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
  mergeTaskSnapshots,
  parseTaskTime,
  riskTone,
  sortGoalSummaries,
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
import { Card, Empty, Failure, Guard, Loading } from '../components/ui'

type ArtifactPreview = {
  path: string
  loading: boolean
  content: string | null
  size: number | null
  error: string | null
}

type GoalMode = 'butler' | 'assigned'

const PENDING_POLL_MS = 30_000

/** 后端 `duration_ms` 是真实时间戳差；没有就退回由时间戳推算的口径。 */
function durationLabel(task: GoalTask, nowSeconds: number): string {
  const ms = Number(task.duration_ms)
  if (task.duration_ms !== undefined && task.duration_ms !== null && Number.isFinite(ms) && ms >= 0) {
    if (ms < 1000) return `${Math.round(ms)} 毫秒`
    if (ms < 60_000) return `${(ms / 1000).toFixed(1)} 秒`
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
  if (value < 60_000) return `${(value / 1000).toFixed(1)} 秒`
  return `${Math.floor(value / 60_000)} 分 ${Math.round((value % 60_000) / 1000)} 秒`
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

/**
 * 把后端稳定错误码翻译成**下一步该做什么**。
 *
 * 关键差别（不能笼统报「提交失败」）：
 *   * `agent_not_found` → 这个人不存在，重新选；
 *   * `agent_unavailable` → 这个人被暂停，先去恢复；
 *   * `task_not_retryable` → 不是失败态，没东西可重试；
 *   * `goal_running` → 目标还在跑，等结束再单任务重试。
 */
function goalWorkMessage(error: unknown, fallback: string): string {
  const code = error instanceof ApiError ? error.code : null
  switch (code) {
    case 'agent_not_found':
    case 'employee_not_found':
      return '指定的员工不存在，或不在当前可见的名册里。不会换成其他员工执行，请重新选择。'
    case 'employee_disabled':
      return '这位员工已停用，不能接受指派。请先到员工页启用它，再重新派活。'
    case 'agent_unavailable':
    case 'employee_unavailable':
      return '这位员工当前没有可用的执行个体（暂停中），不能接受指派。请先到员工页恢复，再重新派活。'
    case 'assign_target_required':
      return '必须且只能指定一个执行主体：员工，或某个执行个体。请重新选择。'
    case 'task_not_retryable':
      return '这个任务当前不是失败态，没有可重试的内容。只有失败任务能单独重试。'
    case 'goal_running':
      return '这个目标还在运行。等它结束后，再回来单独重试失败的任务。'
    case 'task_not_found':
      return '后端已经找不到这个任务（可能已被清理）。刷新目标后按最新任务卡操作。'
    case 'goal_not_found':
      return '这个目标已经不存在（可能已被清理）。'
    default:
      return `${fallback}：${errorText(error)}`
  }
}

function approveFailureMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return '你没有签发权限：只有具备签发范围的主体能签发授权。这不是队列为空。'
  }
  return errorText(error)
}

function grantIdOf(raw: Json): string {
  const grant = raw.grant
  if (grant && typeof grant === 'object' && !Array.isArray(grant)) {
    const id = (grant as Record<string, unknown>).grant_id
    if (typeof id === 'string') return id
  }
  return ''
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
}: {
  query: string
  onOpenApproval: () => void
  canApproveQueue: boolean
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
        map.set(agent.id, `${employee.name}（${agent.name || agent.id}）`)
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
        setSubmitError('后端返回了目标但没有 goal_id：无法追踪这次执行，请检查网关版本。')
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
        setAssignError('后端返回了指派结果但没有 goal_id：无法追踪这次执行。')
        return
      }
      let label = employeeTarget
      if (!label && agentId) {
        const roster = employees.data?.employees ?? []
        for (const employee of roster) {
          const agent = (employee.agents ?? []).find((item) => item.id === agentId)
          if (agent) {
            label = `${employee.name} · ${agent.name || agent.id}`
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
      setAssignNotice(`已点名派给 ${label}（目标 ${id}）。这次不经过管家拆解，下面只显示这位员工的任务。`)
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
        '已发送中止请求。下面显示的是后端真实终态；已经结束的目标不会被改写。',
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
      setReplanNotice('管家已重新规划并重跑；下面显示后端返回的最新结果。')
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
        setReplanNotice('单任务重试已完成（同步执行）；下面刷新为该目标的最新任务清单。')
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
        const raw = await approvePending(requestBody)
        const grantId = grantIdOf(raw)
        setApproveNotices((current) => ({
          ...current,
          [item.pending_id]: `已签发授权${grantId ? `（${grantId}）` : ''}。授权不会自动继续执行：回到失败任务点「重试此任务」，或「重新规划并重跑」。`,
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
        label: `${employee.name}（整位员工）`,
        paused: disabled || !(employee.agents ?? []).some((agent) => agent.status !== 'paused'),
      })
      for (const agent of employee.agents ?? []) {
        agentOptions.push({
          value: `agent:${agent.id}`,
          label: `${employee.name} · ${agent.name || agent.id}`,
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
  const employeeNames = (employees.data?.employees ?? []).map((item) => item.name)
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
                    : '不指定（管家自动拆解与分派）'}
              </option>
              {assignableEmployees.map((employee) => (
                <option key={employee.name} value={employee.name}>
                  {employee.name}
                </option>
              ))}
            </select>
          </div>
          <p className="os-hint os-butler-options-hint">
            {promptEmployee
              ? `整个目标会钉给「${promptEmployee}」执行，不再由管家改派；它的子任务都归这位员工。`
              : projects.error
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
          提交后管家在后台执行，页面每 2 秒向 GET /v1/goals/&#123;goal_id&#125; 确认一次状态。
          任务卡会随增量快照逐条出现；只有全部终结后才给总进度百分比，不用假进度充数。
        </p>
      </Card>

      {/* ── 单独指派（点名执行） ─────────────────────────────── */}
      <Card
        title="单独指派（点名执行）"
        sub="不走管家拆解：你选员工、写任务，直接派给这个人"
        span={activeId ? 5 : 12}
      >
        <div className="os-butler-assign-flag" aria-label="指定执行，不经过自动拆解">
          <span className="os-badge" data-tone="accent">指定执行</span>
          <span>这条任务不会交给管家自动拆解，也不会换人执行。</span>
        </div>

        <div className="os-field">
          <label htmlFor="butler-assign-employee">员工</label>
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
              <optgroup label="按员工（整位）">
                {assignOptions.employees.map((option) => (
                  <option key={option.value} value={option.value} disabled={option.paused}>
                    {option.label}
                    {option.paused ? '（停用或无可执行个体）' : ''}
                  </option>
                ))}
              </optgroup>
            )}
            {assignOptions.agents.length > 0 && (
              <optgroup label="按执行个体（精确到个体）">
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
            执行主体：{selectedAssignOption.label}
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
              ? `${assignedGoal ? '单独指派' : '管家自动拆解'} · ${goalStateLabel(state)} · 目标 ${activeId}`
              : `目标 ${activeId}`
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
                      执行主体：{assignedLabel}（这是你点名指派的，不是管家自动拆解）
                    </p>
                  )}
                </div>
                <div className="os-butler-run-badges">
                  <span className="os-badge" data-tone={assignedGoal ? 'accent' : 'muted'}>
                    {assignedGoal ? '单独指派' : '管家自动拆解'}
                  </span>
                  <span className="os-badge" data-tone={acceptanceTone(state)}>
                    {goalStateLabel(state)}
                  </span>
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
                        第一张任务卡到达后会逐条出现。后端现在会在执行期间返回增量任务快照，
                        不需要等整次执行结束；在此之前不伪造任何任务级进度。
                      </p>
                      <p className="os-hint" style={{ marginTop: 4 }}>
                        如果你不想等了，可以「中止本次执行」；中止后下面会显示后端真实终态。
                      </p>
                    </div>
                  ) : (
                    <p className="os-hint" style={{ marginTop: 8 }}>
                      任务卡按增量快照更新：某次快照可能只包含刚变化的任务，
                      已出现的卡不会被清空，也不会因为字段暂缺而消失。
                    </p>
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

                  {detail.employees && detail.employees.length > 0 && (
                    <div className="os-note" role="status" style={{ marginTop: 10 }}>
                      <strong>员工汇总（按真实任务归属统计）</strong>
                      <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                        {detail.employees.map((row) => (
                          <li key={row.employee ?? 'unattributed'}>
                            {row.employee ?? '未归属'} · 做了 {row.tasks} 项 · 成功 {row.completed} · 失败{' '}
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
                            ? '这些任务是被策略拦截后失败的，不是普通报错。待审批入口在下方「待我确认」；批准后回到任务卡点「重试此任务」，授权才会生效。'
                            : '这些任务被策略拦下，需要确认后才能重试。确认完成后再回到任务卡点「重试此任务」，无需其他操作。'}
                        </p>
                        {canApproveQueue && (
                          <div className="os-kb-actions" style={{ marginTop: 8 }}>
                            <button className="os-btn" onClick={scrollToPending}>
                              去「待我确认」
                            </button>
                            <button className="os-link" onClick={onOpenApproval}>
                              查看已签发凭据
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

      {/* ── 待我确认（真实待审批队列；无签发范围时整块不渲染） ── */}
      {canApproveQueue && (
      <Card
        title="待我确认"
        sub="策略拦截后真实待签发的动作 · 采用队列给出的请求体批准"
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
              <strong>你没有签发权限</strong>
              <p style={{ margin: '4px 0 0' }}>
                待审批队列只对具备签发范围的主体可见。
                这不是「队列为空」，也不代表没有动作在等你 —— 只是当前账号看不到。
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
            title="没有等待你确认的动作"
            hint="队列只显示最近被策略 defer 拦下的真实事件；普通失败不会出现在这里。"
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
                    <span className="os-badge" data-tone={riskTone(item.risk_level)}>
                      风险 {item.risk_level || '未知'}
                    </span>
                    <strong className="os-butler-pending-action">{item.action}</strong>
                    <span className="os-hint">{blockedAtText(item.blocked_at)}</span>
                  </header>
                  <p className="os-butler-pending-reason">{item.reason || item.summary || '后端未给出拦截原因'}</p>
                  <p className="os-hint">
                    规则 {item.policy_rule ?? '未记录'} · 关联 {item.correlation_id ?? '未记录'} ·
                    拦截主体 {item.blocked_by ?? '未记录'}
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
                      我确认这条授权覆盖所有目标（target = *）
                    </label>
                  )}
                  <div className="os-kb-actions">
                    <button
                      className="os-btn"
                      onClick={() => void approve(item, body, wildcard)}
                      disabled={busy || (wildcard && !confirmed)}
                    >
                      {busy ? '批准中…' : '采用此请求体批准'}
                    </button>
                    <span className="os-hint">
                      {wildcard
                        ? '后端要求通配授权显式二次确认；请求体仅追加 confirm_wildcard。'
                        : '按队列给出的动作与目标签发，不改参数。'}
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
                : `总进度 ${stats.percent}% · 已结束 ${stats.finished} / ${stats.total} 项`
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
                hint="管家开始拆解后，任务卡会随着增量快照逐条出现；第一张卡到达前这里保持为空。"
              />
            )
          ) : tasks.length === 0 ? (
            <Empty
              title={live ? '管家正在拆解，第一张任务卡还没到' : '本次没有分解出任务'}
              hint={
                live
                  ? '运行期间后端会返回增量快照；已收到的任务卡会保留，不会因为某次快照不完整而被清空。'
                  : '目标已结束但没有任何任务：可能需要换一种更具体的说法，或检查员工是否可用。'
              }
            />
          ) : (
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
                        {task.name || '（未命名任务）'}
                      </strong>
                      <span className="os-butler-task-time" title={`原始状态：${info.raw || '缺失'}`}>
                        {durationLabel(task, now / 1000)}
                      </span>
                    </header>
                    <p className="os-butler-task-meta">
                      指派给{' '}
                      <strong>
                        {task.assigned_employee
                          ? task.assigned_employee
                          : task.assigned_agent
                            ? agentOwners.get(task.assigned_agent) ?? task.assigned_agent
                            : '后端未记录'}
                      </strong>
                      {task.capability_id ? ` · 能力 ${capabilityLabel(task.capability_id)}` : ''}
                    </p>
                    {info.note && <p className="os-hint">{info.note}</p>}
                    {task.description && <p className="os-hint">{task.description}</p>}
                    {task.result && (
                      <details className="os-butler-task-result">
                        <summary>查看任务结果</summary>
                        <pre className="os-pre">{task.result}</pre>
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
                          title="若审批队列已签发授权，可在这里重新执行这个任务"
                        >
                          {busy ? '重试中…（同步执行）' : '授权后重试此任务'}
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
                        title="只重跑这一个任务；其余任务的结果不变。后端这个调用是同步的，完成才返回。"
                      >
                        {busy ? '重试中…（同步执行）' : '重试此任务'}
                      </button>
                    )}
                  </article>
                )
              })}
            </div>
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
                    ? '搜索只匹配目标描述、id 与状态；清空搜索框可看到全部。'
                    : '在上面输入一句话并交给管家，或点名派活；执行记录会出现在这里。'
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
                      {item.assigned_employee ? ` · ${item.assigned_employee}` : ''}
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
          <dt>待审批授权的目标粒度</dt>
          <dd>
            队列的授权模板使用 target=*（审计事件没有记录具体目标），所以无法只批准
            「某一个路径 / 某一个项目」。页面要求你显式勾选「覆盖所有目标」，后端才签发
            通配授权；这是当前真实边界。
          </dd>
          <dt>产物内联预览</dt>
          <dd>
            文本文件可在汇总里点开，走 GET /v1/files/content 显示正文；二进制或超大文件
            会被后端拒绝，页面如实显示拒绝原因，不会伪装成空内容。
          </dd>
        </dl>
      </Card>
    </div>
  )
}

export default Butler
