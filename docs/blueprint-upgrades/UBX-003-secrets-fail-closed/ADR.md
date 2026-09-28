# ADR — UBX-003-secrets-fail-closed: 密钥 fail-closed（不泄漏 / 不静默失败）

> **Status:** PROPOSED
> **Date:** 2026-09-28
> **Owner:** security-identity (sec-impl)
> **Type:** auto-apply（fail-closed 收紧；不触及 HC-01 级人类主权事件）

## 1. Context（为什么）
- **U45**：`revoke_all_user_tokens` 仅是**内存级**补救，跨重启 / 跨 worker 不持久——吊销在重启后失效，等于没吊销。
- 密钥访问当前缺统一 fail-closed 语义：secret 存储不可达 / 读取失败 / 解密失败时，系统可能 fallback 或静默放行，违反"不假装成功"（GOVERNANCE §2.2）。
- `UNKNOWN-TO-OWNER.md:121` 已确认密钥生成用 `secrets.token_*`（强 CSPRNG），本 UBX 把**使用与吊销**也纳入 fail-closed。

## 2. Decision（决策）
密钥全生命周期 fail-closed：
1. **访问**：secret 存储不可达 / 解密失败 / 权限不足 ⇒ **refuse**，绝不 fallback 到明文、默认值或缓存残留。
2. **吊销（U45）**：token 吊销必须**持久且跨 worker 一致**（落持久层 + 广播失效），重启后仍生效；吊销写入失败 ⇒ deny 续权。
3. **泄漏防护**：审计仅 redact secrets（已有），本 UBX 固化该规则为硬约束；PII 不在本 UBX 范围（→ U36 PII 框架，属 human-decision）。

## 3. Consequences（后果）
- **正向：** 闭合 U45 的"重启即失效吊销"；密钥故障不再静默放行。
- **负向：** 持久层不可用时所有需密钥的动作 deny（正确 fail-closed）。
- **不做：** 吊销可被重启绕过，密钥故障静默放行 = 静默越权。

## 4. References
- UNKNOWN-TO-OWNER: `docs/autonomous/UNKNOWN-TO-OWNER.md#u45`、密钥生成注记（L121）
- RISK-REGISTER: `docs/autonomous/RISK-REGISTER.md` §4（fail-closed 总规则）
- 蓝图索引: `docs/spec/UNIFIED-BLUEPRINT.md §9`
