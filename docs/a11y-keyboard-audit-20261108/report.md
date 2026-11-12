# 控制台无障碍与键盘可用性审计报告

- 日期：2026-11-08
- 范围：登录、总览、对话（工作台）、员工（我的员工）、记忆、项目、知识库（知识中心）、审计（审计链）、用量（数据中心）、设置（系统设置）
- 代码基线：HEAD `dc324ca8` 的当前工作区；本次用 `npm run build` 重新构建 `apps/console/console/dist` 后审计（构建时间 2026-11-07 12:27）
- 环境：Windows / Microsoft Edge（Playwright 驱动），视口 1440×900 与 390×844；深色与浅色主题；系统"减少动效"通过 `prefers-reduced-motion: reduce` 模拟
- 方式：真实启动网关（隔离数据目录），全程键盘操作 + 计算样式/DOM 断言 + 截图取证；只读审计，未修改任何产品代码
- 原始测量数据：`evidence/probe*.json`（DOM 与计算样式的原始断言，供复核）

## 一、结论

**阻断（用不了）：0 项。** 登录 → 进对话 → 发一句 → 看结果 → 派活 → 退出，全链路仅用键盘可完成。

**影响（难用）：10 项；建议：9 项。** 主要问题集中在：发送后焦点丢失、流式回复不播报、缺少跳过导航、通知弹层键盘不闭环、展开/派发后焦点落回 `body`、小字号与小触控目标、浅色主题状态色对比不足、宣传的 `/` 快捷键未实现。

### 键盘核心链路实录

| 步骤 | 键盘操作 | 实测结果 |
| --- | --- | --- |
| 首次创建账号 | 账号输入框自动聚焦 → 填账号/显示名/密码/确认 → Tab 到"创建并进入系统" → Enter | 成功进入总览（`shots/81-boot-submit-focus.png`） |
| 登录 | 账号自动聚焦 → Tab 到密码 → Enter | 成功进入总览，标题"总览" |
| 进入对话 | 8×Tab 到侧栏"工作台" → Enter | 进入工作台 |
| 输入并发送 | 29×Tab 到指令输入框 → 输入 → Enter | 消息发出，约 12s 后收到真实回复 |
| 看结果 | — | 回复可见，但焦点变为 `body`，且无 `aria-live`（`shots/55-chat-result.png`） |
| 派活 | 14×Tab 到"我的员工" → Enter；30×Tab 到名字输入、6×Tab 到"创建员工" → Enter；43×Tab 到"单独指派一个任务" → Enter；Tab 到任务输入 → 输入；Tab 到"派给它" → Enter | 派发成功；提示"已派给「键盘员工」…"通过 `role=status` 播报（`shots/61-assign-opened.png`、`shots/62-assign-done.png`） |
| 退出 | 37×Tab 到"系统设置" → Enter；1×Tab 到"退出登录" → Enter | 返回登录页 |

---

## 二、阻断（用不了）

无。上述 6 个核心动作均已在纯键盘下完成，并有截图与 DOM 断言佐证。

---

## 三、影响（难用）

### I-1 发送后焦点丢失，连续对话需重走 37 次 Tab
- 页面：工作台（对话）
- 元素：`.jarvis-composer textarea`（指令输入框）
- 复现：登录 → 8×Tab 到"工作台" → Enter → 29×Tab 到输入框 → 输入 → Enter。发送后 `document.activeElement` 变为 `BODY`；再发一句需从侧栏重新 Tab（8 次到工作台 + 29 次到输入框）。
- 证据：`evidence/probe7.json` → `chat.focus = "BODY"`；截图 `shots/55-chat-result.png`

### I-2 对话回复与"思考中"状态不被屏幕阅读器播报
- 页面：工作台（对话）
- 元素：`.chat-log`、`.chat-row`、流式状态
- 复现：发送消息后检查 DOM：`aria-live` 节点 0 个，`.chat-log` 无 `role`，消息行无 `role`，无 `aria-busy`。流式追加与最终回复均不会主动播报。
- 证据：`evidence/probe7.json` → `chat.live = []`、`listRole = null`；截图 `shots/54-chat-thinking.png`、`shots/55-chat-result.png`

### I-3 无"跳到主内容"链接，顶部搜索要按 30 次 Tab
- 页面：全部内页（以总览、工作台为例）
- 元素：侧栏 `aside[aria-label="主导航"]`、顶栏搜索框
- 复现：登录后按 Tab，焦点从侧栏第一项"总览"开始逐项移动，共 29 个导航按钮；第 30 次 Tab 才到搜索框。页面无 skip link。
- 证据：`evidence/probe13.json` → `navButtons = 29`、`searchTabSteps = 30`；截图 `shots/71-search-focus.png`（第 30 次 Tab，焦点框可见）

### I-4 通知弹层键盘不闭环：焦点不进入、Esc 不关闭、不归还
- 页面：总览（顶栏）
- 元素："系统通知，N 条"按钮与其弹层
- 复现：Tab 到通知按钮 → Enter 打开。焦点仍停在触发按钮；弹层无 `role`、无 `aria-controls`，内部可聚焦元素 0 个；按 Esc 弹层仍打开，焦点不归还。全站未发现 `role="dialog"` 的模态框，通知弹层是唯一的浮层，故焦点进入/归还仅能在此验证。
- 证据：`evidence/probe4.json` → `popover.afterOpen.active = "系统通知，0 条"`、`inPopover = false`、`role = null`、`focusables = 0`；`afterEscape.stillOpen = true`；截图 `shots/10-popover.png`

### I-5 展开"单独指派一个任务"后焦点掉到 body；派发完成后再次掉到 body
- 页面：我的员工
- 元素："单独指派一个任务"按钮、任务输入框、"派给它"按钮
- 复现：Tab 到"单独指派一个任务"（从页面顶部起 43 次 Tab）→ Enter。触发按钮被替换，焦点变为 `body`；提交后焦点再次变为 `body`。
- 证据：`evidence/probe8.json` → `focusAfterOpen.tag = "body"`、`assignDone.focus = "BODY/"`；截图 `shots/61-assign-opened.png`、`shots/62-assign-done.png`

### I-6 小字号普遍存在，长文与表格阅读吃力
- 页面：全部内页，数据中心最重
- 元素：说明文字、表头、状态徽标、侧栏辅助文字
- 复现：1440×900 下统计可见文本节点（字号 <12px）：总览 64/157、数据中心 119/262、系统设置 45/151、审计链 19/90、知识中心 18/84、工作台 30/77；最小 10.5px（中文）。
- 证据：计算样式统计；截图 `shots/page-data.png`

### I-7 小屏触控目标小于 44×44
- 页面：总览（390×844）
- 元素：搜索输入 222×23、切换主题 34×34、系统通知 34×34、刷新 23×21
- 复现：390×844 下测量可见交互元素盒模型，4 个目标小于 44×44；无横向滚动（该点为好）。
- 证据：`evidence/probe8.json` → `mobileOverview.smallTargets`；截图 `shots/63-mobile-overview.png`

### I-8 浅色主题状态色文本对比不足
- 页面：登录（浅色）
- 元素："已配置"绿色文字（12.5px）、`os-login-aside-note` 橙色提示（12px）
- 实测：绿色 `rgb(15,157,118)` / 背景 `rgb(244,247,253)` = **3.21:1**；橙色 `rgb(196,124,20)` / 同背景 = **3.14:1**。均低于 AA 正文 4.5:1。
- 令牌层同源问题：浅色 `--os-ok #0f9d76` = 3.21:1、`--os-warn #c47c14` = 3.14:1（在 `#f4f7fd` 上）。深色令牌均达标（`#2ee6a8` 11.66:1、`#ffb74d` 10.88:1）。
- 证据：`evidence/probe11.json` → `loginLightHits`；截图 `shots/light-login.png`

### I-9 顶栏标注 `/` 快捷键，但按键无响应
- 页面：全部内页顶栏搜索框
- 元素：搜索框右侧 `<kbd>/</kbd>`；源码中无任何 `keydown`/`metaKey` 搜索快捷键处理
- 复现：登录后直接按 `/`，焦点仍在 `body`；搜索框未被聚焦。
- 证据：`evidence/probe12.json` → `slash.tag = "BODY"`；截图 `shots/70-search.png`

### I-10 登录页 Tab 控件语义不完整
- 页面：登录
- 元素："登录 / 创建账户" `role=tab` 按钮组
- 复现：两个 Tab 均可被 Tab 键逐个聚焦（无 roving tabindex）；按 ArrowRight 不切换；无 `aria-controls`，页面无 `role=tabpanel`。
- 证据：`evidence/probe9.json` → `loginTabBefore`（controls 全为 null、tabpanel 0）；截图 `shots/50-login.png`

---

## 四、建议

| 编号 | 建议 | 对应问题 |
| --- | --- | --- |
| S-1 | 发送后把焦点留在输入框（或提供"继续输入"焦点）；对话输入框补 `aria-label`，不再只依赖 placeholder | I-1 |
| S-2 | 消息容器加 `role="log"` + `aria-live="polite"`（流式可用节流），"思考中"容器加 `aria-busy="true"` | I-2 |
| S-3 | 增加"跳到主内容"skip link；把顶栏搜索移到 DOM 前部，或实现已标注的 `/`（及 Ctrl/Cmd+K）快捷键 | I-3、I-9 |
| S-4 | 通知弹层加 `aria-controls`/`role`，打开时移焦到首项，Esc 关闭并把焦点还给触发按钮 | I-4 |
| S-5 | 展开"单独指派"后把焦点移入新输入框；派发完成后把焦点还给"单独指派一个任务" | I-5 |
| S-6 | 12px 以下字号提升到 ≥12px（正文建议 13–14px），重点处理数据中心表头与状态说明 | I-6 |
| S-7 | 移动端图标按钮与输入框高度统一到 ≥44×44 | I-7 |
| S-8 | 浅色主题的 ok/warn 文本改用更深的 ink 令牌（目标 ≥4.5:1），仅保留浅色用于背景/描边 | I-8 |
| S-9 | 登录 Tab 组补 `aria-controls` + `tabpanel` 与左右方向键切换；或降级为普通切换按钮并加 `aria-pressed` | I-10 |

---

## 五、已达标项（正面结论）

- 焦点可见：侧栏按钮、主按钮、搜索框均有可见焦点框；侧栏实测 `outline: solid 2px rgb(77,141,255)`，对背景 `#050912` 对比 **6.23:1**；渐变主按钮另有外发光（`shots/81-boot-submit-focus.png`）。
- 可读名称：总览、工作台、我的员工、记忆、项目、知识中心、审计链、数据中心、系统设置 9 个页面中，未发现空名称的按钮/链接（0 个）。
- 地标与当前页：`aside[aria-label="主导航"]`、`header`、`main`、`nav[aria-label="底部导航"]` 齐备；当前侧栏项带 `aria-current`。
- 状态播报：创建员工、派活的成功提示使用 `role="status"` + `aria-live="polite"`，实测可播报。
- 减少动效：`prefers-reduced-motion: reduce` 下 `html[data-reduced-motion="true"]`，过渡时长降为 `1e-06s`，无运行动画。
- 移动端无横向滚动（390×844）。

## 六、对比度实测表

| 场景 | 前景 / 背景 | 比值 | AA（正文 4.5 / 大字 3） |
| --- | --- | --- | --- |
| 深色正文 | `#e8eefc` / `#0b1120` | 16.20:1 | 通过 |
| 深色次要 | `#93a3c4` / `#0b1120` | 7.42:1 | 通过 |
| 深色弱化 | `#8a9ac0` / `#0b1120` | 6.69:1 | 通过 |
| 深色按钮字 | `#06121f` / `#4d8dff` | 5.90:1 | 通过 |
| 深色按钮字（渐变另一端） | `#06121f` / `#9b6bff` | 5.33:1 | 通过 |
| 深色链接 | `#4d8dff` / `#0b1120` | 5.89:1 | 通过 |
| 浅色正文 | `#101a30` / `#ffffff` | 17.32:1 | 通过 |
| 浅色次要 | `#57648a` / `#ffffff` | 5.84:1 | 通过 |
| 浅色弱化 | `#59647e` / `#ffffff` | 5.92:1 | 通过 |
| 浅色按钮字 | `#ffffff` / `#2f6fe4` | 4.65:1 | 通过 |
| 浅色 ok 文本 | `#0f9d76` / `#f4f7fd` | **3.21:1** | 不通过 |
| 浅色 warn 文本 | `#c47c14` / `#f4f7fd` | **3.14:1** | 不通过 |
| 输入占位符（深色） | `#8a9ac0` / 面板 | 6.69:1 | 通过 |

## 七、截图索引

| 文件 | 内容 |
| --- | --- |
| `shots/81-boot-submit-focus.png` | 首次运行表单，键盘焦点在"创建并进入系统" |
| `shots/50-login.png` | 登录页"登录 / 创建账户"两个 Tab（无 tabpanel/aria-controls） |
| `shots/01-login-focus.png` | 首次运行表单（提示"还没有设置登录密码"），焦点在第 2 个字段 |
| `shots/52-overview.png` | 总览（深色） |
| `shots/71-search-focus.png` | 第 30 次 Tab 到达顶部搜索框，焦点框可见 |
| `shots/70-search.png` | 顶栏 `/` 快捷键提示（按键无响应） |
| `shots/54-chat-thinking.png` | 对话"思考中"状态（无 aria-busy/aria-live） |
| `shots/55-chat-result.png` | 真实回复已出现，焦点落到 body |
| `shots/61-assign-opened.png` | 展开"单独指派一个任务"后焦点丢失 |
| `shots/62-assign-done.png` | 派发成功（`role=status` 播报） |
| `shots/10-popover.png` | 通知弹层打开，焦点未进入 |
| `shots/59-settings.png` | 系统设置与退出登录 |
| `shots/63-mobile-overview.png` | 390×844 小屏触控目标 |
| `shots/page-data.png` | 数据中心小字号密集 |
| `shots/light-login.png` | 浅色登录页 ok/warn 低对比 |
| `shots/light-overview.png` | 浅色总览整体 |
