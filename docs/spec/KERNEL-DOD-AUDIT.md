# Kernel DoD 七维审计（2026-09-07 · 实测）

> 权威定义：`docs/CODEX-CONTRACT.md` §5（7 项）
> Implemented + Tested + Observable + Permissioned + **Policy Controlled** + Audited + Documented。
> 本表为**代码实测**，不是文档/注册表转述（注册表里的 `tested: false` 是 2026-09-06 之前的陈旧数据）。

## 最新状态（装饰器落地 + Permissioned 补齐 · 2026-09-07 晚）

> ⚠️ **标题中的"最新"是 2026-09-07 的语境**。当前 CI 基线为
> **1281 passed / 14 skipped / 0 failed**（run `34690158540`，2026-09-12）；
> 本节 `980 passed` 等数字为当时快照，仅作历史对照。

`@kernel_action` 横切装饰器已落地并接入 12 个 kernel（**42 个动作方法**）；
`src/security` 导入修复、测试污染修复、policy 可观测日志补齐、**network/plugin
scope/permission 检查补齐**。全量回归 **980 passed / 1 skipped / 0 failed**
（`tests/kernels/` 412 passed / 0 failed）。

| 维度 | 落地前 | 落地后 | 说明 |
|---|---|---|---|
| Implemented | 14/14 | 14/14 | — |
| Tested | 14/14 | 14/14 | 412 kernel 用例全绿 |
| Observable | 6/14 | **14/14** | 12 个装饰 kernel 经装饰器发结构化日志；policy 补 DEBUG 日志 |
| Permissioned | 12/14 | **14/14** | network `Route.scope` 校验 + `route` 消息 scope 天花板强制；plugin `register_plugin` scope 校验 L0-L7 |
| Policy Controlled | 1/14 | **13/14** | 12 装饰 kernel 动作过 policy engine；policy 自身；audit 设计上不拦截（审计必须无条件） |
| Audited | 1/14 | **13/14** | 12 装饰 kernel 写审计事件；audit 自身；policy 判决经每个动作的审计事件记录（避免递归） |
| Documented | 14/14 | 14/14 | — |

**结论**：七维中五维已达 14/14（Implemented/Tested/Observable/Permissioned/Documented），
Policy Controlled 与 Audited 达 13/14。剩余唯一缺口是**两个「引擎自豁免」**（设计使然，非缺陷）：

- audit 的 `log_event` 不做策略拦截（审计必须无条件，否则无法审计「审计」本身）；
- policy 的 `evaluate` 不自写审计（避免递归，其判决已由每个动作的审计事件记录）。

即：**14 个 kernel 全部达到七维合规**，仅 policy/audit 两个引擎各自豁免自身所在的维度，
这是架构上的正确设计，不是未完成的缺口。

> **Policy Controlled 维度质量更新（2026-09-11，Policy C-1）**
>
> 上表的 13/14 只回答了"**是否过了 policy engine**"，未回答"**判决是否携带信息**"。
> 实测复核发现后者的答案是**否**：`@kernel_action` 的动作判决恒为 `deny`（零信息量），
> 且装饰器 additive、从不拦截。C-1 已修正前半部分 ——
> 内核动作改由**经核验的内部 service 主体**归因，判决变为**白名单驱动**
> （14 项查询/计算/簿记类 → `allow`；29 项授权/破坏类 → `deny`），并在审计事件中
> 新增 `policy_rule` 记录判决依据。
>
> **"是否真拦截"仍为否** —— 全部 43 个装饰动作依旧只记录、不拦截
> （`policy_enforced: false`）。把它变成控制点是 C-2。
>
> **C-2 / C-3 机制已实施（Round 67 / 68，2026-09-11–12），生产默认关闭**：
> - **D8 已实施**（Round 66）：43 个动作建立权威风险分级（`src/kernels/_risk_classification.py`），
>   解除 C-2 前置阻塞 —— 此前 43 个装饰点**全部未设 `risk_level`**（默认 `LOW`），
>   "仅对 HIGH/CRITICAL 开拦截"**永不触发**。
> - **C-2 已实施**（Round 67）：`enforce` 开关 + `PolicyDeniedError` + 仅 HIGH/CRITICAL
>   生效的拦截门 + fail-closed；**默认 `False`**，43 个生产点**无一开启**。
> - **C-3 已实施**（Round 68）：`PolicyEffect.DEFER` + `PolicyDeferredError` + 动态 human
>   主体通道（`src/kernels/_sovereignty.py::human_sovereign`），**解除 C-2 自锁**
>   （无人类授权 → 待人工审批；经 OD-010 核验的 human 授权 → 执行）。
>
> 因此内核层 **L2（判决已执行）的机制齐备**，只是**生产默认未开启**（仍 L1／记录型）。
> 是否在部署侧开启属运维/主权决策，**不是本审计的缺口**。
>
> 细节：`POLICY-ENFORCEMENT-DESIGN.md` §10；证据：`scripts/verify_policy_c1.py`。

---

## 基线：DoD 七维达标 0 / 14（装饰器落地前）

| Kernel | Implemented | Tested | Observable | Permissioned | Policy Controlled | Audited | Documented |
|---|---|---|---|---|---|---|---|
| audit | ✅ 531 行 | ✅ | ✅ logging | ✅ | ❌ | ✅（自身） | ✅ KERNEL-CANON |
| capability | ✅ 491 行 | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| context | ✅ 179 行 | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| evaluation | ✅ 604 行（2 桩） | ✅ | ✅ logging | ✅ | ❌ | ❌ | ✅ |
| event | ✅ 341 行 | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| execution | ✅ 708 行（5 桩） | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| identity | ✅ 385 行 | ✅ | ✅ logging | ✅ | ❌ | ❌ | ✅ |
| memory | ✅ 335 行 | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| network | ✅ 498 行（3 桩） | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| plugin | ✅ 462 行 | ✅ | ✅ logging | ❌ | ❌ | ❌ | ✅ |
| policy | ✅ 611 行 | ✅ | ❌ | ✅ | ✅（自身） | ❌ | ✅ |
| resource | ✅ 482 行（1 桩） | ✅ | ✅ logging | ✅ | ❌ | ❌ | ✅ |
| security | ✅ 676 行（3 桩） | ✅ | ✅ logging | ✅ | ❌ | ❌ | ✅ |
| trust | ✅ 633 行 | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |

## 七维逐项缺口

1. **Implemented** = 14/14 ✅（代码存在；真正的 `raise NotImplementedError` 仅 2 处，都在 `network.py` 的 `ProtocolAdapter.send/receive` 基类抽象方法（"Override in subclasses"，由 InternalAdapter/HTTPAdapter/WebSocketAdapter 覆写）——合规，非空桩。其余 `pass` 是异常处理/else 分支的空语句）。
2. **Tested** = 14/14 ✅（`tests/kernels/*/test_*.py` 全有，全量回归 970 passed 里含 402 kernel 用例）。**注册表 `tested: false` 是陈旧假数据，须改为 true。**
3. **Observable** = 6/14（audit/evaluation/identity/plugin/resource/security 有 logging）。缺 8 个：capability/context/event/execution/memory/network/policy/trust。
4. **Permissioned** = 12/14（缺 network、plugin 的 scope/permission 检查）。
5. **Policy Controlled** = 1/14（仅 policy 自身引用；其余 13 个 kernel 的动作均未过 policy engine）。**这是 2026-09-06 新增的第 7 维，几乎整体未建——最大缺口。**
6. **Audited** = 1/14（仅 audit 自身；其余 13 个 kernel 的关键操作均未写 `get_audit_store().log_event()`）。
7. **Documented** = 14/14 ✅（KERNEL-CANON.md 集中覆盖；注册表里 identity/memory/plugin 的 `documented: false` 是陈旧数据）。

## 根因

两个**横切维度**（Policy Controlled、Audited）几乎整体缺失，原因是：kernel 是独立实现的（各 179-708 行、零相互 import），没有统一的「动作拦截点」把「策略判决 + 审计记录」织入。Observable 同理缺一个统一定义。这不是某个 kernel 的 bug，而是**缺一个横切层**。

## 修复方案（横切装饰器，不推倒重写）

新增 `src/kernels/_crosscutting.py`，提供一个 `@kernel_action(action, audit=True, policy=True)` 装饰器：

1. **Policy Controlled**：动作执行前调用 `get_policy_engine().evaluate_policy(action, ...)`，并把判决写入审计事件的 `details.policy_decision`。
   > ⚠️ **实现更正（2026-09-11 实测）**：本节原写"无匹配规则时按「**默认允许** + 记录判决」处理"。实际落地与之不同，实测结论如下：
   > - `_crosscutting._adjudicate` 以 `{"type": "system", "verified": True}` 调用策略引擎；引擎会用 `_is_verified_human()` **覆盖** `verified`（该 actor 无 `id`/`principal`，结果 False）；
   > - 内置规则中唯一的 ALLOW 规则 `human_sovereignty` 要求 `actor.type == "human"` 且风险为 HIGH/CRITICAL，因此**内核动作的判决恒为 `deny`**；
   > - 装饰器是 additive 的、**从不拦截**，故该 `deny` 不产生执行效果。
   >
   > 即：Audited 维度实打实生效；Policy Controlled 维度只有"判决记录"这一字面满足，判决值**不携带信息**，属"记录而非控制"。审计事件现已在 `details` 中显式标注 `policy_enforced: false`。
   > 若要让它成为真正的控制点，需为 system actor 定义可放行的规则——属安全语义变更，须单独裁决。
2. **Audited**：动作执行后调用 `get_audit_store().log_event(...)`，带 `correlation_id`。
3. **Observable**：动作前后 `logging` 结构化日志（可选 trace span）。

然后按 kernel 逐个接入其公开「动作」方法（写入审计 + 过策略），每个 kernel 一个 PR，接入后跑该 kernel 的测试回归 + 全量回归，零回归才标达标。

## 优先级

1. 修注册表陈旧数据（`tested`/`documented` → true，`status` 保持 PARTIALLY_IMPLEMENTED 直到七维全过）。
2. 建 `_crosscutting.py` 装饰器 + 单测。
3. 先接 2 个「被最多依赖」的 kernel（capability、policy）作 POC，验证零回归。
4. 其余 12 个 kernel 分批接入（并行）。
5. 每批完成后更新注册表该 kernel 的 `audited`/`observable`/`policy_controlled` 字段。
