# Verification-Plan — {{UBX-NNN-<slug>}}

> 验证的纪律（GOVERNANCE §2.2）：**VERIFIED 仅当被独立证明**。禁止"假装成功"。

## 1. Done Criteria（完成判据）
- [ ] {{可独立复现的判据 1}}
- [ ] {{判据 2}}

## 2. How to Prove（怎么证明）
- **测试：** `pytest {{path}}::{{test}}` 应 {{}}
- **Fail-closed 检查：** 当 {{不确定性场景}} 时，系统必须 {{deny / refuse / report UNVERIFIED}}，而非 carry-on + 报成功
- **可观测性：** 指标 `{{metric}}` 应在 {{阈值}} 触发告警
- **回归：** 运行 {{suite}} 确认无回归

## 3. Negative / Error Paths（必须测的错误路径）
- [ ] {{例如：围栏失效应 deny 而非 best-effort}}
- [ ] {{例如：密钥缺失应 refuse 而非 fallback}}

## 4. Evidence to Record（要留存的证据）
- 提交 hash：`{{commit}}`
- 测试输出（EXIT 0 摘要）：{{}}
