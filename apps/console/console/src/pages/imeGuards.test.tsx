/**
 * 「回车即查询」类页面的输入法护栏（Trust / Identity）。
 *
 * 中文输入法组词中按 Enter 是选词；如果页面把它当成提交，就会拿半截拼音去查询并
 * 渲染一个必然查不到的 404。这里用 compositionstart/end + keyCode 229 复现真实序列。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { buildProfile } from '../lib/accountCaps'
import type { Session } from '../lib/auth'
import type { AccountsPayload } from '../lib/accounts'
import { Trust } from './Trust'
import { Identity } from './Identity'

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
    if (url === '/v1/accounts') return json(200, { members: [], count: 0 })
    return json(404, { detail: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/**
 * 根账号才渲染「账号详情查询」卡片（非根会整页只读化并提前返回）；
 * 三个全站清单端点用 404 兜住，用例只关心手动查询是否被输入法组合态拦住。
 */
const SESSION: Session = {
  token: 't',
  principal: 'root',
  displayName: '设备负责人',
  scope: null,
  permissions: [],
  expiresAt: 0,
  client: 'web',
  isRoot: true,
}
const PAYLOAD: AccountsPayload = { members: [], count: 0 }
const PROFILE = buildProfile(SESSION, PAYLOAD, 'live', null)

function trustCalls() {
  return fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/v1/trust/'))
}

function principalCalls() {
  return fetchMock.mock.calls.filter(([url]) => String(url).startsWith('/v1/identity/principals/'))
}

describe('信任状态查询 · 输入法回车', () => {
  const box = () => screen.getByPlaceholderText('实体标识（例如员工名）') as HTMLInputElement

  test('组词中回车不查询，拼音保留；compositionend 之后回车才查询', async () => {
    render(<Trust query="" />)

    const input = box()
    fireEvent.change(input, { target: { value: 'yuangong' } })
    fireEvent.compositionStart(input)
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })

    await waitFor(() => expect(trustCalls()).toHaveLength(0))
    expect(input.value).toBe('yuangong')

    fireEvent.compositionEnd(input)
    // 越过组合结束后的余波窗口（浏览器可能补发一次 Enter）。
    await new Promise((resolve) => setTimeout(resolve, 60))
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(trustCalls()).toHaveLength(1))
  })
})

describe('账号详情查询 · 输入法回车', () => {
  const box = () => screen.getByPlaceholderText('输入账号名称或标识') as HTMLInputElement

  test('组词中回车不查询，拼音保留；compositionend 之后回车才查询', async () => {
    render(<Identity query="" profile={PROFILE} />)

    const input = box()
    fireEvent.change(input, { target: { value: 'zhanghao' } })
    fireEvent.compositionStart(input)
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })

    await waitFor(() => expect(principalCalls()).toHaveLength(0))
    expect(input.value).toBe('zhanghao')

    fireEvent.compositionEnd(input)
    await new Promise((resolve) => setTimeout(resolve, 60))
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(principalCalls()).toHaveLength(1))
  })
})
