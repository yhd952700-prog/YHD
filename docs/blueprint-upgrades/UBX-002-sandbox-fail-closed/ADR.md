# ADR — UBX-002-sandbox-fail-closed: 沙箱 fail-closed 强制

> **Status:** ACCEPTED + IMPLEMENTED（2026-09-28）
> **Date:** 2026-09-28
> **Owner:** os-systems (os-impl)
> **Type:** auto-apply（fail-closed 收紧；不触及 HC-01 级人类主权事件，GOVERNANCE §3）
> **实现注记：** 默认态势为 **降级继续（warn）**，fail-closed 仅在 `LIUHAO_SANDBOX_ENFORCE=on` + 风险 HIGH/CRITICAL + 无真正隔离后端时触发。默认不阻断是为了避免无 gVisor 的本地开发被误伤；是否武装是部署决策（HD 级），见 verification-plan §5。

## 1. Context（为什么）
- **U39**：Windows 上 subprocess 沙箱**不强制资源限制**（无 CPU/内存上限），自主路径上的外部命令可无界占用主机。
- 当前沙箱是 **best-effort**：无法保证合约（限制 / 隔离）时仍运行，违反"fail-closed"总规则（RISK-REGISTER §4）。
- 与安全链 `Sandbox(if required)` 环节矛盾——沙箱"required"却无强制力。

## 2. Decision（决策）
沙箱执行**改为 fail-closed**：
1. 若沙箱被标记为 `required` 但**无法证明**其合约（资源限制 / 隔离）已生效 ⇒ 受管动作 **deny**，绝不无界运行。
2. 资源限制（CPU/内存/IO/时间）在所有受支持平台**必须有可验证的强制手段**；缺失平台的对应动作 deny。
3. 沙箱逃逸或合约失效 ⇒ 计入 RISK-REGISTER 的 fail-closed 行为（deny + 上报 UNVERIFIED）。

## 3. Consequences（后果）
- **正向：** 补 U39 的无界占用缺口；让安全链 `Sandbox` 环节名副其实。
- **负向：** Windows 上部分重度动作在限制手段落地前会被 deny（属正确收紧）。
- **不做：** 自主命令可拖垮主机，且沙箱形同虚设。

## 4. References
- UNKNOWN-TO-OWNER: `docs/autonomous/UNKNOWN-TO-OWNER.md#u39`
- RISK-REGISTER: `docs/autonomous/RISK-REGISTER.md` §4（fail-closed 总规则）
- 蓝图索引: `docs/spec/UNIFIED-BLUEPRINT.md §9`
- 配套: UBX-001（围栏内运行）、UBX-005（跨节点协调）
