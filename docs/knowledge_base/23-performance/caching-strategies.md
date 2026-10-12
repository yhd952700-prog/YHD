# 缓存策略

缓存是智能体系统性价比最高的优化手段：一次 LLM 调用成本是 Redis 查询的一万倍。本文档覆盖 LiuHao AI OS 的多级缓存体系：语义缓存（LLM 结果）、检索缓存、读模型缓存与会话缓存，以及失效策略、穿透/击穿/雪崩防护。

---

## 目录

- [缓存全景](#缓存全景)
- [语义缓存（LLM 结果缓存）](#语义缓存llm-结果缓存)
- [精确缓存与多级架构](#精确缓存与多级架构)
- [失效策略](#失效策略)
- [三大缓存陷阱防护](#三大缓存陷阱防护)
- [Python 实现示例](#python-实现示例)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 缓存全景

| 缓存层 | 内容 | 存储 | 命中率目标 | 收益 |
|--------|------|------|-----------|------|
| L1 进程内 | 配置、模型路由表、schema | 内存 | >99% | 微秒级 |
| L2 分布式 | 读模型、会话、检索结果 | Redis | 60~90% | 毫秒级 |
| L3 语义缓存 | LLM 提示→响应 | Redis + 向量索引 | 10~40% | 省整次 LLM 调用 |

缓存决策树：

```
请求进来
  ├─ 精确匹配 L1/L2？ ──命中──▶ 返回（<5ms）
  ├─ 语义相似 L3？ ──相似度>阈值──▶ 返回（<50ms，省 $0.01~0.1）
  └─ 未命中 ──▶ 调 LLM ──▶ 写回 L2/L3 ──▶ 返回
```

---

## 语义缓存（LLM 结果缓存）

智能体系统特有：用户问题千变万化，但**语义重复**很多（"怎么重置密码" vs "密码忘了怎么办"）。

### 工作原理

```python
import hashlib
import numpy as np

class SemanticCache:
    def __init__(self, redis, embedder, threshold: float = 0.95):
        self._redis = redis
        self._embed = embedder
        self._threshold = threshold

    async def get(self, prompt: str, model: str) -> str | None:
        # 1. 先精确匹配（免费午餐）
        exact_key = f"llm:exact:{model}:{hashlib.sha256(prompt.encode()).hexdigest()}"
        if hit := await self._redis.get(exact_key):
            return hit

        # 2. 语义匹配：向量检索最近邻
        vec = await self._embed(prompt)
        candidates = await self._redis.ft("llm_semantic_idx").search(
            f"*=>[KNN 1 @embedding $vec AS score]",
            query_params={"vec": vec.tobytes()})
        if candidates and candidates[0].score >= self._threshold:
            SEMANTIC_HITS.inc()
            return await self._redis.get(candidates[0].response_key)
        return None

    async def set(self, prompt: str, model: str, response: str,
                  ttl_s: int = 86400) -> None:
        exact_key = f"llm:exact:{model}:{hashlib.sha256(prompt.encode()).hexdigest()}"
        await self._redis.set(exact_key, response, ex=ttl_s)
        vec = await self._embed(prompt)
        await self._redis.hset(f"llm:sem:{uuid4().hex}", mapping={
            "embedding": vec.tobytes(), "response_key": exact_key,
            "model": model, "created_at": time.time()})
```

### 使用边界（关键！）

语义缓存**不是万能的**，以下场景禁用：

| 场景 | 原因 |
|------|------|
| 含实时数据的提示（"今天的股价"） | 缓存结果必然过期 |
| 含用户私有数据的提示 | 跨用户泄露风险 |
| 创造性任务（温度 > 0.7） | 用户期待多样性 |
| 工具调用链中间结果 | 外部状态已变 |

```python
def cacheable(request) -> bool:
    return (
        request.temperature <= 0.3
        and not request.has_tool_calls
        and not contains_realtime_markers(request.prompt)   # 今天/最新/current
        and request.scope == "shared"                        # 非私有上下文
    )
```

阈值调优：0.95 是保守起点；降到 0.90 命中率翻倍但错误响应率上升，需配合人工抽检。

---

## 精确缓存与多级架构

### 进程内缓存（L1）

```python
from cachetools import TTLCache

# 模型路由表：变化极少、读取极频繁
_route_cache: TTLCache = TTLCache(maxsize=1000, ttl=60)

async def get_route(task_type: str) -> Route:
    if route := _route_cache.get(task_type):
        return route
    route = await db.fetch_route(task_type)
    _route_cache[task_type] = route
    return route
```

L1 要点：**小、短 TTL、进程间不一致可接受**。多实例部署时 L1 失效靠短 TTL 收敛，不做跨进程广播（复杂度不值）。

### Redis 读模型缓存（L2）

```python
async def get_agent_dashboard(agent_id: str) -> dict:
    key = f"dash:agent:{agent_id}"
    if cached := await redis.get(key):
        return orjson.loads(cached)
    data = await build_dashboard_from_db(agent_id)   # 回源
    await redis.set(key, orjson.dumps(data), ex=300)
    return data
```

### 缓存 Aside vs Read-Through

| 模式 | 说明 | 适用 |
|------|------|------|
| Cache-Aside（上文示例） | 应用负责读缓存+回源+写缓存 | 大多数场景，简单可控 |
| Read-Through | 缓存层自己回源 | 多语言共享缓存时 |
| Write-Behind | 写缓存异步刷库 | 高写低读、可容忍丢失（计数器类） |

LiuHao AI OS 默认 Cache-Aside。

---

## 失效策略

### TTL 设计

| 数据 | TTL | 理由 |
|------|-----|------|
| LLM 精确缓存 | 24h | 内容稳定，模型升级时整体清 |
| LLM 语义缓存 | 24h | 同上 |
| 检索结果 | 5~15min | 记忆库持续写入 |
| 仪表盘读模型 | 5min | 监控容忍分钟级延迟 |
| 路由/配置 | 60s | 变更需快速生效 |

### 主动失效

事件驱动失效优于纯 TTL：

```python
@bus.subscribe("MemoryStored")
async def invalidate_retrieval_cache(event: dict) -> None:
    # 新记忆写入 → 该智能体的检索缓存作废
    await redis.delete(f"search:{event['agent_id']}:*")

@bus.subscribe("ModelRouteChanged")
async def bump_route_version(event: dict) -> None:
    # 版本号键：批量失效的优雅方式
    await redis.incr("version:routes")
    # 缓存键含版本号 f"route:{ver}:{type}"，旧键自然过期
```

**版本号键模式**适合批量失效：不用扫删成千上万个键，递增一个版本号即全体作废。

---

## 三大缓存陷阱防护

### 缓存穿透（查询不存在的数据）

```python
# 方案：空值也缓存（短 TTL）
async def get_agent(agent_id: str) -> dict | None:
    key = f"agent:{agent_id}"
    cached = await redis.get(key)
    if cached == b"__NULL__":
        return None
    if cached:
        return orjson.loads(cached)
    data = await db.fetch_agent(agent_id)
    if data is None:
        await redis.set(key, "__NULL__", ex=60)      # 空值短 TTL
    else:
        await redis.set(key, orjson.dumps(data), ex=300)
    return data
```

### 缓存击穿（热点键过期瞬间被打爆）

```python
# 方案：单飞（singleflight）——同一 key 只放一个回源请求
class SingleFlight:
    def __init__(self):
        self._inflight: dict[str, asyncio.Task] = {}

    async def do(self, key: str, fn) -> any:
        if key in self._inflight:
            return await self._inflight[key]         # 搭便车
        task = asyncio.create_task(fn())
        self._inflight[key] = task
        try:
            return await task
        finally:
            self._inflight.pop(key, None)

sf = SingleFlight()
data = await sf.do(f"rebuild:{key}", lambda: build_from_db(key))
```

### 缓存雪崩（大量键同时过期）

```python
# 方案：TTL 加随机抖动
ttl = base_ttl * (0.8 + 0.4 * random.random())       # ±20% 抖动
await redis.set(key, value, ex=int(ttl))

# 热点键永不过期 + 后台异步刷新
async def refresh_loop():
    for key in await redis.smembers("hot_keys"):
        data = await build(key)
        await redis.set(key, data, keepttl=True)     # 永不过期，只更新值
        await asyncio.sleep(0.1)
```

---

## 配置示例

```yaml
# config/caching.yaml
caching:
  l1:
    enabled: true
    max_size: 1000
    ttl_s: 60

  l2:
    backend: redis
    key_prefix: "liuhao:v2:"          # 版本号前缀，升级时换 v3
    default_ttl_s: 300
    null_cache_ttl_s: 60
    ttl_jitter: 0.2

  semantic:
    enabled: true
    threshold: 0.95
    ttl_s: 86400
    max_entries: 100000
    excluded_task_types: [creative, realtime]
    require_temperature_below: 0.3

  protections:
    singleflight: true
    hot_key_refresh_interval_s: 120
    versioned_keys: [routes, policies, schemas]

  invalidation:
    event_driven:
      MemoryStored: ["search:{agent_id}:*"]
      PolicyUpdated: ["policy:*"]
      ModelRouteChanged: ["version:routes"]
```

---

## 最佳实践

1. **命中率先行**：先上指标（`cache_hits/cache_misses`），命中率 <30% 的缓存层要么键设计错了，要么根本不该存在。
2. **缓存是优化不是依赖**：回源路径必须始终可用，缓存挂了只是变慢不是变挂。
3. **语义缓存先小规模验证**：从只读查询类智能体开始，人工抽检 1% 命中结果的正确性。
4. **私有数据不共享缓存**：按 `tenant_id` 隔离键空间，或干脆禁用。
5. **键名带版本**：`liuhao:v2:...`，序列化结构或逻辑升级时换版本，旧键自然过期。
6. **大对象压缩**：>10KB 的值 zstd 压缩，Redis 内存减半。
7. **模型升级清语义缓存**：`gpt-5` 换成 `gpt-5.1` 后，旧模型的缓存响应应整体作废（键含模型名则天然隔离）。

---

## 常见问题

### Q1: 语义缓存返回了"过时但语义相似"的答案怎么办？

阈值调高（0.95→0.97）、`contains_realtime_markers` 规则扩充、TTL 缩短。并接受它是概率性优化：配合"答案可能来自缓存"的 UI 标识与一键重试按钮。

### Q2: Redis 内存不够用？

优先级排序：语义缓存（最大头，可裁）> 检索缓存 > 读模型。手段：`maxmemory-policy allkeys-lru`、大值压缩、语义缓存条目上限、低命中键型下线。

### Q3: 多实例部署 L1 不一致导致脏数据？

L1 只放"短暂不一致可接受"的数据（配置、路由）。要求强一致的读走 L2 或回源。L1 TTL ≤ 60s。

### Q4: 缓存回源把数据库打挂了？

单飞 + 舱壁：回源并发限制（如 10 并发），其余请求等待或返回降级内容。监控 `cache_miss` 突增告警——雪崩前兆。

### Q5: 如何评估语义缓存的收益？

指标三板斧：命中率（目标 10~40%）、节省成本（命中次数 × 平均调用成本）、错误命中投诉率（人工抽检）。收益 = 节省成本 − 错误命中造成的返工成本，通常 ROI > 10。

---

**相关文档**：[性能调优](performance-tuning.md) · [API 速率优化](api-rate-optimization.md) · [数据库优化](database-optimization.md)
