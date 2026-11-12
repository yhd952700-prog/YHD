/**
 * 企业总览的纯聚合层。
 *
 * 只做「把已挂载接口的返回值算成页面要看的数字」，不发请求、不造字段：
 * - 输入形状对着 `GET /v1/employees`、`GET /v1/org/tree`、`GET /v1/tasks` 的
 *   真实返回写；缺字段时返回 `null`，由页面写「暂未提供」并给出原因。
 * - 「今日完成」按本地时区自然日判断，读的是任务台账真实的 `completed_at`；
 *   旧数据没有完成时间时单独计数，不拿 `created_at` 冒充。
 * - 部门任务是「涉及该节点成员的任务数」：任务负责人可能是 `甲、乙` 这样的
 *   多人串，按 `、` 拆开；同一任务涉及多个部门时会在这些节点各计一次。
 */

export interface EmployeeAgentLike {
  status?: string
}

export interface EmployeeLike {
  name?: string
  employment_status?: string
  agents?: EmployeeAgentLike[]
}

export interface EmployeesLike {
  employees?: EmployeeLike[]
  employee_count?: number
  /** 兼容旧字段：没有 employees 时才用它数默认员工的 agent pool。 */
  agents?: EmployeeAgentLike[]
}

export interface EmployeeSummary {
  total: number
  active: number
  disabled: number
  /** 状态不是 active/disabled，或列表里没铺开的那部分；不并入任何已知态。 */
  other: number
  agents: number
  runningAgents: number
  employeesWithRunningAgents: number
}

export function summarizeEmployees(payload: EmployeesLike | null): EmployeeSummary | null {
  if (!payload) return null
  const list = Array.isArray(payload.employees) ? payload.employees : null
  const hasCount = typeof payload.employee_count === 'number' && Number.isFinite(payload.employee_count)
  // 响应里既没有名册也没有总数：这是形状不对，不是「0 个员工」。
  if (!list && !hasCount) return null

  const employees = list ?? []
  const total = hasCount ? Number(payload.employee_count) : employees.length
  let active = 0
  let disabled = 0
  let other = Math.max(0, total - employees.length)
  for (const employee of employees) {
    const status = String(employee.employment_status ?? '').trim().toLowerCase()
    if (status === 'active') active += 1
    else if (status === 'disabled') disabled += 1
    else other += 1
  }

  // 有名册时以名册里的 agent pool 为准；没有名册才退回旧字段，避免两处重复计数。
  const agentLists = list
    ? employees.map((employee) => (Array.isArray(employee.agents) ? employee.agents : []))
    : [Array.isArray(payload.agents) ? payload.agents : []]
  const agents = agentLists.reduce((sum, items) => sum + items.length, 0)
  const runningAgents = agentLists.reduce(
    (sum, items) =>
      sum + items.filter((agent) => String(agent.status ?? '').toLowerCase() === 'running').length,
    0,
  )
  const employeesWithRunningAgents = list
    ? employees.filter((employee) =>
        (Array.isArray(employee.agents) ? employee.agents : []).some(
          (agent) => String(agent.status ?? '').toLowerCase() === 'running',
        ),
      ).length
    : runningAgents > 0
      ? 1
      : 0

  return {
    total,
    active,
    disabled,
    other,
    agents,
    runningAgents,
    employeesWithRunningAgents,
  }
}

export interface OrgAssignmentLike {
  subject_type?: string
  subject_id?: string
  subject_label?: string
}

export interface OrgNodeLike {
  node_id?: string
  name?: string
  kind?: string
  parent_id?: string | null
  assignments?: OrgAssignmentLike[]
  children?: OrgNodeLike[]
}

export interface OrgTreeLike {
  roots?: OrgNodeLike[]
  node_count?: number
  assignment_count?: number
}

export interface OrgSummary {
  nodeCount: number
  assignmentCount: number
  employeeMemberCount: number
  humanMemberCount: number
  duplicateEmployeeCount: number
  roots: OrgNodeLike[]
}

function employeeLabel(assignment: OrgAssignmentLike): string {
  return String(assignment.subject_id ?? assignment.subject_label ?? '').trim()
}

function humanLabel(assignment: OrgAssignmentLike): string {
  return String(assignment.subject_id ?? assignment.subject_label ?? '').trim()
}

function walkOrg(
  nodes: OrgNodeLike[],
  visit: (node: OrgNodeLike, depth: number, path: string) => void,
  depth = 0,
  prefix = '',
) {
  nodes.forEach((node, index) => {
    const path = `${prefix}/${index}`
    visit(node, depth, path)
    const children = Array.isArray(node.children) ? node.children : []
    walkOrg(children, visit, depth + 1, path)
  })
}

export function summarizeOrg(payload: OrgTreeLike | null): OrgSummary | null {
  if (!payload) return null
  const roots = Array.isArray(payload.roots) ? payload.roots : null
  const hasCount = typeof payload.node_count === 'number' && Number.isFinite(payload.node_count)
  if (!roots && !hasCount) return null

  const tree = roots ?? []
  let countedNodes = 0
  let countedAssignments = 0
  const employees = new Set<string>()
  const humans = new Set<string>()
  const employeeNodes = new Map<string, Set<string>>()

  walkOrg(tree, (node, depth, path) => {
    countedNodes += 1
    const nodeKey = String(node.node_id ?? `${depth}:${path}:${node.name ?? ''}`)
    const assignments = Array.isArray(node.assignments) ? node.assignments : []
    countedAssignments += assignments.length
    for (const assignment of assignments) {
      const kind = String(assignment.subject_type ?? '').trim().toLowerCase()
      if (kind === 'employee') {
        const name = employeeLabel(assignment)
        if (!name) continue
        employees.add(name)
        const nodes = employeeNodes.get(name) ?? new Set<string>()
        nodes.add(nodeKey)
        employeeNodes.set(name, nodes)
      } else if (kind === 'human') {
        const name = humanLabel(assignment)
        if (name) humans.add(name)
      }
    }
  })

  let duplicateEmployeeCount = 0
  for (const nodes of employeeNodes.values()) {
    if (nodes.size > 1) duplicateEmployeeCount += 1
  }

  return {
    nodeCount: hasCount ? Number(payload.node_count) : countedNodes,
    assignmentCount:
      typeof payload.assignment_count === 'number' && Number.isFinite(payload.assignment_count)
        ? Number(payload.assignment_count)
        : countedAssignments,
    employeeMemberCount: employees.size,
    humanMemberCount: humans.size,
    duplicateEmployeeCount,
    roots: tree,
  }
}

export interface TaskAssigneeLike {
  type?: string
  id?: string
  name?: string
}

export interface TaskLike {
  task_id?: string
  status?: string
  title?: string
  assignee?: TaskAssigneeLike | null
  completed_at?: string | null
  updated_at?: string | null
  created_at?: string | null
  started_at?: string | null
  human_hold?: boolean
  error?: string | null
}

export interface TaskPageLike {
  total?: number
  items?: TaskLike[]
  visible_scope?: string
}

export interface TaskCoverage {
  scanned: number
  total: number
  /** 扫描条数小于全量：所有按扫描结果算出来的数都只是下限。 */
  partial: boolean
}

export function taskCoverage(page: TaskPageLike | null): TaskCoverage | null {
  if (!page || !Array.isArray(page.items)) return null
  const scanned = page.items.length
  const total =
    typeof page.total === 'number' && Number.isFinite(page.total) ? Number(page.total) : scanned
  return { scanned, total, partial: total > scanned }
}

export interface CompletedToday {
  count: number
  scanned: number
  total: number
  exact: boolean
  /** 已完成但缺少完成时间的条数；这些不能按今日算，也不能拿创建时间顶替。 */
  missingTime: number
}

function sameLocalDay(left: Date, right: Date): boolean {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  )
}

export function completedToday(page: TaskPageLike | null, nowMs: number): CompletedToday | null {
  if (!page || !Array.isArray(page.items)) return null
  const today = new Date(nowMs)
  const scanned = page.items.length
  const total =
    typeof page.total === 'number' && Number.isFinite(page.total) ? Number(page.total) : scanned
  let count = 0
  let missingTime = 0
  for (const task of page.items) {
    if (!task.completed_at) {
      missingTime += 1
      continue
    }
    const stamp = Date.parse(task.completed_at)
    if (!Number.isFinite(stamp)) {
      missingTime += 1
      continue
    }
    if (sameLocalDay(new Date(stamp), today)) count += 1
  }
  return { count, scanned, total, exact: total <= scanned, missingTime }
}

export interface HumanHoldSummary {
  count: number
  scanned: number
  total: number
  exact: boolean
}

export function summarizeHumanHold(page: TaskPageLike | null): HumanHoldSummary | null {
  if (!page || !Array.isArray(page.items)) return null
  const scanned = page.items.length
  const total =
    typeof page.total === 'number' && Number.isFinite(page.total) ? Number(page.total) : scanned
  return {
    count: page.items.filter((task) => task.human_hold === true).length,
    scanned,
    total,
    exact: total <= scanned,
  }
}

export interface DepartmentRow {
  key: string
  nodeId: string
  name: string
  kind: string
  depth: number
  memberCount: number
  /** `null` 表示任务数据没读到，不是 0。 */
  directTaskCount: number | null
  subtreeTaskCount: number | null
  children: DepartmentRow[]
}

/** 任务负责人是员工名；后端在多执行者时用 `、` 串起来。 */
export function taskEmployeeNames(task: TaskLike): string[] {
  const assignee = task.assignee
  if (!assignee || String(assignee.type ?? '').trim().toLowerCase() !== 'employee') return []
  return String(assignee.name ?? assignee.id ?? '')
    .split('、')
    .map((item) => item.trim())
    .filter(Boolean)
}

interface TaskNameIndex {
  byMember: Map<string, Set<string>>
}

function indexTasks(page: TaskPageLike | null): TaskNameIndex {
  const byMember = new Map<string, Set<string>>()
  const items = page && Array.isArray(page.items) ? page.items : []
  items.forEach((task, index) => {
    const key = String(task.task_id ?? `task-${index}`)
    for (const name of taskEmployeeNames(task)) {
      const tasks = byMember.get(name) ?? new Set<string>()
      tasks.add(key)
      byMember.set(name, tasks)
    }
  })
  return { byMember }
}

function memberNames(assignments: OrgAssignmentLike[]): Set<string> {
  const names = new Set<string>()
  for (const assignment of assignments) {
    if (String(assignment.subject_type ?? '').trim().toLowerCase() !== 'employee') continue
    const name = employeeLabel(assignment)
    if (name) names.add(name)
  }
  return names
}

export function buildDepartmentRows(
  org: OrgTreeLike | null,
  tasks: TaskPageLike | null,
): DepartmentRow[] {
  if (!org || !Array.isArray(org.roots)) return []
  const taskIndex = indexTasks(tasks)
  const tasksKnown = tasks !== null && Array.isArray(tasks.items)

  interface Built {
    row: DepartmentRow
    taskIds: Set<string>
  }

  const build = (node: OrgNodeLike, depth: number, siblingIndex: number): Built => {
    const assignments = Array.isArray(node.assignments) ? node.assignments : []
    const names = memberNames(assignments)
    const children = Array.isArray(node.children) ? node.children : []
    const childBuilds = children.map((child, index) => build(child, depth + 1, index))

    const directIds = new Set<string>()
    if (tasksKnown) {
      for (const name of names) {
        for (const taskId of taskIndex.byMember.get(name) ?? []) directIds.add(taskId)
      }
    }
    const subtreeIds = new Set<string>(directIds)
    for (const child of childBuilds) {
      for (const taskId of child.taskIds) subtreeIds.add(taskId)
    }

    const nodeId = String(node.node_id ?? `node-${depth}-${siblingIndex}`)
    return {
      row: {
        key: nodeId,
        nodeId,
        name: String(node.name ?? '').trim() || '未命名节点',
        kind: String(node.kind ?? '').trim(),
        depth,
        memberCount: names.size,
        directTaskCount: tasksKnown ? directIds.size : null,
        subtreeTaskCount: tasksKnown ? subtreeIds.size : null,
        children: childBuilds.map((child) => child.row),
      },
      taskIds: subtreeIds,
    }
  }

  return org.roots.map((node, index) => build(node, 0, index).row)
}

// ─── 待审批 ────────────────────────────────────────────────────────────────

export interface PendingApprovalLike {
  pending_id?: string
  action?: string
  risk_level?: string
  source_channel?: string
  source_device?: string | null
  blocked_at?: number | string | null
  blocked_by?: string | null
  reason?: string
  summary?: string
}

export interface PendingApprovalsLike {
  count?: number
  items?: PendingApprovalLike[]
  as_of?: number | null
  lookback_seconds?: number | null
}

const RISK_WEIGHT: Record<string, number> = {
  CRITICAL: 4,
  HIGH: 3,
  MEDIUM: 2,
  LOW: 1,
}

export function approvalRiskWeight(raw: string | null | undefined): number {
  return RISK_WEIGHT[String(raw ?? '').trim().toUpperCase()] ?? 0
}

export function approvalRiskLabel(raw: string | null | undefined): string {
  switch (String(raw ?? '').trim().toUpperCase()) {
    case 'CRITICAL':
      return '极高'
    case 'HIGH':
      return '高'
    case 'MEDIUM':
      return '中'
    case 'LOW':
      return '低'
    default:
      return '未知'
  }
}

export function approvalBlockedSeconds(value: number | string | null | undefined): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 1e12 ? value / 1000 : value
  }
  if (typeof value === 'string' && value.trim()) {
    const stamp = Date.parse(value)
    return Number.isFinite(stamp) ? stamp / 1000 : null
  }
  return null
}

/** 按风险降序、拦截时间升序排序；返回 `null` 表示响应形状不对。 */
export function sortPendingApprovals(
  payload: PendingApprovalsLike | null,
): PendingApprovalLike[] | null {
  if (!payload || !Array.isArray(payload.items)) return null
  return payload.items.slice().sort((left, right) => {
    const risk = approvalRiskWeight(right.risk_level) - approvalRiskWeight(left.risk_level)
    if (risk !== 0) return risk
    const leftAt = approvalBlockedSeconds(left.blocked_at) ?? Number.POSITIVE_INFINITY
    const rightAt = approvalBlockedSeconds(right.blocked_at) ?? Number.POSITIVE_INFINITY
    if (leftAt !== rightAt) return leftAt < rightAt ? -1 : 1
    return String(left.pending_id ?? '').localeCompare(String(right.pending_id ?? ''))
  })
}
