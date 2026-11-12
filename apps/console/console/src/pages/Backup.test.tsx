/**
 * 备份导出回归。
 *
 * 钉住五件事：
 *   1. 没有备份能力的账号看不到导出入口，只看到"无权执行该操作"；
 *   2. 口令不足 8 位或未勾选二次确认时，导出按钮就是点不了的；
 *   3. 导出成功回报的是后端 manifest 里的真实统计（账号数 / 文件数 / 字节数），
 *      不是写死的数字；同时披露审计链密钥不随包；
 *   4. 导出失败原样显示后端原因，不伪装成成功；
 *   5. 只读查看中写操作禁用。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Backup } from './Backup'
import type { AccountCaps, AccountProfile } from '../lib/accountCaps'

function json(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    json: async () => body,
  } as unknown as Response
}

function profile(overrides: Partial<AccountProfile> = {}): AccountProfile {
  return {
    principal: 'root',
    displayName: '根账号',
    scope: 'L0',
    identityPermissions: [],
    accountPermissions: [],
    isRoot: true,
    canSelfRecover: true,
    members: [],
    memberCount: 2,
    source: 'live',
    loaded: true,
    error: null,
    ...overrides,
  }
}

function caps(overrides: Partial<AccountCaps> = {}): AccountCaps {
  return {
    isRoot: true,
    isOwner: true,
    canMembers: true,
    canMatrix: true,
    canTemplates: true,
    canBackup: true,
    canRecovery: true,
    canSettings: true,
    canSystemStatus: true,
    canAllKeys: true,
    canMyMembers: false,
    canSubtreeMemory: true,
    canViewAs: true,
    canGrant: false,
    canApproveQueue: true,
    ...overrides,
  }
}

let exportResponse: { status: number; body: unknown }
let fetchMock: ReturnType<typeof vi.fn>
let clickSpy: ReturnType<typeof vi.spyOn>
let originalCreateObjectURL: typeof URL.createObjectURL
let originalRevokeObjectURL: typeof URL.revokeObjectURL

beforeEach(() => {
  exportResponse = {
    status: 200,
    body: {
      bundle: {},
      scope: 'all',
      manifest: {
        members: ['root', 'member-a', 'member-b'],
        member_count: 3,
        files: 12,
        bytes: 2048,
        models: 0,
        credentials: 1,
        skipped: [],
      },
    },
  }
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith('/v1/accounts/backup/export')) {
      return json(exportResponse.status, exportResponse.body)
    }
    return json(404, { detail: 'not found' })
  })
  vi.stubGlobal('fetch', fetchMock)
  originalCreateObjectURL = URL.createObjectURL
  originalRevokeObjectURL = URL.revokeObjectURL
  URL.createObjectURL = () => 'blob:fake-backup'
  URL.revokeObjectURL = () => {}
  clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})

afterEach(() => {
  clickSpy.mockRestore()
  URL.createObjectURL = originalCreateObjectURL
  URL.revokeObjectURL = originalRevokeObjectURL
  vi.unstubAllGlobals()
})

/** jest-dom 未装：用原生 disabled 属性断言。 */
function isDisabled(el: HTMLElement): boolean {
  return (el as HTMLButtonElement).disabled === true
}

function renderPage(
  prof: AccountProfile = profile(),
  cap: AccountCaps = caps(),
  readOnly = false,
) {
  return render(<Backup profile={prof} caps={cap} readOnly={readOnly} />)
}

async function fillPassphraseAndConfirm(passphrase = 'correct-horse') {
  fireEvent.change(screen.getByLabelText('口令'), { target: { value: passphrase } })
  fireEvent.click(screen.getByLabelText(/我知道导出包包含他人的个人数据/))
}

describe('入口与门禁', () => {
  test('没有备份能力：只看到无权，不渲染导出表单', () => {
    renderPage(profile(), caps({ canBackup: false }))
    expect(screen.getByText('无权执行该操作')).toBeTruthy()
    expect(screen.queryByLabelText('口令')).toBeNull()
    expect(screen.queryByRole('button', { name: '导出并下载' })).toBeNull()
  })

  test('整棵树导出先说清包含几个账号（成员数 + 自己）', () => {
    renderPage()
    expect(screen.getByText(/整棵树共 3 个账号/)).toBeTruthy()
  })

  test('只读查看中导出按钮禁用', () => {
    renderPage(profile(), caps(), true)
    expect(isDisabled(screen.getByRole('button', { name: '导出并下载' }))).toBe(true)
  })
})

describe('二次确认', () => {
  test('口令不足 8 位：按钮禁用，点不了', () => {
    renderPage()
    fireEvent.change(screen.getByLabelText('口令'), { target: { value: 'short' } })
    fireEvent.click(screen.getByLabelText(/我知道导出包包含他人的个人数据/))
    expect(isDisabled(screen.getByRole('button', { name: '导出并下载' }))).toBe(true)
  })

  test('口令够长但未勾选确认：按钮仍禁用', () => {
    renderPage()
    fireEvent.change(screen.getByLabelText('口令'), {
      target: { value: 'correct-horse' },
    })
    expect(isDisabled(screen.getByRole('button', { name: '导出并下载' }))).toBe(true)
  })
})

describe('导出', () => {
  test('成功回报 manifest 真实统计，并披露审计链密钥不随包', async () => {
    renderPage()
    await fillPassphraseAndConfirm()
    fireEvent.click(screen.getByRole('button', { name: '导出并下载' }))

    // 注意：迁移卡片里也有一条 role=status 的说明，不能用 findByRole('status')。
    const notice = await screen.findByText(/导出完成：3 个账号/)
    expect(notice.textContent).toContain('导出完成：3 个账号 / 12 个文件 / 2.0 KB')
    expect(notice.textContent).toContain('审计记录与审计链校验密钥都不随包')
    expect(clickSpy).toHaveBeenCalled()

    const post = fetchMock.mock.calls.find(([url]) =>
      String(url).startsWith('/v1/accounts/backup/export'),
    )
    expect(post).toBeTruthy()
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({
      scope: 'all',
      passphrase: 'correct-horse',
    })
  })

  test('仅我的账号：范围随请求提交', async () => {
    renderPage()
    fireEvent.change(screen.getByLabelText('导出范围'), { target: { value: 'self' } })
    expect(screen.getByText(/仅你本人的账号/)).toBeTruthy()
    await fillPassphraseAndConfirm()
    fireEvent.click(screen.getByRole('button', { name: '导出并下载' }))

    await screen.findByText(/导出完成：3 个账号/)
    const post = fetchMock.mock.calls.find(([url]) =>
      String(url).startsWith('/v1/accounts/backup/export'),
    )
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({
      scope: 'self',
      passphrase: 'correct-horse',
    })
  })

  test('导出失败原样显示后端原因，不伪装成成功', async () => {
    exportResponse = {
      status: 500,
      body: { detail: '备份服务暂时不可用' },
    }
    renderPage()
    await fillPassphraseAndConfirm()
    fireEvent.click(screen.getByRole('button', { name: '导出并下载' }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('备份服务暂时不可用')
    expect(screen.queryByText(/导出完成/)).toBeNull()
    expect(clickSpy).not.toHaveBeenCalled()
  })
})
