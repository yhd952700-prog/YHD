# Verification-Plan — UBX-002-sandbox-fail-closed

## 1. Done Criteria
- [ ] 沙箱标 `required` 但合约不可证 ⇒ 动作 deny。
- [ ] Windows 无资源限制手段 ⇒ 对应动作 deny（U39 闭合）。
- [ ] 沙箱失效事件上报 UNVERIFIED。

## 2. How to Prove
- **测试：** 注入"沙箱合约探测失败" ⇒ 断言受管动作被 deny（非 best-effort 放行）。
- **Fail-closed：** 限制手段缺失 ⇒ deny，绝不 carry-on + 报成功。
- **可观测性：** 沙箱拒绝计数作为指标告警。

## 3. Negative Paths
- [ ] 合约校验自身失败 ⇒ fail-closed deny（非 fail-open）。
- [ ] 逃逸事件 ⇒ deny + 登记，不静默。

## 4. Evidence
- 提交 hash：`<回填>`／测试输出：`<回填>`
