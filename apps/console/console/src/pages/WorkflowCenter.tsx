/**
 * 工作流中心 —— 定义列表 + 运行实例 + 可视化编辑器。
 *
 * 诚实边界
 * --------
 * * 运行状态、每步输入输出、耗时、失败原因全部来自后端（`/v1/workflow/runs*`），
 *   取不到就说「暂未提供」，不按状态猜。
 * * 审批通过/拒绝**不在这个页面做**：这里只给出口「到待办审批处理」，
 *   真正的驱动在 `ApprovalQueue.tsx` → `/v1/approvals/*`。
 * * 流程定义的可执行语义由服务端校验；保存失败时原样显示后端原因。
 *
 * 页面登记
 * --------
 * 导航 key 是 `workflow`（见 `lib/nav.ts`），`App.tsx` 用
 * `import { WorkflowCenter } from './pages/WorkflowCenter'` 挂载。
 */

import { useCallback, useMemo, useRef, useState } from 'react'
import './Workflow.css'
import { ApiError, apiFetch, useApi } from '../lib/api'
import { humanDuration, relativeTime } from '../lib/format'
import { Card, Empty, Guard } from '../components/ui'
import {
  WorkflowEditor,
  blankDefinition,
  definitionFromRecord,
  NODE_TYPE_LABELS,
} from './WorkflowEditor'
import type { WorkflowDefinition, WorkflowDefinitionRecord } from './WorkflowEditor'

interface WorkflowRun {
  run_id: string
  def_id: string
  def_version: number
  name: string
  status: string
  outcome: string
  trigger_type: string
  submitted_by: string
  current_node_id: string | null
  error: string | null
  error_code: string | null
  created_at?: number
  created_at_text?: string | null
  started_at?: number
  started_at_text?: string | null
  finished_at?: number
  finished_at_text?: string | null
  node_count: number
}

interface WorkflowStep {
  step_id: string
  node_id: string
  node_type: string
  name: string
  seq: number
  status: string
  attempt: number
  inputs: Record<string, unknown>
  outputs: Record<string, unknown>
  error: string | null
  error_code: string | null
  duration_ms: number | null
  started_at_text?: string | null
  finished_at_text?: string | null
}

interface RunApproval {
  approval_id: string
  title: string
  operation: string
  status: string
  submitted_by: string
  decided_by: string | null
  comment: string | null
  expires_at_text?: string | null
}

interface RunDetail {
  run: WorkflowRun
  steps: WorkflowStep[]
  approvals: RunApproval[]
}

interface WorkflowSummary {
  definitions: number
  runs: Record<string, number>
  pending_approvals: number
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

const APPROVAL_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  approved: '已通过',
  rejected: '已拒绝',
  expired: '已过期',
  cancelled: '已取消',
}

const RUN_STATUS_ORDER = ['pending', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled']

function statusTone(status: string): string {
  if (status === 'completed' || status === 'approved') return 'ok'
  if (status === 'failed' || status === 'rejected' || status === 'expired') return 'danger'
  if (status === 'cancelled') return 'muted'
  return 'warn'
}

function statusLabel(map: Record<string, string>, status: string): string {
  return map[status] ?? status ?? '未知'
}

function actionErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return '当前账号无权执行这个操作，请换用有权限的账号。'
    if (error.status === 404) return '流程或运行不存在，可能已被删除；刷新后再看。'
    if (error.status === 409) return error.message || '这个操作与当前状态冲突，未执行。'
    return error.message || '操作没有完成，请稍后重试。'
  }
  if (error instanceof Error && error.message) return error.message
  return '操作没有完成，请稍后重试。'
}

function triggerText(trigger: { type: string; every_seconds?: number; at?: string; event?: string }): string {
  if (trigger.type === 'interval') return `每 ${humanDuration(trigger.every_seconds ?? 0)}`
  if (trigger.type === 'daily') return `每天 ${trigger.at ?? '—'}`
  if (trigger.type === 'event') return `事件 ${trigger.event ?? '—'}`
  return '手动'
}

function runDuration(run: WorkflowRun): string {
  if (!run.started_at) return '—'
  const end = run.finished_at ?? Date.now() / 1000
  return humanDuration(Math.max(0, end - run.started_at))
}

function stepDuration(step: WorkflowStep): string {
  if (step.duration_ms === null || step.duration_ms === undefined) {
    return step.status === 'running' ? '进行中' : '—'
  }
  return humanDuration(step.duration_ms / 1000)
}

function jsonText(value: unknown): string {
  if (value === null || value === undefined) return '（空）'
  if (typeof value === 'object' && Object.keys(value as object).length === 0) return '（空）'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function downloadJson(filename: string, payload: unknown): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

interface Feedback {
  tone: 'ok' | 'danger'
  text: string
}

/**
 * 页面登记：导航 key 必须与 `lib/nav.ts` 的 `TASKS_NAV` 条目一致（都是 `workflow`）。
 * 放在页面文件里而不是 nav.ts，是为了让导航路由与页面实现不会各改各的。
 */
export const WORKFLOW_CENTER_PAGE = {
  key: 'workflow',
  title: '工作流',
  navLabel: '工作流',
  sub: '画流程、点运行、看每一步的输入输出与耗时',
}

type EditorState =
  | { mode: 'create'; draft: WorkflowDefinition }
  | { mode: 'edit'; defId: string; draft: WorkflowDefinition }

export function WorkflowCenter({ query }: { query: string }) {
  const definitions = useApi<{ definitions: WorkflowDefinitionRecord[] }>(
    '/v1/workflow/definitions?include_disabled=true&limit=200',
    15_000,
  )
  const summary = useApi<WorkflowSummary>('/v1/workflow/summary', 15_000)
  const [runStatus, setRunStatus] = useState('')
  const runs = useApi<{ runs: WorkflowRun[] }>(
    `/v1/workflow/runs?limit=40${runStatus ? `&status=${runStatus}` : ''}`,
    8_000,
  )

  const [editor, setEditor] = useState<EditorState | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [cancelConfirm, setCancelConfirm] = useState<string | null>(null)
  const [runTarget, setRunTarget] = useState<WorkflowDefinitionRecord | null>(null)
  const [runInstruction, setRunInstruction] = useState('')
  const [runExtra, setRunExtra] = useState('{}')
  const [runError, setRunError] = useState<string | null>(null)
  const [runBusy, setRunBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)

  const detail = useApi<RunDetail>(selectedRunId ? `/v1/workflow/runs/${selectedRunId}` : null, 3_000)

  const need = query.trim().toLowerCase()
  const defList = useMemo(() => {
    const list = definitions.data?.definitions ?? []
    if (!need) return list
    return list.filter((item) =>
      [item.name, item.description, item.def_id, ...item.node_types.map((type) => NODE_TYPE_LABELS[type] ?? type)]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(need)),
    )
  }, [definitions.data, need])

  const runList = useMemo(() => {
    const list = runs.data?.runs ?? []
    if (!need) return list
    return list.filter((item) =>
      [item.name, item.run_id, item.def_id, item.submitted_by, statusLabel(RUN_STATUS_LABELS, item.status)]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(need)),
    )
  }, [runs.data, need])

  const reloadAll = useCallback(() => {
    definitions.reload()
    summary.reload()
    runs.reload()
  }, [definitions, summary, runs])

  const openCreate = () => {
    setEditor({ mode: 'create', draft: blankDefinition() })
    setSaveError(null)
    setFeedback(null)
  }

  const openEdit = (record: WorkflowDefinitionRecord) => {
    setEditor({ mode: 'edit', defId: record.def_id, draft: definitionFromRecord(record) })
    setSaveError(null)
    setFeedback(null)
  }

  const save = async (draft: WorkflowDefinition) => {
    if (!editor || saving) return
    if (!draft.name.trim()) {
      setSaveError('流程名称不能为空')
      return
    }
    setSaving(true)
    setSaveError(null)
    try {
      if (editor.mode === 'create') {
        await apiFetch<{ definition: WorkflowDefinitionRecord }>('/v1/workflow/definitions', {
          method: 'POST',
          body: JSON.stringify(draft),
        })
        setFeedback({ tone: 'ok', text: `已创建流程「${draft.name}」。` })
      } else {
        await apiFetch<{ definition: WorkflowDefinitionRecord }>(
          `/v1/workflow/definitions/${editor.defId}`,
          { method: 'PUT', body: JSON.stringify(draft) },
        )
        setFeedback({ tone: 'ok', text: `已保存流程「${draft.name}」（版本号已递增）。` })
      }
      setEditor(null)
      reloadAll()
    } catch (error) {
      setSaveError(actionErrorText(error))
    } finally {
      setSaving(false)
    }
  }

  const toggleEnabled = async (record: WorkflowDefinitionRecord) => {
    setBusyId(record.def_id)
    setFeedback(null)
    try {
      const result = await apiFetch<{ definition: WorkflowDefinitionRecord }>(
        `/v1/workflow/definitions/${record.def_id}/enable`,
        { method: 'POST', body: JSON.stringify({ enabled: !record.enabled }) },
      )
      setFeedback({
        tone: 'ok',
        text: `流程「${record.name}」已${result.definition.enabled ? '启用' : '停用'}${
          result.definition.enabled ? '。' : '：定时与事件触发器不再启动新运行，已有运行不受影响。'
        }`,
      })
      reloadAll()
    } catch (error) {
      setFeedback({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusyId(null)
    }
  }

  const duplicate = async (record: WorkflowDefinitionRecord) => {
    setBusyId(record.def_id)
    setFeedback(null)
    try {
      const result = await apiFetch<{ definition: WorkflowDefinitionRecord }>(
        `/v1/workflow/definitions/${record.def_id}/duplicate`,
        { method: 'POST', body: JSON.stringify({}) },
      )
      setFeedback({ tone: 'ok', text: `已复制为「${result.definition.name}」。` })
      reloadAll()
    } catch (error) {
      setFeedback({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusyId(null)
    }
  }

  const exportDefinition = async (record: WorkflowDefinitionRecord) => {
    setBusyId(record.def_id)
    setFeedback(null)
    try {
      const payload = await apiFetch<Record<string, unknown>>(
        `/v1/workflow/definitions/${record.def_id}/export`,
      )
      const safeName = record.name.replace(/[\\/:*?"<>|\s]+/g, '-').slice(0, 60) || record.def_id
      downloadJson(`workflow-${safeName}.json`, payload)
      setFeedback({ tone: 'ok', text: `已导出「${record.name}」为 JSON 文件。` })
    } catch (error) {
      setFeedback({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusyId(null)
    }
  }

  const importFile = async (file: File) => {
    setFeedback(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(await file.text())
    } catch {
      setFeedback({ tone: 'danger', text: '这个文件不是合法 JSON，未导入。' })
      return
    }
    try {
      const result = await apiFetch<{ definition: WorkflowDefinitionRecord }>('/v1/workflow/import', {
        method: 'POST',
        body: JSON.stringify(parsed),
      })
      setFeedback({ tone: 'ok', text: `已导入流程「${result.definition.name}」。` })
      reloadAll()
    } catch (error) {
      setFeedback({ tone: 'danger', text: `导入失败：${actionErrorText(error)}` })
    }
  }

  const openRun = (record: WorkflowDefinitionRecord) => {
    setRunTarget(record)
    setRunInstruction('')
    setRunExtra('{}')
    setRunError(null)
    setFeedback(null)
  }

  const submitRun = async () => {
    if (!runTarget || runBusy) return
    let extra: Record<string, unknown>
    try {
      const parsed = JSON.parse(runExtra || '{}')
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('not an object')
      }
      extra = parsed as Record<string, unknown>
    } catch {
      setRunError('额外变量必须是 JSON 对象，例如 {"topic": "周报"}；留空等于 {}。')
      return
    }
    const inputs: Record<string, unknown> = { ...extra }
    if (runInstruction.trim()) inputs.instruction = runInstruction.trim()
    setRunBusy(true)
    setRunError(null)
    try {
      const result = await apiFetch<{ run: WorkflowRun }>(
        `/v1/workflow/definitions/${runTarget.def_id}/run`,
        { method: 'POST', body: JSON.stringify({ inputs, trigger_payload: {} }) },
      )
      const run = result.run
      setRunTarget(null)
      setSelectedRunId(run.run_id)
      setFeedback({
        tone: run.status === 'failed' ? 'danger' : 'ok',
        text:
          run.status === 'waiting_approval'
            ? `运行 ${run.run_id} 已启动，现在卡在人工审批：请到「待办审批」里通过或拒绝。`
            : `运行 ${run.run_id} 已启动，当前状态：${statusLabel(RUN_STATUS_LABELS, run.status)}${
                run.error ? `；原因：${run.error}` : ''
              }。`,
      })
      reloadAll()
    } catch (error) {
      setRunError(actionErrorText(error))
    } finally {
      setRunBusy(false)
    }
  }

  const cancelRun = async (runId: string) => {
    setBusyId(runId)
    setFeedback(null)
    try {
      await apiFetch<{ run: WorkflowRun }>(`/v1/workflow/runs/${runId}/cancel`, { method: 'POST' })
      setFeedback({ tone: 'ok', text: `运行 ${runId} 已取消；未处理的审批会一并失效。` })
      setCancelConfirm(null)
      reloadAll()
      if (selectedRunId === runId) detail.reload()
    } catch (error) {
      setFeedback({ tone: 'danger', text: actionErrorText(error) })
    } finally {
      setBusyId(null)
    }
  }

  const summaryRuns = summary.data?.runs ?? {}

  return (
    <div className="os-grid">
      <Card
        title="概览"
        sub="只统计你能看到的流程（每 15 秒刷新）"
        span={12}
        action={
          <div className="wf-head-actions">
            <button className="os-btn" type="button" onClick={openCreate}>
              新建流程
            </button>
            <button className="os-btn os-btn-ghost" type="button" onClick={() => fileRef.current?.click()}>
              导入 JSON
            </button>
            <input
              ref={fileRef}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0]
                event.target.value = ''
                if (file) void importFile(file)
              }}
            />
          </div>
        }
      >
        {summary.error ? (
          <p className="wf-error" role="alert">
            {summary.error}
          </p>
        ) : (
          <>
            <div className="wf-counts">
              <span className="wf-count">
                <span className="wf-count-label">流程定义</span>
                <strong>{summary.data?.definitions ?? '—'}</strong>
              </span>
              <span className="wf-count">
                <span className="wf-count-label">待办审批</span>
                <strong className={summary.data?.pending_approvals ? 'wf-warn' : ''}>
                  {summary.data?.pending_approvals ?? '—'}
                </strong>
              </span>
              {RUN_STATUS_ORDER.map((status) => (
                <span key={status} className="wf-count">
                  <span className="wf-count-label">{RUN_STATUS_LABELS[status]}</span>
                  <strong>{summaryRuns[status] ?? 0}</strong>
                </span>
              ))}
            </div>
            {summary.data?.pending_approvals ? (
              <p className="os-hint">
                有 {summary.data.pending_approvals} 条流程运行在等人工审批 —— 到左侧「任务 → 待办审批」处理。
              </p>
            ) : null}
          </>
        )}
        {feedback && (
          <p
            className={feedback.tone === 'ok' ? 'wf-feedback wf-feedback-ok' : 'wf-feedback wf-feedback-error'}
            role={feedback.tone === 'ok' ? 'status' : 'alert'}
          >
            {feedback.text}
          </p>
        )}
      </Card>

      {editor && (
        <Card
          title={editor.mode === 'create' ? '新建流程' : `编辑流程 · ${editor.defId}`}
          sub="节点、连线与条件都会保存成 JSON；保存后即可运行、导出或复制"
          span={12}
        >
          <WorkflowEditor
            key={editor.mode === 'edit' ? `edit-${editor.defId}` : 'create'}
            initial={editor.draft}
            saving={saving}
            error={saveError}
            onSave={(draft) => void save(draft)}
            onCancel={() => {
              setEditor(null)
              setSaveError(null)
            }}
          />
        </Card>
      )}

      <Card
        title="流程定义"
        sub="可编辑、复制、导出、停用；停用后不再启动新运行"
        span={12}
        action={
          <button className="os-btn os-btn-ghost" type="button" onClick={definitions.reload}>
            刷新
          </button>
        }
      >
        {runTarget && (
          <div className="wf-run-form" role="dialog" aria-label="启动运行">
            <strong>运行「{runTarget.name}」</strong>
            <p className="os-hint">
              流程里用 <code>{'{{input.instruction}}'}</code> 的节点会拿到下面的「本次指令」；
              额外变量会以 <code>{'{{input.键名}}'}</code> 注入。
            </p>
            <label className="wf-field">
              本次指令（可留空）
              <div className="os-input">
                <input
                  value={runInstruction}
                  maxLength={2000}
                  placeholder="例如：把本周的客户反馈整理成一页周报"
                  onChange={(event) => setRunInstruction(event.target.value)}
                />
              </div>
            </label>
            <label className="wf-field">
              额外输入变量（JSON 对象）
              <textarea
                className="wf-textarea wf-mono"
                rows={3}
                value={runExtra}
                onChange={(event) => setRunExtra(event.target.value)}
              />
            </label>
            {runError && (
              <p className="wf-error" role="alert">
                {runError}
              </p>
            )}
            <div className="wf-row-actions">
              <button className="os-btn" type="button" disabled={runBusy} onClick={() => void submitRun()}>
                {runBusy ? '启动中…' : '确认运行'}
              </button>
              <button
                className="os-btn os-btn-ghost"
                type="button"
                disabled={runBusy}
                onClick={() => setRunTarget(null)}
              >
                取消
              </button>
            </div>
          </div>
        )}

        {defList.length === 0 && !definitions.loading && !definitions.error ? (
          <Empty
            title={need ? '没有匹配的流程' : '还没有流程'}
            hint={
              need
                ? '换个关键词，或清空搜索框。'
                : '点右上角「新建流程」，或从下方模板一键创建「提交 → 审批 → 执行 → 归档」。'
            }
          />
        ) : (
          <Guard loading={definitions.loading} error={definitions.error} data={definitions.data} onRetry={definitions.reload}>
            {() => (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>流程</th>
                      <th>状态</th>
                      <th>版本</th>
                      <th>节点 / 连线</th>
                      <th>触发</th>
                      <th>更新时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {defList.map((record) => (
                      <tr key={record.def_id}>
                        <td>
                          <span className="wf-def-name">{record.name}</span>
                          {record.description && <div className="wf-sub">{record.description}</div>}
                          <div className="wf-sub">{record.def_id}</div>
                        </td>
                        <td>
                          <span className="os-badge" data-tone={record.enabled ? 'ok' : 'muted'}>
                            {record.enabled ? '启用中' : '已停用'}
                          </span>
                        </td>
                        <td>v{record.version}</td>
                        <td>
                          {record.node_count} 节点 / {record.edge_count} 连线
                        </td>
                        <td className="wf-triggers">
                          {record.triggers.length === 0
                            ? '手动'
                            : record.triggers.map((trigger, index) => (
                                <span key={`${trigger.type}-${index}`} className="wf-trigger-tag">
                                  {triggerText(trigger)}
                                </span>
                              ))}
                        </td>
                        <td>{record.updated_at_text ?? relativeTime(record.updated_at ?? 0)}</td>
                        <td>
                          <div className="wf-row-actions">
                            <button
                              className="os-link"
                              type="button"
                              disabled={!record.enabled || busyId === record.def_id}
                              title={record.enabled ? '按当前定义启动一次运行' : '流程已停用，先启用再运行'}
                              onClick={() => openRun(record)}
                            >
                              运行
                            </button>
                            <button className="os-link" type="button" onClick={() => openEdit(record)}>
                              编辑
                            </button>
                            <button
                              className="os-link"
                              type="button"
                              disabled={busyId === record.def_id}
                              onClick={() => void duplicate(record)}
                            >
                              复制
                            </button>
                            <button
                              className="os-link"
                              type="button"
                              disabled={busyId === record.def_id}
                              onClick={() => void exportDefinition(record)}
                            >
                              导出
                            </button>
                            <button
                              className="os-link"
                              type="button"
                              disabled={busyId === record.def_id}
                              onClick={() => void toggleEnabled(record)}
                            >
                              {record.enabled ? '停用' : '启用'}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Guard>
        )}
      </Card>

      <Card
        title="运行实例"
        sub="每 8 秒刷新；点一行看每一步的输入、输出、耗时与失败原因"
        span={selectedRunId ? 7 : 12}
        action={
          selectedRunId ? (
            <button className="os-btn" type="button" onClick={() => setSelectedRunId(null)}>
              收起详情
            </button>
          ) : undefined
        }
      >
        <div className="wf-tabs" role="group" aria-label="按状态筛选运行">
          <button
            type="button"
            className={`wf-tab${runStatus === '' ? ' wf-tab-active' : ''}`}
            aria-pressed={runStatus === ''}
            onClick={() => setRunStatus('')}
          >
            全部
          </button>
          {RUN_STATUS_ORDER.map((status) => (
            <button
              key={status}
              type="button"
              className={`wf-tab${runStatus === status ? ' wf-tab-active' : ''}`}
              aria-pressed={runStatus === status}
              onClick={() => setRunStatus(status)}
            >
              {RUN_STATUS_LABELS[status]}
            </button>
          ))}
        </div>

        {runList.length === 0 && !runs.loading && !runs.error ? (
          <Empty
            title={runStatus ? '这个状态下没有运行' : '还没有运行记录'}
            hint="在流程定义里点「运行」就会产生一条；定时与事件触发也会自动产生。"
          />
        ) : (
          <Guard loading={runs.loading} error={runs.error} data={runs.data} onRetry={runs.reload}>
            {() => (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>运行</th>
                      <th>状态</th>
                      <th>触发</th>
                      <th>提交人</th>
                      <th>耗时</th>
                      <th>创建时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runList.map((run) => (
                      <tr
                        key={run.run_id}
                        className="os-clickable"
                        onClick={() => setSelectedRunId(run.run_id)}
                      >
                        <td>
                          <span className="wf-def-name">{run.name || run.run_id}</span>
                          <div className="wf-sub">{run.run_id}</div>
                          {run.error && <div className="wf-error">{run.error}</div>}
                        </td>
                        <td>
                          <span className="os-badge" data-tone={statusTone(run.status)}>
                            {statusLabel(RUN_STATUS_LABELS, run.status)}
                          </span>
                        </td>
                        <td>{run.trigger_type === 'manual' ? '手动' : run.trigger_type}</td>
                        <td>{run.submitted_by || '—'}</td>
                        <td>{runDuration(run)}</td>
                        <td>{run.created_at_text ?? relativeTime(run.created_at ?? 0)}</td>
                        <td>
                          <div className="wf-row-actions">
                            <button
                              className="os-link"
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation()
                                setSelectedRunId(run.run_id)
                              }}
                            >
                              详情
                            </button>
                            {(run.status === 'pending' ||
                              run.status === 'running' ||
                              run.status === 'waiting_approval') && (
                              <button
                                className="os-link"
                                type="button"
                                disabled={busyId === run.run_id}
                                onClick={(event) => {
                                  event.stopPropagation()
                                  setCancelConfirm(run.run_id)
                                }}
                              >
                                取消
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Guard>
        )}

        {cancelConfirm && (
          <div className="wf-run-form" role="dialog" aria-label="取消运行">
            <strong>确认取消运行 {cancelConfirm}？</strong>
            <p className="os-hint">
              取消会立即终止这条运行；它名下未处理的审批会一并失效，不能撤销。
            </p>
            <div className="wf-row-actions">
              <button
                className="os-btn"
                type="button"
                disabled={busyId === cancelConfirm}
                onClick={() => void cancelRun(cancelConfirm)}
              >
                {busyId === cancelConfirm ? '取消中…' : '确认取消运行'}
              </button>
              <button
                className="os-btn os-btn-ghost"
                type="button"
                disabled={busyId === cancelConfirm}
                onClick={() => setCancelConfirm(null)}
              >
                返回
              </button>
            </div>
          </div>
        )}
      </Card>

      {selectedRunId && (
        <Card title={`运行详情 · ${selectedRunId}`} sub="每 3 秒刷新；步骤按执行顺序列出" span={5}>
          <Guard
            loading={detail.loading}
            error={detail.error}
            data={detail.data}
            onRetry={detail.reload}
          >
            {(data) => (
              <>
                <dl className="os-kv">
                  <dt>流程</dt>
                  <dd>
                    {data.run.name} · v{data.run.def_version}
                  </dd>
                  <dt>状态</dt>
                  <dd>
                    <span className="os-badge" data-tone={statusTone(data.run.status)}>
                      {statusLabel(RUN_STATUS_LABELS, data.run.status)}
                    </span>
                    {data.run.outcome && <span className="wf-sub"> 结果：{data.run.outcome}</span>}
                  </dd>
                  <dt>当前节点</dt>
                  <dd>{data.run.current_node_id || '—'}</dd>
                  <dt>提交 / 触发</dt>
                  <dd>
                    {data.run.submitted_by || '—'} · {data.run.trigger_type}
                  </dd>
                  <dt>开始 / 结束</dt>
                  <dd>
                    {data.run.started_at_text ?? '—'} · {data.run.finished_at_text ?? '—'}
                  </dd>
                  <dt>耗时</dt>
                  <dd>{runDuration(data.run)}</dd>
                  {data.run.error && (
                    <>
                      <dt>失败原因</dt>
                      <dd className="wf-error">
                        {data.run.error}
                        {data.run.error_code ? `（${data.run.error_code}）` : ''}
                      </dd>
                    </>
                  )}
                </dl>

                <h3 className="os-detail-title">步骤（{data.steps.length}）</h3>
                {data.steps.length === 0 ? (
                  <p className="os-hint">这条运行还没有步骤记录。</p>
                ) : (
                  <ol className="wf-steps">
                    {data.steps.map((step, index) => (
                      <li key={step.step_id || `${step.node_id}-${index}`} className={`wf-step wf-step-${step.status}`}>
                        <div className="wf-step-head">
                          <span className="wf-step-seq">第 {step.seq || index + 1} 步</span>
                          <span className="wf-step-name">{step.name || step.node_id}</span>
                          <span className="os-badge" data-tone={statusTone(step.status)}>
                            {statusLabel(STEP_STATUS_LABELS, step.status)}
                          </span>
                        </div>
                        <div className="wf-step-meta">
                          <span>{NODE_TYPE_LABELS[step.node_type] ?? step.node_type}</span>
                          <span>尝试 {step.attempt}</span>
                          <span>耗时 {stepDuration(step)}</span>
                          <span>{step.node_id}</span>
                        </div>
                        {step.error && (
                          <div className="wf-step-error">
                            {step.error}
                            {step.error_code ? `（${step.error_code}）` : ''}
                          </div>
                        )}
                        <details className="wf-io">
                          <summary>输入 / 输出</summary>
                          <div className="wf-io-grid">
                            <div>
                              <span className="wf-io-label">输入</span>
                              <pre className="wf-pre">{jsonText(step.inputs)}</pre>
                            </div>
                            <div>
                              <span className="wf-io-label">输出</span>
                              <pre className="wf-pre">{jsonText(step.outputs)}</pre>
                            </div>
                          </div>
                        </details>
                      </li>
                    ))}
                  </ol>
                )}

                <h3 className="os-detail-title">审批（{data.approvals.length}）</h3>
                {data.approvals.length === 0 ? (
                  <p className="os-hint">这条运行没有审批节点记录。</p>
                ) : (
                  <ul className="wf-approval-list">
                    {data.approvals.map((approval) => (
                      <li key={approval.approval_id}>
                        <div className="wf-step-head">
                          <span className="wf-step-name">{approval.title || approval.operation || approval.approval_id}</span>
                          <span className="os-badge" data-tone={statusTone(approval.status)}>
                            {statusLabel(APPROVAL_STATUS_LABELS, approval.status)}
                          </span>
                        </div>
                        <div className="wf-step-meta">
                          <span>提交 {approval.submitted_by || '—'}</span>
                          <span>审批人 {approval.decided_by || '待处理'}</span>
                          <span>失效 {approval.expires_at_text ?? '不过期'}</span>
                        </div>
                        {approval.comment && <p className="wf-sub">意见：{approval.comment}</p>}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </Guard>
        </Card>
      )}
    </div>
  )
}
