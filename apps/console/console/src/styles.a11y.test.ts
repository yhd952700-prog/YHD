/**
 * 样式契约（a11y / 对比度 / 窄屏 / 预算）。
 *
 * 这些都是"肉眼看着还行、实际不可用"的高危退化，必须有自动断言：
 * 1) 焦点不可见：曾出现 6 处 `outline:none`，键盘用户完全看不到当前位置；
 * 2) 对比度：浅色正文陶土色 #9a4a22 对白底 6.1:1、深色 #e8a37e 对面板底 7.2:1，均达 WCAG AA 4.5:1；
 * 3) 窄屏：长任务名 / 长文件名 / 搜索行会把页面撑出横向滚动；
 * 4) 预算：同屏动画不得超过 4 个（第 4 个是方舟反应堆标识的 24s 慢转，0.4.4 Phase 2 要求），新增 CSS 不得失控。
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
  test('两套主题都定义 --os-accent-text，且浅色正文陶土色达到 AA', () => {
    const osCss = read('./os.css')
    const definitions = osCss.match(/--os-accent-text:\s*#[0-9a-f]{6}/gi) ?? []
    expect(definitions).toHaveLength(2)

    // 浅色主题：文字用 #9a4a22，实底填充仍是陶土色 #b85a2e（白字不变）。
    expect(osCss).toMatch(/--os-accent-text:\s*#9a4a22/)
    expect(osCss).toMatch(/--os-accent:\s*#b85a2e/)

    expect(contrast('#9a4a22', '#ffffff')).toBeGreaterThanOrEqual(4.5)
    expect(contrast('#9a4a22', '#fffdf9')).toBeGreaterThanOrEqual(4.5)
    // 深色主题的正文陶土色在面板底上同样必须达标。
    expect(contrast('#e8a37e', '#2b2521')).toBeGreaterThanOrEqual(4.5)
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
    // 品牌主色是陶土色（0.4.4 Phase 2 Muse 风格改版，不再是旧的品牌蓝）；
    // 两套主题各一个定义，值与 os.css 的令牌一致。
    expect(osCss).toMatch(/--os-accent:\s*#b85a2e/)
    expect(osCss).toMatch(/--os-accent:\s*#d98a63/)
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

  test('1024 宽下图表刻度、图例与页面定位句达到可读字号', () => {
    const osCss = read('./os.css')
    // 身份层（旧值 9.5px）在 1024 宽下几乎不可读；正文色 + 12px 是可读下限。
    expect(ruleBody(osCss, '.os-chart-axis')).toMatch(/font-size:\s*12px/)
    expect(ruleBody(osCss, '.os-chart-axis')).toMatch(/var\(--os-text-dim\)/)
    expect(ruleBody(osCss, '.os-legend-item')).toMatch(/font-size:\s*12\.5px/)
    expect(ruleBody(osCss, '.os-legend-item')).toMatch(/var\(--os-text\)/)
    expect(ruleBody(osCss, '.os-position {')).toMatch(/font-size:\s*12\.5px/)
    expect(ruleBody(osCss, '.os-onboard-steps')).toMatch(/font-size:\s*12\.5px/)
  })

  test('1280×720 / 1366×768 首屏放得下账号、密码与登录按钮', () => {
    const osCss = read('./os.css')
    // 内容高于视口时必须从顶部排布，不能向上溢出到点不到的位置。
    expect(ruleBody(osCss, '.os-login {')).toMatch(/place-items:\s*safe center/)

    // 矮窗口收窄登录卡与字段间距，否则登录按钮会落到 720 视口之外。
    const short = osCss.slice(osCss.indexOf('@media (max-height: 820px)'))
    expect(short).not.toBe('')
    expect(ruleBody(short, '.os-login-card')).toMatch(/padding:\s*20px 24px/)
    expect(ruleBody(short, '.os-field')).toMatch(/margin-bottom:\s*10px/)
  })

  test('同屏动画不超过 4 个，且新增焦点/断行规则不带动画', () => {
    const osCss = read('./os.css')
    // `animation: none` 是 reduced-motion 的关闭项，不算一个动画。
    // 上限 4 个：第 4 个是方舟反应堆标识的 24s 慢转（0.4.4 Phase 2 要求，
    // 干活时才转快），同样受 prefers-reduced-motion 门控。
    const animations = osCss.match(/animation:\s*(?!\s*none)[^;]+;/g) ?? []
    expect(animations.length).toBeLessThanOrEqual(4)

    // 聚焦与窄屏兜底段落里绝不能引入新的动画。
    const focusSection = osCss.slice(osCss.indexOf('键盘焦点（a11y）'))
    expect(focusSection).not.toMatch(/animation:/)
    expect(focusSection).not.toMatch(/@keyframes/)
  })
})

describe('读屏（屏幕阅读器）契约', () => {
  const shell = readFromRoot('src/components/Shell.tsx')
  const app = readFromRoot('src/App.tsx')
  const employees = readFromRoot('src/pages/MyEmployees.tsx')
  const login = readFromRoot('src/components/Login.tsx')

  test('切页必须播报「已进入「页面名」」，且不抢走导航焦点', () => {
    // 常驻礼貌播报区：文字变化时读屏才会朗读。
    expect(shell).toMatch(/role="status" aria-live="polite" aria-atomic="true"/)
    expect(shell).toMatch(/已进入「\$\{pageTitle\}」/)
    // 页面名由 App 传入，而不是读 DOM 猜。
    expect(app).toMatch(/pageTitle=\{meta\.title\}/)
    // 切页不移动焦点（焦点留在导航项），只播报。
    expect(shell).not.toMatch(/os-main'\)\?\.focus\(\)/)
  })

  test('登录成功进入壳层后焦点落在主内容，不再掉到 body', () => {
    expect(app).toMatch(/focusMainAfterLogin/)
    expect(app).toMatch(/main\.focus\(\)/)
  })

  test('建员工成功/失败都有朗读出口：状态提示可聚焦、错误与输入框关联', () => {
    // 提交按钮 disabled 期间会把焦点丢给 body，成功后交给结果提示。
    expect(employees).toMatch(/createNoticeRef/)
    expect(employees).toMatch(/focusNoticeAfterCreate/)
    expect(employees).toMatch(/role="status" aria-live="polite" tabIndex=\{-1\} ref=\{createNoticeRef\}/)
    // 必填错误：role=alert + 输入框 aria-invalid / aria-describedby 指向同一个提示。
    expect(employees).toMatch(/id=\{nameError \? 'emp-create-error' : undefined\}/)
    expect(employees).toMatch(/aria-invalid=\{nameError \? true : undefined\}/)
    expect(employees).toMatch(/aria-describedby=\{nameError \? 'emp-create-error' : undefined\}/)
  })

  test('登录失败：错误是 alert，且焦点回到密码框（已实测按钮 disabled 会丢焦点）', () => {
    expect(login).toMatch(/className="os-alert" role="alert"/)
    expect(login).toMatch(/document\.getElementById\('os-secret'\)\?\.focus\(\)/)
  })

  test('关键路径的控件都有可读名字：不许出现「按钮 未命名」', () => {
    // 图标按钮必须带 aria-label / title；导航项用可见文字。
    expect(shell).toMatch(/aria-label=\{`通知，\$\{items\.length\} 条`\}/)
    expect(shell).toMatch(/aria-label="退出登录"/)
    // 全站图标一律 aria-hidden，避免读屏读出乱码路径。
    expect(login).toMatch(/aria-hidden="true"/)
  })
})

describe('Windows 高对比度（forced-colors）契约', () => {
  const osCss = read('./os.css')
  const hc = osCss.slice(osCss.indexOf('@media (forced-colors: active)'))

  test('适配块存在，且不改动正常主题', () => {
    expect(hc).not.toBe('')
    // @media 包裹：只在系统高对比度下生效。
    expect(osCss.indexOf('@media (forced-colors: active)')).toBeGreaterThan(
      osCss.indexOf('prefers-reduced-motion'),
    )
  })

  test('按钮/输入/卡片改用系统色重建边界，而不是只剩文字', () => {
    expect(hc).toMatch(/\.os-btn[\s\S]{0,400}border:\s*1px solid ButtonText/)
    expect(hc).toMatch(/background:\s*ButtonFace/)
    expect(hc).toMatch(/\.os-input[\s\S]{0,600}border:\s*1px solid CanvasText/)
    expect(hc).toMatch(/\.os-card[\s\S]{0,600}border:\s*1px solid CanvasText/)
  })

  test('当前页/当前页签仍有可辨状态：2px Highlight 描边 + 加粗', () => {
    expect(ruleBody(hc, ".os-nav-item[aria-current='page'],")).toMatch(
      /border:\s*2px solid Highlight/,
    )
    expect(ruleBody(hc, ".os-nav-item[aria-current='page'],")).toMatch(/font-weight:\s*700/)
    // 回归防线：Highlight 实底 + HighlightText 白字在 Edge 下会把中文画成白块。
    expect(hc).not.toMatch(/color:\s*HighlightText/)
  })

  test('状态点与徽章不再靠彩色实底：空心环 + 系统色边框', () => {
    expect(ruleBody(hc, '.os-dot')).toMatch(/background:\s*Canvas !important/)
    expect(ruleBody(hc, '.os-dot')).toMatch(/border:\s*1px solid CanvasText/)
    expect(hc).toMatch(/\.os-badge\[data-tone\][\s\S]{0,120}border:\s*1px solid CanvasText/)
  })

  test('禁用按钮不靠 55% 透明度消失，焦点环使用系统高亮色', () => {
    expect(ruleBody(hc, '.os-btn:disabled')).toMatch(/opacity:\s*1/)
    expect(ruleBody(hc, '.os-btn:disabled')).toMatch(/dashed GrayText/)
    expect(ruleBody(hc, '.os-input:focus-within,')).toMatch(/outline:\s*3px solid Highlight/)
  })
})

describe('手机端触控目标（360px 复查）', () => {
  test('可点元素在手机端 ≥44×44：图标按钮、链接、按钮、输入与下拉', () => {
    const osCss = read('./os.css')
    // 只查新增的手机端修正段，避免命中同文件里更早的同名选择器。
    const mobileFix = osCss.slice(osCss.indexOf('手机端触控与窄屏修正（360px 复查）'))

    const icon = ruleBody(mobileFix, "[data-mode='mobile'] .os-icon-btn")
    expect(icon).toMatch(/width:\s*44px/)
    expect(icon).toMatch(/height:\s*44px/)

    for (const selector of [
      "[data-mode='mobile'] .os-link",
      "[data-mode='mobile'] .os-btn",
      "[data-mode='mobile'] .os-input button",
      "[data-mode='mobile'] .sess-btn",
      "[data-mode='mobile'] .jarvis-send",
      "[data-mode='mobile'] .jarvis-chips .chip",
      "[data-mode='mobile'] .chat-model-action",
    ]) {
      expect(ruleBody(mobileFix, selector), selector).toMatch(/min-height:\s*44px/)
    }

    const input = ruleBody(mobileFix, "[data-mode='mobile'] .os-input input,")
    expect(input).toMatch(/min-height:\s*44px/)

    // 输入框本身（不是只套在外层）也要 ≥16px：否则 iOS Safari 聚焦时自动放大整页。
    const inputShell = ruleBody(mobileFix, "[data-mode='mobile'] .os-input {")
    expect(inputShell).toMatch(/font-size:\s*16px/)

    // 顶栏搜索：外层 label 负责 44px 高，点空白处也能聚焦。
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .os-search")).toMatch(/min-height:\s*44px/)

    // 首次初始化 / 账号面的无壳输入框（.os-input-plain）同样要 44px + 16px。
    const plainInput = ruleBody(mobileFix, "[data-mode='mobile'] .os-input-plain")
    expect(plainInput).toMatch(/min-height:\s*44px/)
    expect(plainInput).toMatch(/font-size:\s*16px/)

    const approval = ruleBody(mobileFix, "[data-mode='mobile'] .profile-input,")
    expect(approval).toMatch(/min-height:\s*44px/)

    // 登录页两个切换页签与「忘记密码」。
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .os-check,")).toMatch(/min-height:\s*44px/)
  })

  test('窄屏下表单、会话栏与副标题不再被压成竖排窄柱', () => {
    const osCss = read('./os.css')
    const mobileFix = osCss.slice(osCss.indexOf('手机端触控与窄屏修正（360px 复查）'))

    // 创建员工 / 模型表单在手机上单列排布，字段不再被 3 列网格压到 4px。
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .os-operator-form")).toMatch(
      /flex-direction:\s*column/,
    )
    expect(
      readFromRoot('src/pages/MyEmployees.tsx'),
      '行内 gridTemplateColumns 会压过手机端单列规则，必须移除',
    ).not.toMatch(/style=\{\{\s*gridTemplateColumns/)

    // 会话栏允许换行，身份输入独占一行，不再把「新会话 / 删除」挤出卡片。
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .jarvis-sessions")).toMatch(
      /flex-wrap:\s*wrap/,
    )
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .sess-user")).toMatch(/flex:\s*1 1 100%/)

    // 副标题独占一行。
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .os-page-sub")).toMatch(
      /flex-basis:\s*100%/,
    )
  })

  test('窄屏对话区：装饰全息头像隐藏，输入框至少两行高', () => {
    const osCss = read('./os.css')
    const mobileFix = osCss.slice(osCss.indexOf('手机端触控与窄屏修正（360px 复查）'))
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .jarvis-left")).toMatch(/display:\s*none/)
    expect(ruleBody(mobileFix, "[data-mode='mobile'] .jarvis-input-wrap textarea")).toMatch(
      /min-height:\s*60px/,
    )
  })

  test('软键盘弹起时收缩布局视口，底栏与输入框不被键盘盖住', () => {
    const html = readFromRoot('index.html')
    expect(html).toMatch(/interactive-widget=resizes-content/)
    expect(html).toMatch(/viewport-fit=cover/)
  })
})

describe('手机端七项底栏（目标与任务入口）', () => {
  const osCss = read('./os.css')

  test('七项共用一行时允许等比压缩，不会被长标签撑宽', () => {
    expect(ruleBody(osCss, "[data-mode='mobile'] .os-bottom-nav")).toMatch(
      /grid-auto-columns:\s*minmax\(0,\s*1fr\)/,
    )
    expect(ruleBody(osCss, '.os-bottom-item')).toMatch(/min-width:\s*0/)
  })

  test('360px 窄屏收紧字号与内边距，不换行、不横向溢出', () => {
    const narrow = osCss.slice(osCss.indexOf('@media (max-width: 420px)'))
    expect(narrow).not.toBe('')
    expect(ruleBody(narrow, '.os-bottom-item')).toMatch(/font-size:\s*11px/)
    expect(ruleBody(narrow, '.os-bottom-item')).toMatch(/padding:\s*6px 1px/)
  })

  test('底栏入口仍是原生 button（可 Tab 聚焦），且当前页有 aria-current', () => {
    const shell = read('./components/Shell.tsx')
    expect(shell).toMatch(/className="os-bottom-item"/)
    expect(shell).toMatch(/aria-current=\{active === item\.key \? 'page' : undefined\}/)
  })
})
