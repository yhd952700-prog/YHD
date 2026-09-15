# Evaluation Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **真实实现**：`src/kernels/evaluation/__init__.py:141` 的 `Evaluator`。`lifecycle` 字段见 :143；`initialize/shutdown/pause/resume` 见 :548/:551/:554/:559。
> - **进程级入口**：`get_evaluator()`（`src/kernels/evaluation/__init__.py:570`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。`Evaluator` 已接入生命周期协议；存在即 READY（首次使用时惰性构造并 `initialize()`）。`shutdown/pause/resume` 无后台驱动方，仅由显式调用者触发。
> - **本文档性质**：目标态契约 + 已核对现状。标注「目标态（尚未实现）」者为设计意图；未标注者以代码为准。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。

## 1. 定义

Evaluation Kernel 是 Human-Sovereign Agent OS 的**结果评估与反馈闭环内核**，负责把执行结果对照目标做评分、生成结构化反馈、在目标未达标时触发重规划（replan）并升级人工（human checkpoint）。它产出 `EvaluationResult`（成功/部分/偏离/失败/需人工），在架构上应当接在 execution 后步钩子之后。

## 2. 目标（现状可实现的能力）

- 对照目标评估实际产出：`evaluate(intended_goal, actual_outcome, criteria, scope, task_id)`（`src/kernels/evaluation/__init__.py:150`，`@kernel_action("evaluation.evaluate")`）。
- 结构化反馈：`EvaluationResult.feedback` / `feedback_type` / `replan_triggered` / `escalation_required` / `human_checkpoint`（:82-98）。
- 触发重规划与人工升级：`_create_replan_request`（:415），`approve_replan`/`execute_replan`（:509/:519）。
- 反馈闭环：应用反馈 `apply_feedback`（:498），历史可追溯 `get_evaluation_history`（:450）。
- 五类结果语义：`SUCCESS`/`PARTIAL`/`OFF_TRACK`/`FAILED`/`NEEDS_HUMAN`（`EvaluationOutcome`，:28）；反馈分 `POSITIVE/CORRECTIVE/WARNING/CRITICAL` 四类（`FeedbackType`，:48）。

## 3. 生命周期（现状）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- `Evaluator`（`src/kernels/evaluation/__init__.py:141`）已实现 `lifecycle` 字段（:143）与四个方法（:548-559）。原稿称「当前无 lifecycle」不实。
- **单例**：`get_evaluator()`（:570）为进程级惰性单例，构造后立刻 `initialize()`，不变量为「存在即 READY」。
- **无后台驱动**：`shutdown`/`pause`/`resume` 仅由显式调用者触发；`pause`/`resume` 当前仅置位状态（PAUSED 语义「缓冲 evaluate 调用」属目标态，见第 8 节）。

## 4. 输入模型

- `evaluate(intended_goal: Dict[str,Any], actual_outcome: Dict[str,Any], criteria: Optional[EvaluationCriteria]=None, scope: str="L1", correlation_id: Optional[str]=None, task_id: Optional[str]=None) -> EvaluationResult`（:150）。
- `EvaluationCriteria`（:56）：`success_threshold=0.9`、`partial_threshold=0.7`、`off_track_threshold=0.4`、`max_retries`、`require_all_critical`、`custom_weights`。
- 比较逻辑 `_compare_values`（:259）：精确相等=1.0；数值按相对差，阈值 `>=0.9`（:274）；字符串按词重叠 `>=0.8`（:281-285）；列表/集合重叠 `>=0.8`（:294）；字典逐键递归、`>=0.9`（:306）。
- 决策 `_determine_outcome`（:329）：空 criteria 集判 SUCCESS（:337）；任意 `critical_` 前缀判据失败强制 FAILED（:341-349）；否则按 `overall_score` 落入四档阈值。
- 便利函数 `evaluate_outcome`（:563）转发至全局 `Evaluator`（`get_evaluator`，:570）。

## 5. 输出模型

- `EvaluationResult`（:82）：`evaluation_id`、`goal_id`、`task_id`、`outcome`、`overall_score`、`criteria_results`、`feedback`、`replan_triggered`、`escalation_required`、`human_checkpoint`、`correlation_id`。
- `FeedbackEntry`（:109）与 `ReplanRequest`（:125）写入内存历史；`_evaluation_history` 上限 1000（:207）。
- `stats()`（:530）返回按 outcome 分布与未应用反馈计数。
- **当前无持久化**：进程重启即失，且无事件总线发出（`replan_triggered` 仅内存，未被消费）。

## 6. 错误处理（对齐 `src/kernels/_base.py`）

- `KernelNotInitializedError`：READY 前调用 `evaluate`。
- `KernelConfigurationError`：`EvaluationCriteria` 阈值非法（如 `success_threshold>1`）应抛。
- `KernelPermissionError`：scope 若未来接入 Policy 裁决，越界应抛；当前 `scope` 处理待核实（见第 8 节）。
- **目标态（尚未实现）**：`evaluate` 内 `_compare_values` 抛非预期异常应转入 ERROR 而非返回 `score=0.0` 静默（当前 `except` 吞异常，:308）。

## 7. 权限边界

- 权限名（冒号形式，authority A）见 `src/kernels/security/_permission_map.py`：`evaluation:run`（operator，对应 authority B 的 `evaluation.evaluate`）。访问控制收敛设计见 `docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md`。
- Evaluation 应作为 execution 后步钩子被调用，将 `replan_triggered` 真正驱动 execution 重规划——当前二者接线状态属目标态（见第 8 节）。

## 8. 目标态契约与已知缺口（尚未实现，待代码实测）

以下为设计契约要求与当前实现的**待核实差距**，非以 HEAD 验证过的事实；不得引用任何已删除的审计文档。

- 接线：评估内核是否真正接入运行时（作为 execution 后步钩子、`replan_triggered` 是否驱动重规划）——原稿称「全仓无调用方、未实现」属未核实断言，已删除；需代码实测核对。
- `scope` 参数是否真正被消费（:156、:567）——待核实，不得作为现状事实陈述。
- 阈值一致性：数值 `>=0.9`、列表 `>=0.8`、字典 `>=0.9` 三档阈值差异是否有意，需代码实测。
- 历史上限：连续 >1000 次 evaluate 不丢非自身条目（:207）。
