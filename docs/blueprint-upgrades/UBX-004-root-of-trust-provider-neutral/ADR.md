# ADR — UBX-004-root-of-trust-provider-neutral: 根信任 providers 中立（local / rfc3161 / tpm 可插拔）

> **Status:** PROPOSED
> **Date:** 2026-09-28
> **Owner:** governance-legal (gov-impl)
> **Type:** **部分 human-decision-gated** —— 机制（provider 中立）auto-apply；**最终生产 provider 选择属 HC-01 级（GOVERNANCE §3），升给人**

## 1. Context（为什么）
- **U54（原 U36/HD-05）**：HD-05 本地可信时间戳 **mock 是自签名 RSA-3072**（进程内临时密钥），未锚定任何外部 CA/TSA/TPM。把它当法院可采信时间戳 = **假信任**。
- 当前根信任**硬编码**为本地 mock，无法切换；这是 provider lock-in，也是合规风险。

## 2. Decision（决策）
根信任改为 **provider-neutral** 抽象：
1. 定义 `RootOfTrustProvider` 接口：`local`（默认，自签名 dev/placement 桩）、`rfc3161`（外部 TSA）、`tpm`（硬件根）、`external-ca`。
2. 通过配置 `LIUHAO_TSA_PROVIDER` 选择；默认 `local` 但**明确标注为不可采信**。
3. 任何"对外声称可信"的证据封印，**默认 local 下必须打 `self_attested: true` 标记**，禁止伪称第三方可信。
4. **最终生产 provider（TSA/TPM/CA 选型与采购）属 HC-01 级人类主权决策**，保留 HUMAN DECISION，团队不得自动落地。

## 3. Consequences（后果）
- **正向：** 解除 provider lock-in；消除"本地 mock 当第三方"的假信任（U54）。
- **负向：** 需实现 provider 抽象层与切换逻辑；合规采信仍需 human 拍板。
- **不做：** 长期把自签名 mock 当生产根信任 = 持续假信任。

## 4. References
- UNKNOWN-TO-OWNER: `docs/autonomous/UNKNOWN-TO-OWNER.md#u54`（原 U36/HD-05，已重编号）
- RISK-REGISTER: `docs/autonomous/RISK-REGISTER.md`（R-G3-08 未锚定尾段的可采性属 HUMAN/治理-legal）
- 蓝图索引: `docs/spec/UNIFIED-BLUEPRINT.md §9`
