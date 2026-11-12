/**
 * 待办审批 —— 工作流卡在人工审批节点时的独立队列。
 *
 * 和「设置 → 审批中心」的区别
 * ---------------------------
 * 那边是权限线发放的一次性授权令牌（`/v1/policy/approvals`）；这里是**流程审批**：
 * 一条待办 = 某条工作流运行卡在 approval/review 节点上，通过会从该节点继续走，
 * 拒绝会直接把那条运行终止。判定与推进全部在服务端完成，本页只发决定 + 展示结果。
 *
 * 页面登记
 * --------
 * 导航 key 是 `workflow-approvals`（见 `lib/nav.ts`），`App.tsx` 用
 * `import { ApprovalQueue } from './pages/ApprovalQueue'` 挂载。
 */

import { useCallback, useMemo, useState } from 'react'
import './Workflow.css'
import { ApiError, apiFetch, useApi } from '../lib/api'
import { actionLabel, humanDuration, outcomeLabel, principalLabel, relativeTime, useNow } from '../lib/format'
import { Card, Empty, Guard } from '../components/ui'

interface ApprovalRunRef {
  run_id: string
  name: string
  status: string
  def_id: string
  current_node_id: string | null
  submitted_by: string
}

interface ApprovalItem {
  approval_id: string
  run_id: string
  step_id: string
  node_id: string
  def_id: string
  title: string
  operation: string
  reason: string
  approvers: string[]
  status: string
  submitted_by: string
  owner_account_id: string
  decided_by: string | null
  comment: string | null
  created_at?: number
  created_at_text?: string | null
  expires_at?: number | null
  expires_at_text?: string | null
  decided_at_text?: string | null
  remaining_seconds: number | null
  run: ApprovalRunRef | null
}

interface ApprovalAuditItem {
  event_id?: string | null
  timestamp?: number
  timestamp_text?: string | null
  event_type?: string
  principal_id?: string
  outcome?: string
  action?: string
  details?: Record<string, unknown>
}

interface ApprovalStep {
  step_id: string
  node_id: string
  node_type: string
  name: string
  seq: number
  status: string
  attempt: number
  error: string | null
  duration_ms: number | null
}

interface ApprovalDetail extends ApprovalItem {
  can_decide: boolean
  steps?: ApprovalStep[]
  audit?: ApprovalAuditItem[]
}

interface ApprovalSummary {
  pending: number
  overdue: number
  due_within_5m: number
}

const STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  approved: '已通过',
  rejected: '已拒绝',
  expired: '已过期',
  cancelled: '已取消',
}

const RUN_STATUS_LABELS: Record<string, string> = {
  pending: '等待开始',
  running: '执行中',
  waiting_approval: '等待审批',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const STEP_STATUS_LABELS: Record<string, string> = {
  pending: '等待',
  running: '执行中',
  waiting_approval: '等待审批',
  completed: '完成',
  failed: '失败',
  rejected: '已拒绝',
  cancelled: '已取消',
}

const STATUS_TABS: { value: string; label: string }[] = [
  { value: 'pending', label: '待处理' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'expired', label: '已过期' },
  { value: '', label: '全部' },
]

/** 页面登记：key 与 `lib/nav.ts` 的 `TASKS_NAV` 条目一致（都是 `workflow-approvals`）。 */
export const WORKFLOW_APPROVALS_PAGE = {
  key: 'workflow-approvals',
  title: '待办审批',
  navLabel: '待办审批',
  sub: '工作流卡在人工审批时，在这里通过或拒绝',
}

function toneOf(status: string): string {
  if (status === 'approved') return 'ok'
  if (status === 'rejected' || status === 'expired') return 'danger'
  if (status === 'cancelled') return 'muted'
  return 'warn'
}

function stepTone(status: string): string {
  if (status === 'completed') return 'ok'
  if (status === 'failed' || status === 'rejected') return 'danger'
  if (status === 'cancelled') return 'muted'
  return 'warn'
}

function label(map: Record<string, string>, status: string): string {
  return map[status] ?? status ?? '未知'
}

function actionErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '你不在本审批的审批人范围内，或当前账号没有这个权限。'
    if (error.status === 404) return '这条审批不存在或已被清理；刷新队列后再看。'
    if (error.status === 409) return error.message || '这条审批已经处理过或已过期，未执行。'
    return error.message || '操作没有完成，请稍后重试。'
  }
  if (error instanceof Error && error.message) return error.message
  return '操作没有完成，请稍后重试。'
}

/** 剩余有效期：null = 不过期；<=0 = 已超时。 */
function remainingText(expiresAt: number | null | undefined, now: number): string {
  if (!expiresAt) return '不过期'
  const seconds = Math.floor(expiresAt - now / 1000)
  if (seconds <= 0) return '已超时'
  return `${humanDuration(seconds)}后失效`
}

function remainingTone(expiresAt: number | null | undefined, now: number): string {
  if (!expiresAt) return 'muted'
  const seconds = expiresAt - now / 1000
  if (seconds <= 0) return 'danger'
  if (seconds <= 300) return 'danger'
  if (seconds <= 1800) return 'warn'
  return 'accent'
}

export function ApprovalQueue({ query }: { query: string }) {
  const now = useNow(1000)
  const [status, setStatus] = useState('pending')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [decision, setDecision] = useState<'approved' | 'rejected' | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<{ tone: 'ok' | 'danger'; text: string } | null>(null)

  const summary = useApi<ApprovalSummary>('/v1/approvals/summary', 10_000)
  const list = useApi<{ approvals: ApprovalItem[] }>(
    `/v1/approvals?status=${encodeURIComponent(status)}&limit=200`,
    8_000,
  )
  const detail = useApi<{ approval: ApprovalDetail }>(
    selectedId ? `/v1/approvals/${encodeURIComponent(selectedId)}` : null,
    5_000,
  )

  const need = query.trim().toLowerCase()
  const items = useMemo(() => {
    const all = list.data?.approvals ?? []
    if (!need) return all
    return all.filter((item) =>
      [
        item.title,
        item.operation,
        item.reason,
        item.submitted_by,
        item.run?.name,
        item.run_id,
        item.approval_id,
        ...(item.approvers ?? []),
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(need)),
    )
  }, [list.data, need])

  const reload = useCallback(() => {
    list.reload()
    summary.reload()
    if (selectedId) detail.reload()
  }, [list, summary, detail, selectedId])

  const openDetail = (approvalId: string) => {
    setSelectedId(approvalId)
    setDecision(null)
    setComment('')
    setFeedback(null)
  }

  const submitDecision = async () => {
    const approval = detail.data?.approval
    if (!approval || !decision || busy) return
    setBusy(true)
    setFeedback(null)
    try {
      const result = await apiFetch<{ approval: ApprovalDetail }>(
        `/v1/approvals/${encodeURIComponent(approval.approval_id)}/${decision === 'approved' ? 'approve' : 'reject'}`,
        { method: 'POST', body: JSON.stringify({ comment: comment.trim() }) },
      )
      const updated = result.approval
      const runStatus = updated.run?.status ?? ''
      setFeedback({
        tone: decision === 'approved' ? 'ok' : 'danger',
        text:
          decision === 'approved'
            ? `已通过「${approval.title || approval.operation || approval.approval_id}」。流程${
                runStatus ? `当前状态：${label(RUN_STATUS_LABELS, runStatus)}` : '已继续'
              }。`
            : `已拒绝「${approval.title || approval.operation || approval.approval_id}」。流程已终止，当前状态：${
                runStatus ? label(RUN_STATUS_LABELS, runStatus) : '已取消'
              }。`,
      })
      setDecision(null)
      setComment('')
      reload()
    } catch (error) {
      setFeedback({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusy(false)
    }
  }

  const current = detail.data?.approval

  return (
    <div className="os-grid">
      <Card
        title="待办概览"
        sub="只统计你能看到的审批（每 10 秒刷新）"
        span={12}
        action={
          <button className="os-btn os-btn-ghost" type="button" onClick={reload}>
            刷新
          </button>
        }
      >
        <div className="wf-counts">
          <span className="wf-count">
            <span className="wf-count-label">待处理</span>
            <strong>{summary.data?.pending ?? '—'}</strong>
          </span>
          <span className="wf-count">
            <span className="wf-count-label">已超时未处理</span>
            <strong className={summary.data?.overdue ? 'wf-warn' : ''}>
              {summary.data?.overdue ?? '—'}
            </strong>
          </span>
          <span className="wf-count">
            <span className="wf-count-label">5 分钟内到期</span>
            <strong className={summary.data?.due_within_5m ? 'wf-warn' : ''}>
              {summary.data?.due_within_5m ?? '—'}
            </strong>
          </span>
        </div>
        <p className="os-hint">
          超时的审批会被后台扫描落为「已过期」，对应的流程运行同时终止 —— 过期不是只改状态。
        </p>
        {feedback && (
          <p
            className={feedback.tone === 'ok' ? 'wf-feedback wf-feedback-ok' : 'wf-feedback wf-feedback-error'}
            role={feedback.tone === 'ok' ? 'status' : 'alert'}
          >
            {feedback.text}
          </p>
        )}
      </Card>

      <Card
        title="审批队列"
        sub="谁提交的、什么操作、为什么需要批准、多久自动失效"
        span={selectedId ? 7 : 12}
        action={
          selectedId ? (
            <button className="os-btn" type="button" onClick={() => setSelectedId(null)}>
              收起详情
            </button>
          ) : undefined
        }
      >
        <div className="wf-tabs" role="group" aria-label="按审批状态筛选">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value || 'all'}
              type="button"
              className={`wf-tab${status === tab.value ? ' wf-tab-active' : ''}`}
              aria-pressed={status === tab.value}
              onClick={() => {
                setStatus(tab.value)
                setSelectedId(null)
                setDecision(null)
                setComment('')
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {items.length === 0 && !list.loading && !list.error ? (
          <Empty
            title={status === 'pending' ? '没有待处理的审批' : '这个状态下没有记录'}
            hint={
              status === 'pending'
                ? '工作流运行卡在人工审批节点时会出现在这里；没有待办说明当前没有流程在等人工决定。'
                : '切换上面的状态看历史处理记录。'
            }
          />
        ) : (
          <Guard loading={list.loading} error={list.error} data={list.data} onRetry={list.reload}>
            {() => (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>审批</th>
                      <th>谁提交的</th>
                      <th>操作</th>
                      <th>为什么需要批准</th>
                      <th>状态</th>
                      <th>有效期</th>
                      <th>提交时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.approval_id} className="os-clickable" onClick={() => openDetail(item.approval_id)}>
                        <td>
                          <button
                            className="os-link wf-def-name"
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation()
                              openDetail(item.approval_id)
                            }}
                          >
                            {item.title || item.operation || '未命名审批'}
                          </button>
                          <div className="wf-sub">{item.run?.name || item.run_id}</div>
                          <div className="wf-sub">节点 {item.node_id}</div>
                        </td>
                        <td>{item.submitted_by || '—'}</td>
                        <td>{item.operation || '—'}</td>
                        <td className="wf-reason-cell">{item.reason || '未写明理由'}</td>
                        <td>
                          <span className="os-badge" data-tone={toneOf(item.status)}>
                            {label(STATUS_LABELS, item.status)}
                          </span>
                        </td>
                        <td>
                          {item.status === 'pending' ? (
                            <span className="os-badge" data-tone={remainingTone(item.expires_at, now)}>
                              {remainingText(item.expires_at, now)}
                            </span>
                          ) : (
                            <span className="wf-sub">
                              {item.status === 'expired' ? '已超时失效' : item.decided_at_text ?? '—'}
                            </span>
                          )}
                        </td>
                        <td>{item.created_at_text ?? relativeTime(item.created_at ?? 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Guard>
        )}
      </Card>

      {selectedId && (
        <Card
          title={current ? `审批详情 · ${current.title || current.operation || current.approval_id}` : '审批详情'}
          sub="通过/拒绝会直接驱动对应流程继续或终止；每次决定都写进审计"
          span={5}
        >
          <Guard loading={detail.loading} error={detail.error} data={detail.data} onRetry={detail.reload}>
            {(data) => {
              const approval = data.approval
              const canDecide = approval.can_decide && approval.status === 'pending'
              return (
                <>
                  <dl className="os-kv">
                    <dt>状态</dt>
                    <dd>
                      <span className="os-badge" data-tone={toneOf(approval.status)}>
                        {label(STATUS_LABELS, approval.status)}
                      </span>
                      {approval.status === 'pending' && (
                        <span className="wf-sub"> · {remainingText(approval.expires_at, now)}</span>
                      )}
                    </dd>
                    <dt>操作</dt>
                    <dd>{approval.operation || '—'}</dd>
                    <dt>为什么需要批准</dt>
                    <dd className="wf-reason-cell">{approval.reason || '提交时没有写理由'}</dd>
                    <dt>谁提交的</dt>
                    <dd>
                      {approval.submitted_by || '—'}
                      {approval.created_at_text ? ` · ${approval.created_at_text}` : ''}
                    </dd>
                    <dt>审批人</dt>
                    <dd>{approval.approvers.length > 0 ? approval.approvers.join('、') : '流程所属账号'}</dd>
                    <dt>所属流程</dt>
                    <dd>
                      {approval.run?.name || approval.def_id}
                      {approval.run?.status ? ` · ${label(RUN_STATUS_LABELS, approval.run.status)}` : ''}
                    </dd>
                    <dt>超时失效</dt>
                    <dd>
                      {approval.expires_at_text ?? '不过期'}
                      {approval.status === 'pending' && approval.expires_at
                        ? ` · ${remainingText(approval.expires_at, now)}`
                        : ''}
                    </dd>
                    {approval.decided_by && (
                      <>
                        <dt>处理人</dt>
                        <dd>
                          {approval.decided_by}
                          {approval.decided_at_text ? ` · ${approval.decided_at_text}` : ''}
                        </dd>
                      </>
                    )}
                    {approval.comment && (
                      <>
                        <dt>处理意见</dt>
                        <dd>{approval.comment}</dd>
                      </>
                    )}
                  </dl>

                  {approval.status === 'pending' && !approval.can_decide && (
                    <p className="os-note" role="status">
                      你不在本审批的审批人范围内：可以查看，但不能通过或拒绝。
                    </p>
                  )}

                  {canDecide && (
                    <div className="wf-decision">
                      {decision === null ? (
                        <div className="wf-row-actions">
                          <button className="os-btn" type="button" onClick={() => setDecision('approved')}>
                            通过
                          </button>
                          <button
                            className="os-btn os-btn-ghost wf-danger"
                            type="button"
                            onClick={() => setDecision('rejected')}
                          >
                            拒绝
                          </button>
                        </div>
                      ) : (
                        <>
                          <strong className={decision === 'rejected' ? 'wf-danger-text' : undefined}>
                            {decision === 'approved'
                              ? '确认通过：流程会从这个审批节点继续往下走。'
                              : '确认拒绝：这条流程运行会被立即终止。'}
                          </strong>
                          <label className="wf-field">
                            处理意见（可留空，会写进审计）
                            <textarea
                              className="wf-textarea"
                              rows={2}
                              maxLength={1000}
                              value={comment}
                              onChange={(event) => setComment(event.target.value)}
                            />
                          </label>
                          <div className="wf-row-actions">
                            <button
                              className="os-btn"
                              type="button"
                              disabled={busy}
                              onClick={() => void submitDecision()}
                            >
                              {busy ? '提交中…' : decision === 'approved' ? '确认通过' : '确认拒绝'}
                            </button>
                            <button
                              className="os-btn os-btn-ghost"
                              type="button"
                              disabled={busy}
                              onClick={() => {
                                setDecision(null)
                                setComment('')
                              }}
                            >
                              返回
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  <h3 className="os-detail-title">流程步骤</h3>
                  {(approval.steps ?? []).length === 0 ? (
                    <p className="os-hint">这条运行还没有步骤记录。</p>
                  ) : (
                    <ol className="wf-steps">
                      {(approval.steps ?? []).map((step, index) => (
                        <li key={step.step_id || `${step.node_id}-${index}`} className={`wf-step wf-step-${step.status}`}>
                          <div className="wf-step-head">
                            <span className="wf-step-seq">第 {step.seq || index + 1} 步</span>
                            <span className="wf-step-name">{step.name || step.node_id}</span>
                            <span className="os-badge" data-tone={stepTone(step.status)}>
                              {label(STEP_STATUS_LABELS, step.status)}
                            </span>
                          </div>
                          {step.error && <div className="wf-step-error">{step.error}</div>}
                        </li>
                      ))}
                    </ol>
                  )}

                  <h3 className="os-detail-title">审计记录</h3>
                  {(approval.audit ?? []).length === 0 ? (
                    <p className="os-hint">
                      这条流程还没有 workflow.* 审计事件。审计文件未启用时不会有记录，详情请到「设置 → 审计链」查看。
                    </p>
                  ) : (
                    <ol className="wf-audit">
                      {(approval.audit ?? []).map((event, index) => (
                        <li key={event.event_id || `${event.timestamp}-${index}`}>
                          <div className="wf-step-head">
                            <span className="wf-step-name">{actionLabel(event.action) || '流程事件'}</span>
                            <span className="wf-sub">
                              {event.outcome ? outcomeLabel(event.outcome) : ''}
                            </span>
                          </div>
                          <div className="wf-step-meta">
                            <span>{event.timestamp_text ?? '—'}</span>
                            <span>{principalLabel(event.principal_id)}</span>
                          </div>
                          {event.details && Object.keys(event.details).length > 0 && (
                            <details className="wf-io">
                              <summary>详情</summary>
                              <pre className="wf-pre">{JSON.stringify(event.details, null, 2)}</pre>
                            </details>
                          )}
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )
            }}
          </Guard>
        </Card>
      )}
    </div>
  )
}
