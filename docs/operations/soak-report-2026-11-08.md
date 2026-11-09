# LiuHao AI OS 长稳与恢复能力复测报告（2026-11-08）

> 承接 [2026-11-07 报告](./soak-report-2026-11-07.md)。前半程只做测量与登记；后半程按用户点名的
> 四条缺陷（D-1/D-2/D-4 + 保留策略与 RSS）修复了**被点名模块与测试**，改动与证据集中在 §12，
> 未改 `dist\`、未跑 `setup.exe`。
> 数据与脚本全部在 `D:\cache\temp\liuhao-fix\`（隔离 run 目录，独立端口 40001-40021 / 诊断口 39991-39999）。

## 0. 结论速览

### 0.1 四指标是否"趋零"（同一 30 分钟窗口口径）

判据按本轮要求：**斜率归零才算解决；持平为正一律写"未解决"**。

| 指标 | 改前基线 `base60`（0-10/10-20/20-30 分钟） | HEAD `head60b`（同口径） | 当前树 `p36short`（同口径） | 判定 |
|---|---|---|---|---|
| 句柄斜率 | +105.8 / +182.3 / **+220.5** 个/小时（全程 +166 个） | +84.0 / +198.0 / -99.4 个/小时（全程 +162 个） | +7.6 / -7.9 / +7.2 个/小时，**30 分钟净 -2 个（933→931）**；5-30 分钟段 **+3.7/h** | **收敛（抖动级，严格说非数学零）** |
| RSS 斜率 | +47.8 / +38.7 / +23.6 MB/h（全程 +34 MB） | +13.9 / +22.5 / -11.9 MB/h（全程 +13 MB） | +31.5 / +65.3 / +6.7 MB/h（全程 +15 MB）；10-30 分钟 **+24.5 MB/h** | **未解决（仍为正）** |
| 审计库斜率（db+wal+shm） | db 全程 **+22.2 MB/小时**，wal +5.9 MB/小时；post-warmup +23.8 MB/h | db 全程 **+22.6 MB/小时**；post-warmup +24.1 MB/h | db+wal 从 10.0→20.2 MB（**+10.3 MB / 30 分钟 ≈ +20.5 MB/h 平均**），10-30 分钟段 **+2.97 MB/h** | **未解决**（默认保留策略下仍线性增长；启用 6000 行裁剪时也只能压到 +2.97 MB/h，非零） |
| verify p95（审计校验） | 304 / 565 / **708** ms | 73 / 112 / **61** ms | 58 / **392** / **390** ms | **未解决**（比改前好，但相对 HEAD 劣化约 6 倍，且非零） |

### 0.2 恢复能力四项（本轮新增）

| 场景 | 结论 | 关键数字 |
|---|---|---|
| 空闲 2 小时后首请求 | **可下的结论：空闲期零增长、不泄漏；系统会把工作集从 185 MB 回收到 41.7 MB。** 但"2 小时后首请求"这一格被外部清理打断（原探针 t=85.1 min 消失，与 24h#5 同刻），已重跑（`resil_idle2`，120 分钟真实空闲，ETA 16:49）；冷启动代价用加速模拟给出 | 空闲 0→80 min：RSS 185.0→41.7、句柄 780→770 后恒平、audit db/wal/goals **Δ=0**；`EmptyWorkingSet` 后首 chat 10.5 s vs 热态 1.4 s、首 goal 7.5 s vs 3.06 s |
| 睡眠→唤醒（以整棵进程树冻结 600 s 模拟） | **活，不用重启**：SQLite 连接未坏、写路径恢复、检查点定时任务继续 | 唤醒后 2 s 内补跑检查点（覆盖 seq 385-957）；health 200/11.6 ms；verify 200/439 ms；goal 200/1023 ms |
| 网络断开→恢复 | **自愈，不用重启**：中继恢复后第 1 次调用即成功 | 断网期间 2 次调用均 HTTP 200 但 `body.status=error`（模型降级）；恢复后首次 200、"在线"，8.59 s |
| 长时间空闲后内存被系统回收 → 首请求变慢 | **确有冷启动代价**：工作集被回收后首请求显著变慢 | 空闲 35 分钟 RSS 从 185 MB 自降到 63.5 MB（无人为干预）；EmptyWorkingSet 后 RSS 188→2.7 MB，首 chat 10.5 s vs 热态 p50 1.4 s |

### 0.3 缺陷登记（"必须重启才能恢复"）

| 编号 | 缺陷 | 复现 | 证据 |
|---|---|---|---|
| D-1 | **网关在长跑 6-12 分钟后整体僵死**：HTTP 全部超时（含 `/v1/health`、`/v1/metrics/prometheus`），诊断端口不再接受连接，审计库写入与检查点同时停摆；≥26 分钟未自愈，只能 `taskkill` 重启 | 宿主 CPU 85-100% 时，跑 `harness_head.py --minutes 30` 即可复现（3/3 次） | `wt30p` 6.10 min 最后一次 200（13:21:21）→ 6.84 min `POST /v1/goals` 240 s 超时 → 再无任何 200；`p36long4` 11.18 min 最后 200（13:26:26）→ 11.92 min `POST /v1/goals` 超时 → 20 min 无恢复；`wt30p2` 5.02 min 最后 200 → 7.6 min 起 4 s 超时 |
| D-2 | **一次慢模型调用会让整站不可用**（不是只慢那一个请求） | `p36long4` 7.17 min 起 metrics 连续 30 s 超时，7.92 min 那次 chat 以 95 s 完成后 metrics 立即恢复 16-35 ms | 同一进程内 `/v1/metrics/prometheus` 在 chat 期间全部超时，chat 一结束立刻恢复；说明请求处理被串行在同一临界区 |
| D-3 | **HEAD `ff1d00b6` 无法启动**：`src/desktop/diagnostics.py:29` `from ..brand import BRAND_LOCKUP`，但仓库与 HEAD 均无 `src/brand`（`git ls-files src/brand` 为空，`BRAND_LOCKUP` 只在前端 TS 与测试里） | `git archive HEAD` 快照后 `harness_head.py` 启动即 `ModuleNotFoundError: No module named 'src.brand'` | 本轮 30 分钟窗口因此改在 `p36tipsrc`（含同样三项修复且可启动）上跑，详见 §1.2 |
| D-4 | **审计 verify 延迟在 p36 树上劣化**：HEAD 60 分钟维持 37-50 ms，p36 树 10 分钟后稳定在 322-343 ms（服务端日志 0.336-0.453 s） | 同口径复测即现 | 见 §2.2；原因未定，指向审计写入/检查点与校验读路径的锁竞争（未逐行证实）。补充：同一棵树在 `p36long5` 前 10 分钟（5,063 行）p95 仅 74/142 ms，13,053 行时才到 ~390 ms → 劣化**随行数增长**，与"固定倍数"不同 |

> D-1/D-2 的归因仍需谨慎：本轮宿主 CPU 长期 100%（`llama-server` 2.4 GB 走 CPU 推理、`edpnotify`/`HrAppStoreAppOM` 各已累计 1 万秒以上 CPU、并行还有其它会话的压测进程）。同代码在 10:07-11:54（宿主相对空闲）能连跑满 60 分钟。因此"D-1 是纯代码死锁"尚未证实；但"**一旦进入该状态就无法自愈、必须重启**"这一点是实测成立的（进程 CPU≈0、ollama 同期 166 ms 正常、后台告警线程仍每 2-4 分钟输出）。另：24h 第 5 次（`p36long5`）的结局是"进程消失"而不是"僵死"，日志在正常 checkpoint 处截断、无任何崩溃记录，按外部强杀登记，**不并入 D-1**（详见 §8）。

### 0.4 用户点名的四条缺陷：修复判定（详见 §12）

| 编号 | 改前 | 修复 | 判定 |
|---|---|---|---|
| D-1 满载整站僵死 | CPU 85–100% 时 6–12 分钟后全站无响应，≥26 分钟不自愈 | ①探针独立池 + 硬超时 503（中文原因）+ 纯 ASGI 快速失败闸 + 循环停滞看门狗；②**根因修复**：anyio/默认线程池不再在事件循环里 `Thread.start().wait()`（§12.8）；③探针池预热改栅栏法，真建 4 条（§12.11）；④身份解析移出事件循环、探针端点精确豁免限流（§12.12）；⑤goal 线程非阻塞启动（§12.12） | **未达标（第三次复现）**：`rss8h5` 跑到 22 分钟（19:40:06）再次僵死，到 19:46:46 终止仍无响应（6 分 41 秒不自愈）；446 次探活中 **25 次非 200**；**8 小时数据不存在，22 分钟窗口作废**（§12.11）。④⑤落地前不再声称"已修复" |
| D-2 慢调用堵 metrics | 95 秒调用期间 metrics 连续超时 | 指标/健康/就绪/预检全部移出事件循环，与业务临界区分离 | **达标**：105 秒慢调用窗口 34 次探活全绿；复测 43.5 s / 39.4 s 窗口 14 / 13 次全绿（§12.7、§12.10） |
| D-3 缺 `src.brand` 起不来 | `ModuleNotFoundError` | 后续提交已补 `src/brand.py` | **关闭**：当前 tip 启动 + health/ready 均 200（§12.3） |
| D-4 verify 随行数劣化 | 5,063 行 74–142 ms；13,053 行 ~390 ms | 单次硬预算 600 行 + `rooted_through_verified` 接受截断段 | **达标**：24,039 行 p95 **67.1 ms**，且与总行数解耦（§12.4；满载 2.3k 行复测 p95 138 ms 为调度抖动） |
| 审计库 +20.5MB/h | 默认策略（30 天/500k/256MB）短窗口不触发 | 默认 90 天 / 100k 行 / **100MB**，`enabled` 默认开 | **预期内**：20.5MB/h 是触顶前真实写入速率；触顶后每 pass 裁 5 千行、WAL 归零；改后同负载 **+4.5 MB/h**（§12.5、§12.10） |
| RSS +24.5MB/h | 相关性索引/死信/黑板/路由历史只增不删 | 四处加上限并同步回收索引 | **句柄/gc 无趋势**（前 35 分钟分别 14/9 次回落）；RSS 前 30 分钟 +14.3 MB/h 但 **9 次回落**；**8 小时曲线不存在**（`rss8h5` 22 分钟即僵死），只能写"22 分钟内未见单调增长，4 次回落"，不外推（§12.6、§12.11） |

#### 0.4.1 D-1 第三次复现（`rss8h5`）的分档明细

| 项 | 观测 | 分档 |
|---|---|---|
| 8 小时 RSS 完整曲线 | **不存在**：只有 22 个样本（22 分钟） | **未完成** |
| 探活失败次数 | **446 次探活，25 次非 200**（19:40:06–19:46:46，全部 `hung`，每次 3.00–3.04 s） | **未达标** |
| 最差窗口 p50/p95 | 该窗口 25/25 全失败，p50 = p95 = 3.00–3.04 s | **未达标** |
| 正常期探活 p50/p95（health） | 12.6 ms / 42.0 ms（446 次中前 421 次） | — |
| 看门狗 | 19:41:09 报「事件循环已停滞 84.5 秒」；至 19:46:46 未再恢复 | **未达标** |
| 栈转储 | 2 次（`probe_stacks.py` 的 `/stacks` 请求超时，服务端无响应） | **未达标** |
| 22 分钟 RSS | 110.8 → 峰值 117.9 → 116.1 MB，+18.56 MB/h，**4 次回落**，非单调 | 不外推 |
| verify | 29 次 0 失败，p50 37.5 / p95 58.5 / max 219.8 ms | 窗口作废 |
| 审计库 | 0.25 → 1.87 MB（22 分钟，+4.8 MB/h） | 不外推 |

> 现场：19:44 两次 py-spy 同栈 —— 事件循环停在 `RevocationStore._connect`
> （`sqlite3.connect`）← `JWTHandler.validate_token` ← `RateLimiter._identity`
> ← `RateLimitMiddleware.__call__`；同一时刻一个 anyio worker 停在
> `Thread.start (threading.py:999)` ← `AIStateManager.create_and_execute_goal`
> （`ai_management.py:1592`，应用自己 new 的普通线程）。进程 5 秒 CPU 只涨
> **0.02 秒**：不是忙死，是阻塞调用占住了事件循环。

---

## 1. 测法与口径

### 1.1 装置

- 同构 harness：`D:\cache\temp\liuhao-fix\harness_head.py`，`--run-id / --minutes / --crashes 0 / --port`；`LIUHAO_HARNESS_REPO` 指定源码快照；`LIUHAO_DIAG_PORT` 进程内诊断口；`LIUHAO_LOG_DIR` 指向隔离日志目录；数据目录每次新建（harness 启动时 `rmtree`）。
- 采样：每 60 s 一条 `sample` 事件（`handles`、`root_handles`、`rss_mb`、`private_mb`、`db_bytes`、`wal_bytes`、`goals_json_bytes`、`logs_bytes`、`proc_count`、`threads`、`handle_types` 逐类型计数）。
- 延迟口径：`requests.csv` 每请求一行；百分位取排序后 `k=round((n-1)p)`；窗口 0-10/10-20/20-30 分钟对齐，便于与 60 分钟基线的头 30 分钟直接比。
- 斜率：最小二乘，单位"每小时"；RSS 取 `rss_mb`；审计库取 `db_bytes+wal_bytes+shm_bytes`（单看 db 会被 WAL 周期掩盖）。

### 1.2 被测源码

- `base60`：改前基线（60 分钟）。
- `head60b`：HEAD 快照（60 分钟），用于隔离出"指标端点归一化"这一项的收益。
- `p36short`：`p36tipsrc` 快照（30 分钟，完整），含三项修复：指标端点归一化（2948e69e）、审计库回收（110b5ccd）、指标基数修复。**注意**：该 run 的进程环境沿用了上一轮遗留的 `LIUHAO_AUDIT_RETENTION_MAX_ROWS≈6000`，因此审计库在最后一刻（10:54:11）触发过一次裁剪（floor 7042、rows 6011）。
- `wt30p` / `wt30p2`：`p36tipsrc` + **默认保留策略**（显式清除 `LIUHAO_AUDIT_RETENTION_*`）的 30 分钟复测两次；两次都在 6-8 分钟僵死（D-1），未产出可用窗口。
- 启动方式：`pythonw.exe`、`Start-Process`、`schtasks` 在本环境都会被终端管理器在命令结束时连带杀掉（`schtasks` Last Result=`-1073741510` = `STATUS_CONTROL_C_EXIT`）。**唯一可跨命令存活的宿主是 WMI `Win32_Process.Create`**，全部长跑均以该方式拉起。
- **隔离快照的取法（踩坑记录）**：用 `LIUHAO_HARNESS_REPO` 指向隔离目录时，只拷 `src/` + `config/` 不够——`src/knowledge/embedding.py` 直接 `from error_types import ProviderError`，漏掉仓库根的 `error_types.py` 会在 import 期 `ModuleNotFoundError` 退出（`fix30` 第一次启动即如此）。**复制仓库做快照必须带上根目录的 `*.py`**（以及 `core/libs/plugins/skills/configs`），拷完先跑一次 `import src.gateway.main` 冒烟再开窗口。

### 1.3 宿主状态（必须随数字一起读）

- `Win32_Processor.LoadPercentage` 全程 **100%**；24 核。
- 主要占用者：`llama-server`（2.4 GB，CPU 推理，我的压测与其它会话共用）、`edpnotify`（11681 s）、`HrAppStoreAppOM`（10519 s）、`Trae CN`、`QQPYUserCenter`，以及其它会话的压测网关（`D:\_LH_W2`、`D:\_LH_WALK3`、`D:\_LH_ACCEPT`、`liuhao-perf-20261108`）。
- 影响：**本机无法提供稳定算力，所有延迟分位数（尤其模型相关）都被宿主争用污染**；只有"句柄/库体积/RSS"这类与算力无关的量可信度较高。

---

## 2. 改后复测（30 分钟窗口）

### 2.1 四指标并排

见 §0.1。补充原始字节数：

| 量 | base60（60 min） | head60b（60 min） | p36short（30 min） |
|---|---|---|---|
| handles | 起始→结束 | 全程 +166 | 全程 +162 | 全程 **-2**（933→931） |
| rss_mb | 全程 | +34 MB | +13 MB | +15 MB |
| audit_store.db | 全程 | 1.79→24.04 MB（+22.2 MB） | 1.79→24.35 MB（+22.6 MB） | 1.79→6.73 MB（+4.94 MB） |
| audit_store.db-wal | 全程 | 8.17→14.07 MB | 8.17→14.08 MB | 8.17→13.48 MB |
| goals.json | 全程 | 129 KB→2.65 MB（+2.4 MB/h） | 129 KB→654 KB | 129 KB→654 KB（封顶生效） |
| 审计事件行数 | 全程 | 24 126 | 24 126 | 13 053（裁剪 7 042） |

要点：
- **句柄**：p36 树上指标 label 组合在启动后 5.5 分钟即枚举完，Semaphore 569→586 后**完全平**（27.8 分钟处一次瞬态 +10 又回落）；非 Semaphore 类型（Event/Thread/File）在 2.5 分钟后也全部走平。→ 指标基数泄漏被治住。
- **goals 热文件**：129 KB→654 KB 封顶（`LIUHAO_GOALS_MAX_ACTIVE=128`）→ 已收敛。
- **审计库**：p36short 的 10-30 分钟段为 +2.97 MB/h，但**非零**；且它的下降主要是 6000 行裁剪在起作用（默认策略不触发）。→ 默认口径下判定"未解决"。
- **verify**：p36 树在 10-30 分钟窗口稳定在 ~390 ms（HEAD 同口径 61-112 ms，劣化 3.5-6 倍，见 D-4）。但同一棵树在 `p36long5` 的前 10 分钟只有 **74/142 ms**（此时审计 5,063 行），说明劣化**随审计行数增长而显现**（O(rows)），不是固定倍数；`p36short` 的 392 ms 对应 13,053 行。

### 2.2 两次默认策略复测尝试都僵死（D-1 的现场）

| 尝试 | run-id | 启动 | soak 起点 | 最后一次 200 | 首个 240 s 超时 | 结局 |
|---|---|---|---|---|---|---|
| 第 1 次 | `wt30p` | 13:15:16 | 13:15:55 | 6.10 min（13:21:21，metrics 0.0146 s） | 6.84 min `POST /v1/goals` | 13:47 人工杀，僵死 ≥26 min |
| 第 2 次 | `wt30p2` | 13:52 左右 | — | 5.02 min（health 200，1.5 s） | 7.6 min 起 health 4 s 超时 | 14:08 人工杀，僵死 ≥10 min |

`p36long4`（24h 第 4 次尝试）同一模式：7.17 min metrics 超时 → 7.92 min chat 以 95 s 完成 → 恢复 → 11.18 min 最后 200（13:26:26）→ 11.92 min `POST /v1/goals` 240 s 超时 → 僵死 20 min 后被人工杀。

僵死时的旁证（同时满足）：
1. `Get-NetTCPConnection` 仍显示 4001x/3999x 在 LISTEN，但 TCP 连上后无任何响应；
2. 进程 CPU 增量 6 s 内 **0.00 s**（不是算力饥饿，而是等待）；
3. 同机 ollama `/api/tags` **166 ms** 正常，`/api/ps` 显示两个模型已加载；
4. `audit_store.db` / `-wal` / `-shm` **mtime 停在僵死前 1 分钟**，`verification_checkpoints` 最后一条也停在同一秒 → 写路径与检查点线程一起被挂住；
5. 后台告警线程仍在输出（`High CPU Usage 84.4/99.1/88.3/80.7%`，每 2-4 分钟一条）→ 不是整个进程死，是请求处理路径被串行阻塞。

### 2.3 为什么这一轮拿不到"干净的默认策略 30 分钟窗口"

`p36short` 是唯一跑完的 30 分钟窗口，但带了 6000 行裁剪开关；`wt30p`/`wt30p2` 想补默认策略，两次都撞上 D-1。**结论：在本机当前负载下，默认策略的 30 分钟连续测量不可得**；要补这一格，需要在宿主 CPU 回落的窗口重跑（命令见 §10）。

---

## 3. "余 42 个句柄未归因"——归因收尾

### 3.1 结论：42 是口径错误，不是新泄漏

上一轮算式是 `185 − 11×13 = 42`，其中把"每个新指标 label 组合的锁数"当成了 **11**（来自 `/metrics` 文本里 histogram 的 11 条 bucket 行）。本轮用真实进程实测（`probe_metric_types.py`，逐步创建对象并用 NT 句柄类型普查差分）：

| 单步创建 | Semaphore 增量 |
|---|---|
| `threading.Lock()` × 10 | **+10** |
| 1 个 `Counter` label 子对象（`http_requests_total.labels(method,endpoint,status)`） | **+1** |
| 1 个 `Histogram` label 子对象（10 buckets + `+Inf`，即 `http_request_duration_seconds.labels(method,endpoint)`） | **+12** |
| **1 个新 (method,endpoint) 组合（counter+histogram）** | **+13** |

即真实系数是 **13/组合**，不是 11。代入上一轮实测：`14 个新组合 × 13 = 182`，实测 +185，**真实残余 = +3**（一次瞬态锁 + 采样抖动），"42" 是把系数低估为 11 造成的算术残差。

### 3.2 逐类型计数（NT 句柄类型，首末差分 + 斜率）

`head60b`（60 分钟，改前最严重的一次）：

| 类型 | 起 | 末 | Δ | 斜率/小时 | 说明 |
|---|---|---|---|---|---|
| **Semaphore** | 569 | 754 | **+185** | **+175.1** | 唯一增长的类；阶梯 +13/次（每出现一个新 URL 组合） |
| File | 52 | 60 | +8 | +0.08 | 在 52↔62 间抖动，随 `open_files` 27↔29、`open_sockets` 5↔6 同步 → WAL/SHM/日志句柄的开关瞬态，非泄漏 |
| Event | 47 | 38 | -9 | -2.02 | 前 2.5 分钟一次性回落（`threads` 45→32），之后恒平 |
| Thread | 35 | 29 | -6 | -1.23 | 同上，线程池收敛 |
| ALPC Port | 7 | 4 | -3 | -0.53 | 一次性 |
| EtwRegistration / Key / Timer | 94/21/1 | 95/22/0 | +1/+1/-1 | 各 ±0.3 | 一次性 |

`p36short`（30 分钟，改后）：Semaphore 569→586（+17，5.5 分钟前完成，此后 0.00/h）；File 在 52-60 间抖动、末样本（final 阶段）落峰值 62，序列为 `52,53,54,54,54,54,58,54,54,54,58,54,52,56,53,56,56,56,53,56,52,56,53,52,56,60,60,53,56,56,62` → **无净增长**；Event 47→38、Thread 35→29、ALPC 7→4 均为一次性。**除 Semaphore 外没有任何类型呈正斜率。**

### 3.3 逐类创建点

- **Semaphore（唯一增长源）**
  - `src/observability/metrics.py:26` `http_requests_total = Counter(..., ["method","endpoint","status"])`
  - `src/observability/metrics.py:33` `http_request_duration_seconds = Histogram(..., ["method","endpoint"])`
  - 子对象在请求处理时懒创建：`src/observability/metrics.py:106-111`（`record_http_request` 内 `.labels(...)`）
  - 每个子对象的锁来自 `prometheus_client/metrics.py`：基类 `Lock()`（1 把）、Histogram 的 11 个 `ValueClass`（每 bucket 一把 `MutexValue` 锁）+ 1 把，Counter 1 把；CPython 在 Windows 上把 `threading.Lock` 实现为**内核 semaphore**，故全部计入 `Semaphore` 类型。
  - 修复点即"把 label 值从 `request.url.path` 换成 `endpoint_template(scope)` + `__unmatched__`"（`src/gateway/main.py:1311`），把无限基数压成有限模板集。
- **File**：`src/kernels/audit/__init__.py` 的 SQLite 连接（db/wal/shm）、`logging` 的文件 handler、以及 harness/uvicorn 的 socket。波动量与 `open_files` 同步，无净增长；创建点无泄漏。
- **Event / Thread**：一次性收敛，来源是线程池/条件变量在 bootstrap 与并发阶段结束后的退场（`threads` 45→32、`Thread` 35→29 同步发生）。无持续创建点。
- **ALPC / Key / EtwRegistration / Timer**：Windows 运行时与事件追踪注册，一次性，无需处理。

---

## 4. 空闲 2 小时后首请求

装置：`resil_idle.py`（端口 40015，诊断口 39997，默认保留策略），13:00:25 启动：warm 基线（3 chat / 3 goal / 2 verify）→ 120 分钟零请求（每 5 分钟记录 RSS/句柄/DB/goals）→ 首请求 `health / chat / verify / goal` → post-idle 成组测量 → `EmptyWorkingSet` 冷启动对照。

**第一次运行在 t=85.1 分钟被打断（环境噪声，非产品行为）**：`resil_idle.csv` 从 t=85.1 起 `proc_count=0 / handles=0 / rss=0`（之后 5 个样本全 0），网关进程消失；消失时刻（≈14:23-14:25）与 24h 第 5 次 `p36long5` **完全相同**，两个实例同时归零 → 判定为同机外部批量清理（详见 §8），不计入缺陷。因此本次拿不到"120 分钟空闲后的首请求"。

已拿到的 85 分钟真实空闲实测（t=0→80.1，17 个存活样本，全程零请求）：

| 指标 | 0 min | 30 min | 35 min | 80.1 min | 斜率/说明 |
|---|---|---|---|---|---|
| RSS (MB) | 185.0 | 185.0 | **63.5** | 41.7 | -2.47 MB/min（-148 MB/h）；**第 30-40 分钟阶跃式回落**（185→63.5→40.7） |
| private (MB) | 900.2 | 900.0 | 900.0 | 900.0 | **0**：回收的是可丢弃页，提交内存不释放 |
| 句柄 | 780 | 770 | 770 | 770 | -0.04/min：bootstrap 线程退场（threads 35→31）后**恒平** |
| audit db / wal | 696320 / 4152992 | 不变 | 不变 | 不变 | **0 / 0**（空闲期无写入，无检查点） |
| goals 字节 | 30751 | 不变 | 不变 | 不变 | **0** |
| 进程存活 | 是 | 是 | 是 | 是 | t=85.1 起消失（外部） |

结论（能下的部分）：

1. **空闲本身不产生任何增长**：库、WAL、goals、句柄全部零变化 → 泄漏只在"有请求"时发生。
2. **系统确实会回收工作集**：RSS 在 30-40 分钟从 185.0 → 40.7 MB（-78%），而 `private` 恒 900 MB（回收的是 standby/可丢弃页，不是释放提交内存）。
3. **2 小时后的首请求本次未测到**（网关被外部清理）。探测器本身按计划跑完 120.09 分钟并于 15:01:45 写出 `resil_idle/resil_idle_result.json`：`post_idle_first` 的 `health / chat / verify / goal` **全部 `status=0`**（2.03-2.15 s，连接被拒），`post_idle` 的 2 chat / 3 goal / 2 verify 同样全 0，`idle.rss_end=0` → 这份 JSON 只能作为"进程已不在"的证据，**不能当作 2h 首请求结论**；它记录的 warm 基线为 chat p50 **8,546 ms**、goal p50 **3,632.6 ms**、verify p50 **17 ms**（宿主满载，chat 本身已经很慢，后续对比必须用同一 run 的 warm 段而不是用别处的 1.4 s）。另外该 JSON 里 `idle.rss_slope=-1.839 MB/min`、`handles_slope=-8.06/min` 被死亡后的 0 值污染，不可引用；有效段只用上表 0-80.1 分钟。已按同一装置重跑：`resil_idle2`（端口 40016 / 诊断 39996，默认保留策略，**14:44:20 启动，120 分钟真实空闲**），预计 16:49 写入 `resil_idle2/resil_idle_result.json`；本轮报告不含该格的编造数字。

加速模拟答案（**不是 2 小时真实空闲**，见 §7）：`EmptyWorkingSet` 强制回收后 RSS 188→2.7 MB，首个 chat **10.5 s**（热态 p50 1.4 s，≈7.5 倍）、首个 goal **7.5 s**（热态 3.06 s，≈2.5 倍）→ 工作集被回收后首请求确实明显变慢，但仍**正常成功、无需重启**。

---

## 5. 睡眠 → 唤醒（以进程树冻结 600 s 模拟）

做法（**是模拟，不是真实 S4**）：对网关整棵进程树（launcher + uvicorn 子进程 2 个 PID）调用 `NtSuspendProcess`，冻结 600 s 后 `NtResumeProcess`。`resil_fault2`，端口 40019，13:23:54 冻结 → 13:34:02 唤醒（608 s）。

| 检查项 | 结果 |
|---|---|
| 冻结期间 `/v1/health` | 超时（8.03 s 无响应）——符合"主机睡眠期间不可用"的预期 |
| 唤醒后 `/v1/health` | **200，11.6 ms** → 服务活着 |
| 唤醒后 `/v1/audit/verify` | **200，439 ms** → SQLite 连接未坏，读路径正常 |
| 唤醒后 `POST /v1/goals` | **200，1022.9 ms**，任务完成 → 写路径正常 |
| 唤醒后 `GET /v1/goals` | **200，27.7 ms** |
| 唤醒后 `POST /v1/chat` | 200，11279 ms（模型慢，宿主 CPU 争用；非连接问题） |
| 定时任务（检查点） | **继续**：`verification_checkpoints` 第 3 条在唤醒后 **2 s** 内补跑（seq 385-957，573 条），第 4 条 13:36:55（seq 958-1151） |
| 定时任务（裁剪） | 本次未触发（行数 1054 < 阈值 1200）；另一组 `resil_fault` 在跨过阈值时观测到 floor 0→120、`pruned_events=120`、`auto_vacuum=2`、`freelist=0`、WAL 3.64 MB → 机制可用 |
| 是否需要手动重启 | **不需要** |

---

## 6. 网络断开 → 恢复

做法：本地 TCP 中继 11435→11434，网关的 Ollama base_url 指到 11435；`kill_tree(中继)` = 断网，重启中继 = 恢复（不动主机防火墙/真实网络）。

| 阶段 | 结果 |
|---|---|
| 断网期间（2 次 chat） | 传输层仍 200，但 `body.status="error"`、回复"这次没有拿到回复..."；耗时 3.2-4.5 s。**网关不崩、进程不退** |
| 断网期间 `/v1/health` | 200，16.8 ms → 网关自身健康 |
| 恢复后第 1 次 chat | **200，"在线"，8.59 s，一次成功** → 自愈，无需重启 |
| 恢复后重试次数 | 0（第一次就成功） |

附带发现（观测口径缺陷）：**模型侧失败时 HTTP 状态仍是 200**，失败信息只在 `body.status` 里。按 HTTP 状态做监控会把"模型全挂"看成 100% 健康。

---

## 7. 冷 / 热对比（工作集被回收后首请求）

`resil_fault2` 末段：`SetProcessWorkingSetSize(h,-1,-1)`（EmptyWorkingSet）后立刻发首请求。

| 量 | 热态基线 | 冷态（trim 后首请求） | 倍数 |
|---|---|---|---|
| RSS | 188.0 MB | **2.7 MB**（trim 后瞬间） | — |
| 首 chat | p50 1430 ms / p95 2100 ms | **10 500 ms** | ~7.3× |
| 首 goal | p50 3060 ms | **7500 ms** | ~2.4× |

补充：冷态两次请求后 RSS 回到 39.9 MB，`private_mb` 始终约 900 MB → 被回收的只是工作集，提交内存没释放，所以"空闲 2 小时后内存被回收"不会导致 OOM，只会带来**页入延迟**。

> 该组数字受宿主 CPU 100% 影响（同一次运行里"唤醒后 chat"也是 11.3 s），只能定性"明显变慢"，不能精确归因于 trim。

---

## 8. 24 小时浸泡现状

| 尝试 | run-id | 起 | 结局 |
|---|---|---|---|
| 1 | `p36long` | 12:02 | 12:16 被宿主清理（背景命令批量 exit -1） |
| 2 | `mem8g` | 12:03 | 12:16 同上 |
| 3 | `p36long3` | 12:50 | 5 分钟后请求卡死（`requests.csv` 0 字节），13:14 人工杀 |
| 4 | `p36long4` | 13:15 | 11.18 分钟 D-1 僵死，13:47 人工杀 |
| 5 | `p36long5` | 14:08 | **约 14 分钟起不再服务，随后进程消失（与 D-1 的"僵死"不同）**：0-8 min 正常（每 2 分钟 30-38 条请求）；9.26 min 首个 `TimeoutError`（`/v1/audit/verify`，14:17:16）；8-10 min 请求数掉到 11 条、10-12 min 2 条、12-14 min 1 条；13.93 min 最后一次 200（14:21:56，`/v1/metrics/prometheus` 20.7 ms）；18.00 min `ConnectionResetError`（14:26:01）→ 18.04 min 起持续 `URLError(10061)`（每 2 分钟 51-59 条快速失败）。网关 stdout 最后一行是 **14:23:08 `checkpoint_tick_complete`（#32，status ok）**；**无 traceback、无 WER/Application Error(1000)、无 `gateway-stdout-2.log`**（`harness_head.py` 从不自动重启）→ **疑被同机其它会话的批量清理强杀**，按环境噪声登记，不并入 D-1。14:38 人工停 harness（`taskkill /F /T`，共停 27828/5380/20460/23892/27824/15116）。 |

**结论：本机当前负载下 24 小时浸泡无法连续完成**（5/5 失败：3 次 D-1 僵死［`wt30p`、`wt30p2`、`p36long4`］、1 次请求卡死［`p36long3`，`requests.csv` 无写入］、1 次进程被外部终止［`p36long5`］）。第 5 次不作为 D-1 证据：它的日志在**正常**的检查点 tick 处截断，没有任何 Python 级异常、没有崩溃转储、也没有崩溃事件记录；而同机此时运行着 ≥10 个其它网关实例与多个会话的清理脚本，"被别的进程 `taskkill` 整批带走"是最符合证据的解释（不能证实，也不排除）。

---

## 9. 未完成 / 未验证

1. 默认保留策略下的 30 分钟窗口（§2.3）：需宿主 CPU 回落时重跑 `wt30p3`。
2. 24 小时末 6 小时斜率定论：无法在本机完成，当前只能说"未验证"。
3. 8 GB 内存受限 2 小时（`mem8g3` 在 12:58 停止）：未完成，需在负载回落窗口重跑。
4. D-1 的逐行根因：没有 `py-spy` / `procdump`（不往 `.venv` 装包、不动工作区），只能给到"请求处理路径被串行阻塞 + 审计写事务停摆"这一层；需要能拿到线程栈时补一轮。
5. `p36long5` 的"消失"只能排除 Python 级崩溃（无 traceback / 无 WER / 无事件日志），**不能证实**是哪个进程把它杀掉的；需要下一次运行时记录 PID 与父进程，并在消失后查 `Security` 审计日志才能定论。
6. D-4（p36 树 verify 变慢）的机制未定位。
7. 2 小时空闲后首请求（§4）：原探针 t=85.1 min 被外部清理，`resil_idle2` 重跑中（14:44:20 启动，ETA 16:49）；报告目前只给 85 分钟空闲段 + 加速模拟的冷启动数字。

> **§12 已闭环的项**：4 → D-1 根因已逐端点定位（同步 I/O 占住事件循环 + 线程池排队），
> 且现在内置 `LoopStallWatchdog`（>15 s 无心跳即落全线程栈），下次不必再依赖外部工具；
> 6 → D-4 机制已定位为"checkpoint 之间的事件数"并给出分批硬预算；1 → 默认保留策略已实测
> （12.5）；D-3 已确认当前 tip 可启动。仍未闭环：2（8 小时斜率，`rss8h` 正在累积）、
> 3（8 GB 受限）、5（`p36long5` 消失的外部归因）、7（空闲 2 小时后首请求的完整窗口）。

---

## 10. 复现命令

```powershell
# 30 分钟窗口（当前树，默认保留策略）
$env:LIUHAO_HARNESS_REPO='D:\cache\temp\liuhao-fix\p36tipsrc'
$env:LIUHAO_DIAG_PORT='39992'
$env:LIUHAO_LOG_DIR='D:\cache\temp\liuhao-fix\wt30p3\data\logs'
D:\LiuHao-AI-OS\.venv\Scripts\python.exe D:\cache\temp\liuhao-fix\harness_head.py `
  --run-id wt30p3 --minutes 30 --crashes 0 --port 40022
# 必须用 WMI 拉起才能在终端命令结束后存活：
# Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine="..."}

# 分析
D:\LiuHao-AI-OS\.venv\Scripts\python.exe D:\cache\temp\liuhao-fix\cmp30.py wt30p3
D:\LiuHao-AI-OS\.venv\Scripts\python.exe D:\cache\temp\liuhao-fix\hdelta.py wt30p3
```

- 句柄逐类斜率：`hdelta.py <run>`（按 NT 类型首末/斜率）
- 句柄逐类时序：`hseries.py <run> Semaphore,File,Event,Thread`
- 审计库只读体检：`audit_probe.py <db> --source-ro --out ...`
- 恢复能力：`resil_idle.py`（2h 空闲）/ `resil_idle2.py`（重跑，`RESIL_IDLE_MIN=120`，端口 40016 / 诊断 39996）/ `resil_fault.py`（冻结、断网、trim）
- 空闲探针被外部清理时的判据：`resil_idle.csv` 里 `proc_count=0 / handles=0 / rss=0` 连续出现，且与其它实例同一时刻 → 环境噪声，不是产品退出

---

## 11. 30 分钟洁净复跑（`p36long6`，2026-11-08 14:47–15:20）

> 本节只记录本次实际跑到的结果；**24 小时仍未验证**，不代表 24h 通过。

**配置**：run-id `p36long6`，网关端口 40023 / 诊断端口 39993（启动前确认无监听）；代码快照 `p36tipsrc`（与 `p36long5` 同一冻结 tip）；模型 `qwen2.5:3b`。

**监控修正**（`D:\cache\temp\liuhao-fix\watch_health.py`、`hourly_probe.py`）：
- 端口不再硬编码：每轮从 `<run>/data/run/instance.json` 读取 `port/pid`，回退 `meta.json`；同端口重启后自动跟随新 pid。
- 健康探测统一 `GET /v1/health`，按 HTTP 状态 + pid 存活区分 `ok / not_ready / hung / dead`：连接拒绝且 pid 不在 = `dead`；5xx 或 pid 存活但未就绪 = `not_ready`；可连接但超时且 pid 存活 = `hung`。

**看门狗**（`harness_head.py`）：
- 浸泡循环每周期检查 `proc.poll()` 与 `/v1/health`；进程消失立即在同一端口重启，事件流写 `backend_down` / `restart`（old_pid、exit_code、new_pid、seconds）；连续 3 次不可达且 >120 s 无 200 则强杀后重启。
- `Recorder` 全程不关闭，重启前后 `requests.csv` / `events.jsonl` 连续写入，不丢证据（本次已实测）。

**首条 health**：`2026-11-07T14:47:38`，`200`，849 ms，`pid=18224`，端口 40023；`health_ready` 事件后 `t0` 重置（`elapsed_min=0`），计时从首条 200 之后开始。

**实跑事件**：
1. 14:52:23 / 14:52:43 watch 记录 `hung`（TCP 可连但 4 s 无响应）；14:52:47 网关原生崩溃：pid 18224，`0xC0000005`（ACCESS_VIOLATION），故障模块 `ntdll.dll+0x64e4e`，Windows 事件 1000；WER dump `%LOCALAPPDATA%\CrashDumps\python.exe.18224.dmp`（8.3 MB）。无 Python traceback（未启用 faulthandler）。
2. 14:53:17 看门狗检测 `process_exit`（exit_code 3221225477），14:54:02 同端口重启成功（45.0 s，pid 4896→31340），`requests.csv` 连续。
3. 重启后审计库持续不可读：写请求 503「审计库不可读：disk I/O error」（`audit_appender.py:97` / `conversation_store.py:57` 的 `PRAGMA journal_mode=WAL`）。根因：崩溃前 1 秒由网关派生的子进程 **30924**（创建 14:52:46，父进程 18224，零线程僵尸）持有 `audit_store.db-shm`；把库拷到临时目录可正常打开且 `integrity_check=ok`，终止 30924 后 `-shm` 立即释放、只读打开恢复。→ **产品级缺陷：崩溃时子进程遗留持有 WAL 共享内存，进程重启无法自愈**（未改产品代码）。
4. 15:19:58 harness 正常收尾（含末次重启 56.8 s、持久化对比），随后看门狗与 watcher 退出。

**30 分钟窗口计数**（`requests.csv` 共 488 条）：
- bootstrap 25：25 PASS；concurrency 56：56 PASS（含 1 个越权 404 探针，属预期，不计 FAIL）。
- soak 394：200×136 + 201×13 = **PASS 149（37.8%）**；**FAIL 245** = 503×222 + 500×15 + 传输失败×8。
- final 7：PASS 2 / FAIL 5（均 500）；final_restart 6：PASS 1 / FAIL 5。
- 健康（窗口内 93 行）：`ok` 84、`not_ready` 2、`hung` 5、`dead` 2。
- `results.json`：`final.audit.ok=false`（`entries_checked=0`）、`goals_total=0`、`persistence.before/after` 全 0、`restart_ok=true`（56.8 s）→ harness 自判 **FAIL**（审计库不可用）。

**结论**：30 分钟窗口内发生 **1 次原生崩溃**，看门狗 45 s 内同端口自动重启成功且未丢 CSV；但崩溃后审计库 503 持续到窗口结束，本窗口 **FAIL**。**24 小时浸泡仍未完成、未验证**。

## 12. 用户点名的四条缺陷：修复与验证（2026-11-08 第二轮）

### 12.1 D-1 宿主 CPU 吃满时整站僵死

**根因**（不是"慢"，是事件循环被同步 I/O 占住后无法再调度任何请求）：

| 端点 | 旧实现的问题 |
| --- | --- |
| `/v1/health` | `async def` 里**直接**同步读模型配置库（SQLite），读一次就把事件循环钉住一次 |
| `/v1/ready`、`/v1/metrics` | 声明为 `async def`，却把整套同步 DB 采集跑在事件循环线程上 |
| `/v1/metrics/prometheus` | 指标导出（含逐项采集）同样在循环上 |
| `/v1/ready/subsystems`、`/v1/ready/production` | 在循环上**导入并实例化 14 个内核**，首次可达数秒 |

宿主 85–100% CPU 时，这些同步段被放大到秒级；事件循环没有机会跑心跳与探针，于是
`/v1/health` 和所有异步请求一起停——6–12 分钟后整体不服务，26 分钟也不自愈。
另一条并行的僵死路径是同步端点把 anyio 线程池（约 40 个 token）占满后，新请求无限排队。

**修复**（`src/gateway/health.py`、`main.py`、`observability.py`）：

1. **探针隔离池**：`ThreadPoolExecutor(max_workers=4, thread_name_prefix="liuhao-probe")`，
   采集一律 `run_in_executor` + `asyncio.wait_for(asyncio.shield(...), 2.0s)`。
   超时返回 `503` + **四字段**（`code:"PROBE_BUSY"` / 中文 `message`（含"探针繁忙"）/
   `correlation_id` / `retry_after:1`）+ `Retry-After: 1` + `X-Correlation-ID`，与业务
   503 同一套形状（改前是另一种 `status:"busy"` 形状，见 §12.12），不再挂住。
2. **`/v1/health` 纯内存路径**：模型配置快照 5 秒 TTL、只在探针池里刷新；读不到时
   `ai_provider.type="unknown"` 仍返回 200。存活语义 = "进程还能响应"。
3. **子系统/生产预检**移入同一探针池（10 s / 8 s 预算），不再在循环上 import 内核。
4. **快速失败闸** `OverloadGuardMiddleware`（纯 ASGI，最外层）：在途业务请求超过
   `LIUHAO_MAX_INFLIGHT`（默认 150）立即 `503` + 中文原因；`/v1/health`、`/v1/ready`、
   `/v1/metrics`、`/v1/metrics/prometheus` 与 `OPTIONS` 豁免。
5. **停滞看门狗** `LoopStallWatchdog`：事件循环 >15 s 无心跳即 ERROR，并在前 3 次
   `faulthandler.dump_traceback(all_threads=True)` 落全线程栈（下一次僵死可直接定位）。

**回归护栏**（`tests/test_gateway_health_wiring.py::TestProbeIsolation`，4 条）：
采集慢于预算时端点必须在预算内 503；慢采集之后 `/v1/health` 仍 <500 ms 返回 200；
模型配置库读失败仍 200；源码级断言 liveness 不得直接出现 `ai_provider_status()`
（防止有人把它改回循环上）。

### 12.2 D-2 一次慢模型调用把 metrics 全堵住

串行点就是 12.1 里那些"在事件循环/同一执行路径上跑同步采集"的端点：95 秒的模型调用
期间，这些端点每一次都要排队等同一个被占住的循环，表现就是 metrics 连续超时、chat
一结束立刻恢复。修复后指标、健康、就绪、Prometheus 导出全部走**独立探针池**，与业务
请求的临界区彻底分开；慢业务调用只影响它自己。

### 12.3 D-3 `ff1d00b6` 起不来（缺 `src.brand`）

当前 tip 已包含 `src/brand.py`（`BRAND_LOCKUP`）；实测启动 + `GET /v1/health` 200 +
`GET /v1/ready` 200（无压测、单实例）。D-3 关闭，不再追查。

### 12.4 D-4 审计 verify 随行数劣化

**根因**：旧实现每次都从最后一个 checkpoint 重算到表尾，一次请求的工作量 = "两次
checkpoint 之间产生的事件数"；事件产生速率越高、窗口越长，单次就越慢，所以不是固定
倍数。给单次请求加**硬预算**后，单次工作量与库总行数彻底解耦。

**改动**（`src/gateway/audit.py`、`src/kernels/audit/{__init__,verification}.py`）：
- 默认增量校验单次上限 **600 行**（`_VERIFY_BATCH_EVENTS`）。
- 截断时如实返回 `verification_complete=false` + `pending_events` + `tail_seq`；
  响应 `mode="incremental_batch"`，下一次请求从该处继续。
- 新增 `rooted_through_verified`（"已校验前缀已锚定到 retention floor"）：截断的段
  只要锚定就接受，**不退回 20k 行全量重算**——这正是旧劣化路径的根源。
  `rooted_at_genesis` 仍按表尾计算（保持既有契约与测试语义）。
- `full=true` 仍是权威全量（人工检查用）。

**实测**（24,039 行真实审计库副本，清空 checkpoint 从 genesis 起，宿主 CPU ~79%）：

| 指标 | 旧（全量 `verify_integrity`） | 新（分批 600 行） |
| --- | --- | --- |
| 单次耗时 | 1,714–2,055 ms | p50 **55.8 ms** / p95 **67.1 ms** / max **70.2 ms** |
| 推进 | 一次扫完 | 每次 +600 行，`pending` 23,439→0（41 次） |
| 语义 | ok/失败 | 41 次全部 `rooted_through_verified=true`；仅最后一次 `complete=true` |

**判定**：2 万行内 p95 稳定在 100 ms 量级（67 ms），且与库总行数无关。达标。

### 12.5 审计库 +20.5MB/h：默认策略实测

**"20.5MB/h 是不是预期"**：是预期，且不是泄漏——审计是 append-only 的防篡改证据
（HC-01 口径：设计上 tamper-evident；未接外部锚定时属 UNVERIFIED，见 GOVERNANCE.md §7），
不能采样、不能丢行。实测单行约 **1.39KB**（`retention_bench`：110,000 行 → 153.6MB，
33.0s），按合成强负载 ~7 条/s 折合约 **35MB/h**，与长跑观测的 20.5MB/h（~4 条/s）同源。

**默认策略（不设任何 `LIUHAO_AUDIT_RETENTION_*` 环境变量）已生效**：
90 天 / 100,000 行 / 100MB，`enabled` 默认 true。110,000 行实测触顶后连续裁剪：

| pass | retention floor | 行数 | db | -wal |
| --- | --- | --- | --- | --- |
| 0（触顶） | 0 | 110,000 | 153.57 MB | 4.88 MB |
| 1 | 5,000 | 105,000 | 148.02 MB | **0.00 MB** |
| 2 | 10,000 | 100,000 | 141.02 MB | 0.00 MB |
| 3 | 15,000 | 95,000 | 134.02 MB | 0.00 MB |
| 4 | 20,000 | 90,000 | 126.99 MB | 0.00 MB |

- 每次裁剪 5,000 行（`MAX_DELETE`，避免一次请求多秒停顿），约 -7MB/次；
  持续写入下最多 7 次即可回到 100MB 以内，裁剪能力（~420MB/h）远高于写入速率。
- `-wal` 每次裁剪都被 `wal_checkpoint(TRUNCATE)` 归零（旧版 +2.97MB/h 的残余正是
  WAL 常驻）。
- **裁剪后 `verify_integrity()` 仍 `ok=true`，校验 90,000 行**：删前缀不破坏可验证性
  （从 retention floor 锚定）。
- 结论：默认值本身把库钉在 ≤100MB 的锯齿区间；20.5MB/h 是"还没触顶时的真实写入
  速率"，触顶后转为"裁剪 + 回收"稳态，不需要外部环境变量。

### 12.6 RSS +24.5MB/h：归因与修复

按"每事件 ~1.39KB、~7 条/s ≈ 35MB/h"的量纲对齐，找到三处**只增不删**的进程内结构：

| 位置 | 问题 | 修复 |
| --- | --- | --- |
| `kernels/event/__init__.py::EventBus._correlation_index` | 历史只留 10,000 条，但相关性索引**永不回收**，旧 `Event`（含 payload）被强引用钉住 | 历史裁剪时同步摘除被淘汰事件；空链移除键 |
| `kernels/event/__init__.py::_dead_letters` | `PAUSED` 时每次发布都记一条，无上限 | 上限 1,000 条 |
| `ai/blackboard.py::Blackboard._entries` | 多智能体协作缓存无上限 | 2,000 条 FIFO（同步清理 4 个索引） |
| `model_gateway/model_router.py::_routing_history` | 每次路由追加一条 `RouteResult`，无上限 | 2,000 条上限 |

其中**相关性索引是主因**：按事件速率与 payload 大小估算，其增长正是 +24.5MB/h 的量级；
事件历史本身在 ~24 分钟内就会填满 10,000 条，若不回收索引，RSS 会永远线性上涨。
8 小时连续观测（`rss8h`，17:20 启动，`--loaders 0 --heavy --goals-every 90 --sample-every 60`）
正在跑；判定标准是"填满历史窗口后 RSS 不再单调上升"。本报告只登记已积累时段的斜率，
未满 8 小时的部分标注为"仍在累积"，不外推。

> 采样装置修正：`diag/sitecustomize.py` 原本没有 `/memory` 路由，长跑采到的是
> `{"error":"unknown"}`（所以 `post30` 窗口没有 RSS 曲线）。已在 17:19 补上该路由并
> 重启长跑；`rss8h` 首条采样 `rss_mb=111.1 / handles=627 / gc_objects=153,329` 正常。

### 12.7 D-1 30 分钟满载探活窗口（`post30`，16:46:19–17:16:42）

**条件**（这就是 D-1 要求的"人造 CPU 满载 + 持续探活"）：

- 本进程自带 **6 个 CPU 燃烧进程**（`--loaders 6`）+ 5 账号全量引导（`--heavy`）+ tracemalloc；
- 宿主同期 **CPU 100%**，且并行有 12+ 个外部压测进程（`pytest` ×9、`acceptance/startup_baseline`
  等）。闸门超时记录：`contended=True`，占用者含 `10920 / 2432 / 5868 / 12728`（gate log 首行）。

> **口径声明**：按用户约束"有别的压测在跑，数字不算"，这个窗口**不作为长跑基线**。
> 它的用途只有一个：证明 **CPU 85–100% 时不再整体僵死**。D-4 与保留策略的验收数字
> 用的是上一节（12.4/12.5）干净宿主上的基准。

| 探针 | 次数 | 非 200 | p50 | p95 | max | 说明 |
| --- | --- | --- | --- | --- | --- | --- |
| `/v1/health` | 571 | **0**（全 `ok`） | 26.8 ms | 264.6 ms | 1,357.8 ms | 全程可答，含 105 秒慢调用期间 |
| `/v1/ready` | 571 | **0** | 22.0 ms | 116.0 ms | 941.2 ms | 依赖检查同样没被业务阻塞 |
| `/v1/metrics/prometheus` | 571 | **1**（16:57:33 超 3 s 预算） | 24.3 ms | 121.3 ms | 3,005.8 ms | 同一时刻 health 88 ms、ready 941 ms 仍 200 |
| 轻量业务接口（`/v1/goals`、`/v1/audit/events`） | 1,466 | **0**（末 2 条是 17:16:42 进程退出的正常断连） | 79.0 ms | 690.7 ms | 2,464.7 ms | 慢，但不 503、不僵死 |
| `chat` | 139 | 0（末 1 条为退出断连） | 16.2 s | 31.1 s | — | 模型推理被 CPU 饥饿拖慢，属"慢"不属"死" |
| 事件循环全线程栈 dump | — | **0** | — | — | — | `LoopStallWatchdog` 从未触发（>15 s 无心跳才算） |

**D-2 直接对照**：`slow_chat` 从 16:46:19 跑到 16:48:04（**105,017 ms**，200）。这 105 秒内
共 34 次探活：`health` 0 次非 ok、`metrics` **0 次非 200**、`ready` 0 次非 200。旧行为是
"这段时间 metrics 连续超时、chat 一结束立刻恢复"，现在两者完全解耦。

**判定**：
- D-1：**达标**。CPU 100% 连续 30 分钟、571 次探活，`/v1/health` 与轻量接口一次没整体停；
  没有再出现"6–12 分钟后全站无响应"。唯一 1 次 metrics 超时是**单次 3 秒预算被打满后
  如实失败**，不是排队僵死（下一轮探测即恢复 200）。
- D-2：**达标**。105 秒慢调用窗口内 metrics/health/ready 全绿。
- 残留（如实登记）：满载下 `/v1/metrics` 偶发越过 3 秒（571 次中 1 次）；`verify` 在同窗口
  p50 153 ms / p95 607 ms（库仅 828 行），这是 100% CPU + 外部 12 个压测进程下的**调度
  抖动**，不是随行数劣化——同样的 ~20 行工作在 17:15:41 只用 44.3 ms。若真实用户在
  满载机器上要求 `verify` 也稳定在 100 ms 内，还需要把校验的写锁（派生 checkpoint）
  进一步降频，本轮未做。

### 12.8 D-1 的第二入口：anyio 在事件循环里 `Thread.start`（`rss8h` 实测，已修）

`post30` 通过之后，同一份代码在 8 小时长跑 `rss8h`（17:20:02 启动，`--loaders 0`）里
**2 分钟就再次僵死**，说明 D-1 还有第二个入口，且与"打点多不多"无关：

| 时间 | 事实 |
|---|---|
| 17:22:34 | 事件循环最后一次心跳 |
| 17:23:03 | `LoopStallWatchdog` 首次告警（30.4 s 无心跳），此后每 15 s 一条，直到 **17:38:41（967 s）仍无恢复** |
| 17:22 起 | `liuhao-diag` 端口连接被拒（探针拿不到 `/stacks`、`/memory`，RSS 采样全为 null） |
| 全程 | 进程仍在运行（看门狗线程每 15 s 正常写日志），CPU 累计只 18.9 s/14 min → **不是 GIL 饥饿、不是忙等** |
| 17:39 | 人工 `taskkill`（与 D-1 历史表现一致：只能重启） |

**根因（py-spy 对活进程取样，`stacks/gw19156.dump.txt`）**：

```
Thread (idle) MainThread
  File "threading.py", line 999 in start        ← self._started.wait()，无超时
  File "anyio/_backends/_asyncio.py", line 2676 in run_sync_in_worker_thread
        worker.start()                          ← 在事件循环线程上同步创建 worker
  File "starlette/concurrency.py", line 40 in run_in_threadpool
  File "fastapi/dependencies/utils.py", line 676 in solve_dependencies
        kwargs: {"x_liuhao_token": None, ...}   ← 同步 auth 依赖，每个请求都占一个 worker

Thread (idle) liuhao-diag
  File "threading.py", line 999 in start        ← 同一行
  File "socketserver.py", line 372 in process_request_thread
  File "socketserver.py", line 597 in process_request   ← ThreadingMixIn 每连接一个线程
```

三件事由此确定：

1. **不是业务锁**：`verify`/审计/模型都不在这条链上；卡点是"创建新线程并在事件循环里
   **无超时**等它真正跑起来"。
2. **是进程级的线程创建停滞**：`liuhao-diag` 与事件循环**同时**卡在同一行，说明新线程
   在 bootstrap 阶段不再推进（既有线程照常调度，所以看门狗还活着）。
3. **不可自愈**：事件循环就停在那一行，后续每个请求都在等同一个卡住的 `Thread.start`，
   重启是唯一出口 —— 这正是用户描述的"死"。

**修复**（[main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py) 的
`install_nonblocking_worker_start()`，在 lifespan 起跑时安装）：

- 只替换 anyio 自己的 `WorkerThread.start`：线程照常创建、任务照常入队，**唯独省掉
  `self._started.wait()` 这一步**（CPython `Thread.start` 里唯一会无限阻塞的语句）。
- 效果：线程创建停滞最多拖住**触发它的那一个请求**（由外层限流/超时收口），事件循环、
  `/v1/health`、`/v1/metrics`、`/v1/ready` 继续工作 —— 从"整站僵死"降级为"个别慢/503"，
  符合用户要求的"不允许整体僵死"。
- 回归护栏：`tests/test_gateway_health_wiring.py::TestThreadPoolStartDoesNotStallLoop`
  用"线程创建成功但永不 bootstrap"复现卡点，断言 `WorkerThread.start()` 必须 <0.5 s 返回
  且不阻塞调用线程；另断言探针池 4 条线程、默认线程池 4 条线程在启动时已建满
  （整文件 20 passed）。
- **反证（护栏可证伪）**：`test_the_warm_guard_goes_red_without_prewarming` 走"不预热"的
  旧行为（`before=0`、`after=1`），用**同一条**不变量断言跑它并断言其抛 `AssertionError`。
  把 `warm_default_executor()` 换成空操作后，正例
  `test_default_executor_threads_are_created_before_first_to_thread` 现场变红，
  错误信息正是"默认线程池没有被预热：运行期第一次 submit 会在事件循环里建线程（D-1）"，
  说明这条护栏不是空转。
- 探针池预热（[health.py](file:///d:/LiuHao-AI-OS/src/gateway/health.py) 的
  `warm_probe_pool()`）：`ThreadPoolExecutor` 是"第一次 submit 才建线程"，而
  `_run_probe` 走 `loop.run_in_executor(_PROBE_POOL, ...)`，所以**第一个** `/v1/health`
  也会在事件循环里现场 `Thread.start`。启动时预热 4 条线程后，探针路径上不再有任何
  运行期线程创建。
- 默认线程池预热（[main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py) 的
  `warm_default_executor()`）：`asyncio.to_thread`（知识库文档解析）用的是
  `loop.run_in_executor(None, ...)` 的默认执行器，**同一类"首次 submit 才建线程"**。
  `ThreadPoolExecutor` 的线程数不会超过 `max_workers`，所以启动时（CPU 空闲）用栅栏把
  全部线程同时拉起建满（本机 `min(32, cpu+4)` = 28 条），运行期这条路径也不再建线程。
  代价是一次性 +28 条空闲线程（`threads` 27→55、进程句柄基线 713→823），是启动台阶，
  不是泄漏；增长率仍是判据。
- 测量装置同源修复（`diag/sitecustomize.py`）：诊断口改为**单线程 HTTPServer**（不再每连接
  `Thread.start`），并去掉满载下最慢的 `net_connections` 枚举，避免诊断口跟着一起失联、
  RSS 采样再丢。

> 复现口径：`rss8h` 的流量形态是"每 60 s 一次采样 + 每 90 s 一个 goal"，请求间隔大于
> anyio 的 worker 空闲回收窗口；`post30` 是 5 条轻量线程持续打点（平均 1.2 s 一次），
> worker 几乎不回收，所以 30 分钟没撞上。**这说明不能靠"多打点"规避**，必须让创建线程
> 本身不再能卡住循环。验收窗口见 §12.9。

### 12.9 修复后验收（一）：`rss8h3` 前 35 分钟（17:54:43–18:29，653 次探活 0 失败）

> 这一轮跑在"anyio 护栏 + 探针池预热"之后，但**早于默认线程池预热**（该改动在 18:30
> 落地），所以 18:29 手工停下、用同一份流量形态重开 `rss8h4`。下面是这 35 分钟的完整
> 数据；最终判定以 §12.10 的 `rss8h4`（含全部修复）为准。

同一台满载宿主（CPU 100%，外部仍有 10+ 个压测进程），本进程自带 **4 个 CPU 燃烧进程**，
流量形态与"撞死"的 `rss8h` 完全一致（`--loaders 4 --heavy --goals-every 90 --sample-every 60`）：

| 指标 | 结果 |
| --- | --- |
| 探活次数 | **653**（每 ~3 s 一次，35 分钟无中断） |
| `/v1/health` 非 200 | **0**（p50 **12.1** / p95 **23.1** / max 97.6 ms） |
| `/v1/metrics` 非 200 | **0**（p50 17.7 / p95 34.4 / max 285.8 ms） |
| `/v1/ready` 非 200 | **0**（p50 22.1 / p95 38.3 / max 112.9 ms） |
| `LoopStallWatchdog` 告警 | **0 条**（旧 `rss8h` 在 30 秒后就连续告警到 967 秒） |
| 慢调用窗口 | 43.5 s 的 chat 期间 14 次探活，health/metrics 均 0 次非 200 |
| `chat` | 226 次，**0** 失败 |
| `goal` | 21 个，**0** 失败 |
| `/v1/audit/verify` | 41 次，0 非 200，p50 **38.4** / p95 **101.3** / max 213 ms（库 2,454 行） |

对照：同一份流量形态在修复前（`rss8h`）2 分 30 秒就僵死且 967 秒不自愈。修复后 35 分钟
（653 次探活、每 3 s 一次）一次没停。**D-1 验收通过（本轮）**。

**RSS/审计库的 30 分钟中间数**（8 小时判据仍在累积，不外推）：

| 量 | 起点 | 30 分钟末 | 形态 |
| --- | --- | --- | --- |
| RSS | 109.1 MB | 118.9 MB | 斜率 +15.8 MB/h，但**非单调**（8 次回落，如 18:03 116.3→112.5、18:19 117.6→113.2） |
| 句柄 | 713 | 752（峰值 776） | **无趋势**：全程在 748–776 之间震荡，10 次回落 |
| gc 对象数 | 152,388 | 159,750 | **无单调增长**：一直在 156k–159.8k 震荡 —— 本窗口内"只增不删"的集合未见累积 |
| 审计库 db | 0.25 MB | 2.55 MB | ≈ **+4.6 MB/h**（旧默认策略下同负载是 +20.5 MB/h；负载条件同 §12.10）；WAL 在 1.1–10.5 MB 间被周期性截断 |

> 结论口径：句柄与 gc 对象**无趋势**（此前这两项是单调上涨）——这只能说明"这两类泄漏源在
> 当前窗口内未见增长"，**不等于「没有内存泄漏」**。RSS 的 +15.8 MB/h 出现在前 30 分钟，
> 且伴随 8 次回落，判断是 pymalloc arena/碎片随工作集扩张而不是对象滞留；这是**判断**，
> 不外推。最终判定只写"8 小时内未见单调增长"（或如实写未达标），要等 `rss8h4` 跑满。

### 12.10 修复后验收（二）：`rss8h4` 前 30 分钟（18:34:50–19:05）——**19:08 复现，本轮作废**

> **先读 §12.11**：这一窗 30 分钟全绿，但在 **19:08**（第 33 分钟）整站再次僵死——
> 探针池预热只建出 2 条线程（`liuhao-probe_0/1`），第三个并发探针又在事件循环里
> 现场 `Thread.start`。所以下表**不能**作为 D-1 的验收证据，只保留为"修复前"的对照。


这一轮包含**全部**修复（anyio 护栏 + 探针池预热 + 默认线程池预热），流量形态与 §12.9
完全相同（`--loaders 4 --heavy --goals-every 90 --sample-every 60`，宿主 CPU 82–87%）：

| 指标 | 结果 |
| --- | --- |
| 探活次数 | **591**（每 ~3 s 一次） |
| `/v1/health` 非 200 | **0**（p50 **13.1** / p95 **27.3** / max 101.7 ms） |
| `/v1/metrics` 非 200 | **0**（p50 17.2 / p95 33.9 / max 299.6 ms） |
| `/v1/ready` 非 200 | **0**（p50 22.9 / p95 37.8 / max 286.1 ms） |
| `LoopStallWatchdog` 告警 / 栈转储 | **0 条 / 0 次** |
| 慢调用窗口 | 39.4 s 的 chat 期间 13 次探活，health/metrics **0** 次非 200 |
| `chat` / `goal` | 198 次 / **0** 失败；21 个 goal / **0** 失败 |
| `/v1/audit/verify` | 41 次全 200，p50 47.6 / p95 **138.0** / max 211.7 ms（库 2,299 条、增量模式） |

**RSS/句柄/gc 前 30 分钟（31 个采样）**：

| 量 | 起点 | 30 分钟末 | 形态 |
| --- | --- | --- | --- |
| RSS | 107.6 MB（t+0）/ 109.9（首帧） | 117.1 MB（峰值 119.9） | 斜率 +14.3 MB/h，**非单调**（9 次回落，如 18:44 117.9→18:45 113.2、19:02 119.9→19:03 116.1） |
| 句柄 | 823（含预热的 28 条线程） | 862（峰值 896） | **无趋势**：在 861–877 之间震荡，14 次回落 |
| 线程数 | 55（bootstrap 后） | 40–45 | 稳定，无增长 |
| gc 对象数 | 153,461 | 159,394 | 在 157k–160.5k 震荡，无单调增长 |
| 审计库 db | 0.25 MB | 2.44 MB | ≈ **+4.5 MB/h**；WAL 在 1.1–10.5 MB 之间被周期性截断 |

> 审计库增速的负载条件（同 §12.10 表头，与改前 20.5 MB/h 的对比必须带这个前提）：宿主
> CPU 82–87%（4 个 CPU 燃烧进程 + 其它 agent 的 pytest 并发）、`--heavy` 流量（每 30 s 一次
> chat，含 39 s 慢调用窗口）、每 90 s 建 1 个 goal、每 3 s 一次探活、每 60 s 一次 audit verify。
> 即"满载 + 每 60 s 校验"这一档；不是空载库的写入速率。

> 与 §12.9 相比，RSS 斜率 15.8 → 14.3 MB/h、句柄由"713 起步"变成"823 起步 + 862 末值"
> （预热的 28 条线程各带句柄，是启动台阶）。**判据仍是"非单调"**：两项都出现 9/14 次
> 回落。8 小时终值待跑满（预计 **02:34**）。

### 12.11 D-1 第二次复现：`warm_probe_pool()` 只建出 2 条线程（19:08，已修）

**时间线（`rss8h4`，18:34:50 启动）**

| 时刻 | 事件 |
| --- | --- |
| 18:34:50–19:04 | 探活 591 次，health/metrics/ready 0 次非 200（§12.10），看门狗 0 告警 |
| 19:07:30 左右 | 最后一次正常探活 |
| 19:08:13 | 起连续探活失败（HTTP 3 s 超时 → 记 `hung`），共 **16 次**（19:08–19:13） |
| 19:09 / 19:13 | `LoopStallWatchdog` 告警："事件循环已停滞 264.4 秒（阈值 15s）" |
| 19:13:48 | py-spy 对活进程 22692 取样（证据：`D:\cache\temp\liuhao-fix\stacks\gw22692-1913.dump.txt`） |

**py-spy 现场（与修复前同一个卡点）**

```
Thread 520 (idle): "MainThread"
    Condition.wait (threading.py:355)
    Event.wait (threading.py:655)
    Thread.start (threading.py:999)          <- 无超时
    ThreadPoolExecutor._adjust_thread_count (concurrent\futures\thread.py:203)
    ThreadPoolExecutor.submit (concurrent\futures\thread.py:180)
    BaseEventLoop.run_in_executor (asyncio\base_events.py:867)
    _run_probe (src\gateway\health.py:57)     <- 探针池自己在建线程
    liveness_probe (src\gateway\health.py:182)
```

同一份 dump 里探针线程**只有 2 条**：`liuhao-probe_0`、`liuhao-probe_1`（另有预热成功的
`asyncio_0…27` 共 28 条默认线程池线程）。即：anyio 护栏与默认线程池预热都生效了，唯独
**探针池预热是假成功**。

**根因**：`warm_probe_pool()` 用的是 `list(_PROBE_POOL.map(lambda _: None, range(4)))`。
`ThreadPoolExecutor` 的空闲 worker 会被 `_adjust_thread_count` 的 `_idle_semaphore` 复用，
而 no-op 任务在两次 submit 之间就已完成 —— 线程数涨不到 4（实测 2）。此后三个探针并发
（超时用 `asyncio.shield`，上一发不会被取消，会继续占着线程）一旦超过 2 条，第三次
submit 就在事件循环线程里现场 `Thread.start` → 满载时永不自愈。

**修复**（[health.py](file:///d:/LiuHao-AI-OS/src/gateway/health.py)）：改成与
`warm_default_executor()` 相同的栅栏法 —— 提交 N 个任务，每个都卡在
`threading.Barrier(N)` 上，逼迫执行器真的建出 N 条线程，再一起放行；返回值改为
**实际线程数**（`len(_PROBE_POOL._threads)`），不再硬编码 4。

**护栏（可证伪）**：`test_probe_pool_threads_are_created_before_the_first_probe` 改用
**独立的新池**（避免被前面 TestClient 用例提前"喂饱"而假绿），断言预热后 `_threads == 4`，
并让 4 个任务同时站在 4 方栅栏上。反证：用旧的 map 预热法 + 一个已空闲 worker，实测只
建出 **2** 条线程，上述断言必然失败（`D:\cache\temp\liuhao-fix\red_probe_pool.py`，输出
"旧预热法建出的线程数: 2"）。

### 12.12 本轮收尾：繁忙报文统一为四字段 + 30 分钟满载复测（`fix30b`，23:28–23:58）

#### 12.12.1 同一产品只剩一套 503 繁忙报文

改前，同一类"探针超时"有**两种**形状：`/v1/ready`、`/v1/metrics` 走
`_busy_response`（四字段），而
`/v1/ready/subsystems`、`/v1/production/preflight` 的超时分支各自手写了一份
`{"status":"busy","reason":"probe_timeout","user_message":"…请稍后重试。"}`。

改后这两处直接复用 `health.py` 的 `_busy_response()`。实采报文（`busy_after.txt`）：

```json
{"code":"PROBE_BUSY","message":"就绪检查暂时取不到数据：系统正在处理较慢的请求（探针繁忙），服务本身仍在运行，请 1 秒后重试。","correlation_id":"trace-busy-abc","retry_after":1}
```

响应头：`Retry-After: 1`、`X-Correlation-ID: trace-busy-abc`。

> **结论（写给后来改代码的人）**：旧形状的提示只放在 `user_message` 里，而前端
> `errorMessage.ts` 的 `describeFailure` 读的是顶层 `message` / `code`（兼容
> `detail.code`），**从不读 `user_message`** —— 旧形状的中文提示**一直在丢**，
> 用户只能拿到 503 的兜底文案。不要把繁忙报文改回旧形状；`status=busy` 的语义
> 已保留在 `code=PROBE_BUSY` 和中文 `message` 里。

护栏：[test_production.py](file:///d:/LiuHao-AI-OS/tests/observability/test_production.py)
的 `TestHttpEndpoints::test_probe_timeout_503_uses_four_fields` 参数化覆盖
`/v1/ready/subsystems` 与 `/v1/production/preflight`，断言
`set(body) == {code, message, correlation_id, retry_after}`、`code=PROBE_BUSY`、
`correlation_id` 回传请求的 `X-Trace-ID`、`Retry-After` / `X-Correlation-ID` 头一致。
`/v1/metrics` 的同类断言在 `tests/test_gateway_health_wiring.py`。两个文件合跑
**62 passed**（2 warnings，22.49 s）。

消费方核查：全仓**没有**消费 HTTP 503 报文里 `status:"busy"` 的代码（后端与
`apps/console` 均无此分支）。命中的 `busy` 都属于其它语义：`src/desktop/probe.py`
是端口占用探测结果，`src/ai/employee.py` 的 `BUSY` 与 `apps/console/.../employees.ts`
的 `'busy'` 是员工运行态。

**残留（未改，如实登记）**：[main.py](file:///d:/LiuHao-AI-OS/src/gateway/main.py#L536-L548)
里的 `OverloadGuardMiddleware` 在"在途请求超过上限"时仍回旧的五字段形状
（`status` / `reason:"too_many_in_flight"` / `status_label` / `user_message` /
`max_in_flight`）。它没有走 `_busy_response`，本轮只统一了**探针超时**这一类；
`main.py` 不在本轮改动范围，是否一并统一需单独授权。

#### 12.12.2 30 分钟满载窗口

条件：**14 个 CPU 燃烧进程**（`--loaders 14`）、每 1 秒一轮探活（health/ready/metrics）
+ 轻量业务请求；宿主同期 CPU **84.7–100%**（353 次采样，均值 **97.8%**）。被测源码快照
`fix30v2src` 当时包含 **health.py 的四字段繁忙报文 + "超时清理不在事件循环线程里
close 连接"** 两条修复；`observability.py` 两处统一是在这一窗结束之后（00:06）才落地的，
因此本窗口没有跑到那条分支（它本来也是 0 次非 200）。

| 指标 | 结果 |
| --- | --- |
| 总请求 | **6,177**（health 1,765 + ready 1,765 + metrics 1,765 + business 882） |
| 非 200 | **0**（四类全 0） |
| `/v1/health` 延迟 | p50 **0** / p95 **16** / max **3,890** ms（单次进程冷启动尖峰） |
| `/v1/ready` 延迟 | p50 **16** / p95 **16** / max **219** ms |
| `/v1/metrics` 延迟 | p50 **0** / p95 **16** / max **31** ms |
| 轻量业务 | p50 **15** / p95 **16** / max **31** ms |
| 网关 CPU | 进程树累计 **56.05 s** = **3.1%** 单核 |
| RSS | 86.4 → 107.7 MB（Δ **+21.3**），**非严格单调**（35 次回落，单次最大 2.2 MB） |
| 线程数 | 55 → 44（峰值 55；预热的 28 条默认池线程在启动台阶后回落） |
| 身份降级 | 全程 `degraded=false`，`degraded_total=0`、`rejected_total=0`，4 lane 存活 |
| `LoopStallWatchdog` | **0** 告警、0 次栈转储 |

**判定口径**：这 30 分钟窗口内探活一次没失败、事件循环没有停滞；但**8 小时 RSS 曲线
仍然不存在**，不外推"已修复"，§0.4 的 D-1 判定保持"未达标"。

#### 12.12.3 `/v1/ready/subsystems` 真实返回（TCP，满载）

人类主体 token（请求头 `X-Trace-ID: trace-subsys-tcp`），宿主 CPU **99.9%**；验证用的是
00:10 刷新后的快照（已含 `observability.py` 的两处统一）：

| 轮次 | 状态 | 耗时 | 响应头 | 响应体 |
| --- | --- | --- | --- | --- |
| 1 | **200** | 390 ms | 含 `x-trace-id` | `status:"ready"`、`total:15`、`healthy:15`、`degraded:0`、`unavailable:0` |
| 2 | **200** | 16 ms | 含 `x-trace-id` | 同上 |
| 3 | **200** | 15 ms | 含 `x-trace-id` | 同上 |

首轮 390 ms 是 15 个内核首次 import；后两轮 15–16 ms。本轮磁盘上 **15** 个内核（含
`retention` 数据保留）。冷启动没有撞到 10 s 探针预算，所以四字段 503 路径由
§12.12.1 的 ASGI 用例覆盖（真实 TCP 无法人工制造 10 s 卡顿）。

> 口径提醒：验证时首轮曾用 `metadata={}` 的 token 打回来 **403**
> （"当前登录的不是个人账号…"），补 `{"kind":"human","scope":"L0"}` 后才 200。
> 这是 `require_human_principal` 的前置条件，不是端点故障。


