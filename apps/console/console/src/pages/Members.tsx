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
  countTreeMembers,
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

/**
 * 成员列表客户端分页大小。
 *
 * `/v1/accounts` 一次返回全部成员，没有 offset / limit；改契约会同时影响
 * 根视图与成员视图，因此沿用现有接口，在客户端按页切片。
 */
const MEMBERS_PAGE_SIZE = 50

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

  const [treeQuery, setTreeQuery] = useState('')
  // 折叠状态本身在模块级 Map（换页 / 换查看范围都不重置），这里只用计数器触发重渲染。
  const [, bumpFold] = useState(0)
  const toggleFold = (principal: string, expanded: boolean) => {
    foldOverrides.set(principal, expanded)
    bumpFold((value) => value + 1)
  }
  // 搜索只作用于根视图；成员视图没有树，这里恒为空集合。
  const treeQueryText = treeQuery.trim()
  const treeFolding: TreeFolding = {
    query: treeQueryText,
    matched: collectMatchedPaths(tree.data?.tree?.children ?? [], treeQueryText),
  }

  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [issued, setIssued] = useState<{ principal: string; code: string; notice?: string } | null>(
    null,
  )
  const [resetFor, setResetFor] = useState<string | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [pageIndex, setPageIndex] = useState(0)
  // 创建成功后先本地插入刚建的账号：reload 是异步的，服务端列表回来之前
  // Guard 仍拿旧数据判定「空」，用户会看到「刚建完却说没有」。
  const [justCreated, setJustCreated] = useState<AccountMember[]>([])

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
      setJustCreated((prev) => [result.data.account, ...prev])
      setPageIndex(0)
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

  const serverMembers = accounts.data?.members ?? []
  // 服务端列表一旦包含该账号，就用真实行替换本地占位，不会出现重复行。
  const memberList = [
    ...justCreated.filter(
      (created) => !serverMembers.some((member) => member.principal === created.principal),
    ),
    ...serverMembers,
  ]
  const memberPageCount = Math.max(1, Math.ceil(memberList.length / MEMBERS_PAGE_SIZE))
  const safeMemberPage = Math.min(pageIndex, memberPageCount - 1)
  const pageMembers = memberList.slice(
    safeMemberPage * MEMBERS_PAGE_SIZE,
    (safeMemberPage + 1) * MEMBERS_PAGE_SIZE,
  )

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
          <div
            className={notice.tone === 'ok' ? 'os-note' : 'os-alert'}
            role={notice.tone === 'ok' ? 'status' : 'alert'}
          >
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

        {isRoot && (
          <div className="os-field">
            <label htmlFor="member-search">搜索账号</label>
            <input
              id="member-search"
              className="os-input-plain"
              type="search"
              value={treeQuery}
              onChange={(event) => setTreeQuery(event.target.value)}
              placeholder="输入账号名，命中后自动展开整条路径"
            />
          </div>
        )}

        <Guard
          loading={accounts.loading}
          error={accounts.error}
          data={accounts.data}
          isEmpty={(data) => data.members.length === 0 && justCreated.length === 0}
          emptyTitle={isRoot ? '还没有成员' : '还没有账号'}
          emptyHint={isRoot ? '在下方创建第一个成员。' : '在下方创建第一个账号。'}
          onRetry={accounts.reload}
        >
          {() => {
            if (isRoot && tree.data?.tree) {
              return (
                <div className="os-table-wrap">
                  <table className="os-table">
                    {treeFolding.query && treeFolding.matched.size === 0 && (
                      <caption className="os-hint">没有命中的账号</caption>
                    )}
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
                          folding={treeFolding}
                          onToggleFold={toggleFold}
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
                    {pageMembers.map((member) => (
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
                {memberPageCount > 1 && (
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
                      disabled={safeMemberPage === 0}
                      onClick={() => setPageIndex(safeMemberPage - 1)}
                    >
                      上一页
                    </button>
                    <span className="os-hint">
                      第 {safeMemberPage + 1} / {memberPageCount} 页 · 共 {memberList.length} 条
                    </span>
                    <button
                      className="os-btn os-btn-ghost"
                      style={{ width: 'auto' }}
                      disabled={safeMemberPage >= memberPageCount - 1}
                      onClick={() => setPageIndex(safeMemberPage + 1)}
                    >
                      下一页
                    </button>
                  </div>
                )}
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
  folding,
  onToggleFold,
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
  folding: TreeFolding
  onToggleFold: (principal: string, expanded: boolean) => void
  readOnly: boolean
  busy: boolean
  onViewAs: (principal: string) => void
  onToggleStatus: (member: AccountMember) => void
  onIssue: (member: AccountMember) => void
  onToggleKey: (member: AccountMember) => void
  onReset: (member: AccountMember) => void
}) {
  // 搜索态只保留命中节点及其祖先路径，其余整支不渲染 —— 搜到了就必须看得见。
  if (folding.query && !folding.matched.has(node.principal)) return null
  const canFold = node.children.length > 0
  // 搜索时整条路径强制展开；平时默认展开两层，用户显式切换优先。
  const expanded = folding.query
    ? true
    : (foldOverrides.get(node.principal) ?? depth + 1 < DEFAULT_VISIBLE_DEPTH)
  const hidden = canFold && !expanded ? countTreeMembers(node) - 1 : 0
  return (
    <>
      <MemberRow
        member={node}
        isRoot
        depth={depth}
        readOnly={readOnly}
        busy={busy}
        fold={{ canToggle: canFold && !folding.query, expanded, hidden }}
        onToggleFold={onToggleFold}
        query={folding.query}
        onViewAs={onViewAs}
        onToggleStatus={onToggleStatus}
        onIssue={onIssue}
        onToggleKey={onToggleKey}
        onReset={onReset}
      />
      {expanded &&
        node.children.map((child) => (
          <TreeNodeRows
            key={child.principal}
            node={child}
            depth={depth + 1}
            folding={folding}
            onToggleFold={onToggleFold}
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
  fold,
  onToggleFold,
  query,
}: {
  member: AccountMember
  isRoot: boolean
  depth: number
  readOnly: boolean
  busy: boolean
  /** 仅树视图传入：能否折叠、当前是否展开、折叠了多少个后代。 */
  fold?: { canToggle: boolean; expanded: boolean; hidden: number }
  onToggleFold?: (principal: string, expanded: boolean) => void
  /** 搜索词：命中片段高亮。 */
  query?: string
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
        <span style={{ paddingLeft: depth * 16 }}>
          {fold?.canToggle && (
            <button
              type="button"
              className="os-link"
              style={{ marginRight: 6 }}
              aria-expanded={fold.expanded}
              aria-label={`${fold.expanded ? '收起' : '展开'} ${member.principal}`}
              onClick={() => onToggleFold?.(member.principal, !fold.expanded)}
            >
              <span aria-hidden="true">{fold.expanded ? '▾' : '▸'}</span>
            </button>
          )}
          {highlightPrincipal(member.principal, query ?? '')}
          {fold && fold.hidden > 0 && (
            <span className="os-hint">（还有 {fold.hidden} 个）</span>
          )}
        </span>
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

/** 默认展开两层：根的直接子节点是第一层，再展开一层，更深的收起。 */
const DEFAULT_VISIBLE_DEPTH = 2

/**
 * 用户显式切换过的折叠状态（账号 → 是否展开）。
 *
 * 放在模块级而不是组件 state：翻页、切换查看范围、组件重新挂载都不重置 ——
 * 用户折叠了就是不想看，不该因为一次刷新又全部弹开。
 */
const foldOverrides = new Map<string, boolean>()

/** 树视图的检索上下文：query 非空时只渲染命中路径，并强制展开。 */
interface TreeFolding {
  query: string
  matched: Set<string>
}

/** 命中节点 + 其全部祖先；路径之外的节点一律不渲染。 */
function collectMatchedPaths(nodes: AccountNode[], query: string): Set<string> {
  const matched = new Set<string>()
  const needle = query.trim().toLowerCase()
  if (!needle) return matched
  const walk = (node: AccountNode, ancestors: string[]) => {
    const path = [...ancestors, node.principal]
    if (node.principal.toLowerCase().includes(needle)) {
      for (const principal of path) matched.add(principal)
    }
    for (const child of node.children) walk(child, path)
  }
  for (const node of nodes) walk(node, [])
  return matched
}

/** 把账号名里第一处命中的片段包成 `<mark>`；未命中时原样返回。 */
function highlightPrincipal(text: string, query: string): ReactNode {
  const needle = query.trim().toLowerCase()
  if (!needle) return text
  const index = text.toLowerCase().indexOf(needle)
  if (index < 0) return text
  return (
    <>
      {text.slice(0, index)}
      <mark>{text.slice(index, index + needle.length)}</mark>
      {text.slice(index + needle.length)}
    </>
  )
}
