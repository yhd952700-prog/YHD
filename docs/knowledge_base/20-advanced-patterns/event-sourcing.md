# 事件溯源模式（Event Sourcing）

事件溯源（Event Sourcing）是一种架构模式，它不存储实体的当前状态，而是存储导致状态变化的**事件序列**。当前状态通过重放事件流重建。在 LiuHao AI OS 中，事件溯源是记忆内核与审计内核的基石：每一次智能体决策、记忆写入、策略变更都以不可变事件的形式持久化。

---

## 目录

- [核心概念](#核心概念)
- [为什么需要事件溯源](#为什么需要事件溯源)
- [事件建模](#事件建模)
- [Python 实现示例](#python-实现示例)
- [快照与性能优化](#快照与性能优化)
- [配置示例](#配置示例)
- [与消息总线集成](#与消息总线集成)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 核心概念

### 事件（Event）

事件是已经发生的事实的不可变记录，具有三个要素：

1. **是什么**（What）：事件类型，如 `MemoryStored`、`PolicyUpdated`
2. **何时发生**（When）：精确时间戳（建议单调时钟 + 墙钟双记录）
3. **影响谁**（Who）：聚合根 ID（Aggregate ID）

### 聚合（Aggregate）

聚合是一致性边界，是事件的归属者。例如 `Agent` 聚合的所有事件共享同一个 `aggregate_id`。

### 事件流（Event Stream）

同一聚合的事件按版本号（version）严格递增排列，构成事件流。

### 投影（Projection）

从事件流中派生出的读模型，例如"智能体当前状态视图"、"任务统计报表"。

---

## 为什么需要事件溯源

| 传统 CRUD | 事件溯源 |
|-----------|----------|
| 只保留最新状态 | 保留完整历史 |
| 审计需额外建表 | 审计天然内置 |
| 难以回溯 bug 现场 | 可精确重放到任意时间点 |
| 状态变更原因丢失 | 每次变更都有事件描述原因 |
| 并发冲突难排查 | 乐观并发控制（版本号校验） |

在智能体系统中，事件溯源特别适合：

- **决策审计**：监管要求"为什么智能体做了这个决定"可追溯
- **记忆回放**：调试时重放智能体的记忆形成过程
- **时间旅行**：恢复到任意历史状态重新执行
- **事件驱动集成**：下游系统订阅事件流而非轮询状态

---

## 事件建模

### 事件命名规范

使用**过去时**动词 + 领域名词：

```
✅ MemoryStored       （记忆已存储）
✅ TaskCompleted      （任务已完成）
✅ PolicyViolated     （策略已违反）
❌ StoreMemory        （命令式，是 Command 而非 Event）
❌ MemoryStore        （名词化，语义不清）
```

### 事件结构

```json
{
  "event_id": "evt_01HF8K2ZQX0",
  "event_type": "MemoryStored",
  "aggregate_id": "agent_42",
  "aggregate_type": "Agent",
  "version": 137,
  "occurred_at": "2026-10-12T08:31:22.481Z",
  "causation_id": "cmd_01HF8K2Y...",
  "correlation_id": "trace_abc123",
  "payload": {
    "memory_layer": "semantic",
    "content_hash": "sha256:9f2c...",
    "ttl_seconds": 86400
  },
  "metadata": {
    "actor": "system",
    "ip": "10.0.3.17"
  }
}
```

关键字段说明：

- `version`：聚合内单调递增，用于乐观并发控制
- `causation_id`：触发本事件的命令 ID，构成因果链
- `correlation_id`：跨聚合/跨服务追踪 ID

---

## Python 实现示例

### 事件存储（Event Store）

```python
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class Event:
    event_id: str
    event_type: str
    aggregate_id: str
    version: int
    occurred_at: float
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(event_type: str, aggregate_id: str, version: int,
            payload: dict[str, Any], **metadata) -> "Event":
        return Event(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            event_type=event_type,
            aggregate_id=aggregate_id,
            version=version,
            occurred_at=time.time(),
            payload=payload,
            metadata=metadata,
        )


class ConcurrencyError(Exception):
    """乐观并发冲突：期望版本与实际版本不一致。"""


class SQLiteEventStore:
    """最小可用的 SQLite 事件存储。生产环境建议替换为 PostgreSQL 或 EventStoreDB。"""

    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                aggregate_id TEXT NOT NULL,
                version      INTEGER NOT NULL,
                event_id     TEXT NOT NULL,
                event_type   TEXT NOT NULL,
                occurred_at  REAL NOT NULL,
                payload      TEXT NOT NULL,
                metadata     TEXT NOT NULL,
                PRIMARY KEY (aggregate_id, version)
            )
        """)
        self._conn.commit()

    def append(self, aggregate_id: str, expected_version: int,
               events: Iterable[Event]) -> None:
        """以乐观并发方式追加事件。expected_version 为调用方读到的最新版本。"""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) FROM events WHERE aggregate_id = ?",
            (aggregate_id,),
        )
        actual_version = cur.fetchone()[0]
        if actual_version != expected_version:
            raise ConcurrencyError(
                f"aggregate {aggregate_id}: expected v{expected_version}, "
                f"actual v{actual_version}"
            )
        for e in events:
            cur.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                (e.aggregate_id, e.version, e.event_id, e.event_type,
                 e.occurred_at, json.dumps(e.payload), json.dumps(e.metadata)),
            )
        self._conn.commit()

    def load(self, aggregate_id: str, after_version: int = 0) -> list[Event]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE aggregate_id = ? AND version > ? "
            "ORDER BY version",
            (aggregate_id, after_version),
        )
        return [
            Event(event_id=r[2], event_type=r[3], aggregate_id=r[0],
                  version=r[1], occurred_at=r[4],
                  payload=json.loads(r[5]), metadata=json.loads(r[6]))
            for r in cur.fetchall()
        ]
```

### 聚合根与重放

```python
class AgentAggregate:
    """智能体聚合：状态完全由事件重放得出。"""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.version = 0
        self.status = "created"
        self.memory_count = 0
        self._pending: list[Event] = []

    # ---------- 命令方法（产生事件） ----------
    def activate(self) -> None:
        if self.status == "active":
            return
        self._raise("AgentActivated", {"agent_id": self.agent_id})

    def store_memory(self, layer: str, content_hash: str) -> None:
        if self.status != "active":
            raise RuntimeError("agent not active")
        self._raise("MemoryStored", {"layer": layer, "hash": content_hash})

    # ---------- 事件应用（状态迁移） ----------
    def _raise(self, event_type: str, payload: dict) -> None:
        event = Event.new(event_type, self.agent_id,
                          self.version + len(self._pending) + 1, payload)
        self._apply(event)
        self._pending.append(event)

    def _apply(self, event: Event) -> None:
        handler: Callable[[Event], None] | None = getattr(
            self, f"_on_{event.event_type}", None)
        if handler:
            handler(event)
        self.version = event.version

    def _on_AgentActivated(self, event: Event) -> None:
        self.status = "active"

    def _on_MemoryStored(self, event: Event) -> None:
        self.memory_count += 1

    # ---------- 持久化接口 ----------
    @classmethod
    def load(cls, store: SQLiteEventStore, agent_id: str) -> "AgentAggregate":
        agg = cls(agent_id)
        for event in store.load(agent_id):
            agg._apply(event)
        return agg

    def save(self, store: SQLiteEventStore) -> None:
        store.append(self.agent_id, self.version - len(self._pending),
                     self._pending)
        self._pending.clear()
```

### 使用示例

```python
store = SQLiteEventStore("events.db")

agent = AgentAggregate("agent_42")
agent.activate()
agent.store_memory("semantic", "sha256:9f2c...")
agent.save(store)

# 另一个进程：从事件流重建状态
reloaded = AgentAggregate.load(store, "agent_42")
assert reloaded.status == "active"
assert reloaded.memory_count == 1
```

---

## 快照与性能优化

当事件流超过数千条时，每次全量重放代价过高。解决方案是**快照（Snapshot）**：

```python
@dataclass
class Snapshot:
    aggregate_id: str
    version: int
    state: dict[str, Any]

def load_with_snapshot(store, snap_store, agent_id) -> AgentAggregate:
    snap = snap_store.latest(agent_id)
    agg = AgentAggregate(agent_id)
    if snap:
        agg.__dict__.update(snap.state)
    for event in store.load(agent_id, after_version=agg.version):
        agg._apply(event)
    return agg
```

快照策略建议：

- **频率**：每 100~1000 个事件拍一次快照
- **存储**：与事件流分离（对象存储或独立表）
- **版本化**：快照记录对应的事件版本号，重建时只重放快照之后的事件

---

## 配置示例

```yaml
# config/event_sourcing.yaml
event_store:
  backend: postgres          # sqlite | postgres | eventstoredb
  dsn: postgresql://liuhao:***@db:5432/events
  pool_size: 20
  append_timeout_ms: 500

snapshots:
  enabled: true
  interval_events: 500       # 每 500 个事件拍快照
  backend: s3
  bucket: liuhao-snapshots
  retention_days: 90

projections:
  rebuild_on_start: false
  batch_size: 1000
  lag_alert_seconds: 30      # 投影落后事件流超过 30s 告警

upcasting:
  enabled: true              # 旧版本事件在读取时自动升级
  registry: liuhao.events.upcasters
```

---

## 与消息总线集成

事件持久化后需发布到消息总线供下游订阅。**务必先落库再发布**，否则进程崩溃会导致事件丢失：

```python
class TransactionalOutbox:
    """发件箱模式：事件与业务数据在同一事务写入，后台中继发布到总线。"""

    def __init__(self, store: SQLiteEventStore, bus):
        self._store = store
        self._bus = bus

    def relay_pending(self) -> int:
        rows = self._store._conn.execute(
            "SELECT event_id, event_type, payload FROM outbox WHERE published = 0 "
            "LIMIT 100"
        ).fetchall()
        for event_id, event_type, payload in rows:
            self._bus.publish(event_type, json.loads(payload))
            self._store._conn.execute(
                "UPDATE outbox SET published = 1 WHERE event_id = ?", (event_id,))
        self._store._conn.commit()
        return len(rows)
```

---

## 最佳实践

1. **事件不可变，永不删除**。纠错用新的补偿事件（如 `MemoryStoreReverted`），不要 UPDATE。
2. **事件即契约**。事件 schema 变更要走版本化（`MemoryStoredV2`），配合 upcaster 升级旧事件。
3. **保持事件小而专注**。一个事件描述一件事，payload 只放状态迁移必需的数据。
4. **命令与事件分离**。命令（`StoreMemory`）是意图，可以被拒绝；事件（`MemoryStored`）是事实，不可拒绝。
5. **幂等消费**。下游投影必须能安全地重复消费同一事件（用 `event_id` 去重）。
6. **监控投影延迟**。投影落后事件流是系统亚健康的重要信号。
7. **谨慎暴露内部事件**。对外集成使用独立的"集成事件"，避免内部重构破坏外部消费者。

---

## 常见问题

### Q1: 事件溯源会不会让存储膨胀？

会增长，但可控。手段：快照压缩重放成本、冷事件归档到对象存储、投影承载查询而非直接扫事件流。相比其带来的审计与回放能力，对智能体系统通常值得。

### Q2: 如何处理事件 schema 演进？

三种方式，按推荐顺序：
1. **Upcasting**：读取时把旧事件升级为最新结构（代码内转换，不动存储）
2. **多版本并存**：新版本事件用新类型名（`MemoryStoredV2`）
3. **全量迁移**：离线重写事件流（代价高，仅大版本升级时用）

### Q3: 事件溯源和 CQRS 必须一起用吗？

不必须，但强烈建议。事件溯源天然是写侧模型，查询效率低；配合 [CQRS 模式](cqrs-pattern.md) 的独立读模型（投影）才能发挥最大价值。

### Q4: 如何调试"智能体为什么做出这个决定"？

用 `correlation_id` 捞出该决策相关的全部事件，按 `version`/`occurred_at` 排序重放，可完整还原决策上下文——这正是 LiuHao AI OS 审计内核的核心能力。

### Q5: 并发写同一聚合冲突怎么办？

事件存储通过 `(aggregate_id, version)` 唯一约束 + 期望版本校验实现乐观并发。冲突时重新加载最新状态、重试命令。高频冲突的聚合应考虑拆分。

---

**相关文档**：[CQRS 读写分离](cqrs-pattern.md) · [Saga 分布式事务](saga-pattern.md) · [审计日志](../24-security/audit-logging.md)
