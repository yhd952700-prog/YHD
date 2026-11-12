# Execution Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> - **真实实现**：`src/kernels/execution/__init__.py:572` 的 `ExecutionEngine`。`lifecycle` 字段见 :574（默认 `UNINITIALIZED`，`initialize()` 置 `READY`）；`initialize/shutdown/pause/resume` 见 :895/:898/:901/:906。
> - **进程级入口**：工厂 `create_execution_engine(...)`（`src/kernels/execution/__init__.py:913`）。**无进程级单例**，也没有 `get_execution_engine()`。
> - **生命周期判定：已实现**。本类已接入统一基座：具备 `lifecycle` 字段与四个生命周期方法（`initialize` 置 `READY`、`shutdown` 置 `STOPPED`、`pause/resume` 置 `PAUSED/READY` 并带状态校验）。
>   - **是否自动驱动**：**否**——只有工厂，构造后 `lifecycle` 停留在 `UNINITIALIZED`，**不会被自动驱动到 READY**；`src/kernels/_registry.py` 的 `snapshot()` 将其报告为「无规范实例」。把它写成「启动即就绪 / 构造即 READY」即属不实。
> - **未接线/仅置位的部分**：默认路径为模拟器（未注入 `capability_executor` 时回退 `simulated`）；`GoalDecomposer` 为关键词启发式而非 LLM 规划；`replan` 钩子为空；`pause()`/`resume()` 仅置位状态；`execution:plan`/`execution:trigger` 权限在 authority A 内裁决（`execution:trigger` 对应 B 的 `execution.execute`）。
>
> **性质提示**：本规范整体是「目标态契约 + 已核对现状」，不是「现状规范」。已核对的现状以上述行号为准；未标注的段落（尤其是 §9 的缺口清单）以代码为准，待实测后修订。
>

## 1. 定义

Execution Kernel 是 Human-Sovereign Agent OS 的**编排内核**，负责把一句自然语言目标
（`Goal`）经过 `Goal → Task → Plan → Action → Verify` 全链路编排成可执行的动作序列并驱动
执行、校验与崩溃续跑。依据 Definition Lock §112，它是系统中唯一负责"把意图变成动作并
验证结果"的内核，严格单向依赖下层内核（`context` / `capability` / `event` / `resource` /
`_crosscutting`）与 `_journal`，**不依赖 `src.ai` 上层**——真实能力执行器由上层在装配时
通过 `set_capability_executor` 注入（`src/kernels/execution/__init__.py:33-36`）。

## 2. 目标

- **目标分解**：把自然语言 `Goal` 拆成结构化 `Task`（`GoalDecomposer.decompose`，`__init__.py:189`）。
- **计划构建**：按 `parallel/sequential/auto` 模式生成带依赖的 `ExecutionPlan`（`PlanBuilder.build`，`__init__.py:281`）。
- **动作执行**：经 `ActionExecutor.execute` 调能力注册表并支持重试/熔断（`__init__.py:341`）。
- **结果校验**：`Verifier.verify` 按期望输出对 `ActionResult` 评分（`__init__.py:467`）。
- **检查点与续跑**：经 `ExecutionJournal` 支持崩溃后断点续跑（`_journal.py:75`、`__init__.py:612`）。
- **可追溯**：逐任务/逐动作发 `event` 事件并共享 `correlation_id`（`__init__.py:594-670`）。

**当前实现与目标的差距（现状，基于代码实测，非引用外部审计文档）**：
- 默认路径是**模拟器**：`ActionExecutor._simulate_capability`（`__init__.py:450-458`）返回
  `status:"simulated"`，仅当注入 `capability_executor` 才真执行（`__init__.py:373`）。
- **关键词启发式分解**：`GoalDecomposer.decompose`（`__init__.py:189-272`）靠 `kw in goal_lower`
  匹配，注释自承"in production would use LLM"（`__init__.py:191`）——并非真正的 LLM 规划。
- **重试与 provider 重复**：手动 `while task.retry_count<=max_retries`（`__init__.py:790-846`）
  与 `src/ai/providers.generate_with_retry` 两套重试逻辑。
- **魔法数防死循环**：`_execute_plan` 用 `max_iterations=100` 占位（`__init__.py:680`）。
- **续跑按任务名匹配**：`_adopt_journaled_progress`（`__init__.py:712-742`）因 `Task.id` 用
  `uuid4()[:8]` 跨进程不一致（`__init__.py:200` 等），被迫用 `task.name` 作恢复键 → 重名任务误恢复。
- **死导入**：`resource` 内核导入后未使用（`__init__.py:28`）。
- **重复 DAG**：`get_ready_tasks` / 依赖解析自实现（`__init__.py:117-131`、`__init__.py:315-320`）
  与 `goal_task_graph.py`、死代码 `src/workflow` 重复同一套 DAG。

## 3. 生命周期（现状已核对）

对齐统一基座 `src/kernels/_base.py` 的 `KernelLifecycle`
（`UNINITIALIZED → INITIALIZING → READY ↔ PAUSED → STOPPED`，以及 `ERROR`）。

- **当前状态（已核对 HEAD）**：`ExecutionEngine`（`__init__.py:572`）**已接入 `_base`**，
  拥有 `lifecycle` 字段（`:574`，默认 `UNINITIALIZED`）与完整的 `initialize()/shutdown()/
  pause()/resume()` 方法（`:895/:898/:901/:906`）。`initialize()` 将 `lifecycle` 置为 `READY`，
  `shutdown()` 置 `STOPPED`，`pause/resume` 在合法状态间迁移并抛 `KernelStateError` 作非法迁移保护。
  它是构造后需显式 `initialize()` 的有状态编排器——**不会自动驱动到 READY**。
- **契约（目标态）**：
  - `initialize()`：构造 `Decomposer/Planner/Executor/Verifier/ContextKernel` 并确认就绪，
    进入 `READY`；装配 `capability_executor` 与 `journal` 应在此之后（`set_capability_executor`
    `__init__.py:576`、`set_journal` `__init__.py:580`）。
  - `shutdown()`：落盘最终 `execution_completed` 事件（`__init__.py:647-656`）、`journal.close()`
    （`_journal.py:174`），进入 `STOPPED`。
  - `pause()/resume()`：挂起/恢复 `_execute_plan` 主循环（`__init__.py:674`）。
- **错误转入 ERROR**：编排中出现不可恢复异常（如 `Verifier` 路径崩溃、注入执行器持续抛错）
  应置 `ERROR`，而非静默返回 `ActionResult(success=False)`。

**Goal→Task→Plan→Action→Verify 状态衔接**：
- `Goal`（`__init__.py:68`）→ `decompose` 产出 `Task`（`TaskStatus.PENDING`，`__init__.py:39`）。
- `PlanBuilder` 产出 `ExecutionPlan`（`PlanStatus.ACTIVE`，`__init__.py:50/607`）。
- `_execute_plan`（`__init__.py:674`）取 `get_ready_tasks`（`PENDING` 且依赖满足）→
  `_execute_task`：`RUNNING`（`__init__.py:759`）→ 成功 `COMPLETED` / 重试 `RETRYING`
  （`__init__.py:830`）/ 耗尽 `FAILED`（`__init__.py:845`）。
- `Action`（`__init__.py:135`）执行产出 `ActionResult`（`__init__.py:148`）。
- `Verifier.verify` 产出 `VerificationResult`（`VerifyResult`，`__init__.py:59/159`）→
  `replan_required` 触发重规划钩子（当前为空，`__init__.py:809-811`）。
- 终态：`PlanStatus.COMPLETED/PARTIAL/FAILED`（`__init__.py:642-645`）。

## 4. 输入模型

- `Goal`（`__init__.py:68`）：
  - `id: str`、`natural_language: str`、`scope: str = "L1"`、`metadata: Dict[str, Any]`、
    `created_at: datetime`、`correlation_id: str`。
- `execute_goal(goal, plan_mode="auto", verification_criteria=None) → ExecutionContext`
  （`__init__.py:584`）。
- `Action`（`__init__.py:135`）：`id / task_id / capability_id / capability_namespace /
  inputs / correlation_id / scope / timeout_seconds`。
- `CapabilityExecutor = Callable[[str, Dict[str, Any]], Any]`（`__init__.py:36`）——注入式真实执行器。
- `ExecutionJournal`（`_journal.py:75`）：可选持久化边车，`record/events/completed_task_names`。

## 5. 输出模型

- `ExecutionContext`（`__init__.py:171`）：含 `goal / plan / context_kernel /
  completed_tasks / failed_tasks / task_results: Dict[str, ActionResult] /
  verification_results: Dict[str, VerificationResult] / checkpoints`。
- `ActionResult`（`__init__.py:148`）：`action_id / success / output / error / duration_ms / retry_count`。
- `VerificationResult`（`__init__.py:159`）：`task_id / result / score / details / feedback /
  replan_required / escalation_required`。
- **事件**（经 `event` 内核）：`goal_decomposed / plan_created / task_started / task_completed /
  task_failed / action_completed / action_failed / execution_started / execution_completed /
  execution_resumed`（`__init__.py:264-670`）。
- **持久化**：仅当注入 `journal` 时，`ExecutionJournal` 落盘 `execution_started /
  task_started / task_completed / task_failed`（`__init__.py:744-751`）。

## 6. 错误处理（现状已核对）

对齐 `src/kernels/_base.py` 的异常族：
`KernelError`(基类) / `KernelNotInitializedError` / `KernelStateError` /
`KernelPermissionError` / `KernelCapabilityError` / `KernelConfigurationError`。

- 本 kernel **当前不抛任何标准异常**：所有失败均被包装成 `ActionResult(success=False, error=...)`
  （`__init__.py:353/443`）或 `VerificationResult(result=FAILED)`（`__init__.py:475`）。
- 应抛异常的点：
  - `capability_executor` 未注入且调用方要求真实执行 → `KernelCapabilityError`
    （当前仅回退模拟器，`__init__.py:397-400`）。
  - scope 校验失败（`__init__.py:346-358`）应抛 `KernelCapabilityError` 而非返回失败 `ActionResult`。
  - `journal` 初始化失败（`_journal.py:88`）应抛 `KernelConfigurationError`。
  - 非法 `plan_mode` / 循环依赖（`__init__.py:692-698`）应抛 `KernelStateError`。
- **当前缺失/错误（现状实测，非引用外部审计）**：
  - **续跑误恢复**：`_adopt_journaled_progress`（`__init__.py:712-742`）按 `task.name` 匹配，
    重名任务会被错误标为 `COMPLETED` 并注入伪造 `ActionResult`，掩盖真实失败。
  - 异常被 `try/except`（`__init__.py:431-448`）吞掉转 `ActionResult`——注入执行器抛错时
    调用方拿不到异常，仅拿到 `success=False`，不利于上层 `ERROR` 状态判定。

## 7. 权限边界（现状已核对）

- **所需 capability**：经 `check_capability_scope(action.capability_id, CapabilityScope(scope),
  namespace)`（`__init__.py:346-350`）检查执行动作的能力与 scope。
- **scope（L0-L7）**：`Goal.scope` 默认 `"L1"`（`__init__.py:73`），向下贯穿 `Task`/`Action`
  /事件（`EventScope`，`__init__.py:269` 等）。
- **与 policy/security 边界**：执行动作受 `@kernel_action("execution.execute")` 织入
  （`__init__.py:340`）记录判决，但默认 `enforce=False`，且 `_adjudicate` 不传
  `agent.*/resource.*` → 真实拦截未生效（以代码实测为准，需重测确认当前拦截是否落地）。
- **当前越界/重复（现状实测）**：
  - **三套 DAG 重复**：`execution` 自实现依赖解析（`__init__.py:117-131`、`315-320`）与
    `src/ai/goal_task_graph.py`、死代码 `src/workflow` 重复的同一套有向无环图。
  - 重试基元与 `src/ai/providers.py` 重复，应收敛为共享原语。
  - `resource` 内核被导入却未消费（`__init__.py:28`），配额约束未真正接入执行路径。

## 7.1 幂等与重入语义（ACTION-01 / TOOL-04，现状已核对）

副作用去重靠三条相互独立的机制：稳定 key、默认 fail-closed 的判定、以及「执行前占坑
→ 执行 → 落账」的账本。缺任何一条，发消息 / 调外部接口 / 写记忆这类副作用都可能被重放。

### 7.1.1 稳定 action id 会传到执行器

- `make_action_id(goal_id, task_name, attempt=0)`（`src/kernels/execution/__init__.py:203`）
  产出 `act-<sha1(goal_id:task_name) 前 12 位>-<attempt>`。`Task.id` 是 `uuid4()[:8]`，
  跨进程每次重跑都会变，**不能**当幂等键；同一次执行的所有重试复用同一个 action id。
- 执行器契约支持可选关键字参数
  `executor(capability_id, inputs, *, action_id: str | None = None)`。
  `invoke_capability_executor`（`:226`）先做签名内省（`executor_accepts_action_id`，
  `:214`），只把 `action_id` 传给声明了该参数（或 `**kwargs`）的执行器；旧签名
  `executor(capability_id, inputs)` 不会因多传参数而 `TypeError`。`action_id` 走显式
  关键字、不塞进 `inputs`，避免被业务字段覆盖。

### 7.1.2 默认 fail-closed：名单外一律按不可重入

- 只有 `REENTRANT_CAPABILITIES`（`__init__.py:153` 起）里的天然幂等能力免去重：
  `file_write`、`python_compute`、`file.read`、`file.list`、`memory.search`、
  `audit.query`、`system.status`、`preview_stop`。
- 判定 `capability_is_reentrant`（`:193`）是**白名单**：不在名单内即为不可重入。
- 另有一份**硬副作用**清单 `HARD_SIDE_EFFECT_CAPABILITIES`（`:179-185`）：
  `network_bus`、`personal.write`、`multi_tier_memory`、`preview_start`、
  `execution_pipeline`。它**不参与**去重判定，只用于「账本缺席时该拒绝谁」（见 7.1.5）。
  `NON_REENTRANT_CAPABILITIES`（`:190`）是历史名字，语义已等同于这份硬副作用清单。
- 新增能力默认落在「不可重入」一侧，不需要有人记得把它加进黑名单；但新增
  「不可撤销外部后果」的能力时**必须**加进 `HARD_SIDE_EFFECT_CAPABILITIES`，
  否则在无账本部署里它会被当作可安全重入而放行。
- `_action_idempotency_key`（`:1367`）只对不可重入能力返回键；把幂等能力强制
  走账本，会把一次无害重试误报成「重放」。

### 7.1.3 占坑 → 执行 → 落账

1. 执行前 `journal.claim_idempotency(key, execution_id, ...)` 原子占坑
   （`src/kernels/execution/_journal.py` 的 `INSERT OR IGNORE` + 主键唯一；跨进程
   共用一个 SQLite 文件时也只有一个调用方能赢）。
2. 占坑失败 → `_replay_action`（`:1480`）：
   - 已落账：直接返回首次结果（`replayed=True`），**不再执行**；
   - **有占坑、无结果**（副作用已发生但结果没写进账本）：`status="replayed_unknown"`、
     `success=False`，错误文案「该动作已执行过但结果未落账（结果未知），已停止重复执行；
     请人工确认后再决定是否重试」，**不再执行**。
3. 执行后 `_settle_idempotency`（`:1536`）：
   - 成功 → `remember_idempotent_result` 落账；
   - 超时 → **保留占坑不释放**（工具可能在超时前已提交副作用）；
   - 只有执行器**显式**返回 `success: False` → `release_idempotency`（允许下次真跑）；
   - 缺 `success` 字段或返回非 dict → 结果未知，**保留占坑**。

### 7.1.4 崩溃续跑的两条分界

`_adopt_journaled_progress`（`:2236`）按 journal 事件区分「已领取未开始」与
「已开始」：

| journal 里有什么 | 含义 | 续跑动作 |
| --- | --- | --- |
| 只有 `task_started`，没有 `task_exec_started` | 已领取、执行尚未开始 → 不可能有副作用 | **允许自动重跑**（`task_requeued`，reason=`no_exec_started`） |
| 有 `task_exec_started` 且能力天然幂等 | 副作用可重放（同路径覆盖写 / 纯计算 / 只读） | **允许自动重跑**（reason=`reentrant_capability`） |
| 有 `task_exec_started` 且能力非幂等 | 副作用可能已发生、结果未知 | 标 `FAILED`、落 `task_interrupted_unknown`，错误「执行中断：结果未知…已停止自动重跑；请人工确认后再决定是否重试该任务」，**不自动重跑** |
| `ctx.explicit_retry_names` 命中 | 人工已显式确认可以再跑一次 | 执行一次，并释放该任务「已占坑但无结果」的幂等账（键不变，仍是同一个 action id） |

`task_exec_started` 是在真正调用执行器之前落的一条，因此它是
「副作用可能已经开始」的分界线；只有它之前的 `task_started` 才算「已领取未开始」。
另有一条独立的动作级兜底：同一 action id 命中「有占坑无结果」时同样转人工，而不是
靠任务级事件推断。

### 7.1.5 账本缺席 / 写不进：分档与健康面（现状已核对）

账本（`ExecutionJournal`）有两种「不可用」，处理方式不同，都不静默：

| 情形 | 实现 | 动作 |
| --- | --- | --- |
| 构造时没注入账本（`journal=None`） | `_ledger_missing_refusal`（`:1382`） | **硬副作用能力拒绝执行**（`HARD_SIDE_EFFECT_CAPABILITIES`，见 7.1.2），返回中文可解释的失败并打 `evt=idempotency_hard_side_effect_refused`；只读/可安全重入能力**放行**，记一条告警 |
| 账本存在但写入抛异常（磁盘只读/权限不足） | `_ledger_unavailable_result` | **不可重入动作一律拒绝执行**（fail-closed），避免「无法证明只执行一次」时赌副作用；打 `evt=idempotency_ledger_write_failed`，错误文案提示修复 journal 写入权限 |

告警不是只躺在日志里：`dedupe_advisories()`（`:338`）把快照交给健康/就绪面，
同一 code 只记一条、`occurrences` 持续累加（`_record_dedupe_advisory`，`:293`）。
三个 code 与显示文案：

- `hard_side_effect_without_ledger` → 「硬副作用已拒绝（账本未接入）」
- `journal_write_failed` → 「幂等账本不可写」
- `ledger_missing_safe_replay` → 「幂等账本未接入（安全能力已放行）」

健康面 `checks.idempotency_dedupe`（`src/gateway/health.py:491`、`:1265`）：

- 无告警 → `status="healthy"`，显示「幂等去重正常」，文案「幂等账本已接入：不可重入动作的重复投递会复用首次结果。」
- 有告警 → `status="degraded"`、`advisory=True`、**`blocks_readiness=False`**，并带
  `codes` / `capabilities` / `occurrences` / `first_seen_at` / `last_seen_at`；
  硬副作用被挡下时 `user_message` 末尾追加「本次被挡下的硬副作用能力：…」。
- `blocks_readiness=False` 的语义是：**它不让业务断服、不参与 503 判定**
  （`health.py:1282-1291` 把 `blocking_unhealthy` 与 `advisory_checks` 分开），
  但总览必须看得到。**不要把它读成「检查通过」**。
- 告警按 code 去重，`occurrences` 才是实际发生次数；同一 code 只产生一条告警对象。

### 7.1.6 L-Core 直路由路径（绕过 ExecutionEngine 的那条）

`LCore.handle_intent`（`src/ai/lcore.py`）不走 `ExecutionEngine`，而是直接
`ToolRouter.execute`。它必须和内核**共用**同一套判定，否则执行内核的闸门旁边
就有一扇常开的门：

- **闸门共用**：执行前调用 `evaluate_ledger_gate(capability_id, journal_available=...)`
  （`src/kernels/execution/__init__.py`）。账本缺席时硬副作用拒绝、只读/可重入放行，
  与 7.1.5 完全一致；拒绝文案、`status` 也来自同一个函数。
- **稳定幂等键**：`handle_intent(..., action_id=...)` 的 `action_id` 是调用方给的
  稳定标识。传入时它直接作为 goal id，每个任务的账本键由
  `make_action_id(goal.id, task.name)` 派生（与 ExecutionEngine 同一个函数），
  于是同一意图被重复投递/重试/重启续跑会命中同一占坑：第二次不再执行副作用，
  回放首次结果（输出带 `replayed_from`，`task_result_text` 渲染成
  「已复用首次执行结果 …，未重复执行副作用」）。
  占坑/回放/落账分别复用 `replay_from_journal` 与 `settle_idempotency`，
  超时保留占坑、明确 `success: False` 才释放的规则与内核一致。
- **缺省不复用**：`action_id=None` 时每次生成新的 goal id，**不占坑、不回放**，
  与改动前行为一致；跨调用没有重复保护，只剩「账本缺席时拒绝硬副作用」这一道
  静态闸门。`e2e_demo` / `vhl_benchmark` 属于这一类（它们只需要能跑通，不需要
  跨调用去重）。
- **失败进事件流**：拒绝时发布 `action_failed`（`source="lcore"`，带
  `status` / `capability_id` / 中文 `error`），回放时发布 `action_replayed` /
  `action_replayed_unknown`。这样以后即使有人把 `handle_intent` 接进网关，
  也不会出现「界面显示成功、实际被闸门拒绝」的静默失败。
- **残余**：L-Core 不跑 `check_capability_scope`（scope 校验仍由工具注册表的
  可用性承担），也不写 `execution_*` 系列事件；它的事件只有上面三类。这条路径
  目前没有 HTTP 入口，网关只把 `LCore` 当能力执行器与能力目录来源。

## 8. 测试要求

通过 DoD（Implemented + Tested + Observable + Permissioned + Audited + Documented）必须满足：

- **单元**：`GoalDecomposer.decompose` 关键词映射正确性；`PlanBuilder` 三种 mode 的依赖产出；
  `Verifier.verify` 在 `score>=0.7` 阈值（`__init__.py:522`）与无 criteria（`__init__.py:492`）
  分支；`get_ready_tasks` 依赖解析（`__init__.py:117`）。
- **边界**：`ActionExecutor` 注入 `capability_executor` 返回 `success:False` 时，必须如实
  透传失败（`__init__.py:378-388`），不得被误判成功。
- **失败路径测试**：
  - **续跑误恢复**：构造两个同名 `Task`，验证 `task.name` 匹配键不会把未完成/失败任务错误恢复
    （`__init__.py:712-742`）——必须改用确定性 task id（基于 goal+name 哈希）。
  - 重试耗尽后保留根因（`__init__.py:840-846`）：验证 `task.error` 含 "last error"。
  - 死锁检测：`_execute_plan` 在循环依赖时把任务标 `FAILED`（`__init__.py:692-698`）。
  - 模拟器标记：未注入执行器时 `output["status"]=="simulated"`（`__init__.py:453`）。
- **集成**：`journal` 挂载后，二次 `execute_goal` 同 goal 应跳过已完成任务（`__init__.py:623-635`）。

## 9. 目标态契约与已知缺口（尚未实现，待代码实测）

以下条目为**目标态契约**（本规范希望 execution 达到的状态），并非全部已在 HEAD 实测确认；
落地前须以当前代码重新核实，不可照抄：

- `replan` 钩子（`:809-811`）当前为空，应在 `replan_required` 时触发真正的重规划而非空转。
- `pause()/resume()` 当前仅置位 `lifecycle`，未真正挂起/恢复 `_execute_plan` 主循环（`:674`）。
- 异常吞掉转 `ActionResult` 的若干点应改为抛出标准 `Kernel*` 异常，以支持上层 `ERROR` 判定。
- 配额约束（`resource` 内核）应真正接入执行路径，而非仅导入未消费（`:28`）。
- DAG 依赖解析应收敛为单一共享原语（消除与 `goal_task_graph.py` / `src/workflow` 的三套重复）。
