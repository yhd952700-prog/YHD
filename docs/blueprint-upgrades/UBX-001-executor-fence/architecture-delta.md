# Architecture-Delta — UBX-001-executor-fence

> 本文件**只描述**架构增量与代码落点，**不实现**（实现在本波 p36 代码改动中完成）。

## 1. In Scope / Out of Scope
- **In scope:**
  - 执行链（UNIFIED-BLUEPRINT §5 安全链的 `Execution` 环节及其前置 `Authorization`）
  - 非人类执行主体：`WorldInterface`、subprocess/shell 适配器、外部写工具、agent 框架构造点
  - `executor_id` / `lease` / `fencing token` 三要素的**单节点**注入与校验
- **Out of scope:**
  - 跨节点围栏 / 选主 / 租约接管（→ **UBX-005** distributed-real-coordination）
  - HD-05 根信任（→ **UBX-004**）

## 2. Delta vs Current
| 维度 | 当前 | 目标 |
|---|---|---|
| 执行主体身份 | `authorize is None` ⇒ 默认放行（U42） | 必须持可验证 `executor_id` |
| 租约 | 无（单写者 RLock，无 TTL） | 带 TTL 租约；过期 ⇒ deny |
| 围栏 | `ac3a90bc` 自锁但无死进程检测（U47） | 单调递增 fencing token + 死进程检测 ⇒ inert 而非自锁 |
| 默认语义 | permit-by-default（人类 L-Core 除外） | default-deny across 执行链 |

## 3. Impact on the Chains
- **安全链：** 在 `Execution` 前插入"executor 围栏校验"门；`Sandbox(if required)` 之前必须已过围栏。
- **Spawn 链：** 新 agent 创建时继承/绑定父 `executor_id + lease`，避免子进程匿名执行。
- **记忆链：** 不影响（记忆访问的 scope 过滤独立）。

## 4. Code Landpoints（仅描述）
- `src/ai/world_interface.py`：已设 `actor="autonomous"` default-deny（commit `6d7769e6`、`17ca073c`）；本 UBX 将其泛化为"无有效围栏三要素 ⇒ deny"。
- `src/ai/lcore.py:131-132`：人类 L-Core 保留默认放行（人类主权，按 directive）；但**新增**非人类能力必须走围栏。
- `src/security/`：围栏校验可作为 policy engine 的可执行判决（L2, 见 UNIFIED-BLUEPRINT §7）。
- 死进程检测（Fix A，sec-impl）：租约心跳 + 陈旧写者取代逻辑。

## 5. Blueprint Edits Triggered
- UNIFIED-BLUEPRINT §3：新增 **C13 — 执行链默认围栏 = default-deny**（`executor_id + 活租约 + 当前 fencing token` 缺失即 deny）。
- UNIFIED-BLUEPRINT §5：安全链图在 `Execution` 前补"executor 围栏校验"节点。
