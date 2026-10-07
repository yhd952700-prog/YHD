# LIUHAO — 文档索引

> **本文件是`docs/` 树的地图**：它回答两个问题 —— **按什么顺序读**，以及
> **每篇文档是"现状"还是"目标态/历史"**。
>
> **覆盖状态（2026-10-07 实测）**：活跃文档 **125** 篇（不含 `archive/` 与
> `spec/incoming/`），本索引**逐一列全 125 篇**。该数字由
> [`scripts/verify_docs_index_coverage.py`](../scripts/verify_docs_index_coverage.py)
> 在 CI 中机械校验：漏列、虚列、目录结构块与真实目录不一致，都会让CI 变红。
>
> **⚠️ 阅读前必读**：本项目里有三类文档，混读会得出相反结论。**先看下面的
> 「文档类别」一节**，再开始读。

---

## 0. 先认清三类文档（最重要的一节）

本仓库的文档不是同质的。把「目标态契约」当成「现状」，或把「历史快照」当成
「当前事实」，会得到与代码相反的结论 —— 这类误读本项目已反复发生。

| 类别 | 标记 | 是什么 | 能不能当作现状引用 |
|---|---|---|---|
| **现状规格** |无标记 | 与代码逐条核对过，含诚实状态头 | ✅ 可以，但要看它的证据日期 |
| **目标态契约** | 文首「设计意图 / 目标态」 | 规定应该是什么，**不是**现在是什么 | ❌ 不能。缺口清单才是现状 |
| **历史快照** | ⚠️ 快照 / 已过时 / DEPRECATED | 某一时刻的观测，保留作演进对照 | ❌ 不能。文首通常已自我否定 |
| **治理/登记册** | LIVING / 持续维护 | 活文档，随项目滚动更新 | ✅ 可以，但要认它的更新时间 |

**判定纪律（项目宪法，仲裁优先级）**：
**代码事实 > KERNEL-CANON > 其余一切**。docstring、同名方法、registry 字段
都**不是**证据。文档与代码冲突时，以代码为准，并回去修文档。

---

## 1. 按角色与目的的阅读顺序

### 我是 AI 编码代理 / 要改这个仓库的

1. **[`CODEX-CONTRACT.md`](CODEX-CONTRACT.md)** — 执行契约，**必读第一站**（25 条硬规则）
2. **[`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md)** — 宪法层单一入口（冲突裁决 C1–C12）
3. **[`spec/KERNEL-CANON.md`](spec/KERNEL-CANON.md)** — Kernel 唯一权威清单
   ⚠️ 计数必须用运行时 `len(KERNEL_ENTRIES)`，**不能数目录**（`retention/` 是非注册子系统）
4. **[`autonomous/GOVERNANCE.md`](autonomous/GOVERNANCE.md)** — 授权范围、硬边界、人类保留项
5. 按需查 [`spec/`](spec/) 下的权威规格与DoD 审计

> ⚠️ `lint_audit_claims.py` **只扫 markdown**，看不见 `.py` docstring。
> 本项目最严重的一类文档缺陷恰恰在 docstring 里（「描述了一个从未存在的功能」）。

### 我是新成员 / 第一次接触

1. **[`quickstart.md`](quickstart.md)** — 快速上手
2. **[`RUN.md`](RUN.md)** — 把后端网关 + 前端驾驶舱真跑起来（**已实测跑通**，含必需环境变量）
   · 5 分钟版：[`QUICKSTART-RUN.md`](QUICKSTART-RUN.md)
3. **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — 架构导航门户
4. **[`product/PRODUCT-READINESS.md`](product/PRODUCT-READINESS.md)** — 产品门禁 P1–P10 真实状态
5. **[`MASTER_PROJECT_READINESS.md`](../MASTER_PROJECT_READINESS.md)**（仓库根）— 工程状态与已知风险全集

### 我是架构师

1. [`ARCHITECTURE.md`](ARCHITECTURE.md) → [`architecture/Architect-Architecture-v3.0.md`](architecture/Architect-Architecture-v3.0.md)
2. [`architecture/kernels-interface.md`](architecture/kernels-interface.md) · [`architecture/dependency-map.md`](architecture/dependency-map.md)
3. [`architecture/gap-analysis.md`](architecture/gap-analysis.md) · [`architecture/existing-codebase-audit.md`](architecture/existing-codebase-audit.md)
4. **现状规格**：[`kernel-spec/README.md`](kernel-spec/README.md) 及其 14 篇
5. **目标态规格**：[`spec/MASTER-SPEC-v3.0.md`](spec/MASTER-SPEC-v3.0.md) · [`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md)
6. [`autonomous/ROADMAP.md`](autonomous/ROADMAP.md) — 演进方向（LIVING）

### 我要上线 / 运维

1. **[`operations/LAUNCH-CRITERIA.md`](operations/LAUNCH-CRITERIA.md)** — **「上线」到底意味着什么**（先读这个，它专治「代码有了就算完成」）
2. [`operations/production-runbook.md`](operations/production-runbook.md) · [`operations/runbook.md`](operations/runbook.md)
3. [`operations/health-check-spec.md`](operations/health-check-spec.md) · [`operations/scenario-chatbot.md`](operations/scenario-chatbot.md)
4. [`autonomous/G9-deployment-contract.md`](autonomous/G9-deployment-contract.md) · [`autonomous/G9-image-build-runbook.md`](autonomous/G9-image-build-runbook.md)

### 我要做安全 / 审计 / 治理判断

1. **[`autonomous/GOVERNANCE.md`](autonomous/GOVERNANCE.md)** — 什么必须升给人
2. **[`autonomous/HUMAN-DECISION-BACKLOG.md`](autonomous/HUMAN-DECISION-BACKLOG.md)** — 已隔离的人类主权项
3. [`security/shell-adapter-threat-model.md`](security/shell-adapter-threat-model.md)
4. **登记册**：[`autonomous/UNKNOWN-TO-OWNER.md`](autonomous/UNKNOWN-TO-OWNER.md)（盲点）·
   [`autonomous/RISK-REGISTER.md`](autonomous/RISK-REGISTER.md)（风险）·
   [`autonomous/IDENTIFIER-POLICY.md`](autonomous/IDENTIFIER-POLICY.md)（标识符，DRAFT）
5. [`adr/`](adr/) 的架构决策记录 · [`autonomous/ADR-*`](autonomous/) 的审计存储 ADR

### 我要评估「这项目到底能不能用」

1. [`product/PRODUCT-READINESS.md`](product/PRODUCT-READINESS.md) — 产品门禁，「有代码」≠「可用」
2. [`product/KERNEL-PRODUCT-CAPABILITY.md`](product/KERNEL-PRODUCT-CAPABILITY.md) — 14 内核逐个的产品成熟度
3. [`product/capability-traceability.md`](product/capability-traceability.md) · [`product/PM-PRD-v3.0.md`](product/PM-PRD-v3.0.md)
4. [`spec/CAPABILITY-REGISTRY.md`](spec/CAPABILITY-REGISTRY.md) · [`spec/KERNEL-DOD-AUDIT.md`](spec/KERNEL-DOD-AUDIT.md) · [`spec/AI-LAYER-DOD-AUDIT.md`](spec/AI-LAYER-DOD-AUDIT.md)

---

## 2. 目录结构（14 个目录，实测）

```text
docs/
├── README.md                    ← 本文件（索引）
├── CODEX-CONTRACT.md            ← AI 编码代理执行契约（必读）
├── quickstart.md / QUICKSTART-RUN.md / RUN.md / HUMAN-DECISIONS.md
├── ARCHITECTURE.md              ← 架构导航门户
├── *-DESIGN.md 等 15 篇根级设计文档（见 §4）
├── adr/                (2)     架构决策记录
├── architecture/       (8)     架构
├── autonomous/         (23)    治理章程 · ADR · 风险/盲点登记册 · 部署契约 · 基准
├── blueprint-upgrades/ (29)    UBX 蓝图自动升级机制（6 个包 + 模板）
├── ci/                 (1)     CI 门禁豁免登记册
├── decisions/          (1)     悬而未决决策登记
├── governance/         (1)     控制声明裁决登记册（control-claim）
├── kernel-spec/        (15)    14 内核规范（现状 + 诚实状态头）
├── l10k/               (1)     L10K 基准设计
├── operations/         (5)     运维与上线
├── product/            (7)     产品需求 · UI/UX · 产品就绪门禁
├── security/           (1)     安全威胁模型
├── spec/               (12)    权威规格 / 宪法蓝图 / DoD 审计 / 迁移矩阵
│   └── incoming/       (6)     ⚠️ 已被取代的合并草案（由 MERGE-LEDGER.md 登记）
└── archive/            (7)     ⚠️ 历史归档，**保持原貌**，路径为整理前原路径
```

---

## 3. 权威层级（冲突时从上往下）

```text
第 0 层  宪法单一入口   spec/UNIFIED-BLUEPRINT.md（索引 + 冲突裁决 C1–C12）
第 1 层  权威规格       spec/KERNEL-CANON.md · spec/CAPABILITY-REGISTRY.md · spec/MASTER-SPEC-v3.0.md
第 2 层  治理与宪法位   autonomous/GOVERNANCE.md（授权与硬边界）· docs/HUMAN-DECISIONS.md
第 3 层  架构导航       ARCHITECTURE.md（门户，非权威）
第 4 层  现状规格       kernel-spec/ · product/PRODUCT-READINESS.md · 根级 MASTER_PROJECT_READINESS.md
第 5 层  支撑文档       spec/GAP-MIGRATION-MATRIX.md · architecture/gap-analysis.md · spec/REMEDIATION-PLAN.md
第 6 层  执行与决策     autonomous/ROADMAP.md · EXECUTION-QUEUE.md · adr/ · autonomous/ADR-* · decisions/
第 7 层  登记册（LIVING）autonomous/UNKNOWN-TO-OWNER.md · RISK-REGISTER.md · HUMAN-DECISION-BACKLOG.md
第 8 层  演进机制       blueprint-upgrades/（受 UNIFIED-BLUEPRINT §9.1 约束）
第 9 层  历史归档       archive/（保持原貌）
```

> ⚠️ `docs/archive/` **不参与**冲突裁决。它按自身声明「保持原貌」，正文里的
> 文件路径是整理前的原始路径，**不可**当作现行路径引用。

---

## 4. 全量清单（125 篇，逐一列出）

### 4.1 根级（4）

| 文件 | 说明 |
|---|---|
| [`README.md`](README.md) | 本文件（索引） |
| [`CODEX-CONTRACT.md`](CODEX-CONTRACT.md) | **AI 编码代理执行契约**：25 条硬规则 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 架构导航门户 · 读者顺序表 |
| [`quickstart.md`](quickstart.md) | 快速上手 |

### 4.2 根级设计与决策（15）

| 文件 | 说明 |
|---|---|
| [`RUN.md`](RUN.md) | 把网关 + 驾驶舱真跑起来（实测跑通，含环境变量与 fail-closed 语义） |
| [`QUICKSTART-RUN.md`](QUICKSTART-RUN.md) | 5 分钟快捷版（登录/门禁以 `RUN.md` 为准） |
| [`HUMAN-DECISIONS.md`](HUMAN-DECISIONS.md) | 人类已作出的决策记录 |
| [`ORPHAN-AUDIT.md`](ORPHAN-AUDIT.md) | 孤儿资产审计 |
| [`REPO-LINE-CLOSURE.md`](REPO-LINE-CLOSURE.md) | 仓库线索收敛 |
| [`AGENT-RUNTIME-DESIGN.md`](AGENT-RUNTIME-DESIGN.md) | Agent 运行时设计 |
| [`ACCESS-CONTROL-CONVERGENCE-DESIGN.md`](ACCESS-CONTROL-CONVERGENCE-DESIGN.md) | 访问控制收敛设计 |
| [`CACHE-LAYER-DESIGN.md`](CACHE-LAYER-DESIGN.md) | 缓存层设计 |
| [`EVALUATION-KERNEL-DESIGN.md`](EVALUATION-KERNEL-DESIGN.md) | 评估内核设计 |
| [`MEMORY-SYSTEM-DESIGN.md`](MEMORY-SYSTEM-DESIGN.md) | 记忆系统设计 |
| [`MESSAGE-BUS-BACKEND-DESIGN.md`](MESSAGE-BUS-BACKEND-DESIGN.md) | 消息总线后端设计 |
| [`MODEL-ROUTER-DESIGN.md`](MODEL-ROUTER-DESIGN.md) | 模型路由设计 |
| [`POLICY-RULE-OBSERVABILITY-DESIGN.md`](POLICY-RULE-OBSERVABILITY-DESIGN.md) | 策略规则可观测性设计 |
| [`POSTGRES-BACKEND-DESIGN.md`](POSTGRES-BACKEND-DESIGN.md) | Postgres 后端设计 |
| [`PRODUCTION-READINESS-DESIGN.md`](PRODUCTION-READINESS-DESIGN.md) | 生产就绪探测设计 |
| [`QUOTA-ACCOUNTING-DESIGN.md`](QUOTA-ACCOUNTING-DESIGN.md) | 配额计费设计 |

### 4.3 `adr/`（2）— 架构决策记录

| 文件 | 说明 |
|---|---|
| [`adr/ADR-HOLD-SCOPE.md`](adr/ADR-HOLD-SCOPE.md) | D27：HOLD 范围决策 |
| [`adr/ADR-root-of-trust-hd05.md`](adr/ADR-root-of-trust-hd05.md) | HD-05：预留根信任provider |

### 4.4 `architecture/`（8）— 架构

| 文件 | 说明 |
|---|---|
| [`architecture/Architect-Architecture-v3.0.md`](architecture/Architect-Architecture-v3.0.md) | 架构总纲（⚠️ §4 用 K01–K12 旧编号，以 KERNEL-CANON 为准） |
| [`architecture/kernels-interface.md`](architecture/kernels-interface.md) | 内核接口定义 |
| [`architecture/dependency-map.md`](architecture/dependency-map.md) | 模块依赖图 |
| [`architecture/existing-codebase-audit.md`](architecture/existing-codebase-audit.md) | 现有代码库全量审计 |
| [`architecture/gap-analysis.md`](architecture/gap-analysis.md) | 现状 ↔ Definition Lock 差距 |
| [`architecture/p7-baseline.md`](architecture/p7-baseline.md) | P7 架构基线 |
| [`architecture/migration-matrix.md`](architecture/migration-matrix.md) | 迁移矩阵 |
| [`architecture/phase-9-10-12-15-e2e-demo.md`](architecture/phase-9-10-12-15-e2e-demo.md) | Phase 9/10/12/15 端到端 demo |

### 4.5 `autonomous/`（23）— 治理 · 决策 · 登记册 · 部署 · 基准

> ⚠️ **本目录包含项目最高治理文件 [`GOVERNANCE.md`](autonomous/GOVERNANCE.md)
> （自主开发授权、硬边界、人类主权保留项）。旧版索引完全遗漏本目录。**

**治理与授权**

| 文件 | 说明 |
|---|---|
| [`autonomous/GOVERNANCE.md`](autonomous/GOVERNANCE.md) | 🔴 **治理章程**：授权范围 · 硬边界 · 人类决策保留项 |
| [`autonomous/HUMAN-DECISION-BACKLOG.md`](autonomous/HUMAN-DECISION-BACKLOG.md) | 已隔离的人类主权项（**是透明度，不是闸门**） |
| [`autonomous/HUMAN-DECISION-HD06-U37.md`](autonomous/HUMAN-DECISION-HD06-U37.md) | HD-06 / U37 决策记录 |
| [`autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md`](autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md) | 留存 / 删除义务 vs 证据保全冲突（分析已交付，**策略本身留给 HD-06 人类决策**，未自动实施） |

**活登记册（LIVING）**

| 文件 | 说明 |
|---|---|
| [`autonomous/UNKNOWN-TO-OWNER.md`](autonomous/UNKNOWN-TO-OWNER.md) | U-NNN 盲点登记册（只读发现） |
| [`autonomous/RISK-REGISTER.md`](autonomous/RISK-REGISTER.md) | R-G2/G3-* 风险登记册 |
| [`autonomous/ROADMAP.md`](autonomous/ROADMAP.md) | 演进路线图 |
| [`autonomous/EXECUTION-QUEUE.md`](autonomous/EXECUTION-QUEUE.md) | 执行队列 |
| [`autonomous/IDENTIFIER-POLICY.md`](autonomous/IDENTIFIER-POLICY.md) | 审计标识符策略（**DRAFT**） |

**ADR（审计存储与证据）**

| 文件 | 说明 |
|---|---|
| [`autonomous/ADR-audit-storage-generation-2.md`](autonomous/ADR-audit-storage-generation-2.md) | 审计存储 Gen2 |
| [`autonomous/ADR-audit-storage-C3.md`](autonomous/ADR-audit-storage-C3.md) | 百万级存储架构（C3） |
| [`autonomous/ADR-audit-single-writer-lease.md`](autonomous/ADR-audit-single-writer-lease.md) | 单写者租约 |
| [`autonomous/ADR-audit-writer-lease-identity.md`](autonomous/ADR-audit-writer-lease-identity.md) | 写者租约身份模型 |
| [`autonomous/ADR-audit-head-notary.md`](autonomous/ADR-audit-head-notary.md) | 链头公证 |
| [`autonomous/ADR-audit-transparency-anchor.md`](autonomous/ADR-audit-transparency-anchor.md) | 透明日志锚定 |
| [`autonomous/ADR-tsa-quorum-ha.md`](autonomous/ADR-tsa-quorum-ha.md) | TSA 多 quorum 高可用 |

**部署 · 性能 · 实验**

| 文件 | 说明 |
|---|---|
| [`autonomous/G9-deployment-contract.md`](autonomous/G9-deployment-contract.md) | G9 部署契约：证据表 + 诚实的缺口 + 人类决策清单 |
| [`autonomous/G9-image-build-runbook.md`](autonomous/G9-image-build-runbook.md) | G9 镜像构建运行手册 |
| [`autonomous/G7-perf-baseline-2026-10-01.md`](autonomous/G7-perf-baseline-2026-10-01.md) | G7 性能基线（实测 + 陈旧项标注） |
| [`autonomous/SCALE-HARNESS-RESULTS.md`](autonomous/SCALE-HARNESS-RESULTS.md) | 规模台实测结果（1M 事件） |
| [`autonomous/PERFORMANCE-BASELINE.md`](autonomous/PERFORMANCE-BASELINE.md) | 性能基线 |
| [`autonomous/EXPERIMENT-MATRIX-crash-powerloss.md`](autonomous/EXPERIMENT-MATRIX-crash-powerloss.md) | 崩溃/掉电实验矩阵 |
| [`autonomous/FIX-B-capability-register-exemption-design.md`](autonomous/FIX-B-capability-register-exemption-design.md) | 能力注册豁免设计 |

### 4.6 `blueprint-upgrades/`（29）— 蓝图自动升级机制（UBX）

> 机制说明见 [`blueprint-upgrades/README.md`](blueprint-upgrades/README.md)。
> 受 [`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md) §9.1 约束，
> 授权来源 [`autonomous/GOVERNANCE.md`](autonomous/GOVERNANCE.md) §4。
> 每包四篇：`ADR.md` · `architecture-delta.md` · `migration-path.md` · `verification-plan.md`

| 包 | 主题 | 四篇文档 |
|---|---|---|
| **UBX-001** | executor fence | [`ADR`](blueprint-upgrades/UBX-001-executor-fence/ADR.md) · [`delta`](blueprint-upgrades/UBX-001-executor-fence/architecture-delta.md) · [`migration`](blueprint-upgrades/UBX-001-executor-fence/migration-path.md) · [`verification`](blueprint-upgrades/UBX-001-executor-fence/verification-plan.md) |
| **UBX-002** | sandbox fail-closed | [`ADR`](blueprint-upgrades/UBX-002-sandbox-fail-closed/ADR.md) · [`delta`](blueprint-upgrades/UBX-002-sandbox-fail-closed/architecture-delta.md) · [`migration`](blueprint-upgrades/UBX-002-sandbox-fail-closed/migration-path.md) · [`verification`](blueprint-upgrades/UBX-002-sandbox-fail-closed/verification-plan.md) |
| **UBX-003** | secrets fail-closed | [`ADR`](blueprint-upgrades/UBX-003-secrets-fail-closed/ADR.md) · [`delta`](blueprint-upgrades/UBX-003-secrets-fail-closed/architecture-delta.md) · [`migration`](blueprint-upgrades/UBX-003-secrets-fail-closed/migration-path.md) · [`verification`](blueprint-upgrades/UBX-003-secrets-fail-closed/verification-plan.md) |
| **UBX-004** | root-of-trust provider-neutral | [`ADR`](blueprint-upgrades/UBX-004-root-of-trust-provider-neutral/ADR.md) · [`delta`](blueprint-upgrades/UBX-004-root-of-trust-provider-neutral/architecture-delta.md) · [`migration`](blueprint-upgrades/UBX-004-root-of-trust-provider-neutral/migration-path.md) · [`verification`](blueprint-upgrades/UBX-004-root-of-trust-provider-neutral/verification-plan.md) |
| **UBX-005** | distributed real coordination | [`ADR`](blueprint-upgrades/UBX-005-distributed-real-coordination/ADR.md) · [`delta`](blueprint-upgrades/UBX-005-distributed-real-coordination/architecture-delta.md) · [`migration`](blueprint-upgrades/UBX-005-distributed-real-coordination/migration-path.md) · [`verification`](blueprint-upgrades/UBX-005-distributed-real-coordination/verification-plan.md) |
| **UBX-006** | perf gate normalization | [`ADR`](blueprint-upgrades/UBX-006-perf-gate-normalization/ADR.md) · [`delta`](blueprint-upgrades/UBX-006-perf-gate-normalization/architecture-delta.md) · [`migration`](blueprint-upgrades/UBX-006-perf-gate-normalization/migration-path.md) · [`verification`](blueprint-upgrades/UBX-006-perf-gate-normalization/verification-plan.md) |
| **TEMPLATE** | 新包模板 | [`ADR`](blueprint-upgrades/TEMPLATE/ADR.md) · [`delta`](blueprint-upgrades/TEMPLATE/architecture-delta.md) · [`migration`](blueprint-upgrades/TEMPLATE/migration-path.md) · [`verification`](blueprint-upgrades/TEMPLATE/verification-plan.md) |

### 4.7 `kernel-spec/`（15）— 14 内核规范

| 文件 | 说明 |
|---|---|
| [`kernel-spec/README.md`](kernel-spec/README.md) | 索引 + **诚实边界**：14 篇清单（真实实现类/行号）· 可观测入口 |
| [`kernel-spec/{audit,capability,context,evaluation,event,execution,identity,memory,network,plugin,policy,resource,security,trust}.md`](kernel-spec/) | 14 篇内核规范，各含统一「诚实状态头」 |

> ⚠️ **定位**：本目录是「**设计意图 / 目标态契约 + 已核对现状**」，**不是**现状规范。
> 各篇文首「诚实状态头」中的类名与行号已逐条对代码验证；未标注行号的段落
> （尤其缺口清单）须以代码实测为准。

### 4.8 `spec/`（12 + `incoming/` 6）— 权威规格

| 文件 | 说明 |
|---|---|
| [`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md) | 🔴 **宪法层单一入口**：索引 + 冲突裁决 C1–C12 |
| [`spec/KERNEL-CANON.md`](spec/KERNEL-CANON.md) | 🔴 **Kernel 唯一权威清单**（计数用运行时注册表，非目录数） |
| [`spec/CAPABILITY-REGISTRY.md`](spec/CAPABILITY-REGISTRY.md) | 能力注册表：LHX-C-NNN 体系与真实状态 |
| [`spec/MASTER-SPEC-v3.0.md`](spec/MASTER-SPEC-v3.0.md) | 目标规格（To-Be） |
| [`spec/DEFINITION-LOCK-STATUS.md`](spec/DEFINITION-LOCK-STATUS.md) | 宪法缺失声明与引用重定向 |
| [`spec/GAP-MIGRATION-MATRIX.md`](spec/GAP-MIGRATION-MATRIX.md) | As-Built → To-Be 对账 |
| [`spec/KERNEL-DOD-AUDIT.md`](spec/KERNEL-DOD-AUDIT.md) | 内核 DoD 七维审计 |
| [`spec/AI-LAYER-DOD-AUDIT.md`](spec/AI-LAYER-DOD-AUDIT.md) | 能力层 DoD 审计 |
| [`spec/AI-LAYER-OBSERVABILITY.md`](spec/AI-LAYER-OBSERVABILITY.md) | 能力层可观测性 |
| [`spec/POLICY-ENFORCEMENT-DESIGN.md`](spec/POLICY-ENFORCEMENT-DESIGN.md) | 策略执行设计 |
| [`spec/REMEDIATION-PLAN.md`](spec/REMEDIATION-PLAN.md) | 修复计划 |
| [`spec/REPO-LINE-CONVERGE.md`](spec/REPO-LINE-CONVERGE.md) | 仓库线索收敛计划 |
| [`spec/incoming/`](spec/incoming/) | ⚠️ **已被取代的合并草案**（6 篇，由同目录 `MERGE-LEDGER.md` 登记合并过程） |

### 4.9 `operations/`（5）· `product/`（7）· `l10k/`（1）· `security/`（1）· `decisions/`（1）

| 文件 | 说明 |
|---|---|
| [`operations/LAUNCH-CRITERIA.md`](operations/LAUNCH-CRITERIA.md) | 🔴 **「上线」的判据**（专治「代码有了就算完成」） |
| [`operations/production-runbook.md`](operations/production-runbook.md) | 生产运维手册 |
| [`operations/runbook.md`](operations/runbook.md) | 运行手册 |
| [`operations/health-check-spec.md`](operations/health-check-spec.md) | 健康检查规范 |
| [`operations/scenario-chatbot.md`](operations/scenario-chatbot.md) | 场景案例 |
| [`product/PRODUCT-READINESS.md`](product/PRODUCT-READINESS.md) | 🔴 **产品门禁 P1–P10**（有代码 ≠ 可用） |
| [`product/KERNEL-PRODUCT-CAPABILITY.md`](product/KERNEL-PRODUCT-CAPABILITY.md) | 14 内核逐个产品成熟度 |
| [`product/PM-PRD-v3.0.md`](product/PM-PRD-v3.0.md) | 产品需求 v3.0 |
| [`product/Designer-UIUX-v3.0.md`](product/Designer-UIUX-v3.0.md) | UI/UX 设计 v3.0 |
| [`product/capability-traceability.md`](product/capability-traceability.md) | 能力追溯矩阵 |
| [`product/LOCAL-RUN.md`](product/LOCAL-RUN.md) | 本地运行 |
| [`product/console-clickthrough-spec.md`](product/console-clickthrough-spec.md) | 控制台点击贯通规格（7 字段可追溯，EAP 01 的判据单一真相） |
| [`l10k/test-design.md`](l10k/test-design.md) | L10K 基准设计（基线数据在根 `l10k-baseline.yaml`） |
| [`security/shell-adapter-threat-model.md`](security/shell-adapter-threat-model.md) | Shell 适配器威胁模型 |
| [`decisions/OPEN-DECISIONS.md`](decisions/OPEN-DECISIONS.md) | 悬而未决决策登记 |

### 4.10 `ci/`（1）— CI 门禁豁免登记

| 文件 | 说明 |
|---|---|
| [`ci/gate-exemptions.md`](ci/gate-exemptions.md) | gate 豁免登记册：**为什么** 4 条 meta-guardrail 断言保持红色、各自归属、以及转绿条件（不伪装成"通过"） |

### 4.11 `governance/`（1）— 控制声明裁决登记

| 文件 | 说明 |
|---|---|
| [`governance/CONTROL-CLAIM-LEDGER.md`](governance/CONTROL-CLAIM-LEDGER.md) | control-claim 裁决登记册（三态判据 FALSIFIED/NOT VERIFIED/PASS + 更正规则，ratcheted，由 `lint_control_claims.py` 接线 CI） |

### 4.12 `archive/`（7）— ⚠️ 历史归档，**保持原貌**

| 文件 | 说明 |
|---|---|
| [`archive/Y1_AUDIT_REPORTS.md`](archive/Y1_AUDIT_REPORTS.md) | Y1 审计报告合集 |
| [`archive/PHASE_ACCEPTANCE_REPORTS.md`](archive/PHASE_ACCEPTANCE_REPORTS.md) | Phase 验收报告合集 |
| [`archive/EXECUTION_AND_DECISIONS.md`](archive/EXECUTION_AND_DECISIONS.md) | 执行与决策记录合集 |
| [`archive/PLANS_ROADMAP.md`](archive/PLANS_ROADMAP.md) | 规划与路线图合集 |
| [`archive/drafts/`](archive/drafts/) | ⚠️ **DEPRECATED** 草稿 3 篇（已被正式版取代） |

> **归档原则**（该目录自身声明）：`archive/` 内的历史文档**保持原貌**，正文中的
> 文件路径为整理前的原始路径，**不作改动**。其中的 `file:///d:/...` 绝对链接是
> "当时路径指向何处"的忠实记录 —— 为了让 linter 变绿而改写历史快照，是**更坏的谎**。
> 查现行路径请看本索引。

---

## 5. 门禁：本文档的可核验性

本索引不是"写得好看就算"。以下机械检查在 CI `guardrails` job 中运行，
**任何一条变红都说明文档在骗读者**：

| 门禁 | 保证什么 | 抓过的真实缺陷 |
|---|---|---|
| [`verify_docs_index_coverage.py`](../scripts/verify_docs_index_coverage.py) | 本索引**逐一列全** 125 篇活跃文档；不虚列；目录结构块与真实目录一致 | 旧版只覆盖 **43/121（35.5%）**，且完全遗漏 `autonomous/`（含治理章程）与 `blueprint-upgrades/` |
| [`verify_doc_links.py`](../scripts/verify_doc_links.py) | 活跃文档面的**每条相对链接都可达** | `ARCHITECTURE.md` 的"读者应该先读"入口是死链 |
| [`verify_doc_count_claims.py`](../scripts/verify_doc_count_claims.py) | KERNEL-CANON 的**每个计数可从 HEAD 复算**，且合计与表格自洽 | KERNEL-CANON v1.0：14/14 行数全错，且合计与自身表格差 190 行 |
| [`verify_openapi_contract.py`](../scripts/verify_openapi_contract.py) | 公布的 API 契约 **等于运行时真实契约** | `openapi.yaml` 声明 6 条全不存在，漏掉全部 58 条真实路径 |
| `lint_audit_claims.py` | 审计/合规声明必须带 UNVERIFIED 等诚实限定 | HC-01/HC-09/HC-10 的无限定声明 |

> ⚠️ **已知覆盖缺口（不掩饰）**：`lint_audit_claims.py` **只扫 markdown**，
> **看不见 `.py` docstring**。本项目最严重的一类文档缺陷恰恰在 docstring 里
> —— 「描述了一个从未存在的功能」。补上这个面是待办。

---

## 6. 本索引的诚实边界

- **覆盖完整 ≠ 阅读顺序最优**。门禁保证「没有文档被遗漏、没有路径是虚构的」，
  **不保证** §1 的阅读顺序是唯一正确的顺序，也不保证各篇摘要准确。
- **本索引自身也可能过时**。它由门禁守的是「覆盖与链接」，不是「内容正确」。
  若发现某篇摘要与该文实际内容不符，请以**该文本身**为准并回报。
- **计数会漂移**。本索引中出现的数字只在标注了 commit / 日期时可复现。
  引用任何数字前，请回到 §5 的门禁复核。