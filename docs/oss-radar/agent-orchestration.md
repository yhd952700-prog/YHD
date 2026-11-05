# 开源技术雷达 · 第 1 路：智能体与编排

> 调研对象：`D:\LiuHao-AI-OS`（鎏灏 LIUHAO AI OS）
> 调研范围：AI Agent / 多智能体协作 / 任务编排与执行引擎 / Agent Runtime / AI 工作流与自动化 / 任务调度与分布式作业 / 进程与长时任务断点续跑
> 数据抓取时间：2026-11-06（Asia/Shanghai）
> 数据口径：star 数、最近提交时间来自 GitHub REST API / GitHub MCP 当日抓取；许可证以仓库根目录 `LICENSE` 文件正文为准，GitHub 元数据仅作交叉参考；源码目录以实际目录列表为准。
> 边界：本文件只做调研与判断，不改任何代码、不引入任何第三方代码、不提交、不跑测试、不启动服务。

---

## 0. 结论速览

### 0.1 本路核心判断

**明确倾向：先把鎏灏已有的计划器接线，比接入外部智能体框架更划算。**

不是一个"感觉上更轻"的判断，而是由当前代码结构决定的：

- `ExecutionEngine` 已经是事实上的唯一执行权威（SSOT），执行前后串联了 capability 注册表、scope 校验、审计 correlation、journal 断点续跑、验证器与事件。
- `src/ai/planner/` 下的 `LLMPlanner`、`MultiStrategyPlanner`、`PlanValidator`、`PlanParser`、`PlanOptimizer`、`PlanMemory` 已完整存在，且有 **6 个测试文件**（`tests/ai/planner/`），但在 `src/` 生产路径中**零引用**。
- 生产路径已经有一个现成的"计划器注入缝"：`AgentRuntime(executable_capabilities=...)` 会把 `GoalDecomposer` 包成 `ExecutableAwareDecomposer`（`src/ai/agent_runtime.py#L227-L242`），网关 `src/gateway/ai_management.py#L226` 已经实际注入了它。补上"计划来源"的适配层即可，不需要换引擎。
- 外部框架（LangGraph / CrewAI / AutoGen / OpenAI Agents SDK）各自带一整套 runtime、state、checkpoint、memory 语义。整包接管会制造**第二个 SSOT**，并迫使现有审计哈希链、capability 作用域、fence/lease、journal 恢复键全部重写。

因此推荐路线：**自家计划器接线（主）+ 外部成熟语义局部借鉴（辅）**，不反过来。

### 0.2 本路优先级最高的五个候选项

1. **DBOS Transact Python**（MIT）——直接命中 `_journal.py` 自述的三个缺口：副作用去重/幂等键、计划版本化、恢复扫描。
2. **APScheduler**（MIT）——鎏灏当前**完全没有**定时/周期/延迟调度运行时，这是真缺口。
3. **Taskiq**（MIT）——补 broker 抽象、worker、middleware、result backend；但要避免与现有 Redis Streams 事件总线重复。
4. **OpenAI Agents SDK**（MIT）——最值得借鉴的是 run loop、handoff、guardrail、tracing 的"薄"实现方式。
5. **LangGraph**（MIT）——已是现有依赖且有实验模块；只建议作为参考实现/可选后端，不建议升格为主引擎。

### 0.3 明确不建议

- **Restate**：BUSL-1.1，非 OSI 开源，生产使用受限。
- **Windmill**：backend/frontend 为 AGPLv3，链接/修改会传染。
- **n8n**：Sustainable Use License，非 OSI，限制商业分发/托管。
- **AutoGen**：代码 MIT 但文档 CC-BY-4.0；最近提交是"archive caution"，维护收缩信号明确。
- **MetaGPT**：近 9 个多月无提交（2026-01-21 之后），维护风险高。
- **Temporal / Prefect / Hatchet**：技术上强，但对当前单机/单进程为主的鎏灏属于"用服务器换能力"，运维成本与收益不匹配；只借鉴语义。

---

## 1. 该域能力现状表

状态口径：**已有** = 有真实生产路径且被调用；**部分** = 有实现但能力不完整或未接线；**缺失** = 没有该能力。

| # | 能力项 | 状态 | 现有实现路径 | 证据与不足 |
|---|--------|------|--------------|------------|
| 1 | 目标分解（Goal → Task） | 部分 | `src/kernels/execution/__init__.py#L273-L627`（`GoalDecomposer`，`decompose()` 在 L459） | 关键词/正则规则：文件写入用两条正则（L283-L296），其余能力用关键词表 + 整词匹配（L412-L445）。没有语义理解，目标措辞稍变就分解失败 |
| 2 | LLM 规划 | 部分（未接线） | `src/ai/planner/llm_planner.py`、`multi_strategy.py`、`validator.py`、`parser.py`、`optimizer.py`、`plan_memory.py`、`prompts.py` | 模块齐全、有 6 个测试（`tests/ai/planner/test_llm_planner.py` 等），但 `src/` 生产路径零引用；`PlannerContract` 只包了关键词分解器 |
| 3 | 计划可执行性校验（Plan ↔ Capability 契约） | 已有 | `src/ai/planner_contract.py`；`src/ai/agent_runtime.py#L227-L242`；`src/gateway/ai_management.py#L226` | 在计划阶段就拒绝"需要不存在能力"的目标，避免先产生真实副作用再失败；已接入生产 |
| 4 | 计划依赖构建 | 已有 | `src/kernels/execution/__init__.py#L659-L704`（`PlanBuilder`） | `sequential` / `parallel` / `auto` 三种模式；`auto` 的依赖推断只是"输出 key 与输入 key 求交集"（L699-L704），不是真正的数据流分析 |
| 5 | 执行内核（唯一 SSOT） | 已有 | `src/kernels/execution/__init__.py#L1213+`（`ExecutionEngine`）；`ActionExecutor` L707+；`Verifier` | 覆盖：分解→计划→动作→验证；动作前有 capability scope 检查；未注入真实 executor 时 fail-closed，模拟路径需显式 `LIUHAO_ALLOW_SIMULATED_EXECUTION=1` |
| 6 | 崩溃恢复 / 断点续跑 | 部分 | `src/kernels/execution/_journal.py` | SQLite append-only journal；恢复按**任务名**匹配（跨进程稳定）。自述缺口：无副作用去重/幂等键、无计划版本化重建、无压缩归档（L29-L31） |
| 7 | 跨进程协调（租约/围栏/选主） | 已有 | `src/distribution/coordination.py`（`FencingToken`/`FileLockLease`/`RedisLease`/`DistributedExecutorLease`）；`src/distribution/election.py`；`src/distribution/msg_bus.py` | 失败即闭、不静默降级；Redis Streams 事件总线；这是很多同类项目没有的基础 |
| 8 | 任务调度（cron / 周期 / 延迟） | 缺失 | 无 | `src/` 中无 `APScheduler`/`croniter`/`schedule` 等调度运行时；`src/tasks/models.py` 只有最小 Task 数据类；没有 waiting/scheduled 状态 |
| 9 | 分布式任务队列（ack/重试/DLQ/worker 心跳） | 部分 | `src/distribution/msg_bus.py` + `coordination.py` | 有消息总线与租约，但没有统一的"任务投递→worker 认领→ack→超时重投→死信"队列语义 |
| 10 | 多智能体协作 | 部分（装饰性） | `src/ai/employee.py`、`collaboration.py`、`committee.py`、`blackboard.py`、`runtime_loop.py` | 有角色/黑板/委员会等结构；但 `docs/product/PRODUCT-READINESS.md` 明确把 P3 多智能体标为 BLOCKED/装饰性，任务执行没有真实的 agent 间因果链 |
| 11 | Agent 生命周期 | 已有 | `src/ai/agent_factory.py` | `start/pause/resume/stop/cancel/execute/checkpoint/recover` 八个生命周期方法，复用既有 `employee.py` 的 Agent 模型 |
| 12 | Agent Runtime 组合层 | 已有 | `src/ai/agent_runtime.py#L206+` | 组合 `ExecutionEngine` + Observer + Evaluator + Memory；`run_goal()` 有 `_replans`、stop flag、journal 注入 |
| 13 | 工作流状态机 | 部分 | `src/workflow/state_machine.py` | 只有 `pending/running/completed/failed/cancelled` 五态；无 `waiting/paused/retrying/timed_out`；无状态持久化 |
| 14 | 工作流事件总线 | 部分 | `src/workflow/event_bus.py` | 纯内存 `defaultdict`，进程重启即丢；不是持久事件流 |
| 15 | 通用工作流引擎 | 缺失（诚实占位） | `src/workflow/workflow.py#L25-L80` | `WorkflowEngine` 明确标注 PLACEHOLDER：不执行、不写审计、不做安全检查，并返回 `stub=True` |
| 16 | LangGraph 执行引擎 | 部分（实验、未接线） | `src/ai/langgraph_workflow.py`（`ENGINE_AUTHORITY = "experimental"`，L47） | `langgraph` 已写入 `pyproject.toml`；该文件在 `src/` 内无任何引用，测试明确声明它**不是** SSOT |
| 17 | 取消 / 长任务停止 | 已有 | `ExecutionEngine.execute_goal(stop_event=...)`；`GoalCancelled`（L70-L79） | 协作式取消，每个任务前与重试前检查；终止状态为 CANCELLED，不伪造成功 |
| 18 | 审计与执行关联 | 已有 | `kernel_action_correlation_id(goal.correlation_id)`（L1255）；`src/ai/audit.py` | 每个动作可回到触发它的 goal；这是替换外部框架时最难保留的部分 |

**结论：** 真正缺的不是"Agent 框架"，而是三件更具体的事——
1）**LLM 计划器没有接线**（能力已有、入口没有）；
2）**调度运行时缺失**（cron/延迟/周期）；
3）**持久执行的语义不完整**（幂等键、计划版本化、恢复扫描）。

---

## 2. 候选清单（按"值得引入程度"排序）

> 排序逻辑：先看"是否命中真实缺口"，再看"能否以适配器/语义借鉴方式落地而不破坏 SSOT"，最后看许可证与维护风险。

| 排名 | 仓库 | Stars | 最近提交 | 语言 | LICENSE（已核验正文） | 测试 | 安全公告 | 核心源码目录 |
|---|---|---|---|---|---|---|---|---|
| 1 | `dbos-inc/dbos-transact-py` | 1,659 | 2026-11-04 | Python | MIT | `tests/`、`chaos-tests/` | 0 条（已查询） | `dbos/` |
| 2 | `agronholm/apscheduler` | 7,684 | 2026-10-26 | Python | MIT | `tests/` | 未获取（API 限流） | `src/apscheduler/` |
| 3 | `taskiq-python/taskiq` | 2,368 | 2026-10-08 | Python | MIT | `tests/` | 未获取（API 限流） | `taskiq/` |
| 4 | `openai/openai-agents-python` | 30,491 | 2026-11-04 | Python | MIT | `tests/`、`integration_tests/` | 未获取（API 限流） | `src/agents/` |
| 5 | `langchain-ai/langgraph` | 43,962 | 2026-11-05 | Python | MIT | `libs/langgraph/tests/`、`libs/checkpoint-conformance/` | 2 条（已查询，见深评） | `libs/langgraph/langgraph/`、`libs/checkpoint-*/` |
| 6 | `crewAIInc/crewAI` | 60,275 | 2026-11-05 | Python | MIT | `lib/crewai/tests/` | 0 条（已查询） | `lib/crewai/src/crewai/` |
| 7 | `hatchet-dev/hatchet` | 8,206 | 2026-11-05 | Go + Python SDK | MIT | `sdks/python/tests/` | 未获取 | `internal/`、`sdks/python/hatchet_sdk/` |
| 8 | `PrefectHQ/prefect` | 24,104 | 2026-11-04 | Python | Apache-2.0 | `tests/`、`integration-tests/` | 未获取 | `src/prefect/` |
| 9 | `temporalio/temporal` | 14,668 | 2026-11-05 | Go | MIT | `tests/` | 未获取 | `service/`、`internal/`、`common/` |
| 10 | `spotify/luigi` | 18,782 | 2026-10-07 | Python | Apache-2.0 | `test/` | 未获取 | `luigi/` |
| 11 | `pydantic/pydantic-ai` | 12,978 | 2026-11-05 | Python | MIT | `tests/` | 未获取 | `pydantic_graph/pydantic_graph/` |
| 12 | `microsoft/autogen` | 61,682 | 2026-10-28 | Python/.NET | 代码 MIT（`LICENSE-CODE`）+ 文档 CC-BY-4.0（`LICENSE`） | `python/` 下测试目录（未展开） | 未获取 | `python/`、`dotnet/`、`protos/` |
| 13 | `FoundationAgents/MetaGPT` | 71,183 | 2026-01-21 | Python | MIT | `tests/` | 未获取 | `metagpt/` |
| 14 | `restatedev/restate` | 4,654 | 2026-11-05 | Rust | **BUSL-1.1** | Rust 内联测试（无根级 `tests/`） | 未获取 | `crates/` |
| 15 | `windmill-labs/windmill` | 14,553 | 2026-11-05 | HTML/TS/Go/Rust | **AGPLv3**（backend/frontend）+ Apache-2.0（客户端/OpenAPI） | `integration_tests/` | 未获取 | `backend/`（AGPL） |
| 16 | `n8n-io/n8n` | 209,627 | 2026-11-05 | TypeScript | **Sustainable Use License**（非 OSI） | 未验证 | 未获取 | 根目录列表被截断，未获取 |

> Star 数为 2026-11-06 抓取值。`最近提交` 为默认分支最新 commit 时间（GitHub MCP `list_commits`），比 API 的 `pushed_at` 更可信。
> 安全公告一栏中"已查询"指通过 GitHub Advisory API 按 `ecosystem=pip&affects=<包名>` 实际查询；其余因 API 限流未获取，详见第 6 节。

---

## 3. 最值得的 5 个深评

### 3.1 DBOS Transact Python（排名 1）

**事实卡**
- 仓库：`https://github.com/dbos-inc/dbos-transact-py`
- Stars：1,659；最近提交：2026-11-04（`More Scalable Scheduler (#936)`）；语言：Python
- LICENSE：MIT（`LICENSE` 正文首行 `MIT License / Copyright (c) 2024 DBOS, Inc.`）
- 测试：根级 `tests/` + `chaos-tests/`（故障注入目录）
- 安全公告：按 `pip` 包名 `dbos` 查询，0 条
- 核心目录：`dbos/_dbos.py`、`_workflow_commands.py`、`_recovery.py`、`_queue.py`、`_scheduler.py`、`_debouncer.py`、`_outcome.py`、`_serialization.py`、`_sys_db.py`、`_sys_db_sqlite.py`、`_sys_db_postgres.py`、`_datasource.py`、`_migration.py`

**九问**

1. **鎏灏是否已有同类功能？** 部分。已有 `src/kernels/execution/_journal.py`（SQLite append-only 记录）与 `AgentRuntime.recover()`；已有 `src/distribution/coordination.py` 的租约/围栏。
2. **当前实现有什么不足？** journal 自述三个缺口（`_journal.py#L29-L31`）：不做副作用去重（没有幂等键）、不做计划结构版本化、没有压缩归档；恢复按任务名匹配，计划本身重新跑一遍。跨进程执行没有统一的任务状态库。
3. **候选能解决什么问题？** 数据库支撑的 durable workflow：工作流/步骤状态落在系统库；恢复扫描；事务性入队；幂等结果记录；调度与 debounce。正好对应鎏灏 journal 的三个缺口。
4. **哪些具体源码目录值得借鉴？**
   - `dbos/_workflow_commands.py`：步骤级状态/命令的持久化与状态迁移写法
   - `dbos/_recovery.py`：启动时恢复扫描与"未完成步骤"重新驱动
   - `dbos/_outcome.py`：步骤结果记录与异常语义
   - `dbos/_queue.py` + `_scheduler.py`：事务性入队、延迟/周期任务
   - `dbos/_sys_db*.py`：SQLite/Postgres 双后端的系统库抽象
   - `chaos-tests/`：崩溃/故障注入测试的组织方式
5. **能否独立封装成模块？** 可以。封装为 `DurableStepStore` / `RecoveryScanner`，通过接口注入 `ExecutionEngine`，不替换引擎。
6. **会不会破坏现有接口与数据结构？** 只新增表与端口则不会；若把 `ExecutionEngine` 换成 DBOS 执行器，会破坏 `Task/Action/ExecutionContext`、审计 correlation 与 journal 恢复键，属于高风险。
7. **许可证是否允许使用与修改？** MIT，允许商用、修改、闭源分发，保留版权与许可声明即可。无 GPL/AGPL 传染。
8. **安全风险与维护成本？** 需要新增系统库 schema 与迁移；SQLite 并发下要确认 WAL 与写锁行为；项目处于 1.x 演进期，API 可能变动。风险主要是"第二套持久化状态"与现有 journal 的一致性。
9. **集成测试与回归测试怎么写？**
   - 契约测试：同一 `(execution_id, step_id)` 重放两次，副作用只发生一次
   - 崩溃注入：步骤完成后 `kill -9`，重启只补未完成步骤，不重复已完成副作用
   - 恢复并发：两个进程同时恢复，只有一个通过租约抢到
   - 一致性：DBOS 状态表与现有 journal 双写时，终态必须一致；用临时 SQLite fixture，不打真实库

**判断：值得借鉴其"步骤结果 + 幂等键 + 恢复扫描"三件套，不建议整包替换执行内核。**

---

### 3.2 APScheduler（排名 2）

**事实卡**
- 仓库：`https://github.com/agronholm/apscheduler`
- Stars：7,684；最近提交：2026-10-26（`Clarify scheduler identity across pod replacements (#1176)`）；语言：Python
- LICENSE：MIT（`LICENSE.txt` 首行 `The MIT License (MIT) / Copyright (c) 2009 Alex Grönholm`）
- 测试：根级 `tests/`
- 安全公告：未获取（GitHub Advisory API 限流）
- 核心目录：`src/apscheduler/_schedulers/`、`triggers/`、`executors/`、`datastores/`、`eventbrokers/`、`_retry.py`、`_events.py`

**九问**

1. **鎏灏是否已有同类功能？** 缺失。`src/` 内没有调度器运行时；`src/tasks/models.py` 只有最小 `Task/TaskResult`。没有 cron/interval/date 触发器。
2. **当前实现有什么不足？** 无法表达"每天 9 点执行"、"30 分钟后重试"、"错过触发后补跑"；`state_machine.py` 也没有 `waiting/scheduled` 状态。
3. **候选能解决什么问题？** 提供成熟的触发器模型（cron/interval/date）、job store、executor、错过触发合并（misfire grace）、暂停/恢复/改期。
4. **哪些具体源码目录值得借鉴？**
   - `src/apscheduler/triggers/`：cron/interval/date 的触发计算（含时区/DST）
   - `src/apscheduler/_schedulers/`：调度循环与 job 生命周期
   - `src/apscheduler/datastores/`：job 持久化与多实例共享
   - `src/apscheduler/executors/`：执行器抽象与并发控制
   - `src/apscheduler/eventbrokers/`：调度事件（错过、提交、错误）
5. **能否独立封装成模块？** 可以。新增 `SchedulerService`，只负责"到点把 goal/task 提交给 ExecutionEngine"，不复用它的执行语义。
6. **会不会破坏现有接口与数据结构？** 新增服务与 job 表即可；但需要给工作流状态机增加 `scheduled/waiting` 状态，这是**扩展**不是替换。不要让它接管 `ExecutionEngine`。
7. **许可证是否允许使用与修改？** MIT，允许商用与修改。
8. **安全风险与维护成本？** 4.x 仍在演进，API 有变动；多副本部署必须共享 job store + 分布式锁，否则同一 job 会重复触发；时区配置错误会造成"看起来跑了其实没跑"。维护成本中等。
9. **集成测试与回归测试怎么写？**
   - 触发器：cron 表达式、时区、DST 前后各一例
   - misfire：停机跨过触发点后，按策略补跑或跳过，且只执行一次
   - 多副本：两个实例抢同一 job，断言只触发一次（复用 `coordination.py` 的租约）
   - 生命周期：暂停/恢复/改期/删除后，job store 与审计事件一致

**判断：这是本路最"纯增量"的候选，建议优先补。**

---

### 3.3 Taskiq（排名 3）

**事实卡**
- 仓库：`https://github.com/taskiq-python/taskiq`
- Stars：2,368；最近提交：2026-10-08（`fix(worker): configure logging in forkserver worker processes (#686)`）；语言：Python
- LICENSE：MIT（`LICENSE` 首行 `MIT License / Copyright (c) 2022-2023 Pavel Kirilin`）
- 测试：根级 `tests/`
- 安全公告：未获取（API 限流）
- 核心目录：`taskiq/brokers/`、`taskiq/scheduler/`、`taskiq/middlewares/`、`taskiq/result_backends/`、`taskiq/receiver/`、`taskiq/serializers/`、`taskiq/api/`

**九问**

1. **鎏灏是否已有同类功能？** 部分。`src/distribution/msg_bus.py` 有 Redis Streams 事件总线，`coordination.py` 有租约/围栏；但没有统一的任务 broker/worker/ack/重试/结果后端。
2. **当前实现有什么不足？** 任务投递与事件广播混在一起；没有 middleware 管道（重试、超时、指标、日志可统一切面）；没有标准 result backend；worker 进程模型缺失。
3. **候选能解决什么问题？** 全异步 broker 抽象、scheduler、middleware、result backend、worker 启动/关闭生命周期；让"任务"从事件里独立出来。
4. **哪些具体源码目录值得借鉴？**
   - `taskiq/scheduler/`：延迟/周期任务的调度实现
   - `taskiq/middlewares/`：洋葱模型中件（重试、超时、指标）
   - `taskiq/result_backends/`：结果持久化与查询
   - `taskiq/receiver/`：worker 收消息、执行、ack/nack 的循环
   - `taskiq/brokers/`：多 broker 适配层的接口设计
5. **能否独立封装成模块？** 可以，作为 `WorkerQueueAdapter`；但必须明确它与 `msg_bus` 的边界：**命令/任务走队列，状态事件走事件总线**。
6. **会不会破坏现有接口与数据结构？** 新增适配器不改接口；若用 taskiq 消息替换 `MsgBus`，会改变序列化格式、重试与消费位点语义，风险高。
7. **许可证是否允许使用与修改？** MIT。
8. **安全风险与维护成本？** 需要额外 broker（Redis/NATS/RabbitMQ）与 worker 进程；会形成"双队列系统"，必须避免同一任务两处投递。项目由小型团队维护（响应速度未量化）。
9. **集成测试与回归测试怎么写？**
   - 投递/消费：broker 重启后任务至少一次投递，幂等键去重
   - middleware 顺序与异常传播
   - scheduler 时区与延迟精度
   - 与现有 `msg_bus` 影子运行：同一事件两路消费结果对比，确认不丢不重

---

### 3.4 OpenAI Agents SDK（排名 4）

**事实卡**
- 仓库：`https://github.com/openai/openai-agents-python`
- Stars：30,491；最近提交：2026-11-04；语言：Python
- LICENSE：MIT（`LICENSE` 首行 `MIT License / Copyright (c) 2025 OpenAI`）
- 测试：`tests/` + `integration_tests/`
- 安全公告：未获取（API 限流）
- 核心目录：`src/agents/run_internal/run_loop.py`、`turn_resolution.py`、`tool_planning.py`、`tool_execution.py`、`handoffs/history.py`、`guardrail.py`、`tool_guardrails.py`、`tracing/`、`memory/`、`agent.py`、`run.py`

**九问**

1. **鎏灏是否已有同类功能？** 部分。`src/ai/employee.py`、`agent_factory.py`、`runtime_loop.py`、`collaboration.py`、`committee.py` 已有 Agent 与协作结构。
2. **当前实现有什么不足？** 多智能体是装饰性的（PRODUCT-READINESS 标 P3 BLOCKED）；没有标准的 handoff 语义、输入/输出 guardrail 拦截层、统一 trace/span；`runtime_loop.py` 是同步轮询。
3. **候选能解决什么问题？** 极薄的 agent run loop：工具调用规划、回合解析、handoff 历史、guardrail、tracing、session/memory；代码量小，易读。
4. **哪些具体源码目录值得借鉴？**
   - `src/agents/run_internal/run_loop.py`：单回合循环与终止条件
   - `src/agents/run_internal/turn_resolution.py`：工具调用/最终输出的回合裁决
   - `src/agents/run_internal/tool_planning.py` + `tool_execution.py`：工具规划与执行分离
   - `src/agents/handoffs/history.py`：handoff 的历史传递
   - `src/agents/tool_guardrails.py`：工具级 guardrail
   - `src/agents/tracing/`：span 组织方式
5. **能否独立封装成模块？** 可以，做 `AgentExecutionAdapter`：把一次 agent 回合映射成 `Task/Action`，仍由 `ExecutionEngine` 执行。
6. **会不会破坏现有接口与数据结构？** 只做适配层不会；若让它替换 `AgentRuntime`，会绕开现有审计、策略、Memory 绑定，不可接受。
7. **许可证是否允许使用与修改？** MIT。
8. **安全风险与维护成本？** 与 OpenAI 生态耦合较强（模型层可替换但默认路径绑定）；版本迭代快；引入前要评估 tool schema 与现有 capability registry 的映射成本。
9. **集成测试与回归测试怎么写？**
   - handoff：A→B 后历史/上下文完整，B 的输出能回到 A
   - guardrail：被拦截时任务状态是 FAILED 而非 COMPLETED，且审计有记录
   - tracing：一次运行产生完整 span，父子关系正确
   - 离线单测：mock model，不触网；与 `runtime_loop.py` 做同目标对照

---

### 3.5 LangGraph（排名 5）

**事实卡**
- 仓库：`https://github.com/langchain-ai/langgraph`
- Stars：43,962；最近提交：2026-11-05；语言：Python
- LICENSE：MIT（`LICENSE` 首行 `MIT License / Copyright (c) 2024 LangChain, Inc.`）
- 测试：`libs/langgraph/tests/`、`libs/checkpoint-conformance/`、`libs/checkpoint-sqlite/`、`libs/checkpoint-postgres/`
- 安全公告：按 `pip` 包名 `langgraph` 查询到 2 条
  - `GHSA-3fx2-cqw9-xr3c`：LangGraph SDK v3 streaming 中未编码的 identifier
  - `GHSA-g48c-2wqr-h844`：checkpoint 加载使用不安全的 msgpack 反序列化
- 核心目录：`libs/langgraph/langgraph/pregel/`、`graph/`、`channels/`、`func/`、`runtime/`；`libs/checkpoint/`、`libs/checkpoint-sqlite/`、`libs/checkpoint-postgres/`、`libs/checkpoint-conformance/`

**九问**

1. **鎏灏是否已有同类功能？** 已有实验实现 `src/ai/langgraph_workflow.py`（`ENGINE_AUTHORITY = "experimental"`，L47），且 `langgraph` 已是 `pyproject.toml` 依赖；但该文件在 `src/` 内无引用，测试明确声明它不是 SSOT。
2. **当前实现有什么不足？** 实验模块未接线；现有 `ExecutionEngine` 已自带 journal/fence/audit，LangGraph 若接管会重复；checkpoint 的反序列化有已知安全公告。
3. **候选能解决什么问题？** Pregel/BSP 超步调度、channel 合并、中断/恢复、checkpoint serde、跨 checkpoint 实现的一致性测试套件。
4. **哪些具体源码目录值得借鉴？**
   - `libs/langgraph/langgraph/pregel/`：超步调度、中断与恢复
   - `libs/langgraph/langgraph/channels/`：状态通道与合并语义
   - `libs/checkpoint-conformance/`：**跨实现一致性测试套件**（最值得借鉴）
   - `libs/checkpoint-sqlite/`：SQLite checkpoint 的 schema 与序列化
5. **能否独立封装成模块？** 可以作为"可选执行后端"，但不能成为主链路；现有实验文件应保持明确标注。
6. **会不会破坏现有接口与数据结构？** 替换主引擎会改变 `Task/Action` 生命周期、审计关联、journal 恢复键，高风险；旁路使用不影响。
7. **许可证是否允许使用与修改？** MIT。
8. **安全风险与维护成本？** 已有 2 条安全公告（其中 checkpoint msgpack 反序列化需配置 allowlist/安全加载）；框架版本演进快；若引入 checkpoint 需要额外的序列化白名单与依赖审计。
9. **集成测试与回归测试怎么写？**
   - 借鉴 `checkpoint-conformance` 的思路，为鎏灏 journal 写一套"写入→崩溃→恢复→终态一致"的一致性测试
   - 中断/恢复：在图中间 interrupt，恢复后不重复执行已完成节点
   - 安全：恶意 msgpack payload 必须被拒绝
   - 对照：同目标分别走 `ExecutionEngine` 与 LangGraph 旁路，比较任务数与副作用次数

---

### 3.6 其余候选九问速答（B/C 级）

#### 3.6.1 CrewAI（排名 6）

- **事实**：60,275 stars；2026-11-05；Python；MIT（`LICENSE` 正文首行 `Copyright (c) 2025 crewAI, Inc.` + MIT 条款）；`lib/crewai/tests/`；pip `crewai` 查询 0 条公告；核心 `lib/crewai/src/crewai/`。
- 1）已有：`employee.py` / `collaboration.py` / `committee.py` / `blackboard.py` 提供角色与协作雏形；无 Flow DSL。
- 2）不足：协作无因果链、无事件驱动的 Flow、无知识绑定；P3 被标 BLOCKED。
- 3）解决：Flow 编排、process 模式、memory/knowledge 组合、A2A。
- 4）借鉴：`flow/`（事件驱动 Flow）、`execution.py`（任务执行）、`memory/`、`knowledge/`、`a2a/`。
- 5）可独立封装：只借鉴 Flow 模型；整包会带入第二套 Agent/Task 模型。
- 6）破坏：整包引入会与 `employee.py`、`ExecutionEngine` 冲突；只借鉴不改接口。
- 7）许可：MIT，允许修改与商用。
- 8）风险：依赖较重、遥测需显式关闭；版本迭代快。
- 9）测试：Flow 事件顺序、状态持久化、知识检索一致性；不引包则不新增运行时测试，只做设计对照。

#### 3.6.2 Hatchet（排名 7）

- **事实**：8,206 stars；2026-11-05；Go + Python SDK；MIT；`sdks/python/tests/`；公告未获取；核心 `internal/`、`sdks/python/hatchet_sdk/`。
- 1）已有：Redis Streams + 租约/围栏 + journal。
- 2）不足：没有完整的"投递→认领→ack→超时重投→DLQ"队列，也没有 worker 注册/心跳。
- 3）解决：后台任务与 durable workflow 引擎、worker 与队列、重试与可观测。
- 4）借鉴：`internal/queueutils/`、`internal/operation/`、`internal/services/`、`sql/` 迁移组织。
- 5）可独立封装：可作为独立任务服务，但引入 Go 服务成本高。
- 6）破坏：旁路不改；替换会重写执行层。
- 7）许可：MIT。
- 8）风险：需要 Postgres + Redis + Go 服务，运维重；API token 面扩大。
- 9）测试：worker 崩溃重投、幂等、退避、DLQ、租户隔离。

#### 3.6.3 Prefect（排名 8）

- **事实**：24,104 stars；2026-11-04；Python；Apache-2.0；`tests/` + `integration-tests/`；公告未获取；核心 `src/prefect/`。
- 1）已有：`ExecutionEngine` + 五态状态机。
- 2）不足：没有 flow run/task run 双层状态、work pool/worker、结果缓存策略。
- 3）解决：状态模型、重试/缓存、并发限制、schedules。
- 4）借鉴：`src/prefect/flow_engine.py`、`task_engine.py`、`states.py`、`workers/`、`concurrency/`、`schedules.py`。
- 5）可独立封装：可借鉴状态与调度；整包会与执行内核重复。
- 6）破坏：整包会引入第二套 flow/task 模型；只借鉴。
- 7）许可：Apache-2.0（允许商用/修改，保留 NOTICE）。
- 8）风险：服务端 + DB 运维重，组件多，供应链面大。
- 9）测试：状态迁移矩阵、缓存命中、并发限流、取消语义。

#### 3.6.4 Temporal（排名 9）

- **事实**：14,668 stars；2026-11-05；Go；MIT；`tests/`；公告未获取；核心 `service/`、`internal/`、`common/`、`client/`。
- 1）已有：journal + 租约/围栏 + 重试。
- 2）不足：没有跨进程确定性重放、workflow/activity 双层重试、版本兼容。
- 3）解决：确定性重放、activity 重试、timer、signal、版本化。
- 4）借鉴：`service/`（history service）、`internal/`（worker/namespace）、`common/`（超时/重试）、`client/`。
- 5）可独立封装：只能作为外部服务。
- 6）破坏：替换执行层等于重写；旁路则双系统。
- 7）许可：MIT。
- 8）风险：运维最重（数据库 + 多服务），学习曲线陡，资源占用大。
- 9）测试：确定性重放、activity 至少一次、版本升级兼容、timer 精度。

#### 3.6.5 Luigi（排名 10）

- **事实**：18,782 stars；2026-10-07；Python；Apache-2.0；`test/`；公告未获取；核心 `luigi/`。
- 1）已有：`PlanBuilder` 依赖图 + `ExecutionEngine`。
- 2）不足：没有 target 幂等、worker 集中调度、历史追踪。
- 3）解决：DAG 依赖解析、target 完成判定、worker 调度。
- 4）借鉴：`luigi/task.py`（requires/output 契约）、`target.py`（幂等完成标记）、`scheduler.py`/`worker.py`、`execution_summary.py`。
- 5）可独立封装：只借鉴 target 幂等模型；整包偏批处理。
- 6）破坏：引入 Luigi Task 子类会改数据模型；只借鉴。
- 7）许可：Apache-2.0。
- 8）风险：批处理时代定位，API 老派，与 Agent 场景不匹配。
- 9）测试：DAG 依赖正确性、target 幂等、循环检测、失败重跑。

#### 3.6.6 Pydantic AI（排名 11）

- **事实**：12,978 stars；2026-11-05；Python；MIT；`tests/`；公告未获取；核心 `pydantic_graph/pydantic_graph/`。
- 1）已有：`PlanValidator` + `parser.py` + `ParsedPlan`。
- 2）不足：计划是 dict/ParsedPlan，缺少强类型状态机与编译期校验。
- 3）解决：强类型图、node/decision/join、并行分支、状态持久化。
- 4）借鉴：`graph_builder.py`、`node.py`、`decision.py`、`join.py`、`step.py`、`parent_forks.py`。
- 5）可独立封装：可作为 Plan DAG 的类型层。
- 6）破坏：新增类型层需适配 `Task` 模型，不改现有接口。
- 7）许可：MIT。
- 8）风险：依赖 pydantic 版本；框架较新。
- 9）测试：图编译失败、分支/join 语义、状态序列化往返、循环拒绝。

#### 3.6.7 AutoGen（排名 12）

- **事实**：61,682 stars；2026-10-28；Python/.NET；`LICENSE` 为 CC-BY-4.0，`LICENSE-CODE` 为 MIT；公告未获取；核心 `python/`、`dotnet/`、`protos/`。
- 1）已有：`collaboration.py` / `committee.py` / `blackboard.py` 有协作结构。
- 2）不足：没有标准 group chat/actor 模型与跨语言消息协议。
- 3）解决：多智能体对话、group chat 终止条件、actor 编排。
- 4）借鉴：`python/` 下 agentchat/groupchat、`protos/`。
- 5）可独立封装：只借鉴 group chat 协议；不建议整包。
- 6）破坏：整包引入第二套 Agent 模型。
- 7）许可：**代码 MIT（`LICENSE-CODE`），文档 CC-BY-4.0（`LICENSE`）**——必须逐文件确认，不能把文档内容当代码使用。
- 8）风险：最近提交为 `docs: add archive caution to landing page`（2026-10-28），维护收缩信号明确；API 已向 Microsoft Agent Framework 迁移，采用即背历史包袱。
- 9）测试：若借鉴，测 group chat 终止条件、发言顺序、失败传播；不整包则不新增运行时测试。

#### 3.6.8 MetaGPT（排名 13）

- **事实**：71,183 stars；2026-01-21；Python；MIT；`tests/`；公告未获取；核心 `metagpt/`。
- 1）已有：`agent_factory.py` 的 role profiles + `committee.py`。
- 2）不足：没有 SOP 流水线、没有角色间产物契约。
- 3）解决：SOP 式角色协作、产物 schema。
- 4）借鉴：`metagpt/` 的 roles/actions/team 组织方式。
- 5）可独立封装：只借鉴 SOP 契约。
- 6）破坏：会引入第二套角色/消息模型。
- 7）许可：MIT。
- 8）风险：**截至 2026-11-06 已约 9.5 个月无提交**，维护风险高；依赖较旧。
- 9）测试：若借鉴，测产物契约与失败传播；不整包则不新增。

#### 3.6.9 Restate（排名 14）

- **事实**：4,654 stars；2026-11-05；Rust；**BUSL-1.1**；Rust 内联测试；公告未获取；核心 `crates/`。
- 1）已有：journal + 重试。
- 2）不足：没有 durable execution server、虚拟对象、定时器。
- 3）解决：持久化执行、虚拟对象、定时器、幂等。
- 4）借鉴：`crates/core/`、`timer-queue/`、`wal-protocol/`、`invoker-impl/`。
- 5）可独立封装：只能作为外部服务。
- 6）破坏：旁路即双系统。
- 7）许可：**BUSL-1.1，非 OSI 开源**；LICENSE 的 Additional Use Grant 限制 "Public Restate Platform Service"；不能当作普通开源库嵌入/修改分发。变更日期（Change Date）未获取。
- 8）风险：许可风险最高 + 运维重。
- 9）测试：不建议集成。

#### 3.6.10 Windmill（排名 15）

- **事实**：14,553 stars；2026-11-05；TS/Go/Rust；`LICENSE` 明示：backend/frontend **AGPLv3**，客户端与 OpenAPI Apache-2.0，enterprise 片段为专有许可；`integration_tests/`；公告未获取；核心 `backend/`（AGPL）。
- 1）已有：部分 workflow/scheduler 语义。
- 2）不足：没有脚本→工作流平台与 UI。
- 3）解决：脚本工作流、调度、UI、集成。
- 4）借鉴：只可借鉴 API/OpenAPI 设计（Apache-2.0 部分）；`backend/` 为 AGPL，不能抄。
- 5）可独立封装：不能以库形式链接（AGPL 传染）；只能独立进程 + HTTP。
- 6）破坏：独立服务旁路。
- 7）许可：**AGPLv3**——修改并通过网络提供服务需开源整体；法务风险高。
- 8）风险：许可 + 运维双重成本。
- 9）测试：不建议集成。

#### 3.6.11 n8n（排名 16）

- **事实**：209,627 stars；2026-11-05；TypeScript；**Sustainable Use License**（`LICENSE.md` 正文，非 OSI）；企业功能文件另行许可；测试未验证；公告未获取；核心目录未获取（根目录列表被截断）。
- 1）已有：缺失节点式自动化编排 UI。
- 2）不足：没有低代码工作流与集成生态。
- 3）解决：节点编排、触发器、集成。
- 4）借鉴：只参考节点/触发器交互设计，不抄代码。
- 5）可独立封装：不能作为库；只能独立部署。
- 6）破坏：独立旁路。
- 7）许可：Sustainable Use License，限制商业分发/托管服务，不是 OSI。
- 8）风险：许可风险高；代码量与运维大。
- 9）测试：不引入。

---

## 4. 明确不建议引入的，及原因

| 候选 | 原因 | 结论 |
|------|------|------|
| Restate | BUSL-1.1，非 OSI；生产用途受限；变更日期未获取 | 不引入，不复制代码 |
| Windmill | backend/frontend AGPLv3，网络服务修改需开源整体；enterprise 片段专有 | 不引入，不复制代码 |
| n8n | Sustainable Use License，非 OSI，限制商业分发/托管 | 不引入 |
| AutoGen | 代码 MIT 但文档 CC-BY-4.0；最近提交为 "archive caution"；生态向 Agent Framework 迁移 | 不引入，仅读设计 |
| MetaGPT | 约 9.5 个月无提交，维护风险高 | 不引入 |
| Temporal | 运维最重（多服务 + 数据库），当前鎏灏以单机/单进程为主，收益不匹配 | 不引入，仅借鉴重放/activity 语义 |
| Prefect | 服务端 + DB + 多组件，和现有 `ExecutionEngine` 职责重叠 | 不引入整包，仅借鉴状态模型 |
| Hatchet | 需要 Go 服务 + Postgres + Redis，运维成本高 | 不引入，仅借鉴队列语义 |
| CrewAI | 会引入第二套 Agent/Task/Flow 模型，与 `employee.py`、`ExecutionEngine` 冲突；遥测需关闭 | 不引入整包，仅借鉴 Flow |
| LangGraph（作为主引擎） | 已有 experimental 模块且非 SSOT；接管会破坏审计/围栏/journal；checkpoint 有 2 条安全公告 | **不升格为主引擎**，保持可选/参考 |

---

## 附录 A：扫描到但未进入候选清单的仓库（仅记录，不推荐）

这些仓库与本路相关，但不满足"能安全、低成本增强当前架构"的门槛，因此不列为候选。记录在此是为了证明检索覆盖面，并避免后续重复调研。

| 仓库 | Stars | 最近提交 | LICENSE（核验情况） | 不列入候选的原因 |
|------|-------|----------|----------------------|------------------|
| `agentscope-ai/agentscope` | 35,430 | 2026-11-04 | Apache-2.0（已核验正文） | 多智能体框架完整，但与 `employee.py`/`committee.py` 能力重叠；引入会形成第二套消息模型 |
| `huggingface/smolagents` | 30,220 | 2026-11-03 | Apache-2.0（已核验正文） | Code-agent 定位；鎏灏已有工具路由与沙箱，增量有限 |
| `celery/celery` | 25,455 | 2026-11-05 | BSD-3-Clause（已核验正文，非 GitHub 元数据） | 同步/重运维的经典队列；鎏灏已有 Redis Streams + 租约，整包引入过重 |
| `agno-agi/agno` | 25,213 | 2026-11-05 | LICENSE 正文为 Apache-2.0，GitHub 元数据为 MPL-2.0（**不一致，需复核**） | Memory/Knowledge 与现有 Kernel 重叠；许可证口径不一致 |
| `langgenius/dify` | 未获取 | 未获取 | modified Apache-2.0（已核验首段，含额外限制） | 平台型产品，非可嵌入库；许可有附加条件 |
| `FlowiseAI/Flowise` | 55,553 | 2026-08-13 | Apache-2.0 + enterprise 目录单独商业许可 | 可视化平台，与后端架构不匹配；存在商业 carve-out |
| `microsoft/semantic-kernel` | 28,738 | 2026-11-05 | MIT（已核验正文） | 与现有 Agent/Planner 大量重叠；Python/C# 双栈，引入面过大 |
| `camel-ai/camel` | 18,012 | 2026-11-02 | Apache-2.0（已核验正文） | 研究型多智能体框架；工程化编排增量有限 |
| `microsoft/TaskWeaver` | 6,160 | 2026-03-23 | MIT（GitHub 元数据，**正文未核验**） | 代码解释器/插件式 agent；近 7 个月低活跃 |

---

## 5. 核心问题：接外部框架，还是接自家计划器？

### 5.1 结论

**接自家计划器。** 外部框架不是"更成熟所以更好"，而是"会把鎏灏已经做对的部分重新做一遍，并且做得更差"。

### 5.2 证据链（全部来自当前代码）

1. `ExecutionEngine` 是唯一执行权威：`src/kernels/execution/__init__.py#L1213+`，`execute_goal()` L1242+。
2. 生产入口已经把它接实：`src/gateway/ai_management.py#L208-L228` 注入真实 `capability_executor`、`executable_capabilities`、`journal`。
3. 计划器已经存在且有测试：`src/ai/planner/` 共 8 个文件（约 90KB），`tests/ai/planner/` 有 6 个测试文件。
4. 计划器零生产引用：`src/ai/planner/__init__.py` 只做导出；`multi_strategy.py` 内部才 import `LLMPlanner`；`src/` 其余位置没有引用。
5. 已有现成注入缝：`AgentRuntime.__init__` 的 `executable_capabilities` 参数（L221）会把 `self.engine.decomposer` 替换为 `ExecutableAwareDecomposer`（L240-L242）。
6. LangGraph 是"实验、未接线"：`src/ai/langgraph_workflow.py#L47` `ENGINE_AUTHORITY = "experimental"`，全 `src/` 无引用。
7. journal 的恢复依赖**任务名确定性**：`_journal.py#L20-L21` 明确"恢复按 task 名字匹配，不按 id"。任何引入 LLM 计划器的方案，都必须先解决"任务名稳定"。

### 5.3 为什么外部框架不划算

- **会产生第二个 SSOT。** LangGraph/CrewAI/AutoGen 都有自己的 state、checkpoint、memory、retry。只要它们接管执行，`ExecutionEngine` 的 capability scope、审计 correlation、fence/lease、journal 就不再是唯一真相。
- **会丢掉最难重建的部分。** 审计哈希链与 goal→action 关联（`kernel_action_correlation_id`，L1255）是鎏灏特有的；外部框架没有等价物。
- **会把"计划质量问题"误当成"引擎问题"。** 当前失败大多是关键词分解覆盖不足（`planner_contract.py` 的注释直接点名：`search`/`memory`/`plan` 三类目标因能力不存在而在计划阶段失败），不是执行引擎不够强。换引擎不解决这个。
- **迁移成本远高于接线成本。** 接线只需要一个 `PlanSource` 适配层；换引擎要重写 104 个内核测试覆盖的行为与网关 E2E。

### 5.4 推荐的最小接线设计（只描述，不改代码）

**目标：让 `LLMPlanner` 成为可选的计划来源，同时保持 `ExecutionEngine` 为唯一 SSOT。**

1. **新增计划来源协议**（放在 `src/ai/planner/plan_source.py` 或 `planner_contract.py`）：
   - `plan(goal) -> PlanDraft`，`PlanDraft` 含 `tasks`、`reasoning`、`strategy`、`plan_hash`。
2. **新增 `LLMPlanAdapter`**：
   - 调 `LLMPlanner.plan()` → `PlanParser` → `ParsedPlan`；
   - 映射为 `kernels.execution.Task`；
   - **任务名必须确定性**：例如 `f"{index:02d}-{intent_slug}-{capability_id}"`，不含时间/uuid；这是 journal 恢复的前提。
3. **校验顺序（fail-closed）**：
   - `PlanValidator`（结构、依赖、环、必填字段）
   - `ExecutableAwareDecomposer`（能力是否真实可执行）
   - `PlanBuilder`（依赖与模式）
   - 任一步失败 → **不执行任何任务**，返回明确原因。
4. **降级策略（显式、可审计）**：
   - provider 不可用 / 超时 / schema 非法 → 回退到 `GoalDecomposer`；
   - 在 journal、event、审计里写 `planner="llm"|"rule"` 与 `fallback_reason`；
   - 禁止静默降级（与 `planner_contract.py` 的既有哲学一致）。
5. **计划版本化**：
   - 在 journal 记录 `plan_hash` 与 `planner_version`；
   - 恢复时若 `plan_hash` 变化，不盲目复用旧完成记录，而是重新校验并按新计划执行（否则会把"旧计划的完成"错算成"新计划的完成"）。
6. **成本与策略开关**：
   - `plan_mode="auto"` 保持；新增 `planner="rule"|"llm"|"multi"` 配置；
   - 多策略只在高价值/高不确定目标启用，设置超时与 token 预算。

### 5.5 什么时候才考虑外部框架

只有当以下条件**同时**成立时，才值得评估外部执行后端：

1. 需要跨进程/跨节点执行，且现有 `coordination.py` + journal 已无法满足；
2. 需要确定性重放/长时间定时器，且自研成本高于引入服务；
3. 有明确的运维预算（Postgres/Redis/Go 服务）；
4. 能接受"执行层外置"，但仍由 `ExecutionEngine` 负责计划/校验/审计。

满足时优先评估 **DBOS 语义**（嵌入式、MIT、SQLite/Postgres）或 **Taskiq worker 层**（MIT、轻量）；Temporal/Prefect/Hatchet 只作为架构参照。

---

## 6. 未验证项清单

1. **Codeberg 检索失败**：`https://codeberg.org/api/v1/repos/search?q=agent...` 返回 `Access denied, old Chrome version`，未获得结果。
2. **GitLab 检索失败**：`https://gitlab.com/api/v4/projects?search=...` 返回空内容/超时，未获得结果。
3. **SourceForge 检索失败**：页面返回"正在进行安全验证"反爬页，未获得结果。
4. **Hugging Face**：`/api/spaces?search=agent` 只返回课程/demo Spaces，未发现生产级编排库；未系统检索 HF models/datasets。
5. **安全公告覆盖不全**：仅 `langgraph`（2 条）、`crewai`（0 条）、`dbos`（0 条）查询成功；其余因 GitHub Advisory API 限流未获取；**未查询 OSV 数据库**。
6. **测试覆盖率未量化**：仅确认 `tests/` / `integration_tests/` 目录存在，未运行任何覆盖率工具，未获取百分比。
7. **issue 响应速度未量化**：未统计近半年 issue 打开/关闭时长。
8. **未做性能基准**：调度吞吐、恢复时延、checkpoint 体积均未实测。
9. **未做法务复核**：尤其 AutoGen 的 `LICENSE` 与 `LICENSE-CODE` 边界、Agno 的 LICENSE 正文（Apache-2.0）与 GitHub 元数据（MPL-2.0）不一致、Windmill 的 AGPL/专有边界。
10. **未做 PoC**：没有安装、运行或调用任何候选项目；本报告只基于源码目录与许可证文件。
11. **n8n 核心目录未获取**：根目录列表被截断，未能确认 `packages/` 结构。
12. **依赖兼容性未验证**：未检查候选与鎏灏当前 Python 版本、pydantic、redis 客户端的冲突。
13. **未检查 SBOM / pip-audit**：仅查询 GitHub Advisory，未做依赖树漏洞扫描。
14. **star 数为时点值**：2026-11-06 抓取，之后会变化。
