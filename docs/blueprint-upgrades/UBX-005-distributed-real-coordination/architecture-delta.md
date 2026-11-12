# Architecture-Delta — UBX-005-distributed-real-coordination

> 只描述，不实现。

## 1. In Scope / Out of Scope
- **In scope:** `DistributedLease` + `FencingToken` + leader election 后端；替换 `src/distribution/lock.py` 存根；跨进程围栏承接 UBX-001。
- **Out of scope:** 具体 etcd/redis 运维部署；根信任（→ UBX-004）。

## 2. Delta vs Current
| 维度 | 当前 | 目标 |
|---|---|---|
| 跨进程围栏 | Redis-less 存根（U12） | 真实 `DistributedLease` + 单调 `FencingToken` |
| 选主 | 无 | 后端选主，失主释放租约 |
| 默认部署 | — | 本地锁退化（零依赖），接口一致 |

## 3. Impact on the Chains
- **安全链（Execution）：** 跨进程执行主体经协调层围栏，陈旧写者被拒。
- **Spawn 链：** 跨节点 agent 创建继承分布式租约。

## 4. Code Landpoints（仅描述）
- `src/distribution/lock.py`：替换为协调层客户端。
- `src/distribution/coordination.py`（新增）：lease/fencing/leader 抽象 + 后端驱动。

## 5. Blueprint Edits Triggered
- UNIFIED-BLUEPRINT §3：新增 **C15 — 跨进程围栏由分布式协调层保证**（无真实原语则 deny）。
