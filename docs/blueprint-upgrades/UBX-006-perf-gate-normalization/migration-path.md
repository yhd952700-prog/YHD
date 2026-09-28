# Migration-Path — UBX-006-perf-gate-normalization

## 1. Rollout Steps
1. 抽出集中 perf-gate 配置（容量/延迟/校验预算）。
2. 接 R-G2-01 有界队列 + R-G2-07 UNVERIFIED 映射。
3. event_id 升宽（C2-5）纳入规模上限 gate（U49）。
4. 链校验迁移到快照/离线路径（U50）。
5. 更新 UNIFIED-BLUEPRINT §7。

## 2. Compatibility
配置化改动；默认阈值取当前实测值，行为不变，仅加可观测 + 越界 fail-closed。

## 3. Rollback
revert 回散落常量（失去统一 gate），无数据迁移。

## 4. Approval Gate
- [x] **auto-apply**（GOVERNANCE §4）：可观测性 / gate 归一化。

## 5. Sequencing
依赖 UBX-001/005（围栏内运行校验）。可与 UBX-002/003 并行。
