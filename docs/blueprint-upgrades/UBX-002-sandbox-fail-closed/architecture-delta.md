# Architecture-Delta — UBX-002-sandbox-fail-closed

> 只描述，不实现。

## 1. In Scope / Out of Scope
- **In scope:** `WorldInterface` / 外部命令 / subprocess 适配器的沙箱**契约校验**；资源限制可验证性。
- **Out of scope:** 具体沙箱后端选型（container/WSL/jail）；身份围栏（→ UBX-001）。

## 2. Delta vs Current
| 维度 | 当前 | 目标 |
|---|---|---|
| 沙箱合约 | best-effort，无强制 | 合约不可证 ⇒ deny（fail-closed） |
| 资源限制（Win） | 未强制（U39） | 必须有可验证强制手段，否则 deny |
| 逃逸/失效上报 | 静默 | deny + 登记 UNVERIFIED |

## 3. Impact on the Chains
- **安全链：** `Sandbox(if required)` 环节改为"合约可证才放行"，否则 deny。
- **执行链（UBX-001）：** 沙箱在围栏之内运行；围栏 deny 优先于沙箱 best-effort。

## 4. Code Landpoints（仅描述）
- `src/ai/world_interface.py` 的 sandbox 构造点：引入 `sandbox_contract_verified` 前置校验。
- 平台能力探测：Windows 限制手段探测失败时置 deny 标志。

## 5. Blueprint Edits Triggered
- UNIFIED-BLUEPRINT §5 安全链：`Sandbox` 环节补注"合约不可证 ⇒ deny"。
