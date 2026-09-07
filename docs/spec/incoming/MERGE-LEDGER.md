# 框架合并台账（MERGE LEDGER）

> **状态**：✅ **已合并** — 2026-09-06 用户下达"开始"指令后完成
> **产出**：
> - [`../UNIFIED-BLUEPRINT.md`](../UNIFIED-BLUEPRINT.md) — 统一蓝图（索引 + 裁决 + 映射）
> - [`../REMEDIATION-PLAN.md`](../REMEDIATION-PLAN.md) — 完善修复清单（R1–R10，待批准）
> - [`../DEFINITION-LOCK-STATUS.md`](../DEFINITION-LOCK-STATUS.md) — 方案 A 已证伪并关闭
> **建立时间**：2026-09-06
> **负责人**：鎏灏长期 Principal Engineer
>
> **合并方式说明**：统一蓝图**不复制任何原文**，只做三件事——
> ① 文档分层与仲裁优先级 ② 命名空间隔离（解决节号撞车）③ C1–C12 冲突裁决 + 附录映射表。
> 各原文保持单一真相源（SSOT）。理由：缺的是裁决，不是又一份 100KB 副本。

---

## 0. 接收状态

| # | 框架 | 版本 | 节数 | 归档位置 | 状态 |
|---|------|------|------|----------|------|
| 01 | LIUHAO X v3.0 DEFINITION LOCK | v3.0 | 95 节 | `incoming/FRAMEWORK-01-v3.0-DEFINITION-LOCK.md` | 已归档，未合并 |
| 02 | LIUHAO X v3.0 DEFINITION LOCK（变体 B） | v3.0 | 94 节 | `incoming/FRAMEWORK-02-v3.0-DEFINITION-LOCK-B.md` | 已归档，未合并 |
| 03 | TEN INTELLIGENCE ARCHITECTURES UNIFIED — REAL ENGINEERING MASTER BLUEPRINT | **v2.0** | 92 节 | `incoming/FRAMEWORK-03-v2.0-MASTER-BLUEPRINT.md` | ✅ 已归档（**已修正 2 处归档缺陷**），未合并 |
| 04 | 同上（标题 ARCHITECTURES） | **v2.0** | 92 节 | `incoming/FRAMEWORK-04-v2.0-MASTER-BLUEPRINT-B.md` | ✅ 已归档，未合并。**≡ #03（逐字等价）** |
| 05 | 同上 | **v2.0** | 92 节 | `incoming/FRAMEWORK-05-DUPLICATE-RECORD.md` | ✅ 归档为**重复声明**，不收录全文 |

**⚠️ 发现 1**：#01 与 #02 **标题完全相同**（均为 LIUHAO X v3.0 DEFINITION LOCK），
但是 95 节 vs 94 节的**两个变体**，章节划分与内容差异显著（详见 C8）。

**⚠️ 发现 2（本次）**：#03 / #04 / #05 **是同一份文档投递了三次**。
经 `diff` 逐行验证：#03 与 #04 正文仅差 **2 处引号字符**（中文弯引号 vs 直角引号），
无语义差异；#05 与 #04 逐节一致，仅 §51/§58 代码块在粘贴时被压平为单行。→ **详见 C12**

**去重后的真实输入**：

| 组 | 文档 | 实际份数 | 合并权重 |
|---|---|---|---|
| A 组 | v3.0 Definition Lock（#01 95节 / #02 94节） | 2 份**变体** | 需选定主干 |
| B 组 | v2.0 Master Blueprint（#03/#04/#05） | **1 份** | 1 |

**重要含义**：#03 的 17-Phase / 无 Trust Kernel / L0–L9 架构分层等偏离，
**不存在"三份印证"** —— 它只是同一份文档投递三次，不得因出现次数多而提高权重。

**归档缺陷自纠（本次修复）**：
1. #03 标题 `ARCHETYPES` → `ARCHITECTURES`（我归档时的笔误，已改回原文）
2. #03 §27 组织树多出一行 `├── Teams`（#02 §40 内容串入，已删除）

**用户原话**："加多几个框架" → 是否还有 #06+ 待确认。

---

## 1. 仓库现有权威文档清单（合并基准）

| 文件 | 位置 | 规模 | 权威性 |
|------|------|------|--------|
| KERNEL-CANON.md | `docs/spec/` | 14 kernel | **最高**（代码实测佐证） |
| MASTER-SPEC-v3.0.md | `docs/spec/` | 221 节 | 高（含 221 节原文） |
| CAPABILITY-REGISTRY.md | `docs/spec/` | — | 高 |
| DEFINITION-LOCK-STATUS.md | `docs/spec/` | — | 现状声明（原件缺失） |
| capability-registry.yaml | 根目录 | 316 行 / 14 能力 | **可信源** |
| implementation-status.yaml | 根目录 | — | **假数据，不可信** |
| Definition Lock 原件 | `D:\LiuHao-AI-OS\LIUHAO-X-V3.0-DEFINITION-LOCK.md` | §1-§122 | **缺失** |

---

## 2. 关键冲突清单（C1–C12）

> 每条只做**事实对照 + 证据**，不给修复方案（修复方案等"开始"后再提）。

### C1 — 节号体系四重冲突【最严重·阻塞级】

同一条款号在不同文档中指向完全不同的内容。

| 文档 | 节数 | `§112` 的实际含义 |
|------|------|-------------------|
| 框架 01（新） | §1–§95 | 不存在（未到 112） |
| 框架 02（新，变体 B） | §1–§94 | 不存在（未到 112） |
| MASTER-SPEC-v3.0.md | §0–§220 | **L10K / VHL** |
| Definition Lock 原件（缺失） | §1–§122 | **十二核心 Kernel**（代码 docstring 佐证） |

**证据**：
- `MASTER-SPEC-v3.0.md:195` → `## 12. L10K / VHL（§112-116）`
- `src/kernels/security/__init__.py` docstring → "依据 Definition Lock §112"（指十二核心 Kernel）
- `capability-registry.yaml:12,34` → `traceability: ["Definition Lock §112", ...]`（追溯 Kernel，但 §112 在 MASTER-SPEC 是 L10K）

**后果**：追溯链指向错误内容，验证链根节点不可查。

**待决策**：以哪一套节号为准？还是废弃节号、改用语义化锚点（如 `KERNEL-CANON#identity`）？

---

### C2 — Kernel 数量：12 vs 14

| 来源 | 数量 | 差异 |
|------|------|------|
| 框架 01 §8 FINAL KERNEL | 12 | 漏 audit、plugin |
| MASTER-SPEC §6 CORE KERNEL | 12 | 漏 audit、plugin |
| `src/kernels/` 实测 | **14** | ← 真实 |
| KERNEL-CANON.md | **14** | ← 权威 |
| 代码 docstring 结构 | 14 | §112(12) + §113(audit) + §115(plugin) |

**证据**：
- `KERNEL-CANON.md:18` → "Master Spec §6 | 12 | 漏 audit、plugin"
- `DEFINITION-LOCK-STATUS.md:47` → "12 (§112) + 1 (§113) + 1 (§115) = 14 = src/kernels/ 实际目录数"

**倾向（leaning）**：以 14 为准（代码 + KERNEL-CANON 双佐证），框架 01 §8 需补 audit / plugin。

**待决策**：确认以 14 kernel 为权威，框架 01 §8 相应修订？

---

### C3 — DoD 完成定义：三套标准并存

| 来源 | 标准 |
|------|------|
| 项目当前（CODEX-CONTRACT §5，KERNEL-CANON §4.2 引用） | 6 项：Implemented + Tested + Observable + Permissioned + Audited + Documented |
| 框架 01 §72 RELEASE REQUIREMENT | **7 项**：上述 6 项 + **Policy Controlled** |
| MASTER-SPEC §117-118 | 12 问 + 11 道 Release Gate（另一套） |

**差异点**：项目当前 DoD **缺 `Policy Controlled`**。

**证据**：
- `KERNEL-CANON.md:126` → "按 CODEX-CONTRACT.md §5 的完成定义（Implemented + Tested + Observable + Permissioned + Audited + Documented）"（6 项）
- 框架 01 §72 → 7 项，含 Policy Controlled

**待决策**：DoD 统一为 7 项？还是保留 6 项 + MASTER-SPEC 的 Gate 作为流程门禁（分层）？

---

### C4 — Capability ID 格式不匹配

| 来源 | 格式 |
|------|------|
| 框架 01 §16 | `LHX-U-xxx` / `LHX-V-xxx` / `LHX-A-xxx` / `LHX-E-xxx` / `LHX-F-xxx` / `LHX-J-xxx` / `LHX-JC-xxx` / `LHX-K-xxx` / `LHX-N-xxx` / `LHX-Z-xxx`，统一去重 `LHX-C-xxx` |
| capability-registry.yaml 实际 | `context_compression`、`capability_registry`（snake_case 描述性） |

**证据**：
- `capability-registry.yaml:5` → `- id: "context_compression"`
- `capability-registry.yaml:29` → `- id: "capability_registry"`

**附带问题**：实际注册表**缺 `Source` 字段**（无法追溯 U/V/A/E/F/J/JC/K/N/Z/C 十源归属），而框架 01 §15 要求 16 个字段含 Source。

**待决策**：迁移到 LHX 结构化 ID？还是保留现有 ID + 新增 `source` 字段？

---

### C5 — Status 虚标（违反框架 01 §18 / §72）

框架 01 §18 明确："不得将 PLANNED 伪装成 IMPLEMENTED"。
框架 01 §72 要求 7 项全满足才允许标记 IMPLEMENTED。

**实际**：
- `capability-registry.yaml` 中多个能力标记 `status: "IMPLEMENTED"`
- 但同一条目内 `audited: false`、`tested: false`、`test_coverage: "0/0"`
- `KERNEL-CANON.md:130` → "标记 IMPLEMENTED 的 9 个…应降级为 PARTIALLY_IMPLEMENTED"

**待决策**：确认降级规则（IMPLEMENTED → PARTIALLY_IMPLEMENTED）？

---

### C6 — Phase 编号错位

| 来源 | Phase 定义 |
|------|-----------|
| 框架 01 §75 / MASTER-SPEC §177-198 | Phase 2 = Kernel，Phase 3 = **Agent Runtime**，Phase 4 = **Model Gateway** |
| 项目当前自称 | "**Phase 4** — Agent Runtime, Wave 2" |

**冲突**：项目自称 Phase 4，但内容是 v3.0 的 Phase 3（Agent Runtime）；而 v3.0 的 Phase 4 是 Model Gateway（项目称已完成）。

**附带**：capability-registry.yaml 使用 "Phase 2 Wave 1"（Kernel），与"Phase 4 Wave 2"并存 → 项目内部 Phase 表述不统一。

**待决策**：统一 Phase 编号口径（以 v3.0 为准 + Wave 子编号）？

---

### C7 — Definition Lock 原件缺失（框架 01 无法直接补全）

- 声称路径 `D:\LiuHao-AI-OS\LIUHAO-X-V3.0-DEFINITION-LOCK.md` **不存在**
- 全库 **16 处以上**引用其条款（代码 docstring / implementation-status.yaml / capability-registry.yaml / 架构文档 / 验收报告 / blueprint-requirements.yaml）
- 代码引用至 **§122**（条款总数）
- **框架 01 只有 95 节** → 不等于缺失原件

**结论**：框架 01 **不能直接充当宪法正本**，否则 §96–§122 仍然缺失，且 §112 语义会与代码冲突（见 C1）。

**待决策**：
- (a) 框架 01 作为"精简版 Definition Lock"归档，另寻 122 节原件？
- (b) 以框架 01 为骨架 + 从 MASTER-SPEC 221 节回填 §96–§122？
- (c) 用户手上有 122 节原件，稍后提供？

---

### C8 — 框架 #01 与 #02 是同一文档的两个变体【新增·高】

**标题完全相同**（均为 "LIUHAO X v3.0 DEFINITION LOCK"），但 95 节 vs 94 节，内容差异显著。

| 维度 | #01（95 节） | #02（94 节） |
|---|---|---|
| §1 Primary Use | Codex Master Engineering Specification | **+ Product architecture** |
| §4 形式 | 15 条编号列表 | key-value 块 |
| §7 内容 | **DEDUPLICATED CORE**（34 能力） | **FINAL ARCHITECTURE** |
| §21–§30 | 十源各一节 ENGINEERING LAYER | 无（改为 §28/§32/§34/§36/§37/§39/§41） |
| #01 独有 | §42 WORLD MODEL / §43 WORLD INTERFACE / §44 SIMULATION / §62 PLUGIN / §63 DATA GOVERNANCE / §64 VERSIONING | — |
| #02 独有 | — | §14 IDENTITY KERNEL / §15 AGENT IDENTITY / §18 CONTROLLED SPAWNING / §29 PLANNING ENGINE / §38 EVENT ENGINE / §48 BUDGET / §50 GOVERNANCE / **§56 API 架构** / §58 REDIS / §59 QDRANT / §60 对象存储 / **§61–63 MODEL GATEWAY·ROUTER·REGISTRY** / **§70 技术基线** / §72 韧性 / §73 分布式控制 / §74 备份DR / §76 安全测试 / §77–78 评测 |
| §91 结构 | 扁平 FINAL LOCKED STATEMENT | **§91.1–§91.17 子节** |
| §92 结构 | 整块 DIRECTIVE | **§92.1–§92.13 子节** |
| 结尾 | §95 | §93 MASTER LOCK + §94 FINAL FORM |

**倾向**：#02 工程细节更丰富（含 API / DB / Redis / Qdrant / Model Gateway / 技术基线 / 韧性 / DR / 评测），
#01 保留十源分层与 Plugin / 数据治理 / 版本化。**合并时宜以 #02 为主干，#01 独有章节回填。**

**待决策**：以 #02 为主干？还是用户指定 #01？

---

### C9 — Kernel：Trust vs Runtime【新增·高】

| 来源 | 12 Kernel 列表差异 |
|---|---|
| #01 §8 / #02 §8 | 含 **Trust**，**无 Runtime** |
| #03 §4 | 含 **Runtime**，**无 Trust** |
| 仓库实测 14 kernel | **含 `trust`**（`src/kernels/trust/`），**无 `runtime` kernel** |

**结论**：#01/#02 与代码一致；#03 的 Kernel 列表与仓库实际偏离。

**待决策**：确认 Kernel 列表以 #01/#02（含 Trust）为准，#03 的 "Runtime" 视为 Agent Runtime 层（非 kernel）？

---

### C10 — Phase：21 vs 17【新增·高】

| 来源 | Phase 数 | 关键差异 |
|---|---|---|
| #01 §75 / #02 §85 | **21** | 含独立 Model Gateway(P4)、Policy/Approval(P7)、Execution(P8)、Production Hardening(P21) |
| #03 §85 | **17** | Policy/Approval 并入 Kernel；Capability-Tool 与 World 合并为 Tool-World(P6)；**L-Core 提前到 P7**（#01/#02 为 P9）；**无独立 Production Hardening** |
| MASTER-SPEC §177-198 | 21 | 与 #01/#02 一致 |

**倾向**：以 21 Phase 为准（#01/#02/MASTER-SPEC 三方一致，#03 为少数）。

**待决策**：确认 21 Phase？

---

### C11 — L0–L7 编号语义撞车【新增·高】

同一套 `L0–L7` 编号在两份框架里含义完全不同：

| 来源 | L0–L7 含义 |
|---|---|
| #01 §35 / #02 §25 | **Memory Scope**：L0 System / L1 Human / L2 Organization / L3 Workspace / L4 Team / L5 Agent / L6 Task / L7 Session |
| #03 §3 | **架构分层**：L0 Human Interface / L1 L-Core / L2 Intelligence Fabric / L3 Agent Runtime / L4 Organization / L5 Network / L6 World Interface / L7 Sovereignty-Trust-Governance（另有 L8 Economy / L9 Evaluation） |

**且仓库已按 Memory Scope 实现**：`KERNEL-CANON.md` §1 载明 `identity` 与 `memory` kernel 均含 "L0-L7 作用域过滤"。

**结论**：L0–L7 **只能保留 Memory Scope 用法**；#03 的架构分层若需保留，必须改用其他编号（如 A0–A9 或 Layer-0…Layer-9），否则与已实现代码冲突。

**待决策**：确认 L0–L7 = Memory Scope 唯一占用；#03 架构分层改号？

---

### C12 — #03/#04/#05 为同一文档重复投递【新增·中·影响权重评估】

**事实**：#03、#04、#05 三份投递内容相同，均为
`TEN INTELLIGENCE ARCHITECTURES UNIFIED / REAL ENGINEERING MASTER BLUEPRINT v2.0`（§0–§92 + FINAL STATEMENT）。

**验证方法**：`diff` 逐行比对（去掉各自归档头后比正文）。

**验证结果**：

| 对比 | 差异 |
|---|---|
| #03 vs #04 | 仅 2 处**引号字符**：`"电影 AI"`（§0）、`"向量相似"`（§61）→ 弯引号 vs 直角引号。**无语义差异** |
| #05 vs #04 | 逐节一致；仅 §51 AGENT FILESYSTEM、§58 API ARCHITECTURE 两个代码块在粘贴时被压平为单行。**格式差异，非内容差异** |

**判定**：三份投递 = **1 份独立文档**。

**对合并的影响（重要）**：

1. B 组在合并中的**权重为 1**，不因出现三次而提高。
2. #03 相对 #01/#02 的三处偏离（17-Phase、无 Trust Kernel、L0–L9 架构分层）
   **不构成"三份文档印证"**，仍按 1 份文档的少数意见处理。
3. 正本选用：以 **#03/#04 的多行代码块形态**为准（#05 单行形态是粘贴产物）。

**归档处理**：#05 不收录全文，改为 `FRAMEWORK-05-DUPLICATE-RECORD.md` 记录判定依据与差异，保证可追溯。

**归档缺陷自纠**（归档保真原则：逐字收录，不增不减）：

| 缺陷 | 位置 | 修正 |
|---|---|---|
| 标题笔误 `ARCHETYPES` → 应为 `ARCHITECTURES` | #03 L1 / L47 | ✅ 已改回原文（§89 的 "TEN CAPABILITY ARCHETYPES" 是原文，保留） |
| 多出一行 `├── Teams`（#02 §40 内容串入） | #03 §27 组织树 | ✅ 已删除，组织树恢复为 9 项（Goals/Members/Roles/Departments/Policies/Budget/Memory/Capabilities/KPI） |

**待决策**：无 —— 此项为事实认定，不需用户拍板。

---

## 3. 合并前置待决策（阻塞项）

> **2026-09-06 更新**：用户下达"开始"后，以下全部已裁决。
> 裁决结果写入 [`UNIFIED-BLUEPRINT.md §3`](../UNIFIED-BLUEPRINT.md)。
> **标注"✅ 裁决"的为有代码事实支撑、我直接定的；标注"⚠️ 待确认"的为用户偏好项，建议值已生效但可推翻。**

| # | 问题 | 裁决 | 状态 |
|---|------|------|------|
| Q1 | 节号体系以哪套为准（C1） | **命名空间隔离**：`DL:` / `MS:` / `UB:` 并存 + 稳定锚点 `UB-A1`… | ✅ 裁决（§2） |
| Q2 | Kernel 以 12 还是 14 为准（C2） | **14** | ✅ 裁决（代码实测） |
| Q3 | DoD 统一为几项（C3） | **7 项**（补 `Policy Controlled`） | ✅ 裁决（#01/#02 一致） |
| Q4 | Capability ID 是否迁移到 LHX（C4） | **是**，双写过渡（`legacy_id` 保留） | ⚠️ 待确认（改动大） |
| Q5 | Status 是否批量降级（C5） | **是**，9 个 → `PARTIALLY_IMPLEMENTED` | ⚠️ 待确认（改 yaml） |
| Q6 | Phase 编号口径（C6） | **21 Phase**；项目"Phase 4"表述需对齐 | ⚠️ 待确认（R7 二选一） |
| Q7 | 框架 01 是否宪法正本 / 122 节原件（C7） | **均否**。原件 git 查无此文件，不可恢复 | ✅ 结案（已验证） |
| Q8 / Q14 | #01 vs #02 谁当主干（C8） | **#02 为主干**，#01 独有章节回填 | ⚠️ 待确认（建议值已生效） |
| Q9 | Kernel 含 Trust 还是 Runtime（C9） | **含 Trust**，Runtime 属 Agent Runtime 层非 kernel | ✅ 裁决（代码实测） |
| Q10 | Phase 用 21 还是 17（C10） | **21** | ✅ 裁决（三方对一份） |
| Q11 | L0–L7 语义（C11） | **Memory Scope 独占**；#03 架构分层改 `AL0–AL9` | ✅ 裁决（代码已实现） |
| ~~Q12~~ | ~~是否还有 #04/#05~~ | ✅ 已关闭：与 #03 同一份（C12） | ✅ 结案 |
| Q13 | 是否还有 #06+ | 未收到即视为无；若后续收到走增量合并 | ⚠️ 待确认 |
| Q15 | 逐条拍 vs 先出草案 | 已按"先出完整草案"执行（UNIFIED-BLUEPRINT） | ✅ 结案 |

---

## 4. 纪律声明（2026-09-06 执行后更新）

**已执行**：
- ✅ 产出 [`UNIFIED-BLUEPRINT.md`](../UNIFIED-BLUEPRINT.md)（新增文件，未覆盖任何既有文档）
- ✅ 产出 [`REMEDIATION-PLAN.md`](../REMEDIATION-PLAN.md)（修复清单 R1–R10）
- ✅ 更新 `DEFINITION-LOCK-STATUS.md`：方案 A 标记 ❌ 已验证失败并附 git 证据（**唯一被动过的既有文档**）
- ✅ 本台账裁决回写

**未执行（严格遵守）**：
- ❌ **未改** `KERNEL-CANON.md` —— 保留为 Kernel 唯一权威
- ❌ **未改** `MASTER-SPEC-v3.0.md` —— 保留为 221 节真相源
- ❌ **未改** `CAPABILITY-REGISTRY.md`
- ❌ **未改** `GAP-MIGRATION-MATRIX.md`
- ❌ **未改** `capability-registry.yaml` —— R1/R2/R3 需先跑消费方前置检查
- ❌ **未改** `CODEX-CONTRACT.md` —— R5 待批准
- ❌ **未改** 任何 `src/` 代码（含 kernel docstring）
- ❌ **未删** `implementation-status.yaml`（R9 建议改名/删除，待批准）

**原则**：合并产出用**新增文件**承载，不覆盖既有文档；
唯一例外是 `DEFINITION-LOCK-STATUS.md` 中已被证伪的方案 A（属事实修正，可逆性高，且留着会持续误导）。
