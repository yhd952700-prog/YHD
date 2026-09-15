# Audit Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **真实实现**：`src/kernels/audit/__init__.py:104` 的 `AuditStore`。`lifecycle` 字段见 :106；`initialize/shutdown/pause/resume` 见 :539/:542/:545/:550。
> - **进程级入口**：`get_audit_store()`（`src/kernels/audit/__init__.py:560`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。`AuditStore` 已接入生命周期协议，具备 `lifecycle` 字段与四个方法；存在即 READY（首次使用时惰性构造并 `initialize()`）。`shutdown/pause/resume` 无后台驱动方，仅由显式调用者触发。
> - **本文档性质**：目标态契约 + 已核对现状。凡标注「目标态（尚未实现）」的段落为设计意图，非以 HEAD 验证过的事实；未标注的段落以代码为准。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。

## 1. 定义

Audit Kernel 是 Human-Sovereign Agent OS 的**防篡改审计内核**，提供基于 SHA256 哈希链的不可变审计日志（SQLite 后端），支持 `correlation_id` 关联查询、完整性校验与完整事件生命周期管理。它依据 Definition Lock §113，是系统合规与可追溯性的最终事实来源（sink），仅依赖标准库（`json/hashlib/sqlite3`），被 `_crosscutting` 织入层懒导入以记录全部 kernel action 判决（`src/kernels/audit/__init__.py:104`）。

## 2. 目标（现状可实现的能力）

- **记录**：`log_event(event_type, principal_id, scope, outcome, details, correlation_id)`（`src/kernels/audit/__init__.py:206`）写哈希链（`_log_event_locked` :225）。
- **完整性校验**：`verify_integrity() → (is_ok, total)`（`src/kernels/audit/__init__.py:297`），校验链链接、序列连续性、内容完整性、尾部锚点（:318-384）。
- **关联查询**：`query_events`（按 principal/scope/时间/correlation_id 过滤，`src/kernels/audit/__init__.py:386`）。
- **不可变**：历史事件不可修改，链状态锚点防截断（`chain_state` `src/kernels/audit/__init__.py:162`）。
- **人类主权覆盖记录**：`AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE`（`src/kernels/audit/__init__.py:40`）。

## 3. 生命周期（现状）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`（`UNINITIALIZED → INITIALIZING → READY ↔ PAUSED → STOPPED`，以及 `ERROR`）。

- `AuditStore`（`src/kernels/audit/__init__.py:104`）已实现 `lifecycle` 字段（:106）与 `initialize()`（:539）/`shutdown()`（:542）/`pause()`（:545）/`resume()`（:550）。
- **单例**：`get_audit_store()`（:560）为进程级惰性单例，构造后立刻 `initialize()`，不变量为「存在即 READY」。
- **无后台驱动**：`shutdown`/`pause`/`resume` 目前没有任何定时器/守护协程自动流转，仅由显式调用者触发；`src/kernels/_registry.py` 的 `initialize_all()`/`shutdown_all()` 是显式驱动入口（且绝不创建实例）。
- 错误转入 `ERROR`：数据库不可打开 / WAL 初始化失败应抛 `KernelConfigurationError` 并置 `ERROR`（属目标态，见第 8 节）。

## 4. 输入模型

- `AuditEventType`（`src/kernels/audit/__init__.py:29`）：`ACCESS_CHECK/ROLE_GRANT/.../HUMAN_SOVEREIGNTY_OVERRIDE/KERNEL_IMPLEMENTATION/KERNEL_STATUS/PHASE_GATE/STATE_CHANGE`。
- `AuditScope`（`src/kernels/audit/__init__.py:47`）：`L0-L7`。
- `log_event(event_type, principal_id, scope, outcome, details=None, correlation_id=None) → AuditEvent`（:206）。
- `verify_integrity() → Tuple[bool, int]`（:297）。
- `query_events(principal_id?, scope?, start_time?, end_time?, outcome?, event_type?, correlation_id?, limit?, reverse=False)`（:386）。
- `AuditEvent`（`src/kernels/audit/__init__.py:59`）：`event_id/event_type/principal_id/scope/timestamp/correlation_id/outcome/details/event_hash/prev_event_hash`，`compute_hash` 用 SHA256（:73）。

## 5. 输出模型

- `AuditEvent`（:59）：单条已签名事件，`event_hash` 由 `compute_hash` 生成。
- `get_event(event_id) → Optional[Dict]`（`src/kernels/audit/__init__.py:493`）。
- `get_stats() → Dict`（事件类型/结果分布、`db_path`，:519）。
- **持久化**：SQLite（WAL 模式，:139），`audit_events` + `chain_state` 双表（:141-165）；由 `AUDIT_DB_PATH` 控制路径（默认仓库根 `audit_store.db`，:117）。
- **全局单例**：`get_audit_store()`（:560）。

## 6. 错误处理（对齐 `src/kernels/_base.py`）

`KernelError`(基类) / `KernelNotInitializedError` / `KernelStateError` / `KernelPermissionError` / `KernelCapabilityError` / `KernelConfigurationError`。

- **现状**：`verify_integrity` 在校验期捕获 `ValueError/KeyError`（坏枚举/坏 JSON）时计入 `broken`（:372）而非抛异常；其余路径不抛标准异常。
- **目标态（尚未实现）**：DB 初始化失败（`_init_db`）应抛 `KernelConfigurationError`；写入并发冲突异常应包装为 `KernelError`；`query_events`/`get_event` 在 store 未就绪时调用应抛 `KernelNotInitializedError`。

## 7. 权限边界

- 事件 `scope` 来自调用方传入（`log_event` 的 `scope: AuditScope`，:207）；`@kernel_action` 织入层写审计事件时把 action scope 置为 `L0`（`src/kernels/_crosscutting.py:160`）——这是当前代码实测可见的行为。
- 权限名（冒号形式，authority A）见 `src/kernels/security/_permission_map.py`：`audit:query`（auditor）、`audit:log`（admin）；两者目前均无 authority B（policy 的 point-dot 动作）对应项。访问控制收敛设计见 `docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md`。
- audit 是纯 sink，不裁决；其权威应与 `policy`/`security` 的审计日志统一为单一权威（属目标态，见第 8 节）。

## 8. 目标态契约与已知缺口（尚未实现，待代码实测）

以下为设计契约要求与当前实现的**待核实差距**。**这些不是以 HEAD 验证过的事实**，需在改代码前以当前代码实测核对；不得引用任何已删除的审计文档。

- `verify_integrity()` 无后台/定时自动调用：篡改仅被动发现，契约要求在 `initialize` 与定时任务触发校验。
- `get_event`（:493）未持 `self._lock`，与写路径/查询路径（:404 持 RLock）不一致，存在读到链中间态的风险——需代码实测确认。
- SQLite 无界增长：无保留/轮转策略。
- 审计权威统一：identity/security 的内存审计与持久 `AuditStore` 是否统一为单一权威，需代码实测核对。
