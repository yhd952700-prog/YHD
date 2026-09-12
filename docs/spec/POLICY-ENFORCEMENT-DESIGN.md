# Policy Controlled 真拦截设计方案（提案 · 待裁决）

| 字段 | 值 |
|---|---|
| 状态 | **设计提案，未实施** —— 本轮不改任何代码/行为 |
| 日期 | 2026-09-11 |
| 提出 | Principal Engineer（承接 `AI-LAYER-DOD-AUDIT.md` §3.5.4 与 §5.5 的裁决项） |
| 裁决人 | 用户（项目主权） |
| 关联 | `OD-010`、`AI-LAYER-DOD-AUDIT.md` §3.5.4/§4.1/§5.5、`KERNEL-DOD-AUDIT.md`、`_crosscutting.py`、DL:§112、`UNIFIED-BLUEPRINT.md` 附录 A（DoD 第 5 项） |

---

## 0. TL;DR

1. **纠正一个关键误解**：**"真拦截"在能力层（`src/ai/`）早已存在**，不是空白。`LiuHaoAssistant.chat` / `RuntimeLoop.step` / `NetworkGateway` / `WorldInterface` 四处都在拿到 `deny`/非 allow 后**真的不执行**，且采用 **default-deny** 语义（实测 `chat` 放行、`msg:*` 拒绝）。
2. **真正"只记录、不拦截、且判决无信息"的只有内核层** —— `src/kernels/_crosscutting.py::@kernel_action` 装饰的 **48 个内核动作**：判决恒为 `deny`（实测四档风险等级均如此），且装饰器 additive，`deny` 没有任何执行效果。
3. 所以真实议题不是"要不要真拦截"，而是两个更窄的问题：
   - **Q1**：要不要把**内核横切装饰器**也变成控制点？
   - **Q2**：要不要给**内部 system 主体**一条合法的 ALLOW 路径（否则一旦开启拦截 = 全系统 48 个动作全被拒）？
4. **推荐路线 C（分层分级）**：能力层维持现状（已达标，不动）；内核层保留"记录"，但把判决**从恒 deny 改为白名单驱动**（判决开始携带信息），并**仅对 HIGH/CRITICAL 开启真拦截**。详见 §4。
5. 需用户裁决 **D1–D7**（§5）。若选路线 A，则只需把 DoD 第 5 项措辞明确为"策略判决已记录"，零代码改动。

---

## 1. 现状实证

> 本节全部结论来自**运行时探针**，不是关键字扫描。复现见 §1.5。

### 1.1 判定点全景

| 层 | 判定点 | 判决来源 | **是否真拦截** | 判决是否携带信息 |
|---|---|---|---|---|
| 内核层（48 个动作） | `@kernel_action`（`_crosscutting.py`） | `evaluate_policy_simple(system actor)` | ❌ **从不拦截**（additive） | ❌ **恒 deny** |
| 能力层 · 对话 | `LiuHaoAssistant.chat` / `.chat_stream` | `AgentPolicy.authorize` | ✅ `if not is_allowed: return denied` | ✅ allow/deny |
| 能力层 · 运行环 | `RuntimeLoop.step` | `AgentPolicy.authorize` | ✅ 拒绝执行、不落记忆 | ✅ default-deny |
| 能力层 · 网络 | `NetworkGateway`（`network_gateway.py:370`） | `AgentPolicy.authorize` | ✅ `return denied`，不路由 | ✅ |
| 能力层 · 世界接口 | `WorldInterface.observe/execute` | 注入的 `authorize_fn` | ✅ `return denied/error` | ✅（默认可注入，无注入=allow） |
| 能力层 · 治理 | `SecurityChain.evaluate` | security RBAC/ABAC | ⚠️ **返回 status**（软） | ✅ |
| 能力层 · 治理 | `EmergencyControl.guard` | 全局 kill-switch | ⚠️ **返回 blocked**（软） | ✅ |

**读法**：能力层的「硬拦截」是真拦截（调用方拒绝执行）；治理层的两个是**软拦截**（返回状态，调用方须自觉检查）。**唯一完全空转的是内核层。**

### 1.2 内核层实证（恒 deny）

内置 5 条规则（按 precedence 降序）：

| rule id | precedence | 条件 | action | scope |
|---|---|---|---|---|
| `human_sovereignty` | 1000 | `actor.type==human` ∧ `risk_level∈{HIGH,CRITICAL}` ∧ `actor.verified==True` | ALLOW | L0 |
| `capability_required` | 200 | `action.required_capability` EXISTS ∧ ¬(agent.capabilities ∋ it) | DENY | L1 |
| `quota_enforcement` | 150 | `resource.available` < `action.estimated_cost` | DENY | L2 |
| `scope_enforcement` | 100 | `agent.scope` < `action.required_scope` | DENY | L0 |
| `default_deny` | −1000 | `action` EXISTS | DENY | L7 |

`_adjudicate` 发的是 `actor={"type":"system","verified":True}`、`action={"name":…,"risk_level":…}`、`resource=None`、`scope=None`。逐规则推导：

- `human_sovereignty`：`type != "human"` → 不匹配；
- `capability_required` / `quota_enforcement` / `scope_enforcement`：所需属性缺失 → 条件为 `False`，不匹配；
- `default_deny`：`action` 存在 → **命中，DENY**。

实测（四档风险等级）：

```
risk=LOW      -> deny   trace=['RULE:default_deny:deny']
risk=MEDIUM   -> deny   trace=['RULE:default_deny:deny']
risk=HIGH     -> deny   trace=['RULE:default_deny:deny']
risk=CRITICAL -> deny   trace=['RULE:default_deny:deny']
```

→ **判决与动作、风险完全无关**，是一个常量。装饰器又 additive，故该 `deny` 无执行效果。审计事件已显式标注 `policy_enforced: false` 以避免误读。

### 1.3 能力层实证（真拦截 + default-deny）

注册 `LiuHaoAssistant._ensure_chat_policy()` 的 `liuhao_agent_low_risk_allow`（`actor.type==agent ∧ action.name=="chat"` → ALLOW，precedence 50）后，以 `actor=agent, scope=L1` 逐动作实测：

```
chat                 -> allow           allowed=True   trace=['RULE:liuhao_agent_low_risk_allow:allow']
msg:task             -> not_applicable  allowed=False  trace=[]
msg:query            -> not_applicable  allowed=False  trace=[]
p15.world.execute    -> not_applicable  allowed=False  trace=[]
tool.run             -> not_applicable  allowed=False  trace=[]
```

两个结论：

1. **策略引擎确实能返回 ALLOW**（`human_sovereignty` 之外还有可注册的业务 allow 规则）—— 规则机制是活的，不是死的。
2. 未注册 allow 的动作落在 `NOT_APPLICABLE`（因为 `default_deny` 在 **L7**，被 `scope=L1` 的 scope filter 排除），而调用方一律把「非 allow」当拒绝 —— 于是形成**「引擎 NOT_APPLICABLE + 调用方 fail-closed」**的复合 default-deny。`tests/test_runtime_loop.py` 将这一语义固化为「默认拒绝」测试（`test_denied_message_no_execute_no_memory`）。

> **设计观察（非缺陷，但值得记）**：`default_deny` 的 scope 是 L7，导致它在 L0–L6 的评估中恒被过滤，引擎自身从不返回 `DENY`（只返回 `NOT_APPLICABLE`）。"默认拒绝"实际由**调用方**兜底，而非引擎。这意味着：**任何忘记检查 `is_allowed` 的新调用点都会静默放行** —— 这是路线 C 要顺手收紧的一点。

### 1.4 唯一"记录而非控制"的位置

`src/kernels/_crosscutting.py::@kernel_action` 已在其 docstring 中以 `.. warning::` **诚实登记**了这一点（2026-09-11 实测记录）。48 个装饰动作分布在 14 个内核：

`capability / context / evaluation / event / execution / identity / memory / network / plugin / resource / security / trust`（+ ai 层 audit/observability/personal_context 各 1）。

### 1.5 复现命令

```bash
# 内核层恒 deny + 规则表
.venv/Scripts/python.exe D:/cache/temp/policy_probe.py
# 能力层 gate 结果（allow / not_applicable）
.venv/Scripts/python.exe D:/cache/temp/policy_probe3.py
# 既有回归（锁定"记录契约"与"system actor 恒 deny"）
.venv/Scripts/python.exe -m pytest tests/kernels/test_crosscutting.py \
  tests/test_ai_layer_dod_delegation.py tests/test_runtime_loop.py -q
```

---

## 2. 问题本质

原裁决项写作"Policy Controlled 是否应从『只记录』升级为『真拦截』"，隐含了一个前提：*整个系统的策略维度都不拦截*。**实证否定了这个前提**（§1.1）：能力层已在真拦截。真实缺口被两次混淆放大：

- **混淆一：DoD 语义 vs 安全语义。** DoD 第 5 项字面是「通过 policy engine 判决」（`UNIFIED-BLUEPRINT.md` 附录 A）。内核层**满足字面**（确有判决记录），但不满足「控制」的安全语义。
- **混淆二：内核层 vs 能力层。** 两层的策略成熟度**完全不同**：能力层 default-deny 真拦截；内核层 record-only 常量 deny。混为一谈会得出"推倒重来"的错误结论。

因此本方案的落点收敛为 **Q1（内核装饰器要不要变控制点）+ Q2（要不要给内部主体合法 ALLOW 路径）**。

---

## 3. 候选路线

### 路线 A —— 维持「记录型」，改措辞

不动代码。把 DoD 第 5 项的判定口径明确为「**策略判决已记录**」，并在 `KERNEL-DOD-AUDIT` / `CAPABILITY-REGISTRY` 里把该维度的措辞补齐（承认内核层是记录型、能力层是真控制）。

- ✅ 零风险；诚实；文档与代码一致。
- ❌ `Policy Controlled` 在内核层**名不副实**；48 个动作的策略判决仍不携带信息。

### 路线 B —— 内核层全面真拦截

给 system actor 加放行规则 + 装饰器改为 enforcement gate（DENY → 抛异常），**全部**动作生效。

- ✅ 真正统一的内核控制点。
- ❌ **高风险**：48 个动作 + 400+ 内核用例。若 system actor 的 allow 覆盖不全 → 内核动作成片被拒 → 系统性崩溃。**必须**先有 Q2 的白名单，否则等于自锁。
- ❌ 与既有「审计必须无条件」的设计冲突需重新论证。

### 路线 C —— 分层分级（**推荐**）

- **能力层**：维持现状（已真拦截，含 4 处硬 gate + 2 处软 gate）。**不改**。
- **内核层**：
  1. 引入**内部主体**与**白名单规则**，使 `_adjudicate` 的判决**从恒 deny 变为白名单内 allow / 其余 deny**（判决开始携带信息，仍有信息价值）；
  2. **仅 HIGH/CRITICAL** 开启真拦截（DENY → 拒绝执行）；LOW/MEDIUM 保持记录（additive）。
- ✅ 爆炸半径可控（低风险动作行为不变，400+ 用例基本不动）。
- ✅ 补齐 Q2，为后续任何全面拦截铺路。
- ⚠️ 引入新语义（`PolicyDeniedError` / `DEFER`），需裁决（§5）。

### 路线对比

| 维度 | A 维持 | B 全面拦截 | **C 分层（推荐）** |
|---|---|---|---|
| 代码改动 | 无（仅文档） | 大 | 中 |
| 内核用例回归风险 | 无 | **高** | 低 |
| 判决携带信息 | ❌ | ✅ | ✅ |
| HIGH/CRITICAL 真受控 | ❌ | ✅ | ✅ |
| 与能力层一致性 | 措辞对齐 | 全对齐 | 全对齐 |
| 可回滚性 | — | 差 | 好（开关可切） |

---

## 4. 路线 C 详细设计

> 以下为**设计草案**，伪代码仅示意，非最终实现。

### 4.1 内部主体（Q2 的答案）

新增一类**非人类**的合法主体，与 `human_sovereignty` 并列，避免"system actor 永远 deny"：

```
规则 id: internal_service_allow
  conditions:
    actor.type == "service"            # 新类型，区别于 human / agent / system
    actor.verified == True             # 由引擎从 identity kernel 重算，不信任自填
    action.name IN <内部动作白名单>     # 显式枚举，禁止通配
  action: ALLOW
  scope: L0
  precedence: 900                      # 低于 human_sovereignty(1000)，高于各 DENY
```

- `_adjudicate` 的 actor 改为引用一个**已注册且 ACTIVE 的 service 身份**（复用 `_is_verified_human` 同款核验思路，`_is_verified_service`），而不是 `{"type":"system","verified":True}` 这种裸声明。
- **白名单显式枚举**：不做 `*` 通配，新增内核动作必须显式登记（制造"默认拒绝新动作"的摩擦，这是安全上想要的方向）。

### 4.2 DENY 语义（Q1 的答案）

引入 `PolicyDeniedError`（继承 `PermissionError`）：

```
if policy == "enforce" and decision == DENY and risk in {"HIGH","CRITICAL"}:
    raise PolicyDeniedError(action, decision)
```

- 仅在 `risk_level ∈ {HIGH,CRITICAL}` 且装饰器显式 `enforce=True` 时抛。
- 装饰器保持向后兼容：默认 `enforce=False`（现状），逐点按需开启。
- 抛错前先写审计事件（`policy_enforced: true`），保证「被拒绝」也可审计。

### 4.3 DEFER 语义

当前 `PolicyEffect` 无 `DEFER`（`PolicyAction.ABSTAIN` 是"无意见"，非"待审批"）。建议新增 `PolicyEffect.DEFER`：

- 命中 DEFER → 装饰器**不执行**，返回/抛出「待人工审批」信号（HIGH/CRITICAL 的人工主权闸门）。
- 与 `OD-010`（human 必须经 identity kernel 核验）衔接：DEFER 的解除必须由**已核验 human**作出。

### 4.4 fail-closed 收紧

现状：策略引擎异常 → `_adjudicate` 返回 `None` → 不拦截（**fail-open**）。安全语义要求：

- 内核层：引擎异常且 `enforce=True` → **拒绝**（fail-closed），并记 `policy_error` 审计事件。
- 能力层：把 §1.3 的「引擎 NOT_APPLICABLE + 调用方兜底」改为**引擎返回 DENY**（将 `default_deny` 的 scope 从 L7 调整为覆盖评估 scope，或显式补一条 L1 兜底 DENY）。**此项须评估对 `test_runtime_loop` 等既有 default-deny 测试的影响。**

### 4.5 伪代码（示意）

```python
def kernel_action(action, *, risk_level="LOW", enforce=None, audit=True, observable=True):
    ...
    decision = _adjudicate(action, risk_level)          # now carries info (whitelist)
    should_enforce = enforce if enforce is not None else (risk_level in _ENFORCING)
    if should_enforce and decision == "deny":
        _call_audit(..., policy_enforced=True)          # audit before raising
        raise PolicyDeniedError(action)
    ...  # LOW/MEDIUM: unchanged additive path
```

---

## 5. 决策点清单（需裁决 D1–D7）

| # | 决策点 | 选项 | 影响 |
|---|---|---|---|
| **D1** | 内部主体身份 | (a) 新增 `service` 类型 + 白名单；(b) 复用 identity kernel 注册 service 身份；(c) 不改，维持 system actor | 决定 ALLOW 是否可能发生（Q2） |
| **D2** | DENY 语义 | (a) 抛 `PolicyDeniedError`；(b) 返回错误码；(c) 维持记录 | 决定拦截力（Q1） |
| **D3** | DEFER 语义 | (a) 新增 `PolicyEffect.DEFER`；(b) 复用 `ABSTAIN`；(c) 不引入 | HIGH/CRITICAL 的人工闸门 |
| **D4** | 异常时行为 | (a) fail-closed（拒绝）；(b) 维持 fail-open | 安全姿态 |
| **D5** | 拦截粒度 | (a) 仅 HIGH/CRITICAL；(b) 全部动作 | 爆炸半径 |
| **D6** | `default_deny` scope | (a) 维持 L7（引擎靠调用方兜底）；(b) 调整为 L0 兜底（引擎自返 DENY） | 是否收紧「忘记检查即放行」 |
| **D7** | DoD 措辞 | (a) 明确为"判决已记录"（路线 A 口径）；(b) 明确为"判决 + 分级执行"（路线 C 口径） | 文档与代码一致性 |

---

## 6. 爆炸半径 / 兼容性清单

若走路线 C，预计触及：

| 文件 | 变更 | 说明 |
|---|---|---|
| `src/kernels/policy/__init__.py` | 新增 `PolicyEffect.DEFER`、`internal_service_allow` 规则、`_is_verified_service` | 安全语义变更 |
| `src/kernels/_crosscutting.py` | `enforce` 开关 + `PolicyDeniedError` + fail-closed | 当前默认行为不变 |
| `tests/test_ai_layer_dod_delegation.py` | `test_kernel_action_records_policy_as_not_enforced` **必改** | 该测试**故意**在契约变更时失败（设计即如此） |
| `tests/kernels/test_crosscutting.py` | `test_policy_decision_recorded` 仍兼容（`in ("allow","deny","defer")`） | 可能因白名单变为 `allow` |
| `tests/kernels/policy/test_policy.py` | `TestDesignGapP10` 4 用例**不得回归** | P10/OD-010 红线 |
| 48 个 `@kernel_action` 调用点 | 若 D5 选"全部"，需逐点确认白名单覆盖 | 高风险 |

**红线**：不得削弱 `OD-010`（human 必须经 identity kernel 核验）、不得破坏「审计必须无条件」（`audit.log_event` 不能被策略拦截）。

---

## 7. 分阶段实施计划（若批准路线 C）

| 阶段 | 内容 | 风险 | 验证 |
|---|---|---|---|
| **C-0** | 只做文档：D7 定措辞；把 §1 实证写进 `AI-LAYER-DOD-AUDIT` | 零 | — |
| **C-1** | 引入 `service` 主体 + 白名单规则；`_adjudicate` 判决变为白名单驱动；**仍 additive（不拦截）** | 低 | probe 显示白名单内 `allow`；内核用例零回归 |
| **C-2** | 加 `enforce` 开关 + `PolicyDeniedError`；**仅 HIGH/CRITICAL** 生效 | 中 | 新增针对性用例；`test_ai_layer_dod_delegation` 同步更新 |
| **C-3**（可选） | 引入 `DEFER` + fail-closed；评估 `default_deny` scope（D6） | 中 | `test_runtime_loop` 等 default-deny 用例复核 |

---

## 8. 与既有决策的关系

- **OD-010（已 RESOLVED）**：human 主权必须经 identity kernel 核验。本方案**继承并强化**该思路 —— 把"核验"从 human 推广到 `service` 主体。
- **Round 60（能力层审计）**：能力层已确立「只审计、不判决」的边界（§3.6）。本方案**不越界**：真拦截只在**内核边界**发生，能力层继续通过调用内核动作间接受控。
- **`KERNEL-DOD-AUDIT`**：其"14/14 达标（仅 policy/audit 引擎自豁免）"结论**不受影响** —— 本方案不改"是否过引擎"，只改"判决是否有信息、是否分级执行"。

---

## 9. 结论与请求

- **不建议**路线 B（全面拦截）：在给内部主体合法 ALLOW 路径之前开启全面拦截 = 自锁。
- **推荐**路线 C 分步推进；**最低成本的第一步是 C-0 + C-1**（只加白名单让判决携带信息，仍不拦截），风险极低且立刻消除"判决无信息"的空转。
- **若倾向零改动**，则选路线 A：本文档即为该口径的书面依据，只需更新 DoD 措辞。

**请裁决**：① 选哪条路线；② 若选 C，D1/D2/D3/D4/D5 各取哪个选项。

---

*END OF POLICY-ENFORCEMENT-DESIGN*
