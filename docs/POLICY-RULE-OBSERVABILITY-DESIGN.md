# POLICY 规则「静默跳过」可观测化 —— 设计

> 状态：设计已定，实现中。范围 = 修 **P0-3b**（`quota_enforcement` 的 fail-open 与其
> 背后的**一整类**问题：内置 DENY 规则在所需属性缺失时**静默不适用**，且决策里没有任何
> 痕迹能表达"这条拒绝对策没被评估"）。
> 纪律同前：**先实测再判断**、**附加式改动**、**默认零行为变化**、**禁止静默降级**、
> **诚实优先于好看**。

## 1. 先实测（结论与 MEMORY.md 的记载有出入）

MEMORY.md 的 P0-3 原文断言两件事。逐条实测：

### 1.1 P0-3a（scope 用字符串 LT 比较 ⇒ L 级序漏判）—— **假警报**

`PolicyScope` 就是 `L0`..`L7`（**全部 2 字符**）。实测枚举全部 56 个有序对：

```
pairs where lexical != numeric: NONE
agent=L1 required=L3 -> LT matches (deny) = True     # 正确：L1 低于 L3
agent=L3 required=L3 -> LT matches (deny) = False    # 正确
agent=L5 required=L3 -> LT matches (deny) = False    # 正确
agent=L0 required=L1 -> LT matches (deny) = True     # 正确
agent=L7 required=L0 -> LT matches (deny) = False    # 正确
```

**零**对出现「字典序 ≠ 数值序」⇒ 规则判 allow/deny **正确**。字符串 LT 在此**不是**漏洞。
（同批实测还发现：文档里的 **POL-3**（builtin 规则读 `agent.*` 而 `evaluate_simple` 只给
`actor.*`）**早已修好** —— `evaluate_simple:629-634` 已把 actor 镜像进 `agent` 键。）

### 1.2 P0-3b（quota）—— **真问题，但形态与记载不同**

实测（`scope=None`，即不过滤规则）：

| 输入 | 判决 | 命中 |
|---|---|---|
| `resource.available=1` + `estimated_cost=10`（超预算） | `deny` | `quota_enforcement` |
| `resource.available=100` + `estimated_cost=10`（在预算内） | `deny` | `default_deny`（动作未登记） |
| `resource={}`（缺 `available`）+ `estimated_cost=10` | `deny` | `default_deny` |
| `resource=None` + `estimated_cost=10` | `deny` | `default_deny` |
| `resource.available=1` + **缺 `estimated_cost`** | `deny` | `default_deny` |

机理由代码决定：`PolicyCondition.evaluate` 有

```python
elif attr_value is None:
    result = False            # ← 属性缺失 ⇒ 条件为假 ⇒ DENY 规则不适用
```

以及右值引用解析后为 `None` 时 `_safe_compare` 直接返回 `False`。**两条 fail-open 路径**。

### 1.3 但真正更严重的是：这条规则**当时在生产里根本不可能触发**（已修，见 §6）

- **没有任何调用点传 `estimated_cost`**。全仓实测：`estimated_cost` 只出现在
  `agent_factory.authorize` 的**形参**（`estimated_cost: Optional[float] = None`，且
  「非 None 才写进 action」）、`economy.py` 自建的**无关字典**（实测 `economy.py` **零调用**
  policy 引擎）、以及模型注册表/路由器里同名的**另一回事**字段。
- `AgentPolicy.authorize` 传 **`scope=PolicyScope.L1`**，而 `quota_enforcement` 声明
  **`scope=L2`**；scope 过滤是 `scope_order[rule.scope] <= scope_order[scope]` ⇒
  **`L2 <= L1` 为假 ⇒ 该规则被整个滤掉**。同一路径上 `default_deny`（L7）**也被滤掉**。
- `NetworkGateway.delegate` 的 `scope` 默认 **`None`** ⇒ 不过滤，quota 会被评估；但它传的
  `resource=params`（默认 `{}`）**从来不含 `available`**，且同样从不传 `estimated_cost`。

⇒ **`quota_enforcement` 是一条"声明为强制、实际无法触发"的规则**（装饰性强制）。
实测四组合均为「不匹配 ⇒ 落到 `default_deny`」，故对**未登记动作**而言 fail-open 被
`default_deny` 掩盖；但对**已登记/allowed 动作**，它就是纯粹的放行漏洞。

### 1.4 一致性缺陷

同样「属性缺失」，三条 DENY 规则的**失败方向不一致**，且**没有任何断言或文档强制某个方向**：

| 规则 | 缺哪个属性 | 方向 | 机制 |
|---|---|---|---|
| `capability_required` | `agent.capabilities` | **fail-closed**（照样 DENY） | `CONTAINS` + `negate=True` ⇒ 缺失→False→取反→True |
| `capability_required` | `action.required_capability` | **fail-open**（不适用） | `EXISTS` 门为假（这是**声明式**的适用性前提） |
| `quota_enforcement` | `resource.available` 或 `action.estimated_cost` | **fail-open**（不适用） | `LT` ⇒ 缺失→False |
| `scope_enforcement` | `agent.scope` 或 `action.required_scope` | **fail-open**（不适用） | `LT` ⇒ 缺失→False |

**注意 `capability_required` 是"好例子"**：它早就用 `EXISTS` **声明**了自己只在动作声明了能力要求时才适用。
问题在于 `quota`/`scope` **没有**这样的声明 —— 它们的"不适用"是**隐式**的：你无法从决策里区分
「因为没给数据所以没查」和「查过了，在预算内」。**这正是本次要消除的歧义。**

## 2. 设计决策

### 2.1 为什么**不**选「缺失即 DENY」

最直觉的"fail-closed"改法（属性缺失就判 DENY）会**当场打死系统**：真实调用方**从来不给**
`estimated_cost` ⇒ 每个动作都会因"成本未知"被拒。这不是安全，是自毁。

**判据**：一条策略规则只有在**它声明的输入齐备**时才能做判断。缺输入时的正确语义**不是**
「通过」也**不是**「拒绝」，而是「**这条规则没有被评估**」——必须**可观测**。

### 2.2 选定方案：让「未评估」成为决策里的一等公民（零行为变化）

三件事，全部**附加式**、**不改任何判决结果**：

1. **`PolicyCondition.missing_operands(context) -> List[str]`**
   新增只读方法，返回本次求值**被缺失操作数挡住**的上下文路径
   （左属性 + 右值 `$path`）。仅对**有序/相等类**算子有意义；`EXISTS`/`NOT_EXISTS`
   是对存在性的**断言**，不算缺失。

2. **内置 DENY 规则显式声明输入前提**（加 `EXISTS` 门）
   `quota_enforcement` 增加 `resource.available EXISTS` 与 `action.estimated_cost EXISTS`；
   `scope_enforcement` 增加 `agent.scope EXISTS` 与 `action.required_scope EXISTS`。
   **行为等价**（门为真时 `LT` 才有可能为真），但把"需要什么输入"从**隐含**变成**声明**，
   并使"因缺输入而未评估"与"评估了但在预算内"**可区分**——现在这两者完全无声无别。

3. **`PolicyDecision` 记录被跳过的 DENY 规则**
   新增 `unapplied_deny_rules: List[PolicyRule]` 与 `unresolved_operands: List[str]`
   （均有默认值 ⇒ 既有构造调用不受影响）。在 `evaluate` 里对**未匹配且 action==DENY**
   的规则计算 `missing_operands`，非空即登记。**判决不变**，只是把"哪条拒绝对策没生效、
   因为缺哪个属性"写进决策对象。

### 2.3 为什么不**在那一轮**顺手改 `authorize` 的 `scope=L1`

> **后续（同日）：boss 已授权接线，本节的"不改"已作废 —— 实际落地见 §6。**
> 下面保留当时的理由，因为它解释了为什么当时**没有**顺手改、而要单独决策。

那会让 `default_deny`（L7）与 `quota_enforcement`（L2）参与运行时环的判决 ⇒ **改变
所有现有判决**（大量动作会从 `not_applicable` 变 `deny`），爆炸半径覆盖 `RuntimeLoop`
与 `NetworkGateway`。**这是安全策略决策，不是技术细节**，须由 boss 定；本次只把它
**如实记录**在文档里，不擅自改。

## 3. 交付物

- 本文档。
- `src/kernels/policy/__init__.py`：`missing_operands` 方法、`PolicyDecision` 两个新字段、
  `evaluate` 的登记逻辑、两条内置规则的 `EXISTS` 门。
- `tests/kernels/policy/test_policy_rule_observability.py`：新测试（含**反橡皮图章**）。
- `docs/spec/POLICY-ENFORCEMENT-DESIGN.md`：§1.2 表格补上"输入前提"与"实测不可触发"的诚实标注。

## 4. 验收判据

1. **零行为变化**：`tests/kernels/policy/` 既有 **109** 项全部仍然通过。
2. **新增可观测**：超预算 → `deny` 且 `unapplied_deny_rules` 为空；
   缺 `available`/缺 `cost` → 记录 `quota_enforcement` 与具体缺失路径；**判决与改动前逐字相同**。
3. **反橡皮图章**：逐字复刻**改动前**的规则（无 `EXISTS` 门、无登记）并断言
   「旧形态确实产不出任何未评估信号」，而新形态产出 ⇒ 双向对照。
4. **一致性**：`scope_enforcement` 同样被登记。
5. compileall / flake8 / importlib / 干净检出复跑全绿。

## 5. 诚实的边界（本轮）

- **不修** `quota_enforcement` 无法触发的问题（需要把经济系统的真实成本估算接进
  `estimated_cost`，属独立的接线任务，且会改变判决 ⇒ 需 boss 定）。→ **boss 已授权，已在 §6 完成。**
- **不修** `authorize` 的 `scope=L1` 过滤（同上）。→ **已在 §6 完成。**
- 新增的 `unapplied_deny_rules` 是**观测面**，不改变任何调用方的判决消费方式；
  调用方若要据此收紧，属后续决策。

---

## 6. 后续（2026-09-14，boss 授权后）：把「可观测的缺口」接成「真会拦的控制」

boss 授权执行「把 economy 的真实成本估算接进 `estimated_cost`，并修 `authorize` 的
scope 过滤」。改动如下。

### 6.1 让规则**可达**：`quota_enforcement` 的 scope `L2` → `L1`

`AgentPolicy.authorize` 是**唯一**的 agent 动作闸门，它以 `scope=L1` 求值；`evaluate`
只保留 `rule.scope <= 请求 scope` 的规则 ⇒ 声明 L2 的规则在**唯一可能用到它的路径上**
被整个滤掉。资源配额是与 `capability_required`（已 L1）并列的**逐动作护栏**，L1 是它
该在的位置。`precedence=150` 保持不变 —— 它必须压过应用层
`liuhao_agent_low_risk_allow`（precedence 50），否则"低风险"会掩盖"超预算"。

### 6.2 让规则**有真数据**：`agent_factory.economy_inputs()`

新增 `economy_inputs(principal, model, tokens_in, tokens_out) -> kwargs`，只从真实来源取：

| 输入 | 真实来源 |
|---|---|
| `action.estimated_cost` | `BillingEngine`（`economy.py`）的价目表；按模型定价取 `ModelRegistry.estimated_cost_per_1k` |
| `resource.available` | `ResourceQuotaManager`（resource 内核）的 `COST` 配额；优先该主体自己的，否则系统级；取最紧的一条 |

**绝不编数**。任一输入真 unknown 时，对应的 key **整个省略**，而不是传 0：
`0` 会是个谎言（0 永远在预算内 ⇒ 反而把护栏静音）；省略则让规则「不适用」，而上一轮
建立的可观测面**会把这件事报出来**（`unapplied_deny_rules` / `unresolved_operands`）。
为此给 `BillingEngine` 补了 `has_price(model)` —— 防止把"未定价"读成"免费"
（`estimate_cost` 对未知模型返回 0.0）。

取源失败**降级为省略 + WARNING**，不向授权路径抛异常：价目表损坏不该让助手整体倒下，
但也不能静默。

### 6.3 接到生产闸门：`LiuHaoAssistant.chat` / `chat_stream`

两处 `authorize("chat", ...)` 改为 splat `self._quota_authorize_kwargs(message)`，它用
prompt（system + 历史 + 本轮）+ 模型声明的 `max_output_tokens` 估**最坏情况**成本，再交给
`economy_inputs`。生产链路 `src/gateway/chat.py:88/:111 → assistant.chat()` 因此被覆盖。

### 6.4 验收（实测）

| 判据 | 结果 |
|---|---|
| 规则可达 | `quota_enforcement.scope == L1`，`precedence 150 > 50` |
| 超预算真拒 | `authorize(..., resource={"available":1}, estimated_cost=10)` → **DENY by `quota_enforcement`** |
| 预算内不打扰 | 同上但 `available=100` → 不 DENY |
| 缺输入仍 fail-open 且可观测 | `NOT_APPLICABLE` + `unapplied_deny_rules` 含该规则 |
| 未定价模型不当作免费 | `economy_inputs(..., "no-such-model", ...)` **无** `estimated_cost` 键 |
| 生产闸门真拦 | 把 `COST` 可用额压到 0 → `LiuHaoAssistant.chat()` 返回 `status="denied"` |
| 回归 | `tests/kernels/policy` + assistant + agent_factory + ai-layer + runtime_loop **208 passed**（随后在本轮加固后为 **179 passed / 整个 policy 目录**，见 §6.6） |

新测试 `tests/kernels/policy/test_quota_enforcement.py`（18 项，复核加固后 **39 项**）。上一轮的
`tests/kernels/policy/test_policy_rule_observability.py` 里**断言"规则不可达"的测试已被反转**
（`TestQuotaIsUnreachableInProduction` → `TestQuotaIsNowReachable`）；两条 AST 绊线也改为
断言"闸门确实在喂数据"—— 其中一条原本会**假装通过**（`liuhao` 用 `**splat` 传参，扫字面
`estimated_cost=` 关键字永远扫不到），已改成结构性断言。

### 6.5 仍然诚实的边界（**重要**）

1. **成本侧要真起作用，模型必须有价。** 已部署的模型（线上 `gpt-5.6-sol`、本机
   `qwen2.5:3b`）在 `ModelRegistry` 里**没有定价条目** ⇒ `economy_inputs` 会省略
   `estimated_cost` ⇒ 规则不适用（可观测，但不拦）。**把真实价格登记进模型注册表后，
   护栏立刻在生产生效** —— 价格是 boss 才知道的真实数字，我没有编。
   （注：本机 ollama 的真实成本**就是 0**，但"未定价"与"定价为 0"是两回事，代码刻意不混淆。）
2. **配额的消耗：Round 98 已接上，但记的是"预估最坏成本"，不是"实测用量"。**
   旧状态（`30df706b` / `d964231a` 时如实记录）：`ResourceQuotaManager` 的 `used/reserved`
   **没有任何生产调用点**在记账（`allocate()` 无调用方；`commit()` 是
   `@kernel_action("resource.commit")` 且**不在** internal-service 白名单）⇒ 默认系统 `COST`
   配额（$100）恒 `available=100` ⇒ 护栏只在配额被人为设紧时才拦。
   **Round 98 已闭合**：新增 `resource.account_spend`（LOW、进白名单，只把 `used` 往上推，
   比已放行的 `resource.release` 更保守），由 `LiuHaoAssistant._commit_turn` 在
   `status == "completed"` 时按**放行时评估的同一个数**记账（`_turn_estimated_cost`），
   主体自己有 `COST` 配额就记自己的，否则回落系统级。
   **仍然诚实的边界**：① 记的是**预估**成本（最坏情况输出），不是 provider 回传的真实
   token 用量 —— 用量回传要新增 provider 接口，未做；② 只覆盖 `chat`/`chat_stream`，
   §6.5 第 3 条的 `runtime_loop` / `network_gateway.delegate` **依然未接**。
3. **护栏只覆盖"真花 LLM 钱"的那条路。** 生产链路 `src/gateway/chat.py → LiuHaoAssistant.chat/
   chat_stream` 是用户实际让 OS 花钱的路径，已被覆盖。**未**覆盖：
   `runtime_loop.py:102`（agent 总线循环，`authorize(action="msg:<kind>")` 不传成本）与
   `network_gateway.delegate`（agent 间委派，`evaluate_simple(resource=params)` 不传成本）。
   这两处要接成本，得先有"一条总线消息 / 一次委派值多少 token"的模型 —— 目前不存在，
   我不编一个数字去凑。（要接，是独立一轮的设计决策。）

### 6.6 复核后的加固（Round 95，独立 verifier 提出）

独立验证 agent 复核 `30df706b` 时提出三项，其中两项是**真缺陷**，已修：

1. **认证路径可被畸形历史打崩（可用性缺陷）。** `_quota_authorize_kwargs` 用裸
   `len(m.get("content") or "")` 估 prompt；`self.history` 来自 `conversation_store.load_recent`
   （**外部持久化数据**，可损坏/被手改）。实测三种直接抛：
   `{"content": 1234}` → `TypeError`、非 dict 条目 → `AttributeError`、`system_prompt=9999`
   → `TypeError`，且**异常从 `chat()` 逃出去 = 认证路径 500**。
   修法：prompt/输出估算全部走不抛的 `_safe_text_len` / `_history_char_count` /
   `_declared_max_output_tokens`；`len()` 被限制在唯一被守卫的 `_safe_text_len` 里。
2. **荒谬的 `max_output_tokens` 会把护栏变成"全部拒绝"（误杀）。** 估算取**最坏情况**输出，
   于是 provider 声明 `max_output_tokens=12_000_000` × $0.01/1k = **$120 > $100 预算** ⇒
   一条 1 字符的合法消息被拒（实测 pre-fix：`estimated_cost=120.0`，`allowed=False`）。
   修法：`MAX_OUTPUT_TOKENS_CEILING = 32768` 夹紧（真实 provider 声明 1024–8192，天花板远高于
   真实补全长度 ⇒ 不削弱常态执法），**夹紧时 warning 出声**而非静默吞掉。
   实测 post-fix：同一场景 `allowed=True`。
3. **覆盖缺口** —— 即上面 §6.5 第 3 条，本轮**如实记录、不擅自扩面**。

反橡皮图章：新增测试对着**回退到 HEAD（未修）**的源文件跑，**18 项失败 / 3 项通过**
（通过的三项恰好是 `null`/缺 key 这类原本就没崩的输入）——证明这些断言真的在测新信号，
不是装饰。修后全绿。

