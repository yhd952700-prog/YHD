# Policy Controlled 真拦截设计方案（路线 C 已部分实施）

| 字段 | 值 |
|---|---|
| 状态 | **C-0 + C-1 已实施并验证**（2026-09-11，Round 65）；**C-2/C-3 待裁决** |
| 日期 | 2026-09-11（提案）/ 2026-09-11（C-1 实施） |
| 提出 | Principal Engineer（承接 `AI-LAYER-DOD-AUDIT.md` §3.5.4 与 §5.5 的裁决项） |
| 裁决人 | 用户（项目主权）—— 授权 Principal Engineer 按路线 C 第一步自主推进 |
| 关联 | `OD-010`、`AI-LAYER-DOD-AUDIT.md` §3.5.4/§5.5、`KERNEL-DOD-AUDIT.md`、`_crosscutting.py`、DL:§112、`UNIFIED-BLUEPRINT.md` 附录 A（DoD 第 5 项） |
| 实施证据 | `scripts/verify_policy_c1.py`（ALL GREEN）、`tests/kernels/policy/test_internal_service_policy.py` |

---

## 0. TL;DR

1. **纠正一个关键误解**：**"真拦截"在能力层（`src/ai/`）早已存在**，不是空白。`LiuHaoAssistant.chat` / `RuntimeLoop.step` / `NetworkGateway` / `WorldInterface` 四处都在拿到 `deny`/非 allow 后**真的不执行**，且采用 **default-deny** 语义（实测 `chat` 放行、`msg:*` 拒绝）。
2. **真正"只记录、不拦截、且判决无信息"的只有内核层** —— `src/kernels/_crosscutting.py::@kernel_action` 装饰的 **43 个内核动作**（原提案误记为 48，2026-09-11 实测更正）：修复前判决恒为 `deny`（四档风险等级均如此），且装饰器 additive，`deny` 没有任何执行效果。
3. 所以真实议题不是"要不要真拦截"，而是两个更窄的问题：
   - **Q1**：要不要把**内核横切装饰器**也变成控制点？
   - **Q2**：要不要给**内部主体**一条合法的 ALLOW 路径（否则一旦开启拦截 = 全部内核动作被拒）？
4. **路线 C（分层分级）已在推进**：能力层维持现状（已达标，不动）；内核层保留"记录"，但判决**已从恒 deny 改为白名单驱动**（C-1 已实施），并计划**仅对 HIGH/CRITICAL 开启真拦截**（C-2 待做）。详见 §4、§10。
5. 需裁决 **D1–D8**（§5）。**D8 是 C-2 的前置阻塞**：实测全部装饰动作未设 `risk_level`，C-2 按现状永不触发。

---

## 1. 现状实证

> 本节全部结论来自**运行时探针**，不是关键字扫描。复现见 §1.5。

### 1.1 判定点全景

| 层 | 判定点 | 判决来源 | **是否真拦截** | 判决是否携带信息 |
|---|---|---|---|---|
| 内核层（43 个动作） | `@kernel_action`（`_crosscutting.py`） | `evaluate_policy_simple(service actor)` | ❌ **从不拦截**（additive） | ✅ **白名单驱动**（C-1 后；修复前为恒 deny） |
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

> **⚠️ 本节为"修复前"基线。** C-1 已实施（2026-09-11）：actor 改为经核验的内部
> service 主体，内置规则 `internal_service_allow`（白名单 14 项）使判决变为
> `allow`/`deny` **随动作变化**。实施记录见 §10。
>
> **另一处实测更正**：43 个装饰点**全部未显式设置 `risk_level`**（一律默认 `LOW`），
> 因此上表的"四档风险等级"只是**探针手工构造**的输入，真实运行中装饰器永远只发
> `LOW`。这使 C-2「仅 HIGH/CRITICAL 拦截」在现状下**永不触发** —— 见新增决策点 **D8**。

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

`src/kernels/_crosscutting.py::@kernel_action` 已在其 docstring 中以 `.. warning::` **诚实登记**了这一点。**43 个装饰动作**（原提案误记为 48，2026-09-11 实测更正）分布在 12 个内核：

`capability / context / evaluation / event / execution / identity / memory / network / plugin / resource / security / trust`。

### 1.5 复现命令

```bash
# 内核层策略判决 + 白名单 + 伪造主体 + kill-switch + 覆盖完整性（仓库资产）
.venv/Scripts/python.exe scripts/verify_policy_c1.py
# 策略/身份/横切回归
.venv/Scripts/python.exe -m pytest tests/kernels/policy/ tests/kernels/identity/ \
  tests/kernels/test_crosscutting.py tests/test_ai_layer_dod_delegation.py -q
# 能力层 gate 结果（allow / not_applicable）
.venv/Scripts/python.exe scripts/verify_ai_layer_audit.py
```

> 本节 §1.1–§1.4 的"修复前"数字来自一次性探针脚本（已清理）。当前状态的权威
> 复现入口是上表第一项的 `scripts/verify_policy_c1.py`（六重检查，退出码 0 = 全绿）。

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
- ❌ `Policy Controlled` 在内核层**名不副实**；43 个动作的策略判决仍不携带信息。

### 路线 B —— 内核层全面真拦截

给 system actor 加放行规则 + 装饰器改为 enforcement gate（DENY → 抛异常），**全部**动作生效。

- ✅ 真正统一的内核控制点。
- ❌ **高风险**：43 个动作 + 400+ 内核用例。若内部主体的 allow 覆盖不全 → 内核动作成片被拒 → 系统性崩溃。**必须**先有 Q2 的白名单，否则等于自锁。
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

### 4.1 内部主体（Q2 的答案）✅ 已实施（C-1）

新增一类**非人类**的合法主体，与 `human_sovereignty` 并列，避免"内部主体永远 deny"：

> 实施差异说明（唯一两处）：① 规则 `action.name IN (...)` 的取值必须用 **`list`**
> 而非 `frozenset` —— 引擎 `IN` 运算符只接受 `list`/`set`/`tuple`，而 `frozenset`
> 不是 `set` 的子类，用错会静默恒为 `False`；② 主体身份的**权威定义在身份内核**
> （`INTERNAL_SERVICE_PRINCIPAL` + 内置身份），策略内核只认 `metadata["kind"] == "service"`
> 标记，不硬编码 principal 名。

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
| **D8** | **动作风险分级（C-2 前置阻塞，2026-09-11 新发现）** | (a) 维持全部 `LOW`（则 C-2 永不触发，分级拦截形同虚设）；(b) 逐点为 43 个装饰动作标注真实 `risk_level`（授权/破坏类标 HIGH）；(c) 只在装饰器内按**动作命名空间**推导风险（如 `identity.*` / `security.*` / `plugin.*` → HIGH） | 决定 C-2 是否有实际拦截力 |

**D8 的证据**：实测 43 个 `@kernel_action(...)` 调用点**无一处传入 `risk_level`**，
全部落在默认值 `LOW`（`grep -rn 'risk_level=' src/` 仅命中 `src/ai/` 的 3 处与
`approval.py` 的参数传递）。因此 §4.2 的 `risk in {"HIGH","CRITICAL"}` 条件在
当前代码中**恒为假**。

---

## 6. 爆炸半径 / 兼容性清单

若走路线 C，预计触及：

| 文件 | 变更 | 说明 |
|---|---|---|
| `src/kernels/policy/__init__.py` | **已实施**：新增 `internal_service_allow` 规则、`INTERNAL_SERVICE_ALLOWED_ACTIONS`(14) / `INTERNAL_SERVICE_DENIED_ACTIONS`(29)、`_compute_verified` / `_is_verified_service`（`PolicyEffect.DEFER` 属 C-3，未做） | 安全语义变更 |
| `src/kernels/identity/__init__.py` | **已实施**：新增内置 `INTERNAL_SERVICE_PRINCIPAL` 身份（`metadata.kind == "service"`，L0，空权限） | 新内置身份 +2 处计数型测试同步 |
| `src/kernels/_crosscutting.py` | **已实施**：`_adjudicate` 改用 service actor 并返回 `(decision, rule_id)`；审计新增 `policy_rule`。`enforce` 开关 + `PolicyDeniedError` 属 **C-2，未做** | 当前默认行为不变（仍 additive） |
| `tests/kernels/policy/test_internal_service_policy.py` | **新增**：核验/白名单/伪造/可撤销/分类完备性 | 新增护栏 |
| `tests/kernels/policy/test_policy.py` | **已同步**：内置规则计数 5→6、`by_action.allow` 1→2 | 契约变更 |
| `tests/kernels/identity/test_identity.py` | **已同步**：种子身份计数 2→3、L0 计数 1→2、fixture 文档串 | 契约变更 |
| `tests/test_ai_layer_dod_delegation.py` | **已同步**：新增 service 路径正向用例；`system` 裸声明恒 deny 的用例保留（仍成立） | 见 §6 红线 |
| `tests/kernels/test_crosscutting.py` | 兼容；已加 `policy_rule` 断言 | — |
| `tests/kernels/policy/test_policy.py::TestDesignGapP10` | **零回归**（4 用例全过） | P10/OD-010 红线 |
| 43 个 `@kernel_action` 调用点 | 本轮**未改**（D5 未定）；分类完备性由测试强制 | 待 C-2/风险分级时处理 |

**红线**：不得削弱 `OD-010`（human 必须经 identity kernel 核验）、不得破坏「审计必须无条件」（`audit.log_event` 不能被策略拦截）。

---

## 7. 分阶段实施计划（若批准路线 C）

| 阶段 | 内容 | 风险 | 验证 | 状态 |
|---|---|---|---|---|
| **C-0** | 只做文档：D7 定措辞；把 §1 实证写进 `AI-LAYER-DOD-AUDIT` | 零 | — | ✅ **已完成** |
| **C-1** | 引入 `service` 主体 + 白名单规则；`_adjudicate` 判决变为白名单驱动；**仍 additive（不拦截）** | 低 | `scripts/verify_policy_c1.py` ALL GREEN；内核用例零回归（494 passed） | ✅ **已完成**（Round 65） |
| **C-2** | 加 `enforce` 开关 + `PolicyDeniedError`；**仅 HIGH/CRITICAL** 生效 | 中 | 新增针对性用例；`test_ai_layer_dod_delegation` 同步更新 | ⛔ **阻塞于 D8**（风险分级未做，条件恒假） |
| **C-3**（可选） | 引入 `DEFER` + fail-closed；评估 `default_deny` scope（D6） | 中 | `test_runtime_loop` 等 default-deny 用例复核 | ⏸ 未开始 |

---

## 8. 与既有决策的关系

- **OD-010（已 RESOLVED）**：human 主权必须经 identity kernel 核验。本方案**继承并强化**该思路 —— 把"核验"从 human 推广到 `service` 主体。
- **Round 60（能力层审计）**：能力层已确立「只审计、不判决」的边界（§3.6）。本方案**不越界**：真拦截只在**内核边界**发生，能力层继续通过调用内核动作间接受控。
- **`KERNEL-DOD-AUDIT`**：其"14/14 达标（仅 policy/audit 引擎自豁免）"结论**不受影响** —— 本方案不改"是否过引擎"，只改"判决是否有信息、是否分级执行"。

---

## 9. 结论与请求

- **不建议**路线 B（全面拦截）：在给内部主体合法 ALLOW 路径之前开启全面拦截 = 自锁。
- **路线 C 分步推进**；**最低成本的第一步 C-0 + C-1 已实施**（只加白名单让判决携带信息，仍不拦截），
  风险极低且已消除"判决无信息"的空转。**实施记录见 §10。**
- C-2/C-3 仍待裁决；**D8（动作风险分级）是 C-2 的前置阻塞** —— 不先做，C-2 的
  "仅 HIGH/CRITICAL 拦截"永不触发。

**待裁决**：① 是否继续走 C-2；② D2/D3/D4/D5/D6/D8 各取哪个选项。

---

## 10. 实施记录（C-0 + C-1，2026-09-11 Round 65）

> 授权依据：用户指示"继续按照你的方向去执行，不需要问我，你是这个项目总负责人"，
> 采纳本方案 §9 的建议（C-0 + C-1）。

### 10.1 改了什么

| 文件 | 变更 |
|---|---|
| `src/kernels/identity/__init__.py` | 新增常量 `INTERNAL_SERVICE_PRINCIPAL = "liuhao-internal-service"`；在 `IdentityManager.__init__` 中与内置 `system` 身份并列**种子化**该身份（`scope=L0`、`permissions=set()`、`metadata={"kind": "service"}`）。放位置于身份内核而非策略内核，是因为**身份内核才是"存在哪些身份"的权威**（`_is_verified_human` 沿用的信任模型） |
| `src/kernels/policy/__init__.py` | 新增 `INTERNAL_SERVICE_ALLOWED_ACTIONS`（14）/ `INTERNAL_SERVICE_DENIED_ACTIONS`（29）；新增内置规则 `internal_service_allow`；新增 `_compute_verified()`（按 actor 类型分派）与 `_is_verified_service()`；`evaluate()` / `evaluate_simple()` 改用 `_compute_verified` |
| `src/kernels/_crosscutting.py` | `_adjudicate` 改用 `{"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL}` 并返回 `(decision, rule_id)`；审计 `details` 新增 `policy_rule`；docstring 的 `.. warning::` 重写为实施后的诚实描述 |

### 10.2 为什么这样设计（关键取舍）

1. **白名单是显式枚举、禁止通配**（§4.1 原设计）：新增内核动作默认不获放行，必须显式分类。
   该"摩擦"由 **AST 完备性测试**强制 —— 漏分类即测试失败，也不会静默漂移。
2. **分类规则是机械的、可审计的**，而非逐条品味：`allow = 查询/计算/簿记`（不改变权限、不改变
   拓扑、不销毁状态）；`deny = 授权/破坏`。43 个动作据此分为 14 / 29。
3. **`verified` 由引擎重算，绝不采信调用方**（延续 P10/OD-010 的做法）。
   因此 `{"type":"service","verified":true,"principal":"不存在"}` 仍被判 deny；
   而引用真实主体的同一动作被判 allow —— 差异**只能**来自核验。
4. **核验可撤销**：挂起/停用该身份或抹掉 `kind` 标记，白名单的全部 allow 立即塌陷为 deny。
   这让"服务主体"成为一个**可运维的开关**，而不是一个写死的常量。
5. **仍然 additive**：判决从不产生执行效果，`policy_enforced` 仍为 `false`。
   本维度此刻的定性 = 「判决携带信息，但尚未控制」。

### 10.3 验证结果

| 层 | 检查 | 结果 |
|---|---|---|
| 1 | `compileall`（3 个改动文件） | OK |
| 2 | `flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（CI 口径） | 0 违规 |
| 3 | AST：43 个装饰动作全部被显式分类、无重叠、无通配 | 通过 |
| 4 | importlib 全量导入 `src/` 下 **194** 个模块 | **FAILED: 0** |
| 5 | 装饰器运行时可解析（82 个被包裹可调用对象，签名全部可取得） | 通过 |
| 6 | `scripts/verify_policy_c1.py` 六重断言 | **ALL GREEN**（退出码 0） |
| — | `pytest tests/kernels/` | **494 passed** |
| — | `pytest tests/kernels tests/test_ai_layer_dod_delegation.py tests/test_liuhao_assistant.py tests/test_runtime_loop.py tests/security/` | **595 passed / 1 skipped** |

### 10.4 未做的事（明确边界）

- **未加 `enforce` 开关、未加 `PolicyDeniedError`**（C-2）—— 拦截力为零。
- **未引入 `PolicyEffect.DEFER`**（C-3）。
- **未改 43 个装饰点的 `risk_level`**（D8 待裁决）。
- **未动能力层** —— 它已是真拦截，本方案刻意不越界。

---

*END OF POLICY-ENFORCEMENT-DESIGN*
