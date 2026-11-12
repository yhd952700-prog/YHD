/**
 * Owner 全局总览 —— 只有 Owner（首次注册的账号）能进。
 *
 * 一页看清四件事：成员、全部任务、用量、审计。数据全部来自
 * `/v1/owner/*` 的真实计数与真实列表；没有成员 / 没有任务 / 没有审计
 * 事件时显示空态，不放任何演示成员或演示任务。
 *
 * 入口裁剪在 `lib/nav.ts`（`requires: 'owner'`）：普通用户在导航里根本
 * 看不到这一页；深链 `?p=owner-overview` 也会被 `App.tsx` 的
 * `visibleNavKeys` 挡回首页。后端 `require_owner` 是真正的授权依据。
 */

import { useCallback, useEffect, useState } from 'react'
import { Card, Empty, Failure, Loading } from '../components/ui'
import {
  loadOwnerAudit,
  loadOwnerMembers,
  loadOwnerOverview,
  loadOwnerTasks,
  inviteOwnerMember,
  type OwnerAuditPayload,
  type OwnerMembersPayload,
  type OwnerOverview as OwnerOverviewPayload,
  type OwnerTasksPayload,
} from '../lib/owner'
import { ApiError } from '../lib/api'
import { relativeTime } from '../lib/format'

export const OWNER_OVERVIEW_PAGE = {
  key: 'owner-overview',
  navLabel: '全局总览',
  title: '全局总览',
  sub: 'Owner 专属：全部成员、全部任务、用量与审计，一页看清',
} as const

export interface OwnerOverviewProps {
  onNavigate?: (key: string) => void
}

function originLabel(origin: string): string {
  if (origin === 'legacy_migration') return '老版本数据升级后确认'
  if (origin === 'standalone_single_user') return '单人模式'
  if (origin === 'first_registration') return '首次启动时注册'
  return '尚未初始化'
}

export function OwnerOverview({ onNavigate }: OwnerOverviewProps) {
  const [overview, setOverview] = useState<OwnerOverviewPayload | null>(null)
  const [members, setMembers] = useState<OwnerMembersPayload | null>(null)
  const [tasks, setTasks] = useState<OwnerTasksPayload | null>(null)
  const [audit, setAudit] = useState<OwnerAuditPayload | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [inviteName, setInviteName] = useState('')
  const [inviteRole, setInviteRole] = useState('viewer')
  const [inviteBusy, setInviteBusy] = useState(false)
  const [inviteError, setInviteError] = useState<string | null>(null)
  const [inviteCode, setInviteCode] = useState<string | null>(null)
  const [invitedName, setInvitedName] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [o, m, t, a] = await Promise.all([
        loadOwnerOverview(),
        loadOwnerMembers(),
        loadOwnerTasks(),
        loadOwnerAudit(20),
      ])
      setOverview(o)
      setMembers(m)
      setTasks(t)
      setAudit(a)
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function onInvite() {
    const name = inviteName.trim()
    if (!name || inviteBusy) return
    setInviteBusy(true)
    setInviteError(null)
    setInviteCode(null)
    try {
      const result = await inviteOwnerMember({ principal: name, role: inviteRole })
      setInviteCode(result.activation_code)
      setInvitedName(name)
      setInviteName('')
      await load()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setInviteError('该用户名已被使用，请换一个')
      } else {
        setInviteError(err instanceof Error ? err.message : '邀请失败，请稍后重试')
      }
    } finally {
      setInviteBusy(false)
    }
  }

  if (loading) return <Loading rows={4} />
  if (error || !overview || !members || !tasks || !audit) {
    return <Failure message={error ?? '读取失败，请稍后重试'} onRetry={() => void load()} />
  }

  const usage = overview.usage
  const usageCalls = typeof usage.calls === 'number' ? usage.calls : null
  const usageTokens =
    typeof usage.tokens_in === 'number' || typeof usage.tokens_out === 'number'
      ? (typeof usage.tokens_in === 'number' ? usage.tokens_in : 0) +
        (typeof usage.tokens_out === 'number' ? usage.tokens_out : 0)
      : null

  return (
    <div className="os-grid">
      <div className="os-span-12 os-kpi-row">
        <div className="os-kpi">
          <div className="os-kpi-label">成员</div>
          <div className="os-kpi-value">{overview.members.total}</div>
          <div className="os-kpi-foot">
            {overview.members.total <= 1 ? '只有你自己，还没有邀请任何人' : '含你自己在内'}
          </div>
        </div>
        <div className="os-kpi">
          <div className="os-kpi-label">目标 / 任务</div>
          <div className="os-kpi-value">
            {overview.tasks.goals} / {overview.tasks.tasks}
          </div>
          <div className="os-kpi-foot">全部账号的目标与任务合计</div>
        </div>
        <div className="os-kpi">
          <div className="os-kpi-label">模型调用</div>
          <div className="os-kpi-value">{usageCalls === null ? '—' : String(usageCalls)}</div>
          <div className="os-kpi-foot">
            {usage.checked ? '全部账号合计' : '用量暂时读不到'}
          </div>
        </div>
        <div className="os-kpi">
          <div className="os-kpi-label">Token</div>
          <div className="os-kpi-value">{usageTokens === null ? '—' : String(usageTokens)}</div>
          <div className="os-kpi-foot">
            {usage.checked ? '输入 + 输出合计' : '用量暂时读不到'}
          </div>
        </div>
        <div className="os-kpi">
          <div className="os-kpi-label">审计事件</div>
          <div className="os-kpi-value">
            {overview.audit.events === null ? '—' : String(overview.audit.events)}
          </div>
          <div className="os-kpi-foot">
            {overview.audit.checked ? '全部账号的操作记录' : '审计暂时读不到'}
          </div>
        </div>
      </div>

      <div className="os-span-12">
        <Card title="Owner">
          <p>
            当前 Owner 是 <strong>{overview.owner.owner_principal ?? '—'}</strong>（
            {originLabel(overview.owner.origin)}）。Owner 是首次启动时注册的第一个账号，
            拥有全部权限；其他人只能看到自己的东西。
          </p>
          {overview.owner.migration_notes.length > 0 && (
            <ul>
              {overview.owner.migration_notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="os-span-6">
        <Card
          title="成员"
          action={
            onNavigate ? (
              <button type="button" onClick={() => onNavigate('members')}>
                成员与权限管理
              </button>
            ) : undefined
          }
        >
          {members.members.length === 0 ? (
            <Empty title="还没有成员" hint="邀请一个人，他会收到一次性激活码，激活后设置自己的密码。" />
          ) : (
            <ul>
              {members.members.map((member) => (
                <li key={member.principal}>
                  {member.principal}
                  {member.is_owner ? '（Owner）' : ''} · {member.status}
                  {member.permissions.length > 0 ? ` · ${member.permissions.join('、')}` : ''}
                </li>
              ))}
            </ul>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void onInvite()
            }}
          >
            <label>
              邀请新成员
              <input
                value={inviteName}
                onChange={(event) => setInviteName(event.target.value)}
                placeholder="输入用户名，例如 zhangsan"
                maxLength={64}
              />
            </label>
            <label>
              角色
              <select value={inviteRole} onChange={(event) => setInviteRole(event.target.value)}>
                <option value="viewer">查看者（只能看自己的）</option>
                <option value="operator">操作者</option>
                <option value="manager">管理员（可管自己的下级）</option>
              </select>
            </label>
            <button type="submit" disabled={!inviteName.trim() || inviteBusy}>
              {inviteBusy ? '邀请中…' : '邀请'}
            </button>
          </form>
          {inviteError && <p role="alert">{inviteError}</p>}
          {inviteCode && (
            <p role="status">
              已邀请 {invitedName}，一次性激活码是 <strong>{inviteCode}</strong>。
              请把这个码交给对方，由对方在激活页设置密码；它只显示这一次。
            </p>
          )}
        </Card>
      </div>

      <div className="os-span-6">
        <Card
          title="全部任务"
          action={
            onNavigate ? (
              <button type="button" onClick={() => onNavigate('owner-tasks')}>
                查看全部任务
              </button>
            ) : undefined
          }
        >
          {tasks.goals.length === 0 ? (
            <Empty title="还没有任务" hint="任何人提交目标后，这里会按账号列出全部目标与任务。" />
          ) : (
            <ul>
              {tasks.goals.slice(0, 5).map((goal) => (
                <li key={goal.goal_id ?? goal.title}>
                  {goal.title || '（无标题）'} · {goal.owner_account_id} · {goal.state} ·{' '}
                  {goal.tasks.length} 个任务
                </li>
              ))}
            </ul>
          )}
          {tasks.goals.length > 5 && <p>还有 {tasks.goals.length - 5} 个目标，去「全部任务」查看。</p>}
        </Card>
      </div>

      <div className="os-span-12">
        <Card
          title="最近审计"
          action={
            onNavigate ? (
              <button type="button" onClick={() => onNavigate('audit')}>
                打开操作记录
              </button>
            ) : undefined
          }
        >
          {audit.events.length === 0 ? (
            <Empty title="还没有审计事件" hint="有人登录、邀请成员、改权限时，这里会出现记录。" />
          ) : (
            <ul>
              {audit.events.slice(0, 10).map((event) => (
                <li key={event.seq}>
                  {event.action ?? event.event_type} · {event.principal_id}
                  {event.target ? ` · 对 ${event.target}` : ''} · {event.outcome} ·{' '}
                  {relativeTime(event.timestamp)}
                </li>
              ))}
            </ul>
          )}
          {audit.truncated && <p>只显示最近 {audit.events.length} 条，去「操作记录」查看全部。</p>}
        </Card>
      </div>
    </div>
  )
}
