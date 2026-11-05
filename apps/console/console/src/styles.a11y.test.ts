/**
 * 样式契约（a11y / 对比度）。
 *
 * 这两条曾经真实退化过：
 * 1) App.css 里 4 处 `outline: none` 让键盘用户完全看不到焦点；
 * 2) 浅色主题把 #2f6fe4 当正文使用，对比度只有 4.4:1（不达 WCAG AA 4.5:1）。
 *
 * 读源码而不是渲染，是因为 jsdom 不实现 :focus-visible 与颜色对比计算。
 */

import { readFileSync } from 'node:fs'
import { describe, expect, test } from 'vitest'

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf8')

describe('样式契约', () => {
  test('旧组件不得再用 outline: none 抹掉焦点', () => {
    const appCss = read('./App.css')
    expect(appCss).not.toMatch(/outline:\s+none/)
    expect(appCss).toMatch(/:focus-visible/)
  })

  test('两套主题都提供 --os-accent-text（正文用深蓝）', () => {
    const osCss = read('./os.css')
    const definitions = osCss.match(/--os-accent-text:\s*#[0-9a-f]{6}/gi) ?? []
    expect(definitions).toHaveLength(2)
    expect(osCss).toContain('--os-accent-text: #1f5bd0')
  })
})
