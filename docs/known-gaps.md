# Known gaps

> 需求逐条追溯（哪些做了、哪些只是文档、哪些延后）见
> [需求追溯表](requirements-traceability.md) 与 [总任务台账](MASTER-TASK-LEDGER.md)。
> 本文件负责登记「已明确延后、不得遗忘」的事项；三者互补，缺一不可。

Facts about this checkout that are true today and that we are **not** fixing
now. Each entry says what is broken, how we know, what it affects, and what
would close it. No "should be fine" entries — if it is unverified, it says so.

| # | Gap | Impact | Status |
|---|-----|--------|--------|
| 1 | Local `semgrep` CLI cannot start | Development tooling only | Open, accepted |
| 2 | Gemini real success path unverified (no API key) | Gemini option is wired to `google-genai`; only a fake-key 400 proves the request reaches Google | Open, accepted |
| 3 | `file_write` 的占位内容会被当成真实产物 | 模型可以把任务名/描述原样写进文件，`artifacts` 非空即被算作可交付 | **发布后处理**（本轮只登记，不改代码） |

---

## 1. Local semgrep CLI is unusable because of a `mcp` version conflict

**What happens.** Running `semgrep` from this checkout's virtualenv fails at
import/startup: semgrep pulls a `mcp` dependency range that conflicts with the
`mcp>=2.0.0,<3.0.0` pin the product now needs for its MCP adapter (installed:
`mcp==2.1.1`). Either the CLI resolves against the wrong `mcp`, or it refuses to
install the version it wants without downgrading the product dependency.

**Why we are not fixing it.** The conflict is inside the development tool's
dependency closure, not in the shipped application. Resolving it by changing
`pyproject.toml` would mean downgrading `mcp` below the range the MCP adapter
(FastMCP 4.0.3) requires — the product would lose working MCP support to
satisfy a local linter. That trade is wrong, so semgrep stays out of this
checkout's virtualenv.

**Impact.** None on the product at runtime. Static analysis with semgrep has to
run from a separate tool environment instead of this venv.

**How to close it if it matters later.** Install semgrep in an isolated
environment (`uvx semgrep` / a dedicated venv) and point it at the source tree;
or wait for a semgrep release whose `mcp` range includes 2.x.

**Evidence.** `pip install -r requirements.txt` succeeds and the test suite
passes with `mcp==2.1.1` installed; the failure only appears when semgrep is
added to the same environment.

---

## 2. Gemini's real success path is unverified because no API key is available

**What happens.** `GoogleProvider.generate()` now uses the `google-genai` SDK
(`google.genai`), and the lock ships `google-genai==2.22.0` with
`protobuf==6.33.6`. With a deliberately invalid key the call reaches Google
and fails with `400 API_KEY_INVALID`, which proves the client wiring is real.
A **successful** generation has not been observed: this machine has no valid
`GOOGLE_API_KEY` / `GEMINI_API_KEY`.

**Why we are not closing it now.** Verifying the success path needs a funded
Google AI Studio key; we do not have one and will not fabricate a passing
result. Response parsing (single part, multi-part, empty, upstream error,
missing SDK) is covered by a fake `google.genai` client in
`tests/test_provider_google_genai_parsing.py` — that tests our code, not
Google's live endpoint.

**Impact.** The Gemini option is wired and reports failures honestly, but
"fill in a real key and get an answer" is unproven in this build.

**How to close it if it matters later.** Run one real generation with a valid
key (e.g. `GOOGLE_API_KEY=... python -c "..."` against
`GoogleProvider.generate()`), record the response and evidence, then remove
this entry.

---

## 3. `file_write` 的占位内容会被当成真实产物（发布后处理）

**What happens.** `LLMTaskDecomposer` 目前只校验 `file_write` 的 `inputs` 里
`path` / `content` 非空，不校验 `content` 是否真的是一份可交付正文。模型可以把
任务名或描述原样塞进文件（例如 `content="整理好的会议纪要"`），执行器照样落盘，
`artifacts` 非空，目标就被算成"有产出"。

**Why it matters.** 这与本轮修复的「没有产物文件就不算完成」是同一类问题的
两端：一端是 **没有产物却报成功**（已修：`artifacts=[]` 时降级为 `partial`）；
另一端是 **有文件但内容不是交付物**（本项）。两端都不能用"绿"骗人。

**Status.** **发布后处理**。本轮只登记，不改代码：收紧内容校验需要先定义
"什么算正文"（最小字节数 / 与 name、description 不同 / 可解析格式），否则容易
把合法的短文件误杀。改之前必须先补这条验收标准与回归用例。

**How to close it if it matters later.** 在执行前或执行后校验 `file_write` 的
`inputs.content`：不能与任务名/描述相同、不能是明显的占位说明；不满足则不计入
`artifacts`，并按"无产出"处理。验收用例至少覆盖：占位内容 → 不算交付；真实
正文 → 算交付；短但合法的文件（如 `hello.txt`）不被误杀。

---

# 已明确延后、不得遗忘

> 以下事项**不是**「以后有空再说」，而是已经判定本轮不做、但没有取消。
> 每项写清：为什么延后、依赖什么、什么算做完（可验证的验收标准）。
> 任何人重新捡起这些工作前，先读这一节，不要重复发明或悄悄降级标准。

| 编号 | 事项 | 状态（四类口径） |
|---|---|---|
| D-1 | 自升级 + 受控自改代码 | 部分完成（自升级链已提交；受控自改代码 **决定延后**，见下） |
| D-2 | 管家主面板（一句话 → 分派员工 → 结果汇总） | 部分完成（代码与测试已入库；真实一句话链路未实跑） |
| D-3 | 三个快效技能（定时提醒、本地语音输入、操作前预览） | 部分完成（提醒已入库；语音、预览 **决定延后**） |
| D-4 | 自动化真实后端（浏览器 / 安卓 / Windows 桌面键鼠） | 决定延后（安全门槛未闭合，理由见下） |
| D-5 | 30 方向战略落地 | 部分完成（真有 8 / 半成品 11；不做项 **决定延后**） |
| D-6 | 情报中心后续阶段（漏洞查询、缺口对照、更新提醒） | 决定延后（依赖 advisory 接线与产品形态，理由见下） |
| D-7 | 知识闭环（入库 UI + 检索 + 对话注入） | 部分完成（代码已入库，真实 E2E 未验证） |
| D-8 | 进程内排程器（定时/循环 Goal） | 部分完成（`3bda8c7d` 已入库，真实定时验收未做） |
| D-9 | 备份 / 恢复 / 导出（一键化） | 部分完成（CLI 已操作真实归档 `dcf025c3`；无设置页/恢复流程） |
| D-10 | 产物索引与执行回溯 | 部分完成（`c14ab20c` + `82f95450`；权限面已审计：HTTP 无跨账号列出/下载入口，下载面按账号分区；未做 UI） |

### 四类归属统计（2026-11-06，按 `docs/strategy-priority-matrix.md` `9772b80a`）

口径：**已完成 / 部分完成 / 决定延后或不做 / 未验证（附原因）**；不再允许
「待办」「进行中」这类既无主人也无理由的状态。

| 登记范围 | 已完成 | 部分完成 | 决定延后或不做 | 未验证 |
|---|---|---|---|---|
| D-1～D-10 交付待办 | 0 | 8（D-1、2、3、5、7、8、9、10） | 2 主条目（D-4、D-6）+ 3 子项（自改代码、语音、操作前预览） | 0 个独立条目（原因在正文） |
| 30 方向（矩阵口径） | 0（未跑运行时 E2E，不标已完成） | 8（「现在」8 项 = in-progress） | 13（「不现在做」= 不做 8 + 仅设计 5） | 0 |
| **合计（去重前）** | **0** | **16** | **18**（2 主条目 + 3 子项 + 13「不现在做」） | **0** |

> 合计是登记条目数，不是独立能力数：D-7 与方向 3、D-8 与方向 8 等有重叠。
> **「下一阶段」9 项（2、5、7、9、16、17、20、24、29）是 planned-next、计划做，不是
> 「决定延后」**，故不在上表「决定延后或不做」列里；它们记「待做」，见
> [总任务台账](MASTER-TASK-LEDGER.md) D-04。原表把 9 项并入延后且合计写 24，与
> 「2 主条目 + 3 子项 + 22 方向 = 27」自相矛盾，本次一并修正。

**接手方（按仓库实际流）。** D-1 = upgrade 流；D-2 = console + gateway 流；D-3 =
tasks 流（提醒已落），语音与预览需新任务；D-4 = automation 流；D-5 = 战略流；
D-6 = intel 流；D-7 = knowledge 流；D-8 = tasks 流；D-9 = ops 流；D-10 =
artifacts 流。

### 30 方向逐条落地状态（矩阵 `9772b80a`）

标签口径：**in-progress**（「现在」且未完成）/ **planned-next**（下一阶段）/
**deferred-by-decision**（决定不做）/ **design-only**（只做设计不落地）。
来源均为 `docs/strategy-priority-matrix.md` §1（`:20-52`）。

> **与 T-11「真有 8」不是同一把尺子**：T-11 的「真有 / 半成品 / 假实现 / 无实现」
> 是**静态存在判定**；本节的 in-progress / planned-next / design-only 是**排期优先级**。
> 因此 25 可以是「真有」（有 CLI）同时是 `design-only`（不现在投入）；16、29 同理。
> 「现在 8」= 1、3、8、22、23、26、28、30，与 T-11 的「真有 8」（1、8、16、22、23、25、29、30）
> **集合不同**，两轴不可相加。原文档未写这条，易被误读为矛盾。

| # | 方向 | 标签 | 当前到哪一步 / 理由 |
|---|---|---|---|
| 1 | 通用智能与认知架构 | in-progress | 真实主链（`liuhao.py`/`providers.py`/`planner`/`AgentRuntime`）已在；未跑运行时 E2E |
| 2 | 多智能体协作 | planned-next | 依赖 8、24、3；生产只走单员工 resolver，committee/blackboard 仅测试引用 |
| 3 | 长期记忆与知识图谱 | in-progress | documents→chunk→embedding→vector_store 真链路；`/v1/chat` 已接 `build_knowledge_context`；缺真实 embedding E2E 与入库 UI 完整闭环 |
| 4 | 自主研究与科学发现 | design-only | 零实现；只设计状态机与安全边界 |
| 5 | 多模态智能 | planned-next | 依赖 1、3；`perception.py` 仅文本，图像/音频/视频显式未实现 |
| 6 | 电脑自主操作 | deferred-by-decision | 三闸未全开返回 `[]`，唯一后端 `fake.py`；放开 OS 控制风险大于收益 |
| 7 | AI 软件工厂 | planned-next | 依赖 26；仅受限 Python + 单文件写入，不做任意仓库改码/PR |
| 8 | 自动化工作流 | in-progress | Goal/AgentRuntime 真；进程内 SQLite 排程器 `3bda8c7d` 已入库；缺真实定时 Goal 跑满周期 + 重启恢复 |
| 9 | 全球信息情报 | planned-next | 依赖 8、3、25；只做本地订阅摘要，不做情报平台 |
| 10 | 数字孪生与仿真 | deferred-by-decision | 零实现，单机无采集面 |
| 11 | 物联网 | deferred-by-decision | 无 MQTT/Modbus/OPC-UA/Zigbee 代码，缺设备与 7×24 运维 |
| 12 | 机器人与具身智能 | deferred-by-decision | 无 ROS/Gazebo/运动控制，安全关键作动需硬件与认证 |
| 13 | 云端与边缘计算 | design-only | 只设计可选出口；默认形态单进程单端口、不依赖 Docker |
| 14 | 空间智能 | deferred-by-decision | 无 GIS/地图/点云/3D 代码，缺空间数据与 GPU |
| 15 | 工业自动化 | deferred-by-decision | 无 PLC/SCADA/OPC-UA，缺设备与资质；法律责任边界 HUMAN DECISION REQUIRED（本项目尚无已实现的责任框架） |
| 16 | 企业管理智能体 | planned-next | 依赖 22、8；内部运营可用，外部连接器只设计（依赖 27） |
| 17 | 金融经济分析 | planned-next | 依赖 16、23；只保留 token 成本账本，交易/投资建议不做 |
| 18 | 个性化教育 | deferred-by-decision | 38 个行业模板仅测试导入，无学生模型/课程 |
| 19 | 创意生产 | deferred-by-decision | 插件未加载、无生成执行路径；需 GPU/云 API 且竞争无胜算 |
| 20 | 个人数字生活管理 | planned-next | 依赖 8、3、28；画像与会话已真，提醒运行时已入库，日程未做 |
| 21 | 分布式系统 | design-only | 只设计一致性契约；无多节点部署与集群调度 |
| 22 | 网络安全与数字身份 | in-progress | security/gateway/审批真实；外部 IdP/KYC 不做 |
| 23 | 数据基础设施 | in-progress | 6 迁移 + 主库 + 8 个 SQLite；路径收口与备份进行中，不做数仓 |
| 24 | 自适应与持续优化 | planned-next | 依赖 8、23；反馈服务仍是内存 dict，`evolution.py` 仅测试引用 |
| 25 | 开源技术情报 | design-only | 保留 CLI；无控制台/调度，快照 2026-09 已过期 |
| 26 | 数字资产管理 | in-progress | 产物索引 `c14ab20c` 已入库；缺权限过滤/UI，不做重型 DAM |
| 27 | 隐私计算与可信 AI | design-only | 只设计 PII 强制点；MPC/TEE 单机做不出真实隐私计算 |
| 28 | 高可用与灾难恢复 | in-progress | `backup.py` 加密归档真实但无生产调用方；缺调度与恢复流程，不做复制/切换 |
| 29 | 跨平台生态 | planned-next | 依赖 1/8、22；只维护 PWA/SDK/桌面集成，不做原生 App |
| 30 | 人机协同决策 | in-progress | 审批签发/列表/撤销 + enforcement + 审计链真实；committee 未接线 |

### 决定不做 / 只做设计的合并清单（矩阵 §2）

| 判定 | 项 | 理由（一句） |
|---|---|---|
| deferred-by-decision（不做） | 6 电脑自主操作、10 数字孪生、11 IoT、12 机器人、14 空间智能、15 工业自动化、18 教育、19 创意 | 风险大于收益 / 无采集面 / 缺设备与运维 / 安全关键作动 / 缺空间数据 / 缺工业资质 / 模板 Demo / 无 GPU 竞争力 |
| design-only（只做设计） | 4 科研、13 云边、21 分布式、25 开源情报、27 隐私计算 | 零实现或与单机形态冲突 / 单机单进程 / 低频工具保留 CLI / MPC/TEE 无落地条件 |

## D-1 自升级 + 受控自改代码

**当前状态（2026-11-06 对齐）。** 自升级链已**提交**：`85e1f639` / `3afa5a56` /
`4b519bbe` 引入 `src/upgrade/`（manager/dataguard/backups/selfcheck），并带
`tests/test_upgrade_selfcheck.py`。但 CI run `42127243953` 上
`test_unreadable_database_fails_the_check` 仍红（提交态失败，不是工作树问题），
因此只能记「部分完成」，不能记「已完成」。

**受控自改代码：决定延后（deferred-by-decision）。** 让产品改自己的代码并热更新
是最高风险能力：一旦被提示注入或错误模型输出驱动，等于把「已被审计约束的系统」
换成「能重写审计约束的系统」。这不是缺时间，而是**决定不现在做**。

**依赖什么。**
1. 发布物签名与哈希校验（安装器/打包流）；
2. 审批中心（`src/gateway/policy.py`）与人工确认票据（`src/automation/confirm.py`）；
3. 版本回滚与数据库迁移向后兼容（`alembic/versions/` 需先解决 SQLite-only DDL）；
4. 执行围栏（`LIUHAO_EXECUTOR_FENCE` 相关，见 `scripts/verify_deployment_gate_vars.py`）。

**验收标准。**
- 升级包必须有签名，签名校验失败时拒绝安装并留审计；
- 自改代码必须走「生成 diff → 人工审批 → 沙箱测试 → 原子替换 → 失败自动回滚」；
- 端到端演练一次：升级失败后系统回到原版本且数据无损；
- 全流程写入审计链并可查询。

## D-2 管家主面板（一句话 → 分派员工 → 结果汇总）

**当前状态（2026-11-06 对齐）。** 已**提交**：`94003942` 建管家面板，
`3510cab3` 接线「点名派给员工 / 失败任务重试 / 增量进度 / 待办审批」，前端
`apps/console/console/src/pages/Butler.tsx` 自述链路为
`POST /v1/goals(background) → GET /v1/goals/{id} 增量 → 汇总`，并带
`Butler.test.tsx`、`lib/butler.test.ts`。**未验证**的是真实一句
「一句话 → 建目标 → 分派 → 执行 → 汇总」的端到端运行（前端单测不等于链路可用），
因此记「部分完成」，不记「已完成」。

**依赖什么。**
1. 目标/规划链路（planner + workflow）真实可用并支持多员工分派；
2. 员工状态与执行结果的结构化汇总接口；
3. 控制台管家视图（不是聊天页改名）。

**验收标准。**
- 用户只输入一句话，系统创建目标、生成任务、指派 ≥1 名员工并开始执行；
- 执行完成后在同一会话返回结构化汇总（谁做了什么、结果、失败原因）；
- 可单独指派某个员工并从 UI 看到其当前任务与结果；
- 全链路数据落库，刷新页面/重启服务后仍可见。

## D-3 三个快效技能（定时提醒、本地语音输入、操作前预览）

**当前状态（2026-11-06 对齐）。**
- **定时提醒：已入库，部分完成。** `manage_reminders` 成为第 10 个内置工具
  （`src/ai/tools/`，`a5508a2a` 前后任务流提交），底层是 `3bda8c7d` 的进程内
  SQLite 排程器，并带 `tests/tasks/test_reminders.py`。**未验证**「创建后重启
  服务仍按时触发」的真实定时端到端。
- **本地语音输入：决定延后。** 需要离线 ASR 模型接入产品进程与设备权限/隐私边界，
  本机仅有开发侧 `local-asr` 技能，未接线。
- **操作前预览：决定延后。** 现有只有确认票据里的文本 `preview` 字段
  （`src/automation/confirm.py:31`、`src/automation/contracts.py:156`），没有面向
  UI 的结构化预览与撤销；且它依赖 D-4 的真实自动化后端同时上线。

**依赖什么。**
- 提醒：持久化调度（进程重启不丢）+ 通知渠道；
- 语音：本地 ASR 模型与设备权限、隐私边界（音频不落盘）；
- 预览：自动化动作计划的结构化渲染 + 审批票据 + 撤销。

**验收标准。**
- 提醒：创建后重启服务仍按时触发，可在 UI 查看/取消；
- 语音：离线识别一段中文音频为文字并可直接发送，音频不外传；
- 预览：高风险动作执行前展示「将做什么、影响范围、如何撤销」，未确认不执行。

## D-4 自动化真实后端（浏览器 / 安卓 / Windows 桌面键鼠）

**决定延后（deferred-by-decision）。** 模块自述「本轮只提供 fake 后端，不接真实键鼠」
（`src/automation/__init__.py:1-6`、`src/automation/gate.py:19`）。
真实键鼠/浏览器/安卓控制涉及权限、误操作破坏性与安全边界，必须与审批、
围栏、急停一起上线，不能单独开。

**依赖什么。**
- 三重闸门（装配 / 后端 / 主体）已存在，需要真实后端实现并仍默认关闭；
- 审批票据与急停（`src/automation/estop.py`）在真实动作路径上验证；
- 操作前预览（D-3）与审计（`src/automation/audit.py`）。

**验收标准。**
- 至少一个真实后端（建议浏览器）在隔离环境完成一次可复现任务；
- 未审批动作一律拒绝，急停 1 秒内生效；
- 每个动作产生审计记录，可回放定位；
- fake 后端保留用于测试，生产默认关闭。

## D-5 30 方向战略落地

**当前状态（2026-11-06 对齐）。** `docs/strategy-30-directions.md` 已完成逐方向核查，结论是
**真有 8 个**（1、8、16、22、23、25、29、30）、**半成品 11 个**（2、3、7、13、
17、20、21、24、26、27、28）、**假实现 4 个**（5 多模态、6 电脑操作、18 教育、
19 创意）、**完全没实现 7 个**（4 科研、9 情报平台、10 数字孪生、11 IoT、
12 机器人、14 空间智能、15 工业自动化），逐条证据见该文档 `:48-86` 矩阵。
文档 `:224-233` 明确声明**没有跑运行时 E2E**；五大中心被判定为「命名即空壳」，
不应拆成五套系统（`:135-147`）。所以本轮只做信息架构归位，不做 30 个方向的
全量工程落地。

**依赖什么。**
- 知识入库/检索注入对话（D-7，`src/knowledge/` 已存在但控制台未接）；
- 反馈持久化（`src/feedback/feedback_service.py:13` 仍是进程内 `dict`）；
- 排程与备份（D-8、D-9）；
- 假实现方向的真实链路（多模态/电脑操作/教育/创意各自有独立的验收标准）。

**验收标准。**
- 「真有 8」要有可操作入口与真实链路验证，不是只过静态检查；
- 「半成品 11」要么接线要么在 UI/文档明确标注未接线；
- 「假实现 4」不得出现在对外能力清单；
- 30 方向矩阵的每一行都能指向代码路径或明确的「不做」理由。

## D-7 知识闭环（入库 UI → 检索 → 对话注入）

**当前状态。** `69db011e` 已把知识中心做成可用的导入/文档/分块预览/检索测试
界面；此后 `79bd69c8`（文档台账、完整分块、事务删除、上传、异步任务）、
`095ecdb9`（相关性门限与拒答阈值）、`8858c77e`（检索无相关内容时跳过注入）、
`82404fdb`（拒答契约与注入门禁测试）继续补齐。`/v1/chat` 通过
`build_knowledge_context` 注入检索上下文（`src/gateway/chat.py:159,185`，
带 `use_knowledge` 开关）。**仍未验证**的是真实 embedding provider 下的端到端
链路（上传→切分→向量→对话引用来源），以及「未配置 provider 时返回 503」的
实际行为。`tests/knowledge/test_documents_admin_api.py` 复跑 **17 passed**
（此前 422 vs 400 的失败已收口，见 requirements T-19）。

**依赖什么。** 可用的 embedding provider（未配置时必须返回 503「未启用」，不得 mock
伪装）；向量模型已就绪但向量库打不开时必须返回 503「向量库不可用」，不能并成一句
「未启用」；对话侧引用来源展示。

**验收标准。** 上传文档 → 真实切分 → 真实 embedding → 落库 → 对话能引用
检索片段并显示来源；未配置 provider 时明确 503 而非假装可用；向量库打不开时明确
503「向量库不可用」，同样不得伪装可用。
**触发条件。** 见 K-1。

## D-8 进程内排程器（定时/循环 Goal）

**当前状态。** `3bda8c7d` 已入库进程内 SQLite 排程器（重试退避、死信、
重启恢复），代码在 `src/tasks/`，单测 `tests/tasks/test_local_scheduler.py`、
`tests/tasks/test_taskiq_queue.py`。**仍未验证**的是「真实定时 Goal 跑满一个
周期并重启恢复」的端到端行为（单测只覆盖调度器本身，不等于 Goal 链路可用）。

**依赖什么。** 调度器接入 Goal 创建入口（当前需要显式调用），SQLite 持久化
调度表、单进程锁、失败重试与审计。
**验收标准。** 创建定时 Goal 后重启服务仍按时触发；同一时刻不重复执行；
失败有重试与审计记录；不引入 Redis/外部队列（保持零运维）。
**触发条件。** 见 K-2。

## D-9 备份 / 恢复 / 导出（一键化）

**当前状态。** `dcf025c3` 已让 CLI 备份命令操作**真实归档**（`src/cli/backup_commands.py`：
SQLite `VACUUM INTO`、归档元数据随包、跨进程可读）；本轮实测命令路径可用。
但 `scripts/ops/backup.py:11` 仍自述「CLI but no production caller wired」，
且**没有**设置页入口、没有恢复流程、没有版本兼容校验 —— 所以只是「CLI 真了」，
不是「一键化可用」。

**依赖什么。** 数据库在线备份一致性、恢复校验、导出格式与权限控制。
**验收标准。** 设置页一键备份 → 产出可校验文件 → 新环境一键恢复到可用状态；
恢复失败可回滚；全过程留审计。
**触发条件。** 见 K-3。

## D-10 产物索引与执行回溯

**当前状态。** `c14ab20c` 增加只读产物索引 CLI 与工作区报表，`82f95450`
把产物索引接入执行链（**可选、默认行为不变**，显式开关才生效），单测
`tests/artifacts/test_artifact_index.py`、`test_artifact_report.py`、
`test_artifact_wiring.py`。**仍未做**：控制台入口、执行回放界面。

**权限面审计（2026-11-07）。** 产物索引只有本机 CLI / 报表入口，HTTP 没有
任何"列出产物索引或按下标下载"的路由（`/v1/artifacts` 返回 404）；真正的产物
下载面是 `/v1/files/content`，已按 `accounts/<owner>/` 分区，跨账号读取返回中性
404。文件列表的 `rel_path` 现为工作区相对路径（含账号前缀），前端回填到
list/read 后仍落在正确账号空间。回归见
`tests/test_cross_account_isolation.py::TestArtifactAccountIsolation`。

**依赖什么。** 执行记录结构化、产物命名与归属、控制台/回放入口（下载面已按账号
分区，索引本机入口不跨账号）。
**验收标准。** 给定目标或执行 ID，能列出其产物、来源步骤与时间；点开可预览；
越权不可见。
**触发条件。** 见 K-3。

---

## 四步用户链路暴露的缺口（本轮实测）

> 来源：`docs/END-USER-GUIDE.md` 的四步走查（静默安装 → 首次启动 → 填 Key →
> 发第一条消息）。以下均为**未修或未复验**的事实，不得当成已完成。

| 编号 | 缺口 | 影响 | 状态 |
|---|---|---|---|
| E-1 | ~~`packaging/stage_payload.py` 的延迟依赖修复尚未提交~~ **已解除**：修复随 `b789d244` 入库（含 `jiter`、`httpx2/httpcore2/truststore` 与运行自检） | — | 已提交（`b789d244`） |
| E-2 | 首次运行向导只保存 Key，不做连通性测试 | Key 无效/模型名错要到发第一条消息才暴露 | 未改产品；文档改为引导去「模型管理 → 测试连接」 |
| E-3 | 本机非 Ollama 的 OpenAI 兼容端点：聊天可用，但 `/v1/models/{id}/test` 仍按 SSRF 拒绝 | 「测试连接」与聊天两套校验不一致，本地 LM Studio 用户会被误判 | 安全相关，本轮未改 |
| E-4 | ~~静默卸载（`/VERYSILENT`）停在“保留数据”确认框~~ **已修**：`installer/liuhao.iss` 的 `CurUninstallStepChanged` 在 `UninstallSilent` 时直接退出，按安全默认保留数据，不再弹确认框 | 批量部署/自动化卸载不再挂住 | 已修（安装器实测 `/VERYSILENT` 卸载可正常结束）；交互式卸载的“删除数据”分支已单独实测 |
| E-5 | 真实厂商 Key 未由本流复验 | 本轮只用本地 OpenAI 兼容桩验证 HTTP 请求路径 | 未验证（网关流报称已用 DeepSeek 实测，但无原始输出） |
| E-6 | `packaging/smoke_payload.py` 接受 MockProvider 通过 | 打包冒烟无法发现真实 provider 依赖缺失（`httpcore2/jiter` 就是这样漏出的） | 未改；真实链路验证在仓库外的 E2E 脚本 |
| E-7 | 前端正在重写，安装包内是当前 `console/dist` | 前端稳定后必须重建安装包，否则界面过时 | 待重建 |
| E-8 | 安装包未做代码签名 | Windows 首次运行提示「未知发布者」，部分杀软误报 | 未做（见 D-1 的签名依赖） |
| E-9 | 真实双击路径未逐字实跑：本轮 E2E 用 `launch_installed.py --skip-wizard --no-browser` 复用向导函数，没有在命令行窗口里逐条键入答案，也没有复验「向导成功后浏览器自动打开」 | 向导提示文案、输入容错与自动开浏览器三处只有代码依据，没有本轮实测输出 | 未验证；组件函数与 UI 可达性已单独验证 |
| E-10 | ~~测试删除护栏在“仓库位于 temp 根下”时整体放行~~ **已修**：`tests/conftest.py` 改为 fail-closed。放行只认两种归属：目录项与解析后目标都在声明的临时根内（`gettempdir` 或显式 `--basetemp`），或仓库内路径经 `allow_repo_deletion` 登记；仓库归属优先于临时根，符号链接两侧都校验，无法确认归属一律拒绝。顺带补上原护栏漏掉的 `os.rmdir` 接口 | 事故记录（修复前）：回归工作树放在 `D:\cache\temp\liuhao-regression` 时，测试清理逻辑真的删掉了 `README.md`、`oss-ecosystem/capabilities/*.yaml`、`src/knowledge/memory.py`，连带 19 failed + 9 errors 全是假失败 | 已修（`392055f7`）。`tests/test_cwd_delete_guard.py` 覆盖：仓库在 temp 根下、无标记目录、链接指向外部、`os.rmdir`、正常 `tmp_path` 清理，整文件 26 passed。同类扫描：全仓无第二处“按路径推断归属”的删除；scripts 里的删除都基于显式 `mkdtemp`/具名目录 |
| E-11 | `tests/kernels/network/test_network.py::TestRouting::test_add_route_and_deliver` 在全量负载下会抖动 | 全量回归 1 次失败（delivered 变 failed）；单跑 2.67s 通过，属回环 HTTP 在满负载下的时序抖动 | 未修；已记录，单跑通过 |
| E-12 | 全量回归基线落后交付 HEAD：跑在 `cc3af30d`，报告时 HEAD 已到 `de1b0d87`（12 个提交） | 全量结果不覆盖期间的 desktop 启动门、升级 API、可观测性、reminder、console 错误读取器修复 | 已补跑这些提交改动的 15 个测试文件（123 passed / 80.85s），但未再跑第二次全量 |
| E-13 | ~~`dist/installer` 为空、清单里的数值对应文件从未存在~~ **已更新**：`dist/installer` 现有 0.1.0 的四项正式产物（`LiuHao-Setup-0.1.0.exe` 34,838,298 字节、`liuhao-0.1.0.zip` 2,529,546 字节、`SHA256SUMS.txt`、`DELIVERY.txt`）。这些是**上一版**交付物，不是本次待转正的最终包 | 交付清单不再 404；但读者可能把 0.1.0 产物误当成本次构建结果 | 最终包（本次同源构建）验收通过并原子替换后，必须回填路径/字节/SHA256 + 对应源码提交，并重跑同一 E2E 绑定到该提交 |
| E-14 | **手机端真机未验证**：服务侧 LAN `192.168.31.171:8000` 返回 200，但手机侧「打开 → 登录 → 发消息」从未跑过。登录阻塞（`LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 未设置时身份行全被拒）**已改**：`src/common/integrity_key.py` 在安装版启动器与源码启动器首次启动时自动生成并注入密钥，落盘数据目录 `config/integrity.key`（POSIX 0600 / Windows `icacls` 仅当前用户），覆盖全新安装、旧数据升级（无签名行补签）、密钥丢失（明确恢复提示，不静默）三条路径；回归 `tests/test_identity_key_bootstrap.py` | 手机侧仍无设备；「密钥不再阻塞」不等于「手机可登录」 | **部分关闭**：密钥自举已实现并有回归（18 passed，含向导/启动器）；**真机登录+发消息仍为未验证**。关闭条件不变：真机在同一 Wi-Fi 完成一次登录+发消息并留存日志/截图 |
| E-15 | **交叉验证 `3fa88be0` 的 C3/C12 FAIL、31 failed**：账号/知识切片提交了测试与调用方，却把被调用实现（`src/ai/model_router.py` 的 `visible_to`、`src/gateway/ai_management.py` 的 `GoalViewer` 与结构化错误体）留在工作区 | 27 条失败 + 1 条采集错误同根因；同一 HEAD 上测试必红；该轮报告只能算**过程凭证，不是交付凭证** | **未关闭（归属账号/知识切片）**。关闭：两切片提交实现 → 干净 worktree 重跑 `verify_readiness.py --full`，C3/C12 转 PASS。证据 `docs/evidence/cross-verify-3fa88be0.md` |
| E-16 | **最终冻结 HEAD 没有交付级全量凭证**：现有全量是 `bb6b654c` 基线（7458 passed）与 `3fa88be0` 过程凭证（C3/C12 FAIL），均不是最终 HEAD | `RELEASE READY=False`；不能据此对外宣布可交付 | **已完成**（2026-11-06）：冻结 hash `72fd9b17` 上 `--full` 得 `PASS=10 FAIL=0 BLOCKED=1(C11) NOT_VERIFIED=1(C9) ERROR=0`。裁决后 `RELEASE READY=False` 的理由**只剩 C9**（桌面分发形态无真实 CD，属形态差异）；C11 已裁决（[ADR-HC01](adr/ADR-HC01-baseline-binding.md)），机器 `check_hc01` 仍按冻结门设计打印 `BLOCKED`。证据 `docs/evidence/regression-baseline-coverage-2026-11-06.md` §8 |
| E-17 | **打包白名单必须覆盖「方法体内延迟导入」的依赖**：`src/ai/providers.py` 在 `provider.chat()` 内部自建 `httpx.Client`，启动图与 openai 自带的 `httpx2` 都覆盖不到；同类的还有 anthropic / python-docx(+lxml) / pypdf / sqlalchemy(+greenlet) / taskiq。它们都是 `requirements.txt` 的运行期硬依赖，缺了不是优雅降级，而是真实使用时 `ModuleNotFoundError` | 安装包在干净机器上「对话 / 目标拆解 / 知识导入 / 任务代理」整条链路失败；冒烟测试用 MockProvider 发现不了（见 E-6） | **已修**（`3ec7d755`、`b5ee295c`）。**发现方式**：真 Key 对话直接报 `No module named 'httpx'`。**防法**：`_MEASURE_CODE`、`EXPLICIT_DISTS`、`_validate_runtime` **三处必须同步** —— 只加白名单不加测量/自检代码，构建期不会真的把包带进 payload。**实测数字**：构建期运行时自检 58→63 个发行包（httpx/httpcore），补齐全量后 63→72 个发行包；离线运行时实测 `sqlalchemy 2.0.52`、`anthropic 1.3.0` 等均可 import |
| E-18 | **`src/upgrade/startup_probe.py` 把 `/openapi.json` 当健康兜底路径**（原实现：对 `health_paths` 中每条路径只发一次请求、单请求超时 1.0s、采纳首条 2xx 即退出，冷启动首请求常超过 1.0s，于是 `/v1/health` 超时后直接采纳 `/openapi.json`，把“路由已挂载”当“lifespan/数据库已就绪”） | 安装后自检的“服务正常”结论可能来自错误探针；`http_status=200` 成立但 `health_path` 暴露真相 | **已修（`22c2e867`）**。**改法**：① 优先路径改为**轮次重试**：一轮 = 所有优先路径各一次（默认 `/v1/health`、`/health`、`/healthz`），单请求超时保持 1.0s（不因冷启动砍小），轮间退避 0.25s；优先阶段同时受 `startup_timeout_seconds`（90s）与 `health_probe_budget_seconds`（90s）约束，约合 27 轮全超时，轮次与预算均写在 `StartupCheckConfig` 注释里；② 只有连续 2 轮**所有优先路径都明确返回 404/405**（`health_fallback_after_missing_rounds`）才允许尝试 `/openapi.json`，超时、连接失败、5xx 一律继续重试，绝不提前降级；③ 兜底成功时 `StartupReport.health_path_is_fallback=True`，并进入 `to_dict()`、`service_detail` 与自检日志（“route table only, lifespan not proven”），不再当作普通健康通过；④ 超时文案带上实际重试轮次。**测试**：`tests/test_upgrade_selfcheck.py` 23 passed（含新增“前两次超时、第三次 200 ⇒ 仍报 `/v1/health`”与“全部 404 ⇒ 兜底且 flagged”）；`tests/test_upgrade_api.py` 同跑 35 passed。**剩余**：仅在自检沙箱内验证，未在真实安装包冷启动路径上复测（安装器/打包由另一路负责，本轮未动 `installer/`、`packaging/`） |
| E-19 | **未设置 `HUMAN_IDENTITIES_INTEGRITY_KEY` 时，身份页警告泄露内部细节**（关联 E-14）：`GET /v1/identity/principals` 的 `warnings` 原样返回环境变量名、注册表绝对路径、`fail-closed` / `HC-11` 代号与 `scripts/register_human_identity.py`；控制台 `plainNote` 见“整段英文”就把警告替换成与密钥无关的权限串说明，用户既看不到真相、又被说错 | 首启/密钥丢失时普通用户看到机器串或错误解释；真机登录前的第一印象受损 | **已修（`8c04bbcc`）**。警告改为中性中文：① 未启用完整性校验 → “已登记账号暂时不会被接纳，这不等于「没有账号」，启用完整性校验并重新登记后即可恢复”；② 被拒收行 → “有 N 个已登记账号未通过完整性校验，因此不在清单中：…”；③ 启动时登记被拒 → 只给结论，原始原因留在诊断日志（内核已 `logger.error`）。机器可读字段 `registry.integrity_key_env` 保留供诊断工具，界面不渲染。回归 `tests/gateway/test_account_wording.py::test_registry_warnings_are_plain_chinese_when_integrity_key_is_unset`（含中文用户名+空格路径、含堆栈的拒绝原因；纯中文、无机器串、无层级词）；真实 TestClient 复测（临时数据目录、未设密钥）warnings 已为中文。**剩余**：未在真实安装包上删除密钥后复测首启（安装器由另一路负责） |
| E-20 | **中文/空格/非默认盘符路径矩阵：运行时链路已过，安装器落盘与卸载仍未验证**（本轮用「正式 payload 结构 + `scripts/launch_installed.py` + 独立数据目录 + 独立端口」模拟安装版；未运行安装器，遵守安装器由另一路负责的边界） | 用户把程序装到中文/空格路径、数据目录在中文用户名或 D:/E: 盘时可能起不来、写错位置或升级失败 | **部分完成（2026-11-07 实测）**。已实测 4 组并全部通过：① 中文用户名（`LOCALAPPDATA=C:\Users\张三\AppData\Local`，数据目录由默认规则推导）；② 中文+空格安装路径 + 中文数据目录；③ E: 盘 + 中文数据目录；④ D: 盘 + 中文数据目录。每组：首启 ready 4.782–5.297s；控制台 `GET /` 200；注册/登录 200；保存模型配置 200 且 `model_registry.sqlite3` 落在数据目录；`python -m src.upgrade check` 退出码 0 / `up_to_date`；停止后程序目录无 `data/`、无 `.pyc`，密钥/凭据/身份库/模型库/日志/升级状态全部在数据目录（19 个文件）。**未验证（不写成通过）**：a) 真实安装器在中文/空格路径落盘、快捷方式目标、写入开始菜单/卸载登记——复现条件：用 `dist\final-72fd9b17\installer` 以 `/DIR=<中文 空格路径>` 静默安装到临时目录后核对；b) 卸载后程序目录/数据目录残留（含“删除数据”选项）——同上安装后执行卸载并分别列出两目录；c) 浏览器自动打开的可视化确认（本轮以控制台 HTTP 200 代替，桌面交互未肉眼验证）。**已排除的假缺陷**：本轮模拟初次漏拷根级 `error_types.py` 导致启动失败；正式 payload `dist\final-72fd9b17\payload\app\error_types.py` 存在，补齐后四组全过，非产品缺陷 |
| E-21 | **8GB / 16GB 低配内存没有对应硬件，未实测**（本机为 32GB / Xeon E5-2673 v3 12 核 24 线程） | 无法给出低配机器的真实冷启动与首次对话数字；只能外推，不能当验收结论 | **部分完成（2026-11-07 实测）**。本机 3 次冷启动（独立数据目录、独立端口、mock 模型）：ready 4.875–5.031s（`/v1/startup` 自报 4.205–4.302s）；ready 后控制台首字节 0.516–0.562s；首次对话 2.968–3.125s（服务端 `LiuHaoAssistant.chat` 日志 56ms，其余为首次懒加载）；整进程树 RSS 峰值 212.2–212.9MB，网关单进程 176.2–177.0MB。**外推（未验证）**：内存侧 213MB 远低于 8GB，不是瓶颈；瓶颈更可能在 CPU/磁盘冷读，24 线程数字不能外推到 2–4 核，故不给 8GB/16GB 的“预计秒数”当结论。**要补的观测项**：① 冷启动到 `/v1/startup` ready 的分阶段耗时（`stages`）；② 控制台首字节；③ 首次对话（区分懒加载与服务端耗时）；④ 网关 RSS/整树 RSS 峰值与系统可用内存最低点；⑤ 是否发生换页/磁盘 IO 抖动、单核 CPU 占用；⑥ 8GB 上 1 会话 + 控制台并发，16GB 上 3 会话并发。复现条件：在 8GB（2–4 核）与 16GB（4–8 核）机器上按本轮同一脚本（正式 payload + 独立数据目录 + 独立端口）各跑 3 次 |
| E-22 | **安装版系统层体验（托盘 / 开机自启 / 后台驻留 / 单实例）在 0.1.1 新启动器上首次实测**（用正式 payload 结构 + 桌面壳 `scripts/liuhao_desktop.py` + 独立数据目录 + 独立端口模拟安装版；遵守「不动业务逻辑、不动前端视觉」的边界） | 第二次双击会起第二个网关并报端口冲突；托盘右键没有日志入口；托盘退出被误报成「未优雅收尾」——三项都会让普通用户以为程序坏了 | **已修 3 个缺陷（`3d3af233`）**：① 桌面壳此前没有自身单实例，第二次启动会再建一个托盘窗口、再让网关锁失败；改为按数据目录派生命名互斥量（`Local\LiuHaoDesktopShell_<sha1>`，`ERROR_ALREADY_EXISTS`），第二实例经 `RegisterWindowMessage("LiuHao.ShowStatusWindow")` 唤起已有窗口，找不到窗口则打开 `run/instance.json` 公布的 url，实测 0.91–1.22s 内以退出码 0 结束，托盘数 / 监听数均保持 1；② 托盘窗口类名原为随机 uuid、跨进程找不到，改为稳定派生 `LiuHaoTray_<sha1(数据目录)>`，并补右键「打开日志目录」（1005，实测新增 1 个资源管理器窗口）；③ `stop_process` 只读 `desktop-gateway.log`（WARNING+），而 uvicorn 的 `Application shutdown complete` 写在数据目录 `logs/liuhao.log`，导致托盘退出被误报「未确认优雅收尾」；改为同时读应用日志，实测 `graceful=True / shutdown_确认=True / 0.67s`。**其余实测**：关窗后窗口隐藏、网关继续服务（`/v1/startup` 200、python 子进程 1、托盘 1）；托盘退出后端口释放、WAL 清空、重启后账号/会话仍在；只强杀托盘时网关因 `LIUHAO_PARENT_PID` 自行退出，再次启动正常（无残留锁）；自启注册项写入 `HKCU\...\Run`（命令指向 `runtime\pythonw.exe scripts\liuhao_desktop.py --autostart`），在 `cwd=C:\Windows\System32` + 仅系统环境变量下等效启动成功，关闭后注册项删除。回归 `tests/desktop/test_desktop_system_layer.py` 新增 6 条（互斥量/类名稳定性、真实托盘窗口唤起、交接兜底、日志菜单、停止确认来源）。**未验证（不写成通过）**：a) 真实重启机器（只做了等效注册项启动）；b) 真实安装器快捷方式（`{app}\LiuHao.bat`）在已有实例运行时的完整路径——工具终端没有交互 stdin，控制台启动器在首次向导处被 `[FAIL] 首次运行需要在命令行窗口里完成向导` 阻断，未走到 `start_liuhao` 的「已在运行」分支；c) 通知区图标的人工可视化确认（程序侧已验证窗口/菜单消息/唤起可达，F-4 仍保持未验证）；d) `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 与实际密钥不一致时的英文原始提示（`REFUSING registry row ... Remedy: set that variable ...`，含环境变量名与绝对路径）与 `LiuHao.bat` 无论何种结局都打印 `LiuHao stopped. Press any key to close.`——两者分属身份内核与 packaging 生成物，超出本任务边界，只登记不改。**普通用户会不会被卡住**：不会。单实例、关窗驻留、托盘退出、自启、强杀恢复五条主路径都能走完；剩余风险只在「双击快捷方式时是否停在向导」这一未验证路径，以及关控制台窗口时那句英文提示的观感 |
| E-23 | **0.1.0 的 `/v1/health` 把版本号报成 `1.0.0`**（实测：0.1.0 安装实例自报 `1.0.0`，与 `LIUHAO_RELEASE.json`/`pyproject.toml` 的 `0.1.0` 不符）。原因是旧版 health 处理器读错了版本来源，属于 **0.1.0 自身缺陷**，不是升级链引入的回归 | 对运行中的 0.1.0 做版本探测/故障诊断会拿到错误版本，可能误判「装错了版本」或让升级前置判断失真；升级到 0.1.1 后该值正确 | **已修（0.1.1）**：health 改为报告真实运行版本，回归随升级/健康检查测试覆盖。**0.1.0 不回补**：已发布的 0.1.0 二进制不可改，回补需要重发旧版本号，收益为零且会让版本语义更乱；用户升级到 0.1.1 即消失。后续如需支持「识别 0.1.0 旧实例」，只能按已知缺陷解释，不得据此判定升级失败 |

---

## 看起来有、实际不生效（最容易误判进度）

| # | 现象 | 真相 | 证据 |
|---|---|---|---|
| R-1 | 顶层 61 个 `plugins/` 目录 | 插件管理器默认只扫 `src/plugins`，顶层目录运行时不加载；主程序只注册 builtin | `src/plugins/manager.py:48`；实测 61 个目录 |
| R-2 | `solutions/industry/` 38 个行业方案 | 只有 `tests/test_all_industry_solutions.py` 导入，`src/` 无引用，产品运行时不加载 | `tests/test_all_industry_solutions.py:32+`；`src/` 检索无命中 |
| R-3 | `src/automation/` 体系完整 | 后端只有 `fake.py`，三重闸门默认关闭，无真实浏览器/键鼠/安卓执行 | `src/automation/backends/`、`src/automation/gate.py:19`、`src/automation/__init__.py:1-6` |
| R-4 | ~~「知识中心」只是端点清单~~ **已解除** | `69db011e` 后已是导入/文档/分块预览/检索测试界面，且 `/v1/chat` 注入检索上下文；剩余是真实 embedding E2E 未验证 | `apps/console/console/src/pages/Directory.tsx`、`src/gateway/chat.py:159` |
| R-5 | 反馈服务有接口 | 进程内 `dict` 存储，重启即丢 | `src/feedback/feedback_service.py:13` |
| R-6 | 控制台 dist 已构建 | 源码改动后是否重建未验证 | `docs/strategy-30-directions.md:232` |

---

## 明确不做清单（不是延后，是不做）

来源：`docs/strategy-30-directions.md:188-220`。以下六类**不进入路线图**，
除非重新裁决并给出安全/合规前提：

1. **电脑自主操作**（无审批的任意桌面/浏览器操作）；
2. **任意仓库自动改代码 / 自动提 PR**；
3. **金融交易**；
4. **外部 KYC**；
5. **全网常驻采集**；
6. **机器人 / IoT / 工业执行**（依赖物理硬件与现场责任）。

## 五大战略中心的决策

只做**信息架构、命名与权限边界**，不拆五套后端。理由：现状是 1 进程 1 主库
+ 至少 9 个 SQLite，与「双击即用、零运维」冲突；且中心边界不干净（知识被执
行链消费、安全拦执行、进化评估执行）。详见
`docs/strategy-30-directions.md:42-46`、`:87-147`。若未来改为多服务，必须先
解除「零运维」承诺并给出迁移与运维方案（触发条件 K-6）。


## D-6 情报中心后续阶段（漏洞查询、缺口对照、更新提醒）

**决定延后（deferred-by-decision）。** `src/intel/` 本轮只做发现、许可证判定与提案，漏洞查询接口
**明确未接线**：`src/intel/scanner.py:298`、`:428` 记录
`error_reason="advisory_endpoint_not_wired_in_this_round"`。`src/intel/` 与
`docs/oss-radar/`（8 份）现已提交跟踪，但更新提醒与缺口对照仍无调度与通知。

**依赖什么。**
- GitHub Advisory / OSV 真实查询与限流配额（`src/intel/github_client.py` 已有客户端骨架）；
- 与现有能力的缺口对照（`src/intel/capabilities.py`）接入真实能力注册表；
- 定时扫描与通知（依赖 D-3 的调度能力）。

**验收标准。**
- 对 ≥1 个真实依赖仓库跑出真实 advisory 结果（非占位）；
- 缺口对照输出「已有 / 缺什么 / 建议是否采用」，unknown 许可证仍拒绝；
- 版本更新与漏洞变化能生成可订阅提醒；
- 报告可追溯到来源、版本、许可证、扫描时间。

---

## 验收提醒

- 以上延后项**不得**在交付报告里被写成「已完成」；
- 若某项被提前捡起，先更新本文件的状态与验收标准，再动代码；
- 需求逐条状态以 [需求追溯表](requirements-traceability.md) 为准。

---

## 故障面未执行边界（2026-11-06，**状态：未验证**）

> 2026-11-06 补测的五类故障面里，以下场景**没有真跑**（原因：共享机器 / 无独立可写满卷 /
> 前端与账号树正被另一路修改）。它们是 **未验证**，不是已通过项；交付报告里不得写成
> 「已验证」。现有替代证据见 [fault-surfaces-2026-11-06.md](evidence/fault-surfaces-2026-11-06.md) §7。

| 编号 | 未执行场景 | 原因 | 现有替代证据 | 状态 |
|---|---|---|---|---|
| F-1 | 真正 OS 睡眠 / 唤醒 | 共享机器，会中断其它正在跑的验收与切片 | 网关进程 suspend 8s 后 `/v1/health` 前后各 200（0ms）；端口未丢 | **未验证** |
| F-2 | 真拔网线 / 切 Wi-Fi / DNS 变更 | 同上 | 本地假服务断开 `ConnectTimeout` 2000ms；半开黑洞 `ReadTimeout` 2030ms；恢复后 16ms 自愈 | **未验证** |
| F-3 | 真实磁盘满 `ENOSPC` | 本机无独立可写满卷，格式化会破坏环境 | 注入 `OSError(ENOSPC)`：审计旧文件不变、内存事件回滚 | **未验证** |
| F-4 | 真实托盘图标点击（GUI） | 无头/远程会话无法点托盘 | `GatewayService.start()` 在 CLI 持锁时真实拒绝且不拉子进程 | **未验证** |
| F-5 | 两个真实浏览器窗口同时发消息 E2E | 前端/账号树正由另一路修改，按 owner 要求不动 | 同进程双线程并发 400/400 条、重复 0；跨进程锁与升级互斥有真进程用例 | **未验证** |
| F-6 | 真实全局系统时钟跳变 | 改全局时钟会影响同机其它进程 | 进程内锚点模拟前进/后退，覆盖 JWT/吊销/审计链；非改系统时钟 | **未验证** |

**什么算关掉这些条目**：F-1/F-2 需要在可独占的机器上真睡眠、真断网后各跑一次端到端；
F-3 需要一块可写满的独立卷；F-4 需要在有桌面会话的机器上真实点击托盘；F-5 需要前端冻结后
开两个浏览器窗口做一次并发发送；F-6 需要在一台可牺牲的机器上真实改系统时钟并验证登录态。
在此之前，状态保持 **未验证**。

---

## 索引门禁用裸文件名匹配（2026-11-08，**状态：已知未修**）

`scripts/verify_docs_index_coverage.py` 判断「某篇文档是否被 `docs/README.md` 收录」时
**只比文件名，不比路径**。后果：`docs/a11y-keyboard-audit-20261108/report.md` 与
`docs/a11y-sr-hc-keyboard-20261108/report.md` 因为撞上 `docs/ui-audit-20261106/report.md`
被判定「已收录」，实际从未进过索引。本轮已把这 3 篇同名文件显式列进 §4.20，
但**匹配规则本身没改**。

**为什么不顺手改。** 改成全路径匹配会一次性暴露更多历史漏项（同名的
`README.md`、`report.md`、`index.md` 类文件），属于独立事项，需要单独一轮核对与补录；
混在「修门禁误报」这一批里做，等于把一个可验证的小改动变成一次无法复核的大翻修。

**什么算关掉它。** 覆盖率判定改为「规范化后的仓库相对路径」匹配，且改完后
`verify_docs_index_coverage` 在补录完成前**必须红**（证明它真的开始看路径了）。

---

## 满盘 503 响应缺 `correlation_id`（2026-11-08，**状态：已知未修**）

磁盘满（`ENOSPC`）时身份/撤销库不可用，网关返回 503，但响应体**只有 `code` / `message` /
`retry_after`，缺 `correlation_id`**，用户报障时无法把「这一次 503」与日志里的那一行对上。
同一套错误模型里其他 5xx 都带该字段，属遗漏而不是设计。

**排期。** 与 `main.py` 其后批次一起补（统一 503 响应体 + `Retry-After` 头），
不单独开批。验收标准：构造一次 `ENOSPC`，503 body 含 `code` / `message` /
`correlation_id` / `retry_after` 四字段，且 `correlation_id` 能在日志中检索到同值。

