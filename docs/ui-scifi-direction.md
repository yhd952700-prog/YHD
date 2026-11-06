# 鎏灏控制台 · 科幻未来风视觉规范

> 版本：Y1.0 / 2026-11-06
> 适用范围：`apps/console/console`（React 19 + Vite + TS）
> 文档性质：实施规范，不含代码改动。本文只定义做法、令牌、时机与验收标准。
>
> 边界声明
> - 本文不复制任何第三方项目的源码、品牌名、Logo、图标资产、字体文件或文案原句。
> - 对通用交互范式（HUD 角标、时间线节点、命令面板、状态灯）只借鉴其“交互模式与信息结构”，实现全部原创重写。
> - 品牌条款不变：`LiuHao AI OS / 鎏灏智能中枢` 名称、现有六边形标志、金色品牌徽记均保持原样，不重绘、不改名、不替换。
> - 字体只用系统字体；任何开源字体必须在核实许可证（SIL OFL 等）并随包附带许可证后才允许自托管，否则不引入。

---

## 0. 现有基线（规范必须叠在它上面）

先确认现状，避免“推倒重来”：

| 项目 | 现状 | 位置 |
|---|---|---|
| 样式体系 | 纯 CSS + 语义类名，无 Tailwind、无 CSS Modules、无 CSS-in-JS | `src/os.css`、`src/App.css` |
| 主题驱动 | `<html data-mode="desktop|web|mobile">` + `data-theme="dark|light"`，全部颜色走 CSS 变量 | `src/lib/mode.ts`、`src/os.css` L17-L72 |
| 双令牌体系 | `os.css` 的 `--os-*`（新组件）+ `App.css` 的 `--bg/--neon/--gold`（旧对话、审批组件） | `src/os.css`、`src/App.css` L6-L29 |
| 默认主题 | 桌面/手机深色，网页版浅色 | `src/lib/mode.ts` L30-L35 |
| 布局 | 12 列网格、248px 侧栏、卡片 14px 圆角、16px 间距 | `src/os.css` L115-L142、L454-L483 |
| 移动导航 | `MOBILE_NAV_KEYS = ['overview','employees','approval','settings']`，**缺对话入口** | `src/lib/nav.ts` L79、`src/components/Shell.tsx` L157 |
| 焦点可见性 | 无全局 `:focus-visible` 规则；6 处 `outline: none` 仅靠边框变色 | `src/App.css` L1104/1173/1190/1317；`src/os.css` L392/1017 |
| 动效 | 极少：`0.14s` 悬停过渡、骨架 shimmer、旧组件 blink/pulse | `src/App.css` L1242-L1252、`src/os.css` L720-L730 |

结论：**不引入新框架、不新增依赖、不重写组件结构**。规范以“新增一层 `--sc-*` 语义令牌 + 追加少量类 + 修正无障碍”的方式落地，旧组件通过令牌映射逐步收编。

---

## 1. 视觉基调

### 1.1 深色为主，浅色保留为真实可用的第二主题

- 默认深色：控制台是长时间盯屏的监控/指挥界面，深底能让“光”承担状态语义（活跃=发光、异常=红），并降低大面积亮度疲劳。现有桌面端与手机端已是深色，延续即可。
- 浅色不是“降级版”：网页版/白天/强光环境、打印截图仍需完整可用。浅色下禁止套用发光效果，改为“边框 + 阴影 + 实底徽章”表达层级。
- 禁止只有深色能看、浅色文字糊成一团的实现。两套主题的对比度都必须过 AA（见 1.4）。

### 1.2 品牌色与语义色（十六进制）

沿用现有令牌值，新增仅 5 个：

| 令牌 | 深色值 | 浅色值 | 用途 | 解决的问题 |
|---|---|---|---|---|
| `--os-bg` | `#050912` | `#F4F7FD` | 画布 | 保持现有观感，不改整体色温 |
| `--sc-bg-deep` | `#03060E` | `#E9EFFA` | 画布底部渐隐 | 给页面一个“深度底”，避免纯平背景显得像没做完 |
| `--os-panel` | `#0B1120` | `#FFFFFF` | 一级面板 | 与背景拉开 1 级层次 |
| `--os-panel-2` | `#101A2E` | `#F7F9FE` | 输入框/内嵌块 | 输入区在一级面板上仍可辨认 |
| `--os-border` | `#1C2942` | `#DBE4F3` | 结构边界 | 明确但不过亮，避免“满屏线条” |
| `--os-border-soft` | `#141F36` | `#E8EEF8` | 分隔线 | 表格/列表分隔不抢内容 |
| `--sc-hairline` | `rgba(120,190,255,.14)` | `rgba(47,111,228,.10)` | 面板顶部 1px 高光 | 让卡片有“受光面”，替代纯阴影堆叠 |
| `--os-text` | `#E8EEFC` | `#101A30` | 正文 | — |
| `--os-text-dim` | `#93A3C4` | `#57648A` | 次级文字 | — |
| `--os-text-faint` | `#8A9AC0` | `#59647E` | 元信息 | 现有值已过 AA，不用“更淡的灰”换美观 |
| `--os-accent` | `#4D8DFF` | `#2F6FE4` | 主强调、选中、链接 | 保持品牌蓝，承担“当前焦点”语义 |
| `--os-accent-2` | `#9B6BFF` | `#7C4DDB` | 渐变副色 | 与主色形成冷紫过渡，不引入第三种主色 |
| `--sc-cyan` | `#35E0FF` | `#0096B8` | 实时/数据流/扫描 | 唯一“动”的颜色，出现即有实时含义 |
| `--sc-gold` | `#FFC93C` | `#A8730A` | 品牌徽记、成就/目标 | 沿用旧 `--gold`，只用于品牌与目标，不用于警告 |
| `--os-ok` | `#2EE6A8` | `#0F9D76` | 成功/健康 | — |
| `--os-warn` | `#FFB74D` | `#C47C14` | 待处理/降级 | — |
| `--os-danger` | `#FF5D6C` | `#D93A4A` | 失败/拒绝/越权 | — |

浅色主题新增 3 个文字专用墨色（实底填充色不变，避免视觉漂移）：

- `--os-accent-text: #1F5BD0`（浅底上的蓝色文字 5.7:1；`#2F6FE4` 直接当文字只有 4.4:1，不达标）
- `--os-ok-ink: #0A7A5B`（浅底绿色文字 5.3:1）
- `--os-warn-ink: #8A5606`（浅底橙色文字 6.2:1）
- `--os-danger-ink` 已有 `#B4232F`，沿用。

### 1.3 用色纪律（否则“科幻”会变成“廉价 RGB”）

1. 一屏最多 2 个强调色：主蓝 + 青；紫色仅出现在品牌渐变和装饰性线条，不用于正文或按钮文字。
2. 金色只属于品牌、目标、成就；**绝不**当警告色（警告已有的橙/红）。
3. 状态不靠颜色单一维度：颜色 + 形状 + 文字三者同时表达（见 4.6）。
4. 发光只给“正在发生”的东西。静止面板一律不发光，避免“处处发光=处处不发光”。

### 1.4 对比度实测（WCAG 2.1 相对亮度公式）

| 前景 / 背景 | 对比度 | 结论 |
|---|---|---|
| `#E8EEFC` / `#050912` | 17.1:1 | AAA |
| `#93A3C4` / `#0B1120` | 7.4:1 | AAA 小字可用 |
| `#8A9AC0` / `#0B1120` | 6.7:1 | AA 小字可用 |
| `#4D8DFF` / `#0B1120` | 5.9:1 | AA 文字可用 |
| `#9B6BFF` / `#0B1120` | 5.3:1 | AA 文字可用 |
| `#2EE6A8` / `#0B1120` | 11.7:1 | AAA |
| `#FFB74D` / `#0B1120` | 10.0:1 | AAA |
| `#FF5D6C` / `#0B1120` | 6.3:1 | AA |
| `#FFC93C` / `#0B1120` | 12.3:1 | AAA |
| `#35E0FF` / `#0B1120` | 11.9:1 | AAA |
| 白色 / `#4D8DFF`（深色主色实底） | 3.2:1 | **不可**；深色主色实底必须用 `--os-on-accent: #06121F`（6.0:1） |
| 白色 / `#2F6FE4`（浅色主色实底） | 4.7:1 | AA，浅色主题按钮白字可用 |

---

## 2. 字体与层级

### 2.1 字体栈（不下载字体）

```css
--sc-font-ui: system-ui, -apple-system, 'Segoe UI', 'PingFang SC',
  'Microsoft YaHei', 'Noto Sans SC', sans-serif;
--sc-font-mono: ui-monospace, 'Cascadia Mono', 'SF Mono', Consolas,
  'Liberation Mono', monospace;
```

- 去掉现有 `'Inter'` 首选：仓库未自带字体文件，首选 `Inter` 在多数机器上直接回落，声明是死配置。若未来要真正使用 Inter，必须自托管 woff2、保留 SIL OFL 1.1 许可证并核实分发条款，未核实前不引入。
- 中文优先由系统字体承担（PingFang SC / Microsoft YaHei / Noto Sans SC），避免中文落到西文字体后字重不一致。
- 等宽字体只用于 ID、哈希、trace_id、token 计数、时间戳；**正文和中文禁止等宽**，中文字符等宽会破坏节奏且 Windows 下常回落成难看的宋体。

### 2.2 字号阶梯（px，现有体系延续）

| 层级 | 字号 / 行高 | 字重 | 用途 | 约束 |
|---|---|---|---|---|
| Display | 28 / 34 | 700 | 登录页主标题（桌面 ≥1280 才用） | 手机降到 22/28 |
| Page Title | 20 / 26 | 650 | 页面标题（现有 `--os-page-title`） | 手机 17/23 |
| Section | 15 / 22 | 620 | 卡片组标题、区块分隔 | 不与 Page Title 同级使用 |
| Card Title | 13.5 / 19 | 620 | 卡片标题（现有值） | 单行省略，禁止换行挤压 |
| Body | 14 / 22 | 400 | 正文、说明 | 中文行高 1.6，西文 1.5 |
| Body-sm | 12.5 / 19 | 400/560 | 列表主行 | 行距不低于 1.5 |
| Meta | 11 / 16 | 400 | 次要说明、来源 | 不再往下设字号 |
| Micro | 10.5 / 14 | 620 | 导航分组标签、英文大写标签 | 仅英文/数字；中文不用 10.5 |
| Metric | 25 / 28 | 680 | KPI 数值 | 手机 21/24 |

规则：

1. 字重只用 400 / 560 / 620 / 650 / 680 / 700/680 六档，现有 660 保留；不引入 300 细体（深底细体小字会发虚）。
2. 中文正文**不加字间距**；`letter-spacing` 只用于英文大写标签（`LIVE`、`ENTRY` 等），值 1.1px。
3. 数字统一 `font-variant-numeric: tabular-nums`，避免实时刷新时列宽跳动（现有 KPI、时钟已这么做，需推广到表格与计数）。
4. 标题与正文的层级差靠“字号+字重”表达，不靠发光或彩色文字。彩色标题只允许登录页品牌标题一处。
5. 200% 缩放：字号用 px 不影响浏览器缩放，真正要避免的是固定高度容器裁切；所有容器改为 `min-height`，文字容器允许换行（见 8.3）。

---

## 3. 空间与网格

### 3.1 间距刻度（4px 基准）

```
4  8  10  12  14  16  18  20  22  24  26  32  40  48
```

- 组件内：4/6/8；卡片内：14/16；卡片间：14/16；区块间：24；页面左右：26（网页 20、手机 14，沿用现有）。
- 新增令牌：`--sc-space-1` 到 `--sc-space-10` 对应 `4/8/12/16/20/24/32/40/48/64`。旧 px 值不强制替换，新增代码优先用令牌。

### 3.2 圆角

| 用途 | 值 |
|---|---|
| 徽章 / 药丸 | 999 |
| 内联标签、代码块 | 6 |
| 导航项、图标按钮 | 9 |
| 输入框 | 10 |
| 空态框 | 11 |
| 卡片、KPI、面板 | 14 |
| 登录卡、模态 | 18 |

禁止超过 18 的大圆角（会从“控制台”滑向“消费级 App”），也禁止全直角（与现有体系冲突）。

### 3.3 边框与分隔

1. 结构边界：`1px solid var(--os-border)`。
2. 分隔线：`1px solid var(--os-border-soft)`，只用于信息分组，不用于装饰性横线堆叠。
3. **虚线语义唯一**：`dashed` 只表示“此处本应有数据但暂无/不可用”（现有 `.os-empty` 用法）；有数据的容器一律实线。
4. 焦点环例外：2px，见第 8 节。
5. 面板顶部受光线：`inset 0 1px 0 var(--sc-hairline)`，与阴影一起构成“受光 + 投影”，比纯黑阴影更像实体面板。

### 3.4 栅格

- 12 列、gap 16、内容区最大宽度 1560px（超宽屏居中，避免一行 8 列图表拉伸到 3000px 无法扫读）。
- KPI 4 列 → 网页 2 列 → 手机 2 列；主图表 8 列 + 侧栏 4 列；手机全部 12 列（现有 `os-span-*` 规则已具备，不新增）。
- 列表页信息密度优先于留白：单行高度 36-44px，禁止为“科技感”把行高拉到 56px 以上。

---

## 4. 科幻感的实现手法

每条都写明：解决什么问题、怎么实现、性能代价、什么时候不要用。**原则：科幻感来自“信息在发光”，不是“背景在动”。**

### 4.1 静态网格底 + 地平线光（Shell 级，1 处）

**解决的问题**：现有背景只有一个径向渐变，大面积深色缺少尺度感，页面显得“空”。

```css
.os-app {
  background-image:
    linear-gradient(var(--os-grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--os-grid-line) 1px, transparent 1px),
    var(--os-bg-grad);
  background-size: 32px 32px, 32px 32px, auto;
  background-attachment: fixed;
}
```

- 代价：仅一次绘制，静态，无动画；`background-attachment: fixed` 在移动端可能触发滚动重绘，**手机禁用**。
- 不要用：卡片内部（叠加会形成摩尔纹）、表格页、亮色主题（浅色网格会像草稿纸）。
- 网格透明度沿用 `--os-grid-line`（深色 9%），超过 14% 会与文字抢注意力。

### 4.2 活跃面板微光（HUD 焦点）

**解决的问题**：现在“当前面板”只能靠标题文字辨认；多卡片页面里，人不知道该看哪块。

```css
.sc-panel-active {
  border-color: color-mix(in srgb, var(--os-accent) 45%, var(--os-border));
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--os-accent) 22%, transparent),
    0 0 24px color-mix(in srgb, var(--os-accent) 16%, transparent),
    var(--os-shadow);
}
```

- 触发条件必须是真实状态（当前选中会话、正在执行的 workflow、待处理的审批），不能“随机发光”。
- 代价：静态阴影，切换时一次重绘；1-2 个元素无感。
- 不要用：列表行、正文卡片、超过 2 个同屏元素；浅色主题改用 `0 0 0 2px var(--os-accent-soft)`，禁止大范围外发光。

### 4.3 HUD 四角括号（当前焦点容器）

**解决的问题**：面板之间只有边框粗细差异，缺少“这块被锁定/被聚焦”的强信号。

```css
.sc-hud { position: relative; }
.sc-hud::before,
.sc-hud::after {
  content: '';
  position: absolute;
  width: 14px; height: 14px;
  border: 0 solid color-mix(in srgb, var(--os-accent) 70%, transparent);
  pointer-events: none;
}
.sc-hud::before { top: -1px; left: -1px; border-width: 1px 0 0 1px; }
.sc-hud::after { right: -1px; bottom: -1px; border-width: 0 1px 1px 0; }
```

- 代价：2 个伪元素，静态，零动画。
- 不要用：每张卡片都加（会变成“满屏括号”噪声）；手机端不加（14px 角标在小屏上就是脏点）。

### 4.4 面板三级层级

| 层级 | 表现 | 用途 |
|---|---|---|
| L0 画布 | `--os-bg` + 网格 | 页面底 |
| L1 面板 | `--os-panel` + 1px 边框 + 顶部受光线 + `--os-shadow` | 卡片、KPI |
| L2 浮层 | `--os-panel-2` + 更强阴影 + 1px 主色边 | 通知弹层、模态、命令面板 |

- 问题：现有卡片和 KPI 用同一套阴影，浮层弹出来时层级不明显。
- 代价：无；只是令牌分配。
- 禁止：L2 再套 L2（三层以上阴影会糊成一团），禁止对同一元素同时用外发光 + 大阴影 + 边框高亮。

### 4.5 状态环（替代纯色圆点）

**解决的问题**：现有 `.os-dot` 只有颜色，红绿色盲用户无法区分“运行中/失败”。

```css
.sc-status {
  display: inline-grid;
  place-items: center;
  width: 12px; height: 12px;
  border-radius: 50%;
  border: 1.5px solid currentColor;
}
.sc-status::after {
  content: '';
  width: 4px; height: 4px;
  border-radius: 50%;
  background: currentColor;
}
/* 仅“正在执行”时呼吸 */
.sc-status[data-state='active']::after { animation: sc-breathe 2.4s ease-in-out infinite; }
@keyframes sc-breathe {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .45; transform: scale(.72); }
}
```

状态语义（必须同时配文字，例如“运行中”）：

| 状态 | 颜色 | 形态 |
|---|---|---|
| 空闲 | `--os-text-faint` | 空心环 |
| 运行中 | `--os-ok`（或青） | 空心环 + 实心点呼吸 2.4s |
| 等待人类 | `--os-warn` | 空心环 + 实心点慢闪 1.2s |
| 失败/拒绝 | `--os-danger` | 实心环（无内点，形状即差异） |
| 未知/降级 | `--os-text-faint` | 虚线环（`border-style: dashed`） |

- 代价：`opacity/transform` 动画走合成层，单元素可忽略；同屏 ≤ 8 个呼吸元素。
- 不要用：让所有状态环都呼吸（分不清谁在动）；用纯颜色替代文字。

### 4.6 数据流线（工作流/时间线专用）

```css
.sc-flow-line {
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--sc-cyan), transparent)
    no-repeat;
  background-size: 40% 100%;
  animation: sc-flow 2.2s linear infinite;
}
@keyframes sc-flow {
  from { background-position: -40% 0; }
  to { background-position: 140% 0; }
}
```

- 解决的问题：Goal→Workflow→Execution 的推进关系在现有页面上只能靠文字，链路方向不直观。
- 代价：只重绘 1px 高的区域，可忽略；同屏 ≤ 2 条。
- 不要用：静态列表当装饰线；跨卡片长距离连线（跨容器定位脆弱）；暂停时继续流动（会撒谎）。

### 4.7 流式前沿扫描（只在真实流式期间）

**首选做法**：在正在输出的消息底部放一条 2px 高的横向渐变条，结束时立即移除。

```css
.sc-stream-frontier {
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--sc-cyan), transparent);
  animation: sc-flow 1.6s linear infinite;
  opacity: .8;
}
```

- 解决的问题：现有流式输出只有一个闪烁光标，长回答里“还在写”不够明显。
- 代价：极小区域重绘，可忽略。
- **不要用**：整屏覆盖的扫描线（`inset: 0` 覆盖文字会降低对比度并触发大面积合成）；给静态卡片加扫描线。全屏扫描只有在“页面级加载/初始化”时最多 1 次、≤400ms。

### 4.8 KPI 数值切换

- 值变化时：`opacity 1 → .55 → 1` + `translateY(2px → 0)`，160ms，`ease-out`。
- 禁止：数字乱序滚动、逐位翻转、无中生有的“涨跌音效”。控制台里，数字可读性 > 炫技。
- 代价：单元素 160ms，可忽略；仅在值真实变化时触发。

### 4.9 品牌全息字与金色徽记

- 现有品牌渐变字（`brand-cn` / `os-brand-name`）保留，只允许出现在品牌区、登录页标题。
- 正文标题一律纯色；`background-clip: text` 在移动端低端机上有掉帧风险，禁止扩散到列表标题。
- 金色徽记只用于目标/成就/品牌副标，不做按钮底色（金色实底上的黑字在浅色主题下会失去层级）。

### 4.10 噪点纹理（默认不用）

- 若必须用：一张 128×128 的静态 SVG data-URI，`opacity ≤ .03`，只铺在 Shell 背景，禁止动画。
- 代价：一个全屏混合图层，移动端明显增加 GPU 负担。
- 结论：**默认不做**；用 4.1 网格已足够提供质感。

---

## 5. 动效规范

### 5.1 时长与缓动令牌

```css
--sc-dur-hover: 80ms;
--sc-dur-state: 120ms;
--sc-dur-enter: 180ms;
--sc-dur-layout: 240ms;
--sc-ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
--sc-ease-in: cubic-bezier(0.4, 0, 0.2, 1);
--sc-ease-loop: linear;
```

- 交互反馈：80ms（颜色、边框、背景）。
- 状态切换：120ms（选中、展开、徽章）。
- 浮层进入：180ms（`opacity` + `translateY(4px)`）；退出 120ms。
- 布局变化：240ms，且**只允许** `transform` / `opacity`，禁止过渡 `width/height/top/left`。
- 环境循环：1.6s（扫描前沿）/ 2.2s（数据流）/ 2.4s（状态呼吸）。

### 5.2 允许动的地方

| 场景 | 动效 | 上限 |
|---|---|---|
| 悬停/聚焦 | 背景、边框、文字色 | 80ms |
| 选中面板 | 微光出现 | 120ms，无位移 |
| 流式回答 | 底部前沿线 | 仅流式期间 |
| 状态环 | 呼吸 | 仅“运行中/等待” |
| 工作流推进 | 连接线流动 | 同屏 ≤ 2 条 |
| 浮层打开 | 淡入 + 4px 上移 | 180ms |
| 新事件入列 | 淡入 + 4px | 180ms，禁止整列重排动画 |

### 5.3 禁止动的地方

1. 正文、表格、日志、审批内容上方的任何动画覆盖层。
2. 输入框聚焦时的缩放/位移（会让光标跳动）。
3. 页面切换的整页翻转/3D 透视/视差。
4. 常驻无限动画超过 3 个（同屏）。
5. 用 `filter: blur()` 或大面积 `box-shadow` 做循环动画（每帧重绘，低端机掉到 20fps）。
6. 滚动驱动的特效（本产品是数据密集控制台，滚动里加效果只会拖慢阅读）。
7. 阻止输入的“入场动画”（例如按钮 400ms 内不可点）。

### 5.4 减少动态与省电

```css
@media (prefers-reduced-motion: reduce) {
  .sc-status[data-state='active']::after,
  .sc-flow-line,
  .sc-stream-frontier { animation: none; }
  * { transition-duration: 80ms !important; }
}
```

- 现有 `os.css` L1441-L1448 已全局关闭 transition，保留；新增动画必须显式补进该块，避免遗漏。
- 页面不可见时（`document.hidden`）不新增 JS 定时器；能纯 CSS 的循环不写 JS。

---

## 6. 关键页面重构示意

统一原则：**先回答“系统现在怎么样、需要我做什么”，再展示“系统多酷”。**

### 6.1 登录页

现状：单卡居中（`max-width: 396px`），品牌区 + 表单。见 `src/components/Login.tsx`、`os.css` L932-L950。

改动（桌面 ≥1280）：

```
┌───────────────────────────────┬──────────────────┐
│ 品牌区（六边形标志 + 名称）      │  登录卡片 396px   │
│ 真实认证状态（已配置/未配置）    │  身份输入         │
│ 端形态标记（桌面/网页/手机）     │  密码 + 显示切换  │
│ 地平线光 + 网格（唯一装饰）      │  登录按钮         │
└───────────────────────────────┴──────────────────┘
```

- 左列为 42%，只放**真实信息**：`/v1/auth/config` 的配置状态、当前客户端形态（`CLIENT_LABEL`）；禁止放“系统已安全运行 99.9%”这类假 HUD 数字。
- 信息优先级：① 为什么不能登录（未配置凭据时的修复命令）② 身份输入 ③ 形态标记。
- 视觉：左列一条地平线渐变 + 网格；卡片右侧加 1px 主色受光边；登录按钮保持渐变实底。
- 手机：删除左列，回到单卡居中，上下留白 24px；不做全屏动画。

### 6.2 总览

现状：4 KPI + 图表（`BrainMap/DonutChart/LineChart/NetworkGraph`）+ 活动流，见 `src/pages/Overview.tsx`。

改动：

```
第一屏（自上而下）
1. 指挥带：4 KPI（AI 员工 / 运行中任务 / 审计事件 / 健康度）
2. 需要我：待审批 / 失败执行 / 降级检查 —— 真实计数，无则显示“无待处理”
3. 主图表（8 列，加 HUD 角标）+ 健康环（4 列）
4. 实时活动流（真实审计事件，≤8 条）
```

- 新增“需要我”条：把现在散落在侧栏徽标、审批页、健康卡里的“待处理”聚合到首屏；解决的问题是“登录后要翻三个页面才知道有没有事”。
- HUD 角标只加在“主图表”一处，标出当前焦点。
- KPI 数值字号 25→28（桌面），手机 21；数值变化走 4.8。
- 禁止：给四个 KPI 每个都加不同颜色的发光；图表外加装饰性星图。

### 6.3 对话 / 管家时间线（工作台）

现状：`ChatPanel` 复用旧样式（`.jarvis-zone`），消息列表 + 工具卡片 + 快捷 chips，见 `src/components/ChatPanel.tsx`。

改动：

```
┌ 会话轨 180px ┬──────── 时间线（最大 760px） ────────┬ 执行侧栏 260px ┐
│ 会话列表      │ 用户节点（右/浅底）                  │ 当前工具调用    │
│ 新建会话      │ 管家节点（左/面板底 + 状态环）        │ 耗时/结果       │
│              │ 工具调用折叠为“执行节点”              │ 失败原因        │
│              │ 流式时底部前沿线                     │                │
└──────────────┴───────────────────────────────────┴────────────────┘
```

- 信息优先级：① 当前执行状态（状态环 + 一行文字）② 助手正文 ③ 工具入参/出参（默认折叠）。
- 时间线左侧 1px 竖线 + 8px 节点，只有“正在执行”的节点呼吸；已完成的节点静态。
- 执行侧栏在 <1280 折叠进消息卡片，不另起一列。
- 文字宽度限制 72 字符/行（中文约 38 字），超宽屏不拉伸；输入框固定底部，高度自适应 1-4 行。
- 禁止：打字机逐字动画（延迟阅读）、消息气泡发光、每轮对话换颜色。

### 6.4 员工列表（我 AI 员工 / Roster）

现状：`Roster` 读 `/v1/dashboard/roster`，分内核/能力层/服务面三组，见 `src/pages/Directory.tsx` L37 起。

改动：

- 列表行升级为“员工卡带”：左侧 3px 竖条区分内核（蓝）/能力层（紫）/服务面（青），右侧状态环 + 状态文字。
- 顶部三段计数（员工 / 内核 / 能力层）沿用真实 totals；加一个“筛选”输入（现有 `query` 已支持搜索）。
- 卡片不做 3D 倾斜、不做头像插画（无真实头像数据）；字母/图标沿用现有原创图标集。
- 手机：单列，竖条保留，状态环 12px，卡内两行信息。

### 6.5 审批中心

现状：旧 `ApprovalCenter` + `.approval-panel` 暗色样式，令牌行 + 状态 + 授权表单 + 列表。

改动：

- 这是全站最“安静”的页面：无网格、无扫描线、无流动线。仅保留 1px 边框与状态色。
- 布局：顶部状态条（已认证/未认证、真实主体）→ 待处理列表（主）→ 授权表单（次，默认折叠在侧栏或底部）。
- 每条授权记录：动作 / 目标 / 有效期倒计时 / 撤销；倒计时用 tabular-nums，临近到期（<60s）转 warn 色 + 文案，不闪烁。
- 危险操作（通配 `*`）必须二次确认；确认区用 danger 边框，不用红色整块底（避免整页报警感）。
- 信息优先级：① 未认证时明确说明“未认证，不能操作” ② 待处理数量 ③ 表单细节。
- 禁止：审批页放任何装饰动画（信任页面动起来会削弱“可靠”感）。

---

## 7. 移动端适配（390 × 844）

### 7.1 导航五项（必须补对话入口）

现状四项：总览 / 员工 / 审批 / 我的（`src/lib/nav.ts` L79），工作台（对话）在手机上无法一键进入。

目标：

```ts
// 实施时改这一行即可；Shell 通过 ALL_NAV 查找，无需改结构
export const MOBILE_NAV_KEYS = ['overview', 'employees', 'workbench', 'approval', 'settings']
```

| 位置 | key | 显示文案 | 说明 |
|---|---|---|---|
| 1 | `overview` | 总览 | 默认页 |
| 2 | `employees` | 员工 | 现有逻辑把“我AI员工”替换为“员工”，保留 |
| 3 | `workbench` | 管家 | **新增，居中强调**：图标 22px，标签加 2px 主色点，作为主入口 |
| 4 | `approval` | 审批 | 待处理时显示真实计数徽标（≤99） |
| 5 | `settings` | 我的 | 现有文案，保留 |

`Shell.tsx` L157-L173 无需改遍历逻辑；仅当 `item.key === 'workbench'` 时加 `data-accent="center"` 即可（或在 CSS 用 `.os-bottom-item[data-key='workbench']`）。

### 7.2 尺寸与安全区（390×844）

| 项 | 值 |
|---|---|
| 顶部栏 | 高度 ≥52px + `env(safe-area-inset-top)`；搜索框全宽；时钟隐藏（已有规则） |
| 内容内边距 | 14px；底部额外 84px（为底栏 + 安全区留白，**现有 22px 不够，会遮住最后一行**） |
| 底栏 | `min-height: 56px`，`padding: 6px 4px calc(6px + env(safe-area-inset-bottom))` |
| 底栏项 | 每项 `min-height: 48px`；图标 20px（管家 22px）；文字 10.5-11px |
| 图标按钮 | 32→**40px**，热区 44px（用透明 padding 扩展，不放大视觉尺寸） |
| KPI | 2 列，gap 10，数值 21px |
| 卡片 | 圆角 12，padding 14，gap 12 |
| 触摸目标 | 所有可点元素 ≥44×44（含列表行、复选框、chip） |

### 7.3 必须移除或降级的特效

- 网格底（`background-attachment: fixed`）→ 关闭。
- HUD 角标 / 数据流线 / 扫描前沿 → 关闭。
- 卡片外发光 → 关闭（LED 屏上会发灰）。
- `backdrop-filter` 只保留顶栏与底栏两层；卡片内部禁止再加。
- 图表保持 2D，不做手势旋转/缩放动画。

### 7.4 表单与键盘

- 输入框 `font-size ≥ 16px`（防 iOS 自动放大）；当前 14px 会在聚焦时导致整页缩放。
- 登录页输入框、工作台 composer 均适用；视觉字号可用 zoom 前的 14px 设计值，但移动端实际渲染不得低于 16px。
- composer 随键盘上移使用 `position: sticky; bottom: calc(56px + env(safe-area-inset-bottom))`，不监听键盘高度做 JS 动画。

---

## 8. 无障碍底线与两项已知问题的解法

### 8.1 全局焦点环（先做，风险最低）

现状：`outline: none` 共 6 处，且全站没有 `:focus-visible` 规则，见：

- [os.css L392](file:///D:/LiuHao-AI-OS/apps/console/console/src/os.css#L387-L394)（顶栏搜索输入）
- [os.css L1017](file:///D:/LiuHao-AI-OS/apps/console/console/src/os.css#L1012-L1019)（登录输入）
- [App.css L1104](file:///D:/LiuHao-AI-OS/apps/console/console/src/App.css#L1093-L1110)（对话输入框）
- [App.css L1173](file:///D:/LiuHao-AI-OS/apps/console/console/src/App.css#L1165-L1178)（会话身份输入）
- [App.css L1190](file:///D:/LiuHao-AI-OS/apps/console/console/src/App.css#L1180-L1191)（会话下拉）
- [App.css L1317](file:///D:/LiuHao-AI-OS/apps/console/console/src/App.css#L1307-L1327)（画像输入/下拉）

统一规则（追加到 `os.css` 末尾，优先级足够覆盖旧规则）：

```css
:where(button, a, input, select, textarea, [role='button'], [tabindex]:not([tabindex='-1'])):focus-visible {
  outline: 2px solid var(--os-accent);
  outline-offset: 2px;
}

/* 旧组件暗底：金色环在深空底上对比更强，避免蓝环被背景吃掉 */
.approval-panel :where(button, input, select, textarea):focus-visible,
.jarvis-zone :where(button, textarea, input, select):focus-visible {
  outline: 2px solid var(--sc-gold);
  outline-offset: 2px;
}
```

注意：`outline: none` 的规则如果保持原样，会与焦点环冲突且难以排查。**实施时直接删除这 6 处 `outline: none`**，改由上面的 `:focus-visible` 统一接管；内层输入框若由外层容器显示焦点环，可保留 `outline: none`，但外层必须满足下面两条：

```css
/* 搜索框当前缺 focus-within，必须补 */
.os-search:focus-within {
  border-color: var(--os-accent);
  box-shadow: 0 0 0 2px var(--os-bg), 0 0 0 4px var(--os-accent);
}

/* 登录输入现有 3px 16% 软环，对比不足；改为实色双环 */
.os-input:focus-within {
  border-color: var(--os-accent);
  box-shadow: 0 0 0 2px var(--os-bg), 0 0 0 4px var(--os-accent);
}
```

旧组件（对话、审批）的输入框没有外层环，直接为它们补 `:focus-visible`：

```css
.jarvis-input-wrap textarea:focus-visible,
.sess-user-input:focus-visible,
.sess-select:focus-visible,
.profile-input:focus-visible,
.profile-select:focus-visible {
  outline: 2px solid var(--sc-gold);
  outline-offset: 2px;
  border-color: var(--border-strong);
}
```

验收：Tab 走遍侧栏 → 顶栏 → 内容 → 底栏，每一步都能看见环；用键盘 Enter/Space 能激活；Esc 能关闭通知/浮层。

### 8.2 手机底栏补对话入口

见 7.1。验收：390×844 下底栏 5 项不换行、不重叠，工作台可一键进入；`aria-current` 正确；每项 ≥44px；底栏不遮内容（内容底部留白 84px）。

### 8.3 200% 缩放

- 浏览器缩放 200% 时：不出现横向滚动（除图表内部允许 `overflow-x: auto`）；文字不裁切；按钮文字不溢出。
- 禁止固定高度：`height: 40px` 改 `min-height: 40px`；行高、卡片允许换行。
- 表格在缩放后自动切换为堆叠行（现有 `.os-model-table` 的 `data-label` 方案可复用，见 `os.css` L1394-L1423）。

### 8.4 对比度与不依赖颜色

- 所有正文对比度 ≥4.5:1，大字号 ≥3:1（见 1.4）。
- 状态同时有形状/文字（见 4.5）；图表色板必须能区分（现有图表已用色差，新增图例文字）。
- 焦点环与相邻颜色对比 ≥3:1；金色环在深色面板上 ≥10:1。

---

## 9. 实施顺序

| 阶段 | 内容 | 涉及文件 | 风险 | 收益 | 验收 |
|---|---|---|---|---|---|
| P0 | 无障碍三件事：全局焦点环 + 删除 6 处 `outline: none` + 手机底栏补 `workbench` + 44px 热区 | `os.css`、`App.css`、`lib/nav.ts` | 最低 | 最高（所有用户立即受益） | Tab 全链路可见；390×844 五栏可达 |
| P1 | 新增 `--sc-*` 语义令牌（不改现有值，仅补青/墨色/时长/缓动） | `os.css` | 低 | 中（为后续提供统一语言） | 构建通过；双主题无回归 |
| P2 | Shell + 登录页：网格底、地平线、面板层级、品牌区重排 | `os.css`、`Shell.tsx`、`Login.tsx` | 中 | **最高视觉收益**（第一印象） | 桌面/网页/手机三形态截图对比 |
| P3 | 总览：指挥带 + “需要我”条 + 主图表 HUD | `Overview.tsx`、`os.css` | 中 | 高（首屏决策效率） | 数据全部来自真实接口；空态正确 |
| P4 | 工作台时间线：会话轨、节点、执行侧栏、流式前沿 | `ChatPanel.tsx` 样式层、`App.css`/`os.css` | 中高 | 高（核心使用页） | 长回答、工具调用、失败重试均正常 |
| P5 | 员工列表 + 审批中心：竖条、状态环、安静化 | `Directory.tsx`、`ApprovalCenter.tsx`、`os.css` | 低 | 中 | 未认证态、通配二次确认、倒计时正确 |
| P6 | 微特效：扫描前沿、数据流线、数字切换（全部受真实状态门控） | `os.css`、少量组件状态类 | 中 | 低-中（锦上添花） | 无 JS 定时器；reduced-motion 下静止 |
| P7 | 旧 `App.css` 令牌收编到 `--sc-*`，删除重复色值 | `App.css` | 高（牵动对话/审批） | 低（维护性） | 视觉无差异，回归测试通过 |

顺序理由：先在 P0 修“不能用”的问题，再用 P2 拿“看起来变了”的收益；P6 特效放最后，因为它依赖 P3/P4 的真实状态类，提前做只能造假。

每阶段必须跑：`npm run build`、`npm test`、390×844 与 1440×900 两档目检、Tab 键走查。任一阶段不通过，不进入下一阶段。

---

## 10. 明确不做（看起来科幻但实际有害）

| 不做 | 理由 |
|---|---|
| 满屏粒子/星空/流星雨 | 需要 Canvas/WebGL 或大量 DOM，首屏掉帧；对监控数据无任何信息增益 |
| 大面积流动渐变背景 | 每帧重绘区域大，低端机 GPU 压力高；阅读时注意力被拉走 |
| 文字外发光/bloom/色差 | 降低文字锐度，中文小字尤其糊；深底上发光会溢出到相邻行 |
| 全身 glitch/故障字效 | 与“系统可靠”语义直接冲突；只允许在错误页标题用 1 次静态错位，不做循环 |
| 打字机逐字输出 | 延迟阅读，长回答时用户等待；流式文本本身就是渐进出现 |
| 全屏扫描线覆盖 | 覆盖正文降低对比度，且触发全屏合成；只在加载时最多 1 次 |
| 3D 倾斜卡片 / 透视面板 | 数据密度高的界面里会扭曲表格与文字；触屏上误触率高 |
| 鼠标跟随光斑 | 无信息价值，移动端不可用，且频繁触发重绘 |
| 每个卡片都发光/加角标 | 焦点稀释，等于没有焦点 |
| 假 HUD 数字（99.9% 安全、实时 FPS、假坐标） | 违反项目 NO-FAKE 原则；控制台所有数字必须可追溯后端 |
| 给审批/审计页加动效 | 信任页面需要“静止、可核对”，动效削弱可信度 |
| 引入动画库（framer-motion / GSAP） | 增加包体与运行时开销；本规范所需效果纯 CSS 即可 |
| 自托管未核实许可证的字体 | 法律风险；系统字体已足够 |
| 深色下用纯 `#000` 背景 + 纯白 100% 文字 | 对比过强导致眩光与残影；保持 `#050912 / #E8EEFC` |

---

## 11. 性能预算

| 指标 | 预算 | 验证方式 |
|---|---|---|
| 新增 JS 依赖 | 0 | `package.json` diff |
| 新增网络请求 | 0（不引字体、不引图片、不引图标库） | DevTools Network |
| CSS 增量 | ≤ 3 KB gzip | 构建产物体积对比 |
| 首屏 LCP | 桌面 ≤ 2.0s、手机（本地）≤ 2.5s | Lighthouse / DevTools |
| CLS | < 0.02 | DevTools Performance |
| 长任务 | 无 > 200ms | Performance 面板 |
| 同屏无限动画 | ≤ 3 个，且全部 `transform/opacity` | 代码审查 |
| `backdrop-filter` 层 | ≤ 2（顶栏、底栏） | 代码审查 |
| 动画元素面积 | 单个 ≤ 视口 15%，禁止全屏 | 代码审查 |
| 移动端特效 | 网格/HUD/流动线/扫描线全部关闭 | 390×844 实测 |
| `prefers-reduced-motion` | 所有循环动画停止，仅保留 ≤80ms 状态过渡 | 手动切换系统设置 |

实现红线：任何“每帧修改 `box-shadow`、`filter`、`background` 大面积”的动画直接拒收；优先 `transform` 与 `opacity`。

---

## 12. 验收清单

- [ ] 深色与浅色两套主题，正文/次级/元信息对比度全部达标（1.4 表可复核）。
- [ ] Tab 键从侧栏到内容到底栏，每一步都有 ≥2px 可见焦点环；无 `outline: none` 遗留（6 处已处理）。
- [ ] 390×844：底栏 5 项（总览/员工/管家/审批/我的），不换行、不重叠、每项 ≥44px，内容不被遮挡。
- [ ] 200% 缩放无横向滚动、无文字裁切；表格自动堆叠。
- [ ] 登录页、总览、工作台、员工、审批五页均有明确的“信息优先级”，不依赖颜色单一表达状态。
- [ ] 所有发光/流动/呼吸效果只在真实状态（执行中、待审批、流式输出）出现；空态与失败态不伪装。
- [ ] 同屏动画 ≤3 个；`prefers-reduced-motion` 下全部停止。
- [ ] 首屏无新增网络请求、无新增 JS 依赖；CSS 增量 ≤3 KB gzip。
- [ ] 品牌名称、六边形标志、金色徽记未改动；无第三方 Logo/图标/字体资产。
- [ ] `npm run build`、`npm test` 通过；三形态（desktop/web/mobile）无样式错位。

---

## 附录 A：可直接追加的令牌块（P1 实施用）

```css
/* 语义扩展：科幻层（叠加在现有 --os-* 之上，不替换） */
:root,
[data-theme='dark'] {
  --sc-bg-deep: #03060e;
  --sc-hairline: rgba(120, 190, 255, 0.14);
  --sc-cyan: #35e0ff;
  --sc-gold: #ffc93c;
  --sc-accent-ink: #06121f;
  --sc-space-1: 4px;
  --sc-space-2: 8px;
  --sc-space-3: 12px;
  --sc-space-4: 16px;
  --sc-space-5: 20px;
  --sc-space-6: 24px;
  --sc-space-7: 32px;
  --sc-space-8: 40px;
  --sc-space-9: 48px;
  --sc-space-10: 64px;
  --sc-dur-hover: 80ms;
  --sc-dur-state: 120ms;
  --sc-dur-enter: 180ms;
  --sc-dur-layout: 240ms;
  --sc-ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
  --sc-ease-in: cubic-bezier(0.4, 0, 0.2, 1);
}

[data-theme='light'] {
  --sc-bg-deep: #e9effa;
  --sc-hairline: rgba(47, 111, 228, 0.1);
  --sc-cyan: #0096b8;
  --sc-gold: #a8730a;
  --os-accent-text: #1f5bd0;
  --os-ok-ink: #0a7a5b;
  --os-warn-ink: #8a5606;
}
```

## 附录 B：令牌与现有文件映射

| 规范概念 | 现有实现 | 处理方式 |
|---|---|---|
| 深色/浅色主题 | `data-theme` + `--os-*` | 保留，仅补墨色与 `--sc-*` |
| 三形态 | `data-mode` + `os.css` 选择器 | 保留，不改断点 |
| 卡片 | `.os-card` | 增加顶部受光线与 HUD 变体，不改结构 |
| KPI | `.os-kpi` | 字号升级、数值切换动效，保留 `::after` 顶线 |
| 徽章/圆点 | `.os-badge` / `.os-dot` | `os-dot` 新增环形态变体，旧用法保留 |
| 旧对话/审批 | `.jarvis-zone` / `.approval-panel` + `App.css --neon` | 先补焦点环；P7 再统一令牌，禁止提前重写 |
| 移动底栏 | `.os-bottom-nav` + `MOBILE_NAV_KEYS` | 只改 key 列表与尺寸，不改渲染逻辑 |
| 图表 | `components/charts.tsx` | 不改绘图实现，只在外层加 HUD 与图例文字 |
</content>
