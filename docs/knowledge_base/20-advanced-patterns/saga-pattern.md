# Saga 分布式事务模式

Saga 模式用一串**本地事务 + 补偿操作**替代跨服务的 ACID 分布式事务（2PC）。每一步本地事务提交后发布事件触发下一步；任何一步失败，按相反顺序执行已完成步骤的**补偿事务**，把系统恢复到一致状态。在 LiuHao AI OS 中，跨内核操作（如"创建智能体 → 分配配额 → 初始化记忆空间 → 注册到网络"）就是典型的 Saga。

---

## 目录

- [核心概念](#核心概念)
- [编排 vs 编制](#编排-vs-编制)
- [补偿设计](#补偿设计)
- [Python 实现示例](#python-实现示例)
- [隔离性问题与对策](#隔离性问题与对策)
- [配置示例](#配置示例)
- [在 LiuHao AI OS 中的应用](#在-liuhao-ai-os-中的应用)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 核心概念

### 本地事务（Local Transaction）

Saga 的每一步都是单个服务/聚合内的普通事务，独立提交，立即释放锁——这是 Saga 优于 2PC 的根本原因：不长时间持有分布式锁。

### 补偿事务（Compensating Transaction）

语义上撤销一个已提交步骤的效果。注意：补偿**不是**数据库回滚——原步骤已提交，补偿是一次新的正向操作（如"退款"补偿"扣款"）。

### Saga 执行协调器（SEC）

记录 Saga 状态机：当前步骤、已完成步骤、补偿进度。协调器自身必须持久化，否则崩溃后无法续跑。

---

## 编排 vs 编制

| 维度 | 编制（Choreography） | 编排（Orchestration） |
|------|---------------------|----------------------|
| 协调方式 | 各服务订阅事件自行接力 | 中心化协调器发号施令 |
| 优点 | 去中心化、低耦合 | 流程可见、易排查、易改 |
| 缺点 | 流程隐式、链路难追踪 | 协调器是单点（需高可用） |
| 适用 | 3 步以内简单流程 | 复杂流程、强审计要求 |

**LiuHao AI OS 采用编排式**：主权内核要求所有跨内核事务有显式、可审计的状态机记录。

---

## 补偿设计

补偿设计的三种类型：

1. **可补偿事务**：可以撤销（扣款→退款、创建→删除）
2. **枢纽事务（Pivot）**：不可撤销的分界点（发送邮件、调用不可逆外部 API）——失败后只能向前重试，不能回退
3. **可重试事务**：枢纽之后保证最终成功的步骤

设计流程时尽量让**不可补偿步骤靠后**，减少需要补偿的窗口。

补偿示例映射表（智能体开通流程）：

| 步骤 | 正向操作 | 补偿操作 |
|------|---------|---------|
| T1 | 创建智能体记录 | 删除智能体记录 |
| T2 | 分配资源配额 | 回收配额 |
| T3 | 初始化记忆空间 | 清空并销毁记忆空间 |
| T4 | 注册到智能体网络 | 从网络注销 |
| T5 | 发送欢迎通知 | **不可补偿（枢纽）**——失败仅告警 |

---

## Python 实现示例

### 编排式 Saga 框架

```python
from __future__ import annotations

import enum
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


class SagaStatus(enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    FAILED = "failed"          # 补偿也失败，需人工介入


@dataclass
class Step:
    name: str
    action: Callable[[dict], Any]            # 正向操作
    compensate: Callable[[dict], Any] | None # 补偿操作；None 表示枢纽步骤
    retry_limit: int = 3


@dataclass
class SagaContext:
    saga_id: str
    data: dict[str, Any] = field(default_factory=dict)
    completed_steps: list[str] = field(default_factory=list)
    status: SagaStatus = SagaStatus.RUNNING


class SagaOrchestrator:
    """持久化编排器：崩溃后可从最后状态续跑。"""

    def __init__(self, db_path: str = ":memory:"):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sagas (
                saga_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                completed TEXT NOT NULL,   -- JSON list
                data TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self._conn.commit()

    # ---------- 持久化 ----------
    def _save(self, ctx: SagaContext) -> None:
        self._conn.execute(
            "INSERT INTO sagas VALUES (?,?,?,?,?) "
            "ON CONFLICT(saga_id) DO UPDATE SET status=?, completed=?, "
            "data=?, updated_at=?",
            (ctx.saga_id, ctx.status.value, json.dumps(ctx.completed_steps),
             json.dumps(ctx.data), time.time(),
             ctx.status.value, json.dumps(ctx.completed_steps),
             json.dumps(ctx.data), time.time()),
        )
        self._conn.commit()

    def _load(self, saga_id: str) -> SagaContext:
        row = self._conn.execute(
            "SELECT status, completed, data FROM sagas WHERE saga_id=?",
            (saga_id,)).fetchone()
        if not row:
            raise LookupError(saga_id)
        return SagaContext(saga_id=saga_id,
                           status=SagaStatus(row[0]),
                           completed_steps=json.loads(row[1]),
                           data=json.loads(row[2]))

    # ---------- 执行 ----------
    def run(self, steps: list[Step], initial_data: dict,
            saga_id: str | None = None) -> SagaContext:
        ctx = SagaContext(saga_id or f"saga_{uuid.uuid4().hex[:12]}",
                          data=dict(initial_data))
        self._save(ctx)
        return self._advance(steps, ctx)

    def resume(self, steps: list[Step], saga_id: str) -> SagaContext:
        """进程重启后续跑未完成的 Saga。"""
        ctx = self._load(saga_id)
        if ctx.status in (SagaStatus.COMPLETED, SagaStatus.COMPENSATED):
            return ctx
        return self._advance(steps, ctx)

    def _advance(self, steps: list[Step], ctx: SagaContext) -> SagaContext:
        remaining = [s for s in steps if s.name not in ctx.completed_steps]
        for step in remaining:
            if ctx.status == SagaStatus.COMPENSATING:
                break
            try:
                result = self._with_retry(step.action, ctx.data, step.retry_limit)
                ctx.data[f"{step.name}.result"] = result
                ctx.completed_steps.append(step.name)
                self._save(ctx)
            except Exception as exc:  # noqa: BLE001
                ctx.data["error"] = f"{step.name}: {exc}"
                ctx.status = SagaStatus.COMPENSATING
                self._save(ctx)
                self._compensate(steps, ctx)
                break
        else:
            if ctx.status == SagaStatus.RUNNING:
                ctx.status = SagaStatus.COMPLETED
                self._save(ctx)
        return ctx

    def _compensate(self, steps: list[Step], ctx: SagaContext) -> None:
        by_name = {s.name: s for s in steps}
        for name in reversed(ctx.completed_steps):
            step = by_name[name]
            if step.compensate is None:
                continue  # 枢纽步骤不可补偿
            try:
                self._with_retry(step.compensate, ctx.data, retries=5)
            except Exception as exc:  # noqa: BLE001
                ctx.status = SagaStatus.FAILED
                ctx.data["compensation_error"] = f"{name}: {exc}"
                self._save(ctx)
                # 补偿失败必须告警并人工介入
                raise RuntimeError(
                    f"saga {ctx.saga_id} compensation failed at {name}") from exc
        ctx.status = SagaStatus.COMPENSATED
        self._save(ctx)

    @staticmethod
    def _with_retry(fn: Callable, data: dict, retries: int) -> Any:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                return fn(data)
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(min(0.1 * 2 ** attempt, 2.0))  # 指数退避
        raise last  # type: ignore[misc]
```

### 使用示例：智能体开通流程

```python
orchestrator = SagaOrchestrator("sagas.db")

steps = [
    Step("create_agent",
         action=lambda d: agent_svc.create(d["name"]),
         compensate=lambda d: agent_svc.delete(d["create_agent.result"]["id"])),
    Step("allocate_quota",
         action=lambda d: quota_svc.allocate(d["create_agent.result"]["id"], 1000),
         compensate=lambda d: quota_svc.reclaim(d["create_agent.result"]["id"])),
    Step("init_memory",
         action=lambda d: memory_svc.init_space(d["create_agent.result"]["id"]),
         compensate=lambda d: memory_svc.destroy(d["create_agent.result"]["id"])),
    Step("notify",
         action=lambda d: notify_svc.send_welcome(d["owner"]),
         compensate=None),  # 枢纽步骤：通知已发出不可撤回
]

ctx = orchestrator.run(steps, {"name": "researcher-01", "owner": "liuhao"})
assert ctx.status == SagaStatus.COMPLETED
```

---

## 隔离性问题与对策

Saga 放弃了 ACID 的隔离性（I），中间状态对其他事务可见。典型异常与对策：

| 异常 | 描述 | 对策 |
|------|------|------|
| 脏读 | 读到未完成 Saga 的中间状态 | 状态字段标记 `pending`；读侧过滤 |
| 丢失更新 | 并发 Saga 覆盖彼此写入 | 语义锁（记录级 `saga_owner` 字段） |
| 幻读 | 同一 Saga 内两次读取不一致 | 关键读加版本校验 |

实用手段：

- **语义锁**：记录加 `_saga_lock` 字段，其他 Saga 操作前检查
- **交换式更新**：把"先读后写"改为原子 SQL（`UPDATE ... WHERE balance >= ?`）
- **版本化读**：读操作带版本号，发现中间态则重试

---

## 配置示例

```yaml
# config/saga.yaml
saga:
  orchestrator:
    store: postgres
    dsn: postgresql://liuhao:***@db:5432/sagas
    heartbeat_interval_s: 10
    lease_ttl_s: 60              # 协调器租约，防双活

  defaults:
    step_retry_limit: 3
    step_timeout_s: 30
    compensation_retry_limit: 5
    backoff_base_ms: 100
    backoff_max_ms: 5000

  sagas:
    agent_provisioning:
      steps: [create_agent, allocate_quota, init_memory, register_network, notify]
      pivot_step: notify
      timeout_s: 300
    memory_migration:
      steps: [export_memory, validate_export, import_memory, verify, cleanup_source]
      pivot_step: cleanup_source
      timeout_s: 3600

  alerts:
    on_compensation_failure:
      channel: pagerduty
      severity: critical
    on_saga_timeout:
      channel: slack
      severity: warning
```

---

## 在 LiuHao AI OS 中的应用

1. **智能体生命周期**：开通/迁移/下线均为 Saga，每步对应一个内核操作
2. **跨内核记忆迁移**：导出（记忆内核）→ 校验（评价内核）→ 导入 → 清源，清源为枢纽
3. **策略变更下发**：更新策略（策略内核）→ 通知所有智能体 → 确认生效；部分智能体确认失败则补偿回旧策略
4. **审计关联**：每个 Saga 的 `saga_id` 贯穿所有事件，审计可按 Saga 维度完整回放

---

## 最佳实践

1. **每个步骤幂等**。重试和补偿都可能重复执行，幂等键（saga_id + step_name）去重。
2. **补偿也必须幂等**，且补偿失败要升级到人工——补偿失败的 Saga 是系统里最危险的状态。
3. **编排器状态持久化**。内存状态机的 Saga 框架不可用于生产。
4. **枢纽步骤尽量靠后**、尽量少；枢纽前失败可回滚，枢纽后失败只能向前。
5. **设置整体超时**。Saga 卡死比失败更难排查，超时后强制进入补偿。
6. **可观测性**：上报每一步耗时、补偿次数、Saga 存活时长到监控系统。
7. **优先用 Saga，慎用 2PC**。2PC 在智能体长流程（分钟级）下会造成灾难性锁等待。

---

## 常见问题

### Q1: Saga 和 2PC 怎么选？

跨服务、长耗时（>秒级）、含外部系统调用的流程选 Saga；单数据库内、毫秒级强一致场景可直接用本地事务。2PC 仅在外部系统强制要求且链路短时考虑。

### Q2: 补偿执行时原数据又被别的请求改了怎么办？

语义锁 + 版本检查。补偿前校验记录当前版本是否仍是 Saga 写入时的版本；若已被污染，补偿转为人工工单而非自动覆盖。

### Q3: 编排器本身挂了呢？

编排器是无状态的（状态全在 saga 表），多起几个副本用租约选主；崩溃副本的未完成 Saga 由新主通过 `resume()` 续跑。

### Q4: 步骤里有调用 LLM 这种昂贵操作，重试会烧钱吗？

对昂贵步骤单独设 `retry_limit=1` 并记录中间产物；正向失败优先走"续用已有产物"逻辑而非重试。也可在步骤内先做 dry-run 估算成本再执行。

### Q5: Saga 内可以嵌套 Saga 吗？

可以，但深度建议不超过 2 层。子 Saga 作为父 Saga 的一个步骤，子 Saga 的整体补偿即其自身的补偿流程。过深嵌套是流程该拆分的信号。

---

**相关文档**：[事件溯源模式](event-sourcing.md) · [CQRS 读写分离](cqrs-pattern.md) · [熔断策略进阶](circuit-breaker-advanced.md)
