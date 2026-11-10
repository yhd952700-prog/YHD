/**
 * 组织架构 —— 数据层接口与纯展示计算。
 *
 * 对应本地服务 `src/gateway/org.py` 的 15 个 `/v1/org/*` 接口。这里只做三件事：
 * 1. 把请求收敛成有名字的函数（页面不拼 URL、不猜字段）；
 * 2. 把树的扁平化、可移动目标、影响面、回滚差异算成纯函数 —— 这些是"说得对不对"
 *    的关键，单独可测，不依赖渲染；
 * 3. 如实翻译服务端的诚实边界：任务库读不到就是「未检查」，绝不写成「无风险」。
 *
 * 组织数据**当前不参与权限判定**。影响检查里的「组织内任职失效」只表示组织内的
 * 任职/负责人记录本身失效，不代表账号权限被收回；界面文案必须原样保留这层含义。
 */

import { ApiError, apiFetch } from './api'

const BASE = '/v1/org'

// ── 节点类型 ────────────────────────────────────────────────────

export const ORG_KINDS = [
  'company',
  'business_unit',
  'branch',
  'department',
  'team',
  'position',
] as const

export type OrgKind = (typeof ORG_KINDS)[number]

const KIND_LABELS: Record<string, string> = {
  company: '企业',
  business_unit: '事业部',
  branch: '分公司',
  department: '部门',
  team: '小组',
  position: '岗位',
}

/** 类型 → 人话。未知类型原样返回，不编造新类别。 */
export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

// ── 数据结构 ────────────────────────────────────────────────────

export interface OrgNode {
  node_id: string
  account_id: string
  parent_id: string | null
  kind: string
  name: string
  description: string
  sort_order: number
  created_at: number
  updated_at: number
  created_by: string
}

export interface OrgAssignment {
  assignment_id: string
  account_id: string
  node_id: string
  subject_type: string
  subject_id: string
  subject_label: string
  title: string
  is_lead: boolean
  is_primary: boolean
  reports_to_assignment_id: string | null
  created_at: number
  updated_at: number
}

export interface OrgTreeNode extends OrgNode {
  lead: OrgAssignment | null
  assignments: OrgAssignment[]
  depth: number
  children: OrgTreeNode[]
}

export interface OrgVersion {
  version_id: string
  account_id: string
  seq: number
  created_at: number
  actor: string
  action: string
  target_node_id: string | null
  summary: Record<string, unknown>
}

export interface OrgSnapshotNode {
  node_id: string
  parent_id: string
  kind: string
  name: string
  description: string
  sort_order: number
  created_at: number
  updated_at: number
  created_by: string
}

export interface OrgSnapshotAssignment {
  assignment_id: string
  node_id: string
  subject_type: string
  subject_id: string
  subject_label: string
  title: string
  is_lead: number
  is_primary: number
  reports_to_assignment_id: string
  created_at: number
  updated_at: number
}

export interface OrgSnapshot {
  nodes: OrgSnapshotNode[]
  assignments: OrgSnapshotAssignment[]
}

export interface OrgVersionDetail extends OrgVersion {
  snapshot: OrgSnapshot
}

export interface OrgTreeResponse {
  account_id: string
  roots: OrgTreeNode[]
  node_count: number
  assignment_count: number
  latest_version: OrgVersion | null
  view_account: string | null
}

export interface OrgVersionsResponse {
  account_id: string
  versions: OrgVersion[]
  count: number
  total: number
}

export interface ImpactBlocker {
  code: string
  message: string
  node_ids?: string[]
  node_names?: string[]
  assignment_ids?: string[]
}

export interface ImpactItem {
  node_id?: string
  node_name?: string
  reason?: string
  subject_type?: string
  subject_id?: string
  assignment_id?: string
  role?: string
  goal_id?: string
  task_id?: string
  task_status?: string
  employee?: string
  goal?: string
}

export interface ImpactWarning {
  label?: string
  count?: number | null
  items?: ImpactItem[]
  note?: string
  checked?: boolean
  reason?: string
}

export interface OrgImpact {
  action: string
  account_id: string
  allowed: boolean
  blockers: ImpactBlocker[]
  warnings: {
    orphaned_lead?: ImpactWarning
    unattended_tasks?: ImpactWarning & { checked?: boolean; reason?: string }
    revoked_authority?: ImpactWarning
  }
  affected_node_ids: string[]
  note: string
}

export interface ImpactRequest {
  action: 'delete_node' | 'move_node' | 'clear_lead' | 'remove_assignment'
  node_id?: string
  assignment_id?: string
  target_parent_id?: string
  cascade?: boolean
  clear_reports?: boolean
}

export interface NodeCreateInput {
  name: string
  kind: string
  parent_id?: string | null
  description?: string
}

export interface MemberInput {
  subject_type: string
  subject_id: string
  subject_label?: string
  title?: string
  is_primary?: boolean
  is_lead?: boolean
  reports_to_assignment_id?: string | null
}

// ── 请求 ────────────────────────────────────────────────────────

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  return apiFetch<T>(`${BASE}${path}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

export function loadOrgTree(): Promise<OrgTreeResponse> {
  return send<OrgTreeResponse>('/tree', 'GET')
}

export function createOrgNode(input: NodeCreateInput): Promise<{ node: OrgNode }> {
  return send('/nodes', 'POST', {
    name: input.name,
    kind: input.kind,
    parent_id: input.parent_id ?? null,
    description: input.description ?? '',
  })
}

export function patchOrgNode(
  nodeId: string,
  patch: { name?: string; kind?: string; description?: string },
): Promise<{ node: OrgNode }> {
  return send(`/nodes/${encodeURIComponent(nodeId)}`, 'PATCH', patch)
}

export function moveOrgNode(nodeId: string, parentId: string | null): Promise<{ node: OrgNode }> {
  return send(`/nodes/${encodeURIComponent(nodeId)}/move`, 'POST', { parent_id: parentId ?? null })
}

export function deleteOrgNode(
  nodeId: string,
  cascade: boolean,
): Promise<{ removed_nodes: number; removed_assignments: number }> {
  return send(
    `/nodes/${encodeURIComponent(nodeId)}?cascade=${cascade ? 'true' : 'false'}`,
    'DELETE',
  )
}

export function setOrgLead(
  nodeId: string,
  input: { subject_type: string; subject_id: string; title?: string },
): Promise<{ assignment: OrgAssignment }> {
  return send(`/nodes/${encodeURIComponent(nodeId)}/lead`, 'PUT', {
    subject_type: input.subject_type,
    subject_id: input.subject_id,
    title: input.title ?? '',
  })
}

export function clearOrgLead(
  nodeId: string,
  clearReports: boolean,
): Promise<{ reports_unbound: number }> {
  return send(
    `/nodes/${encodeURIComponent(nodeId)}/lead?clear_reports=${clearReports ? 'true' : 'false'}`,
    'DELETE',
  )
}

export function addOrgMember(
  nodeId: string,
  input: MemberInput,
): Promise<{ assignment: OrgAssignment }> {
  return send(`/nodes/${encodeURIComponent(nodeId)}/members`, 'POST', {
    subject_type: input.subject_type,
    subject_id: input.subject_id,
    subject_label: input.subject_label ?? '',
    title: input.title ?? '',
    is_primary: Boolean(input.is_primary),
    is_lead: Boolean(input.is_lead),
    reports_to_assignment_id: input.reports_to_assignment_id || null,
  })
}

export function patchOrgAssignment(
  assignmentId: string,
  patch: {
    title?: string
    is_primary?: boolean
    is_lead?: boolean
    reports_to_assignment_id?: string
    clear_reports_to?: boolean
  },
): Promise<{ assignment: OrgAssignment }> {
  return send(`/assignments/${encodeURIComponent(assignmentId)}`, 'PATCH', patch)
}

export function removeOrgAssignment(
  assignmentId: string,
  clearReports: boolean,
): Promise<{ reports_unbound: number }> {
  return send(
    `/assignments/${encodeURIComponent(assignmentId)}?clear_reports=${
      clearReports ? 'true' : 'false'
    }`,
    'DELETE',
  )
}

export function loadOrgVersions(limit = 50): Promise<OrgVersionsResponse> {
  return send<OrgVersionsResponse>(`/versions?limit=${limit}`, 'GET')
}

export function loadOrgVersion(versionId: string): Promise<OrgVersionDetail> {
  return send<OrgVersionDetail>(`/versions/${encodeURIComponent(versionId)}`, 'GET')
}

export function rollbackOrgVersion(versionId: string): Promise<{
  restored_nodes: number
  restored_assignments: number
  version: OrgVersion
}> {
  return send(`/versions/${encodeURIComponent(versionId)}/rollback`, 'POST', {})
}

export function checkOrgImpact(body: ImpactRequest): Promise<OrgImpact> {
  return send<OrgImpact>('/impact', 'POST', body)
}

// ── 错误 ────────────────────────────────────────────────────────

/** 把异常收敛成「可展示的一句话 + 稳定错误码」。 */
export function orgFailure(err: unknown): { message: string; code: string | null } {
  if (err instanceof ApiError) return { message: err.message, code: err.code }
  if (err instanceof Error) return { message: err.message, code: null }
  return { message: '操作没有完成，请重试。', code: null }
}

/** 同级重名的就地提示。只有服务端判定的 409 才走到这里。 */
export function duplicateNameHint(code: string | null): string | null {
  if (code === 'ORG_DUP_NAME') {
    return '同一上级下已有同名节点（大小写不敏感）。请换一个名字，或先给已有节点改名。'
  }
  return null
}

// ── 树 ──────────────────────────────────────────────────────────

export interface OrgRow {
  node: OrgTreeNode
  depth: number
  /** 从根到父节点的名称链，用于搜索结果里说清"在谁下面"。 */
  path: string
}

/** 深度优先展开成可键盘遍历的行序列（顺序 = 视觉顺序）。 */
export function flattenOrg(roots: OrgTreeNode[]): OrgRow[] {
  const rows: OrgRow[] = []
  const walk = (nodes: OrgTreeNode[], depth: number, trail: string[]) => {
    for (const node of nodes) {
      const path = trail.join(' / ')
      rows.push({ node, depth, path })
      walk(node.children ?? [], depth + 1, [...trail, node.name])
    }
  }
  walk(roots, 0, [])
  return rows
}

/** 节点自身 + 全部后代 id（拖拽与移动目标都要排除这些）。 */
export function descendantIds(node: OrgTreeNode): Set<string> {
  const ids = new Set<string>()
  const walk = (current: OrgTreeNode) => {
    ids.add(current.node_id)
    for (const child of current.children ?? []) walk(child)
  }
  walk(node)
  return ids
}

export interface MoveCandidate {
  id: string
  label: string
  depth: number
}

/**
 * 「可以移到谁下面」的候选：根 + 全部节点，排除自己与后代（否则必然成环）。
 * 标签带缩进，纯键盘用下拉框也能看出层级。
 */
export function moveCandidates(rows: OrgRow[], nodeId: string): MoveCandidate[] {
  const target = rows.find((row) => row.node.node_id === nodeId)
  // 节点已不存在时不提供任何让人误以为"可以随便挪"的候选，只留下顶层。
  if (!target) return [{ id: '', label: '顶层（没有上级）', depth: -1 }]
  const blocked = descendantIds(target.node)
  const options: MoveCandidate[] = [{ id: '', label: '顶层（没有上级）', depth: -1 }]
  for (const row of rows) {
    if (blocked.has(row.node.node_id)) continue
    options.push({
      id: row.node.node_id,
      label: `${'　'.repeat(row.depth)}${row.node.name}`,
      depth: row.depth,
    })
  }
  return options
}

// ── 影响面 ──────────────────────────────────────────────────────

const ORPHAN_REASONS: Record<string, string> = {
  lead_removed: '负责人被移除',
  node_has_no_lead: '节点没有负责人',
  moved_subtree_without_lead: '移动的子树里没有负责人',
  new_parent_has_no_lead: '新的上级没有负责人',
}

export function orphanReason(reason: string | undefined): string {
  if (!reason) return '失去负责人'
  return ORPHAN_REASONS[reason] ?? reason
}

/**
 * 任务接管检查的结论。
 *
 * `checked === false` 是服务端明确的「没查」，必须原样说「未检查」并给出原因；
 * 任何情况下都不能把它翻译成「无风险」——那是在拿"查不到"冒充"没问题"。
 */
export function taskWarning(warning: ImpactWarning | undefined): {
  state: 'checked' | 'unchecked'
  count: number | null
  text: string
} {
  if (!warning || warning.checked === false) {
    const reason = (warning?.reason ?? '').trim() || '任务库暂时读不到，本次没有检查。'
    return { state: 'unchecked', count: null, text: `未检查 —— ${reason}` }
  }
  const count = typeof warning.count === 'number' ? warning.count : 0
  return {
    state: 'checked',
    count,
    text: count === 0 ? '已检查：没有未结束的任务。' : `已检查：${count} 个任务仍在进行。`,
  }
}

/** 「组织内任职失效」的固定说明；与服务端 note 同时出现时以服务端为准。 */
export const AUTHORITY_NOTE =
  '这里只表示组织内的任职/负责人记录本身失效，不代表账号权限被收回。'

// ── 版本与回滚 ──────────────────────────────────────────────────

const ACTION_LABELS: Record<string, string> = {
  create_node: '新建节点',
  update_node: '修改节点',
  move_node: '移动节点',
  delete_node: '删除节点',
  set_lead: '设置负责人',
  promote_lead: '提升为负责人',
  clear_lead: '取消负责人',
  add_member: '添加成员',
  update_assignment: '调整任职',
  remove_member: '移除成员',
  rollback: '回滚',
}

export function versionActionLabel(action: string): string {
  return ACTION_LABELS[action] ?? '组织调整'
}

/** Unix 秒 → 本地时间；缺失时如实显示「—」。 */
export function formatOrgTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return '—'
  }
  try {
    return new Date(seconds * 1000).toLocaleString()
  } catch {
    return String(seconds)
  }
}

export interface RollbackDiff {
  addedNodes: Array<{ name: string; kind: string }>
  removedNodes: Array<{ name: string; kind: string }>
  renamedNodes: Array<{ before: string; after: string }>
  movedNodes: Array<{ name: string; from: string; to: string }>
  assignmentsAdded: number
  assignmentsRemoved: number
  assignmentsChanged: number
  identical: boolean
}

/**
 * 回滚会把整棵树替换成历史快照。这里逐项列出"会发生什么"：
 * 现在有、快照没有的会被删除；快照有、现在没有的会被恢复；同 id 的改名/换上级会被改回。
 */
export function diffSnapshots(
  snapshot: OrgSnapshot,
  currentNodes: OrgNode[],
  currentAssignments: OrgAssignment[],
): RollbackDiff {
  const snapshotNodes = Array.isArray(snapshot?.nodes) ? snapshot.nodes : []
  const snapshotAssignments = Array.isArray(snapshot?.assignments) ? snapshot.assignments : []

  const currentById = new Map(currentNodes.map((node) => [node.node_id, node]))
  const snapshotById = new Map(snapshotNodes.map((node) => [node.node_id, node]))
  const nameOfSnapshotParent = (parentId: string): string =>
    parentId ? (snapshotById.get(parentId)?.name ?? '（已不存在的上级）') : '顶层'
  const nameOfCurrentParent = (parentId: string | null): string => {
    if (!parentId) return '顶层'
    return currentById.get(parentId)?.name ?? '（已不存在的上级）'
  }

  const addedNodes: RollbackDiff['addedNodes'] = []
  for (const node of snapshotNodes) {
    if (!currentById.has(node.node_id)) addedNodes.push({ name: node.name, kind: node.kind })
  }
  const removedNodes: RollbackDiff['removedNodes'] = []
  for (const node of currentNodes) {
    if (!snapshotById.has(node.node_id)) removedNodes.push({ name: node.name, kind: node.kind })
  }
  const renamedNodes: RollbackDiff['renamedNodes'] = []
  const movedNodes: RollbackDiff['movedNodes'] = []
  for (const node of currentNodes) {
    const before = snapshotById.get(node.node_id)
    if (!before) continue
    if (before.name !== node.name) renamedNodes.push({ before: node.name, after: before.name })
    const currentParent = node.parent_id ?? ''
    const beforeParent = before.parent_id ?? ''
    if (currentParent !== beforeParent) {
      movedNodes.push({
        name: node.name,
        from: nameOfCurrentParent(node.parent_id),
        to: nameOfSnapshotParent(before.parent_id),
      })
    }
  }

  const currentAssignmentById = new Map(
    currentAssignments.map((item) => [item.assignment_id, item]),
  )
  const snapshotAssignmentIds = new Set(snapshotAssignments.map((item) => item.assignment_id))
  let assignmentsAdded = 0
  let assignmentsRemoved = 0
  let assignmentsChanged = 0
  for (const item of snapshotAssignments) {
    const now = currentAssignmentById.get(item.assignment_id)
    if (!now) {
      assignmentsAdded += 1
      continue
    }
    const changed =
      now.node_id !== item.node_id ||
      now.subject_id !== item.subject_id ||
      now.subject_type !== item.subject_type ||
      now.subject_label !== item.subject_label ||
      now.title !== item.title ||
      Number(now.is_lead) !== Number(item.is_lead) ||
      Number(now.is_primary) !== Number(item.is_primary) ||
      (now.reports_to_assignment_id ?? '') !== (item.reports_to_assignment_id ?? '')
    if (changed) assignmentsChanged += 1
  }
  for (const item of currentAssignments) {
    if (!snapshotAssignmentIds.has(item.assignment_id)) assignmentsRemoved += 1
  }

  return {
    addedNodes,
    removedNodes,
    renamedNodes,
    movedNodes,
    assignmentsAdded,
    assignmentsRemoved,
    assignmentsChanged,
    identical:
      addedNodes.length === 0 &&
      removedNodes.length === 0 &&
      renamedNodes.length === 0 &&
      movedNodes.length === 0 &&
      assignmentsAdded === 0 &&
      assignmentsRemoved === 0 &&
      assignmentsChanged === 0,
  }
}
