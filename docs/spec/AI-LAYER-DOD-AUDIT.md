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
3. **不重跑全量测试**（第 11 轮口径）：测试存在性与全绿已有验证。**当时记录的 `1133 passed / 1 skipped / 0 failed` 是本地口径、已作废**；当前 CI 实证基线为 **`1173 passed / 14 skipped / 0 failed`**（见 [`docs/README.md`](../README.md)）。第 59 轮的复核已改为运行时观测量（审计事件增量），不再依赖数字声明。

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
>
> ⚠️ **重要更正（2026-09-11）**：本表的 `K` 是**架构推理**的结论，**从未验证过**"下沉这条路真的通"。经 21 项**运行时探针**复核（见 §3.5），Audited 维度的 `K` 有 **15/21 不成立**；且部分 `L` 的括号计数来自 **docstring 里的关键字**（例如 P5 的 `L:conv 3` 实为 `conversation_store.py` 注释中提到 "audit kernel" 的次数，该模块**零 kernel import**）。请以 §3.5 的实测矩阵为准。

> ✅ **补充（Round 60，2026-09-11）**：§3.5 指出的 15 个 ❌ 中，**14 个已补齐**
> 能力层自身审计埋点并逐点运行时验证（见 §3.6）；仅 **P17 Economy** 因 docstring
> 明示有意独立实现而保持原样。本表其余 `K` 的架构推断仍未逐项复核。

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

## 3.5 运行时复核（2026-09-11 · Round 59）

### 3.5.1 方法

原方法（关键字扫描）有两个可证伪的弱点：它无法区分「模块调用了被 `@kernel_action`
装饰的内核方法」与「模块恰好有个同名方法」，也会把 **docstring 里提到的
"kernel" 字样**记成证据。本轮改用**运行时观测量**：

- **观测量**：`audit store` 的累计事件数增量（内核动作被装饰后必然写审计事件）。
- **对照实验**：先直接调用已知被装饰的 `get_memory_kernel().store()`，确认计数 `+1`。
  **只有对照通过，"增量为 0" 才能解释为"该模块确实不产生审计"**，而不是"测量失灵"。
- **判据**（`CODEX-CONTRACT` §5）：Audited = 关键操作写入 audit log（含 `correlation_id`）。

### 3.5.2 实测矩阵（21 项探针）

| Phase | 探针操作 | 审计增量 | Audited 下沉是否成立 |
|---|---|---|---|
| P3 | `agent_factory.AgentRuntimeService.start()` | 0 | ❌ |
| P3 | `runtime_loop.RuntimeLoop(...).step()` | 0 | ❌ |
| P5 | `personal_context.set_preference()` | **+1** | ✅（`memory.store`） |
| P5 | `conversation_store.append()` | 0 | ❌ |
| P9 | `lcore.LCore()` | **+12** | ✅（`capability.register`） |
| P9 | `tool_registry.register(Tool)` | 0 | ❌ |
| P10 | `collaboration.MessageBus.send()` | 0 | ❌ |
| P11 | `perception.TextPerceiver.perceive()` | 0 | ❌ |
| P11 | `ada.ComputeEngine.run_python()` | 0 | ❌ |
| P12 | `organization.Organization.create_goal/department()` | 0 | ❌ |
| P13 | `enoch.create_mission()` | 0 | ❌ |
| P14 | `network_gateway.AgentNetworkGateway()` | **+1** | ✅（`network.add_route`） |
| P15 | `world_interface.execute(filesystem.read)` | 0 | ❌ |
| P16 | `governance.SecurityChain.evaluate()` | **+1** | ✅（`security.decide_access`） |
| P17 | `economy.BudgetEngine.reserve/consume()` | 0 | ❌ |
| P18 | `verification.VerificationEngine.verify()` | 0 | ❌ |
| P18 | `verification.ExperienceEngine.store()` | **+1** | ✅（`memory.store`） |
| P19 | `evolution.EvolutionEngine()` | 0 | ❌ |
| P20 | `l10k.L10KRegistry.register_task()` | 0 | ❌ |
| P20 | `vhl_benchmark.run_vhl_benchmark()` | **+3** | ✅ |
| P21 | `hardening.run_hardening_suite()` | 0（预热后）※ | ❌ |

> ※ `hardening` **首次**调用会因内部构造 `AgentNetworkGateway` 附带 1 条
> `network.add_route`（一次性路由注册副作用，非其自身检查动作）；预热后恒为 0。

**汇总：6 项确认产生审计 / 15 项确认不产生 / 0 项不确定。**

### 3.5.3 修正后的逐 Phase 结论

| Phase | 模块 | Audited（修正） | 依据 |
|---|---|---|---|
| 3 Agent Runtime | runtime_loop / agent_factory | ❌ 仅 policy 侧有直接引用 | `start()`/`step()` 零审计 |
| 5 Memory | conversation_store / personal_context | 半 ✅ | `personal_context` ✅；`conversation_store` 零 kernel import，原 `L:conv 3` 系 docstring 计数 |
| 9 L-Core | lcore / tool_registry | 半 ✅ | `lcore` ✅（构造即 +12）；`tool_registry` 零审计 |
| 10 Multi-Agent | collaboration | ❌ | 零审计 |
| 11 Perception/ADA | perception / ada | ❌ | 两者零审计 |
| 12 Organization | organization | ❌ | 零审计 |
| 13 ENOCH | enoch | ❌ | 仅 import `Goal`/`GoalDecomposer` 两个 dataclass，零审计 |
| 14 Network | network_gateway | ✅ | `network.add_route` |
| 15 World | world_interface | ❌ | 仅 import `ActionResult` dataclass；`execute` 是自身方法，零审计 |
| 16 Governance | governance | ✅ | `security.decide_access` |
| 17 Economy | economy | ❌ | docstring 明示"self-contained, dependency-free"，**有意不下沉** |
| 18 Verification | verification | 半 ✅ | `ExperienceEngine.store` ✅；`VerificationEngine.verify` ❌（同模块内不一致） |
| 19 Evolution | evolution | ❌ | 零审计 |
| 20 L10K | l10k / vhl_benchmark | 半 ✅ | `vhl_benchmark` ✅；`l10k` ❌ |
| 21 Hardening | hardening | ❌ | 自身操作零审计 |

> ✅ **本表 9 个 ❌ 已于 Round 60 补齐**（2026-09-11）：新增 `src/ai/audit.py` +
> 22 个 `@audited` 注入点，除 **P17 Economy**（docstring 明示有意独立实现，保持原样）
> 外全部转为 ✅。逐项证据与验证见 **§3.6**。
> 本表保留 Round 59 的原始观测，作为"**修复前**"基线。

### 3.5.4 Policy Controlled 的结构性问题（新发现）

`src/kernels/_crosscutting.py` 的 `_adjudicate` 以
`actor={"type": "system", "verified": True}` 调用策略引擎，但：

1. 引擎会用 `_is_verified_human()` **覆盖** `verified` 字段；该 actor 无
   `id`/`principal`，覆盖结果为 `False`；
2. 内置 5 条规则中唯一的 ALLOW 规则 `human_sovereignty` 要求
   `actor.type == "human"` 且风险为 HIGH/CRITICAL。

→ **内核动作记录到的判决恒为 `deny`**（实测 19/19 采样事件均为 `deny`，四种风险等级均如此）。
又因装饰器 additive、从不拦截，该 `deny` **不产生任何执行效果**。

**判定**：Policy Controlled 维度**满足 DoD 字面要求**（"必须有明确的策略判决记录"），
但判决值**不携带信息**，实为"记录而非控制"。已在审计事件的 `details` 中显式标注
`policy_enforced: false`，避免读审计者把 `deny` 误读为"动作被拒绝"。

> 是否需要让它成为真正的控制点（为 system actor 定义可放行规则）属**安全语义变更**，
> 需单独裁决，本轮未改。

### 3.5.5 复现方式

```bash
.venv/Scripts/python.exe scripts/verify_ai_layer_audit.py
```

- 脚本：`scripts/verify_ai_layer_audit.py`（含对照实验 + 21 项探针矩阵 + policy 判决采样）。
  隔离到独立临时 DB，不污染工作区内的 `audit_store.db` / `memory_store.db`。
- **Round 60 后的预期输出**：除 **P17 economy 恒为 0**（有意设计）外，
  其余探针均应 > 0。P19 的探针已从"只构造 `EvolutionEngine()`"改为跑完整生命周期
  —— 只构造不写审计是**正确**行为，原探针测不到 `@audited`。
- 回归测试（已入库）：`tests/test_ai_layer_dod_delegation.py`（14 例），
  锁定已验证的下沉路径、`record-only` 契约与"system actor 恒判 deny"的结构性事实。
  这些测试**故意会在上述任一事实改变时失败**，以强制同步本文档。

---

## 3.6 Round 60 补齐（2026-09-11）：能力层自身审计埋点

**根因**：§3.5 的 15 个 ❌ 并非"设计上不需要审计"，而是
**"传递性满足"只对真的调用了被装饰内核动作的操作成立** —— 编排/算法类模块
（collaboration / perception / organization / enoch / world_interface / evolution /
l10k / hardening / conversation_store / tool_registry）一个内核动作都不调，
因此在端到端意义上**完全没有审计**。§3.4 早已给出正确方向：
"需补一层 orchestration-level 审计埋点"。

**处置**：

| 项 | 内容 |
|---|---|
| 新增 | `src/ai/audit.py` —— 能力层审计助手（与 `src/ai/observability.py` 同构） |
| 契约 | **additive**（不改返回值/异常行为）、**fail-loud-not-fatal**（审计失败记 warning 但不阻断业务）、correlation_id 复用 `TraceContext` |
| 注入 | 14 个模块、**22 个 `@audited` 注入点**（同步与 async 方法均支持） |
| 职责边界 | **只做审计，不做策略判决** —— 在这里再加一个 record-only 判决只会制造第二条噪音记录（见 §3.5.4） |

注入点清单：

| Phase | 模块 | 注入点 |
|---|---|---|
| P3 | `agent_factory` | `AgentRuntimeService.start` / `.stop` |
| P3 | `runtime_loop` | `RuntimeLoop.step` |
| P5 | `conversation_store` | `ConversationStore.append` |
| P9 | `tool_registry` | `register` / `approve` / `activate` / `suspend` / `revoke` |
| P10 | `collaboration` | `MessageBus.send` |
| P11 | `perception` | `TextPerceiver.perceive` |
| P11 | `ada` | `ComputeEngine.run_python` |
| P12 | `organization` | `create_goal` / `create_department` |
| P13 | `enoch` | `create_mission` |
| P15 | `world_interface` | `WorldInterface.execute` |
| P18 | `verification` | `VerificationEngine.verify` |
| P19 | `evolution` | `propose` / `approve` / `deploy` |
| P20 | `l10k` | `L10KRegistry.register_task` |
| P21 | `hardening` | `HardeningSuite.run_checks` |

**未注入（有意）**：**P17 Economy** —— `economy.py` docstring 明示
"self-contained, dependency-free"，其预算引擎为**有意独立实现**，不接线。
（`tests/test_ai_layer_dod_delegation.py::test_economy_still_has_no_capability_layer_audit`
会在此决定被推翻时失败。）

**验证**（全部是运行时观测量，不是关键字扫描）：

| 手段 | 结果 |
|---|---|
| 22 点埋点探针（audit store 事件增量） | 全部出现预期动作 |
| `tests/test_ai_layer_dod_delegation.py` | **14 passed**（正向锁定 + 埋点契约 + fail-loud 断言） |
| 受影响模块测试（16 个文件） | **184 passed / 0 failed** |
| `tests/kernels` + `tests/security` + `tests/governance` | **506 passed / 1 skipped** |
| `flake8 src/`（CI 口径） | 0 违规 |
| importlib 全模块扫描 | **194 个模块，FAILED: 0** |

**经验教训**：不要用"某层声称下沉到内核"当作审计达标的证据 ——
**传递性满足对"没有调用被装饰动作"的操作不成立**。判据必须是
"该操作**运行时**是否真的产生了 audit 事件"（对照实验先行，见 §3.5.1）。

---

## 4. 结论

- **能力层七维在端到端意义上全部满足**：Implemented/Tested/Documented 层内达标；Observable/Permissioned/Policy/Audited 由内核边界（14 kernel 七维收口）传递性覆盖。
- **GAP-MIGRATION-MATRIX 表 3 的 `IMPLEMENTED` 结论维持有效**，其含义明确为"端到端（含下沉内核）七维达标"，而非"每个能力层独立七维达标"。
- **唯一真实缺口（原）= 能力层粒度的可观测性（Observability）**：**已于 2026-09-09 在核心编排层闭合**（新增 `src/ai/observability.py` 并接入 liuhao/lcore/collaboration/runtime_loop，见 §3.4）。剩余 16 个能力层模块仍以 kernel 边界下沉为主，属可选扩展（见 §5）。

### 4.1 上述"传递性覆盖"的实证限定（2026-09-11 追加）

§3.5 的运行时复核表明，**"由内核边界传递性覆盖"这一说法对 Audited 维度只在少数模块成立**：

- 15/21 探针显示能力层的关键操作**不产生任何审计事件**（含 P3/P10/P11/P12/P13/P15/P17/P19/P21）。
  因此"端到端七维达标"的正确读法是"**端到端链路中审计确实存在**"，而非
  "每个 Phase 的关键操作都被审计"。
  > ✅ **已于 Round 60 补齐**（2026-09-11）：14 个模块 22 个 `@audited` 注入点，
  > 上述 15 项中除 **P17 Economy**（有意独立实现）外全部转为运行时可见的审计。见 §3.6。
- Policy Controlled 维度存在**结构性空转**：内核动作的判决恒为 `deny`，
  且从不拦截（见 §3.5.4）。它满足 DoD 的字面要求，但不构成真正的策略控制。
- 因此本条与 §3.4 的"全部满足"应理解为：**维度在架构上已建立、在部分路径上已生效**，
  而非"已均匀覆盖到每个能力层操作"。若要按 Phase 粒度宣称达标，需先补齐上表 ❌ 项。

---

## 5. 后续可选治理（非阻塞）

1. ~~**能力层可观测性增强（推荐若上生产）**：在 `src/ai/` 编排入口加结构化日志 / OpenTelemetry span。~~ **✅ 已于 2026-09-09 实现**（见 §3.4）：核心编排层已接入 `src/ai/observability.py`。
   > ⚠️ **口径更正（2026-09-11）**：此处原写"全量测试 1141 passed 零回归"，该数字是**本地口径且未经 CI 验证** —— 当时的 `ci.yml` 用 `|| echo` 吞掉退出码，run #52-74 的"全绿"是假象。经 CI 真实运行验证的基线见 `docs/README.md`（当前：**1173 passed / 14 skipped / 0 failed**，CI run #88）。
2. **能力层审计埋点（按需）**：若审计需覆盖"哪个能力层触发了哪个 kernel action"的因果链，可在编排层补 audit 上下文透传（现仅 kernel action 粒度，已足够）。
3. 其余 16 个能力层模块的层内日志为可选扩展；当前架构已满足 DoD 七维的端到端语义，不强制。
4. **能力层审计下沉补齐（P1，由 §3.5 新增）**：P3/P10/P11/P12/P13/P15/P17/P19/P21 的关键操作
   当前不写审计。若要按 Phase 粒度宣称 Audited 达标，需为这些模块显式接线
   `src/kernels/audit.log_event` 或调用被 `@kernel_action` 装饰的内核方法。
   注意 `economy` 的 docstring 明确声明其预算引擎为**有意独立实现**（不下沉
   `resource` kernel），补审计时不应破坏该设计意图。
5. **裁决项（需决策，勿擅自改）**：Policy Controlled 是否应从"只记录"升级为"真拦截"。
   现状恒为 `deny` 且不生效（§3.5.4）。若升级，需为 system actor 定义可放行规则，
   属安全语义变更；若不升级，建议把 DoD 中该维度的措辞明确为"策略判决已记录"。

---

## 6. 证据方法复现

- 关键字扫描脚本（本地过程文件，非仓库资产）对 20 个 `src/ai/*.py` 模块逐维计数。
- 下沉验证：`grep -E "from src.kernels|kernel.execute|policy|audit" src/ai/lcore.py` 等确认能力层调用内核。
- 全量测试现状见 `docs/README.md`（**CI 验证口径**：`1173 passed / 14 skipped / 0 failed`，run #88）。
  > 历史口径说明：本节早期记录的 `1141 passed / 1 skipped / 0 failed`（含本增强新增 8 例 `test_observability.py`）是**本地运行结果，未经 CI 验证**，现已由上述 CI 真实基线取代。
