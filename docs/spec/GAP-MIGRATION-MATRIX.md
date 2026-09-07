# LIUHAO X v3.0 — Gap & Migration Matrix

> 连接 **As-Built（现状）** 与 **To-Be（目标规格）** 的桥梁。
> 版本：v1.0 · 建立日期：2026-09-06
> 相关：[`KERNEL-CANON.md`](KERNEL-CANON.md) · [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md) · [`MASTER-SPEC-v3.0.md`](MASTER-SPEC-v3.0.md)

---

> ## ⚠️ 状态横幅（2026-09-07 更新）
>
> 本文件「§0 当前定位」及下方的 **21-Phase 状态表（表 2）** 已**过期**，不得作为现状依据。
> 其结论（"Agent Runtime / L-Core / Organization / Network / World 尚未开始"）与真实代码库**矛盾**：
> 这些能力已实质落地，只是集中在不同的源路径。真实现状见下：
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

**当前定位**：代码已完成 Kernel 层地基（14 个 / 6,391 行），但缺测试与审计；
目标规格要求的 Agent Runtime、L-Core、Organization、Network、World 等**尚未开始**。

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
| `src/sre/` | `infrastructure/` | **搬迁** | 对应 §198 Hardening |
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

## 表 3 — 21 Phase 目标态 vs 现状

MS:§177 定义 21 个实施 Phase。当前实际进度：

| Phase | 内容 | 现状 | 判定 |
|---|---|---|---|
| 1 | Foundation（仓库/配置/DB/Docker/CI） | ✅ 已有仓库、docker、alembic、health | **已完成** |
| 2 | Kernel / Identity | ⚠️ 14 kernel 有代码，0 测试 | **部分**（缺测试） |
| 3 | Agent Runtime | ❌ `src/agents/` 不存在 | **未开始** |
| 4 | Model Gateway | ⚠️ `src/providers/` 存在 | **部分** |
| 5 | Memory | ⚠️ `kernels/memory` + `knowledge/memory.py` 双份 | **部分**（需合并） |
| 6 | Capability / Tool | ⚠️ `kernels/capability` 有，`packages/tools` 无 | **部分** |
| 7 | Policy / Approval | ⚠️ `kernels/policy` 有，Approval 无 | **部分** |
| 8 | Execution | ⚠️ `kernels/execution` 有 | **部分**（缺测试） |
| 9 | L-Core | ❌ | **未开始** |
| 10 | Multi-Agent | ❌ WS-D BLOCKED | **未开始** |
| 11 | Perception / ADA | ❌ | **未开始** |
| 12 | Organization | ❌ | **未开始** |
| 13 | Long-Horizon | ❌ | **未开始** |
| 14 | Network | ⚠️ `kernels/network` 有（协议适配层） | **部分** |
| 15 | World Interface | ❌ | **未开始** |
| 16 | Trust / Security / Governance | ⚠️ `kernels/{trust,security,audit}` 有 | **部分**（缺测试） |
| 17 | Economy | ⚠️ `src/cost/` 有雏形 | **未开始** |
| 18 | Verification / Experience | ⚠️ `kernels/evaluation` 有 | **部分** |
| 19 | Evolution | ❌ | **未开始** |
| 20 | L10K / Benchmark | ⚠️ `l10k-baseline.yaml` + `docs/l10k/` 有 | **未开始** |
| 21 | Production Hardening | ⚠️ `src/sre/` 有 | **部分** |

**小结**：
- 已完成 1 / 21
- 部分完成 10 / 21（且"部分"普遍缺测试）
- 未开始 10 / 21

> **状态口径对齐（2026-09-06，R6）**：上表"判定"列使用三态简写，正式状态枚举以 [`CAPABILITY-REGISTRY.md` §5](CAPABILITY-REGISTRY.md) 的 9 枚举为准，映射如下：
> | 简写 | 9 枚举 | 含义 |
> |---|---|---|
> | 已完成 | `IMPLEMENTED` | 7 项 DoD 全过（见 `CODEX-CONTRACT.md` §5） |
> | 部分 | `PARTIALLY_IMPLEMENTED` | 代码存在但 DoD 未过（普遍缺 Tested / Audited / Policy Controlled） |
> | 未开始 | `PLANNED` | 已定义未开工 |
>
> ⚠️ 当前**没有任何 Phase 能达到 `IMPLEMENTED`**：即便"部分"的 kernel 层也仍缺测试与审计，故"部分"严格对应 `PARTIALLY_IMPLEMENTED`，不可写作 `IMPLEMENTED`。

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
