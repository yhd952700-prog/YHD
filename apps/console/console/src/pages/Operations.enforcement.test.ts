/**
 * 「生效等级」恒空的回归断言。
 *
 * 后端 `GET /v1/policy/enforcement` 返回的是 `enforced_actions`（被拦截的
 * **动作名**，如 `tool.execute`），从来没有 `enforced_tiers`。前端读错字段，
 * 这一行就永远是「—」。以后谁再把字段名写回去，这条断言会立刻红。
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

const SRC = join(process.cwd(), 'src')

describe('拦截配置读取后端真实字段', () => {
  test('Operations 读 enforced_actions，标签是「生效动作」', () => {
    const page = readFileSync(join(SRC, 'pages', 'Operations.tsx'), 'utf8')
    expect(page).toContain('data.enforced_actions')
    expect(page).toContain('生效动作')
    expect(page).not.toContain('enforced_tiers')
  })

  test('契约声明 enforced_actions（后端语义是动作，不是风险等级）', () => {
    const contracts = readFileSync(join(SRC, 'lib', 'contracts.ts'), 'utf8')
    expect(contracts).toContain('enforced_actions?: string[]')
    // 注释里可以提到旧字段名，但不能再有它的类型声明。
    expect(contracts).not.toMatch(/enforced_tiers\s*\??:/)
  })
})
