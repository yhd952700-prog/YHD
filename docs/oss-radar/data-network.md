# 开源技术雷达 · 第 4 路：数据、存储与网络

> 调研对象：D:\LiuHao-AI-OS（鎏灏 LIUHAO AI OS）
> 调研时间：2026-11-06
> 口径：先读本仓库代码，再看外部仓库；仓库 star / 最近提交 / 许可证来自 GitHub API 与 LICENSE 文件；Windows + Python 3.13 的 wheel 可用性来自 PyPI JSON 实时查询；安全公告来自 OSV API。
> 边界：本文件只做调研，不改代码、不提交、不跑测试、不启动服务；不复制第三方源码进仓库。

---

## 0. 本路核心结论（先读这里）

1. **单机桌面最该补的不是“换数据库”，而是 SQLite 的数据安全闭环。** 现有 `src/backup/backup_manager.py` 把数据源当 UTF-8 文本读取（`read_text(..., errors="replace")`，第 380、396 行），再用 gzip + JSON 打包（第 348–351 行）。SQLite 的 `.db`、未 checkpoint 的 `-wal`、图片/PDF/模型等二进制会被读坏或截断。这是本域 **P0 级数据安全缺陷**，比引入任何新库都优先。
2. **正确的备份原语仓库里已经有了，只是没被备份系统复用。** `src/kernels/audit/durability.py` 已实现 `VACUUM INTO` 事务一致快照（第 57–88 行），并在注释里明确写了“WAL 模式下直接 `cp` 会丢数据”（第 8–11 行）。应把它泛化到全部 SQLite 库，而不是另起炉灶。
3. **JSON 状态存储不是“轻量”，而是“非原子”。** `src/storage/backends.py` 的 `JSONFileBackend` 直接 `open(..., 'w')` + `json.dump`（第 244–245 行），没有临时文件、没有 `fsync`、没有 `os.replace`、没有写锁。进程崩溃或并发写会留下截断 JSON。改成原子写即可，不需要换存储引擎。
4. **向量层的瓶颈不是“没有 ANN”，而是存储格式与扫描方式。** `src/knowledge/vector_store.py` 把 embedding 以 JSON TEXT 存（第 147 行），启动时全量加载进内存 dict（第 130、158–171 行），检索是 Python 逐条 cosine（第 70–74 行）。1536 维 float JSON 约 30KB/条，BLOB float32 约 6KB/条，约 5 倍差距。
5. **FTS5 是零依赖、可立即用的增强，优先级高于 ANN。** 本机 Python 3.13.15 自带 SQLite 3.50.4，编译选项实测包含 `ENABLE_FTS5`、`ENABLE_FTS4`、`ENABLE_RTREE`、`ENABLE_MATH_FUNCTIONS`。中文可用 `tokenize='trigram'`，无需外部分词服务。
6. **日志与可观测存储正在无界增长。** `src/logging_config.py` 用裸 `logging.FileHandler`（第 45、52、59 行），无轮转；`src/observability/store.py` 每次 `_save()` 都全量重写 JSON 并重建哈希链（第 98–113 行），数据量上来后是 O(n²)。
7. **Redis / 消息队列在单机双击场景是伪需求。** `MessageBus` 默认 `mode="memory"`（`src/distribution/msg_bus.py:93–95`）；`docker-compose.prod.yml` 里的 `redis` 与 `postgres` 都在 `profiles: ["infra"]`，并注明“当前代码未使用”（第 163–197 行）。触发条件见 §3.5，未触发前不要引入。
8. **大文件/附件路径目前是假的。** 唯一上传端点在 `src/api/knowledge_api.py`，文档存在进程内 dict，`chunk_count` 是写死的 5，搜索分数是 `0.95 - i*0.05`，完全不读取上传字节；该 legacy router 仍在 `src/gateway/main.py:891` 挂载。附件必须先做 content-addressed blob store + 元数据表。
9. **本路不需要“大而全”的新框架。** 最高价值的 4 件事全部可以用标准库 + 现有模块完成：SQLite 快照备份/校验/恢复、FTS5 混合检索、embedding BLOB 化、日志轮转与 JSON 原子写。

### 0.1 本地实测证据（本次真实执行）

| 项目 | 实测结果 |
|---|---|
| Python | 3.13.15 (MSC v.1944 64-bit) |
| 内置 SQLite | 3.50.4 |
| 关键编译选项 | `ENABLE_FTS3/FTS4/FTS5`、`ENABLE_RTREE`、`ENABLE_MATH_FUNCTIONS`、`DEFAULT_SYNCHRONOUS=2`、`DEFAULT_WAL_AUTOCHECKPOINT=1000`、`THREADSAFE=1` |
| 现有审计库体积 | `data/audit_store.db` 1,679,360 B；`data/audit_store.db-wal` 4,148,872 B（WAL 约为 DB 的 2.47 倍，说明 checkpoint 不及时） |

### 0.2 可行 vs 伪需求（单机桌面前提）

| 增强 | 判定 | 理由 |
|---|---|---|
| SQLite `VACUUM INTO` / `Connection.backup` 快照 | **可行，P0** | 标准库自带，无外部服务，直接解决备份坏库 |
| JSON 原子写（temp + fsync + `os.replace`） | **可行，P0** | 纯标准库，防截断 |
| `PRAGMA integrity_check` + 启动自检 + 隔离损坏文件 | **可行，P0** | 纯 SQL 调用 |
| FTS5 全文检索 + 向量混合排序 | **可行，P0** | 内置扩展，零依赖 |
| embedding 改 BLOB float32 | **可行，P1** | 只改存储类型，接口不变 |
| 日志轮转 / 可观测数据落 SQLite | **可行，P1** | 标准库 `RotatingFileHandler` |
| content-addressed 附件/大文件存储 | **可行，P1** | 文件系统 + 一张元数据表 |
| zstd 压缩备份 | **可行，P2** | BSD-3、有 Windows cp313 wheel |
| sqlite-vec / USearch | **条件可行，P2** | 仅在 chunk 规模/延迟触发后 |
| Redis / Valkey / Kafka / NATS | **伪需求（当前）** | 外部进程、端口、内存、运维；单机单进程无收益 |
| PostgreSQL / Qdrant / MinIO | **伪需求（当前）** | 同上，且迁移/备份成本远大于收益 |
| LiteFS / rqlite / Litestream | **伪需求（当前）** | 面向集群或远端对象存储，不符合“双击即用” |
| DuckDB 列式分析 | **条件伪需求** | 只有 metrics 达到百万级、且需要复杂聚合时才值得 |
| SQLCipher 全库加密 | **暂不需要** | 桌面已有磁盘加密（BitLocker/FileVault）；密钥管理成本高 |

---

## 1. 该域能力现状表（附源码位置）

状态口径：🟢 已实现且能持久化/可验证；🟡 部分实现或有条件；🔴 未实现/假实现/明确风险；⚫ 外部环境阻塞。

| 子能力 | 现有实现与源码位置 | 状态 | 关键事实 |
|---|---|---|---|
| 关系型/嵌入式数据库 | `src/knowledge/db.py`、`alembic/versions/`、`src/storage/backends.py` | 🟢 | 业务主库使用 SQLite；Alembic 管理迁移 |
| 存储后端抽象 | `src/storage/backends.py:21–25、148–245` | 🟡 | 有 `JSONFileBackend` / `InMemoryBackend`；`StorageType.SQLALCHEMY` 枚举存在但 `create_backend` 直接抛 `ValueError`，未实现 |
| JSON 状态写入 | `src/storage/backends.py:244–245` | 🔴 | 全量覆盖写，无临时文件/fsync/原子替换/写锁；崩溃可截断 |
| WAL 与并发 | `src/knowledge/vector_store.py:142`、`src/kernels/execution/_journal.py:100`、`src/kernels/audit/durability.py:4–11` | 🟡 | 各库零散开启 WAL；无统一的 `busy_timeout` / `synchronous` / `wal_checkpoint` 策略；审计库已用 `synchronous=FULL` |
| 备份 | `src/backup/backup_manager.py:348–351、380、396` | 🔴 | gzip 压缩 JSON；所有源文件按 UTF-8 文本读取；SQLite/二进制会被破坏；未调用 `snapshot_audit_db` |
| 恢复 | `src/backup/backup_manager.py:483–573` | 🟡 | 能读回 JSON 并校验 gzip，但无法恢复真实 SQLite/二进制 |
| 损坏检测与隔离 | `src/kernels/audit/recovery.py`、`src/kernels/audit/__init__.py:143` | 🟡 | 审计库有 fail-closed + quarantine；普通 DB 启动时无 `integrity_check` |
| 迁移 | `alembic/versions/` | 🟢 | Alembic 迁移基线存在 |
| 缓存 | `src/performance/cache.py` | 🟡 | 内存/磁盘分层缓存自研；Redis 层可选但未部署 |
| 会话存储 | `src/ai/conversation_store.py` | 🟢 | SQLite 持久化，含 JSON→SQLite 迁移逻辑 |
| 向量检索 | `src/knowledge/vector_store.py:70–74、130、147、158–171` | 🟡 | embedding 为 JSON TEXT；全量加载内存；O(n) cosine；WAL 已开 |
| 知识库文件入库 | `src/api/knowledge_api.py`、`src/api/routes/knowledge.py` | 🔴 | 真正可用的路由只接受文本；legacy 上传写进程内 dict、假 `chunk_count`、假搜索分数，且仍挂载（`src/gateway/main.py:891`） |
| 文件/工作区 | `src/gateway/files.py`、`src/ai/workspace.py` | 🟡 | 有路径策略与工作区写入；无 blob/配额/流式上传 |
| 对象存储/云存储 | 无 | 🔴 | 无 S3/MinIO/云盘适配；单机默认不需要 |
| HTTP API | `src/gateway/main.py`、`src/api/*` | 🟢 | FastAPI + uvicorn；生产关闭 docs/redoc |
| 流式响应 | `src/gateway/chat.py` | 🟢 | SSE（`text/event-stream`）已用于对话 |
| WebSocket | `src/api/events_ws.py:24–89` | 🔴 | 产品 app 未挂载（仅 `src/api/server.py` 挂载，`src/api/server.py` 不是产品入口；见 `docs/product/KERNEL-PRODUCT-CAPABILITY.md:480–484`）；无鉴权；`asyncio.create_task` 无背压 |
| 事件历史 | `src/gateway/events.py` | 🟢 | `/v1/events/history` 提供轮询式历史 |
| 进程内事件总线 | `src/kernels/event/__init__.py` | 🟢 | 进程内 pub/sub，供工作流/执行链使用 |
| 消息队列/流 | `src/distribution/msg_bus.py:93–95`、`src/distribution/bus_backends.py` | 🟡 | 支持 `memory` / `redis`；默认 `memory`；Redis 未部署 |
| 分布式协调 | `src/distribution/coordination.py`、`election.py`、`lock.py` | 🟡 | 选举/成员/锁能力存在，但单机路径未使用 |
| 持久执行/断点 | `src/kernels/execution/_journal.py`、`src/workflow/checkpoint.py:90–188、394–416` | 🟢 | WAL 事件日志 + checkpoint TTL 清理；journal 无压缩/归档 |
| 审计持久化 | `src/kernels/audit/__init__.py`、`durability.py` | 🟢 | SQLite 哈希链、`synchronous=FULL`、`VACUUM INTO` 快照、修复重试 |
| 日志 | `src/logging_config.py:12–59` | 🔴 | `logging.FileHandler` 无轮转；`app.log` / `operations.log` / error 日志会无限增长 |
| 可观测存储 | `src/observability/store.py:44–113` | 🔴 | 每次保存全量重写 JSON，并重建哈希链；随 span/metric 增长 O(n²) |
| 进程/OS 接口 | `scripts/start_liuhao.py` | 🟡 | 有端口探测；无 Windows 命名互斥体单实例保护 |
| 大文件/附件 | 无 | 🔴 | 无流式上传、无 blob 表、无配额/回收 |
| 时序/列式 | 无 | 🔴 | metrics 仍在 JSON/SQLite 行存；无列式分析 |

---

## 2. 候选清单（按“值得引入程度”排序）

### 2.1 仓库元数据（2026-11-06 通过 GitHub API / PyPI JSON / OSV 实测）

| 排名 | 仓库 | Star | 最近提交 | 语言/栈 | 许可证（LICENSE 文件） | 维护状态 | 测试目录 | OSV(PyPI) | 结论 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | [sqlite/sqlite](https://github.com/sqlite/sqlite) | 10,927 | 2026-11-05 | C | Public Domain（SQLite 官方声明） | 官方持续维护 | `test/`（TCL） | 不适用 | **S：直接借用能力，不引入依赖** |
| 2 | [simonw/sqlite-utils](https://github.com/simonw/sqlite-utils) | 2,187 | 2026-10-11 | Python | Apache-2.0 | 活跃（4.2.1） | `tests/` | 无记录 | **A：仅开发期 CLI/参考** |
| 3 | [indygreg/python-zstandard](https://github.com/indygreg/python-zstandard) | 645 | 2026-10-29 | C/Rust + Python | BSD-3-Clause | 活跃（0.25.0，cp313 win wheel 有） | `tests/` | 无记录 | **A：备份压缩可引入** |
| 4 | [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) | 8,239 | 2026-05-18 | C + Python | MIT OR Apache-2.0 | 偏 alpha（GitHub 0.1.10-alpha.4；PyPI 0.1.9） | `tests/`、`benchmarks/` | 1 条历史（已修，≥0.1.3） | **A：达到阈值再引入** |
| 5 | [unum-cloud/USearch](https://github.com/unum-cloud/USearch) | 4,370 | 2026-10-31 | C++ + 多语言绑定 | Apache-2.0 | 活跃（2.26.6，cp313 win wheel 有） | `tests/`、`python/` | 无记录 | **A：大规模 ANN 备选** |
| 6 | [gorakhargosh/watchdog](https://github.com/gorakhargosh/watchdog) | 7,436 | 2026-11-05 | Python + C | Apache-2.0 | 活跃（7.1.0，win wheel 有） | `tests/` | 无记录 | **A：文件夹增量入库** |
| 7 | [coleifer/huey](https://github.com/coleifer/huey) | 6,060 | 2026-11-05 | Python | MIT | 活跃（3.5.4，纯 Python） | `huey/tests/` | 无记录 | **B：只做周期维护任务** |
| 8 | [rogerbinns/apsw](https://github.com/rogerbinns/apsw) | 953 | 2026-11-01 | C + Python | zlib 风格 + `any-OSI` | 活跃（3.54.1.0，cp313 win wheel 有） | `tests/` | 无记录 | **B：只做诊断工具** |
| 9 | [nalgeon/sqlean](https://github.com/nalgeon/sqlean) | 4,389 | 2026-10-31 | C | MIT | 活跃 | `test/` | 不适用 | **B：收益低，暂不引入** |
| 10 | [duckdb/duckdb](https://github.com/duckdb/duckdb) | 42,844 | 2026-11-05 | C++ | MIT | 活跃（2.0.0） | `test/`（大型） | 4 条历史（需按版本核对） | **B：百万级 metrics 再考虑** |
| 11 | [grantjenks/python-diskcache](https://github.com/grantjenks/python-diskcache) | 2,917 | 2024-03-03 | Python | Apache-2.0 | **停更约 2.7 年** | `tests/` | 2 条（含未修复 pickle 反序列化） | **C：只借鉴，不引入** |
| 12 | [tox-dev/filelock](https://github.com/tox-dev/filelock) | 986 | 2026-11-04 | Python | MIT | 活跃（4.2.9） | `tests/` | 22 条（多为 SoftFileLock/符号链接场景） | **C：只借鉴，不引入** |

### 2.2 每个候选的九问（逐条回答）

#### 候选 1：SQLite 官方（sqlite/sqlite）

1. 鎏灏是否已有同类功能：有。`src/storage/backends.py`、`src/knowledge/vector_store.py`、`src/kernels/execution/_journal.py`、`src/kernels/audit/`、`alembic/versions/` 全部基于 SQLite。
2. 当前不足：备份绕开 SQLite 备份 API；JSON 后端非原子；WAL 无统一 checkpoint/回收；普通库启动不做完整性检查。
3. 候选能解决：事务一致快照、WAL 边界处理、损坏检测/恢复、FTS5 全文检索、维护型 PRAGMA。
4. 值得借鉴的源码：`src/backup.c`（在线备份 API）、`src/wal.c` / `src/pager.c`（WAL 与 checkpoint）、`ext/fts5/`（FTS5）、`tool/recover.c`（损坏恢复）、`tool/sqlite3_rsync.c`（增量拷贝）、`src/pragma.c`。
5. 能否独立封装：能。建议新增 `src/storage/sqlite_maintenance.py`，复用 `src/kernels/audit/durability.py` 的既有实现。
6. 会不会破坏现有接口/数据结构：不会。只增加 PRAGMA、快照、校验与恢复路径；表结构不变。
7. 许可证：Public Domain，可自由使用/修改/商用。
8. 安全风险与维护成本：低。风险点是 PRAGMA 选错（如 `synchronous=OFF`）降低持久性；备份期间长读事务会阻塞 checkpoint；无第三方依赖。
9. 集成/回归测试：构造“WAL 未 checkpoint + 进程崩溃”的临时库；用 `VACUUM INTO` 备份后 `integrity_check`；kill -9 后重启恢复；损坏文件隔离；备份→恢复→逐行比对。

#### 候选 2：sqlite-utils

1. 已有：Alembic + 原生 `sqlite3`；无通用 FTS5 封装。
2. 不足：无 BM25 混合检索；维护操作（vacuum/analyze/transform）没有统一入口。
3. 能解决：FTS5 建表/查询、`vacuum` / `analyze`、schema 变更与数据迁移的成熟写法。
4. 借鉴模块：`sqlite_utils/db.py` 中的 `enable_fts`、`Table.search`、`vacuum`、`analyze`；`sqlite_utils/cli.py`；`tests/`。
5. 独立封装：开发期 CLI 或一次性迁移脚本；不建议作为运行时依赖。
6. 破坏性：只借鉴写法不改接口；若新增运行时依赖，纯 Python 包体很小，但会扩大依赖面。
7. 许可证：Apache-2.0，允许商用/修改/再分发（保留 NOTICE）。
8. 安全/维护：风险低；API 面较大，避免把 CLI 默认行为带进生产。
9. 测试：中文 trigram 查询、特殊字符注入、空表、BM25 排序、混合检索 RRF、迁移前后行数/内容一致。

#### 候选 3：python-zstandard

1. 已有：gzip 备份（`src/backup/backup_manager.py:348–351`）。
2. 不足：gzip 压缩比/速度一般；但更根本的问题是备份格式本身把二进制当文本。
3. 能解决：zstd 流式压缩、字典训练（适合重复日志/审计）、更小的备份体积。
4. 借鉴模块：`zstandard/__init__.py`（`ZstdCompressor`、`stream_writer`/`stream_reader`）、`c-ext/`、`tests/`。
5. 独立封装：`src/backup/compression.py` 提供 `compress_stream()` / `decompress_stream()`。
6. 破坏性：备份文件格式变化；必须写版本号并保留 gzip 只读兼容。
7. 许可证：BSD-3-Clause，允许商用/修改。
8. 安全/维护：native wheel；PyPI 0.25.0 有 `cp313-win_amd64`；CPU 可控；维护活跃。
9. 测试：大文件流式 round-trip、损坏帧报错、旧 gzip 备份可读、压缩比/耗时基准、磁盘不足时失败可回滚。

#### 候选 4：sqlite-vec

1. 已有：`src/knowledge/vector_store.py` 自研向量表与 cosine。
2. 不足：JSON TEXT 存向量、全量进内存、O(n) Python 扫描。
3. 能解决：在 SQLite 内做 C 层距离计算，事务一致，无需独立向量服务。
4. 借鉴模块：`sqlite-vec.c`、`sqlite-vec.h.tmpl`、`bindings/python/`、`tests/`、`benchmarks/`。
5. 独立封装：作为 `VectorIndex` 接口的 SQLite 实现，替换 `PersistentVectorStore.search()`。
6. 破坏性：需要加载扩展、增加列/表、迁移旧 JSON 向量；对外 API 可保持不变。
7. 许可证：MIT OR Apache-2.0（二选一，均可商用）。
8. 安全/维护：仍是 alpha 节奏（GitHub 0.1.10-alpha.4、最后提交 2026-05-18、249 open issues）；PyPI 0.1.9 为稳定发布；`py3-none-win_amd64` wheel 存在；历史公告 GHSA-vrcx-gx3g-j3h8 已在 0.1.3 修复。
9. 测试：与暴力 cosine 的 recall@10 对比、10 万向量 p95、扩展缺失时降级、维度不匹配报错、备份/恢复、旧 JSON 数据迁移。

#### 候选 5：USearch

1. 已有：同上。
2. 不足：无 ANN/HNSW；大库内存压力大。
3. 能解决：HNSW、量化、磁盘索引、多线程检索。
4. 借鉴模块：`include/usearch/index.hpp`、`python/`、`sqlite/`、`tests/`。
5. 独立封装：`VectorIndex` 的 ANN 实现；可独立索引文件。
6. 破坏性：索引文件与 SQLite 事务不同步，需要重建/校验；对外 API 可不变。
7. 许可证：Apache-2.0。
8. 安全/维护：native + numpy 依赖；索引文件要纳入备份；参数（M/ef）需调优；项目活跃（2.26.6，cp313 win wheel 有）。
9. 测试：索引/库双写一致性、崩溃后重建、召回率、删除/更新、维度变化、Windows 打包加载。

#### 候选 6：watchdog

1. 已有：`src/ai/workspace.py`、`src/gateway/files.py`；无文件系统监听。
2. 不足：知识库/工作区变更需手动触发；无法增量索引。
3. 能解决：Windows `ReadDirectoryChangesW` 监听，文件落盘后自动入队。
4. 借鉴模块：`src/watchdog/observers/winapi.py`、`read_directory_changes.py`、`tests/`。
5. 独立封装：`src/knowledge/watcher.py`，输出事件到现有 EventBus。
6. 破坏性：只新增事件源；需去抖、忽略规则、大小上限。
7. 许可证：Apache-2.0。
8. 安全/维护：监听线程/句柄泄漏、OneDrive/网络盘事件不可靠、重复事件；成本中等。
9. 测试：临时目录创建/修改/删除、去抖、忽略 `.git/node_modules`、大文件写完再索引、退出清理、异常路径不崩溃。

#### 候选 7：huey

1. 已有：`src/kernels/execution/_journal.py` 持久执行、`src/workflow/checkpoint.py`、`ThreadPoolExecutor`；无通用周期任务队列。
2. 不足：无 cron/周期任务；journal 只服务执行链，不适合维护型任务。
3. 能解决：SQLite 存储的任务队列、优先级、重试、周期任务。
4. 借鉴模块：`huey/storage.py` 的 `SqliteStorage`（WAL、`begin exclusive`、`timeout=5`、`check_same_thread=False`）、`consumer.py`、`api.py`。
5. 独立封装：可独立为 `src/tasks/maintenance_queue.py`；不要替换执行链。
6. 破坏性：若替换 Goal 执行会与 audit/checkpoint 形成双执行源；建议只用于备份/VACUUM/清理等周期任务。
7. 许可证：MIT。
8. 安全/维护：SQLite 写锁串行、consumer 线程生命周期、双队列一致性；成本中等。
9. 测试：重试/幂等、进程重启恢复、与 journal 不交叉、WAL 并发、定时触发、任务超时。

#### 候选 8：APSW

1. 已有：stdlib `sqlite3`。
2. 不足：拿不到细粒度 WAL 状态、authorizer、备份进度、`apsw.shell` 等诊断能力。
3. 能解决：更完整的 SQLite C API 暴露与诊断。
4. 借鉴模块：`src/`（connection/cursor）、`tools/`、`tests/`。
5. 独立封装：只做离线诊断/维护 CLI，不替换运行时连接层。
6. 破坏性：全量替换会改变事务/异常语义；不建议。
7. 许可证：zlib 风格，且可改用任意 OSI 许可证（`SPDX-License-Identifier: any-OSI`）。
8. 安全/维护：native 依赖、双驱动并存；成本中等。
9. 测试：只读诊断、与 stdlib 打开同一 DB 的兼容、WAL 统计、绝不写生产库。

#### 候选 9：sqlean

1. 已有：无 SQLite 扩展。
2. 不足：缺 `crypto`、`uuid`、`regexp`、`text`、`stats`、`vsv` 等 SQL 函数。
3. 能解决：单文件扩展补齐这些函数。
4. 借鉴模块：`src/`（各扩展）、`test/`。
5. 独立封装：可选加载，不默认启用。
6. 破坏性：需要 `enable_load_extension`、平台 DLL、可能改变 SQL 行为。
7. 许可证：MIT。
8. 安全/维护：加载第三方二进制扩大攻击面；打包复杂；当前收益低。
9. 测试：加载失败降级、函数正确性、打包后路径、安全审计。

#### 候选 10：DuckDB

1. 已有：SQLite + JSON metrics。
2. 不足：大规模聚合慢；无列式存储/Parquet。
3. 能解决：分析型查询、窗口函数、Parquet 归档。
4. 借鉴模块：`src/`、`extension/parquet/`、`tools/pythonpkg/`。
5. 独立封装：离线报表/归档分析，独立文件，不进入主事务路径。
6. 破坏性：需要 ETL 导出；双库一致性是新负担。
7. 许可证：MIT。
8. 安全/维护：包体大、冷启动、双库同步；成本中等。
9. 测试：SQLite 导出→DuckDB 查询结果一致、Parquet round-trip、内存上限、归档文件校验。

#### 候选 11：python-diskcache

1. 已有：`src/performance/cache.py` 自研磁盘缓存。
2. 不足：TTL/容量/并发淘汰策略未必完整；无跨进程。
3. 能解决：SQLite 索引 + 文件缓存的成熟实现（TTL、LRU、事务）。
4. 借鉴模块：`diskcache/core.py`（`Cache`/`FanoutCache`）、`tests/`。
5. 独立封装：可替换现有磁盘层。
6. 破坏性：缓存键序列化格式变化；需双读迁移。
7. 许可证：Apache-2.0。
8. 安全/维护：**2024-03 后停更**；OSV 显示 pickle 反序列化公告未修复（最新 5.6.3 仍受影响）。只借鉴，不引入。
9. 测试：若借鉴，禁用 pickle，改用 JSON/msgpack；TTL、容量、并发、损坏缓存清理。

#### 候选 12：filelock

1. 已有：`src/distribution/lock.py` 自研锁。
2. 不足：Windows 锁语义、超时、陈旧锁清理可能不完整。
3. 能解决：跨平台文件锁与超时。
4. 借鉴模块：`src/filelock/_windows.py`、`_unix.py`、`_soft.py`。
5. 独立封装：只借鉴，不新增依赖。
6. 破坏性：替换锁实现会改变租约语义；必须保持现有接口。
7. 许可证：MIT。
8. 安全/维护：OSV 有 22 条公告（多为 `SoftFileLock`/符号链接场景）；单机应用自有目录风险较低，但引入收益不抵依赖成本。
9. 测试：双进程竞争、超时、异常退出释放、只读目录、Windows 路径、陈旧锁清理。

---

## 3. 最值得的深评（5 个）

### 3.1 SQLite 数据安全闭环（最高优先级）

**现状事实**

- 备份系统 `src/backup/backup_manager.py` 对所有源文件执行 `read_text(encoding="utf-8", errors="replace")`（第 380、396 行），再 gzip + JSON 写入（第 348–351 行）。
- 恢复端只校验 gzip 能否解压（第 510–514、572–573 行），不校验 SQLite 完整性。
- 仓库已有正确实现：`src/kernels/audit/durability.py:57–88` 的 `snapshot_audit_db()` 使用 `VACUUM INTO`，注释明确指出 WAL 下直接 `cp` 会丢数据（第 8–11 行）；审计库还使用 `synchronous=FULL`（第 4–6 行）。
- 实测 `data/audit_store.db` 1.6MB，`-wal` 4.1MB，说明 checkpoint 不及时。

**建议做法（全部为标准库/现有模块）**

1. **备份入口统一走 SQLite 快照**：对每个 `.db` 调用 `VACUUM INTO` 到临时目录（目标文件必须不存在），或使用 `sqlite3.Connection.backup()`；禁止把 `.db` 当文本读。
2. **快照后立即校验**：`PRAGMA quick_check`（快）、必要时 `PRAGMA integrity_check`（慢）；记录 `PRAGMA user_version`、`schema_version`、行数、SHA-256。
3. **清单化**：`manifest.json` 记录每个库的源路径、快照路径、大小、SHA-256、schema 版本、应用版本、创建时间；再整体 zstd 压缩。
4. **恢复流程**：停服务 → 校验清单与 SHA-256 → 当前库改名隔离（不删除）→ 复制快照 → 删除残留 `-wal`/`-shm` → `integrity_check` → 启动 → 业务自检。
5. **启动自检**：对核心库先 `quick_check`；失败则隔离损坏文件并提示“从最近快照恢复”，禁止静默重建空库。
6. **损坏兜底**：只读打开 → 可读则 `VACUUM INTO` 抢救；不可读再用 SQLite `tool/recover.c`（`.recover` CLI）作为最后手段。
7. **并发策略**：每个库单写连接；`busy_timeout=5000`；WAL 开启；批量写后 `wal_checkpoint(PASSIVE)`；关闭/备份前 `wal_checkpoint(TRUNCATE)`；`journal_size_limit` 限制 WAL 上限；启动/关闭 `PRAGMA optimize`。
8. **路径护栏**：检测 DB 路径是否位于 OneDrive/Dropbox/网络盘/只读目录，命中则明确警告；桌面端再加 Windows 命名互斥体单实例，避免两个后端写同一库。

**集成/回归测试**

- WAL 未 checkpoint 时创建快照，恢复后逐行比对。
- 备份过程中并发写入，快照必须是一致时间点。
- 人为截断/破坏 DB，启动自检必须隔离并给出可恢复路径。
- 模拟断电（进程 kill）后重启，WAL 能正确回放。
- 备份→恢复→再备份，SHA-256 与行数稳定。
- 空库、只有 schema、超大 BLOB、Unicode 文件名。

### 3.2 FTS5 混合检索（零依赖，优先级高于 ANN）

**现状事实**

- 本机 SQLite 3.50.4 已内置 `ENABLE_FTS5`（实测 `pragma compile_options`）。
- 当前知识库只有向量检索，且向量存储方式低效（见 3.3）。
- `src/api/routes/knowledge.py` 只接受文本，没有真实文件解析/入库。

**建议做法**

1. 新建外部内容表（避免正文重复存两份）：

```sql
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
  chunk_id UNINDEXED,
  doc_id UNINDEXED,
  content,
  tokenize='trigram',
  detail='none'
);
```

2. 中文用 `trigram`（SQLite 3.34+ 内置），英文/代码可用 `unicode61`；按语言建两张 FTS 表或按内容类型路由。
3. 检索时 `bm25(knowledge_fts)` 取 top-N，向量 cosine 取 top-N，用 RRF（`k=60`）融合；同文档多 chunk 做去重/聚合。
4. 过滤条件（tenant、model、dim、doc_type）放在普通表上，FTS 只负责召回。
5. `sqlite-utils` 仅作为开发期参考/CLI：`enable_fts`、`Table.search`、`vacuum`、`analyze`；不要把它变成运行时依赖。

**为什么优先于 ANN**

- 中文关键词/专有名词/ID 查询，FTS5 的精确召回比向量更稳。
- 零新依赖、零外部进程、备份天然覆盖。
- ANN 只解决“向量扫描慢”，不解决“召回质量”。

**测试**

- 中文、英文、中英混合、数字/ID、标点/特殊字符注入。
- 空查询、超长查询、停用词、同义词。
- RRF 融合后 top-10 与人工标注对比。
- FTS 表与正文表行数一致；重建索引后结果一致。
- 备份/恢复后 FTS 可用。

### 3.3 向量规模化：sqlite-vec vs USearch

**现状事实**

- `src/knowledge/vector_store.py:147`：`embedding TEXT NOT NULL`，写入 `json.dumps`。
- 初始化时 `SELECT ... FROM knowledge_vectors` 全量读入 `self._store`（第 158–171 行），检索逐条 Python cosine（第 70–74 行）。
- 1536 维 float JSON 约 30KB/条；float32 BLOB 约 6KB/条；10 万 chunk 时 JSON 方案约 3GB，BLOB 约 600MB，且 Python 全量扫描的 CPU 成本更高。

**分阶段建议**

| 阶段 | 触发条件 | 方案 | 理由 |
|---|---|---|---|
| P1 | 立即 | embedding 改 `BLOB`（float32）+ `dim`/`model` 列；保留 JSON 列做双读迁移 | 零依赖，存储降约 5 倍，接口不变 |
| P2 | chunk > 5 万，或检索 p95 > 200ms，或进程内存 > 500MB | sqlite-vec | 仍在 SQLite 内，事务一致，备份天然覆盖；PyPI 0.1.9 有 Windows wheel |
| P3 | chunk > 50 万，或需要量化/高吞吐 | USearch | HNSW/量化/多线程；独立索引文件，需要与 DB 做一致性校验 |

**sqlite-vec 注意点**

- 它不是 HNSW，核心仍是 C 层扫描（新版仓库有 IVF/DiskANN 代码），但比 Python 逐条 cosine 快得多。
- 扩展必须在每个连接加载；打包时必须带 Windows DLL；扩展缺失要有降级路径。
- GitHub 主分支仍是 alpha（0.1.10-alpha.4，2026-05-18），PyPI 稳定版 0.1.9；锁定版本并做回归。
- 历史公告 GHSA-vrcx-gx3g-j3h8 已在 0.1.3 修复，0.1.9 不受影响。

**USearch 注意点**

- Apache-2.0，2.26.6；PyPI 实测有 `cp313-win_amd64` wheel。
- 需要 numpy；索引文件独立于 SQLite，必须在备份清单中单独列出并校验哈希。
- 更新/删除需要与 DB 双写或定期重建；召回率与参数（M、ef）强相关。

**接口与数据兼容**

- 保持 `VectorStore` 的 `insert/search/delete/count` 签名不变。
- 新表/新列通过 `PRAGMA user_version` + 迁移脚本落地；旧 JSON 列保留一个版本周期。
- 每条向量记录 `model`、`dim`、`normalized`，避免换模型后混算。

**测试**

- 暴力 cosine 作为基准，ANN recall@10 ≥ 0.95。
- 1 万 / 10 万 / 50 万三档的 p50/p95、内存、索引体积。
- 维度不匹配、模型切换、空库、单条、重复 ID。
- 备份/恢复后索引可用；扩展/索引损坏时能重建。

### 3.4 大文件、附件、日志与备份的增长控制

**大文件/附件（当前完全没有）**

建议的最小模型：

```text
data/blobs/<sha256[0:2]>/<sha256>     # 内容寻址，天然去重
SQLite: blobs(id, sha256, size, mime, created_at, ref_count, last_access)
```

流程：流式写入临时文件 → 边写边算 SHA-256 → `fsync` → 原子 rename → 事务插入元数据 → 返回引用。禁止：

- 把二进制读成 UTF-8；
- base64 塞进 JSON/DB；
- 一次性读入内存。

配套：单文件大小上限（可配置，超限返回 413）、剩余磁盘检查、引用计数、启动/每周 mark-sweep 回收、孤儿文件隔离而非直接删除。

**日志**

- 把 `src/logging_config.py` 的 `FileHandler` 换成标准库 `RotatingFileHandler`（如 10MB × 5）或 `TimedRotatingFileHandler`（按天，保留 14/30 天）。
- 错误日志与操作日志分开轮转；避免把异常堆栈重复写进多个文件。

**可观测数据**

- `src/observability/store.py` 的全量 JSON 重写必须改：迁到 SQLite 表（span/metric 行存 + 时间/名称索引），或改为 NDJSON 追加 + 轮转。
- 设定保留窗口（如 30/90 天）与聚合降采样（分钟/小时 rollup），否则 span 数量线性增长会拖垮启动与保存。

**备份**

- 全量备份 = SQLite 快照 + blobs 清单 + manifest；blobs 按 SHA-256 增量复制，不重复打包。
- 保留策略：最近 N 份 + 每日/每周各一份；恢复前必须校验 SHA-256 与 `integrity_check`。
- 压缩从 gzip 迁移到 zstd（BSD-3，cp313 Windows wheel 可用），保留旧 gzip 只读兼容。

**测试**

- 100MB 文件流式上传的内存峰值；中断上传不留半文件。
- 相同内容二次上传去重；引用计数删除；孤儿回收。
- 日志轮转后旧文件可读、总大小受限。
- 备份包含 blobs 且能恢复到新目录；恢复后附件可打开。

### 3.5 何时才值得引入 Redis / 消息队列

**当前事实**

- `MessageBus` 默认 `memory`；`redis` 模式存在但未部署（`src/distribution/msg_bus.py:93–95`）。
- `docker-compose.prod.yml` 的 `redis` / `postgres` 都在 `profiles: ["infra"]`，注释写明“当前代码未使用”（第 163–197 行）。
- 已有持久执行日志 `src/kernels/execution/_journal.py`，能支撑单机 Goal 的崩溃恢复；再引入第二套队列会增加一致性负担。

**触发条件（满足任意 2 条再考虑）**

1. 出现多个**写同一业务域**的进程（API + worker + 插件 runner）且需要跨进程通信。
2. 需要跨机器/多用户部署（团队版、服务器版），不再是单机桌面。
3. 事件持续 > 1,000 条/秒，或未处理积压 > 10 万条。
4. 需要 at-least-once、消费组、重放、死信、延迟队列，而现有 journal 无法表达。
5. 需要多 worker 公平调度、优先级、速率限制。
6. 需要 < 50ms 向 > 100 个客户端 fanout。

**未触发前的替代方案**

- 跨线程/进程：现有 EventBus + SQLite outbox 表 + 轮询/SSE。
- 周期维护任务（备份、VACUUM、清理）：`huey` + `SqliteStorage` 足够，纯 Python、无需服务。
- 事件推送：对话已用 SSE；UI 事件可用“SSE + Last-Event-ID 重放”，比未挂载的 WebSocket 更稳。

**触发后的选型**

- 单机但仍需 KV/Streams：优先 **Valkey**（BSD-3，27,626 star，2026-11-05 活跃），不要默认捆绑 Redis（7.4+ 为 RSALv2/SSPLv1，非 OSI；Redis 8 提供 AGPLv3 选项）。
- 多机消息：Kafka/Redpanda/NATS 只在确有跨机吞吐需求时评估；单机桌面不引入。
- 向量服务：Qdrant/Milvus 只在多用户服务器版评估；单机用 SQLite 内方案。

**测试**

- 重启后未消费消息不丢；重复投递幂等。
- 消费者崩溃/卡死后的租约回收。
- 背压：生产速度 > 消费速度时内存/磁盘有上限。
- 毒消息进入死信，不阻塞队列。
- 与现有 journal 的边界：同一个 Goal 不能同时进两套执行器。

---

## 4. 明确不建议引入的（及原因）

| 方案 | 类型 | 不引入原因 | 备注 |
|---|---|---|---|
| Redis（`redis:7-alpine`） | 外部服务 | 需要独立进程/端口/密码/内存/持久化；桌面双击场景增加失败点；7.4+ 许可证非 OSI | 如未来确需，改用 Valkey（BSD-3） |
| Valkey / KeyDB / Dragonfly | 外部服务 | 同上；单机单进程没有跨进程收益 | 仅在 §3.5 触发后评估 |
| PostgreSQL | 外部服务 | 迁移/运维/备份成本高；SQLite 已满足单机并发 | `docker-compose.prod.yml` 也仅在 infra profile |
| Qdrant / Milvus / Weaviate | 外部服务 | 向量服务需额外进程与索引备份；SQLite 内方案足够 | 多用户服务器版再评估 |
| Kafka / Redpanda / NATS | 消息中间件 | 单机无吞吐/多机需求；运维复杂 | 伪需求 |
| MinIO / S3 兼容服务 | 对象存储服务 | 外部服务 + AGPL-3.0（MinIO）；桌面不需要 | 云备份仅作为可选高级功能 |
| rqlite | 分布式 SQLite | HTTP 服务 + 集群语义；单机无收益 | 伪需求 |
| LiteFS（superfly/litefs） | 集群复制 | 基于 FUSE，Windows 不支持；面向多机复制而非单机备份 | Apache-2.0，但不适用 |
| Litestream | 持续复制 | 需要 S3/Azure/GCS 等远端；默认不可用；Go 二进制 | 仅“高级用户云备份”可选项 |
| sqlite-zstd | 扩展 | **LGPL-3.0**；扩展加载 + 触发器；最后提交 2025-06-30；商用闭源合规成本高 | 不建议；压缩改用 zstd 备份层 |
| Celery / RQ / arq / Taskiq(Redis) | 任务队列 | 依赖 Redis/RabbitMQ 等 broker；桌面不适用 | 单机维护任务用 huey+SQLite |
| DBOS Transact | 持久工作流 | 需额外数据库/运行时；与现有 journal+checkpoint 重复 | 未核验 SQLite 模式，暂不评估 |
| Pyro / ZeroMQ | 本地 IPC | 增加协议层；单进程 FastAPI 不需要 | 若拆分 worker 再评估 |
| DuckDB（当前） | 嵌入式列式 | 包体大、双库一致性；当前 metrics 规模未触发 | 百万级分析需求再引入 |
| SQLCipher | 全库加密 | 密钥管理/性能/迁移成本；桌面磁盘加密已覆盖 | 暂不需要 |
| BLAKE3 替换 SHA-256 | 哈希算法 | 审计链已固定 SHA-256，不能为速度改算法 | 禁止 |

---

## 5. 未验证项清单（不得当作结论使用）

1. **sqlite-vec 在 Windows + Python 3.13 的真实加载未验证**：仅确认 PyPI 有 `sqlite_vec-0.1.9-py3-none-win_amd64.whl`，未在本机 `load_extension` 实测。
2. **USearch 在本项目打包环境中的构建/运行未验证**：仅确认存在 `cp313-win_amd64` wheel，未实测 numpy 依赖与索引持久化。
3. **huey `SqliteStorage` 在本项目 WAL 并发下的行为未验证**：只读了源码（WAL、`begin exclusive`、`timeout=5`），未做双进程压测。
4. **DuckDB 2.0.0 在 Python 3.13 的可用性未验证**：Windows 仅有 `cp311` 与 `cp312-abi3` wheel，abi3 理论上兼容 3.13，未实测。
5. **sqlean 的 Windows DLL 加载未验证**：未在打包环境测试 `enable_load_extension`。
6. **filelock 的 22 条 OSV 公告未逐条映射到具体版本/场景**：只知数量与主题，未逐条确认是否影响 Windows `FileLock`。
7. **Litestream 的 Windows 支持与恢复流程未验证**：未实测其 CLI/服务模式。
8. **sqlite-zstd 的 LGPL-3.0 合规边界未做法务确认**：仅按许可证文本判断“不建议直接引入”。
9. **MinIO 的 AGPL-3.0 未逐文件核验**：按公开许可信息判断，未打开 LICENSE。
10. **所有“测试覆盖”只确认 `tests/` 目录存在**，未计算覆盖率、未运行第三方测试套件。
11. **本文所有性能数字均为估算或本地文件体积观测**，未做 ANN/FTS5 基准测试。
12. **OSV 查询仅覆盖 PyPI 生态的包名**；“无记录”不等于无漏洞，也未覆盖 GitHub Security Advisories 的全部条目。
13. **GitHub star / 提交时间是 2026-11-06 的快照**，后续会变化。
14. **未做任何真实 E2E**（登录→知识库→检索→AI），本文件不构成功能验收。
15. **`data/audit_store.db-wal` 偏大只说明当前实例 checkpoint 不及时**，未证明存在泄漏或损坏。

---

## 6. 建议的最小落地顺序（供总策划参考，不在本文件执行）

| 顺序 | 动作 | 依赖 | 风险 |
|---|---|---|---|
| P0-1 | `BackupManager` 改为二进制安全 + 复用 `snapshot_audit_db` + `integrity_check` + 恢复校验 | 无 | 低；只改备份路径 |
| P0-2 | `JSONFileBackend` 原子写（temp + fsync + `os.replace` + 锁） | 无 | 低 |
| P0-3 | 日志轮转 + `observability/store.py` 停止全量 JSON 重写 | 无 | 低 |
| P1-1 | FTS5（trigram）+ 向量混合检索（RRF） | 无 | 中；需迁移索引 |
| P1-2 | embedding 改 BLOB float32 + `dim`/`model` 列 + 双读迁移 | 无 | 中；需迁移 |
| P1-3 | 真实附件入库：blob store + 元数据表，替换假上传端点 | 无 | 中 |
| P2-1 | zstd 备份压缩 + manifest + 增量 blobs | P0-1 | 低 |
| P2-2 | `_journal.py` 归档/压缩/保留策略 | 无 | 低 |
| P2-3 | 达到阈值后引入 sqlite-vec；更高阈值再评估 USearch | P1-2 | 中高 |
| P3 | huey 仅做周期维护任务；Redis/Valkey 仅在 §3.5 触发后 | P0/P2 | 中 |
