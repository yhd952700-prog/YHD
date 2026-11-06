/**
 * 账号面客户端的诚实性测试。
 *
 * 关键区分：
 * * 只有"路由未挂载"（404 且响应体没有业务 code）才允许落到契约桩；
 * * 业务拒绝（403 / 404 + code）与 501（功能未启用）必须原样抛出，
 *   不能被桩伪装成成功 —— 否则界面会把"没做完"显示成"已完成"。
 * * 桩的形状必须与 `_accounts.public_member` 一致：不含 parent / depth / children。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import {
  AccountApiError,
  countTreeMembers,
  descendantsOf,
  isRouteMissing,
  loadAccounts,
  loadUsage,
  neutralDenial,
  stubEnabled,
  type AccountMember,
  type AccountNode,
} from './accounts'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn(async () => json(404, { detail: 'Not Found' }))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('错误分类', () => {
  test('404 且无业务 code 才算"路由未挂载"', () => {
    expect(isRouteMissing(new AccountApiError(404, null, 'Not Found'))).toBe(true)
    expect(isRouteMissing(new AccountApiError(404, 'ACCOUNT_NOT_FOUND', '不存在'))).toBe(false)
    expect(isRouteMissing(new AccountApiError(403, null, '拒绝'))).toBe(false)
    expect(isRouteMissing(new Error('boom'))).toBe(false)
  })

  test('403 / 404 一律中性化，不把结构信息透给界面', () => {
    expect(neutralDenial(new AccountApiError(403, 'ACCOUNT_FORBIDDEN', '只允许上级账号'))).toBe(
      '无权执行该操作',
    )
    expect(neutralDenial(new AccountApiError(404, 'ACCOUNT_NOT_FOUND', '层级不存在'))).toBe(
      '无权执行该操作',
    )
  })

  test('501 保留真实原因（功能未启用不是"无权"）', () => {
    const error = new AccountApiError(501, 'ACCOUNT_FEATURE_UNAVAILABLE', '该功能尚未启用')
    expect(neutralDenial(error)).toBe('该功能尚未启用')
  })

  test('网络不可达给出可行动提示', () => {
    expect(neutralDenial(new AccountApiError(0, null, '无法连接到服务，请检查服务是否在运行'))).toContain(
      '检查服务',
    )
  })

  test('401（未带令牌 / 令牌被吊销）一律中性化，不透出 missing bearer token', async () => {
    fetchMock.mockResolvedValue(json(401, { detail: 'missing bearer token' }))
    await expect(loadAccounts({ principal: 'root', isRoot: true })).rejects.toMatchObject({
      status: 401,
      message: '登录状态已失效，请重新登录',
    })
    expect(neutralDenial(new AccountApiError(401, 'CREDENTIAL_STALE', '凭据已失效'))).toBe(
      '登录状态已失效，请重新登录',
    )
  })

  test('409（重复成员 / 已有账号）保留服务端真实原因', () => {
    expect(neutralDenial(new AccountApiError(409, 'ACCOUNT_EXISTS', '该账号已存在'))).toBe(
      '该账号已存在',
    )
  })
})

describe('契约桩', () => {
  test('测试环境必须启用桩（否则下面的桩用例无意义）', () => {
    expect(stubEnabled()).toBe(true)
  })

  test('404 无 code → 落桩，且标记 source=stub', async () => {
    const result = await loadAccounts({ principal: 'root', isRoot: true })
    expect(result.source).toBe('stub')
    expect(result.data.members.length).toBeGreaterThan(0)
  })

  test('桩成员形状与 public_member 一致：不含 parent / depth / children', async () => {
    const result = await loadAccounts({ principal: 'root', isRoot: true })
    for (const member of result.data.members as AccountMember[]) {
      expect(typeof member.principal).toBe('string')
      expect(Array.isArray(member.permissions)).toBe(true)
      expect(typeof member.status).toBe('string')
      expect(member).not.toHaveProperty('parent')
      expect(member).not.toHaveProperty('parent_account_id')
      expect(member).not.toHaveProperty('depth')
      expect(member).not.toHaveProperty('children')
    }
  })

  test('404 带业务 code → 抛错，不落桩', async () => {
    fetchMock.mockResolvedValue(json(404, { code: 'ACCOUNT_NOT_FOUND', detail: '账号不存在' }))
    await expect(loadAccounts({ principal: 'root', isRoot: true })).rejects.toBeInstanceOf(
      AccountApiError,
    )
  })

  test('501（模块未落地）→ 抛真实原因，不落桩', async () => {
    fetchMock.mockResolvedValue(
      json(501, { code: 'ACCOUNT_FEATURE_UNAVAILABLE', detail: '该功能尚未启用' }),
    )
    await expect(loadUsage('member-a')).rejects.toMatchObject({
      status: 501,
      message: '该功能尚未启用',
    })
  })
})

describe('真实接口形状', () => {
  test('根账号整棵树计数与后代拍平（列表接口只返回直接成员）', () => {
    const tree: AccountNode = {
      principal: 'root',
      permissions: ['*'],
      status: 'active',
      children: [
        {
          principal: 'member-a',
          permissions: [],
          status: 'active',
          children: [
            { principal: 'deep-a', permissions: [], status: 'active', children: [] },
          ],
        },
        { principal: 'member-b', permissions: [], status: 'active', children: [] },
      ],
    }
    expect(countTreeMembers(tree)).toBe(4)
    expect(descendantsOf(tree).map((node) => node.principal)).toEqual([
      'member-a',
      'deep-a',
      'member-b',
    ])
  })

  test('200 → source=live，成员字段原样保留', async () => {
    fetchMock.mockResolvedValue(
      json(200, {
        members: [
          { principal: 'root', permissions: ['*'], status: 'active' },
          {
            principal: 'member-a',
            permissions: ['account:read'],
            status: 'active',
            allow_superior_key_use: true,
            key_auth_source: 'explicit_toggle',
          },
        ],
        count: 2,
      }),
    )
    const result = await loadAccounts({ principal: 'root', isRoot: true })
    expect(result.source).toBe('live')
    expect(result.data.count).toBe(2)
    expect(result.data.members[1].key_auth_source).toBe('explicit_toggle')
  })
})
