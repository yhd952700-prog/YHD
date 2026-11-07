/**
 * 成员与权限管理（根）/ 我的成员（有管理范围的成员）。
 *
 * 同一个组件、两套内容：
 * - 根：整棵树的嵌套视图，能看到每个人的 Key 授权状态与结构；
 * - 成员：只渲染服务端返回的成员清单，不出现层级、不出现 Key 授权来源。
 *
 * 所有写操作都是显式按钮；只读查看模式下全部禁用。拒绝理由直接用服务端文本，
 * 账号类 403/404 统一为"无权执行该操作"（见 lib/accounts neutralDenial）。
 */

import { useState } from 'react'
import type { ReactNode } from 'react'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'
import { keyAuthorizationLabel } from '../lib/accountCaps'
import {
  type AccountMember,
  type AccountNode,
  createMember,
  loadAccounts,
  loadRoles,
  loadTree,
  reissueActivation,
  resetMemberPassword,
  setKeySharing,
  updateStatus,
  useAccountData,
} from '../lib/accounts'
import { Card, Empty, Guard } from '../components/ui'
import { OneTimeSecret, StubBadge } from '../components/AccountChrome'
import { permissionLabel, permissionTitle } from '../lib/format'

type Variant = 'root' | 'member'

export function Members({
  profile,
  caps,
  variant,
  readOnly,
  onViewAs,
}: {
  profile: AccountProfile
  caps: AccountCaps
  variant: Variant
  readOnly: boolean
  onViewAs: (principal: string) => void
}) {
  const isRoot = variant === 'root'
  const allowed = isRoot ? caps.canMembers : caps.canMyMembers
  const accounts = useAccountData(() =>
    loadAccounts({ principal: profile.principal, isRoot: profile.isRoot }),
  )
  const tree = useAccountData(() =>
    isRoot ? loadTree({ principal: profile.principal, isRoot: profile.isRoot }) : null,
  )
  const roles = useAccountData(() => loadRoles())

  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [issued, setIssued] = useState<{ principal: string; code: string; notice?: string } | null>(
    null,
  )
  const [resetFor, setResetFor] = useState<string | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const act = async (label: string, run: () => Promise<unknown>) => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      await run()
      setNotice({ tone: 'ok', text: `${label}已完成` })
      accounts.reload()
      tree.reload()
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : `${label}失败`,
      })
    } finally {
      setBusy(false)
    }
  }

  const handleCreate = async (input: {
    principal: string
    displayName: string
    role: string
    allowKeySharing: boolean
  }) => {
    if (readOnly) {
      setNotice({ tone: 'error', text: '只读查看中，写操作已禁用' })
      return
    }
    setBusy(true)
    setNotice(null)
    try {
      const result = await createMember({
        principal: input.principal.trim(),
        display_name: input.displayName.trim() || undefined,
        role: input.role,
        allow_key_sharing: input.allowKeySharing,
      })
      setIssued({
        principal: result.data.account.principal,
        code: result.data.activation_code,
        notice: result.data.notice,
      })
      accounts.reload()
      tree.reload()
    } catch (error) {
      setNotice({
        tone: 'error',
        text: error instanceof Error ? error.message : '创建失败',
      })
    } finally {
      setBusy(false)
    }
  }

  const source = accounts.source === 'stub' || tree.source === 'stub' ? 'stub' : 'live'

  if (!allowed) {
    return (
      <div className="os-grid">
        <Card title="账号" sub="当前账号没有该入口" span={12}>
          <div className="os-empty">无权执行该操作</div>
        </Card>
      </div>
    )
  }

  return (
    <div className="os-grid">
      <Card
        title={isRoot ? '成员与权限管理' : '我的账号'}
        sub={
          isRoot
            ? '整棵树的成员、状态与 Key 授权。写操作都会落到服务端审计。'
            : '你创建的账号。日常对话、知识库与自有 Key 不受影响。'
        }
        span={12}
        action={<StubBadge source={source} />}
      >
        {notice && (
          <div className={notice.tone === 'ok' ? 'os-note' : 'os-alert'} role="status">
            {notice.text}
          </div>
        )}

        {issued && (
          <OneTimeSecret
            label={`${issued.principal} 的激活码`}
            value={issued.code}
            notice={issued.notice}
            onDone={() => setIssued(null)}
          />
        )}

        <Guard
          loading={accounts.loading}
          error={accounts.error}
          data={accounts.data}
          isEmpty={(data) => data.members.length === 0}
          emptyTitle={isRoot ? '还没有成员' : '还没有账号'}
          emptyHint={isRoot ? '在下方创建第一个成员。' : '在下方创建第一个账号。'}
          onRetry={accounts.reload}
        >
          {(data) => {
            if (isRoot && tree.data?.tree) {
              return (
                <div className="os-table-wrap">
                  <table className="os-table">
                    <thead>
                      <tr>
                        <th>成员</th>
                        <th>状态</th>
                        <th>Key 授权</th>
                        <th>可执行范围</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tree.data.tree.children.map((child) => (
                        <TreeNodeRows
                          key={child.principal}
                          node={child}
                          depth={0}
                          readOnly={readOnly}
                          busy={busy}
                          onViewAs={onViewAs}
                          onToggleStatus={(member) =>
                            void act('状态变更', () =>
                              updateStatus(
                                member.principal,
                                member.status === 'active' ? 'suspended' : 'active',
                                '界面操作',
                              ),
                            )
                          }
                          onIssue={(member) =>
                            void act('激活码重发', async () => {
                              const result = await reissueActivation(member.principal)
                              setIssued({ principal: member.principal, code: result.data.activation_code })
                            })
                          }
                          onToggleKey={(member) =>
                            void act('Key 授权变更', () =>
                              setKeySharing(member.principal, !member.allow_superior_key_use),
                            )
                          }
                          onReset={(member) => {
                            setResetFor(member.principal)
                            setResetPassword('')
                          }}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            }
            return (
              <div className="os-table-wrap">
                <table className="os-table">
                  <thead>
                    <tr>
                      <th>账号</th>
                      <th>状态</th>
                      <th>可执行范围</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.members.map((member) => (
                      <MemberRow
                        key={member.principal}
                        member={member}
                        isRoot={false}
                        depth={0}
                        readOnly={readOnly}
                        busy={busy}
                        onViewAs={onViewAs}
                        onToggleStatus={(target) =>
                          void act('状态变更', () =>
                            updateStatus(
                              target.principal,
                              target.status === 'active' ? 'suspended' : 'active',
                              '界面操作',
                            ),
                          )
                        }
                        onIssue={(target) =>
                          void act('激活码重发', async () => {
                            const result = await reissueActivation(target.principal)
                            setIssued({ principal: target.principal, code: result.data.activation_code })
                          })
                        }
                        onToggleKey={() => undefined}
                        onReset={(target) => {
                          setResetFor(target.principal)
                          setResetPassword('')
                        }}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }}
        </Guard>

        {resetFor && (
          <form
            className="os-field"
            onSubmit={(event) => {
              event.preventDefault()
              if (resetPassword.length < 8) {
                setNotice({ tone: 'error', text: '密码至少 8 位' })
                return
              }
              void act('密码重置', async () => {
                await resetMemberPassword(resetFor, resetPassword)
                setResetFor(null)
                setResetPassword('')
              })
            }}
          >
            <label htmlFor="reset-principal">为 {resetFor} 设置新密码</label>
            <input
              id="reset-principal"
              className="os-input-plain"
              type="password"
              value={resetPassword}
              onChange={(event) => setResetPassword(event.target.value)}
              placeholder="至少 8 位"
              disabled={readOnly}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="os-btn" style={{ width: 'auto' }} type="submit" disabled={readOnly || busy}>
                确认重置
              </button>
              <button
                className="os-btn os-btn-ghost"
                style={{ width: 'auto' }}
                type="button"
                onClick={() => setResetFor(null)}
              >
                取消
              </button>
            </div>
          </form>
        )}
      </Card>

      <CreateMemberCard
        isRoot={isRoot}
        readOnly={readOnly}
        busy={busy}
        roles={roles.data?.roles ?? []}
        source={roles.source}
        onCreate={(input) => void handleCreate(input)}
      />
    </div>
  )
}

function TreeNodeRows({
  node,
  depth,
  readOnly,
  busy,
  onViewAs,
  onToggleStatus,
  onIssue,
  onToggleKey,
  onReset,
}: {
  node: AccountNode
  depth: number
  readOnly: boolean
  busy: boolean
  onViewAs: (principal: string) => void
  onToggleStatus: (member: AccountMember) => void
  onIssue: (member: AccountMember) => void
  onToggleKey: (member: AccountMember) => void
  onReset: (member: AccountMember) => void
}) {
  return (
    <>
      <MemberRow
        member={node}
        isRoot
        depth={depth}
        readOnly={readOnly}
        busy={busy}
        onViewAs={onViewAs}
        onToggleStatus={onToggleStatus}
        onIssue={onIssue}
        onToggleKey={onToggleKey}
        onReset={onReset}
      />
      {node.children.map((child) => (
        <TreeNodeRows
          key={child.principal}
          node={child}
          depth={depth + 1}
          readOnly={readOnly}
          busy={busy}
          onViewAs={onViewAs}
          onToggleStatus={onToggleStatus}
          onIssue={onIssue}
          onToggleKey={onToggleKey}
          onReset={onReset}
        />
      ))}
    </>
  )
}

function MemberRow({
  member,
  isRoot,
  depth,
  readOnly,
  busy,
  onViewAs,
  onToggleStatus,
  onIssue,
  onToggleKey,
  onReset,
}: {
  member: AccountMember
  isRoot: boolean
  depth: number
  readOnly: boolean
  busy: boolean
  onViewAs: (principal: string) => void
  onToggleStatus: (member: AccountMember) => void
  onIssue: (member: AccountMember) => void
  onToggleKey: (member: AccountMember) => void
  onReset: (member: AccountMember) => void
}) {
  const active = member.status === 'active'
  return (
    <tr>
      <td>
        <span style={{ paddingLeft: depth * 16 }}>{member.principal}</span>
      </td>
      <td>{active ? '使用中' : member.status}</td>
      {isRoot && <td>{keyAuthorizationLabel(member, true)}</td>}
      <td>
        <span className="os-hint">
          {member.permissions.length === 0
            ? '（未授予范围）'
            : member.permissions.map((value, index) => (
                <span key={`${value}-${index}`} title={permissionTitle(value)}>
                  {index > 0 ? ' · ' : ''}
                  {permissionLabel(value)}
                </span>
              ))}
        </span>
      </td>
      <td>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {isRoot && (
            <button
              className="os-link"
              disabled={readOnly}
              onClick={() => onToggleStatus(member)}
              title={active ? '暂停该账号' : '恢复该账号'}
            >
              {active ? '暂停' : '恢复'}
            </button>
          )}
          <button
            className="os-link"
            disabled={readOnly || busy}
            onClick={() => onIssue(member)}
            title="重新生成一次性激活码"
          >
            激活码
          </button>
          <button
            className="os-link"
            disabled={readOnly}
            onClick={() => onReset(member)}
          >
            重置密码
          </button>
          {isRoot && (
            <button
              className="os-link"
              disabled={readOnly}
              onClick={() => onToggleKey(member)}
              title="切换是否允许主账号代用该账号的 Key"
            >
              {member.allow_superior_key_use ? '取消共享' : '允许共享'}
            </button>
          )}
          <button className="os-link" disabled={readOnly} onClick={() => onViewAs(member.principal)}>
            只读查看
          </button>
        </div>
      </td>
    </tr>
  )
}

function CreateMemberCard({
  isRoot,
  readOnly,
  busy,
  roles,
  source,
  onCreate,
}: {
  isRoot: boolean
  readOnly: boolean
  busy: boolean
  roles: Array<{ id: string; permissions: string[] }>
  source: 'live' | 'stub'
  onCreate: (input: {
    principal: string
    displayName: string
    role: string
    allowKeySharing: boolean
  }) => void
}): ReactNode {
  const [principal, setPrincipal] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('')
  const [allowKeySharing, setAllowKeySharing] = useState(false)

  return (
    <Card
      title={isRoot ? '新建成员' : '新建账号'}
      sub="创建后生成一次性激活码；对方首次登录必须设置自己的密码。"
      span={12}
      action={<StubBadge source={source} />}
    >
      <form
        className="os-grid"
        onSubmit={(event) => {
          event.preventDefault()
          if (!principal.trim()) return
          onCreate({ principal, displayName, role: role || roles[0]?.id || '', allowKeySharing })
        }}
      >
        <div className="os-field os-span-6">
          <label htmlFor="new-principal">账号</label>
          <input
            id="new-principal"
            className="os-input-plain"
            value={principal}
            onChange={(event) => setPrincipal(event.target.value)}
            placeholder="登录时使用，创建后不可改"
            disabled={readOnly}
            required
          />
        </div>
        <div className="os-field os-span-6">
          <label htmlFor="new-name">显示名（可选）</label>
          <input
            id="new-name"
            className="os-input-plain"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            disabled={readOnly}
          />
        </div>
        <div className="os-field os-span-6">
          <label htmlFor="new-role">可执行范围</label>
          <select
            id="new-role"
            className="os-input-plain"
            value={role}
            onChange={(event) => setRole(event.target.value)}
            disabled={readOnly}
          >
            {roles.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {roleLabel(preset.id)}
              </option>
            ))}
          </select>
        </div>
        <label className="os-check os-span-6">
          <input
            type="checkbox"
            checked={allowKeySharing}
            onChange={(event) => setAllowKeySharing(event.target.checked)}
            disabled={readOnly}
          />
          <span>{isRoot ? '允许我代用该账号的 Key' : '允许授权使用我的 Key'}</span>
        </label>
        <div className="os-span-12">
          <button className="os-btn" style={{ width: 'auto' }} type="submit" disabled={readOnly || busy}>
            {busy ? '正在创建…' : '创建并生成激活码'}
          </button>
        </div>
      </form>
      {roles.length === 0 && !readOnly && (
        <Empty title="角色模板不可用" hint="接口未返回可执行范围，暂时无法创建账号。" />
      )}
    </Card>
  )
}

/** 预置范围 id → 人话；未知预置不把英文 id 直接铺给用户。 */
const ROLE_LABELS: Record<string, string> = {
  viewer: '只读',
  operator: '日常操作',
  manager: '管理',
}

function roleLabel(id: string): string {
  return ROLE_LABELS[id] ?? '自定义范围'
}
