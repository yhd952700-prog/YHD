# 访问控制权威收敛设计（ACCESS-CONTROL-CONVERGENCE-DESIGN）

> 状态：**设计稿，待 boss 拍板后实现**。本轮只做调研与设计，未改生产代码。
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

## 5. 收敛建议：三层映射，**先补后切**（不搞二选一）

1. **唯一内核权威 = 权威 B（policy，点号）**。
2. 权威 A **降级为 adapter**：保留 `decide_access` 的公开签名（不破坏 `governance.py:323`），
   内部改为委托 `_adjudicate`。
3. **先补后切**：把 A 的 12 条冒号权限**逐条映射成点号动作**并显式写进 ALLOWED/DENIED。
   ⚠️ **不补就切 = 这 12 条全部落到 `default_deny` ⇒ 功能直接断**（实测已证明 B 不认识它们）。
4. 权威 C（`src/security/`）**明文划为 gateway / 租户边界**，禁止承担内核授权职责。

### 风险与回滚

| | |
|---|---|
| 风险 | 收敛后 fail-closed 会让 deny 变多；A 的自定义规则若只存在于内存，切换时可能丢失 |
| 回滚 | `_adjudicate` 返回 `None` 时回落旧 RBAC；加 env 开关（默认关） |
| 不动的代价 | 盲区持续存在；A 规则全内存（重启丢自定义规则） |
| 动了的代价 | 短期 deny 增加，长期单一权威 |

## 6. 需 boss 拍板

1. **是否收敛**（我建议收敛，但**必须先补映射再切**）。
2. **A 的 12 条冒号权限，哪些可弃、哪些必须保留** —— 需确认它们的实际消费者
   （本轮只找到 1 处生产调用点 `governance.py:323`）。
3. **孤儿文件是否删除**（目前只登记未删，我倾向先删 4 个零引用模块，但这是不可逆操作，等你点头）。

## 7. 诚实的边界（本轮未验证）

- `deploy/cloud/` 里的副本是否随发布包部署（只查了本地仓）。
- `is_human_identity` 在**真实身份数据**下的行为（本轮只测了"未注册 principal 被拒"）。
- A 的 12 条冒号权限的完整消费者清单（只找到 1 处，可能有间接调用未覆盖）。
- 权威 C 在服务层的实际覆盖面（本轮未逐条扫其调用点）。
- 本轮未跑全量 pytest（会 MemoryError），只跑了 policy 子集。
