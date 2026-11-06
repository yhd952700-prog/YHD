# C9 部署 / 升级 / 回滚（2026-11-06，两套口径并存）

> **本文件刻意保留两条独立结论，不合并成一条 PASS：**
>
> - **原口径（Docker / CD）**：`C9 = NOT_VERIFIED`，**判定未改动**。
> - **分发形态（安装树）**：已用真实安装树副本完成升级/重启/回滚/数据保全验证，
>   数字与日志见 §2。
>
> 两条结论描述的是两个不同的交付形态，互不替代。

## 1. 原口径：`C9 = NOT_VERIFIED`（保持不变）

`scripts/verify_readiness.py::check_deployment` 的判定逻辑与理由本轮**一行未改**：

```
[C9] Deployment / upgrade / rollback: NOT VERIFIED — Dockerfile + alembic present; real CD absent=True (honest)
```

- 为什么仍是 NOT_VERIFIED（`CONDITION_META["C9"]` 原文要点）：
  - 权威的 `docker compose config` 未验证（本环境缺少 compose v2 插件）；
  - 镜像没有被真正构建；
  - 真实的 CD 按设计缺失（`deploy` job 里 `exit 1`，`cd_absent=True`）；
  - `verify_deploy_readiness.py` 需要 alembic/sqlalchemy/pyyaml，而 `verify_readiness.py`
    自身就跑在仓库 venv 上，`g9_py == sys.executable` 时回落到「文件存在性」判定。
- 部署目标（主机/密钥/数据库/规模/审批）仍未选定，属人类决策，不在本切片内解决。

**结论：原口径 NOT_VERIFIED。** 本节不因 §2 的安装树验证而改变。

## 2. 分发形态（安装树）：已独立验证

### 2.1 验证对象与来源

| 项目 | 值 |
|---|---|
| 安装树副本 | `D:\copilot-worktrees\liuhao-c9-installed-20261106`（由 `D:\cache\temp\liuhao-e2e\installed` robocopy 而来，**在 temp 根之外**） |
| 文件/字节 | **10,769 个文件 / 204,834,804 字节**（含 `runtime/`、`__pycache__`） |
| 运行解释器 | `<tree>\runtime\python.exe` → **CPython 3.12.14**（安装器自带 runtime，非仓库 venv） |
| 版本标记 | `<tree>\LIUHAO_RELEASE.json`：`{"schema":"liuhao.release-manifest/1","version":"0.1.0","channel":"installed-test","build":"2026-11-05T13:43:02"}` |
| 数据哨兵 | `<tree>\data\c9-sentinel.txt` = `sentinel-20261105-211139`（29 字节），SHA256 `051F79B9F45934E70C67EBC25FBD10318C016B231FB5EDA1F656DBCBADD8A4A9` |
| 用户数据 | `<tree>\data\c9-userdata.sqlite3`（8192 字节），表 `note`，行 `(1, 'keep-across-upgrade-and-rollback')` |

> 安装树原本既没有 `pyproject.toml` 也没有 `LIUHAO_RELEASE.json`，
> `src/upgrade/versioning.py` 只能回落到 installed metadata，版本不确定。
> 本轮补写 `LIUHAO_RELEASE.json` 只是为了得到确定的 `0.1.0`，不改产品代码。

### 2.2 升级包（本地发布目录）

| 项目 | 值 |
|---|---|
| 发布目录 | `D:\copilot-worktrees\liuhao-c9-release`（含 `LIUHAO_RELEASE.json` 指向 zip） |
| 载荷 | `liuhao-0.1.1.zip`，**662 个文件 / 2,741,388 字节** |
| SHA256 | `1a56e7a83991841921c8b3496344fa0d5ce9ac425744b4e1691f5d6ca3af9c10` |
| 来源 | clean worktree `D:\copilot-worktrees\liuhao-delta-d2a62b0e`（commit `d2a62b0e`） |
| 签名 | `signature=false`；工作树只做 **SHA256 校验**（`signature_verified=false`，ledger 记「仅哈希校验，无签名」） |
| 载荷边界 | 含 `src/`、`config/`、`scripts/`、`requirements.txt`、`oss-registry.yaml`、`capability-registry.yaml`、`error_types.py`；**不含 `console/`、`assets/`、`LiuHao.bat`、`START-HERE.txt`**（这些路径在该 worktree 布局里不存在，组装时被跳过）。所以升级只覆盖后端，不覆盖前端 |

### 2.3 两轮完整循环（真实数字）

两轮都在同一棵安装树副本上执行，用安装树自带 runtime 调 `python -m src.upgrade`。

**第 1 轮（数据目录只有哨兵文本，无 SQLite）**

| 步骤 | 结果 | 数字 |
|---|---|---|
| `check` | `update_available`，`0.1.0 → 0.1.1` | 0.71 s |
| `upgrade`（download→backup→apply→selfcheck） | 完成，`0.1.0 → 0.1.1` | **12.50 s** |
| 备份 | `backup_id=20261106T051249Z-89fd3963`，代码 **512 文件**，`code_zip_sha256=38e7d05d…7727`，数据 **1 文件 / 29 字节**（`data_sqlite_count=0`） | — |
| 安装 | `installed_files=662` | — |
| 自检 | `self_check_passed=true`，**真实启动服务并 `/v1/health` 返回 200**，`startup_seconds=5.484`，`databases_checked=0`、`tables_checked=0` | 5.48 s |
| 重启 0.1.1 | 健康检查 200 | **5.34 s** |
| `rollback` | `0.1.1 → 0.1.0`，`restored_files=513` | **2.29 s** |
| 重启 0.1.0 | 健康检查 200 | 9.87–9.90 s |

**第 2 轮（数据目录加入真实用户 SQLite）**

| 步骤 | 结果 | 数字 |
|---|---|---|
| `check` | `update_available` | — |
| 预检 | `files_checked=3`、`sqlite_checked=1`、`ok=true` | — |
| 备份 | `backup_id=20261106T051506Z-40ae47c1`，代码 **512 文件**，`code_zip_sha256=06baccd1…c989`，数据 **3 文件 / 14,878 字节**（`data_sqlite_count=1`，`key_tables` 含 `c9-userdata.sqlite3`） | — |
| `upgrade` | `installed_files=662`、`data_checked_files=3`、`0.1.0 → 0.1.1` | **12.62 s** |
| 自检 | `self_check_passed=true`，真实启动 + `/v1/health` 200，`startup_seconds=5.531`，`databases_checked=1`、`tables_checked=1` | 5.53 s |
| `rollback` | `0.1.1 → 0.1.0`，`restored_files=515` | **2.45 s** |

重启日志（stderr）：
[c9-restart-0.1.1.err.log](c9-restart-0.1.1.err.log)（1,243 字节）、
[c9-restart-0.1.0.err.log](c9-restart-0.1.0.err.log)（1,244 字节）。
两份日志都记录了 `background_verifier_started` 与 `checkpoint_tick_complete`
（`status=ok`、`coverage_ratio=1.0`），说明重启后后台校验器真实工作。

> 第 1 轮自检的日志里出现过一次「`REFUSING registry row ...`」与
> 「`AUDIT_DB_PATH is not set ...`」——原因是自检子进程继承了本机 ambient 环境变量
> （`LIUHAO_HUMAN_IDENTITIES_DB`、`AUDIT_DB_PATH`）指向仓库根数据。
> 这是**测试环境的污染**，不是产品缺陷；重启前清空这些变量、显式指向安装树数据目录后，
> 0.1.1 干净重启 5.34 s、health 200、checkpoint 正常。如实记录在此。

### 2.4 数据目录保全

| 检查 | 结果 |
|---|---|
| 升级后哨兵 | `c9-sentinel.txt` 内容与 SHA256 **完全不变**（`051F79B9…A4A9`） |
| 升级后用户库 | `note` 表仍为 `(1, 'keep-across-upgrade-and-rollback')` |
| 回滚后哨兵 | 内容与 SHA256 仍不变 |
| 回滚后用户库 | `note` 行仍在 |
| 回滚后数据目录条目 | **6 项**：`observability/`、`audit_store.db`（4,096 B）、`audit_store.db-shm`（32,768 B）、`audit_store.db-wal`（1,050,632 B）、`c9-sentinel.txt`（29 B）、`c9-userdata.sqlite3`（8,192 B） |

**必须披露的两个行为（不粉饰）：**

1. **回滚是「覆盖恢复」而不是「字节级还原」**：`restore_data` 把备份里的文件覆盖回 live，
   但**不删除**备份之后新建的文件。上面 `audit_store.db*` 三个文件是回滚到 0.1.0 之后
   再次启动 0.1.1 期间创建的，回滚后仍然留着。
2. 因此「数据保全」的准确表述是：**升级前已有的数据（哨兵、用户库）在升级与回滚后都完好；
   回滚不会把数据目录恢复到升级前那一刻的精确文件集合。**

### 2.5 未覆盖的边界

| 边界 | 状态 |
|---|---|
| 代码签名 | 未做（`signature=false`），升级只校验 SHA256 |
| 真实 CD / 生产部署目标 | 未做，见 §1 |
| 前端（console）随升级更新 | **未覆盖**：本载荷不含 `console/`；前端更新需另建含前端的载荷 |
| 跨大版本降级防护 | 只验证 `0.1.0 ↔ 0.1.1` |
| 升级中断（进程被杀）后的自动恢复 | 未在本轮演练；该逻辑由 `UpgradeManager._recover_interrupted_upgrade` 与单测覆盖，非本验证项 |
| 真实云发布源（HTTP 下载 + 签名） | 未覆盖：本轮用本地发布目录 |

## 3. 为什么两条结论不能合并

- 原口径问的是「**容器化/持续部署交付链**是否可验证」——答案仍是 NOT_VERIFIED（工具与目标都缺）。
- 分发形态问的是「**装到用户机器上的安装树**能否安全升级、重启、回滚且不丢数据」——答案是已验证（带上述边界）。
- 把后者写成 C9 PASS 会掩盖「镜像没构建、CD 不存在」这两个事实；把前者写成覆盖了升级链
  会掩盖本轮真实做过的安装树验证。两者必须并列。

## 4. 证据清单

| 文件 | 字节 | 内容 |
|---|---|---|
| [c9-check.json](c9-check.json) | 678 | 第 1 轮 check 原始结果（含载荷 sha256/size） |
| [c9-upgrade-1.json](c9-upgrade-1.json) | 301 | 第 1 轮 upgrade 结果（backup_id、662 文件、自检通过） |
| [c9-rollback-1.json](c9-rollback-1.json) | 230 | 第 1 轮回滚（513 文件） |
| [c9-upgrade-2.json](c9-upgrade-2.json) | 301 | 第 2 轮 upgrade（含用户库，data_checked_files=3） |
| [c9-rollback-2.json](c9-rollback-2.json) | 230 | 第 2 轮回滚（515 文件） |
| [c9-history.json](c9-history.json) | 42,617 | 完整 ledger：check/download/backup/apply/selfcheck/rollback 全记录（含自检启动日志尾部） |
| [c9-restart-0.1.1.err.log](c9-restart-0.1.1.err.log) | 1,243 | 0.1.1 重启 stderr |
| [c9-restart-0.1.0.err.log](c9-restart-0.1.0.err.log) | 1,244 | 回滚后 0.1.0 重启 stderr |

安装树本身（10,769 文件 / 204,834,804 字节）与本地发布目录、两个 `upgrade-backups`
（`20261106T051249Z-89fd3963`、`20261106T051506Z-40ae47c1`）都是 temp 之外的本地副本，
**不是仓库产物**，只作本轮追溯。

## 5. 复现命令

```powershell
$t = "D:\copilot-worktrees\liuhao-c9-installed-20261106"
$env:LIUHAO_DATA_DIR = "$t\data"
$env:AUDIT_DB_PATH   = "$t\data\audit_store.db"

& "$t\runtime\python.exe" -m src.upgrade check  --app-root $t
& "$t\runtime\python.exe" -m src.upgrade upgrade --app-root $t
& "$t\runtime\python.exe" -m src.upgrade rollback --app-root $t
& "$t\runtime\python.exe" -m src.upgrade status --app-root $t
```
