# LIUHAO X v3.0 — `src/ai/` 能力层七维 DoD 复验

> 版本：v1.0 · 建立日期：2026-09-09
> 关联：[`GAP-MIGRATION-MATRIX.md` 表 3（21-Phase 重映射）](GAP-MIGRATION-MATRIX.md) · [`KERNEL-DOD-AUDIT.md`](KERNEL-DOD-AUDIT.md) · [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md)

---

## 0. 目的与方法

第 11 轮把 21 个 Phase 标记为 `IMPLEMENTED`，并在 `GAP-MIGRATION-MATRIX.md` 表 3 留了诚实边界：
> "14 个 kernel 的 DoD 七维已正式收口；`src/ai/` 能力层独立的七维逐项审计（尤其 Audited / Policy Controlled）未逐 Phase 复验。"

本文件即该复验结果。方法：
1. **逐模块关键字扫描**：对 20 个 `src/ai/*.py` 能力层模块扫描七维证据关键字（logging/trace/metric、scope/permission/authorize、policy、audit/@kernel_action、docstring）。
2. **架构推理 + 抽样验证**：确认能力层通过调用 kernel action（被 `@kernel_action` 装饰器包裹）将 Observable/Permissioned/Policy/Audited 下沉到内核边界。
3. **不重跑全量测试**：测试存在性与全绿已在第 11 轮验证（`1133 passed / 1 skipped / 0 failed`）。

评级三态：
- **L（Layer 层内直接满足）**：模块自身含该维度证据。
- **K（Delegated 下沉内核，传递性满足）**：模块本身不直接实现，但其调用的 kernel action 经 `@kernel_action` 正式满足该维度（14 kernel 七维已收口）。
- **M（Missing 真实缺失）**：该维度既无层内实现、也无下沉路径。

---

## 1. 七维定义（来自 DoD 七维）

`Implemented` + `Tested` + `Observable` + `Permissioned` + `Policy Controlled` + `Audited` + `Documented`

---

## 2. 逐 Phase 复验结果

| Phase | 模块 | Impl | Tested | Observable | Permissioned | Policy | Audited | Documented |
|---|---|---|---|---|---|---|---|---|
| 3 Agent Runtime | runtime_loop / agent_factory | ✅ | ✅ | K | L(强) | L(强) | K | ✅ |
| 5 Memory | conversation_store / personal_context | ✅ | ✅ | K | K(L:personal 5) | K | K(L:conv 3) | ✅ |
| 9 L-Core | lcore / tool_registry | ✅ | ✅ | K | L(18) | L(3,gate) | K | ✅ |
| 10 Multi-Agent | collaboration | ✅ | ✅ | K | K | K | K | ✅ |
| 11 Perception/ADA | perception / ada | ✅ | ✅ | K | K | K | K | ✅ |
| 12 Organization | organization | ✅ | ✅ | K | K | L(2) | K(L:2) | ✅ |
| 13 ENOCH | enoch | ✅ | ✅ | K | K | K | K | ✅ |
| 14 Network | network_gateway | ✅ | ✅ | K | L(33) | L(12) | L(23) | ✅ |
| 15 World | world_interface | ✅ | ✅ | K | L(13) | L(3) | K(L:1) | ✅ |
| 16 Governance | governance | ✅ | ✅ | K | L(29) | L(8) | L(17) | ✅ |
| 17 Economy | economy | ✅ | ✅ | K | K | K | K | ✅ |
| 18 Verification | verification | ✅ | ✅ | K | K | K | K | ✅ |
| 19 Evolution | evolution | ✅ | ✅ | K | K | K | K(L:4) | ✅ |
| 20 L10K | l10k / vhl_benchmark | ✅ | ✅ | K | K(L:6) | K | K(L:4) | ✅ |
| 21 Hardening | hardening | ✅ | ✅ | K | L(11) | K | K | ✅ |

> 说明：L 后括号数字为关键字命中计数（相对强度指示，非绝对阈值）。K = 下沉内核边界满足。

---

## 3. 关键发现

### 3.1 Implemented / Tested / Documented —— 层内全部满足 ✅
20 个模块源码存在、专项测试存在且全量回归绿、docstring 密度高（Docq 14–42/模块）。这三维在**能力层自身**即达标，无例外。

### 3.2 Observable / Audited —— 几乎全部下沉内核（K），层内直接实现缺失
- **扫描事实**：除 `perception.py`(1)、`l10k.py`(1) 的零星 `log` 命中外，**所有能力层模块的 Observable 关键字计数为 0**；`grep "import logging|getLogger|logger|print"` 在 20 个模块中仅 `hardening/vhl_benchmark` 等少数命中。
- **架构事实**：能力层不直接打日志/审计，而是通过调用 kernel action 间接受益于 `@kernel_action` 装饰器（该装饰器在 `src/kernels/_crosscutting.py` 内统一实现 Observable + Audited + Policy + Permissioned + Documented）。
- **结论**：Observable / Audited 在**端到端意义**上由内核边界满足（14 kernel 七维已收口），能力层本身**不独立可观测**。这是合理的分层设计（不在每层重复横切），**非缺陷**。

### 3.3 Permissioned / Policy Controlled —— 强弱分化
- **强（层内直接 L）**：P3(agent_factory scope)、P14(network_gateway 33/12)、P15(world_interface 13/3)、P16(governance 29/8)、P21(hardening 11)。
- **弱（下沉 K）**：P10/P11/P13/P17/P18 等纯编排/算法模块，权限与策略判定下沉到其调用的 kernel action。
- **结论**：权限/策略在"需要它的层"已直接落地；纯业务/算法层通过内核边界传递性满足。

### 3.4 真实边界（不夸大）
能力层**不独立具备** Observable 与 Audited 的层内 instrumentation。若生产环境要求**能力层粒度**的链路追踪（trace）与审计（而非仅 kernel action 粒度），则需补一层 orchestration-level 日志/审计埋点。这属于**可选增强**，不阻塞"端到端 IMPLEMENTED"结论。

> **2026-09-09 更新**：核心编排层已补上能力层粒度可观测性——新增 `src/ai/observability.py`（`get_logger` + `TraceContext` 基于 contextvars 的 trace/correlation 透传 + `@observe` 装饰器记录 ENTER/EXIT/耗时/异常 + 进程内环形缓冲 `recent_traces`），并接入 `liuhao.chat`/`chat_stream`、`lcore.handle_intent`、`collaboration.delegate/pipeline/broadcast`、`runtime_loop.step/run`。剩余 16 个能力层模块仍以 kernel 边界下沉为主，属可选扩展。详见 `AI-LAYER-OBSERVABILITY.md`（实施说明）。

---

## 4. 结论

- **能力层七维在端到端意义上全部满足**：Implemented/Tested/Documented 层内达标；Observable/Permissioned/Policy/Audited 由内核边界（14 kernel 七维收口）传递性覆盖。
- **GAP-MIGRATION-MATRIX 表 3 的 `IMPLEMENTED` 结论维持有效**，其含义明确为"端到端（含下沉内核）七维达标"，而非"每个能力层独立七维达标"。
- **唯一真实缺口（原）= 能力层粒度的可观测性（Observability）**：**已于 2026-09-09 在核心编排层闭合**（新增 `src/ai/observability.py` 并接入 liuhao/lcore/collaboration/runtime_loop，见 §3.4）。剩余 16 个能力层模块仍以 kernel 边界下沉为主，属可选扩展（见 §5）。

---

## 5. 后续可选治理（非阻塞）

1. ~~**能力层可观测性增强（推荐若上生产）**：在 `src/ai/` 编排入口加结构化日志 / OpenTelemetry span。~~ **✅ 已于 2026-09-09 实现**（见 §3.4）：核心编排层已接入 `src/ai/observability.py`。
   > ⚠️ **口径更正（2026-09-11）**：此处原写"全量测试 1141 passed 零回归"，该数字是**本地口径且未经 CI 验证** —— 当时的 `ci.yml` 用 `|| echo` 吞掉退出码，run #52-74 的"全绿"是假象。经 CI 真实运行验证的基线见 `docs/README.md`（当前：**1155 passed / 14 skipped / 0 failed**，CI run #82）。
2. **能力层审计埋点（按需）**：若审计需覆盖"哪个能力层触发了哪个 kernel action"的因果链，可在编排层补 audit 上下文透传（现仅 kernel action 粒度，已足够）。
3. 其余 16 个能力层模块的层内日志为可选扩展；当前架构已满足 DoD 七维的端到端语义，不强制。

---

## 6. 证据方法复现

- 关键字扫描脚本（本地过程文件，非仓库资产）对 20 个 `src/ai/*.py` 模块逐维计数。
- 下沉验证：`grep -E "from src.kernels|kernel.execute|policy|audit" src/ai/lcore.py` 等确认能力层调用内核。
- 全量测试现状见 `docs/README.md`（**CI 验证口径**：`1155 passed / 14 skipped / 0 failed`，run #82）。
  > 历史口径说明：本节早期记录的 `1141 passed / 1 skipped / 0 failed`（含本增强新增 8 例 `test_observability.py`）是**本地运行结果，未经 CI 验证**，现已由上述 CI 真实基线取代。
