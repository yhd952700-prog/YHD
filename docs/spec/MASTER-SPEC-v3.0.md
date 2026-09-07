# LIUHAO X v3.0 — MASTER SPEC（合并版）

> **来源**：《TEN INTELLIGENCE SYSTEMS COMPLETE REAL-IMPLEMENTABLE ENGINEERING MASTER SPEC v3.0》（221 节 + Codex 执行指令）
> **本文件结构（合并版 v1.1）**：
> - **第一部分 · 工程可执行提炼版** — 全部硬性条款 + 主题索引，Codex 日常执行看这里
> - **第二部分 · 221 节原文** — 逐字完整收录的法条层，条款有疑义时以原文为准
> **本文件性质**：To-Be 目标规格。
> **与代码的关系**：本文件描述**目标态**。当前实际进度见 [`GAP-MIGRATION-MATRIX.md`](GAP-MIGRATION-MATRIX.md) 表 3。
> **Kernel 清单以代码为准**：见 [`KERNEL-CANON.md`](KERNEL-CANON.md)（14 个，本 Spec §6 只列 12 个是自身的遗漏）。
> 版本：2026-09-06 v1.0 提炼版落盘 · 2026-09-06 v1.1 合并 221 节原文

---

# 第一部分：工程可执行提炼版

---

## 0. 使命（§0-1）

**LIUHAO X = A Human-Sovereign Operating System for Autonomous Agent Systems**

十源能力体系（ULTRON / VISION / ADA / EDITH / FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）
提取为现实可工程化能力 → 去重 → 统一 → 一个系统。

这不是品牌宣传文档，是**工程总规格、代码边界、Runtime 边界、安全边界、数据边界、测试边界与验收边界**。

---

## 1. 可实现性分级（§2）— 每个能力必须标注

```text
R0 = 现有技术可直接实现          ┐
R1 = 成熟组件组合即可实现         ├ Production 允许
R2 = 明显工程研发，无科学障碍     ┘
R3 = 研究依赖，不保证生产可靠性   → Experimental（须 Feature Flag + 隔离 + 回滚 + 审计）
R4 = 虚构能力                    → Never Claim（AGI/意识/全知/无限复制/完美预测等，§152）
```

---

## 2. 硬规则 25 条（§3）— 违反即 Reject

> 完整版已复制到 [`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md) §2，以该文件为准执行。

---

## 3. 十源 DNA（§4 + §143）

| DNA | 能力定位 | 最终模块映射（§143） |
|---|---|---|
| ULTRON | Agency / Scale / Parallel Execution | AgentRuntime, Scheduler, AgentFactory, Spawning, Parallelism, Recovery, MissionEngine |
| VISION | Perception / World Understanding | Perception, Observation, WorldModel, ChangeDetection, MultimodalUnderstanding |
| ADA | Computation / Analysis / Data | DataEngine, Python, SQL, Statistics, Mathematics, Simulation, Optimization |
| EDITH | World Access / External Systems | WorldInterface, Browser, Computer, Filesystem, Shell, Git, Cloud, Database, Devices |
| FRIDAY | Realtime Intelligence | RealtimeEngine, EventBus, Monitoring, Alerts, Notifications, IncidentDetection |
| JARVIS | Human Interface / Coordination | LCore, Intent, Context, Goal, Planning, Delegation, Coordination, DecisionSupport |
| JOCaSTA | Organization / Management | OrganizationEngine, Roles, Teams, Departments, KPI, AgentManagement, Governance |
| KAREN | Personal Context | PersonalContext, PersonalMemory, Preferences, Personalization, UserAssistance |
| ENOCH | Long-Horizon / Persistent | LongRunningRuntime, PersistentState, Missions, HistoricalIntelligence, StrategicMonitoring |
| ZOON | Specialized Domains | SpecializedAgentFramework, DomainTemplates, DomainSkills, DomainKnowledge, SpecializedTools |

**注意**：十源不是十个 AI 角色。十源是十组能力 DNA，去重后进入统一 Kernel（§144 列出 35 项统一能力）。

---

## 4. 统一架构（§5 + §145）

```text
HUMAN
  └→ L-CORE（§19-21，人类主接口）
       └→ LIUHAO KERNEL（§6）
            ├→ INTELLIGENCE  (Perception/Reasoning/Memory/Context/Analysis/WorldModel)
            ├→ AGENCY        (Runtime/Planning/Execution/Spawning/Scheduling/Orchestration)
            └→ SOVEREIGNTY   (Identity/Permission/Policy/Security/Trust/Governance)
       └→ AGENT FACTORY（§47-48）→ SPECIALIZED AGENTS
       └→ AGENT ORGANIZATION（§61-64）→ AGENT NETWORK（§68-69）
       └→ WORLD INTERFACE（§36-37）→ DIGITAL / INTERNET / PHYSICAL
```

最终产品栈（§145）：L-CORE → INTELLIGENCE FABRIC → LIUHAO KERNEL → AGENT RUNTIME → AGENT FACTORY → SPECIALIZED AGENTS → MULTI-AGENT ORCHESTRATION → AI ORGANIZATION → AGENT NETWORK → WORLD INTERFACE → REAL SYSTEMS

---

## 5. Kernel 层（§6）— ⚠️ 与代码的差异

本 Spec §6 列了 12 个 Kernel：Identity, Memory, Context, Capability, Policy, Execution, Resource, Event, Network, Trust, Security, Evaluation。

**但代码实际有 14 个**（多出 audit §113、plugin §115，且这两者有 Definition Lock 代码佐证）。
**本 Spec 的 §6 清单是遗漏，不是权威。** Kernel 以 [`KERNEL-CANON.md`](KERNEL-CANON.md) 为准。

规则（§6 仍然有效）：每一个高级功能都必须依赖 Kernel，**不得创建第二套互相冲突的 Identity / Policy / Capability / Audit 系统**。

---

## 6. 目标仓库结构（§7 精简）

```text
liuhao-x/
├── apps/        api / worker / scheduler / runtime / lcore / web
├── packages/    kernel + 34 个领域包（identity…evolution，见 GAP-MIGRATION-MATRIX 表 2）
├── infrastructure/  postgres / redis / qdrant / redpanda / minio / vault / otel / prometheus / grafana / loki / tempo
├── migrations/  tests/（unit/integration/e2e/security/agent/memory/policy/network/organization/performance/chaos/benchmark/regression）
└── docs/ docker/ scripts/ .github/
```

**当前 `src/` 单体 → `packages/` 的完整映射见 [`GAP-MIGRATION-MATRIX.md`](GAP-MIGRATION-MATRIX.md) 表 2。**

## 7. 技术基线（§8）

- Backend：Python 3.12+ / FastAPI / Pydantic v2 / SQLAlchemy 2 / Alembic / asyncio / httpx / tenacity / structlog
- Frontend：React / TypeScript / Vite / TanStack Query / Zustand / WebSocket / SSE
- Infra：PostgreSQL 15+ / Redis 7.2+ / Qdrant 1.8+ / Redpanda|Kafka / S3|MinIO / Vault / Docker / K8s
- Observability：OpenTelemetry / Prometheus / Grafana / Loki / Tempo|Jaeger
- Security：OAuth2 / OIDC / JWT / RBAC / ABAC / Capability Authorization / Vault / Sandbox / Network Policy

## 8. 部署策略（§9）— 禁止过早微服务化

```text
第一阶段：Modular Monolith + API + Worker + Runtime + Scheduler + Event Bus
后续：仅当真实性能/可靠性/团队边界驱动时，才拆 Identity/AgentRuntime/Memory/ModelGateway/Tool/Network/Organization/Economy 服务
```

## 9. 领域契约（§10）

每个 Domain 必须包含：`models.py schemas.py repository.py service.py policy.py events.py errors.py`；
Runtime 型 Domain 追加：`process.py scheduler.py executor.py checkpoint.py recovery.py`；加 `tests/`。

---

## 10. 子系统规格索引（§11–§176）

按主题快速定位（完整原文见第二部分对应节号）：

| 主题 | 节号 | 要点 |
|---|---|---|
| Identity / Agent Identity | §11-14 | Principal 体系；无 Identity 不许执行；Agent 是受管 Process（9 状态机 + start/pause/resume/stop/cancel/checkpoint/recover） |
| Goal / Task / Action / Execution | §15-18 | Goal≠Task；每次执行可追踪（ExecutionID/TraceID/Cost/Latency/Verification） |
| L-Core（JARVIS） | §19-21 | Intent→Context→Goal→Plan→Policy→Execution→Verify→Response；含 Python 契约类 |
| KAREN 个人智能 | §22 | 个性化/连续性/偏好；必须过 Memory Permission |
| Memory | §23-27 | 12 类记忆；Owner 原则；L0-L7 作用域；12 种权限动词；Provenance 9 字段 |
| Context Engine | §28 | 11 种来源 → Permission/Security/Sensitivity 过滤 → 压缩 → 窗口 |
| VISION 感知 | §29-32 | 统一输出 Observation；Entity Resolution → World Model（不是现实世界本身） |
| ADA 计算 | §33-35 | 代码执行必须进 Sandbox（CPU/Mem/FS/Net/Timeout/Process 限制） |
| EDITH 世界接口 | §36-37 | observe/validate/authorize/execute/verify 五段契约 |
| Capability / Tool | §38-41 | Capability≠Permission≠Policy；Tool 生命周期 REGISTER→VALIDATE→APPROVE→ACTIVE→…→REVOKED |
| Policy / Approval / Autonomy | §42-45 | 输出 ALLOW/DENY/REQUIRE_APPROVAL；A0-A5 自治等级不得自动跃迁 |
| ULTRON 运行时 | §46-54 | Controlled Spawning（Quota/Budget/Resource/Depth/Lifetime 限制）；Saga 补偿；Checkpoint |
| FRIDAY 实时 | §55-57 | 事件目录 + EventBus（Redis Streams 或 Redpanda，二选一未拍板） |
| ENOCH 长程 | §58-60 | Mission 不依赖单次 HTTP 生命周期 |
| JOCaSTA 组织 | §61-64 | Organization 是一等对象；必须是真实 Runtime Entity；KPI 体系 |
| ZOON 领域智能 | §65-67 | 不实现为固定 Agent，实现为 Specialized Agent Framework（模板可扩展） |
| Network / Trust / Security | §68-73 | A2A/MCP/HTTP/WS/gRPC 适配；外部 Agent 不能绕过 Identity/Trust/Capability/Policy/Audit；Threat Model 11 项 |
| Resource / Budget / Economy | §74-76 | 9 种资源；Budget 六动词；Economy 三阶段且可关闭 |
| World Simulation | §77 | Digital Twin 只做 Entity/State/Relationship/Event/Scenario；禁止宣称完美预测 |
| Verification / Experience / Evolution | §78-80 | 四态验证（VERIFIED/PARTIAL/FAILED/UNKNOWN）；禁止未经评估的生产自修改 |
| Governance / Emergency / World Safety | §81-83 | 9 种紧急控制；物理设备更严策略 |
| API / DB / Redis / Vector / Object / Model | §84-91 | 全 API 路由清单；59 张表 schema；Model Gateway/Router/Registry（Agent 不得硬编码绑定单一模型） |
| Observability / Audit | §92-93 | OTel 贯穿 12 层；Critical Action Audit 必答 12 问 |
| L-Core 前端 | §94-96 | 状态必须来自真实 Runtime Event，禁止假动画 |
| Plugin / Agent FS / Sovereignty / External | §97-100 | Plugin 只许 Capability API；Agent FS 是逻辑抽象；外部 Agent 禁止直连 Kernel |
| Capability Registry | §101-104 | LHX-X-xxx ID 体系；状态 8 种；Release 前四项 100% 覆盖 |
| 测试体系 | §105-109 | 19 类测试；14 项安全测试；1h/6h/24h 长跑 |
| 性能 / 成本 / L10K | §110-116 | 9 项性能测量；Cost per Verified Task；VHL 公式与反作弊 |
| DoD / Gates / CI / DevEnv | §117-122 | 每功能 12 问；Release 11 道门禁；docker compose up 一键起 |
| 运维 | §123-131 | Alembic 强制；Vault 管密钥；幂等/重试/熔断/舱壁 |
| 人类控制 | §132-136 | 人可随时 Pause/Stop/Cancel/Override；Human 主权层级不可被 Trust/Performance 自动超越 |
| 七大循环 | §137-142 | Intelligence / World / Human / Organization / Long-Horizon / Evolution Loop |
| 十源映射 / 去重核心 | §143-144 | 见本文 §3 |
| 产品栈 / UX / 场景 | §145-151 | 用户只表达 Goal；四个端到端场景（AI 公司 / 研究100家 / 上线项目 / 90天监控） |
| 边界声明 | §152-153 | 能做的 14 项 vs 永不声称的 10 项（AGI/意识/无限…） |
| 最终审计 | §154-156 | Capability Audit 11 步；Engineering Audit 10 个 Every；UI/API/Service/Domain/Runtime/DB 六层一致 |
| 反假货 | §157-161 | NO FAKE Frontend / AI / Tool / Agent / Organization |
| 工程质量 / 错误 / 版本 | §162-169 | 16 项代码质量要求；15 类统一错误；9 类版本化 |
| 隐私 / 声誉 / 合约 / 图 | §170-176 | 跨界必须查权限；Reputation≠Permission；三类关系图 |
| 实施顺序 | §177-198 | 21 Phase，每 Phase 有验收（见下） |
| 运维收尾 | §199-213 | Backup/DR/Incident/文档/ADR/各 View |
| 世界定义 / 最终公式 | §213-221 | Agent World 工程定义（9 要素）；产品定位五阶段；Codex 总指令 |

---

## 11. Capability ID 体系（§101-104）

```text
LHX-U/V/A/E/F/J/JC/K/N/Z-xxx（十源） + LHX-C-xxx（统一核心）
状态：PLANNED / IN_PROGRESS / IMPLEMENTED / PARTIALLY_IMPLEMENTATED /
      EXPERIMENTAL / RESEARCH / NOT_REALIZABLE / DEPRECATED
Release 前：Source Coverage / Atomic Mapping / Critical Policy / Critical Audit / Critical Test 全 100%
（100% = 所有已定义能力都有明确状态、归属、验证路径，不是全部开发完成）
```

→ 执行版见 [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md)

---

## 12. L10K / VHL（§112-116）

```text
VHL = Verified Value Output / Human Active Minutes
对比基准：Human Only / Copilot / Single Agent / Multi-Agent / LIUHAO X
目标：VHL_Liuhao / VHL_Baseline ≥ 10,000（仅在质量/安全/可靠性/授权/成本门槛满足时才有意义）
反作弊（§116）：禁止靠加 Agent/加 Token/做简单任务/重复任务/降质量/忽略人类时间刷指标
Benchmark 必须：Fixed / Weighted / Held-out / Reproducible / Auditable
```

---

## 13. DoD 与 Release Gates（§117-118）

**每个功能必须回答 12 问**（Domain/State/Identity/Permission/Capability/Runtime/Event/Failure/Security/Test/Metric/Rollback），缺一不算完成。

**Release 前 11 道门**：Format / Lint / Type / Unit / Integration / Security / E2E / Performance / Agent Evaluation / Benchmark / Regression。Critical Failure → Block Release。

---

## 14. 21 Phase 实施顺序与验收（§177-198）

| Phase | 内容 | 验收要点 |
|---|---|---|
| 1 Foundation | 仓库/配置/DB/Docker/CI | compose up → API 起 → migrate → health 过 |
| 2 Kernel | Identity/Principal/Role/Capability/Policy/Event/Resource/Audit | 建用户/组织/Agent 身份，授权，拒绝越权，留审计 |
| 3 Agent Runtime | Agent/状态机/Worker/Scheduler/Checkpoint | create→run→pause→resume→stop→recover |
| 4 Model Gateway | Provider/Registry/Router/Streaming/成本 | 调模型、测延迟成本、failover、按能力路由 |
| 5 Memory | 多层存储/权限/向量检索 | write/read/filter/deny/vector/delete/audit |
| 6 Capability/Tool | 注册/执行/健康/Sandbox | 注册、授权、执行、拒绝未授权、审计 |
| 7 Policy/Approval | DSL/工作流/风险分级 | ALLOW/DENY/REQUIRE_APPROVAL 三态 |
| 8 Execution | Goal/Task/Plan/Retry/补偿/验证 | 复杂任务 E2E、故障恢复、断点续跑 |
| 9 L-Core | 对话/意图/规划/委派/综合 | 意图→真任务→真执行→真验证结果 |
| 10 Multi-Agent | Factory/Spawn/编排/消息 | Manager→Researchers→Analyst→QA→结果 |
| 11 Perception/ADA | 视觉/文档/音频/分析 | 图像/文档/数据→真实 Observation |
| 12 Organization | 组织/部门/团队/KPI/预算 | 建组织、配 Agent、分派、评估、报告 |
| 13 ENOCH | 持久 Agent/Mission/Checkpoint | 长程任务：重启→恢复→观察→重规划 |
| 14 Network | 发现/A2A/MCP/外部身份 | 外部 Agent：认证→授权→执行→审计 |
| 15 World | Browser/FS/Git/DB/HTTP/设备 | 真实外部系统：Policy→Action→Verify→Audit |
| 16 Trust/Sec/Gov | 威胁模型/声誉/撤销/急停 | 攻击→检测/阻断→审计 |
| 17 Economy | 预算/配额/计费/市场 | reserve/consume/block/usage report |
| 18 Verification | 验证器/评估/经验提取 | 结果→VERIFIED/PARTIAL/FAILED/UNKNOWN |
| 19 Evolution | 实验/基准/审批/回滚 | 禁止无界自修改 |
| 20 L10K | 基准注册/基线/任务分级 | 输出 VHL/成功率/干预率/成本 |
| 21 Hardening | 安全加固/混沌/DR/Runbook | 全套生产化 |

**当前实际进度对照 → [`GAP-MIGRATION-MATRIX.md`](GAP-MIGRATION-MATRIX.md) 表 3（已完成 1 / 部分 10 / 未开始 10）**

---

## 15. 反假货五连（§157-161）原文

```text
§157 NO FAKE FRONTEND — 前端必须从 API/WebSocket/SSE/Event Store 读真实状态，
     禁止 setTimeout / fake progress / fake agent count / fake execution / fake success
§158 NO FAKE AI — 未实现就 return NOT_IMPLEMENTED 或标 PLANNED/EXPERIMENTAL，
     不要生成看起来像成功的假答案
§159 NO FAKE TOOL — 必须 Registered + Validated + Authorized + Executable + Observable + Auditable，
     只有 JSON Schema 不算实现
§160 NO FAKE AGENT — 必须有 Identity/Runtime State/Memory/Capabilities/Permissions/Budget/Lifecycle/History
§161 NO FAKE ORGANIZATION — 必须有 Members/Roles/Departments/Teams/Goals/Policies/Budget/Memory/KPIs/Audit
```

---

## 16. 最终工程原则（§220）

> 任何智能都必须有边界。任何能力都必须有身份。任何 Tool 都必须有 Capability。
> 任何 Action 都必须有 Policy。任何 Critical Action 都必须可审计。任何 Memory 都必须有 Owner。
> 任何外部 Agent 都必须有 Trust Boundary。任何 Autonomous Behavior 都必须有 Resource Boundary。
> 任何生产改动都必须经过 Evaluation。**任何能力状态都必须真实反映实际实现状态。**

---

## 附：221 节主题速查

§0-1 定位 · §2 可实现性 · §3 硬规则 · §4 十源 · §5 架构图 · §6 Kernel · §7 仓库 · §8 技术栈 · §9 部署 · §10 领域契约 · §11-14 身份与进程 · §15-18 执行链 · §19-22 L-Core/KAREN · §23-28 记忆/上下文 · §29-32 感知 · §33-35 计算 · §36-41 世界/能力/工具 · §42-45 策略/审批/自治 · §46-54 运行时/恢复 · §55-57 实时 · §58-60 长程 · §61-67 组织/领域 · §68-73 网络/信任/安全 · §74-76 资源/经济 · §77 仿真 · §78-80 验证/经验/进化 · §81-83 治理/急停/物理安全 · §84-93 API/DB/模型/观测/审计 · §94-96 前端 · §97-100 插件/主权/外部 · §101-104 能力注册 · §105-116 测试/性能/成本/L10K · §117-122 DoD/CI · §123-131 配置/限流/幂等 · §132-136 人类控制 · §137-142 七大循环 · §143-146 十源映射/产品栈 · §147-151 场景 · §152-153 现实边界 · §154-156 终审计 · §157-161 反假货 · §162-176 质量/版本/隐私/图 · §177-198 21 Phase · §199-213 运维/文档 · §213-221 世界定义/最终指令

---

# 第二部分：221 节原文（逐字收录）

> **编者注（2026-09-06）**：以下为 Spec 原文逐字收录，未做任何删改，作为法条层备查。原文自身的已知问题——§6 Kernel 清单缺 audit / plugin（代码实为 14 个，见 [`KERNEL-CANON.md`](KERNEL-CANON.md)）、§57 事件总线 Redis Streams vs Redpanda 未拍板、§143 十源映射与 As-Built 架构文档 §4 冲突——以第一部分的说明与 KERNEL-CANON.md / GAP-MIGRATION-MATRIX.md 的裁决为准。

---

# LIUHAO X

# TEN INTELLIGENCE SYSTEMS

# COMPLETE REAL-IMPLEMENTABLE ENGINEERING MASTER SPEC v3.0

## ULTRON + VISION + ADA + EDITH + FRIDAY + JARVIS + JOCaSTA + KAREN + ENOCH + ZOON

## = LIUHAO X

---

# 0. DOCUMENT STATUS

**Project:** 鎏灏 / LIUHAO X
**Specification:** Complete Real-Implementable Engineering Master Spec
**Version:** v3.0
**Base Architecture:** 63827
**Role:** Master engineering specification + Codex execution contract

这不是品牌宣传文档。

这不是概念 PPT。

这不是科幻设定。

这是：

> **LIUHAO X 的工程总规格、代码边界、Runtime 边界、安全边界、数据边界、测试边界与验收边界。**

---

# 1. FINAL MISSION

将以下十种能力体系：

```text
ULTRON
VISION
ADA
EDITH
FRIDAY
JARVIS
JOCaSTA
KAREN
ENOCH
ZOON
```

提取为现实可工程化能力，去除重复，实现统一，并形成：

# LIUHAO X

> **A Human-Sovereign Operating System for Autonomous Agent Systems**

中文：

> **一个具有人类主权的自主智能体操作系统。**

最终系统负责统一：

```text
Perception
Understanding
Reasoning
Memory
Context
Planning
Autonomy
Execution
Computation
Tool Use
World Interaction
Realtime Intelligence
Personal Intelligence
Long-Horizon Intelligence
Specialized Intelligence
Multi-Agent Collaboration
Organization
Network
Trust
Security
Resource Management
Economy
Governance
Verification
Experience
Controlled Evolution
Observability
```

---

# 2. NON-FICTION ENGINEERING POLICY

Codex 必须始终区分：

```text
REAL NOW
REAL WITH INTEGRATION
REAL WITH ENGINEERING
EXPERIMENTAL
RESEARCH
FICTIONAL
```

定义：

```text
R0 = 现有技术可直接实现
R1 = 通过成熟组件组合即可实现
R2 = 需要明显工程研发，但不存在基础科学障碍
R3 = 研究依赖，当前不能保证生产级可靠性
R4 = 影视/虚构能力，不能当作真实产品能力
```

Production 允许：

```text
R0
R1
R2
```

Experimental：

```text
R3
```

Never Claim:

```text
R4
```

---

# 3. ABSOLUTE CODEX RULES

Codex 必须遵守：

```text
1. 不把概念当实现。
2. 不把 TODO 当完成。
3. 不把 Mock 当生产能力。
4. 不把硬编码 Demo 当智能系统。
5. 不把模型输出默认当事实。
6. 不把 Tool Call 默认当授权。
7. 不把 Agent 默认当可信。
8. 不把 Memory 默认公开。
9. 不允许 Agent 自动扩大权限。
10. 不允许无限 Agent Spawn。
11. 不允许未经验证的生产自修改。
12. 不允许隐藏 Tool Execution。
13. 不允许隐藏 Network Access。
14. 不允许隐藏 Side Effect。
15. Critical Action 必须 Audit。
16. Critical Action 必须 Policy。
17. 高风险 Action 必须 Approval。
18. Untrusted Code 必须 Sandbox。
19. External Agent 必须 Trust Boundary。
20. Agent 必须拥有 Identity。
21. Tool 必须拥有 Capability。
22. Memory 必须拥有 Owner。
23. Release 必须通过 Test。
24. Release 必须通过 Security Gate。
25. Release 必须通过 Evaluation。
```

---

# 4. THE TEN SOURCE SYSTEMS

十源系统最终不是十个 AI。

它们是十组能力 DNA：

```text
ULTRON
→ Agency / Scale / Parallel Execution

VISION
→ Perception / World Understanding

ADA
→ Computation / Analysis / Data Intelligence

EDITH
→ World Access / External System Interaction

FRIDAY
→ Realtime Intelligence / Monitoring

JARVIS
→ Human Intelligence Interface / Coordination

JOCaSTA
→ Organization / Management

KAREN
→ Personal Context / Personal Assistance

ENOCH
→ Long-Horizon / Persistent Intelligence

ZOON
→ Specialized Domain Intelligence
```

---

# 5. FINAL UNIFIED SYSTEM

```text
                        HUMAN
                          │
                          ▼
                       L-CORE
                          │
                          ▼
                  LIUHAO KERNEL
                          │
          ┌───────────────┼────────────────┐
          │               │                │
      INTELLIGENCE      AGENCY        SOVEREIGNTY
          │               │                │
      Perception       Runtime          Identity
      Reasoning        Planning         Permission
      Memory           Execution        Policy
      Context          Spawning         Security
      Analysis         Scheduling       Trust
      World Model      Orchestration     Governance
          │               │                │
          └───────────────┼────────────────┘
                          │
                    AGENT FACTORY
                          │
                    SPECIALIZED AGENTS
                          │
                   AGENT ORGANIZATION
                          │
                     AGENT NETWORK
                          │
                   WORLD INTERFACE
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
       DIGITAL          INTERNET        PHYSICAL
        WORLD             / CLOUD        ADAPTERS
```

---

# 6. CORE KERNEL

以下为 Kernel 级系统：

```text
Identity Kernel
Memory Kernel
Context Kernel
Capability Kernel
Policy Kernel
Execution Kernel
Resource Kernel
Event Kernel
Network Kernel
Trust Kernel
Security Kernel
Evaluation Kernel
```

每一个高级功能都必须依赖 Kernel。

不得创建第二套互相冲突的 Identity、Policy、Capability、Audit 系统。

---

# 7. REPOSITORY TARGET

目标仓库：

```text
liuhao-x/
```

结构：

```text
liuhao-x/

├── apps/
│   ├── api/
│   ├── worker/
│   ├── scheduler/
│   ├── runtime/
│   ├── lcore/
│   └── web/
│
├── packages/
│   ├── kernel/
│   ├── identity/
│   ├── context/
│   ├── memory/
│   ├── perception/
│   ├── reasoning/
│   ├── analysis/
│   ├── planning/
│   ├── goal/
│   ├── task/
│   ├── agent/
│   ├── runtime/
│   ├── execution/
│   ├── capability/
│   ├── tools/
│   ├── policy/
│   ├── approval/
│   ├── orchestration/
│   ├── organization/
│   ├── network/
│   ├── world/
│   ├── realtime/
│   ├── trust/
│   ├── security/
│   ├── resource/
│   ├── economy/
│   ├── governance/
│   ├── verification/
│   ├── experience/
│   ├── evolution/
│   ├── observability/
│   ├── sandbox/
│   └── common/
│
├── migrations/
├── infrastructure/
│   ├── postgres/
│   ├── redis/
│   ├── qdrant/
│   ├── redpanda/
│   ├── object-storage/
│   ├── vault/
│   ├── otel/
│   ├── prometheus/
│   ├── grafana/
│   ├── loki/
│   └── tempo/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   ├── security/
│   ├── agent/
│   ├── memory/
│   ├── policy/
│   ├── network/
│   ├── organization/
│   ├── performance/
│   ├── chaos/
│   ├── benchmark/
│   └── regression/
│
├── docs/
├── scripts/
├── docker/
├── .github/
│
├── pyproject.toml
├── alembic.ini
├── docker-compose.yml
├── Makefile
└── README.md
```

---

# 8. TECHNOLOGY BASELINE

Backend：

```text
Python 3.12+
FastAPI
Pydantic v2
SQLAlchemy 2
Alembic
asyncio
httpx
tenacity
structlog
```

Frontend：

```text
React
TypeScript
Vite
Ant Design / Mantine
TanStack Query
Zustand
WebSocket
SSE
ECharts / Recharts
```

Infrastructure：

```text
PostgreSQL 15+
Redis 7.2+
Qdrant 1.8+
Redpanda / Kafka
S3 / MinIO
Vault
Docker
Kubernetes
```

Observability：

```text
OpenTelemetry
Prometheus
Grafana
Loki
Tempo / Jaeger
```

Security：

```text
OAuth2
OIDC
JWT
RBAC
ABAC
Capability Authorization
Vault
Sandbox
Network Policy
```

---

# 9. ARCHITECTURAL DEPLOYMENT STRATEGY

第一阶段禁止为了“看起来像大系统”而拆出大量微服务。

采用：

```text
Modular Monolith
+
API
+
Worker
+
Runtime
+
Scheduler
+
Event Bus
```

后续根据真实负载拆分。

可能拆分为：

```text
Identity Service
Agent Runtime Service
Memory Service
Model Gateway
Tool Service
Network Service
Organization Service
Economy Service
```

但必须由真实性能、可靠性或团队边界驱动。

---

# 10. DOMAIN CONTRACT

每个 Domain 必须包含：

```text
models.py
schemas.py
repository.py
service.py
policy.py
events.py
errors.py
```

Runtime 型 Domain：

```text
process.py
scheduler.py
executor.py
checkpoint.py
recovery.py
```

测试：

```text
tests/
```

---

# 11. IDENTITY KERNEL

主体：

```text
Owner
User
Organization
Workspace
Principal
Agent
SubAgent
Service
ExternalAgent
```

核心字段：

```text
principal_id
owner_id
organization_id
workspace_id
role
identity_state
credentials
trust_level
```

---

# 12. AGENT IDENTITY

每个 Agent：

```text
AgentID
PrincipalID
OwnerID
OrganizationID
RoleID
Version
State
Trust
Budget
Capabilities
Permissions
MemoryPolicy
NetworkPolicy
ParentAgentID
```

没有 Identity：

> 不允许 Agent 执行。

---

# 13. AGENT PROCESS MODEL

Agent 是受管理的 Process。

状态：

```text
CREATED
READY
RUNNING
WAITING
BLOCKED
SUSPENDED
FAILED
COMPLETED
TERMINATED
```

Agent Runtime 必须支持：

```text
start()
pause()
resume()
stop()
cancel()
execute()
checkpoint()
recover()
```

---

# 14. AGENT LIFECYCLE

```text
DEFINE
 ↓
CREATE
 ↓
EVALUATE
 ↓
PROVISION
 ↓
ACTIVATE
 ↓
RUN
 ↓
MONITOR
 ↓
LEARN
 ↓
RE-EVALUATE
 ↓
SUSPEND
 ↓
REVOKE
 ↓
TERMINATE
```

---

# 15. GOAL ENGINE

Goal：

```text
GoalID
Owner
Description
SuccessCriteria
Priority
Deadline
Budget
Risk
ParentGoal
```

Goal 不等于 Task。

Goal 可以产生多个 Task。

---

# 16. TASK ENGINE

Task：

```text
TaskID
GoalID
Owner
Agent
Priority
Deadline
Budget
Risk
Status
Dependencies
RequiredCapabilities
Result
Verification
```

Task Model：

```text
Intent
→ Goal
→ Task
→ Action
→ Execution
→ Outcome
```

---

# 17. ACTION MODEL

Action：

```text
ActionID
Actor
Capability
Tool
Target
Parameters
RiskLevel
PolicyDecision
Approval
Result
```

---

# 18. EXECUTION MODEL

Execution：

```text
ExecutionID
TaskID
AgentID
PlanID
Status
StartedAt
FinishedAt
Cost
Latency
Result
Verification
TraceID
```

每一步可追踪。

---

# 19. JARVIS / L-CORE

L-Core 是人类与 LIUHAO X 的主智能接口。

能力：

```text
Conversation
Intent Understanding
Context Understanding
Goal Formation
Planning
Delegation
Agent Selection
Tool Selection
Approval
Execution Monitoring
Decision Support
Result Synthesis
Explanation
Interruption
Personalization
```

---

# 20. L-CORE FLOW

```text
Human Intent
 ↓
Intent Parser
 ↓
Context Builder
 ↓
Goal Creator
 ↓
Planner
 ↓
Agent Selector
 ↓
Policy
 ↓
Execution
 ↓
Verification
 ↓
Response
```

---

# 21. L-CORE CONTRACT

```python
class LCore:

    async def handle_intent(self, intent):

        context = await self.context.build_for_intent(
            intent
        )

        goal = await self.goal.create(
            intent=intent,
            context=context,
        )

        plan = await self.planner.create_plan(
            goal=goal,
            context=context,
        )

        decision = await self.policy.authorize(
            plan
        )

        result = await self.execution.execute(
            plan=plan,
            decision=decision,
        )

        verified = await self.verifier.verify(
            result
        )

        return await self.response.synthesize(
            verified=verified,
            context=context,
        )
```

---

# 22. KAREN / PERSONAL INTELLIGENCE

Personal Intelligence：

```text
User Profile
User Preferences
Conversation History
Personal Memory
Personal Knowledge
Behavior Context
Task Context
Relationship Context
```

能力：

```text
Personalization
Continuity
Preference Awareness
Contextual Suggestions
User Assistance
```

必须通过 Memory Permission。

---

# 23. MEMORY KERNEL

Memory：

```text
Working
Session
Episodic
Semantic
Procedural
Preference
Decision
Experience
Organizational
Knowledge
Event
World
```

---

# 24. MEMORY OWNERSHIP

原则：

```text
Human → Human Memory
Agent → Agent Memory
Organization → Organization Memory
System → System Memory
```

Liuhao 负责：

```text
Storage
Index
Retrieval
Authorization
Lifecycle
Audit
Federation
```

Liuhao 不自动拥有所有人的 Memory。

---

# 25. MEMORY SCOPES

```text
L0 System
L1 Human
L2 Organization
L3 Workspace
L4 Team
L5 Agent
L6 Task
L7 Session
```

Visibility：

```text
Private
Restricted
Team
Workspace
Organization
Network
Public
```

---

# 26. MEMORY PERMISSIONS

必须支持：

```text
DISCOVER
READ
WRITE
APPEND
MODIFY
DELETE
SHARE
EXPORT
DELEGATE
SUMMARIZE
DERIVE
ADMIN
```

---

# 27. MEMORY PROVENANCE

每条 Memory 至少追踪：

```text
MemoryID
Owner
CreatedBy
Source
SourceType
Provenance
Confidence
Timestamp
Sensitivity
Version
```

---

# 28. CONTEXT ENGINE

Context Sources：

```text
User
Agent
Task
Goal
Organization
Memory
World
Tools
Policy
Time
Environment
```

Pipeline：

```text
Collect
 ↓
Permission Filter
 ↓
Security Filter
 ↓
Sensitivity Filter
 ↓
Relevance
 ↓
Compression
 ↓
Context Window
```

---

# 29. VISION / PERCEPTION ENGINE

输入：

```text
Image
Video
Audio
Document
Browser
Screen
```

能力：

```text
Image Understanding
Video Understanding
Audio Understanding
Document Understanding
OCR
Object Recognition
Scene Understanding
Spatial Understanding
Temporal Understanding
Multimodal Fusion
Visual Monitoring
Change Detection
Entity Extraction
Relationship Extraction
```

---

# 30. OBSERVATION MODEL

Perception 的统一输出：

```text
ObservationID
Source
Timestamp
Entity
Attributes
Relations
State
Confidence
Provenance
```

流程：

```text
Input
 ↓
Perception
 ↓
Observation
 ↓
Entity Resolution
 ↓
World Model
```

---

# 31. WORLD MODEL

实体：

```text
Entity
Relationship
State
Event
Timeline
Source
Confidence
```

World Model 是：

> 系统当前可验证的世界状态模型。

不是现实世界本身。

---

# 32. WORLD STATE

```text
WorldState
 ├── Entities
 ├── Relationships
 ├── Events
 ├── Resources
 ├── Goals
 ├── Risks
 └── Constraints
```

---

# 33. ADA / COMPUTATIONAL INTELLIGENCE

能力：

```text
Data Ingestion
Data Cleaning
Data Transformation
SQL
Python
Statistics
Mathematics
Visualization
Pattern Detection
Anomaly Detection
Correlation
Classification
Clustering
Optimization
Simulation
Experimentation
Numerical Analysis
Forecasting
Report Generation
Code Execution
```

---

# 34. COMPUTE ENGINE

```text
Question
 ↓
Data Discovery
 ↓
Permission
 ↓
Query / Code
 ↓
Sandbox
 ↓
Compute
 ↓
Verification
 ↓
Result
```

---

# 35. CODE EXECUTION

Code：

```text
Python
SQL
Approved Shell Tasks
Approved Data Processing
```

进入：

```text
Compute Sandbox
```

必须存在：

```text
CPU Limit
Memory Limit
Filesystem Policy
Network Policy
Execution Timeout
Process Limit
```

---

# 36. EDITH / WORLD INTERFACE

适配：

```text
Browser
Computer
Filesystem
Shell
Git
Database
Cloud
HTTP
Email
Calendar
Enterprise API
IoT
Devices
Robotics Adapter
```

---

# 37. WORLD ACTION CONTRACT

统一：

```python
class WorldInterface:

    async def observe(self, request):
        ...

    async def validate(self, request):
        ...

    async def authorize(self, request):
        ...

    async def execute(self, request):
        ...

    async def verify(self, result):
        ...
```

---

# 38. CAPABILITY KERNEL

Capability 示例：

```text
browser.read
browser.write

computer.read
computer.control

filesystem.read
filesystem.write

shell.execute

github.read
github.write

database.read
database.write

cloud.read
cloud.write
cloud.deploy

email.read
email.send

calendar.read
calendar.write

payment.create

device.observe
device.control
```

---

# 39. CAPABILITY ≠ PERMISSION

Capability：

> 系统提供某种能力。

Permission：

> 主体被授予这种能力。

Policy：

> 当前环境下是否允许。

---

# 40. TOOL REGISTRY

Tool：

```text
ToolID
Name
Version
Description
Schema
Capability
Risk
Permission
Dependencies
SandboxPolicy
Health
AuditPolicy
```

生命周期：

```text
REGISTER
 ↓
VALIDATE
 ↓
APPROVE
 ↓
ACTIVE
 ↓
SUSPENDED
 ↓
REVOKED
```

---

# 41. TOOL EXECUTION FLOW

```text
Agent
 ↓
Capability
 ↓
Policy
 ↓
Approval
 ↓
Sandbox
 ↓
Tool
 ↓
External System
 ↓
Result
 ↓
Verification
 ↓
Audit
```

---

# 42. POLICY ENGINE

Policy 输出：

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

输入：

```text
Identity
Role
Capability
Tool
Action
Risk
Budget
Resource
Organization
Environment
Target
Time
```

---

# 43. POLICY EVALUATION EXAMPLES

```text
email.send
→ ALLOW / APPROVAL

database.read
→ ALLOW

database.write
→ APPROVAL

payment.create
→ APPROVAL

production.delete
→ DENY
```

具体策略必须可配置和版本化。

---

# 44. HUMAN APPROVAL SYSTEM

Approval：

```text
APPROVE
REJECT
APPROVE_ONCE
APPROVE_TASK
APPROVE_SESSION
```

Approval 必须包含：

```text
Approver
Action
Scope
Reason
CreatedAt
ExpiresAt
AuditID
```

---

# 45. AUTONOMY LEVELS

```text
A0 Observe

A1 Recommend

A2 Low-Risk Execute

A3 Autonomous Task

A4 Autonomous Workflow

A5 Autonomous Organization
```

不得自动跃迁。

---

# 46. ULTRON / AUTONOMOUS RUNTIME

能力：

```text
Goal Pursuit
Task Decomposition
Parallel Execution
Dynamic Scheduling
Agent Delegation
Agent Spawning
Agent Coordination
Persistent Runtime
Event Response
Failure Recovery
Replanning
Resource Allocation
Mission Management
Distributed Execution
```

---

# 47. AGENT FACTORY

Agent Factory 输入：

```text
AgentType
Goal
Capabilities
MemoryPolicy
ModelPolicy
Budget
Resources
Permissions
SandboxPolicy
EvaluationSuite
```

输出：

```text
Agent
Identity
Runtime
Memory
Capabilities
Policy
Budget
Sandbox
```

---

# 48. CONTROLLED SPAWNING

```text
Parent Agent
 ↓
Spawn Request
 ↓
Parent Authorization
 ↓
Quota Check
 ↓
Budget Check
 ↓
Resource Check
 ↓
Capability Check
 ↓
Policy Check
 ↓
Security Check
 ↓
Sandbox
 ↓
Create Agent
 ↓
Audit
```

必须：

```text
Spawn Limit
Depth Limit
Budget Limit
Resource Limit
Lifetime Limit
```

---

# 49. AGENT COMMUNICATION

消息：

```text
message_id
sender
receiver
type
payload
correlation_id
timestamp
priority
security_context
```

消息类型：

```text
REQUEST
RESPONSE
DELEGATION
STATUS
EVENT
APPROVAL
RESULT
ERROR
```

---

# 50. MULTI-AGENT ORCHESTRATION

支持：

```text
Sequential
Parallel
Hierarchical
Supervisor
Peer-to-Peer
Swarm
Dynamic Team
```

---

# 51. REPLANNING

触发条件：

```text
Task Failure
Tool Failure
Policy Change
Resource Shortage
World State Change
New Evidence
Deadline Change
Budget Change
```

流程：

```text
Observe
 ↓
Detect
 ↓
Re-evaluate
 ↓
Replan
 ↓
Authorize
 ↓
Continue
```

---

# 52. FAILURE RECOVERY

失败类型：

```text
Model Failure
Tool Failure
Network Failure
Agent Failure
Policy Failure
Permission Failure
Resource Failure
Memory Failure
Verification Failure
External System Failure
```

处理：

```text
Detect
 ↓
Classify
 ↓
Retry
 ↓
Fallback
 ↓
Replan
 ↓
Compensate
 ↓
Escalate
```

---

# 53. SAGA / COMPENSATION

涉及副作用的任务必须支持：

```text
Action A
 ↓
Action B
 ↓
Action C
```

如果 C 失败：

```text
Compensation C
 ↓
Compensation B
 ↓
Recovery
```

---

# 54. CHECKPOINTING

Execution Checkpoint：

```text
ExecutionID
StepID
State
ContextRef
MemoryRef
ResourceState
ToolState
Timestamp
```

支持：

```text
Pause
Resume
Recovery
Migration
Retry
Rollback
```

---

# 55. FRIDAY / REALTIME INTELLIGENCE

能力：

```text
Realtime Monitoring
Agent Monitoring
Task Monitoring
System Monitoring
Event Detection
Alerting
Notification
Progress Reporting
Failure Detection
Incident Detection
Realtime Context Update
Rapid Response
```

---

# 56. EVENT ENGINE

事件：

```text
AgentCreated
AgentStarted
AgentPaused
AgentResumed
AgentFailed
AgentCompleted
AgentTerminated

TaskCreated
TaskStarted
TaskProgress
TaskCompleted
TaskFailed

ToolCalled
ToolCompleted

ApprovalRequested
ApprovalGranted
ApprovalRejected

PolicyDenied

MemoryWritten
MemoryRetrieved

BudgetWarning
BudgetExceeded

VerificationCompleted
```

---

# 57. EVENT BUS

推荐：

```text
Redis Streams
```

或：

```text
Redpanda / Kafka
```

用于：

```text
Agent Events
Task Events
Execution Events
Memory Events
Organization Events
Network Events
Audit Events
Telemetry
```

---

# 58. ENOCH / LONG-HORIZON ENGINE

能力：

```text
Long-running Tasks
Persistent Agents
Scheduled Missions
Continuous Monitoring
Event-driven Execution
Long-term Memory
Historical Analysis
Long-horizon Planning
Periodic Re-evaluation
Mission Continuity
State Persistence
Background Intelligence
Strategic Monitoring
```

---

# 59. LONG-RUNNING MISSION

```text
Mission
 ↓
Persistent Agent
 ↓
Checkpoint
 ↓
Scheduler
 ↓
Observe
 ↓
Event Detection
 ↓
Replan
 ↓
Execute
 ↓
Verify
 ↓
Memory
 ↓
Continue
```

不得依赖单次 HTTP Request 生命周期。

---

# 60. SCHEDULER

Scheduler 输入：

```text
Task Priority
Deadline
Agent Capability
Model
Tool
Resource
Risk
Budget
Availability
Trust
```

Scheduler 输出：

```text
Who
When
Where
With Which Model
With Which Tool
Using Which Resource
```

---

# 61. JOCaSTA / ORGANIZATION ENGINE

Organization 是一等对象：

```text
Organization
Goals
Members
Roles
Departments
Teams
Policies
Budget
Memory
Capabilities
KPI
```

---

# 62. ORGANIZATION FUNCTIONS

支持：

```text
Create
Hire
Assign
Delegate
Evaluate
Promote
Suspend
Terminate
Budget
Report
Reorganize
```

---

# 63. AI ORGANIZATION MODEL

```text
Organization
│
├── Executive Agent
│
├── Research Department
│   ├── Research Agent
│   └── Data Agent
│
├── Engineering Department
│   ├── Backend Agent
│   ├── Frontend Agent
│   ├── DevOps Agent
│   └── QA Agent
│
├── Security
│
├── Finance
│
└── Operations
```

这些必须是真实 Agent Runtime Entity。

---

# 64. KPI

Organization KPI：

```text
Task Completion
Quality
Reliability
Cost
Latency
Human Intervention
Budget
Security Incidents
Recovery
```

Agent KPI：

```text
Success Rate
Verification Rate
Reliability
Cost per Task
Tool Accuracy
Recovery
```

---

# 65. ZOON / SPECIALIZED INTELLIGENCE

ZOON 不实现为固定单 Agent。

实现：

# Specialized Agent Framework

---

# 66. SPECIALIZED AGENT FACTORY

```text
Domain
 ↓
Agent Template
 ↓
Skills
 ↓
Knowledge
 ↓
Tools
 ↓
Memory
 ↓
Policies
 ↓
Evaluation Suite
 ↓
Agent
```

---

# 67. SPECIALIZED DOMAINS

至少支持模板：

```text
Research
Coding
Data
Security
Finance
Marketing
DevOps
Operations
Monitoring
Science
Legal Research
Product
QA
```

模板可扩展。

---

# 68. NETWORK KERNEL

网络能力：

```text
Discovery
Identity
Authentication
Authorization
Routing
Messaging
Delegation
Federation
Capability Discovery
Service Discovery
```

---

# 69. PROTOCOL ADAPTERS

支持：

```text
A2A
MCP
HTTP
WebSocket
gRPC
Event Bus
```

External Agent 不能绕过：

```text
Identity
Trust
Capability
Policy
Audit
```

---

# 70. TRUST ENGINE

Trust 输入：

```text
Task Success
Verification
Reliability
Security
Recovery
Historical Performance
Task Difficulty
```

Trust 用途：

```text
Ranking
Routing
Agent Selection
Delegation Recommendation
```

Trust 不得自动产生无限权限。

---

# 71. SECURITY ENGINE

Threat Model：

```text
Prompt Injection
Tool Abuse
Credential Theft
Privilege Escalation
Data Exfiltration
Malicious Agent
Unauthorized Delegation
Supply Chain Risk
Memory Leakage
Policy Bypass
Network Abuse
```

---

# 72. SECURITY CHAIN

```text
Identity
 ↓
Authentication
 ↓
Authorization
 ↓
Capability
 ↓
Policy
 ↓
Approval
 ↓
Sandbox
 ↓
Execution
 ↓
Verification
 ↓
Audit
```

---

# 73. SANDBOX

不可信或高风险执行：

```text
Code
Shell
Browser Automation
Plugin
Untrusted Agent
Data Processing
```

必须支持隔离。

限制：

```text
CPU
Memory
Filesystem
Network
Process
Time
```

---

# 74. RESOURCE KERNEL

资源类型：

```text
CPU
Memory
Storage
Network
Tokens
Time
Money
Tool Quota
Agent Slots
```

资源状态：

```text
Available
Reserved
Consumed
Released
Exceeded
```

---

# 75. BUDGET ENGINE

每个：

```text
Human
Organization
Agent
Task
Tool
Mission
```

可以拥有 Budget。

支持：

```text
Limit
Reserve
Consume
Release
Warning
Block
```

---

# 76. ECONOMY ENGINE

第一阶段：

```text
Budget
Quota
Usage
Cost
Internal Billing
```

第二阶段：

```text
Pricing
Marketplace
Contracts
Agent Services
```

第三阶段：

```text
Agent-to-Agent Transactions
External Agent Economy
```

任何经济能力必须可关闭。

# 77. WORLD SIMULATION

Digital Twin 第一阶段只实现：

```text
Entity
State
Relationship
Event
Scenario
```

模拟：

```text
Current State
+
Proposed Action
+
Constraints
```

输出：

```text
Expected Cost
Expected Risk
Dependencies
Scenario Outcome
```

不得宣称完美预测未来。

---

# 78. VERIFICATION ENGINE

统一：

```text
Generate
 ↓
Execute
 ↓
Verify
 ↓
Accept / Reject
```

验证：

```text
Schema
Source
State
Policy
Tests
Human
```

结果：

```text
VERIFIED
PARTIALLY_VERIFIED
FAILED
UNKNOWN
```

---

# 79. EXPERIENCE ENGINE

```text
Task
 ↓
Outcome
 ↓
Evaluation
 ↓
Experience Extraction
 ↓
Memory
```

Experience：

```text
ExperienceID
TaskID
Outcome
Evidence
Verification
Confidence
Lessons
Source
```

---

# 80. EVOLUTION ENGINE

```text
Observe
 ↓
Measure
 ↓
Detect Bottleneck
 ↓
Generate Improvement Proposal
 ↓
Experiment
 ↓
Benchmark
 ↓
Approve
 ↓
Deploy
 ↓
Monitor
```

禁止：

```text
Production Self-modification Without Evaluation
```

---

# 81. GOVERNANCE ENGINE

治理：

```text
Policy
Risk
Approval
Audit
Accountability
Revocation
Compliance
Change Management
```

治理对象：

```text
Agent
Organization
Capability
Tool
Memory
Network
Contract
Policy
```

---

# 82. EMERGENCY CONTROL

必须支持：

```text
Pause Agent
Stop Agent
Cancel Task
Revoke Capability
Revoke Credential
Disable Tool
Disable Network
Freeze Organization
Global Emergency Stop
```

---

# 83. WORLD SAFETY

物理设备能力必须拥有更严格的策略：

```text
PhysicalRiskLevel
SafetyPolicy
EmergencyStop
Approval
Audit
```

连接设备：

> 不等于拥有设备无限控制权。

---

# 84. API STRUCTURE

基础：

```text
/api/v1/health
/api/v1/ready
```

Identity：

```text
/api/v1/users
/api/v1/principals
/api/v1/organizations
/api/v1/workspaces
```

Agents：

```text
/api/v1/agents
/api/v1/agents/{id}
/api/v1/agents/{id}/start
/api/v1/agents/{id}/pause
/api/v1/agents/{id}/resume
/api/v1/agents/{id}/stop
/api/v1/agents/{id}/spawn
```

Tasks：

```text
/api/v1/goals
/api/v1/tasks
/api/v1/tasks/{id}
/api/v1/plans
/api/v1/executions
```

Intelligence：

```text
/api/v1/memory
/api/v1/context
/api/v1/models
/api/v1/perception
/api/v1/analysis
```

Tools：

```text
/api/v1/capabilities
/api/v1/tools
/api/v1/tool-runs
```

Governance：

```text
/api/v1/policies
/api/v1/approvals
/api/v1/audit
```

Organization：

```text
/api/v1/organizations
/api/v1/departments
/api/v1/teams
/api/v1/roles
```

Network：

```text
/api/v1/network
/api/v1/external-agents
```

World：

```text
/api/v1/world
/api/v1/scenarios
/api/v1/simulation
```

Economy：

```text
/api/v1/budgets
/api/v1/usage
/api/v1/billing
/api/v1/contracts
/api/v1/marketplace
```

Evaluation：

```text
/api/v1/evaluations
/api/v1/benchmarks
```

L-Core：

```text
/api/v1/lcore/intent
/api/v1/lcore/stream
```

---

# 85. DATABASE MASTER SCHEMA

PostgreSQL：

```text
users
organizations
workspaces
principals

agents
agent_versions
agent_relationships
agent_states

roles
permissions
capabilities
role_permissions

credentials
delegations

goals
tasks
task_dependencies
actions
executions
execution_steps

plans
plan_steps
checkpoints

memories
memory_permissions
memory_provenance
memory_versions

tools
tool_versions
tool_permissions
tool_runs

policies
policy_rules
policy_versions
approval_requests

events
audit_logs

budgets
budget_allocations
usage_records
billing_records

evaluations
evaluation_runs
benchmark_results

reputation_records

departments
teams
organization_members
kpis

contracts
marketplace_items

world_entities
world_relationships
world_states
world_events
scenarios

agent_messages
network_peers
external_agents
```

---

# 86. REDIS DATA PLAN

Redis：

```text
sessions
cache
locks
rate_limits
heartbeats
task_queue
agent_queue
approval_state
temporary_context
runtime_state
event_streams
```

---

# 87. VECTOR DATABASE

Qdrant：

```text
semantic_memory
knowledge
experience
document_chunks
context_retrieval
```

但任何 Vector Search 必须执行：

```text
Owner Filter
Scope Filter
Permission Filter
Sensitivity Filter
Policy Filter
```

---

# 88. OBJECT STORAGE

用于：

```text
Documents
Images
Videos
Audio
Reports
Datasets
Artifacts
Agent Files
Execution Outputs
```

---

# 89. MODEL GATEWAY

统一模型请求：

```text
ModelRequest
ModelResponse
Streaming
ToolCalling
StructuredOutput
TokenUsage
Latency
Cost
Error
```

---

# 90. MODEL ROUTER

候选模型评分：

```text
Capability
Quality
Latency
Cost
Context
Availability
Risk
```

Agent 不得硬编码绑定单一模型。

---

# 91. MODEL REGISTRY

记录：

```text
ModelID
Provider
Version
Capabilities
ContextWindow
Cost
Latency
Availability
RiskClass
Status
```

生命周期：

```text
REGISTER
VALIDATE
ACTIVE
DEPRECATED
DISABLED
```

---

# 92. OBSERVABILITY

OpenTelemetry 必须贯穿：

```text
API
Agent
Task
Plan
Execution
Tool
Model
Memory
Policy
Network
World
Verification
```

Correlation IDs：

```text
request_id
trace_id
span_id
agent_id
task_id
execution_id
tool_id
model_id
organization_id
```

---

# 93. CRITICAL ACTION AUDIT

Audit 必须回答：

```text
WHO?
WHAT?
WHY?
WHICH AGENT?
WHICH MODEL?
WHICH TOOL?
WHICH CAPABILITY?
WHICH POLICY?
WHO APPROVED?
HOW MUCH COST?
WHAT RESULT?
WAS IT VERIFIED?
```

---

# 94. L-CORE FRONTEND

前端不是传统 Admin Dashboard。

核心体验：

```text
L-Core Island
Command Center
Agent Graph
Task Timeline
Execution Console
Approval Center
Memory Explorer
Organization View
World View
Network View
Evaluation Center
```

---

# 95. L-CORE STATES

```text
IDLE
LISTENING
THINKING
PLANNING
EXECUTING
MULTI_AGENT
WAITING_APPROVAL
VERIFYING
COMPLETED
ERROR
```

这些状态必须来自真实 Runtime Event。

禁止用假动画代表真实执行。

---

# 96. WEBSOCKET / SSE EVENTS

```text
agent.started
agent.progress
agent.paused
agent.resumed
agent.failed
agent.completed

task.created
task.started
task.progress
task.completed
task.failed

tool.called
tool.completed

approval.required
approval.granted
approval.rejected

policy.denied

memory.written
memory.retrieved

verification.completed

budget.warning
budget.exceeded
```

---

# 97. PLUGIN SYSTEM

Manifest：

```text
PluginID
Version
Capabilities
Permissions
Dependencies
Sandbox
Health
Lifecycle
Audit
```

Plugin 不允许：

```text
direct kernel access
direct database superuser access
bypass policy
bypass audit
```

只允许：

```text
Capability API
```

---

# 98. AGENT FILESYSTEM

每个 Agent 可以拥有逻辑资源：

```text
/identity
/memory
/tasks
/knowledge
/skills
/tools
/relationships
/budget
/policies
/state
/history
```

这是逻辑资源抽象。

不能默认给 Agent 宿主机真实 root filesystem。

---

# 99. AGENT SOVEREIGNTY

每个 Agent 拥有自己的：

```text
Identity
State
Memory
Capabilities
Permissions
Budget
Resources
History
```

但 Agent 永远受：

```text
Owner
Organization
Policy
Governance
```

约束。

---

# 100. EXTERNAL AGENT INTEROPERABILITY

外部 Agent：

```text
External Agent
 ↓
Adapter
 ↓
Identity
 ↓
Trust
 ↓
Capability
 ↓
Policy
 ↓
Sandbox
 ↓
Execution
 ↓
Verification
 ↓
Audit
```

禁止：

```text
External Agent → Direct Kernel Access
External Agent → Privilege Escalation
External Agent → Hidden Tool Use
```

---

# 101. CAPABILITY REGISTRY

建立正式：

# Capability Registry

字段：

```text
CapabilityID
Source
Name
Description
ParentCapability
EngineeringModule
Interface
API
Permission
Policy
Risk
Realizability
ImplementationStatus
TestSuite
Benchmark
Owner
Version
```

例如：

```text
LHX-U-001
Source: ULTRON
Capability: Autonomous Task Execution
Module: AgentRuntime
Realizability: R1
Status: Implemented
Test: AutonomousTaskE2E
```

---

# 102. CAPABILITY ID SYSTEM

建议：

```text
LHX-U-xxx   ULTRON
LHX-V-xxx   VISION
LHX-A-xxx   ADA
LHX-E-xxx   EDITH
LHX-F-xxx   FRIDAY
LHX-J-xxx   JARVIS
LHX-JC-xxx  JOCaSTA
LHX-K-xxx   KAREN
LHX-N-xxx   ENOCH
LHX-Z-xxx   ZOON
```

去重后的统一能力：

```text
LHX-C-xxx
```

---

# 103. CAPABILITY STATUS

必须允许：

```text
PLANNED
IN_PROGRESS
IMPLEMENTED
PARTIALLY_IMPLEMENTED
EXPERIMENTAL
RESEARCH
NOT_REALIZABLE
DEPRECATED
```

Never：

```text
PLANNED → pretend IMPLEMENTED
```

---

# 104. CAPABILITY COVERAGE

Release 前：

```text
Source Coverage = 100%
Atomic Capability Mapping = 100%
Critical Capability Policy Coverage = 100%
Critical Audit Coverage = 100%
Critical Test Coverage = 100%
```

注意：

这里的 100% 是：

> 所有“已定义能力”都有明确状态、归属和验证路径。

不是宣称所有能力都已经完成开发。

---

# 105. TEST PYRAMID

必须：

```text
Unit Tests
Integration Tests
Contract Tests
E2E Tests
Security Tests
Permission Tests
Agent Tests
Memory Tests
Tool Tests
Policy Tests
Network Tests
Organization Tests
Performance Tests
Load Tests
Stress Tests
Chaos Tests
Benchmark Tests
Regression Tests
```

---

# 106. SECURITY TESTS

至少：

```text
Prompt Injection
Tool Abuse
Credential Leakage
Privilege Escalation
Memory Leakage
Cross Organization Access
Cross Workspace Access
Unauthorized Delegation
Budget Bypass
Policy Bypass
Spawn Abuse
Network Abuse
Malicious Plugin
External Agent Attack
```

---

# 107. AGENT TESTING

Agent 测试：

```text
Identity
Lifecycle
Planning
Tool Selection
Capability Use
Permission
Policy
Execution
Recovery
Memory
Cost
Verification
```

---

# 108. ORGANIZATION TESTING

```text
Create Organization
Create Department
Create Team
Assign Agent
Delegate
Evaluate
Budget
Suspend
Terminate
Reorganize
```

---

# 109. LONG-RUNNING TESTS

必须进行：

```text
1h
6h
24h
long-duration scenario
```

测试：

```text
Checkpoint
Memory Persistence
Scheduler Recovery
Network Failure
Tool Failure
Model Failure
Restart
Resume
```

---

# 110. PERFORMANCE

至少测量：

```text
Agent Creation Latency
Task Scheduling Latency
Tool Invocation Latency
Memory Retrieval Latency
Model Routing Latency
Event Throughput
Concurrent Agents
Concurrent Tasks
API Throughput
```

---

# 111. COST ENGINEERING

每次 AI Execution 追踪：

```text
Input Tokens
Output Tokens
Model Cost
Tool Cost
Compute Cost
Storage Cost
Network Cost
Total Cost
```

最终：

```text
Cost Per Verified Task
```

---

# 112. L10K

核心指标：

```text
Verified Human Leverage

VHL =
Verified Value Output
/
Human Active Minutes
```

比较：

```text
Human Only
Copilot
Single Agent
Multi-Agent
LIUHAO X
```

目标：

```text
VHL_Liuhao
----------------
VHL_Baseline

≥ 10,000
```

只有满足质量、安全、可靠性、授权、成本等门槛时才有意义。

---

# 113. L10K TASK CLASSES

```text
T1 Simple
T2 Standard
T3 Complex
T4 Long Horizon
T5 Organization
```

Task 必须可重复测试。

---

# 114. PRIMARY METRICS

```text
Task Success Rate
Verified Work Rate
Human Active Time
Human Intervention Rate
Autonomous Completion Rate
Parallel Throughput
Agent Utilization
Memory Recall Accuracy
Plan Success Rate
Verification Pass Rate
Cost per Verified Task
Critical Failure Rate
Unauthorized Action Rate
Recovery Rate
```

---

# 115. RELIABILITY GATES

必须监控：

```text
Critical Task Success
Unauthorized Critical Action
Data Loss
Policy Enforcement
Audit Coverage
Critical Trace Coverage
Recovery
```

关键目标：

```text
Unauthorized Critical Action = 0
Data Loss = 0
```

其他阈值必须通过实际 Benchmark 校准。

---

# 116. ANTI-GAMING

严禁通过：

```text
More Agents
More Tokens
More Tasks
Easier Tasks
Repeated Tasks
Lower Quality
Ignoring Human Time
Ignoring Security
Ignoring Failures
```

提高 L10K 指标。

Benchmark 必须：

```text
Fixed
Weighted
Held-out
Reproducible
Auditable
```

---

# 117. ENGINEERING ACCEPTANCE RULE

每个功能必须能够回答：

```text
Domain
State
Identity
Permission
Capability
Runtime
Event
Failure Mode
Security Model
Test
Metric
Rollback
```

任何一个没有：

> 功能不算完成。

---

# 118. RELEASE GATES

Release 前必须：

```text
Format
Lint
Type Check
Unit Tests
Integration Tests
Security Tests
E2E Tests
Performance Tests
Agent Evaluation
Benchmark
Regression
```

Critical Failure：

> Block Release。

---

# 119. CI PIPELINE

推荐：

```text
Commit
 ↓
Lint
 ↓
Type Check
 ↓
Unit
 ↓
Integration
 ↓
Security
 ↓
E2E
 ↓
Performance
 ↓
Agent Benchmark
 ↓
Regression
 ↓
Build
 ↓
Deploy
```

---

# 120. DEV ENVIRONMENT

必须支持：

```text
docker compose up
```

启动：

```text
PostgreSQL
Redis
Qdrant
Redpanda
MinIO
Vault
OTel Collector
Prometheus
Grafana
Loki
Tempo
API
Worker
Scheduler
Runtime
Web
```

---

# 121. HEALTH

统一：

```text
/health
/ready
```

Health 检查：

```text
Database
Redis
Vector DB
Event Bus
Object Storage
Vault
Model Gateway
```

---

# 122. MIGRATIONS

所有数据库结构必须使用：

```text
Alembic
```

禁止：

```text
Manual undocumented schema changes
```

Migration 必须：

```text
Versioned
Reversible where practical
Tested
Documented
```

---

# 123. CONFIGURATION

统一使用：

```text
Environment
Pydantic Settings
Secret Manager
Vault
```

禁止在代码里硬编码：

```text
API Keys
Passwords
Private Keys
Production Credentials
```

---

# 124. SECRET MANAGEMENT

Secret：

```text
Store
Read
Rotate
Expire
Revoke
Audit
```

通过：

```text
Vault
```

---

# 125. RATE LIMITING

至少支持：

```text
User Rate Limit
Agent Rate Limit
Organization Rate Limit
Tool Rate Limit
Provider Rate Limit
Network Rate Limit
Spawn Rate Limit
```

---

# 126. LOCKING

需要分布式锁的位置：

```text
Agent Scheduling
Task Claiming
Leader Election
Plugin Activation
Critical Resource Allocation
Organization Reconfiguration
```

Redis / etcd 均可作为实现方案，但整个系统必须统一抽象。

---

# 127. LEADER ELECTION

支持：

```text
Scheduler Leader
Worker Leader
Organization Coordinator
Mission Coordinator
```

避免重复执行。

---

# 128. IDEMPOTENCY

必须确保：

```text
Payment
Email
Deployment
Database Mutation
Agent Spawn
Task Submission
External API Calls
```

不会因为 Retry 造成意外重复副作用。

使用：

```text
Idempotency Key
Execution ID
Action ID
```

---

# 129. RETRY POLICY

Retry 必须区分：

```text
Transient
Permanent
Unknown
```

采用：

```text
Exponential Backoff
Jitter
Max Attempts
Circuit Breaker
```

---

# 130. CIRCUIT BREAKER

外部 Provider：

```text
Model Provider
Tool Provider
API
Cloud
Database
Network
```

支持：

```text
Closed
Open
Half-Open
```

---

# 131. BULKHEAD

资源隔离：

```text
Organization
Agent
Task
Provider
Tool
```

避免一个失控任务拖垮整个系统。

---

# 132. AGENT INTERRUPTION

人可以随时：

```text
Pause
Stop
Cancel
Override
Change Goal
Change Budget
Change Permission
```

Agent 必须响应 Runtime Control。

---

# 133. HUMAN SOVEREIGNTY

最高层：

```text
Human Owner
 ↓
Organization
 ↓
Agent
 ↓
Sub-Agent
 ↓
Capability
 ↓
Tool
 ↓
Action
```

系统不能因为：

```text
Trust
Performance
Historical Success
```

自动获得超越 Owner 授权的权限。

---

# 134. MEMORY SECURITY

任何 Memory Retrieval 必须：

```text
Resolve Identity
 ↓
Resolve Scope
 ↓
Check Ownership
 ↓
Check Permission
 ↓
Check Sensitivity
 ↓
Apply Policy
 ↓
Retrieve
 ↓
Audit
```

---

# 135. WORLD ACTION SECURITY

任何 World Action：

```text
Resolve Identity
 ↓
Capability
 ↓
Policy
 ↓
Approval
 ↓
Sandbox
 ↓
Execute
 ↓
Verify
 ↓
Audit
```

---

# 136. AGENT SPAWN SECURITY

任何 Spawn：

```text
Parent Identity
 ↓
Parent Authorization
 ↓
Quota
 ↓
Budget
 ↓
Resource
 ↓
Capability
 ↓
Policy
 ↓
Sandbox
 ↓
Create
 ↓
Audit
```

---

# 137. THE UNIFIED INTELLIGENCE LOOP

LIUHAO X 最终核心循环：

```text
PERCEIVE
 ↓
UNDERSTAND
 ↓
RETRIEVE
 ↓
REASON
 ↓
PLAN
 ↓
AUTHORIZE
 ↓
CREATE / DELEGATE
 ↓
ORGANIZE
 ↓
EXECUTE
 ↓
OBSERVE
 ↓
VERIFY
 ↓
STORE EXPERIENCE
 ↓
EVALUATE
 ↓
IMPROVE
```

---

# 138. THE WORLD LOOP

```text
World
 ↓
Perception
 ↓
Observation
 ↓
World Model
 ↓
Reasoning
 ↓
Planning
 ↓
Policy
 ↓
Action
 ↓
World
 ↓
Observation
 ↓
Verification
```

---

# 139. THE HUMAN LOOP

```text
Human
 ↓
Intent
 ↓
L-Core
 ↓
Goal
 ↓
Plan
 ↓
Authorization
 ↓
Execution
 ↓
Verification
 ↓
Result
 ↓
Human
```

---

# 140. THE ORGANIZATION LOOP

```text
Goal
 ↓
Organization
 ↓
Roles
 ↓
Agents
 ↓
Tasks
 ↓
Execution
 ↓
KPI
 ↓
Evaluation
 ↓
Optimization
```

---

# 141. THE LONG-HORIZON LOOP

```text
Mission
 ↓
Persistent Agent
 ↓
Checkpoint
 ↓
Observe
 ↓
Event
 ↓
Replan
 ↓
Execute
 ↓
Verify
 ↓
Memory
 ↓
Continue
```

---

# 142. THE EVOLUTION LOOP

```text
Experience
 ↓
Evaluation
 ↓
Bottleneck
 ↓
Proposal
 ↓
Experiment
 ↓
Benchmark
 ↓
Approval
 ↓
Deploy
 ↓
Monitor
```

---

# 143. THE FINAL TEN-SOURCE MAPPING

```text
ULTRON
→ AgentRuntime
→ Scheduler
→ AgentFactory
→ Spawning
→ Parallelism
→ Recovery
→ MissionEngine

VISION
→ Perception
→ Observation
→ WorldModel
→ ChangeDetection
→ MultimodalUnderstanding

ADA
→ DataEngine
→ Python
→ SQL
→ Statistics
→ Mathematics
→ Simulation
→ Optimization

EDITH
→ WorldInterface
→ Browser
→ Computer
→ Filesystem
→ Shell
→ Git
→ Cloud
→ Database
→ Devices

FRIDAY
→ RealtimeEngine
→ EventBus
→ Monitoring
→ Alerts
→ Notifications
→ IncidentDetection

JARVIS
→ LCore
→ Intent
→ Context
→ Goal
→ Planning
→ Delegation
→ Coordination
→ DecisionSupport

JOCaSTA
→ OrganizationEngine
→ Roles
→ Teams
→ Departments
→ KPI
→ AgentManagement
→ Governance

KAREN
→ PersonalContext
→ PersonalMemory
→ Preferences
→ Personalization
→ UserAssistance

ENOCH
→ LongRunningRuntime
→ PersistentState
→ Missions
→ HistoricalIntelligence
→ StrategicMonitoring

ZOON
→ SpecializedAgentFramework
→ DomainTemplates
→ DomainSkills
→ DomainKnowledge
→ SpecializedTools
→ SpecializedEvaluation
```

---

# 144. DEDUPLICATED CORE

十源能力最终统一为：

```text
Identity
Context
Perception
Memory
Reasoning
Computation
Planning
Goal
Task
Agent
Runtime
Capability
Tool
Policy
Permission
Approval
Execution
Scheduling
Orchestration
Communication
Organization
World Model
World Interface
Realtime
Network
Trust
Security
Resource
Economy
Governance
Verification
Experience
Evolution
Observability
Human Interface
```

---

# 145. FINAL PRODUCT STACK

```text
L-CORE
     ↓
INTELLIGENCE FABRIC
     ↓
LIUHAO KERNEL
     ↓
AGENT RUNTIME
     ↓
AGENT FACTORY
     ↓
SPECIALIZED AGENTS
     ↓
MULTI-AGENT ORCHESTRATION
     ↓
AI ORGANIZATION
     ↓
AGENT NETWORK
     ↓
WORLD INTERFACE
     ↓
REAL DIGITAL / PHYSICAL SYSTEMS
```

---

# 146. FINAL UX

用户不需要知道：

```text
哪个 Agent
哪个 Tool
哪个 Model
哪个 Worker
哪个 Service
```

用户表达：

```text
Goal
```

L-Core 决定：

```text
Context
Plan
Agents
Capabilities
Tools
Policies
Resources
Execution
Verification
```

但用户始终拥有：

```text
Visibility
Approval
Interruption
Override
Revocation
```

---

# 147. EXAMPLE: “建立一个 AI 公司”

用户：

```text
建立一个 AI 公司，负责产品、研发、市场和运营。
```

系统：

```text
L-Core
 ↓
Goal
 ↓
Organization Creation
 ↓
Org Policy
 ↓
Create Departments
 ↓
Create / Select Agents
 ↓
Assign Roles
 ↓
Allocate Budget
 ↓
Provision Tools
 ↓
Create Memory Scopes
 ↓
Activate Teams
 ↓
Execute
 ↓
KPI
 ↓
Report
```

不是简单生成几段文字。

必须真正产生：

```text
Organization
Agents
Roles
Tasks
Budgets
Capabilities
Policies
Memory
Events
Audit
```

---

# 148. EXAMPLE: “研究 100 家公司”

```text
Human
 ↓
L-Core
 ↓
Goal
 ↓
Research Manager Agent
 ↓
Spawn Researchers
 ↓
Quota / Budget / Policy
 ↓
Parallel Research
 ↓
Browser / HTTP
 ↓
Evidence
 ↓
Memory
 ↓
Data Agent
 ↓
Analysis
 ↓
QA Agent
 ↓
Verification
 ↓
Report
 ↓
L-Core
```

---

# 149. EXAMPLE: “把项目上线”

```text
Human
 ↓
L-Core
 ↓
Planning
 ↓
Engineering Agent
 ↓
QA Agent
 ↓
Security Agent
 ↓
DevOps Agent
 ↓
Git
 ↓
Tests
 ↓
Build
 ↓
Deploy
 ↓
Verification
 ↓
Monitor
 ↓
Report
```

Production Deployment 必须有适当 Policy / Approval。

---

# 150. EXAMPLE: “未来 90 天监控市场”

```text
Mission
 ↓
ENOCH Runtime
 ↓
Persistent Agent
 ↓
Scheduler
 ↓
Browser / Data Sources
 ↓
Event Detection
 ↓
Analysis
 ↓
World Model Update
 ↓
Replan
 ↓
Notification
 ↓
Memory
 ↓
90-day report
```

---

# 151. EXAMPLE: “发现系统异常”

```text
FRIDAY Event Engine
 ↓
Detect
 ↓
Context
 ↓
World State
 ↓
Security Agent
 ↓
Analysis
 ↓
Policy
 ↓
Mitigation Plan
 ↓
Approval if required
 ↓
Action
 ↓
Verify
 ↓
Audit
```

# 152. REALITY BOUNDARY

LIUHAO X 可以真实实现：

```text
AI Employee OS
Agent OS
Multi-Agent Runtime
Agent Factory
Agent Organization
Realtime Intelligence
Long-running Agents
Multimodal Perception
Computational Agents
World Interface
Agent Network
Agent Governance
Agent Economy
Agent Evaluation
```

不能把下列内容作为现成事实：

```text
AGI
Consciousness
Sentience
Omniscience
Infinite Intelligence
Unlimited Replication
Unlimited Autonomy
Perfect Future Prediction
Universal Control
Superintelligence
```

---

# 153. RESEARCH BOUNDARY

对于研究型能力：

```text
R3
```

必须进入：

```text
Experimental
```

而不是：

```text
Production
```

实验性能力必须有：

```text
Feature Flag
Isolation
Evaluation
Rollback
Audit
```

---

# 154. FINAL CAPABILITY AUDIT

每次 Release 前执行：

```text
Enumerate Sources
 ↓
Enumerate Atomic Capabilities
 ↓
Check Capability Registry
 ↓
Check Implementation Status
 ↓
Check Module
 ↓
Check API
 ↓
Check Permission
 ↓
Check Policy
 ↓
Check Test
 ↓
Check Metric
 ↓
Check Security
 ↓
Check Documentation
```

发现缺失：

> Release Block。

---

# 155. FINAL ENGINEERING AUDIT

必须验证：

```text
Every Agent has Identity
Every Tool has Capability
Every Action has Policy
Every Critical Action has Audit
Every Memory has Owner
Every External Agent has Trust Boundary
Every Spawn has Quota
Every Resource has Limit
Every Production Evolution has Evaluation
Every Critical Result has Verification
```

---

# 156. FINAL DATABASE / API / RUNTIME CONSISTENCY

禁止：

```text
API 定义了 Agent
但 Runtime 没有 Agent。

DB 定义了 Capability
但 Policy 不认识 Capability。

UI 显示 Task Running
但真实 Runtime 没有 Task。

Audit 记录 Tool Call
但真实 Tool 没有产生 Event。
```

所有层必须一致：

```text
UI
↕
API
↕
Service
↕
Domain
↕
Runtime
↕
DB / Event Bus
```

---

# 157. NO FAKE FRONTEND

前端必须从：

```text
API
WebSocket
SSE
Event Store
```

读取真实状态。

不得用：

```text
setTimeout
fake progress
fake agent count
fake execution
fake success
```

冒充生产系统。

---

# 158. NO FAKE AI

如果某个能力尚未真正实现：

```text
Return NOT_IMPLEMENTED
```

或者：

```text
Feature status = PLANNED / EXPERIMENTAL
```

不要生成看起来像成功的假答案。

---

# 159. NO FAKE TOOL

Tool 必须：

```text
Registered
Validated
Authorized
Executable
Observable
Auditable
```

不能只存在一个 Tool JSON Schema 就声称 Tool 已实现。

---

# 160. NO FAKE AGENT

Agent 必须真实拥有：

```text
Identity
Runtime State
Memory
Capabilities
Permissions
Budget
Resource
Lifecycle
Execution History
```

---

# 161. NO FAKE ORGANIZATION

Organization 必须真实拥有：

```text
Members
Roles
Departments
Teams
Goals
Policies
Budget
Memory
KPIs
Audit
```

---

# 162. CODE QUALITY RULES

必须：

```text
Type Safe
Async Safe
Idempotent Where Required
Structured Logging
Explicit Error Types
Domain Validation
Transactional Boundaries
Retry Boundaries
Timeouts
Circuit Breakers
Resource Limits
```

---

# 163. ERROR MODEL

统一错误分类：

```text
ValidationError
AuthenticationError
AuthorizationError
CapabilityError
PolicyDeniedError
ApprovalRequiredError
ResourceLimitError
BudgetExceededError
ToolError
ModelError
NetworkError
ExecutionError
VerificationError
RecoveryError
```

---

# 164. OBSERVABLE EXECUTION

每一次 Execution 至少记录：

```text
ExecutionID
Actor
Agent
Task
Plan
Model
Tool
Capability
Policy
Approval
Resources
Cost
Latency
Result
Verification
Audit
```

---

# 165. VERSIONING

需要独立版本：

```text
Architecture Version
Capability Registry Version
Agent Version
Agent Runtime Version
Model Version
Tool Version
Policy Version
Memory Schema Version
Benchmark Version
```

---

# 166. AGENT VERSIONING

Agent 每次重要修改生成：

```text
agent_version
```

记录：

```text
Instructions
Capabilities
Tools
Policy
Memory Policy
Model Policy
Evaluation Results
Created By
Created At
```

---

# 167. POLICY VERSIONING

Policy 必须：

```text
Versioned
Auditable
Testable
Rollbackable
```

Critical Policy 修改必须进入 Governance。

---

# 168. MEMORY VERSIONING

支持：

```text
Memory Version
Conflict Resolution
Merge
Archive
Expiry
Deletion
Restore
```

---

# 169. DATA RETENTION

Memory、Audit、Artifacts 支持：

```text
TTL
Retention Policy
Archive
Delete
Export
Redaction
```

---

# 170. PRIVACY BOUNDARY

Cross:

```text
User
Workspace
Organization
Agent
Network
```

时必须检查权限。

不得仅因为“相关”就跨边界检索 Memory。

---

# 171. AGENT REPUTATION

Reputation 不等于 Permission。

只作为：

```text
Ranking
Selection
Routing
Risk Assessment
```

参考指标：

```text
Success
Reliability
Verification
Security
Recovery
Task Difficulty
```

---

# 172. AGENT CONTRACT

组织中的 Agent 可以拥有：

```text
Role
Responsibilities
KPIs
Budget
Capability Set
Permission Set
Memory Scope
Reporting Line
Supervisor
```

---

# 173. AGENT ORGANIZATION GRAPH

支持：

```text
reports_to
manages
member_of
delegates_to
collaborates_with
supervises
contracts_with
```

---

# 174. AGENT NETWORK GRAPH

支持：

```text
knows
discovers
trusts
delegates
communicates
provides_service
consumes_service
```

---

# 175. WORLD GRAPH

支持：

```text
Entity
owns
contains
depends_on
connected_to
located_at
changed_by
caused_by
observed_by
```

---

# 176. UNIFIED GRAPH MODEL

最终可形成：

```text
Human
Agent
Organization
Memory
Tool
Capability
World Entity
Event
Task
```

之间的：

```text
Ownership
Permission
Relationship
Dependency
Trust
Communication
Causality
Provenance
```

---

# 177. FIRST IMPLEMENTATION ORDER

Codex 必须按照：

```text
Phase 1  Foundation
Phase 2  Identity / Kernel
Phase 3  Agent Runtime
Phase 4  Model Gateway
Phase 5  Memory
Phase 6  Capability / Tool
Phase 7  Policy / Approval
Phase 8  Execution
Phase 9  L-Core
Phase 10 Multi-Agent
Phase 11 Perception / Analysis
Phase 12 Organization
Phase 13 Long-Horizon
Phase 14 Network
Phase 15 World
Phase 16 Trust / Security / Governance
Phase 17 Economy
Phase 18 Verification / Experience
Phase 19 Evolution
Phase 20 L10K / Benchmark
Phase 21 Hardening / Production
```

---

# 178. PHASE 1 — FOUNDATION

实现：

```text
Repository
Settings
Logging
Config
Postgres
Redis
Docker
CI
Health
Ready
```

验收：

```text
docker compose up
API starts
DB migrates
Health passes
Tests run
```

---

# 179. PHASE 2 — KERNEL

实现：

```text
Identity
Principal
Role
Permission
Capability
Policy
Event
Resource
Audit
```

验收：

```text
Create User
Create Organization
Create Agent Identity
Grant Capability
Deny Unauthorized Action
Audit
```

---

# 180. PHASE 3 — AGENT RUNTIME

实现：

```text
Agent
State Machine
Lifecycle
Worker
Scheduler
Execution
Checkpoint
Cancellation
Recovery
```

验收：

```text
Create Agent
Run Agent
Pause
Resume
Stop
Recover
Terminate
```

---

# 181. PHASE 4 — MODEL GATEWAY

实现：

```text
Provider Adapter
Model Registry
Model Router
Usage
Cost
Streaming
Structured Output
Tool Calling
```

验收：

```text
Call model
Measure latency
Measure cost
Failover provider
Route by capability
```

---

# 182. PHASE 5 — MEMORY

实现：

```text
Memory Model
Postgres Store
Qdrant Store
Object Store
Provenance
Permissions
Retrieval
Context
```

验收：

```text
Write
Read
Filter
Permission Deny
Vector Retrieve
Delete
Audit
```

---

# 183. PHASE 6 — CAPABILITY / TOOL

实现：

```text
Capability Registry
Tool Registry
Tool Executor
Tool Health
Sandbox
```

验收：

```text
Register Tool
Authorize Tool
Execute Tool
Reject Unauthorized Tool
Audit Tool
```

---

# 184. PHASE 7 — POLICY / APPROVAL

实现：

```text
Policy DSL / Engine
Approval Workflow
Risk Classification
Policy Versions
```

验收：

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

---

# 185. PHASE 8 — EXECUTION

实现：

```text
Goal
Task
Plan
Action
Execution
Retry
Recovery
Compensation
Verification
```

验收：

```text
Complex Task E2E
Failure Recovery
Checkpoint Resume
Verification
```

---

# 186. PHASE 9 — L-CORE

实现：

```text
Conversation
Intent
Context
Goal
Planning
Delegation
Result Synthesis
Realtime
```

验收：

```text
Human Intent
→ Real Task
→ Real Execution
→ Real Verified Result
```

---

# 187. PHASE 10 — MULTI-AGENT

实现：

```text
Agent Factory
Spawning
Orchestration
Agent Messages
Parallel Execution
Supervisor
Dynamic Team
```

验收：

```text
Manager
→ Researchers
→ Analyst
→ QA
→ Final Result
```

---

# 188. PHASE 11 — PERCEPTION / ADA

实现：

```text
Vision Adapter
Document
Audio
Observation
World Model

Data
Python
SQL
Statistics
Simulation
```

验收：

```text
Image / Document / Data
→ Real Observation / Analysis
```

---

# 189. PHASE 12 — ORGANIZATION

实现：

```text
Organization
Departments
Teams
Roles
KPI
Budget
Memory
```

验收：

```text
Create Organization
Hire/Provision Agents
Assign
Delegate
Evaluate
Report
```

---

# 190. PHASE 13 — ENOCH

实现：

```text
Persistent Agents
Mission Scheduler
Checkpoint
Long-running State
Event Monitor
Historical Memory
```

验收：

```text
Persistent Mission
Restart
Resume
Observe
Replan
Report
```

---

# 191. PHASE 14 — NETWORK

实现：

```text
Agent Discovery
A2A Adapter
MCP Adapter
External Agent Identity
Trust
Delegation
```

验收：

```text
External Agent
→ Authenticate
→ Authorize
→ Execute
→ Audit
```

---

# 192. PHASE 15 — WORLD

实现：

```text
Browser
Filesystem
Git
Database
HTTP
Cloud
Email
Calendar
Device Adapters
World Model
Simulation
```

验收：

```text
Real External System
→ Policy
→ Action
→ Verify
→ Audit
```

---

# 193. PHASE 16 — TRUST / SECURITY / GOVERNANCE

实现：

```text
Threat Model
Reputation
Trust
Risk
Policy
Audit
Revocation
Emergency Stop
```

验收：

```text
Attack
→ Detect / Block
→ Audit
```

---

# 194. PHASE 17 — ECONOMY

实现：

```text
Budget
Quota
Usage
Billing
Pricing
Contracts
Marketplace
```

验收：

```text
Budget Reserve
Budget Consume
Budget Block
Usage Report
```

---

# 195. PHASE 18 — VERIFICATION / EXPERIENCE

实现：

```text
Verifier
Evaluation
Experience Extraction
Evidence
Provenance
```

验收：

```text
Execution Result
→ Verified / Partial / Failed / Unknown
```

---

# 196. PHASE 19 — EVOLUTION

实现：

```text
Experiment
Benchmark
Proposal
Approval
Deployment
Rollback
Monitoring
```

不允许：

```text
Unbounded self-modification
```

---

# 197. PHASE 20 — L10K

实现：

```text
Benchmark Registry
Baseline
Task Classes
Value Measurement
Human Active Time
Verified Output
```

输出：

```text
VHL
Task Success
Human Intervention
Cost
Security
Reliability
```

---

# 198. PHASE 21 — PRODUCTION HARDENING

必须完成：

```text
Security Hardening
Rate Limiting
Circuit Breaker
Bulkhead
Backup
Restore
DR
Chaos Testing
Load Testing
Observability
Alerting
Runbooks
Incident Response
```

---

# 199. BACKUP / DR

PostgreSQL：

```text
Backup
Restore
Point-in-Time Recovery
Replication
```

目标根据实际部署制定：

```text
RPO
RTO
```

必须进行真实 Restore Drill。

---

# 200. INCIDENT RESPONSE

Incident lifecycle：

```text
Detect
 ↓
Classify
 ↓
Contain
 ↓
Investigate
 ↓
Recover
 ↓
Verify
 ↓
Postmortem
 ↓
Prevent Recurrence
```

---

# 201. DOCUMENTATION REQUIRED

必须同步维护：

```text
README
Architecture
ADR
API Documentation
Capability Registry
Security Model
Memory Model
Agent Lifecycle
Organization Model
Network Model
Operations Manual
Runbooks
Benchmark Specification
```

---

# 202. ADR REQUIREMENT

重大技术决策需要记录：

```text
Context
Decision
Alternatives
Tradeoffs
Consequences
```

---

# 203. OPERATIONS DASHBOARD

必须显示真实：

```text
Agents
Tasks
Executions
Errors
Approvals
Tool Calls
Models
Cost
Memory
Events
Security Alerts
Organizations
```

---

# 204. AGENT GRAPH UI

必须支持：

```text
Agent
Parent
Children
Supervisor
Peers
Organization
Tasks
Tools
Capabilities
```

图数据必须来自真实数据库 / Event Store。

---

# 205. EXECUTION TIMELINE UI

显示：

```text
Goal
Plan
Step
Agent
Tool
Policy
Approval
Result
Verification
Cost
Time
```

---

# 206. MEMORY EXPLORER

必须展示：

```text
Owner
Scope
Type
Source
Confidence
Provenance
Permissions
CreatedAt
UpdatedAt
```

用户不应只能看到一段“黑盒记忆文本”。

---

# 207. APPROVAL CENTER

显示：

```text
Who
Agent
Action
Target
Risk
Reason
Budget
Policy
Expiry
Evidence
```

操作：

```text
Approve
Reject
Approve Once
Pause
Revoke
```

---

# 208. WORLD VIEW

显示：

```text
Entities
States
Events
Relationships
Changes
Confidence
Sources
```

---

# 209. NETWORK VIEW

显示：

```text
Internal Agents
External Agents
Organizations
Protocols
Trust
Delegations
Messages
```

---

# 210. GOVERNANCE VIEW

显示：

```text
Policies
Violations
Approvals
Revocations
Risk
Audits
Security Events
```

---

# 211. ECONOMY VIEW

显示：

```text
Budget
Usage
Cost
Agent Cost
Task Cost
Organization Cost
Contracts
Marketplace
```

---

# 212. EVALUATION VIEW

显示：

```text
Agent Evaluation
Task Evaluation
Benchmark
Success
Verification
Reliability
Cost
Regression
```

---

# 213. AGENT WORLD DEFINITION

Agent World 在工程上定义为：

```text
Agents
+
Organizations
+
Protocols
+
Memory
+
Capabilities
+
Services
+
Data
+
Compute
+
Governance
```

不是：

```text
digital consciousness
```

不是：

```text
digital life
```

---

# 214. FINAL ARCHITECTURE FORMULA

```text
LIUHAO X

=
JARVIS
+
ULTRON
+
VISION
+
ADA
+
EDITH
+
FRIDAY
+
JOCaSTA
+
KAREN
+
ENOCH
+
ZOON
```

但在工程上实际表示：

```text
Human Interface
+
Autonomous Runtime
+
Perception
+
Computation
+
World Interface
+
Realtime Intelligence
+
Organization
+
Personal Context
+
Long-Horizon Runtime
+
Specialized Intelligence
```

最终统一为：

```text
LIUHAO KERNEL
+
AGENT RUNTIME
+
AGENT ORGANIZATION
+
AGENT NETWORK
+
WORLD INTERFACE
+
GOVERNANCE
```

---

# 215. FINAL PRODUCT POSITION

第一阶段：

> AI Employee Operating System

第二阶段：

> Agent Operating System

第三阶段：

> Agent Infrastructure

第四阶段：

> Agent Network

第五阶段：

> Agent Economy

长期：

> **The Operating System for the Agent World**

---

# 216. FINAL USER MODEL

用户不需要管理：

```text
几十个 AI 聊天窗口
```

而是：

```text
一个 L-Core
一个 Agent Organization
多个 Specialized Agents
多个 Tools
多个 Missions
一个统一 Memory System
一个统一 Policy System
一个统一 Execution System
```

---

# 217. FINAL HUMAN MODEL

Human 主要控制：

```text
Intent
Goal
Direction
Permission
Risk Boundary
Budget
Final Decision
```

LIUHAO 负责：

```text
Understanding
Planning
Delegation
Execution
Coordination
Monitoring
Verification
Memory
Optimization
Reporting
```

---

# 218. FINAL LIUHAO LOOP

```text
HUMAN INTENT
      ↓
L-CORE
      ↓
PERCEPTION
      ↓
CONTEXT
      ↓
MEMORY
      ↓
REASONING
      ↓
PLANNING
      ↓
POLICY
      ↓
AGENT FACTORY
      ↓
MULTI-AGENT
      ↓
ORGANIZATION
      ↓
WORLD INTERFACE
      ↓
EXECUTION
      ↓
OBSERVATION
      ↓
VERIFICATION
      ↓
EXPERIENCE
      ↓
EVALUATION
      ↓
EVOLUTION
      ↓
L-CORE
```

---

# 219. FINAL SAFETY LOOP

```text
IDENTITY
 ↓
AUTHENTICATION
 ↓
AUTHORIZATION
 ↓
CAPABILITY
 ↓
POLICY
 ↓
APPROVAL
 ↓
SANDBOX
 ↓
EXECUTION
 ↓
VERIFICATION
 ↓
AUDIT
 ↓
REVOCATION
```

---

# 220. FINAL ENGINEERING PRINCIPLE

> **任何智能都必须有边界。**

> **任何能力都必须有身份。**

> **任何 Tool 都必须有 Capability。**

> **任何 Action 都必须有 Policy。**

> **任何 Critical Action 都必须可审计。**

> **任何 Memory 都必须有 Owner。**

> **任何外部 Agent 都必须有 Trust Boundary。**

> **任何 Autonomous Behavior 都必须有 Resource Boundary。**

> **任何生产改动都必须经过 Evaluation。**

> **任何能力状态都必须真实反映实际实现状态。**

---

# 221. CODEX MASTER EXECUTION DIRECTIVE

## BEGIN EXECUTION DIRECTIVE

你正在实现：

# LIUHAO X v3.0

不要把它理解为一个普通 Agent Chat App。

不要把它实现成十个独立聊天机器人。

不要把十个名字分别做成十个假的 Agent。

你必须构建一个统一的：

# Human-Sovereign Agent Operating System

基础架构继承并保留 LIUHAO X 63827。

本版本 v3.0 在其基础上扩展完整十源能力体系：

```text
ULTRON
VISION
ADA
EDITH
FRIDAY
JARVIS
JOCaSTA
KAREN
ENOCH
ZOON
```

将这些能力去重后统一进入：

```text
LIUHAO KERNEL
```

---

## EXECUTION ORDER

开始之前：

1. 检查现有 repository。
2. 检查当前 branch。
3. 检查已有代码。
4. 检查数据库 migrations。
5. 检查现有 Agent Runtime。
6. 检查现有 API。
7. 检查现有 frontend。
8. 检查现有 tests。
9. 保留已经正确实现的能力。
10. 不要重复造轮子。
11. 不要无理由重构稳定模块。

然后按照：

```text
Foundation
→ Kernel
→ Agent Runtime
→ Model Gateway
→ Memory
→ Capability / Tool
→ Policy
→ Execution
→ L-Core
→ Multi-Agent
→ Perception / Analysis
→ Organization
→ Long-Horizon
→ Network
→ World
→ Security / Governance
→ Economy
→ Verification
→ Evolution
→ L10K
→ Production Hardening
```

执行。

---

## IMPLEMENTATION REQUIREMENT

每一个实现都必须至少形成：

```text
Code
Data Model
Service
API
State
Permission
Policy
Event
Audit
Test
```

不能只写接口。

不能只写模型。

不能只写 UI。

不能只写 README。

---

## REAL IMPLEMENTATION REQUIREMENT

当能力未实现：

```text
DO NOT FAKE IT
```

使用：

```text
NotImplemented
Experimental
Planned
Research
```

明确状态。

---

## INTELLIGENCE REQUIREMENT

不要用：

```text
hardcoded fake responses
fake task success
fake progress
fake agent counts
fake execution
fake verification
```

替代真实 Runtime。

---

## AGENT REQUIREMENT

每个 Agent 必须拥有：

```text
Identity
Runtime
State
Memory
Capabilities
Permissions
Budget
Resources
Lifecycle
Execution History
```

---

## TOOL REQUIREMENT

每个 Tool 必须：

```text
Registered
Validated
Capability-bound
Policy-checked
Executable
Observable
Auditable
```

---

## MEMORY REQUIREMENT

每条 Memory 必须：

```text
Owned
Scoped
Permissioned
Provenance-tracked
Retrievable
Auditable
```

---

## AUTONOMY REQUIREMENT

自主执行不得绕过：

```text
Identity
Permission
Capability
Policy
Resource
Budget
Sandbox
Audit
```

---

## SPAWN REQUIREMENT

Agent Spawn 必须：

```text
Authorize
Quota
Budget
Resource
Capability
Policy
Sandbox
Create
Audit
```

禁止无限复制。

---

## WORLD REQUIREMENT

World Interface 必须通过：

```text
Capability
Policy
Approval
Sandbox where appropriate
Verification
Audit
```

---

## EXTERNAL AGENT REQUIREMENT

所有外部 Agent 必须进入：

```text
Identity
Trust
Capability
Policy
Execution
Audit
```

---

## EVOLUTION REQUIREMENT

AI 自我优化只能经过：

```text
Experiment
Benchmark
Evaluation
Approval
Deployment
Monitoring
Rollback
```

---

## PRODUCTION REQUIREMENT

不允许：

```text
unbounded autonomy
unbounded spawning
privilege escalation
hidden network access
hidden credentials
hidden tool calls
unaudited critical actions
cross-owner memory leakage
unverified production mutation
```

---

## TEST REQUIREMENT

每个重大功能都必须添加：

```text
Unit
Integration
E2E
Security
Failure
Regression
```

---

## ACCEPTANCE REQUIREMENT

一个功能只有同时满足：

```text
Implemented
Tested
Observable
Permissioned
Audited
Documented
```

才可以标记：

```text
IMPLEMENTED
```

---

## FINAL OBJECTIVE

最终构建：

```text
ONE HUMAN
        ↓
L-CORE
        ↓
LIUHAO KERNEL
        ↓
AGENT FACTORY
        ↓
SPECIALIZED AGENTS
        ↓
MULTI-AGENT ORGANIZATION
        ↓
AGENT NETWORK
        ↓
WORLD INTERFACE
        ↓
REAL DIGITAL / PHYSICAL SYSTEMS
```

目标不是构建“十个电影角色”。

目标是：

> **把这十个能力体系中所有能够现实工程化的能力，变成一个统一、真实、可运行、可验证、可治理的 LIUHAO X。**

---

# FINAL CODEX COMMAND

BUILD LIUHAO X v3.0.

Preserve the valid architecture inherited from 63827.

Extend it into the complete ten-source capability architecture.

Do not build fictional characters.

Build the underlying real engineering primitives.

Every capability must have:

```text
Capability ID
Source
Atomic Capability
Module
Interface
API
State
Permission
Policy
Risk
Realizability
Implementation Status
Test
Metric
Failure Handling
Audit
```

Build the complete runtime.

Build the complete identity system.

Build the complete memory system.

Build the complete capability system.

Build the complete policy system.

Build the complete execution engine.

Build the complete multi-agent system.

Build the complete organization system.

Build the complete realtime system.

Build the complete long-horizon system.

Build the complete perception system.

Build the complete computational intelligence system.

Build the complete world interface.

Build the complete agent network.

Build the complete trust and security system.

Build the complete economy and governance system.

Build the complete verification and evaluation system.

Build controlled evolution.

Build L-Core.

Build L10K.

Do not claim unsupported intelligence.

Do not fake unsupported capabilities.

Do not use hardcoded demos as production systems.

Do not expose privileged execution.

Do not bypass human sovereignty.

Do not bypass authorization.

Do not bypass audit.

Do not bypass verification.

Every critical operation must be:

```text
Identity
→ Authorization
→ Capability
→ Policy
→ Approval if required
→ Sandbox
→ Execution
→ Verification
→ Audit
```

Build the system incrementally.

Run tests after every phase.

Do not mark a feature complete until it is actually implemented and tested.

When blocked by an unavailable external dependency, implement the correct interface, adapter boundary, tests, and explicit status rather than fabricating functionality.

When an existing implementation is correct, preserve it.

When an implementation is incomplete, finish it rather than creating a parallel duplicate.

Keep the architecture modular and extensible.

Keep security boundaries explicit.

Keep state explicit.

Keep events explicit.

Keep failures explicit.

Keep metrics explicit.

Keep capability coverage explicit.

Build LIUHAO X.

# LIUHAO X

## One Kernel.

## One Agent Runtime.

## One Intelligence Infrastructure.

## One Agent World.

## Human Sovereignty Above All.

## END EXECUTION DIRECTIVE
