/**
 * 账号能力裁剪 —— 只决定"渲染哪些入口"，不做任何授权判定。
 *
 * 输入只有会话位置（`/auth/me` 的 parent/path/depth）与账号条目权限
 * （`/v1/accounts` 中自己的那条）。两者都来自服务端；前端不猜测、不缓存特权。
 */

import type { AccountMember, DataSource, AccountsPayload } from './accounts'
import type { Session } from './auth'

export const SCOPE_MANAGE_CHILDREN = 'account:manage_children'
export const SCOPE_GRANT = 'account:grant'

/** 非根界面的绝对禁用词（单一来源，测试与实现共用）。 */
export const BANNED_ACCOUNT_TERMS = [
  '子账号',
  '孙账号',
  '上级',
  '下级',
  '层级',
  '越级',
  '主账号',
  '根账号',
  '被管理',
  '受限',
  '管理员',
  // 层级关系的委婉说法同样算痕迹：子账号界面里不该出现"成员 / 邀请 / 加入"，
  // 也不该出现"团队 / 组织"这种暗示归属的措辞。
  '成员',
  '邀请',
  '加入',
  '团队',
  '组织',
] as const

export interface AccountCaps {
  isRoot: boolean
  canMembers: boolean
  canMatrix: boolean
  canTemplates: boolean
  canBackup: boolean
  canRecovery: boolean
  canSettings: boolean
  canSystemStatus: boolean
  canAllKeys: boolean
  canMyMembers: boolean
  canSubtreeMemory: boolean
  canViewAs: boolean
  canGrant: boolean
  /** 待审批队列只对能签发凭据的主体可见；其余账号整块不渲染。 */
  canApproveQueue: boolean
}

export interface AccountProfile {
  principal: string
  displayName: string | null
  scope: string | null
  identityPermissions: string[]
  accountPermissions: string[]
  parentAccountId: string | null
  path: string
  depth: number
  isRoot: boolean
  members: AccountMember[]
  /** 可管理的成员数（根 = 子树人数；成员 = 可见人数 - 自己）。 */
  memberCount: number
  source: DataSource
  loaded: boolean
  error: string | null
}

export function buildProfile(
  session: Session,
  payload: AccountsPayload | null,
  source: DataSource,
  error: string | null,
  /** 根账号的整棵子树人数（来自 `/v1/accounts/tree`）；列表接口只返回直接成员。 */
  subtreeMemberCount?: number,
): AccountProfile {
  const isRoot = session.parentAccountId === null && session.depth === 0
  const self = payload?.members.find((member) => member.principal === session.principal)
  const members = payload?.members ?? []
  const directCount = members.filter((member) => member.principal !== session.principal).length
  const memberCount = Math.max(0, subtreeMemberCount ?? directCount)
  return {
    principal: session.principal,
    displayName: session.displayName,
    scope: session.scope,
    identityPermissions: session.permissions,
    // `/v1/accounts` 只返回直接成员，不含调用者自己；此时权限要回落到登录令牌
    // 里的主体权限，否则成员会丢掉 `account:manage_children`，"我的成员"入口消失。
    accountPermissions: self?.permissions ?? session.permissions,
    parentAccountId: session.parentAccountId,
    path: session.path,
    depth: session.depth,
    isRoot,
    members,
    memberCount,
    source,
    loaded: payload !== null,
    error,
  }
}

export function deriveCaps(profile: AccountProfile | null): AccountCaps {
  const isRoot = profile?.isRoot === true
  const permissions = profile?.accountPermissions ?? []
  const canManageChildren = permissions.includes(SCOPE_MANAGE_CHILDREN)
  return {
    isRoot,
    canMembers: isRoot,
    canMatrix: isRoot,
    canTemplates: isRoot,
    canBackup: isRoot,
    canRecovery: isRoot,
    canSettings: isRoot,
    canSystemStatus: isRoot,
    canAllKeys: isRoot,
    canMyMembers: !isRoot && canManageChildren,
    canSubtreeMemory: isRoot,
    canViewAs: isRoot || canManageChildren,
    canGrant: !isRoot && permissions.includes(SCOPE_GRANT),
    canApproveQueue: isRoot || (profile?.scope ?? '') === 'L0',
  }
}

/**
 * 身份条文案：所有账号**完全一致**（品牌栏 + `我的工作空间`）。
 *
 * 顶部不承载任何层级信息：`主账号 · 可管理 N 位成员` 这类文案本身就是
 * "我是子账号"的破绽。可管理范围只在有权限的管理页里出现。
 */
export function identityBarRole(_profile: AccountProfile): string {
  return '我的工作空间'
}

/** 成员页的 Key 授权文案；只有根能看到真实归属与代用标记。 */
export function keyAuthorizationLabel(member: AccountMember, isRoot: boolean): string {
  if (!isRoot) return 'Key 归属：我的账号'
  return member.allow_superior_key_use
    ? '主账号可代用 · 已授权'
    : '仅本人使用 · 未授权代用'
}

/** 我的 Key 页面（非根）唯一允许出现的归属写法。 */
export function selfKeyLabel(): string {
  return 'Key 归属：我的账号'
}

/** 供测试使用的扫描器：返回命中的禁用词，空数组即通过。 */
export function findBannedTerms(text: string): string[] {
  return BANNED_ACCOUNT_TERMS.filter((term) => text.includes(term))
}
