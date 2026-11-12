# Verification-Plan — UBX-001-executor-fence

> 验证纪律（GOVERNANCE §2.2）：**VERIFIED 仅当被独立证明**。禁止"假装成功"。

## 1. Done Criteria（完成判据）
- [x] 每个非人类执行站点构造时携带可验证 `executor_id`（uuid，非 PID）— 无匿名执行主体；匿名 ⇒ `ExecutorUnknownError` → `PolicyDeniedError`。
- [x] 租约过期/撤销后，该主体动作被 deny（非 best-effort 放行）— `LeaseExpiredError` / `StaleExecutorError`。
- [x] fencing token 非当前值 ⇒ 动作被拒绝 — `validate()` 的 token 比对 + 全局单调 token 序列。
- [ ] U47 的"强制自锁"回归消除：`capability.register` 在跨进程陈旧写者下 **inert**（Fix A 落地）。
      → **未闭合**：UBX-001 本波未触及 `capability.register` 的跨进程陈旧写者路径；已确认全局 `LIUHAO_EXECUTOR_FENCE=on` 会把
      `capability.register` 一并武装（在 `ActionExecutor.__init__` 引导期触发），因此生产改为逐 action 白名单
      （`_FENCE_ACTIONS`）武装，本项留作后续独立工作。

## 2. How to Prove（怎么证明）
- **测试：** `tests/kernels/execution/test_fence.py`（20 cases，逐项对应 16 项关注），每条断言的是"动作被 BLOCKED"，
  不是"代码存在"。关键用例：
  - `test_executor_id_persists_across_calls_not_pid`（身份非 PID）
  - `test_token_strictly_increasing`（单调 token + release 后旧 token 失效）
  - `test_stale_executor_expired_denied` / `test_missing_heartbeat_is_stale`（过期/心跳缺失 ⇒ deny）
  - `test_restart_invalidates_old_lease` / `test_recovered_executor_reacquires_and_orphan_reclaimed`（重启/崩溃恢复）
  - `test_pid_reuse_does_not_fence_wrong_executor`（PID 复用不误伤）
  - `test_replay_same_token_denied`（重放）
  - `test_n_executors_ok_nplus1_denied` / `test_concurrent_distinct_executors_allowed`（并发 N+1 上限）
  - `test_capability_escalation_denied`（能力升级）
  - `test_split_brain_only_one_winner`（脑裂：仅一个 token 通过 validate）
  - `test_force_new_era_invalidates_all_leases`（epoch 代际）
  - `test_audit_unavailable_denies`（审计不可用 ⇒ fail-closed）
  - `test_authorization_revoked_at_execution_denies`（授权新鲜度）
  - `test_fence_fields_in_audit_record`（审计联动：executor_id/fence_token/lease_epoch 落链）
  - `test_decorator_gate_*`（`@kernel_action` 闸门集成，允许/拒绝双向）
- **Fail-closed 检查：** 以上每条 deny 路径均由独立运行证明（见 §4 测试输出）。
- **可观测性：** `get_executor_fence_total()` 暴露累计围栏拒绝计数；降级/拒绝均经 `ExecutorFenceDenied` 子类区分原因。
  `audit_lock_hold_seconds_last`（RISK-REGISTER A-01，待认领 owner）仍待落地。

## 3. Negative / Error Paths（必须测的错误路径）
- [x] 伪造/重放的 `executor_id` 被拒（身份不可伪造；跨 id 借用 token ⇒ deny）。
- [x] 租约心跳中断 ⇒ 陈旧写者被取代（`_is_live` 心跳超时 ⇒ takeover + epoch+1）。
- [x] 围栏校验自身失败时 ⇒ 默认 deny（fail-closed），不是 fail-open（缺失身份/未知执行者/审计不可用均拒绝）。

## 4. Evidence to Record
- 提交 hash：`c003190f`（`feat(executor-fence): Agent-Safety Execution Fence (P0 / D19-D21)`，分支 `p36`，未推送）
- 测试输出：
  - `tests/kernels/execution/test_fence.py` → **20 passed**
  - `tests/kernels/execution/` + `tests/kernels/audit/` → **255 passed**
  - 跨模块（`@kernel_action` / `world_interface` / `host_command` / `broker`）→ **352 passed**
  - flake8（E,F,W / max-line 100 / ignore E501,W503）→ 全部 touched 文件 **clean**
- 关联：`UBX-002`（沙箱隔离 fail-closed）、`UBX-005`（跨节点协调：本围栏当前为单节点，SQLite 为唯一真相源）。
