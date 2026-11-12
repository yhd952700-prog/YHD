# ADR — UBX-005-distributed-real-coordination: 分布式真实协调（跨进程围栏 / 租约 / 选主）

> **Status:** PROPOSED
> **Date:** 2026-09-28
> **Owner:** os-systems (os-impl) + security-identity
> **Type:** auto-apply（基础设施机制；不触及 HC-01 级人类主权事件）

## 1. Context（为什么）
- **U12**：`src/distribution/lock.py` 是 **Redis-less 存根**——跨进程围栏 / 选主 / 租约**无真实原语**，单机单写者之外无强制力。
- **U47 / R-G3-09**：一旦跨节点（C3 联邦 / 多节点 anchor writer），"单写者"需要**跨节点租约 + 围栏令牌**，而本仓无此原语。
- UBX-001 提供了单节点围栏三要素；跨节点接管必须由真实协调层兜底，否则陈旧写者无法被安全取代。

## 2. Decision（决策）
引入**真实分布式协调原语**，承接 UBX-001 的围栏语义到跨进程/跨节点：
1. 用可插拔后端（默认：本地文件锁 / 可选 etcd / redis）实现 `DistributedLease` + `FencingToken`（单调递增）。
2. 选主（leader election）走同一后端，失主 ⇒ 释放租约 ⇒ fencing token 递增，陈旧 leader 写入被拒（R-G3-09）。
3. 移除 `src/distribution/lock.py` 的"假"实现，调用统一协调层。
4. 默认单节点部署下退化为本地锁（零依赖），但**接口与跨节点一致**，未来联邦无需重写。

## 3. Consequences（后果）
- **正向：** 闭合 U12；给 C3 联邦 / 多节点 anchor 提供强制围栏（R-G3-09 可解）；让 UBX-001 的围栏在跨进程真实生效。
- **负向：** 引入协调后端依赖（可选，默认本地）；需处理租约心跳与脑裂。
- **不做：** 跨节点陈旧写者无法被取代 = 静默数据损坏风险。

## 4. References
- UNKNOWN-TO-OWNER: `docs/autonomous/UNKNOWN-TO-OWNER.md#u12`、`#u47`
- RISK-REGISTER: `docs/autonomous/RISK-REGISTER.md` R-G3-09（跨节点锚写围栏）
- 蓝图索引: `docs/spec/UNIFIED-BLUEPRINT.md §9`
- 前置: UBX-001（单节点围栏三要素）
