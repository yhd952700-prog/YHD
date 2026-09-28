# ADR — UBX-001-executor-fence: 执行器身份 / 租约 / 围栏 / default-deny 贯穿整条执行链

> **Status:** PROPOSED（**实现中** —— 本波 p36 正在落地核心 gate）
> **Date:** 2026-09-28
> **Owner:** security-identity (sec-impl)
> **Type:** auto-apply（fail-closed 收紧 + 已实现能力登记；不触及 HC-01 级人类主权事件，GOVERNANCE §3）

## 1. Context（为什么）

UNIFIED-BLUEPRINT §5 的**安全链**要求每个 Critical Action 必过
`Identity → Authentication → Authorization → Capability → Policy → Approval → Sandbox → Execution → Verification → Audit`。
但执行链的实际落地存在三个已知缺口：

- **U38 / U42**（permit-by-default）：`WorldInterface` / `L-Core` 在 `authorize is None` 时**默认放行**；
  自主路径上的 shell 适配器正坐在这个默认放行之后（U38）。`WorldInterface` 自主路径已在 `6d7769e6`
  改为 default-deny + `actor="autonomous"`，但这是**逐点修复**，不是贯穿整链的统一围栏。
- **U47**（C-6 不 inert 回归）：`ac3a90bc` 加了围栏自锁但**缺死进程检测**，跨进程 `StaleWriterError`
  会**强制证据自锁**（主动锁死而非安全失效），违反 C-6 "inert" 保证。
- **U12**（Redis-less 存根）：跨进程围栏 / 选主 / 租约尚无真实原语，单写者无跨进程强制力。

**根因**：执行链没有一个**统一的 executor 身份 + 租约 + 围栏令牌**语义，导致"谁能执行、其权限是否还有效、它是否已被取代"三件事在每个执行点各算各的。

## 2. Decision（决策）

引入**贯穿整条执行链的 executor 围栏语义**，四件套目标：

1. **executor_id**：每个执行站点必须携带一个**经签名/可验证**的 `executor_id`（绑定到具体 agent/进程/lease），拒绝匿名执行主体。
2. **lease（租约）**：执行主体持有带 TTL 的租约；租约过期或撤销 ⇒ 该执行主体的后续动作被 deny。
3. **fencing token（围栏令牌）**：每次租约接管分配**单调递增**的 fencing token；写入/关键动作携带的 token 非当前值 ⇒ 拒绝（这正是 U12 / C3 同类机制在执行层的落地）。
4. **default-deny across the chain**：执行链上任何缺少有效 `executor_id + 活租约 + 当前 fencing token` 的动作，**默认 deny**，不允许 best-effort 放行。

> 蓝图裁决联动：UNIFIED-BLUEPRINT §5 安全链补充一条硬约束——"Execution 之前必须完成 executor 围栏校验"，
> 列入 §3 裁决表（新增 C13：执行链默认围栏 = default-deny）。

## 3. Consequences（后果）

- **正向：** 补齐 U38/U42 的系统性默认放行漏洞；给 U47 的"强制自锁"回归正确的 inert 语义；
  为 UBX-005（分布式真实协调）提供单节点内的围栏原语基础。
- **负向 / 代价：** 每个执行站点需注入/校验围栏三要素，少量样板；需要死进程检测（Fix A，sec-impl 负责）才能真正 inert。
- **不做会怎样：** 自主路径上的 host-command / 外部写能力仍可能默认放行，且跨进程陈旧写者无法被安全取代。

## 4. References
- UNKNOWN-TO-OWNER: `docs/autonomous/UNKNOWN-TO-OWNER.md#u12`（围栏存根）、`#u38`（shell 默认放行）、`#u42`（permit-by-default 框架）、`#u47`（C-6 不 inert）
- RISK-REGISTER: `docs/autonomous/RISK-REGISTER.md`（R-G3-09 跨节点围栏、R-G2-07c 锁持有）
- 蓝图索引: `docs/spec/UNIFIED-BLUEPRINT.md §9`
- 配套升级: UBX-005（分布式真实协调，承接跨节点围栏）
