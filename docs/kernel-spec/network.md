# Network Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> - **真实实现**：`src/kernels/network/__init__.py:348` 的 `NetworkBus`。`lifecycle` 字段见 :350；`initialize/shutdown/pause/resume` 见 :578/:581/:584/:589。
> - **进程级入口**：`get_network_bus()`（`src/kernels/network/__init__.py:600`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。本类具备 `lifecycle` 字段与四个生命周期方法（存在即 READY）。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；`route()` 读 `message.metadata["scope"]` 但 `Message` 无 `scope` 属性（scope 默认 L1）；通配路由 `internal_default` 优先级最高且改写 `protocol`，导致 HTTP/WS 不可达；A2A/MCP/GRPC 适配器静默失败；`httpx` 硬导入缺依赖则整 kernel 导入失败。
>
> **性质提示**：本规范整体是「目标态契约 + 已核对现状」，不是「现状规范」。已核对的现状以上述行号为准；未标注的段落（尤其是 §9 的缺口清单）以代码为准，待实测后修订。
>

## 1. 定义

Network Kernel 是 Human-Sovereign Agent OS 的**协议适配与通信路由内核**，负责在内核间/智能体间路由消息，并适配多种协议（A2A / MCP / gRPC / HTTP / WebSocket / INTERNAL）。`NetworkBus`（`__init__.py:348`）维护适配器注册表、路由表与消息历史，被 `ai/network_gateway.py` 消费，是"Agent 可跨进程/跨协议通信"能力主张的承载者。

**依赖**：仅 `src._time`、`src.kernels._crosscutting`（`@kernel_action` 织入）；**硬导入 `httpx`**（`__init__.py:24`，缺依赖则整个 kernel 导入失败，现状实测）。是 `_crosscutting` 星型 hub 的单向叶子，不反向依赖上层。

## 2. 目标

- 协议适配：`InternalAdapter` / `HTTPAdapter` / `WebSocketAdapter`（`__init__.py:205 / :239 / :305`）。
- 路由：`route(message)`（`__init__.py:441`）、`add_route`（`__init__.py:402`）、`remove_route`（`__init__.py:412`）。
- 发送便利：`send(...)`（`__init__.py:502`）、`send_message`（`__init__.py:593`）。
- 关联追踪：`Message.correlation_id` / `causation_id`（`__init__.py:57`），`get_correlation_chain`（`__init__.py:552`）。
- 消息序列化：`to_dict` / `from_dict`（`__init__.py:83 / :106`）。

**差距（现状，基于代码实测，非引用外部审计文档）**：
- **scope 读错字段**：`route()` 读 `message.metadata.get("scope","L1")`（`__init__.py:460`），但 `Message` **无 `scope` 属性**（`__init__.py:58-77`）→ 每条消息都被当 L1，scope 控制默认失效。
- **通配路由强制进程内**：`internal_default` 为 `pattern="*"`→`INTERNAL` 优先级 100（`__init__.py:383-389`），`route()` 改写 `message.protocol`（`__init__.py:480`）→ 即使 `send(...,protocol=HTTP)` 也走 `InternalAdapter`，HTTP/WS 不可达。
- A2A/MCP/GRPC 路由静默失败：`_adapters.get(route.protocol)` 为 None → `FAILED "No adapter"`（`__init__.py:473-477`）。
- `httpx` 无条件导入（`__init__.py:24`）→ 缺失则整个 kernel 导入失败（与 WS 适配器"无依赖守卫"矛盾）。

## 3. 生命周期（现状已核对）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- **UNINITIALIZED**：`NetworkBus()` 构造（`__init__.py:350`，`lifecycle` 默认 `UNINITIALIZED`）。
- **INITIALIZING → READY**：`initialize()`（`:578`）置 `READY`；`_register_builtin_adapters`（`__init__.py:360`）注册 internal/http/ws 三适配器并加默认 `internal_default` 路由。注意：构造/初始化会触发 `import httpx`（`__init__.py:24`），依赖缺失则构造即崩。
- **PAUSED / RESUME**：`pause()/resume()`（`:584/:589`）已存在，带状态校验（非法迁移抛 `KernelStateError`）并仅置位 `lifecycle`（PAUSED/READY）；未真正挂起读写。语义目标：PAUSED 时应拒绝 `route`/`send`。
- **STOPPED**：`shutdown()`（`:581`）置 `lifecycle=STOPPED`；应 `HTTPAdapter.close()`（`__init__.py:299`）释放 httpx 连接，需实测确认是否已释放。
- **ERROR**：适配器 `send` 抛非预期异常应转 ERROR；当前 `route` 仅把消息标 FAILED（如 `__init__.py:454/475`），bus 自身不转 ERROR。

## 4. 输入模型

- `route(message: Message) -> Message`（`__init__.py:441`，`@kernel_action("network.route")`）：按 destination 匹配路由、做 scope 校验、改写 `message.protocol` 并派发。
- `send(type, content, source, destination, protocol=None, correlation_id=None, priority=NORMAL, headers=None, metadata=None) -> Message`（`__init__.py:502`）。
- `register_adapter(adapter)`（`__init__.py:391`）、`add_route(route)`（`__init__.py:402`）、`remove_route(route_id)`（`__init__.py:412`）。
- `register_internal_handler(destination, handler)`（`__init__.py:528`）。
- 类型：`Message`（`__init__.py:57`，无 scope 字段）、`ProtocolType`（`__init__.py:29`）、`Route`（`__init__.py:149`，含 `scope` 授权上限）、`AdapterConfig`（`__init__.py:161`）。

## 5. 输出模型

- `route`/`send` 返回 `Message`，状态由 `MessageStatus` 反映（`PENDING/SENT/DELIVERED/FAILED`，`__init__.py:39`）。
- 成功经适配器：`InternalAdapter.send` 直接调 handler（`__init__.py:220`）；`HTTPAdapter.send` 真实 POST、仅 2xx 标 DELIVERED（`__init__.py:260`）；`WebSocketAdapter.send` 诚实拒绝标 FAILED（`__init__.py:330`）。
- 失败：`message.metadata["error"]` 记录原因（`__init__.py:288/337/454/463`）。
- `get_message_history` / `get_correlation_chain`（`__init__.py:534/552`）、`stats`（`__init__.py:557`）。
- **当前无持久化**：消息历史全内存。

## 6. 错误处理（现状已核对）

对齐 `_base.py` 异常族。

- **缺失**：READY 前调用 `route/send` 应抛 `KernelNotInitializedError`（类已具备 `lifecycle` 字段与方法，是否在各入口做校验需实测）。
- **配置错误**：`add_route` 的非法 scope 抛 `ValueError`（`__init__.py:405`）应改为 `KernelConfigurationError`；`httpx` 缺失导致构造失败应抛 `KernelConfigurationError` 而非 `ImportError` 崩溃。
- **权限**：`route` 的 scope 越界应抛 `KernelPermissionError` 而非仅标 FAILED（让调用方区分"被拒"与"投递失败"）。当前 `route` 把越界标 FAILED（`__init__.py:465`）但读的是错误字段，需实测。
- **当前错误点（核心缺陷）**：`route` 读 `message.metadata["scope"]`（`__init__.py:460`）但 `Message` 无该属性 → 永远取默认 "L1"，scope 控制**形同虚设**；且其"改写 `message.protocol`"（`__init__.py:480`）覆盖了调用方显式指定的协议，导致通配路由强制进程内。

## 7. 权限边界（现状已核对）

- **scope**：`_VALID_SCOPES`（`__init__.py:136`）与 `_scope_rank`（`__init__.py:144`）。`Route.scope` 是**授权上限**（`__init__.py:157`），`route` 校验 `msg_scope <= route.scope`（`__init__.py:465`）。
- **capability**：需 `network.register_adapter` / `network.add_route` / `network.remove_route` / `network.route` 动作能力。
- **与 policy/security 边界**：Policy 的 `scope_enforcement` 规则**已激活**（L0，precedence=100，DENY 越权），并非失活；`_adjudicate` 调用 `evaluate_policy_simple` 时传 `scope=None`。network 自身的 scope 校验由本地 `_is_valid_scope`/`_scope_rank` 实现，是否经 Policy 统一裁决需以代码实测确认，不可断言"未受统一裁决"。
- **越界/重复（现状实测）**：
  - scope 控制**失效**：`Message` 缺 `scope` 字段，读 `metadata["scope"]` 永远 L1。
  - 通配路由强制进程内：默认 `internal_default`（`__init__.py:383`）优先级 100 高于任何具体路由，且 `route` 改写 protocol（`__init__.py:480`）→ HTTP/WS 不可达。

## 8. 测试要求（DoD）

- **单元**：`route` 命中具体路由（非通配）正确选协议；`send(...,protocol=HTTP)` 真正走 `HTTPAdapter`（`__init__.py:520` 默认 INTERNAL）；`InternalAdapter` handler 送达（`__init__.py:220`）；`HTTPAdapter` 2xx→DELIVERED、非 2xx→FAILED（`__init__.py:280`）；`WebSocketAdapter` 诚实 FAILED（`__init__.py:330`）。
- **边界**：`add_route` 非法 scope 抛错（`__init__.py:405`）；`remove_route`；`get_message_history` 过滤。
- **失败路径测试**：
  1. **scope 字段**：给 `Message` 加真实 `scope` 字段后，断言 `route` 读取它而非 `metadata["scope"]`；越界抛 `KernelPermissionError`。
  2. **协议尊重**：`send(...,protocol=HTTP)` 必须不被 `internal_default` 改写（`__init__.py:480`）——注册默认 HTTP/WS 路由。
  3. **未实现协议**：A2A/MCP/GRPC 应诚实 FAILED 并明确原因，而非静默。
  4. **依赖守卫**：`httpx` 缺失时 `NetworkBus` 不应整体崩溃——懒导入。
  5. **生命周期**：READY 前 `route` 抛 `KernelNotInitializedError`。
  6. **协议适配器**：`register_adapter`（`__init__.py:391`）覆盖默认注册；`get_adapter`（`__init__.py:397`）对未注册协议返回 None；`HTTPAdapter.close`（`__init__.py:299`）在 STOPPED 时释放连接。

## 9. 目标态契约与已知缺口（尚未实现，待代码实测）

以下条目为**目标态契约**，并非全部已在 HEAD 实测确认；落地前须以当前代码重新核实，不可照抄：

- ❌ **误报（2026-09-15 实测）**：`Message` **没有** `scope` 字段（字段见 `__init__.py:59-77`），scope 只能来自 `metadata["scope"]`，且**控制确实生效**——实测非法 scope `L9` → FAILED「Invalid message scope: 'L9'」；`L7` 消息 vs 路由上限 `L1` → FAILED「Message scope L7 exceeds route scope L1」；既有 `test_route_message_scope_exceeding_route_denied` 已钉住。原条目基于一个不存在的字段，判为误报。
- ⚠️ **真实（2026-09-15 实测，未修）**：`internal_default`（`pattern="*"`, `priority=100`）会**覆盖调用方显式指定的 `protocol`**——实测 `Message(destination="x", protocol=HTTP)` 经 `route()` 后 `protocol` 变为 `internal`、`x-route-id=internal_default`；且未注册默认 HTTP/WS 路由，故 HTTP/WS 适配器已注册却无路由可达。**未修原因**：修它要改路由匹配语义（协议感知 + 新增默认协议路由），影响面覆盖**所有**消息路由，属设计级变更，需单独决策。
- ✅ **已实现（本次核实）**：A2A/MCP/GRPC 无适配器时由 `route()` 的 `if not adapter` 分支**诚实 FAILED** 并附原因；WebSocket 适配器 `supports_send=False` / `is_available()=False`，`send()` 一律 FAILED 附可操作原因，绝不谎报 DELIVERED（`:307-341`）。
- `httpx` 应改为懒导入/依赖守卫——**真实但未修**：`httpx` 由 `HTTPAdapter` 真实使用，且已是声明依赖，改懒导入属健壮性增强而非静默缺陷。
- `pause()/resume()` 当前仅置位 `lifecycle`，应真正拒绝 PAUSED 态的 `route`/`send`；`shutdown()` 应释放 httpx 连接。**（未修；`HTTPAdapter` 已有 `close()`，`shutdown` 接线待做。）**
- 消息历史当前全内存，无持久化。**（未修；功能项。）**
