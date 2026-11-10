/**
 * 组织架构 —— 自建企业 / 事业部 / 分公司 / 部门 / 小组 / 岗位。
 *
 * 本轮只新增文件，不改公共路由：`ORG_PAGE` 就是接线路由时要用的唯一信息
 * （key / 路径 / 标题 / 副说明），接线时在 `nav.ts` 与 `App.tsx` 各加一行即可。
 *
 * 交互上的三条硬约束
 * ------------------
 * 1. **拖拽不是唯一路径**：节点行可拖到另一个节点下调整上级；键盘用户选中节点后，
 *    在右侧「移动」里用原生下拉框选择新上级，得到同样的结果。拖放本身也只打开
 *    确认面板，不直接落库。
 * 2. **破坏性操作先看影响**：删除、移动、移除成员都先请求只读预演，把
 *    「有人失去负责人 / 任务无人接管 / 组织内任职失效」摆出来，再让人确认。
 * 3. **回滚先说要发生什么**：选版本后先对比"当前结构 vs 该版快照"，逐项列出
 *    会恢复、会删除、会改名、会换上级的节点与任职变化，确认后才回滚。
 *
 * 诚实边界
 * --------
 * 「任务无人接管」查不到任务库时，服务端返回"未检查"；界面必须原样显示
 * 「未检查 —— 原因」，绝不写成"无风险"。「组织内任职失效」始终标注
 * "不代表账号权限被收回"（组织数据当前不参与权限判定）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'
import { useApi } from '../lib/api'
import type { EmployeesPayload } from '../lib/contracts'
import { employeesOf } from '../lib/employees'
import { Card, Empty, Failure, Loading } from '../components/ui'
import {
  AUTHORITY_NOTE,
  addOrgMember,
  checkOrgImpact,
  clearOrgLead,
  createOrgNode,
  deleteOrgNode,
  diffSnapshots,
  duplicateNameHint,
  flattenOrg,
  formatOrgTime,
  kindLabel,
  loadOrgVersion,
  moveCandidates,
  moveOrgNode,
  orgFailure,
  orphanReason,
  patchOrgAssignment,
  patchOrgNode,
  removeOrgAssignment,
  rollbackOrgVersion,
  setOrgLead,
  taskWarning,
  versionActionLabel,
} from '../lib/org'
import type {
  MoveCandidate,
  OrgAssignment,
  OrgImpact,
  OrgNode,
  OrgRow,
  OrgSnapshot,
  OrgTreeResponse,
  OrgTreeNode,
  OrgVersion,
  OrgVersionDetail,
} from '../lib/org'
import './Org.css'

/** 接线路由时用的唯一信息。改导航只需引用这里，不用再抄一份文案。 */
export const ORG_PAGE = {
  key: 'org',
  path: '?p=org',
  title: '组织架构',
  sub: '自建企业、事业部、分公司、部门、小组与岗位；挂 AI 员工、设负责人、调层级与汇报关系',
} as const

const TREE_PATH = '/v1/org/tree'
const EMPLOYEES_PATH = '/v1/employees'

type Panel =
  | { type: 'create'; parentId: string | null }
  | { type: 'rename' }
  | { type: 'move'; initialParentId: string }
  | { type: 'delete' }
  | { type: 'lead' }
  | { type: 'member' }

interface FailureText {
  message: string
  code: string | null
}

/** 表单内的统一提交器：按钮忙碌态、错误就地显示、成功后刷新树。 */
function useOrgMutation(onSuccess: (message: string) => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<FailureText | null>(null)

  const run = useCallback(
    async (task: () => Promise<unknown>, successMessage: string): Promise<boolean> => {
      setBusy(true)
      setError(null)
      try {
        await task()
        onSuccess(successMessage)
        return true
      } catch (err) {
        setError(orgFailure(err))
        return false
      } finally {
        setBusy(false)
      }
    },
    [onSuccess],
  )

  return { busy, error, setError, run }
}

/** 提交按钮：忙碌时禁用并换文案，错误就地播报。 */
function SubmitRow({
  busy,
  label,
  busyLabel = '正在写入…',
  danger,
  disabled,
  onCancel,
  cancelLabel = '取消',
}: {
  busy: boolean
  label: string
  busyLabel?: string
  danger?: boolean
  disabled?: boolean
  onCancel?: () => void
  cancelLabel?: string
}) {
  return (
    <div className="org-form-actions">
      <button type="submit" className={danger ? 'os-btn org-btn-danger' : 'os-btn'} disabled={busy || disabled}>
        {busy ? busyLabel : label}
      </button>
      {onCancel && (
        <button type="button" className="os-btn os-btn-ghost" onClick={onCancel} disabled={busy}>
          {cancelLabel}
        </button>
      )}
    </div>
  )
}

/** 就地错误：同一份文案同时供读屏与视觉使用。 */
function FieldError({ error, hint }: { error: FailureText | null; hint?: string | null }) {
  if (!error && !hint) return null
  return (
    <p className="org-field-error" role="alert">
      {error?.message ?? hint}
    </p>
  )
}

function KindBadge({ kind }: { kind: string }) {
  return <span className="org-kind">{kindLabel(kind)}</span>
}

// ── 影响面报告 ──────────────────────────────────────────────────

const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '待执行',
  queued: '排队中',
  running: '进行中',
  in_progress: '进行中',
  assigned: '已分派',
  blocked: '等待条件',
}

function taskStatusLabel(status: string | undefined): string {
  const key = String(status ?? '').toLowerCase()
  return TASK_STATUS_LABELS[key] ?? '进行中'
}

/**
 * 把一次只读预演如实摆出来。
 *
 * 三块告警都必须出现（哪怕是 0），因为"没显示"会被读成"没问题"。
 */
function ImpactReport({ impact }: { impact: OrgImpact }) {
  const orphan = impact.warnings.orphaned_lead
  const tasks = taskWarning(impact.warnings.unattended_tasks)
  const authority = impact.warnings.revoked_authority
  const taskItems = impact.warnings.unattended_tasks?.items ?? []

  return (
    <section className="org-impact" aria-label="影响检查结果" aria-live="polite">
      <p className={impact.allowed ? 'org-impact-head' : 'org-impact-head org-impact-blocked'}>
        {impact.allowed ? '检查结论：可以执行。' : '检查结论：当前不能执行。'}
        <span className="org-impact-note">{impact.note}</span>
      </p>

      {impact.blockers.length > 0 && (
        <ul className="org-blockers" role="alert">
          {impact.blockers.map((blocker) => (
            <li key={blocker.code}>{blocker.message}</li>
          ))}
        </ul>
      )}

      <dl className="org-impact-list">
        <dt>有人失去负责人</dt>
        <dd>
          {orphan && orphan.count
            ? `${orphan.count} 处：${(orphan.items ?? [])
                .map((item) => `${item.node_name || item.node_id}（${orphanReason(item.reason)}）`)
                .join('、')}`
            : '没有。'}
        </dd>

        <dt>任务无人接管</dt>
        <dd className={tasks.state === 'unchecked' ? 'org-impact-unknown' : undefined}>
          <span>{tasks.text}</span>
          {taskItems.length > 0 && (
            <ul className="org-impact-items">
              {taskItems.slice(0, 8).map((item) => (
                <li key={`${item.goal_id}-${item.task_id}`}>
                  {item.employee || '成员'} · {taskStatusLabel(item.task_status)}
                  {item.goal ? ` · ${item.goal}` : ''}
                </li>
              ))}
              {taskItems.length > 8 && <li>还有 {taskItems.length - 8} 条未列出。</li>}
            </ul>
          )}
        </dd>

        <dt>组织内任职失效</dt>
        <dd>
          {authority && authority.count ? `${authority.count} 条任职/负责人记录失效。` : '没有。'}
          <span className="org-impact-note">{authority?.note || AUTHORITY_NOTE}</span>
          <span className="org-impact-note">权限不受影响：这里不代表账号权限被收回。</span>
        </dd>
      </dl>
    </section>
  )
}

// ── 新建 / 改名 ─────────────────────────────────────────────────

function CreateNodeForm({
  parent,
  onDone,
  onCancel,
}: {
  parent: OrgTreeNode | null
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState('department')
  const [description, setDescription] = useState('')
  const { busy, error, run } = useOrgMutation(onDone)
  const nameRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    nameRef.current?.focus()
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    void run(
      () =>
        createOrgNode({
          name: trimmed,
          kind,
          parent_id: parent?.node_id ?? null,
          description: description.trim(),
        }),
      parent ? `已在「${parent.name}」下新建节点。` : '已新建顶层节点。',
    )
  }

  return (
    <form className="org-form" onSubmit={submit} aria-label={parent ? '新建下级节点' : '新建顶层节点'}>
      <p className="org-form-scope">
        {parent ? `上级：${parent.name}（${kindLabel(parent.kind)}）` : '上级：顶层（没有上级）'}
      </p>
      <div className="os-field">
        <label htmlFor="org-new-name">节点名称</label>
        <input
          id="org-new-name"
          ref={nameRef}
          value={name}
          maxLength={200}
          placeholder="例如：华东分公司"
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <div className="os-field">
        <label htmlFor="org-new-kind">节点类型</label>
        <select id="org-new-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="company">企业</option>
          <option value="business_unit">事业部</option>
          <option value="branch">分公司</option>
          <option value="department">部门</option>
          <option value="team">小组</option>
          <option value="position">岗位</option>
        </select>
      </div>
      <div className="os-field">
        <label htmlFor="org-new-desc">说明（可选）</label>
        <input
          id="org-new-desc"
          value={description}
          maxLength={2000}
          placeholder="这个节点负责什么"
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      <FieldError error={error} hint={duplicateNameHint(error?.code ?? null)} />
      <SubmitRow
        busy={busy}
        label="新建"
        busyLabel="正在新建…"
        disabled={!name.trim()}
        onCancel={onCancel}
      />
    </form>
  )
}

function RenameForm({
  node,
  onDone,
  onCancel,
}: {
  node: OrgTreeNode
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(node.name)
  const { busy, error, run } = useOrgMutation(onDone)
  const nameRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    nameRef.current?.focus()
    nameRef.current?.select()
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed || trimmed === node.name) return
    void run(() => patchOrgNode(node.node_id, { name: trimmed }), `已改名为「${trimmed}」。`)
  }

  return (
    <form className="org-form" onSubmit={submit} aria-label="重命名节点">
      <div className="os-field">
        <label htmlFor="org-rename">新的名称</label>
        <input
          id="org-rename"
          ref={nameRef}
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
        />
      </div>
      <FieldError error={error} hint={duplicateNameHint(error?.code ?? null)} />
      <SubmitRow
        busy={busy}
        label="保存名称"
        disabled={!name.trim() || name.trim() === node.name}
        onCancel={onCancel}
      />
    </form>
  )
}

// ── 移动 ────────────────────────────────────────────────────────

function MoveForm({
  node,
  rows,
  initialParentId,
  onDone,
  onCancel,
}: {
  node: OrgTreeNode
  rows: OrgRow[]
  initialParentId: string
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const candidates = useMemo(() => moveCandidates(rows, node.node_id), [rows, node.node_id])
  const [parentId, setParentId] = useState(initialParentId)
  const [impact, setImpact] = useState<OrgImpact | null>(null)
  const [checking, setChecking] = useState(false)
  const { busy, error, setError, run } = useOrgMutation(onDone)

  // 换了目标就作废上一次检查：绝不让"检查过 A、执行 B"同时成立。
  useEffect(() => {
    setImpact(null)
  }, [parentId])

  const check = async () => {
    setChecking(true)
    setError(null)
    try {
      const result = await checkOrgImpact({
        action: 'move_node',
        node_id: node.node_id,
        target_parent_id: parentId || undefined,
      })
      setImpact(result)
    } catch (err) {
      setError(orgFailure(err))
    } finally {
      setChecking(false)
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!impact || !impact.allowed) return
    void run(
      () => moveOrgNode(node.node_id, parentId || null),
      `已把「${node.name}」移到新的上级下。`,
    )
  }

  return (
    <form className="org-form" onSubmit={submit} aria-label="移动节点">
      <div className="os-field">
        <label htmlFor="org-move-parent">新的上级</label>
        <select
          id="org-move-parent"
          value={parentId}
          onChange={(event) => setParentId(event.target.value)}
        >
          {candidates.map((candidate: MoveCandidate) => (
            <option key={candidate.id || 'root'} value={candidate.id}>
              {candidate.label}
            </option>
          ))}
        </select>
      </div>
      <p className="os-hint">
        只能移到不属于自己下级的节点下；把自己或自己的下级当作上级会被拒绝。
      </p>
      <FieldError error={error} />
      {impact && <ImpactReport impact={impact} />}
      <div className="org-form-actions">
        <button type="button" className="os-btn os-btn-ghost" onClick={() => void check()} disabled={busy || checking}>
          {checking ? '正在检查…' : impact ? '重新检查影响' : '先检查影响'}
        </button>
        <button
          type="submit"
          className="os-btn"
          disabled={busy || checking || !impact || !impact.allowed}
        >
          {busy ? '正在移动…' : '确认移动'}
        </button>
        <button type="button" className="os-btn os-btn-ghost" onClick={onCancel} disabled={busy}>
          取消
        </button>
      </div>
      {!impact && <p className="os-hint">移动前必须先检查一次影响；没有通过检查不会写入。</p>}
    </form>
  )
}

// ── 删除 ────────────────────────────────────────────────────────

function DeleteNodeForm({
  node,
  onDone,
  onCancel,
}: {
  node: OrgTreeNode
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const [cascade, setCascade] = useState(false)
  const [impact, setImpact] = useState<OrgImpact | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState<FailureText | null>(null)
  const { busy, error, run } = useOrgMutation(onDone)

  const check = useCallback(
    async (nextCascade: boolean) => {
      setChecking(true)
      setCheckError(null)
      try {
        const result = await checkOrgImpact({
          action: 'delete_node',
          node_id: node.node_id,
          cascade: nextCascade,
        })
        setImpact(result)
      } catch (err) {
        setImpact(null)
        setCheckError(orgFailure(err))
      } finally {
        setChecking(false)
      }
    },
    [node.node_id],
  )

  useEffect(() => {
    void check(false)
  }, [check])

  const hasChildrenBlocker = Boolean(
    impact?.blockers.some((blocker) => blocker.code === 'ORG_NODE_HAS_CHILDREN'),
  )
  const allowed = Boolean(impact?.allowed)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!allowed) return
    void run(
      () => deleteOrgNode(node.node_id, cascade),
      cascade ? `已连同下级一起删除「${node.name}」。` : `已删除「${node.name}」。`,
    )
  }

  return (
    <form className="org-form" onSubmit={submit} aria-label="删除节点">
      <p className="org-form-scope">
        即将删除：{node.name}（{kindLabel(node.kind)}）。这一步会写入一条删除记录；
        需要时可以用版本历史回滚。
      </p>
      {checking && <Loading rows={2} />}
      <FieldError error={checkError} />
      {impact && <ImpactReport impact={impact} />}
      {hasChildrenBlocker && (
        <label className="org-check">
          <input
            type="checkbox"
            checked={cascade}
            onChange={(event) => {
              const next = event.target.checked
              setCascade(next)
              void check(next)
            }}
          />
          连同其下的全部子节点一起删除（会一并移除这些节点上的任职记录）
        </label>
      )}
      <FieldError error={error} />
      <SubmitRow
        busy={busy}
        danger
        label={cascade ? '确认删除（含子节点）' : '确认删除'}
        busyLabel="正在删除…"
        disabled={!allowed || checking}
        onCancel={onCancel}
      />
    </form>
  )
}

// ── 负责人 / 成员 ───────────────────────────────────────────────

function SubjectPicker({
  idPrefix,
  subjectType,
  subjectId,
  onTypeChange,
  onIdChange,
  employees,
}: {
  idPrefix: string
  subjectType: string
  subjectId: string
  onTypeChange: (value: string) => void
  onIdChange: (value: string) => void
  employees: Array<{ name: string; role: string }>
}) {
  return (
    <>
      <div className="os-field">
        <label htmlFor={`${idPrefix}-type`}>成员类型</label>
        <select
          id={`${idPrefix}-type`}
          value={subjectType}
          onChange={(event) => {
            onTypeChange(event.target.value)
            onIdChange('')
          }}
        >
          <option value="employee">AI 员工</option>
          <option value="human">账号（人）</option>
        </select>
      </div>
      <div className="os-field">
        <label htmlFor={`${idPrefix}-subject`}>
          {subjectType === 'employee' ? '选择 AI 员工' : '账号名'}
        </label>
        {subjectType === 'employee' && employees.length > 0 ? (
          <select
            id={`${idPrefix}-subject`}
            value={subjectId}
            onChange={(event) => onIdChange(event.target.value)}
          >
            <option value="">请选择</option>
            {employees.map((employee) => (
              <option key={employee.name} value={employee.name}>
                {employee.role ? `${employee.name}（${employee.role}）` : employee.name}
              </option>
            ))}
          </select>
        ) : (
          <input
            id={`${idPrefix}-subject`}
            value={subjectId}
            placeholder={subjectType === 'employee' ? '输入员工库里已有的名字' : '输入账号名'}
            onChange={(event) => onIdChange(event.target.value)}
          />
        )}
      </div>
    </>
  )
}

function LeadForm({
  node,
  employees,
  onDone,
  onCancel,
}: {
  node: OrgTreeNode
  employees: Array<{ name: string; role: string }>
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const [subjectType, setSubjectType] = useState('employee')
  const [subjectId, setSubjectId] = useState('')
  const [title, setTitle] = useState('')
  const { busy, error, run } = useOrgMutation(onDone)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!subjectId.trim()) return
    void run(
      () =>
        setOrgLead(node.node_id, {
          subject_type: subjectType,
          subject_id: subjectId.trim(),
          title: title.trim(),
        }),
      `已设置「${node.name}」的负责人。`,
    )
  }

  return (
    <form className="org-form" onSubmit={submit} aria-label="设置负责人">
      <p className="org-form-scope">
        {node.lead
          ? `当前负责人：${node.lead.subject_label || node.lead.subject_id}。保存后会替换。`
          : '这个节点还没有负责人。'}
      </p>
      <SubjectPicker
        idPrefix="org-lead"
        subjectType={subjectType}
        subjectId={subjectId}
        onTypeChange={setSubjectType}
        onIdChange={setSubjectId}
        employees={employees}
      />
      <div className="os-field">
        <label htmlFor="org-lead-title">岗位称谓（可选）</label>
        <input
          id="org-lead-title"
          value={title}
          maxLength={120}
          placeholder="例如：研发负责人"
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <FieldError error={error} />
      <SubmitRow
        busy={busy}
        label="保存负责人"
        disabled={!subjectId.trim()}
        onCancel={onCancel}
      />
    </form>
  )
}

function MemberForm({
  node,
  employees,
  assignments,
  onDone,
  onCancel,
}: {
  node: OrgTreeNode
  employees: Array<{ name: string; role: string }>
  assignments: OrgAssignment[]
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const [subjectType, setSubjectType] = useState('employee')
  const [subjectId, setSubjectId] = useState('')
  const [title, setTitle] = useState('')
  const [isPrimary, setIsPrimary] = useState(false)
  const [reportsTo, setReportsTo] = useState('')
  const { busy, error, run } = useOrgMutation(onDone)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!subjectId.trim()) return
    void run(
      () =>
        addOrgMember(node.node_id, {
          subject_type: subjectType,
          subject_id: subjectId.trim(),
          title: title.trim(),
          is_primary: isPrimary,
          reports_to_assignment_id: reportsTo || null,
        }),
      `已把 ${subjectId.trim()} 加到「${node.name}」。`,
    )
  }

  return (
    <form className="org-form" onSubmit={submit} aria-label="添加成员">
      <p className="org-form-scope">添加到：{node.name}</p>
      <SubjectPicker
        idPrefix="org-member"
        subjectType={subjectType}
        subjectId={subjectId}
        onTypeChange={setSubjectType}
        onIdChange={setSubjectId}
        employees={employees}
      />
      <div className="os-field">
        <label htmlFor="org-member-title">岗位称谓（可选）</label>
        <input
          id="org-member-title"
          value={title}
          maxLength={120}
          placeholder="例如：产品经理"
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="os-field">
        <label htmlFor="org-member-reports">汇报对象（可选）</label>
        <select
          id="org-member-reports"
          value={reportsTo}
          onChange={(event) => setReportsTo(event.target.value)}
        >
          <option value="">不指定</option>
          {assignments.map((item) => (
            <option key={item.assignment_id} value={item.assignment_id}>
              {item.subject_label || item.subject_id}
              {item.title ? ` · ${item.title}` : ''}
            </option>
          ))}
        </select>
      </div>
      <label className="org-check">
        <input
          type="checkbox"
          checked={isPrimary}
          onChange={(event) => setIsPrimary(event.target.checked)}
        />
        设为主岗（同一人可以有多条任职，主岗只有这一个）
      </label>
      <FieldError error={error} />
      <SubmitRow
        busy={busy}
        label="添加成员"
        disabled={!subjectId.trim()}
        onCancel={onCancel}
      />
    </form>
  )
}

/** 移除成员：先预演，有下级汇报时必须显式勾选才能继续。 */
function RemoveMemberForm({
  assignment,
  onDone,
  onCancel,
}: {
  assignment: OrgAssignment
  onDone: (message: string) => void
  onCancel: () => void
}) {
  const [impact, setImpact] = useState<OrgImpact | null>(null)
  const [checking, setChecking] = useState(false)
  const [clearReports, setClearReports] = useState(false)
  const [checkError, setCheckError] = useState<FailureText | null>(null)
  const { busy, error, run } = useOrgMutation(onDone)

  const check = useCallback(
    async (nextClear: boolean) => {
      setChecking(true)
      setCheckError(null)
      try {
        setImpact(
          await checkOrgImpact({
            action: 'remove_assignment',
            assignment_id: assignment.assignment_id,
            clear_reports: nextClear,
          }),
        )
      } catch (err) {
        setImpact(null)
        setCheckError(orgFailure(err))
      } finally {
        setChecking(false)
      }
    },
    [assignment.assignment_id],
  )

  useEffect(() => {
    void check(false)
  }, [check])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!impact?.allowed) return
    void run(
      () => removeOrgAssignment(assignment.assignment_id, clearReports),
      `已移除 ${assignment.subject_label || assignment.subject_id}。`,
    )
  }

  const hasReportsBlocker = Boolean(
    impact?.blockers.some((blocker) => blocker.code === 'ORG_ASSIGNMENT_HAS_REPORTS'),
  )

  return (
    <form className="org-form" onSubmit={submit} aria-label="移除成员">
      <p className="org-form-scope">
        即将移除：{assignment.subject_label || assignment.subject_id}
      </p>
      {checking && <Loading rows={2} />}
      <FieldError error={checkError} />
      {impact && <ImpactReport impact={impact} />}
      {hasReportsBlocker && (
        <label className="org-check">
          <input
            type="checkbox"
            checked={clearReports}
            onChange={(event) => {
              const next = event.target.checked
              setClearReports(next)
              void check(next)
            }}
          />
          一并解除其下级的汇报关系
        </label>
      )}
      <FieldError error={error} />
      <SubmitRow
        busy={busy}
        danger
        label="确认移除"
        busyLabel="正在移除…"
        disabled={checking || !impact?.allowed}
        onCancel={onCancel}
      />
    </form>
  )
}

// ── 版本历史 ────────────────────────────────────────────────────

function DiffList({ diff }: { diff: ReturnType<typeof diffSnapshots> }) {
  if (diff.identical) {
    return <p className="os-hint">当前结构与这一版一致，回滚不会改变任何东西。</p>
  }
  return (
    <ul className="org-diff">
      {diff.addedNodes.length > 0 && (
        <li>
          会恢复 {diff.addedNodes.length} 个节点：
          {diff.addedNodes.map((node) => node.name).join('、')}
        </li>
      )}
      {diff.removedNodes.length > 0 && (
        <li>
          会删除 {diff.removedNodes.length} 个节点：
          {diff.removedNodes.map((node) => node.name).join('、')}
        </li>
      )}
      {diff.renamedNodes.length > 0 && (
        <li>
          会改回名字：
          {diff.renamedNodes.map((item) => `「${item.before}」→「${item.after}」`).join('、')}
        </li>
      )}
      {diff.movedNodes.length > 0 && (
        <li>
          会调整上级：
          {diff.movedNodes.map((item) => `${item.name}（${item.from} → ${item.to}）`).join('、')}
        </li>
      )}
      {(diff.assignmentsAdded > 0 ||
        diff.assignmentsRemoved > 0 ||
        diff.assignmentsChanged > 0) && (
        <li>
          任职记录：恢复 {diff.assignmentsAdded} 条 / 移除 {diff.assignmentsRemoved} 条 / 变更{' '}
          {diff.assignmentsChanged} 条
        </li>
      )}
    </ul>
  )
}

function VersionPanel({
  currentNodes,
  currentAssignments,
  latestVersionId,
  onApplied,
}: {
  currentNodes: OrgNode[]
  currentAssignments: OrgAssignment[]
  latestVersionId: string | null
  onApplied: (message: string) => void
}) {
  const versions = useApi<{ versions: OrgVersion[]; total: number }>(
    '/v1/org/versions?limit=50',
    0,
  )
  const [selected, setSelected] = useState<OrgVersionDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [detailError, setDetailError] = useState<FailureText | null>(null)
  const { busy, error, run } = useOrgMutation(onApplied)
  const confirmRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (latestVersionId) versions.reload()
  }, [latestVersionId, versions.reload])

  const diff = useMemo(() => {
    if (!selected) return null
    return diffSnapshots(selected.snapshot as OrgSnapshot, currentNodes, currentAssignments)
  }, [selected, currentNodes, currentAssignments])

  const openVersion = async (version: OrgVersion) => {
    setLoadingDetail(true)
    setDetailError(null)
    setSelected(null)
    try {
      setSelected(await loadOrgVersion(version.version_id))
    } catch (err) {
      setDetailError(orgFailure(err))
    } finally {
      setLoadingDetail(false)
    }
  }

  useEffect(() => {
    if (selected) confirmRef.current?.focus()
  }, [selected])

  const doRollback = () => {
    if (!selected) return
    void run(
      () => rollbackOrgVersion(selected.version_id),
      `已回滚到第 ${selected.seq} 版，结构已按当时的快照重建。`,
    )
  }

  return (
    <div className="org-versions">
      {versions.loading && <Loading rows={3} />}
      {versions.error && (
        <Failure message={versions.error} onRetry={versions.reload} />
      )}
      {!versions.loading && !versions.error && (versions.data?.versions ?? []).length === 0 && (
        <Empty
          title="还没有历史版本"
          hint="每次新建、改名、移动、删除或调整任职都会留下一个可回滚的版本。"
        />
      )}
      <div className="org-version-list" role="list" aria-label="历史版本">
        {(versions.data?.versions ?? []).map((version) => (
          <div
            key={version.version_id}
            role="listitem"
            className={
              selected?.version_id === version.version_id
                ? 'org-version org-version-active'
                : 'org-version'
            }
          >
            <div className="org-version-main">
              <strong>第 {version.seq} 版</strong>
              <span>{versionActionLabel(version.action)}</span>
              <span className="org-version-time">{formatOrgTime(version.created_at)}</span>
            </div>
            <button
              type="button"
              className="os-link"
              onClick={() => void openVersion(version)}
            >
              查看回滚影响
            </button>
          </div>
        ))}
      </div>

      {loadingDetail && <Loading rows={2} />}
      <FieldError error={detailError} />

      {selected && diff && (
        <section className="org-rollback" aria-label="回滚预览">
          <h3 className="org-section-title">回滚到第 {selected.seq} 版会发生什么</h3>
          <p className="os-hint">
            回滚会用这一版的结构替换当前整棵组织树，并追加一条回滚记录；历史版本本身不会被删除。
          </p>
          <DiffList diff={diff} />
          <FieldError error={error} />
          <div className="org-form-actions">
            <button
              type="button"
              ref={confirmRef}
              className="os-btn org-btn-danger"
              disabled={busy || diff.identical}
              onClick={doRollback}
            >
              {busy ? '正在回滚…' : '确认回滚'}
            </button>
            <button type="button" className="os-btn os-btn-ghost" onClick={() => setSelected(null)}>
              取消
            </button>
          </div>
        </section>
      )}
    </div>
  )
}

// ── 成员行 ──────────────────────────────────────────────────────

function MemberRow({
  node,
  assignment,
  allAssignments,
  onChanged,
  onFail,
}: {
  node: OrgTreeNode
  assignment: OrgAssignment
  allAssignments: OrgAssignment[]
  onChanged: (message: string) => void
  onFail: (error: FailureText) => void
}) {
  const [removing, setRemoving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<FailureText | null>(null)
  const reportsTo = allAssignments.find(
    (item) => item.assignment_id === assignment.reports_to_assignment_id,
  )
  const reportsOptions = allAssignments.filter(
    (item) => item.assignment_id !== assignment.assignment_id,
  )
  const roleLabel = assignment.is_lead ? '负责人' : assignment.is_primary ? '主岗' : '成员'

  const toggleLead = async () => {
    setBusy(true)
    setLocalError(null)
    try {
      if (assignment.is_lead) {
        await clearOrgLead(node.node_id, false)
        onChanged(`已取消「${node.name}」的负责人。`)
      } else {
        await patchOrgAssignment(assignment.assignment_id, { is_lead: true })
        onChanged(`已把 ${assignment.subject_label || assignment.subject_id} 设为负责人。`)
      }
    } catch (err) {
      const failure = orgFailure(err)
      if (failure.code === 'ORG_ASSIGNMENT_HAS_REPORTS') {
        setLocalError({
          message:
            '这位负责人还有下级汇报关系，直接取消会留下无上级的汇报。请先在「移除」里确认一并解除，或先把下级改到别人名下。',
          code: failure.code,
        })
      } else {
        onFail(failure)
      }
    } finally {
      setBusy(false)
    }
  }

  const changeReports = async (value: string) => {
    setBusy(true)
    setLocalError(null)
    try {
      if (!value) {
        await patchOrgAssignment(assignment.assignment_id, { clear_reports_to: true })
      } else {
        await patchOrgAssignment(assignment.assignment_id, { reports_to_assignment_id: value })
      }
      onChanged('汇报关系已更新。')
    } catch (err) {
      const failure = orgFailure(err)
      if (failure.code === 'ORG_REPORT_CYCLE') {
        setLocalError({
          message: '这样会形成循环汇报：对方的汇报链上已经包含这个人。',
          code: failure.code,
        })
      } else {
        onFail(failure)
      }
    } finally {
      setBusy(false)
    }
  }

  const reportsToLabel = assignment.reports_to_assignment_id
    ? reportsTo
      ? `${reportsTo.subject_label || reportsTo.subject_id}${reportsTo.title ? ` · ${reportsTo.title}` : ''}`
      : '指向一条已不存在的任职记录'
    : '未指定'

  return (
    <div className="org-member">
      <div className="org-member-head">
        <strong>{assignment.subject_label || assignment.subject_id}</strong>
        <span className="os-badge" data-tone={assignment.is_lead ? 'accent' : 'muted'}>
          {roleLabel}
        </span>
        {assignment.title && <span className="org-member-title">{assignment.title}</span>}
      </div>
      <p className="org-member-sub">汇报对象：{reportsToLabel}</p>
      <div className="org-member-actions">
        <button type="button" className="os-link" onClick={() => void toggleLead()} disabled={busy}>
          {assignment.is_lead ? '取消负责人' : '设为负责人'}
        </button>
        <label className="org-inline-select">
          汇报对象
          <select
            value={assignment.reports_to_assignment_id ?? ''}
            disabled={busy}
            onChange={(event) => void changeReports(event.target.value)}
          >
            <option value="">未指定</option>
            {reportsOptions.map((item) => (
              <option key={item.assignment_id} value={item.assignment_id}>
                {(item.subject_label || item.subject_id) + (item.title ? ` · ${item.title}` : '')}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="os-link" onClick={() => setRemoving((prev) => !prev)}>
          {removing ? '收起移除' : '移除'}
        </button>
      </div>
      <FieldError error={localError} />
      {removing && (
        <RemoveMemberForm
          assignment={assignment}
          onDone={(message) => {
            setRemoving(false)
            onChanged(message)
          }}
          onCancel={() => setRemoving(false)}
        />
      )}
    </div>
  )
}

// ── 页面 ────────────────────────────────────────────────────────

export function Org({ query }: { query: string }) {
  const tree = useApi<OrgTreeResponse>(TREE_PATH, 30_000)
  const employeesApi = useApi<EmployeesPayload>(EMPLOYEES_PATH, 0)
  const refresh = tree.reload

  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [panel, setPanel] = useState<Panel | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [pageError, setPageError] = useState<FailureText | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const [showVersions, setShowVersions] = useState(false)
  const nodeRefs = useRef<Map<string, HTMLButtonElement | null>>(new Map())

  const roots = tree.data?.roots ?? []
  const rows = useMemo(() => flattenOrg(roots), [roots])
  const rowById = useMemo(() => new Map(rows.map((row) => [row.node.node_id, row])), [rows])
  const allAssignments = useMemo(
    () => rows.flatMap((row) => row.node.assignments),
    [rows],
  )
  const nodeById = useMemo(
    () => new Map<string, OrgTreeNode>(rows.map((row) => [row.node.node_id, row.node])),
    [rows],
  )
  const employees = useMemo(
    () =>
      employeesOf(employeesApi.data).map((employee) => ({
        name: employee.name,
        role: employee.role_description ?? '',
      })),
    [employeesApi.data],
  )

  const selected = selectedId ? (nodeById.get(selectedId) ?? null) : null

  // 首次拿到数据后把键盘焦点落到第一行，避免"树在那儿但 Tab 进不去"。
  useEffect(() => {
    if (focusId && nodeById.has(focusId)) return
    const first = rows[0]?.node.node_id ?? null
    setFocusId(first)
    if (!selectedId && first) setSelectedId(first)
  }, [rows, focusId, nodeById, selectedId])

  // 选中节点被删掉后，回到第一行，而不是让右侧停在一个不存在的节点上。
  useEffect(() => {
    if (selectedId && tree.data && !nodeById.has(selectedId)) {
      setSelectedId(rows[0]?.node.node_id ?? null)
    }
  }, [nodeById, rows, selectedId, tree.data])

  const visibleRows = useMemo(() => {
    const out: OrgRow[] = []
    const walk = (nodes: OrgTreeNode[], depth: number, trail: string[]) => {
      for (const node of nodes) {
        out.push({ node, depth, path: trail.join(' / ') })
        if (!collapsed.has(node.node_id)) {
          walk(node.children ?? [], depth + 1, [...trail, node.name])
        }
      }
    }
    walk(roots, 0, [])
    return out
  }, [roots, collapsed])

  const rovingId = focusId && nodeById.has(focusId) ? focusId : (visibleRows[0]?.node.node_id ?? null)

  const toggleCollapsed = (nodeId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(nodeId)) next.delete(nodeId)
      else next.add(nodeId)
      return next
    })
  }

  const focusRow = (nodeId: string | undefined) => {
    if (!nodeId) return
    setFocusId(nodeId)
    nodeRefs.current.get(nodeId)?.focus()
  }

  const onTreeKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const index = visibleRows.findIndex((row) => row.node.node_id === rovingId)
    if (index < 0) return
    const row = visibleRows[index]
    const id = row.node.node_id
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault()
        focusRow(visibleRows[Math.min(index + 1, visibleRows.length - 1)]?.node.node_id)
        break
      case 'ArrowUp':
        event.preventDefault()
        focusRow(visibleRows[Math.max(index - 1, 0)]?.node.node_id)
        break
      case 'Home':
        event.preventDefault()
        focusRow(visibleRows[0]?.node.node_id)
        break
      case 'End':
        event.preventDefault()
        focusRow(visibleRows[visibleRows.length - 1]?.node.node_id)
        break
      case 'ArrowRight':
        event.preventDefault()
        if ((row.node.children?.length ?? 0) > 0) {
          if (collapsed.has(id)) toggleCollapsed(id)
          else focusRow(row.node.children[0]?.node_id)
        }
        break
      case 'ArrowLeft':
        event.preventDefault()
        if ((row.node.children?.length ?? 0) > 0 && !collapsed.has(id)) {
          toggleCollapsed(id)
        } else if (row.node.parent_id) {
          focusRow(row.node.parent_id)
        }
        break
      default:
        break
    }
  }

  const canDropOn = (targetId: string): boolean => {
    if (!dragging || dragging === targetId) return false
    const source = nodeById.get(dragging)
    if (!source) return false
    let cursor: OrgTreeNode | undefined = nodeById.get(targetId)
    while (cursor) {
      if (cursor.node_id === dragging) return false
      cursor = cursor.parent_id ? nodeById.get(cursor.parent_id) : undefined
    }
    return true
  }

  const openMove = (targetParentId: string) => {
    if (!dragging) return
    setSelectedId(dragging)
    setPanel({ type: 'move', initialParentId: targetParentId })
    setDragging(null)
    setDropTarget(null)
  }

  const renderNode = (node: OrgTreeNode, level: number): ReactNode => {
    const hasChildren = (node.children?.length ?? 0) > 0
    const expanded = !collapsed.has(node.node_id)
    const isSelected = selectedId === node.node_id
    return (
      <div
        key={node.node_id}
        role="treeitem"
        aria-level={level}
        aria-expanded={hasChildren ? expanded : undefined}
        aria-selected={isSelected}
        className={
          dropTarget === node.node_id
            ? 'org-treeitem org-treeitem-drop'
            : isSelected
              ? 'org-treeitem org-treeitem-selected'
              : 'org-treeitem'
        }
        data-node-id={node.node_id}
        draggable
        onDragStart={(event) => {
          event.dataTransfer.setData('text/plain', node.node_id)
          event.dataTransfer.effectAllowed = 'move'
          setDragging(node.node_id)
        }}
        onDragEnd={() => {
          setDragging(null)
          setDropTarget(null)
        }}
        onDragOver={(event) => {
          if (!canDropOn(node.node_id)) return
          event.preventDefault()
          event.dataTransfer.dropEffect = 'move'
          setDropTarget(node.node_id)
        }}
        onDrop={(event) => {
          if (!canDropOn(node.node_id)) return
          event.preventDefault()
          openMove(node.node_id)
        }}
      >
        <div className="org-row" style={{ paddingLeft: 6 + (level - 1) * 18 }}>
          {hasChildren ? (
            <button
              type="button"
              className="org-twisty"
              tabIndex={-1}
              aria-label={`${expanded ? '收起' : '展开'} ${node.name}`}
              onClick={() => toggleCollapsed(node.node_id)}
            >
              {expanded ? '▾' : '▸'}
            </button>
          ) : (
            <span className="org-twisty org-twisty-empty" aria-hidden="true" />
          )}
          <button
            type="button"
            ref={(element) => {
              nodeRefs.current.set(node.node_id, element)
            }}
            className="org-node"
            tabIndex={rovingId === node.node_id ? 0 : -1}
            aria-label={`${node.name}，${kindLabel(node.kind)}${
              node.lead ? `，负责人 ${node.lead.subject_label || node.lead.subject_id}` : '，没有负责人'
            }`}
            onClick={() => {
              setSelectedId(node.node_id)
              setFocusId(node.node_id)
              setPanel(null)
            }}
          >
            <KindBadge kind={node.kind} />
            <span className="org-node-name">{node.name}</span>
            {node.lead ? (
              <span className="org-node-lead">
                负责人：{node.lead.subject_label || node.lead.subject_id}
              </span>
            ) : (
              <span className="org-node-lead org-node-lead-none">没有负责人</span>
            )}
            <span className="org-node-count">{node.assignments.length} 人</span>
          </button>
        </div>
        {hasChildren && expanded && (
          <div role="group" className="org-group">
            {node.children.map((child) => renderNode(child, level + 1))}
          </div>
        )}
      </div>
    )
  }

  const selectedPath = selectedId ? (rowById.get(selectedId)?.path ?? '') : ''
  const need = query.trim().toLowerCase()
  const matches = need
    ? rows.filter(
        (row) =>
          row.node.name.toLowerCase().includes(need) ||
          kindLabel(row.node.kind).includes(need) ||
          (row.node.lead?.subject_label ?? '').toLowerCase().includes(need),
      )
    : []

  const handleChanged = (message: string) => {
    setNotice(message)
    setPageError(null)
    setPanel(null)
    refresh()
  }

  const openPanel = (next: Panel) => {
    setNotice(null)
    setPageError(null)
    setPanel(next)
  }

  return (
    <div className="org-page">
      {notice && (
        <div className="os-note org-notice" role="status">
          {notice}
        </div>
      )}
      {pageError && (
        <div className="os-alert org-page-error" role="alert">
          {pageError.message}
        </div>
      )}

      <div className="os-grid">
        <Card
          title="组织树"
          sub={
            tree.data
              ? `${tree.data.node_count} 个节点 · ${tree.data.assignment_count} 条任职`
              : '正在读取组织节点'
          }
          span={7}
          action={
            <button
              type="button"
              className="os-btn"
              onClick={() => openPanel({ type: 'create', parentId: null })}
            >
              新建顶层节点
            </button>
          }
        >
          <div className="org-toolbar">
            <button
              type="button"
              className="os-btn os-btn-ghost"
              disabled={!selected}
              onClick={() => selected && openPanel({ type: 'create', parentId: selected.node_id })}
            >
              新建下级
            </button>
            <button
              type="button"
              className="os-btn os-btn-ghost"
              disabled={rows.length === 0}
              onClick={() => setCollapsed(new Set())}
            >
              全部展开
            </button>
            <button
              type="button"
              className="os-btn os-btn-ghost"
              disabled={rows.length === 0}
              onClick={() => setCollapsed(new Set(rows.map((row) => row.node.node_id)))}
            >
              全部收起
            </button>
            <button
              type="button"
              className="os-btn os-btn-ghost"
              onClick={() => setShowVersions((prev) => !prev)}
              aria-expanded={showVersions}
            >
              {showVersions ? '收起版本历史' : '版本历史'}
            </button>
            {/* 行业模板留入口：本轮不实现，点开只说明后续提供，不伪造模板。 */}
            <button
              type="button"
              className="os-btn os-btn-ghost"
              disabled
              title="行业模板将在后续版本提供；本轮不做预设部门"
            >
              行业模板（后续提供）
            </button>
          </div>

          {tree.error && <Failure message={tree.error} onRetry={refresh} />}
          {!tree.error && tree.loading && <Loading rows={4} />}

          {!tree.error && !tree.loading && rows.length === 0 && (
            <Empty
              title="还没有任何组织节点"
              hint="从「新建顶层节点」开始：先建企业，再往下建事业部、分公司、部门、小组或岗位。"
              action={
                <button
                  type="button"
                  className="os-btn"
                  onClick={() => openPanel({ type: 'create', parentId: null })}
                >
                  新建第一个节点
                </button>
              }
            />
          )}

          {!tree.error && rows.length > 0 && (
            <>
              {need ? (
                <div className="org-search" aria-label="搜索结果">
                  <p className="os-hint">搜索「{query.trim()}」：{matches.length} 个节点。</p>
                  {matches.length === 0 ? (
                    <Empty title="没有匹配的节点" hint="换个名称或类型再试。" />
                  ) : (
                    <ul className="org-search-list">
                      {matches.map((row) => (
                        <li key={row.node.node_id}>
                          <button
                            type="button"
                            className="os-link"
                            onClick={() => {
                              setSelectedId(row.node.node_id)
                              setFocusId(row.node.node_id)
                              setPanel(null)
                            }}
                          >
                            {row.node.name}
                          </button>
                          <span className="org-search-path">
                            {kindLabel(row.node.kind)}
                            {row.path ? ` · 上级：${row.path}` : ' · 顶层'}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <>
                  <p className="os-hint">
                    选中节点后用方向键在树内移动，左右键展开/收起；调整上级可以拖拽，也可以在右侧「移动」里选择新上级。
                  </p>
                  {dragging && (
                    <div
                      className="org-drop-root"
                      onDragOver={(event) => {
                        event.preventDefault()
                        event.dataTransfer.dropEffect = 'move'
                      }}
                      onDrop={(event) => {
                        event.preventDefault()
                        openMove('')
                      }}
                    >
                      拖到这里，移为顶层节点
                    </div>
                  )}
                  <div
                    role="tree"
                    aria-label="组织架构树"
                    className="org-tree"
                    onKeyDown={onTreeKeyDown}
                  >
                    {roots.map((root) => renderNode(root, 1))}
                  </div>
                </>
              )}
            </>
          )}
        </Card>

        <Card
          title={selected ? selected.name : '节点详情'}
          sub={selected ? `${kindLabel(selected.kind)}${selectedPath ? ` · 上级：${selectedPath}` : ' · 顶层'}` : '在左侧选择一个节点'}
          span={5}
        >
          {!selected && (
            <Empty
              title="还没有选中节点"
              hint="在左侧组织树里点一个节点，就能在这里改名、移动、设负责人、加成员或删除。"
            />
          )}

          {selected && (
            <>
              {selected.description && <p className="org-detail-desc">{selected.description}</p>}

              <div className="org-actions">
                <button
                  type="button"
                  className="os-btn os-btn-ghost"
                  onClick={() => openPanel({ type: 'rename' })}
                >
                  重命名
                </button>
                <button
                  type="button"
                  className="os-btn os-btn-ghost"
                  onClick={() =>
                    openPanel({
                      type: 'move',
                      initialParentId: selected.parent_id ?? '',
                    })
                  }
                >
                  移动
                </button>
                <button
                  type="button"
                  className="os-btn os-btn-ghost"
                  onClick={() => openPanel({ type: 'lead' })}
                >
                  设负责人
                </button>
                <button
                  type="button"
                  className="os-btn os-btn-ghost"
                  onClick={() => openPanel({ type: 'member' })}
                >
                  添加成员
                </button>
                <button
                  type="button"
                  className="os-btn os-btn-ghost"
                  onClick={() => openPanel({ type: 'delete' })}
                >
                  删除节点
                </button>
              </div>

              {panel?.type === 'create' && (
                <CreateNodeForm
                  parent={panel.parentId ? (nodeById.get(panel.parentId) ?? null) : null}
                  onDone={handleChanged}
                  onCancel={() => setPanel(null)}
                />
              )}
              {panel?.type === 'rename' && (
                <RenameForm node={selected} onDone={handleChanged} onCancel={() => setPanel(null)} />
              )}
              {panel?.type === 'move' && (
                <MoveForm
                  node={selected}
                  rows={rows}
                  initialParentId={panel.initialParentId}
                  onDone={handleChanged}
                  onCancel={() => setPanel(null)}
                />
              )}
              {panel?.type === 'lead' && (
                <LeadForm
                  node={selected}
                  employees={employees}
                  onDone={handleChanged}
                  onCancel={() => setPanel(null)}
                />
              )}
              {panel?.type === 'member' && (
                <MemberForm
                  node={selected}
                  employees={employees}
                  assignments={allAssignments}
                  onDone={handleChanged}
                  onCancel={() => setPanel(null)}
                />
              )}
              {panel?.type === 'delete' && (
                <DeleteNodeForm
                  node={selected}
                  onDone={handleChanged}
                  onCancel={() => setPanel(null)}
                />
              )}

              <h3 className="org-section-title">
                成员与负责人（{selected.assignments.length}）
              </h3>
              {selected.assignments.length === 0 ? (
                <Empty
                  title="这个节点还没有成员"
                  hint="先设一个负责人，或把 AI 员工 / 账号添加进来。"
                />
              ) : (
                <div className="org-members">
                  {selected.assignments.map((assignment) => (
                    <MemberRow
                      key={assignment.assignment_id}
                      node={selected}
                      assignment={assignment}
                      allAssignments={allAssignments}
                      onChanged={handleChanged}
                      onFail={(failure) => setPageError(failure)}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </Card>

        {showVersions && (
          <div className="os-span-12 org-version-card">
            <Card
              title="版本历史"
              sub="每次调整都会留下可回滚的版本；回滚前先列出会发生什么"
              span={12}
            >
              <VersionPanel
                currentNodes={rows.map((row) => row.node)}
                currentAssignments={allAssignments}
                latestVersionId={tree.data?.latest_version?.version_id ?? null}
                onApplied={handleChanged}
              />
            </Card>
          </div>
        )}
      </div>
    </div>
  )
}

/** 供未来接线使用：一处声明页面入口，不在这里改导航。 */
export const ORG_NAV_HINT = {
  key: ORG_PAGE.key,
  label: ORG_PAGE.title,
  hint: ORG_PAGE.sub,
} as const
