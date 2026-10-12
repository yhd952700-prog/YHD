# 舱壁隔离深入（Bulkhead Advanced）

舱壁模式借鉴船舶设计：把船体分隔成多个水密舱，一个舱进水不至于沉船。软件中的舱壁通过**隔离资源**（线程池、连接池、信号量、队列）确保单一依赖或租户的故障/突发不会耗尽全局资源。本文档讨论线程池舱壁、信号量舱壁、租户级隔离、队列隔离与配额联动等进阶主题。LiuHao AI OS 的配额核算内核与执行内核大量依赖舱壁保证多租户公平性。

---

## 目录

- [舱壁的四种形态](#舱壁的四种形态)
- [线程池舱壁 vs 信号量舱壁](#线程池舱壁-vs-信号量舱壁)
- [Python 实现示例](#python-实现示例)
- [租户级舱壁与配额联动](#租户级舱壁与配额联动)
- [队列隔离与背压](#队列隔离与背压)
- [配置示例](#配置示例)
- [容量规划](#容量规划)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 舱壁的四种形态

| 形态 | 隔离对象 | 典型用途 | 代价 |
|------|---------|---------|------|
| 线程池舱壁 | 线程 | 同步阻塞调用（JDBC、文件 IO） | 线程内存、上下文切换 |
| 信号量舱壁 | 并发许可数 | 异步/轻量调用（HTTP、SDK） | 几乎为零 |
| 连接池舱壁 | 数据库连接 | 每个下游库独立池 | 连接数翻倍 |
| 队列舱壁 | 排队容量 | 任务调度、消息消费 | 队列堆积监控成本 |

在智能体系统中还有第五种：**租户舱壁**——按租户/智能体隔离并发与配额，防止单租户突发挤垮共享服务。

---

## 线程池舱壁 vs 信号量舱壁

### 选择矩阵

```
调用是阻塞 IO 且耗时长？ ──是──▶ 线程池舱壁（把阻塞关进笼子）
        │否
调用需要超时控制？ ──────────是──▶ 线程池舱壁（信号量无法中断线程）
        │否
高并发轻量调用？ ─────────────是──▶ 信号量舱壁（开销最小）
```

Python 世界中，asyncio 生态下**信号量舱壁是绝对主力**：协程没有线程内存代价，信号量 + 超时即可覆盖绝大多数场景。线程池舱壁仅在调用同步阻塞库（某些 C 扩展、老式 SDK）时使用。

### 常见错误

```python
# ❌ 错误：全局一个 session，一个慢下游拖死所有调用
session = aiohttp.ClientSession()

# ✅ 正确：每个下游独立 session + 独立连接器（连接数即舱壁）
sessions = {
    "llm_provider": aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=50)),
    "vector_db": aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=20)),
    "slack_api": aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10)),
}
```

---

## Python 实现示例

### 信号量舱壁（asyncio）

```python
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


class BulkheadFullError(RuntimeError):
    """舱壁已满且等待超时，快速失败。"""


@dataclass
class BulkheadStats:
    acquired: int = 0
    rejected: int = 0
    timeouts: int = 0
    max_concurrent_seen: int = 0


class SemaphoreBulkhead:
    """带等待上限和统计的并发舱壁。"""

    def __init__(self, name: str, max_concurrent: int,
                 max_wait_s: float = 0.5):
        self.name = name
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._max_wait_s = max_wait_s
        self._inflight = 0
        self.stats = BulkheadStats()

    async def run(self, coro_fn, *args, timeout_s: float = 30.0, **kwargs):
        # 1. 尝试进入舱壁（等待上限防止队列无限堆积）
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self._max_wait_s)
        except asyncio.TimeoutError:
            self.stats.rejected += 1
            raise BulkheadFullError(
                f"bulkhead {self.name} full ({self._max_concurrent})") from None

        self._inflight += 1
        self.stats.acquired += 1
        self.stats.max_concurrent_seen = max(
            self.stats.max_concurrent_seen, self._inflight)
        try:
            # 2. 业务超时独立于舱壁等待
            return await asyncio.wait_for(coro_fn(*args, **kwargs),
                                          timeout=timeout_s)
        except asyncio.TimeoutError:
            self.stats.timeouts += 1
            raise
        finally:
            self._inflight -= 1
            self._sem.release()


# ---------- 用法 ----------
llm_bulkhead = SemaphoreBulkhead("llm-calls", max_concurrent=50)
db_bulkhead = SemaphoreBulkhead("db-writes", max_concurrent=10, max_wait_s=1.0)

async def handle_request(prompt: str):
    # LLM 调用与 DB 写入互不挤占
    result = await llm_bulkhead.run(call_llm, prompt, timeout_s=60)
    await db_bulkhead.run(persist, result, timeout_s=5)
    return result
```

### 线程池舱壁（同步代码）

```python
from concurrent.futures import ThreadPoolExecutor

executors = {
    "pdf_parsing": ThreadPoolExecutor(max_workers=4,   # CPU 密集，限核数
                                      thread_name_prefix="pdf"),
    "legacy_erp":  ThreadPoolExecutor(max_workers=8,   # 阻塞 IO，按下游容量
                                      thread_name_prefix="erp"),
}

def submit(pool_name: str, fn, *args, timeout_s: float = 30.0):
    future = executors[pool_name].submit(fn, *args)
    return future.result(timeout=timeout_s)  # 超时保护线程池不被占满
```

---

## 租户级舱壁与配额联动

多租户系统的舱壁需要两个维度：

1. **全局舱壁**：保护下游总量（如 LLM 提供商账号级 QPS 上限）
2. **租户舱壁**：保证租户间公平（单租户不超过全局的一定比例）

```python
class TenantBulkhead:
    """两级许可：先过租户闸，再过全局闸。"""

    def __init__(self, global_limit: int, per_tenant_ratio: float = 0.3):
        self._global = asyncio.Semaphore(global_limit)
        self._per_tenant_limit = max(1, int(global_limit * per_tenant_ratio))
        self._tenant_sems: dict[str, asyncio.Semaphore] = {}

    def _tenant_sem(self, tenant_id: str) -> asyncio.Semaphore:
        if tenant_id not in self._tenant_sems:
            self._tenant_sems[tenant_id] = asyncio.Semaphore(self._per_tenant_limit)
        return self._tenant_sems[tenant_id]

    async def run(self, tenant_id: str, coro_fn, *args, **kwargs):
        tenant_sem = self._tenant_sem(tenant_id)
        if tenant_sem.locked() and tenant_sem._value == 0:
            raise BulkheadFullError(f"tenant {tenant_id} quota exhausted")
        async with tenant_sem:
            async with self._global:
                return await coro_fn(*args, **kwargs)
```

### 与配额内核联动

舱壁管**瞬态并发**，配额管**累计用量**，二者互补：

- 租户 A 并发 1 但每天调 100 万次 → 配额拦截
- 租户 B 并发 1000 但总量小 → 舱壁拦截

```python
async def guarded_llm_call(tenant_id: str, prompt: str):
    await quota_kernel.check_and_consume(tenant_id, cost=estimate_tokens(prompt))
    return await tenant_bulkhead.run(tenant_id, call_llm, prompt)
```

---

## 队列隔离与背压

任务执行内核为不同优先级任务建独立队列：

```python
QUEUES = {
    "interactive": asyncio.Queue(maxsize=100),   # 用户实时请求
    "batch":       asyncio.Queue(maxsize=10000), # 批处理
    "background":  asyncio.Queue(maxsize=50000), # 后台维护
}

async def enqueue(priority: str, task) -> None:
    queue = QUEUES[priority]
    if queue.full():
        # 背压：交互队列满了立即失败，后台队列满了丢弃最旧的
        if priority == "interactive":
            raise BulkheadFullError("interactive queue full")
        queue.get_nowait()  # 丢弃最旧
    await queue.put(task)
```

背压策略选择：

| 策略 | 适用 | 风险 |
|------|------|------|
| 快速失败 | 交互式请求 | 用户看到错误 |
| 丢弃最旧 | 遥测、日志类 | 丢数据 |
| 阻塞上游 | 流式管道 | 级联阻塞 |
| 降级处理 | 可降级业务 | 实现复杂度 |

---

## 配置示例

```yaml
# config/bulkheads.yaml
bulkheads:
  llm_calls:
    type: semaphore
    max_concurrent: 50
    max_wait_ms: 500
    timeout_s: 60
  db_writes:
    type: semaphore
    max_concurrent: 10
    max_wait_ms: 1000
  pdf_parsing:
    type: thread_pool
    max_workers: 4
    queue_size: 100

tenant_isolation:
  enabled: true
  per_tenant_ratio: 0.30        # 单租户最多占全局 30%
  premium_tenant_ratio: 0.50    # 付费租户更高
  exempt_tenants: ["system"]    # 系统租户不受限（慎用）

queues:
  interactive: { maxsize: 100,  on_full: reject }
  batch:       { maxsize: 10000, on_full: reject, workers: 8 }
  background:  { maxsize: 50000, on_full: drop_oldest, workers: 4 }

metrics:
  export: prometheus
  stats: [acquired, rejected, timeouts, inflight, queue_depth]
```

---

## 容量规划

舱壁大小的计算公式：

```
max_concurrent = 下游可持续 QPS × 平均调用时长(秒)
```

例：LLM 提供商限额 100 QPS，平均调用 2s → `max_concurrent = 200`。再打 20% 余量 = 240。

注意事项：

- **下游限额是账号级的**：多实例部署时，`max_concurrent` 要在实例间分配（全局 240 / 4 实例 = 每实例 60）
- **留头房间给管理流量**：健康检查、配置拉取走独立小舱壁，避免业务洪峰把管理面也压垮
- **队列长度 = 可容忍延迟**：队列 1000 × 处理 50ms/条 = 最坏 50s 排队，交互流量不可接受

---

## 最佳实践

1. **每个下游依赖一个舱壁**，禁止共享。共享池 = 没有舱壁。
2. **舱壁 + 熔断成对部署**：舱壁防资源耗尽，[熔断](circuit-breaker-advanced.md)防故障扩散；只用其一都会留缺口。
3. **快速失败优于排队**。舱壁等待上限设小（<1s），让压力尽快反馈给上游，配合重试在别处消化。
4. **监控 `rejected` 指标**。拒绝率持续 >1% 说明容量不足或下游变慢，先于用户投诉发现。
5. **管理面与数据面分离**。健康检查、metrics 抓取走独立连接/线程池。
6. **定期评审比例**。租户结构变化后，30% 的默认比例可能不再合理。
7. **压测验证**。对每个舱壁单独做突增压测，确认溢出行为符合设计（拒绝而非挂死）。

---

## 常见问题

### Q1: 舱壁满了之后的拒绝，上游怎么处理？

看业务：交互请求返回 503 + Retry-After，让客户端退避重试；消息消费场景利用 MQ 本身的重投；批处理任务回队列尾部。核心是**拒绝要可见**（指标 + 日志），静默拒绝等于丢数据。

### Q2: asyncio 里用了信号量还需要连接池限制吗？

需要。信号量限制"同时在跑的调用数"，连接器 `limit` 限制"同时打开的 TCP 连接数"。一个调用可能复用连接，也可能因超时重开连接。两者都要设，且通常连接数 ≥ 并发数。

### Q3: 线程池舱壁的线程数设多少？

CPU 密集：`CPU 核数`。IO 阻塞：`下游可持续 QPS × 单次调用秒数`，与信号量舱壁同一公式。不要盲目设 200——每个线程 8MB 栈，且 GIL 下线程切换有成本。

### Q4: 租户舱壁会不会造成资源浪费（大租户不够用、小租户闲置）？

会有一点，这是公平性的代价。缓解：per_tenant_ratio 只作上限而非预留（空闲租户的额度可被他人使用，仅在实际争抢时限制大租户）；对付费租户提高比例实现差异化服务。

### Q5: 如何发现"舱壁配置太保守"？

看三个指标：`inflight` 长期贴近上限、`rejected` > 1%、下游资源利用率 < 50%。三者同时成立说明舱壁是瓶颈而非保护，应扩容或调大。

---

**相关文档**：[熔断策略进阶](circuit-breaker-advanced.md) · [性能调优指南](../23-performance/performance-tuning.md) · [API 速率优化](../23-performance/api-rate-optimization.md)
