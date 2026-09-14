# Phase 3 — Agent Runtime 设计文档 (AGENT-RUNTIME-DESIGN)

> 阶段目标：把"调用 LLM 的脚本"升级为"有身份/权限/记忆/执行/评估/扩展能力的 Agent 运行时"。
> 本阶段只做**增量式编排层 + 一处根因加固**，不重写、不破坏任何既有公开 API。
>
> 设计原则（来自项目硬性要求）：
> 1. 不增加代码量优先 —— 复用既有内核，新增 1 个编排文件 + 1 处 3 行加固。
> 2. 所有改动可回滚 —— 新增文件整体删除即回滚；加固为单处 Edit。
> 3. 不破坏已有 API —— 既有 `TaskStatus` / `ExecutionEngine.execute_goal` / `Evaluator.evaluate` 签名保持不变。
> 4. 先设计方案，再编码 —— 本文即方案。

---

## 1. 当前状态（实测事实，均带 file:line）

| 模块 | 事实 | 位置 |
|------|------|------|
| 执行内核 SSOT | `ExecutionEngine(scope, capability_executor=None, journal=None)`；`execute_goal(goal, plan_mode="auto", verification_criteria=None) -> ExecutionContext` | `src/kernels/execution/__init__.py:557-591` |
| 任务状态 | `TaskStatus(str,Enum)`: PENDING/PLANNED/RUNNING/COMPLETED/FAILED/SKIPPED/RETRYING | `:40-48` |
| 重试 | `while task.retry_count <= task.max_retries`（默认 max_retries=3，最多 4 次尝试） | `:792`, `:95` |
| 验证器崩溃点 | `Verifier.verify` 在 `actual = action_result.output` 后 `if key in actual` —— output 为 `None`/非 dict 时抛 `TypeError` | `:491`, `:508` |
| 重规划空洞 | `if verification.replan_required: pass`（L811-813 纯 no-op） | `:811-813` |
| 事件 | `EventBus.publish` 是**同步内联**派发，handler 异常被吞入 DeadLetter（非异步通道） | `src/kernels/event/__init__.py:150-174` |
| 评估器孤儿 | `Evaluator.evaluate` / `evaluate_outcome` **无任何运行时调用方** | `src/kernels/evaluation/__init__.py:153,580` |
| 评估器输出 | `EvaluationResult` 含 `replan_triggered`/`escalation_required`/`human_checkpoint`/`outcome`/`overall_score` | `:84-99` |
| 记忆内核 | `get_memory_kernel().store(key,value,tier=MID_TERM,scope=L1,tags,ttl)` 为 SQLite upsert（同 key 安全） | `src/kernels/memory/__init__.py:273,541` |
| 记忆分层 | `MemoryTier`: SHORT_TERM/MID_TERM/LONG_TERM/PERSISTENT | `:29-34` |
| 离线工作流 | `src/ai/goal_task_graph.py` (LLM 驱动) 仅被 `langgraph_workflow.py` 引用，**非 SSOT** | 研究确认 |

**结论**：`ExecutionEngine` 是正确且唯一的执行编排权威；`goal_task_graph` 保持离线（不接入）。评估器与记忆内核是"已实现但无人接线"的能力，本阶段把它们接进主链路。

---

## 2. 目标能力（用户要求的 7 项）

1. Goal → Planner → Task Graph → Executor → Observer → Evaluator → Memory Update 闭环
2. Task State Machine：CREATED / PLANNING / EXECUTING / WAITING / VERIFYING / COMPLETED / FAILED
3. Execution Trace（输入/决策/工具调用/模型调用/输出/错误，按 correlation_id 串联）
4. Failure Recovery：retry（已有） + rollback（重放/幂等） + replan（填补 L811 空洞）
5. 评估器接线（让 `replan_triggered` / `escalation_required` 真正触发）
6. 记忆更新（Episodic 运行记录 + Semantic 学习沉淀，为 Phase 4 打底）
7. 全部复用既有内核，新增代码不破坏既有 API

---

## 3. 架构

```
                  ┌─────────────────────────────────────────────┐
   goal_text ───▶ │            AgentRuntime (新增编排层)          │
                  │  src/ai/agent_runtime.py                      │
                  │                                               │
   ┌──────────────┤  state: AgentRunState (CREATED→…→COMPLETED)  │
   │              │                                               │
   │  ① PLANNING  │  GoalDecomposer + PlanBuilder                 │ (复用, 不改)
   │  ② EXECUTING │  ExecutionEngine.execute_goal()  ── SSOT ──▶ │ (复用, 不改)
   │  ③ OBSERVE   │  AgentObserver 订阅 EventBus(correlation_id) │ (复用 EventBus)
   │  ④ VERIFYING │  Evaluator.evaluate(intended, actual)        │ (接线孤儿)
   │  ⑤ MEMORY    │  MemoryKernel.store(episodic+semantic)       │ (接线孤儿)
   │  ⑥ RECOVER   │  replan/retry/rollback 钩子                   │ (填补 L811)
   └──────────────┘                                               │
                  └─────────────────────────────────────────────┘
```

**关键决策**：
- `AgentRuntime` **组合** `ExecutionEngine`，不继承、不 fork。引擎内部零改动即可被包装，因其 `execute_goal` 已是完整闭环。
- Observer 通过 `subscribe_event(event_type, handler, correlation_id=...)` 只收本目标链路事件；handler **永不抛异常**（EventBus 会把异常丢进 DeadLetter 且静默），所有异常在 handler 内 try/except 吞掉并记进 trace。
- 评估器在 `execute_goal` 返回**之后**接线：用 `ExecutionContext` 构造 `actual_outcome`，调 `Evaluator.evaluate`。这样 `replan_triggered` 真实生效。
- 记忆更新：运行结束写一条 Episodic（整轮成败/评估分数/反馈），并把 `FeedbackEntry.suggested_actions` 沉淀为 Semantic（可跨轮复用）。

---

## 4. Task State Machine

用户要求的 7 态分为两层，**均不改动既有 `TaskStatus`**：

### 4.1 目标级运行态 `AgentRunState`（新增枚举，描述"整轮目标"）
`CREATED → PLANNING → EXECUTING → VERIFYING → COMPLETED | FAILED`
（`WAITING` 作为 EXECUTING 期间的子观察态：存在依赖未满足的任务时进入，依赖满足即回 EXECUTING）

### 4.2 任务级态映射（落到既有 `TaskStatus`，仅文档化，不改枚举）
| 用户态 | TaskStatus | 说明 |
|--------|-----------|------|
| CREATED | PENDING | 分解完成前 |
| PLANNING | PLANNED | `PlanBuilder.build` 后（引擎内部设定） |
| EXECUTING | RUNNING | `task.status=RUNNING`（`:761`） |
| WAITING | （派生观察） | `get_ready_tasks` 为空且仍有 PENDING 时，运行时观测态，不存为枚举值 |
| VERIFYING | （派生观察） | `Verifier.verify` 执行期间，运行时观测态 |
| COMPLETED | COMPLETED | `:797` |
| FAILED | FAILED | `:847` |

> `WAITING` / `VERIFYING` 是 **AgentRuntime 派生观测态**，不新增到 `TaskStatus` 枚举，避免触碰既有 API（满足"不破坏已有 API"）。

---

## 5. Execution Trace

`ExecutionTrace` 数据类（新增），按 `correlation_id` 收集：
- `entries: List[TraceEntry]`，每条含 `task_id, event_type, ts, capability, decision, tool_call, model_call, output, error`
- 由 `AgentObserver` 在事件回调中追加（`task_started/task_completed/task_failed/task_retrying/execution_completed`）
- `EventBus` 同步派发 → trace 与执行同序；`get_event_chain(correlation_id)` 可作为只读校验源

---

## 6. Failure Recovery

| 机制 | 现状 | 本阶段处理 |
|------|------|-----------|
| retry | 引擎内 `max_retries` 已生效（`:792`） | 不改，AgentRuntime 透传 `verification_criteria` |
| **replan** | `:811-813` 是 `pass` 空洞 | **在运行时层填补**：读完 `ctx.verification_results`，凡 `replan_required=True` 或 `evaluation.replan_triggered=True`，调用 `AgentRuntime.replan()` —— 重新 `decompose` 并对未完成任务调 `execute_goal`（幂等：引擎 journal 按 `task.name` 续跑，`:714-744`） |
| **rollback** | 无 | v1 = **重放即回滚**：因引擎对已完成任务按 name 幂等恢复（`:730-743`），重跑 `execute_goal` 不会重复副作用。提供 `AgentRuntime.recover(goal)` 公开方法；跨服务副作用的真正回滚（补偿事务）列入 Phase 7 生产化待办，**本阶段诚实标注为 out-of-scope** |

---

## 7. 根因加固：`Verifier.verify` 空输出崩溃（必做）

`src/kernels/execution/__init__.py:491,508` 当 `action_result.output` 为 `None`/非 dict 且存在验证标准时抛 `TypeError`，导致整轮执行在中途崩溃（而非优雅判失败）。

**修改（3 行，单处 Edit，可回滚）**：在 `verify` 取 `actual` 后加守卫：
```python
actual = action_result.output
if not isinstance(actual, dict):
    return VerificationResult(
        task_id=task.id, result=VerifyResult.FAILED, score=0.0,
        feedback=f"Action output is not a dict: {type(actual).__name__}",
        replan_required=True,
    )
```
- 不改变成功路径；仅把"崩溃"变成"判失败 + 触发重规划"，与 `action_result.success=False` 分支（`replan_required=True`）行为一致。
- 风险：极低（只命中崩溃输入）。回滚 = 删除该守卫。

---

## 8. 新增公开 API（`src/ai/agent_runtime.py`，全新增，不破坏任何既有）

```python
class AgentRunState(str, Enum):          # CREATED/PLANNING/EXECUTING/WAITING/VERIFYING/COMPLETED/FAILED
class TraceEntry: ...                     # 单条 trace
class ExecutionTrace: ...                 # entries + summary()
class AgentRunResult: ...                 # goal_id, correlation_id, state, context, evaluation, trace, memory_keys
class AgentObserver:                      # subscribe(correlation_id)/trace
class AgentRuntime:
    def __init__(self, scope="L1", capability_executor=None, journal=None,
                 evaluator=None, memory=None) -> None
    def run_goal(self, goal_text, *, goal_id=None, scope=None,
                 plan_mode="auto", verification_criteria=None,
                 persist=True, correlation_id=None) -> AgentRunResult
    def replan(self, prior: AgentRunResult, *, extra_criteria=None) -> AgentRunResult
    def recover(self, goal: Goal) -> AgentRunResult   # 幂等重放
def get_agent_runtime(...) -> AgentRuntime             # 进程内单例（与 get_evaluator/get_memory_kernel 同哲学）
```

---

## 9. 回滚方案

- `src/ai/agent_runtime.py` 为纯新增文件 —— `git rm` 即整体回滚，无既有调用方（除本阶段测试）。
- `Verifier.verify` 守卫为单处 Edit —— 删除该块即回滚。
- 不修改 `ExecutionEngine` / `Evaluator` / `MemoryKernel` / `EventBus` 的公开签名。

---

## 10. 测试计划（新增 `tests/ai/test_agent_runtime.py`）

1. `test_run_goal_happy_path`：用默认模拟执行器跑一个含 "search" 关键词的目标，断言 `state==COMPLETED`、`trace` 非空、`memory_keys` 非空。
2. `test_observer_builds_trace`：断言 trace 含 `task_started` 与 `task_completed` 各至少一条，且 `correlation_id` 一致。
3. `test_verifier_none_output_no_crash`（根因）：构造 `ActionResult(success=True, output=None)` + 有 expected，断言返回 `FAILED`/`replan_required=True`，不抛异常。
4. `test_replan_on_replan_required`：注入一个验证失败任务，断言 `replan()` 返回新 `AgentRunResult` 且 `state` 合理。
5. `test_evaluator_wired`：断言 `AgentRunResult.evaluation` 非 None 且 `replan_triggered` 字段可读。
6. `test_memory_persist`：断言 `get_memory_kernel().recall` 能取回 Episodic 记录。

运行：`tests/ai/` 子集（避免全量 pytest 的 MemoryError / 环境抖动，见 Phase 2 记录）。

---

## 11. 风险

- **EventBus 同步吞异常**：Observer handler 必须零异常，否则事件丢失且无提示 → 已在设计上强制 try/except。
- **replan 无限循环**：`replan()` 设最大重规划次数（默认 2），避免对永远失败的目标死循环。
- **记忆写入放大**：每轮写 Episodic + 可能的 Semantic；用 TTL（MID_TERM）限流，不写 PERSISTENT（除非评估通过且明确）。
- **Verifier 加固回归**：仅命中崩溃输入，成功路径逐字节不变（附测试守护）。

---

## 12. 实施步骤（编码顺序）

1. 写 `src/ai/agent_runtime.py`（枚举 + Observer + Runtime + 单例）。
2. 3 行加固 `Verifier.verify`。
3. 写 `tests/ai/test_agent_runtime.py`。
4. 跑 `tests/ai/` 子集 + 受影响的 `tests/kernels/execution/` 相关用例。
5. 出变更报告 + 风险说明（本消息即报告）。
6. 不提交（保持可回滚，等用户确认后再 commit）。
