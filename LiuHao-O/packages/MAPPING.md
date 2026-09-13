# LIUHAO X 十源 packages 层 — 映射表 (SSOT)

本层是 `src/` 的**薄 facade（复用不重写）**：每个包 `LiuHao-O/packages/<name>/`
把真实实现重新导出为可分发包。连字符导入屏障由 `pyproject.toml` 的
`package-dir = {"" = "packages"}` 解决，使 `import liuhao_x.<name>` 可用。

| 包 | 状态 | 复用模块 | 十源 |
|---|---|---|---|
| agent | IMPLEMENTED（facade） | src.ai.agent_factory, src.ai.employee | — |
| analysis | IMPLEMENTED（facade） | src.ai.ada | — |
| approval | IMPLEMENTED（facade） | src.ai.approval | — |
| capability | IMPLEMENTED（facade） | src.kernels.capability | — |
| common | IMPLEMENTED（facade） | error_types（根目录）+ 十源常量 | — |
| context | IMPLEMENTED（facade） | src.kernels.context | — |
| economy | IMPLEMENTED（facade） | src.ai.economy | — |
| evolution | IMPLEMENTED（facade） | src.ai.evolution | — |
| execution | IMPLEMENTED（facade） | src.kernels.execution | — |
| experience | IMPLEMENTED（facade） | src.ai.verification | — |
| goal | IMPLEMENTED（facade） | src.ai.goal_task_graph | — |
| governance | IMPLEMENTED（facade） | src.ai.governance | — |
| identity | IMPLEMENTED（facade） | src.kernels.identity | — |
| memory | IMPLEMENTED（facade） | src.kernels.memory | — |
| network | IMPLEMENTED（facade） | src.kernels.network, src.ai.network_gateway | — |
| observability | IMPLEMENTED（facade） | src.observability, src.observability.metrics, src.observability.tracing | — |
| orchestration | IMPLEMENTED（facade） | src.ai.lcore | — |
| organization | IMPLEMENTED（facade） | src.ai.organization, src.ai.employee | — |
| perception | IMPLEMENTED（facade） | src.ai.perception | — |
| planning | IMPLEMENTED（facade） | src.kernels.execution, src.ai.goal_task_graph | — |
| policy | IMPLEMENTED（facade） | src.kernels.policy | — |
| realtime | IMPLEMENTED（facade） | src.ai.collaboration | — |
| reasoning | IMPLEMENTED（facade） | src.ai.reasoning | — |
| resource | IMPLEMENTED（facade） | src.kernels.resource | — |
| runtime | IMPLEMENTED（facade） | src.ai.agent_factory, src.ai.runtime_loop | — |
| sandbox | IMPLEMENTED（facade） | src.ai.ada | — |
| security | IMPLEMENTED（facade） | src.kernels.security | — |
| task | IMPLEMENTED（facade） | src.ai.employee, src.ai.goal_task_graph | — |
| tools | IMPLEMENTED（facade） | src.ai.tool_registry | — |
| trust | IMPLEMENTED（facade） | src.kernels.trust | — |
| verification | IMPLEMENTED（facade） | src.ai.verification, src.kernels.evaluation | — |
| world | IMPLEMENTED（facade） | src.ai.world_interface | — |
| kernel | IMPLEMENTED（自包含 foundation，非 facade） | 自身实现（config/database/logging/health/ready） | — |

> 32 个包为 facade（复用 src/ 真实实现，NOT_IMPLEMENTED 占位为 0）；`kernel` 为
> 自包含 foundation 包（Phase 1 遗留实现），不走 facade 映射。合计 33 个目录。

## 工作流引擎权威归属（2026-09-13 裁决）

`goal` / `planning` / `task` 三行映射到 **`src.ai.goal_task_graph`（手写 DAG）**，
这是本层唯一权威的工作流实现，**不要改动**。

另有一个已评估、未采纳的备选引擎 `src.ai.langgraph_workflow`（LangGraph 状态机，
自带独立测试 `tests/test_langgraph_workflow.py`）：

- 它**不在 SSOT 路径上**，模块内以常量 `ENGINE_AUTHORITY = "experimental"` 显式标注；
- 其内部同名 `GoalTaskGraph` 只是**向后兼容适配器**，不是权威；
- 裁决理由：无实测收益前不做迁移（迁移需重新验证 facade / 测试 / 七维 DoD，成本高而收益未证）；
- 已知代价：`langgraph` / `langchain` / `langchain-openai` / `langchain-community`
  目前**仅被该模块引用**。若后续确定不迁移，应在确认 mem0 链路不依赖后评估移除这四项；
- 护栏：`tests/test_workflow_engine_authority.py` 钉死「权威 = 手写 DAG」，防止静默翻转。
