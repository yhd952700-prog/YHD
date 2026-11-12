# 无障碍第三轮：读屏 / 高对比度 / 纯键盘全流程

日期：2026-11-08 · 范围：`apps/console/console/src` 的样式与可访问性
约束遵守：未改业务逻辑；未动 `dist/`；未运行 `setup.exe`；未提交 commit；只做字符串级编辑，未整文件覆盖他人改动。

## 0. 方法与诚实声明

- 本机没有可被自动化驱动的 NVDA / 讲述人，**没有声称运行过它们**。
  读屏走查改用 Chromium/Edge 的真实 **Accessibility Tree**（CDP `Accessibility.getFullAXTree`），
  逐页抓 `role / name / description / invalid / live / busy`，再配合真实键盘事件走完整流程。
  这正是读屏软件读取的同一棵树：树里没有名字的按钮，读屏就会报「按钮 未命名」。
- 高对比度使用 Edge 的真实 `forced-colors: active` 上下文（`browser.newContext({ forcedColors: 'active' })`），
  不是用媒体查询模拟；并额外检测「正文颜色 == 有效背景色」的文字消失。
- 键盘流程全部使用 `page.keyboard.press('Tab' / 'Enter')`，没有任何鼠标点击。
- 后端用路由 mock（只读/可写内存态），业务组件本身未改。

## 1. 修复前实测（基线）

| 检查项 | 实测结果 |
| --- | --- |
| 登录页 / 5 个主页面未命名控件（role 有 name 为空或等于 role） | **0**，无「按钮 未命名」 |
| 登录失败 | `role="alert"`（live=assertive）进 AX 树，焦点回到 `#os-secret` |
| 登录成功进入壳层 | 焦点落到 **body**（`activeElement = body`） |
| 建员工成功 | 焦点落到 **body**（提交按钮 `disabled` 期间被浏览器丢弃） |
| 建员工必填错误 | 原生 `required` 气泡拦截，自定义 `role="alert"` 根本不触发；输入框无 `aria-invalid` |
| 切换页面（键盘 Enter 激活侧栏） | 无任何播报，读屏不知道内容已换 |
| 跳转链接 | 第一次 Tab = 「跳到主内容」，Enter 后焦点 = `main#os-main`（已通过） |
| 高对比度：主按钮 | 禁用态 `opacity: .55`，只剩浅粉描边+浅粉字，像不可用/不可点；正常态无填充 |
| 高对比度：当前页 / 当前页签 | 背景 `rgba(255,255,255,.16)`，无边框、无高亮 → **当前项不可辨** |
| 高对比度：状态点 | inline 彩色底被强制清除，圆点消失 |
| 高对比度：文字消失检测 | 0 处（登录 + 壳层） |
| 键盘全流程（1280×720） | 冷启动→登录→建员工→派活→回执可完成；`body` 落点 **1 次**（登录后） |
| 640×360 / 200% | 可完成到派活回执；底部导航没有「目标与任务」入口 |

> 另外发现（非本轮主题，但读屏会读到乱码级排版）：首次注册页「为了不在匿名接口里泄露根账号名…」
> 与恢复码页两处提示，因为 `.os-note` 是 flex，裸文本与 `<strong>` 各自成为 flex item，
> 被排成两列，文字读起来是断开的。

## 2. 本轮修复

| # | 文件 | 改动 | 原因 |
| --- | --- | --- | --- |
| 1 | `App.tsx` | 登录成功（`onSuccess`）置 `focusMainAfterLogin`，主内容出现后 `main#os-main.focus()`（带重试） | 修复「登录后焦点掉 body」 |
| 2 | `Shell.tsx` + `App.tsx` | 新增常驻 `role="status" aria-live="polite"` 播报区；页面名由 App 的 `meta.title` 传入，切页播报「已进入「XX」」；不移动焦点 | 页内切换对读屏是静默的 |
| 3 | `MyEmployees.tsx` | 创建成功把焦点交给结果提示（`tabIndex={-1}` + ref）；必填失败聚焦 `#emp-name` 并 `aria-invalid` / `aria-describedby` 指向 `role="alert"`；表单加 `noValidate` | 修复「建员工成功/失败焦点丢失、错误不关联字段」 |
| 4 | `Login.tsx` | 3 处 `.os-note` 的裸文本包 `<span>` | 修复 flex 把提示排成两列的乱码级阅读 |
| 5 | `os.css` | 新增 `@media (forced-colors: active)` 适配块 | 高对比度下按钮/当前项/状态点/边框可见 |
| 6 | `styles.a11y.test.ts` | 新增「读屏契约」「forced-colors 契约」共 10 条断言（总 28 条） | 把「必须朗读出什么」固化 |

关键取舍：
- 切页采用「播报 + 焦点留在导航项」，而不是把焦点搬到 `main`。两种都是 APG 认可的模式；
  不搬焦点可避免鼠标用户看到焦点环跳动，同时保证读屏收到页面名。
- 高对比度**没有**用 `Highlight` 实底 + `HighlightText` 白字：Edge 实测会把中文渲染成**白块**
  （截图 `hc-variants.png` 可复现）。改用「Canvas 底 + 2px Highlight 描边 + 加粗」，
  文字与状态都可见。
- 禁用按钮在高对比度下取消 55% 透明度，改成 `1px dashed GrayText`：不能把「不可用」做成「看不见」。

## 3. 修复后实测

### 3.1 读屏树（AX）

| 页面 | 未命名控件 | 播报区 | 备注 |
| --- | --- | --- | --- |
| 登录 | 0 | alert(assertive) | 账号/密码/显示密码/登录/忘记密码/记住我/2 个页签，全部有名字 |
| 首次注册 | 0 | 2 个 status | 6 个控件：账号、显示名（可选）、密码、确认密码、模型 Key 勾选框、创建并进入系统 |
| 总览 | 0 | 9 | 标题层级：总览 → 能力层分布 / 最近运行 / … |
| 我的员工 | 0 | 2 | 标题：我的员工 → 创建员工 → 员工名册 |
| 鎏灏管家 | 0 | 3 | 标题：鎏灏管家 → 让管家安排 / 只派给一个人 / … |
| 目标与任务 | 0 | 2 | |
| 系统设置 | 0 | 5 | |

建员工必填错误（AX 原样）：

```json
{ "role": "textbox", "name": "名字（必填）", "desc": "请先填写员工名字",
  "invalid": "true", "required": true }
```

切页播报：`已进入「鎏灏管家」`，焦点仍在原导航项（不抢焦点）。

### 3.2 纯键盘全流程（Tab 数 / 焦点落点）

| 视口 | body 落点 | 登录后焦点 | 建员工后焦点 | 展开指派 | 提交指派后 | Tab 数（导航/名字/创建/展开/提交） |
| --- | --- | --- | --- | --- | --- | --- |
| 1280×720 | **0** | `main#os-main` | 结果提示 `div[role=status]` | 输入框 | 触发按钮 | 11 / 32 / 4 / 4 / 1 |
| 640×360（矮视口） | **0** | `main#os-main` | 结果提示 | 输入框 | 触发按钮 | 7 / 10 / 4 / 4 / 1 |
| 960×540（1920×1080 的 200%） | **0** | `main#os-main` | 结果提示 | 输入框 | 触发按钮 | 11 / 32 / 4 / 4 / 1 |
| 1280×720 + 减少动画 | **0** | `main#os-main` | 结果提示 | 输入框 | 触发按钮 | 11 / 32 / 4 / 4 / 1 |

- 全程无焦点陷阱；唯一经过 `body` 的位置是「从文档最后一个可聚焦元素继续 Tab」时浏览器把焦点交回
  浏览器 UI（标准行为），不在业务流程中间。
- 窄视口 / 200% 下仍可完成「登录 → 建员工 → 派活 → 看到回执」。
- 已知 IA 缺口（未改，属导航/业务范围）：手机端底部导航只有 5 项，没有「目标与任务」入口。
  窄屏下派活回执即是可见结果；建议产品侧补一个入口，或让回执带可点击的「查看目标」。

### 3.3 Windows 高对比度（forced-colors: active）

| 元素 | 修复前 | 修复后 |
| --- | --- | --- |
| 登录页签（当前） | 无高亮，与未选中无差别 | `border: 2px solid rgb(55,0,110)`（Highlight）+ 加粗，文字正常黑字 |
| 侧栏当前页 | 半透明白底，等于没有 | 2px Highlight 描边 + 加粗 |
| 主按钮 | 仅浅粉描边/浅粉字，禁用态 55% 透明 | `1px solid ButtonText` + `ButtonFace` 底；禁用 `1px dashed GrayText`，`opacity: 1` |
| 输入框 | 内层无边框（靠外层柔光） | 外层 `1px solid CanvasText`；内部输入框由壳提供边界 |
| 卡片 / 提示 / 用户块 | 靠阴影，边界消失 | `1px solid CanvasText` |
| 状态点 | 彩色 inline 底被清除 → 不可见 | 空心环：`background: Canvas !important` + `1px solid CanvasText` |
| 徽章 | 彩色实底，强制后与文字同色风险 | `1px solid CanvasText` + Canvas 底 + CanvasText 字 |
| 焦点环 | 作者色在强制色下不可控 | `outline: 3px solid Highlight` |
| 文字消失检测 | 0 处 | **0 处**（登录 + 壳层） |

证据图（修复后）：`hc-login.png`、`hc-my-employees.png`、`hc-notifications.png`、`hc-tabs.png`（2×）。

### 3.4 自动断言

`npx vitest run src/styles.a11y.test.ts` → **28 passed**（新增 10 条）：

- 切页播报区存在、文案为 `已进入「${pageTitle}」`、App 传入 `pageTitle={meta.title}`；
- 登录后 `main.focus()`；
- 建员工：结果提示可聚焦、`aria-invalid` / `aria-describedby` / `role=alert` 三件套；
- 登录失败 alert + 焦点回密码框；
- 图标按钮必须有 `aria-label`；
- forced-colors 块存在且位于 reduced-motion 之后；
- 按钮 `ButtonText`、容器 `CanvasText`；
- 当前项 2px Highlight + 加粗，且**禁止** `color: HighlightText`（白块回归防线）；
- 状态点空心环、禁用按钮 `opacity: 1` + 虚线、焦点 3px Highlight。

## 4. 「必须朗读出什么」检查清单

登录 / 首次注册
- [x] 账号输入框读「账号，编辑框」；密码读「密码，编辑框」；不得读成「编辑框 未命名」
- [x] 「显示密码」读「显示密码，按钮」
- [x] 「记住我（在已安装的桌面端 / 手机端保持登录），复选框」
- [x] 登录失败必须读出一句完整错误（role=alert），随后焦点回到密码框
- [x] 首次注册：6 个控件全部有名字；空提交由浏览器读出原生错误（「请填写此字段」）并把焦点放到账号
- [x] 提示段落必须整段连续朗读（不能拆成两列/两段交叉）

壳层与切页
- [x] 第一次 Tab 读「跳到主内容，链接」，Enter 后焦点在 `main`
- [x] 键盘激活侧栏项后，读屏播报「已进入「页面名」」
- [x] 当前页导航项读「当前页」状态（`aria-current=page`）
- [x] 通知按钮读「通知，N 条，按钮」；退出读「退出登录，按钮」
- [x] 不得出现「按钮 未命名」（7 个界面实测 0）

我的员工 / 派活
- [x] 「名字（必填）」为空时：输入框 `aria-invalid=true`，描述里带「请先填写员工名字」，焦点回到该输入框
- [x] 创建成功：焦点落在结果提示（role=status），不落 body
- [x] 「单独指派一个任务」展开后焦点进入任务输入框
- [x] 提交后焦点回到「单独指派」按钮；回执由 role=status 播报
- [x] 「派给它」空内容时按钮 disabled（不会出现点了没反应的静默失败）

高对比度
- [x] 文字不消失（颜色 == 背景色的节点 0 个）
- [x] 按钮有系统色边框，当前页/当前页签有 2px 高亮描边
- [x] 状态点以空心环呈现，徽章有边框
- [x] 焦点环 3px 系统高亮色

## 5. 复现方式

```powershell
# 1) 起前端
cd apps/console/console
npx vite --port 18300 --strictPort

# 2) 读屏树 + 键盘 + 高对比度
node .probe_tmp/a11y3/probe.mjs        # 输出 .probe_tmp/a11y3/probe.json 与 shots/
node .probe_tmp/a11y3/form-error.mjs   # 建员工必填错误的 AX 断言
node .probe_tmp/a11y3/bootstrap.mjs    # 首次注册页 AX / Tab 顺序

# 3) 样式契约
npx vitest run src/styles.a11y.test.ts
```

> 探针脚本与临时截图在交付后已删除，证据图保留在本目录。
