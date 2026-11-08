/**
 * 项目删除的真实路径。
 *
 * 后端对「还有目标的项目」先回 409 `{code:"project_has_goals", message:"…"}`
 * （结构化错误已摊平到顶层），只有带 `force=true` 才真正删除。前端此前只读
 * `detail.code`，`ApiError.code` 恒为 null，于是强制确认分支永远进不去 ——
 * 用户点删除毫无反应。这里把「出现确认框 → 确认 → 带 force 删除成功」
 * 整条链路钉住，防止再退回同一个 bug。
 */

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Projects } from './Projects'
import { saveSession } from '../lib/auth'
import type { ProjectSummary } from '../lib/operator'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const PROJECT: ProjectSummary = {
  project_id: 'p1',
  name: 'Q4 客户交付',
  description: '把交付目标归到一起',
  created_at: 1_700_000_000,
  archived_at: null,
  creator: 'member-b',
  goal_ids: ['g1', 'g2'],
  goal_count: 2,
  last_activity_at: 1_700_100_000,
  output_count: 0,
  outputs: [],
  outputs_truncated: false,
}

const CONFLICT_MESSAGE =
  '该项目下还有 2 个目标：删除项目只会解除归属，目标与产出都会保留。'

let deleteCalls: string[]
let removed: boolean

/** 测试环境没有 localStorage（Node 提示 --localstorage-file 未提供），补内存实现。 */
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length() {
      return data.size
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => [...data.keys()][index] ?? null,
    removeItem: (key: string) => {
      data.delete(key)
    },
    setItem: (key: string, value: string) => {
      data.set(key, String(value))
    },
  } as Storage
}

beforeEach(() => {
  Object.defineProperty(window, 'localStorage', {
    value: memoryStorage(),
    configurable: true,
  })
  Object.defineProperty(window, 'sessionStorage', {
    value: memoryStorage(),
    configurable: true,
  })
  saveSession(
    {
      token: 't',
      principal: 'member-b',
      displayName: '账号 B',
      scope: null,
      permissions: [],
      expiresAt: Math.floor(Date.now() / 1000) + 3600,
      client: 'web',
      isRoot: false,
    },
    true,
  )

  deleteCalls = []
  removed = false
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.startsWith('/v1/projects') && method === 'DELETE') {
      deleteCalls.push(url)
      if (!url.includes('force=true')) {
        return json(409, {
          code: 'project_has_goals',
          message: CONFLICT_MESSAGE,
          detail: CONFLICT_MESSAGE,
        })
      }
      removed = true
      return json(200, { removed: 'p1', ok: true })
    }
    if (url.startsWith('/v1/projects')) {
      return json(200, {
        projects: removed ? [] : [PROJECT],
        count: removed ? 0 : 1,
        view_account: null,
      })
    }
    return json(404, { detail: 'Not Found' })
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('项目删除：有目标时先确认影响面', () => {
  test('409 带 project_has_goals → 出现确认框 → 确认后带 force 删除成功', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<Projects query="" />)

    await screen.findByText('Q4 客户交付')
    fireEvent.click(screen.getByRole('button', { name: '删除' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    // 确认框里必须是后端给的真实影响面，而不是一句「删除失败」。
    expect(String(confirm.mock.calls[0][0])).toContain(CONFLICT_MESSAGE)

    await waitFor(() =>
      expect(deleteCalls).toEqual(['/v1/projects/p1', '/v1/projects/p1?force=true']),
    )
    // 删除成功后列表刷新，项目不再出现。
    await waitFor(() => expect(screen.queryByText('Q4 客户交付')).toBeNull())
  })

  test('用户取消确认时不发强制删除请求，项目保留', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<Projects query="" />)

    await screen.findByText('Q4 客户交付')
    fireEvent.click(screen.getByRole('button', { name: '删除' }))

    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(deleteCalls).toEqual(['/v1/projects/p1'])
    expect(screen.getByText('Q4 客户交付')).toBeTruthy()
  })
})
