/**
 * 我的员工 —— 员工名册的创建、调整、停用与单独派活。
 *
 * 与「鎏灏管家」的分工：
 *   管家：你下目标，它拆解分派，给结论；
 *   本页：你自己管理员工，或把一件具体的事直接派给某个员工。
 *
 * 端点全部是后端真实实现（`src/gateway/ai_management.py`）：
 *   GET    /v1/employees                 当前账号可见的员工（按 owner 过滤）
 *   GET    /v1/employees/capabilities    可选默认能力清单
 *   POST   /v1/employees                 创建
 *   PATCH  /v1/employees/{name}          改名 / 改职责 / 停用启用
 *   DELETE /v1/employees/{name}          删除（默认员工不可删）
 *   POST   /v1/goals/assign              指名派活
 *
 * 归属与权限由后端强制：别人的员工统一 404，停用后指派 409。`read_only=true`
 * 的员工只渲染查看信息，不渲染编辑/删除/派活按钮——不给一个点下去才报错的按钮。
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useApi } from '../lib/api'
import type { EmployeeCapabilitiesPayload, EmployeesPayload } from '../lib/contracts'
import { capabilityLabel } from '../lib/format'
import {
  agentStatusLabel,
  assignEmployeeTask,
  createEmployee,
  deleteEmployee,
  employeeAssignable,
  employeeCapabilities,
  employeeDisplayName,
  employeeLastActivity,
  employeeRole,
  employeeStatsText,
  employeeStatus,
  employeesOf,
  isSeedEmployee,
  readableError,
  updateEmployee,
} from '../lib/employees'
import type { MyEmployee } from '../lib/employees'
import { BudgetDenialActions, Card, Empty, Failure, Guard } from '../components/ui'
import { isBudgetDenialMessage } from '../lib/errorMessage'

/** 单页员工数：接口一次返回全量，分页只在渲染层切片。 */
const EMPLOYEE_PAGE_SIZE = 50

function capabilityOptions(payload: EmployeeCapabilitiesPayload | null): Array<{
  id: string
  label: string
}> {
  return (payload?.capabilities ?? []).map((item) => ({
    id: item.id,
    // 目录里的 name 可能是工具名；只有中文时才直接展示，否则用统一人话标签。
    label:
      item.name && /[\u4e00-\u9fa5]/.test(item.name)
        ? item.name
        : capabilityLabel(item.id),
  }))
}

export function MyEmployees({
  query,
  onOpenUsage,
  onConfigureModel,
}: {
  query: string
  /** 402 预算拒绝的可点出口：用量页 / 模型管理。 */
  onOpenUsage?: () => void
  onConfigureModel?: () => void
}) {
  const list = useApi<EmployeesPayload>('/v1/employees', 0)
  const catalogApi = useApi<EmployeeCapabilitiesPayload>('/v1/employees/capabilities', 0)
  const employees = employeesOf(list.data)
  const catalog = useMemo(() => capabilityOptions(catalogApi.data), [catalogApi.data])
  const catalogLabels = useMemo(
    () => catalog.map((item) => ({ id: item.id, name: item.label })),
    [catalog],
  )

  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nameError, setNameError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [draft, setDraft] = useState({ name: '', role: '', capabilities: [] as string[] })
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [assigning, setAssigning] = useState<string | null>(null)
  const [assignValue, setAssignValue] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [pageIndex, setPageIndex] = useState(0)

  // 派活时焦点接力：展开后进输入框，提交/取消后回"单独指派一个任务"。
  // 不用 autoFocus，是因为同一个员工行会被重渲染，只有当前名字的引用有效。
  const assignInputRefs = useRef<Record<string, HTMLInputElement | null>>({})
  const assignTriggerRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const previousAssigning = useRef<string | null>(null)
  // 创建成功后提交按钮会因 disabled 把焦点丢给 body；把焦点交给结果提示。
  const createNoticeRef = useRef<HTMLDivElement | null>(null)
  const focusNoticeAfterCreate = useRef(false)
  useEffect(() => {
    if (!focusNoticeAfterCreate.current) return
    if (error) {
      focusNoticeAfterCreate.current = false
      return
    }
    if (!notice) return
    focusNoticeAfterCreate.current = false
    createNoticeRef.current?.focus()
  }, [error, notice])
  useEffect(() => {
    const previous = previousAssigning.current
    previousAssigning.current = assigning
    if (assigning) {
      assignInputRefs.current[assigning]?.focus()
      return
    }
    if (previous) assignTriggerRefs.current[previous]?.focus()
  }, [assigning])

  const need = query.trim().toLowerCase()
  const filtered = need
    ? employees.filter(
        (employee) =>
          employee.name.toLowerCase().includes(need) ||
          employeeRole(employee).toLowerCase().includes(need) ||
          employeeCapabilities(employee, catalogLabels).toLowerCase().includes(need),
      )
    : employees

  const pageCount = Math.max(1, Math.ceil(filtered.length / EMPLOYEE_PAGE_SIZE))
  const safePage = Math.min(pageIndex, pageCount - 1)
  const pageEmployees = filtered.slice(
    safePage * EMPLOYEE_PAGE_SIZE,
    (safePage + 1) * EMPLOYEE_PAGE_SIZE,
  )

  // 搜索/筛选变了就回第 1 页；否则会停在旧页，看起来像「没有这个人」。
  useEffect(() => {
    setPageIndex(0)
  }, [need])

  const runAction = useCallback(
    async (key: string, action: () => Promise<void>, success: string): Promise<boolean> => {
      if (busy) return false
      setBusy(key)
      setError(null)
      setNotice(null)
      try {
        await action()
        setNotice(success)
        list.reload()
        return true
      } catch (err) {
        setError(readableError(err))
        return false
      } finally {
        setBusy(null)
      }
    },
    [busy, list],
  )

  function toggleCapability(id: string) {
    setDraft((current) => ({
      ...current,
      capabilities: current.capabilities.includes(id)
        ? current.capabilities.filter((value) => value !== id)
        : [...current.capabilities, id],
    }))
  }

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = draft.name.trim()
    if (!name) {
      setNameError('请先填写员工名字')
      setError('请先填写员工名字')
      // 错误同时由 role="alert" 播报；焦点回到出错的输入框，键盘用户不用再找。
      document.getElementById('emp-name')?.focus()
      return
    }
    setNameError(null)
    focusNoticeAfterCreate.current = true
    void runAction(
      'create',
      () =>
        createEmployee({
          name,
          role_description: draft.role.trim(),
          default_capabilities: draft.capabilities,
        }).then(() => undefined),
      `已创建员工「${name}」`,
    ).then((ok) => {
      if (ok) setDraft({ name: '', role: '', capabilities: [] })
    })
  }

  const renderEmployee = (employee: MyEmployee) => {
    const status = employeeStatus(employee)
    const seed = isSeedEmployee(employee)
    const assignable = !employee.read_only && employeeAssignable(employee)
    const isRenaming = renaming === employee.name
    const isAssigning = assigning === employee.name
    const failed = Number(employee.stats?.total_tasks_failed ?? 0)
    const lastActivity = employeeLastActivity(employee)

    return (
      <div className="os-row" key={employee.name}>
        <div className="os-emp-row" style={{ flex: 1 }}>
          <div className="os-emp-head">
            <span className="os-emp-name">{employeeDisplayName(employee)}</span>
            <span className="os-badge" data-tone={status.tone}>
              {status.label}
            </span>
            {employee.read_only && (
              <span className="os-badge" data-tone="muted">
                只读
              </span>
            )}
            {seed && (
              <span className="os-badge" data-tone="muted">
                系统默认
              </span>
            )}
          </div>
          <div className="os-emp-meta">
            职责：{employeeRole(employee)} · 默认能力：
            {employeeCapabilities(employee, catalogLabels)}
          </div>
          <div className="os-emp-meta">
            {employeeStatsText(employee)} · {employee.agent_count} 位执行个体
            {failed > 0 ? ' · 有失败任务，可在「目标与任务」看原因' : ''}
          </div>
          {lastActivity && (
            <div className="os-emp-meta">最近一次：{lastActivity}</div>
          )}

          {employee.agents.length > 0 && (
            <div className="os-emp-meta">
              个体状态：
              {employee.agents.map((agent, index) => {
                return (
                  <Fragment key={agent.id || `agent-${index}`}>
                    {index > 0 && ' / '}
                    <span>
                      {`执行个体 ${index + 1}·${agentStatusLabel(agent)}`}
                    </span>
                  </Fragment>
                )
              })}
            </div>
          )}

          {employee.read_only ? (
            <div className="os-emp-meta">
              只读：这位员工归属其他账号，这里只能查看；如需调整请由创建它的账号操作。
            </div>
          ) : seed ? (
            <div className="os-emp-meta">
              系统默认员工：服务依赖它执行内部任务，不能改名、停用或删除。
            </div>
          ) : (
            <>
              {isRenaming ? (
                <div className="os-emp-actions">
                  <div className="os-input">
                    <input
                      aria-label="新的员工名字"
                      value={renameValue}
                      maxLength={128}
                      onChange={(event) => setRenameValue(event.target.value)}
                    />
                  </div>
                  <button
                    className="os-btn"
                    disabled={busy !== null || !renameValue.trim()}
                    onClick={() => {
                      const next = renameValue.trim()
                      void runAction(
                        `rename:${employee.name}`,
                        () => updateEmployee(employee.name, { name: next }).then(() => undefined),
                        `已把「${employee.name}」改名为「${next}」`,
                      ).then((ok) => {
                        if (ok) setRenaming(null)
                      })
                    }}
                  >
                    {busy === `rename:${employee.name}` ? '保存中…' : '保存'}
                  </button>
                  <button className="os-btn os-btn-ghost" onClick={() => setRenaming(null)}>
                    取消
                  </button>
                </div>
              ) : (
                <div className="os-emp-actions">
                  <button
                    className="os-btn os-btn-ghost"
                    style={{ width: 'auto' }}
                    disabled={busy !== null}
                    onClick={() => {
                      setRenaming(employee.name)
                      setRenameValue(employee.name)
                      setConfirmDelete(null)
                    }}
                  >
                    改名
                  </button>
                  <button
                    className="os-btn os-btn-ghost"
                    style={{ width: 'auto' }}
                    disabled={busy !== null}
                    onClick={() => {
                      const disabled =
                        String(employee.employment_status).toLowerCase() === 'disabled'
                      void runAction(
                        `status:${employee.name}`,
                        () =>
                          updateEmployee(employee.name, {
                            employment_status: disabled ? 'active' : 'disabled',
                          }).then(() => undefined),
                        disabled
                          ? `已启用「${employee.name}」，现在可以接受派活`
                          : `已停用「${employee.name}」，停用期间不能接受派活`,
                      )
                    }}
                  >
                    {busy === `status:${employee.name}`
                      ? '处理中…'
                      : String(employee.employment_status).toLowerCase() === 'disabled'
                        ? '启用'
                        : '停用'}
                  </button>
                  {confirmDelete === employee.name ? (
                    <>
                      <span className="os-emp-meta">确认删除？</span>
                      <button
                        className="os-btn os-btn-ghost"
                        style={{ width: 'auto', color: 'var(--os-danger)' }}
                        disabled={busy !== null}
                        onClick={() => {
                          void runAction(
                            `delete:${employee.name}`,
                            () => deleteEmployee(employee.name),
                            `已删除员工「${employee.name}」`,
                          ).then((ok) => {
                            if (ok) setConfirmDelete(null)
                          })
                        }}
                      >
                        {busy === `delete:${employee.name}` ? '删除中…' : '确认删除'}
                      </button>
                      <button className="os-btn os-btn-ghost" onClick={() => setConfirmDelete(null)}>
                        取消
                      </button>
                    </>
                  ) : (
                    <button
                      className="os-btn os-btn-ghost"
                      style={{ width: 'auto' }}
                      disabled={busy !== null}
                      onClick={() => {
                        setConfirmDelete(employee.name)
                        setRenaming(null)
                      }}
                    >
                      删除
                    </button>
                  )}
                </div>
              )}

              {isAssigning ? (
                <div className="os-emp-actions">
                  <div className="os-input">
                    <input
                      ref={(element) => {
                        assignInputRefs.current[employee.name] = element
                      }}
                      aria-label={`派给「${employee.name}」的任务`}
                      placeholder="例如：把昨天的会议纪要整理成一页"
                      value={assignValue}
                      maxLength={2000}
                      onChange={(event) => setAssignValue(event.target.value)}
                    />
                  </div>
                  <button
                    className="os-btn"
                    disabled={busy !== null || !assignValue.trim()}
                    onClick={() => {
                      const instruction = assignValue.trim()
                      let goalId = ''
                      void runAction(
                        `assign:${employee.name}`,
                        () =>
                          assignEmployeeTask({
                            employee_name: employee.name,
                            instruction,
                          }).then((goal) => {
                            goalId = String(goal.goal_id ?? '')
                          }),
                        `已派给「${employee.name}」：${instruction}`,
                      ).then((ok) => {
                        if (!ok) return
                        setAssigning(null)
                        setAssignValue('')
                        if (goalId) {
                          setNotice(
                            `已派给「${employee.name}」（目标 ${goalId}）；到「目标与任务」看执行结果、员工归属与耗时。`,
                          )
                        }
                      })
                    }}
                  >
                    {busy === `assign:${employee.name}` ? '派发中…' : '派给它'}
                  </button>
                  <button className="os-btn os-btn-ghost" onClick={() => setAssigning(null)}>
                    取消
                  </button>
                </div>
              ) : (
                <div className="os-emp-actions">
                  {assignable ? (
                    <button
                      ref={(element) => {
                        assignTriggerRefs.current[employee.name] = element
                      }}
                      className="os-btn os-btn-ghost"
                      style={{ width: 'auto' }}
                      disabled={busy !== null}
                      onClick={() => {
                        setAssigning(employee.name)
                        setAssignValue('')
                      }}
                    >
                      单独指派一个任务
                    </button>
                  ) : (
                    <span className="os-emp-meta">
                      当前不能接活
                      {String(employee.employment_status).toLowerCase() === 'disabled'
                        ? '：这位员工已停用，先启用再派。'
                        : '：它的执行个体都处于暂停状态，先恢复再派。'}
                    </span>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="os-grid">
      <div className="os-span-12">
        <div className="os-position" role="note">
          <span>
            <strong>我的员工</strong>：在这里创建、调整、停用，或把一件具体的事直接派给它。
            让管家替你拆解目标，去「鎏灏管家」。
          </span>
        </div>
      </div>

      <Card title="创建员工" sub="名字必填；职责与默认能力可稍后再补" span={12}>
        {list.error && (
          <p className="os-note" role="status">
            名册还没读到：暂时无法判断是否重名、是否已停用。下面仍然可以填写，但保存前建议先到「员工名册」里点重试，把名册读回来。
          </p>
        )}
        <form className="os-operator-form" onSubmit={submitCreate} noValidate>
          {/* 关掉浏览器原生校验气泡：它不会被稳定朗读、失焦即消失。
              必填校验走 submitCreate 的 role="alert" + aria-invalid 路径。 */}
          <div className="os-emp-create">
            <div className="os-field">
              <label htmlFor="emp-name">名字（必填）</label>
              <div className="os-input">
                <input
                  id="emp-name"
                  required
                  maxLength={128}
                  placeholder="例如：小周"
                  value={draft.name}
                  aria-invalid={nameError ? true : undefined}
                  aria-describedby={nameError ? 'emp-create-error' : undefined}
                  onChange={(event) => {
                    setDraft({ ...draft, name: event.target.value })
                    if (nameError) setNameError(null)
                  }}
                />
              </div>
            </div>
            <div className="os-field">
              <label htmlFor="emp-role">职责</label>
              <div className="os-input">
                <input
                  id="emp-role"
                  maxLength={2000}
                  placeholder="例如：整理会议纪要与周报"
                  value={draft.role}
                  onChange={(event) => setDraft({ ...draft, role: event.target.value })}
                />
              </div>
            </div>
          </div>

          <div className="os-field" style={{ marginTop: 10 }}>
            <span>默认能力（可多选，只能选已登记的能力）</span>
            {catalogApi.error ? (
              <p className="os-kb-error">能力清单不可用：{catalogApi.error}</p>
            ) : catalogApi.loading ? (
              <p className="os-hint">正在读取可用能力…</p>
            ) : catalog.length === 0 ? (
              <p className="os-hint">暂时没有可用能力；可以先创建员工，之后再补。</p>
            ) : (
              <div className="os-emp-actions" style={{ marginTop: 6 }}>
                {catalog.map((item) => (
                  <label key={item.id} className="os-emp-meta" style={{ display: 'inline-flex', gap: 6 }}>
                    <input
                      type="checkbox"
                      checked={draft.capabilities.includes(item.id)}
                      onChange={() => toggleCapability(item.id)}
                    />
                    {item.label}
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="os-model-form-foot">
            <button className="os-btn" type="submit" disabled={busy !== null}>
              {busy === 'create' ? '创建中…' : '创建员工'}
            </button>
          </div>
        </form>
      </Card>

      {error && (
        <div className="os-span-12">
          <div className="os-alert" role="alert" id={nameError ? 'emp-create-error' : undefined}>
            <span>{error}</span>
            {isBudgetDenialMessage(error) && onOpenUsage && onConfigureModel && (
              <BudgetDenialActions
                onOpenUsage={onOpenUsage}
                onConfigureModel={onConfigureModel}
              />
            )}
          </div>
        </div>
      )}
      {notice && !error && (
        <div className="os-span-12">
          <div className="os-note" role="status" aria-live="polite" tabIndex={-1} ref={createNoticeRef}>
            <span>{notice}</span>
          </div>
        </div>
      )}

      <Card title="员工名册" sub="名字、职责、默认能力与当前状态" span={12}>
        {list.error ? (
          <Failure message={list.error} onRetry={list.reload} />
        ) : list.loading && employees.length === 0 ? (
          <div className="os-emp-meta">正在读取员工名册…</div>
        ) : employees.length === 0 ? (
          <Guard
            loading={false}
            error={null}
            data={employees}
            isEmpty={() => true}
            emptyTitle="还没有员工"
            emptyHint="先在上面创建第一个员工：给它起个名字，写清职责（例如“整理周报”），再选默认能力。"
          >
            {() => null}
          </Guard>
        ) : filtered.length === 0 ? (
          <Empty title={`没有员工匹配「${query}」`} hint="试试名字或职责里的其他关键词。" />
        ) : (
          <>
            <div className="os-list">{pageEmployees.map(renderEmployee)}</div>
            {filtered.length > EMPLOYEE_PAGE_SIZE && (
              <div
                style={{
                  display: 'flex',
                  gap: 8,
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  marginTop: 8,
                }}
              >
                <button
                  className="os-btn os-btn-ghost"
                  style={{ width: 'auto' }}
                  disabled={safePage === 0}
                  onClick={() => setPageIndex(safePage - 1)}
                >
                  上一页
                </button>
                <span className="os-hint">
                  第 {safePage + 1} / {pageCount} 页 · 共 {filtered.length} 位
                </span>
                <button
                  className="os-btn os-btn-ghost"
                  style={{ width: 'auto' }}
                  disabled={safePage >= pageCount - 1}
                  onClick={() => setPageIndex(safePage + 1)}
                >
                  下一页
                </button>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
