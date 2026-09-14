# Phase 7c — PostgreSQL 记忆后端设计（基于 SQLAlchemy，不引入受限许可依赖）

> 状态：**已实现并验证**（`tests/kernels/memory/test_postgres_backend.py`：40 passed + 1 skipped）。
> 范围是 Phase 7（工程生产化）第三片：把 Phase 4 留下的
> `get_memory_backend("postgres")` **fail-closed 占位**落成真实实现。
> 纪律同前：**先实测再判断**、**附加式改动**、**默认零行为变化**、**禁止静默降级**。

## 1. 实测发现：三个决定设计走向的硬约束

| # | 实测事实 | 证据 | 对设计的影响 |
|---|----------|------|--------------|
| 1 | **本机无 PostgreSQL 服务，且无 Docker** | `127.0.0.1:5432` / `localhost:5432` 均 `TimeoutError`；`docker ps` → daemon 未运行 | **无法做真实 PG 端到端验证** ⇒ 必须把"未验证的部分"显式标出并做成运行时可见，而不是假装测过 |
| 2 | **`psycopg2-binary` 是幻影依赖，且许可受限** | 实测元数据 `license='LGPL with exceptions'`、classifier `GNU Library or Lesser General Public License (LGPL)`；`grep -niE "psycopg\|postgres" pyproject.toml requirements.txt` → **两份清单里都没有它**；而 `oss-registry.yaml` 的 `restricted_licenses` **含 `LGPL-3.0`** | **不能用 psycopg2**：它既未登记（登记册硬规则"新增任何第三方依赖都必须先登记"），又落在受限许可里（需书面 `acknowledged`）。这是**留给 boss 的决策**，不是我该单方面做的 |
| 3 | **`sqlalchemy` 是 MIT 且已登记** | `oss-registry.yaml:92` `name: sqlalchemy / license: MIT / used: "yes" / risk: low`；实测元数据 `license='MIT'`；`requirements.txt:648 sqlalchemy==2.0.52` 为直接 pin；项目**已在** `src/integrations/orm_models.py` 使用它 | **改用 SQLAlchemy** —— "能真做就真做，优先用**已声明**的依赖，不要新增" |

**结论**：Phase 7c 的选型不是偏好问题，是被许可与登记规则**逼出来的唯一合理解**。用 psycopg2
会同时踩中「未登记」+「受限许可」两条护栏；用 SQLAlchemy 则**零新增依赖**。

### 1.1 顺带发现的、**不属于本轮**但必须上报的问题

- `sqlalchemy` **未写在 `pyproject.toml` 的 `dependencies` 里**（只在 `requirements.txt` 作为
  解析产物出现）。而 `src/integrations/orm_models.py` 在**模块顶层** import 它 ⇒ CI 若只执行
  `pip install -e .`，等于依赖传递链"碰巧"带上它。这与本仓已修过三次的问题同型
  （`mcp` / `qdrant-client` / `aiosqlite` 都因此被显式声明）。**本轮不动它**（超出 Phase 7c 范围，
  且属于独立的依赖声明链任务），仅上报。

## 2. 交付物

### 2.1 扩展 `src/kernels/memory/backends.py`

| 符号 | 职责 |
|------|------|
| `BackendUnavailableError(RuntimeError)` | 后端不可用时的**诚实失败**（沿用 Phase 7b 的 `BusUnavailableError` 同一哲学） |
| `SqlAlchemyBackend` | 基于 SQLAlchemy Core 的真实后端。`postgresql://` 是**目标**；同一套 CRUD 代码路径可用 `sqlite://` 驱动以便**离线验证** |
| `postgres_available(url)` | 能力探针：`(可用, 原因)`，分别报告 ① SQLAlchemy 是否可导入 ② 该 URL 的 DBAPI 驱动是否可导入 ③ 连接是否可达。**不抛异常** |
| `get_memory_backend` 的 postgres 分支 | 由"无条件抛 `NotImplementedError`"改为**返回真后端**；URL 取 `LIUHAO_POSTGRES_URL` > `DATABASE_URL`；缺 URL 时抛 `BackendUnavailableError`（仍 fail-closed，但原因可操作） |

**表结构**：与既有 `MemoryStore` 的 `memory_entries` **逐列一致**（13 列：
`key_hash` PK / `entry_id` / `key` / `value` / `tier` / `scope` / `created_at` / `expires_at` /
`tags` / `correlation_id` / `access_count` / `last_accessed` / `provenance`）——
不另造 schema，否则切换后端会静默丢字段。

**upsert 语义**：`persist` 必须是 `ON CONFLICT(key_hash) DO UPDATE`（与 SQLite 侧逐字段一致）。
不同方言的 UPSERT 语法不同，故按 `engine.dialect.name` **显式分派**
（`postgresql` / `sqlite`），未支持的方言**抛错**而不是退化成可能静默覆盖的 `INSERT`。

**driver 解析**：`sqlalchemy.create_engine("postgresql://…")` 会在建引擎时导入 DBAPI 驱动；
驱动缺失表现为 `ModuleNotFoundError`。⇒ 统一捕获并转成 `BackendUnavailableError`，
错误信息里给出**可操作建议**（装哪个包），不让它变成裸 ImportError。

### 2.2 验证策略（关键：诚实区分「已验证」与「未验证」）

| 层次 | 手段 | 强度 |
|------|------|------|
| **共享 CRUD 逻辑**（建表/upsert/删/清/读/计数/并发锁） | 用**同一个 `SqlAlchemyBackend`** 跑 `sqlite://` 内存库，做真实 I/O 往返 | **真验证**（真数据库、真 SQL、真 I/O） |
| **PostgreSQL 方言 SQL**（DDL + upsert 语句） | 用 `sqlalchemy.dialects.postgresql.dialect()` **编译**建表语句与 upsert，断言生成的是 `ON CONFLICT (key_hash) DO UPDATE`、列类型正确 | **真验证**（不需要服务器即可证明 PG 方言 SQL 正确生成） |
| **连接失败诚实性** | 指向必然连不上的 PG DSN，断言抛 `BackendUnavailableError` 且原因可操作 | **真验证** |
| **真 PG 端到端** | `skipif` 探测 5432 可达才跑 | **本机未验证** —— 明确 skip 并给出原因 |
| **`MemoryKernel` 接上 PG 后的整体行为** | 复用 Phase 4 的 kernel 测试 | 本机未验证（无 PG） |

⚠️ 诚实边界（必须写进结论，不许含糊）：**"用 SQLite 跑通同一代码路径" ≠ "PostgreSQL 上跑通"**。
两者共享全部 Python 逻辑与 SQLAlchemy 抽象，但 PG 特有的类型/并发/权限/连接池行为**未经真机验证**。
本设计**不**把前者包装成后者。

## 3. 默认零行为变化 / 风险 / 回滚

- `MEMORY_BACKEND` 未设 ⇒ 仍是 `sqlite`，**逐字节不变**（既有 Phase 4 测试即护栏）。
- `sqlite` / `memory` 两个分支不动。
- 唯一行为改变：`MEMORY_BACKEND=postgres` 从"恒抛 NotImplementedError"变成"真连"。
  在无 PG 的环境里它**仍会抛错**，但错误从"计划中"变成"连不上，原因是 X，请做 Y"。
- 回滚 = `git checkout -- src/kernels/memory/backends.py` + 删 2 个新文件。

## 4. 交付给 boss 的决策项（不在本轮自动执行）

1. **生产是否启用 PostgreSQL 记忆后端？** 若要，需要一并解决 **DBAPI 驱动**的选型与登记：
   - 选项 A：`psycopg2-binary` —— LGPL（受限许可，需 `acknowledged` 书面理由 + 登记），且上游
     明确不建议生产使用 `-binary` 变体；
   - 选项 B：`psycopg[binary]`（v3）—— 同为 LGPL，仍需同样处置；
   - 选项 C：`pg8000` —— **BSD-3-Clause**（在 `allow_licenses` 内，纯 Python 无 C 构建），
     与 SQLAlchemy 原生兼容，**新增依赖的合规成本最低**。
   本条我不替 boss 决定，因为它是许可与供应链决策，不是技术细节。
2. `sqlalchemy` 是否补进 `pyproject.toml` 的 `dependencies`（见 §1.1）。
