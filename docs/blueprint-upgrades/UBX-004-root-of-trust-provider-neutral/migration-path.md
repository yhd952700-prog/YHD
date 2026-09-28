# Migration-Path — UBX-004-root-of-trust-provider-neutral

## 1. Rollout Steps
1. 抽象 `RootOfTrustProvider` 接口，落地 `local` 默认实现。
2. 在所有封印路径加 `provider` + `self_attested` 字段（local ⇒ `true`）。
3. 接入 `LIUHAO_TSA_PROVIDER` 配置；`rfc3161/tpm` 留骨架。
4. 触发 HUMAN DECISION：最终生产 provider 选型（GOVERNANCE §3）。

## 2. Compatibility
向后兼容：默认仍是 `local`，行为不变，仅加标记。

## 3. Rollback
revert 回硬编码 local（丢失可插拔），无数据迁移。

## 4. Approval Gate
- [x] **auto-apply**：provider 抽象与标记机制（团队自主）。
- [x] **human-decision-gated**：最终生产 TSA/TPM/CA 选型与采购 —— 升给 owner，不阻塞其余工作。

## 5. Sequencing
依赖 UBX-001（围栏内运行）。独立可先行于 UBX-005。
