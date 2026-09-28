# Verification-Plan — UBX-005-distributed-real-coordination

## 1. Done Criteria
- [ ] 两进程争同一租约，仅一持锁；另一被拒。
- [ ] 持有者崩溃 ⇒ 租约超时释放，新 leader 的 fencing token 严格大于旧。
- [ ] 陈旧 leader 用旧 token 写入 ⇒ 被拒（R-G3-09）。

## 2. How to Prove
- **测试：** 双进程集成测试断言互斥 + token 单调；kill -9 模拟崩溃后接管。
- **Fail-closed：** 协调后端不可达 ⇒ 拒绝获取围栏（deny），不 best-effort 放行。
- **可观测性：** 租约持有/争用指标 + fencing token 当前值。

## 3. Negative Paths
- [ ] 网络分区 ⇒ 旧 leader 写入被拒（非双写）。
- [ ] 后端不可用 ⇒ 执行 deny（fail-closed）。

## 4. Evidence
- 提交 hash：`<回填>`／测试输出：`<回填>`
