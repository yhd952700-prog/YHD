# Migration-Path — UBX-001-executor-fence

## 1. Rollout Steps
1. 非人类 `WorldInterface` 构造点已全量置 `actor="autonomous"`（`17ca073c`，回归守卫已锁）。
2. 引入 `executor_id` 注入：agent 框架在 spawn 时绑定父 `executor_id + lease`。
3. 引入 `fencing token`：租约接管单调递增；写/关键动作校验 token 为当前值。
4. 死进程检测（Fix A，sec-impl）：租约心跳 + 陈旧写者安全取代，使 C-6 真正 inert（解决 U47）。
5. 更新 UNIFIED-BLUEPRINT §3 C13 + §5 安全链图（手动步骤，见 §9 接线规则）。

## 2. Compatibility / 兼容
- 人类 L-Core（`lcore.py:131-132`）保留默认放行，**不回归**；仅非人类能力强制围栏。
- 既有测试需补围栏三要素的构造，否则新 gate 会 deny（符合预期，属正确性收紧）。

## 3. Rollback / 回滚
- 围栏为**纯收紧**，无数据迁移；回滚即 revert 对应提交，执行链回到逐点 default-deny 状态，不会更松。
- Fix A（死进程检测）可逆；Fix B（UBX-001 在 `capability.register` 的有界 carve-out）属 **human-decision-gated**，见下。

## 4. Approval Gate
- [x] **auto-apply**（GOVERNANCE §4）：围栏收紧与已实现能力登记，不触及 HC-01 级主权。
- [ ] **human-decision-gated** 仅限：U47 的 **Fix B**（`capability.register` 有界 carve-out 边界）——
      属 owner 安全姿态决策，团队**不得**自动实现（GOVERNANCE §3）。

## 5. Sequencing / 顺序依赖
- 依赖：UBX-005（跨节点围栏）在单节点围栏稳定后承接。
- 被依赖：UBX-002（sandbox fail-closed）、UBX-003（secrets fail-closed）均在围栏之内运行。
