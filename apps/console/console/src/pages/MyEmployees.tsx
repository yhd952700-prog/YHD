/**
 * 我的员工 —— 员工名册的创建、调整、停用与单独派活。
 *
 * 与「贾维斯」的分工：
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
import { capabilityLabel, numberText } from '../lib/format'
import {
  agentStatusLabel,
  applyEmployeeImport,
  assignEmployeeTask,
  BATCH_HIRE_LIMIT,
  budgetPercent,
  capabilityRisk,
  catalogHasNoHighRisk,
  clearEmployeeBudget,
  copyEmployee,
  createEmployee,
  createEmployeesBatch,
  deleteEmployee,
  employeeAssignable,
  employeeCapabilities,
  employeeCapabilityRisks,
  employeeDisplayName,
  employeeLastActivity,
  employeeRole,
  employeeStatsText,
  employeeStatus,
  employeesOf,
  exportEmployeeConfig,
  fetchEmployeeBudget,
  fetchEmployeeQuality,
  isSeedEmployee,
  parseEmployeeBatch,
  previewEmployeeImport,
  readableError,
  saveEmployeeBudget,
  updateEmployee,
} from '../lib/employees'
import type {
  BatchHireResult,
  BudgetPeriod,
  EmployeeBudgetPatch,
  EmployeeBudgetView,
  EmployeeQuality,
  ImportApplyResult,
  ImportConflictMode,
  ImportPlan,
  MyEmployee,
} from '../lib/employees'
import { BudgetDenialActions, Card, Empty, Failure, Guard } from '../components/ui'
import { isBudgetDenialMessage } from '../lib/errorMessage'

/** 单页员工数：接口一次返回全量，分页只在渲染层切片。 */
const EMPLOYEE_PAGE_SIZE = 50

function capabilityOptions(payload: EmployeeCapabilitiesPayload | null): Array<{
  id: string
  label: string
  risk?: string
}> {
  return (payload?.capabilities ?? []).map((item) => ({
    id: item.id,
    // 目录里的 name 可能是工具名；只有中文时才直接展示，否则用统一人话标签。
    label:
      item.name && /[\u4e00-\u9fa5]/.test(item.name)
        ? item.name
        : capabilityLabel(item.id),
    // 风险级别原样带下来，由展示层翻成人话；不在这里猜。
    risk: item.risk,
  }))
}

const PERIOD_OPTIONS: Array<{ value: BudgetPeriod; label: string }> = [
  { value: 'day', label: '每天重置' },
  { value: 'week', label: '每周重置' },
  { value: 'month', label: '每月重置' },
]

/** 比率/耗时算不出来时如实显示，不用 0 或 100% 冒充。 */
function percentText(value: number | null): string {
  return value === null ? '算不出来' : `${numberText(value * 100, 1)}%`
}

function durationText(seconds: number | null): string {
  if (seconds === null) return '算不出来'
  if (seconds < 60) return `${numberText(seconds, 1)} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds - minutes * 60)
  if (minutes < 60) return `${minutes} 分 ${rest} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes - hours * 60} 分`
}

const IMPORT_ACTION_LABELS: Record<string, string> = {
  create: '新建',
  rename: '改名新建',
  skip: '跳过',
  error: '无法导入',
}

function importActionLabel(action: string): string {
  return IMPORT_ACTION_LABELS[action] ?? action
}

/**
 * 预算表单 → 请求体：留空 = 该项不限制（传 null），非法输入返回 null 由调用方提示。
 * 不用 0 代替空，否则"没填"会被存成"一次都不许调"。
 */
function parseBudgetDraft(draft: {
  calls: string
  tokens: string
  cost: string
  period: BudgetPeriod
}): EmployeeBudgetPatch | null {
  const number = (text: string): number | null | undefined => {
    const trimmed = text.trim()
    if (!trimmed) return null
    const value = Number(trimmed)
    if (!Number.isFinite(value) || value < 0) return undefined
    return value
  }
  const calls = number(draft.calls)
  const tokens = number(draft.tokens)
  const cost = number(draft.cost)
  if (calls === undefined || tokens === undefined || cost === undefined) return null
  return {
    calls_limit: calls === null ? null : Math.trunc(calls),
    tokens_limit: tokens === null ? null : Math.trunc(tokens),
    cost_limit: cost,
    period: draft.period,
  }
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
  const [batchText, setBatchText] = useState('')
  const [batchResult, setBatchResult] = useState<BatchHireResult | null>(null)
  const [batchError, setBatchError] = useState<string | null>(null)
  const [batchBusy, setBatchBusy] = useState(false)

  // ── 员工补齐能力：复制 / 导出导入 / 质量 / 预算 ──
  // detail 只允许同时展开一行的一个面板，避免一屏全是表单。
  const [detail, setDetail] = useState<{ name: string; kind: 'quality' | 'budget' } | null>(null)
  const [detailBusy, setDetailBusy] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [quality, setQuality] = useState<EmployeeQuality | null>(null)
  const [budget, setBudget] = useState<EmployeeBudgetView | null>(null)
  const [budgetDraft, setBudgetDraft] = useState({
    calls: '',
    tokens: '',
    cost: '',
    period: 'day' as BudgetPeriod,
  })
  const [importText, setImportText] = useState('')
  const [importConflict, setImportConflict] = useState<ImportConflictMode>('skip')
  const [importPlan, setImportPlan] = useState<ImportPlan | null>(null)
  const [importResult, setImportResult] = useState<ImportApplyResult | null>(null)
  const [importBusy, setImportBusy] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)

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

  function submitBatch() {
    const parsed = parseEmployeeBatch(batchText)
    setBatchError(null)
    setBatchResult(null)
    if (parsed.items.length === 0) {
      setBatchError('没有可提交的内容：每行至少要有员工名字。')
      return
    }
    if (parsed.items.length > BATCH_HIRE_LIMIT) {
      setBatchError(`一次最多 ${BATCH_HIRE_LIMIT} 位，请分批粘贴（当前 ${parsed.items.length} 位）。`)
      return
    }
    setBatchBusy(true)
    void createEmployeesBatch(parsed.items)
      .then((result) => {
        setBatchResult(result)
        list.reload()
        if (result.created_count > 0) setBatchText('')
      })
      .catch((err) => setBatchError(readableError(err)))
      .finally(() => setBatchBusy(false))
  }

  const copyAgent = useCallback(
    async (employee: MyEmployee) => {
      if (busy) return
      setBusy(`copy:${employee.name}`)
      setError(null)
      setNotice(null)
      try {
        const result = await copyEmployee(employee.name)
        const warning = result.warnings.length > 0 ? `；注意：${result.warnings.join('；')}` : ''
        setNotice(
          `已复制「${employee.name}」为「${result.name}」，副本默认停用，确认配置后再点启用${warning}`,
        )
        list.reload()
      } catch (err) {
        setError(readableError(err))
      } finally {
        setBusy(null)
      }
    },
    [busy, list],
  )

  const exportAgent = useCallback(
    async (employee: MyEmployee) => {
      if (busy) return
      setBusy(`export:${employee.name}`)
      setError(null)
      setNotice(null)
      try {
        const doc = await exportEmployeeConfig(employee.name)
        // 导出的是配置白名单（提示词/能力/技能），后端已剔除疑似凭据，这里原样下载。
        const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' })
        const url = URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `${employee.name}-employee.json`
        link.click()
        URL.revokeObjectURL(url)
        setNotice(`已导出「${employee.name}」的配置（不含任何密钥）`)
      } catch (err) {
        setError(readableError(err))
      } finally {
        setBusy(null)
      }
    },
    [busy],
  )

  const openDetail = useCallback(
    async (employee: MyEmployee, kind: 'quality' | 'budget') => {
      if (detail?.name === employee.name && detail.kind === kind) {
        setDetail(null)
        return
      }
      setDetail({ name: employee.name, kind })
      setDetailError(null)
      setQuality(null)
      setBudget(null)
      setDetailBusy(true)
      try {
        if (kind === 'quality') {
          setQuality(await fetchEmployeeQuality(employee.name))
        } else {
          const view = await fetchEmployeeBudget(employee.name)
          setBudget(view)
          setBudgetDraft({
            calls: view.policy.calls_limit === null ? '' : String(view.policy.calls_limit),
            tokens: view.policy.tokens_limit === null ? '' : String(view.policy.tokens_limit),
            cost: view.policy.cost_limit === null ? '' : String(view.policy.cost_limit),
            period: view.policy.period,
          })
        }
      } catch (err) {
        setDetailError(readableError(err))
      } finally {
        setDetailBusy(false)
      }
    },
    [detail],
  )

  const saveBudget = useCallback(
    async (name: string, resetUsage = false) => {
      const parsed = parseBudgetDraft(budgetDraft)
      if (!parsed) {
        setDetailError('上限只能填非负数字；留空表示该项不限制。')
        return
      }
      setDetailBusy(true)
      setDetailError(null)
      try {
        const view = await saveEmployeeBudget(name, { ...parsed, reset_usage: resetUsage })
        setBudget(view)
        if (view.exceeded) {
          setNotice(`已保存「${name}」的预算，当前已超限：下一次派活会被 402 拦下。`)
        } else {
          setNotice(resetUsage ? `已保存「${name}」的预算并清零本期用量` : `已保存「${name}」的预算`)
        }
      } catch (err) {
        setDetailError(readableError(err))
      } finally {
        setDetailBusy(false)
      }
    },
    [budgetDraft],
  )

  const removeBudget = useCallback(async (name: string) => {
    setDetailBusy(true)
    setDetailError(null)
    try {
      await clearEmployeeBudget(name)
      setBudget(await fetchEmployeeBudget(name))
      setBudgetDraft({ calls: '', tokens: '', cost: '', period: 'day' })
      setNotice(`已移除「${name}」的预算（回到不限制）`)
    } catch (err) {
      setDetailError(readableError(err))
    } finally {
      setDetailBusy(false)
    }
  }, [])

  async function previewImport() {
    setImportError(null)
    setImportResult(null)
    if (!importText.trim()) {
      setImportError('请先粘贴导出的员工配置（JSON）。')
      return
    }
    setImportBusy(true)
    try {
      setImportPlan(await previewEmployeeImport({ config: importText, onConflict: importConflict }))
    } catch (err) {
      setImportPlan(null)
      setImportError(readableError(err))
    } finally {
      setImportBusy(false)
    }
  }

  async function confirmImport() {
    if (!importPlan) return
    setImportBusy(true)
    setImportError(null)
    try {
      const result = await applyEmployeeImport({
        config: importText,
        onConflict: importConflict,
      })
      setImportResult(result)
      setImportPlan(null)
      setImportText('')
      list.reload()
    } catch (err) {
      setImportError(readableError(err))
    } finally {
      setImportBusy(false)
    }
  }

  const renderDetail = (employee: MyEmployee) => {
    if (!detail || detail.name !== employee.name) return null
    if (detailBusy) return <div className="os-emp-meta">正在读取…</div>
    if (detailError) {
      return (
        <div className="os-alert" role="alert">
          {detailError}
        </div>
      )
    }

    if (detail.kind === 'quality') {
      if (!quality) return null
      return (
        <div className="os-note" role="status">
          <div>
            基于历史任务记录：样本 {quality.sample} 个 · 终态{' '}
            {quality.terminal} 个 · 执行中 {quality.in_progress} 个
          </div>
          <dl className="os-kv">
            <dt>完成率</dt>
            <dd>{percentText(quality.completion_rate)}</dd>
            <dt>失败率</dt>
            <dd>{percentText(quality.failure_rate)}</dd>
            <dt>平均耗时</dt>
            <dd>
              {durationText(quality.avg_duration_seconds)}
              {quality.duration_sample > 0 ? `（${quality.duration_sample} 个任务有完整起止时间）` : ''}
            </dd>
            <dt>人工返工</dt>
            <dd>
              {quality.manual_rework_count === null ? '算不出来' : `${quality.manual_rework_count} 次`}
              {quality.manual_retry_count !== null && quality.takeover_count !== null
                ? `（人工重试 ${quality.manual_retry_count} 次 · 人工接管 ${quality.takeover_count} 次）`
                : ''}
            </dd>
            <dt>完成 / 失败 / 取消</dt>
            <dd>
              {quality.completed} / {quality.failed} / {quality.cancelled}
            </dd>
            <dt>最近一次任务</dt>
            <dd>
              {quality.last_task_at
                ? new Date(quality.last_task_at).toLocaleString('zh-CN', { hour12: false })
                : '没有记录'}
            </dd>
          </dl>
          {quality.notes.map((note, index) => (
            <div key={`quality-note-${index}`} className="os-hint">
              {note}
            </div>
          ))}
        </div>
      )
    }

    if (!budget) return null
    return (
      <div className="os-note" role="status">
        {budget.exceeded && (
          <div className="os-alert" role="alert" style={{ marginBottom: 8 }}>
            {budget.violations.map((item) => item.message).join('；')}
          </div>
        )}
        <div>
          周期：{budget.period_label}（{budget.window}）· 下次重置：
          {budget.next_reset_at
            ? new Date(budget.next_reset_at).toLocaleString('zh-CN', { hour12: false })
            : '未知'}
          {budget.enforced ? ' · 超限会直接拦下派活（402）' : ' · 当前没有生效的上限'}
        </div>
        <dl className="os-kv">
          <dt>本期调用</dt>
          <dd>
            {budget.usage.calls}
            {budget.policy.calls_limit === null ? ' 次（不限）' : ` / ${budget.policy.calls_limit} 次`}
            {budgetPercent(budget.usage.calls, budget.policy.calls_limit) !== null
              ? `（已用 ${budgetPercent(budget.usage.calls, budget.policy.calls_limit)}%）`
              : ''}
          </dd>
          <dt>本期模型用量</dt>
          <dd>
            {budget.usage.tokens}
            {budget.policy.tokens_limit === null
              ? '（不限）'
              : ` / ${budget.policy.tokens_limit}`}
          </dd>
          <dt>本期费用</dt>
          <dd>
            {numberText(budget.usage.cost, 4)}
            {budget.policy.cost_limit === null ? '（不限）' : ` / ${budget.policy.cost_limit}`}
            {budget.usage.cost_complete ? '' : '（部分模型无价格，费用未完全核算）'}
          </dd>
          <dt>剩余额度</dt>
          <dd>
            调用 {budget.remaining.calls === null ? '不限' : `${budget.remaining.calls} 次`} · 模型用量{' '}
            {budget.remaining.tokens === null ? '不限' : budget.remaining.tokens} · 费用{' '}
            {budget.remaining.cost === null ? '不限' : numberText(budget.remaining.cost, 4)}
          </dd>
        </dl>
        <div className="os-emp-create" style={{ marginTop: 6 }}>
          <div className="os-field">
            <label htmlFor={`budget-calls-${employee.name}`}>调用次数上限</label>
            <div className="os-input">
              <input
                id={`budget-calls-${employee.name}`}
                inputMode="numeric"
                placeholder="留空 = 不限制"
                value={budgetDraft.calls}
                onChange={(event) => setBudgetDraft({ ...budgetDraft, calls: event.target.value })}
              />
            </div>
          </div>
          <div className="os-field">
            <label htmlFor={`budget-tokens-${employee.name}`}>模型用量上限</label>
            <div className="os-input">
              <input
                id={`budget-tokens-${employee.name}`}
                inputMode="numeric"
                placeholder="留空 = 不限制"
                value={budgetDraft.tokens}
                onChange={(event) => setBudgetDraft({ ...budgetDraft, tokens: event.target.value })}
              />
            </div>
          </div>
          <div className="os-field">
            <label htmlFor={`budget-cost-${employee.name}`}>金额上限</label>
            <div className="os-input">
              <input
                id={`budget-cost-${employee.name}`}
                inputMode="decimal"
                placeholder="留空 = 不限制"
                value={budgetDraft.cost}
                onChange={(event) => setBudgetDraft({ ...budgetDraft, cost: event.target.value })}
              />
            </div>
          </div>
          <div className="os-field">
            <label htmlFor={`budget-period-${employee.name}`}>重置周期</label>
            <div className="os-input">
              <select
                id={`budget-period-${employee.name}`}
                value={budgetDraft.period}
                onChange={(event) =>
                  setBudgetDraft({ ...budgetDraft, period: event.target.value as BudgetPeriod })
                }
              >
                {PERIOD_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
        <div className="os-emp-actions">
          <button
            className="os-btn"
            style={{ width: 'auto' }}
            disabled={detailBusy || employee.read_only}
            onClick={() => void saveBudget(employee.name)}
          >
            {detailBusy ? '保存中…' : '保存预算'}
          </button>
          <button
            className="os-btn os-btn-ghost"
            style={{ width: 'auto' }}
            disabled={detailBusy || employee.read_only}
            onClick={() => void saveBudget(employee.name, true)}
          >
            保存并清零本期用量
          </button>
          {budget.configured !== false && (
            <button
              className="os-btn os-btn-ghost"
              style={{ width: 'auto' }}
              disabled={detailBusy || employee.read_only}
              onClick={() => void removeBudget(employee.name)}
            >
              移除预算
            </button>
          )}
          <button
            className="os-btn os-btn-ghost"
            style={{ width: 'auto' }}
            onClick={() => void openDetail(employee, 'budget')}
          >
            收起
          </button>
        </div>
        {employee.read_only && (
          <div className="os-hint">只读员工只能查看预算，不能修改。</div>
        )}
        {budget.notes.map((note, index) => (
          <div key={`budget-note-${index}`} className="os-hint">
            {note}
          </div>
        ))}
      </div>
    )
  }

  const renderEmployee = (employee: MyEmployee) => {
    const status = employeeStatus(employee)
    const seed = isSeedEmployee(employee)
    const assignable = !employee.read_only && employeeAssignable(employee)
    const isRenaming = renaming === employee.name
    const isAssigning = assigning === employee.name
    const failed = Number(employee.stats?.total_tasks_failed ?? 0)
    const lastActivity = employeeLastActivity(employee)
    const riskText = employeeCapabilityRisks(employee, catalogApi.data?.capabilities)

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
          {riskText && (
            // 能力只列名字看不出危险度：这里把每项的实际风险级别摆到同一行，
            // 一眼判断它能不能删文件、能不能联网。
            <div className="os-emp-meta">能力风险：{riskText}</div>
          )}
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

          {/* 四项补齐能力：复制、导出、质量评估对可见员工都开放；
              预算写入在只读员工上由面板内的按钮禁用拦住。 */}
          <div className="os-emp-actions">
            <button
              className="os-btn os-btn-ghost"
              style={{ width: 'auto' }}
              disabled={busy !== null}
              onClick={() => void copyAgent(employee)}
            >
              {busy === `copy:${employee.name}` ? '复制中…' : '复制'}
            </button>
            <button
              className="os-btn os-btn-ghost"
              style={{ width: 'auto' }}
              disabled={busy !== null}
              onClick={() => void exportAgent(employee)}
            >
              {busy === `export:${employee.name}` ? '导出中…' : '导出配置'}
            </button>
            <button
              className="os-btn os-btn-ghost"
              style={{ width: 'auto' }}
              aria-expanded={detail?.name === employee.name && detail.kind === 'quality'}
              onClick={() => void openDetail(employee, 'quality')}
            >
              质量评估
            </button>
            <button
              className="os-btn os-btn-ghost"
              style={{ width: 'auto' }}
              aria-expanded={detail?.name === employee.name && detail.kind === 'budget'}
              onClick={() => void openDetail(employee, 'budget')}
            >
              预算
            </button>
          </div>
          {renderDetail(employee)}
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
            让管家替你拆解目标，去「贾维斯」。
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
                {catalog.map((item) => {
                  const risk = capabilityRisk(item.risk)
                  return (
                    <span
                      key={item.id}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
                    >
                      <label className="os-emp-meta os-check-hit" style={{ display: 'inline-flex', gap: 6 }}>
                        <input
                          type="checkbox"
                          checked={draft.capabilities.includes(item.id)}
                          onChange={() => toggleCapability(item.id)}
                        />
                        {item.label}
                      </label>
                      {risk && (
                        // 选之前就能看见危险度：高风险项不会被当成普通开关随手勾上。
                        <span
                          className="os-badge"
                          data-tone={risk.tone}
                          title={`这一项的真实风险级别：${risk.label}`}
                        >
                          {risk.label}
                        </span>
                      )}
                    </span>
                  )
                })}
              </div>
            )}
            {catalogHasNoHighRisk(catalogApi.data?.capabilities) && (
              // 这句只在数据支持时才出现：目录里真出现高风险能力，它会自己消失。
              <p className="os-hint">
                当前能力目录里没有高风险或极高风险的能力：{catalog.length} 项全部为低风险或中风险。
              </p>
            )}
          </div>

          <div className="os-model-form-foot">
            <button className="os-btn" type="submit" disabled={busy !== null}>
              {busy === 'create' ? '创建中…' : '创建员工'}
            </button>
          </div>
        </form>
      </Card>

      <Card
        title="批量创建"
        sub="一行一位：名字 | 职责 | 默认能力（后两段可省略，能力用逗号分隔）"
        span={12}
      >
        <div className="os-field">
          <label htmlFor="emp-batch">粘贴员工清单</label>
          <div className="os-input">
            <textarea
              id="emp-batch"
              rows={6}
              maxLength={20000}
              placeholder={'小周 | 整理会议纪要 | 写作,搜索\n小陈 | 跟进客户回访'}
              value={batchText}
              onChange={(event) => setBatchText(event.target.value)}
            />
          </div>
          <span className="os-hint">
            重名不会覆盖，会如实回报为「已存在」；单次最多 {BATCH_HIRE_LIMIT} 位。
          </span>
        </div>
        <div className="os-model-form-foot">
          <button className="os-btn" type="button" disabled={batchBusy} onClick={submitBatch}>
            {batchBusy ? '提交中…' : '批量创建'}
          </button>
        </div>
        {batchError && (
          <div className="os-alert" role="alert">
            {batchError}
          </div>
        )}
        {batchResult && (
          <div className="os-note" role="status" aria-live="polite">
            <div>
              共提交 {batchResult.requested_count} 位：新建 {batchResult.created_count} 位 ·
              已存在 {batchResult.existing_count} 位 · 失败 {batchResult.failed_count} 位
            </div>
            {batchResult.failed.length > 0 && (
              <ul style={{ margin: '6px 0 0 18px' }}>
                {batchResult.failed.map((item, index) => (
                  <li key={`${item.name ?? 'item'}-${index}`}>
                    {item.name ? `${item.name}：` : ''}
                    {item.message || '没有创建成功，原因未返回'}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>

      <Card
        title="导入员工配置"
        sub="粘贴导出的配置文件内容：先预览、确认后才保存；导入的员工一律默认停用"
        span={12}
      >
        <div className="os-field">
          <label htmlFor="emp-import">员工配置 JSON</label>
          <div className="os-input">
            <textarea
              id="emp-import"
              rows={6}
              maxLength={200000}
              placeholder="把导出的配置文件内容整段粘贴到这里"
              value={importText}
              onChange={(event) => {
                setImportText(event.target.value)
                setImportPlan(null)
                setImportResult(null)
              }}
            />
          </div>
          <span className="os-hint">
            只导入配置（提示词、执行个体、默认能力、技能绑定）；不含任务、记忆，也不含任何密钥。
          </span>
        </div>
        <div className="os-field">
          <label htmlFor="emp-import-conflict">同名冲突处理</label>
          <div className="os-input">
            <select
              id="emp-import-conflict"
              value={importConflict}
              onChange={(event) => {
                setImportConflict(event.target.value as ImportConflictMode)
                setImportPlan(null)
              }}
            >
              <option value="skip">跳过，不改动现有员工</option>
              <option value="rename">自动改名，加 -import 后缀</option>
            </select>
          </div>
        </div>
        <div className="os-model-form-foot">
          <button
            className="os-btn"
            type="button"
            disabled={importBusy}
            onClick={() => void previewImport()}
          >
            {importBusy ? '处理中…' : '预览（不会保存）'}
          </button>
          {importPlan && (
            <button
              className="os-btn"
              type="button"
              disabled={importBusy}
              onClick={() => void confirmImport()}
            >
              确认导入
            </button>
          )}
          {importPlan && (
            <button
              className="os-btn os-btn-ghost"
              type="button"
              onClick={() => setImportPlan(null)}
            >
              取消
            </button>
          )}
        </div>
        {importError && (
          <div className="os-alert" role="alert">
            {importError}
          </div>
        )}
        {importPlan && (
          <div className="os-note" role="status">
            <div>
              预览（尚未落盘）：新建 {importPlan.summary.create} 位 · 改名{' '}
              {importPlan.summary.rename} 位 · 跳过 {importPlan.summary.skip} 位 · 错误{' '}
              {importPlan.summary.error} 条
            </div>
            <div className="os-table-wrap">
              <table className="os-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>原名</th>
                    <th>最终名字</th>
                    <th>动作</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {importPlan.plan.map((row) => (
                    <tr key={`import-row-${row.index}`}>
                      <td>{row.index}</td>
                      <td>{row.requested_name}</td>
                      <td>{row.final_name}</td>
                      <td>{importActionLabel(row.action)}</td>
                      <td>
                        {[row.reason, ...row.errors, ...row.warnings].filter(Boolean).join('；') ||
                          '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="os-hint">
              确认时服务端会重新校验一遍；导入出来的员工默认停用，需要手动启用才会接活。
            </div>
          </div>
        )}
        {importResult && (
          <div className="os-note" role="status" aria-live="polite">
            <div>
              导入完成：新建 {importResult.created_count} 位 · 跳过{' '}
              {importResult.skipped_count} 位 · 失败 {importResult.failed_count} 位
            </div>
            {importResult.results.length > 0 && (
              <ul style={{ margin: '6px 0 0 18px' }}>
                {importResult.results.map((row, index) => (
                  <li key={`import-result-${row.name}-${index}`}>
                    {row.name || '（未命名）'}：{row.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
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
