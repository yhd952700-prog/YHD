# HC-01 审计链状态（事实与缺口）

> 本文只陈述**已执行的运行时事实**与**仍然存在的缺口**，不写 GO / NO-GO，不做放行判断。
> 复核基线：`HEAD 82f95450`；复核日期 2026-11-06；本机 Windows，`AUDIT_DB_PATH` 未覆盖。

## 1. 本轮实际执行的命令与结果

| 命令 | 退出码 | 真实结果 |
|---|---|---|
| `python scripts/verify_p08b_chain_matrix.py` | 0 | **81/81 条已确认断言通过，0 失败**；HC-01 的 8 条断言全 PASS；HC-09/HC-10 记 `UNVERIFIED`（内存态，非 HC-01） |
| `python scripts/verify_p08_final_status_matrix.py` | 1 | 汇总 `COMPLIANT 8 / LEGACY 0 / UNVERIFIED 3 / BLOCKED 0`；**HC-01 = UNVERIFIED**；唯一漂移是 HC-11（见 §4.2） |
| `pytest tests/kernels/audit tests/kernels/test_hash_chain_p08*.py tests/test_p08_hc01_evidence_grade.py tests/test_verify_go_readiness.py -q` | 0 | **277 passed, 17 skipped, 0 failed**，耗时 144.42s |

## 2. HC-01 代码实现：运行时测到的 8 个维度

来源：`scripts/verify_p08b_chain_matrix.py::probe_hc01`（每次执行都新建临时库、独立重算哈希，不读文档、不继承旧报告）。

| 维度 | 实测结果 | 证据 |
|---|---|---|
| `hash_alg` 声明 | 每一行都持久化 `sha256` | `declared=['sha256']` |
| 规范化 | 8 字段 `json(sort_keys, separators)`；`event_hash` / `prev_event_hash` 不参与输入 | 独立重算 == 存储的 `event_hash` |
| 链接规则 | `prev_event_hash` 指向上一条 `event_hash`（仅链接，不进哈希输入） | 篡改 `prev_event_hash` 后 `verify_integrity()==False`，但内容哈希不变 |
| 独立重载校验 | 全新实例从磁盘重载后 `verify_integrity()==True` | P08b 断言 PASS |
| 篡改检出 | 改 `principal_id` → `False`；改链接 → `False` | P08b / P08 双脚本 |
| 未知算法 fail-closed | `hash_alg='sha512-unknown'` → `False`，不回退 sha256 | 日志明确打印 “counting it as broken rather than assuming a default algorithm” |
| 缺失声明 fail-closed | `hash_alg=''` → `False`（此前是静默回退，现已 fail-closed） | P08b “missing hash_alg -> fail-closed” |
| 多写者防分叉 | 单写者租约在**写事务内部**获取与释放；整条 append 在一个 `BEGIN IMMEDIATE` 事务内完成 | `src/kernels/audit/__init__.py:878-889`；测试 `tests/kernels/audit/test_multiprocess_append.py`、`test_fencing_wired.py`、`test_audit_fork_detection.py` |

## 3. 持久数据：本机观测事实（不是发布证据）

本机仓库根存在 `audit_store.db`（gitignored 运行产物）。对它的只读快照检查结果：

- 声明覆盖：**40482 / 40482 行带 `hash_alg` 声明**；
- 链连续性：**0 broken joins、0 duplicate seq、0 orphan links**；
- `verify_integrity()`：**`is_ok=True`，覆盖 40482 行**；
- 结论口径：**这些数字只说明本机这份运行数据当前是完整的，不能作为发布证据**。原因是该文件不进版本库、换一台机器就不存在，同一份代码会得到不同状态。

当前矩阵据此把 `continuity_ok` 固定为 `N/A`，HC-01 的 `why` 为：

```
persisted chain continuity not demonstrated
```

因此 **HC-01 在所有主机上都稳定派生 `UNVERIFIED`，与本机数据是否健康无关**。这是刻意的证据分级，不是实现缺陷。

## 4. 缺口

### 4.1 “真实持久链”没有可复核的证据入口（HC-01 的唯一实质缺口）

- 现状：临时库证明“实现正确”；仓库根运行库证明“本机数据健康”，但二者都不构成可复核的发布证据。
- 影响：HC-01 永远停在 `UNVERIFIED`，即使数据完全健康。
- 缺口边界：缺少一个**由复核人显式指定路径**的校验入口（例如 `--db <path>`），让发布流程可以对一份受控数据库生成并归档证据。当前矩阵刻意**不自动发现**任何运行产物，所以没有这个入口。
- 本轮**未新增**该入口：它会改变 `EXPECTED` 的判定语义（显式库健康时是否允许 `COMPLIANT`），属于产品负责人的判定口径决定，不在本切片自行改断言。

### 4.2 最终矩阵 exit 1 的原因不在 HC-01

- 漂移项：`HC-11: expected UNVERIFIED, derived COMPLIANT`。
- 实测原因：本机 `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 已配置（安装版启动器 `scripts/launch_installed.py:167-250` 会生成并注入该密钥），`classify()` 因此认为“原始行可被接纳”，派生 `COMPLIANT`；而 `EXPECTED["HC-11"]` 仍写着“本机无密钥”。
- 影响：**这是声明期望与主机姿态不一致**，不是 HC-01 回归，也不是链实现回归。HC-11 的机制（篡改必被拒）在两种姿态下都实测为 `FAIL-CLOSED`。
- 本轮**未修改**该声明：改它等于改断言，按红线留给产品负责人决定（更新期望为“姿态相关”，或固定测试姿态）。

### 4.3 残留的产物读取（仅负向、仅取证）

- `continuity_ok` 在产物断裂时会置 `NO`，从而把 `why` 写成 “persisted chain continuity broken”。
- 它**不会**把 UNVERIFIED 变成别的状态，也不会把红变绿；本轮已把产物观测单独打印为 `runtime artifact: ... [NOT release evidence]`，并加回归测试锁定“健康产物不得成为正向证据”。
- 回归：`tests/test_p08_hc01_evidence_grade.py`（2 passed）——健康产物 → `N/A`；断裂产物 → `NO` 但状态仍 `UNVERIFIED`。

### 4.4 非 HC-01 但同矩阵的未决项（不计入 HC-01）

- `HC-09`、`HC-10`：`UNVERIFIED`，原因是审计事件只存在内存 list，无持久化调用（P08b 明确记录）。
- `HC-11`：机制 `FAIL-CLOSED`；部署姿态取决于是否配置完整性密钥（见 §4.2）。

## 5. 本轮改了什么（HC-01 相关）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `scripts/verify_p08_final_status_matrix.py` | 把运行产物观测从 `migration` 字段拆出，单独输出 `runtime artifact ... [NOT release evidence]`；`continuity_ok` 的注释与判据写清“健康/缺失均为 N/A、断裂仅取证”；更新 HC-01 的 `EXPECTED` 注释（原注释称“本机链已分叉”，与实测不符） | 消除“把运行产物当证据”的残余歧义；让矩阵结论与主机数据解耦 |
| `tests/test_p08_hc01_evidence_grade.py` | 新增 2 条回归：健康产物不得成为正向证据；断裂产物只能作为取证信号 | 用测试锁住证据规则，防止以后有人把 `YES` 加回来 |
| `docs/hc01-status.md` | 本文件 | 按要求只陈述事实与缺口 |

未改动：`src/kernels/audit/**`（审计链实现）、`audit_store.db`（冻结/真实数据）、`EXPECTED["HC-01"]` 的结论值。

## 6. 可复现命令

```powershell
# 1) 实现层（临时库，证明代码）
.venv\Scripts\python.exe scripts\verify_p08b_chain_matrix.py

# 2) 状态矩阵（含 HC-11 姿态漂移）
.venv\Scripts\python.exe scripts\verify_p08_final_status_matrix.py

# 3) 只看本机持久数据的观测（不是发布证据）
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('audit_store.db'); print(c.execute('SELECT COUNT(*), SUM(CASE WHEN hash_alg IS NULL OR hash_alg=\"\" THEN 1 ELSE 0 END) FROM audit_events').fetchone())"

# 4) HC-01 相关测试
.venv\Scripts\python.exe -m pytest tests\kernels\audit tests\kernels\test_hash_chain_p08.py tests\kernels\test_hash_chain_p08b_matrix.py tests\kernels\test_hash_chain_p08c_migration.py tests\test_p08_hc01_evidence_grade.py tests\test_verify_go_readiness.py -q
```
