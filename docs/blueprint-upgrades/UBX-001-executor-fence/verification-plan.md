# Verification-Plan — UBX-001-executor-fence

> 验证纪律（GOVERNANCE §2.2）：**VERIFIED 仅当被独立证明**。禁止"假装成功"。

## 1. Done Criteria（完成判据）
- [ ] 每个非人类执行站点构造时携带可验证 `executor_id`（无匿名执行主体）。
- [ ] 租约过期/撤销后，该主体动作被 deny（非 best-effort 放行）。
- [ ] fencing token 非当前值 ⇒ 写入/关键动作被拒绝。
- [ ] U47 的"强制自锁"回归消除：`capability.register` 在跨进程陈旧写者下 **inert**（Fix A 落地）。

## 2. How to Prove（怎么证明）
- **测试：** `tests/test_world_interface_actor_wiring.py` 已锁 `actor="autonomous"`；扩展为"无围栏三要素 ⇒ deny"的断言。
- **Fail-closed 检查：**
  - 场景：`executor_id` 缺失 / 租约过期 / fencing token 陈旧 ⇒ 系统 **deny**，绝不 carry-on + 报成功。
  - 场景（U47）：构造跨进程 `StaleWriterError` ⇒ `capability.register` 应 **inert/安全失败**，不是强制证据自锁。
- **可观测性：** `audit_lock_hold_seconds_last`（RISK-REGISTER A-01，待认领 owner）应在 >1s 告警，用于识别围栏死锁而非静默 deny。

## 3. Negative / Error Paths（必须测的错误路径）
- [ ] 伪造/重放的 `executor_id` 被拒（身份不可伪造）。
- [ ] 租约心跳中断 ⇒ 陈旧写者被**取代**而非双写（Fix A）。
- [ ] 围栏校验自身失败时 ⇒ 默认 deny（fail-closed），不是 fail-open。

## 4. Evidence to Record
- 提交 hash：`<待本波代码提交后回填>`
- 测试输出（EXIT 0 摘要）：`<回填>`
