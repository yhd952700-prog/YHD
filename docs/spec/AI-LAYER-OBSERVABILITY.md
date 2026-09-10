# LIUHAO X v3.0 — 能力层可观测性实施说明

> 版本：v1.0 · 建立日期：2026-09-09
> 关联：[`AI-LAYER-DOD-AUDIT.md`](AI-LAYER-DOD-AUDIT.md)（第 12 轮七维复验，本增强即其推荐项 ①）
> 代码：[`src/ai/observability.py`](../../src/ai/observability.py) · 测试：[`tests/test_observability.py`](../../tests/test_observability.py)

---

## 1. 为什么做

第 12 轮七维复验发现：14 kernel 七维已正式收口，但 `src/ai/` 能力层（编排层）**几乎不打日志/审计**（18/20 模块直接 logging 计数为 0），横切关注点全下沉到 kernel action 边界（`@kernel_action`）。能力层粒度可观测性缺失——无法从日志直接看出"哪一轮对话 / 哪个意图触发了哪些能力层动作及其耗时与结果"。

本增强在不改变任何既有行为的前提下，为能力层补上结构化日志 + 调用链透传 + 调用记录缓冲。

---

## 2. 模块 API（`src/ai/observability.py`）

| 符号 | 用途 |
|---|---|
| `get_logger(name)` | 返回 `liuhao.<name>` 标准 `logging.Logger`，自动挂 `_TraceFilter`，每条日志带 `tid` / `cid` 字段 |
| `TraceContext.set(trace_id=, correlation_id=)` | 用 `contextvars` 在当前调用链上设置 trace / correlation id（跨函数透传，无需显式传参） |
| `TraceContext.current()` / `new_trace_id()` / `reset()` | 读取 / 生成 / 清除上下文 |
| `@observe("Phase.method")` | 装饰函数/方法：记录 `ENTER`(DEBUG) / `EXIT`(INFO，含耗时与 outcome) / `ERROR`（异常原样抛出）；写入环形缓冲。不入参生成器 |
| `recent_traces(limit)` | 返回进程内最近 N 条能力层调用记录（capped 200），供驾驶舱"最近动态"等消费 |

**零外部依赖**：仅用标准库（`logging` / `contextvars` / `threading` / `uuid` / `time`）。

---

## 3. 已接入点

| 模块 | 接入点 |
|---|---|
| `src/ai/liuhao.py` | `LiuHaoAssistant.chat`（@observe）+ `chat_stream`（手动 ENTER/EXIT + `TraceContext.set(correlation_id)` 透传回话 id） |
| `src/ai/lcore.py` | `LCore.handle_intent`（@observe） |
| `src/ai/collaboration.py` | `MultiAgentTeam.delegate` / `pipeline` / `broadcast`（@observe） |
| `src/ai/runtime_loop.py` | `RuntimeLoop.step` / `run`（@observe） |

`chat` 把每轮 `correlation_id` 注入 `TraceContext`，因此能力层日志与该轮审计事件、kernel action 审计可用同一 `cid` 串联，实现"对话轮 → 能力层动作 → kernel action"的因果链追踪。

---

## 4. 消费方式（示例）

```python
from src.ai.observability import recent_traces

# 驾驶舱"最近动态"可轮询（替代仅读 kernel audit 噪声）：
for t in recent_traces(limit=20):
    print(t.phase, t.outcome, f"{t.elapsed_ms:.1f}ms", t.trace_id)
```

日志样例（stderr，INFO 级仅 EXIT/ERROR，DEBUG 级含 ENTER）：
```
2026-09-09 23:00:01 INFO liuhao.liuhao [tid=abc123 cid=corr99] EXIT LiuHaoAssistant.chat ok=completed elapsed_ms=842.3
2026-09-09 23:00:01 INFO liuhao.lcore [tid=abc123 cid=-] EXIT LCore.handle_intent ok=completed elapsed_ms=12.1
```

---

## 5. 验证

- 单测 `tests/test_observability.py`：8 例全绿（logger 注入 trace/cid、`@observe` EXIT 记录与环形缓冲、异常重抛与 ERROR、嵌套 observe 的 trace 透传、trace 继承）。
- **全量回归**：`1141 passed / 1 skipped / 0 failed`（相对第 11 轮 1133 +8，零回归）。
- flake8：本增强引入的 `src/ai/observability.py` + 4 个接入文件的新增行 **0 新增违规**（仓库既有 E501 行宽债务不在此轮范围）。
- 行为不变：纯增量埋点，未改任何既有调用语义；生成器路径用手动埋点避免 trace 上下文提前重置。

---

## 6. 后续可选扩展

1. 其余 16 个能力层模块（perception/ada/organization/enoch/network/world/governance/economy/verification/evolution/l10k/hardening/agent_factory/tool_registry/personal_context/conversation_store）接入 `@observe` —— 可选，不影响端到端语义。
2. 若需跨进程 trace：把 `trace_id` 透传到 kernel audit 记录的 `details`，或将 `recent_traces` 落盘/上报。
3. 可选接入 OpenTelemetry：当前用标准 `logging` + 进程内缓冲，零依赖；上生产若需分布式追踪再引入 OTel。
