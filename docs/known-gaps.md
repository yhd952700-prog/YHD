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
| D-10 | 产物索引与执行回溯 | 部分完成（`c14ab20c` + `82f95450`；未做权限过滤/UI） |

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

**依赖什么。** 可用的 embedding provider（未配置时必须返回 503「未启用」，
不得 mock 伪装）；对话侧引用来源展示。

**验收标准。** 上传文档 → 真实切分 → 真实 embedding → 落库 → 对话能引用
检索片段并显示来源；未配置 provider 时明确 503 而非假装可用。
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
`test_artifact_wiring.py`。**仍未做**：按权限过滤（越权不可见）、控制台入口、
执行回放界面。

**依赖什么。** 执行记录结构化、产物命名与归属、权限过滤（只给有权限的人看）。
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
| E-4 | 静默卸载（`/VERYSILENT`）停在“保留数据”确认框 | 批量部署/自动化卸载会挂住 | 未修；交互式卸载未验证 |
| E-5 | 真实厂商 Key 未由本流复验 | 本轮只用本地 OpenAI 兼容桩验证 HTTP 请求路径 | 未验证（网关流报称已用 DeepSeek 实测，但无原始输出） |
| E-6 | `packaging/smoke_payload.py` 接受 MockProvider 通过 | 打包冒烟无法发现真实 provider 依赖缺失（`httpcore2/jiter` 就是这样漏出的） | 未改；真实链路验证在仓库外的 E2E 脚本 |
| E-7 | 前端正在重写，安装包内是当前 `console/dist` | 前端稳定后必须重建安装包，否则界面过时 | 待重建 |
| E-8 | 安装包未做代码签名 | Windows 首次运行提示「未知发布者」，部分杀软误报 | 未做（见 D-1 的签名依赖） |
| E-9 | 真实双击路径未逐字实跑：本轮 E2E 用 `launch_installed.py --skip-wizard --no-browser` 复用向导函数，没有在命令行窗口里逐条键入答案，也没有复验「向导成功后浏览器自动打开」 | 向导提示文案、输入容错与自动开浏览器三处只有代码依据，没有本轮实测输出 | 未验证；组件函数与 UI 可达性已单独验证 |
| E-10 | ~~测试删除护栏在“仓库位于 temp 根下”时整体放行~~ **已修**：`tests/conftest.py` 改为 fail-closed。放行只认两种归属：目录项与解析后目标都在声明的临时根内（`gettempdir` 或显式 `--basetemp`），或仓库内路径经 `allow_repo_deletion` 登记；仓库归属优先于临时根，符号链接两侧都校验，无法确认归属一律拒绝。顺带补上原护栏漏掉的 `os.rmdir` 接口 | 事故记录（修复前）：回归工作树放在 `D:\cache\temp\liuhao-regression` 时，测试清理逻辑真的删掉了 `README.md`、`oss-ecosystem/capabilities/*.yaml`、`src/knowledge/memory.py`，连带 19 failed + 9 errors 全是假失败 | 已修（`392055f7`）。`tests/test_cwd_delete_guard.py` 覆盖：仓库在 temp 根下、无标记目录、链接指向外部、`os.rmdir`、正常 `tmp_path` 清理，整文件 26 passed。同类扫描：全仓无第二处“按路径推断归属”的删除；scripts 里的删除都基于显式 `mkdtemp`/具名目录 |
| E-11 | `tests/kernels/network/test_network.py::TestRouting::test_add_route_and_deliver` 在全量负载下会抖动 | 全量回归 1 次失败（delivered 变 failed）；单跑 2.67s 通过，属回环 HTTP 在满负载下的时序抖动 | 未修；已记录，单跑通过 |
| E-12 | 全量回归基线落后交付 HEAD：跑在 `cc3af30d`，报告时 HEAD 已到 `de1b0d87`（12 个提交） | 全量结果不覆盖期间的 desktop 启动门、升级 API、可观测性、reminder、console 错误读取器修复 | 已补跑这些提交改动的 15 个测试文件（123 passed / 80.85s），但未再跑第二次全量 |
| E-13 | 交付清单曾把临时构建的数值写到 `dist/installer/LiuHao-Setup-0.1.0.exe`，该路径下从未存在该文件；当前 `dist/installer` 为空。2026-11-05 的安装版 E2E 实际安装的是 `D:\cache\temp\liuhao-e2e\build\installer\LiuHao-Setup-0.1.0.exe`（34,199,800 / `314839ED…`），其 payload `scripts/launch_installed.py`（blob `a90272cf…`）不属于任何提交 | 清单点击即 404；E2E 证据的行为结论仍成立，但对应安装器无法从任何提交复现，不能当作“最终包已验证”的证据 | 已更正为「无有效安装包（待最终包回填）」。最终包出来后必须回填路径/字节/SHA256 + 对应源码提交，并重跑同一 E2E 绑定到该提交 |
| E-14 | **手机端真机未验证**：服务侧 LAN `192.168.31.171:8000` 返回 200，但手机侧「打开 → 登录 → 发消息」从未跑过；且 `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 未设置时身份行全被拒 ⇒「无人可登录」 | 普通用户换机/手机访问会直接卡在登录；T-17 的「仅服务侧已验证」不能当手机可用 | **未验证**。关闭：安装版启动器自动生成并注入该密钥；用真机在同一 Wi-Fi 完成一次登录+发消息并留存日志/截图 |
| E-15 | **交叉验证 `3fa88be0` 的 C3/C12 FAIL、31 failed**：账号/知识切片提交了测试与调用方，却把被调用实现（`src/ai/model_router.py` 的 `visible_to`、`src/gateway/ai_management.py` 的 `GoalViewer` 与结构化错误体）留在工作区 | 27 条失败 + 1 条采集错误同根因；同一 HEAD 上测试必红；该轮报告只能算**过程凭证，不是交付凭证** | **未关闭（归属账号/知识切片）**。关闭：两切片提交实现 → 干净 worktree 重跑 `verify_readiness.py --full`，C3/C12 转 PASS。证据 `docs/evidence/cross-verify-3fa88be0.md` |
| E-16 | **最终冻结 HEAD 没有交付级全量凭证**：现有全量是 `bb6b654c` 基线（7458 passed）与 `3fa88be0` 过程凭证（C3/C12 FAIL），均不是最终 HEAD | `RELEASE READY=False`；不能据此对外宣布可交付 | **待做**。关闭：四路停止提交 → 记下最终 hash → `--full` 跑完 → `RELEASE READY=True`（C9/C11 的既定判定事实照实写） |

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

