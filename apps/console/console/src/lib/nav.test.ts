/**
 * 手机端底栏契约 + 七组导航契约。
 *
 * 2026-11 Boss 定稿：手机首屏从运行看板换成**企业总览**，并要求底栏加入
 * **贾维斯**且放在第二项 —— 企业总览 / 贾维斯 / 办公室 / 任务 / 知识 / 能力 /
 * 设置，共七项。这是产品决策，不是为了让测试变绿；改断言时须在提交说明里写明。
 *
 * 历史缺陷（已修）：底栏曾只有四项，贾维斯与「目标与任务」在手机上无入口，
 * 窄屏只能看到派活回执、打不开结果列表。这两条继续钉死。
 */

import { describe, expect, test } from 'vitest'
import { ALL_NAV, DAILY_NAV_KEYS, MERGE_MAP, MOBILE_NAV_KEYS, NAV_GROUPS, dailyNavKeysFor, groupForPage, isMergedAwayKey, mobileNavKeysFor, navGroupsFor, navGroupsForScope, resolveMergedTarget, resolveNavScope, visibleNavItemKeys, visibleNavKeys } from './nav'
import { buildProfile, deriveCaps } from './accountCaps'
import type { Session } from './auth'

/** 一个最普通的成员会话：没有系统设置、没有成员管理。 */
function memberCaps() {
  const session: Session = {
    token: 't',
    principal: 'member',
    displayName: '成员',
    scope: 'human',
    permissions: [],
    expiresAt: 0,
    client: 'web',
    isRoot: false,
  }
  return deriveCaps(
    buildProfile(
      session,
      { members: [{ principal: 'member', permissions: [], status: 'active' }], count: 1 },
      'live',
      null,
    ),
  )
}

describe('手机端底部导航', () => {
  test('恰好七项，首项是企业总览、第二项是贾维斯（Boss 决定）', () => {
    expect(MOBILE_NAV_KEYS).toHaveLength(7)
    // 契约锚点：首项必须是企业总览，不能退回运行看板。
    expect(MOBILE_NAV_KEYS[0]).toBe('enterprise-overview')
    // 契约锚点：贾维斯固定在第二项，不能被权限裁剪或排序挤走。
    expect(MOBILE_NAV_KEYS[1]).toBe('butler')
    expect(MOBILE_NAV_KEYS).not.toContain('overview')
    expect(MOBILE_NAV_KEYS).not.toContain('workbench')
  })

  test('七项覆盖办公室、任务、知识、能力、设置', () => {
    expect(MOBILE_NAV_KEYS).toContain('office')
    expect(MOBILE_NAV_KEYS).toContain('task-center')
    expect(MOBILE_NAV_KEYS).toContain('knowledge')
    expect(MOBILE_NAV_KEYS).toContain('capability-center')
    expect(MOBILE_NAV_KEYS).not.toContain('skills-center')
    expect(MOBILE_NAV_KEYS).toContain('settings')
  })

  test('每个 key 都能在 ALL_NAV 里找到（否则底栏会渲染出空洞）', () => {
    for (const key of MOBILE_NAV_KEYS) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })

  test('非根账号看不到设置时，第七项回退「我的账号与安全」而不是死入口', () => {
    const keys = mobileNavKeysFor(memberCaps())
    expect(keys).toHaveLength(7)
    expect(keys[0]).toBe('enterprise-overview')
    expect(keys[1]).toBe('butler')
    expect(keys[6]).toBe('my-account-security')
    // 「我的」已被并进「我的账号与安全」，底栏不再单独放它。
    expect(keys).not.toContain('me')
  })

  test('账号安全、我的数据、恢复码、关于与隐私仍可从设置组进入', () => {
    for (const key of ['account', 'my-data', 'recovery', 'about']) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })
})

describe('七组导航（简约版：可见 23 个）', () => {
  test('恰好七组，顺序固定', () => {
    expect(NAV_GROUPS.map((group) => group.label)).toEqual([
      '首页',
      'Owner',
      '办公室',
      '任务',
      '知识',
      '能力',
      '设置',
    ])
  })

  test('Owner 组只有三项：总览 + 成员与权限 + 全部任务与审计', () => {
    const owner = NAV_GROUPS.find((group) => group.key === 'owner')
    expect(owner?.items.map((item) => item.key)).toEqual([
      'owner-overview',
      'members-permissions',
      'tasks-audit',
    ])
    expect(owner?.items.map((item) => item.label)).toEqual([
      '全局总览',
      '成员与权限',
      '全部任务与审计',
    ])
    // 被并掉的旧 key 不在可见组里，只在 ALL_NAV 注册表里。
    for (const key of ['owner-members', 'owner-tasks', 'owner-audit']) {
      expect(owner?.items.some((item) => item.key === key)).toBe(false)
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })

  test('每组都有主视图，且主视图在本组内', () => {
    for (const group of NAV_GROUPS) {
      expect(group.items.some((item) => item.key === group.defaultKey)).toBe(true)
    }
  })

  test('操作台在首页组首位，运行看板不再单独占入口', () => {
    const home = NAV_GROUPS.find((group) => group.key === 'home')
    expect(home?.items.map((item) => item.key)).toEqual(['console', 'enterprise-overview', 'butler'])
    // 点"首页"组名落到操作台（登录后的默认页），不是企业总览。
    expect(home?.defaultKey).toBe('console')
    expect(ALL_NAV.some((item) => item.key === 'overview')).toBe(false)
    expect(ALL_NAV.some((item) => item.key === 'workbench')).toBe(false)
  })

  test('办公室组 3 项：办公室、我的员工、组织', () => {
    expect(NAV_GROUPS.find((group) => group.key === 'office')?.items.map((item) => item.key)).toEqual([
      'office',
      'my-employees',
      'org',
    ])
  })

  test('员工管理入口独立于只读的能力注册表；能力注册表已并进能力中心', () => {
    const mine = ALL_NAV.find((item) => item.key === 'my-employees')
    const registry = ALL_NAV.find((item) => item.key === 'employees')
    expect(mine?.label).toBe('我的员工')
    expect(registry?.label).toBe('能力注册表')
    // 一个在办公室（要动手），一个在注册表里（只读旧页面，不占可见入口）。
    expect(NAV_GROUPS.find((g) => g.key === 'office')?.items.some((i) => i.key === 'my-employees')).toBe(true)
    expect(NAV_GROUPS.flatMap((g) => g.items).some((i) => i.key === 'employees')).toBe(false)
    expect(resolveMergedTarget('employees')).toEqual({ page: 'capability-center', tab: 'roster' })
    // 能力组现在只有合并页一个入口，点组名就落到它。
    expect(NAV_GROUPS.find((g) => g.key === 'capabilities')?.items.map((i) => i.key)).toEqual([
      'capability-center',
    ])
    expect(NAV_GROUPS.find((g) => g.key === 'capabilities')?.defaultKey).toBe('capability-center')
  })

  test('设置组 8 项：系统设置、我的账号与安全、审批签发、备份、通知、预检、关于、系统状态与诊断', () => {
    const settings = NAV_GROUPS.find((group) => group.key === 'settings')
    expect(settings?.items.map((item) => item.key)).toEqual([
      'settings',
      'my-account-security',
      'approval',
      'backup',
      'notifications',
      'preflight',
      'about',
      'system-diagnostics',
    ])
    expect(settings?.items.find((item) => item.key === 'approval')?.label).toBe('审批签发')
  })
})

describe('导航去重（截图事故回归）', () => {
  test('每个 key 只出现一次', () => {
    const keys = ALL_NAV.map((item) => item.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  test('每个显示名只出现一次（防止分组拼接时重复渲染）', () => {
    const labels = ALL_NAV.map((item) => item.label)
    expect(new Set(labels).size).toBe(labels.length)
  })

  test('分组之间没有相同入口', () => {
    const overlaps = ALL_NAV.filter(
      (item, index) =>
        ALL_NAV.findIndex((other) => other.key === item.key) !== index,
    )
    expect(overlaps).toEqual([])
  })
})

describe('Owner 专属入口（F 组）', () => {
  /** Owner 会话：首次注册的账号，后端下发 is_owner。 */
  function ownerCaps() {
    const session: Session = {
      token: 't',
      principal: 'owner',
      displayName: 'Owner',
      scope: 'human',
      permissions: [],
      expiresAt: 0,
      client: 'web',
      isRoot: true,
      isOwner: true,
    }
    return deriveCaps(
      buildProfile(
        session,
        { members: [{ principal: 'owner', permissions: [], status: 'active' }], count: 1 },
        'live',
        null,
      ),
    )
  }

  test('Owner 看得到三项 Owner 组入口；普通成员只看得到「全部任务与审计」', () => {
    const ownerKeys = visibleNavKeys(ownerCaps())
    for (const key of ['owner-overview', 'members-permissions', 'tasks-audit']) {
      expect(ownerKeys.has(key)).toBe(true)
    }
    // 被并掉的 Owner 旧 key 仍在深链白名单里（旧深链不断），且只对 Owner 开放。
    for (const key of ['owner-members', 'owner-tasks', 'owner-audit']) {
      expect(ownerKeys.has(key)).toBe(true)
    }
    const memberKeys = visibleNavKeys(memberCaps())
    expect(memberKeys.has('owner-overview')).toBe(false)
    expect(memberKeys.has('members-permissions')).toBe(false)
    for (const key of ['owner-members', 'owner-tasks', 'owner-audit']) {
      expect(memberKeys.has(key)).toBe(false)
    }
    // 「全部任务与审计」人人可见（旧的操作记录/事件流/数据中心本来就人人可见），
    // 所以成员的 Owner 组里只剩这一项 —— 不是"成员看不到 Owner 组"失效，
    // 而是 Owner 组里唯一人人可见的入口本来就在这里。
    expect(memberKeys.has('tasks-audit')).toBe(true)
    const memberOwnerGroup = navGroupsFor(memberCaps()).find((group) => group.key === 'owner')
    expect(memberOwnerGroup?.items.map((item) => item.key)).toEqual(['tasks-audit'])
  })

  test('Owner 入口在 Owner 组（不是设置组），不另开第八组', () => {
    expect(NAV_GROUPS).toHaveLength(7)
    const owner = NAV_GROUPS.find((group) => group.key === 'owner')
    expect(owner?.items.map((item) => item.key)).toEqual([
      'owner-overview',
      'members-permissions',
      'tasks-audit',
    ])
    const settings = NAV_GROUPS.find((group) => group.key === 'settings')
    expect(settings?.items.some((item) => item.key === 'owner-overview')).toBe(false)
    expect(settings?.items.some((item) => item.key === 'tasks-audit')).toBe(false)
  })

  test('旧会话没有 is_owner 时回落到 isRoot，Owner 入口不消失', () => {
    const session: Session = {
      token: 't',
      principal: 'owner',
      displayName: 'Owner',
      scope: 'human',
      permissions: [],
      expiresAt: 0,
      client: 'web',
      isRoot: true,
    }
    const caps = deriveCaps(
      buildProfile(
        session,
        { members: [{ principal: 'owner', permissions: [], status: 'active' }], count: 1 },
        'live',
        null,
      ),
    )
    expect(caps.isOwner).toBe(true)
    expect(visibleNavKeys(caps).has('owner-overview')).toBe(true)
  })
})

describe('日常 / 全部导航收小（2026-11，Joseph 定稿）', () => {
  /** Owner/根会话：8 个日常入口全可见（含 requires root 的系统设置）。 */
  function ownerCaps() {
    const session: Session = {
      token: 't',
      principal: 'owner',
      displayName: 'Owner',
      scope: 'human',
      permissions: [],
      expiresAt: 0,
      client: 'web',
      isRoot: true,
      isOwner: true,
    }
    return deriveCaps(
      buildProfile(
        session,
        { members: [{ principal: 'owner', permissions: [], status: 'active' }], count: 1 },
        'live',
        null,
      ),
    )
  }

  test('日常固定是这 8 个，顺序就是主线：聊天→总览→目标→任务→审批→产出→资料→设置', () => {
    expect(DAILY_NAV_KEYS).toEqual([
      'console',
      'enterprise-overview',
      'butler',
      'task-center',
      'workflow-approvals',
      'files',
      'knowledge',
      'settings',
    ])
  })

  test('可见 23 个入口，可路由 51 个：46 个旧 key 一个不少，另加 5 个新合并页', () => {
    const visible = NAV_GROUPS.flatMap((group) => group.items)
    expect(visible).toHaveLength(23)
    expect(new Set(visible.map((item) => item.key)).size).toBe(23)
    // 46 个旧 key 全在注册表里（深链不断），5 个新 key 也在（新入口可路由）。
    expect(ALL_NAV).toHaveLength(51)
    for (const key of DAILY_NAV_KEYS) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
    for (const key of ['workflow', 'goals', 'projects', 'knowledge-enterprise', 'memory', 'skills-center', 'tools-center', 'employees', 'business', 'apps', 'owner-members', 'members', 'matrix', 'templates', 'owner-tasks', 'owner-audit', 'audit', 'events', 'data', 'my-keys', 'account', 'recovery', 'my-members', 'me', 'status', 'trust', 'network', 'identity']) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
    for (const key of ['members-permissions', 'tasks-audit', 'capability-center', 'my-account-security', 'system-diagnostics']) {
      expect(ALL_NAV.some((item) => item.key === key)).toBe(true)
    }
  })

  test('可见清单与深链白名单是两回事：侧栏 23 个，白名单 51 个', () => {
    expect(visibleNavItemKeys(ownerCaps()).size).toBe(23)
    // 成员看不到系统设置、组织、Owner 专属与审批签发等 7 项，可见 16 个。
    expect(visibleNavItemKeys(memberCaps()).size).toBe(16)
    for (const key of ['settings', 'org', 'owner-overview', 'members-permissions', 'approval', 'backup', 'preflight']) {
      expect(visibleNavItemKeys(memberCaps()).has(key)).toBe(false)
    }
    // 白名单比可见清单多 28 个被并掉的旧 key（根少 1 个：根不需要成员面的「我的账号」）。
    expect(visibleNavKeys(ownerCaps()).size).toBe(50)
  })

  test('日常模式下 Owner 看到 8 个，非根账号看到 7 个（没有系统设置），其余权限规则不变', () => {
    expect(dailyNavKeysFor(ownerCaps())).toEqual(DAILY_NAV_KEYS)
    const memberDaily = dailyNavKeysFor(memberCaps())
    expect(memberDaily).toHaveLength(7)
    expect(memberDaily).not.toContain('settings')
    expect(memberDaily).toEqual(DAILY_NAV_KEYS.filter((key) => key !== 'settings'))
    // Owner 专属入口本来就不在日常里，日常/全部都不应把它塞给普通账号。
    expect(dailyNavKeysFor(memberCaps())).not.toContain('owner-overview')
  })

  test('日常分组只剩四个组，组内顺序与日常清单一致', () => {
    const groups = navGroupsForScope(ownerCaps(), 'daily')
    expect(groups.map((group) => group.key)).toEqual(['home', 'tasks', 'knowledge', 'settings'])
    expect(groups.flatMap((group) => group.items.map((item) => item.key))).toEqual(DAILY_NAV_KEYS)
  })

  test('全部模式与原来 navGroupsFor 完全一致，一个入口不少', () => {
    expect(navGroupsForScope(ownerCaps(), 'all')).toEqual(navGroupsFor(ownerCaps()))
    expect(navGroupsForScope(memberCaps(), 'all').flatMap((g) => g.items)).toHaveLength(
      navGroupsFor(memberCaps()).flatMap((g) => g.items).length,
    )
  })

  test('当前页不在日常里时（深链跳进来），侧栏仍保留这一项，不让人找不到自己', () => {
    const groups = navGroupsForScope(ownerCaps(), 'daily', 'office')
    const keys = groups.flatMap((group) => group.items.map((item) => item.key))
    expect(keys).toContain('office')
    expect(keys).toHaveLength(9)
  })

  test('无权看的当前页不会被"偷渡"进日常', () => {
    const groups = navGroupsForScope(memberCaps(), 'daily', 'settings')
    const keys = groups.flatMap((group) => group.items.map((item) => item.key))
    expect(keys).not.toContain('settings')
    expect(keys).toHaveLength(7)
  })

  test('范围解析：URL 参数优先于本地记住的值，都没有或写错了回落到日常', () => {
    expect(resolveNavScope('all', 'daily')).toBe('all')
    expect(resolveNavScope('daily', 'all')).toBe('daily')
    expect(resolveNavScope(null, 'all')).toBe('all')
    expect(resolveNavScope(null, 'daily')).toBe('daily')
    expect(resolveNavScope(null, null)).toBe('daily')
    expect(resolveNavScope('bogus', 'bogus')).toBe('daily')
    expect(resolveNavScope(null, 'bogus')).toBe('daily')
  })
})

describe('合并映射 MERGE_MAP（简约版：28 个旧 key 一个不少）', () => {
  /** Owner/根会话：8 个日常入口全可见（含 requires root 的系统设置）。 */
  function ownerCaps() {
    const session: Session = {
      token: 't',
      principal: 'owner',
      displayName: 'Owner',
      scope: 'human',
      permissions: [],
      expiresAt: 0,
      client: 'web',
      isRoot: true,
      isOwner: true,
    }
    return deriveCaps(
      buildProfile(
        session,
        { members: [{ principal: 'owner', permissions: [], status: 'active' }], count: 1 },
        'live',
        null,
      ),
    )
  }

  const MERGED_KEYS = [
    'workflow', 'goals', 'projects',
    'knowledge-enterprise', 'memory',
    'skills-center', 'tools-center', 'employees', 'business', 'apps',
    'owner-members', 'members', 'matrix', 'templates',
    'owner-tasks', 'owner-audit', 'audit', 'events', 'data',
    'my-keys', 'account', 'recovery', 'my-members', 'me',
    'status', 'trust', 'network', 'identity',
  ]

  test('恰好 28 个被并掉的旧 key，与可见清单不重叠', () => {
    expect(Object.keys(MERGE_MAP)).toHaveLength(28)
    expect(Object.keys(MERGE_MAP).sort()).toEqual([...MERGED_KEYS].sort())
    const visible = new Set(NAV_GROUPS.flatMap((group) => group.items.map((item) => item.key)))
    for (const key of Object.keys(MERGE_MAP)) {
      expect(visible.has(key)).toBe(false)
      expect(isMergedAwayKey(key)).toBe(true)
    }
    for (const key of visible) {
      expect(isMergedAwayKey(key)).toBe(false)
    }
  })

  test('每个映射指向的合并页都在可见清单里，tab 非空', () => {
    const visible = new Set(NAV_GROUPS.flatMap((group) => group.items.map((item) => item.key)))
    for (const [key, target] of Object.entries(MERGE_MAP)) {
      expect(visible.has(target.page)).toBe(true)
      expect(target.tab.length).toBeGreaterThan(0)
      expect(resolveMergedTarget(key)).toEqual(target)
    }
  })

  test('多个旧 key 可以指向同一标签（如成员管理与成员与权限管理都指向 members 标签）', () => {
    expect(MERGE_MAP['owner-members']).toEqual({ page: 'members-permissions', tab: 'members' })
    expect(MERGE_MAP['members']).toEqual({ page: 'members-permissions', tab: 'members' })
    expect(MERGE_MAP['owner-audit']).toEqual({ page: 'tasks-audit', tab: 'audit' })
    expect(MERGE_MAP['audit']).toEqual({ page: 'tasks-audit', tab: 'audit' })
  })

  test('resolveMergedTarget 对可见 key 返回 undefined', () => {
    for (const key of ['console', 'enterprise-overview', 'butler', 'task-center', 'workflow-approvals', 'files', 'knowledge', 'settings', 'members-permissions', 'tasks-audit', 'capability-center', 'my-account-security', 'system-diagnostics']) {
      expect(resolveMergedTarget(key)).toBeUndefined()
    }
    expect(resolveMergedTarget('不存在的key')).toBeUndefined()
  })

  test('深链白名单仍含全部 46 个旧 key：?p=workflow / ?p=memory 不会被踢回总览', () => {
    const visible = visibleNavKeys(ownerCaps())
    for (const key of ['workflow', 'goals', 'projects', 'knowledge-enterprise', 'memory', 'skills-center', 'tools-center', 'employees', 'business', 'apps', 'audit', 'events', 'data', 'my-keys', 'account', 'me', 'status', 'trust', 'network', 'identity']) {
      expect(visible.has(key)).toBe(true)
    }
  })

  test('groupForPage：被并掉的 key 返回合并页所在的组', () => {
    expect(groupForPage('workflow')?.key).toBe('tasks')
    expect(groupForPage('goals')?.key).toBe('tasks')
    expect(groupForPage('memory')?.key).toBe('knowledge')
    expect(groupForPage('apps')?.key).toBe('capabilities')
    expect(groupForPage('matrix')?.key).toBe('owner')
    expect(groupForPage('audit')?.key).toBe('owner')
    expect(groupForPage('recovery')?.key).toBe('settings')
    expect(groupForPage('status')?.key).toBe('settings')
    expect(groupForPage('capability-center')?.key).toBe('capabilities')
    expect(groupForPage('tasks-audit')?.key).toBe('owner')
    expect(groupForPage('不存在的key')).toBeUndefined()
  })

  test('合并页的 requires：成员与权限只对根与 Owner，四个合并页人人可见', () => {
    const ownerKeys = visibleNavKeys(ownerCaps())
    const memberKeys = visibleNavKeys(memberCaps())
    // members-permissions：合并前最严（owner-members 要 owner，members/matrix/templates 要 root）。
    expect(ownerKeys.has('members-permissions')).toBe(true)
    expect(memberKeys.has('members-permissions')).toBe(false)
    expect(visibleNavItemKeys(ownerCaps()).has('members-permissions')).toBe(true)
    expect(visibleNavItemKeys(memberCaps()).has('members-permissions')).toBe(false)
    // tasks-audit / capability-center / my-account-security / system-diagnostics：
    // 合并前至少有一项人人可见，合并页不设 requires，页内按身份分流。
    for (const key of ['tasks-audit', 'capability-center', 'my-account-security', 'system-diagnostics']) {
      expect(ownerKeys.has(key)).toBe(true)
      expect(memberKeys.has(key)).toBe(true)
      expect(visibleNavItemKeys(memberCaps()).has(key)).toBe(true)
    }
  })
})
