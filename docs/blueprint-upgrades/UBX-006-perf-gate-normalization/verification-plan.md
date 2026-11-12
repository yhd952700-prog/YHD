# Verification-Plan — UBX-006-perf-gate-normalization

## 1. Done Criteria
- [ ] 集中 gate 三件套存在且可配置。
- [ ] 容量跌破 ⇒ R-G2-01 deny（非静默）。
- [ ] event_id 升宽后无新 12-char id（U49 闭合）。
- [ ] 链校验在快照路径运行，不阻塞实时写入（U50）。

## 2. How to Prove
- **测试：** 注入容量跌破 ⇒ 断言受管动作 deny；注入越界 event_id ⇒ 断言拒绝/告警。
- **Fail-closed：** 链校验超预算 ⇒ 报 UNVERIFIED，不伪绿（R-G2-07）。
- **可观测性：** 容量/延迟/校验预算三套指标 + 越界告警。

## 3. Negative Paths
- [ ] 静默丢弃证据（U49）⇒ 被 gate 拦截。
- [ ] 校验越预算仍标通过 ⇒ 必须 UNVERIFIED。

## 4. Evidence
- 提交 hash：`<回填>`／测试输出：`<回填>`
