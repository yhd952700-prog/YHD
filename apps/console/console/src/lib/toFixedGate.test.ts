/**
 * `.toFixed()` 直调门禁的自测。
 *
 * 真仓库必须干净；同时必须证明：故意直调会失败、注释里的示例不误报、
 * `lib/format.ts` 这个唯一出口仍然允许。
 */

import { describe, expect, test } from 'vitest'
import { scanRepo, scanText } from '../../scripts/check-no-direct-tofixed.mjs'

// 夹具拆开拼接，避免本文件自己触发门禁。
const DIRECT_CALL = 'const n = value.' + 'toFixed(2)'

describe('禁止直调 .toFixed', () => {
  test('真仓库：除 lib/format.ts 外没有直调', () => {
    expect(scanRepo(process.cwd())).toEqual([])
  })

  test('反证：组件里的直调必须被拦下（文件 + 行号 + 原文）', () => {
    const hits = scanText('src/pages/Boom.tsx', `export function Boom() {\n${DIRECT_CALL}\n}`)
    expect(hits).toHaveLength(1)
    expect(hits[0].file).toBe('src/pages/Boom.tsx')
    expect(hits[0].line).toBe(2)
    expect(hits[0].source).toContain('toFixed(2)')
  })

  test('唯一出口：lib/format.ts 的 numberText 允许直调', () => {
    expect(scanText('src/lib/format.ts', DIRECT_CALL)).toEqual([])
  })

  test('注释里的历史示例不误报', () => {
    const text = ['/* 旧写法：value.' + 'toFixed(1) */', '// 也曾有 value.' + 'toFixed(0)'].join('\n')
    expect(scanText('src/pages/Note.tsx', text)).toEqual([])
  })
})
