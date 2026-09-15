# Policy Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
>
> - **真实实现**：`src/kernels/policy/__init__.py:357` 的 `PolicyEngine`。`lifecycle` 字段见 :359；`initialize/shutdown/pause/resume` 见 :849/:852/:855/:860。
> - **进程级入口**：`get_policy_engine()`（`src/kernels/policy/__init__.py:871`，构造后立刻 `initialize()`）。
> - **生命周期判定：部分实现**。生命周期协议已落地，本类具备 `lifecycle` 字段与四个方法。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；`scope_enforcement`/`capability_required`/`quota_enforcement` 在真实路径失活（`_adjudicate` 不传 `required_scope`/`required_capability`/`estimated_cost`/`agent.*`/`resource.*`）；`default_deny` 可被 `scope` 关掉形成 fail-open；`NOT_APPLICABLE` 非拒绝。`policy:manage` 权限在 authority A 内、无 authority B 对应动作。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> **性质提示**：本规范整体是「设计意图 / 契约（目标态）」而非「现状事实描述」。其 §3 称 `pause()`/`resume()`「当前无实现」已与当前代码不符（现已实现，但仅置位）。生命周期/权限边界章节可作目标契约，但「差距」段落（尤其 P0 结论）需重测后修订。
>

> 权威定义：`docs/spec/KERNEL-CANON.md` §1（policy，依据 DL §112）
> 真实源码：`src/kernels/policy/__init__.py`

## 1. 定义

Policy Kernel 是系统的 ABAC 策略引擎与权限边界裁决者。它定义并评估规则（human_sovereignty / internal_service_allow / default_deny / scope_enforcement / capability_required / quota_enforcement），产出 ALLOW / DENY / NOT_APPLICABLE / DEFER 决策，并为每个决策提供可追溯链。它是 Human-Sovereign 架构中"谁能做什么"的唯一策略权威（与 Security 的 RBAC 平行存在，见 §7）。不持久化。

## 2. 目标

- 定义带条件与动作的策略规则（`PolicyRule`，`policy/__init__.py:259`）。
- 评估访问决策（`evaluate:506`、`evaluate_simple:610`、`PolicyDecision:281`）。
- 支持优先级与冲突解决（`list_rules` 按 precedence 排序，`:471-478`）。
- 强制 L0–L7 scope 权限（规则 `scope_enforcement:401`）。
- 提供完整决策审计轨迹（`PolicyDecision.traceability:288`）。

**当前实现与目标的差距**：
- **`scope_enforcement`/`capability_required`/`quota_enforcement` 在真实路径是死的**：`_adjudicate`（cross-cutting L232-237）只传 `action={"name","risk_level"}`，从不传 `required_scope`/`required_capability`/`estimated_cost`/`agent.capabilities`/`resource.available` → 三规则无输入可匹配，永不触发。
- **`evaluate` 可按 scope 关掉 `default_deny`（fail-open）**：scope 过滤 `7<=scope`（`:538-540`），传 `scope=L3` 时 `default_deny`（scope=L7，`:394`）被排除 → 未匹配动作返回 `NOT_APPLICABLE` 而非 `DENY`。
- `evaluate` 不镜像 `actor→agent`（仅 `evaluate_simple` 做，`:625-632`）。
- `NOT_APPLICABLE` 非拒绝，消费方须显式当 deny，但织入不处理。

## 3. 生命周期

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- 当前 `PolicyEngine` **未继承** `Kernel` 基类，经 `get_policy_engine()`（`:762`）懒加载：首次构建即 `_register_builtin_policies`（`:322`，6 条规则）。
- `initialize()`：契约 READY（幂等）；当前构建期同步注册内置规则。
- `shutdown()`：契约 STOPPED；当前无持久状态（规则全内存）。
- `pause()/resume()`：当前无实现；PAUSED 语义应为拒绝 `evaluate` 新决策（返回 fail-closed）。
- 错误转入 `ERROR`：当规则条件求值抛非预期异常（应为 `PolicyCondition._safe_compare` 已吞，`:227-240`）时，若越界应置 ERROR。

## 4. 输入模型

- `evaluate(context: Dict[str,Any], scope: Optional[PolicyScope]=None, policy_set_id: Optional[str]=None) -> PolicyDecision`（`:506`）。
- `evaluate_simple(actor: Dict, action: Dict, resource: Optional[Dict]=None, scope=None) -> PolicyDecision`（`:610`，镜像 actor→agent）。
- `register_rule(rule: PolicyRule) -> bool`（`:450`）、`create_policy_set(...)`（`:480`）。
- 数据结构：`PolicyCondition`（`:162`，attribute/operator/value/negate）、`PolicyRule`（`:259`）、`PolicyEffect`（`:34`）、`PolicyScope`（`:42`，L0–L7）、`PolicyOperator`（`:54`）。
- 关键点：`actor.type`（`"human"`/`"service"`）选 verifier；`verified` 由 `_compute_verified` 从 Identity 重算（`:688-706`），绝不信任调用方。

## 5. 输出模型

- `PolicyDecision`（`:281`）：`decision: PolicyEffect`、`matched_rules`/`denied_rules`/`abstained_rules`、`traceability: List[str]`、`context`、`evaluated_at`。
- 属性 `is_allowed`（`:293`）、`is_denied`（`:297`）。
- **不持久化**；决策经由 `@kernel_action` 织入记录审计事件（默认 `enforce=False` 仅记录不拦截）。
- 内部服务预批准/拒绝清单（`INTERNAL_SERVICE_ALLOWED_ACTIONS:82`、`INTERNAL_SERVICE_DENIED_ACTIONS:118`）。

## 6. 错误处理

对齐统一异常基座（`src/kernels/_base.py`）：
- `KernelPermissionError`：当决策为 `DENY` 且 enforcement 闸门开启时，由织入层抛出。当前默认 OFF，故策略仅"建议"。
- `KernelConfigurationError`：注册重复 `rule.id` 当前仅返回 `False`（`:453`），应改抛以暴露配置冲突。
- `KernelStateError`：PAUSED 态调用 `evaluate` 应 fail-closed。
- `KernelNotInitializedError`：READY 之前调用 `evaluate`。

**当前缺失/错误**：
- `default_deny` 可被 scope 关掉（`:538-540` → `:394`）形成 fail-open，应恒评估（与 scope 解耦）。
- `evaluate` 不镜像 actor→agent（`:506-608`），导致 `scope_enforcement`/`capability_required`/`quota_enforcement` 在 `evaluate` 路径恒不触发——三规则失活。
- `NOT_APPLICABLE` 非拒绝（`:601-608`），enforcement 闸门未定义一律当 deny。
- `_adjudicate`（cross-cutting L232-237）缺参 → 三规则输入缺失，需填充 `action.*/agent.*/resource.*`。

## 7. 权限边界

- 本 kernel 需要的 capability / scope：内置 `policy_engine`（owner=`policy_kernel`，scope=L4，`:362-371`）。规则变更（`policy.manage`）被 Security `RBACRole.ADMIN` 保护（security `:202`）。
- 与 Security 边界：Policy 是 ABAC 权威；Security 是 RBAC+ABAC 平行权威（`decide_access`，security `:548`），**二者互不调用**。裁决权归属冲突——应收敛为单一权威。
- **当前越界/重复**：
  - 安全双权威互相矛盾：Policy 与 Security 各判各的，决策可相互矛盾。
  - 命名错配：Security 用冒号权限（`"context:read"`，security `:194`），系统其余用点号动作（`"context.set_scope"`，context `:94`）→ 两套授权永不交叉。
  - Policy 懒导入 identity（`:666`/`:685`/`:725`），依赖方向 Policy→Identity，不反向。

## 8. 测试要求

- 单元：`human_sovereignty` 仅对正向白名单人类放行（`:328-344`、`:643-686`）；`internal_service_allow` 仅对预批准动作列表（`:82`/`:360-380`）；`default_deny` 兜底（`:382-396`）。
- 边界：**修 `_adjudicate` 后 `scope_enforcement`/`capability_required`/`quota_enforcement` 必须触发→ 集成测试证明 agent.scope < required_scope 时 DENY、`agent.capabilities` 缺 `required_capability` 时 DENY、`resource.available < estimated_cost` 时 DENY**。
- 失败路径：**`default_deny` 与 scope 解耦后，传 `scope=L3` 不再 fail-open→ 断言未匹配动作返回 DENY 而非 NOT_APPLICABLE**；`NOT_APPLICABLE` 在 enforcement 闸门一律当 deny；`evaluate` 与 `evaluate_simple` 对 agent.scope 判定一致（`:506` vs `:625-632`）。
