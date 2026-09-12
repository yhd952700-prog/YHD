import { useState } from 'react'
import {
  type ApprovalGrant,
  type EnforcementSnapshot,
  fmtRemaining,
} from '../lib/policyClient'

/**
 * 审批中心（Approval Gateway）—— 内核层真拦截的人工授权入口。
 *
 * 这是一个**展示型**组件：所有策略状态与副作用都由 App 层持有
 * （单一数据源），因为侧栏也要显示同一份真实状态。
 *
 * 诚实边界（NO-FAKE，务必与后端语义一致）
 * ------------------------------------------------------------------
 * 1. 四个 Policy 端点**都要求 Bearer 令牌**。没有令牌时本面板如实显示
 *    「未认证」，绝不假装系统处于某个安全状态。
 * 2. 签发一条凭据 = 记录一次**审计化授权**（谁批的、批了哪些动作、有效到何时）。
 *    它**不会**解除拦截：执行必须由能力层在同进程内于 `grant_window(grant)`
 *    中完成（设计决策 §10.8.3-1 刻意不提供 HTTP 远程执行内核动作的入口）。
 *    因此按钮文案是「记录授权」，而不是「放行」。
 * 3. 主体只能来自令牌的 `sub`；请求体里塞 principal 不会被采信，面板也不提供
 *    该输入框——那正是这道门存在的意义。
 */

export interface ApprovalUiState {
  token: string
  authed: boolean
  snap: EnforcementSnapshot | null
  grants: ApprovalGrant[]
  busy: boolean
  error: string
  /** 令牌被接受但身份未被标记为人类（开放项 C-7）——如实提示，不隐藏。 */
  machineIdentity: string
}

export interface ApprovalActions {
  onTokenChange: (token: string) => void
  onRefresh: () => void
  onIssue: (action: string, reason: string, ttlSeconds: number) => void
  onRevoke: (grantId: string) => void
}

/** 令牌行：粘贴 / 保存 / 清除。 */
function TokenRow({ state, actions }: { state: ApprovalUiState; actions: ApprovalActions }) {
  const [draft, setDraft] = useState('')
  return (
    <div className="approval-token">
      <div className="approval-token-row">
        <input
          className="profile-input approval-token-input"
          type="password"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="粘贴审批令牌（Bearer JWT）"
          autoComplete="off"
        />
        <button
          className="profile-btn"
          onClick={() => {
            actions.onTokenChange(draft)
            setDraft('')
          }}
          disabled={!draft.trim()}
        >
          保存
        </button>
        <button
          className="profile-btn approval-btn-ghost"
          onClick={() => actions.onTokenChange('')}
          disabled={!state.token}
        >
          清除
        </button>
      </div>
      <div className="approval-token-hint">
        {state.token ? (
          state.authed ? (
            <>
              已认证 —— 主体取自令牌的 <code>sub</code>，请求体无法指定。
            </>
          ) : (
            <>已保存令牌，但后端未接受（见下方错误）。</>
          )
        ) : (
          <>
            未认证。用{' '}
            <code>python scripts/issue_console_token.py --principal &lt;id&gt;</code>{' '}
            签发后粘贴。令牌只存 sessionStorage，关标签页即丢弃。
          </>
        )}
      </div>
    </div>
  )
}

/** 拦截态势（真实快照）。 */
function Posture({ snap }: { snap: EnforcementSnapshot }) {
  const state = snap.config_error ? '配置错误' : snap.enabled ? '已武装' : '记录型'
  return (
    <div className="approval-posture">
      <div className="approval-posture-head">
        <span className="approval-label">内核层拦截态势</span>
        <span className={`health-pill ${snap.config_error ? 'bad' : snap.enabled ? 'warn' : 'ok'}`}>
          {state}
        </span>
      </div>
      <ul className="health-list">
        <li>
          <span className="health-dot" />
          <span className="health-name">开关</span>
          <span className="health-val">{snap.env_var}</span>
        </li>
        <li>
          <span className="health-dot" />
          <span className="health-name">选择</span>
          <span className="health-val">{snap.spec || '(空 = 不开启)'}</span>
        </li>
        <li>
          <span className="health-dot" />
          <span className="health-name">武装动作</span>
          <span className="health-val">{snap.count} 个</span>
        </li>
      </ul>
      {snap.config_error && (
        <div className="approval-error">
          配置错误：{snap.config_error}（HIGH/CRITICAL 动作将被拒绝）
        </div>
      )}
      {snap.count > 0 && (
        <div className="approval-chips">
          {snap.enforced_actions.map((a) => (
            <span key={a} className="approval-chip">
              {a}
            </span>
          ))}
        </div>
      )}
      {snap.exempt_actions.length > 0 && (
        <div className="approval-exempt">
          豁免（有实测调用点，武装会造成可用性回归）：{snap.exempt_actions.join('、')}
        </div>
      )}
    </div>
  )
}

/** 凭据列表 + 撤销。 */
function GrantList({
  grants,
  busy,
  onRevoke,
}: {
  grants: ApprovalGrant[]
  busy: boolean
  onRevoke: (grantId: string) => void
}) {
  if (grants.length === 0) {
    return <div className="profile-empty">当前没有有效凭据（后端真实返回为空）</div>
  }
  return (
    <ul className="approval-grants">
      {grants.map((g) => (
        <li key={g.grant_id} className={g.is_active ? '' : 'off'}>
          <div className="approval-grant-head">
            <span className="approval-grant-principal">{g.principal}</span>
            <span className={`approval-grant-state ${g.is_active ? 'on' : 'off'}`}>
              {g.is_active ? `剩余 ${fmtRemaining(g.remaining_seconds)}` : '已失效'}
            </span>
          </div>
          <div className="approval-grant-actions">{g.actions.join('、')}</div>
          {g.reason && <div className="approval-grant-reason">理由：{g.reason}</div>}
          <div className="approval-grant-foot">
            <span className="approval-grant-id">{g.grant_id.slice(0, 12)}</span>
            <button
              className="profile-btn approval-btn-ghost"
              disabled={busy || !g.is_active}
              onClick={() => onRevoke(g.grant_id)}
            >
              撤销
            </button>
          </div>
        </li>
      ))}
    </ul>
  )
}

export function ApprovalCenter({
  state,
  actions,
}: {
  state: ApprovalUiState
  actions: ApprovalActions
}) {
  const [action, setAction] = useState('')
  const [reason, setReason] = useState('')
  const [ttl, setTtl] = useState(300)

  const options = state.snap?.enforced_actions ?? []
  const selected = action || options[0] || ''

  return (
    <div className="panel approval-panel">
      <div className="panel-title">
        <span>审批中心</span>
        <span className="biz-en">Approval Gateway</span>
        <span className={`health-pill ${state.authed ? 'ok' : 'na'}`}>
          {state.authed ? '已认证' : '未认证'}
        </span>
      </div>

      <TokenRow state={state} actions={actions} />

      {state.snap ? (
        <Posture snap={state.snap} />
      ) : (
        <div className="profile-empty">
          未认证时无法读取拦截态势 —— 该快照会暴露**未受管**的动作集合，后端刻意要求令牌。
        </div>
      )}

      {state.authed && (
        <>
          {state.machineIdentity && (
            <div className="approval-warn">
              当前令牌的主体 <b>{state.machineIdentity}</b> 是内置机器身份，
              自 Policy C-7 起<b>无法持有主权</b>：内核判据已改为正向白名单
              （须 <code>metadata.kind == "human"</code>），所以「记录授权」会被
              内核以 400 拒绝，而不是被记录到机器名下（OD-010）。
              <br />
              需要改为已登记的人类主体：
              <code>scripts/register_human_identity.py --principal &lt;name&gt;</code>，
              再用 <code>scripts/issue_console_token.py</code> 重新签发。
            </div>
          )}

          <div className="approval-label">有效凭据</div>
          <GrantList grants={state.grants} busy={state.busy} onRevoke={actions.onRevoke} />

          <div className="approval-label">记录一次授权</div>
          <div className="approval-form">
            <select
              className="profile-select"
              value={selected}
              onChange={(e) => setAction(e.target.value)}
            >
              {options.length === 0 && <option value="">（当前无武装动作）</option>}
              {options.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <input
              className="profile-input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="授权理由（记入审计）"
            />
            <input
              className="profile-input approval-ttl"
              type="number"
              min={1}
              max={3600}
              value={ttl}
              onChange={(e) => setTtl(Number(e.target.value) || 300)}
              title="有效期（秒），上限 3600"
            />
            <button
              className="profile-btn"
              onClick={() => actions.onIssue(selected, reason, ttl)}
              disabled={state.busy || !selected}
              title="记录授权（不是「放行」——执行仍需在 grant_window 内进行）"
            >
              记录授权
            </button>
          </div>

          <div className="approval-note">
            凭据是**审计化授权记录**，不是通行证。后端刻意不提供「用凭据在 HTTP 上执行内核动作」
            的入口（设计决策 §10.8.3-1），所以签发后重试同一请求仍会得到 409 ——
            执行须由能力层在同进程内于 <code>grant_window(grant)</code> 中完成。
          </div>
        </>
      )}

      {state.error && <div className="approval-error">错误：{state.error}</div>}
      {state.busy && <div className="approval-busy">读取中…</div>}
    </div>
  )
}
