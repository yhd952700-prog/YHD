# 性能调优指南

本文档是 LiuHao AI OS 的性能调优总纲：从性能方法论出发，覆盖应用运行时（Python/asyncio）、LLM 调用链路、消息总线与系统级调优。专项主题另见：[缓存策略](caching-strategies.md)、[数据库优化](database-optimization.md)、[API 速率优化](api-rate-optimization.md)。

---

## 目录

- [性能方法论](#性能方法论)
- [性能预算与 SLO](#性能预算与-slo)
- [Python 运行时调优](#python-运行时调优)
- [asyncio 调优](#asyncio-调优)
- [LLM 调用链路优化](#llm-调用链路优化)
- [消息总线调优](#消息总线调优)
- [压测与性能回归](#压测与性能回归)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 性能方法论

**不要猜测，要测量。** 标准流程：

```
1. 定义 SLO ──▶ 2. 建立基线 ──▶ 3. 定位瓶颈 ──▶ 4. 优化 ──▶ 5. 验证回归
     ▲                                                            │
     └──────────────────────── 持续 ◀─────────────────────────────┘
```

### 定位瓶颈的工具矩阵

| 层 | 症状 | 工具 |
|----|------|------|
| 系统 | CPU/IO/内存饱和 | `top`、`iostat`、`vmstat`、eBPF |
| 进程 | CPU 热点 | py-spy（`py-spy top --pid X`） |
| 进程 | 内存增长 | tracemalloc、memray |
| asyncio | 事件循环阻塞 | `asyncio` debug 模式、aiomonitor |
| 请求链路 | 跨服务慢 | OpenTelemetry trace |
| 数据库 | 慢查询 | pg_stat_statements（见[数据库优化](database-optimization.md)） |

经验法则：**80% 的性能问题在 IO 等待**（数据库、LLM API、下游服务），而非 CPU。先看 trace 瀑布图再决定优化方向。

---

## 性能预算与 SLO

LiuHao AI OS 参考 SLO：

| 指标 | 目标 | 说明 |
|------|------|------|
| API P50 延迟 | < 100ms | 普通 CRUD |
| API P99 延迟 | < 800ms | 不含 LLM 调用 |
| 智能体任务首 token | < 3s | 用户感知的关键指标 |
| LLM 调用成功率 | ≥ 99.5% | 含降级链 |
| 记忆检索 P99 | < 200ms | 语义检索含向量查询 |
| 消息端到端延迟 P99 | < 500ms | 总线投递 |
| 任务吞吐 | ≥ 50 task/s/runtime pod | 基线容量 |

**首 token 时间（TTFT）是智能体系统最重要的体验指标**：用户愿意等流式输出 30 秒，但不能忍受 10 秒无任何响应。

---

## Python 运行时调优

### 解释器与依赖

```bash
# 使用最新稳定版 Python：3.12 比 3.10 快 10~25%（解释器优化）
python --version   # 目标 >= 3.12

# 关键环境变量
PYTHONOPTIMIZE=1          # 去掉 assert（生产）
MALLOC_ARENA_MAX=2        # 限制 glibc arena，内存回收更积极
PYTHONHASHSEED=random     # 安全默认
```

### JSON 序列化换 faster 实现

```python
# 标准库 json 是热点之一，换 orjson（快 5~10 倍）
import orjson

def dumps(obj) -> str:
    return orjson.dumps(obj).decode()

def loads(s: str):
    return orjson.loads(s)

# FastAPI 全局替换
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
app = FastAPI(default_response_class=ORJSONResponse)
```

### CPU 热点：批量化与 C 扩展

```python
# ❌ 慢：Python 层逐条处理
results = [transform(e) for e in events]

# ✅ 快：能下推给 C 扩展/数据库/向量库的操作不要留在 Python 层
results = vector_store.batch_transform(events)   # numpy/rust 实现
```

规则：**循环体超过 1000 次迭代的代码路径，都要问一遍"能否批量化/下推"**。

### 内存治理

```python
# tracemalloc 定位泄漏（staging 环境常驻）
import tracemalloc
tracemalloc.start(25)          # 保留 25 帧栈

# 定期快照对比
snapshot = tracemalloc.take_snapshot()
# ... 一段时间后
top = snapshot.compare_to(prev_snapshot, "lineno")
for stat in top[:10]:
    logger.warning("mem_growth", stat=str(stat))
```

常见内存杀手：无界缓存（用 `functools.lru_cache` 或 Redis 替代）、生成器未关闭、asyncio Task 引用未释放。

---

## asyncio 调优

### 事件循环阻塞检测

```python
import asyncio, logging

# 开发/staging 开启：慢回调 > 100ms 告警
loop = asyncio.get_event_loop()
loop.set_debug(True)
loop.slow_callback_duration = 0.1

# 生产：自定义监控协程
async def loop_lag_monitor():
    """事件循环延迟：实际醒来时间 - 期望醒来时间。"""
    while True:
        t0 = time.monotonic()
        await asyncio.sleep(1)
        lag = time.monotonic() - t0 - 1
        LOOP_LAG_GAUGE.set(lag)
        if lag > 0.5:
            logging.error("event loop lag %.2fs", lag)
```

### 消除阻塞的三种手段

```python
# 1. 同步阻塞 IO → 线程池
result = await asyncio.to_thread(legacy_sdk.fetch, key)

# 2. CPU 密集 → 进程池（绕 GIL）
from concurrent.futures import ProcessPoolExecutor
pool = ProcessPoolExecutor(max_workers=4)
result = await loop.run_in_executor(pool, heavy_parse, data)

# 3. 大循环主动让出
for i, item in enumerate(large_list):
    process(item)
    if i % 100 == 0:
        await asyncio.sleep(0)      # 让事件循环喘口气
```

### uvloop 与连接池

```python
# uvloop：Linux 下事件循环性能提升 2~4 倍
import uvloop
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

# 连接池尺寸公式：并发数 ≈ QPS × 平均耗时(秒)
import httpx
client = httpx.AsyncClient(
    limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
    timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=2.0),
    http2=True,                  # 多路复用，减少连接数
)
```

---

## LLM 调用链路优化

LLM 调用占智能体任务耗时的 70~90%，优化收益最大。

### 1. 减少调用次数

| 手段 | 收益 |
|------|------|
| [语义缓存](caching-strategies.md) | 重复/相似请求命中即省一次调用 |
| 合并提示 | 多个小判断合并为一次结构化输出调用 |
| 工具结果摘要 | 长工具输出先本地摘要再进上下文 |
| 提前退出 | 简单请求路由到小模型/规则引擎 |

### 2. 减少 token 数

```python
# 上下文预算管理：固定 system + 可变历史 + 检索片段
def build_prompt(system: str, history: list, retrieved: list, budget: int = 6000):
    tokens = count_tokens(system)
    kept_history, kept_docs = [], []
    # 优先保留检索片段（更相关），历史从新到旧填充
    for doc in retrieved:
        t = count_tokens(doc.text)
        if tokens + t > budget * 0.4: break      # 检索最多占 40%
        kept_docs.append(doc); tokens += t
    for msg in reversed(history):
        t = count_tokens(msg.content)
        if tokens + t > budget: break
        kept_history.insert(0, msg); tokens += t
    return render(system, kept_history, kept_docs)
```

### 3. 并行与流式

```python
# 独立的 LLM 调用并行化：规划 + 检索同时进行
plan_task = asyncio.create_task(llm.plan(goal))
docs_task = asyncio.create_task(memory.retrieve(goal))
plan, docs = await asyncio.gather(plan_task, docs_task)

# 流式输出：TTFT 从 8s 降到 1.5s（用户感知）
async for chunk in llm.stream(prompt):
    yield chunk                      # 边生成边推给客户端
```

### 4. 模型路由降本

```python
ROUTE_TABLE = [
    (lambda r: r.estimated_tokens < 500 and r.task_type == "classify", "gpt-5-nano"),
    (lambda r: r.task_type in ("summarize", "extract"),                "gpt-5-mini"),
    (lambda r: True,                                                    "gpt-5"),
]
# 实测：分类/抽取类请求路由到小模型，成本降 60~80%，质量损失 < 2%
```

---

## 消息总线调优

### NATS JetStream 参考配置

```
# 流配置：按主题拆分，工作队列模式
stream: EVENTS
  subjects: ["events.>"]
  retention: limits
  max_age: 72h
  storage: file                # file 比 memory 慢但可持久
  replicas: 3

consumer: agent-dispatch
  filter_subject: "events.tasks.>"
  ack_wait: 60s                # 处理慢就调大，防重复投递
  max_deliver: 5
  max_ack_pending: 1000        # 流控：未确认上限
```

### 消费端优化

```python
# 批量拉取 + 并发处理 + 批量确认
async def consume():
    sub = await js.pull_subscribe("events.tasks.>", durable="agent-dispatch")
    while True:
        msgs = await sub.fetch(batch=100, timeout=1)
        sem = asyncio.Semaphore(50)             # 舱壁：处理并发上限
        async def handle(m):
            async with sem:
                await process(m)
                await m.ack()
        await asyncio.gather(*(handle(m) for m in msgs))
```

---

## 压测与性能回归

### 压测工具选型

| 工具 | 场景 |
|------|------|
| k6 | HTTP API 压测（JS 脚本，推荐） |
| locust | 复杂用户行为模拟（Python） |
| 自研回放 | LLM 调用回放（录制的真实 prompt） |

### k6 脚本示例

```javascript
// perf/api-smoke.js
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  stages: [
    { duration: '2m', target: 50 },    // 爬坡
    { duration: '5m', target: 50 },    // 稳态
    { duration: '1m', target: 200 },   // 突发
    { duration: '2m', target: 0 },     // 回落
  ],
  thresholds: {
    http_req_duration: ['p(99)<800'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const res = http.post(`${__ENV.BASE_URL}/api/v1/agents`,
    JSON.stringify({ name: `perf-${__VU}-${__ITER}`, role: 'general' }),
    { headers: { 'Content-Type': 'application/json',
                 'Authorization': `Bearer ${__ENV.TOKEN}` } });
  check(res, { 'status 201': (r) => r.status === 201 });
}
```

### 性能回归门禁（CI）

```yaml
# 每次 main 合并后跑基准，与基线对比
- name: Benchmark
  run: |
    pytest tests/perf/bench --benchmark-json=bench.json
    python scripts/compare_bench.py bench.json baseline.json \
      --max-regression 0.10          # 任何指标劣化 >10% 失败
```

---

## 配置示例

```yaml
# config/performance.yaml
runtime:
  python:
    malloc_arena_max: 2
  uvloop: true
  json_backend: orjson

asyncio:
  slow_callback_duration_ms: 100
  loop_lag_alert_s: 0.5

http_client:
  max_connections: 200
  max_keepalive: 50
  http2: true
  timeouts: { connect: 5, read: 60, write: 10, pool: 2 }

llm:
  context_token_budget: 6000
  retrieval_budget_ratio: 0.4
  stream: true
  route_small_model_tasks: [classify, extract, summarize]
  parallel_plan_and_retrieve: true

nats:
  consumer:
    batch_size: 100
    max_ack_pending: 1000
    ack_wait_s: 60
    max_processing_concurrency: 50
```

---

## 最佳实践

1. **先 SLO 后优化**：没有 SLO 的优化是盲目的；优化效果用 SLO 指标验证。
2. **每次只改一个变量**：同时改三处无法归因；基准测试前后对比。
3. **优化 IO 优先于 CPU**：加索引、加缓存、批量化的收益通常远大于重写算法。
4. **TTFT 优先于总时延**：智能体场景中，尽快流式吐第一个 token 比总耗时更重要。
5. **性能回归进 CI**：没有门禁的性能会在三个月内悄悄退化回去。
6. **留余量**：生产持续负载不超过容量的 60%，突发与 GC/重平衡需要空间。
7. **读代码不如看 trace**：每周抽查 10 条最慢 trace，比任何代码评审都更快找到真问题。

---

## 常见问题

### Q1: API 白天慢、夜里快，瓶颈在哪？

典型的共享资源争抢：数据库连接池打满、LLM 提供商限流、或同节点邻居 Pod 吵闹。看 trace 瀑布中哪一段随时间波动——波动段就是瓶颈。

### Q2: asyncio 程序 CPU 100% 但 QPS 上不去？

单事件循环只吃一个核。方案：多进程（gunicorn/uvicorn workers = CPU 核数）、CPU 密集下推进程池、或换 uvloop。先 py-spy 确认 CPU 确实在业务而非序列化。

### Q3: 内存随时间缓慢上涨，OOM 周期约一周？

大概率是缓慢泄漏：无界 dict 缓存、未关闭的生成器/asyncio Task、或 C 扩展碎片。tracemalloc 快照对比 + `MALLOC_ARENA_MAX=2`；短期止血可配 Pod 内存 limit + 滚动重启策略。

### Q4: LLM 调用 P99 很高但 P50 正常？

长尾通常来自：提供商排队（换提供商/加并发）、超大 prompt（上下文预算失控）、重试风暴（检查重试退避）。按 prompt 大小分桶统计延迟即可定位。

### Q5: 调优后压测很好，生产还是慢？

压测与生产的差异：数据分布（生产缓存命中率低）、冷启动、真实 LLM 延迟（压测多用 mock）。用录制的真实流量回放 + 生产小流量灰度验证。

---

**相关文档**：[缓存策略](caching-strategies.md) · [数据库优化](database-optimization.md) · [API 速率优化](api-rate-optimization.md) · [监控栈](../21-deployment/monitoring-stack.md)
