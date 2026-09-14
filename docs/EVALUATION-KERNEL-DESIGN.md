# Phase 6 — Evaluation Kernel 设计文档 (EVALUATION-KERNEL-DESIGN)

> 阶段目标：让评估从"能打分"升级为**自动评估 + 指标 + 评估报告**，为长期演进提供可度量信号。
> 复用既有 `Evaluator`（阶段 3 已接线进 AgentRuntime），不重造、不破坏 API。

---

## 1. 当前状态（实测事实，带 file:line）

| 事实 | 位置 |
|------|------|
| `Evaluator.evaluate(intended_goal, actual_outcome, criteria, scope, correlation_id, task_id) -> EvaluationResult` | `src/kernels/evaluation/__init__.py:153-219` |
| `EvaluationResult`：outcome / overall_score / criteria_results / feedback / feedback_type / replan_triggered / escalation_required / human_checkpoint / 时间 / correlation_id | `:84-99` |
| `EvaluationOutcome`（SUCCESS/PARTIAL/OFF_TRACK/FAILED/NEEDS_HUMAN）、`FeedbackType`、`EvaluationCriteria` | `:28,49,58` |
| `FeedbackEntry`（feedback_type/message/suggested_actions/priority/applied） | `:111-123` |
| 历史访问：`get_evaluation_history(goal_id, outcome, limit)`、`get_feedback_history(...)`、`get_replan_requests(...)` | `:452-498` |
| **`stats()` 只是原始计数**（total/by_outcome/feedback/replan 数），**无成功率/均值/报告** | `:532-546` |
| 阶段 3 已把 `Evaluator` 接进 `AgentRuntime.run_goal`（自动评估，落在主链路） | `src/ai/agent_runtime.py`（`_evaluate`） |
| Prometheus 指标基建**已存在**（`get_metrics_registry()` / `generate_metrics()` + `track_*` 助手） | `src/observability/metrics.py:240-408` |

**结论**：Phase 6 = 在既有 `Evaluator` 之上加一层**纯聚合（报告 + 指标）**，并把"自动评估"从"单次接线"补成"可汇总的信号"。
Prometheus 真正落盘/告警属**阶段 7 生产化**，本阶段只产出结构化指标字典 + 人读报告。

---

## 2. 架构

```
   AgentRuntime.run_goal ──(阶段3)──▶ Evaluator.evaluate ──▶ _evaluation_history
                                                              │
                                       src/ai/eval_report.py  │ (新增聚合层)
                                       ┌──────────────────────▼──────────────────┐
                                       │ build_evaluation_report(results)         │ 纯函数
                                       │ generate_report(evaluator, ...)          │ 拉历史
                                       │ EvaluationReport (success_rate/avg/...)  │
                                       │ render_report_markdown(report)           │ 人读报告
                                       │ to_metrics(report) -> Dict[str,float]    │ 指标字典
                                       └─────────────────────────────────────────┘
```

**关键决策**：报告层是**纯函数式**（输入 `EvaluationResult` 列表，输出报告），不持有状态、不写库、不触网 →
易测、可回滚。唯一副作用入口 `generate_report()` 只读调用既有 `get_evaluation_history()`。

---

## 3. EvaluationReport 字段

| 字段 | 含义 |
|------|------|
| `total` | 窗口内评估数 |
| `outcome_distribution` | {outcome: count} |
| `success_rate` | success / total |
| `avg_score` | overall_score 均值 |
| `replan_rate` / `escalation_rate` / `human_checkpoint_rate` | 三率 |
| `feedback_by_type` | {FeedbackType: count} |
| `top_failures` | 非成功样本的 feedback 频次 Top-N（驱动改进） |
| `by_goal` | goal_id → {total, success, avg_score} |
| `since` / `until` / `generated_at` | 窗口与生成时间 |

---

## 4. 新增公开 API（`src/ai/eval_report.py`，全新增）

```python
@dataclass EvaluationReport: ...
def build_evaluation_report(results: List[EvaluationResult], *, since=None, until=None, top_n=5) -> EvaluationReport
def generate_report(evaluator=None, *, limit=1000, since=None, until=None, top_n=5) -> EvaluationReport
def render_report_markdown(report: EvaluationReport) -> str
def to_metrics(report: EvaluationReport) -> Dict[str, float]
```

---

## 5. 回滚方案
- `src/ai/eval_report.py` 纯新增 → `git rm` 即回滚；无既有调用方。
- 不改 `src/kernels/evaluation/*`、不改 `src/observability/*` 任何签名。

---

## 6. 测试计划（`tests/ai/test_eval_report.py`）
1. `test_empty_report`：无样本 → total=0、success_rate=0.0（不除零）。
2. `test_success_rate_and_avg`：构造 4 条已知 outcome/score → 校验成功率、均值、分布。
3. `test_rates_and_feedback_types`：replan/escalation/human_checkpoint 三率 + feedback_by_type。
4. `test_top_failures`：非成功样本 feedback 频次 Top-N 正确。
5. `test_window_filter`：since/until 过滤生效。
6. `test_by_goal_rollup`：多 goal 汇总正确。
7. `test_render_markdown`：报告含关键字段文本。
8. `test_generate_report_from_evaluator`：向 `get_evaluator()` 写入样本后 `generate_report()` 能读回。

运行：`tests/ai/` 子集，离线。

---

## 7. 风险
- 报告基于**内存历史**（`Evaluator` 最近 1000 条），非持久化统计 → 跨进程不累积；持久化聚合留阶段 7。
- 不新增 Prometheus 指标（避免重复注册/耦合），只给结构化字典 → 阶段 7 接 Grafana 时再挂。
