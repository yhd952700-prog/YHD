# Architecture-Delta — UBX-003-secrets-fail-closed

> 只描述，不实现。

## 1. In Scope / Out of Scope
- **In scope:** secret 读取 / 解密 / token 吊销的 fail-closed 语义；吊销的持久与跨 worker 一致。
- **Out of scope:** PII 合规框架（→ U36，human-decision）；根信任后端（→ UBX-004）。

## 2. Delta vs Current
| 维度 | 当前 | 目标 |
|---|---|---|
| 密钥读取失败 | 可能 fallback / 静默 | refuse（fail-closed） |
| token 吊销（U45） | 内存级，重启失效 | 持久 + 跨 worker 一致 |
| 审计 redact | 仅 secrets（已有） | 固化为硬约束 |

## 3. Impact on the Chains
- **安全链：** `Authorization` / `Capability` 环节密钥校验失败 ⇒ deny。
- **记忆链：** 不影响（scope 过滤独立）。

## 4. Code Landpoints（仅描述）
- `src/security/`：secret 访问封装 fail-closed 入口。
- token 吊销服务：持久层写入 + 失效广播（替代纯内存 `revoke_all_user_tokens`）。

## 5. Blueprint Edits Triggered
- UNIFIED-BLUEPRINT §3：新增 **C14 — 密钥故障 = fail-closed refuse**（不 fallback）。
