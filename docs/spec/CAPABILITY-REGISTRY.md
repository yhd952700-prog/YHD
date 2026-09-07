# LIUHAO X v3.0 — Capability Registry（能力注册表）

> **本文件定义系统的能力清单、ID 规则与真实实现状态。**
> 版本：v1.0 · 建立日期：2026-09-06
> 取代：`capability-registry.yaml` 中不完整的 11 项声明（该文件保留为机器可读副本，待同步）

---

## 1. Capability ID 规则

来自 MS:§102：

```text
LHX-U-xxx    ULTRON     Agency / Scale / Parallel Execution
LHX-V-xxx    VISION     Perception / World Understanding
LHX-A-xxx    ADA        Computation / Analysis / Data Intelligence
LHX-E-xxx    EDITH      World Access / External System Interaction
LHX-F-xxx    FRIDAY     Realtime Intelligence / Monitoring
LHX-J-xxx    JARVIS     Human Intelligence Interface / Coordination
LHX-JC-xxx   JOCaSTA    Organization / Management
LHX-K-xxx    KAREN      Personal Context / Personal Assistance
LHX-N-xxx    ENOCH      Long-Horizon / Persistent Intelligence
LHX-Z-xxx    ZOON       Specialized Domain Intelligence
LHX-C-xxx    ——         十源去重后的统一核心能力（Kernel 层专用）
```

**Kernel 层能力统一使用 `LHX-C-xxx`**，因为 kernel 已经是去重后的结果，不再属于单一来源。

---

## 2. Kernel 层能力清单（14 项）

| ID | 能力 | Kernel | 承载的十源 DNA | 行数 | 状态 | 单元测试 |
|---|---|---|---|---|---|---|
| LHX-C-001 | Agent Identity & Permission | `identity` | JARVIS / JOCaSTA | 356 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-002 | Multi-tier Memory | `memory` | KAREN / ENOCH | 308 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-003 | Context Engineering | `context` | JARVIS | 179 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-004 | Capability Registry | `capability` | （全源） | 485 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-005 | Policy Enforcement (ABAC) | `policy` | JOCaSTA | 502 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-006 | Execution Pipeline | `execution` | ULTRON / JARVIS | 696 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-007 | Resource Quota | `resource` | ULTRON | 482 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-008 | Event Bus & Correlation | `event` | FRIDAY | 341 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-009 | Network & Protocol Adapters | `network` | EDITH | 498 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-010 | Trust Chain | `trust` | JOCaSTA | 629 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-011 | Outcome Evaluation | `evaluation` | （全源） | 595 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-012 | Security (RBAC+ABAC+Vault) | `security` | JOCaSTA | 450 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-013 | Tamper-evident Audit | `audit` | ENOCH | 423 | PARTIALLY_IMPLEMENTED | ❌ 0 |
| LHX-C-014 | Plugin Management | `plugin` | ZOON | 447 | PARTIALLY_IMPLEMENTED | ❌ 0 |

**达标数：0 / 14。**

> **为什么全部不是 IMPLEMENTED？**
> 按 `CODEX-CONTRACT.md` §5 的 DoD（2026-09-06 起为 7 项），IMPLEMENTED 需要同时满足
> Implemented + Tested + Observable + Permissioned + **Policy Controlled** + Audited + Documented。
> 14 个 kernel 全部缺 **Tested** 与 **Audited**，因此最高只能是 `PARTIALLY_IMPLEMENTED`。

---

## 3. 与 `capability-registry.yaml` 的关系（已同步）

> **2026-09-06 同步完成**：`capability-registry.yaml` 已补齐为 **14 项**（含 security/audit/plugin），状态统一 `PARTIALLY_IMPLEMENTED`，id 改为 `LHX-C-NNN` 并保留 `legacy_id`，追溯用 `UB-A1` 锚点。两者现已一致，下面的差异表仅保留作历史对照。

| 项 | `capability-registry.yaml`（同步后） | 本文件 |
|---|---|---|
| 声明数量 | 14 | 14 |
| 实际列出 | 14 | 14 |
| 含 security / audit / plugin | ✅ LHX-C-012 / 013 / 014 | ✅ |
| 状态 | 全部 `PARTIALLY_IMPLEMENTED` | 全部 `PARTIALLY_IMPLEMENTED` |
| 测试覆盖 | `0/0 pending` | 一致 |

<details><summary>历史：建立本文件时 yaml 的状态（已修正）</summary>

| 项 | `capability-registry.yaml`（旧） | 本文件（正确） |
|---|---|---|
| 声明数量 | 12 | 14 |
| 实际列出 | 11 | 14 |
| 含 security | ❌ | ✅ LHX-C-012 |
| 含 audit | ❌ | ✅ LHX-C-013 |
| 含 plugin | ❌ | ✅ LHX-C-014 |
| 标记为 IMPLEMENTED | 9 项 | 0 项 |

</details>

### 缺失的 3 项为何重要（现已补齐）

- **LHX-C-012 security**：MS:§6 的 12 Kernel 里有它，但旧注册表漏了。这是权限判决的核心。
- **LHX-C-013 audit**：MS:§93 / §155 要求 "Every Critical Action has Audit"，但 §6 的 Kernel 清单里没有它——**这是 Master Spec 自身的漏洞**，代码里已经补上了。
- **LHX-C-014 plugin**：所有文档都漏了它。DL:§115 定义，代码 447 行已实现。

---

## 4. 十源 DNA → 目标模块映射（待实现部分）

Kernel 层之上，十源还有大量能力**尚未实现**。以下为追踪清单（状态均为 `PLANNED`）：

| DNA | 目标模块 | 关键能力 | 状态 |
|---|---|---|---|
| **ULTRON** | `packages/agent/`、`runtime/`、`planning/` | Agent Runtime、Spawning、并行执行、故障恢复、Mission 引擎 | PLANNED（`src/agents/` 不存在） |
| **VISION** | `packages/perception/` | 图像/视频/音频/文档理解、OCR、场景理解、变化检测 | PLANNED |
| **ADA** | `packages/analysis/` | SQL/Python 计算、统计、可视化、异常检测、仿真 | PLANNED |
| **EDITH** | `packages/world/` | Browser/Computer/Filesystem/Shell/Git/Cloud/Devices | PLANNED |
| **FRIDAY** | `packages/realtime/` | 实时监控、告警、事件检测、事故响应 | PLANNED |
| **JARVIS** | `packages/lcore/`（apps/lcore） | L-Core 意图理解、目标形成、规划、委派、结果综合 | PLANNED |
| **JOCaSTA** | `packages/organization/` | 组织、部门、团队、角色、KPI、预算 | PLANNED |
| **KAREN** | `packages/personal/` | 个人画像、偏好、个人记忆、个性化 | PLANNED |
| **ENOCH** | `packages/longhorizon/` | 长时任务、持久化 Agent、Mission 调度、历史分析 | PLANNED |
| **ZOON** | `packages/specialized/` | 领域 Agent 模板（研究/编码/数据/安全/财务…） | PLANNED |

> 上表对应 MS:§143 的 FINAL TEN-SOURCE MAPPING。
> 每项能力在启动实现前，需在本文件登记具体 `LHX-X-xxx` ID。

---

## 5. 状态定义（严格使用，不得越级）

```text
PLANNED               已定义，未开工
IN_PROGRESS           开发中
PARTIALLY_IMPLEMENTED 代码存在但 DoD 未过（当前 14 个 kernel 的状态）
IMPLEMENTED           DoD 六项全过
EXPERIMENTAL          实验性，需 Feature Flag 隔离
RESEARCH              研究依赖，不可进生产
NOT_REALIZABLE        不可实现（需注明理由）
DEPRECATED            已废弃
```

**禁止**：`PLANNED` → 假装 `IMPLEMENTED`（MS:§103）。

---

## 6. 可实现性分级（每个能力必须标注）

来自 MS:§2：

```text
R0 = 现有技术可直接实现
R1 = 成熟组件组合即可实现
R2 = 需要明显工程研发，无基础科学障碍
R3 = 研究依赖，不能保证生产级可靠性
R4 = 虚构能力，不得当作产品能力
```

生产环境只允许 R0 / R1 / R2；R3 进 Experimental；**R4 永远不许声称**。

---

## 7. 维护规则

1. 新增能力 → 先登记 ID、来源 DNA、目标模块、可实现性等级、状态，再写代码。
2. 状态变更 → 必须同时满足 DoD 对应条件，并在本文件更新。
3. `capability-registry.yaml` 与本文件冲突时，**以本文件为准**（P1-2 任务会同步 yaml）。
