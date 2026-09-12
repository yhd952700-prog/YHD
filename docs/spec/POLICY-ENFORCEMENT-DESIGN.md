# Policy Controlled 真拦截设计方案（路线 C 已部分实施）

| 字段 | 值 |
|---|---|
| 状态 | **C-0 + C-1 + D8 + C-2 + C-3 + C-4 + C-5（生产开启裁决）已实施并验证**（2026-09-11–12，Round 65–73）；**生产已默认武装 `CRITICAL` 层的真拦截**（`docker-compose.prod.yml`；一处可回滚的运维开关）；**`HIGH` 层仍为 L1**，待调用点审计后单独裁决 |
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
5. 需裁决 **D1–D7**（§5）。**D8 已实施（2026-09-11 Round 66，选 b）**：43 个动作已建权威分级注册表（`src/kernels/_risk_classification.py`），装饰器现在把真实 `risk_level` 喂给引擎（仍 record-only）。**C-2/C-3/C-4/C-5 已依次落地**：截至 Round 73，**生产已默认武装 CRITICAL 层的真拦截**（见 §9 与 §10.9），HIGH 层仍为 record-only。

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
| **C-2** | 加 `enforce` 开关 + `PolicyDeniedError`；**仅 HIGH/CRITICAL** 生效 | 中 | 新增针对性用例；`test_ai_layer_dod_delegation` 同步更新 | ✅ **机制已实施（Round 67）；生产开启已由 C-5 裁决（Round 73）落地：CRITICAL 层默认武装** |
| **C-3**（可选） | 引入 `DEFER` + 动态 human 主体通道（解 C-2 自锁）+ fail-closed 收紧；评估 `default_deny` scope（D6） | 中 | `tests/kernels/test_sovereignty_c3.py`、`scripts/verify_c3_sovereignty.py` | ✅ **机制已实施（Round 68，2026-09-12）；生产开启已由 C-5 落地（Round 73）** |
| **C-4** | **把"开窗口"变成凭据 + 单一执行开关 + 真实审批入口**：审计化的 `SovereigntyGrant`（TTL/可撤销/动作集受限）、`src/kernels/_enforcement.py`（env 选择开启，默认空）、`src/gateway/policy.py`（JWT 认证的签发/查询/撤销端点） | 低（默认零行为变更） | `tests/kernels/test_sovereignty_grants.py`、`tests/kernels/test_enforcement_policy.py`、`tests/test_policy_approval_api.py`、`scripts/verify_c4_approval_channel.py`（44 项 ALL GREEN） | ✅ **已完成（Round 71，2026-09-12）** |
| **C-5** | **生产开启裁决 + HTTP 边界语义**：生产清单武装 `CRITICAL` 层；`PolicyDeferredError`→409、`PolicyDeniedError`→403（此前落 500）；启动期打印开关状态（配置错误按 ERROR）；C-4 护栏演进为「仅生产清单可武装、且解析结果恰为 CRITICAL」 | 低（两个 CRITICAL 动作在 `src/` 无生产调用点 → 零行为变更） | `tests/test_policy_enforcement_api.py`（12 例）、`scripts/verify_c4_approval_channel.py`（48 项 ALL GREEN） | ✅ **已完成（Round 73，2026-09-12）** |

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

- **C-4 已实施（Round 71，2026-09-12）："谁批准、怎么记" 已有答案。** 在此之前
  `human_sovereign(principal, actions)` 只证明"该 principal 是 ACTIVE 非 service 身份"，
  **不证明调用方就是那个人** —— 任何能写 Python 的代码路径传一个真实 human id 即可开窗，
  且事后无法回答"这次是谁批的"。C-4 补上三块：
  1. **凭据化**：`SovereigntyGrant`（grant_id / principal / 动作集 / 理由 / TTL / 撤销者），
     签发与撤销各写一条 `HUMAN_SOVEREIGNTY_OVERRIDE` 审计事件；动作执行时的内核审计带
     `sovereignty_grant` 字段，闭合「内核动作 ← 审批凭据 ← 授权人」因果链。
  2. **单一开关**：`src/kernels/_enforcement.py`（env `LIUHAO_KERNEL_POLICY_ENFORCE`，
     默认**空 = 不开启**）—— 43 个生产点依旧 `enforce=False`，"开启真拦截"变成**一处
     可回滚的运维决策**，而不是编辑 43 个调用点；非法 token（拼错的动作名、LOW/MEDIUM
     动作、非 gated 层级）**大声报错**，不会静默不生效。
  3. **真实入口**：`src/gateway/policy.py` 的 `POST/GET/DELETE /v1/policy/approvals`
     —— **主体只取自 `Authorization: Bearer <JWT>` 的 `sub`，请求体无法指定**，
     这正是 C-3 通道此前唯一的真实缺口。
  默认配置下行为与 C-1/C-2/C-3 **逐字节一致**（生产仍是 L1）。

**已裁决（2026-09-12 Round 73，用户授权"剩余的剩余裁决也交给你"）**：

① **生产开启内核层真拦截 = 是。** 落地在**生产部署清单**，而不是代码：
`docker-compose.prod.yml` 的 `LIUHAO_KERNEL_POLICY_ENFORCE=${LIUHAO_KERNEL_POLICY_ENFORCE:-HIGH,CRITICAL}`。

  Round 73 只开了 `CRITICAL`（2 个），前置条件是「HIGH 待调用点审计」。**Round 75（C-6）
  完成该审计并把武装面扩到 16 个**（全部 HIGH/CRITICAL 减去 1 个实测有活调用点的豁免项
  `capability.register`）。审计方法、逐动作实测结果与反向对照见 **§10.11**；原 CRITICAL-only
  的裁决依据与取舍见 **§10.9**。

② **D6**（`default_deny` scope 是否从 L7 收紧为 L0 兜底）—— **裁决：维持现状**。
理由：它影响的是**能力层**（`test_runtime_loop` 等既有 default-deny 用例），与内核层
CRITICAL 开启之间**没有依赖关系**；在能力层已有 4 处硬 gate + default-deny 的前提下，
收紧 scope 的边际安全收益低而回归面大，**不值得与安全语义变更捆绑**。
**正式裁决记录与复访条件见 §10.13（Round 76 定稿，自此不再作为待办搬运）。**

---

**待裁决（新，Round 76 发现）：**

③ **C-7 —— `_is_verified_human` 的判据是否从「反向排除」改为「正向白名单」？**
实测：内置 `system` 账号（无 `metadata.kind`）被判定为**已核验人类**，可持有主权并放行
CRITICAL 动作，审计记为「system 批准」。不构成越权（需本机文件系统访问），但污染
「动作 ← 凭据 ← **人**」问责链。建议改法为 `metadata.kind == "human"`；影响面已实测
（`src/` 无任何一处登记人类身份 → 改后需先有登记人类身份的正式路径）。
详见 **§10.12.3**。

**已解决**：**D1 / D2 / D3 / D4 / D5 / D7** 已随 C-1 / C-2 / C-3 的实施一并确定（见 §4、§10）；**D8** 已于 Round 66 实施；**C-2 机制**（Round 67）与 **C-3 机制**（Round 68）均已实施并验证；**D6** 已于 Round 76 正式裁决（§10.13）。

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

## 10.8 实施记录（C-4 审批通道，2026-09-12 Round 71）

> 授权依据：用户指示「决策门授权给你按照你的方向去执行」—— 采纳 §9 的方案：
> 不盲目翻转 `enforce=True`，而是先补齐「谁批准、怎么记」，并把"开启"收敛为**一处开关**。

### 10.8.1 发现的真实缺口（本轮的起点）

`human_sovereign(principal, actions)`（C-3）在策略引擎侧只验证
「该 principal 是身份内核中**存在且 ACTIVE 的非 service 身份**」。
它**不验证调用方就是那个人**。因此 C-3 之后：

- 任何能执行 Python 的代码路径，只要传一个真实 human 的 id，即可开窗放行
  HIGH/CRITICAL 内核动作；
- 且审计里只有 `policy_enforced=True` 与 `policy_rule=human_sovereignty`，
  **没有任何字段能回答"这次是谁批的"**。

这不是 bug，是 C-3 刻意留出的边界（通道只收窄"哪些动作"，不做调用方鉴权）。
C-4 的职责就是把它补完。

### 10.8.2 改了什么

| 文件 | 变更 | 风险 |
|---|---|---|
| `src/kernels/_sovereignty.py` | 新增 `SovereigntyGrant`（grant_id/principal/actions/reason/issued_by/granted_at/expires_at/revoked_at）+ `issue_grant` / `revoke_grant` / `get_grant` / `list_grants` / `grant_window` / `clear_grants`；`ActiveSovereignty` 增 `grant_id` / `reason`（有默认值，向后兼容）；`human_sovereign` 增 `grant_id` / `reason` / `granted_at` 关键字参数 | 低（纯新增） |
| `src/kernels/_enforcement.py` | **新增**：单一执行开关。`parse_spec` 把层级名/动作名解析为动作集，非法 token 抛 `ValueError`；`enforced_actions` / `is_enforced` / `describe` / `reload`；env `LIUHAO_KERNEL_POLICY_ENFORCE`，**默认空 = 不开启** | 低（默认关闭） |
| `src/kernels/_crosscutting.py` | 拦截门条件改为 `enforce or is_enforced(action, risk_level)`；审计 `details` 新增 `sovereignty_grant`；模块 docstring 补 C-4 | 低（默认配置下逐字节一致） |
| `src/gateway/policy.py` | **新增**：`POST /v1/policy/approvals`（签发）、`GET /v1/policy/approvals`（列表 + 开关快照）、`GET /v1/policy/approvals/{id}`、`DELETE /v1/policy/approvals/{id}`（撤销）、`GET /v1/policy/enforcement`（只读快照）；`require_human_principal` 从 JWT 解主体 | 低（新端点） |
| `src/gateway/main.py` | 挂载 `policy_router` | 无 |
| `tests/kernels/test_sovereignty_grants.py` | **新增**（22 例）：生命周期 / 有界性 / 最小权限 / 仅人类 / 审计因果链 | — |
| `tests/kernels/test_enforcement_policy.py` | **新增**（20 例）：解析与拒绝 / 默认关闭 / 开关真的武装生产调用点 | — |
| `tests/test_policy_approval_api.py` | **新增**（20 例）：鉴权强制 / **请求体无法指定主体** / 400 与 422 语义 / 签发-列表-撤销 | — |
| `scripts/verify_c4_approval_channel.py` | **新增**：44 项可复现验证（含 5 项安全护栏） | — |

### 10.8.3 关键设计决策

1. **不做 HTTP 远程执行内核动作。** 内核动作是**进程内**调用；做成"HTTP 传动作名 →
   服务端反射调用"会**新增攻击面**，且需要一张反射分发表（现不存在，凭空造即违反
   "不把概念当实现"）。入口的职责是**签发凭据**；执行由**能力层在自身进程内**于凭据
   窗口中进行。
2. **主体只能来自令牌。** `ApprovalRequest` 刻意**不含** `principal` / `issued_by`
   字段，并有专门测试断言"body 里塞 principal 会被忽略"。
3. **凭据只覆盖被拦截门管辖的动作。** LOW/MEDIUM 动作**拒绝签发**（C-3 通道永不升级
   它们，签发即为"看起来有权限的空操作"），未知动作名同样拒绝。
4. **凭据不是能力。** 持有 `SovereigntyGrant` 对象对策略引擎**毫无意义** —— 引擎始终
   从身份内核重算 `verified`。凭据的价值是**问责**，不是授权。
5. **开启收敛为一处。** 43 个生产点保持 `enforce=False`（AST 护栏继续断言）；运维通过
   `LIUHAO_KERNEL_POLICY_ENFORCE` 选择性武装某个动作或某一层，**随时可回滚**。

### 10.8.4 验证结果

| 层 | 检查 | 结果 |
|---|---|---|
| 1 | `flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（CI 口径） | **0 违规** |
| 2 | importlib 全量导入 `src/` 下 **199** 个模块 | **FAILED: 0** |
| 3 | `scripts/verify_c4_approval_channel.py` | **ALL GREEN（44 项）** |
| 4 | `verify_policy_c1` / `verify_d8_risk_classification` / `verify_c2_enforcement`(11) / `verify_c3_sovereignty`(15) | 全部 **ALL GREEN**（零回归） |
| 5 | `pytest tests/kernels tests/test_ai_layer_dod_delegation.py tests/test_liuhao_assistant.py tests/test_runtime_loop.py tests/security/ tests/test_policy_approval_api.py` | **697 passed / 1 skipped / 0 failed**（Round 68 基线 635 + 新增 62，零回归） |

### 10.8.5 未做的事（明确边界）

- **未在默认配置开启任何拦截** —— `enforce` 全 False 且 env 为空，生产行为与 C-1/C-2/C-3 逐字节一致。
- **未做 D6**（`default_deny` scope 收紧）—— 涉能力层，独立决策。
- **未做驾驶舱 UI 接线** —— 后端入口已就绪（`/v1/policy/approvals`），前端"批准"按钮属界面工作。

### 10.8.6 生产开启路径（运维动作，非代码变更）

```bash
# 例：只把两个 CRITICAL 动作纳入真拦截
export LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL
# 或逐动作精确开启
export LIUHAO_KERNEL_POLICY_ENFORCE=capability.retire
```

开启后：无人工审批 → HIGH/CRITICAL 动作抛 `PolicyDeferredError`（待审批）；
经 `/v1/policy/approvals` 签发凭据、并在其窗口内调用 → 正常执行。
**回滚 = 清空该环境变量并重启。**

## 10.9 实施记录（C-5 生产开启裁决，2026-09-12 Round 73）

> 授权依据：用户指示「剩余的剩余裁决也交给你，因为我不懂」—— 即把 §9 剩余的
> ①「生产是否开启」交给我裁决并执行。

### 10.9.1 裁决

**开启，但只开 `CRITICAL` 层；`HIGH` 层继续留在 L1。**

| 层 | 动作数 | 裁决 | 生产状态 |
|---|---|---|---|
| CRITICAL | 2（`capability.retire` / `security.set_abac_rule`） | ✅ **武装** | **L2（判决已执行）** |
| HIGH | 15 | ⛔ **暂不武装** | L1（记录型） |
| MEDIUM | 12 | — 结构性不可武装（`ENFORCED_TIERS` 排除） | L1 |
| LOW | 14 | — 同上 | L1 |

### 10.9.2 依据（三条实测，不是推断）

1. **两个 CRITICAL 动作在 `src/` 无任何生产调用点。** 全仓检索结果：
   `capability.retire` 只出现在装饰器定义（`src/kernels/capability/__init__.py:253`）
   与策略白名单字符串里；`security.set_abac_rule` 只出现在装饰器定义
   （`src/kernels/security/__init__.py:488`），而引擎内部写规则走的是
   `self._abac_rules[permission] = rule`，**不经过**被装饰的 `set_abac_rule`；
   `src/ai/` 对二者零引用。
   → **武装后当前行为零变更**，但门就此就位：将来任何新代码路径、或受损的 service
   主体想退役能力 / 改写 ABAC 边界，都必须先取得经核验的人类审批。
2. **`HIGH` 层不能一起武装。** HIGH 里存在**启动期/后台**路径的强候选
   （`plugin.register_plugin`、`plugin.activate_plugin`、`capability.register`、
   `memory.auto_cleanup`、`event.clear_history`）。未逐个审计调用点就武装，会把正常
   流程变成"待审批"——属**可用性回归**，换不来安全收益。
   → 记为独立裁决项，前置条件写进 `runbook.md` §6.1。

   ⚠️ **Round 75 更正（实测推翻当时推断）**：上句点名了 5 个「启动期/后台强候选」，
   但逐一实测后**只有 1 个成立** —— 只有 `capability.register` 真有活的启动调用点
   （`get_capability_registry()` 懒加载 12 个内置能力）；`plugin.register_plugin`、
   `plugin.activate_plugin`、`memory.auto_cleanup`、`event.clear_history` 在 10 条热路径
   与应用层套件下**均为惰性**。这条更正本身就是本轮的方法论要点：**候选名单是推断，
   武装面必须来自实测**。详见 §10.11。
3. **`MEDIUM` 结构性不可武装（本轮反向验证）。** `identity.create_identity`（MEDIUM）
   是**启动期**动作，若被纳入会直接打断启动 —— 而 `ENFORCED_TIERS` 从一开始就排除
   MEDIUM/LOW。这条设计在本轮得到了实测反向验证。

### 10.9.3 落地方式（一处运维决策，不是 43 点编辑）

```yaml
# docker-compose.prod.yml (api service)
- LIUHAO_KERNEL_POLICY_ENFORCE=${LIUHAO_KERNEL_POLICY_ENFORCE:-CRITICAL}
```

- 43 个生产装饰点**一行未改**，`enforce=False` 保持（AST 护栏继续断言）。
- 用 `:-` 默认值而非硬编码，运维可用 shell 环境覆盖；置空字符串即整体回滚。
- **未**选择"把 `enforce=True` 写进某个内核模块"：那会让回滚变成一次发版，
  也让 dev 环境无法继续跑记录型契约。

### 10.9.4 HTTP 边界语义（本轮补齐的真缺口）

`PolicyDeniedError` / `PolicyDeferredError` 都继承 `PermissionError`，而网关**没有**
对应 handler —— 于是开启拦截后，一个「待人工审批」的动作会以 **500 internal_server_error**
返回：运维看到的是"内部错误"，既误导人，又会被错误率告警误计。已补：

| 异常 | 状态码 | `error` | 语义 |
|---|---|---|---|
| `PolicyDeferredError` | **409 Conflict** | `policy_approval_required` | **不是被禁止**，是等待人类审批 |
| `PolicyDeniedError` | **403 Forbidden** | `policy_denied` | fail-closed 硬拒（引擎不可达、判决未知） |

409 的响应体同时给出 `action` / `verdict` / `rule` 以及"去哪申请审批"的指引。
另在**启动期**打印开关状态（配置错误按 ERROR 级别），使拼错的开关变量在**启动时**就暴露，
而不是等到第一次 CRITICAL 调用才炸。

### 10.9.5 护栏演进（把"决策"编码进去，而不是编码"未决策"）

`scripts/verify_c4_approval_channel.py` 原本断言"仓库任何地方都不得开启" —— 在决策未定
时正确，但现在会**挡住已被授权的决策**。改为更强、更具体的不变量：

1. **只有生产清单**（`docker-compose.prod.yml`）可以武装；dev / CI / Dockerfile 一律不得武装；
2. 武装的 spec 必须**能解析**（fail-loud 契约：拒绝拼错与惰性项）；
3. 解析结果必须**恰为 CRITICAL 集**；
4. **不得武装任何 HIGH 动作**（soak 前置条件）。

验证规模 44 → **48 项，全绿**。同一不变量同时以 pytest 形式落在
`tests/test_policy_enforcement_api.py`，因此在 CI 中也被持续守护（verify 脚本本身不在
CI 里跑）。

### 10.9.6 验证结果

| 层 | 检查 | 结果 |
|---|---|---|
| 1 | compileall（src / tests / scripts） | 通过 |
| 2 | `flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（CI 口径） | **0 违规** |
| 3 | flake8（新增测试 + 改动脚本） | **0 违规**（顺带修掉 `Optional` 未导入的 F821） |
| 4 | importlib 全量导入 `src/` 下 **199** 个模块 | **FAILED: 0** |
| 5 | `scripts/verify_c4_approval_channel.py` | **ALL GREEN（48 项）** |
| 6 | `pytest tests/test_policy_enforcement_api.py` | **12 passed** |
| 7 | 权威回归集（见提交说明） | **零回归** |

### 10.9.7 未做的事（明确边界）

- **未做 HTTP 执行端点**：仍然只有"签发凭据"的入口，没有"用凭据执行某个 CRITICAL 动作"
  的端点（刻意的，见 §10.8.3 决策 1）。执行必须在同进程内于 `grant_window(grant)` 中完成。
  运维侧若确需执行 `capability.retire`，当前途径是：临时置空开关 → 执行 → 恢复开关。
- **未武装 HIGH**：见 §10.9.2 第 2 条。**该边界已于 Round 75（C-6）关闭** —— HIGH 层现已在生产武装（14/15，唯一豁免 `capability.register`），见 §10.11。
- **未做 D6**：见 §9。
- **未接驾驶舱 UI**：后端入口早已就绪（`/v1/policy/approvals`），前端"批准"按钮属界面工作。

### 10.9.8 回滚

清空 `LIUHAO_KERNEL_POLICY_ENFORCE` 并重启即可（无需改代码、无需发版）。
`GET /v1/policy/enforcement` 与启动日志均可即时确认当前状态。

## 10.10 护栏接入 CI（2026-09-12 Round 74）

**发现**：`scripts/verify_*.py` 共 9 个，CI **一个都没跑**。其中 3 个是实质上的「假护栏」：

| 脚本 | 实测缺陷 |
|---|---|
| `verify_orm_vs_db.py` | 缺 `sys.path` 引导 → `ModuleNotFoundError: No module named 'src'`；已算出 `diverged` 却从不返回非零退出码 |
| `verify_persistence.py` | 同上；另在读回失败时 cleanup 会以 `AttributeError` 崩溃 |
| `verify_ai_layer_audit.py` | **确有**失败路径（对照探针失效时 `main()` `return 2`），但从未被任何 workflow 调用 |

**处置**

- `verify_orm_vs_db.py` / `verify_persistence.py`：补齐 `sys.path` 引导并给出真实退出码；
  persistence 的 cleanup 改为容忍读回失败，让退出码承载判定而非抛 `AttributeError`。
- `ci.yml` 新增 `guardrails` job：**8 个**脚本接入（第 9 个 `verify_metrics_persist.py`
  本就有独立工作流 `verify_metrics_persist.yml`）。两个 DB 护栏通过
  `alembic upgrade head` 在 CI 内造 scratch 库后运行。
- 新增元护栏 `tests/test_guardrail_scripts.py`（31 例）：断言每个 `verify_*.py`
  （a）有 `sys.path` 引导、（b）AST 层面存在可产生非零退出的路径、（c）被某个
  workflow 调用。豁免名单 `DIAGNOSTIC_REPORTERS` **当前为空** —— 即不存在无门禁
  能力的 `verify_*` 脚本。

**双向对照（证明修复有效，而非仅仅「看起来能跑」）**

- `verify_orm_vs_db.py` 对 schema 残缺的库 → `DIVERGENCES FOUND: YES`，exit 1；
- `verify_persistence.py` 对无表库 → exit 1（响亮失败，不再静默退出 0）；
- 元护栏自身两组反向对照：删掉失败退出码 → 被抓；把脚本从 CI 摘掉 → 被抓。

**注意**：`sqlalchemy` / `alembic` **不在** `pyproject.dependencies`（只在
`requirements.txt`），故 `guardrails` job 显式 `pip install alembic sqlalchemy`。

---

## 10.11 实施记录（C-6 HIGH 调用点审计与武装面扩展，2026-09-12 Round 75）

> 授权依据：用户在 Round 73 之后的「按顺序执行」—— 第一件事正是 Round 73 亲口留下的
> 「HIGH 那 15 个不动，**待调用点审计**」。

### 10.11.1 结论

**武装面从 2 个 CRITICAL 扩到 16 个 —— 全部 HIGH/CRITICAL 减去 1 个审计化豁免。**

| 层 | 动作数 | 武装 | 豁免 | 生产状态 |
|---|---|---|---|---|
| CRITICAL | 2 | 2 | 0 | **L2（判决已执行）** |
| HIGH | 15 | 14 | 1（`capability.register`） | **L2** |
| MEDIUM / LOW | 26 | 0 | — | L1（`ENFORCED_TIERS` 结构性排除） |

### 10.11.2 审计方法（静态 + 动态 + 对抗，四层）

1. **AST 调用点扫描（全仓）**：对 15 个 HIGH 动作的方法名，在 `src/`、`LiuHao-O/`、
   仓库根、`libs/`、`core/` 做属性调用扫描 → **生产代码外部调用点 = 0**。
   注意一个陷阱：`src/identity/__init__.py`、`src/plugins/registry.py`、
   `src/kernels/*/__init__.py` 里确实存在**同名调用**，但它们全在**门面包装函数体内**，
   而这些门面函数自身**没有任何调用点**（`external calls total: 0`，全仓复核）。
   `apps/` 是纯前端（0 个 `.py`）；`getattr` 动态派发 0 处；无按字符串的动作派发。
2. **逐动作动态探测**：17 个候选（15 HIGH + 2 CRITICAL）各自在**全新进程**里**单独武装**，
   跑 10 条生产热路径（各内核单例引导 + app 工厂）。

   | 结果 | 动作 |
   |---|---|
   | **打破热路径** | `capability.register`（`capability.bootstrap` 抛 `PolicyDeferredError`）|
   | 惰性 | 其余 **16** 个 |

3. **应用层套件对照**（补齐探测覆盖面）：以**完整 spec 武装**运行
   `tests/test_profile_api.py`、`tests/test_liuhao_assistant.py`、
   `tests/test_runtime_loop.py`、`tests/test_ai_layer_dod_delegation.py`、`tests/security/`。

   | 组 | 进度字符统计 |
   |---|---|
   | 对照（未武装） | `.=111  s=1  F=0  E=0` |
   | 实验（16 武装） | `.=111  s=1  F=0  E=0` |

   逐字相同。单调性使**一次**运行覆盖所有子集（武装 A 不影响 B 的判决）。
   （注：两条腿的 `exit code` 都可能是 1 —— 沙箱的 bulk-delete 守卫会拦截 pytest 的
   tmpdir 清理并吞掉汇总行，所以本仓库一律用**进度字符**而非退出码判定。）

4. **对抗性检查**：确认无动态派发、无字符串派发、无遗漏的生产根目录。

### 10.11.3 关键发现：`capability.register` 是唯一有活调用点的动作

`get_capability_registry()` 首次调用时会**懒加载并注册 12 个内置内核能力**
（`_register_builtin_capabilities()` → `CapabilityRegistry.register()`）—— 这是**启动热路径**。
武装它 → `PolicyDeferredError` 从注册表引导逃出 → 所有能力内核消费方失败。

⚠️ **重要教训：它的调用点全在「定义模块内部」，与惰性的门面包装函数在文本上无法区分**
（一个在 `_register_builtin_capabilities()` 里被真执行，一个在 `register_capability()` 里
永不被调用）。所以 **「外部调用点 = 0」不等于「没有调用点」**。静态扫描在这件事上会
把人带到错误结论 —— 这也是本轮最终用手册化的**动态探测**、而不是 grep，来界定武装面的原因。

### 10.11.4 落地：审计化豁免（`EXEMPT_ACTIONS`）

豁免不是政策偏好，是**记录下来的调用点实测结论**。`src/kernels/_enforcement.py`：

- `EXEMPT_ACTIONS: Dict[str, str]` —— 动作 → 理由（含实测日期、现象、复访条件）；
- 层级展开（`HIGH` / `CRITICAL`）**自动扣除**豁免项；
- **显式点名**豁免动作 → `ValueError`（fail-loud），错误信息带出理由；
- `describe()` 增加 `exempt_actions`，因此 `GET /v1/policy/enforcement` 可见。

生产清单：`docker-compose.prod.yml` →
`LIUHAO_KERNEL_POLICY_ENFORCE=${LIUHAO_KERNEL_POLICY_ENFORCE:-HIGH,CRITICAL}`。

### 10.11.5 把「惰性」变成 CI 门禁（本轮最重要的交付）

C-5 的把关条件是「该动作无生产调用点」，而这条**静态断言不出来**（见 §10.11.3）。
新增 `scripts/verify_armed_actions_are_inert.py`（已接入 CI 的 `guardrails` job）：

1. 从**生产清单**读出 spec（跟随部署，而非硬编码），断言其可解析、无豁免泄漏、非空
   （空集会立刻报错 —— 否则护栏是"真空通过"）；
2. **广度**：以完整 spec 武装运行应用层套件，进度字符统计必须 0 failed / 0 error；
3. **逐动作**：每个被武装动作单独在**全新进程**里武装，跑生产热路径 + 未鉴权 GET 路由，
   失败条件有**两条** —
   - 任一探测抛异常；**或**
   - 装饰器打出 `POLICY ENFORCED kernel_action=<该动作>` 日志。

   第二条专门用于捕获**异常被 `except Exception` 吞掉的静默降级**：那种情况下所有探测
   都是绿的，只有日志能揭示动作其实被拦了。

**反向对照（不做这一步就等于又造了一个假护栏）**

| 对照 | 期望 | 实测 |
|---|---|---|
| 在生产代码注入一个**被 `except Exception` 吞掉**的 `memory.auto_cleanup()` 调用点，并武装该动作 | 必须被抓到 | ✅ `failures: []`（无异常）但 `blocks: ["POLICY ENFORCED ..."]` → exit 1 |
| 同一改动下探测**另一个**动作 | 不得误报 | ✅ exit 0、`blocks` 为空（归因精确）|
| 把豁免动作写进生产清单 | 必须拒绝 | ✅ exit 1，并把豁免理由整段报出 |

第二行尤其重要：只捕获日志而不区分动作，就会变成一个到处误报的护栏。

### 10.11.6 同步的断言变更

| 位置 | 原断言 | 新断言 |
|---|---|---|
| `verify_c4_approval_channel.py` | `parse_spec('HIGH') == 15` | `== 14`；另加「层级扣除豁免」「点名豁免即报错」「理由非空」|
| 同上 | 武装集 `== CRITICAL`；「不得武装任何 HIGH」 | 武装集 `== (HIGH∪CRITICAL) − EXEMPT`（**不多不少**）|
| `tests/kernels/test_enforcement_policy.py` | HIGH 展开 15 | 14，另加 6 项豁免契约测试 |
| `tests/test_policy_enforcement_api.py` | 清单 spec `== "CRITICAL"` | `== "HIGH,CRITICAL"` 且解析 == 审计面；「HIGH 刻意不武装」→「无豁免动作被武装」+ 断言惰性护栏仍在 CI |

规模：`verify_c4` 48 → **52** 项；元护栏 31 → **34** 项（它自动把新脚本纳入
「必须有引导 / 必须有失败路径 / 必须被某个 workflow 调用」三项检查 —— 新脚本写完后
它立刻报了红，正是它该有的行为）。

### 10.11.7 边界与未做的事

- **未做 HTTP 执行端点**：仍然只有"签发凭据"入口（§10.8.3 决策 1）。运维要真正执行
  某个已武装动作，当前途径仍是「临时置空开关 → 执行 → 恢复开关」。
- **未做驾驶舱 UI**：后端 `/v1/policy/approvals` 就绪，前端"批准"按钮属界面工作。
- **未做 D6**：见 §9 ②。
- **惰性护栏的诚实边界**：它证明的是**已测路径**上不可达，不是"数学上不可达"。未被热路径
  与应用层套件覆盖的**新增**调用点仍可能漏过。这是采样而非证明 —— 已写进脚本 docstring，
  不做超出证据的宣称。

---

## 10.12 实施记录（驾驶舱审批 UI 接线，2026-09-12 Round 76）

§10.8.5 / §10.9.7 / §10.11.7 三次把「未做驾驶舱 UI」列为待办。本轮关闭它，并在过程中
发现两处此前未记录的事实。

### 10.12.1 做了什么

| 层 | 交付 | 说明 |
|---|---|---|
| 前端 | `apps/console/console/src/lib/policyClient.ts` | 四个 Policy 端点的类型化客户端；令牌存 **sessionStorage**（关标签页即失效） |
| 前端 | `.../components/ApprovalCenter.tsx` | 审批中心面板：真实拦截态势、凭据列表、签发、撤销 |
| 前端 | `App.tsx` / `chrome.tsx` | App 单一持有策略状态；**侧栏导航真正切换视图**；「安全运行模式」由硬编码改为真实数据 |
| 前端 | `dashboardData.ts` | 删除审批导航项写死的 `badge: '1'`（改为由真实凭据数注入） |
| 脚本 | `scripts/issue_console_token.py` | 本机签发驾驶舱令牌（`--list` / `--principal` / `--ttl`） |
| 测试 | `tests/test_policy_approval_http.py` | **28 项** HTTP 层契约测试 |
| CI | `.github/workflows/ci.yml` | Build Verification job 新增 Node + `npm ci` + `npm run build` + `npm run lint` |

**授权面一处未动**：`src/gateway/policy.py` 与 `src/kernels/*` 的生产代码**零改动**（除下述 409 文案）。
没有新增任何端点。UI 的令牌输入框不是妥协——它把「主体只能来自令牌」这条性质**变成了可见的产品行为**。

### 10.12.2 实测更正：签发凭据 ≠ HTTP 重试放行

409 的 `detail` 原文写着「Issue an approval grant via POST /v1/policy/approvals, then
retry inside its window」。**这句话对 HTTP 调用方是假的。** 实测（2026-09-12，全新进程）：

| 步骤 | 结果 |
|---|---|
| 武装 `CRITICAL`，未签发凭据 | `PolicyDeferredError`（verdict=`defer`） |
| 经 `issue_grant` 签发一条覆盖该动作的凭据后**再次重试** | **仍然 `defer`** |
| 在 `grant_window(grant)` 内重试 | `allow` → 动作真的执行 |

原因：`_crosscutting._adjudicate` 读的是 sovereignty **contextvar**（由 `human_sovereign` /
`grant_window` 设置），而 `grant_window` 在生产代码中**没有任何调用方**——凭据存储只被
`/v1/policy/approvals` 的读写接口消费。这不是缺陷，是 §10.8.3 决策 1 的必然推论
（进程内调用，不做 HTTP 反射执行）。

**处置**：改写 409 文案，令其如实指向真正的解锁机制（`grant_window(grant)`），并说明网关
刻意不提供远程执行入口。断言固定在 `tests/test_policy_approval_http.py`：
`test_the_409_detail_names_the_real_mechanism` + `test_grant_does_not_open_the_gate_for_an_http_retry`
（后者断言**当前仍不放行**——若哪天语义变了，它会红，从而强制文案同步改）。

### 10.12.3 新发现：C-7 —— 内置机器身份可冒充「已核验人类」

做 UI 时发现 `--list` 只列出唯一一个「可批准者」：内置 `system` 账号。追下去实测：

| 主体 | 身份 metadata | 以 `type:"human"` 判决 | 规则 |
|---|---|---|---|
| `system`（自动创建，L0，`admin`） | `{}`（**无 kind**） | **`allowed=True`** | `human_sovereignty:allow` |
| `liuhao-internal-service` | `{"kind": "service"}` | `allowed=False` | `default_deny` |

端到端：`issue_grant("system", ["capability.retire"])` **成功**；武装后在该凭据窗口内
`capability.retire` **真的执行了**。审计会把这次 CRITICAL 放行记成「**system** 批准」。

根因：`_is_verified_human` 的判据是**反向排除**（`metadata.get("kind") != "service"`），
而 C-3 只关闭了显式标记为 `service` 的那条路径。`system` 与被它排除的身份在语义上同类
（都是机器），却因为**没有**标记而通过 —— 反向排除对未标记身份**失败开放**。
`_validated_principal`（凭据层）用的是同一条判据，因此凭据层同样放行。

**影响评估（不过度宣称）**：这**不构成越权**——伪造该令牌需要本机文件系统访问（= 已能读签名
密钥，也就能直接清空开关）。真实危害是**问责链被污染**：C-4 用整个 Round 71 建立
「动作 ← 凭据 ← 授权人」，而这一环可以是一个机器身份，与 OD-010「须由**经核验的人类**授权」
语义相悖。

**当时（Round 76）的处置：只标注，不改判据。** 收紧「什么算已核验人类」是安全语义变更，
按本项目一贯做法（C-2 机制先建 → C-5 才裁决武装）应���用户主权裁决，不在界面工作里顺手做掉。

> **⚠️ 本节已被 §10.14 取代（2026-09-12 Round 77）。** 用户裁决「需要」→ **C-7 已实施**：
> 判据改为正向白名单，并配套了人类身份登记路径。**以下为历史记录，不再代表当前状态。**

### 10.12.4 验证

| 层 | 检查 | 结果 |
|---|---|---|
| 1 | `compileall`（src / tests / scripts） | 通过 |
| 2 | `flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（CI 口径） | **0 违规** |
| 3 | 改动文件 flake8 | **0 违规** |
| 4 | importlib 全量导入 `src/` 模块 | **FAILED: 0** |
| 5 | `pytest tests/test_policy_approval_http.py` | **28 passed** |
| 6 | `npm run build`（tsc + vite） | 通过 |
| 7 | `npm run lint`（oxlint） | **0 error**（2 warning，与既有 `panels.tsx` 同模式） |
| 8 | 元护栏 `tests/test_guardrail_scripts.py` | 34 passed |
| 9 | 权威回归集 | 见提交说明 |

### 10.12.5 未做的事（明确边界）

- **仍未做 HTTP 执行端点**（§10.8.3 决策 1 不变）。面板的按钮因此叫「记录授权」，不叫「放行」。
- **未收紧 `_is_verified_human`**（C-7，见上）。
- **未新增登录端点**：网关仍无 `/v1/auth/*`。令牌的信任锚是**本机文件系统访问**；
  加登录表单只增加仪式感，不增加安全（能跑签发脚本的人本就能读签名密钥）。这是如实描述，
  不是「暂时没做」。
- **未给面板加轮询**：策略状态在挂载、令牌变更、签发/撤销后刷新，不做定时轮询
  （审批不是高频操作，轮询只会增加无谓请求）。

---

## 10.13 裁决记录（D6：`default_deny` scope，2026-09-12 Round 76）

**裁决：维持现状（L7，引擎靠调用方兜底）。不做收紧。**

过去三轮（§10.7 / §10.8.5 / §10.9.7 / §10.11.7）都只留一句「未做 D6」，本次把它正式记下，
以免这条待办在每次收尾时被反复搬运而始终不结。

**决策依据**

1. **不构成安全缺口**：能力层（`src/ai/`）**早已有 4 处硬 gate + default-deny**，走的不是
   「忘记检查即放行」的路径；内核层的 43 个动作在 C-5/C-6 之后已由开关统一管辖
   （生产武装 16 个）。D6 要修的是**引擎自身**在 `default_deny` 作用域上的兜底位置，
   而当前所有真实入口都已经过能力层或拦截门 —— 边际安全收益低。
2. **回归面大**：D6 的改动会触及 `test_runtime_loop` 等既有 default-deny 用例的判定预期，
   属于跨层语义变更；与内核层开启之间**没有依赖关系**，因此没有「必须一起做」的理由。
3. **风险收益不对称**：低收益 + 高回归面，且可在未来任何时候独立进行（`default_deny` 的 scope
   是引擎内部常量，不涉及数据迁移、不影响线上格式）。

**复访条件**：若出现下列任一情况，应重新评估 ——
(a) 新增一条绕过能力层硬 gate 的内核动作入口；
(b) 出现一次「因 scope 为 L7 而未被拦下」的真实事故；
(c) 能力层硬 gate 数量下降。

---

## 10.14 实施记录（C-7：人类身份正向白名单 + 登记路径，2026-09-12 Round 77）

用户裁决：**需要**（含配套的人类身份登记路径）。本节取代 §10.12.3。

### 10.14.1 改了什么

| 层 | 交付 |
|---|---|
| 内核 | `src/kernels/identity/__init__.py`：`HUMAN_KIND` / `METADATA_KIND_KEY` 常量、`is_human_identity()`（**正向白名单**）、`create_human_identity()`（唯一被认可的登记入口） |
| 内核 | `policy._is_verified_human` 与 `_sovereignty._validated_principal` 两处**反向排除 → 正向白名单**（fail-closed） |
| 内核 | `IdentityManager` 启动时按 `LIUHAO_HUMAN_IDENTITIES_FILE` 装载已登记人类（**解决内存态无法持久化**） |
| 脚本 | `scripts/register_human_identity.py`（`--list` / `--principal` / `--revoke`），写入种子文件并**验证闭环** |
| 测试 | `tests/kernels/identity/test_human_identity_c7.py`（20 项）+ `test_policy.py` 新增 2 项回归 |
| 配置 | `config/human_identities.example.json`；真实登记表 `config/human_identities.json` 已加入 `.gitignore`（含真实姓名，属运维数据） |

### 10.14.2 为什么必须同时做登记路径（否则就是假修复）

实测：身份内核**纯内存**（`self._identities: Dict`，无 ORM 表、无 load/save），
运行时只有 2 个身份，`system`（`metadata={}`）与 `liuhao-internal-service`（`kind=service`）。
**0 个**能通过 `kind=="human"` 白名单。

若只改判据而不给持久化登记路径，结果就是审批通道**没有任何可用主体** ——
那不是"更安全"，而是把一个能用的（虽然记错人的）通道换成了一个死通道。
这与 §10.12.2 揭示的 `grant_window` 教训同源：**建了机制但没人调用 = 假能力**。

因此登记路径不是可选项，是 C-7 的前置条件。

### 10.14.3 实测确认（端到端）

| 检查 | 结果 |
|---|---|
| `system` 的 `is_human_identity` | **False**（改前 True） |
| 策略判决 `{"type":"human","id":"system"}` | **deny**（改前 `human_sovereignty:allow`） |
| `issue_grant("system", [capability.retire])` | **被拒**（改前成功） |
| 登记真人后 `issue_grant` | 成功 |
| 在 `grant_window` 内执行 CRITICAL 动作 | 真的执行 → **通道未变砖** |

**反向对照（证明测试有效）**：把种子装载改回 `create_identity` 写法后，进程**挂死**
（无限递归，直到超时被杀）；当前实现秒回。故 `test_seeded_humans_are_loaded` 确实能抓住该回归。

### 10.14.4 本轮踩到的坑（已入 PITFALLS）

**在 `__init__` 里调 `@kernel_action` 方法 = 无限递归。**
`IdentityManager.__init__` 调 `create_identity`（带 `@kernel_action`）→ 策略判决 →
`_is_verified_service` → `get_identity_manager()` → 全局单例此时**尚未赋值**
（`_global_manager = IdentityManager()` 在构造完成后才赋值）→ 再构造一个 → 无限递归。
且 `@kernel_action` 的 `except Exception` 会把 `RecursionError` 吞掉，所以表现为**挂死**而非报错。
修法：种子身份按内置 `system`/service 的方式**直接构造 `AgentIdentity`**，不经过 `create_identity`。

### 10.14.5 未做的事（明确边界）

- **未做 DB 持久化**：种子是 JSON 文件，不是数据库表。够用于单机部署；多副本/集中管理
  需要独立的身份持久化方案（含 schema 与迁移），已记录为技术债，不在本轮范围。
- **未新增 HTTP 登记端点**：与不加 `/v1/auth/login` 同理（§10.12.1），登记走本机脚本。
- **未自动迁移存量身份**：改前不存在任何 `kind="human"` 的身份，无存量可迁。

---

*END OF POLICY-ENFORCEMENT-DESIGN*
