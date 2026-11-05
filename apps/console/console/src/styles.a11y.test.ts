/**
 * 样式契约（a11y / 对比度 / 窄屏 / 预算）。
 *
 * 这些都是"肉眼看着还行、实际不可用"的高危退化，必须有自动断言：
 * 1) 焦点不可见：曾出现 6 处 `outline:none`，键盘用户完全看不到当前位置；
 * 2) 对比度：浅色主题把 #2f6fe4 当正文只有约 4.4:1，不达 WCAG AA 4.5:1；
 * 3) 窄屏：长任务名 / 长文件名 / 搜索行会把页面撑出横向滚动；
 * 4) 预算：同屏动画不得超过 3 个，新增 CSS 不得失控。
 *
 * 读源码而不是渲染，是因为 jsdom 不实现 :focus-visible 与颜色对比计算。
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

const read = (name: string) => readFileSync(new URL(name, import.meta.url), 'utf8')
const readFromRoot = (name: string) => readFileSync(resolve(process.cwd(), name), 'utf8')

/** 只取“科幻视觉层”这一段，避免 reduced-motion / 手机端同名选择器抢先命中。 */
function sciCss(osCss: string): string {
  // Windows 检出可能是 CRLF，先归一化再匹配多行选择器。
  const css = osCss.replace(/\r\n/g, '\n')
  return css.slice(
    css.indexOf('科幻视觉层（规范'),
    css.indexOf('键盘焦点（a11y）'),
  )
}

/** 取出某个选择器后面第一条规则体（源码里的规则不嵌套，够用）。 */
function ruleBody(css: string, selector: string): string {
  const index = css.indexOf(selector)
  expect(index, `找不到选择器：${selector}`).toBeGreaterThanOrEqual(0)
  const start = css.indexOf('{', index)
  const end = css.indexOf('}', start)
  return css.slice(start, end)
}

function channel(value: number): number {
  const srgb = value / 255
  return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
}

function luminance(hex: string): number {
  const raw = hex.replace('#', '')
  const rgb = [0, 2, 4].map((offset) => parseInt(raw.slice(offset, offset + 2), 16))
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])
}

function contrast(foreground: string, background: string): number {
  const a = luminance(foreground)
  const b = luminance(background)
  const [high, low] = a > b ? [a, b] : [b, a]
  return (high + 0.05) / (low + 0.05)
}

describe('焦点可见', () => {
  test('旧的 6 处输入都有可自动验证的焦点环', () => {
    const appCss = read('./App.css')
    const osCss = read('./os.css')

    // 旧组件不得再抹掉焦点，且必须由 :focus-visible 接管。
    expect(appCss).not.toMatch(/outline:\s+none/)

    // 对话输入 / 会话身份 / 会话下拉 / 画像输入与下拉：金色实色环。
    const legacy = ruleBody(
      appCss,
      ':where(.jarvis-input-wrap textarea, .jarvis-send, .sess-user-input, .sess-select, .sess-btn, .profile-input, .profile-select):focus-visible',
    )
    expect(legacy).toMatch(/outline:\s*2px solid var\(--gold/)
    expect(legacy).toMatch(/outline-offset:\s*2px/)

    // 顶栏搜索 / 登录输入：内层 input 的 outline:none 由外层容器的实色双环接管。
    for (const selector of ['.os-search:focus-within', '.os-input:focus-within']) {
      const body = ruleBody(osCss, selector)
      expect(body, `${selector} 必须是 4px 实色外环，不能用 16% 柔光`).toMatch(
        /0 0 0 4px var\(--os-focus\)/,
      )
      expect(body, `${selector} 需要 2px 背景间隙，避免环贴着色块`).toMatch(
        /0 0 0 2px var\(--os-bg\)/,
      )
    }
  })

  test('全站有 :focus-visible 规则，且底栏项用内缩环（外扩会被视口裁掉）', () => {
    const osCss = read('./os.css')
    const global = ruleBody(
      osCss,
      ":where(button, a[href], input, select, textarea, summary, [role='button'], [tabindex]:not([tabindex='-1'])):focus-visible",
    )
    expect(global).toMatch(/outline:\s*2px solid var\(--os-focus\)/)

    const bottom = ruleBody(osCss, '.os-bottom-item:focus-visible')
    expect(bottom).toMatch(/outline-offset:\s*-3px/)
  })
})

describe('对比度令牌', () => {
  test('两套主题都定义 --os-accent-text，且浅色正文蓝达到 AA', () => {
    const osCss = read('./os.css')
    const definitions = osCss.match(/--os-accent-text:\s*#[0-9a-f]{6}/gi) ?? []
    expect(definitions).toHaveLength(2)

    // 浅色主题：文字用 #1f5bd0，实底填充仍是品牌蓝 #2f6fe4（白字不变）。
    expect(osCss).toMatch(/--os-accent-text:\s*#1f5bd0/)
    expect(osCss).toMatch(/--os-accent:\s*#2f6fe4/)

    expect(contrast('#1f5bd0', '#ffffff')).toBeGreaterThanOrEqual(4.5)
    expect(contrast('#1f5bd0', '#f7f9fe')).toBeGreaterThanOrEqual(4.5)
    // 深色主题的正文蓝在面板底上同样必须达标。
    expect(contrast('#4d8dff', '#0b1120')).toBeGreaterThanOrEqual(4.5)
  })

  test('彩色文字一律走 --os-accent-text，不再直接把 --os-accent 当正文', () => {
    const osCss = read('./os.css')
    const accentAsText = osCss.match(/(?<![-a-z])color:\s*var\(--os-accent\)/gi) ?? []
    expect(accentAsText).toHaveLength(0)

    const textTokenUses = osCss.match(/color:\s*var\(--os-accent-text\)/gi) ?? []
    expect(textTokenUses.length).toBeGreaterThanOrEqual(4)
  })
})

describe('科幻视觉层（P1 落地）', () => {
  test('语义令牌双主题齐全，且不替换现有 --os-* 取值', () => {
    const osCss = read('./os.css')
    // 双主题都定义青/墨色与动效时长。
    expect(osCss.match(/--sc-cyan:\s*#[0-9a-f]{6}/gi) ?? []).toHaveLength(2)
    for (const token of ['--sc-dur-hover', '--sc-dur-state', '--sc-ease-out', '--sc-hairline']) {
      expect(osCss).toMatch(new RegExp(`${token}:\\s`))
    }
    // 品牌主色与品牌名不动。
    expect(osCss).toMatch(/--os-accent:\s*#2f6fe4/)
    expect(osCss).toMatch(/--os-accent:\s*#4d8dff/)
  })

  test('HUD 角标只画两个伪元素、手机端关闭；活跃面板在浅色下不放大范围外发光', () => {
    const sci = sciCss(read('./os.css'))
    expect(ruleBody(sci, '.sc-hud::before {\n  top')).toMatch(/border-width:\s*1px 0 0 1px/)
    expect(ruleBody(sci, '.sc-hud::after {\n  right')).toMatch(/border-width:\s*0 1px 1px 0/)

    const mobile = ruleBody(sci, "[data-mode='mobile'] .sc-hud::before")
    expect(mobile).toMatch(/display:\s*none/)

    const lightActive = ruleBody(sci, "[data-theme='light'] .sc-panel-active")
    expect(lightActive).toMatch(/0 0 0 2px var\(--os-accent-soft\)/)
    // 浅色主题不得再叠 24px 外发光。
    expect(lightActive).not.toMatch(/0 0 24px/)
  })

  test('状态环用形状+文字表达状态：只有执行中呼吸，失败态改实心环', () => {
    const sci = sciCss(read('./os.css'))
    expect(ruleBody(sci, ".sc-status[data-state='active']::after")).toMatch(
      /animation:\s*sc-breathe/,
    )
    expect(ruleBody(sci, ".sc-status[data-state='failed']")).toMatch(/border-width:\s*2\.5px/)
    expect(ruleBody(sci, ".sc-status[data-state='failed']::after")).toMatch(/display:\s*none/)
    // 旧的 box-shadow 循环动画已移除（规范禁止每帧改阴影）。
    expect(read('./os.css')).not.toMatch(/@keyframes\s+os-butler-pulse/)
  })

  test('流光只动 transform，且与不定进度条共用一条 animation 声明', () => {
    const sci = sciCss(read('./os.css'))
    const flow = sci.slice(sci.indexOf('@keyframes sc-flow'))
    expect(flow.slice(0, 220)).toMatch(/transform:\s*translateX/)
    expect(flow.slice(0, 220)).not.toMatch(/background-position|box-shadow|filter:/)

    const shared = ruleBody(sci, '.sc-flow-line::before,')
    expect(shared).toMatch(/animation:\s*sc-flow/)
    expect(shared).toMatch(/linear infinite/)
    // 三处流光没有各自再写 animation。
    expect(sci.match(/animation:\s*sc-flow/g) ?? []).toHaveLength(1)
  })

  test('特效受真实状态门控：执行中才有流线，流式才有前沿，静态卡不挂光效', () => {
    const butler = readFromRoot('src/pages/Butler.tsx')
    const chat = readFromRoot('src/components/ChatPanel.tsx')

    expect(butler).toMatch(/\{live && <div className="sc-flow-line"/)
    expect(butler).toMatch(/className=\{live \? 'sc-panel-active sc-hud' : undefined\}/)
    expect(chat).toMatch(/\{m\.streaming && <div className="sc-stream-frontier"/)
  })

  test('零新依赖：不引入动画库', () => {
    const pkg = JSON.parse(readFromRoot('package.json')) as {
      dependencies?: Record<string, string>
      devDependencies?: Record<string, string>
    }
    const all = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies })
    expect(all.some((name) => /framer-motion|gsap|animejs|motion/i.test(name))).toBe(false)
  })
})

describe('窄屏与预算', () => {
  test('管家页与知识页在 390×844 下不会横向溢出', () => {
    const osCss = read('./os.css')

    // 内容区本身兜住横向滚动；根因仍要逐个断行。
    expect(ruleBody(osCss, '.os-content {')).toMatch(/overflow-x:\s*hidden/)

    expect(ruleBody(osCss, '.os-butler-task-name {')).toMatch(/overflow-wrap:\s*anywhere/)
    expect(ruleBody(osCss, '.os-butler-task-meta {')).toMatch(/overflow-wrap:\s*anywhere/)
    expect(ruleBody(osCss, '.os-butler-artifacts .os-link')).toMatch(/overflow-wrap:\s*anywhere/)

    // 知识页的长文件名 / 元信息。
    expect(ruleBody(osCss, '.os-kb-name {')).toMatch(/overflow-wrap:\s*anywhere/)
    expect(ruleBody(osCss, '.os-kb-meta {')).toMatch(/overflow-wrap:\s*anywhere/)
    // 后台任务的文档 id / 大模型分数同样可能很长。
    expect(ruleBody(osCss, '.os-hint {')).toMatch(/overflow-wrap:\s*anywhere/)
    expect(ruleBody(osCss, '.os-kb-score {')).toMatch(/overflow-wrap:\s*anywhere/)

    // 动作按钮组 / 卡片头允许换行，搜索行整行占位，而不是把按钮挤出屏幕。
    expect(ruleBody(osCss, '.os-kb-actions {')).toMatch(/flex-wrap:\s*wrap/)
    expect(ruleBody(osCss, '.os-card-head {')).toMatch(/flex-wrap:\s*wrap/)
    const mobileSearch = ruleBody(osCss, "[data-mode='mobile'] .os-kb-search .os-input")
    expect(mobileSearch).toMatch(/min-width:\s*0/)
    const mobileTaskName = ruleBody(osCss, "[data-mode='mobile'] .os-butler-task-name")
    expect(mobileTaskName).toMatch(/flex-basis:\s*100%/)

    // 知识文档表在手机端改成竖向条目，避免 5 列挤成一条缝。
    const mobileTable = ruleBody(osCss, "[data-mode='mobile'] .os-kb-docs-table,")
    expect(mobileTable).toMatch(/display:\s*block/)
  })

  test('同屏动画不超过 3 个，且新增焦点/断行规则不带动画', () => {
    const osCss = read('./os.css')
    // `animation: none` 是 reduced-motion 的关闭项，不算一个动画。
    const animations = osCss.match(/animation:\s*(?!\s*none)[^;]+;/g) ?? []
    expect(animations.length).toBeLessThanOrEqual(3)

    // 聚焦与窄屏兜底段落里绝不能引入新的动画。
    const focusSection = osCss.slice(osCss.indexOf('键盘焦点（a11y）'))
    expect(focusSection).not.toMatch(/animation:/)
    expect(focusSection).not.toMatch(/@keyframes/)
  })
})
