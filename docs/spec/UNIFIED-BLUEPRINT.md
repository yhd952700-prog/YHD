# LIUHAO X — 统一蓝图（UNIFIED BLUEPRINT）

> **版本**：v1.0 · **建立日期**：2026-09-06
> **定位**：宪法层的**单一入口**。承载文档分层、命名空间规则、冲突裁决、追溯索引。
> **负责人**：鎏灏长期 Principal Engineer

---

## 0. 这份文件是什么，不是什么

### 0.1 它解决什么问题

仓库里曾存在**六套互相冲突的架构表述**，导致"按 §112 实现"这类声明**无法验证**：

| 冲突源 | 表现 |
|---|---|
| 两套节号体系 | Definition Lock（122 节）与 Master Spec（221 节）**各自独立编号**，§112 在两者里含义完全不同 |
| 五套 Kernel 清单 | 11 / 12 / 13 / 14 / K01–K12 五套命名并存 |
| 宪法原件缺失 | Definition Lock 原件丢失，**git 历史查无此文件**（已验证，见 §0.3） |
| 状态口径不一 | `IMPLEMENTED` 在 yaml 与 md 里判定标准不同 |
| 新框架重复投递 | v2.0 Blueprint 同一份文档投递三次（#03/#04/#05） |

### 0.2 它不是什么（重要）

**本文件不是第六份全文副本。**

- ❌ 不复制 221 节原文 → 真相源仍是 [`MASTER-SPEC-v3.0.md`](MASTER-SPEC-v3.0.md)
- ❌ 不复制框架 #01/#02/#03 原文 → 真相源在 [`incoming/`](incoming/)
- ❌ 不重复 14 Kernel 清单 → 真相源是 [`KERNEL-CANON.md`](KERNEL-CANON.md)
- ❌ 不复制 `LIUHAO-X-v3.0-FINAL-ENGINEERING-MASTER-PLAN.md`（727 行）→ 该文件已于 2026-09-06 **并入本文件**（其 14 章主题映射见 §6.1），原文已删除，不再单独存在

**它是索引 + 裁决 + 映射。** 各原文保持单一真相源（SSOT），本文件只回答三个问题：

1. 冲突时**听谁的**（§3 裁决表）
2. 某条款**在哪份文档的哪一节**（附录 A 映射表）
3. 引用时**怎么写才不会错**（§2 命名空间规则）

> 这个判断的依据：解决办法缺失的是**裁决**，不是又一份原文。
> 再造一份 100KB 副本只会制造第七份冲突源，违反本项目"避免重复子系统"原则。

### 0.3 宪法原件状态（已结案）

| 项 | 结论 |
|---|---|
| 声称路径 | `D:\LiuHao-AI-OS\LIUHAO-X-V3.0-DEFINITION-LOCK.md` |
| 是否存在 | ❌ 不存在 |
| git 历史找回 | ❌ **已验证失败** —— `git log --all --name-only` 全历史无此文件，也无删除记录 |
| 结论 | 原件**从未进入版本库**，不可恢复 |

**因此**：[`DEFINITION-LOCK-STATUS.md`](DEFINITION-LOCK-STATUS.md) §4 的**方案 A（恢复原件）判定为死路**，正式关闭，改走方案 B+C（重建关键条款 + 重定向引用）。本文件即为方案 B+C 的落地。

**2026-09-12 补充（R8 收口）**：原件状态由"缺失"正式升级为「**永久不可考**」——
全部 git 恢复路径已穷尽，不再以"日后找回"为默认前提（reopen 条件见
[`DEFINITION-LOCK-STATUS.md`](DEFINITION-LOCK-STATUS.md) §6）。与之绑定的**最后 4 处
不可判定节号引用**（`docs/l10k/test-design.md` ×3、`docs/decisions/OPEN-DECISIONS.md` ×2）
已按「**留档注释 + 权威重定向**」处置完毕，**R8 随之关闭**。

---

## 1. 文档分层

```text
L0 宪法层（CONSTITUTION）—— 定义"是什么"，不可轻易变更
    ├── UNIFIED-BLUEPRINT.md      ← 本文件：索引 + 裁决 + 映射
    ├── KERNEL-CANON.md           ← 14 Kernel 权威（代码实测，最高优先级）
    └── CAPABILITY-REGISTRY.md    ← 能力清单 / ID 规则 / 状态枚举

L1 规格层（SPECIFICATION）—— 定义"怎么做"，随实现演进
    ├── MASTER-SPEC-v3.0.md       ← 221 节原文 + 16 章提炼
    ├── GAP-MIGRATION-MATRIX.md   ← As-Built → To-Be 迁移映射
    └── CODEX-CONTRACT.md         ← 执行契约 / DoD

L2 实现层（IMPLEMENTATION）—— 代码事实，最高仲裁权
    ├── src/kernels/*             ← 14 个 kernel（6,391 行）
    └── capability-registry.yaml  ← 能力注册表（机器可读）

L3 暂存层（STAGING）—— 未生效的待合并输入
    └── incoming/FRAMEWORK-0*.md  ← 用户提供的框架，未生效
```

**仲裁优先级（冲突时从高到低）**：

```text
L2 实现层（代码事实）
  ↓ 高于
L0 宪法层中的 KERNEL-CANON（因为它就是代码实测的产物）
  ↓ 高于
L0 宪法层其他文件
  ↓ 高于
L1 规格层
  ↓ 高于
L3 暂存层（未生效）
```

**理由**：代码是可运行的事实，文档是对事实的描述。描述与事实冲突时，改描述。

---

## 2. 命名空间规则（解决节号撞车）

### 2.1 问题根源

`§112` 这个写法在仓库里指向**两个完全不同的东西**：

| 写法 | 实际指向 | 证据 |
|---|---|---|
| `DL:§112` | **十二核心 Kernel** | 12 个 kernel `__init__.py` docstring + `security/__init__.py:386` |
| `MS:§112` | **L10K** | `MASTER-SPEC-v3.0.md:3609` → `# 112. L10K` |

现有文档中大量裸写 `§112`、`§75`、`§30` 而**不声明是哪套**——这是追溯链断裂的直接原因。

### 2.2 规则：强制前缀

**新写的一切引用必须带命名空间前缀**：

| 前缀 | 指代 | 节数 | 真相源 |
|---|---|---|---|
| `DL:§N` | Definition Lock v3.0 | 122 节 | ❌ 原件缺失，仅 §112/§113/§115 可从代码反推 |
| `MS:§N` | Master Spec v3.0 | 221 节 | [`MASTER-SPEC-v3.0.md`](MASTER-SPEC-v3.0.md) 第二部分 |
| `UB:§N` | **本文件** | 17 章 | 本文件 |
| `KR:§N` | KERNEL-CANON | 5 节 | [`KERNEL-CANON.md`](KERNEL-CANON.md) |

❌ 禁止：`依据 §112 实现`（无法判断是哪套）
✅ 正确：`依据 DL:§112 实现`（= 十二核心 Kernel）

### 2.3 稳定锚点（推荐写法）

节号会变，**锚点不变**。跨文档引用优先用锚点：

| 锚点 | 含义 | 等价旧引用 |
|---|---|---|
| `UB-A1` | 十四核心 Kernel | `DL:§112` + `DL:§113` + `DL:§115` |
| `UB-A2` | L10K / VHL | `MS:§112`–`MS:§116` |
| `UB-A3` | 21 Phase 路线图 | `MS:§177`–`MS:§198` |
| `UB-A4` | DoD 与 Release Gates | `MS:§117`–`MS:§118` |
| `UB-A5` | 反假货五连 | `MS:§157`–`MS:§161` |
| `UB-A6` | 十源 DNA 映射 | `MS:§143` |

**迁移策略**：历史文档（尤其 `docs/archive/`）的裸 `§N` 引用**保持原样不批量改写**（那是历史记录），
但新写的代码注释、新文档、yaml 追溯字段一律用锚点或带前缀的节号。

---

## 3. 冲突裁决表（C1–C12）

> 全部裁决在此。裁决后，**任何文档与本表冲突的，以本表为准**。

| # | 冲突项 | 各方主张 | **裁决** | 依据 |
|---|---|---|---|---|
| **C1** | 节号体系 | DL(122) / MS(221) / 框架(94-95) 三套 | **命名空间隔离**：`DL:` / `MS:` / `UB:` 并存，禁止裸写 | §2 |
| **C2** | Kernel 数量 | 11 / 12 / 13 / 14 / K01–K12 | **14**（代码实测封板） | `src/kernels/` 目录 + KERNEL-CANON |
| **C3** | DoD 项数 | 6 项（项目）/ 7 项（框架 #01 §72） | **7 项**，补入 `Policy Controlled` | 框架 #01 §72、#02 §83 一致；7 项更严 |
| **C4** | Capability ID | `LHX-C-xxx` vs `snake_case`（yaml） | **`LHX-C-xxx`**，yaml 需迁移 | 框架 #01 §16 / #02 §11 + CAPABILITY-REGISTRY §1 |
| **C5** | 实现状态 | yaml 标 `IMPLEMENTED` 但 `tested/audited=false` | **降级为 `PARTIALLY_IMPLEMENTED`** | 框架 #01 §18/§72 明令禁止 PLANNED 冒充 IMPLEMENTED |
| **C6** | Phase 编号 | 项目自称 Phase 4 / MS 与框架 #01/#02 为 21 / 框架 #03 为 17 | **21 Phase**，项目"Phase 4"表述需对齐（2026-09-06 已执行：MEMORY.md 进度表述改能力维度，不再自称 Phase 4） | 三方一致（MS+#01+#02）对 1 份（#03） |
| **C7** | 宪法原件 | 95 节（框架 #01）vs 122 节（原件） | **框架 #01 不作正本**，仅作暂存输入 | 原件 122 节且已证不可恢复（§0.3） |
| **C8** | v3.0 主干 | #01（95 节）/ #02（94 节）两变体 | **#02 为主干**，#01 独有章节回填 | #02 含 API/DB/技术栈/韧性/DR/评测，工程可落地性更强 |
| **C9** | Kernel 含谁 | #01/#02 含 Trust / #03 含 Runtime 无 Trust | **含 Trust，无 Runtime kernel** | 代码实测有 `trust`；Runtime 属 Agent Runtime 层非 kernel |
| **C10** | Phase 数量 | 21 vs 17 | **21** | 同 C6 |
| **C11** | L0–L7 语义 | Memory Scope（#01/#02）vs 架构分层（#03） | **L0–L7 = Memory Scope 独占**；#03 架构分层改用 `AL0–AL9` | `identity`/`memory` kernel **已按 Memory Scope 实现** |
| **C12** | 框架份数 | #03/#04/#05 看似三份 | **= 1 份**，权重 1 | `diff` 验证仅差 2 处引号字符 |

### 3.1 三条裁决的详细理由

**C8 — 为什么 #02 当主干**

| 维度 | #01（95 节） | #02（94 节） |
|---|---|---|
| API 架构 | ❌ 无 | ✅ §56 完整 `/api/v1/` 契约 |
| 数据库 | §56 表清单 | ✅ §57 更细（含 `agent_states`、`task_dependencies`、`tool_permissions`） |
| 技术基线 | ❌ 无 | ✅ §70 后端/前端/基础设施/观测四段 |
| 韧性 / 灾备 | ❌ 无 | ✅ §72 幂等/熔断/限流 + §74 备份/DR/PITR |
| 评测 | ❌ 无 | ✅ §77 Agent 评测 / §78 组织评测 |
| 十源分层 | ✅ §21–§30 每源一节 | ❌ 拆散 |
| Plugin / 数据治理 / 版本化 | ✅ §62–§64 | ❌ 无 |

**结论**：#02 提供工程落地所需的接口、存储、技术栈、韧性、灾备、评测；#01 独有部分（十源分层表述、Plugin、数据治理、版本化）作为**回填章节**并入，不丢信息。

**C9 — 为什么是 Trust 不是 Runtime**

`src/kernels/` 实测 14 个目录**含 `trust`、无 `runtime`**。
框架 #03 §4 把 `Runtime` 列入 Kernel 属概念混淆——Agent Runtime 是 **L-Core 之下的执行层**（`apps/runtime`），
不是 kernel 层原语。以代码事实为准。

**C11 — 为什么 L0–L7 归 Memory Scope**

这是**唯一有代码后果的裁决**。`identity` 与 `memory` kernel 已实现 "L0-L7 作用域过滤"
（KERNEL-CANON §1）。若改按 #03 的架构分层理解，`L0` 将从 "System" 变成 "Human Interface"，
**直接改变已实现代码的语义**。改文档的代价远小于改代码，且 Memory Scope 用法有 #01、#02 两份支持。

---

## 4. 产品定义（唯一权威表述）

> **LIUHAO X / 鎏灏 X** 是一个能力开放扩展、具有人类主权的自主智能体操作系统。
> 它在持续扩展模型、Agent、工具、技能、知识、协议、网络和世界接口能力的同时，
> 对自主权限实行明确、可授权、可观测、可验证、可中断、可撤销的边界控制。

**核心等式**：

```text
OPEN-ENDED CAPABILITY  +  BOUNDED AUTONOMOUS AUTHORITY
能力可以持续扩展            权限不能自动扩展
```

**最高原则**：`More Capability ≠ More Authority` · `Human Sovereignty Above All`

**长期定位**：The Operating System for the Agent World

十源（ULTRON / VISION / ADA / EDITH / FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）**不是十个 AI**，
而是**十组能力 DNA**，统一为：
`十种 Archetype → 原子能力 → 统一工程原语 → LIUHAO Kernel → 统一 Runtime → LIUHAO X`

---

## 5. 架构骨架

```text
HUMAN
  ↓
L-CORE                      ← 人类接口（JARVIS + KAREN + FRIDAY 汇入）
  ↓
LIUHAO KERNEL               ← 14 个 kernel（UB-A1）
  ↓
┌──────────────┼──────────────┐
INTELLIGENCE   AGENCY        SOVEREIGNTY
  ↓              ↓              ↓
AGENT FACTORY → SPECIALIZED AGENTS → MULTI-AGENT TEAMS
  ↓
AI ORGANIZATION
  ↓
AGENT NETWORK
  ↓
WORLD INTERFACE
  ↓
DIGITAL / CLOUD / PHYSICAL WORLD
```

**安全链（任何 Critical Action 必过）**：

```text
Identity → Authentication → Authorization → Capability → Policy
  → Approval(if required) → Sandbox(if required) → Execution → Verification → Audit
```

**记忆链（任何 Memory 访问必过）**：

```text
Identity → Ownership → Scope(L0-L7) → Permission → Sensitivity → Policy → Retrieve → Audit
```

**Spawn 链（任何 Agent 创建必过）**：

```text
Parent Identity → Parent Authorization → Quota → Budget → Resource
  → Capability → Policy → Security → Sandbox → Create → Audit
```

---

## 6. 各层索引

> 本节只给指针，不给内容。内容在各自真相源。

| 领域 | 权威来源 | 备注 |
|---|---|---|
| **14 Kernel 清单** | [`KERNEL-CANON.md`](KERNEL-CANON.md) §1 | 唯一权威，含行数与 DL 依据 |
| **Kernel 接口** | `docs/architecture/kernels-interface.md` | 与 KERNEL-CANON 同步 |
| **221 节原文** | [`MASTER-SPEC-v3.0.md`](MASTER-SPEC-v3.0.md) 第二部分 | 逐字收录，L275 起 |
| **16 章提炼版** | [`MASTER-SPEC-v3.0.md`](MASTER-SPEC-v3.0.md) 第一部分 | L1–L274 |
| **能力清单 / ID / 状态** | [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md) | 14 kernel + R0–R4 + 9 状态枚举 |
| **As-Built → To-Be 迁移** | [`GAP-MIGRATION-MATRIX.md`](GAP-MIGRATION-MATRIX.md) | 31 目录映射 + K01–K12 停用令 |
| **DoD / 执行契约** | [`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md) §5 | 6 项 → 按 C3 改 7 项 |
| **Definition Lock 状态** | [`DEFINITION-LOCK-STATUS.md`](DEFINITION-LOCK-STATUS.md) | 原件缺失已结案（§0.3） |
| **待合并框架** | [`incoming/`](incoming/) + `MERGE-LEDGER.md` | 未生效 |
| **21 Phase 进度** | [`GAP-MIGRATION-MATRIX.md`](GAP-MIGRATION-MATRIX.md) L103–134 | 对应 `MS:§177` |

### 6.1 全工程主题导航（原 MASTER-PLAN 14 章 → 现权威源）

> `LIUHAO-X-v3.0-FINAL-ENGINEERING-MASTER-PLAN.md` 原为 16 份重复工程文档的合并件（其自述 `UNIQUE INFORMATION LOST: 0`）。
> 2026-09-06 将其**优化并入本文件**：不再保留 727 行副本，仅把每章指向现权威源，避免第七份冲突源。

| MASTER-PLAN 章 | 现权威源 |
|---|---|
| 一、Definition Lock（宪法） | [`DEFINITION-LOCK-STATUS.md`](DEFINITION-LOCK-STATUS.md) + 本文件 §4/§5 |
| 二、Product Definition（产品定义） | 本文件 §4 + [`../product/PM-PRD-v3.0.md`](../product/PM-PRD-v3.0.md) |
| 三、Ten DNA（十源能力体系） | 本文件 §4 + [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md)（锚点 `UB-A6`） |
| 四、Architecture（架构） | 本文件 §5 + [`../architecture/Architect-Architecture-v3.0.md`](../architecture/Architect-Architecture-v3.0.md) |
| 五、Control / Data Plane | [`../architecture/Architect-Architecture-v3.0.md`](../architecture/Architect-Architecture-v3.0.md) |
| 六、Repository Baseline（代码基线） | [`../architecture/existing-codebase-audit.md`](../architecture/existing-codebase-audit.md) |
| 七、Current Reality & Gap（现状与差距） | [`GAP-MIGRATION-MATRIX.md`](GAP-MIGRATION-MATRIX.md) + [`../architecture/gap-analysis.md`](../architecture/gap-analysis.md) |
| 八、Dependency & Migration（依赖与迁移） | [`../architecture/dependency-map.md`](../architecture/dependency-map.md) + [`../architecture/migration-matrix.md`](../architecture/migration-matrix.md) |
| 九、Risk（风险） | [`../architecture/Architect-Architecture-v3.0.md`](../architecture/Architect-Architecture-v3.0.md)（风险章）+ 本文件 §3 |
| 十、Phase 0–22（实施顺序与验收） | [`GAP-MIGRATION-MATRIX.md`](GAP-MIGRATION-MATRIX.md)（21 Phase 进度，锚点 `UB-A3`）+ `MS:§177`–`MS:§198` |
| 十一、Testing & Acceptance（测试与验收） | [`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md) §5（DoD）+ [`../operations/production-runbook.md`](../operations/production-runbook.md) |
| 十二、Evidence & Rollback（证据与回滚） | [`../operations/production-runbook.md`](../operations/production-runbook.md) + [`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md) |
| 十三、Production Readiness（生产就绪） | [`../operations/production-runbook.md`](../operations/production-runbook.md) + [`../operations/health-check-spec.md`](../operations/health-check-spec.md) |
| 十四、Final DoD（最终完成定义） | 本文件 §7 + [`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md) |

---

## 7. DoD 与 Release Gate（按 C3 定为 7 项）

一个能力只有**同时满足全部 7 项**，才允许标记 `IMPLEMENTED`：

| # | 项 | 判定方式 |
|---|---|---|
| 1 | **Implemented** | 代码已落盘且可导入 |
| 2 | **Tested** | 单元测试 + 集成测试通过 |
| 3 | **Observable** | 有 trace / metric / log 覆盖 |
| 4 | **Permissioned** | 通过 identity + permission 校验 |
| 5 | **Policy Controlled** | 通过 policy engine 判决（**C3 新增**）。判定分两级——见下方注 |
| 6 | **Audited** | 写入 audit 链，`audited: true` |
| 7 | **Documented** | 有对应文档且链接可追溯 |

> **Policy Controlled 的两级判定（2026-09-11 明确，D7 收口）**
>
> 本项容易被读成"过了引擎即达标"，但"过引擎"与"受控制"是两件事。判定拆成两级：
>
> - **L1 判决已记录**：动作经 policy engine 判决，且判决被写入审计事件（可追溯依据规则）。
>   *这是当前 43 个内核动作达到的级别。*
> - **L2 判决已执行**：判决对执行有约束力（`deny` 真的会阻止动作）。
>   *内核层**尚未**达到；能力层的 4 处硬 gate 已达标。*
>
> 只满足 L1 时，**必须在审计事件中标注 `policy_enforced: false`**，避免把 `deny`
> 误读为"动作被拒绝"。详见 `POLICY-ENFORCEMENT-DESIGN.md` §10 与
> `AI-LAYER-DOD-AUDIT.md` §3.5.4。

**状态枚举（9 个，CAPABILITY-REGISTRY §5 为准）**：
`PLANNED` / `IN_PROGRESS` / `IMPLEMENTED` / `PARTIALLY_IMPLEMENTED` / `EXPERIMENTAL` /
`RESEARCH` / `NOT_REALIZABLE` / `DEPRECATED`

⚠️ **严禁**把 `PLANNED` 标为 `IMPLEMENTED`（框架 #01 §18、#02 §13 明令）。

---

## 8. 使用规则

| 你要做什么 | 怎么做 |
|---|---|
| 引用某个条款 | 用锚点（`UB-A1`）或带前缀节号（`DL:§112`），**禁止裸写 `§N`** |
| 查 Kernel 有几个、叫什么 | 查 [`KERNEL-CANON.md`](KERNEL-CANON.md)，本文不重复维护清单 |
| 发现文档与代码冲突 | **以代码为准**，回来更新本文件 §3 裁决表 |
| 新增 kernel | 先在 KERNEL-CANON 登记，说明为何不属于现有 14 个 |
| 遇到无法判断的条款归属 | **停下来问人**，不要自行推断条款内容 |
| 想改本文件 §3 裁决表 | 必须给出代码事实或用户明确指令，否则不改 |

---

## 附录 A — 节号映射表

**已知的 Definition Lock 条款（仅 3 条可从代码反推，其余不可考）**：

| DL 节号 | 内容 | 锚点 | 可信度 |
|---|---|---|---|
| `DL:§112` | 十二核心 Kernel（identity/memory/context/capability/policy/execution/resource/event/network/trust/evaluation/**security**） | `UB-A1` | **高**（12 个 kernel docstring 佐证） |
| `DL:§113` | Audit Kernel（第 13 个） | `UB-A1` | **高**（audit docstring 佐证） |
| `DL:§115` | Plugin Registry（第 14 个） | `UB-A1` | **高**（plugin docstring 佐证） |
| `DL:§122` | 条款总数 = 122 | — | 高 |
| `DL:§1`–`§111`、`§114`、`§116`–`§121` | **不可考** | — | ❌ 原件缺失 |

**Master Spec 关键节号**：

| MS 节号 | 内容 | 锚点 |
|---|---|---|
| `MS:§2` | 可实现性分级 R0–R4 | — |
| `MS:§3` | 硬规则 25 条 | — |
| `MS:§4` + `MS:§143` | 十源 DNA | `UB-A6` |
| `MS:§6` | Kernel 层 ⚠️ **只列 12 个，漏 audit/plugin** | 以 `UB-A1`(14) 为准 |
| `MS:§101`–`§104` | Capability ID 体系 | — |
| `MS:§112`–`§116` | L10K / VHL | `UB-A2` |
| `MS:§117`–`§118` | DoD 与 Release Gates | `UB-A4` |
| `MS:§157`–`MS:§161` | 反假货五连 | `UB-A5` |
| `MS:§177`–`MS:§198` | 21 Phase | `UB-A3` |
| `MS:§220` | 最终工程原则 | — |

> ⚠️ **注意**：`DL:§112`（Kernel）与 `MS:§112`（L10K）**数字相同含义完全不同**。
> 这是全库最容易踩的坑，引用时务必带前缀。

---

## 附录 B — 文档依赖关系

```text
                    UNIFIED-BLUEPRINT.md（本文件）
                    ├── 索引 + 裁决 + 映射
                    │
        ┌───────────┼───────────┬─────────────┐
        ↓           ↓           ↓             ↓
 KERNEL-CANON   CAPABILITY-  MASTER-SPEC   GAP-MIGRATION
 （14 kernel）  REGISTRY     （221 节）     -MATRIX
    ↑           （ID/状态）                  （迁移）
    │                                          │
    └────────── src/kernels/* ─────────────────┘
                （代码事实，最高仲裁）
```

**引用方向规则**：
- ✅ 上层可引用下层（CODEX-CONTRACT → KERNEL-CANON）
- ❌ 下层**不得**反向依赖上层（kernel docstring 不引用本文件的章节号）
- 代码注释引用统一用锚点（`UB-A1`），不用本文件章节号（避免文档重构打断代码注释）

---

## 附录 C — 本次合并的输入与处置

| 输入 | 处置 |
|---|---|
| `docs/spec/KERNEL-CANON.md` | ✅ 保留为权威，未修改 |
| `docs/spec/MASTER-SPEC-v3.0.md` | ✅ 保留为 221 节真相源，未修改 |
| `docs/spec/CAPABILITY-REGISTRY.md` | ✅ 保留，未修改 |
| `docs/spec/GAP-MIGRATION-MATRIX.md` | ✅ 保留，未修改 |
| `docs/spec/DEFINITION-LOCK-STATUS.md` | ✅ **已更新**（方案 A 证伪；2026-09-12 结案为「永久不可考」，见 §0.3） |
| `incoming/FRAMEWORK-01`（95 节） | 📥 暂存，按 C8 作回填源 |
| `incoming/FRAMEWORK-02`（94 节） | 📥 暂存，按 C8 作主干候选 |
| `incoming/FRAMEWORK-03/04/05`（92 节） | 📥 暂存，按 C9/C10/C11 其偏离**不予采纳** |

---

_本文件由鎏灏长期 Principal Engineer 建立。修改 §3 裁决表需代码事实或用户指令。_
