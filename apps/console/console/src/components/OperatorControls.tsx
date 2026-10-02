/**
 * 人类操作员控制组件。
 *
 * 五个真实的「写」动作，全部接到既有的 REST 客户端（见 `lib/operator.ts`），
 * 没有一项是装饰：
 *   - `HireEmployeeForm`   → POST /v1/employees
 *   - `CreateGoalForm`     → POST /v1/goals
 *   - `AgentActionButtons` → POST /v1/employees/{id}/pause | resume
 *   - `EmployeeActionButtons` → DELETE /v1/employees/{name}
 *   - `GoalActionButtons`  → POST /v1/goals/{id}/stop  （主权：中止运行中的自主动作）
 *                            POST /v1/goals/{id}/replan
 *
 * 每个动作成功后回调 `onDone` 让所在页面重新拉取数据，失败则就地显示后端给的
 * 真实原因（不吞错误，与只读层 `Guard` 的约定一致）。
 */

import { useState } from 'react'
import {
  createGoal,
  deleteEmployee,
  hireEmployee,
  pauseAgent,
  replanGoal,
  resumeAgent,
  stopGoal,
} from '../lib/operator'

/** 把一次动作的「进行中 / 失败」状态收敛到一个小 hook，避免每个按钮各写一套。 */
function useAction(onDone: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function run(fn: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

// ─── 雇佣 AI Employee ─────────────────────────────────────────

export function HireEmployeeForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('')
  const [agentCount, setAgentCount] = useState(3)
  const [agentTypes, setAgentTypes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState(false)

  async function submit() {
    if (!name.trim()) {
      setError('请填写员工名（唯一键）')
      return
    }
    setBusy(true)
    setError(null)
    setOk(false)
    try {
      await hireEmployee({
        name: name.trim(),
        agent_count: agentCount,
        agent_types: agentTypes
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
      })
      setOk(true)
      setName('')
      setAgentTypes('')
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : '雇佣失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="os-operator-form">
      <div className="os-field">
        <label>员工名（唯一）</label>
        <div className="os-input">
          <input
            value={name}
            placeholder="例如 research-team"
            onChange={(e) => setName(e.target.value)}
          />
        </div>
      </div>
      <div className="os-field">
        <label>Agent 数量</label>
        <div className="os-input">
          <input
            type="number"
            min={1}
            max={64}
            value={agentCount}
            onChange={(e) => setAgentCount(Number(e.target.value) || 1)}
          />
        </div>
      </div>
      <div className="os-field">
        <label>Agent 类型（逗号分隔，可选）</label>
        <div className="os-input">
          <input
            value={agentTypes}
            placeholder="researcher, coder"
            onChange={(e) => setAgentTypes(e.target.value)}
          />
        </div>
      </div>
      <button className="os-btn" disabled={busy} onClick={() => void submit()}>
        {busy ? '雇佣中…' : '雇佣员工'}
      </button>
      {ok && !error && <span className="os-row-sub" style={{ color: 'var(--os-ok)' }}>已雇佣</span>}
      {error && <span className="os-row-sub" style={{ color: 'var(--os-danger)' }}>{error}</span>}
    </div>
  )
}

// ─── 创建 Goal ───────────────────────────────────────────────

export function CreateGoalForm({ onDone }: { onDone: () => void }) {
  const [text, setText] = useState('')
  const [scope, setScope] = useState('L1')
  const [planMode, setPlanMode] = useState('auto')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState(false)

  async function submit() {
    if (!text.trim()) {
      setError('请填写目标的自然语言描述')
      return
    }
    setBusy(true)
    setError(null)
    setOk(false)
    try {
      await createGoal({
        natural_language: text.trim(),
        scope,
        plan_mode: planMode,
      })
      setOk(true)
      setText('')
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="os-operator-form">
      <div className="os-field os-field-wide">
        <label>目标描述</label>
        <div className="os-input">
          <input
            value={text}
            placeholder="例如：把上月活跃用户汇总成一份周报"
            onChange={(e) => setText(e.target.value)}
          />
        </div>
      </div>
      <div className="os-field">
        <label>范围</label>
        <div className="os-input">
          <select value={scope} onChange={(e) => setScope(e.target.value)}>
            {['L0', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'L7'].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="os-field">
        <label>计划模式</label>
        <div className="os-input">
          <select value={planMode} onChange={(e) => setPlanMode(e.target.value)}>
            <option value="auto">auto</option>
            <option value="manual">manual</option>
          </select>
        </div>
      </div>
      <button className="os-btn" disabled={busy} onClick={() => void submit()}>
        {busy ? '创建中…' : '创建 Goal'}
      </button>
      {ok && !error && <span className="os-row-sub" style={{ color: 'var(--os-ok)' }}>已创建</span>}
      {error && <span className="os-row-sub" style={{ color: 'var(--os-danger)' }}>{error}</span>}
    </div>
  )
}

// ─── 单 agent：暂停 / 恢复 ───────────────────────────────────

export function AgentActionButtons({
  agentId,
  status,
  onDone,
}: {
  agentId: string
  status: string
  onDone: () => void
}) {
  const { busy, error, run } = useAction(onDone)
  const paused = status === 'paused'
  return (
    <span className="os-action-group">
      {paused ? (
        <button
          className="os-btn os-btn-ghost"
          disabled={busy}
          onClick={() => void run(() => resumeAgent(agentId))}
        >
          恢复
        </button>
      ) : (
        <button
          className="os-btn os-btn-ghost"
          disabled={busy}
          onClick={() => void run(() => pauseAgent(agentId))}
        >
          暂停
        </button>
      )}
      {error && (
        <span className="os-row-sub" style={{ color: 'var(--os-danger)' }} title={error}>
          失败
        </span>
      )}
    </span>
  )
}

// ─── 按名员工：解雇 ───────────────────────────────────────────

export function EmployeeActionButtons({
  name,
  onDone,
}: {
  name: string
  onDone: () => void
}) {
  const { busy, error, run } = useAction(onDone)
  return (
    <span className="os-action-group">
      <button
        className="os-btn os-btn-ghost"
        style={{ color: 'var(--os-danger)' }}
        disabled={busy}
        onClick={() => {
          if (window.confirm(`确认解雇员工「${name}」？该操作不可撤销。`)) {
            void run(() => deleteEmployee(name))
          }
        }}
      >
        解雇
      </button>
      {error && (
        <span className="os-row-sub" style={{ color: 'var(--os-danger)' }} title={error}>
          失败
        </span>
      )}
    </span>
  )
}

// ─── 单 Goal：中止 / 重规划 ───────────────────────────────────

export function GoalActionButtons({
  goalId,
  state,
  onDone,
}: {
  goalId: string
  state: string
  onDone: () => void
}) {
  const { busy, error, run } = useAction(onDone)
  // 中止：任何尚未终结的运行态都可被人类喊停（主权）。
  const canStop = state !== 'completed' && state !== 'stopped'
  // 重规划：仅对失败或后端建议重规划的目标开放。
  const canReplan = state === 'failed' || state === 'replan_suggested'
  return (
    <span className="os-action-group">
      <button
        className="os-btn os-btn-ghost"
        style={{ color: 'var(--os-danger)' }}
        disabled={busy || !canStop}
        title={canStop ? '中止这个运行中的自主动作' : '已终结，无需中止'}
        onClick={() => void run(() => stopGoal(goalId))}
      >
        中止
      </button>
      <button
        className="os-btn os-btn-ghost"
        disabled={busy || !canReplan}
        title={canReplan ? '对失败目标重规划' : '仅失败/建议重规划时可重规划'}
        onClick={() => void run(() => replanGoal(goalId))}
      >
        重规划
      </button>
      {error && (
        <span className="os-row-sub" style={{ color: 'var(--os-danger)' }} title={error}>
          失败
        </span>
      )}
    </span>
  )
}
