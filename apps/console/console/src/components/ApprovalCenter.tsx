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
 * 2. 签发一条凭据 = 记录一次**审计化授权**（谁批的、批了哪些动作、对哪个 target、有效到何时）。
 *    它**不会**解除拦截：执行必须由能力层在同进程内于 `grant_window(grant, target=…)`
 *    中完成（设计决策 §10.8.3-1 刻意不提供 HTTP 远程执行内核动作的入口）。
 *    因此按钮文案是「记录授权」，而不是「放行」。
 * 3. 主体只能来自令牌的 `sub`；请求体里塞 principal 不会被采信，面板也不提供
 *    该输入框——那正是这道门存在的意义。
 * 4. 授权的最小单位是 (principal, action, target)：**target 必填**，空值不得提交
 *    （否则后端 422）。仅当确实要授权**全部** target 时才用通配 `"*"`，且必须
 *    二次确认（对应后端 `confirm_wildcard`，未确认的通配会被拒绝）。
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
  onIssue: (
    action: string,
    target: string,
    reason: string,
    ttlSeconds: number,
    confirmWildcard: boolean,
  ) => void
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
          placeholder="粘贴审批令牌"
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
            <>已认证 —— 身份来自你粘贴的令牌，页面上无法改成别人。</>
          ) : (
            <>已保存令牌，但服务端没有接受（见下方提示）。</>
          )
        ) : (
          <>
            未认证。请先获取审批令牌，再粘贴到这里；令牌只保存在本标签页，关闭即丢弃。
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
        <span className="approval-label">动作拦截态势</span>
        <span className={`health-pill ${snap.config_error ? 'bad' : snap.enabled ? 'warn' : 'ok'}`}>
          {state}
        </span>
      </div>
      <ul className="health-list">
        <li>
          <span className="health-dot" />
          <span className="health-name">拦截开关</span>
          <span className="health-val">{snap.enabled ? '已开启' : '未开启'}</span>
        </li>
        <li>
          <span className="health-dot" />
          <span className="health-name">生效方式</span>
          <span className="health-val">
            {snap.enabled ? '按清单拦截' : '只记录，不拦截'}
          </span>
        </li>
        <li>
          <span className="health-dot" />
          <span className="health-name">已拦截动作</span>
          <span className="health-val">{snap.count} 个</span>
        </li>
      </ul>
      {snap.config_error && (
        <div className="approval-error">
          拦截配置有误，高风险动作会被拒绝。请检查配置后重试。
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
    return <div className="profile-empty">当前没有有效凭据</div>
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
  const [target, setTarget] = useState('')
  const [reason, setReason] = useState('')
  const [ttl, setTtl] = useState(300)
  const [wildcardConfirmed, setWildcardConfirmed] = useState(false)

  const options = state.snap?.enforced_actions ?? []
  const selected = action || options[0] || ''
  const trimmedTarget = target.trim()
  const isWildcard = trimmedTarget === '*'
  // 空 target 不得提交（否则后端 422）；通配 "*" 必须二次确认后才允许提交。
  const canIssue = !!selected && trimmedTarget !== '' && (!isWildcard || wildcardConfirmed)

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
          未认证时看不到拦截态势 —— 这是服务端的保护措施，不是读取失败。
        </div>
      )}

      {state.authed && (
        <>
          {state.machineIdentity && (
            <div className="approval-warn">
              当前使用的是内置服务账号 <b>{state.machineIdentity}</b>，它不能签发人工授权。
              <br />
              请改用你自己的账号登录，再回来记录授权。
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
              value={target}
              onChange={(e) => {
                setTarget(e.target.value)
                setWildcardConfirmed(false)
              }}
              placeholder="授权对象（必填）"
              title="该授权只对这一个对象生效；要覆盖全部对象需填写 * 并二次确认"
            />
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
            {isWildcard && (
              <label className="approval-wildcard-confirm">
                <input
                  type="checkbox"
                  checked={wildcardConfirmed}
                  onChange={(e) => setWildcardConfirmed(e.target.checked)}
                />
                确认对<b>全部对象</b>授权（通配 “*”，风险较高，需二次确认）
              </label>
            )}
            <button
              className="profile-btn"
              onClick={() =>
                actions.onIssue(selected, trimmedTarget, reason, ttl, isWildcard && wildcardConfirmed)
              }
              disabled={state.busy || !canIssue}
              title="记录一次授权；记录本身不会放行执行"
            >
              记录授权
            </button>
          </div>

          <div className="approval-note">
            凭据是一条可审计的授权记录，不是通行证。签发后再重试同一请求仍可能被拒绝；
            真正执行要由执行方在授权有效期内完成。
          </div>
        </>
      )}

      {state.error && <div className="approval-error">读取失败：{state.error}</div>}
      {state.busy && <div className="approval-busy">读取中…</div>}
    </div>
  )
}
