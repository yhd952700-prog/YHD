# 人类决定记录（Human Decision Record）—— 指针

> **为什么有这个文件**：权威的《人类决策包》存放于仓库之外
> （`D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\LIUHAO-Human-Decision-Packet-v0.1.md`）。
> 实测 **仓库内 `grep -rn "D17"` = 0 命中**，而决策包内 D17 命中 14 处。
> ⇒ **任何只在仓库内检索的 agent / CI / 复核者，都会得出结论「boss 没决定过 D17」。**
> 对一个以人类主权为核心的系统，「boss 已决定什么」在仓库内不可证是**地基级**问题。
> 本文件是**指针**，不是副本；权威版本始终是仓库外那一份。

| 项 | 值 |
|---|---|
| 权威文件 | `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\LIUHAO-Human-Decision-Packet-v0.1.md` |
| 版本 | v0.2（PARTIALLY DECIDED），D1–D18 |
| 决策归属 | **Human（所有权人 / boss）** |
| 记录时间 | 2026-09-21T10:41 local（2026-09-21T17:41:22Z UTC） |
| 字节数 | 185,022 |
| 行数 | 812 |
| **sha256** | `34d7883b3df7ae01208a807fd3604cff4216e0ca6904edeccc6de69a96d195b7` |

> ⚠️ 校验规则：**若上表 sha256 与权威文件不符，以权威文件为准，本指针须同步更新。**
> 本指针只解决「可发现性」，**不提供证明力**——证明力来自权威文件本身。

---

## 已作出的四条决定（摘要；完整十字段见权威文件）

### H-D6 · 接受 `human:<principal>` 作为身份形态
- **性质**：人类批准的**结构性决定**，不是工程自动决定。
- 受影响历史证据：审计库 295,282 行（采样时点量，持续增长）中含 `human:` 前缀的行数已过 246。
- ⚠️ `principal_id` 在哈希覆盖域内（`src/kernels/audit/__init__.py:101`、`:129`）⇒ 改存储形态会使哈希失效。
- ⚠️ **但主权判定 `is_human_identity`（`src/kernels/identity/__init__.py:268-288`）用的是 `metadata["kind"]`，不看前缀** ⇒ **第三条路：冻结存储 + 读时解释**，不必重写历史行。

### H-D16 · Option B：安全修复 与 安全姿态/开关 彻底分离
- 四步不得隐式合并：`Build Artifact → Verify Artifact → Deploy Artifact → Activate Security Posture`
- 产物可追溯链（2026-09-22 更新）：`source commit → build inputs → build digest → deployment digest → runtime identity`（build digest 与 deployment digest 须分别独立计算；runtime identity 指线上实例指纹）
- ⚠️ 实测：第五跳 `deployed digest` **在仓库内无载体**（`deploy/cloud/` 无 MANIFEST / BUILD_INFO / digest 文件）。

### H-D17 · Option C：按风险分档的审计强制（Risk-Graded Audit Enforcement）
- **审计后端失败 ≠ 一律放行，也 ≠ 一律阻断**；行为由动作的**风险类别**决定。
- **critical / sovereignty-sensitive action：无法产生权威审计证据时，动作不得继续。（硬性要求，无例外）**
- 明确禁止的悖论：`Decision = DENY / Evidence = missing / Action = continues`
- ⚠️ 同一形态在门禁侧也存在（`scripts/verify_ai_layer_audit.py:278` 无条件 `return 0`）。
- ✅ **2026-09-22 已裁决**：D17 的 C **同时约束门禁 / 验证脚本侧**——该脚本的探针异常须进入明确的 PASS / FAIL / DEGRADED / UNVERIFIED，**禁止 `probe failed → exit 0 → green`**（决策包 H-D17 已闭合原 "需要你明确" 的口子）。

### H-D18 · 维持 LEGAL REVIEW REQUIRED
- 唯一允许路径：`Fact Package → Counsel Review → Decision → Architecture Update`
- **不得把「pending legal review」写成「satisfied」。**
- 工程 / 商业战略不得决定 eIDAS 算法适用性、EU AI Act Annex III 分类、许可与分发义务。
- ⚠️ 实测：`docs/` 中 `LEGAL REVIEW REQUIRED` 0 命中；而 CI 已 `push: true` ⇒ **默认按「不需要许可」在推进**。

---

## 与代码库现行政策的冲突（必须知情）

**H-D17 与代码库现行政策正面冲突**，不是「补一个风险等级的小改动」：

```
src/kernels/_sovereignty.py:245   """...audit failure must not break the flow."""   ← 主权内核自己
src/gateway/auth.py:343           """...Audit failure must never break authentication."""
src/gateway/auth.py:361           except Exception as exc:  # noqa: BLE001 - defensive, and never fatal
src/kernels/_crosscutting.py:426  logger.warning("kernel_action audit failed for %r: %s", action, exc)

grep -rn "BLE001" src/ --include=*.py | wc -l  =  41
```

⇒ 「审计失败非致命」是**写进代码的既定政策**（41 处一致，且有人主动 `noqa` 让 linter 闭嘴）。
⇒ 落实 H-D17 需要**分两层**：
- **Layer 1（全等级）**：失败计数器 + 暴露（挂载点现成：`audit_stats()` + `/v1/ready`，后者子系统不健康时如实返回 503）。**不动任何 policy。**
- **Layer 2（仅 critical / sovereignty-sensitive）**：审计失败 ⇒ **阻断**（约 3 处：`_sovereignty.py:245`、`_crosscutting.py:426`、认证路径）。**H-D17 `:163` 已裁，无例外。**
- ⚠️ **只做 Layer 1 会交付一个「看得见但仍不合规」的系统**（`Evidence=missing / Action=continues / 且有一行计数` —— 禁句一个字没变）。
