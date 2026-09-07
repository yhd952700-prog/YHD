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

> 全部 33 个包已落地为 facade（复用 src/ 真实实现，无 NOT_IMPLEMENTED 占位）。
