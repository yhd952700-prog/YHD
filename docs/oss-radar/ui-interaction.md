# 开源技术雷达 · 第 5 路：界面、交互与多模态

> 调研对象：D:\LiuHao-AI-OS（鎏灏 LIUHAO AI OS）
> 调研日期：2026-11-06
> 本路范围：前端框架与组件、状态管理、桌面壳（托盘/自启/原生通知）、数据可视化、无障碍与适老化、国际化、多设备同步、语音/图像/视频/多模态、可解释与可撤销的人机协作界面。
> 证据边界：仓库元数据取自 GitHub API（`pushed_at` 为最近推送时间，含任意分支）；许可证以 LICENSE 文件正文为准，无 LICENSE 文件时单独标注；安全公告用 OSV 对“当前最新版本”逐包查询；未运行任何候选项目、未下载模型、未启动服务、未改其他文件。
> 代码现状以实际被网关消费的 `apps/console/console` 为准；`frontend/` 是未被消费的旧组件库。

---

## 0. 结论摘要

1. **“管家主面板”不需要新框架，需要一套事件模型 + 一个时间线组件。** 现有聊天是“一问一答 + 工具折叠”，缺的是把“管家拆解 → 分派给谁 → 每步结果 → 汇总”表达成可追溯的一条线。最值得借鉴的是 assistant-ui 的 message-part 状态模型与 OpenHands 的 `conversation-events` 分组/折叠结构；协议层参考 AG-UI 的最小事件集，不引入其运行时。
2. **取数层应从“每页各自轮询”升级为共享缓存 + 失效。** 现在 19 个入口各写各的 `useApi`，审批、执行、目标状态之间没有联动；TanStack Query 是成本最低、收益最直接的一项。
3. **桌面壳用 pywebview 而不是 Electron/Tauri。** pywebview 在 Windows 走 EdgeChromium/WebView2，复用现有 Python 与单端口网关；托盘用 pywin32（BSD 式许可），通知用 pywinrt（MIT），自启用 HKCU Run。Tauri 仅在“长期要做独立安装包”时再考虑。
4. **中文语音输入可行，但应按“按住说话”而不是“唤醒词”起步。** sherpa-onnx（Apache-2.0）提供离线中文 Paraformer、标点、ITN、关键词唤醒与 TTS 示例，不依赖云；代价是百 MB 级模型、内存与首次加载时间。中文唤醒词没有开箱即用的可靠方案。
5. **无障碍与国际化在本规模产品里是“最小集”问题，不是框架问题。** 无障碍最小集 = 焦点可见 + 键盘可达 + 语义/live region + 200% 缩放 + 对比度 + 触摸目标 + CI 门禁（axe-core）；国际化最小集 = 文案集中 + 全部走 `Intl` + `lang` 正确。现在**不建议**引入 i18next。

---

## 1. 该域能力现状表

状态说明：**已有** = 有真实实现并被消费；**部分** = 有实现但覆盖不全或有明确缺口；**缺失** = 没有实现。

| # | 能力 | 状态 | 现状与证据（文件路径） | 缺口 |
|---|---|---|---|---|
| 1 | 前端框架 | 已有 | `apps/console/console/package.json`：React 19.2 + Vite 8.2 + TypeScript 6，依赖只有 react/react-dom/ws | 无路由库、无请求库、无 UI 库，全部手写 |
| 2 | 第二套前端 | 部分 | `frontend/package.json`：`@liuhao-ai-os/frontend`，React 18 组件库 | console 未引用（React 版本也不同），属死资产 |
| 3 | 组件体系 | 部分 | `apps/console/console/src/components/ui.tsx`（Card/Dot/Empty/Guard 等） | 无 Dialog/Dropdown/Tabs/Tooltip 等交互原语；无焦点管理 |
| 4 | 样式与主题 | 已有 | `apps/console/console/src/os.css`（CSS 变量、深浅主题、断点） | 仅 1 处 `prefers-reduced-motion`；2 处 `outline: none`（`.os-search input`、`.os-input input`） |
| 5 | 三端形态 | 已有 | `apps/console/console/src/lib/mode.ts`：`<768` mobile / `<1280` web / `>=1280` desktop，localStorage 覆盖 | 形态只是布局，未做真机手势/安全区专项 |
| 6 | 导航 | 部分 | `apps/console/console/src/lib/nav.ts`：主导航 10 + 治理 8 + 设置 1；`MOBILE_NAV_KEYS = ['overview','employees','approval','settings']` | **手机底部导航没有“对话/管家”入口**；19 个一级入口信息架构过载 |
| 7 | 取数层 | 部分 | `apps/console/console/src/lib/api.ts`：`apiFetch`（15s 超时、401 清会话）+ `useApi` 轮询 | 每页独立轮询，无共享缓存、无失效、无重试策略、无请求去重 |
| 8 | 图表 | 已有 | `apps/console/console/src/components/charts.tsx`：手写 SVG 的 LineChart/DonutChart/NetworkGraph/BrainMap | 大数据量无虚拟化/降采样；无交互缩放；无图表库 |
| 9 | 对话 | 部分 | `apps/console/console/src/components/ChatPanel.tsx` + `src/lib/chatClient.ts`（SSE 流式、localStorage 会话列表）；仅在 `src/pages/Workbench.tsx` 挂载 | 消息模型只有 `role/content/tools`；无多员工、无阶段、无时间线、无中断/恢复 |
| 10 | 工具调用可视化 | 部分 | `ChatPanel.tsx` 内 `ToolCall` + `<details>` 折叠 | 无法区分“思考/工具/结果/证据”；没有审计 ID 回链 |
| 11 | 目标与任务 | 部分 | `apps/console/console/src/pages/Goals.tsx`：表格 + 轮询 | 无时间线、无实时推送、无“谁在做/做到哪步” |
| 12 | 事件流 | 部分 | `apps/console/console/src/pages/Events.tsx`：`GET /v1/events?limit=50`，15s 轮询，扁平表格 | 事件未按会话/目标分组；不是“可读的一条线” |
| 13 | 审批与可解释性 | 部分 | `apps/console/console/src/components/ApprovalCenter.tsx`：签发/撤销授权、通配二次确认、审计化 | 不能解释“为什么这一步被拦”；没有“撤销已执行动作”的能力 |
| 14 | PWA | 部分 | `apps/console/console/public/manifest.webmanifest`、`public/sw.js`、`index.html` | 无推送、无应用角标、无后台同步、无离线草稿 |
| 15 | 桌面壳 | 缺失 | 仅 `start-liuhao.bat` + `scripts/start_liuhao.py`（启动网关并开浏览器；`--https` 走本地 TLS 代理，打印局域网 URL） | 无窗口、无托盘、无开机自启、无原生通知 |
| 16 | 多设备同步 | 部分 | 同一服务单端口（`src/gateway/console_static.py` 由网关托管 console dist）；`start_liuhao.py --https` 提供局域网 HTTPS | 手机/电脑同一账号无冲突问题（服务端唯一真相），但无离线队列、无跨设备会话续接 |
| 17 | 语音（ASR/TTS/唤醒） | 缺失 | 仓库内无 ASR/TTS/唤醒词代码；输入仅文本 | 中文语音输入、朗读、唤醒词全缺 |
| 18 | 图像/视频 | 缺失 | 无上传/预览/多模态消息 | 不能发图、不能看图、不能处理视频 |
| 19 | 国际化 | 缺失 | 文案硬编码中文；`index.html` `lang="zh-CN"`；日期用 `toLocaleString()`（`src/pages/Events.tsx`），数字格式在 `src/lib/format.ts` | 无字符串资源、无 locale 切换、无复数/性别规则 |
| 20 | 无障碍 | 部分 | `Shell.tsx`/`ui.tsx` 有部分 aria/role；`os.css` 有 reduced-motion | 无 `:focus-visible` 全局样式、无跳转链接、无 axe 门禁、字号 10.5px 起、无 200% 缩放验收 |
| 21 | 前端测试 | 部分 | `package.json`：`vitest run` + @testing-library/react + jsdom | 无浏览器 E2E（无 Playwright），无视觉/无障碍回归 |
| 22 | 设计意图文档 | 已有 | `docs/ui-reference-terse.md` 明确提出“管家主面板必须先承担聚合职责”“补一个统一时间线” | 意图已写，界面未实现 |

---

## 2. 候选清单（按值得引入程度排序）

排序依据：解决的是不是当前真实缺口 → 集成成本 → 许可证 → 维护与安全 → 是否能独立封装、不破坏现有接口。

| 排序 | 候选 | 优先级 | 星数 | 最近推送 | 语言 | 许可证（LICENSE 正文） |
|---|---|---|---|---|---|---|
| 1 | TanStack Query | P0 立即 | 50,526 | 2026-11-05 | TS | MIT |
| 2 | assistant-ui | P0 立即 | 12,969 | 2026-11-05 | TS | MIT |
| 3 | axe-core | P0 立即 | 7,764 | 2026-11-05 | HTML/JS | MPL-2.0（文件级 copyleft） |
| 4 | OpenHands（仅借鉴） | P0 参考 | 92,731 | 2026-11-05 | TS | MIT |
| 5 | AG-UI（仅协议） | P0 参考 | 17,035 | 2026-11-05 | TS | MIT |
| 6 | pywebview | P1 短期 | 6,153 | 2026-11-05 | Python | BSD-3-Clause |
| 7 | pywin32（托盘/自启） | P1 短期 | 5,633 | 2026-11-03 | C++/Python | BSD 式（`win32/License.txt`，非标准 SPDX） |
| 8 | pywinrt（原生通知） | P1 短期 | 203 | 2026-11-02 | Python | MIT |
| 9 | sherpa-onnx | P1 短期 | 15,759 | 2026-11-05 | C++/Python | Apache-2.0（模型权重另核） |
| 10 | cmdk | P1 短期 | 13,064 | 2025-10-29 | TS | MIT（根目录无 LICENSE 文件） |
| 11 | sonner | P1 短期 | 13,139 | 2026-08-10 | TS | MIT（根目录无 LICENSE 文件） |
| 12 | Radix Primitives | P1 短期 | 19,471 | 2026-10-14 | TS | MIT |
| 13 | faster-whisper | P2 中期 | 26,506 | 2026-11-02 | Python | MIT |
| 14 | FunASR | P2 中期 | 20,949 | 2026-11-05 | Python | 代码 MIT；模型 `MODEL_LICENSE` 自定义 |
| 15 | uPlot | P2 中期 | 10,617 | 2026-10-30 | JS | MIT |
| 16 | i18next | P2 中期 | 8,663 | 2026-11-05 | JS | MIT |
| 17 | Dexie | P2 中期 | 14,675 | 2026-10-30 | TS | Apache-2.0 |
| 18 | vis-timeline | P3 观察 | 2,576 | 2026-11-02 | JS | 双许可：MIT + Apache-2.0 |
| 19 | RealtimeSTT | P3 观察 | 10,231 | 2026-10-30 | Python | MIT |
| 20 | GPT-SoVITS | P3 观察 | 64,937 | 2026-10-31 | Python | MIT |
| 21 | openWakeWord | P3 观察 | 2,917 | 2025-12-30 | Python | Apache-2.0 |
| 22 | Tauri 2 | P3 观察 | 112,527 | 2026-11-05 | Rust | 双许可：MIT + Apache-2.0 |

安全公告：对上述库的**当前最新版本**逐个查询 OSV（2026-11-06），结果为 0 条受影响公告；历史旧版本存在公告的有 i18next、dexie、uplot、echarts、vis-timeline、pywin32（不影响最新版）。未对传递依赖做全树审计。

### 2.1 候选卡（字段 + 九问）

#### C1 TanStack Query — P0
- 仓库：`https://github.com/TanStack/query`｜50,526 star｜最近推送 2026-11-05｜TypeScript
- 许可证：MIT（`LICENSE`）｜维护：活跃，changeset 发布流程｜测试：未获取（本轮只读顶层）｜安全：OSV 最新 `@tanstack/react-query@5.106.1` 0 条
- 核心源码目录：`packages/query-core`（QueryCache/QueryObserver/Mutation）、`packages/react-query`、`packages/query-tests`（未展开）
1) 鎏灏已有同类：`apps/console/console/src/lib/api.ts` 的 `apiFetch` + `useApi`。
2) 不足：逐页轮询；审批/执行后其它页不失效；SSE 与列表状态无合并；并发重复请求。
3) 候选解决：共享缓存、请求去重、`staleTime`、`invalidateQueries`、失败重试，一处更新全站一致。
4) 值得借鉴：`query-core` 的 cache/observer 分层，正好可套在现有 `useApi` 外层。
5) 可独立封装：是，新增 `src/lib/query.ts`，把 `useApi` 改成薄适配，页面逐个迁移。
6) 破坏接口：否。后端、数据结构、现有函数签名都可保持不变。
7) 许可：MIT，可商用可修改，保留版权声明。
8) 风险/成本：增加约 10KB 级 gzip（以构建产物实测为准）；概念迁移成本集中在 19 个页面。
9) 测试：现有 vitest 页面测试保留；新增“审批后 Goals/Overview 自动失效”用例；SSE 写入缓存的红ucer 单测；真浏览器 E2E 验证切换页面不重复请求。

#### C2 assistant-ui — P0
- 仓库：`https://github.com/assistant-ui/assistant-ui`｜12,969 star｜最近推送 2026-11-05｜TypeScript
- 许可证：MIT（`LICENSE`）｜维护：活跃但仍是 0.x（`@assistant-ui/react@0.15.32`）｜测试：`tests/`、`evals/`（已核验目录）｜安全：OSV 0 条
- 核心源码：`packages/react/src/primitives/`（`message`、`messagePart`、`chainOfThought`、`composer`、`actionBar`、`reasoning`、`toolFallback`）、`packages/react/src/assistant-transport.ts`、`packages/store`、`packages/react-ag-ui`、`packages/react-ui`
- 兼容性：`peerDependencies` 明确 `react: ^18 || ^19`，与 console 的 React 19 兼容
1) 已有：`ChatPanel.tsx` 的 `Message{role,content,tools}` + `<details>`。
2) 不足：没有“消息部件”（文本/思考/工具/附件）；没有步骤折叠与操作栏；多员工结果无法归并。
3) 解决：把一条消息拆成可渲染部件，给时间线提供稳定的数据结构。
4) 值得借鉴：`chainOfThought`（拆解过程）、`messagePart`（工具/附件）、`actionBar`（重试/复制/撤销入口）、`assistant-transport`（流协议）。
5) 独立封装：可以只装 primitives，不引 runtime/云能力；也可以只抄状态模型自建。
6) 破坏接口：若只用 primitives，不改 SSE 协议；若替换 runtime，需要重写 `chatClient.ts`，风险中等。
7) 许可：MIT。
8) 风险：0.x API 变动频繁，必须锁版本；`react-ui` 可能带 Tailwind 风格，需只取 headless 层。
9) 测试：mock transport 的组件测试；SSE 事件 → message parts 映射单测；时间线分组快照；React 19 下构建体积对比。

#### C3 axe-core — P0
- 仓库：`https://github.com/dequelabs/axe-core`｜7,764 star｜最近推送 2026-11-05｜HTML/JS
- 许可证：MPL-2.0（`LICENSE`）｜维护：活跃（Deque 官方）｜测试：仓库有完整规则测试（未取覆盖率）｜安全：OSV 最新 `axe-core@4.14.0` 0 条
- 核心源码：`lib/rules/`、`lib/checks/`、`lib/commons/`、`lib/core/`
1) 已有：无任何自动化无障碍检查；`os.css` 有 2 处 `outline: none`。
2) 不足：焦点不可见、字号过小、手机触摸目标未验收、颜色单独表意。
3) 解决：规则引擎，CI 中拦截 serious/critical。
4) 值得借鉴：`lib/rules` 的规则命名与影响等级，可直接映射成验收清单。
5) 独立封装：是，devDependency；`vitest` + jsdom 可跑静态规则，布局类规则需要真浏览器。
6) 破坏接口：否。
7) 许可：MPL-2.0 是**文件级** copyleft；作为未修改的依赖使用不传染；若修改其源码文件，需公开这些文件的修改。判定：允许引入。
8) 风险：jsdom 下 `color-contrast`、`target-size` 等依赖布局的规则会失效，需 Playwright 真浏览器补测；误报需基线文件。
9) 测试：新增 `npm run a11y`（jsdom 冒烟）+ Playwright 真浏览器全页扫描；门槛：serious/critical = 0；每次 UI 改动跑。

#### C4 OpenHands（只借鉴，不作为依赖）— P0 参考
- 仓库：`https://github.com/OpenHands/OpenHands`｜92,731 star｜最近推送 2026-11-05｜TypeScript
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：`__tests__/`、`vitest.setup.ts`、多份 `playwright.*.config.ts`（已核验）｜安全：未查询（不作为依赖）
- 核心源码：`src/components/conversation-events/chat/`（`event-message.tsx`、`group-events.ts`、`event-thought-helpers.ts`、`step-disclosure-context.tsx`、`task-tracking/`）、`src/query-client-config.ts`、`src/i18n/`、`src/components/conversation/`
1) 已有：`Events.tsx` 的扁平事件表。
2) 不足：事件没有“会话/任务”归属；无法回答“这一步是谁做的、为什么、结果是什么”。
3) 解决：把事件按“步骤”分组，思考/动作/观察/结果分层展示，可折叠、可回放。
4) 值得借鉴：`group-events.ts` 的分组规则、`step-disclosure-context` 的展开状态、`task-tracking` 的进度表达。
5) 独立封装：只借鉴结构，自建 `src/components/timeline/`（不复制其代码与视觉资源）。
6) 破坏接口：不改现有接口；需要后端提供带 `goal_id/employee_id/step` 的事件，或在 `src/lib/` 做前端聚合。
7) 许可：MIT；即便借鉴结构，也建议“重写实现、不复制文件”，避免版权与风格绑定。
8) 风险：它是 Electron 全栈应用，直接依赖极重；只取模式，不取依赖。
9) 测试：用真实事件 fixture 渲染时间线；分组、折叠、空态、失败态、超长事件快照。

#### C5 AG-UI 协议 — P0 参考
- 仓库：`https://github.com/ag-ui-protocol/ag-ui`｜17,035 star｜最近推送 2026-11-05｜TypeScript
- 许可证：MIT（`LICENSE`）｜维护：活跃但协议仍在演进（`@ag-ui/core@1.0.4`）｜测试：`apps/`、`integrations/`（未逐一核验）｜安全：OSV 0 条
- 核心源码：`docs/`（事件规范）、`integrations/`（`langgraph`、`pydantic-ai`、`claude-agent-sdk`、`a2a` 等适配）
1) 已有：`chatClient.ts` 自定义 SSE 事件；其它页面靠轮询。
2) 不足：同一件事（一次执行）在不同页面用不同数据形状表达，时间线无法统一消费。
3) 解决：统一最小事件集（RUN_STARTED / TEXT_MESSAGE_* / TOOL_CALL_* / STATE_SNAPSHOT|DELTA / RUN_FINISHED / RUN_ERROR），一个 reducer 驱动对话、时间线、审批、目标。
4) 值得借鉴：事件命名与状态快照/增量语义；`integrations` 里 LangGraph/PydanticAI 的映射思路。
5) 独立封装：是。后端用 Python 自实现 SSE 事件（不引 JS runtime），前端自建 reducer。
6) 破坏接口：新增字段即可；保留旧事件 type 作为兼容，按版本号渐进。
7) 许可：MIT。
8) 风险：过度设计（本项目没有跨厂商 agent 需求）；只取 6-8 个事件类型，不追全协议。
9) 测试：事件序列 → reducer 状态快照；录制的 SSE 流回放；断线重连与 `Last-Event-ID` 续传。

#### C6 pywebview — P1
- 仓库：`https://github.com/r0x0r/pywebview`｜6,153 star｜最近推送 2026-11-05｜Python
- 许可证：BSD-3-Clause（`LICENSE`）｜维护：活跃｜测试：`tests/`（含 `run.ps1`、DOM/JS/下载等用例，已核验）｜安全：OSV 最新 `pywebview@6.2.1` 0 条
- 核心源码：`webview/platforms/`（`edgechromium.py`、`webview2core.py`、`winforms.py`、`winui3.py`）、`webview/window.py`、`webview/menu.py`、`webview/dom/`、`webview/event.py`
- 兼容性：`requires-python >= 3.10`，与项目 `>= 3.11` 兼容
1) 已有：`start-liuhao.bat` + `scripts/start_liuhao.py`，启动后开浏览器。
2) 不足：没有“应用”形态；不能常驻；没有托盘与原生通知；关掉浏览器即“消失”。
3) 解决：用系统 WebView2 承载同一 URL，Python 进程内管理网关生命周期。
4) 值得借鉴：`platforms/edgechromium.py` 的 WebView2 初始化与用户数据目录；`window.py` 的尺寸/置顶/最小化；`menu.py` 的上下文菜单。
5) 独立封装：是，新增 `scripts/desktop_shell.py`，`start-liuhao.bat` 增加 `--desktop` 分支，不动现有启动路径。
6) 破坏接口：否。窗口只是容器，仍走同源单端口，令牌/Cookie 行为不变。
7) 许可：BSD-3-Clause，可商用可修改，保留版权与免责声明。
8) 风险：依赖 WebView2 Runtime（Win10 22H2/Win11 通常预装，旧机器需 bootstrapper）；pywebview 6.x 仍是 6.x 演进；托盘要另配 pywin32（LGPL 的 pystray 不用）。
9) 测试：Windows 冒烟（`--hidden` 启动 → 轮询 `/v1/ready` → 断言窗口进程存活 → 正常退出）；单实例互斥；WebView2 缺失时的错误提示；托盘菜单手工清单。

#### C7 pywin32 — P1
- 仓库：`https://github.com/mhammond/pywin32`｜5,633 star｜最近推送 2026-11-03｜C++/Python
- 许可证：BSD 式（`win32/License.txt`，Mark Hammond 版权；GitHub 未识别 SPDX，需在 NOTICE 中保留原文）｜维护：活跃｜测试：未获取｜安全：OSV 最新 `pywin32@312` 0 条
- 核心源码：`win32/`（`win32gui`、`win32api`、`winreg` 等模块源码）、`com/`、`pythonwin/`
1) 已有：无托盘/自启。
2) 不足：用户每次都要开浏览器；无法“开机即在”。
3) 解决：`Shell_NotifyIcon` 托盘图标与菜单；`winreg` 写 HKCU\...\Run 实现开机自启；单实例互斥。
4) 值得借鉴：`win32/Lib/win32gui.py` 的 `Shell_NotifyIcon` 用法；`winreg` 的键值操作。
5) 独立封装：是，`scripts/desktop_tray.py`；可选依赖，缺 pywin32 时退化为普通窗口。
6) 破坏接口：否。
7) 许可：BSD 式（三条款变体），允许商用/修改，保留版权声明。**不是** LGPL，因此比 pystray 更适合本项目。
8) 风险：Windows-only 代码；托盘图标生命周期与线程消息循环容易写错；注册表写入需可撤销（退出时提供“取消开机自启”）。
9) 测试：注册表读写单测（HKCU，测试后清理）；托盘图标创建/菜单回调的集成测试（Windows runner）；卸载/关闭自启的回归。

#### C8 pywinrt — P1
- 仓库：`https://github.com/pywinrt/pywinrt`｜203 star｜最近推送 2026-11-02｜Python
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：未获取｜安全：PyPI 包按命名空间拆分，OSV 查询未覆盖（未获取）
- 核心源码：`pywinrt/`（代码生成器）、`winrt-runtime/`、按命名空间生成的 `winrt-Windows.UI.Notifications` 等
1) 已有：无原生通知。
2) 不足：任务完成/失败只停留在页面内。
3) 解决：Windows Toast（含操作按钮），无需引入 Electron。
4) 值得借鉴：`Windows.UI.Notifications` 的 Toast 模板与激活回调绑定方式。
5) 独立封装：是，`scripts/notify.py`，失败时降级为无通知（不假装已发送）。
6) 破坏接口：否。
7) 许可：MIT。
8) 风险：WinRT 包多、安装体积与版本匹配；Toast 在 Win10 需要开始菜单快捷方式（AUMID）才能稳定显示；需实测。
9) 测试：Windows 集成测试（发送→断言 API 返回成功），失败路径单测；无 AUMID 时的提示。

#### C9 sherpa-onnx — P1
- 仓库：`https://github.com/k2-fsa/sherpa-onnx`｜15,759 star｜最近推送 2026-11-05｜C++/Python
- 许可证：Apache-2.0（`LICENSE` 正文已核验）；**模型权重另有许可，未逐一核验**｜维护：活跃｜测试：`python-api-examples/`（已核验，含 `offline-decode-files.py`、`keyword-spotter.py`、`add-punctuation.py`、`inverse-text-normalization-*.py`、`http_server.py`）；单测覆盖未获取｜安全：OSV 最新 `sherpa-onnx@2.0.1` 0 条
- 核心源码：`sherpa-onnx/csrc/`、`sherpa-onnx/python/`、`python-api-examples/`、`sherpa-onnx/c-api/`
- 模型：官方文档确认存在中文 Paraformer（含 int8、川渝方言）与流式/离线示例；中文 TTS 模型清单未逐一核验
1) 已有：无。
2) 不足：只能打字；手机端输入成本高；无朗读。
3) 解决：离线中文 ASR（按住说话）、标点与 ITN、可选关键词唤醒、可选 TTS。
4) 值得借鉴：`offline-decode-files.py` 的解码循环、`keyword-spotter.py` 的 KWS、`add-punctuation.py`、`inverse-text-normalization-*.py`、`http_server.py` 的服务化形态。
5) 独立封装：是，新增 `src/voice/asr_service.py` + `/v1/voice/transcribe`（可选依赖，未装则 503「未启用」，不降级假成功）。
6) 破坏接口：否；新增路由与可选依赖。
7) 许可：Apache-2.0（代码）；模型许可证必须逐个核对后再分发。
8) 风险：模型体积/内存/首包延迟；中文口音与噪声准确率需实测；浏览器麦克风需 HTTPS（项目已有 `--https`）；PyPI wheel 与 Python 3.11 兼容未实测。
9) 测试：固定中文音频 fixture → CER 阈值；静音/噪声/长音频；接口超时与取消；模型缺失时 503；前端按住说话的状态机单测。

#### C10 cmdk — P1
- 仓库：`https://github.com/dip/cmdk`（原 `pacocoursey/cmdk`）｜13,064 star｜最近推送 2025-10-29｜TypeScript
- 许可证：GitHub 识别 MIT，但**根目录没有 LICENSE 文件**（引入前必须核对 npm 发布包）｜维护：约 1 年无提交，趋缓｜测试：未获取｜安全：OSV 最新 `cmdk@1.1.1` 0 条
- 核心源码：`packages/cmdk/src/`（`Command`、`useCmdk`、状态 store）
1) 已有：`Shell.tsx` 的搜索框 + `query` 过滤。
2) 不足：只能过滤当前页；键盘用户无法直达“某员工/某目标/某审批”。
3) 解决：⌘K 命令面板，分组、异步搜索、ARIA combobox、键盘导航。
4) 值得借鉴：`useCmdk` 的索引/过滤/选中状态，以及 combobox 的 ARIA 结构。
5) 独立封装：是，新增 `src/components/CommandPalette.tsx`。
6) 破坏接口：否，纯新增。
7) 许可：MIT（GitHub 识别），但缺 LICENSE 文件，**属于待核验项**。
8) 风险：维护放缓；实现不复杂，必要时自建；中文输入法组合态容易误触发。
9) 测试：上下键/回车/Esc；IME composing 时不触发；手机点击；axe 检查 combobox 角色。

#### C11 sonner — P1
- 仓库：`https://github.com/emilkowalski/sonner`｜13,139 star｜最近推送 2026-08-10｜TypeScript
- 许可证：GitHub 识别 MIT，**根目录无 LICENSE 文件**（待核验）｜维护：近 3 个月有更新｜测试：未获取｜安全：OSV 最新 `sonner@2.0.8` 0 条
- 核心源码：`src/`（toast 状态、Toaster、堆叠与动画）
1) 已有：无全局提示；错误散落在各卡片。
2) 不足：长任务完成/失败没有统一到达；手机端切走后完全无感。
3) 解决：轻量 toast + promise 状态 + 操作按钮。
4) 值得借鉴：toast 队列与堆叠、`aria-live` 处理。
5) 独立封装：是。
6) 破坏接口：否。
7) 许可：MIT（识别值，缺 LICENSE 文件，待核验）。
8) 风险：与现有视觉风格重复；通知泛滥；动画需遵守 `prefers-reduced-motion`。
9) 测试：出现/自动消失/操作按钮；与 SSE 去重；reduced-motion 下无动画。

#### C12 Radix Primitives — P1
- 仓库：`https://github.com/radix-ui/primitives`｜19,471 star｜最近推送 2026-10-14｜TypeScript
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：`e2e/`（已核验目录）｜安全：OSV 最新 `@radix-ui/react-dialog@1.3.1` 0 条
- 核心源码：`packages/react/dialog`、`dropdown-menu`、`focus-scope`、`dismissable-layer`、`tooltip`
1) 已有：无 Dialog/Dropdown/Tabs/Tooltip；页面里连 `dialog` 字样都没有。
2) 不足：新增弹层时焦点管理、Esc、ARIA 都要手写，易出错。
3) 解决：只取 3-5 个原语，获得可访问的焦点/键盘行为。
4) 值得借鉴：`focus-scope` 的焦点循环、`dismissable-layer` 的外点关闭/嵌套层级。
5) 独立封装：是，按组件引入。
6) 破坏接口：否，但样式要映射到 `os.css` 变量。
7) 许可：MIT。
8) 风险：依赖数量增加；样式冲突；只选真正需要的组件，不整包引入。
9) 测试：Tab/Esc/外点关闭/焦点回退；axe 零 serious。

#### C13 faster-whisper — P2
- 仓库：`https://github.com/SYSTRAN/faster-whisper`｜26,506 star｜最近推送 2026-11-02｜Python
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：`tests/`（已核验）｜安全：OSV 最新 `faster-whisper@1.2.1` 0 条
- 核心源码：`faster_whisper/`（`transcribe.py`、VAD、时间戳）
1) 已有：无。
2) 不足：同 C9。
3) 解决：中文识别可用；适合“导入音频文件/会议录音”转写，不适合常驻麦克风（CPU 较重）。
4) 值得借鉴：VAD 切分与词级时间戳。
5) 独立封装：是，但依赖 CTranslate2 + 模型下载，体积大。
6) 破坏接口：否。
7) 许可：MIT。
8) 风险：CPU 推理慢于 sherpa-onnx int8；模型下载体积；与 Python 3.11 的 wheel 兼容需实测。
9) 测试：文件转写 fixture；长音频分段；断网时模型缺失的 503。

#### C14 FunASR — P2
- 仓库：`https://github.com/modelscope/FunASR`｜20,949 star｜最近推送 2026-11-05｜Python
- 许可证：代码 MIT（`LICENSE`）；模型 `MODEL_LICENSE`（自定义协议：允许使用/修改/分享，需保留来源与模型名，含社区行为条款）｜维护：活跃｜测试：`tests/`（已核验）｜安全：OSV 最新 `funasr@1.4.22` 0 条
- 核心源码：`funasr/`、`runtime/`、`model_zoo/`、`fun_text_processing/`
1) 已有：无。
2) 不足：同 C9。
3) 解决：中文识别与标点/ITN 精度通常更好；生态完整。
4) 值得借鉴：`fun_text_processing` 的标点与文本正则化；`runtime/` 的服务化封装。
5) 独立封装：是，但依赖 PyTorch，安装体积远大于 sherpa-onnx。
6) 破坏接口：否。
7) 许可：代码 MIT；模型是**非 OSI 自定义许可**，需在“关于/许可证”页展示来源与模型名；判定：允许使用，但需履行署名义务。
8) 风险：PyTorch 依赖重；模型许可与再分发需法务确认；GPU/CPU 资源。
9) 测试：与 sherpa-onnx 同一组中文 fixture 对比 CER；模型缺失/许可未接受时拒绝启用。

#### C15 uPlot — P2
- 仓库：`https://github.com/leeoniya/uPlot`｜10,617 star｜最近推送 2026-10-30｜JavaScript
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：`bench/`、`demos/`（未见独立测试目录）｜安全：OSV 最新 `uplot@1.6.32` 0 条
- 核心源码：`src/`、`dist/`
1) 已有：`charts.tsx` 手写 SVG。
2) 不足：数据量上千后重绘与交互成本高；无缩放/十字线。
3) 解决：极小体积、Canvas 高性能折线/面积图。
4) 值得借鉴：增量重绘与降采样策略。
5) 独立封装：是，替换 `LineChart` 内部实现即可。
6) 破坏接口：否，props 不变。
7) 许可：MIT。
8) 风险：Canvas 文本可访问性差（需配套表格/aria）；样式需自绘。
9) 测试：1k/10k 数据渲染耗时基准；缩放/十字线交互；无障碍替代表格。

#### C16 i18next — P2（结论：暂不引入）
- 仓库：`https://github.com/i18next/i18next`｜8,663 star｜最近推送 2026-11-05｜JavaScript
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：未获取｜安全：OSV 最新 `i18next@26.7.2` 0 条
- 核心源码：`src/Translator.js`、`src/PluralResolver.js`、`src/Formatter.js`、`src/LanguageUtils.js`
1) 已有：无框架，硬编码中文。
2) 不足：文案散落在 19 个页面；`toLocaleString()` 未固定 locale。
3) 解决：资源文件、插值、复数、命名空间、按需加载。
4) 值得借鉴：`PluralResolver` 与 `Formatter` 的 Intl 用法。
5) 独立封装：是，但当前引入收益低。
6) 破坏接口：否，但会改动所有页面文案。
7) 许可：MIT。
8) 风险：为一个中文单用户产品增加抽象层，维护成本大于收益；容易只做“英文壳”。
9) 测试：伪 locale（如 `en-XA`）检查截断；日期/数字固定 locale 的单元测试。

#### C17 Dexie — P2
- 仓库：`https://github.com/dexie/Dexie.js`｜14,675 star｜最近推送 2026-10-30｜TypeScript
- 许可证：Apache-2.0（`LICENSE` + `NOTICE`）｜维护：活跃｜测试：未获取（有 `SECURITY.md`）｜安全：OSV 最新 `dexie@4.4.7` 0 条
- 核心源码：`src/`、`addons/`
1) 已有：`localStorage` 只存 mode/theme/会话 id。
2) 不足：手机离线时写操作会直接失败；无草稿队列。
3) 解决：IndexedDB 离线 outbox，恢复网络后按顺序重放。
4) 值得借鉴：事务与版本升级（schema migration）。
5) 独立封装：是，`src/lib/outbox.ts`。
6) 破坏接口：否，但需要后端支持幂等键（`Idempotency-Key`）。
7) 许可：Apache-2.0。
8) 风险：只有“真离线需求”出现时才值得；否则是重复状态源。
9) 测试：断网→写→恢复→重放；重复投递幂等；版本升级迁移。

#### C18 vis-timeline — P3
- 仓库：`https://github.com/visjs/vis-timeline`｜2,576 star｜最近推送 2026-11-02｜JavaScript
- 许可证：双许可 `LICENSE.MIT.txt` + `LICENSE.Apache-2.0.txt`（已核验文件存在）｜维护：活跃但 issue 多（301）｜测试：`.mocharc.json`（Mocha，已核验）｜安全：OSV 最新 `vis-timeline@8.5.6` 0 条
- 核心源码：`lib/timeline/`（`Timeline.js`、`TimeAxis.js`、`Stack.js`、`ItemSet.js`）
1) 已有：无甘特/日程视图。
2) 不足：项目与目标没有时间轴；只有表格。
3) 解决：横向时间轴、分组、拖动、缩放。
4) 值得借鉴：`Stack.js` 的轨道排布、`TimeAxis.js` 的刻度策略。
5) 独立封装：是，但依赖 `vis-data`，体积不小。
6) 破坏接口：否，新增页面。
7) 许可：MIT/Apache-2.0 双许可，可择一。
8) 风险：**不适合聊天/执行时间线**（它是“历史数据浏览器”，交互重）；只适合“项目甘特/日程”。
9) 测试：缩放/拖动/分组；移动端触摸；大数据量性能。

#### C19 RealtimeSTT — P3
- 仓库：`https://github.com/KoljaB/RealtimeSTT`｜10,231 star｜最近推送 2026-10-30｜Python
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：未获取（含 Docker/示例）｜安全：OSV 最新 `RealtimeSTT@1.1.2` 0 条
- 核心源码：`RealtimeSTT/`、`docs/`、`example_fastapi_server/`
1) 已有：无。
2) 不足：同 C9。
3) 解决：VAD + 流式识别的一体化封装，快速做“按住说话”。
4) 值得借鉴：VAD 静音判定与分段提交。
5) 独立封装：是，但会引入 torch/whisper 依赖。
6) 破坏接口：否。
7) 许可：MIT。
8) 风险：依赖重、资源占用高；与 sherpa-onnx 功能重叠。
9) 测试：端到端按住说话→转写；静音不触发；取消请求。

#### C20 GPT-SoVITS — P3
- 仓库：`https://github.com/RVC-Boss/GPT-SoVITS`｜64,937 star｜最近推送 2026-10-31｜Python
- 许可证：MIT（`LICENSE`）｜维护：活跃｜测试：未获取｜安全：未查询（不作为依赖）
- 核心源码：`GPT_SoVITS/`、`api.py`、`api_v2.py`
1) 已有：无 TTS。
2) 不足：管家只有文字，没有“声音人格”。
3) 解决：高质量中文 TTS/音色克隆。
4) 值得借鉴：`api_v2.py` 的服务化接口形态。
5) 独立封装：是，但需 GPU/大显存，属独立服务。
6) 破坏接口：否。
7) 许可：MIT（代码）；训练/参考音频的版权需另行确认。
8) 风险：体积、显存、启动时间都不适合 Y1.0；语音克隆有伦理与合规风险。
9) 测试：固定文本合成→时长/采样率断言；服务不可用时降级为文字。

#### C21 openWakeWord — P3
- 仓库：`https://github.com/dscripka/openWakeWord`｜2,917 star｜最近推送 2025-12-30｜Python
- 许可证：Apache-2.0（`LICENSE`）｜维护：趋缓（约 10 个月无推送）｜测试：未获取｜安全：未查询
- 核心源码：`openwakeword/`、`examples/`、训练 notebook
1) 已有：无唤醒词。
2) 不足：只能手动点击。
3) 解决：本地唤醒词检测（TFLite）。
4) 值得借鉴：流式帧推理与误唤醒抑制。
5) 独立封装：是，但需先有中文唤醒词模型。
6) 破坏接口：否。
7) 许可：Apache-2.0。
8) 风险：官方预训练模型以英文短语为主，中文“小鎏”需自训练；训练数据与时间成本高；维护趋缓。
9) 测试：正样本唤醒率、负样本误唤醒率；噪声环境；CPU 占用。

#### C22 Tauri 2 — P3
- 仓库：`https://github.com/tauri-apps/tauri`｜112,527 star｜最近推送 2026-11-05｜Rust
- 许可证：双许可 `LICENSE-MIT` + `LICENSE-APACHE-2.0`（API 报 Apache-2.0，实为双许可）｜维护：活跃｜测试：未获取｜安全：未查询
- 核心源码：`crates/`、`plugins/`（托盘/自启/通知均有官方插件）
1) 已有：无桌面壳。
2) 不足：同 C6。
3) 解决：更完整的托盘/自启/通知/自动更新，安装包更专业。
4) 值得借鉴：插件化的 tray/autostart/notification 设计。
5) 独立封装：是，但需要 Rust 工具链，且仍要把 Python 服务作为 sidecar 打包。
6) 破坏接口：否。
7) 许可：MIT/Apache-2.0 双许可。
8) 风险：构建链复杂度远高于 pywebview；sidecar 生命周期/端口/升级需自管；对“只是下载安装来用”的用户，pywebview 足够。
9) 测试：安装包冒烟、sidecar 启停、托盘/自启/通知各一条；升级不丢数据。

---

## 3. 最值得的 5 个深评

### 3.1 assistant-ui + AG-UI：多角色、多任务、有时间线的界面

**结论**：不引入新的“聊天框架”，引入两样东西——assistant-ui 的**消息部件状态模型**（可选用其 primitives 或只抄模型），以及 AG-UI 的**最小事件集**。时间线组件自建。

**为什么是这两个**
- 现有 `ChatPanel.tsx` 的 `Message{role, content, tools}` 无法表达“管家拆解了 3 个子任务、分派给 2 个员工、其中一个失败后重试”。它只能把工具调用塞进 `<details>`。
- assistant-ui 的 `messagePart` 把一条消息拆成 text/reasoning/tool/attachment 等部件，`chainOfThought` 专用于“拆解过程”，`actionBar` 适合放“重试/撤销/查看证据”。这与“管家主面板”的叙事结构同构。
- AG-UI 的价值不是库，而是**事件语义**：一次运行有开始、消息流、工具调用、状态快照/增量、结束/错误。把 SSE 从“聊天专用”升级为“运行时事件流”，对话、时间线、审批、目标页才能消费同一份事实。

**建议的界面结构（管家主面板）**
1. 顶部：一句话输入框 + “按住说话”按钮 + 当前管家状态（空闲/拆解中/执行中/等待审批）。
2. 主体：**一次委托 = 一个时间线**，节点按阶段分组：
   - 拆解：管家给出的步骤与理由（默认折叠，展示“为什么这样拆”）。
   - 分派：员工卡片（头像/名称/职责）+ 任务摘要 + 开始时间。
   - 执行：每个员工一条泳道，节点为“思考 / 工具调用 / 观察 / 产物”；工具节点带审计 ID，可点开证据。
   - 汇总：结果卡（成功/部分成功/失败）+ 产物链接 + 下一步建议。
3. 失败节点：显示错误分类、重试次数、下一次重试时间；“重试”走真实接口，不做前端假动画。
4. 可撤销节点：仅当后端声明该动作可逆（如写文件有快照）才显示“撤销”；不可逆动作显示“已执行，无法撤销”，并链到审计记录。
5. 历史：按天分组的运行列表（可折叠），手机端单列、输入框吸底。

**具体可借鉴的模块**
- `assistant-ui/packages/react/src/primitives/{message,messagePart,chainOfThought,actionBar,composer}`——数据结构与折叠交互。
- `assistant-ui/packages/react/src/assistant-transport.ts`——流式事件到消息状态的映射思路。
- `OpenHands/src/components/conversation-events/chat/{group-events.ts,event-thought-helpers.ts,step-disclosure-context.tsx,task-tracking/}`——事件分组与步骤展开。
- `ag-ui-protocol/ag-ui/docs/`——事件命名与状态快照/增量语义。

**集成方式（最小改动）**
- 后端：在现有 SSE 上新增事件类型与 `goal_id/employee_id/step_id/parent_id` 字段；保留旧字段；新增 `GET /v1/runs/{id}/events?since=` 支持断线续传。
- 前端：新增 `src/lib/runEvents.ts`（事件类型 + reducer）、`src/components/timeline/`（RunTimeline、StepNode、EmployeeLane、EvidenceLink）；`ChatPanel` 继续服务普通问答，不强行改造。
- 手机：`MOBILE_NAV_KEYS` 增加“管家/对话”入口（建议五项：总览、管家、员工、审批、我的；设置收进“我的”）。

**风险**
- assistant-ui 仍是 0.x，锁定版本；只取 headless primitives，不取云/runtime。
- 不要一次把 19 个页面都改成事件驱动；先让“管家主面板 + 时间线”跑通。

**测试**
- reducer 单测：事件序列 → 时间线树（分组、失败重试、部分成功）。
- SSE 回放：录制真实运行的事件流，断言 UI 稳定。
- 断线重连：`Last-Event-ID` 续传不重复、不丢节点。
- 真浏览器 E2E：发一句话 → 出现拆解节点 → 出现员工节点 → 汇总卡出现 → 审计 ID 可点开。

### 3.2 pywebview + pywin32/pywinrt：Windows 轻桌面壳

**结论**：用 pywebview 承载现有单端口控制台；托盘用 pywin32；通知用 pywinrt；自启用 HKCU Run。不引入 Electron，也不在当前阶段引入 Tauri。

**为什么适合鎏灏**
- 项目本身是 Python 服务 + 单端口静态控制台（`src/gateway/console_static.py`），桌面壳只需要“一个窗口 + 生命周期 + 系统集成”，不需要第二套前端运行时。
- Electron 会再带一个 Chromium（安装包 100MB+），与“用户只是下载安装来用”的目标冲突；Tauri 需要 Rust 工具链，且仍要打包 Python sidecar。
- pywebview 在 Windows 的 `edgechromium/webview2core/winui3` 后端复用系统 WebView2；`requires-python >= 3.10` 与本项目兼容；BSD-3 许可干净。

**建议的落地形态**
- `scripts/desktop_shell.py`：单实例互斥 → 启动/复用网关 → 等 `/v1/ready` → 打开窗口；窗口关闭时按设置“最小化到托盘”或“退出并停服务”。
- `scripts/desktop_tray.py`：托盘菜单 = 打开主面板 / 查看运行中任务 / 暂停接收任务 / 开机自启开关 / 退出。
- `scripts/notify.py`：任务完成、失败、等待审批三类通知；点击通知回到对应 `goal_id` 页面。
- 自启：写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值指向 `pythonw.exe scripts\desktop_shell.py --hidden`；设置页提供开关与“立即验证”。
- 打包：仍用现有 Python 环境；WebView2 缺失时提示安装 Evergreen Runtime，不静默失败。

**必须诚实的地方**
- “最小化到托盘”不是 pywebview 内建能力，必须自己写 win32 消息循环；写错会出现“托盘图标不消失/退出后进程残留”。
- 通知必须有 AUMID（开始菜单快捷方式），否则 Win10 上可能不显示；这一点需在目标机器实测。
- 不要用 pystray（LGPL-3.0 且 2025-03 后停滞）替代 pywin32。

**测试**
- CI（Windows）：`desktop_shell.py --hidden` 启动 → 轮询 `/v1/ready` → 断言进程存活 → 发送退出信号 → 断言无残留进程。
- 注册表：写入/读取/删除各一条，测试结束清理；断言默认关闭。
- 通知：发送成功返回码 + 无 AUMID 时的错误提示；手工清单覆盖托盘菜单。
- 回归：桌面模式与 `start-liuhao.bat` 浏览器模式共用同一网关，两路径都要跑一次登录/对话。

### 3.3 sherpa-onnx：中文本地 ASR 的可行方案与代价

**结论**：优先 sherpa-onnx（Apache-2.0、离线、CPU int8、中文 Paraformer），以“按住说话”作为第一版交互；唤醒词与 TTS 放到第二阶段。

**为什么不是先上 Whisper**
- faster-whisper 中文可用、生态成熟（MIT，26.5k star），但 CPU 推理更重，更适合“导入音频文件转写”，不适合常驻麦克风。
- FunASR 中文精度与标点/ITN 生态最好，但依赖 PyTorch，安装体积与内存显著更高；且模型是**自定义 MODEL_LICENSE**（允许使用/修改/分享，要求保留来源与模型名），需要履行署名义务。
- sherpa-onnx 的示例覆盖离线解码、关键词唤醒、标点、ITN、HTTP 服务化，正好对应“语音输入→文本→管家”的最小链路；中文 Paraformer 模型（含 int8）已在官方文档确认存在。

**第一版建议链路**
1. 前端：`MediaRecorder` 录音（HTTPS 下可用；项目已有 `--https`）→ 上传短音频 → 显示“识别中” → 把文本填入输入框（不自动发送，避免误识别直接触发执行）。
2. 后端：`POST /v1/voice/transcribe`（multipart，限制时长/大小/格式）→ sherpa-onnx 解码 → 标点/ITN → 返回文本与置信度。
3. 可选：识别结果进入管家输入框，用户确认后再发送；长文本自动分段。
4. 未安装模型/依赖时返回 503「语音未启用」，前端隐藏入口，**不伪造可用**。

**代价（必须写进验收）**
- 模型体积：百 MB 级，随安装包分发会让下载变大；可做“首次启用时下载 + 校验哈希”。
- 内存与首包：首次加载模型有明显延迟；需要预热或懒加载 + 进度提示。
- 准确率：中文普通话可用性需实测；口音、噪声、专业术语（“鎏灏”“知识库”“审批”）需自定义热词。
- 隐私：本地推理不经过云，是优势；但麦克风权限、录音临时文件清理要写进实现。
- 许可：代码 Apache-2.0；模型权重许可必须逐个核对，不能默认“代码许可=模型许可”。

**测试**
- 固定中文音频 fixture（命令、长句、数字、专有名词）→ CER 阈值；阈值按实测确定，不拍脑袋。
- 静音/纯噪声/超长音频/超大文件 → 明确错误码。
- 模型缺失 → 503；未启用时前端无入口。
- 真机：Windows 麦克风 + 手机浏览器 HTTPS 各跑一次。

### 3.4 axe-core + 自建最小无障碍集

**结论**：引入 axe-core 做门禁，但**先把最小集做完**；不要用组件库替代真正的键盘/焦点/缩放验收。

**这个规模的产品，最小必须做到**
1. 焦点可见：删除/改写两处 `outline: none`，加全局 `:focus-visible` 环；Tab 顺序与视觉顺序一致。
2. 键盘可达：所有菜单、对话历史、审批操作、时间线折叠都能用键盘完成；Esc 关闭弹层；焦点回退到触发元素。
3. 语义与播报：页面标题、landmark、表格表头、按钮名称；流式回答与任务状态用 `aria-live="polite"`，错误用 `assertive`。
4. 缩放与重排：200% 浏览器缩放 + 320px 宽不横向滚动；字号下限提升（当前 10.5px 起，建议关键信息 ≥12px，正文 ≥14px）。
5. 对比度：正文 ≥4.5:1，大字号 ≥3:1；状态不能只靠颜色（成功/失败同时给图标/文字）。
6. 触摸目标：手机底部导航与主要按钮 ≥44×44px；安全区适配。
7. 动效：已有 `prefers-reduced-motion`，需覆盖新增的时间线/通知动画。
8. 门禁：`axe-core` 在 vitest（静态规则）+ 真浏览器（布局规则）各跑一次，serious/critical = 0。

**为什么用 axe-core 而不是先换组件库**
- 现有 UI 是手写 CSS，问题主要是焦点、字号、对比度、ARIA，不是“缺组件”。
- Radix 能解决新弹层的焦点/键盘问题，但不会自动修复已有页面的对比度与字号；按需引入 3-5 个原语即可。

**许可注意**
- MPL-2.0 是文件级 copyleft：作为未修改依赖使用没问题；如果改了 axe-core 的源文件，需要公开这些文件的修改。不要把它复制进仓库再改。

**测试**
- `npm run a11y`（jsdom 静态规则）+ Playwright 真浏览器全页扫描；基线文件记录已知问题，新增 serious/critical 直接失败。
- 键盘走查脚本：Tab 遍历 19 个页面，断言焦点不丢失、不被遮挡。
- 200% 缩放截图对比（人工基线）。

### 3.5 i18next：国际化最小必要（结论：暂不引入）

**这个规模的产品，最小必须做到**
1. 所有日期/数字/相对时间走 `Intl`，并显式传 locale（不要依赖浏览器默认）。
2. 全部面向用户的文案集中在少数资源文件（哪怕先只有 `zh-CN`），页面不再散落硬编码字符串。
3. `index.html` 的 `lang` 与运行时 locale 一致；主题/语言切换持久化。
4. 排版预留：按钮/标签允许换行或截断，避免“中文刚好放下、英文溢出”。
5. 不引入复数/性别等尚不需要的规则。

**为什么不现在引入 i18next**
- 当前是中文单用户产品，引入框架只会增加一层抽象与维护面积；且很容易做成“只有中文资源的空壳”。
- 先把字符串集中、`Intl` 统一、排版预留做完；等出现第二种语言需求时再引入 i18next（MIT，26.x，活跃），迁移成本很低。

**如果未来引入，值得借鉴的模块**
- `src/PluralResolver.js`、`src/Formatter.js`、`src/LanguageUtils.js`——复数与 Intl 封装。

**测试**
- 伪 locale（`en-XA`）跑关键页面，检查截断/溢出。
- 固定 locale 的日期/数字断言（避免 CI 机器时区/语言影响）。
- 断言页面无裸 `Date.prototype.toLocaleString()`（统一走封装）。

---

## 4. 明确不建议引入

| 候选 | 星数/状态 | 许可证 | 不建议原因 |
|---|---|---|---|
| Open WebUI | 157,854，活跃 | BSD-3 + 品牌条款（修改/移除品牌需商业授权；滚动 30 天 ≤50 用户等例外） | 是全量产品（Svelte），不是组件；改品牌/再分发受条款限制；替换 console 等于重做 |
| LobeHub（原 LobeChat） | 84,049，活跃 | Apache-2.0 + 衍生作品商用需授权 | 许可不是纯 Apache；体量大、数据模型完全不同；引入即取代现有控制台 |
| LibreChat | 46,774，活跃 | MIT | 许可干净，但它是完整聊天产品；采用它意味着放弃现有 19 个业务页面与治理面 |
| ChatTTS | 39,987，活跃 | **AGPL-3.0** | 强 copyleft，网络使用即触发源码开放义务；与闭源/商业分发冲突，直接排除 |
| Flowise | 55,553，**已归档**（2026-08） | GitHub 未识别 | 已停止维护；可视化编排与现有 Goal/Workflow 重复 |
| Chainlit | 12,506，**已归档**（2026-09） | Apache-2.0 | 已停止维护；只适合做独立聊天 Demo，无法承载治理面 |
| piper | 11,316，**已归档**（2025-08） | MIT | 归档即不再修安全/兼容问题；TTS 改用 sherpa-onnx 或后续方案 |
| edge-tts | 12,460，活跃 | **LGPLv3**（仅 `srt_composer.py` 为 MIT） | 依赖微软 Edge 在线语音服务，非离线；LGPL 与在线服务条款双重不确定性；本项目优先本地 |
| pystray | 574，2025-03 后停滞 | **LGPL-3.0** | LGPL + 停滞；托盘直接用 pywin32（BSD 式）更干净 |
| Windows-Toasts | 145，2025-11 后停滞 | Apache-2.0 | 停滞近一年；改用 pywinrt（MIT，活跃） |
| Electron | 123,787，活跃 | MIT | 许可没问题，但安装包 100MB+、内存高；与“轻量桌面壳”目标冲突 |
| Yjs | 23,025，活跃 | MIT（`LICENSE` 为 MIT 正文） | 本项目服务端是唯一真相，不需要 CRDT；引入会制造第二个状态源 |
| ElectricSQL | 未列入候选 | Apache-2.0 | 依赖 Postgres 同步栈；本项目是 SQLite 单机，架构不匹配 |
| PouchDB | 未列入候选 | Apache-2.0 | 同上，且体积大；离线需求用 Dexie outbox 即可 |
| CopilotKit runtime | 38,531，活跃 | MIT（`LICENSE`） | 其 `runtime-client-gql` + provider 会新增一套运行时；其模式已被 AG-UI + assistant-ui 覆盖，只作参考 |
| A2A 协议 | 26,427，活跃 | Apache-2.0 | 面向跨厂商 agent 互操作；本项目内部派发已有自己的编排器，现阶段只有协议开销 |

---

## 5. 未验证项清单

1. **ASR 实测**：未测中文 CER、延迟、内存、首次加载时间；未测口音/噪声/专业术语。
2. **模型许可**：未逐一核对 sherpa-onnx 各中文模型权重许可；未核验中文 TTS 模型清单；未核验川渝方言模型的实际效果。
3. **Python 兼容**：未实际安装 sherpa-onnx / pywebview / pywinrt 到 Python 3.11 环境；wheel 兼容性未验证。
4. **WebView2**：未核验目标 Windows 版本是否预装 Evergreen Runtime；未实测 `winui3` 后端。
5. **原生通知**：未实测 pywinrt Toast 在 Win10 的 AUMID 要求与显示行为；未核验 pywin32 托盘消息循环在项目线程模型下的稳定性。
6. **assistant-ui**：未实测 React 19 下的构建体积、样式冲突与 0.x API 稳定性；未核验其 `react-ui` 是否强依赖 Tailwind。
7. **cmdk / sonner**：根目录无 LICENSE 文件，仅 GitHub 识别为 MIT；未核对 npm 发布包内许可文本。
8. **安全公告范围**：只查了顶层包最新版本的 OSV 记录，未做传递依赖全树审计；未查 GitHub 仓库级安全公告（仅抽查 assistant-ui 为 0）。
9. **测试覆盖率**：未获取任何仓库的覆盖率数值；仅确认测试目录/CI 是否存在。
10. **OpenHands 借鉴边界**：只读目录与文件结构，未逐行阅读其实现；未做代码复制，许可边界仍需在真正借鉴时复核。
11. **FunASR 模型许可**：只读了 `MODEL_LICENSE` 前 30 行，未做法务解读；署名义务的具体呈现方式未定。
12. **移动端**：未在真实 iOS/Android 上验证安全区、输入法遮挡、底部导航手势冲突。
13. **axe-core 覆盖**：未实测 jsdom 下哪些规则失效、哪些必须真浏览器；未建立基线。
14. **多设备同步**：未验证同一 token 在多设备并发写入时的行为；未设计幂等键。
15. **数据可视化**：未做 1k/10k 数据量下现有 SVG 图表的性能基准，因此 uPlot 的收益仍是推断。
16. **法律**：以上许可证判断是工程判断，不构成法律意见。
