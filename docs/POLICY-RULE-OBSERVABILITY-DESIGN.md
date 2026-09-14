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

### 1.3 但真正更严重的是：这条规则**在生产里根本不可能触发**

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

### 2.3 为什么不顺手改 `authorize` 的 `scope=L1`

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

## 5. 诚实的边界

- **不修** `quota_enforcement` 无法触发的问题（需要把经济系统的真实成本估算接进
  `estimated_cost`，属独立的接线任务，且会改变判决 ⇒ 需 boss 定）。
- **不修** `authorize` 的 `scope=L1` 过滤（同上）。
- 新增的 `unapplied_deny_rules` 是**观测面**，不改变任何调用方的判决消费方式；
  调用方若要据此收紧，属后续决策。
