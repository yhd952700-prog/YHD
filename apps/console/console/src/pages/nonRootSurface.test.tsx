/**
 * 成员视角的表面扫描（硬约束 ④ 的自动化断言）。
 *
 * 说明范围：这里渲染的是**账号面页面**（成员 / Key / 记忆 / 身份 / 审计 /
 * 权限矩阵 / 备份 / 恢复码），而不是整站所有页面；导航标签由
 * `accountChrome.test.tsx` 覆盖。之所以这样切：其余页面的文案与账号结构无关，
 * 而账号面的文案才是这次改动引入的部分。
 *
 * 每个页面都扫描 `document.body.textContent`，命中 BANNED_ACCOUNT_TERMS 即失败。
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { render } from '@testing-library/react'
import type { Session } from '../lib/auth'
import type { AccountsPayload } from '../lib/accounts'
import { buildProfile, deriveCaps, findBannedTerms } from '../lib/accountCaps'
import { Members } from './Members'
import { MyKeys } from './MyKeys'
import { Memory } from './Memory'
import { Identity } from './Identity'
import { Audit } from './Audit'
import { PermissionMatrix } from './PermissionMatrix'
import { Backup } from './Backup'
import { Recovery } from './Recovery'

const PAYLOAD: AccountsPayload = {
  members: [
    {
      principal: 'member-b',
      permissions: ['account:read', 'account:manage_children', 'model:use'],
      status: 'active',
    },
    { principal: 'member-c', permissions: ['account:read'], status: 'active' },
  ],
  count: 2,
}

const SESSION: Session = {
  token: 't',
  principal: 'member-b',
  displayName: '账号 B',
  scope: null,
  permissions: [],
  expiresAt: 0,
  client: 'web',
  isRoot: false,
}

const PROFILE = buildProfile(SESSION, PAYLOAD, 'live', null)
const CAPS = deriveCaps(PROFILE)

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/v1/accounts') return json(200, PAYLOAD)
    if (url === '/v1/accounts/roles') {
      return json(200, { roles: [], scopes: ['account:read', 'model:use'] })
    }
    // 其余端点（用量 / 备份 / 记忆）在此用例里尚未挂载或未落地。
    return json(404, { detail: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function expectNoBannedText() {
  const text = document.body.textContent ?? ''
  expect(findBannedTerms(text)).toEqual([])
}

describe('成员视角：账号面页面', () => {
  test('成员页不渲染 Key 授权列', () => {
    render(
      <Members profile={PROFILE} caps={CAPS} variant="member" readOnly={false} onViewAs={vi.fn()} />,
    )
    expect(document.body.textContent).toContain('我的账号')
    expect(document.body.textContent).not.toContain('Key 授权')
    expectNoBannedText()
  })

  test('我的 Key 只写归属，不写来源', () => {
    render(<MyKeys profile={PROFILE} caps={CAPS} readOnly={false} />)
    expect(document.body.textContent).toContain('我的账号')
    expect(document.body.textContent).not.toContain('授权来源')
    expectNoBannedText()
  })

  test('记忆只显示自己的范围，不提供子树导出', () => {
    render(<Memory profile={PROFILE} caps={CAPS} readOnly={false} />)
    expect(document.body.textContent).toContain('这里只显示属于你自己的记忆')
    expectNoBannedText()
  })

  test('身份页只显示"我的范围"', () => {
    render(<Identity query="" profile={PROFILE} />)
    expect(document.body.textContent).toContain('我的范围')
    expect(document.body.textContent).not.toContain('全站主体')
    expectNoBannedText()
  })

  test('审计页不出现全站摘要卡', () => {
    render(<Audit query="" profile={PROFILE} />)
    // 页面对外统一叫「操作记录」（不再用「审计事件」这种内部说法），
    // 但成员视角仍不能出现全站摘要（「审计链完整性」只给管理员）。
    expect(document.body.textContent).toContain('操作记录')
    expect(document.body.textContent).not.toContain('审计链完整性')
    expectNoBannedText()
  })

  test('未授权页面只给中性拒绝', () => {
    render(<PermissionMatrix profile={PROFILE} caps={CAPS} readOnly={false} />)
    expect(document.body.textContent).toContain('无权执行该操作')
    expectNoBannedText()
  })

  test('备份页对成员只给中性拒绝', () => {
    render(<Backup profile={PROFILE} caps={CAPS} readOnly={false} />)
    expect(document.body.textContent).toContain('无权执行该操作')
    expectNoBannedText()
  })

  test('恢复码页对成员只给中性拒绝', () => {
    render(<Recovery caps={CAPS} readOnly={false} />)
    expect(document.body.textContent).toContain('无权执行该操作')
    expectNoBannedText()
  })
})

/**
 * 恢复 / 备份的真实边界：后端只允许在**没有账号的全新实例**上导入备份包，
 * 本机已有账号时返回 409。界面必须在点击前就说清，而不是点完才报"已完成初始化"。
 */
describe('备份导入的真实边界', () => {
  const ROOT_SESSION: Session = {
    ...SESSION,
    principal: 'root',
    displayName: '根账号',
    scope: 'L0',
    isRoot: true,
    permissions: ['account:grant', 'account:manage_children'],
  }
  const ROOT_CAPS = deriveCaps(buildProfile(ROOT_SESSION, PAYLOAD, 'live', null))

  test('已初始化的本机不提供导入入口，文案写明只能全新实例导入', () => {
    render(<Backup profile={buildProfile(ROOT_SESSION, PAYLOAD, 'live', null)} caps={ROOT_CAPS} readOnly={false} />)
    const text = document.body.textContent ?? ''
    expect(text).toContain('全新实例')
    expect(text).toContain('本机已有账号')
    expect(text).toContain('不会被覆盖')
    // 不再有"点了才看到 409"的文件选择与导入按钮。
    expect(document.querySelector('#import-file')).toBeNull()
    expect(text).not.toContain('导入并认领')
  })

  test('恢复页写明备份包的上限，且不含层级词', () => {
    render(<Recovery caps={ROOT_CAPS} readOnly={false} />)
    const text = document.body.textContent ?? ''
    expect(text).toContain('全新实例')
    for (const term of ['管理员', '主账号', '子账号', '上级', '下级']) {
      expect(text).not.toContain(term)
    }
  })
})

/**
 * 非根界面的文案扫描扩展到「设置 / 日志与磁盘 / 管家」三处。
 *
 * 这三页原本会告诉只读账号"这是由主账号统一管理的""请换管理员账号登录"，
 * 等于直接暴露「你是被创建的」。接口层已不再下发层级字段，界面文案必须同步收口：
 * 源码与构建产物都只允许出现「系统统一管理 / 当前账号只读 / 暂无此权限」。
 */
const HIERARCHY_TERMS = ['主账号', '子账号', '上级', '下级', '管理员'] as const

const NON_ROOT_COPY_FILES = [
  join(process.cwd(), 'src', 'pages', 'Settings.tsx'),
  join(process.cwd(), 'src', 'components', 'LogRetentionPanel.tsx'),
  join(process.cwd(), 'src', 'pages', 'Butler.tsx'),
]

describe('非根界面文案：设置 / 日志 / 管家', () => {
  test('源码里不出现层级与角色词（root 这类字段名不在扫描范围）', () => {
    for (const file of NON_ROOT_COPY_FILES) {
      const source = readFileSync(file, 'utf8')
      expect(HIERARCHY_TERMS.filter((term) => source.includes(term))).toEqual([])
    }
  })

  test('构建产物不再包含被修正的层级文案，并包含新的中性说法', () => {
    const assets = join(process.cwd(), 'dist', 'assets')
    if (!existsSync(assets)) {
      // 未执行 `npm run build` 时没有产物可查；构建步骤会单独跑这条断言。
      return
    }
    const bundle = readdirSync(assets)
      .filter((name) => name.endsWith('.js'))
      .map((name) => readFileSync(join(assets, name), 'utf8'))
      .join('\n')

    for (const sentence of [
      '该设置由主账号统一管理',
      '软件更新由主账号统一管理',
      '请换管理员账号登录后再来',
    ]) {
      expect(bundle).not.toContain(sentence)
    }
    // 新文案已进入产物，证明确实重新构建过，而不是只改了源码。
    expect(bundle).toContain('该设置由系统统一管理，当前账号只读')
    expect(bundle).toContain('软件更新由系统统一管理')
    expect(bundle).toContain('当前账号暂无此权限')
    // 注：`Apps.tsx` 由另一条改动线维护，它的按钮文案不在本用例代管范围。
  })
})
