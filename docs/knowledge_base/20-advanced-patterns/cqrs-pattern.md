# CQRS 读写分离模式

CQRS（Command Query Responsibility Segregation，命令查询职责分离）将系统的**写操作（Command）**与**读操作（Query）**拆分为两个独立的模型，各自使用最适合的数据结构、存储和扩展策略。在 LiuHao AI OS 中，智能体的决策写入走高一致性的写模型，而驾驶舱的实时监控、记忆检索走为查询优化的读模型。

---

## 目录

- [核心概念](#核心概念)
- [什么时候用 CQRS](#什么时候用-cqrs)
- [架构分层](#架构分层)
- [Python 实现示例](#python-实现示例)
- [读写一致性策略](#读写一致性策略)
- [配置示例](#配置示例)
- [与事件溯源的组合](#与事件溯源的组合)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 核心概念

### 命令（Command）

表达**改变系统状态的意图**，具有以下特征：

- 祈使句命名：`CreateAgent`、`StoreMemory`、`ExecuteTask`
- 可以被**拒绝**（校验失败、权限不足、策略冲突）
- 不返回业务数据，最多返回 ID 和受理状态

### 查询（Query）

表达**读取系统状态的意图**：

- 名词式命名：`GetAgentStatus`、`SearchMemories`、`ListRunningTasks`
- **永不改变状态**，可安全重试、缓存
- 直接返回为前端/调用方定制的 DTO

### 写模型 vs 读模型

| 维度 | 写模型 | 读模型 |
|------|--------|--------|
| 优化目标 | 一致性、正确性 | 查询速度、形状贴合 UI |
| 数据结构 | 规范化、聚合 | 反规范化、宽表/文档 |
| 存储 | PostgreSQL 主库 | Redis / ES / 物化视图 |
| 扩展方式 | 垂直扩展、分片 | 只读副本水平扩展 |

---

## 什么时候用 CQRS

**适合：**

- 读写负载差异悬殊（智能体系统中读远多于写，通常 10:1 以上）
- 查询形状与领域模型差异大（仪表盘聚合视图 vs 聚合根）
- 需要独立扩展读写两侧
- 已经与事件溯源结合，需要投影

**不适合：**

- 简单 CRUD 应用——CQRS 引入的复杂度大于收益
- 强一致读的场景且无法容忍毫秒级延迟（如账户余额扣减确认）

---

## 架构分层

```
                ┌──────────────┐
   写请求 ─────▶│ Command Bus  │─────▶ CommandHandler ─────▶ 写模型(PG)
                └──────────────┘              │
                                              │ 产生事件
                                              ▼
                                        消息总线/发件箱
                                              │
                ┌──────────────┐              ▼
   读请求 ─────▶│  Query Bus   │─────▶ QueryHandler ─────▶ 读模型(Redis/ES)
                └──────────────┘              ▲
                                    Projector 订阅事件更新读模型
```

---

## Python 实现示例

### 命令与查询定义

```python
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


# ---------- 命令 ----------
@dataclass(frozen=True)
class Command:
    command_id: str = field(default_factory=lambda: f"cmd_{uuid.uuid4().hex[:12]}")
    issued_at: float = field(default_factory=time.time)

@dataclass(frozen=True)
class CreateAgent(Command):
    name: str = ""
    role: str = "general"
    capabilities: tuple[str, ...] = ()

@dataclass(frozen=True)
class StoreMemory(Command):
    agent_id: str = ""
    layer: str = "semantic"
    content: str = ""


# ---------- 查询 ----------
@dataclass(frozen=True)
class Query:
    pass

@dataclass(frozen=True)
class GetAgentDashboard(Query):
    agent_id: str = ""

@dataclass(frozen=True)
class SearchMemories(Query):
    query_text: str = ""
    top_k: int = 10
```

### 命令总线与处理器

```python
class CommandHandler(Protocol):
    def handle(self, command: Command) -> Any: ...

class CommandBus:
    """命令总线：路由 + 横切关注点（校验、审计、限流）。"""

    def __init__(self):
        self._handlers: dict[type, CommandHandler] = {}
        self._middlewares: list[Callable[[Command, Callable], Any]] = []

    def register(self, command_type: type, handler: CommandHandler) -> None:
        if command_type in self._handlers:
            raise ValueError(f"handler already registered for {command_type}")
        self._handlers[command_type] = handler

    def use(self, middleware: Callable[[Command, Callable], Any]) -> None:
        self._middlewares.append(middleware)

    def dispatch(self, command: Command) -> Any:
        handler = self._handlers.get(type(command))
        if not handler:
            raise LookupError(f"no handler for {type(command).__name__}")

        def invoke(i: int, cmd: Command) -> Any:
            if i == len(self._middlewares):
                return handler.handle(cmd)
            return self._middlewares[i](cmd, lambda c: invoke(i + 1, c))

        return invoke(0, command)


# 中间件示例：审计
def audit_middleware(command: Command, next_call: Callable) -> Any:
    print(f"[AUDIT] dispatching {type(command).__name__} id={command.command_id}")
    result = next_call(command)
    print(f"[AUDIT] completed {type(command).__name__}")
    return result
```

### 命令处理器（写侧）

```python
class AgentRepository:
    def __init__(self):
        self._agents: dict[str, dict] = {}

    def save(self, agent: dict) -> None:
        self._agents[agent["id"]] = agent

class CreateAgentHandler:
    def __init__(self, repo: AgentRepository, event_bus):
        self._repo = repo
        self._bus = event_bus

    def handle(self, command: CreateAgent) -> str:
        agent_id = f"agent_{uuid.uuid4().hex[:8]}"
        self._repo.save({
            "id": agent_id,
            "name": command.name,
            "role": command.role,
            "status": "created",
        })
        # 发布领域事件，读侧 Projector 订阅后更新读模型
        self._bus.publish("AgentCreated", {
            "agent_id": agent_id,
            "name": command.name,
            "role": command.role,
        })
        return agent_id
```

### 查询处理器（读侧）

```python
class AgentDashboardReadModel:
    """反规范化读模型：直接面向驾驶舱 UI 的宽表。"""

    def __init__(self):
        self._table: dict[str, dict] = {}

    def upsert(self, agent_id: str, **fields) -> None:
        self._table.setdefault(agent_id, {}).update(fields)

    def get(self, agent_id: str) -> dict | None:
        return self._table.get(agent_id)

class GetAgentDashboardHandler:
    def __init__(self, read_model: AgentDashboardReadModel):
        self._rm = read_model

    def handle(self, query: GetAgentDashboard) -> dict:
        row = self._rm.get(query.agent_id)
        if row is None:
            raise LookupError(f"agent {query.agent_id} not found")
        return row   # 已经是 UI 需要的形状，无需组装

class AgentCreatedProjector:
    """订阅写侧事件，维护读模型。"""

    def __init__(self, read_model: AgentDashboardReadModel):
        self._rm = read_model

    def on_agent_created(self, event: dict) -> None:
        self._rm.upsert(event["agent_id"],
                        name=event["name"], role=event["role"],
                        memory_count=0, last_active=time.time())
```

### 组装与使用

```python
class SimpleEventBus:
    def __init__(self):
        self._subs: dict[str, list[Callable]] = {}
    def subscribe(self, topic: str, fn: Callable) -> None:
        self._subs.setdefault(topic, []).append(fn)
    def publish(self, topic: str, event: dict) -> None:
        for fn in self._subs.get(topic, []):
            fn(event)

bus = SimpleEventBus()
read_model = AgentDashboardReadModel()
projector = AgentCreatedProjector(read_model)
bus.subscribe("AgentCreated", projector.on_agent_created)

command_bus = CommandBus()
command_bus.use(audit_middleware)
repo = AgentRepository()
command_bus.register(CreateAgent, CreateAgentHandler(repo, bus))

query_handlers = {GetAgentDashboard: GetAgentDashboardHandler(read_model)}

# 写
agent_id = command_bus.dispatch(CreateAgent(name="researcher-01", role="research"))

# 读（最终一致，通常毫秒级延迟）
dashboard = query_handlers[GetAgentDashboard].handle(GetAgentDashboard(agent_id))
assert dashboard["name"] == "researcher-01"
```

---

## 读写一致性策略

CQRS 读侧默认是**最终一致**。根据业务敏感度选择策略：

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| 读己之写 | 写后返回版本号，读时等待读模型追平该版本 | 用户刚创建完立即查看 |
| 延迟容忍 + UI 提示 | 展示"数据同步中"标识 | 仪表盘、统计报表 |
| 关键读走写库 | 少量强一致查询直连写模型 | 余额、配额校验 |
| 定期校准 | 定时任务比对读写两侧并修复 | 兜底手段 |

读己之写的实现示例：

```python
def read_own_write(self, agent_id: str, expected_version: int,
                   timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = self._rm.get(agent_id)
        if row and row.get("version", 0) >= expected_version:
            return row
        time.sleep(0.02)
    raise TimeoutError("read model lag exceeds threshold")
```

---

## 配置示例

```yaml
# config/cqrs.yaml
command_side:
  repository: postgres
  dsn: postgresql://liuhao:***@db-primary:5432/liuhao
  middlewares:
    - validation
    - audit
    - rate_limit
  rate_limit:
    per_actor_per_minute: 600

query_side:
  read_models:
    agent_dashboard:
      store: redis
      key_prefix: "dash:agent:"
      ttl_seconds: 3600
    memory_search:
      store: elasticsearch
      index: memories_v3
      refresh_interval: 1s

projections:
  agent_dashboard:
    source_events: [AgentCreated, MemoryStored, TaskCompleted]
    batch_size: 500
    max_lag_ms: 2000
  memory_search:
    source_events: [MemoryStored, MemoryDeleted]
    idempotency: event_id

consistency:
  read_your_writes_timeout_ms: 2000
  strong_reads_via: write_db        # write_db | none
```

---

## 与事件溯源的组合

CQRS 与[事件溯源](event-sourcing.md)是黄金搭档：

- **写侧**：聚合重放事件流 → 校验命令 → 追加新事件
- **读侧**：Projector 订阅事件 → 更新任意数量的读模型
- **新增查询需求**：新建一个读模型，重放历史事件回填，写侧零改动

这正是 LiuHao AI OS 记忆内核的工作方式：记忆写入只有一条路径（事件），但工作记忆视图、语义检索索引、审计报表三个读模型各自独立演进。

---

## 最佳实践

1. **先分开，再谈不同存储**。逻辑上分离 Command/Query 代码路径是第一步；物理上用不同数据库是第二步，非必需。
2. **命令瘦、查询自由**。命令只做状态变更；查询可以任意定制 DTO，不必复用领域对象。
3. **读模型可丢弃、可重建**。任何读模型都应能通过重放事件完整重建，这是系统韧性的来源。
4. **一个命令一个处理器**。禁止一个命令路由到多个处理器——那说明该拆成多个命令。
5. **查询不做 join 拼装**。读模型在投影时就完成 join/聚合，查询路径保持 O(1) 读取。
6. **监控投影延迟**。投影滞后是 CQRS 系统最常见的故障模式，必须有指标和告警。
7. **拒绝读侧回写**。任何"从读模型读出来改一改写回去"的代码都是 bug 温床。

---

## 常见问题

### Q1: CQRS 会不会过度设计？

对小系统是。判断标准：如果你的查询可以直接在领域模型上高效完成、且读写负载均衡，就不需要 CQRS。智能体系统读多写少、查询形状复杂，通常值得。

### Q2: 读模型延迟导致用户看到自己刚创建的数据消失怎么办？

用"读己之写"策略：写操作返回版本号或时间戳，读操作等待读模型追平后再返回；或在写入后短期内把该实体的读请求路由到写库。

### Q3: 事务边界在哪？

命令侧：单个聚合一次命令一个事务。跨聚合协调用 [Saga 模式](saga-pattern.md)。读侧不需要事务，只需幂等更新。

### Q4: 读模型该用几个？

按需。常见的是一个 UI 视图一个读模型。维护成本在 Projector，不在存储——读模型存储（Redis/ES）本身很便宜。

### Q5: 与 LiuHao AI OS 的消息总线如何配合？

命令处理器提交事务后，通过发件箱模式把事件发布到消息总线；Projector 作为总线消费者更新读模型。保证"先落库、后发布"，崩溃不丢事件。

---

**相关文档**：[事件溯源模式](event-sourcing.md) · [Saga 分布式事务](saga-pattern.md) · [缓存策略](../23-performance/caching-strategies.md)
