/**
 * 运行时回归：写请求体必须真的带上后端能收的字段（本次缺陷的直接拦截网）。
 *
 * 为什么这个-测试必须存在（与 test_console_request_drift_gate.py 互补）
 * -------------------------------------------------------------------
 * 那条 Python 门禁是**静态**的：它读 `operator.ts` 原文，比对"请求体字面量里的键"
 * 与"后端 Pydantic 模型字段"。它能防住"键被删掉"，但证明不了"运行时 `fetch` 真的
 * 收到了这个键"——中间还隔着 `JSON.stringify`、默认值 `??` 等一层。
 *
 * 这里的断言是**运行时**的：stub 掉全局 `fetch`，调用真实的 `createGoal()`，
 * 直接解析 `init.body`，逐键核对。两个证据互不替代：
 *   - 静态门禁：防字面量漂移（无需运行）
 *   - 本文件：防"字面量在、运行时却没上 wire"（需要真的跑一遍）
 *
 * 缺陷形状：`createGoal` 的类型声明里有 `project_id`，但请求体从未序列化它——于是
 * 传了 `project_id` 也会被静默丢弃，目标落成"独立目标"。`tsc` 全程是绿的，因为
 * TypeScript 不校验"字段有没有进 wire"。
 */

import { afterEach, describe, expect, test, vi } from 'vitest'
import { createGoal, createProject, hireEmployee } from './operator'

/** 后端 `GoalCreateRequest` / `HireEmployeeRequest` / `ProjectCreateRequest` 的字段集。 */
const GOAL_FIELDS = [
  'natural_language',
  'scope',
  'plan_mode',
  'verification_criteria',
  'project_id',
  'background',
].sort()

function stubFetch(): { body: <T = Record<string, unknown>>() => T } {
  const response = {
    ok: true,
    status: 200,
    json: async () => ({ ok: true }),
  } as unknown as Response
  const spy = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', spy)
  return {
    body: <T,>() => JSON.parse(String((spy.mock.calls[0]?.[1] as RequestInit)?.body)) as T,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('createGoal 请求体：声明 = 接线', () => {
  test('project_id 真的上了 wire（本次缺陷回归）', async () => {
    const f = stubFetch()
    await createGoal({
      natural_language: '把上月活跃用户汇总成一份周报',
      project_id: 'proj-abc',
      background: true,
    })
    const body = f.body<Record<string, unknown>>()
    expect(body.project_id).toBe('proj-abc')
  })

  test('不传 project_id 时显式发 null（后端语义：独立目标），而不是丢键', async () => {
    const f = stubFetch()
    await createGoal({ natural_language: '独立目标' })
    const body = f.body<Record<string, unknown>>()
    expect('project_id' in body).toBe(true)
    expect(body.project_id).toBeNull()
  })

  test('请求体键集与后端 GoalCreateRequest 字段集完全一致', async () => {
    const f = stubFetch()
    await createGoal({ natural_language: 'x' })
    const body = f.body<Record<string, unknown>>()
    expect(Object.keys(body).sort()).toEqual(GOAL_FIELDS)
  })

  test('POST 到 /v1/goals', async () => {
    const spy = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    } as unknown as Response)
    vi.stubGlobal('fetch', spy)
    await createGoal({ natural_language: 'x' })
    expect(spy.mock.calls[0]?.[0]).toBe('/v1/goals')
    expect((spy.mock.calls[0]?.[1] as RequestInit)?.method).toBe('POST')
  })
})

describe('其它写端点的请求体也已接线（防止同类缺陷在新函数里复发）', () => {
  test('hireEmployee 发 name / agent_count / agent_types', async () => {
    const f = stubFetch()
    await hireEmployee({ name: 'team-a', agent_count: 2, agent_types: ['coder'] })
    expect(Object.keys(f.body()).sort()).toEqual(
      ['agent_count', 'agent_types', 'name'].sort(),
    )
  })

  test('createProject 发 name / description', async () => {
    const f = stubFetch()
    await createProject({ name: 'P', description: 'd' })
    expect(Object.keys(f.body()).sort()).toEqual(['description', 'name'].sort())
  })
})
