# Migration-Path — UBX-005-distributed-real-coordination

## 1. Rollout Steps
1. 实现 `DistributedLease` + `FencingToken`（默认本地锁后端）。
2. 实现 leader election；失主释放租约、token 递增。
3. 替换 `src/distribution/lock.py` 存根调用点。
4. UBX-001 单节点围栏挂接协调层。
5. 更新 UNIFIED-BLUEPRINT §3 C15。

## 2. Compatibility
默认本地锁与现有单节点行为兼容；引入可选 etcd/redis 不强制。

## 3. Rollback
revert 回存根（失去跨进程强制），无数据迁移。

## 4. Approval Gate
- [x] **auto-apply**（GOVERNANCE §4）：基础设施机制，无 HC-01。

## 5. Sequencing
依赖 UBX-001（围栏三要素）。承接 C3 联邦（R-G3-09）。
