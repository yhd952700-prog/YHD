# Resource Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
>
> - **真实实现**：`src/kernels/resource/__init__.py:146` 的 `ResourceQuotaManager`。`lifecycle` 字段见 :148；`initialize/shutdown/pause/resume` 见 :499/:502/:505/:510。
> - **进程级入口**：`get_resource_manager()`（`src/kernels/resource/__init__.py:521`，构造后立刻 `initialize()`）。
> - **生命周期判定：部分实现**。生命周期协议已落地，本类具备 `lifecycle` 字段与四个方法。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；无持久化（重启即失）；父 scope 向下借 `system` L3 预算；`enforce_limits` 忽略 `reserved` 导致超卖漏报；`release` 无 `allocation_id` 时溯源缺失。`resource:allocate`/`resource:query` 权限在 authority A 内裁决（`resource:allocate` 对应 B 的 `resource.allocate`）。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> **性质提示**：本规范整体是「设计意图 / 契约（目标态）」而非「现状事实描述」。其 §3 当前状态段称 `ResourceQuotaManager`「未接入 `_base`、无 lifecycle 字段」已与当前代码不符（现已接入）。生命周期/权限边界章节可作目标契约，但「差距」段落需重测后修订。
>

## 1. 定义

Resource Kernel 是 Human-Sovereign Agent OS 的**资源配额内核**，统一管理六类资源
（CPU / Memory / Storage / Token / Time / Cost，见 `ResourceType` `src/kernels/resource/__init__.py:26-33`）
的配额定义、分配、预留、提交与释放，并支持 scope 继承与实时用量度量。它依据 Definition Lock
§112，是 execution 内核等下游消费者的预算闸门，当前为纯内存实现（`__init__.py:107`）。

## 2. 目标

- **配额定义**：`create_quota(scope, owner, resource_type, limit)`（`__init__.py:137`）。
- **分配/预留**：`allocate` 在配额内预留（`__init__.py:183`）。
- **提交/释放**：`commit` 把预留转已用（`__init__.py:249`），`release` 归还（`__init__.py:271`）。
- **强制/度量**：`enforce_limits`（`__init__.py:385`）、`get_usage`（`__init__.py:315`）、`stats`。
- **scope 继承**：`_find_parent_quota` 向上借用（`__init__.py:227`）。

**当前实现与目标的差距**：
- **无持久化** → 重启即失。
- **父 scope 继承向下借**：`_find_parent_quota`（`__init__.py:227-247`）在请求 scope 无配额时找
  **更高** scope 号（更多自主权）→ 仅 seed 的 `system` L3 配额（`__init__.py:126-131`）被
  L0-L2 静默消费（低 scope 花高 scope 系统预算）。
- **`enforce_limits` 忽略 `reserved`**（`__init__.py:385-392`）：只查 `used>limit`，未把
  `reserved` 计入，导致预留超卖不被发现。
- **`release` 无 `allocation_id` 时** `used -= min(amount,used)`（`__init__.py:304-313`）→
  无溯源，可与真实分配脱节。
- **默认仅 system 范围配额**，无每 agent 配额（`_init_default_quotas` `__init__.py:122`）。

## 3. 生命周期

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`
（`UNINITIALIZED → INITIALIZING → READY ↔ PAUSED → STOPPED`，以及 `ERROR`）。

- **当前状态**：`ResourceQuotaManager`（`__init__.py:107`）**未接入 `_base`**，无 `lifecycle`
  字段；构造即 `INITIALIZING→READY` 并 `_init_default_quotas`（`__init__.py:117/122`）。
- **契约（目标态）**：
  - `initialize()`：重建/加载配额持久化（当前缺失），进入 `READY`。
  - `shutdown()`：刷新持久化并释放锁，进入 `STOPPED`。
  - `pause()/resume()`：暂停/恢复分配活动（如配额冻结）。
- **错误转入 ERROR**：持久化失败或锁竞争异常应置 `ERROR`。

## 4. 输入模型

- `ResourceType`（`__init__.py:26`）：`CPU/MEMORY/STORAGE/TOKEN/TIME/COST`。
- `ResourceScope`（`__init__.py:36`）：`L0-L7`。
- `create_quota(scope, owner, resource_type, limit, metadata=None) → Quota`
  （`@kernel_action("resource.create_quota")` `__init__.py:137`）。
- `allocate(resource_type, amount, scope, owner, purpose="", expires_at=None, metadata=None)
  → Optional[Allocation]`（`@kernel_action("resource.allocate")` `__init__.py:183`）。
- `commit(allocation_id) → bool`（`@kernel_action("resource.commit")` `__init__.py:249`）。
- `release(resource_type, amount, scope, owner, allocation_id=None) → bool`
  （`@kernel_action("resource.release")` `__init__.py:271`）。
- `Quota`（`__init__.py:48`）：`resource_type/scope/owner/limit/used/reserved/...`，带
  `available/can_allocate/is_exhausted` 属性（`__init__.py:61-76`）。

## 5. 输出模型

- `Quota`（`__init__.py:48`）：配额定义与实时占用。
- `Allocation`（`__init__.py:79`）：`id/quota_id/amount/owner/purpose/expires_at`，分配记录。
- `ResourceUsage`（`__init__.py:92`）：`get_usage` 返回的用量快照（`__init__.py:315-352`）。
- **持久化**：**当前无**（纯内存 `_quotas/_allocations`）。
- **事件**：`create_quota/allocate/commit/release` 均经 `@kernel_action` 织入记录（`__init__.py:137/183/249/271`）。

## 6. 错误处理

对齐 `src/kernels/_base.py` 的异常族：
`KernelError`(基类) / `KernelNotInitializedError` / `KernelStateError` /
`KernelPermissionError` / `KernelCapabilityError` / `KernelConfigurationError`。

- **当前实际**：分配/提交/释放失败均返回 `False` 或 `None`（`__init__.py:196/203/254/284/306`），
  不抛标准异常；`amount <= 0` 直接返回 `None`（`__init__.py:195`）。
- **应抛异常的点**：
  - `commit` 发现 `quota.reserved < allocation.amount`（`__init__.py:262-263`）应抛
    `KernelStateError`（状态不一致）而非返回 `False`。
  - `release` 引用不存在的 `allocation_id`（`__init__.py:284`）应抛 `KernelNotInitializedError`。
  - 配额持久化失败应抛 `KernelConfigurationError`。
- **当前缺失/错误**：
  - **父 scope 向下借**：`_find_parent_quota`（`__init__.py:227-247`）默认允许 L0-L2 借用
    `system` 的 L3 预算，属越权预算消费，应改为 opt-in 且限定。
  - **`enforce_limits` 忽略 `reserved`**（`__init__.py:385-392`）：仅 `used>limit` 判违规，
    预留额度超卖不被检测，应改为 `used+reserved>limit`。

## 7. 权限边界

- **所需 scope**：配额按 `ResourceScope(L0-L7)` 维度组织（`__init__.py:36/119`），但分配不
  校验调用方 scope 授权，仅做配额存在性检查。
- **所需 capability**：分配/提交应受 `policy` 的 `quota_enforcement` 规则裁决（当前因
  `_adjudicate` 不传 `estimated_cost/resource.available` 而失活）。
- **与 policy/security 边界**：配额裁决未真正接入 policy；被 execution 内核消费但 execution
  实际未使用 `resource` 导入（`execution/__init__.py:28` 死导入）。
- **当前越界/重复**：
  - **scope 集合重复**：`ResourceScope`（`__init__.py:36`）与 plugin/capability/security 各自
    重复 L0-L7 定义，应抽到 `_crosscutting` 共享。
  - **向下借系统预算**：低 scope 静默消耗高 scope（`system` L3）配额，违反 scope 隔离语义。

## 8. 测试要求

通过 DoD 必须满足：

- **单元**：`create_quota` 更新既有键（`__init__.py:150-154`）；`allocate` 成功扣 `reserved`
  （`__init__.py:210`）；`commit` 把 `reserved→used`（`__init__.py:265-266`）；`get_usage`
  快照字段正确（`__init__.py:339-349`）。
- **边界**：`allocate(amount<=0)` 返回 `None`（`__init__.py:195`）；`allocate` 超额返回
  `None`（`__init__.py:205-206`）；`release` 超额只减到 0（`__init__.py:292/309`）。
- **失败路径**：
  - **父 scope 向下借**：对 L0 owner 请求无配额资源时，断言不会静默借到 `system` L3 预算
    （除非显式开启继承，`__init__.py:200-202/227-247`）。
  - **`enforce_limits` 忽略 reserved**：构造 `used+reserved>limit` 但 `used<limit` 的配额，
    断言 `enforce_limits` 必须报违规（当前漏报，`__init__.py:385-392`）。
  - **`release` 无 `allocation_id`**：验证无溯源释放不会把 `used` 减到负数（`__init__.py:304-313`）。
  - **持久化缺口**：标记 `xfail`/回归用例，配额在 manager 重建后必须可恢复（当前纯内存必失）。
