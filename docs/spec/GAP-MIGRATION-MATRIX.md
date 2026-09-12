# LIUHAO X v3.0 — Gap & Migration Matrix

> 连接 **As-Built（现状）** 与 **To-Be（目标规格）** 的桥梁。
> 版本：v1.0 · 建立日期：2026-09-06
> 相关：[`KERNEL-CANON.md`](KERNEL-CANON.md) · [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md) · [`MASTER-SPEC-v3.0.md`](MASTER-SPEC-v3.0.md)

---

> ## ⚠️ 状态横幅（2026-09-07 建立 · **2026-09-09 已重映射**）
>
> 本文件「§0 当前定位」及下方的 **21-Phase 状态表（表 3）** 曾因按"预期路径"（`src/agents/`、`src/perception/` 等）判断进度而**系统性误判**为"10 未开始 / 10 部分"。
> **2026-09-09 已基于真实源路径逐 Phase 实测重映射**：21 个 Phase 源码与测试全部存在，全量回归 `1133 passed / 1 skipped / 0 failed`（**该数字为 2026-09-09 本地口径，早已作废**；当前 CI 实证基线见 [`docs/README.md`](../README.md)：**1173 passed / 14 skipped / 0 failed**）。
> 重映射结果见下方 **表 3（已更正）**。权威能力状态另见 [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md) §4。真实现状摘要见下：
>
> | 能力 | 实际源路径 | 现状 |
> |---|---|---|
> | Kernel 层 14 内核 | `src/kernels/` | **DoD 七维 14/14 全过**（`KERNEL-DOD-AUDIT.md`） |
> | Agent Runtime | `src/ai/runtime_loop.py` + `agent_factory.py` | 已实现（有测试） |
> | L-Core | `src/ai/lcore.py` | 已实现（有测试） |
> | Perception / ADA | `src/ai/perception.py` + `ada.py` | 已实现 |
> | Organization | `src/ai/organization.py` | 已实现 |
> | World Interface | `src/ai/world_interface.py` | 已实现 |
> | Long-Horizon / ENOCH | `src/ai/enoch.py` | 已实现 |
> | 十源 DNA 全部 10 源 | `src/ai/` | **全部闭环**（`CAPABILITY-REGISTRY.md` §4） |
> | 全量测试 | `tests/` | **1100 passed / 1 skipped / 0 failed** |
>
> **根因**：本文件按 `src/agents/`、`src/perception/` 等"预期路径"判断 Phase 进度，但项目采用
> `src/ai/*` 单层汇聚实现，导致"路径不存在 ⇒ 未开始"的误判。21-Phase 的**逐阶段可信完成度**
> 仍需一次基于真实源路径的重映射（尚未完成），故**分数不可信**。当前权威状态以
> [`KERNEL-DOD-AUDIT.md`](KERNEL-DOD-AUDIT.md) + [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md) 为准。

---

## 0. 三份文档的关系

```text
Architect-Architecture-v3.0.md  →  As-Built   现状基线（138 .py 实测，但低估了 src/kernels/）
MASTER-SPEC-v3.0.md             →  To-Be      目标规格（221 节，零代码）
本文件                           →  Bridge     怎么从前者走到后者
```

**当前定位（2026-09-09 重映射后）**：Kernel 层 14 内核已落地且 DoD 七维全过（6,391+ 行）；目标规格要求的 Agent Runtime / L-Core / Organization / Network / World / Multi-Agent / Perception / Economy / Evolution / Hardening 等 **21 个 Phase 全部已实现**（`src/ai/*` 单层汇聚 + `src/kernels/*` 内核层），详见下方**表 3（已更正）**。本文件角色转为"已实现能力的对账清单 + 后续可选治理（连接面收敛 / 七维逐项复验 / 包结构重排）指引"。

---

## 表 1 — Kernel 命名对照（五套 → 统一）

| 统一名（代码） | 旧 K 编号 | kernels-interface | MS:§6 | capability-registry | 采用 |
|---|---|---|---|---|---|
| identity | — | #10 | ✅ | ✅ | ✅ |
| memory | K04 | #11 | ✅ | ✅ | ✅ |
| context | — | #1 | ✅ | ✅ | ✅ |
| capability | — | #2 | ✅ | ✅ | ✅ |
| policy | K08（部分） | #6 | ✅ | ✅ | ✅ |
| execution | K05+K06 | #4 | ✅ | ✅ | ✅ |
| resource | — | #5 | ✅ | ✅ | ✅ |
| event | — | #3 | ✅ | ✅ | ✅ |
| network | — | #7 | ✅ | ✅ | ✅ |
| trust | — | #8 | ✅ | ✅ | ✅ |
| evaluation | — | #9 | ✅ | ✅ | ✅ |
| security | K08（部分） | ❌ 漏 | ✅ | ❌ 漏 | ✅ |
| audit | K09 | #12 | ❌ 漏 | ❌ 漏 | ✅ |
| plugin | K07 | ❌ 漏 | ❌ 漏 | ❌ 漏 | ✅ |
| ~~Control Plane~~ | K01 | — | — | — | ❌ 非 kernel |
| ~~Smart Router~~ | K02 | — | — | — | ❌ 非 kernel |
| ~~LLM Adapter~~ | K03 | — | — | — | ❌ 非 kernel |
| ~~Observability~~ | K10 | — | ❌ 漏 | — | ⚠️ 横切关注点 |
| ~~DR / Scaling~~ | K11/K12 | — | — | — | ❌ 属 Hardening |

**行动**：所有 K01–K12 编号**停止使用**，代码引用一律用 `src/kernels/<name>`。
`Architect-Architecture-v3.0.md` §4 待重写（任务 P2-3）。

---

## 表 2 — `src/` → `packages/` 迁移映射

目标结构来自 MS:§7。当前 `src/` 有 31 个一级目录。

| 现有目录 | 目标 package | 处置 | 说明 |
|---|---|---|---|
| `src/kernels/` | `packages/kernel/` | **搬迁** | 14 个模块，系统的核心资产 |
| `src/identity/` | `packages/identity/` | **搬迁** | 人类账号体系（RBAC/ABAC/主子账号） |
| `src/security/` | `packages/security/` | **合并** | 与 `kernels/security/` 存在职责重叠，需先界定边界 |
| `src/audit/` | `packages/security/` 或新增 `audit/` | **合并** | 与 `kernels/audit/` 重叠，同上 |
| `src/ai/` | `packages/agent/` + `packages/reasoning/` | **拆分** | employee.py / goal_task_graph.py / langgraph_workflow.py |
| `src/providers/` | `packages/model-gateway/`（建议新增） | **搬迁** | MS:§89-91 要求 Model Gateway/Registry/Router |
| `src/knowledge/` | `packages/memory/` | **合并** | 与 `kernels/memory/` 重叠，**禁止维护两套** |
| `src/workflow/` | `packages/orchestration/` | **搬迁** | |
| `src/tasks/` | `packages/task/` | **搬迁** | |
| `src/observability/` | `packages/observability/` | **搬迁** | MS:§92 要求 OTel 贯穿 12 层 |
| `src/plugins/` | `packages/sandbox/` + `packages/tools/` | **拆分** | plugin kernel 与 sandbox 分离 |
| `src/api/` `src/gateway/` | `apps/api/` | **搬迁** | 应用入口，非 package |
| `src/integrations/` `src/storage/` | `packages/common/` + `packages/data/` | **搬迁** | ORM 与存储 |
| `src/deployment/` `src/distribution/` | `apps/` 或 infrastructure | **待定** | |
| `src/infra/` | `infrastructure/` | **搬迁** | |
| `src/core/` | `packages/common/` | **合并** | |
| `src/cost/` | `packages/economy/` | **搬迁** | 对应 MS:§75-76 Budget/Economy |
| `src/feedback/` | `packages/experience/` | **搬迁** | 对应 §79 Experience Engine |
| `src/mlops/` `src/datasets/` | `packages/analysis/` | **合并** | 对应 ADA 计算智能 |
| `src/model_gateway/` | `packages/model-gateway/` | **合并** | 与 `providers/` 合并 |
| `src/performance/` | `packages/verification/` | **搬迁** | |
| `src/pipelines/` | `packages/analysis/` | **合并** | |
| `src/adapters/` | `packages/network/` + `packages/world/` | **拆分** | 按协议适配 vs 世界接口 |
| `src/sre/` | `infrastructure/` | **搬迁** | 对应 `MS:§198` Hardening |
| `src/ui/` | `apps/web/` 或 `apps/lcore/` | **搬迁** | |
| `src/models/` | 各 domain 的 `models.py` | **拆分** | 按 MS:§10 Domain Contract |

### 待新建（当前完全不存在）

| 目标 package | 对应 DNA | 对应 Spec 章节 |
|---|---|---|
| `packages/perception/` | VISION | §29-32 |
| `packages/planning/` `packages/goal/` | ULTRON / JARVIS | §15-18 |
| `packages/approval/` | — | §44 |
| `packages/organization/` | JOCaSTA | §61-64 |
| `packages/realtime/` | FRIDAY | §55-57 |
| `packages/world/` | EDITH | §36-37 |
| `packages/governance/` | JOCaSTA | §81-82 |
| `packages/verification/` | — | §78 |
| `packages/evolution/` | — | §80 |
| `packages/economy/` | — | §76 |
| `apps/lcore/` | JARVIS | §19-21 |
| `apps/worker/` `apps/scheduler/` `apps/runtime/` | ULTRON | §46-48, §60 |

---

## 表 3 — 21 Phase 目标态 vs 现状（**2026-09-09 已基于真实源路径重映射**）

> **历史误判根因**：本表旧版按 `src/agents/`、`src/perception/` 等"预期路径"判断 Phase 进度，但项目实际采用
> `src/ai/*` 单层汇聚实现 + `src/kernels/*` 内核层，导致"路径不存在 ⇒ 未开始"的系统性误判。
> 下表为 **实测重映射结果**：逐 Phase 校验「源码存在 + 专项测试函数存在 + 全量回归绿」三项证据。
> 校验脚本为本地过程文件（非仓库资产）。

| Phase | 内容 | 真实源路径（实测存在） | 测试证据（test 函数数） | 判定 |
|---|---|---|---|---|
| 1 | Foundation | `Dockerfile` / `docker-compose.yml` / `alembic` / `src/gateway/main.py` | smoke 等 | `IMPLEMENTED` |
| 2 | Kernel / Identity（14 内核） | `src/kernels/{identity,security,audit,plugin,trust,evaluation,…}` | 423（tests/kernels/*） | `IMPLEMENTED` |
| 3 | Agent Runtime | `src/ai/runtime_loop.py` + `agent_factory.py` | 25 | `IMPLEMENTED` |
| 4 | Model Gateway | `src/ai/providers.py`（5 Provider 适配器） | 8 | `IMPLEMENTED` |
| 5 | Memory | `src/kernels/memory` + `conversation_store.py` + `personal_context.py` | 40 | `IMPLEMENTED` |
| 6 | Capability / Tool | `src/kernels/capability` + `tool_registry.py` + `tools.py` | 12 | `IMPLEMENTED` |
| 7 | Policy / Approval | `src/kernels/policy` + `approval.py` | 10 | `IMPLEMENTED` |
| 8 | Execution | `src/kernels/execution` + `context` | 56（execution+context 子目录） | `IMPLEMENTED` |
| 9 | L-Core | `src/ai/lcore.py` | 16 | `IMPLEMENTED` |
| 10 | Multi-Agent | `src/ai/collaboration.py` | 12 | `IMPLEMENTED` |
| 11 | Perception / ADA | `src/ai/perception.py` + `ada.py` | 21 | `IMPLEMENTED` |
| 12 | Organization | `src/ai/organization.py` | 12 | `IMPLEMENTED` |
| 13 | Long-Horizon (ENOCH) | `src/ai/enoch.py` | 18 | `IMPLEMENTED` |
| 14 | Network | `src/ai/network_gateway.py` + `kernels/network` | 18 | `IMPLEMENTED` |
| 15 | World Interface | `src/ai/world_interface.py` | 11 | `IMPLEMENTED` |
| 16 | Trust / Security / Governance | `kernels/{trust,security,audit}` + `governance.py` | 138 | `IMPLEMENTED` |
| 17 | Economy | `src/ai/economy.py` | 14 | `IMPLEMENTED` |
| 18 | Verification / Experience | `src/ai/verification.py` + `kernels/evaluation` | 16 | `IMPLEMENTED` |
| 19 | Evolution | `src/ai/evolution.py` | 20 | `IMPLEMENTED` |
| 20 | L10K / Benchmark | `src/ai/l10k.py` + `vhl_benchmark.py` | 21 | `IMPLEMENTED` |
| 21 | Production Hardening | `src/ai/hardening.py` + `docs/operations/runbook.md` | 4 | `IMPLEMENTED` |

**小结（2026-09-09 实测）**：
- **已实现 21 / 21**（`IMPLEMENTED`：源码存在 + 专项测试存在 + 全量回归绿）
  - 当前 CI 实证基线：**`1173 passed / 14 skipped / 0 failed`**（对应 21-Phase 全量口径，见 [`docs/README.md`](../README.md)）
  - 表格内各测试函数计数为 2026-09-09 快照，此后有增补（如能力层七维复核 +10 例），不影响 `IMPLEMENTED` 判定
- 14 个 kernel 的 DoD 七维（Implemented / Tested / Observable / Permissioned / Policy Controlled / Audited / Documented）已正式收口（`KERNEL-DOD-AUDIT.md`）
- `src/ai/` 能力层（Phase 3 / 5 / 9–21）由 Sprint 3–14 实现，均带测试 + 真实 LLM 端到端验证（详见 [`CAPABILITY-REGISTRY.md` §4](CAPABILITY-REGISTRY.md) 十源映射）

> **判定口径**：状态枚举以 [`CAPABILITY-REGISTRY.md` §5](CAPABILITY-REGISTRY.md) 的 9 枚举为准（此处统一记为 `IMPLEMENTED`）。
> 旧版"三态简写 + 无任何 Phase 达 IMPLEMENTED"的结论因路径误判已失效，现以真实源路径证据为准。
> ⚠️ 诚实边界：Phase 层级的 `IMPLEMENTED` 指"端到端（含下沉内核）七维达标"，非"每个能力层独立七维达标"。
> 14 kernel 已通过完整七维 DoD 审计（`KERNEL-DOD-AUDIT.md`）；`src/ai/` 能力层为薄编排层，将 Observable / Permissioned / Policy / Audited 下沉到 kernel action 边界（被 `@kernel_action` 装饰器统一满足），层内不独立重实现——属合理分层设计，**非缺陷**。
> 逐 Phase 复验结论与证据见 **[`AI-LAYER-DOD-AUDIT.md`](AI-LAYER-DOD-AUDIT.md)**：能力层 Implemented/Tested/Documented 层内全达标，其余四维持久化由内核边界传递性覆盖；唯一真实缺口为"能力层粒度可观测性"，列为可选增强（非阻塞）。

---

## 迁移原则（执行时遵守）

1. **禁止一上来拆微服务**。MS:§9 明确：采用 Modular Monolith + API + Worker + Runtime + Scheduler + Event Bus，后续按真实负载再拆。
2. **先补测试再搬迁**。带着 0 测试做大规模重构 = 必然引入回归。
3. **一个 package 一次 PR**，每步都要能跑通。
4. **保留已正确实现的**。遇到已正确实现的模块（如 `src/identity/` 的权限系统，有 111 个测试用例），**不要重写**，只搬迁。
5. **禁止维护第二套**。凡是 `src/X/` 与 `src/kernels/X/` 职责重叠的（security / audit / memory），先界定边界再动手，不要并行存在两套。

---

## 推荐执行顺序

```text
Step 0  补 implementation-status 假数据（P0-1/P0-2）
Step 1  建 tests/kernels/ 基础设施 + 为 14 kernel 补单测（P1-1）
Step 2  合并重叠模块：security / audit / memory 去重（P2）
Step 3  建 packages/ 骨架，按上表逐 package 搬迁（P2-1）
Step 4  建 apps/{worker,scheduler,runtime}，把 execution 跑起来（对应 Phase 3）
Step 5  建 apps/lcore（Phase 9）
Step 6  往后按 MS:§177 顺序推进
```
