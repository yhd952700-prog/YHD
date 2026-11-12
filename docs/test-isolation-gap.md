# 缺陷说明：只设 `LIUHAO_DATA_DIR` 不足以隔离数据目录

**状态**：已修。业务存储（第一批 17 项）与身份 / 认证 / 审计 / 告警 / 外围存储
（第二批 10 项）都已统一到 `src/common/data_paths`；不能接入的少数项在文末写明理由。
**原始影响**：任何只设置 `LIUHAO_DATA_DIR` 的自动化测试 / 冒烟脚本，都会把会话、
记忆、员工、目标、模型配置与向量数据写进仓库根目录（共享开发目录），污染开发者本机数据。
身份库还会因 `LIUHAO_HUMAN_IDENTITIES_FILE` 缺失而**静默不落盘**，注册接口报
`ACCOUNT_PERSIST_FAILED`（账号树写盘返回 `False`）。

## 根因

每个存储各自读环境变量，而且各认一部分：会话库只认 `CONVERSATION_DB_PATH`，记忆库只认
`MEMORY_DB_PATH`，员工/目标只认 `LIUHAO_WORKSPACE_ROOT`，模型配置只认
`LIUHAO_MODEL_CONFIG_DB`，知识库只认 `LIUHAO_KNOWLEDGE_DB`。**没有任何一处回落到
`LIUHAO_DATA_DIR`**，所以"一套数据目录"被拆成好几处；审计/隔离脚本以为已经干净，
实际仍在写共享开发目录。

## 修法：单一解析入口

新增 [src/common/data_paths.py](../src/common/data_paths.py) 的 `resolve_store_path(filename, legacy_default, env_var=None, workspace=False)`，
优先级固定为：

1. 单存储显式环境变量（`env_var`，如 `CONVERSATION_DB_PATH`）；
2. `LIUHAO_WORKSPACE_ROOT`（只有 `workspace=True` 的工作区类存储才走这一级）；
3. `LIUHAO_DATA_DIR`；
4. 各存储的历史默认值（**不设任何变量时源码模式位置不变**，安装版行为不变）。

解析在**调用时**求值（`_default_db_path()`），避免"导入后才设环境变量"失效。
新增需要落盘的存储必须经过这个入口，不要再自己拼路径、自己读这些变量。

### 已接入的存储（全部经统一入口）

| # | 存储 | 接入点 | 文件名 | 类别 | 单项覆盖变量 |
| --- | --- | --- | --- | --- | --- |
| 1 | 对话历史 | [conversation_store.py](../src/ai/conversation_store.py) | `conversation_store.db` | 数据根 | `CONVERSATION_DB_PATH` |
| 2 | 记忆内核 | [store.py](../src/kernels/memory/store.py) | `memory_store.db` | 数据根 | `MEMORY_DB_PATH` |
| 3 | 个人画像 | [personal_context.py](../src/ai/personal_context.py) | `personal_context.db` | 数据根 | `PERSONAL_CONTEXT_DB_PATH` |
| 4 | 模型配置 DB | [model_config.py](../src/ai/model_config.py) | `model_registry.sqlite3` | 数据根 | `LIUHAO_MODEL_CONFIG_DB` |
| 5 | 模型配置密钥 | [model_config.py](../src/ai/model_config.py) | `model_config.key` | 数据根 | `LIUHAO_MODEL_CONFIG_KEY_FILE` |
| 6 | 知识库向量 | [vector_store.py](../src/knowledge/vector_store.py) | `knowledge_vectors.sqlite3` | 数据根 | `LIUHAO_KNOWLEDGE_DB` |
| 7 | 用量账本 | [account_usage.py](../src/gateway/account_usage.py) | `account_usage.sqlite3` | 工作区 | `LIUHAO_ACCOUNT_USAGE_DB` |
| 8 | 账号记忆 | [account_memory.py](../src/gateway/account_memory.py) | `account_memory.sqlite3` | 工作区 | `LIUHAO_ACCOUNT_MEMORY_DB` |
| 9 | 员工名册 | [employee_store.py](../src/ai/employee_store.py) | `liuhao_employees.json` | 工作区 | —（`LIUHAO_WORKSPACE_ROOT`） |
| 10 | 目标状态 | [ai_management.py](../src/gateway/ai_management.py) | `liuhao_goals.json` | 工作区 | — |
| 11 | planner state | [goal_task_graph.py](../src/ai/goal_task_graph.py) | `planner_state.json` | 工作区 | — |
| 12 | 项目库 | [projects.py](../src/gateway/projects.py) | `liuhao_projects.json` | 工作区 | — |
| 13 | 执行 journal | [ai_management.py](../src/gateway/ai_management.py) | `liuhao_execution.journal` | 工作区 | — |
| 14 | 产物索引 | [index.py](../src/artifacts/index.py) | `liuhao_artifacts.db` | 工作区 | — |
| 15 | 插件索引 | [kernels/plugin/__init__.py](../src/kernels/plugin/__init__.py) | `plugins/registry_index.json` | 工作区 | — |
| 16 | 插件注册表 | [plugins/registry.py](../src/plugins/registry.py) | `plugins/registry.json` | 工作区 | — |
| 17 | 启动器日志 | [start_liuhao.py](../scripts/start_liuhao.py) | `logs/` | 数据根 | `LIUHAO_LOG_DIR` |

第 15、16 项是同一模式的"隐性"成员：旧实现分别用 cwd 相对的 `"./plugins"` 和
`<repo>/data/plugins/`，运行时把未跟踪的 `registry_index.json`
`registry_index.retired.json` `registry.json` 写进源码目录，同样不受
`LIUHAO_DATA_DIR` 约束。

### 第二批：身份 / 认证 / 审计 / 告警 / 外围（本轮补齐）

| # | 存储 | 接入点 | 数据根内位置 | 单项覆盖变量 | 无数据根时 |
| --- | --- | --- | --- | --- | --- |
| 18 | 身份 + 账号树（JSON 后端） | [identity/_persistence.py](../src/kernels/identity/_persistence.py) | `config/human_identities.json` | `LIUHAO_HUMAN_IDENTITIES_FILE` | 保持 `""`：**不落盘**（fail-closed） |
| 19 | 认证口令库 | [gateway/auth.py](../src/gateway/auth.py) | `auth_secrets.json` | `LIUHAO_AUTH_SECRETS_FILE` | 历史默认 `config/auth_secrets.json` |
| 20 | JWT 吊销库 | [security/jwt_handler.py](../src/security/jwt_handler.py) | `liuhao_jwt_revocation.db` | `LIUHAO_JWT_REVOCATION_DB` | 仓库根 `.liuhao_jwt_revocation.db` |
| 21 | 审计证据库 | [kernels/audit/__init__.py](../src/kernels/audit/__init__.py) | `audit_store.db` | `AUDIT_DB_PATH` | 无数据根且无变量：仍拒绝（保护冰封的 HC-01 证据库） |
| 22 | 告警存储 | [observability/alerts/store.py](../src/observability/alerts/store.py) | `observability/alerts.json` | `LIUHAO_ALERTS_STORE_PATH` | 历史默认 `data/alerts.json` |
| 23 | 账号设置 | [gateway/account_settings.py](../src/gateway/account_settings.py) | `account_settings.json` | `LIUHAO_ACCOUNT_SETTINGS_FILE` | `data/account_settings.json` |
| 24 | 模型注册表元数据 | [models/registry.py](../src/models/registry.py) | `model_registry.json` | `LIUHAO_MODEL_REGISTRY_FILE` | `data/model_registry.json` |
| 25 | 联网偏好 | [ai/web_research.py](../src/ai/web_research.py) | `privacy_settings.json` | `LIUHAO_PRIVACY_SETTINGS_FILE` | `data/privacy_settings.json` |
| 26 | 离线情报库 | [intel/store.py](../src/intel/store.py) | `intel/osi_intel.db` | `OSI_DB_PATH` | `data/intel/osi_intel.db` |
| 27 | ORM 主库 | [integrations/orm_models.py](../src/integrations/orm_models.py) | `liuhao_ai_os.db` | `DATABASE_URL` | `sqlite:///./liuhao_ai_os.db` |

两个刻意保留的语义：

- **第 18 项**只有显式设置 `LIUHAO_DATA_DIR` 才给出路径。没有数据根时仍返回 `""`，
  即"未配置就不持久化"，绝不悄悄写进 `%LOCALAPPDATA%` 或仓库根。这正是此前
  `TestCrossOwnerEmployees` 变红的根因修复：conftest 清空了单项变量但设了数据根，
  账号树现在落进数据根，不再返回 `False`。
- **第 21 项**的 `AUDIT_DB_PATH` 是冻结证据库的保护开关。只有"显式变量"或"数据根"
  之一存在时才允许打开新库，两者都没有时维持原有拒绝行为。
- 第 27 项不能直接套 `resolve_store_path`：`DATABASE_URL` 是 SQLAlchemy URL 而不是
  文件路径，因此单独实现 `default_database_url()`，并补 `_ensure_sqlite_parent()`
  （SQLite 不会自动建父目录）。

## 实测：改前 / 改后「仓库根目录污染」

探针（仓库外临时脚本）流程：只设 `LIUHAO_DATA_DIR`（并清掉 `LIUHAO_WORKSPACE_ROOT`
及各单项覆盖变量，避免掩盖缺陷）→ `TestClient(get_app())` 真实 lifespan →
注册根账号 → 登录 → 建 mock 模型 → `POST /v1/chat` 发一条消息 → `POST /v1/employees`
建一个员工 → `POST /v1/goals` 建一个目标 → 对比启动前后代码根顶层快照。

**改前**（`git archive HEAD` 快照根 `before_src`，数据根 `before_data`）：

```
statuses: register 200, login 200, create_model 200, chat 200, hire_employee 201, create_goal 200
new_top_level_entries:
  .liuhao_artifacts.db(+shm/wal)   .liuhao_employees.json(+lock)
  .liuhao_execution.journal(+shm/wal)   .liuhao_goals.json(+lock)
  conversation_store.db(+shm/wal)   memory_store.db(+shm/wal)
  personal_context.db(+shm/wal)   data   plugins   __pycache__
new_files_under_data: data/account_usage.sqlite3, data/knowledge_vectors.sqlite3(+shm/wal),
  data/model_registry.sqlite3(+shm/wal)
data_dir_top_level: alerts.json, audit_store.db(+shm/wal), auth_secrets.json(+lock),
  identities.json(+lock), jwt_revocation.db, logs
```

→ 数据根只拿到认证与日志，**所有业务库写回代码根**（审计现场实证一致）。

**改后**（真实仓库根 `after4`，全新 `LIUHAO_DATA_DIR`）：

```
statuses: register 200, login 200, create_model 200, chat 200, hire_employee 201, create_goal 200
new_top_level_entries: []
watched_runtime_files_at_root: []
new_files_under_data: []
changed_watched_stamps: []
data_dir_top_level: account_usage.sqlite3, alerts.json, audit_store.db(+shm/wal),
  auth_secrets.json(+lock), conversation_store.db(+shm/wal), identities.json(+lock),
  jwt_revocation.db, knowledge_vectors.sqlite3(+shm/wal), liuhao_artifacts.db(+shm/wal),
  liuhao_employees.json(+lock), liuhao_execution.journal(+shm/wal), liuhao_goals.json(+lock),
  logs, memory_store.db(+shm/wal), model_registry.sqlite3(+shm/wal), personal_context.db(+shm/wal),
  plugins
```

**改后**（干净副本根 `after_src`，初始无 `data/`、无 `plugins/`，验证"全新目录"也不新建）：

```
new_top_level_entries: []      # 修复前该副本会新建 data/ 与 plugins/
data_dir_top_level: 同上 + plugins、run
```

两点补充：

- `after_src` 副本创建后，另一条并行线的 `ai_management.py` 才加入 `_replace_goal_file`，
  该副本上 `create_goal` 返回 507（代码陈旧，非本次改动引起）；其中 `liuhao_goals.json`
  的落盘路径已是数据目录。真实仓库根（`after4`）上 `create_goal` 返回 200。

## 第二轮实测：只设数据根时的身份 / 审计 / 告警落点

同一探针，清掉**全部**单项覆盖变量与 `LIUHAO_WORKSPACE_ROOT`，只设 `LIUHAO_DATA_DIR`：

```
statuses: register 200, login 200, create_model 200, chat 200, hire_employee 201, create_goal 200
new_top_level_entries: []
rewritten_watched_files: []
new_plugin_jsons: []
resolved:
  identities   <data>\config\human_identities.json   exists
  audit        <data>\audit_store.db                  exists
  alerts       <data>\observability\alerts.json       exists
  auth_secrets <data>\auth_secrets.json               exists
data_dir_files: account_memory.sqlite3, account_usage.sqlite3, audit_store.db(+shm/wal),
  auth_secrets.json(+lock), config/human_identities.json, conversation_store.db(+shm/wal),
  knowledge_vectors.sqlite3(+shm/wal), liuhao_artifacts.db(+shm/wal),
  liuhao_employees.json(+lock), liuhao_execution.journal(+shm/wal),
  liuhao_goals.json(+lock), liuhao_jwt_revocation.db, memory_store.db(+shm/wal),
  model_registry.sqlite3(+shm/wal), observability/alerts.json,
  personal_context.db(+shm/wal), run/local_scheduler.lock
```

身份隔离的**红转绿**证据（不是放宽断言）：

- 修复前：`tests/test_employee_entities.py::test_cross_owner_employees_are_invisible_and_404`
  在 conftest 清空 `LIUHAO_HUMAN_IDENTITIES_FILE` 后，账号树写盘返回 `False` →
  `ACCOUNT_PERSIST_FAILED`。
- 修复后：同文件全绿；并新增
  `tests/kernels/identity/test_human_identity_persistence.py::TestBackendResolution::test_a_data_root_alone_places_the_json_store_inside_it`
  钉住"只设数据根 → 身份文件在数据根内"这一契约；"完全未配置"的两条旧断言改为
  **显式删除 `LIUHAO_DATA_DIR`** 后再断言 `location == ""`，fail-closed 语义不变。

写盘失败的用户可见文案（不是裸错误码）：

> 账号创建失败：账号数据无法写入 `<location>`。请确认该目录存在、当前用户有写权限
> （或把 `LIUHAO_DATA_DIR` 指向一个可写目录）后重试。本次注册已回滚，未发放恢复码。

## 第三批：审计高水位 / 主密钥 / 队列（含一次性迁移）

这一批不是"把默认路径换个字符串"就完事：主密钥和任务是**老用户已经写下的数据**，
换路径时若新位置没有就以空库/新密钥启动，等于把旧数据永久锁死。所以统一口径是
「先找旧位置 → 复制 → 校验 → 原子落位 → 旧文件保留」，失败就退回旧位置并明确报错。

| 存储 | 新落点 | 旧落点 | 迁移校验 |
| --- | --- | --- | --- |
| 审计内核 HWM | `<data>/audit_kernel_hwm.json` | `~/.liuhao/audit_kernel_hwm.json` | 计数器文件，直接读新位置（无迁移需求） |
| 加密审计 HWM | `<data>/audit_hwm.json` | `~/.liuhao/audit_hwm.json` | 同上 |
| 主密钥 | `<data>/master.key` | `~/.liuhao/master.key` | 逐字节校验；**任何情况下都不生成新密钥** |
| 任务队列 | `<data>/queue/tasks.db` | `~/.liuhao/queue/tasks.db`（或 `<workspace>/queue`） | SQLite backup + 表集合 + 逐表行数 |
| 幂等 journal | `<data>/queue/tasks.journal` | 同上 | SQLite backup + 表集合 + 逐表行数（WAL 下逐字节比对会误判） |
| 提醒库 | `<data>/queue/reminders.db` | 同上 | SQLite backup + 表集合 + 逐表行数 |
| 提醒出站队列 | `<data>/queue/reminders_outbox.jsonl` | 同上 | 逐字节内容比对 |

主密钥读取顺序固定为 `LIUHAO_MASTER_KEY_FILE` → 数据根 → 旧 `~/.liuhao/master.key`；
迁移日志写「已迁移到 <新路径>；旧文件 <旧路径> 已保留未删」，迁移失败则
`WARNING` + 继续用旧密钥（`src/security/encryption.py` 的 `resolve_master_key_file()`）。

队列解析与迁移集中在 [src/tasks/_queue_paths.py](../src/tasks/_queue_paths.py)
一个入口，五处（broker / journal / reminders / reminders_cli / reminder_notify）共用：
`LIUHAO_DATA_DIR` → `LIUHAO_WORKSPACE_ROOT` → `~/.liuhao/queue`，前两者都设时以数据根为准。
迁移失败记 `ERROR` 并**继续使用旧库**，绝不以空库启动，也不留半截临时文件。

### 并发安全（跨进程）

迁移是「检查 → 复制 → 校验 → 落位」，不加锁就是 check-then-act：两个进程同时启动会
各迁一次、共用同一个临时名互相删文件，甚至一边落位失败退回旧库，形成
「一个进程写新库、一个写旧库」的 split-brain。因此整个临界区由
`src/common/atomic_write.exclusive_file_lock` 串行化：

* 锁文件 `<target>.migrate.lock`，与目标路径绑定、跨进程可见（Windows `msvcrt` /
  POSIX `fcntl`）；
* 进锁后**重新检查** `target.exists()`（等锁期间别人可能已经迁完）；
* 进锁后先做一次**有界**的孤儿 staging 清理（见下条），再开始本次迁移；
* 临时文件名带 `pid + 随机后缀`（`<target>.<pid>.<hex>.migrating` 及
  `-wal/-shm/-journal` sidecar），`finally` 只清理自己的临时文件与 sidecar；
* 落位前对临时文件 `fsync`，rename 后再对目录做一次 best-effort `fsync`
  （Windows 不支持以目录句柄方式打开，`_fsync_directory` 会静默跳过——这一点不假装做到）；
* 锁由操作系统在进程退出/**被强杀**时自动释放，无需人工删锁文件；只有持锁进程一直
  存活并超过 `MIGRATION_LOCK_TIMEOUT_SECONDS`（30s）才抛 `TimeoutError`。超时是**明确
  报错并向上抛出**，绝不退回旧位置——退回旧位置正是「一个进程写新库、一个写旧库」
  两套并存的来源。

**孤儿 staging 的清理条件**（`src/tasks/_queue_paths.py` 的 `_cleanup_orphan_staging`）：
只有三个条件**同时**满足才删 —— ① 文件名完全匹配本模块的
`<target>.<pid>.<hex>.migrating` 模式（含 sidecar）；② 该 pid 已不存在；③ mtime
超过 `ORPHAN_STAGING_MIN_AGE_SECONDS`（默认 3600s，可用
`LIUHAO_QUEUE_STAGING_MIN_AGE_SECONDS` 覆盖）。pid 仍存活、刚创建的、目标库本身及其
`-wal/-shm` 一律不碰；pid 存活探测不确定时**按存活处理**（宁可不删）。单次最多扫描
`_ORPHAN_SCAN_LIMIT = 64` 个匹配项，异常目录不会拖死启动。

**不写成「掉电安全」**：上述保证只覆盖**进程级**故障（崩溃、被强杀）。整机掉电时，
rename 这一步是否已持久化在 Windows 上**没有保证**——`_fsync_directory` 在 Windows
静默跳过，可能回到 rename 前状态。数据不丢、不损坏，最坏多迁一次。该事项已登记在
[known-gaps.md](known-gaps.md) §5「队列迁移的 rename 掉电持久性在 Windows 上只有
best-effort」（总表第 4 条），关闭条件与实测方法以那一节为准。

`local_scheduler.db` 没有另写一份逻辑：`default_scheduler_db()` 已复用同一个
`resolve_queue_file`，因此同样受这把锁保护（有用例用 spy 断言锁文件路径，
不是靠推断）。

实测（`D:\cache\temp\queue_concurrency_probe.py`）：两个真实子进程（pid 28676 /
12580）同时解析同一旧库，两个返回路径完全相同，**只有一个**报告「已迁移」；
目标库 `pending 1 / running 1` 与旧库一致，无 `*.migrating*` 残留。持锁子进程
`os._exit(0)` 后，父进程重新解析 **0.031s** 就拿到锁并完成迁移（锁由系统释放）。

反例覆盖在 [tests/tasks/test_queue_data_root_migration.py](../tests/tasks/test_queue_data_root_migration.py)：
仅旧路径 / 仅新路径 / 两边都有（以新为准且旧文件不删）/ 两边都没有（正常初始化）/
迁移失败退回旧库 / 显式变量最高优先；另有一条**子进程**用例断言
`reminders_cli` 与 `reminder_notify` 这两个独立进程入口解析出的路径与运行时一致。
并发侧另有用例：两个真实子进程并发只迁一次、持锁被 `os._exit` 强杀后能自动恢复、
`default_scheduler_db()` 的迁移锁文件路径被实际断言（不是假设）、持锁超时明确报错
且不退回旧库、`test_orphan_staging_cleanup_only_removes_dead_and_old` 断言「死 pid +
旧 mtime」的孤儿（含 sidecar）会被清理而活 pid 的临时文件绝不删。

实测（`D:\cache\temp\queue_migration_probe.py`，只设数据根）：旧位置 `tasks.db`
含 pending 1 + running 1 → 解析时迁移 → 真实 taskiq `Receiver` 在数据根那份库上
执行了 pending 任务（marker 收到的正是该 task_id），`pending 0 / running 1`；
旧位置两个状态的记录仍在，`tasks.journal` 迁移前后 sha256 完全相同、
幂等键仍被认领，`reminders.db` 里 `rem-evidence` 仍在，出站 JSONL 内容逐字节一致，
数据根下无 `*.migrating` 残留。

## 不纳入统一入口（附理由，不假装已修）

1. **`src/gateway/main.py` 的审计 checkpointer 开关**：仍以 `AUDIT_DB_PATH` 是否设置
   为唯一判据，只设数据根时会打印 `AUDIT CHECKPOINTER SKIPPED -- AUDIT_DB_PATH is not set`。
   该文件在本任务的不可动边界内（归其他线）。**审计存储本身**已跟随数据根（见第 21 项），
   缺的只是这处"是否启用 checkpointer"的判定；持该线的同事可把判据扩为
   `AUDIT_DB_PATH or LIUHAO_DATA_DIR`。
2. 插件**源码**目录 [plugins/manager.py](../src/plugins/manager.py) 的
   `plugins_dir.mkdir(...)`：`plugins.path` 指向源码目录（默认 `src/plugins`），不是运行时
   状态；仅在目录缺失时创建，不属于数据落盘问题。
3. `scripts/` 之外的一次性脚本 / 测试产物（`.pytest_*.log`、`outputs/` 等）不在运行时
   存储范围内。`verify_metrics.db` 不属于此类：`MetricsApplication` 会随导入/启动建库，
   已改为解析到数据根（旧 `<cwd>/verify_metrics.db` 存在时自动迁移）。
4. **`~/.liuhao/queue/local_scheduler.db`**：排程器库由
   [src/tasks/local_scheduler.py](../src/tasks/local_scheduler.py) 定义与写入，该文件在本批
   不可动边界内（归其他线）。因此 `reminders_cli` **只跟随**
   `local_scheduler.default_scheduler_db()`，不另搬一份到数据根——否则排程器进程继续写旧位置、
   CLI 却读新位置，正是"两套并存"。等排程器那条线接入数据根后，CLI 会自动跟随，无需再改。
5. [src/security/secret_store.py](../src/security/secret_store.py) 的
   `require_secret_backend()` / `get_secret_store()`：全仓**没有运行时调用方**（网关凭据走
   `src.gateway.auth.resolve_secret_store`，已跟随数据根）。落点已改为
   `resolve_secret_store_dir()`：`LIUHAO_SECRET_STORE_DIR` > 数据根 > 平台默认，
   旧 `~/.liuhao/secrets` 作为**迁移来源**整体复制（逐文件 SHA256 + 文件集合校验 +
   带 pid/随机后缀的 staging + 原子落位 + 旧目录保留），失败退回旧目录、不建空库。
   因此"以后接上就悄悄写 C 盘"这条隐患已关闭。
6. `automation_tickets.db` / `workflow_checkpoints.db` / `dual_signature_evidence.db`
   统一走 [src/tasks/_queue_paths.py](../src/tasks/_queue_paths.py) 的
   `resolve_sqlite_store()`：数据根优先，旧 `<cwd>/data`、仓库 `data/`、仓库根位置存在时
   按队列同款流程迁移（SQLite backup API + 表集合/行数校验 + 原子落位 + 旧库保留），
   迁移失败继续用旧库。
7. `bootstrap_log` 已改为「设了 `LIUHAO_DATA_DIR` 就先写 `<data>/logs/bootstrap.log`」，
   但保留 `%LOCALAPPDATA%` / `%TEMP%` 兜底——它的用途正是"数据目录写不进去时仍留一条记录"，
   若把它绑死在数据根就失去兜底意义。显式 `LIUHAO_BOOTSTRAP_LOG` 仍最高优先。

## 测试基座护栏（已落地）

[tests/test_data_root_isolation.py](../tests/test_data_root_isolation.py) 的常驻用例，
不再依赖人记：

1. `test_stores_follow_the_data_root`：清掉所有单项覆盖变量，只设 `LIUHAO_DATA_DIR`，
   断言身份库、审计库、告警库、口令库、JWT 吊销库、模型注册表、联网偏好、情报库、
   ORM 默认 URL，以及自动化票据库、指标库、工作流检查点库、双签证据库、密钥库目录
   全部解析到数据根内。
   另有 `test_new_sqlite_stores_migrate_from_legacy` 钉住"旧库复制到数据根、行数不变、
   旧库保留、无 staging 残留"。
2. `test_full_flow_leaves_the_code_root_untouched`：对代码根顶层条目、被监视的运行时
   文件（size + mtime_ns）、`plugins/*.json` 拍快照 → 真实 lifespan 跑一轮
   「建模 / 聊天 / 招员工 / 建目标」→ 断言
   `new_top_level_entries == []`、`rewritten_watched_files == []`、`new_plugin_jsons == []`。

所以以后任何新存储若又按 cwd 相对路径落盘，会直接让这条用例变红，而不是悄悄污染共享目录。
