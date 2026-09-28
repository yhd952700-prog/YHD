# Verification-Plan — UBX-005-distributed-real-coordination

## 1. Done Criteria

- [x] 两进程争同一租约，仅一持锁；另一被拒。
      → `tests/distribution/test_coordination.py::TestTwoProcessesContend`
      `test_exactly_one_holder_across_separate_os_processes`（**4 个真实 subprocess**
      抢同一租约，断言恰好 1 个 `ok=True`，且 4 个 pid 互不相同）、
      `test_loser_gets_false_not_a_fake_token`。
- [x] 持有者崩溃 ⇒ 租约超时释放，新 leader 的 fencing token 严格大于旧。
      → `TestCrashTakeover::test_dead_holder_is_replaced_with_a_strictly_greater_token`。
      工人进程用 `os._exit(9)`（等价 kill -9，不跑 finally / 不释放锁）自杀；
      断言 (a) **到期之前不允许提前夺权**，(b) 到期后接管者令牌 `>` 死者令牌。
      另见 `test_tokens_never_reuse_a_dead_holders_value`。
      *口径说明：证的是**租约**接管。选主走同一个 `DistributedLease`，其接管路径
      由 `TestLeaderElectionIsLeaseBased::test_step_down_lets_another_take_over_with_a_greater_token`
      覆盖（令牌严格变大），但未单独做"leader 进程被 kill -9"的端到端。*
- [x] 陈旧 leader 用旧 token 写入 ⇒ 被拒（R-G3-09）。
      → `TestStaleTokensAreRejected`（过期 / 被取代 / 他人令牌三类）、
      `TestExecutorFenceRunsOnTheCoordinationBackend::test_a_superseded_executor_cannot_act`
      （`force_new_era()` 后旧持有者经 `ExecutorFence.enforce()` 被拒）、
      `test_force_new_era_invalidates_every_live_lease`、`test_released_executor_is_unknown_again`。

## 2. How to Prove

- **测试：** `tests/distribution/test_coordination.py`（50 passed / 2 skipped）。
  双进程互斥用**真 subprocess**（`_run_workers` 起 `sys.executable` 子进程），
  不是线程。崩溃用 `os._exit(9)` 模拟 kill -9。
  ```bash
  python -m pytest tests/distribution/ tests/kernels/execution/ -q
  # 165 passed, 3 skipped in 37.8s
  ```
  变异验证（证明断言非空转）：把 `FileLockLease.release` 的令牌校验去掉 →
  `TestReleaseValidatesTheToken` 2 条失败；把 `acquire` 的持有者检查去掉 →
  `TestTwoProcessesContend` 等 5 条失败。
- **Fail-closed：** `TestUnreachableBackendDenies`（5 条）——`backend="redis"` 指向
  死端口 6399 时 `get_distributed_lease()` **抛** `CoordinationUnavailableError`，
  且**不生成任何本地文件锁**（`test_redis_unreachable_does_not_silently_switch_to_file`
  断言目录保持为空）。另 `test_backend_down_denies_execution_instead_of_allowing_it`
  证 `ExecutorFence.acquire_for()` 在后端死时拒绝放行。
- **可观测性：** ☐ **未实现**。`get_stats()`（lock/election）会报租约持有者与状态，
  但没有 Prometheus 指标——租约持有/争用计数器与 fencing token 当前值的
  **指标导出尚未做**，故此项不勾选。

## 3. Negative Paths

- [ ] 网络分区 ⇒ 旧 leader 写入被拒（非双写）。
      **未验证。** 本环境无法制造真实网络分区；分区下的正确性依赖后端
      （Redis 侧 TTL / 文件锁 + 到期时间），没有可执行的分区注入测试。
- [x] 后端不可用 ⇒ 执行 deny（fail-closed）。
      → 见上 `TestUnreachableBackendDenies` 与
      `test_backend_down_denies_execution_instead_of_allowing_it`。

## 4. Evidence

- 提交 hash：见 §7（本文件与代码同提交）
- 测试输出：
  ```
  $ python -m pytest tests/distribution/ tests/kernels/execution/ -q
  165 passed, 3 skipped in 37.82s
  （重复 3 轮均为 165 passed / 3 skipped，无 flaky）

  $ python -m pytest tests/distribution/test_coordination.py -q
  50 passed, 2 skipped in 29.63s

  $ python -m flake8 src/distribution/ tests/distribution/ \
        --max-line-length=100 --select=E,F,W --ignore=E501,W503
  （无输出，clean）
  ```
- 2 项 skipped = `TestRealRedisEndToEnd`（需要真实 redis-server 127.0.0.1:6379，
  本环境无）。**这意味着：RedisLease 的 4 段 Lua 脚本（acquire / release
  compare-and-delete / renew / validate）的运行时语义在本环境没有被执行验证**；
  `TestRedisLeaseWiring` 只验证了传给 Redis 的 KEYS/ARGV 与返回值解释。

## 5. 本轮改动范围

| 文件 | 改动 |
|---|---|
| `src/distribution/coordination.py` | **新增**。FencingToken / DistributedLease ABC / FileLockLease / RedisLease / DistributedExecutorLease / get_distributed_lease |
| `src/distribution/lock.py` | 重写为协调层客户端；`_try_acquire_memory` 已删除；`release` 校验令牌；`check` 过期返回 False |
| `src/distribution/election.py` | 改为基于租约；`is_leader` 查 TTL；`renew_lease` 真续租；`_stop_renewal` 真停线程 |
| `tests/distribution/test_coordination.py` | **新增**，50 条 |
| `orphan-registry.yaml` | 移除 `src.distribution.lock` / `.election` 两条（已重新被引用，留着会让守护报 stale） |

**未改动**（按约束）：`src/kernels/execution/fence.py`、`src/kernels/audit/**`。
`ExecutorFence` 未改一行即可跑在协调后端上。

## 6. 仍需后续处理（本轮未做）

1. **首次接线未完成**：`lock.py` / `election.py` 至今仍**无生产调用点**（只有测试引用）。
   UBX-005 的"谁在启动时 `set_executor_fence(DistributedExecutorLease(...))`"、
   以及 C15 deny 门的落点，仍未实施。
2. **etcd 未接入**：容器已声明（`docker-compose.infra.yml:86-123`），但无 Python 客户端、
   无代码连接。本轮实现的是 `file`（默认，跨进程）+ `redis`（跨节点）。
3. **CI 无 coordinator 服务**：`.github/workflows/ci.yml` 无 `services:` 块，
   `TestRealRedisEndToEnd` 在 CI 上同样会 skip。
4. **可观测性指标**未做（见 §2）。
5. `deploy/cloud/src/distribution/` 下有字节一致的**生成副本**
   （`scripts/build_cloud_bundle.py`），改完 `src/` 后需**重新构建 bundle**；
   该目录不能手改，且仓库现有脚本不检测 bundle 与 src 的漂移。

## 7. Commit

- 实现提交：`ac17bbbc`（`feat(ubx-005): real distributed coordination
  (lease + fencing token + election)`，分支 `p36`，父提交 `589315de`）
- 本文件 §7 的 hash 回填提交在其之后（自指，故单列一行）。
