# 高级熔断策略（Circuit Breaker Advanced）

熔断器防止故障在分布式系统中级联扩散：当下游依赖（LLM 提供商、数据库、外部 API）持续失败时，熔断器"跳闸"快速失败，给下游恢复时间。本文档讨论超越基础三状态模型的高级策略：慢调用熔断、自适应阈值、半开试探、分层熔断与降级编排。LiuHao AI OS 的模型网关内核依赖这些策略保障多模型路由的稳定性。

---

## 目录

- [基础回顾](#基础回顾)
- [高级熔断维度](#高级熔断维度)
- [Python 实现示例](#python-实现示例)
- [自适应阈值](#自适应阈值)
- [分层熔断与降级编排](#分层熔断与降级编排)
- [配置示例](#配置示例)
- [观测与告警](#观测与告警)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 基础回顾

经典三状态：

```
        失败率超阈值              冷却时间到
CLOSED ──────────────▶ OPEN ──────────────▶ HALF_OPEN
  ▲                                               │
  │            试探成功                    试探失败 │
  └───────────────────────┬───────────────────────┘
                          ▼
                        OPEN
```

- **CLOSED**：正常放行，统计失败率
- **OPEN**：直接拒绝（快速失败），进入冷却
- **HALF_OPEN**：放少量试探请求，成功则闭合，失败则重新断开

基础模型的不足：只看"失败/成功"二元结果、阈值固定、半开期无流量整形。高级策略逐一补齐。

---

## 高级熔断维度

### 1. 慢调用熔断

LLM 调用最常见的问题不是报错，而是**变慢**（排队、限流）。P99 从 2s 涨到 30s 时，等待的线程池会先于错误率指标耗尽：

```python
@dataclass
class CircuitConfig:
    failure_rate_threshold: float = 0.5      # 错误率阈值
    slow_call_duration_ms: int = 10_000      # 超过即为慢调用
    slow_call_rate_threshold: float = 0.8    # 慢调用比例阈值
    sliding_window_size: int = 100           # 统计窗口
    min_calls: int = 20                      # 窗口内最小样本数
```

### 2. 滑动窗口统计

用环形缓冲区维护最近 N 次调用结果，避免计数器突刺：

- **计数窗口**：实现简单，边界处有突刺
- **时间窗口**：按秒分桶，平滑但内存略高
- 推荐：小流量服务用计数窗口（100 次），大流量用时间窗口（60s × 10 桶）

### 3. 按异常类型区分

并非所有失败都应熔断：

| 异常 | 计入熔断 | 理由 |
|------|---------|------|
| 下游 5xx、超时、连接拒绝 | ✅ | 下游故障 |
| 429 限流 | ✅（单独计数） | 下游容量问题，需退避 |
| 下游 4xx（参数错误） | ❌ | 调用方 bug，熔断无意义 |
| 本地校验失败 | ❌ | 与下游健康无关 |

---

## Python 实现示例

### 高级熔断器

```python
from __future__ import annotations

import enum
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable


class State(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """熔断器断开，调用被快速拒绝。"""


@dataclass
class CircuitConfig:
    failure_rate_threshold: float = 0.5
    slow_call_duration_ms: int = 10_000
    slow_call_rate_threshold: float = 0.8
    sliding_window_size: int = 100
    min_calls: int = 20
    open_cooldown_s: float = 30.0
    half_open_max_calls: int = 5             # 半开期并发试探数
    half_open_success_threshold: int = 3     # 连续成功 N 次才闭合
    ignored_exceptions: tuple[type, ...] = ()


class CircuitBreaker:
    def __init__(self, name: str, config: CircuitConfig):
        self.name = name
        self.cfg = config
        self._state = State.CLOSED
        self._opened_at = 0.0
        self._window: deque[bool] = deque(maxlen=config.sliding_window_size)
        self._slow_flags: deque[bool] = deque(maxlen=config.sliding_window_size)
        self._half_open_inflight = 0
        self._half_open_consecutive_ok = 0
        self._lock = threading.Lock()

    # ---------- 主入口 ----------
    def call(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        self._before_call()
        start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except self.cfg.ignored_exceptions:
            raise  # 不计入统计
        except Exception:
            self._after_call(start, ok=False)
            raise
        self._after_call(start, ok=True)
        return result

    # ---------- 状态机 ----------
    def _before_call(self) -> None:
        with self._lock:
            if self._state == State.OPEN:
                if time.monotonic() - self._opened_at >= self.cfg.open_cooldown_s:
                    self._state = State.HALF_OPEN
                    self._half_open_inflight = 0
                    self._half_open_consecutive_ok = 0
                else:
                    raise CircuitOpenError(
                        f"circuit {self.name} is OPEN, "
                        f"retry in {self.cfg.open_cooldown_s - (time.monotonic() - self._opened_at):.1f}s")
            if self._state == State.HALF_OPEN:
                if self._half_open_inflight >= self.cfg.half_open_max_calls:
                    raise CircuitOpenError(f"circuit {self.name} half-open probe limit")
                self._half_open_inflight += 1

    def _after_call(self, start: float, ok: bool) -> None:
        elapsed_ms = (time.monotonic() - start) * 1000
        slow = elapsed_ms >= self.cfg.slow_call_duration_ms
        with self._lock:
            if self._state == State.HALF_OPEN:
                self._half_open_inflight -= 1
                if ok and not slow:
                    self._half_open_consecutive_ok += 1
                    if self._half_open_consecutive_ok >= self.cfg.half_open_success_threshold:
                        self._transition(State.CLOSED)
                else:
                    self._transition(State.OPEN)
                return
            self._window.append(ok)
            self._slow_flags.append(slow)
            if len(self._window) >= self.cfg.min_calls:
                failure_rate = 1 - sum(self._window) / len(self._window)
                slow_rate = sum(self._slow_flags) / len(self._slow_flags)
                if (failure_rate >= self.cfg.failure_rate_threshold
                        or slow_rate >= self.cfg.slow_call_rate_threshold):
                    self._transition(State.OPEN)

    def _transition(self, new_state: State) -> None:
        if new_state == State.OPEN:
            self._opened_at = time.monotonic()
        if new_state == State.CLOSED:
            self._window.clear()
            self._slow_flags.clear()
        self._state = new_state

    @property
    def state(self) -> State:
        return self._state
```

### 与重试组合

熔断与重试必须协同，否则重试会放大故障：

```python
def resilient_call(fn, breaker: CircuitBreaker, max_retries: int = 2):
    for attempt in range(max_retries + 1):
        try:
            return breaker.call(fn)
        except CircuitOpenError:
            raise                        # 熔断不重试，立即降级
        except TransientError:
            if attempt == max_retries:
                raise
            time.sleep(0.2 * 2 ** attempt)  # 仅瞬时错误重试
```

---

## 自适应阈值

固定阈值在流量低谷（如凌晨 5 次调用失败 3 次）会误触发。改进策略：

### 1. 最小样本量门槛

窗口内调用数 < `min_calls` 时不评估，直接放行（上例已实现）。

### 2. 基线偏离检测

以下游历史 P99 延迟为基线，当前窗口 P99 超过基线 2 倍即视为慢调用，而非固定毫秒数：

```python
class AdaptiveSlowCallDetector:
    def __init__(self, baseline_window: int = 1000):
        self._latencies: deque[float] = deque(maxlen=baseline_window)

    def record(self, latency_ms: float) -> bool:
        if len(self._latencies) < 100:
            self._latencies.append(latency_ms)
            return latency_ms > 30_000          # 冷启动期用兜底阈值
        baseline = sorted(self._latencies)[int(len(self._latencies) * 0.99)]
        self._latencies.append(latency_ms)
        return latency_ms > max(baseline * 2, 1000)
```

### 3. 分时段阈值

模型网关在不同时间段对同一提供商配置不同阈值（高峰期容忍更高失败率，避免集体切走造成二次雪崩）。

---

## 分层熔断与降级编排

LiuHao AI OS 模型网关的三层熔断：

```
L1 单模型实例   gpt-5@provider-a-1   ──┐
L2 提供商       provider-a (聚合 L1)   ──┼─ 任一层断开即触发路由切换
L3 能力域       "code-generation"      ──┘
```

降级链示例（代码生成请求）：

1. `gpt-5@provider-a`（首选）→ 熔断则
2. `gpt-5@provider-b`（跨提供商同模型）→ 熔断则
3. `claude-code@provider-c`（替代模型）→ 熔断则
4. 本地小模型 `liuhao-code-mini`（兜底，能力降级但可用）→ 也挂则
5. 返回缓存的相似历史结果 + 明确告知用户降级

```python
FALLBACK_CHAIN = [
    ("gpt-5", "provider-a"),
    ("gpt-5", "provider-b"),
    ("claude-code", "provider-c"),
    ("liuhao-code-mini", "local"),
]

def routed_completion(prompt: str):
    last_err: Exception | None = None
    for model, provider in FALLBACK_CHAIN:
        breaker = registry.get(f"{model}@{provider}")
        try:
            return breaker.call(gateway.complete, model=model,
                                provider=provider, prompt=prompt)
        except CircuitOpenError as e:
            last_err = e
            continue
    raise AllProvidersDownError() from last_err
```

---

## 配置示例

```yaml
# config/circuit_breaker.yaml
defaults:
  failure_rate_threshold: 0.5
  slow_call_duration_ms: 10000
  slow_call_rate_threshold: 0.8
  sliding_window_size: 100
  min_calls: 20
  open_cooldown_s: 30
  half_open_max_calls: 5
  half_open_success_threshold: 3

circuits:
  "gpt-5@provider-a":
    failure_rate_threshold: 0.3      # 主力模型更敏感
    slow_call_duration_ms: 15000
    open_cooldown_s: 60
  "embeddings@provider-b":
    slow_call_duration_ms: 3000
    adaptive_slow_call: true         # 启用基线偏离检测
  "local-fallback":
    failure_rate_threshold: 0.9      # 本地兜底尽量不断

fallback_chains:
  code-generation: ["gpt-5@provider-a", "gpt-5@provider-b", "claude-code@provider-c", "liuhao-code-mini@local"]
  summarization:   ["gpt-5-mini@provider-a", "claude-haiku@provider-c"]

metrics:
  export: prometheus
  labels: [circuit, state]
```

---

## 观测与告警

必备指标：

- `circuit_state{circuit="..."}` — 0/1/2，状态变化即事件
- `circuit_calls_total{circuit, result="ok|error|slow|rejected"}`
- `circuit_open_total{circuit}` — 跳闸次数，突增说明下游在劣化
- `circuit_half_open_probe_result{circuit, result}`

告警建议：

| 告警 | 条件 | 级别 |
|------|------|------|
| 主力模型熔断 | `gpt-5@provider-a` OPEN > 5min | critical |
| 全链路降级 | fallback 链走到本地兜底 | critical |
| 频繁跳闸 | 1h 内同一熔断器 OPEN > 10 次 | warning |
| 慢调用率上升 | slow_rate > 50% 持续 10min | warning |

---

## 最佳实践

1. **所有跨进程调用都套熔断器**，包括看似可靠的内部服务——内部服务的部署窗口就是故障窗口。
2. **熔断 + 舱壁成对使用**：熔断防故障扩散，[舱壁](bulkhead-advanced.md)防资源耗尽，缺一不可。
3. **降级逻辑与业务同评审**。降级返回什么（缓存？简化结果？明确报错？）是产品决策，不是纯技术决策。
4. **熔断事件必须可审计**：谁断了、何时断、基于什么指标，写入审计流。
5. **定期演练**：chaos engineering 注入下游故障，验证熔断和降级链真的工作。
6. **半开期限制并发试探**，避免恢复瞬间被压垮（thundering herd）。
7. **阈值随容量规划评审**，下游扩容后不及时调整阈值会造成"假熔断"。

---

## 常见问题

### Q1: 熔断后请求直接失败，用户体验怎么保证？

熔断的意义就是"快速失败换取系统存活"。体验靠**降级链**保证：替代模型、缓存结果、排队异步处理、明确告知。完全无降级的熔断只是把 30s 超时变成 1ms 报错。

### Q2: 滑动窗口设多大？

经验值：QPS × 10s。QPS 10 的系统用 100 次计数窗口；QPS 1000 用 10s 时间窗口。窗口过小易受突发噪声影响，过大则反应迟钝。

### Q3: 熔断器状态要集中存储吗（如 Redis）？

默认每实例独立即可——实例本地熔断本来就能分散故障。只有当需要"全局一致熔断"（如付费 API 配额即将耗尽时全体停调）才引入集中状态，并容忍其带来的额外延迟。

### Q4: 429 限流要不要计入熔断？

计入，但建议单独维度计数并配合 Retry-After 退避。把 429 当普通错误处理会导致：限流→熔断→冷却→试探→再限流的振荡。更好的做法见 [API 速率优化](../23-performance/api-rate-optimization.md)。

### Q5: 如何测试熔断器？

三件套：单测（模拟失败序列断言状态迁移）、集成测（用 toxiproxy 注入延迟/断连）、混沌演练（生产小规模注入）。至少每季度一次真实演练。

---

**相关文档**：[舱壁隔离深入](bulkhead-advanced.md) · [API 速率优化](../23-performance/api-rate-optimization.md) · [监控栈](../21-deployment/monitoring-stack.md)
