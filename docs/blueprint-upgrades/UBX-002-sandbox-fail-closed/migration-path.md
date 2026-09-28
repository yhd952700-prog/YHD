# Migration-Path — UBX-002-sandbox-fail-closed

## 1. Rollout Steps
1. 在 sandbox 构造点加 `sandbox_contract_verified` 校验（默认 fail-closed）。
2. 平台能力探测：Windows 上缺资源限制手段 ⇒ deny 标志。
3. 逃逸/失效事件接入 RISK-REGISTER 上报（UNVERIFIED，不伪绿）。
4. 更新 UNIFIED-BLUEPRINT §5。

## 2. Compatibility / 兼容
纯收紧；无数据迁移。既有依赖无界沙箱的动作在限制手段落地前会 deny（预期内）。

## 3. Rollback / 回滚
revert 提交即回退到 best-effort（不更安全），无副作用。

## 4. Approval Gate
- [x] **auto-apply**（GOVERNANCE §4）：fail-closed 收紧，无 HC-01。

## 5. Sequencing
依赖 UBX-001（围栏内）。可并行 UBX-003。
