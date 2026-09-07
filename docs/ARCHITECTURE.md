# LIUHAO X v3.0 — 架构总纲

> **定位**：本项目架构的**导航入口（门户）**。所有架构决策、设计原则、系统边界在此定义；**宪法层单一入口为 [`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md)**。
> **版本**：v3.0 · 建立日期：2026-09-06
> **替代**：`LIUHAO-X-V3.0-DEFINITION-LOCK.md`（宪法本体已丢失；宪法层裁决与索引现由 `spec/UNIFIED-BLUEPRINT.md` 承载）

---

## 快速导航

| 读者 | 应该先读 |
|------|----------|
| AI 编码代理 | [`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md) → 本文件 → `spec/KERNEL-CANON.md` |
| 新成员 | 本文件 → `docs/quickstart.md` → `spec/MASTER-SPEC-v3.0.md` 第一部分 |
| 架构师 | 本文件 → `architecture/Architect-Architecture-v3.0.md` → `architecture/gap-analysis.md` |
| 运维 | `operations/production-runbook.md` → `operations/health-check-spec.md` |

---

## 系统定位

**LIUHAO X / 鎏灏 X** = Human-Sovereign Agent Operating System

十源能力体系（ULTRON / VISION / ADA / EDITH / FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）
提取为现实可工程化能力 → 去重 → 统一 → 一个系统。

**不是品牌宣传文档，是工程总规格、代码边界、Runtime 边界、安全边界、数据边界、测试边界与验收边界。**

---

## 核心架构

```
HUMAN
  └→ L-CORE（§19-21，人类主接口）
       └→ LIUHAO KERNEL（14 个，6,391 行）
            ├→ INTELLIGENCE  (Perception/Reasoning/Memory/Context/Analysis/WorldModel)
            ├→ AGENCY        (Runtime/Planning/Execution/Spawning/Scheduling/Orchestration)
            └→ SOVEREIGNTY   (Identity/Permission/Policy/Security/Trust/Governance)
       └→ AGENT FACTORY（§47-48）→ SPECIALIZED AGENTS
       └→ AGENT ORGANIZATION（§61-64）→ AGENT NETWORK（§68-69）
       └→ WORLD INTERFACE（§36-37）→ DIGITAL / INTERNET / PHYSICAL
```

---

## 14 个 Kernel（权威清单）

| # | Kernel | 行数 | Definition Lock | 职责 |
|---|--------|------|-----------------|------|
| 1 | **identity** | 356 | §112 | Agent 身份、权限、L0-L7 作用域过滤 |
| 2 | **memory** | 308 | §112 | 多层记忆存储、L0-L7 作用域过滤、CRUD |
| 3 | **context** | 179 | §112 | 12 类输入 → 压缩 → 模型就绪上下文 |
| 4 | **capability** | 485 | §112 | 能力注册中心、版本、追溯链、作用域检查 |
| 5 | **policy** | 502 | §112 | ABAC 策略引擎、优先级评估、人类主权覆盖 |
| 6 | **execution** | 696 | §112 | Goal→Task→Plan→Action→Verify 全链路 |
| 7 | **resource** | 482 | §112 | CPU/Mem/Storage/Token/Time/$ 六种配额 |
| 8 | **event** | 341 | §112 | 统一事件总线、correlation ID、死信重试 |
| 9 | **network** | 498 | §112 | 协议适配（A2A/MCP/gRPC/HTTP/WS）、路由 |
| 10 | **trust** | 629 | §112 | 信任评分、传播、衰减、撤销 |
| 11 | **evaluation** | 595 | §112 | 结果评估、反馈、重规划触发、升级 |
| 12 | **security** | 450 | §112 | RBAC+ABAC、Vault Transit 加密、审计 |
| 13 | **audit** | 423 | §113 | 防篡改审计链（hash-chain）、关联查询 |
| 14 | **plugin** | 447 | §115 | 插件注册、热加载、版本兼容、作用域激活 |

**合计 6,391 行。**

---

## 8 个 Workstream

| WS | 名称 | 状态 | 覆盖 |
|----|------|------|------|
| A | Foundation & Kernel | COMPLETE | 14 kernels, 6,391 行 |
| B | Data & Storage | COMPLETE | PostgreSQL 15, alembic 31 表 |
| C | Product & UI | IN PROGRESS | PM PRD, UIUX, Console |
| D | Intelligence & Reasoning | IN PROGRESS | RAG, Memory, Knowledge |
| E | Agent Runtime | BLOCKED | 依赖 D |
| F | Network & Protocol | BLOCKED | 依赖 B |
| G | Platform & Tooling | COMPLETE | 工具链, CI/CD |
| H | Operations & Security | 75% | 监控, 审计, 权限 |

---

## 文档层级

```
第 1 层  宪法/架构    **docs/spec/UNIFIED-BLUEPRINT.md（宪法层单一入口）** · docs/ARCHITECTURE.md（架构导航）· docs/spec/MASTER-SPEC-v3.0.md
第 2 层  权威规格    docs/spec/KERNEL-CANON.md · docs/spec/CAPABILITY-REGISTRY.md
第 3 层  正式文档    docs/architecture/ · docs/product/ · docs/operations/
第 4 层  支撑文档    docs/architecture/gap-analysis.md · docs/spec/GAP-MIGRATION-MATRIX.md
第 5 层  草稿        docs/archive/drafts/
第 6 层  历史归档    docs/archive/ 四个合集
```

---

## ⚠️ 当前已知问题

| # | 问题 | 状态 |
|---|------|------|
| 1 | `LIUHAO-X-V3.0-DEFINITION-LOCK.md` 宪法本体缺失 | §112/§113/§115 已从代码 docstring 反推还原 |
| 2 | Kernel 层零单元测试（最高优先级技术债） | 见 `spec/CAPABILITY-REGISTRY.md` |
| 3 | `implementation-status.UNRELIABLE.yaml` 状态假数据已修正 | 修正为 `0 / 0` |
| 4 | `l10k-baseline.yaml` 生产就绪标记已修正 | 修正为 `false` |

详细诊断见：`docs/spec/DEFINITION-LOCK-STATUS.md`

---

## 硬规则摘要（违反即 Reject）

1. 不把概念当实现
2. 不把 TODO 当完成
3. 不把 Mock 当生产能力
4. 不把硬编码 Demo 当智能系统
5. 不把模型输出默认当事实
6. TODO 没做完，状态写 `PARTIALLY_IMPLEMENTED`，不许写 `IMPLEMENTED`
7. 没写测试的功能，不许标 `IMPLEMENTED`
8. R4 虚构能力（AGI/意识/全知/无限复制/完美预测等）→ Never Claim

完整 25 条见：[`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md)

---

## 十源 DNA → 模块映射

| DNA | 能力定位 | 最终模块映射 |
|-----|----------|-------------|
| ULTRON | Agency / Scale / Parallel | AgentRuntime, Scheduler, AgentFactory |
| VISION | Perception / World Understanding | Perception, Observation, WorldModel |
| ADA | Computation / Analysis | DataEngine, Python, SQL, Statistics |
| EDITH | World Access / External | WorldInterface, Browser, Filesystem, Cloud |
| FRIDAY | Realtime Intelligence | RealtimeEngine, EventBus, Monitoring |
| JARVIS | Human Interface / Coordination | LCore, Intent, Context, Planning |
| JOCaSTA | Organization / Management | OrganizationEngine, Roles, Teams |
| KAREN | Personal Context | PersonalContext, PersonalMemory |
| ENOCH | Long-Horizon / Persistent | LongRunningRuntime, PersistentState |
| ZOON | Specialized Domains | SpecializedAgentFramework, DomainSkills |

---

*本文档是 `docs/ARCHITECTURE.md`，为项目架构的导航入口（宪法层单一入口见 `spec/UNIFIED-BLUEPRINT.md`）。所有路径相对本文档。*
