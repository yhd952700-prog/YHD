# Blueprint Auto-Upgrade Mechanism (UBX)

> **Layer:** L0 宪法层的演进机制（受 [`UNIFIED-BLUEPRINT.md`](../spec/UNIFIED-BLUEPRINT.md) §9.1 约束）
> **Authority:** [`GOVERNANCE.md`](../autonomous/GOVERNANCE.md) §4 授权团队自主升级旧 Blueprint
> **Owner:** team-lead
> **Established:** 2026-09-28

---

## 0. 为什么存在这个机制

UNIFIED-BLUEPRINT 是宪法层单一入口，但它的 §3 裁决表 / §6 索引 / §9 活体登记册
**必须能被演进**，否则蓝图会僵化、与代码事实脱节（这正是本项目早期六套冲突架构的根源）。

GOVERNANCE §4 已授权团队"升级旧 Blueprint"，但**没有流程**约束这种升级。
本机制就是那个流程：**任何对蓝图裁决表/索引/活体登记册的实质变更，都必须走 UBX 四件套提案**，
不得直接散改蓝图文件。

> 这与 UNKNOWN-TO-OWNER / RISK-REGISTER 的关系：
> 每条 `U-NNN` 发现、每个 `R-Gx-NN` 风险，若需要**改变架构或蓝图裁决**，就孵化一个 `UBX-NNN-<slug>`
> 把它收口。UBX 是"从发现到决策到落地"的闭环载体。

---

## 1. 四件套结构（每个 UBX = 一个目录）

每个升级是独立目录 `docs/blueprint-upgrades/UBX-NNN-<slug>/`，内含恰好四个文件：

| 文件 | 角色 | 必须回答 |
|---|---|---|
| `ADR.md` | 架构决策记录 | **为什么**改、决策是什么、后果、引用哪些 U-/R- 编号 |
| `architecture-delta.md` | 架构增量 | **改了什么**（in/out of scope）、对执行链/安全链/记忆链的影响、代码落点（仅描述，不实现） |
| `migration-path.md` | 迁移路径 | **怎么 roll out**：步骤、兼容、回滚、谁批准 |
| `verification-plan.md` | 验证计划 | **怎么证明**改对了：测试/gate/fail-closed 检查、done 判据 |

新升级的脚手架来自 [`TEMPLATE/`](TEMPLATE/)，复制后填内容，目录名 `UBX-NNN-<slug>` 中
`NNN` 取当前最大号 +1，`<slug>` 为 kebab-case 主题。

---

## 2. auto-apply vs 人类决策门槛

UBX 提案在 **GOVERNANCE §3 / UNIFIED-BLUEPRINT §9.1** 的框架下分为两类：

### 2.1 auto-apply（自主自动生效）
满足**全部**以下条件，团队按 GOVERNANCE §4 自主裁决并落地，无需升给人：
- 不触及任何 **HC-01 级人类主权事件**（法律 / 数据归属 / 重大合规 / 不可逆删除 / 外部责任）；
- 属于纯文档/索引演进、fail-closed 行为**收紧**、风险登记、或代码层已实现能力的能力登记；
- `verification-plan.md` 的 done 判据可被独立证明（不"假装成功"，GOVERNANCE §2.2）。

### 2.2 human-decision-gated（人类决策门槛）
满足**任一**条件，即升给 owner 拍板（GOVERNANCE §3），**但其余自主工作不被阻塞**：
- 触及 HC-01 级人类主权事件；
- 需要不可逆删除重要原始资产、或外部合同/采购；
- 价值权衡无法从工程目标 + 现有原则推导。

> 关键澄清（解决 GOVERNANCE ↔ UNIFIED-BLUEPRINT §8 旧矛盾）：
> "升给人"只隔离**那一个**人类主权决策，**不暂停**项目的自主演进。
> 这正是 UNIFIED-BLUEPRINT §8 改造后的口径——见 §9.1。

---

## 3. 提交流程（团队纪律）

1. 从 `TEMPLATE/` 复制四件套到 `UBX-NNN-<slug>/`。
2. 填四件套；`ADR.md` 必须链接相关 `U-NNN` / `R-Gx-NN`。
3. 若改动 UNIFIED-BLUEPRINT §3/§6/§9，在本 UBX 的 `architecture-delta.md` 显式列出目标行。
4. 评审：`verification-plan.md` 的 done 判据须可独立复现。
5. **提交纪律**：`git checkout p36` → `git add docs/blueprint-upgrades/UBX-NNN-<slug>`（**不得 `git add -A`**）→ 提交。**不 push。**
6. 落地后，若改动蓝图/活体登记册，回到 UNIFIED-BLUEPRINT §9 同步索引（手动步骤，见 §9 接线规则）。

---

## 4. 升级索引

| UBX | slug | 主题 | 状态 | 关联发现 |
|---|---|---|---|---|
| [UBX-001](UBX-001-executor-fence/) | executor-fence | 执行器身份/租约/围栏/default-deny 贯穿整条执行链 | PROPOSED（实现中） | U12, U38, U42, U47 |
| [UBX-002](UBX-002-sandbox-fail-closed/) | sandbox-fail-closed | 沙箱 fail-closed（含 Windows 资源限制） | PROPOSED | U39 |
| [UBX-003](UBX-003-secrets-fail-closed/) | secrets-fail-closed | 密钥 fail-closed（不泄漏、不静默失败） | PROPOSED | U40, U45 |
| [UBX-004](UBX-004-root-of-trust-provider-neutral/) | root-of-trust-provider-neutral | 根信任 providers 中立（local/rfc3161/tpm 可插拔） | PROPOSED | U54（原 U36/HD-05） |
| [UBX-005](UBX-005-distributed-real-coordination/) | distributed-real-coordination | 分布式真实协调（跨进程围栏/租约/选主） | PROPOSED | U12, U47 |
| [UBX-006](UBX-006-perf-gate-normalization/) | perf-gate-normalization | 性能 gate 归一化（容量/延迟/SLO 门槛） | PROPOSED | U48–U50 |

---

## 5. 与现有 ADR 的关系

`docs/autonomous/ADR-*` 是**已落地**的审计存储 ADR（C2/C3 等）。UBX 不替代它们，
而是蓝图层的**升级提案**载体。若某 UBX 的实现需要一个 `ADR-*`（如新的存储代际），
在 `architecture-delta.md` 里引用/孵化对应 `ADR-*`，编号仍走 `ADR-<topic>` 约定
（不在本 UBX 编号体系内，避免与 `docs/adr/` 的 `ADR-HOLD-SCOPE` 撞名）。
