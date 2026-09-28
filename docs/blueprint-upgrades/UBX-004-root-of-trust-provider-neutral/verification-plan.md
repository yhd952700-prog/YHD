# Verification-Plan — UBX-004-root-of-trust-provider-neutral

## 1. Done Criteria
- [ ] `local` 封印强制带 `self_attested: true`，不声称第三方可信。
- [ ] 切换 `LIUHAO_TSA_PROVIDER` 能路由到对应 provider 骨架。
- [ ] 最终生产 provider 已触发 HUMAN DECISION 工单（未自动落地）。

## 2. How to Prove
- **测试：** 断言 local provider 封印对象含 `self_attested=true`；rfc3161 骨架在配置下被正确选中。
- **Fail-closed：** provider 不可达 ⇒ 封印 refuse，不伪称可信。
- **可观测性：** provider 来源分布指标。

## 3. Negative Paths
- [ ] 误把 local 当第三方采信 ⇒ 被标记拦截。
- [ ] 未配置 provider ⇒ 拒绝封印（不默认可信）。

## 4. Evidence
- 提交 hash：`<回填>`／测试输出：`<回填>`
