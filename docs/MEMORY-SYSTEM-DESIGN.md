# Phase 4 — Memory System 设计文档 (MEMORY-SYSTEM-DESIGN)

> 阶段目标：把记忆从"单一 SQLite 表"升级为**可插拔后端 + 多类型记忆**的长期可演进子系统，
> 支持 SQLite / PostgreSQL / Vector DB，并为 Episodic / Semantic / Procedural / Identity 记忆提供统一入口。
>
> 设计原则（项目硬性要求）：
> 1. 不增加代码量优先 —— 复用既有 `MemoryStore` 接缝与 `VectorStore`，不重造。
> 2. 所有改动可回滚 —— 新增 `backends.py` 整体删除即回滚；`MemoryKernel` 仅加一个可选关键字参数。
> 3. 不破坏已有 API —— `MemoryKernel.store/recall/compress/...` 与 `get_memory_kernel()` 签名不变。
> 4. 先设计方案，再编码 —— 本文即方案。

---

## 1. 当前状态（实测事实，均带 file:line）

| 事实 | 位置 |
|------|------|
| `MemoryKernel`（逻辑层）：内存工作集 `_entries: Dict[key_hash, MemoryEntry]` + 写穿持久化 | `src/kernels/memory/__init__.py:245-314` |
| `MemoryTier`: SHORT_TERM / MID_TERM / LONG_TERM / PERSISTENT（默认 TTL 5min/1h/1d/30d） | `:29-34`, `store():287-294` |
| `MemoryScope`: L0..L7（`_scope_matches` 用 `scope_order[entry] >= scope_order[query]`） | `:37-46`, `:363-366` |
| `key_hash = f"{tier.value}:{key}"`（主键 upsert，同 key 同 tier 覆盖） | `:308` |
| **后端接缝已存在**：`MemoryStore`（SQLite+WAL）暴露 `persist/delete/clear/load_all/count/close` | `src/kernels/memory/store.py:36-188` |
| 默认落盘 `MEMORY_DB_PATH` → 兜底 `<repo>/memory_store.db`；`:memory:` 为纯内存 | `store.py:33,39-48`；`__init__.py:249-252` |
| `get_memory_kernel(db_path=None)` 单例 | `__init__.py:541` |
| `AgentMemory`（agent 绑定）**已正确**委托给 canonical kernel（打 `agent:{principal}` tag） | `src/ai/agent_factory.py:107-150` |
| `VectorStore`（进程内向量库）`insert/search/delete/count/get` | `src/knowledge/vector_store.py:15-107` |
| `src/knowledge/memory.py` 集成 mem0，但全项目 `use_mem0=False`（死配置） | `memory.py:3`；`employee.py:251` 等 |
| ORM/SQLAlchemy 层：`Base`/engine/sessionmaker，`DATABASE_URL` 兜底 `sqlite:///./liuhao_ai_os.db` | `src/integrations/orm_models.py:16-29,937-955` |

**结论**：后端接缝（`MemoryStore`）与向量能力（`VectorStore`）**都已存在**，Phase 4 不是"从零造存储抽象"，而是
**把隐式接缝显式协议化 + 增加后端实现 + 把 5 种记忆类型做成既有 tier/tag 模型之上的约定层**。

---

## 2. 目标能力（用户要求的 5 类记忆 + 后端抽象）

| 用户要求 | 落地方式（不破坏既有模型） |
|----------|---------------------------|
| Short-term | 直接复用 `MemoryTier.SHORT_TERM`（TTL 5min） |
| Episodic | `MID_TERM` + tag `episodic`（`AgentRuntime._persist` 已写 `episodic:run:{id}`） |
| Semantic | `MID/LONG_TERM` + tag `semantic`；相似检索走 `VectorStore`（可选，flag 控制） |
| Procedural | `LONG_TERM/PERSISTENT` + tag `procedural` |
| Identity | `PERSISTENT` + tag `identity`（身份权威仍在 identity kernel，这里只做记忆镜像） |
| MemoryBackend 抽象 | 新增 `MemoryBackend` Protocol；`SqliteBackend` 包既有 `MemoryStore`（默认，零行为变化） |
| SQLite/PostgreSQL/Vector | SQLite=默认；PostgreSQL=Phase 7 启用（配 `DATABASE_URL`）；Vector=复用 `VectorStore` |

**关键决策**：5 种记忆**不新增 `MemoryTier` 枚举值**（避免破坏既有 API），而是作为 **(tier, tag) 约定** 由一层
`MemoryType` 映射 + 便捷方法提供。这一点必须诚实说明：它是"约定层"，不是新的存储引擎。

---

## 3. 架构

```
                 ┌──────────────────────────────────────────────┐
  调用方 ───────▶│  MemoryKernel (逻辑层, 既有, 仅加 1 个可选参数) │
 (store/recall)  │   _entries 工作集 + 写穿                       │
                 └───────────────┬──────────────────────────────┘
                                 │ self._store  (MemoryBackend)
                 ┌───────────────▼──────────────────────────────┐
                 │  MemoryBackend (Protocol, 新增 backends.py)    │
                 │   persist / delete / clear / load_all / count  │
                 │   / close                                      │
                 └───┬───────────────┬───────────────┬───────────┘
                     │               │               │
              SqliteBackend    InMemoryBackend   PostgresBackend
              (包 MemoryStore)  (dict)            (Phase 7 stub →
                                                 DATABASE_URL)
                                 │
                     ┌───────────▼────────────┐
                     │ VectorIndex (可选)       │  ← 复用 src/knowledge/vector_store.py
                     │  语义相似检索            │
                     └────────────────────────┘
```

- `MemoryKernel.__init__` 增加 **可选关键字参数 `backend`**（默认 `None` → 现状 `MemoryStore(db_path)`），
  `__post_init__` 里 `self._store = backend or MemoryStore(db_path)`。**默认路径逐字节不变**（可回滚）。
- `MemoryBackend` 是 `runtime_checkable` Protocol，方法签名**与 `MemoryStore` 完全一致** → `MemoryStore` 天然符合，无需改动。
- `get_memory_backend(spec=None)` 工厂：`spec ∈ {None, "sqlite", "memory", "postgres"}`，env `MEMORY_BACKEND` 兜底 default `sqlite`。

---

## 4. 5 类记忆的约定 API（新增，附加式）

```python
class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    EPISODIC   = "episodic"
    SEMANTIC   = "semantic"
    PROCEDURAL = "procedural"
    IDENTITY   = "identity"

_TYPE_MAP: Dict[MemoryType, tuple[MemoryTier, str]] = {
    MemoryType.SHORT_TERM: (MemoryTier.SHORT_TERM, "short_term"),
    MemoryType.EPISODIC:   (MemoryTier.MID_TERM,    "episodic"),
    MemoryType.SEMANTIC:   (MemoryTier.LONG_TERM,   "semantic"),
    MemoryType.PROCEDURAL: (MemoryTier.LONG_TERM,   "procedural"),
    MemoryType.IDENTITY:   (MemoryTier.PERSISTENT,  "identity"),
}

# MemoryKernel 新增便捷方法（不改既有方法）：
def remember(self, memory_type: MemoryType, key: str, value: Any,
             scope: MemoryScope = MemoryScope.L1, tags: Optional[Set[str]] = None,
             ttl: Optional[timedelta] = None) -> MemoryEntry
def recall_type(self, memory_type: MemoryType, key: str,
                scope: MemoryScope = MemoryScope.L1) -> Optional[MemoryEntry]
```

`remember` = `store(tier=_TYPE_MAP[t][0], tags={_TYPE_MAP[t][1]} | tags)`；`recall_type` = `recall(..., tier_filter=[tier])` 再按 tag 过滤。

---

## 5. 回滚方案

- `src/kernels/memory/backends.py` 纯新增 —— `git rm` 即回滚。
- `MemoryKernel` 改动 = 新增 1 个可选参数 + `__post_init__` 一行 `or` 兜底 —— 不传 `backend` 时行为与现在完全一致。
- `MemoryType` / `remember` / `recall_type` 为纯新增方法 —— 无既有调用方受影响。

---

## 6. 测试计划（新增 `tests/kernels/memory/test_memory_backends.py`）

1. `test_protocol_conformance`：`isinstance(MemoryStore(":memory:"), MemoryBackend)` 为真（runtime_checkable）。
2. `test_factory_default_sqlite`：`get_memory_backend(None)` 返回 Sqlite 后端；`get_memory_backend("memory")` 返回内存后端。
3. `test_inmemory_backend_roundtrip`：persist/load_all/count/delete/clear 全覆盖。
4. `test_kernel_default_unchanged`：`MemoryKernel(db_path=":memory:")` 仍能 store/recall（无 backend 参数）。
5. `test_kernel_with_injected_backend`：注入内存后端，store 后 `load_all` 可见。
6. `test_typed_memory_roundtrip`：`remember(EPISODIC,...)` 后 `recall_type(EPISODIC,...)` 命中，且 tag 正确。

运行：`tests/kernels/memory/` 子集（避免全量 pytest MemoryError）。

---

## 7. 风险

- **tag 约定 ≠ 强制**：调用方仍可直接 `store()` 绕过类型层 → 记为约定，不做运行时拦截（避免破坏既有调用方）。
- **PostgreSQL 后端**：本阶段只提供工厂分支与协议占位，**不引入 psycopg 运行时依赖**（真启用留 Phase 7，配 `DATABASE_URL`）。
- **VectorIndex**：复用 `VectorStore`（进程内、无外部依赖）；embedding 生成仍走既有 provider，本阶段不做隐式网络调用。
- **存储碎片**：项目存在 3 个 `Repository` 类、2 个 `AuditStore`（Phase 1 审计已知）→ 本阶段**不碰**，列入 Phase 7 收敛清单。

---

## 8. 实施步骤

1. 写 `src/kernels/memory/backends.py`（Protocol + InMemory + Sqlite 包装 + 工厂 + `MemoryType`/`_TYPE_MAP`）。
2. `MemoryKernel` 加可选 `backend` 参数 + 便捷方法 `remember/recall_type`。
3. 写 `tests/kernels/memory/test_memory_backends.py`。
4. 跑 `tests/kernels/memory/` + `tests/kernels/` 相关子集回归。
5. 出变更报告 + 风险说明；**不提交**（保持可回滚）。
