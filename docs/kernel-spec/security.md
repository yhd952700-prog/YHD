# Security Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
>
> - **真实实现**：`src/kernels/security/__init__.py:177` 的 `SecurityEngine`。`lifecycle` 字段见 :179；`initialize/shutdown/pause/resume` 见 :747/:750/:753/:758。
> - **进程级入口**：`get_security_engine()`（`src/kernels/security/__init__.py:768`，构造后立刻 `initialize()`）。
> - **生命周期判定：部分实现**。生命周期协议已落地，本类具备 `lifecycle` 字段与四个方法。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；与 Policy 存在双访问权威；`_enforce_principal_scope` 无记录即 no-op（scope 天花板默认失效）；`_eval_condition` 数字比较回退字符串比较；`decide_access` 默认「仅记录」不拦截执行。冒号权限（`context:read` 等）在 authority A（本 kernel）内裁决，与 authority B 的 point-dot 动作互不交叉（见 `_permission_map.py`）。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> **性质提示**：本规范整体是「设计意图 / 契约（目标态）」而非「现状事实描述」。其 §3 称 `pause()`/`resume()`「当前无实现」已与当前代码不符（现已实现，但仅置位）。生命周期/权限边界章节可作目标契约，但「差距」段落需重测后修订。
>

> 权威定义：`docs/spec/KERNEL-CANON.md` §1（security，依据 DL §112）
> 真实源码：`src/kernels/security/__init__.py`

## 1. 定义

Security Kernel 提供 RBAC + ABAC 访问控制、scope 天花板强制与内存审计。它依据 DL §112 还声明支持 Vault Transit 加密与人类主权覆盖（human_override）。它与 Policy 平行、互不调用，是系统中另一套独立的访问权威。仅依赖 `src._time` 与 `_crosscutting`，不导入 Policy 也不导入 Identity（identity 经懒导入校验人类，`:652`）。

## 2. 目标

- RBAC 角色层级访问控制（`check_rbac:315`、`RBACRole:101`）。
- ABAC 属性条件评估（`check_abac:367`、`ABACRule:146`）。
- 组合 RBAC+ABAC 决策（`_evaluate_access:509`、`check_access:504`）。
- L0–L7 scope 天花板强制（`_enforce_principal_scope:235`、`set_principal_scope:223`）。
- 完整访问决策审计轨迹（`AuditLogEntry:157`、`audit_trail:668`）。
- 人类主权覆盖（`decide_access(human_override)`, `:548`）。

**当前实现与目标的差距**：
- **`human_override` 零校验放行**：任何调用方可为任意权限拿到 ALLOW，违反 OD-010。*现状*：源码已落地 SEC-5 修复，`decide_access` 经 `self._is_verified_human(principal_id)`（`:574-576`）复用 `is_human_identity` 校验，非法覆盖被拒绝并审计（`:589-607`）。该缺口在文档中保留为历史证据，当前实现已部分补救。
- 平行重复 Policy 的 RBAC/ABAC，二者决策可相互矛盾。
- 命名错配：Security 用冒号权限（`"context:read"`, `:194`），系统其余用点号动作（`"context.set_scope"`）→ 两套授权永不交叉。
- scope 强制是 opt-in 且未用：`_enforce_principal_scope` 无记录时返回 None（no-op，`:246`）。
- `_eval_condition` 数字比较失败回退**字符串比较**（`:475`/`:482`）→ `"10">"9"` 字符串比较为 False，静默错结果。
- 死代码：`SecurityPrincipal` 从未实例化（`:121`）；`ESCALATE` 从未产出（`:89`）。

## 3. 生命周期

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- 当前 `SecurityEngine` **未继承** `Kernel` 基类，经 `get_security_engine()`（`:722`）懒加载：首次构建即 `_seed_default_rules`（`:184`，12 条冒号权限 RBAC 规则）。
- `initialize()`：契约 READY（幂等）；当前构建期同步播种规则。
- `shutdown()`：契约 STOPPED；当前内存审计 `_audit_log`（`:181`）应 flush 到持久 store。
- `pause()/resume()`：当前无实现；PAUSED 语义应为拒绝 `decide_access`/`check_rbac`。
- 错误转入 `ERROR`：当 Vault 客户端库（已移除幻影依赖，`:45-49`）或被懒导入 identity 模块不可用时不应静默——应记 ERROR。

## 4. 输入模型

- `decide_access(principal_id, permission, scope="L1", attributes=None, human_override=False) -> Dict[str,Any]`（`:548`，标注 `@kernel_action`）。
- `check_rbac(principal_id, permission, scope="L1") -> AccessDecision`（`:315`）。
- `check_abac(principal_id, permission, scope="L1", attributes=None) -> AccessDecision`（`:367`）。
- `check_access(principal_id, permission, scope, attributes) -> AccessDecision`（`:504`）。
- `grant_rbac_role`/`revoke_rbac_role`（`:274`/`:294`）、`set_abac_rule`（`:489`）、`set_principal_scope`（`:223`）。
- 数据结构：`AccessDecision`（`:83`，ALLOW/DENY/CONDITIONAL/DEFER/ESCALATE）、`RBACRole`（`:101`）、`ABACRule`（`:146`）、`AuditLogEntry`（`:157`）。
- scope 治理：`_VALID_SCOPES`（`:59`）、`_is_valid_scope`（`:63`）、`_PROTECTED_SECURITY_ATTRS`（`:76`）。

## 5. 输出模型

- `decide_access` 返回 `Dict`（`:609`）：`decision/principal_id/permission/scope/rbac_check/abac_check/reason/human_override`。
- `check_rbac`/`check_abac`/`check_access` 返回 `AccessDecision`。
- 审计：`AuditLogEntry` 写入 `_audit_log`（`:181`），经 `audit_trail`（`:668`）输出。
- **不持久化**：审计与规则全内存；Vault 文档声称但客户端库不可用已移除桩（`:45-49`，真实实现在 `src/security/vault_client.py`）。

## 6. 错误处理

对齐统一异常基座（`src/kernels/_base.py`）：
- `KernelPermissionError`：当 `decide_access`/`check_rbac` 判 DENY 且 enforcement 开启时抛出。当前 `decide_access` 自身是"仅记录"的 kernel action（`:547`），连拒绝都不拦执行→ 需补强制执行闸门。
- `KernelConfigurationError`：`set_principal_scope` 收到非法 scope（`:231` 当前静默 return）应改抛。
- `KernelStateError`：PAUSED 态调用 `decide_access`。
- `KernelNotInitializedError`：READY 之前调用裁决。
- `KernelCapabilityError`：此处不适用（能力归 capability kernel）。

**当前缺失/错误**：
- `decide_access` 经 `is_human_identity`+主权通道校验（现状 `:574-576`）已修 SEC-5；但 `decide_access` 仍"仅记录"不拦截（`:547`）→ 拒绝不强制。
- `_enforce_principal_scope` 无记录即 no-op（`:246`）→ scope 天花板默认失效。
- `_eval_condition` 数字比较失败回退字符串比较（`:475`/`:482`）→ 静默错结果，应 fail-closed。
- `SecurityPrincipal`（`:121`）死代码、`ESCALATE`（`:89`）从未产出。

## 7. 权限边界

- 本 kernel 需要的 capability / scope：内置 `security_enforcement`（owner=`security_kernel`，scope=L5，`:428-437`）。RBAC 权威变更（`security.grant_rbac_role`/`revoke_rbac_role`/`set_abac_rule`）被 Policy `internal_service_denied_actions` 拦截（policy `:151-153`）；`security.decide_access` 被预批准（policy `:110`）。
- 与 Policy 边界：Security 是 RBAC+ABAC 平行权威；Policy 是 ABAC 权威（policy `:313`）。**谁裁决定、谁执行的边界不清**。
- **当前越界/重复**：
  - **安全双权威**：`kernels/security` 与 `src/security/`（rbac/abac/rbac_abac/audit_policy）同时活引用、互不调用→ 授权结论不确定，须收敛为单一权威。
  - **命名错配**：冒号权限（`context:read`, `:194`）vs 点号动作（`context.set_scope`）→ 两套授权永不交叉。
  - `decide_access` 懒导入 identity（`:652`）校验人类，与 Policy 共用 `is_human_identity` 判定。

## 8. 测试要求

- 单元：`check_rbac` 角色命中返回 ALLOW（`:337-352`）、缺失角色 DENY（`:354-365`）；`_enforce_principal_scope` 超天花板 DENY（`:248-249`）；`_eval_condition` 数字比较正确（`:469-482`，断言 `"10">"9"` 为 True 而非字符串比较 False）。
- 边界：`_is_valid_scope` 拒绝 L9/None（`:63-65`）；`set_principal_scope` 非法 scope 被拒（`:231`）；`_merge_attributes` 受保护属性不被请求覆盖（`:433-456`，S3）。
- 失败路径：**`human_override=True` 对非人类 principal 必须被拒并审计→ 断言 system/service 身份拿不到 ALLOW**；`decide_access` 对被拒决策应强制（非仅记录）；**安全双权威收敛后，决策与 Policy 不矛盾**；删 `SecurityPrincipal` 死代码（`:121`）与未产出的 `ESCALATE`（`:89`）。
