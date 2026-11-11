import { useMemo } from 'react'
import './EnterpriseOverview.css'
import { useApi } from '../lib/api'
import { getSession } from '../lib/auth'
import type { EmployeesPayload } from '../lib/contracts'
import type { UsagePayload } from '../lib/accounts'
import { assigneeText, parseTasksPayload, taskStatusLabel } from '../lib/taskLedger'
import { numberText, relativeTime, taskNameLabel } from '../lib/format'
import {
  approvalBlockedSeconds,
  approvalRiskLabel,
  buildDepartmentRows,
  completedToday,
  sortPendingApprovals,
  summarizeEmployees,
  summarizeHumanHold,
  summarizeOrg,
  taskCoverage,
  type DepartmentRow,
  type OrgTreeLike,
  type PendingApprovalsLike,
} from '../lib/enterpriseOverview'
import { Card, Empty, Failure, Loading } from '../components/ui'

/** 统一的自动刷新节奏；页面内所有数据源共用。 */
const REFRESH_MS = 30_000

/**
 * 路由信息先放在页面模块里。
 *
 * 本轮不改 `nav.ts` / `App.tsx`（公共文件正在被其他工作线改动），
 * 接入时直接引用这个常量即可：
 * `key` 用作导航键，`navLabel` 用作导航文案，`title` / `sub` 用作页头。
 */
export const ENTERPRISE_OVERVIEW_PAGE = {
  key: 'enterprise-overview',
  navLabel: '企业总览',
  title: '企业总览',
  sub: '打开工作台第一眼，看清企业现在什么状态',
} as const

export interface EnterpriseOverviewProps {
  /** 当前登录账号；缺省时从会话读取，方便以后由 App 直接注入。 */
  principal?: string
  /** 预留给宿主页的跳转入口；本轮不接路由，不影响渲染。 */
  onNavigate?: (key: string) => void
}

interface SourceState {
  loading: boolean
  error: string | null
  status: number | null
}

interface MetricProps {
  label: string
  value?: string
  hint?: string
  loading?: boolean
  /** 有值时显示「暂未提供」并把原因写在下面，不显示 0。 */
  fallback?: string
  tone?: 'ok' | 'warn' | 'danger'
}

function Metric({ label, value, hint, loading, fallback, tone }: MetricProps) {
  return (
    <div className="os-kpi" data-tone={tone}>
      <div className="os-kpi-label">{label}</div>
      {loading ? (
        <div className="os-kpi-value eov-metric-loading">正在读取…</div>
      ) : fallback ? (
        <div className="os-kpi-value eov-metric-missing">暂未提供</div>
      ) : (
        <div className="os-kpi-value">{value}</div>
      )}
      <div className={fallback ? 'eov-metric-reason' : 'os-kpi-foot'}>
        {fallback ?? hint}
      </div>
    </div>
  )
}

function sourceReason(source: SourceState, what: string): string {
  if (source.error) return `${what}读取失败：${source.error}`
  if (source.status) return `${what}读取失败：HTTP ${source.status}`
  return `${what}返回的数据结构不符合预期`
}

function Unavailable({ title, reason }: { title: string; reason: string }) {
  return (
    <div className="eov-unavailable" role="status">
      <strong>{title}</strong>
      <span>{reason}</span>
    </div>
  )
}

function Unauthorized({ reason }: { reason: string }) {
  return (
    <div className="eov-unauthorized" role="status">
      <strong>你无权查看</strong>
      <span>{reason}</span>
    </div>
  )
}

function ago(value: string | null | undefined): string {
  if (!value) return '时间未记录'
  const stamp = Date.parse(value)
  if (!Number.isFinite(stamp)) return '时间未记录'
  return relativeTime(stamp / 1000)
}

function kindLabel(kind: string): string {
  switch (kind.trim().toLowerCase()) {
    case 'department':
      return '部门'
    case 'team':
      return '小组'
    case 'position':
      return '岗位'
    case 'company':
      return '公司'
    default:
      return kind.trim() || '节点'
  }
}

function sourceLabel(channel: string | undefined): string {
  switch (String(channel ?? '').trim().toLowerCase()) {
    case 'local':
      return '本机'
    case 'lan':
      return '同一 Wi-Fi 手机'
    default:
      return '来源未标注'
  }
}

function formatCost(value: number): string {
  if (!Number.isFinite(value)) return '未计入'
  if (value === 0) return '¥0.00'
  if (value < 0.01) return `¥${numberText(value, 4)}`
  if (value < 1) return `¥${numberText(value, 3)}`
  return `¥${numberText(value, 2)}`
}

function DeptNode({ row, tasksKnown }: { row: DepartmentRow; tasksKnown: boolean }) {
  return (
    <li>
      <div className="eov-org-row">
        <div className="eov-org-main">
          <span className="eov-org-name">{row.name}</span>
          <span className="eov-org-kind">{kindLabel(row.kind)}</span>
        </div>
        <div className="eov-org-counts">
          <span>AI 员工 {row.memberCount} 人</span>
          {tasksKnown ? (
            <>
              <span>本节点 {row.directTaskCount} 项</span>
              {row.children.length > 0 && <span>含子部门 {row.subtreeTaskCount} 项</span>}
            </>
          ) : (
            <span>任务数暂未提供</span>
          )}
        </div>
      </div>
      {row.children.length > 0 && (
        <ul className="eov-org-tree eov-org-children">
          {row.children.map((child) => (
            <DeptNode key={child.key} row={child} tasksKnown={tasksKnown} />
          ))}
        </ul>
      )}
    </li>
  )
}

export function EnterpriseOverview({ principal, onNavigate }: EnterpriseOverviewProps) {
  // 显式传入优先；没传时用当前会话，避免页面必须依赖 App 改造才能读用量。
  const currentPrincipal = useMemo(
    () => principal ?? getSession()?.principal ?? null,
    [principal],
  )

  const employees = useApi<EmployeesPayload>('/v1/employees', REFRESH_MS)
  const org = useApi<OrgTreeLike>('/v1/org/tree', REFRESH_MS)
  const running = useApi('/v1/tasks?status=running&limit=5&sort_by=created_at&sort_order=desc', REFRESH_MS)
  const completed = useApi(
    '/v1/tasks?status=completed&limit=200&sort_by=completed_at&sort_order=desc',
    REFRESH_MS,
  )
  const failed = useApi(
    '/v1/tasks?status=failed&limit=5&sort_by=updated_at&sort_order=desc',
    REFRESH_MS,
  )
  const recent = useApi('/v1/tasks?limit=200&sort_by=created_at&sort_order=desc', REFRESH_MS)
  const approvals = useApi<PendingApprovalsLike>('/v1/policy/approvals/pending', REFRESH_MS)
  const usage = useApi<UsagePayload>(
    currentPrincipal
      ? `/v1/accounts/${encodeURIComponent(currentPrincipal)}/usage`
      : null,
    REFRESH_MS,
  )

  const employeeSummary = useMemo(() => summarizeEmployees(employees.data), [employees.data])
  const orgSummary = useMemo(() => summarizeOrg(org.data), [org.data])

  const runningPage = useMemo(() => parseTasksPayload(running.data), [running.data])
  const completedPage = useMemo(() => parseTasksPayload(completed.data), [completed.data])
  const failedPage = useMemo(() => parseTasksPayload(failed.data), [failed.data])
  const recentPage = useMemo(() => parseTasksPayload(recent.data), [recent.data])

  const completedTodaySummary = useMemo(
    () => completedToday(completedPage, Date.now()),
    [completedPage],
  )
  const humanHold = useMemo(() => summarizeHumanHold(recentPage), [recentPage])
  const coverage = useMemo(() => taskCoverage(recentPage), [recentPage])
  const departmentRows = useMemo(
    () => buildDepartmentRows(org.data, recentPage),
    [org.data, recentPage],
  )
  const approvalItems = useMemo(() => sortPendingApprovals(approvals.data), [approvals.data])

  const taskPages = [runningPage, completedPage, failedPage, recentPage]
  const taskSources = [running, completed, failed, recent]
  const taskKnownCount = taskPages.filter((page) => page !== null).length
  const taskAllLoading = taskSources.every((source) => source.loading)
  const taskAllUnavailable = taskKnownCount === 0 && !taskAllLoading
  const firstTaskFailure =
    taskSources.find((source) => !source.loading && (source.error || source.status)) ?? running

  const refreshAll = () => {
    employees.reload()
    org.reload()
    running.reload()
    completed.reload()
    failed.reload()
    recent.reload()
    approvals.reload()
    usage.reload()
  }

  const employeesFallback = !employeeSummary && !employees.loading
    ? sourceReason(employees, '员工名册')
    : undefined
  const orgFallback = !orgSummary && !org.loading ? sourceReason(org, '组织架构') : undefined
  const runningFallback =
    !runningPage && !running.loading ? sourceReason(running, '任务台账') : undefined
  const completedFallback =
    !completedPage && !completed.loading ? sourceReason(completed, '任务台账') : undefined
  const failedFallback =
    !failedPage && !failed.loading ? sourceReason(failed, '任务台账') : undefined
  const recentFallback =
    !recentPage && !recent.loading ? sourceReason(recent, '任务台账') : undefined

  const todayHint = completedTodaySummary
    ? [
        completedTodaySummary.exact
          ? '按全部已完成任务统计'
          : `仅扫描最近 ${completedTodaySummary.scanned} 条已完成任务（全量 ${completedTodaySummary.total} 条），今日数可能偏低`,
        completedTodaySummary.missingTime > 0
          ? `${completedTodaySummary.missingTime} 条缺少完成时间，未计入`
          : '',
        '完成时间按本地时区今日 00:00 起',
      ]
        .filter(Boolean)
        .join('；')
    : undefined

  const holdHint = humanHold
    ? humanHold.exact
      ? `全量 ${humanHold.total} 条任务中 ${humanHold.count} 条标记人工挂起`
      : `最近 ${humanHold.scanned} 条任务中 ${humanHold.count} 条标记人工挂起（全量 ${humanHold.total} 条）`
    : undefined

  const coverageHint = coverage
    ? coverage.partial
      ? `部门任务数按最近 ${coverage.scanned} 条任务统计（全量 ${coverage.total} 条），计数可能偏低`
      : `部门任务数按全部 ${coverage.total} 条任务统计`
    : ''

  const usageHasShape =
    usage.data !== null &&
    (typeof usage.data.calls === 'number' ||
      typeof usage.data.total_tokens === 'number' ||
      Array.isArray(usage.data.by_model))

  return (
    <div className="eov-page">
      <div className="eov-head">
        <div>
          <h1 className="os-page-title">{ENTERPRISE_OVERVIEW_PAGE.title}</h1>
          <p className="os-page-sub">
            所有数字都来自本地服务的真实接口；读不到的项会原地写「暂未提供」和原因，不补假数。
            每 30 秒自动刷新。
          </p>
        </div>
        <div className="eov-head-actions">
          <button type="button" className="os-btn os-btn-ghost" onClick={refreshAll}>
            刷新
          </button>
        </div>
      </div>

      <div className="os-grid">
        <Card
          title="企业运行概况"
          sub="组织规模、员工与执行器；在线/暂停按员工在职状态统计"
          span={12}
        >
          <div className="os-kpis">
            <Metric
              label="组织节点"
              value={orgSummary ? numberText(orgSummary.nodeCount) : undefined}
              hint={
                orgSummary
                  ? `${numberText(orgSummary.assignmentCount)} 条任职记录 · ${numberText(orgSummary.employeeMemberCount)} 名 AI 员工`
                  : undefined
              }
              loading={org.loading && !orgSummary}
              fallback={orgFallback}
            />
            <Metric
              label="员工总数"
              value={employeeSummary ? numberText(employeeSummary.total) : undefined}
              hint={
                employeeSummary
                  ? `在线 ${numberText(employeeSummary.active)} 人 · 暂停 ${numberText(employeeSummary.disabled)} 人`
                  : undefined
              }
              loading={employees.loading && !employeeSummary}
              fallback={employeesFallback}
            />
            <Metric
              label="在线 / 暂停"
              value={
                employeeSummary
                  ? `${numberText(employeeSummary.active)} / ${numberText(employeeSummary.disabled)}`
                  : undefined
              }
              hint={
                employeeSummary
                  ? employeeSummary.other > 0
                    ? `在线=在职（active），暂停=停用（disabled）；另有 ${numberText(employeeSummary.other)} 人状态未知`
                    : '在线=在职（active），暂停=停用（disabled）'
                  : undefined
              }
              loading={employees.loading && !employeeSummary}
              fallback={employeesFallback}
            />
            <Metric
              label="执行中执行器"
              value={employeeSummary ? numberText(employeeSummary.runningAgents) : undefined}
              hint={
                employeeSummary
                  ? `共 ${numberText(employeeSummary.agents)} 个执行器 · ${numberText(employeeSummary.employeesWithRunningAgents)} 名员工正在跑`
                  : undefined
              }
              loading={employees.loading && !employeeSummary}
              fallback={employeesFallback}
              tone={employeeSummary && employeeSummary.runningAgents > 0 ? 'ok' : undefined}
            />
          </div>
          {onNavigate && (
            <div className="eov-next-actions">
              <button
                type="button"
                className="os-btn os-btn-ghost"
                onClick={() => onNavigate('office')}
              >
                进入办公室
              </button>
              <button
                type="button"
                className="os-btn os-btn-primary"
                onClick={() => onNavigate('butler')}
              >
                问贾维斯
              </button>
            </div>
          )}
        </Card>

        <Card
          title="任务运行"
          sub="执行中、今日完成、失败与人工挂起；数据来自任务台账"
          span={7}
        >
          {taskAllLoading ? (
            <Loading rows={3} />
          ) : taskAllUnavailable ? (
            <Unavailable
              title="任务数据暂未提供"
              reason={sourceReason(firstTaskFailure, '任务台账')}
            />
          ) : (
            <>
              <div className="os-kpis">
                <Metric
                  label="执行中任务"
                  value={runningPage ? numberText(runningPage.total) : undefined}
                  hint={runningPage ? '任务台账里执行中状态的全量计数' : undefined}
                  loading={running.loading && !runningPage}
                  fallback={runningFallback}
                  tone={runningPage && runningPage.total > 0 ? 'ok' : undefined}
                />
                <Metric
                  label="今日完成"
                  value={
                    completedTodaySummary ? numberText(completedTodaySummary.count) : undefined
                  }
                  hint={todayHint}
                  loading={completed.loading && !completedPage}
                  fallback={completedFallback}
                />
                <Metric
                  label="失败任务"
                  value={failedPage ? numberText(failedPage.total) : undefined}
                  hint={failedPage ? '任务台账里失败状态的全量计数' : undefined}
                  loading={failed.loading && !failedPage}
                  fallback={failedFallback}
                  tone={failedPage && failedPage.total > 0 ? 'danger' : undefined}
                />
                <Metric
                  label="人工挂起"
                  value={humanHold ? numberText(humanHold.count) : undefined}
                  hint={holdHint}
                  loading={recent.loading && !recentPage}
                  fallback={recentFallback}
                  tone={humanHold && humanHold.count > 0 ? 'warn' : undefined}
                />
              </div>

              {runningPage && runningPage.items.length > 0 && (
                <div className="eov-subsection">
                  <h3 className="eov-subtitle">正在执行</h3>
                  <ul className="eov-list">
                    {runningPage.items.slice(0, 5).map((task) => (
                      <li key={task.task_id} className="eov-list-item">
                        <div className="eov-task-title">{taskNameLabel(task.title)}</div>
                        <div className="eov-task-meta">
                          {assigneeText(task.assignee)} · {taskStatusLabel(task.status)} · 开始于{' '}
                          {ago(task.started_at ?? task.created_at)}
                        </div>
                      </li>
                    ))}
                  </ul>
                  {runningPage.total > runningPage.items.length && (
                    <p className="eov-note">
                      另有 {numberText(runningPage.total - runningPage.items.length)} 条执行中任务未列出。
                    </p>
                  )}
                </div>
              )}

              {failedPage && failedPage.items.length > 0 && (
                <div className="eov-subsection">
                  <h3 className="eov-subtitle">最近失败</h3>
                  <ul className="eov-list">
                    {failedPage.items.slice(0, 5).map((task) => (
                      <li key={task.task_id} className="eov-list-item">
                        <div className="eov-task-title">{taskNameLabel(task.title)}</div>
                        <div className="eov-task-meta">
                          {assigneeText(task.assignee)} · 更新于{' '}
                          {ago(task.updated_at ?? task.completed_at ?? task.created_at)}
                        </div>
                        {task.error && <div className="eov-fail-reason">{task.error}</div>}
                      </li>
                    ))}
                  </ul>
                  {failedPage.total > failedPage.items.length && (
                    <p className="eov-note">
                      另有 {numberText(failedPage.total - failedPage.items.length)} 条失败任务未列出。
                    </p>
                  )}
                </div>
              )}

              {runningPage && runningPage.items.length === 0 && (
                <p className="eov-note">当前没有正在执行的任务。</p>
              )}
            </>
          )}
        </Card>

        <Card
          title="待审批事项"
          sub="只对能签发审批凭据的管理员开放；没有权限时明确告知，不显示成空列表"
          span={5}
        >
          {approvals.loading && !approvals.data ? (
            <Loading rows={2} />
          ) : approvals.status === 403 ? (
            <Unauthorized reason="审批队列只对管理员开放，当前账号请求 /v1/policy/approvals/pending 返回 HTTP 403。" />
          ) : approvals.status === 404 || approvals.status === 405 ? (
            <Unavailable
              title="暂未提供"
              reason={`本地服务没有挂载待审批接口（HTTP ${approvals.status}）。`}
            />
          ) : approvals.error ? (
            <Failure message={approvals.error} onRetry={approvals.reload} />
          ) : approvalItems === null ? (
            <Unavailable title="暂未提供" reason="待审批接口返回的数据结构不符合预期。" />
          ) : (
            (() => {
              const total = Math.max(
                typeof approvals.data?.count === 'number' ? approvals.data.count : 0,
                approvalItems.length,
              )
              if (total === 0) {
                return (
                  <Empty
                    title="当前没有待审批事项"
                    hint="最近一次扫描没有发现被策略暂缓的动作；审批队列仍然只对管理员可见。"
                  />
                )
              }
              return (
                <>
                  <div className="eov-count-line">
                    <strong>{numberText(total)}</strong>
                    <span>项等待你确认</span>
                  </div>
                  <ul className="eov-list">
                    {approvalItems.slice(0, 5).map((item, index) => {
                      const blockedAt = approvalBlockedSeconds(item.blocked_at)
                      return (
                        <li
                          key={item.pending_id ?? `pending-${index}`}
                          className="eov-list-item"
                        >
                          <div className="eov-approval-head">
                            <span
                              className="eov-risk"
                              data-risk={String(item.risk_level ?? '').toUpperCase()}
                            >
                              {approvalRiskLabel(item.risk_level)}
                            </span>
                            <span className="eov-task-title">
                              {item.summary || item.action || '待确认动作'}
                            </span>
                          </div>
                          <div className="eov-task-meta">
                            {sourceLabel(item.source_channel)}
                            {blockedAt
                              ? ` · 拦截于 ${relativeTime(blockedAt)}`
                              : ' · 拦截时间未记录'}
                            {item.blocked_by ? ` · 触发方 ${item.blocked_by}` : ''}
                          </div>
                          {item.reason && item.reason !== item.summary && (
                            <div className="eov-task-meta">{item.reason}</div>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                  {total > approvalItems.length && (
                    <p className="eov-note">
                      还有 {numberText(total - approvalItems.length)} 项未列出。
                    </p>
                  )}
                  <p className="eov-note">本页只读展示；批准或拒绝仍需在审批中心完成。</p>
                </>
              )
            })()
          )}
        </Card>

        <Card
          title="部门工作情况"
          sub="按组织节点汇总成员任务；任务归属取任务负责人的员工名"
          span={7}
        >
          {org.loading && !org.data ? (
            <Loading rows={3} />
          ) : org.error ? (
            <Failure message={org.error} onRetry={org.reload} />
          ) : orgSummary === null ? (
            <Unavailable title="暂未提供" reason={sourceReason(org, '组织架构')} />
          ) : orgSummary.nodeCount === 0 ? (
            <Empty
              title="组织架构尚未建立"
              hint="GET /v1/org/tree 返回 0 个节点；添加部门后，这里会按节点汇总成员任务。"
            />
          ) : (
            <>
              <p className="eov-note">
                共 {numberText(orgSummary.nodeCount)} 个节点 ·{' '}
                {numberText(orgSummary.assignmentCount)} 条任职 ·{' '}
                {numberText(orgSummary.employeeMemberCount)} 名 AI 员工
                {orgSummary.humanMemberCount > 0
                  ? ` · ${numberText(orgSummary.humanMemberCount)} 名真人`
                  : ''}
              </p>
              {!recentPage && (
                <Unavailable
                  title="任务数暂未提供"
                  reason={recentFallback ?? '任务台账返回的数据结构不符合预期。'}
                />
              )}
              <ul className="eov-org-tree">
                {departmentRows.map((row) => (
                  <DeptNode key={row.key} row={row} tasksKnown={recentPage !== null} />
                ))}
              </ul>
              {coverageHint && <p className="eov-note">{coverageHint}；同一任务涉及多个成员时会在多个节点各计一次。</p>}
              {orgSummary.duplicateEmployeeCount > 0 && (
                <p className="eov-note">
                  有 {numberText(orgSummary.duplicateEmployeeCount)} 名 AI 员工挂在多个节点，任务会在这些节点各计一次。
                </p>
              )}
            </>
          )}
        </Card>

        <Card
          title="资源与模型消耗"
          sub="来自当前账号的用量接口；没有接入或没有权限时如实说明"
          span={5}
        >
          {!currentPrincipal ? (
            <Unavailable
              title="暂未提供"
              reason="当前会话没有账号标识（未登录或会话已失效），无法读取账号用量。"
            />
          ) : usage.loading && !usage.data ? (
            <Loading rows={2} />
          ) : usage.status === 403 ? (
            <Unauthorized reason="账号用量只对本人或有权查看的上级账号开放，当前账号返回 HTTP 403。" />
          ) : usage.status === 404 || usage.status === 405 ? (
            <Unavailable
              title="暂未接入"
              reason={`本地服务没有挂载用量接口 /v1/accounts/{principal}/usage（HTTP ${usage.status}）。`}
            />
          ) : usage.error ? (
            <Failure message={usage.error} onRetry={usage.reload} />
          ) : !usageHasShape || !usage.data ? (
            <Unavailable
              title="暂未提供"
              reason="用量接口没有返回调用次数、词元或按模型明细字段，无法判断消耗。"
            />
          ) : (
            (() => {
              const data = usage.data
              const models = Array.isArray(data.by_model)
                ? data.by_model.slice().sort((left, right) => right.total_tokens - left.total_tokens)
                : null
              const costReady = typeof data.cost === 'number' && Number.isFinite(data.cost)
              return (
                <>
                  <div className="os-kpis">
                    <Metric
                      label="调用次数"
                      value={typeof data.calls === 'number' ? numberText(data.calls) : undefined}
                      hint="当前统计范围内的模型调用总数"
                      fallback={
                        typeof data.calls === 'number'
                          ? undefined
                          : '用量接口没有返回调用次数。'
                      }
                    />
                    <Metric
                      label="总词元"
                      value={
                        typeof data.total_tokens === 'number'
                          ? numberText(data.total_tokens)
                          : undefined
                      }
                      hint={
                        typeof data.tokens_in === 'number' && typeof data.tokens_out === 'number'
                          ? `输入 ${numberText(data.tokens_in)} · 输出 ${numberText(data.tokens_out)}`
                          : undefined
                      }
                      fallback={
                        typeof data.total_tokens === 'number'
                          ? undefined
                          : '用量接口没有返回词元字段。'
                      }
                    />
                    <Metric
                      label="估算成本"
                      value={costReady ? formatCost(data.cost as number) : undefined}
                      hint={
                        costReady
                          ? data.cost_complete === false
                            ? '部分模型未定价，成本不是全部'
                            : '按已定价模型累计'
                          : undefined
                      }
                      fallback={
                        costReady
                          ? undefined
                          : data.cost_message || '用量接口没有返回成本字段。'
                      }
                    />
                    <Metric
                      label="模型数"
                      value={models ? numberText(models.length) : undefined}
                      hint="按模型明细条数"
                      fallback={models ? undefined : '用量接口没有返回按模型明细。'}
                    />
                  </div>

                  <p className="eov-note">
                    {data.scope === 'subtree'
                      ? '统计范围：本账号及下级子树'
                      : data.scope === 'self'
                        ? '统计范围：仅本账号'
                        : '统计范围：本地服务未标注'}
                    {data.callers_visible === false ? '（上级账号调用不可见）' : ''}
                  </p>

                  {typeof data.calls === 'number' && data.calls === 0 && (
                    <p className="eov-note">当前统计范围还没有模型调用记录。</p>
                  )}

                  {models === null ? (
                    <p className="eov-note">暂未提供：用量接口没有返回按模型明细。</p>
                  ) : models.length === 0 ? (
                    <p className="eov-note">暂未提供：按模型明细为空。</p>
                  ) : (
                    <div className="eov-usage-scroll">
                      <table className="eov-usage-table" aria-label="按模型用量">
                        <thead>
                          <tr>
                            <th scope="col">模型</th>
                            <th scope="col">输入</th>
                            <th scope="col">输出</th>
                            <th scope="col">合计</th>
                            <th scope="col">成本</th>
                          </tr>
                        </thead>
                        <tbody>
                          {models.slice(0, 8).map((model, index) => (
                            <tr key={`${model.model}-${index}`}>
                              <th scope="row" className="eov-model-name">
                                {model.model || '未命名模型'}
                              </th>
                              <td>{numberText(model.tokens_in)}</td>
                              <td>{numberText(model.tokens_out)}</td>
                              <td>{numberText(model.total_tokens)}</td>
                              <td>
                                {typeof model.cost === 'number'
                                  ? formatCost(model.cost)
                                  : model.pricing_status === 'unknown'
                                    ? '未定价'
                                    : '未计入'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {models && models.length > 8 && (
                    <p className="eov-note">仅列出词元用量最多的 8 个模型。</p>
                  )}
                  {data.cost_message && <p className="eov-note">{data.cost_message}</p>}
                  {data.budget_note && <p className="eov-note">{data.budget_note}</p>}
                </>
              )
            })()
          )}
        </Card>
      </div>
    </div>
  )
}

export default EnterpriseOverview
