# 数据库优化

PostgreSQL 是 LiuHao AI OS 的主存储，承载智能体状态、事件流、记忆元数据与审计记录。本文档覆盖索引设计、查询优化、连接管理、分区策略、pgvector 调优与在线迁移。原则：**先用 pg_stat_statements 找到最贵的 10 条查询，优化它们就解决了 90% 的问题。**

---

## 目录

- [诊断先行](#诊断先行)
- [索引设计](#索引设计)
- [查询优化实战](#查询优化实战)
- [连接池管理](#连接池管理)
- [分区与归档](#分区与归档)
- [pgvector 向量检索调优](#pgvector-向量检索调优)
- [在线迁移（Expand-Contract）](#在线迁移expand-contract)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 诊断先行

### 开启 pg_stat_statements

```sql
-- postgresql.conf
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.track = all

-- 找出最贵的查询（按总耗时排序）
SELECT calls,
       round(total_exec_time::numeric, 1)        AS total_ms,
       round(mean_exec_time::numeric, 2)         AS mean_ms,
       rows,
       left(query, 120)                          AS query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 10;
```

### 解读 EXPLAIN

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT * FROM events
WHERE aggregate_id = 'agent_42' AND version > 100
ORDER BY version LIMIT 50;
```

关注三个信号：

| 信号 | 含义 | 对策 |
|------|------|------|
| `Seq Scan` 大表 | 缺索引 | 加索引 |
| `Rows Removed by Filter: N`（N 巨大） | 索引选择性差 | 复合索引/部分索引 |
| `Buffers: shared read=N`（远大于 hit） | 数据不在内存 | 加内存或减扫描量 |

---

## 索引设计

### 事件表的索引组合

```sql
-- 事件溯源核心表（见 event-sourcing.md）
CREATE TABLE events (
    aggregate_id  TEXT        NOT NULL,
    version       BIGINT      NOT NULL,
    event_id      TEXT        NOT NULL,
    event_type    TEXT        NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL,
    payload       JSONB       NOT NULL,
    PRIMARY KEY (aggregate_id, version)          -- 覆盖最高频查询：按聚合重放
);

-- 审计查询：按类型+时间范围
CREATE INDEX idx_events_type_time ON events (event_type, occurred_at DESC);

-- 关联链追踪
CREATE INDEX idx_events_correlation ON events ((payload->>'correlation_id'))
WHERE payload ? 'correlation_id';                -- 部分索引：只索引有的行

-- JSONB 内容检索（谨慎使用，写放大明显）
CREATE INDEX idx_events_payload_gin ON events USING GIN (payload jsonb_path_ops);
```

### 索引原则

1. **最左前缀匹配**：`(a, b, c)` 索引支持 `WHERE a=?`、`WHERE a=? AND b=?`，不支持单独 `WHERE b=?`
2. **等值在前，范围在后**：`(tenant_id, occurred_at)` ✅；`(occurred_at, tenant_id)` ❌（范围列后的列用不上）
3. **部分索引省空间**：`WHERE status = 'active'` 只占表 5% 时，部分索引小 20 倍
4. **覆盖索引免回表**：`CREATE INDEX ... ON t (a, b) INCLUDE (c, d)`，查询只扫索引
5. **写多的表索引要少**：每个索引拖慢 INSERT/UPDATE，事件表索引 ≤ 4 个

### 定期体检

```sql
-- 无用索引（写入负担）
SELECT schemaname, relname, indexrelname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan < 50 AND indexrelname NOT LIKE '%pkey%'
ORDER BY pg_relation_size(indexrelid) DESC;

-- 索引膨胀
SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10;
-- 膨胀严重：REINDEX INDEX CONCURRENTLY idx_name;
```

---

## 查询优化实战

### N+1 问题

```python
# ❌ 经典 N+1：每个智能体查一次任务数
agents = await db.fetch_all("SELECT * FROM agents WHERE tenant = $1", tenant)
for a in agents:
    a.task_count = await db.fetch_val(
        "SELECT count(*) FROM tasks WHERE agent_id = $1", a.id)

# ✅ 一次 JOIN/GROUP BY
rows = await db.fetch_all("""
    SELECT a.*, COALESCE(t.cnt, 0) AS task_count
    FROM agents a
    LEFT JOIN LATERAL (
        SELECT count(*) AS cnt FROM tasks WHERE agent_id = a.id
    ) t ON true
    WHERE a.tenant = $1
""", tenant)
```

### 分页：keyset 优于 OFFSET

```sql
-- ❌ OFFSET 深分页：扫 10 万行丢 10 万行
SELECT * FROM events ORDER BY occurred_at DESC LIMIT 50 OFFSET 100000;

-- ✅ Keyset 分页：O(1) 定位
SELECT * FROM events
WHERE occurred_at < $cursor_time
ORDER BY occurred_at DESC
LIMIT 50;
-- 下一页游标 = 本页最后一行的 occurred_at
```

### JSONB 查询下推

```sql
-- ❌ 应用层过滤：全量拉取
SELECT * FROM memories;  -- Python 里再 if m.payload['layer'] == 'semantic'

-- ✅ 表达式索引 + 下推
CREATE INDEX idx_memories_layer ON memories ((payload->>'layer'));
SELECT * FROM memories WHERE payload->>'layer' = 'semantic';
```

---

## 连接池管理

PostgreSQL 连接代价高（每连接 ~10MB 内存 + 进程），**必须池化**。

### 两层池

```
应用 (asyncpg pool, per-pod: min=2, max=10)
   ▼
PgBouncer (transaction pooling, pool_size=50)
   ▼
PostgreSQL (max_connections=200)
```

```ini
; pgbouncer.ini
[databases]
liuhao = host=postgres port=5432 dbname=liuhao

[pgbouncer]
pool_mode = transaction          ; 事务级复用，连接利用率最高
max_client_conn = 1000
default_pool_size = 50
reserve_pool_size = 10           ; 突发余量
server_idle_timeout = 60
```

> ⚠️ transaction 模式下：不能用 session 级特性（`SET`、预编译语句、advisory lock）。asyncpg 设 `statement_cache_size=0`。

### 应用侧（asyncpg）

```python
pool = await asyncpg.create_pool(
    dsn, min_size=2, max_size=10,          # 宁小勿大，排队比压垮 DB 好
    max_inactive_connection_lifetime=300,
    command_timeout=30,
    statement_cache_size=0,                # pgbouncer transaction 模式必须
)
```

连接数公式：`max_connections ≈ (CPU 核数 × 2) + 磁盘数`。16 核实例给 200 连接，靠 PgBouncer 复用支撑数千客户端。

---

## 分区与归档

事件/审计表是写入大户，按时间分区：

```sql
CREATE TABLE events (
    id BIGSERIAL, occurred_at TIMESTAMPTZ NOT NULL, ...
) PARTITION BY RANGE (occurred_at);

-- 月度分区（自动化脚本每月预建）
CREATE TABLE events_2026_10 PARTITION OF events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE events_2026_11 PARTITION OF events
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
```

收益：

- **查询裁剪**：`WHERE occurred_at > now() - interval '7 days'` 只扫最近分区
- **归档即 detach**：`ALTER TABLE events DETACH PARTITION events_2025_01;` 秒级完成，导出到对象存储后 DROP
- **真空局部化**：autovacuum 不再扫全表

分区管理自动化：用 `pg_partman` 或定时任务预建未来 2 个分区。

---

## pgvector 向量检索调优

语义记忆检索依赖 pgvector：

```sql
CREATE EXTENSION vector;

CREATE TABLE memory_embeddings (
    memory_id  TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL,
    embedding  vector(1536) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- HNSW 索引（召回率与性能的最佳平衡）
CREATE INDEX idx_mem_emb_hnsw ON memory_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

查询：

```sql
-- 租户内检索（先过滤后 ANN，命中率更高）
SET hnsw.ef_search = 64;          -- 召回率旋钮：40=快，200=准
SELECT memory_id, embedding <=> $1 AS distance
FROM memory_embeddings
WHERE agent_id = $2
ORDER BY embedding <=> $1
LIMIT 10;
```

调优参数：

| 参数 | 默认 | 调大效果 | 代价 |
|------|------|---------|------|
| `m`（建索引） | 16 | 召回率↑ | 内存↑、构建慢 |
| `ef_construction` | 64 | 索引质量↑ | 构建时间↑ |
| `ef_search`（查询时） | 40 | 召回率↑ | 延迟↑ |

> 生产建议：`m=16, ef_construction=128, ef_search=64` 起步；召回率抽检 < 95% 时先调 `ef_search`。

---

## 在线迁移（Expand-Contract）

零停机 schema 变更三段式：

```sql
-- Phase 1 (Release N): Expand —— 只加不删
ALTER TABLE agents ADD COLUMN display_name TEXT;          -- 可空，秒级
CREATE INDEX CONCURRENTLY idx_agents_display ON agents(display_name);
-- 应用双写：新旧列都写；读仍用旧列

-- Phase 2 (Release N+1): 回填 + 切换读
UPDATE agents SET display_name = name WHERE display_name IS NULL;
-- 大批量分批回填：
-- UPDATE ... WHERE id IN (SELECT id FROM agents WHERE display_name IS NULL LIMIT 1000);
-- 应用读切到新列

-- Phase 3 (Release N+2): Contract —— 确认无回滚需求后
ALTER TABLE agents DROP COLUMN name;
```

铁律：

- **加 NOT NULL 列必须带 DEFAULT**（PG 11+ 带默认值加列是元数据操作，秒级）
- **索引必须 `CONCURRENTLY`**（不锁写；不能在事务里跑，migration 工具要支持）
- **禁止同一 release 改列 + 改代码**：先 expand 再 contract，永远留回滚路
- **改列类型**：走"加新列 → 双写 → 回填 → 切读 → 删旧列"，绝不 `ALTER COLUMN TYPE`（全表重写锁表）

---

## 配置示例

```yaml
# config/database.yaml
postgres:
  dsn: postgresql://liuhao:***@pgbouncer:6432/liuhao
  pool:
    min_size: 2
    max_size: 10
    max_inactive_lifetime_s: 300
    command_timeout_s: 30
    statement_cache_size: 0          # pgbouncer transaction 模式

  slow_query_log_ms: 200             # 超阈值记日志并上指标
  statement_timeout_s: 60            # 兜底，防失控查询

pgbouncer:
  pool_mode: transaction
  default_pool_size: 50
  reserve_pool_size: 10

maintenance:
  reindex_weekly: true
  partition_pre_create_months: 2
  archive_after_months: 6            # 归档到 S3
  drop_after_months: 12

pgvector:
  hnsw:
    m: 16
    ef_construction: 128
    ef_search: 64
```

---

## 最佳实践

1. **每周看一次 pg_stat_statements Top10**，最贵的查询持续优化。
2. **migration 评审清单**：CONCURRENTLY？可空 DEFAULT？锁表时间？回滚路径？四问必答。
3. **连接数宁少勿多**：应用池排队是保护 DB，不是性能问题。
4. **写入大表必分区**：事件、审计、日志类表从第一天就分区。
5. **JSONB 查询字段必建表达式索引**，否则全表扫。
6. **备份恢复每季演练**：`pg_dump` 逻辑备份 + PITR 物理备份双轨。
7. **autovacuum 不背锅**：写入大表调激进参数（`autovacuum_vacuum_scale_factor=0.05`），别手动 VACUUM 当日常。

---

## 常见问题

### Q1: 查询突然变慢，昨天还好好的？

经典原因三选一：统计信息过期（`ANALYZE table`）、计划翻转（绑定变量窥探，`plan_cache_mode=force_custom_plan`）、表膨胀（`pg_stat_user_tables.n_dead_tup` 占比 >20% 则查 autovacuum）。

### Q2: 事件表写入到瓶颈了（>10k/s）？

分层突破：批量 INSERT（`COPY` 或 unnest 数组，10 倍提升）→ 关不必要索引 → 分区 → 换消息总线削峰。单机 PG 批量写可达 5~10 万行/s。

### Q3: 长事务报警怎么处理？

`pg_stat_activity` 找 `xact_start` 老的连接：交互式 psql 忘了提交（kill）、应用事务里调了 LLM API（事务内禁止网络调用——这是铁律）、报表查询（迁只读副本）。

### Q4: 读写分离值得上吗？

读:写 > 10:1 且主库 CPU > 60% 时值得。注意复制延迟：刚写完读副本可能读不到，关键读走主库或 LSN 等待。

### Q5: pgvector 和独立向量库（Qdrant/Milvus）怎么选？

向量量 < 1000 万、已有 PG 运维体系 → pgvector（少一个组件）。> 1000 万、需要复杂过滤/分片/GPU 索引 → 专用向量库。LiuHao AI OS 默认 pgvector，提供接口抽象可切换。

---

**相关文档**：[性能调优](performance-tuning.md) · [缓存策略](caching-strategies.md) · [事件溯源](../20-advanced-patterns/event-sourcing.md)
