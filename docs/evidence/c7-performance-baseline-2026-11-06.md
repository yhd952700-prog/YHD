# C7 性能基线（2026-11-06）

> 目的：把 C7 从「本机 profile 无基线，数值门禁只能 SKIP」变成「有基线、可判定」。
> 本文只陈述采集事实与判定结果，不改 `scripts/verify_readiness.py` 的判定规则，
> 不改 `scripts/bench_audit_append.py` 的比较阈值（吞吐低 25% / 延迟高 25% 判回归）。

## 1. 结论

- 本机 profile `Windows|py3.12.14|Intel64_Family_6_Model_63_Stepping_2,_GenuineIntel|t24|quick`
  已写入 `scripts/bench_baseline.json`，C7 现在**真的执行数值比较**。
- `python scripts/verify_readiness.py --only C7` → **`[C7] PASS — bench --quick --gate PASS on this machine's own profile`**，
  该次单独运行的汇总为 `PASS=1 FAIL=0 BLOCKED=0 NOT_VERIFIED=0 ERROR=0`。
- **诚实告警（不粉饰）**：本轮 6 次采样的方差很大（单条追加吞吐极差达中位数的 80%，
  p95 延迟极差达 1594%）。基线取的是「安静窗口」的中位数，所以门禁在机器繁忙时
  会判失败。这是基线本身的稳定性问题，不是判定口径问题；详见 §5、§8。

## 2. 机器条件

| 项目 | 值 |
|---|---|
| CPU | Intel(R) Xeon(R) CPU E5-2673 v3 @ 2.40GHz |
| 核心/线程 | 12 核 / 24 线程（`t24`，profile key 的一部分） |
| 内存 | 34,205,548,544 字节（31.9 GiB） |
| 操作系统 | Windows 10.0.19045（`platform: win32`） |
| 解释器 | 仓库 venv `d:\LiuHao-AI-OS\.venv\Scripts\python.exe`，CPython 3.12.14 |
| 采集时段背景负载 | 系统 CPU 约 16%；另一个 pytest 进程（PID 20996）持续占用约 1 核并产生磁盘尖峰 |
| 采集时段 | 2026-11-05 21:06 前后（本地时间 UTC+8） |

磁盘为普通 SSD/HDD 未知型号，SQLite 落在系统盘临时目录（每次运行新建，跑完即删）。
**同一个 profile key 在别的机器上不构成回归信号**：key 内含 CPU 型号与线程数，
换机器就会换 key、门禁回到 SKIP。这是既有设计，本轮没有改。

## 3. 采集方法

- worktree：`D:\copilot-worktrees\liuhao-final-bb6b654c`（clean，detached 到 `bb6b654c`，
  **在 `%TEMP%` 根之外**，满足 E-10 的防护要求）。
- 命令（每个样本一次，`--quick`）：
  `python scripts/bench_audit_append.py --quick --write D:\cache\temp\bench-run-<i>.json`
- 样本数：**6 次**。原计划 3 次；第 4/5 次撞上另一 pytest 进程的负载尖峰后，
  追加 2 次以刻画方差，因此是 6 次。
- 聚合：逐指标取 **中位数**（`statistics.median`，6 个值的中位数 = 第 3、4 位均值）；
  布尔不变量要求 **6 次全为 true** 才记为 true。
- **异常样本未丢弃**：RUN4/RUN5 比安静窗口慢约 3 倍，仍保留在样本集里，
  并如实反映在 §5 的方差表。
- 合并脚本（一次性、不进仓库）：`D:\cache\temp\merge_bench.py`。

## 4. 逐次样本

| # | 单条追加 (事件/秒) | 单条 p95 (ms) | batch=100 (事件/秒) | 多进程（不含启动） | 恢复 (s) | 不变量 |
|---|---|---|---|---|---|---|
| 1 | 658.1 | 2.629 | 12,463.2 | 16,149.9 | 0.040 | 全 true |
| 2 | 690.5 | 2.324 | 11,201.0 | 7,913.0 | 0.030 | 全 true |
| 3 | 706.8 | 2.516 | 14,509.3 | 12,532.4 | 0.027 | 全 true |
| 4 | 220.1 | 41.297 | 4,695.4 | 5,878.5 | 0.050 | 全 true |
| 5 | 191.4 | 43.411 | 4,462.8 | 4,722.5 | 0.061 | 全 true |
| 6 | 630.4 | 2.525 | 11,111.8 | 8,118.7 | 0.049 | 全 true |

不变量四件套（6 次均为 true）：`chain_verifies`、`no_lost_appends`、`contiguous_seq`、
`chain_intact_after_kill`。

## 5. 方差（如实）

| 指标 | min | max | median | 极差 / 中位数 |
|---|---|---|---|---|
| `single_append.events_per_sec` | 191.4 | 706.8 | 644.25 | **80.0%** |
| `single_append.latency_ms_p95` | 2.324 | 43.411 | 2.577 | **1594.4%** |
| `batched[10].events_per_sec` | 1,183.0 | 5,163.0 | 4,639.1 | **85.8%** |
| `batched[100].events_per_sec` | 4,462.8 | 14,509.3 | 11,156.4 | **90.1%** |
| `multi_process`（不含启动） | 4,722.5 | 16,149.9 | 8,015.85 | **142.6%** |
| `multi_process`（含启动） | 1,356.5 | 2,187.4 | 2,004.7 | **41.4%** |
| `recovery.recovery_seconds` | 0.027 | 0.061 | 0.0445 | **76.4%** |

**结论：方差过大，这组数字不能当作 SLA 或容量承诺。** 它们只能证明：
(a) 实现的不变量在所有样本里都成立；(b) 在安静窗口下，这台机器的吞吐落在一个
可重复的窄带（单条 630–707 事件/秒，p95 2.3–2.6 ms）。

## 6. 中位数基线（写入 `scripts/bench_baseline.json` 的条目）

| 指标 | 基线值 |
|---|---|
| `single_append.events` | 500 |
| `single_append.events_per_sec` | **644.25** |
| `single_append.latency_ms_p50 / p95 / p99` | 1.417 / **2.577** / 4.526 ms |
| `batched[10].events_per_sec` | **4,639.1**（批次 p95 3.369 ms） |
| `batched[100].events_per_sec` | **11,156.4**（批次 p95 12.998 ms） |
| `multi_process[2 进程].excluding_startup` | **8,015.85** |
| `multi_process[2 进程].including_startup` | **2,004.7** |
| `recovery.recovery_seconds` | **0.0445**（写入条目按 3 位小数为 0.044） |
| `chain_verifies` / `no_lost_appends` / `contiguous_seq` / `chain_intact_after_kill` | true / true / true / true |
| `disk_bytes_per_event` | 724.1 |
| `database_bytes`（10,500 事件） | 7,697,376 |

仓库文件里同时保留了原有的 `Windows|py3.13.14|...` profile；
本轮只新增 py3.12.14 这一条，未改动旧条目。

## 7. 门禁验证（一次独立样本）

验证样本（与基线分离的一轮 `--quick`）：

| 指标 | 实测 | 基线 | 门禁（±25%） |
|---|---|---|---|
| `single_append.events_per_sec` | 636.8 | 644.25 | 下限 483.2 → 通过 |
| `single_append.latency_ms_p95` | 2.654 ms | 2.577 ms | 上限 3.221 ms → 通过 |
| `batched[10].events_per_sec` | 4,939.8 | 4,639.1 | 下限 3,479.3 → 通过 |
| `batched[100].events_per_sec` | 11,967.4 | 11,156.4 | 下限 8,367.3 → 通过 |
| `multi_process.excluding_startup` | 12,137.4 | 8,015.85 | 下限 6,011.9 → 通过 |
| `multi_process.including_startup` | 1,958.0 | 2,004.7 | 下限 1,503.5 → 通过 |
| `recovery.recovery_seconds` | 0.036 | 0.0445 | 上限 0.0556 → 通过 |
| `chain_verifies` | true | — | 必须 true → 通过 |

输出末行：`=== PERFORMANCE GATE: PASS ===`。
日志：[c7-gate-validation-2026-11-06.log](c7-gate-validation-2026-11-06.log)（1,785 字节）。
`verify_readiness.py --only C7` 的判定日志：
[c7-readiness-only-2026-11-06.log](c7-readiness-only-2026-11-06.log)（493 字节）。

## 8. 基线稳定性告警（必须与结论一起读）

1. **噪声窗口会误判回归**：安静窗口约 630–707 事件/秒，噪声窗口 191–220 事件/秒，
   已低于门禁下限 483.2。若在繁忙机器上跑全量，C7 会 FAIL。
   这不会被解释成「产品回归」，但门禁本身无法区分两者。
2. **p95 极差 1594%**：延迟基线 2.577 ms 在噪声窗口会飙到 41–43 ms，
   上限 3.221 ms 形同虚设（这种情形下 C7 只会因吞吐先失败）。
3. **样本只有 6 个、且分布双峰**：中位数落在安静那簇，代表「安静窗口性能」，
   不代表「平均性能」。
4. 因此本轮的准确表述是：**C7 从「无基线 → 只能 SKIP」变成「有基线、在本机安静窗口可判定」；
   它不是一台稳定 runner 上的 CI 门禁。** 要做成 CI 门禁，仍需按
   `CONDITION_META["C7"].next_action` 在固定 runner 上重采（`--write-baseline`）。

## 9. 复现命令

```powershell
# 采集一个样本（worktree 内，用仓库 venv 解释器）
d:\LiuHao-AI-OS\.venv\Scripts\python.exe scripts\bench_audit_append.py --quick --write D:\cache\temp\bench-run-1.json

# 直接用仓库根做一次 C7 判定（会写入 scripts/readiness_report.json，该文件不入库）
d:\LiuHao-AI-OS\.venv\Scripts\python.exe scripts\verify_readiness.py --only C7
```

## 10. 产物

| 文件 | 字节 | 说明 |
|---|---|---|
| [bench-baseline-py312-2026-11-06.json](bench-baseline-py312-2026-11-06.json) | 6,249 | 6 次样本 + 方差 + profile 条目（可读证据副本） |
| `scripts/bench_baseline.json` | +70 行 | 真正被 C7 读取的基线（新增 py3.12.14 profile，保留 py3.13.14） |
| [c7-gate-validation-2026-11-06.log](c7-gate-validation-2026-11-06.log) | 1,785 | 独立验证样本 + `PERFORMANCE GATE: PASS` |
| [c7-readiness-only-2026-11-06.log](c7-readiness-only-2026-11-06.log) | 493 | C7 单独判定 PASS |
