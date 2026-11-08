/**
 * 登录页首屏可达性 —— CSS 契约回归。
 *
 * jsdom 没有排版引擎，量不出「按钮是否落在视口内」，所以这里锁定让首屏可达
 * 成立的结构性规则；实际像素证据由浏览器实测给出（1280×720、1366×768，以及
 * 125% / 150% 缩放后的等效视口 1024×576 / 853×480 等）。
 *
 * 三条不能被改掉的规则：
 * 1. 内容高于视口时从顶部排布（safe center），不能向上溢出到够不到的地方；
 * 2. 矮窗口把卡片高度收在视口内，主操作吸底；
 * 3. 空间实在不够时先让装饰（客户端清单 / 账号统计）让位，**不缩小字号硬塞**。
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, test } from 'vitest'

const css = readFileSync(join(process.cwd(), 'src', 'os.css'), 'utf-8')

/** 取出第 occurrence 个（从 0 计）指定媒体查询的完整块，按花括号配平切分。 */
function mediaBlock(query: string, occurrence = 0): string {
  const marker = `@media ${query} {`
  let index = -1
  for (let i = 0; i <= occurrence; i += 1) index = css.indexOf(marker, index + 1)
  if (index < 0) throw new Error(`os.css 缺少 ${marker}`)
  let depth = 0
  for (let i = index; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    if (css[i] === '}') {
      depth -= 1
      if (depth === 0) return css.slice(index, i + 1)
    }
  }
  throw new Error(`os.css 中的 ${marker} 未闭合`)
}

describe('登录页首屏可达性', () => {
  test('内容高于视口时从顶部排布，不会向上溢出到够不到的位置', () => {
    expect(css).toContain('place-items: safe center')
  })

  test('矮窗口把卡片收进视口，主操作吸底', () => {
    // ≤620px 高是另一线的块（在 480px 块之前），这里只锁定它的契约不被删掉。
    const compact = mediaBlock('(max-height: 620px)', 1)
    expect(compact).toContain('max-height: calc(100dvh - 24px)')
    expect(compact).toContain('position: sticky')
    expect(compact).toContain("button[type='submit']")
  })

  test('空间不足时装饰让位，且不是靠缩小字号硬塞', () => {
    const giveWayClients = mediaBlock('(max-height: 620px)', 0)
    expect(giveWayClients).toContain('.os-login .os-login-clients')
    expect(giveWayClients).toContain('display: none')
    expect(giveWayClients).not.toContain('font-size')

    const giveWayFoot = mediaBlock('(max-height: 520px)')
    expect(giveWayFoot).toContain('.os-login .os-login-foot')
    expect(giveWayFoot).toContain('display: none')
    expect(giveWayFoot).not.toContain('font-size')
  })

  test('窄屏移动端适配仍在（本轮的让位规则不覆盖它）', () => {
    expect(css).toContain("[data-mode='mobile'] .os-login-tabs button")
    expect(css).toContain('.os-login-aside {')
    expect(mediaBlock('(max-height: 820px)')).toContain('.os-login-card')
  })
})
