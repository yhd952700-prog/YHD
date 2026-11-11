/**
 * 账号能力裁剪的契约测试。
 *
 * 钉住三件事：
 * 1. 根 / 成员（depth=1）/ 更深（depth>=2）三层的入口集合；
 * 2. 未授权入口**不返回**（navFor 过滤而不是置灰）；
 * 3. 非根可见文案里绝不出现结构词（单一来源 BANNED_ACCOUNT_TERMS）。
 */

import { describe, expect, test } from 'vitest'
import type { Session } from './auth'
import type { AccountsPayload } from './accounts'
import {
  BANNED_ACCOUNT_TERMS,
  SCOPE_MANAGE_CHILDREN,
  buildProfile,
  deriveCaps,
  findBannedTerms,
  identityBarRole,
  keyAuthorizationLabel,
} from './accountCaps'
import { mobileNavKeysFor, navFor, visibleNavKeys } from './nav'

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    token: 't',
    principal: 'root',
    displayName: '根',
    scope: 'human',
    permissions: [],
    expiresAt: 0,
    client: 'web',
    isRoot: true,
    ...overrides,
  }
}

const PAYLOAD: AccountsPayload = {
  members: [
    { principal: 'root', permissions: ['*'], status: 'active' },
    { principal: 'member-a', permissions: ['account:read', 'model:use'], status: 'active' },
    {
      principal: 'member-b',
      permissions: ['account:read', SCOPE_MANAGE_CHILDREN],
      status: 'active',
    },
  ],
  count: 3,
}

describe('三层位置 → 能力裁剪', () => {
  test('列表不含自己时（接口只返回直接成员），成员仍按令牌权限保留"我的成员"', () => {
    const profile = buildProfile(
      makeSession({
        principal: 'member-a',
        isRoot: false,
        permissions: [SCOPE_MANAGE_CHILDREN],
      }),
      { members: [{ principal: 'deep-a', permissions: [], status: 'active' }], count: 1 },
      'live',
      null,
    )
    const caps = deriveCaps(profile)
    expect(caps.canMyMembers).toBe(true)
    expect(visibleNavKeys(caps).has('my-members')).toBe(true)
  })

  test('depth=0 且无 parent → 根，看到全部治理入口', () => {
    const profile = buildProfile(makeSession(), PAYLOAD, 'live', null)
    const caps = deriveCaps(profile)
    expect(profile.isRoot).toBe(true)
    // 排掉自己后的可管理人数。
    expect(profile.memberCount).toBe(2)
    expect(caps.canMembers).toBe(true)
    expect(caps.canMatrix).toBe(true)
    expect(caps.canBackup).toBe(true)
    expect(caps.canRecovery).toBe(true)
    expect(caps.canSettings).toBe(true)
    expect(caps.canAllKeys).toBe(true)
    expect(caps.canSubtreeMemory).toBe(true)
  })

  test('depth=1 → 成员：能管成员但看不到矩阵 / 备份 / 系统入口', () => {
    const session = makeSession({
      principal: 'member-b',
      isRoot: false,
    })
    const caps = deriveCaps(buildProfile(session, PAYLOAD, 'live', null))
    expect(caps.isRoot).toBe(false)
    expect(caps.canMyMembers).toBe(true)
    expect(caps.canMembers).toBe(false)
    expect(caps.canMatrix).toBe(false)
    expect(caps.canTemplates).toBe(false)
    expect(caps.canBackup).toBe(false)
    expect(caps.canRecovery).toBe(false)
    expect(caps.canSettings).toBe(false)
    expect(caps.canSystemStatus).toBe(false)
    expect(caps.canAllKeys).toBe(false)
    expect(caps.canSubtreeMemory).toBe(false)
  })

  test('depth>=2 → 更深层默认没有"我的成员"，除非服务端另行授予', () => {
    const session = makeSession({
      principal: 'deep',
      isRoot: false,
    })
    const payload: AccountsPayload = {
      members: [{ principal: 'deep', permissions: ['account:read'], status: 'active' }],
      count: 1,
    }
    const caps = deriveCaps(buildProfile(session, payload, 'live', null))
    expect(caps.canMyMembers).toBe(false)
    expect(caps.canViewAs).toBe(false)
    expect(caps.canGrant).toBe(false)
    expect(caps.isRoot).toBe(false)
  })

  test('数据未回来时不误判为根（isRoot 来自会话位置）', () => {
    const member = makeSession({ principal: 'member-a', isRoot: false })
    const caps = deriveCaps(buildProfile(member, null, 'live', null))
    expect(caps.isRoot).toBe(false)
    expect(caps.canMembers).toBe(false)
  })
})

describe('导航裁剪：不渲染而不是置灰', () => {
  const rootCaps = deriveCaps(buildProfile(makeSession(), PAYLOAD, 'live', null))
  const memberCaps = deriveCaps(
    buildProfile(
      makeSession({
        principal: 'member-b',
        isRoot: false,
      }),
      PAYLOAD,
      'live',
      null,
    ),
  )

  test('根可见的账号面入口', () => {
    const visible = visibleNavKeys(rootCaps)
    for (const key of [
      'members',
      'matrix',
      'templates',
      'backup',
      'recovery',
      'my-keys',
      'memory',
      'settings',
      'status',
      'preflight',
    ]) {
      expect(visible.has(key)).toBe(true)
    }
    expect(navFor(rootCaps).primary.find((item) => item.key === 'my-keys')?.label).toBe(
      '全部 Key',
    )
    // 根不需要成员面的"我的成员"（已有完整的成员与权限管理）。
    expect(visible.has('my-members')).toBe(false)
  })

  test('成员不可见入口在返回集合里根本不存在', () => {
    const visible = visibleNavKeys(memberCaps)
    for (const key of ['members', 'matrix', 'templates', 'backup', 'recovery', 'settings', 'status', 'preflight']) {
      expect(visible.has(key)).toBe(false)
    }
    for (const key of ['enterprise-overview', 'memory', 'my-keys', 'audit']) {
      expect(visible.has(key)).toBe(true)
    }
    // 运行看板与自由对话已并入首页 / 贾维斯，不再作为独立入口出现。
    expect(visible.has('overview')).toBe(false)
    expect(visible.has('workbench')).toBe(false)
  })

  test('成员导航标签不含任何结构词', () => {
    const { primary, governance, footer } = navFor(memberCaps)
    const labels = [...primary, ...governance, ...footer].map((item) => item.label)
    expect(findBannedTerms(labels.join(' '))).toEqual([])
  })

  test('没有签发范围的账号整块不渲染待审批队列', () => {
    expect(rootCaps.canApproveQueue).toBe(true)
    expect(memberCaps.canApproveQueue).toBe(false)
  })

  test('审批入口只对能签发凭据的一方出现，其余账号导航里没有这一项', () => {
    expect(visibleNavKeys(rootCaps).has('approval')).toBe(true)
    expect(visibleNavKeys(memberCaps).has('approval')).toBe(false)
    // 手机底栏同样不能留死入口：成员看不到系统设置时，第七项回退「我的」。
    expect(mobileNavKeysFor(memberCaps)).not.toContain('approval')
    expect(mobileNavKeysFor(memberCaps)).toContain('me')
    expect(mobileNavKeysFor(rootCaps)).toContain('settings')
    expect(mobileNavKeysFor(rootCaps)).not.toContain('approval')
  })

  test('禁用词单一来源覆盖层级与归属措辞', () => {
    for (const term of [
      '子账号',
      '上级',
      '下级',
      '层级',
      '管理员',
      '成员',
      '邀请',
      '加入',
      '团队',
      '组织',
    ]) {
      expect(BANNED_ACCOUNT_TERMS).toContain(term)
    }
  })
})

describe('身份条与 Key 归属文案', () => {
  test('根与非根身份条文案完全一致，且不含任何层级词', () => {
    const rootProfile = buildProfile(makeSession(), PAYLOAD, 'live', null)
    const member = makeSession({ principal: 'member-a', isRoot: false })
    const memberProfile = buildProfile(member, PAYLOAD, 'live', null)

    expect(identityBarRole(rootProfile)).toBe('我的工作空间')
    expect(identityBarRole(memberProfile)).toBe(identityBarRole(rootProfile))
    expect(findBannedTerms(identityBarRole(rootProfile))).toEqual([])
    expect(findBannedTerms(identityBarRole(memberProfile))).toEqual([])
  })

  test('非根永远只看到"Key 归属：我的账号"，看不到授权来源', () => {
    const member = makeSession({ principal: 'member-a', isRoot: false })
    const profile = buildProfile(member, PAYLOAD, 'live', null)
    for (const row of profile.members) {
      expect(keyAuthorizationLabel(row, false)).toBe('Key 归属：我的账号')
    }
  })

  test('根能看到真实归属与代用标记', () => {
    const allowed = { principal: 'a', permissions: [], status: 'active', allow_superior_key_use: true }
    const denied = { principal: 'b', permissions: [], status: 'active', allow_superior_key_use: false }
    expect(keyAuthorizationLabel(allowed, true)).toBe('主账号可代用 · 已授权')
    expect(keyAuthorizationLabel(denied, true)).toBe('仅本人使用 · 未授权代用')
  })
})
