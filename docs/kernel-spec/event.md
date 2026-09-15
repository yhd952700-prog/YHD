# Event Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **真实实现**：`src/kernels/event/__init__.py:137` 的 `EventBus`。`lifecycle` 字段见 :139；`initialize/shutdown/pause/resume` 见 :292/:295/:298/:303。
> - **进程级入口**：`get_event_bus()`（`src/kernels/event/__init__.py:314`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。`EventBus` 已接入生命周期协议；存在即 READY（首次使用时惰性构造并 `initialize()`）。`shutdown/pause/resume` 无后台驱动方，仅由显式调用者触发。
> - **本文档性质**：目标态契约 + 已核对现状。标注「目标态（尚未实现）」者为设计意图；未标注者以代码为准。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。

## 1. 定义

Event Kernel 是 Human-Sovereign Agent OS 的**统一事件总线内核**，为内核间通信与横切关注点提供 pub/sub 能力。所有事件携带 `correlation_id`（跨事件链端到端可追溯）、支持 scope 过滤、死信（dead letter）+ 重试。`EventBus`（`src/kernels/event/__init__.py:137`）是 execution kernel、`api/events_ws.py` 等消费的事件中枢。

## 2. 目标（现状可实现的能力）

- 发布/订阅：`publish` / `subscribe`（`src/kernels/event/__init__.py:147` / :179）。
- 关联追踪：`Event.correlation_id` / `causation_id`（:49），`get_correlation_chain`（:243）。
- 死信与重试：`DeadLetterEntry`（:118），`retry_dead_letter`（:255）。
- 历史与统计：`get_event_history`（:222），`stats`（:278）。
- scope 过滤订阅：`Subscription.matches`（:94）。

## 3. 生命周期（现状）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- `EventBus`（`src/kernels/event/__init__.py:137`）已实现 `lifecycle` 字段（:139）与四个方法（:292-303）。
- **单例**：`get_event_bus()`（:314）为进程级惰性单例，构造后立刻 `initialize()`，不变量为「存在即 READY」。
- **无后台驱动**：`shutdown`/`pause`/`resume` 仅由显式调用者触发；`pause`/`resume` 当前仅置位状态（PAUSED 语义「缓冲 publish」属目标态，见第 8 节）。

## 4. 输入模型

- `publish(event: Event) -> str`（返回 `correlation_id`，`src/kernels/event/__init__.py:147`，`@kernel_action("event.publish")`）。
- `publish_async(event: Event) -> str`（:174）。
- `subscribe(event_type, handler, scope=EventScope.L0, filters=None, correlation_id=None) -> str`（:179）。
- `unsubscribe(subscription_id) -> bool`（:201）。
- `retry_dead_letter(index: int) -> bool`（:255）。
- 类型：`Event`（:49，含 `type/source/data/scope/priority`）、`Subscription`（:82）、`EventScope`（:28，L0–L7）。
- 便利函数 `publish_event`（:307）、`subscribe_event`（:333）。

## 5. 输出模型

- `publish` 返回 `event.correlation_id`（:172）。
- 事件进入 `_event_history`（上限 10000，:142）与 `_correlation_index`。
- 失败 handler → `DeadLetterEntry` 进 `_dead_letters`（:169）。
- `get_event_history` / `get_correlation_chain` / `get_dead_letters`（:222/:243/:248）。
- `stats()`（:278）返回订阅数、历史大小、死信数。
- **当前无持久化**：历史全内存，进程重启即失。

## 6. 错误处理（对齐 `src/kernels/_base.py`）

- `KernelNotInitializedError`：READY 前调用 `publish`/`subscribe`。
- `KernelStateError`：handler 在锁内执行违反「派发前释放锁」原则时，应抛/记录（属目标态）。
- `KernelPermissionError`：越权发布尝试应抛；scope 不匹配的订阅静默不匹配（`Subscription.matches`，:102）。
- **目标态（尚未实现）**：`retry_dead_letter` 在重新 `publish` 后是否无条件置 `dl.resolved=True`（:265）——需代码实测核对，不得作为现状事实陈述。

## 7. 权限边界

- `EventScope` L0–L7（依 `KERNEL-CANON.md` C11）。`Subscription.matches` 要求 `sub.scope >= event.scope`（:102-104）为地板语义。
- 权限名（冒号形式，authority A）见 `src/kernels/security/_permission_map.py`：event 相关动作 `event.publish`/`event.subscribe`/`event.unsubscribe`/`event.retry_dead_letter`/`event.clear_history` 为 point-dot 形式（authority B），非冒号权限。访问控制收敛设计见 `docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md`。

## 8. 目标态契约与已知缺口（尚未实现，待代码实测）

以下为设计契约要求与当前实现的**待核实差距**，非以 HEAD 验证过的事实；不得引用任何已删除的审计文档。

- `publish_async` 是否为真异步（原稿称实为同步调用 `publish`，:174-177）——需代码实测核对。
- handler 是否在 bus 锁内联执行（:150-172），慢/阻塞订阅者是否阻塞全部发布——待核实。
- 死信重试：`retry_dead_letter` 调 `publish` 后是否无条件 `dl.resolved=True`（:265），导致仍失败也被标记已解决、原事件丢失——需代码实测。
- 事件是否转发 audit store（当前不转发，审计断链）——属目标态缺口。
