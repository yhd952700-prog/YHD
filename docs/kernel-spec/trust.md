# Trust Kernel 规范（kernel-spec）

> **诚实状态头（合入时基于当前 HEAD 实测写入；已清除对仓库外审计文档的引用）**
>
> - **真实实现**：`src/kernels/trust/__init__.py:170` 的 `TrustManager`。`lifecycle` 字段见 :172；`initialize/shutdown/pause/resume` 见 :565/:568/:571/:576。
> - **进程级入口**：`get_trust_manager()`（`src/kernels/trust/__init__.py:592`，构造后立刻 `initialize()`）。
> - **生命周期判定：部分实现**。生命周期协议已落地，本类具备 `lifecycle` 字段与四个方法。
>   - **是否自动驱动**：**是**——进程级惰性单例，首次使用时构造并立即 `initialize()`（存在即 READY）；但 `shutdown`/`pause`/`resume` 无任何后台驱动方，仅由显式调用者触发。
> - **未接线/仅置位的部分**：`pause()`/`resume()` 仅置位状态；无持久化（重启即失）；`get_score` 忽略 `_revoked` 已撤销实体仍可返回旧分；置信度仅按事件计数。信任事件不转发 audit store / 事件总线。
> - **外部审计引用**：无 —— 本文件不引用 `ARCHITECTURE-AUDIT.md`（该文档不在仓库中，其结论已被项目纪律认定不准，不作为权威来源）。原稿中的此类「审计 §X.Y」引用已全部删除或改写。
>
> **性质提示**：本规范整体是「设计意图 / 契约（目标态）」而非「现状事实描述」。其 §3 称 `pause()`/`resume()`「当前无挂勾」、§6 称「当前无 lifecycle 校验」已与当前代码不符（现已具备 `lifecycle` 与四个方法）。生命周期/权限边界章节可作目标契约，但「差距」段落需重测后修订。
>

## 1. 定义

Trust Kernel 是 Human-Sovereign Agent OS 的**信任评分与信任链内核**，负责按实体/按 scope 维护 0–1 信任分、通过 BFS 实现信任链传递并逐跳衰减、支持撤销级联，以及做 scope 感知的访问评估。在架构中它由 `ai/governance.py` 与 `ai/network_gateway.py` 消费，是"Agent 能评估对方可信度并据此授权"能力主张的承载者。

**依赖**：仅 `src._time`、`src.kernels._crosscutting`（`@kernel_action` 织入）；是 `_crosscutting` 星型 hub 的单向叶子，不反向依赖上层。消费方为 `src/ai/governance.py` 与 `src/ai/network_gateway.py`。

## 2. 目标

- 评分与更新：`assign_score` / `update_score`（`__init__.py:202 / :238`）。
- 信任链传递+衰减：`establish_trust`（`__init__.py:324`）、`get_trust_chain` BFS（`__init__.py:388`）、`propagate_trust`（`__init__.py:443`）。
- 撤销级联：`revoke`（`__init__.py:453`），移除分数与涉及该实体的链。
- scope 感知访问：`evaluate_trust_for_access`（`__init__.py:516`）。
- 事件记录：`TrustEvent`（`__init__.py:57`），供审计与传播溯源。

**差距**：
- **无持久化**：`TrustManager` 全部状态为内存（`__init__.py:173-177`）→ 重启即失。
- `get_score` 忽略撤销（`__init__.py:294-299`）：已撤销实体分数仍可读，而 `assign/update` 对已撤销抛 `ValueError`（`__init__.py:213/250`）→ 不一致。
- 置信度仅按事件计数 `min(1,count/100)`（`__init__.py:270`），忽略 delta 幅度。
- 衰减/阈值硬编码 `0.9`/hop、`valid>=0.1`（`__init__.py:154/166`），不可配。

## 3. 生命周期

对齐 `src/kernels/_base.py` 的 `KernelLifecycle`。

- **UNINITIALIZED**：`TrustManager()` 构造（`__init__.py:172`），初始化 `_scores/_chains/_events/_revoked` 与默认权重。
- **INITIALIZING → READY**：纯内存、无外部资源，构造即 READY；应在此加载 `TrustStore`。
- **PAUSED / RESUME**：当前无挂勾；暂停时应冻结评分写入。
- **STOPPED**：应 flush 到 `TrustStore`；当前无 shutdown。
- **ERROR**：`establish_trust` 对已撤销实体抛 `ValueError`（`__init__.py:337`）应改为 `KernelStateError`/`KernelPermissionError`；BFS 异常应转入 ERROR。

## 4. 输入模型

- `assign_score(entity_id, initial_score=0.5, scope=TrustScope.L0, reasons=None, confidence=0.5) -> TrustScore`（`__init__.py:202`）。
- `update_score(entity_id, delta, scope=TrustScope.L0, reason="", event_type=NEUTRAL, correlation_id=None) -> TrustScore`（`__init__.py:238`）。
- `establish_trust(from_entity, to_entity, trust_score, scope=TrustScope.L0, expires_in=None, metadata=None) -> TrustChainLink`（`__init__.py:324`）。
- `revoke(entity_id, scope=None, reason="") -> bool`（`__init__.py:453`）。
- `evaluate_trust_for_access(entity_id, required_level=MEDIUM, required_scope=TrustScope.L0) -> bool`（`__init__.py:516`）。
- 类型：`TrustScope`（`__init__.py:27`，L0–L7）、`TrustScore`（`__init__.py:72`）、`TrustChainLink`（`__init__.py:112`）、`TrustChain`（`__init__.py:131`）、`TrustEvent`（`__init__.py:57`）。

## 5. 输出模型

- `get_score` 返回 `Optional[TrustScore]`（`__init__.py:294`）；`get_all_scores`（`__init__.py:301`）。
- `get_trust_chain` 返回 `TrustChain`（含 `composite_score`、`valid`，`__init__.py:132`）。
- `propagate_trust` 返回 float（`__init__.py:443`）。
- `evaluate_trust_for_access` 返回 bool（`__init__.py:516`）。
- `stats()`（`__init__.py:542`）返回实体/链/事件/撤销计数。
- **当前无持久化输出**：`TrustEvent` 仅在内存（`__init__.py:175`），未发事件总线，不可审计。

## 6. 错误处理

对齐 `_base.py` 异常族。

- **缺失**：调用前未 READY 应抛 `KernelNotInitializedError`；当前无 lifecycle 校验。
- **状态错误**：对已撤销实体 `assign/update/establish_trust` 抛 `ValueError`（`__init__.py:213/250/337`）应改为 `KernelStateError` 或 `KernelPermissionError`（撤销 = 失去授权）。
- **权限**：`evaluate_trust_for_access` 失败应抛 `KernelPermissionError` 而非仅返回 False（让调用方区分"不可信"与"评估异常"）。
- **当前错误点（核心缺陷）**：`get_score`（`__init__.py:294`）**不检查 `_revoked`**，已撤销实体仍可返回旧分数；而 `assign/update` 对已撤销抛错 → 读写语义矛盾。应在 `get_score` 内尊重 `_revoked`。
- **置信度误导**：`confidence=min(1,count/100)`（`__init__.py:270`）忽略 delta 幅度，应在计数基础上结合权重幅度。

## 7. 权限边界

- **scope**：`TrustScope` L0–L7（依 `KERNEL-CANON.md` C11）。`get_score_at_scope_or_higher`（`__init__.py:306`）取≥min_scope 的最高 scope 分数 → 地板语义。
- **capability**：需 `trust.assign_score` / `trust.update_score` / `trust.establish_trust` / `trust.revoke` 动作能力。
- **与 policy/security 边界**：信任评估用于访问决策（`evaluate_trust_for_access`），应与 policy 的 ABAC 收敛为单一权威（双权威问题不直接涉及 trust，但需被 policy 消费）。
- **越界/重复**：信任事件应在 `get_score`/`revoke` 时经事件总线发出以便审计；当前无持久化且内存事件不转发 audit store。

## 8. 测试要求（DoD）

- **单元**：`assign_score` 钳制到 [0,1]（`__init__.py:217`）；`update_score` delta 累加与 level 更新（`__init__.py:259/_update_level`）；`get_trust_chain` BFS 多跳与 `is_expired` 过滤（`__init__.py:423`）；`establish_trust` 重复链路覆盖（`__init__.py:359-370`）。
- **边界**：衰减 `0.9**i`（`__init__.py:162`）、`valid>0.1`（`__init__.py:166`）；`scope_order` 比较。
- **失败路径**：
  1. **撤销一致性**：`revoke` 后 `get_score` 必须返回 None；`assign/update` 对已撤销仍应抛错。
  2. **持久化（目标）**：加 `TrustStore` 后断言重启保留分数与撤销集。
  3. **置信度**：验证大 delta 与小 delta 对 confidence 的影响差异。
  4. **级联撤销**：`revoke` 后涉及该实体的链应失效（`__init__.py:473-476`）。
  5. **异常类型**：撤销相关错误应抛 `KernelStateError` 而非裸 `ValueError`（§6）。
  6. **评估访问**：`evaluate_trust_for_access`（`__init__.py:516`）在 `required_scope` 下正确取最高 scope 分数；已撤销实体必须返回 False（`__init__.py:524`）。
