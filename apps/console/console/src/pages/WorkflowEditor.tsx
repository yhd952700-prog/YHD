/**
 * 工作流可视化编辑器 —— 节点/连线/条件/并行/超时/重试/审批，全部落到一份 JSON。
 *
 * 设计取向
 * --------
 * 1. **编辑器只产出数据**：这里不执行任何流程，保存时把 draft 交给
 *    `POST/PUT /v1/workflow/definitions`。校验以服务端为准（`definition.py`），
 *    前端只做"别把空 id 发出去"这类显然的拦截，避免两套规则分叉。
 * 2. **画布是真实可拖的**：节点位置写回 `node.position`，保存后下次打开还在原处；
 *    连线用 SVG 贝塞尔绘制，条件摘要直接标在线上。
 * 3. **拖拽不用第三方库**：pointer 事件 + window 监听，避免为一个页面引入依赖。
 *
 * 这个文件同时导出定义相关的 TypeScript 类型，`WorkflowCenter.tsx` 直接复用，
 * 保证两边看到的是同一份结构。
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

// ── 数据结构（与后端 definition.py 对齐）────────────────────────

export interface WorkflowNodeConfig {
  instruction?: string
  employee_name?: string
  scope?: string
  project_id?: string
  wait?: boolean
  timeout_seconds?: number
  retry?: { max_attempts?: number; backoff_seconds?: number }
  title?: string
  operation?: string
  reason?: string
  approvers?: string[]
  message?: string
  level?: string
  channel?: string
  note?: string
  mode?: string
}

export interface WorkflowNode {
  id: string
  type: string
  name: string
  config: WorkflowNodeConfig
  position: { x: number; y: number }
}

export interface WorkflowCondition {
  field: string
  op: string
  value?: unknown
}

export interface WorkflowEdge {
  id: string
  from: string
  to: string
  condition: WorkflowCondition | null
}

export interface WorkflowTrigger {
  type: string
  every_seconds?: number
  at?: string
  event?: string
  filters?: Record<string, string>
}

export interface WorkflowDefinition {
  schema_version?: number
  name: string
  description: string
  enabled: boolean
  variables?: Record<string, unknown>
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  triggers: WorkflowTrigger[]
}

/** 列表接口返回的定义摘要（含完整节点/连线，足够直接进编辑器）。 */
export interface WorkflowDefinitionRecord extends WorkflowDefinition {
  def_id: string
  version: number
  owner_account_id: string
  created_by: string
  created_at?: number
  created_at_text?: string | null
  updated_at?: number
  updated_at_text?: string | null
  node_count: number
  edge_count: number
  node_types: string[]
}

export const NODE_TYPE_LABELS: Record<string, string> = {
  start: '开始',
  end: '结束',
  task: '创建任务',
  agent: 'Agent 执行',
  approval: '人工审批',
  review: '结果审核',
  notify: '通知',
  archive: '归档',
  parallel: '并行分支',
  join: '汇聚',
}

const CONDITION_OPS = ['eq', 'ne', 'in', 'contains', 'truthy', 'falsy'] as const

const CONDITION_OP_LABELS: Record<string, string> = {
  eq: '等于',
  ne: '不等于',
  in: '属于列表',
  contains: '包含',
  truthy: '为真',
  falsy: '为假',
}

const TRIGGER_TYPE_LABELS: Record<string, string> = {
  manual: '手动',
  interval: '每隔一段时间',
  daily: '每天定时',
  event: '事件触发',
}

const NODE_W = 176
const NODE_H = 72
const CANVAS_MIN_H = 420

function defaultConfig(type: string): WorkflowNodeConfig {
  if (type === 'task' || type === 'agent') {
    return {
      instruction: '',
      employee_name: '',
      scope: 'L1',
      project_id: '',
      wait: type === 'agent',
      timeout_seconds: 600,
      retry: { max_attempts: 0, backoff_seconds: 5 },
    }
  }
  if (type === 'approval' || type === 'review') {
    return { title: '', operation: '', reason: '', approvers: [], timeout_seconds: 3600 }
  }
  if (type === 'notify') return { message: '', level: 'info', channel: 'event' }
  if (type === 'archive') return { note: '' }
  if (type === 'join') return { mode: 'all' }
  return {}
}

/** 新建一个空流程：开始 → 结束，立刻可保存，也能直接加节点。 */
export function blankDefinition(): WorkflowDefinition {
  return {
    schema_version: 1,
    name: '',
    description: '',
    enabled: true,
    variables: {},
    nodes: [
      { id: 'start', type: 'start', name: '开始', config: {}, position: { x: 80, y: 60 } },
      { id: 'end', type: 'end', name: '结束', config: {}, position: { x: 360, y: 60 } },
    ],
    edges: [{ id: 'e1', from: 'start', to: 'end', condition: null }],
    triggers: [{ type: 'manual' }],
  }
}

function toDefinition(record: WorkflowDefinition): WorkflowDefinition {
  // 只取定义字段，剥掉 def_id/version 这类存储元信息，避免保存时把它们写回定义。
  return {
    schema_version: record.schema_version ?? 1,
    name: record.name,
    description: record.description ?? '',
    enabled: record.enabled,
    variables: record.variables ?? {},
    nodes: record.nodes.map((node) => ({
      ...node,
      config: { ...(node.config ?? {}) },
      position: { ...node.position },
    })),
    edges: record.edges.map((edge) => ({ ...edge, condition: edge.condition ?? null })),
    triggers: record.triggers.length > 0 ? record.triggers.map((item) => ({ ...item })) : [{ type: 'manual' }],
  }
}

export function definitionFromRecord(record: WorkflowDefinitionRecord): WorkflowDefinition {
  return toDefinition(record)
}

function nextNodeId(nodes: WorkflowNode[]): string {
  let index = nodes.length + 1
  const used = new Set(nodes.map((node) => node.id))
  while (used.has(`n${index}`)) index += 1
  return `n${index}`
}

function nextEdgeId(edges: WorkflowEdge[]): string {
  let index = edges.length + 1
  const used = new Set(edges.map((edge) => edge.id))
  while (used.has(`e${index}`)) index += 1
  return `e${index}`
}

function conditionText(condition: WorkflowCondition | null): string {
  if (!condition) return ''
  const op = CONDITION_OP_LABELS[condition.op] ?? condition.op
  if (condition.op === 'truthy' || condition.op === 'falsy') {
    return `${condition.field} ${op}`
  }
  return `${condition.field} ${op} ${String(condition.value ?? '')}`
}

function numOrZero(text: string): number {
  const value = Number(text)
  return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0
}

export interface WorkflowEditorProps {
  initial: WorkflowDefinition
  /** 保存中（按钮禁用 + 文案），由父组件控制。 */
  saving: boolean
  /** 服务端校验失败的原样原因；编辑器不自己发明错误文案。 */
  error: string | null
  onSave: (definition: WorkflowDefinition) => void
  onCancel: () => void
}

export function WorkflowEditor({ initial, saving, error, onSave, onCancel }: WorkflowEditorProps) {
  const [draft, setDraft] = useState<WorkflowDefinition>(() => toDefinition(initial))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [linkFrom, setLinkFrom] = useState<string | null>(null)
  const [editingEdgeId, setEditingEdgeId] = useState<string | null>(null)
  const [dragging, setDragging] = useState<string | null>(null)

  const canvasRef = useRef<HTMLDivElement | null>(null)
  const dragOffset = useRef({ x: 0, y: 0 })

  const nodes = draft.nodes
  const byId = useMemo(() => {
    const map: Record<string, WorkflowNode> = {}
    for (const node of nodes) map[node.id] = node
    return map
  }, [nodes])
  const selected = selectedId ? byId[selectedId] : undefined

  // 拖拽：pointermove/up 挂在 window 上，指针移出画布也能继续拖、松手能落位。
  useEffect(() => {
    if (!dragging) return
    const move = (event: PointerEvent) => {
      const canvas = canvasRef.current
      if (!canvas) return
      const rect = canvas.getBoundingClientRect()
      const x = Math.max(0, Math.round(event.clientX - rect.left + canvas.scrollLeft - dragOffset.current.x))
      const y = Math.max(0, Math.round(event.clientY - rect.top + canvas.scrollTop - dragOffset.current.y))
      setDraft((prev) => ({
        ...prev,
        nodes: prev.nodes.map((node) =>
          node.id === dragging ? { ...node, position: { x, y } } : node,
        ),
      }))
    }
    const up = () => setDragging(null)
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [dragging])

  const patchNode = (id: string, patch: Partial<WorkflowNode>) => {
    setDraft((prev) => ({
      ...prev,
      nodes: prev.nodes.map((node) => (node.id === id ? { ...node, ...patch } : node)),
    }))
  }

  const patchConfig = (id: string, patch: WorkflowNodeConfig) => {
    setDraft((prev) => ({
      ...prev,
      nodes: prev.nodes.map((node) =>
        node.id === id ? { ...node, config: { ...node.config, ...patch } } : node,
      ),
    }))
  }

  const addNode = (type: string) => {
    const id = nextNodeId(nodes)
    const last = nodes[nodes.length - 1]
    const node: WorkflowNode = {
      id,
      type,
      name: NODE_TYPE_LABELS[type] ?? type,
      config: defaultConfig(type),
      position: {
        x: last ? last.position.x : 80,
        y: (last ? last.position.y : 60) + 110,
      },
    }
    setDraft((prev) => ({ ...prev, nodes: [...prev.nodes, node] }))
    setSelectedId(id)
  }

  const removeNode = (id: string) => {
    setDraft((prev) => ({
      ...prev,
      nodes: prev.nodes.filter((node) => node.id !== id),
      edges: prev.edges.filter((edge) => edge.from !== id && edge.to !== id),
    }))
    if (selectedId === id) setSelectedId(null)
    if (linkFrom === id) setLinkFrom(null)
  }

  const changeNodeType = (id: string, type: string) => {
    setDraft((prev) => ({
      ...prev,
      nodes: prev.nodes.map((node) =>
        node.id === id ? { ...node, type, config: defaultConfig(type) } : node,
      ),
    }))
  }

  const connect = (targetId: string) => {
    if (!linkFrom || linkFrom === targetId) return
    const exists = draft.edges.some((edge) => edge.from === linkFrom && edge.to === targetId)
    if (!exists) {
      setDraft((prev) => ({
        ...prev,
        edges: [
          ...prev.edges,
          { id: nextEdgeId(prev.edges), from: linkFrom, to: targetId, condition: null },
        ],
      }))
    }
    setLinkFrom(null)
  }

  const startDrag = (event: ReactPointerEvent<HTMLDivElement>, node: WorkflowNode) => {
    event.stopPropagation()
    setSelectedId(node.id)
    if (linkFrom && linkFrom !== node.id) {
      connect(node.id)
      return
    }
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    dragOffset.current = {
      x: event.clientX - rect.left + canvas.scrollLeft - node.position.x,
      y: event.clientY - rect.top + canvas.scrollTop - node.position.y,
    }
    setDragging(node.id)
  }

  const updateEdge = (id: string, patch: Partial<WorkflowEdge>) => {
    setDraft((prev) => ({
      ...prev,
      edges: prev.edges.map((edge) => (edge.id === id ? { ...edge, ...patch } : edge)),
    }))
  }

  const removeEdge = (id: string) => {
    setDraft((prev) => ({ ...prev, edges: prev.edges.filter((edge) => edge.id !== id) }))
    if (editingEdgeId === id) setEditingEdgeId(null)
  }

  const updateTrigger = (index: number, patch: Partial<WorkflowTrigger>) => {
    setDraft((prev) => ({
      ...prev,
      triggers: prev.triggers.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    }))
  }

  const addTrigger = (type: string) => {
    const trigger: WorkflowTrigger =
      type === 'interval'
        ? { type, every_seconds: 3600 }
        : type === 'daily'
          ? { type, at: '09:00' }
          : type === 'event'
            ? { type, event: '' }
            : { type: 'manual' }
    setDraft((prev) => ({ ...prev, triggers: [...prev.triggers, trigger] }))
  }

  const removeTrigger = (index: number) => {
    setDraft((prev) => {
      const triggers = prev.triggers.filter((_, i) => i !== index)
      return { ...prev, triggers: triggers.length > 0 ? triggers : [{ type: 'manual' }] }
    })
  }

  const edgesWithGeometry = draft.edges
    .map((edge) => {
      const from = byId[edge.from]
      const to = byId[edge.to]
      if (!from || !to) return null
      const sx = from.position.x + NODE_W / 2
      const sy = from.position.y + NODE_H
      const tx = to.position.x + NODE_W / 2
      const ty = to.position.y
      const bend = Math.max(40, Math.abs(ty - sy) / 2)
      return {
        edge,
        path: `M ${sx} ${sy} C ${sx} ${sy + bend}, ${tx} ${ty - bend}, ${tx} ${ty}`,
        labelX: (sx + tx) / 2,
        labelY: (sy + ty) / 2 - 6,
      }
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)

  const canvasWidth = Math.max(
    720,
    ...nodes.map((node) => node.position.x + NODE_W + 80),
  )
  const canvasHeight = Math.max(
    CANVAS_MIN_H,
    ...nodes.map((node) => node.position.y + NODE_H + 100),
  )
  const editingEdge = draft.edges.find((edge) => edge.id === editingEdgeId)

  return (
    <div className="wf-editor">
      <div className="wf-editor-head">
        <div className="wf-editor-fields">
          <label className="wf-field">
            流程名称
            <div className="os-input">
              <input
                value={draft.name}
                maxLength={128}
                placeholder="例如：提交 → 审批 → 执行 → 归档"
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </div>
          </label>
          <label className="wf-field">
            说明（可留空）
            <div className="os-input">
              <input
                value={draft.description}
                maxLength={2000}
                placeholder="这个流程解决什么问题"
                onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              />
            </div>
          </label>
        </div>
        <div className="wf-editor-actions">
          <button className="os-btn" type="button" disabled={saving} onClick={() => onSave(draft)}>
            {saving ? '保存中…' : '保存流程'}
          </button>
          <button className="os-btn os-btn-ghost" type="button" disabled={saving} onClick={onCancel}>
            取消
          </button>
        </div>
      </div>

      {error && (
        <p className="wf-error" role="alert">
          保存失败：{error}
        </p>
      )}

      <div className="wf-toolbar">
        <span className="wf-toolbar-label">添加节点</span>
        {['task', 'agent', 'approval', 'review', 'notify', 'archive', 'parallel', 'join', 'end'].map((type) => (
          <button key={type} className="wf-chip" type="button" onClick={() => addNode(type)}>
            + {NODE_TYPE_LABELS[type]}
          </button>
        ))}
        <span className="wf-toolbar-hint">
          {linkFrom
            ? `正在从「${byId[linkFrom]?.name ?? linkFrom}」拉线：点目标节点完成连线（再点一次空白处取消）`
            : '拖动节点移动位置；点节点右上角「→」再点另一个节点即可连线'}
        </span>
        {linkFrom && (
          <button className="os-btn os-btn-ghost" type="button" onClick={() => setLinkFrom(null)}>
            取消连线
          </button>
        )}
      </div>

      <div
        className="wf-canvas"
        ref={canvasRef}
        style={{ height: canvasHeight }}
        onClick={() => {
          setSelectedId(null)
          setLinkFrom(null)
        }}
      >
        <div className="wf-canvas-inner" style={{ width: canvasWidth, height: canvasHeight }}>
          <svg className="wf-edges" width={canvasWidth} height={canvasHeight} aria-hidden="true">
            <defs>
              <marker id="wf-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--os-text-faint)" />
              </marker>
            </defs>
            {edgesWithGeometry.map(({ edge, path, labelX, labelY }) => (
              <g key={edge.id}>
                <path
                  d={path}
                  className="wf-edge"
                  markerEnd="url(#wf-arrow)"
                  onClick={(event) => {
                    event.stopPropagation()
                    setEditingEdgeId(edge.id)
                  }}
                />
                {edge.condition && (
                  <text x={labelX} y={labelY} className="wf-edge-label" textAnchor="middle">
                    {conditionText(edge.condition)}
                  </text>
                )}
              </g>
            ))}
          </svg>

          {nodes.map((node) => (
            <div
              key={node.id}
              className={`wf-node wf-node-${node.type}${selectedId === node.id ? ' wf-node-selected' : ''}${linkFrom === node.id ? ' wf-node-linking' : ''}`}
              style={{ left: node.position.x, top: node.position.y, width: NODE_W, height: NODE_H }}
              onPointerDown={(event) => startDrag(event, node)}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="wf-node-head">
                <span className="wf-node-type">{NODE_TYPE_LABELS[node.type] ?? node.type}</span>
                <button
                  type="button"
                  className="wf-node-link"
                  title="从这里连到另一个节点"
                  aria-label={`从「${node.name}」连线`}
                  onPointerDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.stopPropagation()
                    setLinkFrom(node.id)
                  }}
                >
                  →
                </button>
              </div>
              <div className="wf-node-name">{node.name || node.id}</div>
              <div className="wf-node-id">{node.id}</div>
            </div>
          ))}
        </div>
      </div>

      {selected && (
        <div className="wf-panel">
          <div className="wf-panel-head">
            <strong>节点配置 · {selected.id}</strong>
            <div className="wf-panel-actions">
              {selected.type !== 'start' && (
                <button className="os-btn os-btn-ghost" type="button" onClick={() => removeNode(selected.id)}>
                  删除节点
                </button>
              )}
            </div>
          </div>

          <div className="wf-grid-2">
            <label className="wf-field">
              显示名称
              <div className="os-input">
                <input
                  value={selected.name}
                  maxLength={128}
                  onChange={(event) => patchNode(selected.id, { name: event.target.value })}
                />
              </div>
            </label>
            <label className="wf-field">
              节点类型
              <div className="os-input">
                <select
                  value={selected.type}
                  onChange={(event) => changeNodeType(selected.id, event.target.value)}
                >
                  {Object.entries(NODE_TYPE_LABELS).map(([type, label]) => (
                    <option key={type} value={type}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
            </label>
          </div>

          {(selected.type === 'task' || selected.type === 'agent') && (
            <>
              <label className="wf-field">
                执行指令（支持 {'{{input.xxx}}'} / {'{{steps.节点id.outputs.xxx}}'} 模板）
                <textarea
                  className="wf-textarea"
                  rows={3}
                  maxLength={4000}
                  value={selected.config.instruction ?? ''}
                  placeholder="例如：{{input.instruction}}"
                  onChange={(event) => patchConfig(selected.id, { instruction: event.target.value })}
                />
              </label>
              <div className="wf-grid-2">
                <label className="wf-field">
                  交给哪个员工（可留空 = 默认）
                  <div className="os-input">
                    <input
                      value={selected.config.employee_name ?? ''}
                      maxLength={128}
                      onChange={(event) => patchConfig(selected.id, { employee_name: event.target.value })}
                    />
                  </div>
                </label>
                <label className="wf-field">
                  范围
                  <div className="os-input">
                    <input
                      value={selected.config.scope ?? 'L1'}
                      maxLength={8}
                      onChange={(event) => patchConfig(selected.id, { scope: event.target.value })}
                    />
                  </div>
                </label>
              </div>
              <div className="wf-grid-3">
                <label className="wf-field">
                  超时（秒）
                  <div className="os-input">
                    <input
                      type="number"
                      min={1}
                      value={selected.config.timeout_seconds ?? 600}
                      onChange={(event) =>
                        patchConfig(selected.id, { timeout_seconds: Math.max(1, numOrZero(event.target.value)) })
                      }
                    />
                  </div>
                </label>
                <label className="wf-field">
                  失败重试次数
                  <div className="os-input">
                    <input
                      type="number"
                      min={0}
                      max={10}
                      value={selected.config.retry?.max_attempts ?? 0}
                      onChange={(event) =>
                        patchConfig(selected.id, {
                          retry: {
                            ...(selected.config.retry ?? {}),
                            max_attempts: numOrZero(event.target.value),
                          },
                        })
                      }
                    />
                  </div>
                </label>
                <label className="wf-field">
                  重试间隔（秒）
                  <div className="os-input">
                    <input
                      type="number"
                      min={0}
                      value={selected.config.retry?.backoff_seconds ?? 5}
                      onChange={(event) =>
                        patchConfig(selected.id, {
                          retry: {
                            ...(selected.config.retry ?? {}),
                            backoff_seconds: numOrZero(event.target.value),
                          },
                        })
                      }
                    />
                  </div>
                </label>
              </div>
              <label className="wf-check">
                <input
                  type="checkbox"
                  checked={selected.config.wait !== false}
                  onChange={(event) => patchConfig(selected.id, { wait: event.target.checked })}
                />
                等待执行完成再走下一步（关闭后只派发不等待）
              </label>
            </>
          )}

          {(selected.type === 'approval' || selected.type === 'review') && (
            <>
              <div className="wf-grid-2">
                <label className="wf-field">
                  审批标题
                  <div className="os-input">
                    <input
                      value={selected.config.title ?? ''}
                      maxLength={200}
                      placeholder="例如：批准发布到生产"
                      onChange={(event) => patchConfig(selected.id, { title: event.target.value })}
                    />
                  </div>
                </label>
                <label className="wf-field">
                  操作是什么
                  <div className="os-input">
                    <input
                      value={selected.config.operation ?? ''}
                      maxLength={200}
                      placeholder="例如：发布 build 0.3.0"
                      onChange={(event) => patchConfig(selected.id, { operation: event.target.value })}
                    />
                  </div>
                </label>
              </div>
              <label className="wf-field">
                为什么需要批准
                <textarea
                  className="wf-textarea"
                  rows={2}
                  maxLength={1000}
                  value={selected.config.reason ?? ''}
                  placeholder="写给审批人看的理由：风险、影响面、为什么不能自动过"
                  onChange={(event) => patchConfig(selected.id, { reason: event.target.value })}
                />
              </label>
              <div className="wf-grid-2">
                <label className="wf-field">
                  审批人（逗号分隔，留空 = 流程所属账号）
                  <div className="os-input">
                    <input
                      value={(selected.config.approvers ?? []).join(',')}
                      onChange={(event) =>
                        patchConfig(selected.id, {
                          approvers: event.target.value
                            .split(',')
                            .map((item) => item.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </div>
                </label>
                <label className="wf-field">
                  超时自动失效（秒）
                  <div className="os-input">
                    <input
                      type="number"
                      min={1}
                      value={selected.config.timeout_seconds ?? 3600}
                      onChange={(event) =>
                        patchConfig(selected.id, { timeout_seconds: Math.max(1, numOrZero(event.target.value)) })
                      }
                    />
                  </div>
                </label>
              </div>
            </>
          )}

          {selected.type === 'notify' && (
            <>
              <label className="wf-field">
                通知内容（支持模板变量）
                <textarea
                  className="wf-textarea"
                  rows={2}
                  maxLength={2000}
                  value={selected.config.message ?? ''}
                  placeholder="例如：流程 {{run.name}} 已归档"
                  onChange={(event) => patchConfig(selected.id, { message: event.target.value })}
                />
              </label>
              <div className="wf-grid-2">
                <label className="wf-field">
                  级别
                  <div className="os-input">
                    <select
                      value={selected.config.level ?? 'info'}
                      onChange={(event) => patchConfig(selected.id, { level: event.target.value })}
                    >
                      <option value="info">提示</option>
                      <option value="warn">警告</option>
                      <option value="danger">严重</option>
                    </select>
                  </div>
                </label>
                <label className="wf-field">
                  通道
                  <div className="os-input">
                    <input
                      value={selected.config.channel ?? 'event'}
                      onChange={(event) => patchConfig(selected.id, { channel: event.target.value })}
                    />
                  </div>
                </label>
              </div>
            </>
          )}

          {selected.type === 'archive' && (
            <label className="wf-field">
              归档备注
              <div className="os-input">
                <input
                  value={selected.config.note ?? ''}
                  maxLength={500}
                  onChange={(event) => patchConfig(selected.id, { note: event.target.value })}
                />
              </div>
            </label>
          )}

          {selected.type === 'join' && (
            <label className="wf-field">
              汇聚方式
              <div className="os-input">
                <select
                  value={selected.config.mode ?? 'all'}
                  onChange={(event) => patchConfig(selected.id, { mode: event.target.value })}
                >
                  <option value="all">等所有并行分支完成</option>
                  <option value="any">任一分支完成即可</option>
                </select>
              </div>
            </label>
          )}
        </div>
      )}

      <div className="wf-panel">
        <div className="wf-panel-head">
          <strong>连线（{draft.edges.length}）</strong>
        </div>
        {draft.edges.length === 0 ? (
          <p className="os-hint">还没有连线。点节点右上角「→」，再点目标节点即可连起来。</p>
        ) : (
          <ul className="wf-edge-list">
            {draft.edges.map((edge) => (
              <li key={edge.id} className="wf-edge-row">
                <span className="wf-edge-route">
                  {byId[edge.from]?.name ?? edge.from} → {byId[edge.to]?.name ?? edge.to}
                </span>
                <span className="wf-edge-cond">
                  {edge.condition ? conditionText(edge.condition) : '无条件'}
                </span>
                <button
                  className="os-link"
                  type="button"
                  onClick={() => setEditingEdgeId(editingEdgeId === edge.id ? null : edge.id)}
                >
                  {editingEdgeId === edge.id ? '收起' : '设条件'}
                </button>
                <button className="os-link" type="button" onClick={() => removeEdge(edge.id)}>
                  删除
                </button>
              </li>
            ))}
          </ul>
        )}

        {editingEdge && (
          <div className="wf-edge-form">
            <div className="wf-grid-3">
              <label className="wf-field">
                判定字段
                <div className="os-input">
                  <input
                    value={editingEdge.condition?.field ?? ''}
                    placeholder="填前面节点输出的字段路径，例如审批结果"
                    onChange={(event) =>
                      updateEdge(editingEdge.id, {
                        condition: {
                          field: event.target.value,
                          op: editingEdge.condition?.op ?? 'eq',
                          value: editingEdge.condition?.value ?? '',
                        },
                      })
                    }
                  />
                </div>
              </label>
              <label className="wf-field">
                操作符
                <div className="os-input">
                  <select
                    value={editingEdge.condition?.op ?? 'eq'}
                    onChange={(event) =>
                      updateEdge(editingEdge.id, {
                        condition: {
                          field: editingEdge.condition?.field ?? '',
                          op: event.target.value,
                          value: editingEdge.condition?.value ?? '',
                        },
                      })
                    }
                  >
                    {CONDITION_OPS.map((op) => (
                      <option key={op} value={op}>
                        {CONDITION_OP_LABELS[op]}
                      </option>
                    ))}
                  </select>
                </div>
              </label>
              <label className="wf-field">
                比较值（列表用逗号分隔）
                <div className="os-input">
                  <input
                    value={
                      Array.isArray(editingEdge.condition?.value)
                        ? (editingEdge.condition?.value as unknown[]).join(',')
                        : String(editingEdge.condition?.value ?? '')
                    }
                    disabled={editingEdge.condition?.op === 'truthy' || editingEdge.condition?.op === 'falsy'}
                    onChange={(event) => {
                      const op = editingEdge.condition?.op ?? 'eq'
                      const raw = event.target.value
                      updateEdge(editingEdge.id, {
                        condition: {
                          field: editingEdge.condition?.field ?? '',
                          op,
                          value: op === 'in' ? raw.split(',').map((item) => item.trim()).filter(Boolean) : raw,
                        },
                      })
                    }}
                  />
                </div>
              </label>
            </div>
            <button
              className="os-link"
              type="button"
              onClick={() => updateEdge(editingEdge.id, { condition: null })}
            >
              清除条件（变成无条件连线）
            </button>
          </div>
        )}
      </div>

      <div className="wf-panel">
        <div className="wf-panel-head">
          <strong>触发方式（{draft.triggers.length}）</strong>
          <div className="wf-panel-actions">
            <button className="wf-chip" type="button" onClick={() => addTrigger('manual')}>
              + 手动
            </button>
            <button className="wf-chip" type="button" onClick={() => addTrigger('interval')}>
              + 定时
            </button>
            <button className="wf-chip" type="button" onClick={() => addTrigger('daily')}>
              + 每天
            </button>
            <button className="wf-chip" type="button" onClick={() => addTrigger('event')}>
              + 事件
            </button>
          </div>
        </div>
        <ul className="wf-trigger-list">
          {draft.triggers.map((trigger, index) => (
            <li key={`${trigger.type}-${index}`} className="wf-trigger-row">
              <span className="wf-trigger-label">{TRIGGER_TYPE_LABELS[trigger.type] ?? trigger.type}</span>
              {trigger.type === 'interval' && (
                <label className="wf-inline-field">
                  每
                  <input
                    className="wf-inline-input"
                    type="number"
                    min={1}
                    value={trigger.every_seconds ?? 3600}
                    onChange={(event) =>
                      updateTrigger(index, { every_seconds: Math.max(1, numOrZero(event.target.value)) })
                    }
                  />
                  秒
                </label>
              )}
              {trigger.type === 'daily' && (
                <label className="wf-inline-field">
                  每天
                  <input
                    className="wf-inline-input"
                    type="time"
                    value={trigger.at ?? '09:00'}
                    onChange={(event) => updateTrigger(index, { at: event.target.value })}
                  />
                </label>
              )}
              {trigger.type === 'event' && (
                <label className="wf-inline-field">
                  事件名
                  <input
                    className="wf-inline-input wf-inline-wide"
                    value={trigger.event ?? ''}
                    placeholder="如 task.completed"
                    onChange={(event) => updateTrigger(index, { event: event.target.value })}
                  />
                </label>
              )}
              <button className="os-link" type="button" onClick={() => removeTrigger(index)}>
                移除
              </button>
            </li>
          ))}
        </ul>
        <p className="os-hint">
          定时与事件触发器由后台调度器推进（每 5 秒扫一次）；停用流程后两者都不再启动新运行。
        </p>
      </div>
    </div>
  )
}
