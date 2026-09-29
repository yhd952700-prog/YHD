# Verification-Plan — UBX-005-distributed-real-coordination

> 第二轮（对抗验证之后）已按 P0-1 / P0-2 / P0-3(a~d) / P1-1 / P1-2 逐条返工。
> 本文件**只勾真正被测过的框**；未验证的一律写出来，不涂改。

## 0. 第二轮修复清单（逐条状态）

| 编号 | 问题 | 状态 | 证据 |
|---|---|---|---|
| P0-1 | 裸 `PermissionError` 逃出围栏 | **已修** | `os.open` / 状态读写全部包进 try，任何 `OSError` → `CoordinationUnavailableError`；`DistributedExecutorLease` 边界再加一层，任何后端异常 → `ExecutorFenceBackendError`（`ExecutorFenceDenied` 子类）。`TestFailClosedIO`（5 条，含"每一个边界调用都被包住"） |
| P0-1 附带 | Windows 文件名含 `:` | **已修（新发现）** | `_safe_name` 把 `:` 等非法字符换成 `_` 并附短哈希。旧实现把 `executor:<id>` 直接拼进路径，Windows 上要么落成 NTFS **备用数据流**（单冒号，"碰巧能跑"），要么直接 `PermissionError [Errno 13]`（双冒号，如 `audit:writer`）。回归：`TestDistributedLockIsNowReal::test_two_clients_cannot_both_hold_the_same_key` |
| P0-2 | Windows 上读者阻塞写者 | **已修** | 裸 JSON sidecar + `os.replace` 换成 **SQLite(WAL)**（标准库，零新依赖）。回归：`TestConcurrentReadersAndWriter`（3 个真实读进程 + 1 个写进程，400 次写入） |
| P0-3(a) | 能力/纪元是进程内的 | **已修** | `granted_capabilities` 与 `era` 持久化进后端；`current().state` 能区分 `unknown` 与 `stale`。`TestBridgeStateIsBackendGlobal` |
| P0-3(b) | `count_active()` 是进程内的 | **已修** | 计数走后端全局；且 `DistributedExecutorLease(max_executors=N)` 把"数一次+获取一次"包进**全局准入临界区**，使 N+1 上限原子成立。`TestGlobalExecutorCap`（含 4 进程跨进程） |
| P0-3(c) | 两个进程能同时冒充同一 executor_id | **已修** | ① 夺权必须铸**严格更大**的令牌，绝不把现任令牌交给另一个 owner；② `my_last_token=None` 按 `-1` 处理（与 `SqliteExecutorLease` 一致）⇒ 拿不出身份证明的进程一律被 `FencedExecutorError` 拒绝。`TestOnlyOneProcessPerExecutorId` |
| P0-3(d) | `heartbeat()` 失败不可见 | **已修** | 返回续租后的令牌；失败抛 `StaleExecutorError` 并累加 `heartbeat_failures`。`TestHeartbeatFailureIsObservable` + `TestParityWithSqliteExecutorLease` |
| P1-1 | 变异 M1（删掉 release 的令牌校验）存活 | **已修** | 新增**文件后端**的"正确 owner + 陈旧令牌"测试；并把"锁竞争（可重试）"与"令牌不对（明确拒绝）"分开。变异复测：**KILLED** |
| P1-2 | Redis Lua 未验证 | **部分解决（见 §4）** | 装了 `fakeredis 2.38.0` + `lupa 2.8`，4 段 Lua **真的执行过**并新增 `TestRedisLuaScriptsActuallyExecute`（5 条）。但这两个包**未声明进 pyproject.toml**，CI 上仍会 skip；真 `redis-server` 依旧未验证。 |

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
      *第二轮修了一个真 bug：`force_new_era()` 曾是**空操作**（纪元租约没有行时
      `UPDATE` 影响 0 行），旧持有者照样通过校验 —— 该测试当时是**红的**。*
- [x] 同一 executor_id 同一时刻最多一个进程能通过校验（P0-3c）。
      → `TestOnlyOneProcessPerExecutorId`（3 条，含 4 进程并发）。
- [x] N+1 执行者上限**跨进程**成立（P0-3b）。→ `TestGlobalExecutorCap`。
- [x] 后端 I/O 故障 ⇒ 围栏拒绝，绝不漏裸 `OSError`（P0-1）。→ `TestFailClosedIO`。
- [x] 并发读不撕裂、写者能推进（P0-2）。→ `TestConcurrentReadersAndWriter`。

## 2. How to Prove

- **测试：** `tests/distribution/test_coordination.py`。
  双进程/多进程互斥用**真 subprocess**（`_spawn` 起 `sys.executable` 子进程），
  不是线程。崩溃用 `os._exit(9)` 模拟 kill -9。
  ```bash
  python -m pytest tests/distribution/ tests/kernels/execution/ -q
  # 199 passed, 3 skipped in 67.7s
  ```
- **变异验证（证明断言不是空转）：** 7 个变异，逐个改源码后跑对应子集。

  | 变异 | 结果 |
  |---|---|
  | M1 删掉 `FileLockLease.release` 的令牌校验 | **KILLED**（3 failed；第一轮它 SURVIVED） |
  | M2 删掉 `acquire` 的夺权围栏检查 | **KILLED** |
  | M3 撤掉 `os.open` 的失败即闭包装 | **KILLED** |
  | M4 撤掉 N+1 上限判定 | **KILLED** |
  | M5 `journal_mode=WAL` → `DELETE` | **SURVIVED** ⚠️ |
  | M6 能力授权不再读后端 | **KILLED** |
  | M7 心跳失败不再抛异常 | **KILLED** |

  **M5 为什么存活（如实说明）**：`_with_retry` + `busy_timeout` 会把 rollback
  journal 下的 "database is locked" 也重试成成功，所以**行为层面区分不出 WAL 与
  DELETE**。补了一条 `test_state_store_is_wal_so_readers_never_block_the_writer`
  把"我们确实选了 WAL"这个**设计选择**钉住 —— 它证明的是选择，不是运行时效果。
  SQLite(WAL) 的依据是验证者实测的裸 JSON 失败数据（3 读者 → 写者 0/400）与
  SQLite 文档语义。
- **Fail-closed：** `TestUnreachableBackendDenies`（5 条）——`backend="redis"` 指向
  死端口 6399 时 `get_distributed_lease()` **抛** `CoordinationUnavailableError`，
  且**不生成任何本地文件锁**（`test_redis_unreachable_does_not_silently_switch_to_file`
  断言目录保持为空）。另 `test_backend_down_denies_execution_instead_of_allowing_it`
  证 `ExecutorFence.acquire_for()` 在后端死时拒绝放行。
- **可观测性：** ☐ **未实现**。`get_stats()`（lock/election）会报租约持有者与状态，
  但没有 Prometheus 指标——租约持有/争用计数器与 fencing token 当前值的
  **指标导出尚未做**，故此项不勾选。（本轮新增的只有 `heartbeat_failures` 计数器。）

## 3. Negative Paths

- [ ] 网络分区 ⇒ 旧 leader 写入被拒（非双写）。
      **未验证。** 本环境无法制造真实网络分区；分区下的正确性依赖后端
      （Redis 侧 TTL / 文件锁 + 到期时间），没有可执行的分区注入测试。
- [x] 后端不可用 ⇒ 执行 deny（fail-closed）。→ `TestUnreachableBackendDenies`、
      `TestFailClosedIO`。
- [x] 后端**半死**（并发冷启动 / 权限故障）⇒ 仍是 deny，不是"best-effort 放行"。
      → `TestFailClosedIO::test_every_fence_boundary_call_is_guarded`
      （acquire / validate / current / heartbeat / renew / release / consume /
      known_executor 八个边界调用逐一注入故障）。

## 4. Evidence

- 测试输出（本轮，重复 2 轮一致，无 flaky）：
  ```
  $ python -m pytest tests/distribution/ tests/kernels/execution/ -q
  199 passed, 3 skipped in 67.7s        # run 1
  199 passed, 3 skipped in 66.9s        # run 2

  $ python -m flake8 src/distribution/ tests/distribution/ \
        --max-line-length=100 --select=E,F,W --ignore=E501,W503
  （无输出，clean）
  ```
- 3 项 skipped：
  1. `tests/distribution/test_bus_backends.py:522` —— 无本地 Redis（既有跳过，与本任务无关）
  2. `TestRealRedisEndToEnd` ×2 —— 需要真 `redis-server 127.0.0.1:6379`
- **Redis 路径的真实状态（P1-2，如实）**：
  * 本轮**装了** `fakeredis 2.38.0` + `lupa 2.8`（真 Lua 解释器），4 段 Lua
    **执行过且有断言**：`TestRedisLuaScriptsActuallyExecute`（5 条，本地全绿）：
    跨 client 互斥、错 owner/错令牌释放不生效、错令牌 `renew` 返回 `None`、
    `PX` 过期后才允许接管且令牌严格更大、桥接在 Redis 上的能力/纪元持久化。
  * 期间还修了一个真问题：Lua 里裸调 `redis.replicate_commands()` 在 fakeredis 上
    直接报错（未实现），已改成 `if redis.replicate_commands then ... end`
    （真 Redis 行为不变）。
  * **仍未验证**：真 `redis-server` 上的行为（含复制/故障切换语义）。
  * **仍未解决**：`fakeredis` / `lupa` 没进 `pyproject.toml`，CI 上那 5 条
    **照样 skip**。要变成"CI 也验证"需要声明依赖并重跑
    `uv pip compile pyproject.toml -o requirements.txt`（本轮不做，见 §6）。

## 5. 本轮改动范围

第一轮（实现）：

| 文件 | 改动 |
|---|---|
| `src/distribution/coordination.py` | **新增**。FencingToken / DistributedLease ABC / FileLockLease / RedisLease / DistributedExecutorLease / get_distributed_lease |
| `src/distribution/lock.py` | 重写为协调层客户端；`_try_acquire_memory` 已删除；`release` 校验令牌；`check` 过期返回 False |
| `src/distribution/election.py` | 改为基于租约；`is_leader` 查 TTL；`renew_lease` 真续租；`_stop_renewal` 真停线程 |
| `tests/distribution/test_coordination.py` | **新增** |
| `orphan-registry.yaml` | 移除 `src.distribution.lock` / `.election` 两条 |

第二轮（本轮，按对抗验证返工）：

| 文件 | 改动 |
|---|---|
| `src/distribution/coordination.py` | 状态存储换成 **SQLite(WAL)**；`_safe_name` 修 Windows 非法字符；`_OsMutex`/`_SqliteStore` 全面失败即闭；`bump_epoch` 改 upsert（原为静默无效）；新增**准入临界区**（N+1 上限原子化）；`my_last_token=None` 按 `-1` 处理；`heartbeat` 失败可见；`consume_token` 也纳入围栏边界；`DistributedLease` 新增 `count_live`；`_is_transient` 按 `winerror` 精确分类 |
| `tests/distribution/test_coordination.py` | 新增 9 组：`TestFailClosedIO`、`TestConcurrentReadersAndWriter`、`TestBridgeStateIsBackendGlobal`、`TestGlobalExecutorCap`、`TestOnlyOneProcessPerExecutorId`、`TestHeartbeatFailureIsObservable`、`TestReleaseWithCorrectOwnerButStaleToken`、`TestParityWithSqliteExecutorLease`、`TestRedisLuaScriptsActuallyExecute` |
| `docs/.../verification-plan.md` | 本文件 |

**未改动**（按约束）：`src/kernels/execution/fence.py`、`src/kernels/audit/**`。
`ExecutorFence` 未改一行即可跑在协调后端上。

## 6. 仍需后续处理 / 明确未验证

1. **需要 fence.py 改一行（我没有擅自改）**：
   `SqliteExecutorLease._acquire_within_locked`（`fence.py:494-497`）在
   `my_last_token is None` 时用 `-1` 兜底 ⇒ 只要租约行还在（**哪怕已过期**）就抛
   `FencedExecutorError`；而 `ExecutorFence.acquire_for`（`fence.py:768`）**从不传**
   `my_last_token`。后果：**租约过期后同一个 executor_id 永远无法重新接管**
   （重启即永久锁死）。桥接侧刻意不照抄这个行为（过期允许接管，铸更大令牌），
   差异已由 `TestParityWithSqliteExecutorLease::test_documented_divergence_expired_lease_can_be_reclaimed`
   固定下来。建议改法：`acquire_for` 传入当前已知 token，或让该分支先看过期。
2. **`ExecutorFence.acquire_for` 的上限检查是 check-then-act**（`fence.py:770-774`），
   本身不原子。本轮在桥接里用准入临界区补上了原子性，但**必须把
   `max_executors` 同时传给 `DistributedExecutorLease`**，否则上限仍有竞态
   （实测 4 进程 / 上限 2 → 放行 3~4 个）。
3. **fakeredis / lupa 未声明依赖** ⇒ CI 上 Redis Lua 仍 skip（§4）。
4. **真 Redis / 网络分区未验证**（§3、§4）。
5. **首次接线未完成**：`lock.py` / `election.py` 至今仍**无生产调用点**（只有测试引用）。
   UBX-005 的"谁在启动时 `set_executor_fence(DistributedExecutorLease(...))`"、
   以及 C15 deny 门的落点，仍未实施。
6. **etcd 未接入**：容器已声明（`docker-compose.infra.yml:86-123`），但无 Python 客户端、
   无代码连接。本轮实现的是 `file`（默认，跨进程）+ `redis`（跨节点）。
7. **CI 无 coordinator 服务**：`.github/workflows/ci.yml` 无 `services:` 块。
8. **可观测性指标**未做（见 §2）。
9. `deploy/cloud/src/distribution/` 下有字节一致的**生成副本**
   （`scripts/build_cloud_bundle.py`），改完 `src/` 后需**重新构建 bundle**；
   该目录不能手改，且仓库现有脚本不检测 bundle 与 src 的漂移。
10. **Windows 文件名**：`_safe_name` 现在会给每个名字附 10 位哈希，旧目录里
    形如 `executor:xxx.lock`（实为 NTFS 备用数据流）的残留不会被自动清理；
    换目录即彻底避开，无需迁移。

## 7. Commit

- 第一轮实现：`ac17bbbc`
- 第二轮返工（P0-1/P0-2/P0-3/P1-1/P1-2）：**见下一条提交（本文件与代码同提交，hash 自指故回填）**
