# Capability Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **真实实现**：`src/kernels/capability/__init__.py:105` 的 `CapabilityRegistry`。`lifecycle` 字段见 :107；`initialize/shutdown/pause/resume` 见 :290/:293/:296/:301。
> - **进程级入口**：`get_capability_registry()`（`src/kernels/capability/__init__.py:311`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。`CapabilityRegistry` 已接入生命周期协议；存在即 READY（首次使用时惰性构造并 `initialize()`）。`shutdown/pause/resume` 无后台驱动方，仅由显式调用者触发。
> - **本文档性质**：目标态契约 + 已核对现状。标注「目标态（尚未实现）」者为设计意图；未标注者以代码为准。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。

## 1. 定义

Capability Kernel 是系统能力的"权威注册中心"。它维护每个能力的元数据、版本、可追溯链（capability → kernel → owner）与 L0–L7 作用域检查，是执行层按 `capability_id` 派发动作、以及 Policy `capability_required` 规则判定"agent 是否持有某能力"的唯一事实来源。纯内存、无持久化。

## 2. 目标（现状可实现的能力）

- 注册能力并维护多索引（namespace/owner/tag）（`CapabilityRegistry.register`，`src/kernels/capability/__init__.py:114`）。
- 按 id+version 查找与版本兼容判定（`lookup:142`、`CapabilityEntry.is_compatible_with:72`）。
- 追溯能力链（`get_traceability:222`、`traceability_chain:62`）。
- 提供 scope 检查（`check_scope:192`、`ScopeCheckResult:94`）。
- 支持能力废弃/退役与别名迁移（`deprecate:229`、`retire:253`）。
- 内置 12 个内核能力 + `python_compute` 在首次构建时注册（:303-461）。

## 3. 生命周期（现状）

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- `CapabilityRegistry`（`src/kernels/capability/__init__.py:105`）已实现 `lifecycle` 字段（:107）与四个方法（:290-301）。
- **单例**：`get_capability_registry()`（:311）为进程级惰性单例，构造后立刻 `initialize()`，不变量为「存在即 READY」。
- **无后台驱动**：`shutdown`/`pause`/`resume` 仅由显式调用者触发；`pause`/`resume` 当前仅置位状态（PAUSED 语义「拒绝 register/deprecate 写操作」属目标态，见第 8 节）。

## 4. 输入模型

- `register(capability: CapabilityEntry) -> bool`（:114）。
- `lookup(id, namespace="core", version=None) -> Optional[CapabilityEntry]`（:142）。
- `check_scope(capability_id, requested_scope, namespace="core", version=None) -> ScopeCheckResult`（:192）。
- `deprecate(capability_id, namespace, replacement_id=None, replacement_namespace=None) -> bool`（:229）。
- `retire(capability_id, namespace) -> bool`（:253）。
- 数据结构：`CapabilityEntry`（:45，含 `id/version/namespace/name/scope/owner/status/dependencies/traceability_chain`）、`CapabilityScope`（:25，L0–L7）、`CapabilityStatus`（:37，ACTIVE/DEPRECATED/RETIRED/EXPERIMENTAL）。

## 5. 输出模型

- `register` 返回 `bool`（新注册 True / 更新 False）。
- `lookup` 返回 `Optional[CapabilityEntry]`（无版本时取最高 ACTIVE 版本，:154-169）。
- `check_scope` 返回 `ScopeCheckResult`（:94，`allowed/requested_scope/capability_scope/reason/traceability`）。
- `query`（:171）按过滤条件返回 `List[CapabilityEntry]`；`get_all_by_owner/namespace/tag`（:263-276）。
- **不持久化**：全部驻留 `self._capabilities` 内存字典（:108）。

## 6. 错误处理（对齐 `src/kernels/_base.py`）

- `KernelNotInitializedError`：READY 之前调用 `lookup`/`check_scope`。
- `KernelCapabilityError`：当 `check_scope` 判定 `requested_scope` 超过能力 `scope` 时应改抛此异常（当前 `check_scope` 仅返回 `allowed=False`，:212）；能力不存在时亦应抛 `KernelCapabilityError`。
- `KernelConfigurationError`：`deprecate` 别名解析到不存在的 replacement 时属配置脆弱，应显式报错而非静默构造。
- `KernelStateError`：对已 RETIRED 能力再次 `deprecate`/`retire`。

## 7. 权限边界

- 本 kernel 需要的 capability / scope：内置 `capability_registry`（owner=`capability_kernel`，scope=L3，:318-327）。能力注册/退役（`capability.register`/`deprecate`/`retire`）属权威变更，由 Policy 的 `internal_service_denied_actions` 拦截（policy `:120-122`）。
- 权限名（冒号形式，authority A）见 `src/kernels/security/_permission_map.py`：`capability:lookup`（viewer）、`capability:manage`（admin，对应 authority B 的 `capability.register`/`deprecate`/`retire`）。访问控制收敛设计见 `docs/ACCESS-CONTROL-CONVERGENCE-DESIGN.md`。
- Capability **只回答"某 agent 是否声明持有某能力"**，裁决归 Policy 的 `capability_required`（规则本身已实现并生效，见 `src/kernels/policy/__init__.py:466`）。

## 8. 目标态契约与已知缺口（尚未实现，待代码实测）

以下为设计契约要求与当前实现的**待核实差距**，非以 HEAD 验证过的事实；不得引用任何已删除的审计文档。

- 能力门禁接线：`capability_required` 规则已生效（policy `:466`），但 kernel action 织入层是否稳定向 `evaluate` 传入 `action.required_capability` / `agent.capabilities`，需代码实测核对（原稿称「从不提供」属未核实断言，已删除）。
- `deprecate` 做 3 次冗余 `lookup` 且别名版本回退 `'1.0.0'`（:246-249），脆弱，需代码实测确认。
- 退役/废弃能力仍留在 `_capabilities` 并被 `query`/`get_all_by_*` 返回（:263-276）——是否应过滤 RETIRED 待定。
- 无持久化：运行时注册的能力重启即失。
- `CapabilityScope`（:25）与 identity/policy/security/resource 各自重复定义 L0–L7，宜抽到 `_crosscutting` 共享（设计意图）。
