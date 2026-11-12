# Architecture-Delta — {{UBX-NNN-<slug>}}

> 本文件**只描述**架构增量与代码落点，**不实现**。源码改动在对应开发波次完成。

## 1. In Scope / Out of Scope
- **In scope:**
  - {{受影响子系统 / 链路：执行链 / 安全链 / 记忆链 / Spawn 链}}
  - {{具体模块（src/...）}}
- **Out of scope:**
  - {{明确排除项}}

## 2. Delta vs Current
| 维度 | 当前 | 目标 |
|---|---|---|
| {{如：默认授权}} | {{permit-by-default}} | {{default-deny}} |
| {{如：围栏}} | {{无/存根}} | {{真实跨进程围栏}} |

## 3. Impact on the Chains
- **安全链（Identity→…→Execution→Verification→Audit）：** {{}}
- **记忆链：** {{}}
- **Spawn 链：** {{}}

## 4. Code Landpoints（仅描述）
- `src/{{path}}`：{{角色}}
- 如需新 `ADR-*`：`docs/autonomous/ADR-{{topic}}.md`（编号走 ADR 约定，不进 UBX 号段）

## 5. Blueprint Edits Triggered（若有）
- UNIFIED-BLUEPRINT §{{N}}：{{旧 → 新}}
