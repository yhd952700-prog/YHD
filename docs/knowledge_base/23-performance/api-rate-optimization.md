# API 速率优化

LiuHao AI OS 对外是 API 提供者（被第三方调用），对内是 API 消费者（调 LLM 提供商、Slack、GitHub）。本文档从两个方向讲速率优化：作为提供者如何限流防滥用，作为消费者如何在配额内榨取最大吞吐。

---

## 目录

- [双向视角](#双向视角)
- [限流算法选型](#限流算法选型)
- [作为提供者：限流实现](#作为提供者限流实现)
- [作为消费者：客户端优化](#作为消费者客户端优化)
- [LLM 提供商配额最大化](#llm-提供商配额最大化)
- [Python 实现示例](#python-实现示例)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 双向视角

| 角色 | 目标 | 手段 |
|------|------|------|
| 提供者（别人调我们） | 保护系统、租户公平、防滥用 | 限流、配额、排队、熔断下游 |
| 消费者（我们调别人） | 配额内最大吞吐、避免 429 | 速率整形、退避、并发控制、缓存 |

两个方向共用一套原语：令牌桶、滑动窗口、指数退避、并发舱壁。

---

## 限流算法选型

| 算法 | 特点 | 适用 |
|------|------|------|
| 固定窗口 | 简单；边界双倍突发 | 粗粒度配额（每日调用数） |
| 滑动窗口日志 | 精确；内存大 | 小规模精确限流 |
| 滑动窗口计数 | 近似精确；内存小 | **API 限流主力** |
| 令牌桶 | 允许突发、平滑 | 入口限流、LLM 客户端整形 |
| 漏桶 | 严格匀速 | 下游要求恒定速率时 |

选型经验：**入口用令牌桶（容忍合理突发），配额用滑动窗口计数，出向调用用令牌桶整形**。

---

## 作为提供者：限流实现

### 分层限流

```
L1 全局限流（每 API key）：   600 req/min    —— 防滥用
L2 端点限流（昂贵端点）：     /agents/run 60 req/min
L3 并发限流（舱壁）：         每 key 20 并发  —— 防单 key 占满
L4 成本配额（每日）：         $100/day       —— 防账单爆炸
```

### 令牌桶（Redis + Lua，原子操作）

```lua
-- rate_limit.lua：令牌桶，原子取令牌
-- KEYS[1]=key, ARGV=capacity, refill_rate/s, requested, now
local capacity   = tonumber(ARGV[1])
local refill     = tonumber(ARGV[2])
local requested  = tonumber(ARGV[3])
local now        = tonumber(ARGV[4])

local bucket = redis.call("HMGET", KEYS[1], "tokens", "ts")
local tokens = tonumber(bucket[1]) or capacity
local ts     = tonumber(bucket[2]) or now

tokens = math.min(capacity, tokens + (now - ts) * refill)
if tokens < requested then
  redis.call("HMSET", KEYS[1], "tokens", tokens, "ts", now)
  redis.call("EXPIRE", KEYS[1], math.ceil(capacity / refill))
  return {0, math.ceil((requested - tokens) / refill)}   -- 拒绝 + 重试秒数
end
tokens = tokens - requested
redis.call("HMSET", KEYS[1], "tokens", tokens, "ts", now)
redis.call("EXPIRE", KEYS[1], math.ceil(capacity / refill))
return {1, 0}                                            -- 放行
```

```python
class RedisTokenBucket:
    def __init__(self, redis):
        self._script = redis.register_script(RATE_LIMIT_LUA)

    async def acquire(self, key: str, capacity: int, refill_per_s: float,
                      tokens: int = 1) -> tuple[bool, int]:
        allowed, retry_after = await self._script(
            keys=[f"rl:{key}"],
            args=[capacity, refill_per_s, tokens, time.time()])
        return bool(allowed), retry_after
```

### FastAPI 中间件

```python
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    api_key = request.headers.get("X-API-Key", "anonymous")
    plan = await plans.get(api_key)                        # free/pro/enterprise

    allowed, retry_after = await bucket.acquire(
        key=f"api:{api_key}",
        capacity=plan.rpm, refill_per_s=plan.rpm / 60)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limit_exceeded",
                     "retry_after": retry_after},
            headers={"Retry-After": str(retry_after),
                     "X-RateLimit-Limit": str(plan.rpm),
                     "X-RateLimit-Remaining": "0"})

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(plan.rpm)
    return response
```

**429 响应必须带 `Retry-After`**——这是客户端友好性的分水岭。

---

## 作为消费者：客户端优化

### 指数退避 + 抖动（Jitter）

```python
async def call_with_backoff(fn, max_retries: int = 5):
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except RateLimitError as e:
            if attempt == max_retries:
                raise
            # 尊重服务端 Retry-After，否则指数退避
            base = e.retry_after or min(0.5 * 2 ** attempt, 30)
            # Full Jitter：防止多客户端同步重试形成雷鸣群
            await asyncio.sleep(random.uniform(0, base))
        except TransientError:
            if attempt == max_retries:
                raise
            await asyncio.sleep(random.uniform(0, 0.5 * 2 ** attempt))
```

为什么要抖动：100 个客户端同时被 429，若都严格按 1s/2s/4s 退避，会在同一时刻再次齐射——抖动把重试打散。

### 尊重 Retry-After 的自适应整形

```python
class AdaptiveRateLimiter:
    """探测到 429 后主动降速，稳定后缓慢回升（AIMD：加性增、乘性减）。"""

    def __init__(self, initial_rps: float = 50):
        self._rps = initial_rps
        self._max_rps = initial_rps * 2
        self._sem = asyncio.Semaphore(1)

    async def acquire(self) -> None:
        await asyncio.sleep(1 / self._rps)      # 简化：匀速间隔

    def on_429(self) -> None:
        self._rps = max(self._rps * 0.5, 1)     # 乘性减半

    def on_success(self) -> None:
        self._rps = min(self._rps + 0.5, self._max_rps)  # 加性恢复
```

---

## LLM 提供商配额最大化

LLM API 的限流通常是**双维度**：RPM（每分钟请求数）+ TPM（每分钟 token 数）。优化策略：

### 1. Token 感知调度

```python
class TokenAwareScheduler:
    """按估算 token 数扣配额，大请求给配额让路。"""

    def __init__(self, rpm: int, tpm: int):
        self._req_bucket = TokenBucket(capacity=rpm, refill=rpm / 60)
        self._tok_bucket = TokenBucket(capacity=tpm, refill=tpm / 60)

    async def schedule(self, request) -> None:
        est_tokens = estimate_tokens(request.prompt) + request.max_output
        await self._req_bucket.acquire(1)
        await self._tok_bucket.acquire(est_tokens)   # 大请求自动排队
```

### 2. 批量与合并

```python
# 多个分类请求合并为一次调用：RPM 不变，吞吐 ×10
async def batch_classify(texts: list[str]) -> list[str]:
    prompt = "对以下文本逐条分类，返回 JSON 数组：\n" + "\n".join(
        f"{i+1}. {t}" for i, t in enumerate(texts))
    resp = await llm.complete(prompt, response_format={"type": "json_object"})
    return parse_labels(resp)
```

### 3. 多提供商容量池

```python
# 配额即容量：各提供商独立桶，路由选余量最大的
async def routed_complete(prompt: str):
    provider = min(providers, key=lambda p: p.quota_utilization())
    return await provider.complete(prompt)
```

### 4. 低峰预热与高峰降级

- 高峰来临前（可预测的业务节奏）把[语义缓存](caching-strategies.md)预热
- 配额水位 > 80% 时：非关键任务（报告生成、记忆整理）自动降级到小模型或延迟执行

---

## 配置示例

```yaml
# config/rate_limits.yaml
inbound:                              # 我们作为提供者
  plans:
    free:       { rpm: 60,   concurrency: 2,  daily_cost_quota_usd: 1 }
    pro:        { rpm: 600,  concurrency: 20, daily_cost_quota_usd: 100 }
    enterprise: { rpm: 3000, concurrency: 100, daily_cost_quota_usd: 1000 }
  endpoint_overrides:
    "POST /api/v1/agents/run": { rpm_multiplier: 0.1 }   # 昂贵端点收紧
  response:
    include_retry_after: true
    include_rate_headers: true

outbound:                             # 我们作为消费者
  llm_providers:
    provider-a:
      rpm: 500
      tpm: 200000
      adaptive: true                 # AIMD 自适应
      retry: { max: 5, base_ms: 500, max_s: 30, jitter: full }
    provider-b:
      rpm: 200
      tpm: 100000
      use_as_overflow: true          # 主提供商打满后启用

  integrations:
    slack:  { rps: 1,  burst: 5 }    # Slack 特殊限流：每频道每秒
    github: { rpm: 4000, pause_below_remaining: 100 }

degradation:
  quota_watermark: 0.8
  actions:
    - defer_background_tasks
    - route_to_smaller_models
    - warm_semantic_cache
```

---

## 最佳实践

1. **429 是信号不是错误**：客户端必须把 429 + Retry-After 当作正常流量调度信号，而非失败重试。
2. **限流值公开透明**：文档写明各套餐限额，响应头带 `X-RateLimit-*`，让调用方能自我整形。
3. **退避必加抖动**：没有 jitter 的指数退避在生产等于同步重试风暴。
4. **配额多维**：仅 RPM 不够，昂贵操作（LLM 调用、大查询）按成本/token 计费。
5. **突发要区分**：正常业务突发（令牌桶放行）vs 滥用扫描（行为分析封禁）——限流不替代 WAF。
6. **客户端配额仪表盘**： outbound 各提供商的余量、429 频率上监控，配额快满提前申请提额。
7. **降级链常备**：打满主提供商前，降级路径必须已经过演练（见[熔断策略](../20-advanced-patterns/circuit-breaker-advanced.md)）。

---

## 常见问题

### Q1: 限流放在网关还是应用？

两层都要：网关（nginx/Envoy）做粗粒度全局限流（防 DDoS、每 IP），应用做业务粒度限流（每租户/每端点/成本配额）。网关不知道业务语义，应用扛不住流量洪峰。

### Q2: 分布式限流用 Redis 会不会成为瓶颈/单点？

单次限流检查 ~0.5ms，Lua 脚本原子执行，10 万 QPS 单 Redis 实例可扛。可用性：Redis 挂了时限流器**降级放行**（fail-open）并告警——限流是为了保护系统，不能反过来成为故障点。

### Q3: 如何给用户解释"为什么被限流了"？

429 响应体带机器可读详情：`{"error": "rate_limit_exceeded", "limit": 600, "window": "1m", "retry_after": 12, "docs": "https://..."}`。仪表盘展示该 key 的用量曲线，让用户自助排查。

### Q4: WebSocket/流式连接怎么限流？

按"消息条数 + 连接数"双限：每 key 最多 N 个并发连接，每连接消息 M 条/秒。流式 LLM 输出按 token 计费配额，超配额即中断流并返回明确错误帧。

### Q5: 定时任务整点触发把 API 打爆（整点效应）？

客户端侧：cron 加随机分钟偏移（`7 * * * *` 而非 `0 * * * *`）；服务端侧：令牌桶天然吸收突发；平台侧：对托管定时任务强制抖动窗口。

---

**相关文档**：[性能调优](performance-tuning.md) · [缓存策略](caching-strategies.md) · [舱壁隔离](../20-advanced-patterns/bulkhead-advanced.md) · [熔断策略](../20-advanced-patterns/circuit-breaker-advanced.md)
