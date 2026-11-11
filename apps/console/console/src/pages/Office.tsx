/**
 * 办公室 —— AI 员工的工位墙。
 *
 * 与「我的员工」的分工：
 *   本页：看"现在谁在干活、干到哪、卡在哪"，顺手派一件事；
 *   我的员工：建档、改名、停用、批量雇人这些管理动作。
 *
 * 数据只来自现有接口，缺字段如实写「暂未提供」，不编造：
 *   GET /v1/employees                    员工 + status / last_activity / stats
 *   GET /v1/tasks?status=running         当前任务（按 assignee 匹配员工）
 *   GET /v1/tasks?status=completed       最近完成（兜底 last_activity）
 *   GET /v1/policy/approvals/pending     几件等你确认（无审批权时不请求）
 *
 * 「暂停」不新增员工级状态：只归一显示 employment_status=disabled 与 agent 级
 * paused，并在界面上写明"暂停的是接活，不是删员工"。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from '../lib/api'
import type { EmployeesPayload } from '../lib/contracts'
import type { TasksPayload } from '../lib/taskLedger'
import { tasksPath, formatStamp } from '../lib/taskLedger'
import type { PendingApprovalsResponse } from '../lib/operator'
import {
  assignEmployeeTask,
  employeeCapabilities,
  employeeDisplayName,
  employeeLastActivity,
  employeeRole,
  employeeStatus,
  employeesOf,
} from '../lib/employees'
import type { MyEmployee } from '../lib/employees'
import { Empty, Failure } from '../components/ui'
import './Office.css'

const REFRESH_MS = 30_000

/** 派活抽屉里的员工摘要 + 表单。 */
function stationTasks(employee: MyEmployee, tasks: TasksPayload['items']) {
  const agentIds = new Set((employee.agents ?? []).map((agent) => String(agent.id ?? '')))
  const names = new Set(
    [employee.name, employee.display_name]
      .filter(Boolean)
      .map((value) => String(value)),
  )
  return tasks.filter((task) => {
    const assignee = task.assignee
    if (!assignee) return false
    if (assignee.id && agentIds.has(String(assignee.id))) return true
    if (assignee.name && names.has(String(assignee.name))) return true
    // 直接按员工派活时，后端会写 assignee.type=employee、id=员工名。
    return assignee.type === 'employee' && assignee.id === employee.name
  })
}

function initialOf(employee: MyEmployee): string {
  const label = String(employee.display_name || employee.name || '员')
  return label.slice(0, 1).toUpperCase()
}

function toneClass(tone: string): string {
  return `os-office-pill is-${tone}`
}

export function Office({
  onOpenEmployees,
  canApproveQueue,
}: {
  /** 去「我的员工」做建档、停用这类管理动作。 */
  onOpenEmployees?: () => void
  /** 无审批权时不请求队列，避免把 403 当成"没有待办"。 */
  canApproveQueue?: boolean
}) {
  const employeesApi = useApi<EmployeesPayload>('/v1/employees', REFRESH_MS)
  const runningApi = useApi<TasksPayload>(tasksPath('running', 50), REFRESH_MS)
  const doneApi = useApi<TasksPayload>(tasksPath('completed', 20), REFRESH_MS)
  const approvalsApi = useApi<PendingApprovalsResponse>(
    canApproveQueue ? '/v1/policy/approvals/pending' : null,
    REFRESH_MS,
  )

  const employees = useMemo(() => employeesOf(employeesApi.data), [employeesApi.data])
  const running = useMemo(() => runningApi.data?.items ?? [], [runningApi.data])
  const completed = useMemo(() => doneApi.data?.items ?? [], [doneApi.data])

  const counts = useMemo(() => {
    let working = 0
    let idle = 0
    let error = 0
    let paused = 0
    for (const employee of employees) {
      const { label } = employeeStatus(employee)
      if (label === '执行中') working += 1
      else if (label === '出错') error += 1
      else if (label === '已停用') paused += 1
      else if (label === '空闲' || label === '已完成') idle += 1
    }
    return { working, idle, error, paused }
  }, [employees])

  const pendingCount = canApproveQueue
    ? (approvalsApi.data?.count ?? null)
    : null

  const [openName, setOpenName] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fieldRef = useRef<HTMLTextAreaElement | null>(null)
  const triggerRef = useRef<HTMLElement | null>(null)

  const opened = employees.find((employee) => employee.name === openName) ?? null

  // 打开抽屉后把焦点送进输入框；关闭时回到触发它的工位卡。
  // 依赖 openName 而不是 opened：轮询会换掉员工对象引用，依赖对象会反复清空草稿。
  useEffect(() => {
    if (!openName) return
    setDraft('')
    setNotice(null)
    setError(null)
    const timer = window.setTimeout(() => fieldRef.current?.focus(), 30)
    return () => window.clearTimeout(timer)
  }, [openName])

  const closeDrawer = useCallback(() => {
    setOpenName(null)
    triggerRef.current?.focus()
  }, [])

  // Esc 关闭：抽屉是模态层，键盘用户不能只能靠点右上角。
  useEffect(() => {
    if (!opened) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeDrawer()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [opened, closeDrawer])

  const assign = async () => {
    if (!opened) return
    const instruction = draft.trim()
    if (!instruction) {
      setError('先写一句要它做什么')
      fieldRef.current?.focus()
      return
    }
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await assignEmployeeTask({ employee_name: opened.name, instruction })
      setNotice('已派活，工位状态与当前任务会在下一轮刷新中更新')
      setDraft('')
      employeesApi.reload()
      runningApi.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : '派活失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  const loading = employeesApi.data === null && !employeesApi.error

  return (
    <div className="os-office">
      <div className="os-office-head">
        <p className="os-office-sub">
          每个 AI 员工一张工位。点开可以直接派活；停用与建档去「我的员工」。
        </p>
        <div className="os-office-actions">
          {onOpenEmployees && (
            <button className="os-btn os-btn-ghost" onClick={onOpenEmployees}>
              员工管理
            </button>
          )}
          <button
            className="os-btn os-btn-ghost"
            onClick={() => {
              employeesApi.reload()
              runningApi.reload()
              doneApi.reload()
              approvalsApi.reload()
            }}
          >
            刷新
          </button>
        </div>
      </div>

      <div className="os-office-strip" role="status">
        <div className="os-office-stat">
          <b>{counts.working}</b>
          <span>位在干活</span>
        </div>
        <div className="os-office-stat">
          <b>{counts.idle}</b>
          <span>位待命</span>
        </div>
        <div className="os-office-stat">
          <b>{pendingCount === null ? '—' : pendingCount}</b>
          <span>件等你确认{pendingCount === null ? '（未提供）' : ''}</span>
        </div>
        <div className="os-office-stat">
          <b>{counts.error}</b>
          <span>位出错</span>
        </div>
        {counts.paused > 0 && (
          <div className="os-office-stat">
            <b>{counts.paused}</b>
            <span>位暂停</span>
          </div>
        )}
        <span className="os-office-hint" title="暂停只影响接活，员工本身不会被删除">
          「暂停」是停接活，不是删员工
        </span>
      </div>

      {employeesApi.error && (
        <Failure message={employeesApi.error} onRetry={employeesApi.reload} />
      )}

      {!employeesApi.error && loading && <p className="os-office-loading">正在读取工位…</p>}

      {!employeesApi.error && !loading && employees.length === 0 && (
        <Empty
          title="还没有 AI 员工"
          hint="去「我的员工」创建一个，或从贾维斯直接下目标。"
          action={
            onOpenEmployees ? (
              <button className="os-btn" onClick={onOpenEmployees}>
                去创建员工
              </button>
            ) : undefined
          }
        />
      )}

      <div className="os-office-grid">
        {employees.map((employee) => {
          const status = employeeStatus(employee)
          const assigned = stationTasks(employee, running)
          const current = assigned[0]
          const lastText = employeeLastActivity(employee)
          const lastDone = [...stationTasks(employee, completed)].sort((a, b) =>
            String(b.completed_at ?? '').localeCompare(String(a.completed_at ?? '')),
          )[0]
          return (
            <article
              key={employee.name}
              className="os-station"
              tabIndex={0}
              role="button"
              aria-label={`打开 ${employeeDisplayName(employee)} 的派活面板`}
              onClick={(event) => {
                triggerRef.current = event.currentTarget
                setOpenName(employee.name)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  triggerRef.current = event.currentTarget as HTMLButtonElement
                  setOpenName(employee.name)
                }
              }}
            >
              <div className="os-station-top">
                <span
                  className="os-station-avatar"
                  data-status={status.tone}
                  aria-hidden="true"
                >
                  {initialOf(employee)}
                </span>
                <div className="os-station-id">
                  <strong>{employeeDisplayName(employee)}</strong>
                  <span title={employeeRole(employee)}>{employeeRole(employee)}</span>
                </div>
                <span className={toneClass(status.tone)}>{status.label}</span>
              </div>

              <dl className="os-station-facts">
                <div>
                  <dt>当前在做</dt>
                  <dd>
                    {current ? (
                      <span title={current.title}>{current.title}</span>
                    ) : status.label === '执行中' ? (
                      <span className="os-office-missing">暂未提供（任务未给出归属）</span>
                    ) : (
                      <span className="os-office-missing">暂未提供</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>最近</dt>
                  <dd>
                    {lastText ? (
                      <span title={lastText}>{lastText}</span>
                    ) : lastDone ? (
                      <span title={lastDone.title}>
                        {lastDone.title}
                        {lastDone.completed_at ? ` · ${formatStamp(lastDone.completed_at)}` : ''}
                      </span>
                    ) : (
                      <span className="os-office-missing">暂未提供</span>
                    )}
                  </dd>
                </div>
              </dl>

              <div className="os-station-foot">
                <span>
                  队列 {employee.stats?.task_queue_length ?? 0} · 完成{' '}
                  {employee.stats?.total_tasks_completed ?? 0}
                </span>
                <span className="os-office-open">派活 →</span>
              </div>
            </article>
          )
        })}
      </div>

      {opened && (
        <div className="os-drawer-mask" onClick={closeDrawer}>
          <aside
            className="os-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="office-drawer-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="os-drawer-head">
              <span
                className="os-station-avatar"
                data-status={employeeStatus(opened).tone}
                aria-hidden="true"
              >
                {initialOf(opened)}
              </span>
              <div>
                <h2 id="office-drawer-title">{employeeDisplayName(opened)}</h2>
                <p>{employeeRole(opened)}</p>
              </div>
              <button className="os-drawer-close" onClick={closeDrawer} aria-label="关闭">
                ×
              </button>
            </header>

            <div className="os-drawer-body">
              <div className={toneClass(employeeStatus(opened).tone)}>
                {employeeStatus(opened).label}
              </div>

              <dl className="os-drawer-facts">
                <div>
                  <dt>当前任务</dt>
                  <dd>
                    {stationTasks(opened, running)[0]?.title ?? '暂未提供'}
                  </dd>
                </div>
                <div>
                  <dt>最近活动</dt>
                  <dd>{employeeLastActivity(opened) || '暂未提供'}</dd>
                </div>
                <div>
                  <dt>默认能力</dt>
                  <dd>{employeeCapabilities(opened)}</dd>
                </div>
                <div>
                  <dt>统计</dt>
                  <dd>
                    提交 {opened.stats?.total_tasks_submitted ?? 0} · 完成{' '}
                    {opened.stats?.total_tasks_completed ?? 0} · 失败{' '}
                    {opened.stats?.total_tasks_failed ?? 0} · 队列{' '}
                    {opened.stats?.task_queue_length ?? 0}
                  </dd>
                </div>
              </dl>

              {opened.employment_status === 'disabled' && (
                <p className="os-drawer-note">
                  「暂停」是停接活，不是删员工。恢复后队列继续，不会重建员工；恢复请到「我的员工」。
                </p>
              )}

              {opened.read_only ? (
                <p className="os-drawer-note">这是只读员工，不能从这里派活。</p>
              ) : (
                <div className="os-drawer-assign">
                  <label htmlFor="office-assign">派一件事给它</label>
                  <textarea
                    id="office-assign"
                    ref={fieldRef}
                    rows={3}
                    value={draft}
                    placeholder="例如：把上周的销售数据整理成一页结论"
                    onChange={(event) => setDraft(event.target.value)}
                  />
                  {error && (
                    <p className="os-drawer-error" role="alert">
                      {error}
                    </p>
                  )}
                  {notice && (
                    <p className="os-drawer-notice" role="status">
                      {notice}
                    </p>
                  )}
                  <button
                    className="os-btn"
                    disabled={busy}
                    onClick={assign}
                  >
                    {busy ? '派发中…' : '派活'}
                  </button>
                </div>
              )}

              {onOpenEmployees && (
                <button
                  className="os-link"
                  onClick={() => {
                    closeDrawer()
                    onOpenEmployees()
                  }}
                >
                  在「我的员工」里管理
                </button>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
