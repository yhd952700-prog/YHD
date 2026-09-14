# Phase 7b — 消息总线后端化设计（消除静默失败）

> 状态：设计已定，实现中。范围是 Phase 7（工程生产化）第二片：`src/distribution/msg_bus.py`。
> 纪律同前：**先实测再判断**、**附加式改动**、**默认零行为变化**、**禁止静默降级**。

## 1. 实测发现：一个"零调用方 + 五处静默失败"的模块

`src/distribution/msg_bus.py`（296 行）**被全仓库任何模块 import 的地方为零**（实测：
`grep -rn "distribution.msg_bus\|get_message_bus\|publish_message" src/ tests/` 除自身外无命中；
`src/ai/collaboration.py` 里的 `MessageBus` 是**另一个同名类**，与本模块无关）。
即：这是一处**无人消费的能力声明**，所以下面是"宣称能做、实际不做"的密室缺陷，
没有任何测试或调用方会暴露它们。

| # | 缺陷 | 证据 | 后果 |
|---|------|------|------|
| D1 | **redis 模式 publish 静默丢消息** | `_do_publish:146-151` 调 `_publish_redis`，而 `_publish_redis:153-156` **只打了一条 debug 日志**；随后 `return message.message_id` | 调用方拿到一个**合法长相的消息 ID**，消息实际不存在。分布式发布"成功"了但什么都没发生 |
| D2 | **redis 模式 consume 恒返回空** | `_consume_redis:226-229` 直接 `return []` | 消费者永远收不到消息，且不报错 |
| D3 | **initialize 谎报回退** | `_init_redis:115-121` 日志写 "falling back to memory"，但**没有改 `self.mode`**；`initialize():107` 无条件置 `_initialized = True` | `health_check()` 返回 `mode="redis"` + `initialized=True` ⇒ **健康检查在说谎** |
| D4 | **memory 模式订阅回调从不触发** | `_publish_memory:158-170` 只 append 到 `_queues`；`_subscribers` 仅在 subscribe/unsubscribe/计数处出现，**没有任何派发点** | `subscribe()` 注册的回调永远不被调用；pub/sub 语义名存实亡 |
| D5 | **`total_published` 统计的是"队列剩余量"** | `get_stats:258-260` 用 `len(_queues[...])` 求和 | 指标名与语义不符：消费得越多"发布量"越小 |

D1/D2/D3 叠加的效果最危险：**在 redis 模式下整套总线是一个"永远成功、永远收不到"的黑洞**，
而 `health_check()` 还会报告它一切正常。本仓库的 C-1..C-7 与 DoD 七维要求
「Policy Controlled + Audited」——一个会谎报健康的组件不满足 DoD。

## 2. 交付物

### 2.1 新增 `src/distribution/bus_backends.py`

| 符号 | 职责 |
|------|------|
| `BusUnavailableError(RuntimeError)` | 后端不可用时的**诚实失败**（不再静默降级） |
| `BusBackend` (Protocol) | `publish(topic, message) -> str` / `consume(topic, count, timeout) -> List[Message]` / `health() -> dict` / `stats() -> dict` / `close()` |
| `InMemoryBusBackend` | 真内存队列 + **真订阅派发**（修 D4）+ 真发布计数（修 D5） |
| `RedisStreamsBusBackend` | 真 redis-py Streams（`xadd`/`xread`/`xdel`/`ping`），**client 可注入**（便于离线测试） |
| `get_bus_backend(mode, config, client=None)` | 工厂；未知 mode 抛 `ValueError` |

**依赖方向（避免循环导入）**：`msg_bus.py` → `bus_backends.py` 顶层导入；
`bus_backends.py` 对 `Message` 只在 `TYPE_CHECKING` 下引用、运行时**在方法内 import**。

**Redis 后端语义**（明确写清，不含糊）：
- stream key = `f"{prefix}{topic}"`，`payload` 走 `json.dumps`；**不可序列化即抛错**，不静默 `str()`。
- `consume` 用 per-topic last-id 游标 `xread`，读完即 `xdel` ⇒ **单消费者队列语义**（与既有 memory 模式
  的"消费即移除"保持一致，不擅自改成多消费者组）。
- `timeout=None` → 非阻塞；`timeout` 有值 → `block=int(timeout*1000)`。

### 2.2 改 `src/distribution/msg_bus.py`（外科手术式）

- `initialize()`：真正构造后端。失败 ⇒ `_initialized=False` + `_init_error=<原因>` + error 日志，
  **绝不改 `self.mode`、绝不假装成功**。
- 新增 `_ensure_ready()`：未初始化则**懒初始化一次**；仍失败 ⇒ 抛 `BusUnavailableError`。
  这样 memory 模式"拿来就能用"的旧体验**不变**，redis 模式则**响亮失败**。
- `publish()` / `consume()`：走 `_ensure_ready()` → 后端。
- `health_check()`：如实返回 `initialized` / `init_error` / 后端 health；未就绪时 `status="unavailable"`。
- `get_stats()`：`total_published` 改为真实计数器（修 D5）。
- 新增 `close()`。
- **保持向后兼容的公开面**：`SUPPORTED_MODES` / `Message` / `MessageBus` / `get_message_bus` /
  `publish_message` / `subscribe_topic` 全部保留，memory 模式的 `msg_N` ID 格式不变。

### 2.3 测试 `tests/distribution/test_bus_backends.py`

- memory 模式回归：publish→consume 往返、`msg_N` 格式、订阅回调**真的触发**、unsubscribe 生效。
- **诚实性**（本轮核心）：redis 无服务时 `publish()` **抛 `BusUnavailableError`**；
  `health_check()` 报告 `initialized=False` + `init_error`，**不再谎报健康**。
- **真实 redis 代码路径**：注入 fake client，断言 `xadd`/`xread` 的调用与 `Message` 重建。
- D5 回归：2 次 publish + 1 次 consume 后 `total_published == 2`。
- 真 redis 可用时跑端到端；**不可用则显式 skip 并给出原因**（本机实测不可达，故走 skip）。

## 3. 默认零行为变化 / 风险 / 回滚

- 无调用方 ⇒ 回归面为零；但 memory 模式仍写测试锁死旧语义（`msg_N`、消费即移除、1000 上限）。
- 唯一有意的行为改变：redis 模式从"静默成功"变成"响亮失败"。**这是修 bug，不是改需求**。
- 回滚 = 删 2 个新文件 + `git checkout -- src/distribution/msg_bus.py`。

## 4. 诚实边界（明确不做）

- **不做** Postgres 后端（`kernels/memory/backends.py` 的 postgres 分支仍是 fail-closed 占位）⇒ 留 Phase 7c。
- **不做** Redis 多消费者组 / 消费确认（ACK）/ 死信重投。
- **不做** 把 `MessageBus` 接进 `src/gateway` 或内核（无需求；避免为了"接线"而接线）。
- 本机**无 redis 服务**（实测 `TimeoutError`），故真实 Redis 端到端仅在 `skipif` 下存在。
