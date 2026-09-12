# Policy Controlled 真拦截设计方案（路线 C 已部分实施）

| 字段 | 值 |
|---|---|
| 状态 | **C-0 + C-1 + D8 + C-2(机制) + C-3(机制) 已实施并验证**（2026-09-11–09-12，Round 65–68）；**C-2/C-3 生产开启仍待用户主权裁决** |
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
5. 需裁决 **D1–D7**（§5）。**D8 已实施（2026-09-11 Round 66，选 b）**：43 个动作已建权威分级注册表（`src/kernels/_risk_classification.py`），装饰器现在把真实 `risk_level` 喂给引擎（仍 record-only）。**C-2 现解除阻塞**，但拦截力仍为零，待裁决是否开启。

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
| **D7** | DoD 措辞 | (a) 明确为"判决已记录"（路线 A 口径）；(b) **明确为"判决 + 分级执行"（路线 C 口径）** ✅ **已采纳**（2026-09-12） | 文档与代码一致性 → **已解决**：`UNIFIED-BLUEPRINT` §7 确立 **L1/L2 两级判定**，`CODEX-CONTRACT` §5、`KERNEL-DOD-AUDIT`、`AI-LAYER-DOD-AUDIT` 已同步 |
| **D8** | **动作风险分级（2026-09-11 新发现 → 已实施，选 b）** | (a) 维持全部 `LOW`（**否决**：C-2 永不触发，分级拦截形同虚设）；(b) **逐点标注真实 `risk_level`** ✅ 已实施（权威注册表 + 装饰器接线）；(c) 仅按命名空间推导（**否决**：粒度不够，`network.*` / `trust.*` 混合了 LOW/MEDIUM/HIGH） | 决定 C-2 是否有实际拦截力 → **已解决** |

**D8 实施结论（选 b）**：见 §10.5。核心事实：43 个 `@kernel_action(...)` 调用点此前
**无一处传入 `risk_level`**，全部落在默认值 `LOW`（曾使 §4.2 的
`risk in {"HIGH","CRITICAL"}` 条件恒为假）。现由注册表统一供给真实等级，且该输入对
内部 service 主体路径**惰性无关**（唯一读 `risk_level` 的 `human_sovereignty` 规则要求
`actor.type=="human"`），故 D8 零裁决副作用、纯消除死参数。

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
| **C-2** | 加 `enforce` 开关 + `PolicyDeniedError`；**仅 HIGH/CRITICAL** 生效 | 中 | 新增针对性用例；`test_ai_layer_dod_delegation` 同步更新 | ✅ **机制已实施（Round 67，默认 off）；生产开启待裁决（阻塞于 C-3 human 主体）** |
| **C-3**（可选） | 引入 `DEFER` + 动态 human 主体通道（解 C-2 自锁）+ fail-closed 收紧；评估 `default_deny` scope（D6） | 中 | `tests/kernels/test_sovereignty_c3.py`、`scripts/verify_c3_sovereignty.py` | ✅ **机制已实施（Round 68，2026-09-12，默认 off）；生产开启仍待裁决** |

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
- C-2 **机制已实施（Round 67，2026-09-11）**：`enforce` 开关 + `PolicyDeniedError`
  + 仅 HIGH/CRITICAL 生效的拦截门 + fail-closed；但 `enforce` **默认 `False`** 且
  43 个生产装饰点无一开启 —— 生产行为仍是记录型，拦截力为零。**把某个 HIGH/CRITICAL
  动作的 `enforce` 翻为 `True` 即变成真拦截**，但内部 service 主体对这些动作恒 `deny`
  （不在 C-1 白名单、须 human 主权 OD-010），若无人类授权直接翻转 = **自锁系统**。
  **C-3（Round 68）已提供动态 human 主体通道**：`human_sovereign` 经 OD-010 核验后可将
  HIGH/CRITICAL 动作放行，故生产翻转 `enforce=True` 现已安全（无授权 → `PolicyDeferredError`
  待人工审批；有授权 → 执行）。该翻转仍是刻意、独立的用户主权决策（见 §10.7）。
- C-3 已实施机制（Round 68，2026-09-12）：`PolicyEffect.DEFER` + `PolicyDeferredError` +
  动态 human 主体通道（`src/kernels/_sovereignty` 的 `human_sovereign` 上下文管理器）+
  OD-010 核验加固（`_is_verified_human` 增加 `metadata.kind != "service"` 校验，拒绝以
  human 类型引用 service 身份伪冒）。**生产默认零行为变更**（无 sovereignty 上下文时
  行为与 C-1/C-2 完全一致；43 个生产点仍 `enforce=False`）。`human_sovereign` 经身份内核
  核验的 ACTIVE human 授权后，HIGH/CRITICAL 动作裁决 actor 由 service 切为 human，经
  `human_sovereignty`（precedence 1000）放行 —— 即**生产翻 `enforce=True` 现已安全**：
  无人类授权 → `PolicyDeferredError`（待人工审批）；有授权 → 正常执行。该翻转本身仍是
  用户主权决策（见 §10.7）。

**待裁决（剩余）**：① **生产是否开启内核层真拦截**（把某个 HIGH/CRITICAL 动作的 `enforce` 翻 `True`；C-3 已解自锁，开启安全）；② **D6**（`default_deny` scope 是否从 L7 收紧为 L0 兜底，涉能力层，影响 `test_runtime_loop` 等既有 default-deny 用例）。

**已解决**：**D1 / D2 / D3 / D4 / D5 / D7** 已随 C-1 / C-2 / C-3 的实施一并确定（见 §4、§10）；**D8** 已于 Round 66 实施；**C-2 机制**（Round 67）与 **C-3 机制**（Round 68）均已实施并验证。

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
  - **未加 `enforce` 开关、未加 `PolicyDeniedError`**（C-2）—— 拦截力仍为零；
    **但 D8 已做**：43 个装饰点现从权威注册表读取真实 `risk_level`（仍只记录、不拦截），
    `risk_level` 死参数已消除，审计事件新增 `risk_level` 字段。
  - **未动能力层** —— 它已是真拦截，本方案刻意不越界。

---

---

## 10.5 实施记录（D8，2026-09-11 Round 66）

> 授权依据：用户指示"继续按照你的方向去执行，不需要问我，你是这个项目总负责人"，
> 采纳本方案 §5 的 D8 选项 (b)——为 43 个内核动作建立权威风险分级，解除 C-2 阻塞。

### 10.5.1 改了什么

| 文件 | 变更 | 风险 |
|---|---|---|
| `src/kernels/_risk_classification.py` | **新增**：纯数据注册表。`RiskTier` 枚举、`ActionRisk` 元组、`KERNEL_ACTION_RISK`（43 动作 → 分级）、`get_kernel_action_risk` / `get_action_risk`、`ENFORCED_TIERS={HIGH,CRITICAL}`、`is_enforced_tier`、`discover_kernel_action_names`（AST 完备性扫描）。零执行风险，不依赖策略引擎 | 无（纯数据） |
| `src/kernels/_crosscutting.py` | 装饰器改用哨兵默认值：未显式设 `risk_level` 时从注册表读取真实等级喂给 `_adjudicate`；显式 `risk_level` 仍优先。审计事件新增 `risk_level` 字段。修正模块 docstring 一处数字（"白名单之外 43"→"29"） | 低（service 主体路径 verdict 与 `risk_level` 惰性无关，已实测） |
| `tests/kernels/test_risk_classification.py` | **新增**：完备性（AST 对账 43）、tier 合法性、分布（14/12/15/2）、LOW==C-1 白名单交叉校验、C-2 切割线、装饰器真实等级端到端验证 | — |
| `scripts/verify_d8_risk_classification.py` | **新增**：可复现五重验证（import / decorator / coverage / tiers / wiring） | — |

### 10.5.2 分级表（43 动作）

| 等级 | 数量 | 动作 |
|---|---|---|
| **LOW** | 14 | context.compress, context.process, evaluation.evaluate, event.publish, event.subscribe, event.unsubscribe, event.retry_dead_letter, execution.execute, execution.create_checkpoint, memory.store, memory.compress, network.route, resource.release, security.decide_access |
| **MEDIUM** | 12 | context.set_scope, evaluation.apply_feedback, evaluation.approve_replan, evaluation.execute_replan, identity.create_identity, network.add_route, network.remove_route, resource.create_quota, resource.allocate, resource.commit, trust.assign_score, trust.update_score |
| **HIGH** | 15 | identity.grant_permission, identity.revoke_permission, capability.register, capability.deprecate, trust.establish_trust, trust.revoke, network.register_adapter, plugin.register_plugin, plugin.unregister_plugin, plugin.activate_plugin, plugin.deactivate_plugin, security.grant_rbac_role, security.revoke_rbac_role, memory.auto_cleanup, event.clear_history |
| **CRITICAL** | 2 | capability.retire（级联移除能力）, security.set_abac_rule（改写访问控制边界本身） |

**分级口径（机械、可审计，非品味）**：
- `LOW` = 查询/计算/簿记（不改变权限、不销毁状态）＝ C-1 内部服务白名单；
- `MEDIUM` = 可逆的操作性权限/状态变更，作用域有界；
- `HIGH` = 权限变更 或 局部破坏性 或 代码/适配器生命周期；
- `CRITICAL` = 系统级权限变更 或 不可逆的系统级破坏。

**强交叉校验**：`LOW` 集合 == `INTERNAL_SERVICE_ALLOWED_ACTIONS`（14）——
两套独立分类（C-1 白名单、D8 分级）对"安全动作"的判定一致，否则测试失败。

### 10.5.3 验证结果

| 层 | 检查 | 结果 |
|---|---|---|
| 1 | `flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（CI 口径） | 0 违规 |
| 2 | importlib 全量导入 `src/` 下 **195** 个模块 | FAILED: 0 |
| 3 | `scripts/verify_d8_risk_classification.py` 五重断言 | **ALL GREEN** |
| 4 | 装饰器端到端：CRITICAL 动作审计 `risk_level=CRITICAL`、LOW 动作 `=LOW`；verdict 不变（仍 record-only） | 通过 |
| 5 | `pytest tests/kernels tests/test_ai_layer_dod_delegation.py tests/test_liuhao_assistant.py tests/test_runtime_loop.py tests/security/` | **610 passed / 1 skipped / 0 failed** |

### 10.5.4 未做的事（明确边界）

- **C-2 机制已实施（Round 67）**：`enforce` 开关 + `PolicyDeniedError` + 仅
  HIGH/CRITICAL 拦截门 + fail-closed；但 `enforce` **默认 `False`**、43 个生产点
  **未开启** —— 内核层生产拦截力仍为零（记录型不变）。详见 §10.6。
- **C-2 生产开启仍待裁决**：分级数据就绪，把某个 HIGH/CRITICAL 动作的 `enforce`
  翻为 `True` 即变真拦截，但内部 service 主体对这些动作恒 `deny`（不在 C-1 白名单、
  须 human 主权 OD-010），若无 C-3「动态 human 主体」通道直接翻转 = **自锁系统**。
  故生产开启是用户主权决策（不擅自改执行语义）。
- **未动能力层** —— 它已是真拦截，本方案刻意不越界。
- **未引入 `PolicyEffect.DEFER`**（C-3）。

---

## 10.6 实施记录（C-2 机制，2026-09-11 Round 67）

> 授权依据：用户指示"继续按照你的方向去执行，不需要问我，你是这个项目总负责人"，
> 采纳本方案 §4.2（enforce 开关 + `PolicyDeniedError` + 仅 HIGH/CRITICAL）。

### 10.6.1 改了什么

| 文件 | 变更 | 风险 |
|---|---|---|
| `src/kernels/_crosscutting.py` | 新增 `PolicyDeniedError(PermissionError)`（携带 `action`/`verdict`/`rule_id`）；`@kernel_action` 新增 `enforce: bool = False` 参数；wrapper 内新增仅 `HIGH`/`CRITICAL` 生效的拦截门（`deny`/`defer`/`error` → 抛 `PolicyDeniedError`，抛前写 `policy_enforced=True` 审计，fail-closed）；`_call_audit` 新增 `enforced` 参数使 `policy_enforced` 字段随实；模块 docstring `.. warning::` 注明 C-2 已实施、默认 off | 低（默认 off，生产零行为变更） |
| `tests/kernels/test_enforcement_c2.py` | **新增**：默认 off 仍 additive、enforce+HIGH+deny 真抛错（含 action/verdict/rule_id）、enforce+LOW+allow 不抛、enforce+模拟 allow 不抛、fail-closed（adjudication None）抛、被拦截审计 `policy_enforced=True`、cut line 仅 HIGH/CRITICAL | — |
| `scripts/verify_c2_enforcement.py` | **新增**：11 项可复现验证（机制存在性 / ENFORCED_TIERS / 门逻辑 / fail-closed / **安全护栏：无生产点翻 enforce=True**） | — |

### 10.6.2 安全护栏（关键设计决策）

**43 个生产 `@kernel_action` 调用点全部维持 `enforce=False`。** 原因：内核动作一律以
内部 service 主体裁决，而 17 个 HIGH/CRITICAL 动作**不在 C-1 白名单内** → 判决恒
`deny`。若在生产直接翻转 `enforce=True`，内部服务将无法执行
`capability.retire` / `security.set_abac_rule` / `identity.grant_permission` 等
系统必需动作 → **自锁**。因此「生产开启真拦截」被刻意留作独立决策，**阻塞于 C-3 的
动态 human 主体通道**（让经 human 核验的动作以 human 身份裁决、走 `human_sovereignty`
放行，其余仍 deny）。

`verify_c2_enforcement.py` 内置 AST 护栏：扫描全部生产装饰点，断言**无任何一处传
`enforce=True`** —— 一旦有人在 Round 67 之后误翻生产开关，验证脚本立即失败，防止静默
自锁溜进 CI。

### 10.6.3 验证结果

| 层 | 检查 | 结果 |
|---|---|---|
| 1 | `flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（CI 口径） | 0 违规 |
| 2 | importlib 全量导入 `src/` 下 **98** 个被测包内模块（含全部 kernels） | FAILED: 0 |
| 3 | `scripts/verify_c2_enforcement.py` 11 项断言 | **ALL GREEN**（退出码 0） |
| 4 | `pytest tests/kernels/test_enforcement_c2.py` | **13 passed** |
| 5 | `pytest tests/kernels tests/test_ai_layer_dod_delegation.py tests/test_liuhao_assistant.py tests/test_runtime_loop.py tests/security/` | **623 passed / 1 skipped / 0 failed**（C-2 默认 off，零回归） |

### 10.6.4 未做的事（明确边界）

- **未在生产开启任何 `enforce=True`** —— 内核层生产拦截力仍为零（记录型），保留为刻意、独立的用户主权决策（见 §10.7）。
- **C-3 已实施（Round 68，见 §10.7）**：`PolicyEffect.DEFER` + `PolicyDeferredError` + 动态 human 主体通道（`_sovereignty`）+ OD-010 核验加固；生产默认零行为变更。
- **未动能力层** —— 它已是真拦截，本方案刻意不越界。

---

---

## 10.7 实施记录（C-3 机制，2026-09-12 Round 68）

> 授权依据：用户指示"继续按照你的方向去执行，不需要问我，你是这个项目总负责人"，
> 采纳本方案 §4.3（DEFER）+ §4.4（fail-closed，已在 C-2 落地）+ 「动态 human 主体通道」。

### 10.7.1 改了什么

| 文件 | 变更 | 风险 |
|---|---|---|
| `src/kernels/policy/__init__.py` | `PolicyEffect` 枚举新增 `DEFER = "defer"`；`_is_verified_human` 增加 `metadata.get("kind") != "service"` 校验（关闭以 `type:"human"` 引用 service 身份伪冒 OD-010 的向量）。不增删规则，`stats()` 不变 | 低（仅收紧核验，无规则变更） |
| `src/kernels/_sovereignty.py` | **新增**：`ActiveSovereignty` 数据类 + `contextvars` 通道 + `human_sovereign` 上下文管理器（least-privilege 枚举动作集）。默认无激活上下文 → 生产零行为变更 | 无（纯机制，不自动开启） |
| `src/kernels/_crosscutting.py` | 新增 `PolicyDeferredError(PolicyDeniedError)`（verdict 固定 `"defer"`）；`_adjudicate` 在 HIGH/CRITICAL 且于授权集合内时把裁决 actor 由 `service` 切为 `human`（使 `human_sovereignty` 放行，解 C-2 自锁）；拦截门 fail-closed→`PolicyDeniedError("error")`，引擎有应答的 HIGH/CRITICAL deny→`PolicyDeferredError("defer")`；模块 docstring 补 C-3 | 低（默认 off，生产零行为变更） |
| `tests/kernels/test_sovereignty_c3.py` | **新增**（12 例）：上下文管理、无授权仍 deny、真实 human 身份翻 allow、伪装 service 被拒、出域仍 deny、LOW 不升级、enforce 无授权抛 `PolicyDeferredError`、有授权执行、审计 `policy_enforced` | — |
| `tests/kernels/test_enforcement_c2.py` | 更新：被拦截 HIGH/CRITICAL 的 verdict 由 `"deny"` 演进为 `"defer"`（`PolicyDeferredError`，仍 `PolicyDeniedError` 子类，既有 `PermissionError` 守卫透明捕获） | — |
| `scripts/verify_c2_enforcement.py` | 更新 HIGH+enforce 断言为 `PolicyDeferredError(defer)`（C-3 语义演进） | — |
| `scripts/verify_c3_sovereignty.py` | **新增**：15 项可复现验证（机制/枚举/无授权 deny/有授权 allow/伪冒拒绝/出域 deny/LOW 不升级/enforce 无授权 defer/有授权执行/fail-closed error/**AST 安全护栏：无生产点翻 enforce=True、无生产代码开启 sovereignty 通道**） | — |

### 10.7.2 自锁解除机制（核心）

C-2 之所以不能在生产直接翻 `enforce=True`，是因为内部 service 主体对 17 个 HIGH/CRITICAL
动作**恒 `deny`**（不在 C-1 白名单、须 human 主权 OD-010）—— 直接翻转 = 系统无法执行
`capability.retire` / `security.set_abac_rule` 等必需动作 → 自锁。

C-3 的 `human_sovereign(principal, actions)` 上下文管理器提供**动态 human 主体通道**：当
一个经身份内核核验的 ACTIVE 非 service 人类身份，显式授权了某个 HIGH/CRITICAL 动作（且
该动作在其枚举授权集内）时，该动作的裁决 actor 由 `service` 切为 `human`，于是命中
`human_sovereignty`（precedence 1000，要求 `risk_level ∈ {HIGH,CRITICAL}` 且 `verified==True`）
→ **ALLOW**。机制只收窄"哪些动作可以 human 身份裁决"，绝不能凭空制造 allow；OD-010 仍由
引擎重算 `verified` 守住，伪造主体（含以 human 类型引用 service 身份）必落回 `deny`。

因此生产翻 `enforce=True` 现已安全：
- **无人类授权** → `PolicyDeferredError`（verdict `"defer"`，待人工审批，非永久拒绝）；
- **经已核验 human 授权** → 正常执行。

### 10.7.3 验证结果

| 层 | 检查 | 结果 |
|---|---|---|
| 1 | `flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（CI 口径） | 0 违规 |
| 2 | importlib 全量导入 `src/` 模块 | FAILED: 0 |
| 3 | `scripts/verify_c3_sovereignty.py` 15 项断言 | **ALL GREEN**（退出码 0） |
| 4 | `scripts/verify_c2_enforcement.py` 11 项断言 | **ALL GREEN**（退出码 0） |
| 5 | `pytest tests/kernels/test_sovereignty_c3.py` | **12 passed** |
| 6 | `pytest tests/kernels/test_enforcement_c2.py` | **13 passed** |
| 7 | `pytest tests/kernels tests/test_ai_layer_dod_delegation.py tests/test_liuhao_assistant.py tests/test_runtime_loop.py tests/security/` | **635 passed / 1 skipped / 0 failed**（C-3 默认 off，零回归） |

### 10.7.4 未做的事（明确边界）

- **未在生产开启任何 `enforce=True`** —— 内核层生产拦截力仍为零（记录型）。**把某个
  HIGH/CRITICAL 动作翻 `enforce=True` 现可安全进行**（C-3 已解自锁），但这是用户主权决策，
  不擅自改执行语义。建议的开启路径：驾驶舱/能力层在用户显式点击"批准"时进入
  `human_sovereign(<用户身份>, [动作])` 上下文再调用内核动作。
- **未动能力层** —— 它已是真拦截，本方案刻意不越界。
- **未评估 D6（`default_deny` scope 调整）** —— 该项涉及能力层 default-deny 语义，影响
  `test_runtime_loop` 等既有用例，留作独立决策（不在内核层 C-3 范围内）。

*END OF POLICY-ENFORCEMENT-DESIGN*
