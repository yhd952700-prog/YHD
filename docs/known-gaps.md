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

**为什么延后。** `docs/strategy-30-directions.md` 已逐项判定「真有 / 半成品 /
仅文档」，并明确五大中心不应拆成五套系统（`:135-144`）。其中
Knowledge Universe、Evolution Lab 被判定为「命名即空壳」，只能先补能力再谈
中心。因此本轮只做信息架构归位，不做 30 个方向的全量工程落地。

**依赖什么。**
- 知识入库/检索注入对话（`src/knowledge/` 已存在但控制台未接）；
- 反馈持久化与评估数据集（`src/feedback/` 内存态、`src/mlops/` 无调用方）；
- 备份/DR 接线（`scripts/ops/backup.py` 文件头声明无生产调用方）。

**验收标准。**
- 五个中心各自有可操作入口与真实数据，不是导航改名；
- 30 方向表中每个「真有」项有对应 UI/API 与测试；
- 「半成品」项要么接线要么明确标注未接线，不得默认为可用。

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
