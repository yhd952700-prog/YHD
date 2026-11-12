# Architecture-Delta — UBX-006-perf-gate-normalization

> 只描述，不实现。

## 1. In Scope / Out of Scope
- **In scope：** 集中式 perf-gate 配置（容量下限 / 延迟 SLO / 链校验预算）；gate 越过 → fail-closed 映射；U49 event_id 升宽作为规模上限 gate。
- **Out of scope：** 具体吞吐优化（C2-1 batch）、存储后端选型。

## 2. Delta vs Current
| 维度 | 当前 | 目标 |
|---|---|---|
| 性能门槛 | 散落常量，不可判安全 | 集中三件套 gate |
| 越界行为 | 静默（U49 丢证据） | 明确 fail-closed（deny / UNVERIFIED） |
| 链校验 | 实时阻塞（U50） | 快照/离线，不阻塞写入 |

## 3. Impact on the Chains
- **安全链（Verification / Audit）：** gate 越界 ⇒ R-G2-01 deny / R-G2-07 UNVERIFIED，绝不伪绿。

## 4. Code Landpoints（仅描述）
- `src/reliability/perf_gates.py`（新增）：集中 gate 配置 + 越界映射。
- 审计写入路径接 R-G2-01 有界队列；链校验改快照执行（UBX-001/005 围栏内）。

## 5. Blueprint Edits Triggered
- UNIFIED-BLUEPRINT §7（DoD）：补"性能 gate 越界 = 不标 IMPLEMENTED"的隐性约束。
