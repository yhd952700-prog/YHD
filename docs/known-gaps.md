# Known gaps

> 需求逐条追溯（哪些做了、哪些只是文档、哪些延后）见
> [需求追溯表](requirements-traceability.md)。本文件负责登记「已明确延后、
> 不得遗忘」的事项；两者互补，缺一不可。

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

| 编号 | 事项 | 状态 |
|---|---|---|
| D-1 | 自升级 + 受控自改代码 | 延后 |
| D-2 | 管家主面板（一句话 → 分派员工 → 结果汇总） | 延后（仅有对话入口） |
| D-3 | 三个快效技能（定时提醒、本地语音输入、操作前预览） | 延后（预览仅有确认票据雏形） |
| D-4 | 自动化真实后端（浏览器 / 安卓 / Windows 桌面键鼠） | 延后（当前仅 fake 后端） |
| D-5 | 30 方向战略落地 | 部分落地，其余延后 |
| D-6 | 情报中心后续阶段（漏洞查询、缺口对照、更新提醒） | 延后（advisory 接口本轮未接线） |
| D-7 | 知识闭环（入库 UI + 检索 + 对话注入） | 未做 |
| D-8 | 进程内排程器（定时/循环 Goal） | 未做 |
| D-9 | 备份 / 恢复 / 导出（一键化） | 未做（主线程正在改，以最终状态为准） |
| D-10 | 产物索引与执行回溯 | 未做 |

## D-1 自升级 + 受控自改代码

**为什么延后。** 让产品改自己的代码并热更新，是最高风险能力：一旦被提示注入
或错误模型输出驱动，等于把「已被审计约束的系统」换成「能重写审计约束的
系统」。先在安全侧补齐签名、审批、回滚与沙箱，再谈自动改码。仓库中目前
**没有任何实现**（全仓库检索 `self[-_]update` / `self[-_]upgrade` /
`auto[-_]update` / `自升级` 均无代码命中）。

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

**为什么延后。** 现状里「管家」只是底栏的对话入口（`apps/console/console/src/components/Shell.tsx:161-166`），
后端有员工管理与目标执行（`src/gateway/ai_management.py:710-767`、
`src/kernels/execution/__init__.py:634`），但**没有**「一句话自动建目标、
拆解、分派、汇总并回话」的编排层与汇总界面。做半套（只改文案）会变成假功能，
所以整项延后。

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

**为什么延后。** 三个技能各自依赖不同的外部能力，本轮没有一条完整链路：
定时提醒需要持久化调度器（当前无产品级实现，全仓库仅合规文案里出现
`security_reminders`）；本地语音输入需要离线 ASR 集成（本机开发侧有
`local-asr` 技能，但未接入产品进程）；操作前预览只有确认票据里的文本
`preview` 字段（`src/automation/confirm.py:31`、`src/automation/contracts.py:156`），
没有面向 UI 的预览与撤销。

**依赖什么。**
- 提醒：持久化调度（进程重启不丢）+ 通知渠道；
- 语音：本地 ASR 模型与设备权限、隐私边界（音频不落盘）；
- 预览：自动化动作计划的结构化渲染 + 审批票据 + 撤销。

**验收标准。**
- 提醒：创建后重启服务仍按时触发，可在 UI 查看/取消；
- 语音：离线识别一段中文音频为文字并可直接发送，音频不外传；
- 预览：高风险动作执行前展示「将做什么、影响范围、如何撤销」，未确认不执行。

## D-4 自动化真实后端（浏览器 / 安卓 / Windows 桌面键鼠）

**为什么延后。** 模块自述「本轮只提供 fake 后端，不接真实键鼠」
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

**为什么延后。** `docs/strategy-30-directions.md` 已完成逐方向核查，结论是
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

**为什么延后。** 后端 chunk→embedding→向量入库是真链路，但控制台的「知识中心」
只是 OpenAPI 端点清单（`apps/console/console/src/pages/Directory.tsx:489`），
`/v1/chat` 与 AgentRuntime 都没有注入检索结果。半套 UI 会让人以为知识库已可用，
所以整项延后。

**依赖什么。** 可用的 embedding provider（未配置时必须返回 503「未启用」，
不得 mock 伪装）；控制台知识入库/检索界面；对话侧检索注入与引用来源展示。

**验收标准。** 上传文档 → 真实切分 → 真实 embedding → 落库 → 对话能引用
检索片段并显示来源；未配置 provider 时明确 503 而非假装可用。
**触发条件。** 见 K-1。

## D-8 进程内排程器（定时/循环 Goal）

**为什么延后。** 当前只有审计检查点调用（`src/reliability/checkpointer.py`），
没有 taskiq/APScheduler 的生产调用方。定时能力要先解决「重启不丢」「不漏跑」
「并发不重复」三个问题。

**依赖什么。** SQLite 持久化调度表、单进程锁、失败重试与审计。
**验收标准。** 创建定时 Goal 后重启服务仍按时触发；同一时刻不重复执行；
失败有重试与审计记录；不引入 Redis/外部队列（保持零运维）。
**触发条件。** 见 K-2。

## D-9 备份 / 恢复 / 导出（一键化）

**为什么延后。** `scripts/ops/backup.py:11` 自述「CLI but no production caller
wired」；备份是运维能力，接线要与设置页、恢复流程、版本兼容一起设计。
**注意：主线程正在改这块，最终状态以收尾时的实际代码为准，本行不得按旧结论
判断。**

**依赖什么。** 数据库在线备份一致性、恢复校验、导出格式与权限控制。
**验收标准。** 设置页一键备份 → 产出可校验文件 → 新环境一键恢复到可用状态；
恢复失败可回滚；全过程留审计。
**触发条件。** 见 K-3。

## D-10 产物索引与执行回溯

**为什么延后。** 文件接口只读浏览（`src/gateway/files.py`），没有「从目标/执行
反查产物」的索引，也没有执行回放。做索引需要先稳定执行记录与产物落盘规则。

**依赖什么。** 执行记录结构化、产物命名与归属、权限过滤（只给有权限的人看）。
**验收标准。** 给定目标或执行 ID，能列出其产物、来源步骤与时间；点开可预览；
越权不可见。
**触发条件。** 见 K-3。

---

## 看起来有、实际不生效（最容易误判进度）

| # | 现象 | 真相 | 证据 |
|---|---|---|---|
| R-1 | 顶层 61 个 `plugins/` 目录 | 插件管理器默认只扫 `src/plugins`，顶层目录运行时不加载；主程序只注册 builtin | `src/plugins/manager.py:48`；实测 61 个目录 |
| R-2 | `solutions/industry/` 38 个行业方案 | 只有 `tests/test_all_industry_solutions.py` 导入，`src/` 无引用，产品运行时不加载 | `tests/test_all_industry_solutions.py:32+`；`src/` 检索无命中 |
| R-3 | `src/automation/` 体系完整 | 后端只有 `fake.py`，三重闸门默认关闭，无真实浏览器/键鼠/安卓执行 | `src/automation/backends/`、`src/automation/gate.py:19`、`src/automation/__init__.py:1-6` |
| R-4 | 「知识中心」页面存在 | `Directory.tsx` 只是端点清单，不是知识库管理；`/v1/chat` 未注入检索 | `apps/console/console/src/pages/Directory.tsx:489` |
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

**为什么延后。** `src/intel/` 本轮只做发现、许可证判定与提案，漏洞查询接口
**明确未接线**：`src/intel/scanner.py:298`、`:428` 记录
`error_reason="advisory_endpoint_not_wired_in_this_round"`。另外 `src/intel/`
与 `docs/oss-radar/` 当前均未被提交跟踪，更新提醒与缺口对照尚无调度与通知。

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
