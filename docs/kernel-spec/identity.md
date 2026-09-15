# Identity Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> - **真实实现**：`src/kernels/identity/__init__.py:141` 的 `IdentityManager`。`lifecycle` 字段见 :143；`initialize/shutdown/pause/resume` 见 :576/:579/:582/:587。
> - **进程级入口**：`get_identity_manager()`（`src/kernels/identity/__init__.py:598`，构造后立刻 `initialize()`）。
> - **生命周期判定：已实现**。本类具备 `lifecycle` 字段与四个生命周期方法（存在即 READY）。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发，且 `pause/resume` 仅置位状态。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；`check_permission` 信任调用方传入的 `identity_id`、未绑已认证会话；内置 `system` 身份带 `admin`；非 human 身份不持久化。
>
> **性质提示**：本规范整体是「目标态契约 + 已核对现状」，不是「现状规范」。已核对的现状以上述行号为准；未标注的段落（尤其是 §9 的缺口清单）以代码为准，待实测后修订。
>

> 权威定义：`docs/spec/KERNEL-CANON.md` §1（identity，依据 DL §112）
> 真实源码：`src/kernels/identity/__init__.py`

## 1. 定义

Identity Kernel 是 Human-Sovereign 架构的"身份与权限基座"。它负责 Agent / 服务 / 人类的身份生命周期、权限授予与回收、L0–L7 作用域过滤（注意：此处 L0–L7 仅指 Memory Scope，见 CANON §1 锁定说明），以及所有权限操作的审计痕迹。它是其余 kernel 做裁决时引用的"谁在行动"的唯一事实来源，但不依赖其它任何 kernel（单向依赖，Policy 懒导入 identity）。

## 2. 目标

- 创建并唯一标识身份（`create_identity`，`identity/__init__.py:322`）。
- 以 L0–L7 作用域约束权限授予与检查（`grant_permission:438`、`check_permission:538`）。
- 仅持久化人类身份，保证主权通道重启后仍可用（`create_human_identity:374`、`_persist_human_identity:284`）。
- 提供完整、防篡改的权限审计轨迹（`AuditEntry:127`、`audit_trail:515`）。
- 以正向白名单判定"已验证人类"，支撑 Policy / Security 的主权覆盖（`is_human_identity:69`）。

**当前实现与目标的差距（现状，基于代码实测，非引用外部审计文档）**：
- 重复 principal 时 `create_identity` 返回 `None` 而非抛错（`identity/__init__.py:334-344`）→ 调用方易静默丢失身份。
- 内置 `system` 身份默认带 `permissions={"admin"}`（`:151-158`）。
- 非 human 身份无持久化路径，进程重启即失（仅 human 走 `_store`，`:193`/`:256`）。
- 种子 human 绕过 `@kernel_action` 直接构造（`:256`）以避递归 → 种子不被策略裁决/审计。
- `check_permission` 信任调用方传入的 `identity_id`（`:538-559`），未绑定已认证会话。

## 3. 生命周期（现状已核对）

对齐统一基座 `src/kernels/_base.py` 的 `KernelLifecycle`（`UNINITIALIZED → INITIALIZING → READY ↔ PAUSED → STOPPED`，以及 `ERROR`）。

- 当前 `IdentityManager`（`identity/__init__.py:141`）**已具备** `lifecycle` 字段（`:143`，默认 `UNINITIALIZED`）与四个生命周期方法（`:576/:579/:582/:587`）。实例经全局单例 `get_identity_manager()`（`:598`）懒加载，首次构造即 `initialize()` 进入 `READY`（存在即 READY）。
- `initialize()`（`:576`）：契约为进入 `READY`（幂等）。当前实现为置 `lifecycle=READY`。
- `shutdown()`（`:579`）：契约为进入 `STOPPED` 并释放/落盘内存审计。当前实现为置 `lifecycle=STOPPED`（内存 `_audit_log` 是否 flush 需以代码实测确认）。
- `pause()/resume()`（`:582/:587`）：当前实现仅置位 `lifecycle`（PAUSED/READY），未挂起具体写入语义。语义目标：PAUSED 态应拒绝 `create_identity`/`grant_permission`，resume 回到 READY。
- 错误转入 `ERROR`：当 `_persistence.resolve_human_identity_store()`（`:193`）配置非法或 store 读写异常时，应进入 `ERROR`（见 §6）。

## 4. 输入模型

- `create_identity(principal: str, permissions: Optional[Set[str]]=None, scope: IdentityScope=L1, trust_score: float=0.5, metadata: Optional[Dict]=None) -> AgentIdentity`（`identity/__init__.py:322`）。
- `create_human_identity(principal, permissions=None, trust_score=1.0, display_name=None, metadata=None)`（`:374`）。
- `grant_permission(identity_id: str, permission: str, scope: IdentityScope=L1, reason: str="") -> bool`（`:438`）。
- `revoke_permission(identity_id, permission, reason="") -> bool`（`:483`）。
- `check_permission(identity_id, permission, scope=L1) -> bool`（`:538`）。
- 关键数据结构：`AgentIdentity`（`:111`，含 `id/principal/permissions/scope/trust_score/status/metadata`）、`IdentityScope`（`:92`，L0–L7）、`IdentityStatus`（`:104`，ACTIVE/SUSPENDED/DEACTIVATED）。

## 5. 输出模型

- 返回值：`create_identity`/`create_human_identity` 返回 `AgentIdentity`；`grant_/revoke_permission` 返回 `bool`（失败/未找到返回 `False`，重复 principal 返回 `None`，`:344`）。
- 审计：`AuditEntry` 列表 `_audit_log`（`:146`），经 `audit_trail(identity_id, scope, since)`（`:515`）输出；human 经 `JsonFileStore`/`SqliteHumanIdentityStore` 落盘（`:284-311`）。
- 统计：`stats()`（`:561`）输出身份总数、活跃数、按 scope 分布。

## 6. 错误处理（现状已核对）

对齐统一异常基座（`src/kernels/_base.py`）：
- `KernelNotInitializedError`：在 READY 之前调用 `create_identity` 等需就绪态操作时抛出。
- `KernelStateError`：非法生命周期转换（如在 STOPPED 调 `grant_permission`）。
- `KernelPermissionError`：当权限 scope 超过身份 scope（`grant_permission:454` 当前仅返回 `False`）应改抛此异常。
- `KernelConfigurationError`：human store 配置非法（`_persistence` 解析失败）时抛出。
- `KernelCapabilityError`：此处不适用（能力由 capability kernel 管辖）。

**当前缺失/错误（现状实测，非引用外部审计）**：
- 重复 principal 应抛 `KernelConfigurationError`/`ValueError` 而非返回 `None`（`:334-344`）。
- `grant_permission`（`:449`）、`revoke_permission`（`:493`）、`check_permission`（`:547`）在身份未找到时静默返回 `False`，未区分"无身份"与"被拒"，违反 fail-closed。

## 7. 权限边界（现状已核对）

- 本 kernel 自身需要的 capability / scope：依据 `capability/__init__.py` 内置注册 `agent_identity`（owner=`identity_kernel`，scope=L4，`:405-415`）。身份与权限生命周期变更（`identity.create_identity`/`grant_permission`/`revoke_permission`）属权威变更，须经 Policy `internal_service_denied_actions` 拦截（policy `:132-134`）。
- 与 Policy / Security 边界：Identity **只判定"谁"**，不裁决"能否执行某动作"——裁决归 Policy（ABAC）与 Security（RBAC/ABAC）。Policy 的 `human_sovereignty`/`internal_service_allow` 通过 `is_human_identity`（`:69`）与 `_is_verified_*`（policy `:643/708`）回调本 kernel，单向依赖（Policy 懒导入 identity）。
- **当前越界/重复（现状实测）**：
  - `check_permission`（`:538`）重复实现了本属 Security 的权限检查逻辑，与 `kernels/security:315/367` 形成双份权限判定。
  - `system` 身份自带 `admin`（`:151-158`）构成隐性提权根。
  - `identity_id` 未绑定会话（`:538-559`），与 Security 的列号权限命名（`context:read`）未对齐。

## 8. 测试要求

按 DoD（Implemented+Tested+Observable+Permissioned+Audited+Documented）需满足：

- 单元：重复 principal 抛错（非返回 `None`，`:334-344`）；`system` 身份 `admin` 语义与回收（`:151`）；`is_human_identity` 正向白名单（空 kind / service 均回报 False，`:69`）。
- 边界：非 human 身份重启丢失（持久化缺口，现状实测）；scope 超过身份 scope 时 `grant_permission` 拒绝（`:454`）；`check_permission` 对 SUSPENDED 返回 False（`:551`）。
- 失败路径：human store 不可读时 fail-closed 不回退机器身份（`:212-215`）；`grant_permission` 身份不存在时抛 `KernelPermissionError` 而非静默 False（`:449`）。
- 权限边界：验证 Policy 经 `is_human_identity` 回调本 kernel 不形成导入环（以代码实测为准）。

## 9. 目标态契约与已知缺口（尚未实现，待代码实测）

以下条目为**目标态契约**，并非全部已在 HEAD 实测确认；落地前须以当前代码重新核实，不可照抄：

- `pause()/resume()` 当前仅置位 `lifecycle`，未真正挂起/恢复身份写入；应使 PAUSED 态拒绝 `create_identity`/`grant_permission`。
- `shutdown()` 应 flush 内存 `_audit_log` 到持久 store 后再置 `STOPPED`（当前是否落盘需实测）。
- 重复 principal 应抛异常而非返回 `None`；`check_permission` 等未找到身份时应区分"无身份"与"被拒"（fail-closed）。
- `check_permission` 与 Security 的权限判定应收敛为单一权威，消除双份判定。
- `identity_id` 应与已认证会话绑定，与 Security 列号权限命名体系对齐。
