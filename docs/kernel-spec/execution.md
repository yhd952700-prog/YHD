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
