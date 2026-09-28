# Verification-Plan — UBX-002-sandbox-fail-closed

## 1. Done Criteria
- [x] 沙箱执行不可信 / AI 生成代码前，若没有任何"真正的"隔离后端（gVisor / Docker / Monty / RestrictedPython），且态势已武装 + 风险 HIGH/CRITICAL ⇒ 动作 deny（fail-closed），绝不落到宿主 subprocess。
- [x] Windows / 无资源限制手段的主机：同上，HIGH/CRITICAL 动作 deny（U39 闭合）。
- [x] 默认态势为降级继续（warn）而非阻断，避免无 gVisor 的本地开发被误伤；是否 fail-closed 由 `LIUHAO_SANDBOX_ENFORCE=on` 部署决策控制。

## 2. How to Prove
- **测试（确定性，无需真沙箱）：** `tests/plugins/sandbox/test_sandbox_assurance.py`
  - `test_assure_fail_closed_when_armed_high_critical`：无真正后端 + 武装 + HIGH/CRITICAL ⇒ 抛 `SandboxIsolationUnavailable`（deny）。
  - `test_assure_degraded_continue_when_not_armed` / `test_assure_low_medium_still_degraded_when_armed`：未武装或 LOW/MEDIUM ⇒ 降级继续（warn，不 deny）。
  - `test_assure_allows_when_genuine_backend_present`：有真正后端 ⇒ 放行。
  - `test_manager_execute_untrusted_fail_closed_when_armed`：经 `SandboxBackendManager.execute(requires_untrusted_code=True, enforce_isolation=True, risk_tier="HIGH")` 集成路径 ⇒ deny。
  - `test_manager_execute_default_preserves_behaviour`：默认 `requires_untrusted_code=False` ⇒ 行为不变（历史调用点不受影响）。
- **Fail-closed：** `genuine_isolation_available()` 为假 + 武装 + HIGH/CRITICAL ⇒ deny，绝不放行 + 报成功。
- **可观测性：** 降级继续路径以 `SANDBOX DEGRADED-CONTINUE` 形式 `logger.warning` 暴露（见 `test_assure_emits_warning_when_degraded`）。

## 3. Negative Paths
- [x] `requires_untrusted_code=False`（默认）⇒ 隔离校验整体跳过，不引入回归。
- [x] LOW/MEDIUM 即使武装也仅降级继续（与审计证据闸门分级一致）。

## 4. Evidence
- 实现：`src/plugins/sandbox/assurance.py`（NEW）+ `src/plugins/sandbox/backends/manager.py` 的 `execute()` 新增 `requires_untrusted_code` / `risk_tier` / `enforce_isolation` 可选参数（默认不触发，向后兼容）。
- 测试：`tests/plugins/sandbox/test_sandbox_assurance.py`（11 cases）。
- 提交 hash：`<回填本轮 commit>`／测试输出：`11 passed`（assurance 套件）。
- 关联 UBX-001（执行围栏内运行）与 UBX-005（跨节点协调）。

## 5. 人类决策点（GOVERNANCE）
- 默认态势为 **warn（降级继续）** 是有意选择：在无 gVisor 的 Windows / 本地开发机上，若默认 fail-closed 会阻断所有沙箱执行，属破坏性。
- 是否在生产清单里置 `LIUHAO_SANDBOX_ENFORCE=on` 是**部署决策**（HD 级，非 HC-01 级人类主权事件），由运维 / 治理在确认生产节点具备真实隔离后端后开启。
