# 故障面实测：单实例 / 休眠断网 / 时间跳变 / 存储故障 / 并发（2026-11-06）

> 口径：每类都按「复现步骤 / 实际结果 / 是否缺陷 / 修了什么或为什么没修 / 证据」写；
> 有缺陷的直接修，修完单跑相关套件报数字。没有真跑过的边界单独登记（见 §7），
> 不写成「已验证」。本轮**未改动**账号树与前端在改文件（`apps/console/**`、
> `scripts/acceptance/account_tree_e2e.py` 等），只改后端与对应测试。
>
> 环境：Windows 10.0.19045，仓库 venv `d:\LiuHao-AI-OS\.venv\Scripts\python.exe`
> （CPython 3.12.14）。所有复现脚本位于 `D:\cache\temp\`（仓库外，不提交）。

## 1. 单实例 / 多开

**复现步骤**

1. 独立数据目录 `%TEMP%\liuhao-instance-repro-*`，设 `LIUHAO_INSTANCE_ROOT` 指向它；
2. 起第一个网关：`PORT=18180 python -m src.gateway`，等 `/v1/health` 200；
3. 同一数据目录再起第二个：`PORT=18190 python -m src.gateway`；
4. 检查第二个退出码、stderr、18190 是否被监听、18180 是否仍在服务；
5. 结束第一个网关，2 秒后检查 18180 是否释放、锁是否可再次获取、`run/instance.json` 是否残留；
6. 组合面：托盘侧用 `GatewayService.start()`（`src/desktop/service.py`），在命令行实例持锁时调用，
   断言它**在拉起子进程之前**拒绝；启动器侧由 `tests/test_single_instance.py` 覆盖「第二个启动器」
   与「强杀启动器后网关自动退出」。

**实际结果**

- 第一个网关健康就绪 **3.5s**；
- 第二个进程 **exit=3**，stderr 最后一行：
  `[instance] 鎏灏已经在运行（pid=24292，http://127.0.0.1:18180，启动于 2026-11-06T00:52:18-08:00）。为避免两个进程同时写同一份数据，本次启动已退出；如需重启，请先关闭已经运行的那个鎏灏。`
- 第二个端口 **未被监听**（`second_port_listening=false`），第一个 **仍在服务**（`port_a_still_serving=true`）；
- 杀掉第一个后：**端口释放、锁可获取**（`a_port_closed=true`、`lock_probe_free=true`），
  `run/instance.json` 仍残留（`metadata_present=true`）——探针以**锁**为准，不会把残留元信息误判成"在运行"；
- 托盘组合：`GatewayService.start()` 返回 `False`，`last_error` 含「已经在运行」，且**没有**拉起子进程
  （`service._proc is None`）。

**是否缺陷**：**是**（修复前网关入口没有跨进程互斥：两个入口可以绑不同端口、同时对同一份
SQLite 数据目录写入，第二个实例只会在端口/数据库层面报裸错）。本轮补上单实例锁与父进程看护。

**修了什么**

- 新增 `src/common/instance_lock.py`：`<root>/run/instance.lock` 跨进程文件锁（Windows `msvcrt` /
  POSIX `fcntl`），`run/instance.json` 只作可读元信息；锁随进程死亡由内核释放。
- 新增 `src/common/parent_watch.py`：启动器把自身 pid 通过 `LIUHAO_PARENT_PID` 传给网关，父进程
  消失后网关置 `should_exit`，不留孤儿进程占端口。
- `src/gateway/__main__.py`、`src/desktop/_runner.py`：**绑定端口之前**拿锁；被占用时打印同一条
  可读提示并返回 3；端口真被其它程序占用时单独打印「端口 x:y 被其它程序占用…」。
- `src/desktop/service.py`：启动子进程前先 `probe()`，被占用则直接给出可读错误；子进程 env 带上父 pid。
- `scripts/start_liuhao.py`：先探测实例再选端口（避免「已自动改用 18081」+「已经在运行」自相矛盾）；
  就绪轮询后校验自己的子进程是否已退出，防止把第一个实例的健康响应误当成就绪。

**证据**

- [fault-instance-repro-2026-11-06.log](fault-instance-repro-2026-11-06.log)（真实双进程输出，含 pid/URL/退出码）；
- `tests/test_single_instance.py`：第二个进程可读拒绝、锁随 holder 死亡释放、父死子退、
  残留元信息不误报、**托盘在 CLI 持锁时拒绝且不拉子进程**。

## 2. 休眠唤醒 / 断网 / 网络切换

**复现步骤**

1. 起本地假 OpenAI 兼容服务（正常 / 断开 / TCP 连上但不响应三态）；
2. provider 直连：正常调一次 → 关掉假服务再调 → **同一实例**恢复服务后再调；
3. 「半开」：TCP 可连但服务端 10s 不响应（模拟网络切换后连接黑洞）；
4. 真网关进程 `psutil.suspend` 8 秒后 `resume`（模拟睡眠/唤醒），前后打 `/v1/health`，
   并连续请求两次看连接是否还健康。

**实际结果**

- 正常调用 **188ms** → `pong`；
- 断开后 **2000ms** 返回 `ConnectTimeout`（有界，不挂死）；同实例恢复服务后 **16ms** 又拿到 `pong`（自愈）；
- 半开：**2030ms** 返回 `ReadTimeout (read timeout=2.0)`，未挂死；
- 挂起 8s 后：`/v1/health` 唤醒前 **16ms / 200**、唤醒后 **0ms / 200**，再次请求 **0ms / 200**，
  body 含 `"status":"ok"`、`api_version`/`api_contract:"1"`；端口未丢。
- 桌面启动门的等待也有上限（`tests/desktop/test_startup_gate.py` 真进程 + 真 TCP，见 §6），
  不会无限停在「加载中」。

**是否缺陷**：**否**（这一面现有实现已有有界超时与自愈；本轮未发现需要修改的行为）。

**为什么没修**：没有可证实的缺陷就不动代码。登记两个复现边界：真正 OS 睡眠/唤醒未执行
（共享机器上会影响其它正在跑的进程，用 8s 进程挂起模拟）；「断网」用本地服务断开/黑洞模拟，
未拔网线/切 Wi-Fi（见 §7）。

**证据**：[fault-network-sleep-repro-2026-11-06.log](fault-network-sleep-repro-2026-11-06.log)。

## 3. 系统时间跳变

**复现步骤**

1. 把抗跳变时钟锚点固定在 `(1000.0s 墙钟, 0.0s 单调钟)`，然后分别把墙钟向前跳 1 小时、
   向后拨 1 天，观察 `stable_now_epoch()`；
2. 生成 JWT 后把墙钟向前跳 1 年 / 向后拨 1 年，验证令牌仍可用；再吊销令牌后向前跳 10 年，
   验证仍被拒；再验证真正过期的令牌仍被拒；
3. 审计链：连续写 3 条事件，中间把墙钟回拨 1 年，`verify_integrity()` 必须仍为 `True`；
4. 权威审计链（`src/kernels/audit`，SQLite `seq`/`prev_hash`）不依赖时间戳，随相关套件回归。

**实际结果**

- `stable_now_epoch()` 在 ±1h / ±1d 跳变下都返回单调推算值（1010s），正常走时（±阈值内）跟随墙钟；
- JWT：前进 1 年、后退 1 年都仍通过校验；吊销后前进 10 年仍 `revoked`；真过期令牌仍抛
  `ExpiredSignatureError`；
- 审计链回拨后 `verify_integrity()=True`。

**是否缺陷**：**是，两处**：
1. `PyJWT` 默认 `verify_iat=True`。时钟**回拨**后已签发令牌的 `iat` 会变成"来自未来"，
   即使用单调钟判定 `exp` 仍有效，也会被 `ImmatureSignatureError` 拒绝——用户会被踢下线；
2. 旧版审计存储 `src/audit/store.py` 只在链为 `None` 时重建；**第二次 emit 起链长度就落后**，
   `verify_integrity()` 立即变 `False`，要等进程重启重载才自愈（静默的完整性失败）。

**修了什么**

- `src/security/jwt_handler.py`：两处 `jwt.decode` 显式 `verify_iat=False`（存在性仍由
  `require_iat` 保证），`exp` 继续用 `stable_now_epoch()` 手工比对；吊销库清理/查询同用稳定时钟；
- `src/audit/store.py`：链长度与事件数不一致就重建（幂等、只依赖事件内容），`emit` 写盘失败仍
  回滚内存事件并 `raise`（fail-closed）；
- `src/_time.py`（上一轮已落）：`time.monotonic()` + 墙钟锚点，偏差 > 60s 视为跳变，继续用单调推算值。

**证据**：`tests/test_fault_hardening.py` 中 5 条时间用例（前进/后退/吊销/过期/审计回拨）全绿；
权威链回归见 §6。

## 4. 磁盘满 / 数据目录只读 / 文件被占用

**复现步骤**（`D:\cache\temp\repro_disk.py`，6 个 case）

1. 审计文件设只读（`os.chmod`）→ `emit` 新事件；
2. 注入 `os.replace` 抛 `OSError(ENOSPC)` → `emit`；
3. 独占打开审计文件 → `emit`；
4. `icacls` 把目录设只读 → 创建模型配置；
5. `icacls` 只读数据目录 → 启动器 `prepare_environment()`；
6. 另一个连接 `BEGIN EXCLUSIVE` 锁住记忆库 → `MemoryStore.persist()`。

**实际结果**

| case | 是否拒绝 | 旧数据 | 附加事实 |
|---|---|---|---|
| `audit-readonly` | 是，`PermissionError [WinError 5]` | `previous_file_intact=true` | `in_memory_event_rolled_back=true` |
| `audit-enospc` | 是，`OSError [Errno 28]`（注入） | `previous_file_intact=true` | 内存事件已回滚 |
| `audit-locked` | 是，`PermissionError [WinError 5]` | `previous_file_intact=true` | 走 `atomic_write_text → os.replace` |
| `model-config-readonly-dir` | 是，`ModelConfigError 503`：`无法打开模型配置库 …：unable to open database file。磁盘可能已满、数据目录只读，或文件被其它程序独占；为避免写坏已有配置，本次操作已拒绝。` | 未触碰旧库 | 可读中文原因 |
| `launcher-readonly` | 是，`DataDirectoryUnavailable`：`无法创建数据目录 …（PermissionError…）。请确认磁盘没有写满、路径没有被只读挂载，或用环境变量 LIUHAO_DATA_DIR 指向另一个可写目录。` | 未写任何运行数据 | fail-closed |
| `memory-locked` | 是，`MemoryStoreError`：`无法写入记忆库 …（磁盘可能已满、数据目录只读或文件被占用）：database is locked。本次记忆未保存，已有记忆保持不变。` | `old_row_intact=true` | 内存态回滚 |

**是否缺陷**：**是，三处**：审计原为直接 `open('w')+json.dump`（中途失败会把整库截断）；
模型配置写失败抛裸 `sqlite3` 异常；记忆写入抛裸 `sqlite3.OperationalError` 且内存里已插入的条目
不回滚（"调用方收到失败、内存却认为已记住"）。

**修了什么**

- 审计：`_save()` 改 `atomic_write_text`（临时文件 + `os.replace` + fsync）；`emit` 失败回滚内存
  并重新计算链；链长度修复见 §3。
- 模型配置：建连/schema/写操作失败统一包装成 `ModelConfigError(503)`，文案带「已有配置保持不变」，
  失败时 `rollback()`；读-改-写在同一个 `RLock` 内完成（见 §5）。
- 记忆：`MemoryStore` 新增 `MemoryStoreError`（可读中文原因），构造/`persist`/`delete`/`clear`
  统一包装并回滚事务；`MemoryKernel` 的 `store/compress/auto_cleanup/clear` 在落盘失败时**回滚内存态**。
- 启动器：数据目录不可写 → `DataDirectoryUnavailable`，打印 `[FAIL]` + 「已停止启动，未写入任何运行数据」；
  旧的「历史数据迁移」失败降级为非致命警告，避免把"旧文件被占用"误判成"目录不可写"。

**证据**：[fault-disk-repro-2026-11-06.log](fault-disk-repro-2026-11-06.log)（6 个 case 原始 JSON）；
`tests/test_fault_hardening.py` 中审计/记忆用例。

## 5. 并发一致性

**复现步骤**

1. 两个线程同时向同一个 conversation 追加各 200 条（模拟两个窗口同时发消息）；
2. 两个线程同时修改同一个模型配置的**不同字段**（`timeout` 与 `base_url`），用 Barrier 对齐起跑；
3. 升级互斥：一个线程持有 `_exclusive`，另一个线程再进；以及"另一个进程"先持 OS 锁的情形。

**实际结果**

- 对话：期望 400 条，实际 **400 条**，重复内容 **0**；
- 模型配置：两个并发更新都保留（`timeout=45.0` 且 `base_url=http://127.0.0.1:9`），无异常；
- 升级：第二个线程得到 `ApplyError: 另一个升级操作正在进行中，请等它结束后重试`；模拟"另一个窗口/
  命令行进程"持锁时得到 `另一个升级操作正在进行中（可能是另一个窗口或命令行进程）…`；
- 防回归：两个线程各改不同字段的单元测试稳定通过（不再出现 lost update）。

**是否缺陷**：**是，一处确定性缺陷 + 一处补强**：
1. 模型配置 `update()` 原先在锁外 `get_public()`、再进锁写库——两个窗口同时改不同字段会互相覆盖
   （lost update）；本轮把"读-改-写"整体移入同一把 `RLock`；
2. 升级 `apply/rollback` 原先只有进程内假设、无跨进程互斥；本轮新增"线程锁（非阻塞）+
   跨进程 `InstanceLock`"的 `_exclusive`，第二个执行体得到可读拒绝，不会两个执行体同时备份/替换文件。
- 对话追加原本已有 `threading.RLock` + 单条 INSERT/commit，本轮实测未丢消息；跨进程由 SQLite WAL 串行化。

**证据**：[fault-concurrency-repro-2026-11-06.log](fault-concurrency-repro-2026-11-06.log)；
`tests/test_fault_hardening.py`（模型配置并发、升级跨线程/跨进程互斥）。

## 6. 本轮相关套件单跑数字

命令（仓库 venv）：

```
.venv\Scripts\python.exe -m pytest \
  tests/test_single_instance.py tests/test_fault_hardening.py tests/test_start_liuhao.py \
  tests/desktop tests/test_jwt_revocation_and_rate_limit.py tests/test_jwt_revoke_all.py \
  tests/security/test_jwt_kernel.py tests/test_audit_thread_safety.py tests/test_audit_isolation.py \
  tests/test_audit_single_source_of_truth.py tests/kernels/audit tests/test_model_config.py \
  tests/test_upgrade_apply_rollback.py tests/test_upgrade_api.py tests/test_upgrade_console_contract.py \
  tests/kernels/memory -q --timeout=200
```

结果：**501 passed, 19 skipped, 2 warnings in 230.93s**。
其中新增故障面用例 **16 passed**（`test_single_instance.py` 5 + `test_fault_hardening.py` 11，4.59s）；
桌面真进程/真 TCP 子集 **34 passed in 49.48s**。

本轮为了让"多网关"测试替身彼此不抢锁，给 `tests/desktop/test_startup_gate.py`、
`tests/desktop/test_desktop_system_layer.py` 的 runner 加了独立 `LIUHAO_INSTANCE_ROOT`；
`tests/test_start_liuhao.py` 的 `main()` 输出用例把实例探测置为 `None`（它们不依赖本机真实实例）。
这些是**测试隔离**，不改变生产判定。

## 7. 未执行/不可造的边界（登记，不消失）

| 边界 | 原因 | 现有替代证据 |
|---|---|---|
| 真正 OS 睡眠/唤醒 | 共享机器，会中断其它正在跑的进程/验收 | 网关进程 suspend 8s + 端口/健康实测（§2） |
| 真拔网线 / 切 Wi-Fi | 同上 | 本地假服务断开与"TCP 连着但不响应"黑洞（§2） |
| 真实 `ENOSPC` 磁盘满 | 本机无独立可写满卷，格式化需要管理员且会破坏环境 | 注入 `OSError(ENOSPC)`，验证原子写与回滚路径（§4） |
| 全局系统时钟跳变 | 改全局时钟会影响同机其它进程 | 进程内锚点 + 墙钟跳变模拟，覆盖 JWT/审计语义（§3） |
| 两个真实浏览器窗口 E2E | 账号树/前端正被另一路修改，按指示不动 | 同进程双线程并发 + 跨进程实例锁（§5） |
| 真实托盘 GUI 点击 | 无头 CI 无法点托盘 | `GatewayService.start()` 在锁被占时的真实拒绝 + 不拉子进程（§1） |

## 8. 本轮改动文件（仅后端与本轮测试）

`src/common/instance_lock.py`(新)、`src/common/parent_watch.py`(新)、`src/gateway/__main__.py`、
`src/desktop/_runner.py`、`src/desktop/service.py`、`scripts/start_liuhao.py`、
`scripts/launch_installed.py`、`src/_time.py`、`src/security/jwt_handler.py`、
`src/audit/store.py`、`src/ai/model_config.py`、`src/upgrade/manager.py`、
`src/kernels/memory/__init__.py`、`src/kernels/memory/store.py`、`.gitignore`；
测试 `tests/test_single_instance.py`(新)、`tests/test_fault_hardening.py`(新)、
`tests/test_start_liuhao.py`、`tests/desktop/test_startup_gate.py`、`tests/desktop/test_desktop_system_layer.py`。
