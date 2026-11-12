# ADR — UBX-006-perf-gate-normalization: 性能 gate 归一化（容量 / 延迟 / SLO 门槛）

> **Status:** PROPOSED
> **Date:** 2026-09-28
> **Owner:** quality-reliability (qr-baseline) + os-systems
> **Type:** auto-apply（可观测性 / gate 归一化；不触及 HC-01 级人类主权事件）

## 1. Context（为什么）
审计/证据链存在三类**性能即正确性问题**（UNKNOWN-TO-OWNER）：
- **U48**：已提交审计事件可在掉电时丢失——修复需 batch，但 batch 引入延迟（R-G2-01）。
- **U49**：`event_id` 仅 48 位，百万事件量级下证据被**静默丢弃**。
- **U50**：定时完整性校验本身就是可用性中断，且时长随链增长。
这些不是"慢一点"的小事——它们直接决定 fail-closed 是否会误伤（R-G2-01 把延迟变 deny）或漏报（U49）。

当前缺**统一的性能 gate 语言**：容量下限、延迟 SLO、链校验预算散落各处，无法判定"还安全吗"。

## 2. Decision（决策）
归一化性能 gate：
1. 定义系统级 **容量下限 / 延迟 SLO / 链校验预算** 三件套，集中配置（非散落常量）。
2. 每条 gate 明确**越过时的 fail-closed 行为**：容量跌破证据通道保持速率 ⇒ R-G2-01 deny（正确升级）；延迟越 SLO ⇒ 告警 + 保守降级；链校验超预算 ⇒ 报 UNVERIFIED（R-G2-07），不假装通过。
3. **U49** 归入本 UBX：event_id 升宽（C2-5 已规划）作为 gate 的"规模上限"硬约束，越界即 deny/告警。
4. gate 运行在**快照/离线**路径，绝不阻塞实时证据写入（呼应 U50）。

## 3. Consequences（后果）
- **正向：** 把 U48/U49/U50 从"潜伏问题"变为"可观测 + fail-closed"的 gate；消除静默丢弃（U49）。
- **负向：** 容量跌破时更多 deny（预期内正确收紧）；需维护 gate 配置。
- **不做：** 性能退化静默通过 = 假绿 / 静默丢证据。

## 4. References
- UNKNOWN-TO-OWNER: `docs/autonomous/UNKNOWN-TO-OWNER.md#u48`、`#u49`、`#u50`
- RISK-REGISTER: `docs/autonomous/RISK-REGISTER.md` R-G2-01（队列有界阻塞）、R-G2-07（UNVERIFIED 不伪绿）
- 蓝图索引: `docs/spec/UNIFIED-BLUEPRINT.md §9`
