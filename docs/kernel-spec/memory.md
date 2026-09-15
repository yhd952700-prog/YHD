# Memory Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> - **真实实现**：`src/kernels/memory/__init__.py:268` 的 `MemoryKernel`。`lifecycle` 在 `__init__` 内动态置为 `UNINITIALIZED`（:283），并有四个方法 :578/:581/:584/:589（本类未在类级写 `lifecycle: KernelLifecycle = ...` 注解，而是在构造时赋值，行为与协议一致）。
> - **进程级入口**：`get_memory_kernel()`（`src/kernels/memory/__init__.py:599`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。本类具备 `lifecycle` 状态与四个生命周期方法（存在即 READY）。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；`recall` 不强制 TTL 过期；无 backend 抽象（硬绑 SQLite）；`MemoryTierManager`/`get_tier_manager` 为死代码；`correlation_id` 为本地 uuid 不真连事件总线。
>
> **性质提示**：本规范整体是「目标态契约 + 已核对现状」，不是「现状规范」。已核对的现状以上述行号为准；未标注的段落（尤其是 §9 的缺口清单）以代码为准，待实测后修订。
>

## 1. 定义

Memory Kernel 是 Human-Sovereign Agent OS 的**多层（L0–L7）记忆存储内核**，负责 Agent 上下文增强与状态持久化。它提供带 scope 过滤的完整 CRUD、按 tier（short/mid/long/persistent）晋升压缩整合，并通过 SQLite 写穿（store.py）实现跨进程重启保留记忆。在架构中，它承上承接 execution 的中间状态、context 的压缩输入，并为 audit 提供记忆落盘证据，是"Agent 拥有记忆"能力主张的承载者。

**依赖**：`src._time`、`src.kernels._crosscutting`（`@kernel_action` 织入）、`src.kernels.memory.store`（SQLite 写穿）、`src.ai.observability.observe`；不依赖 Mem0（Mem0 在 `src/knowledge/memory.py` 另一层）。被 execution 间接消费，是 `_crosscutting` 星型 hub 的单向叶子。

## 2. 目标

- 按 L0–L7 多层 scope 过滤存储与召回（`MemoryScope`，`__init__.py:36`）。
- 多 tier 存储：SHORT_TERM / MID_TERM / LONG_TERM / PERSISTENT（`MemoryTier`，`__init__.py:28`），支持晋升压缩（`compress`，`__init__.py:420`）。
- 跨进程持久化：经 `MemoryStore`（store.py:36）写穿 SQLite，env `MEMORY_DB_PATH` 可配（store.py:41）。
- 完整 CRUD：`store` / `recall` / `scope_filter` / `stats`（分别在 `__init__.py:270 / :314 / :366 / :396`）。
- 与事件总线关联以支持审计追踪（docstring 声明 `_correlation_id`，`__init__.py:11`）。
- 层级晋升压缩：将快变 tier 的多条 `MemoryEntry` 打包/语义摘要为慢变 tier 单条（`compress`，`__init__.py:420`），被合并源 id 记录为 `provenance.consolidated_from`（`__init__.py:482`）形成溯源链。

**差距（现状，基于代码实测，非引用外部审计文档）**：
- TTL 在 `recall` 时**不强制**：`recall`（`__init__.py:314-359`）不过滤 `expires_at`；唯一清理 `_evict_tier` 仅由 `auto_cleanup`（`__init__.py:165`）触发，而运行时无调度 → 过期条目被无限召回。
- **无 backend 抽象**：单硬绑定 SQLite，不可插拔（Mem0 / 内存 / PostgreSQL 均不可换）。
- 死单例 `MemoryTierManager`（`__init__.py:124-199`）从未被 `MemoryKernel` 使用（后者用自身 `self._entries`，`__init__.py:252`）。
- 事件关联是假的：`correlation_id` 为本地 `uuid.uuid4()[:8]`（`__init__.py:59`），memory 不 import event kernel，审计只靠 `@kernel_action` 装饰器。
- scope 语义是地板非天花板（`_scope_matches`，`__init__.py:361-364`）。

## 3. 生命周期（现状已核对）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`（`UNINITIALIZED → INITIALIZING → READY ↔ PAUSED → STOPPED`，及 `ERROR`）。

- **UNINITIALIZED**：`MemoryKernel()` 构造后，`lifecycle` 在构造内置为 `UNINITIALIZED`（`__init__.py:283`）。
- **INITIALIZING → READY**：`initialize()`（`:578`）置 `lifecycle=READY`；`__post_init__` 内 `self._store = MemoryStore(db_path)`（store.py:39）并 `_load_persisted()`（`__init__.py:261`）重建 `_entries`；坏条目诚实跳过不拖垮启动（`__init__.py:267`）。
- **PAUSED / RESUME**：`pause()/resume()`（`:584/:589`）已存在，带状态校验（非法迁移抛 `KernelStateError`）并仅置位 `lifecycle`（PAUSED/READY）；未真正挂起读写。语义目标：PAUSED 时应拒绝 `store`/`recall`。
- **STOPPED**：`shutdown()`（`:581`）置 `lifecycle=STOPPED`；应 `MemoryStore.close()`（store.py:183）释放 SQLite 连接；当前 `clear()`（`__init__.py:510`）清数据而非关连接，需实测确认是否已关连接。
- **ERROR**：`_load_persisted` 外抛异常或 `MemoryStore` 建表失败（`_init_db`，store.py:53）应转入 ERROR 而非静默。

## 4. 输入模型

- `store(key: str, value: Any, tier: MemoryTier=MID_TERM, scope: MemoryScope=L1, tags: Optional[Set[str]]=None, ttl: Optional[timedelta]=None) -> MemoryEntry`（`__init__.py:270`，`@kernel_action("memory.store")`）。
- `recall(key: str, scope: MemoryScope=L1, tier_filter: Optional[List[MemoryTier]]=None, tags: Optional[Set[str]]=None) -> Optional[MemoryEntry]`（`__init__.py:314`）。
- `scope_filter(query_scope: MemoryScope, max_access_count/min_age/max_age) -> List[MemoryEntry]`（`__init__.py:366`）。
- `compress(source_tier, target_tier=None, scope=L1, summarizer=None, max_entries=None, older_than=None, target_key=None) -> Optional[MemoryCompression]`（`__init__.py:420`）。
- 持久化层输入：`MemoryStore.persist(entry: Dict[str,Any])`（store.py:86，字段字典约定见 store.py:11-15）。

## 5. 输出模型

- `store` 返回 `MemoryEntry`（`__init__.py:48`），并写穿 `MemoryStore.persist`（`__init__.py:310`）。
- `recall` 返回命中的 `MemoryEntry`（或 `None`）。
- `compress` 返回 `MemoryCompression`（`__init__.py:65`），含 `source_ids` 溯源；合并后写 target、删 source（store.py:96/132）。
- `stats()` 返回 tier/scope 分布与访问计数（`__init__.py:396`）。
- 持久化：`memory_store.db`（env `MEMORY_DB_PATH`，store.py:41），跨进程保留。

## 6. 错误处理（现状已核对）

对齐 `_base.py`：`KernelError` / `KernelNotInitializedError` / `KernelStateError` / `KernelPermissionError` / `KernelCapabilityError` / `KernelConfigurationError`。

- **缺失**：`store`/`recall` 在 READY 前调用应抛 `KernelNotInitializedError`；当前是否在各方法入口做 `lifecycle` 校验需以代码实测确认（类已具备 `lifecycle` 字段与方法）。
- **配置错误**：`MemoryStore` 建表失败、`MEMORY_DB_PATH` 不可写 → 应抛 `KernelConfigurationError`（当前仅裸 `sqlite3.Error`，store.py:53）。
- **权限越界**：scope 地板语义（`_scope_matches`，`__init__.py:361`）应上升为 `KernelPermissionError` 当越权召回被尝试。
- **当前错误点**：`recall` 不过滤 `expires_at` → 属**静默谎报可用**；`_load_persisted` 吞掉坏条目异常（`__init__.py:267`）应改为 `KernelError` 上报告警。
- `MemoryTierManager`/`get_tier_manager`（`__init__.py:189`）为死路径，其异常不应扩散到 `MemoryKernel`。

## 7. 权限边界（现状已核对）

- **scope**：L0–L7（仅 Memory Scope 语义，依 `KERNEL-CANON.md` C11 锁定），由 `identity`/`memory` 已实现过滤。
- **capability**：需 `memory.store` / `memory.recall` / `memory.compress` / `memory.auto_cleanup` 动作能力（均为 `@kernel_action` 织入点）。
- **与 policy/security 边界**：Policy 的 `scope_enforcement` 规则**已激活**（L0，precedence=100，DENY 越权），并非失活；`_adjudicate` 调用 `evaluate_policy_simple` 时传 `scope=None`。memory 自身的 scope 过滤由本地 `_scope_matches` 实现，是否经 Policy 统一裁决需以代码实测确认，不可断言"未受 Policy 裁决"。
- **越界/重复**：`MemoryBackend` 抽象缺失 → 不可插拔、与 `src/knowledge/memory.py:583` 的 `create_memory_manager` 形成双实现（以代码实测为准）。

## 8. 测试要求（DoD：Implemented+Tested+Observable+Permissioned+Audited+Documented）

- **单元**：`store`→`recall` round-trip；`tier_filter` 跨 tier 独立召回（`__init__.py:314`）；`compress` 晋升并溯源 `source_ids`（`__init__.py:500`）；`stats` 统计准确。
- **边界**：同 key 不同 tier/不同 scope 的正确隔离；`scope_filter` 地板语义（仅返回 ≥ 查询 scope 的条目，`__init__.py:378`）。
- **失败路径测试**：
  1. **TTL 强制**：`store(ttl=小值)` 后过期，`recall` 必须返回 `None`（复现过期召回缺陷）。
  2. **backend 抽象**：inject 一个内存 backend 验证可插拔（目标态）。
  3. **关联真实性**：验证 `correlation_id` 真能经 event 总线追踪。
  4. **持久化重建**：进程重启后 `_load_persisted` 正确恢复且坏行被跳过（`__init__.py:261`）。
  5. **生命周期**：READY 前调用 `store` 应抛 `KernelNotInitializedError`。
  6. **压缩整合**：`compress` 合并同源 scope 条目并保持 `provenance.consolidated_from` 溯源（`__init__.py:482`），且禁止跨 scope 合并（`:438-440`）；验证跨 scope 调用被拒。

## 9. 目标态契约与已知缺口（尚未实现，待代码实测）

以下条目为**目标态契约**，并非全部已在 HEAD 实测确认；落地前须以当前代码重新核实，不可照抄：

- `recall` 应强制 TTL 过期，而非无限召回过期条目。
- 应引入 `MemoryBackend` 抽象，使 SQLite / 内存 / PostgreSQL 可插拔，并收敛与 `src/knowledge/memory.py` 的双实现。
- `pause()/resume()` 当前仅置位 `lifecycle`，应真正拒绝 PAUSED 态的 `store`/`recall`。
- `shutdown()` 应关闭 SQLite 连接而非仅清数据；READY 前的方法调用应统一抛 `KernelNotInitializedError`。
- 移除死代码 `MemoryTierManager`/`get_tier_manager`；使 `correlation_id` 真正连入事件总线以实现审计追踪。
