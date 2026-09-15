# 访问控制权威收敛设计（ACCESS-CONTROL-CONVERGENCE-DESIGN）

> 状态：**§1–§4 调研结论有效；§5 已被 2026-09-14 复测推翻并改判（见 §5.1/§5.2）**。
> 上半场已落地（`af5d4eab`：`src/kernels/security/_permission_map.py` + `decide_access` 可见化，判决逻辑未动）；
> 「下半场」——把 A 委托给 B——**经实测否决，不会实施**。
> ⚠️ 本文结论全部来自本轮实测。**不要引用 `docs/ARCHITECTURE-AUDIT.md` 的 P0 结论**——
> 它有两条已被实测证伪（见 `MEMORY.md` §内核安全债）。

## 1. 结论先行

仓内**不是两套，而是三套**并行的访问控制，且**实测零交叉**：

| | 权威 A | 权威 B | 权威 C |
|---|---|---|---|
| 位置 | `src/kernels/security/__init__.py` | `src/kernels/policy/__init__.py` + `src/kernels/_crosscutting.py` | `src/security/` |
| 标识格式 | **冒号**权限 `context:read` | **点号**动作 `context.set_scope` | **枚举**（不收字符串） |
| 入口 | `decide_access`(:550) | `evaluate_policy_simple`(:911) / `_adjudicate`(`_crosscutting.py:198`) | `check_access`(`rbac.py:566`) |
| 规则存储 | **全内存** `_rbac_rules`(:177)，12 条种子(:188-213) | `INTERNAL_SERVICE_ALLOWED_ACTIONS`(:81) + DENIED(:113) | `ResourceType` + `PermissionAction` 枚举(`rbac.py:21/35`) |
| 作用域 | 内核 | 内核（55 个 `@kernel_action`） | gateway / 租户边界 |
| 生产调用点 | **仅 1 处** `src/ai/governance.py:323` | 内核横切（所有 `@kernel_action`） | 服务层 |

⚠️ **上一轮记载需要修正**：点号动作**不在** `src/security/` 里，而在 `src/kernels/policy/`。
`src/security/` 是**独立的第三套**。

## 2. 零交叉 —— 实测证据

```
A('context:read' + VIEWER)        -> allow
A('context.set_scope')            -> deny    # _rbac_rules 无此键
A(..., human_override=True)       -> deny + "override REJECTED"   # SEC-5 修复生效
Policy('context.set_scope')       -> DENY  trace=RULE:default_deny
Policy('context.read')            -> DENY  trace=RULE:default_deny   # 两张表都没有
src/security(传字符串)             -> AttributeError: 'str' has no attribute 'value'
```

**唯一接触点**：`policy/__init__.py:105` 的允许表里有 `"security.decide_access"`——
B 认识 A 的**动作名**，但**不认识 A 的权限名**。这不是真正的交叉，只是"允许调用 A 这个动作"。

⇒ **盲区成立**：A 判 allow 的请求，B 完全不知道；B 判 deny 的请求，A 也不知道。
两套规则表互不引用，"策略说允许"与"另一套有没有查"之间没有任何保证。

## 3. A 的两个额外隐患

1. **规则全内存**：`_rbac_rules`(:177) 在进程内。12 条种子规则(:188-213) 重启后由代码重建，
   但**通过 `set_abac_rule`(:492) 新增的自定义规则会随重启丢失**——除非另有持久化（本轮未发现）。
2. **生产调用点只有 1 处**（`src/ai/governance.py:323`，经
   `LiuHao-O/packages/governance/__init__.py:8 → SecurityChain`）。
   ⇒ A 的实际覆盖面远小于它的规则数量，收敛的紧迫性低于"看起来"的程度。

## 4. 孤儿文件（grep 实测引用数，**不是**数 import）

| 文件 | 代码引用数 | 状态 |
|---|---|---|
| `src/security/vault_client.py` | **0** | 死副本；`src/security/vault_crypto.py:15` 明确 `from ..integrations.vault.client import VaultClient` |
| `src/security/vault_transit.py` | **0** | 同上 |
| `src/security/rbac_abac.py`（572 行） | **0** | 其同名测试实际测的是 `security.rbac` |
| `src/storage/repository.py` | **0** | 第 3 份 `Repository` |

`orphan-registry.yaml:22/31/38/46` 与 `docs/ORPHAN-AUDIT.md:60-63` 的登记**与实测一致**。

## 5. 收敛建议 —— ⚠️ **原方案已被实测证伪（2026-09-14 复测）**

**结论改判：不要执行「A 委托 B」。** 原设计（见 5.3 存档）假设「先补映射、再把 A 切到 B」
即可收敛。复测证明该假设不成立：**映射可以补，但 B 侧没有对应动作可补，而且委托会连主体维度一起丢掉。**
证据见 5.1，全部可复现（探针在 `D:\cache\temp\`，用 `.venv/Scripts/python.exe` 跑）。

### 5.1 推翻原方案的实测证据

**证据 1 —— `_adjudicate` 不接受 principal，委托等于静默删掉 RBAC。**
`src/kernels/_crosscutting.py:198` 的签名是 `_adjudicate(action: str, risk_level: str)`：
**没有 principal 参数**。它内部用固定身份
`actor = {"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL}` 调 `evaluate_policy_simple`。

而 A 与 B 回答的**根本不是同一个问题**：

| | A `decide_access(principal_id, permission)` | B `_adjudicate(action, risk_level)` |
|---|---|---|
| 问的是 | **谁**（principal）有没有**这条权限** | **某个动作**对内部服务是否放行 |
| 输入维度 | 主体 + 权限 + scope | 只有动作（+ 风险等级） |
| 规则来源 | `_principal_roles` 角色绑定 | `INTERNAL_SERVICE_ALLOWED_ACTIONS` 动作白名单 |

⇒ 委托后 `decide_access` 对**任何** principal 返回同一答案：boss 与陌生调用者无区别。
这不是「收敛成一套」，是把主体级 RBAC 降级成一张动作白名单（一次默认放行的提权面）。

**证据 2 —— B 无法表达 A 的 12 条授权，实测 10/12 会从 allow 翻成 deny。**
`INTERNAL_SERVICE_ALLOWED_ACTIONS` 实测共 **15** 条，**没有任何 read 类动作**
（全部是 `context.compress` / `memory.store` / `event.publish` / `execution.execute` 这类**效应型**动作）。
把 A 的 12 条种子权限（`security/__init__.py:200-213`）按 `_permission_map.py` 映射后，
逐条送进 `_adjudicate` 的实际结果：

| A 权限 | 种子角色 | 映射到的 B 动作 | B 实测判决 | 切换后果 |
|---|---|---|---|---|
| `context:read` | viewer | （无对应） | `deny` / `default_deny` | allow → **deny** |
| `capability:lookup` | viewer | （无对应） | `deny` / `default_deny` | allow → **deny** |
| `resource:query` | viewer | （无对应） | `deny` / `default_deny` | allow → **deny** |
| `execution:plan` | operator | （无对应） | `deny` / `default_deny` | allow → **deny** |
| `audit:query` | auditor | （无对应） | `deny` / `default_deny` | allow → **deny** |
| `context:write` | admin | `context.set_scope` | `deny` / `default_deny`（不在允许表） | allow → **deny** |
| `capability:manage` | admin | `capability.register`/`.deprecate`/`.retire` | `deny` / `default_deny`（不在允许表） | allow → **deny** |
| `resource:allocate` | operator | `resource.allocate` | `deny` / `default_deny`（不在允许表） | allow → **deny** |
| `policy:manage` | admin | （无对应） | `deny` / `default_deny` | allow → **deny** |
| `audit:log` | admin | （无对应） | `deny` / `default_deny` | allow → **deny** |
| `execution:trigger` | admin | `execution.execute` | **`allow` / `internal_service_allow`** | 不变 |
| `evaluation:run` | operator | `evaluation.evaluate` | **`allow` / `internal_service_allow`** | 不变 |

⇒ **10 / 12 断，2 / 12 幸存**，断掉的包括**全部读权限**与 3 条管理权限。

> 原设计（5.3 第 3 条）写的「不补就切 = 12 条全落 `default_deny`」**低估了问题**：
> **补了也还是 10 条断**。因为缺的不是映射，是 B 侧的动作——B 里压根没有读动作。

**根因**：A 与 B **不是同一种东西**。A 是 *principal → permission* 的角色表；
B 是 *action* 的服务白名单。既然它们不是「同一个问题的两套实现」，
就不存在「收敛成一套」的路径——只有**职责切分**。

### 5.2 改判后的方案：职责切分（单一权威按问题域，不合并）

1. **「某个内核动作是否放行」= 权威 B**（`@kernel_action` / `INTERNAL_SERVICE_ALLOWED_ACTIONS`）。
   B 维持现状，**不吸收** A 的权限名。
2. **「某个 principal 是否持有某条权限」= 权威 A**（冒号 RBAC）。A **不下沉到 B**。
3. 两者靠 `src/kernels/security/_permission_map.py` 的映射**互相可见**（上半场 `af5d4eab` 已落地）：
   A 的判决结果里带 `kernel_actions`，B 侧动作可反查对应权限。**可见 ≠ 合并，可见就是终点。**
4. 权威 C（`src/security/`）明文划为 gateway / 租户边界，禁止承担内核授权职责。
5. **「盲区」不再靠切换消除，改靠可见性消除**：`decide_access` 已能区分
   「未知被拒 / 未播种被拒 / 查过并拒」，`unmapped_permissions()` / `unseeded_permissions()`
   让缺口可枚举、可断言。**盲区从"无人知道"变成"可被测试发现"，这已是本问题能达到的上限。**

### 5.3 原设计（**存档对照，勿照做**）

> 以下 4 条是复测前写的，保留以便追溯为什么改判。第 1–3 条已被 5.1 的实测推翻。

1. ~~**唯一内核权威 = 权威 B（policy，点号）**。~~
2. ~~权威 A **降级为 adapter**：保留 `decide_access` 的公开签名（不破坏 `governance.py:323`），
   内部改为委托 `_adjudicate`。~~
3. ~~**先补后切**：把 A 的 12 条冒号权限**逐条映射成点号动作**并显式写进 ALLOWED/DENIED。~~
4. 权威 C（`src/security/`）**明文划为 gateway / 租户边界**，禁止承担内核授权职责。← **这一条仍然有效**

### 5.4 风险与回滚（对改判后的方案）

| | |
|---|---|
| 风险 | 职责切分**不消除**盲区：A 判 allow 的动作，B 仍不知道（反之亦然）。这是被接受的残留风险 |
| 缓解 | 映射表 + `unmapped_permissions()` 让缺口可枚举；`decide_access` 的判决结果里直接带 `kernel_actions`，调用方可自行校验 |
| 回滚 | 无需回滚 —— 本方案**不改任何判决逻辑**，只补可见性 |
| 如果将来真要合并 | 前提是先给 B 补齐读动作**并**给 `_adjudicate` 加 principal 入口；那是一次独立立项，不是"下半场" |

## 6. 需 boss 拍板

1. ~~**是否收敛**~~ → **已由实测回答，不需要拍板**：不合并。A 与 B 不是同一问题的两套实现，
   合并会让 10/12 条权限断掉并丢掉主体维度（§5.1）。改判为职责切分（§5.2），**不涉及任何行为变更**。
2. **A 的 12 条冒号权限，哪些可弃、哪些必须保留** —— 仍待确认实际消费者
   （目前只找到 1 处生产调用点 `governance.py:323`）。⚠️ 注意：这一条**不再是收敛的前置条件**
   （既然不下沉，就不必先修规则表），它只是"这片规则表是否过剩"的独立问题。
3. **孤儿文件是否删除**（目前只登记未删，我倾向先删 4 个零引用模块，但这是不可逆操作，等你点头）。

## 7. 诚实的边界

**2026-09-14 复测新增「已验证」的一节**（此前是推断，现在是实测）：

- ✅ `_adjudicate` 无 principal 入口（读签名 + 全文件搜 policy 判决入口确认无第二处）。
- ✅ `INTERNAL_SERVICE_ALLOWED_ACTIONS` = 15 条，无 read 类动作（逐条列出核对）。
- ✅ A 的 12 条种子权限逐个送 `_adjudicate` 的判决结果（10 deny / 2 allow）。
- ✅ 用两个不同 principal 跑同一条权限拿到不同结果，证明 A 确实是主体驱动的。

**仍未验证（保持原样，不含糊）**：

- `deploy/cloud/` 里的副本是否随发布包部署（只查了本地仓）。
- `is_human_identity` 在**真实身份数据**下的行为（只测了"未注册 principal 被拒"）。
- A 的 12 条冒号权限的完整消费者清单（只找到 1 处，可能有间接调用未覆盖）。
- 权威 C 在服务层的实际覆盖面（未逐条扫其调用点）。
- 权威 A 的自定义规则（`set_abac_rule`）是否真的只有内存一份（未找到持久化，但未做重启实测）。
