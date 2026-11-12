# Verification-Plan — UBX-003-secrets-fail-closed

## 1. Done Criteria
- [ ] secret 存储不可达 ⇒ 动作 refuse（非 fallback）。
- [ ] token 吊销在重启后对所有 worker 仍生效（U45 闭合）。
- [ ] 审计 secret redact 规则不可被绕过。

## 2. How to Prove
- **测试：** 注入 secret 读取异常 ⇒ 断言 deny；吊销后重启 + 多 worker 验证仍拒权。
- **Fail-closed：** 解密失败 ⇒ refuse，绝不 carry-on + 报成功。
- **可观测性：** 密钥拒绝计数指标。

## 3. Negative Paths
- [ ] 持久层写入吊销失败 ⇒ deny 续权（不静默放行）。
- [ ] 残留缓存 secret ⇒ 拒绝读取（防 fallback）。

## 4. Evidence
- 提交 hash：`<回填>`／测试输出：`<回填>`
