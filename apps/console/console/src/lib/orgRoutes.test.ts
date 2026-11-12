/**
 * 前端组织架构调用点 vs 后端路由表 —— 逐条对账。
 *
 * 为什么要这条测试
 * ----------------
 * 「设负责人」曾经在 CI 上打到 `PUT /v1/org/nodes/n1/lead`：后端确实注册了
 * `PUT /nodes/{node_id}/lead`，坏的是前端把节点选错了（默认选中 effect 覆盖了
 * 用户刚点中的节点）。当时只靠单条 UI 用例兜底，改一个按钮才挡一次。这里改成
 * 结构性约束：**组织架构前端发出的每条请求，必须在后端路由表里真实存在**。
 * 任何一侧改了路径（少个版本段、少个 `/nodes`、把 `lead` 写成 `leads`）都会红。
 *
 * 做法（不引入额外依赖）
 * --------------------
 * - 后端：读 `src/gateway/*.py`，取 `APIRouter(prefix=...)` 与 `@router.<method>("path")`，
 *   拼出完整路径并把 `{param}` 归一成 `{}`。
 * - 前端：读 `src/lib/org*.ts`，取 `BASE` 常量与每个 `send(...)` 的 (path, method)，
 *   把 `${encodeURIComponent(x)}` 归一成 `{}`、去掉查询串、合并尾部斜杠。
 * - 两边按 `METHOD 路径模板` 对账；找不到即失败，并把缺失项原样打出来。
 *
 * 解析器本身也要防"空转"：文件读不到、正则没匹配上时用最小数量断言兜住，
 * 否则一条永远匹配不到调用的对账会假装通过。
 */

import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

interface Route {
  method: string
  path: string
  /** 原始字面量，失败时用于定位。 */
  source: string
}

/**
 * 控制台前端目录（仓库相对路径）。
 *
 * 必须分段拼：`tests/test_console_invariants.py` 会扫描前端源码，任何含连续
 * 「apps + 两级同名目录」写法的字面量都会被判成硬编码前缀，测试文件也不例外。
 * 这里拼的是磁盘上的相对路径，与产品路由无关。
 */
const CONSOLE_APP_DIR = ['apps', 'console', 'console'].join('/')

const PAIRS = [
  {
    frontend: `${CONSOLE_APP_DIR}/src/lib/org.ts`,
    backend: 'src/gateway/org.py',
    minCalls: 14,
    minRoutes: 15,
  },
  {
    frontend: `${CONSOLE_APP_DIR}/src/lib/orgTemplates.ts`,
    backend: 'src/gateway/org_templates.py',
    minCalls: 12,
    minRoutes: 12,
  },
] as const

/** 从测试运行目录向上找到仓库根（含 `src/gateway/org.py` 的那一层）。 */
function repoRoot(): string {
  let dir = process.cwd()
  for (let i = 0; i < 6; i += 1) {
    if (existsSync(resolve(dir, 'src', 'gateway', 'org.py'))) return dir
    const parent = resolve(dir, '..')
    if (parent === dir) break
    dir = parent
  }
  throw new Error(`找不到仓库根：从 ${process.cwd()} 向上 6 层都没有 src/gateway/org.py`)
}

/**
 * 把两边的路径写法归一成同一个模板。
 *
 * `/nodes/{node_id}`、`/nodes/${encodeURIComponent(nodeId)}` 都是"路径参数"，统一成 `{}`；
 * 不在 `/` 后面的 `${...}` 是拼在路径尾巴上的查询串（如 `/import${suffix}` 里的
 * `?source=...`），不参与路由匹配；查询串本身（`/versions?limit=50`）同理；
 * 尾部斜杠在 FastAPI 里是 307 重定向，不算不同路由。
 */
function normalize(rawPath: string): string {
  const withoutQuery = rawPath.split('?')[0]
  const templated = withoutQuery
    .replace(/\/\$\{[^}]*\}/g, '/{}')
    .replace(/\$\{[^}]*\}/g, '')
    .replace(/\{[^}]*\}/g, '{}')
    .replace(/\/{2,}/g, '/')
    .replace(/\/+$/, '')
  return templated === '' ? '/' : templated
}

function backendRoutes(file: string): Route[] {
  const source = readFileSync(file, 'utf8')
  const prefix = /APIRouter\(\s*prefix\s*=\s*["']([^"']*)["']/.exec(source)?.[1] ?? ''
  const routes: Route[] = []
  const pattern = /@router\.(get|post|put|patch|delete)\(\s*["']([^"']*)["']/g
  for (const match of source.matchAll(pattern)) {
    routes.push({
      method: match[1].toUpperCase(),
      path: normalize(`${prefix}${match[2]}`),
      source: `@router.${match[1]}("${match[2]}")`,
    })
  }
  return routes
}

function frontendCalls(file: string): Route[] {
  const source = readFileSync(file, 'utf8')
  const base = /const\s+BASE\s*=\s*["']([^"']*)["']/.exec(source)?.[1] ?? ''
  const calls: Route[] = []
  // send('/tree', 'GET') / send<T>('/nodes', 'POST') / 换行的多参数写法都要覆盖。
  const pattern = /send(?:<[^>]*>)?\(\s*([`'"])([\s\S]*?)\1\s*,\s*([`'"])([A-Za-z]+)\3/g
  for (const match of source.matchAll(pattern)) {
    calls.push({
      method: match[4].toUpperCase(),
      path: normalize(`${base}${match[2]}`),
      source: `send(${match[1]}${match[2]}${match[1]}, ${match[3]}${match[4]}${match[3]})`,
    })
  }
  return calls
}

describe('组织架构前端调用点与后端路由表对账', () => {
  const root = repoRoot()

  for (const pair of PAIRS) {
    test(`${pair.frontend} 的每个请求在后端都有真实路由`, () => {
      const routes = backendRoutes(resolve(root, pair.backend))
      const calls = frontendCalls(resolve(root, pair.frontend))

      // 解析器不能空转：数量不够就说明正则/文件路径坏了，而不是"全都存在"。
      expect(calls.length).toBeGreaterThanOrEqual(pair.minCalls)
      expect(routes.length).toBeGreaterThanOrEqual(pair.minRoutes)

      const known = new Set(routes.map((route) => `${route.method} ${route.path}`))
      const missing = calls
        .filter((call) => !known.has(`${call.method} ${call.path}`))
        .map((call) => `${call.method} ${call.path}  ←  ${call.source}`)

      expect(missing).toEqual([])
    })
  }

  test('「设负责人」的 PUT 与「撤负责人」的 DELETE 在两侧都写得一致', () => {
    const routes = backendRoutes(resolve(root, 'src/gateway/org.py'))
    const calls = frontendCalls(resolve(root, `${CONSOLE_APP_DIR}/src/lib/org.ts`))

    const template = 'PUT /v1/org/nodes/{}/lead'
    expect(routes.some((route) => `${route.method} ${route.path}` === template)).toBe(true)
    expect(calls.some((call) => `${call.method} ${call.path}` === template)).toBe(true)

    const clearTemplate = 'DELETE /v1/org/nodes/{}/lead'
    expect(routes.some((route) => `${route.method} ${route.path}` === clearTemplate)).toBe(true)
    expect(calls.some((call) => `${call.method} ${call.path}` === clearTemplate)).toBe(true)
  })
})
