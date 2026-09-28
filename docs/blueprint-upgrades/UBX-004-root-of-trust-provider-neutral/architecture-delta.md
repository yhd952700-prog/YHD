# Architecture-Delta — UBX-004-root-of-trust-provider-neutral

> 只描述，不实现。

## 1. In Scope / Out of Scope
- **In scope:** `RootOfTrustProvider` 接口与 `local/rfc3161/tpm/external-ca` 实现骨架；`LIUHAO_TSA_PROVIDER` 配置开关；`self_attested` 标记。
- **Out of scope:** 实际 TSA/TPM 采购与证书（→ HC-01 级 human-decision）。

## 2. Delta vs Current
| 维度 | 当前 | 目标 |
|---|---|---|
| 根信任后端 | 硬编码 local 自签名 | provider 可插拔 |
| 可采信声明 | 无标记，易误用 | local 强制 `self_attested: true` |
| 生产选型 | 无 | 配置驱动；最终选型 human 拍板 |

## 3. Impact on the Chains
- **安全链（Verification / Audit）：** 证据封印带 provider 来源标记；非可信 provider 的证据不声称第三方采信。

## 4. Code Landpoints（仅描述）
- `src/security/root_of_trust.py`（新增）：provider 接口 + local 默认实现。
- 封印路径：写入 `provider` + `self_attested` 字段。

## 5. Blueprint Edits Triggered
- UNIFIED-BLUEPRINT §9 活体登记册：HD-05 关联升级指向本 UBX（U54）。
