# Migration-Path — UBX-003-secrets-fail-closed

## 1. Rollout Steps
1. secret 访问封装为 fail-closed 入口；读取失败 ⇒ refuse。
2. token 吊销服务改为持久 + 跨 worker 失效（U45）。
3. 固化审计 redact 规则为硬约束。
4. 更新 UNIFIED-BLUEPRINT §3 C14。

## 2. Compatibility
纯收紧；吊销从内存升级为持久，向后不兼容"重启即失效"旧行为（即修复）。

## 3. Rollback
revert 回退到内存级吊销（不更安全），无数据迁移。

## 4. Approval Gate
- [x] **auto-apply**（GOVERNANCE §4）：fail-closed 收紧。

## 5. Sequencing
依赖 UBX-001（围栏内）。可并行 UBX-002。
