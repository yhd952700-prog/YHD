# Network Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> - **真实实现**：`src/kernels/network/__init__.py:348` 的 `NetworkBus`。`lifecycle` 字段见 :350；`initialize/shutdown/pause/resume` 见 :578/:581/:584/:589。
> - **进程级入口**：`get_network_bus()`（`src/kernels/network/__init__.py:600`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。本类具备 `lifecycle` 字段与四个生命周期方法（存在即 READY）。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；`route()` 的 scope 取自 `message.metadata["scope"]`（`Message` 无 `scope` 字段，默认 L1；2026-09-15 实测 scope 控制**确实生效**，详见 §9）；`httpx` 硬导入缺依赖则整 kernel 导入失败。（原「通配路由改写 protocol 致 HTTP/WS 不可达」已于 2026-09-15 修复，见 §9。）
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
- ~~**scope 读错字段**~~ **误报（2026-09-15 实测）**：`route()` 读 `message.metadata.get("scope","L1")`（`__init__.py:462`）——`Message` 确无 `scope` **属性**，但 scope 的载体本就是 `metadata`，且控制**确实生效**：非法 `L9` → FAILED「Invalid message scope」；`L7` vs 路由上限 `L1` → FAILED「exceeds route scope」（既有 `test_route_message_scope_exceeding_route_denied` 钉住）。原条目把「无该属性」误判成「控制失效」。详见 §9。
- ~~**通配路由强制进程内**~~ **已于 2026-09-15 修复**：原 `internal_default`（`pattern="*"`→`INTERNAL`, priority 100）会让 `route()` 改写 `message.protocol`，`send(...,protocol=HTTP)` 也走 `InternalAdapter`。现 `_match_route` 协议感知（同优先级内优先匹配请求协议），并为三适配器各注册同优先级（100）通配路由 `internal_default`/`http_default`/`websocket_default`；无可路由的协议（如 A2A）会写 `metadata["protocol_downgraded"]`，不再静默。详见 §9。
- A2A/MCP/GRPC 路由静默失败：`_adapters.get(route.protocol)` 为 None → `FAILED "No adapter"`（`__init__.py:473-477`）。
- `httpx` 无条件导入（`__init__.py:24`）→ 缺失则整个 kernel 导入失败（与 WS 适配器"无依赖守卫"矛盾）。

## 3. 生命周期（现状已核对）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- **UNINITIALIZED**：`NetworkBus()` 构造（`__init__.py:350`，`lifecycle` 默认 `UNINITIALIZED`）。
- **INITIALIZING → READY**：`initialize()`（`NetworkBus.initialize`）置 `READY`；`_register_builtin_adapters` 注册 internal/http/ws 三适配器，并为三者各注册同优先级（100）通配路由 `internal_default` / `http_default` / `websocket_default`（2026-09-15 起；此前仅 `internal_default`）。
  ⚠️ 2026-09-15 起**构造不再导入 `httpx`**：`HTTPAdapter` 改为懒建客户端（`_ensure_client()`），因此「依赖缺失则构造即崩」已不成立（见 §9）。
- **PAUSED / RESUME**：`pause()/resume()` 已存在，带状态校验（非法迁移抛 `KernelStateError`）。
  **2026-09-15 起 PAUSED/STOPPED 真拒收**：`route()` 取锁后立刻校验生命周期，命中 PAUSED/STOPPED 则消息标 FAILED 并写入 `metadata["error"]`（不抛异常、不静默丢弃）。`resume()` 后投递照常恢复。
- **STOPPED**：`shutdown()` 置 `lifecycle=STOPPED`，并**遍历 `self._adapters` 逐个 `close()`**（逐个 try/except，失败 `logger.warning` 且不中断其余适配器）⇒ httpx 连接真正释放（此前 `shutdown()` 仅置位，连接泄漏）。
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
- **配置错误**：`add_route` 的非法 scope 抛 `ValueError`（实测已钉住）应改为 `KernelConfigurationError`（**仍开放**）；`httpx` 缺失**自 2026-09-15 起不再导致构造失败**——已改懒导入，缺依赖时 `HTTPAdapter.send()` 把原因写进 `message.metadata["error"]` 并返回 `False`（消息 FAILED），不逃逸 ImportError。
- **权限**：`route` 的 scope 越界应抛 `KernelPermissionError` 而非仅标 FAILED（让调用方区分"被拒"与"投递失败"）。当前 `route` 把越界标 FAILED，**仍开放**。
- ~~**当前错误点（核心缺陷）**：`route` 读 `message.metadata["scope"]` 但 `Message` 无该属性 → scope 控制形同虚设~~ ❌ **撤回（2026-09-15 实测误报）**：scope 本来就只来自 `metadata["scope"]`（`Message` 无独立 scope 字段是**设计如此**，见 §9），且控制**确实生效**：非法 scope `L9` → FAILED「Invalid message scope」；`L7` 消息 vs `L1` 路由 → FAILED「Message scope L7 exceeds route scope L1」。
- **`protocol` 被改写**（原核心缺陷）：`route()` 用 `route.protocol` 覆写 `message.protocol`，曾让通配 `internal_default` 吃掉调用方显式指定的协议。2026-09-15 已修：`_match_route` 协议感知 + 三默认路由 + 不可达时写 `x-protocol-downgraded` / `metadata["protocol_downgraded"]`（见 §9）。
- **持久化**：`history_path` 启用后落盘失败**不吞**——记入 `stats()["history_persist_errors"]`（含原因），投递照常完成。

## 7. 权限边界（现状已核对）

- **scope**：`_VALID_SCOPES`（`__init__.py:136`）与 `_scope_rank`（`__init__.py:144`）。`Route.scope` 是**授权上限**（`__init__.py:157`），`route` 校验 `msg_scope <= route.scope`（`__init__.py:465`）。
- **capability**：需 `network.register_adapter` / `network.add_route` / `network.remove_route` / `network.route` 动作能力。
- **与 policy/security 边界**：Policy 的 `scope_enforcement` 规则**已激活**（L0，precedence=100，DENY 越权），并非失活；`_adjudicate` 调用 `evaluate_policy_simple` 时传 `scope=None`。network 自身的 scope 校验由本地 `_is_valid_scope`/`_scope_rank` 实现，是否经 Policy 统一裁决需以代码实测确认，不可断言"未受统一裁决"。
- **越界/重复（现状实测）**：
  - ~~scope 控制**失效**~~ **误报**：scope 载体是 `metadata["scope"]`（非属性），默认 L1 且越权/非法均 FAILED，控制有效（2026-09-15 实测，见 §9）。
  - ~~通配路由强制进程内~~ **已于 2026-09-15 修复**（见 §6 与 §9）：默认路由现为三条同优先级（100）通配，按请求协议决胜；具体路由仍靠更高 priority 取胜。

## 8. 测试要求（DoD）

- **单元**：`route` 命中具体路由（非通配）正确选协议；`send(...,protocol=HTTP)` 真正走 `HTTPAdapter`（`__init__.py:520` 默认 INTERNAL）；`InternalAdapter` handler 送达（`__init__.py:220`）；`HTTPAdapter` 2xx→DELIVERED、非 2xx→FAILED（`__init__.py:280`）；`WebSocketAdapter` 诚实 FAILED（`__init__.py:330`）。
- **边界**：`add_route` 非法 scope 抛错（`__init__.py:405`）；`remove_route`；`get_message_history` 过滤。
- **失败路径测试**：
  1. ✅ **scope**（2026-09-15 已钉住）：`TestScopeEnforcement` 4 条——非法 scope 被拒、消息 scope 超路由上限被拒、路由 scope 非法被拒、合法范围内正常送达。scope 来自 `metadata["scope"]`，`Message` 无独立 scope 字段（设计如此）。
  2. ✅ **协议尊重**（2026-09-15 已修）：`TestProtocolAwareRouting` 5 条——`send(...,protocol=HTTP)` 不再被 `internal_default` 改写；注册默认 HTTP/WS 路由使其可达。
  3. ✅ **未实现协议**：A2A/MCP/GRPC 无适配器时诚实 FAILED 并附原因（`No adapter for protocol ...`）。
  4. ✅ **依赖守卫**（2026-09-15 已修）：`TestHttpxIsLazy` 3 条——模块无顶层 `import httpx`、客户端首次发送才建、缺 httpx 时 `send()` 返回 False 且 `metadata["error"]` 可操作。
  5. ❌ **生命周期**（**仍开放**）：READY 前 `route` 抛 `KernelNotInitializedError` —— 未实现，当前只对 PAUSED/STOPPED 做拒收。
  6. ✅ **协议适配器**：`register_adapter` 覆盖默认注册；`get_adapter` 对未注册协议返回 None；`shutdown()` 遍历全部适配器 `close()`（`TestLifecycleGating::test_shutdown_closes_adapters` 已钉住）。

- **当前基线（2026-09-15 实测）**：`tests/kernels/network/` **35 passed / 0 failed**
  （20 基线 + 5 `TestProtocolAwareRouting` + 4 `TestLifecycleGating` + 3 `TestHttpxIsLazy` + 3 `TestHistoryPersistence`）。

## 9. 目标态契约与已知缺口（尚未实现，待代码实测）

以下条目为**目标态契约**，并非全部已在 HEAD 实测确认；落地前须以当前代码重新核实，不可照抄：

- ❌ **误报（2026-09-15 实测）**：`Message` **没有** `scope` 字段（字段见 `__init__.py:59-77`），scope 只能来自 `metadata["scope"]`，且**控制确实生效**——实测非法 scope `L9` → FAILED「Invalid message scope: 'L9'」；`L7` 消息 vs 路由上限 `L1` → FAILED「Message scope L7 exceeds route scope L1」；既有 `test_route_message_scope_exceeding_route_denied` 已钉住。原条目基于一个不存在的字段，判为误报。
- ✅ **真实且已修（2026-09-15 实测并修复）**：`internal_default`（`pattern="*"`, `priority=100`）会**覆盖调用方显式指定的 `protocol`**——实测 `Message(destination="x", protocol=HTTP)` 经 `route()` 后 `protocol` 变为 `internal`、`x-route-id=internal_default`；且未注册默认 HTTP/WS 路由，故 HTTP/WS 适配器已注册却无路由可达。

  **修法**（`src/kernels/network/__init__.py`）：
  1. `_match_route(destination, protocol)` 改为**协议感知**：先按 `priority` 排序（具体路由仍压过通配），**同优先级内**优先取 `route.protocol == message.protocol` 的路由。
     ⚠️ 为什么协议只能做「平局决胜」而不能做硬过滤：`Message.protocol` 默认 `INTERNAL`，
     「未指定」与「显式 internal」在值上不可区分；若把协议做成硬过滤，所有默认消息都会
     塌到 internal 通配、从而忽略更具体的路由（既有 `test_add_route_and_deliver` 正是
     钉住「路由协议可覆盖消息默认协议」）。平局决胜只在无歧义处生效。
  2. 为内置三适配器各注册**同优先级（100）**通配路由 `internal_default` / `http_default` /
     `websocket_default` ⇒ 请求 HTTP/WS 时确实可达对应适配器。
  3. 兜底路径不再静默：若所选路由协议 ≠ 请求协议，**且**该 destination 下不存在该协议的
     任何路由（如未注册适配器的 A2A），则写入 `metadata["protocol_downgraded"]` 与
     header `x-protocol-downgraded`（如 `"a2a -> internal"`），换传输不再不可见。

  同步改动：`test_send_no_route_fails` 原只删 `internal_default` 即断言「No route」；
  默认路由变为三条后需删除全部三条（该用例意图是「一条路由都没有」）。
  回归：`TestProtocolAwareRouting`（5 条）；反证：源码退回 HEAD ⇒ 3 红 / 2 守卫绿。
- ✅ **已实现（本次核实）**：A2A/MCP/GRPC 无适配器时由 `route()` 的 `if not adapter` 分支**诚实 FAILED** 并附原因；WebSocket 适配器 `supports_send=False` / `is_available()=False`，`send()` 一律 FAILED 附可操作原因，绝不谎报 DELIVERED（`:307-341`）。
- ✅ **真实且已修（2026-09-15 实测并修复）**：`httpx` 已改为**懒导入 + 依赖守卫**。
  原先 `src/kernels/network/__init__.py` 顶层 `import httpx`，缺依赖时**导入期即 ImportError**，
  整个 network 内核连同其测试一起收集失败——一个可选传输依赖能拖垮内核导入。

  **修法**：`HTTPAdapter.__init__` 只置 `self._client = None`；新增 `_ensure_client()`，
  首次 `send()` 时才 `import httpx` 并建 `httpx.Client(timeout=...)`；捕获 `ImportError`
  转为 `RuntimeError`，由 `send()` 落进 `message.metadata["error"]` 并返回 `False`
  （消息变 FAILED，**不逃逸异常**）。另加 `logger = logging.getLogger("liuhao.kernel.network")`，
  `close()` 不再无条件触碰未建客户端。

  **回归**：`TestHttpxIsLazy`（3 条：模块无顶层 httpx / 客户端首次发送才建 / 缺 httpx 时诚实失败）。
  **反证**：源码退回 HEAD ⇒ 3 条全红。
- ✅ **真实且已修（2026-09-15 实测并修复）**：`pause()/resume()` 原先**只置位 `lifecycle`**，
  PAUSED 态下 `route()`/`send()` 照常投递——生命周期位形同虚设，违反 `KernelLifecycle` 语义。

  **修法**：`route()` 在取锁后立刻校验 `self.lifecycle in (PAUSED, STOPPED)`，
  命中则 `message.status = FAILED` + `metadata["error"]` 写明「bus is <state>; route()/send()
  are refused while paused or stopped」并返回（**不抛异常、不静默丢弃**）。
  `shutdown()` 改为遍历 `self._adapters` 调 `close()`（逐个 try/except，失败 `logger.warning`
  且不中断其余适配器），真正释放 httpx 连接。

  **回归**：`TestLifecycleGating`（4 条：paused 拒 / stopped 拒 / resume 恢复 / shutdown 关适配器）。
  **反证**：源码退回 HEAD ⇒ 3 条红；`test_resume_restores_routing` 作为**防过度拦截守卫**仍绿（正确）。
- ✅ **真实且已修（2026-09-15 实测并修复）**：消息历史原先**全内存**，进程重启即全丢且无痕。

  **修法**：`NetworkBus.__init__(history_path: Optional[str] = None)` 新增可选持久化——
  **默认 `None` = 完全保持旧行为（不落盘）**，调用方显式传入路径才启用。
  落盘用 JSON Lines（`_append_history` 追加、`_rewrite_history` 在超过 `_max_history` 裁剪后重写、
  `_load_history` 启动时回放）。**持久化失败不吞**：异常记入 `stats()["history_persist_errors"]`
  （含原因），投递本身照常完成——「历史没存下」变成可见事实而非沉默损失。

  **回归**：`TestHistoryPersistence`（3 条：默认不写文件 / 跨重启保留 / 失败进 `history_persist_errors`
  且不影响投递）。**反证**：源码退回 HEAD ⇒ 3 条全红。
