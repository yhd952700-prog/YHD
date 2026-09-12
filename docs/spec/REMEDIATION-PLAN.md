# LIUHAO X — 完善修复清单（REMEDIATION PLAN）

> **建立日期**：2026-09-06 · **配套文档**：[`UNIFIED-BLUEPRINT.md`](UNIFIED-BLUEPRINT.md)
> **状态**：**待用户批准后才执行**。本文档只列缺陷、方案、风险，未改动任何生产文件。

---

## 执行状态复核（2026-09-11，逐条实测）

本清单建立后各条目陆续处置，但从未做过一次统一复核。下表为 **2026-09-11 逐条查证结果**（查的是仓库当前实际内容，不是当时的计划）。

| 编号 | 优先级 | 复核结论 | 证据 |
|---|---|---|---|
| R1 | P0 | ✅ **已完成** | `capability-registry.yaml` 14 能力状态均为 `IMPLEMENTED`，与 `CAPABILITY-REGISTRY.md` 一致 |
| R2 | P0 | ✅ **已完成** | capability ID 已带 `LHX-*` 前缀并含 `source` 字段 |
| R3 | P0 | ✅ **已完成** | 缺失的 3 个 kernel 已补入（现 14 个） |
| R4 | P1 | ✅ **已完成** | `DEFINITION-LOCK-STATUS.md` §4 方案 A 标注 ❌ 已关闭，默认改走 B+C |
| R5 | P1 | ✅ **已完成** | `CODEX-CONTRACT.md` §5 已含第 5 项 `Policy Controlled`（7 项 DoD） |
| R6 | P1 | ✅ **已完成** | `GAP-MIGRATION-MATRIX.md` 表 3 已改用九态枚举（`IMPLEMENTED` 等） |
| R7 | P1 | 🟡 **主体完成，有残留** | 项目自身进度表述已不再挂 "Phase 4" 编号；但 `docs/architecture/` 下 4 份历史分析文档仍按旧编号写作（"Phase 4 = Agent Runtime"），与 21-Phase 口径（Phase 4 = Model Gateway）冲突。见下方 §R7 残留 |
| R8 | P2 | 🟡 **未完成（保留为技术债）** | 活跃文档仍有大量裸 `§N`：`docs/spec` 590 处、`docs/architecture` 137 处。多数是文档内部章节自引用（`§3.4`），并非跨文档命名空间引用；真正的 DL/MS 歧义引用需逐个语义判断，无法机械替换。见下方 §R8 残留 |
| R9 | P2 | ✅ **已完成** | 已重命名为 `implementation-status.UNRELIABLE.yaml`（2026-09-06），旧名文件不存在 |
| R10 | P2 | ✅ **已完成** | `KERNEL-CANON.md` §1 已含 L0–L7 语义锁定防御说明（C11 裁决） |

**净结果：P0 3/3、P1 3/4（R7 主体完成）、P2 2/3（R9/R10 完成，R8 保留）**

### §R7 残留

`docs/architecture/` 下的 `existing-codebase-audit.md`、`gap-analysis.md`、`kernels-interface.md`、`migration-matrix.md`
是对**重构前老代码库**的分析记录，其中的 Phase 编号沿用了当时的口径。它们的问题不是"内容错"，
而是**编号体系与 21-Phase 路线图不一致**，单独阅读时会被误读。

**处置建议**：不批量改写历史分析结论（会丢失当时的语境），而是在这 4 份文件头部加一行口径说明。

### §R8 残留

原方案"活跃文档全部改为 `DL:§N` / `MS:§N`"的**前提不成立**：统计出的 590 处里，绝大多数是
文档**内部**章节自引用（如"见 §3.4"、"见 §5"），这类引用与 Definition Lock / Master Spec 命名空间无关。
真正需要消歧的是引用 DL 或 MS 章节号的裸写，而 `§112` 这类编号**在两套文档里都存在且含义不同**，
只能逐条语义判断，不能机械替换。

**处置建议**：按文件、按引用性质分批收敛，优先处理 `docs/spec/` 下被当作权威依据的文档。
在收敛完成前，`UNIFIED-BLUEPRINT.md` 附录 A 是唯一裁决口径。

---

## 评分口径

| 字段 | 含义 |
|---|---|
| **优先级 P0/P1/P2** | P0 = 影响安全或追溯链根节点；P1 = 影响正确性；P2 = 影响一致性 |
| **风险** | 改动可能造成的连带影响 |
| **可逆性** | 是否容易回滚 |

---

## R1【P0】capability-registry.yaml 状态造假

**文件**：`capability-registry.yaml`（根目录，316 行 / 14 能力）

**现状**：
```yaml
# 9 个能力标记 IMPLEMENTED，但同时：
tested: false
audited: false
```

**问题**：违反框架 #01 §18 / #02 §13 明令——"**不得将 PLANNED 伪装成 IMPLEMENTED**"。
按 `UNIFIED-BLUEPRINT.md` §7 的 7 项 DoD，`tested=false` 且 `audited=false` 的能力**不可能是 IMPLEMENTED**。

**修复方案**：
9 个能力（context / capability / event / execution / resource / policy / network / trust / evaluation）
状态由 `IMPLEMENTED` → `PARTIALLY_IMPLEMENTED`。

**风险**：
- 下游若有代码读 `status == "IMPLEMENTED"` 做门禁，降级后可能改变行为 → **需先 grep 确认消费方**
- 达标统计口径会变化（0/14 → 仍为 0/14，但"声称 9 个达标"的虚假印象被消除）

**可逆性**：高（改 yaml 字段值）

**前置检查**：`grep -rn "IMPLEMENTED" --include=*.py .` 确认消费方

---

## R2【P0】Capability ID 不符合规范（缺 LHX 前缀与 Source 字段）

**文件**：`capability-registry.yaml`

**现状**：
```yaml
- id: context_compression        # snake_case，无来源标识
  traceability: ["Definition Lock §112", "Phase 2 Wave 1"]
```

**问题（三项合一）**：
1. ID 用 `snake_case`，框架 #01 §16 / #02 §11 要求 `LHX-C-xxx` 结构化 ID
2. **缺 `source` 字段**（十源 DNA 溯源），框架 #01 §15 / #02 §10 列为必填
3. `traceability` 写裸 `§112` —— **无法判断是 DL 还是 MS**（UNIFIED-BLUEPRINT §2）

**修复方案**：
```yaml
- id: LHX-C-001                  # 去重后的统一能力编号
  source: JARVIS                 # 十源 DNA 溯源（必填）
  legacy_id: context_compression # 保留旧 ID 做迁移对照
  traceability: ["UB-A1", "Phase 2 Wave 1"]   # 用锚点，不用裸节号
```

**风险**：
- **高** —— ID 是外键，若被代码引用会直接断链。必须**先查消费方再改**
- 建议**双写过渡期**：保留 `legacy_id` 字段，新旧 ID 并存一个版本周期

**可逆性**：中（需同步回滚所有引用点）

**前置检查**：`grep -rn "context_compression\|memory_\|policy_" --include=*.py --include=*.ts .`

---

## R3【P0】capability-registry.yaml 漏 3 个 kernel

**文件**：`capability-registry.yaml`

**现状**：声明 12 个能力，实际 kernel 有 **14 个**（KERNEL-CANON §1 实测）。
缺 **security**、**audit**、**plugin**。

**问题**：`CAPABILITY-REGISTRY.md` L71-75 已指出此问题，但 **yaml 至今未修**，
而 yaml 是机器可读的注册表 —— 代码读它做能力校验时会**漏掉安全相关 kernel**。

**风险评级**：**这是安全问题，不只是数据不一致**。
若能力校验以 yaml 为准，则 `security` / `audit` / `plugin` 可能绕过能力注册检查。

**修复方案**：补齐 3 项，字段与现有 11 项对齐（含 `source`、`traceability`、行数、DoD 七维）。

**风险**：中（新增记录不改现有记录，但需确认消费方是否假设 12 个）

**可逆性**：高

---

## R4【P1】DEFINITION-LOCK-STATUS.md 方案 A 已证伪，需改状态

**文件**：`docs/spec/DEFINITION-LOCK-STATUS.md`

**现状**：§4 方案 A（从 git 历史恢复原件）列为"首选"。

**已验证结论**：
```bash
git log --all --name-only | grep -i definition    # 只有 node_modules 里的无关文件
git log --all --diff-filter=D                      # 无删除记录
```
→ **原件从未进入版本库，不可恢复**。

**修复方案**：
1. 方案 A 标记 ❌ 已关闭，附 git 验证命令与结果
2. §1 问题表的"声称路径不存在"改为"从未入版本库，不可恢复"
3. §4 默认方案改为 **B+C**（重建关键条款 + 重定向引用）
4. 新增指向 `UNIFIED-BLUEPRINT.md` 的引用

**风险**：低（文档改动）

**可逆性**：高

---

## R5【P1】CODEX-CONTRACT DoD 缺第 5 项

**文件**：`docs/CODEX-CONTRACT.md` §5

**现状**：6 项 DoD —— Implemented / Tested / Observable / Permissioned / Audited / Documented

**问题**：框架 #01 §72 / #02 §83 均为 **7 项**，多出 `Policy Controlled`。
按 C3 裁决统一为 7 项。缺这一项意味着**一个能力可以没有策略控制就标记完成**，
与"Every Action needs Policy"的最高原则直接冲突。

**修复方案**：§5 补入 `Policy Controlled`，并说明判定方式（通过 policy engine 判决）。

**风险**：低，但**会导致已达标项重新判定**（14 kernel 目前 0/14，补项后仍 0/14，但门槛更严）

**可逆性**：高

---

## R6【P1】GAP-MIGRATION-MATRIX 状态口径与 CAPABILITY-REGISTRY 不一致

**文件**：`docs/spec/GAP-MIGRATION-MATRIX.md` L109-134

**现状**：用"已完成 / 部分 / 未开始"三态。
**冲突**：CAPABILITY-REGISTRY §5 规定 **9 个状态枚举**（含 `PARTIALLY_IMPLEMENTED`、`EXPERIMENTAL` 等）。

**问题**：两份 L0/L1 文档对同一批能力用不同状态词汇，导致进度无法对齐。

**修复方案**：统一为 CAPABILITY-REGISTRY §5 的 9 枚举，并给出三态 → 九态的映射：
`已完成 → IMPLEMENTED` / `部分 → PARTIALLY_IMPLEMENTED` / `未开始 → PLANNED`

**风险**：低

**可逆性**：高

---

## R7【P1】项目自称 "Phase 4" 与 21 Phase 路线图错位

**文件**：`MEMORY.md`、多处进度表述、可能的 README

**现状**：项目自称 "Phase 4 — Agent Runtime, Wave 2"。
**冲突**：21 Phase 路线图中，`Phase 3 = Agent Runtime`、`Phase 4 = Model Gateway`。

**问题**：口径错位会导致进度汇报与外部文档对不上，也会误导后续排期。

**修复方案（二选一，需用户裁决）**：
- **方案 a（推荐）**：项目进度改用**能力维度**表述（如 "Agent Runtime Wave 2"），不挂 Phase 编号
- **方案 b**：按 MS:§177-198 的 21 Phase 重新对齐，明确当前应处哪个 Phase

**风险**：低（表述问题），但**影响所有对外进度沟通**

**可逆性**：高

---

## R8【P2】全库裸 `§N` 引用无法解析命名空间

**文件**：`docs/architecture/*.md`、`docs/archive/*.md`、`reconcile.py`、`capability-registry.yaml`

**现状**：大量 `依据 §112`、`Definition Lock §75`、`§30` 等裸写或半裸写引用。

**问题**：`DL:§112`（Kernel）与 `MS:§112`（L10K）含义完全不同，裸写导致追溯链不可判定。

**修复方案（分两批）**：
- **活跃文档**（`docs/spec/`、`docs/architecture/`、代码 yaml）：改为 `DL:§N` / `MS:§N` 或锚点 `UB-A1`
- **历史归档**（`docs/archive/`）：**保持原样**，仅在文件头加一句"本文件 § 引用未做命名空间标注，解读时参照 UNIFIED-BLUEPRINT 附录 A"

**风险**：低（纯注释/文字改动，不改逻辑）
**可逆性**：高

---

## R9【P2】implementation-status.yaml 是已知假数据

**文件**：`implementation-status.UNRELIABLE.yaml`（根目录；原 `implementation-status.yaml` 已于 2026-09-06 重命名，见下）

> ✅ **执行状态（2026-09-06）**：已重命名为 `implementation-status.UNRELIABLE.yaml` 并在文件头加不可信横幅；全库 20 处活跃文档引用已同步到新名（archive / incoming 按 R8 规则保持原样）。

**现状**：MEMORY.md 已记录此文件"**假数据，不可信**"，但它仍在仓库中且文件名像是权威源。

**问题**：**最危险的缺陷不是数据假，而是它看起来像真的**。
任何人（或任何 agent）看到这个文件名都会误以为是权威状态源。

**修复方案（推荐 a）**：
- **方案 a（推荐）**：重命名为 `implementation-status.UNRELIABLE.yaml` 并在文件头加醒目警告，或直接删除
- **方案 b**：重建为真实数据（从代码实测生成）

**风险**：低（若无人消费）；**需先 grep 确认无消费方**
**可逆性**：高

---

## R10【P2】L0–L7 语义冲突的防御性标注

**文件**：`docs/spec/KERNEL-CANON.md` §1（identity / memory 行的"L0-L7 作用域过滤"）

**现状**：代码已按 Memory Scope 实现 L0–L7（C11 裁决）。
**风险**：若后续有人误按框架 #03 §3 的架构分层（L0=Human Interface）去改代码，会**直接破坏已实现的权限语义**。

**修复方案**：在 KERNEL-CANON §1 加一段防御性说明：
> ⚠️ `L0–L7` 在本项目中**仅指 Memory Scope**（L0=System … L7=Session），
> 与任何"架构分层"编号体系无关。禁止按其他文档的 L0–L7 含义修改本字段。

**风险**：低（加注释）
**可逆性**：高

---

## 汇总

| 优先级 | 数量 | 编号 |
|---|---|---|
| **P0** | 3 | R1（状态造假）、R2（ID 违规）、R3（漏 3 kernel / 安全隐患） |
| **P1** | 4 | R4（方案 A 证伪）、R5（DoD 缺项）、R6（状态口径）、R7（Phase 错位） |
| **P2** | 3 | R8（裸节号）、R9（假数据文件）、R10（L0-L7 防御标注） |

## 执行前置条件（全部 P0 项都必须先做）

```bash
# R1/R2/R3 前置：确认 capability-registry.yaml 的消费方
grep -rn "capability-registry\|capability_registry" --include=*.py --include=*.ts --include=*.yaml .

# R1 前置：确认谁在读 IMPLEMENTED
grep -rn '"IMPLEMENTED"\|IMPLEMENTED' --include=*.py .

# R9 前置：确认 implementation-status.yaml 消费方
grep -rn "implementation-status\|implementation_status" --include=*.py --include=*.yaml .
```

**未跑完前置检查前，不执行 R1/R2/R3/R9。**

---

_本清单由鎏灏长期 Principal Engineer 提出。**等用户批准后执行**。_
