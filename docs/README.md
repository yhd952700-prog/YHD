# LIUHAO X — 文档索引

> **最近整理**：2026-09-06 v3
> **整理内容**：
> 1. docs 下 11 个子目录精简为 5 个大类（合并 9 个单文件目录，统一命名）
> 2. **新增 `ARCHITECTURE.md`**：架构总纲·导航入口（替代丢失的 `LIUHAO-X-V3.0-DEFINITION-LOCK.md`；宪法层单一入口见 `spec/UNIFIED-BLUEPRINT.md`）
> 3. **新增 `decisions/OPEN-DECISIONS.md`** 登记入口
> 4. `archive/drafts/` 3 个草稿标记 **⚠️ DEPRECATED**（已被正式版取代）
> 5. 导航表补充 spec/、decisions/、ARCHITECTURE.md 条目
> 6. 文档层级重构：增加「第 7 层 决策」
> 7. **文档最终收敛**：16 个重复工程文档 → 先合并到 `LIUHAO-X-v3.0-FINAL-ENGINEERING-MASTER-PLAN.md`，再于 2026-09-06 **并入 `spec/UNIFIED-BLUEPRINT.md`**（原文已删除，其 14 章映射见该文件 §6.1）
> **全量备份**：`D:\WorkBuddyFiles\LiuHao-docs-backup-20260906-140435`
> **文档整合**：`LIUHAO-X-v3.0-FINAL-ENGINEERING-MASTER-PLAN.md` 已并入 `spec/UNIFIED-BLUEPRINT.md` 并删除；宪法层单一入口即 UNIFIED-BLUEPRINT

---

## 按目的快速导航

| 我要… | 看这里 |
|---|---|
| **我是 Codex / AI，要写代码** | **[`CODEX-CONTRACT.md`](CODEX-CONTRACT.md)** ← 必读入口 |
| **统一蓝图（宪法层单一入口）** | **[`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md)** ← 所有架构/规格/裁决的唯一入口 |
| 查 Kernel 权威清单（14 个） | [`spec/KERNEL-CANON.md`](spec/KERNEL-CANON.md) |
| 查能力注册与真实状态 | [`spec/CAPABILITY-REGISTRY.md`](spec/CAPABILITY-REGISTRY.md) |
| 查从现状到目标的迁移路径 | [`spec/GAP-MIGRATION-MATRIX.md`](spec/GAP-MIGRATION-MATRIX.md) |
| 查目标规格（221 节原文） | [`spec/MASTER-SPEC-v3.0.md`](spec/MASTER-SPEC-v3.0.md) |
| 查 Definition Lock 缺失声明 | [`spec/DEFINITION-LOCK-STATUS.md`](spec/DEFINITION-LOCK-STATUS.md) |
| 查悬而未决决策 | [`decisions/OPEN-DECISIONS.md`](decisions/OPEN-DECISIONS.md) |
| 了解系统整体架构 | [`ARCHITECTURE.md`](ARCHITECTURE.md)（架构总纲，导航入口） |
| 查 14 Kernel 的权威清单 | [`spec/KERNEL-CANON.md`](spec/KERNEL-CANON.md) |
| 查 14 Kernel 接口定义 | [`architecture/kernels-interface.md`](architecture/kernels-interface.md) |
| 看现有代码到底有什么 | [`architecture/existing-codebase-audit.md`](architecture/existing-codebase-audit.md)（780 行全量审计） |
| 查权限系统怎么工作 | [`ARCHITECTURE.md`](ARCHITECTURE.md) §权限系统 |
| 看产品需求 / UI 设计 | [`product/PM-PRD-v3.0.md`](product/PM-PRD-v3.0.md)、[`product/Designer-UIUX-v3.0.md`](product/Designer-UIUX-v3.0.md) |
| 查能力追溯矩阵 | [`product/capability-traceability.md`](product/capability-traceability.md) |
| 上线部署 / 故障处理 | [`operations/production-runbook.md`](operations/production-runbook.md) |
| 查健康检查规范 | [`operations/health-check-spec.md`](operations/health-check-spec.md) |
| 了解 L10K 基准 | [`l10k/test-design.md`](l10k/test-design.md) |
| 第一次接触这个项目 | [`quickstart.md`](quickstart.md) |
| 找历史记录 / 验收报告 | [`archive/`](archive/)（4 个合集，含 60 份原文） |

---

## 目录结构

```
docs/
├── README.md              ← 本文件（索引）
├── CODEX-CONTRACT.md      Codex 执行契约（AI 编码代理必读入口）
├── quickstart.md          快速上手
├── architecture/          架构（7 篇）
├── product/               产品与设计（3 篇）
├── operations/            运维与场景（3 篇）
├── l10k/                  L10K 基准（1 篇）
├── spec/                  V3.0 目标规格（5 篇，2026-09-06 新增）
└── archive/               历史归档
    ├── Y1_AUDIT_REPORTS.md
    ├── PHASE_ACCEPTANCE_REPORTS.md
    ├── EXECUTION_AND_DECISIONS.md
    ├── PLANS_ROADMAP.md
    └── drafts/            已被正式版取代的草稿（3 篇）
```

---

## 一、架构 `architecture/`

| 文件 | 行数 | 说明 |
|---|---|---|
| [`Architect-Architecture-v3.0.md`](architecture/Architect-Architecture-v3.0.md) | 591 | **架构总纲**。8 Workstreams、14 Kernel 映射、数据流、NFR、风险、§13 权限系统附录 |
| [`kernels-interface.md`](architecture/kernels-interface.md) | 364 | 12 Kernel 接口定义 + 并行 Workstream 规则 + 汇合点（原 `liuhao-x-kernels-interface.md`） |
| [`existing-codebase-audit.md`](architecture/existing-codebase-audit.md) | 780 | 现有代码库 19 节全量审计，138 个 .py 文件的实测证据 |
| [`dependency-map.md`](architecture/dependency-map.md) | 183 | 模块依赖图、Kernel ↔ Workstream 映射 |
| [`gap-analysis.md`](architecture/gap-analysis.md) | 188 | 当前代码 ↔ Definition Lock 的差距分析（原 `architecture-gap-analysis.md`） |
| [`p7-baseline.md`](architecture/p7-baseline.md) | 122 | P7 架构基线，10 项要求自动验证（原 `P7-architecture-baseline.md`） |
| [`migration-matrix.md`](architecture/migration-matrix.md) | 161 | LiuHao-X 迁移矩阵（原 `migrations/liuhao-x-migration-matrix.md`） |

## 二、产品与设计 `product/`

| 文件 | 行数 | 说明 |
|---|---|---|
| [`PM-PRD-v3.0.md`](product/PM-PRD-v3.0.md) | 191 | 产品需求文档 v3.0（原 `requirements/`） |
| [`Designer-UIUX-v3.0.md`](product/Designer-UIUX-v3.0.md) | 543 | UI/UX 设计文档 v3.0（原 `design/`） |
| [`capability-traceability.md`](product/capability-traceability.md) | 109 | 12 Kernel × 10 DNA × 22 Phase 追溯矩阵（原 `capabilities/capability-traceability-matrix.md`） |

## 三、运维与场景 `operations/`

| 文件 | 行数 | 说明 |
|---|---|---|
| [`production-runbook.md`](operations/production-runbook.md) | 312 | 生产运维手册（原 `runbooks/production_runbook.md`） |
| [`health-check-spec.md`](operations/health-check-spec.md) | 42 | 健康检查规范（原 `infrastructure/`） |
| [`scenario-chatbot.md`](operations/scenario-chatbot.md) | 266 | 场景案例（原 `scenarios/chatbot.md`） |

## 四、L10K 基准 `l10k/`

| 文件 | 行数 | 说明 |
|---|---|---|
| [`test-design.md`](l10k/test-design.md) | 208 | L10K 测试设计（对应蓝图 §71-72）。基线数据在仓库根 `l10k-baseline.yaml` |

## 五、统一蓝图与规格 `spec/`（2026-09-06 新增/整合）

| 文件 | 说明 |
|---|---|
| **[`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md)** | **🔥 宪法层单一入口**：索引+裁决+映射（文档分层 / 命名空间规则 / 冲突裁决 C1–C12 / 14 章主题导航）。原 `LIUHAO-X-v3.0-FINAL-ENGINEERING-MASTER-PLAN.md` 已于 2026-09-06 并入本文件，原文删除 |
| [`spec/KERNEL-CANON.md`](spec/KERNEL-CANON.md) | **Kernel 唯一权威清单**：14 个（以 `src/kernels/` 代码为准），含与旧 K01–K12 / MS:§6 对照 |
| [`spec/CAPABILITY-REGISTRY.md`](spec/CAPABILITY-REGISTRY.md) | 能力注册表：LHX-C-xxx ID 体系、14 项真实状态（全部 PARTIALLY_IMPLEMENTED）、十源 DNA → 目标模块映射 |
| [`spec/GAP-MIGRATION-MATRIX.md`](spec/GAP-MIGRATION-MATRIX.md) | As-Built → To-Be 桥梁：Kernel 命名对照 / `src/`→`packages/` 迁移 / 21 Phase 进度 |
| [`spec/MASTER-SPEC-v3.0.md`](spec/MASTER-SPEC-v3.0.md) | 目标规格（To-Be）：第一部分工程提炼版速查 + 第二部分 221 节原文逐字收录 |
| [`spec/DEFINITION-LOCK-STATUS.md`](spec/DEFINITION-LOCK-STATUS.md) | 宪法缺失声明、§112/§113/§115 条款还原（代码 docstring 佐证）、引用重定向表 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | **架构总纲·导航入口**：14 Kernel × 8 Workstream × 十源 DNA 映射、硬规则摘要（宪法入口见 UNIFIED-BLUEPRINT） |

## 六、历史归档 `archive/`

| 文件 | 行数 | 合并自 |
|---|---|---|
| `Y1_AUDIT_REPORTS.md` | 1505 | 3 份 Y1 审计报告 |
| `PHASE_ACCEPTANCE_REPORTS.md` | 2766 | 29 份验收文档（Phase 0-9 + SEC 安全系列） |
| `EXECUTION_AND_DECISIONS.md` | 3634 | 22 份执行报告与决策记录 |
| `PLANS_ROADMAP.md` | 3000 | 6 份规划文档 |
| `drafts/ARCHITECTURE-v3.0-draft.md` | 188 | v3.0 架构草稿 → 已被 `architecture/Architect-Architecture-v3.0.md` 取代 **⚠️ DEPRECATED** |
| `drafts/UIUX-v3.0-draft.md` | 236 | v3.0 UIUX 草稿 → 已被 `product/Designer-UIUX-v3.0.md` 取代 **⚠️ DEPRECATED** |
| `drafts/PRD-v3.0-draft.md` | 173 | v3.0 PRD 草稿 → 已被 `product/PM-PRD-v3.0.md` 取代 **⚠️ DEPRECATED** |

> **归档原则**：`archive/` 内的历史文档**保持原貌**，正文中的文件路径为整理前的原始路径，不作改动。查现行路径请看本索引。

---

## 七、决策 `decisions/`

| 文件 | 说明 |
|---|---|
| `decisions/OPEN-DECISIONS.md` | 悬而未决登记（持续维护） |

---

## 八、架构总纲 `ARCHITECTURE.md`

| 文件 | 说明 |
|---|---|
| `ARCHITECTURE.md` | **架构导航入口**：14 Kernel × 8 Workstream × 十源 DNA 映射、硬规则摘要、文档层级 |

---

## 文档层级

```text
第 0 层  宪法层单一入口  spec/UNIFIED-BLUEPRINT.md（索引 + 冲突裁决 C1–C12 + 命名空间 + 主题映射；一切文档冲突以它为唯一准绳）
第 1 层  权威规格    spec/KERNEL-CANON.md · spec/CAPABILITY-REGISTRY.md · spec/MASTER-SPEC-v3.0.md
第 2 层  架构导航    ARCHITECTURE.md（门户，非权威）
第 3 层  正式文档    architecture/ · product/ · operations/
第 4 层  支撑文档    spec/GAP-MIGRATION-MATRIX.md · architecture/gap-analysis.md
第 5 层  草稿        archive/drafts/
第 6 层  历史归档    archive/ 四个合集
第 7 层  决策        decisions/OPEN-DECISIONS.md
```

---

## ⚠️ 当前已知问题（2026-09-06 核查）

在读这些文档时，请注意以下四点，否则会被误导：

**1. Definition Lock 本体缺失** ✅ **已结案（2026-09-12，R8 收口）** —— 原件判定为**永久不可考**，
引用一律改走 [`spec/UNIFIED-BLUEPRINT.md`](spec/UNIFIED-BLUEPRINT.md) 的锚点/带前缀节号；
最后 4 处不可判定节号已留档重定向，见 [`spec/DEFINITION-LOCK-STATUS.md`](spec/DEFINITION-LOCK-STATUS.md)。
`LIUHAO-X-V3.0-DEFINITION-LOCK.md` 不在仓库中，全库 16 处以上引用其 `§xx` 条款（capability-registry、implementation-status、gap-analysis 均引用）。

**2. Kernel 清单多套并存（已收敛）**
`src/kernels/` 实际有 **14 个** kernel（含 security、audit、plugin）。`capability-registry.yaml` 已于 2026-09-06 同步为 **14 项**（含 security/audit/plugin），状态统一为 `PARTIALLY_IMPLEMENTED`；`spec/KERNEL-CANON.md` 为**唯一权威清单**（旧 K01–K12 编号已废弃）。其余叙事文档若仍出现"12/13 个"旧口径，一律以 KERNEL-CANON 为准。

**3. ~~Kernel 层测试覆盖 7/14~~ ✅ 已解决（2026-09-11 实测更正）**
14 个 kernel 共 6,391 行代码。曾记录为"仅 7 个 kernel 有测试"，**该陈述已过时**：`tests/kernels/` 现有 **14 个 kernel 测试目录全部齐全**（audit / capability / context / evaluation / event / execution / identity / memory / network / plugin / policy / resource / security / trust），14 项能力状态已全部为 `IMPLEMENTED`（DoD 七维 14/14，见 [`spec/KERNEL-DOD-AUDIT.md`](spec/KERNEL-DOD-AUDIT.md) 与 [`spec/CAPABILITY-REGISTRY.md`](spec/CAPABILITY-REGISTRY.md)）。
**当前 CI 真实基线（run `34692597622`，2026-09-12）：1343 passed / 14 skipped / 0 failed**（**Python 3.11.16 与 3.12.14 双腿同值**：test job 自 Round 72 起为 `['3.11','3.12']` 矩阵，见 `.github/workflows/ci.yml`）—— 此前"1173 / 1141 / 1281 passed"及 run #69-74 的"全绿"均不可信（ci.yml 曾用 `|| echo` 吞退出码所致）。
> ✅ **版本分叉盲区已关闭（2026-09-12，Round 72）**：该基线由 CI 的 test job 矩阵 **Python 3.11.16 与 3.12.14 各自独立跑出，两条腿数值完全相同**（1343 / 14 / 0，21 warnings）。本机 venv 为 3.12.14，与 CI 的 3.12 腿版本一致，故此前「只在 3.12 暴露的差异本地可见、CI 不可见」的盲区不再成立 —— 见 `.github/workflows/ci.yml`。

**4. 状态文件假数据已修正（2026-09-06）**
`implementation-status.UNRELIABLE.yaml` 的 `tested: 14 / audited: 14` 与 `l10k-baseline.yaml` 的 `production_ready: true` 均为基于错误声明的判定，已分别修正为 `0 / 0` 与 `false`。修正前曾导致 L10K 虚报 28 个验证单元、3 个质量门禁误判 PASS。

详细诊断见：`D:\WorkBuddyFiles\LIUHAO-X-v3.0-状态核查诊断报告.md`
