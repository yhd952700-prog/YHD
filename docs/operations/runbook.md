# LIUHAO X v3.0 运维 Runbook（Phase 21 · `MS:§199`-`MS:§213`）

> 状态：V3.0 收口版（2026-09-07）
> 用途：生产化操作手册，覆盖急停、回滚、DR、事件响应、安全态势自检。

---

## 1. 快速自检（安全态势电池）

任一发布前，运行加固套件验证 7 项安全不变量：

```bash
cd D:/LiuHao-AI-OS && .venv/Scripts/python.exe -m pytest tests/test_hardening.py -q
```

7 项检查（`src/ai/hardening.py` `HardeningSuite`）：

| # | 检查 | 不变量 |
|---|------|--------|
| 1 | sandbox_timeout | 代码执行被真实超时钳制，死循环被杀 |
| 2 | emergency_stop | 急停激活时 guard 拒绝受保护动作 |
| 3 | threat_detection | prompt injection / credential theft 被识别 |
| 4 | tool_lifecycle | 非 ACTIVE 工具不可执行 |
| 5 | world_authorize_deny | 被拒绝的外部写盘无副作用 |
| 6 | economy_overspend_rejected | 超预算预留被拒绝 |
| 7 | network_auth_enforced | 未注册外部 Agent 在 authenticate 步被拒 |

全绿（`all_passed=True`）才可继续发布。

---

## 2. 紧急停机（§82 Emergency Control）

```python
from src.ai.governance import get_emergency_control

ec = get_emergency_control()
ec.activate("incident-xxx: 检测到提权尝试")   # 立即冻结一切受保护动作
# ... 处置 ...
ec.deactivate()                                # 确认安全后恢复
```

- 激活期间 `guard(fn)` 返回 `{"blocked": True, "reason": ...}`，不执行 fn。
- 急停是**进程内**开关；跨进程需在网关/编排层入口调用 `is_active()` 短路。

---

## 3. 回滚（§80 Evolution rollback）

```python
from src.ai.evolution import EvolutionEngine
engine = EvolutionEngine()
engine.rollback(experiment_id)   # 状态 -> ROLLED_BACK，写入 history 审计
```

- 未评估 / 未批准的实验**无法 deploy**（`deploy()` 返回 False），这是防无界自修改的硬守卫。
- 回滚前先确认实验当前状态，避免对已 APPLIED 外的状态误操作。

---

## 4. 事件响应清单

1. **检测**：威胁检测（`ThreatDetector`）、安全链（`SecurityChain`）、审计日志（`audit_trail()`）。
2. **阻断**：高危威胁在 `SecurityChain.evaluate` 即 block，不咨询访问内核。
3. **取证**：读取 `SecurityChain.audit`、`AgentNetworkGateway.audit_trail()`、trust kernel `get_trust_events`。
4. **止损**：`EmergencyControl.activate()`。
5. **撤销**：`get_trust_manager().revoke(entity_id, reason=...)` 拉黑涉事 Agent。
6. **复盘**：`ExperienceEngine.extract/store` 沉淀经验到 Memory Kernel。

---

## 5. 灾难恢复（DR）要点

- **长程任务持久化**：`MissionStore`（Phase 13 ENOCH）把 Mission 落盘，进程重启后新实例读同一目录即恢复，不依赖单次 HTTP 生命周期。
- **关键状态单点**：审计（`audit_store.db`）、记忆（Memory Kernel）、能力注册表（`capability-registry.yaml`）应纳入备份窗口。
- **恢复验证**：重启后先跑 `tests/test_enoch.py::test_cross_instance_restart_recovery` 语义一致的自检。

---

## 6. 发布门（§117-118）

- DoD 七维：Implemented + Tested + Observable + Permissioned + **Policy Controlled** + Audited + Documented。
- Release 前 11 道门：Format / Lint / Type / Unit / Integration / Security / E2E / Performance / Agent Evaluation / Benchmark / Regression。
- **Critical Failure → Block Release**。任一加固检查失败视为 Critical。

### 6.1 内核层策略拦截开关（Policy C-4 / C-5 裁决）

内核层 43 个 `@kernel_action` 默认**只记录、不拦截**（L1）。把它们变成**真拦截**（L2）
不需要改代码 —— 用一处运维开关：

```bash
# 当前生产默认（已写入 docker-compose.prod.yml）：只武装两个 CRITICAL 动作
export LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL
# 逐动作精确开启
export LIUHAO_KERNEL_POLICY_ENFORCE=capability.retire
# 整体回滚为记录型（L1）
export LIUHAO_KERNEL_POLICY_ENFORCE=
```

- **当前生产状态（2026-09-12 Round 73 裁决）**：生产清单默认 **`CRITICAL`**。
  两个 CRITICAL 动作（`capability.retire` / `security.set_abac_rule`）在 `src/`
  **没有任何生产调用点** —— 该默认值因此是**零行为变更**的，但门已就位：
  任何后续（或受损）的调用路径都必须先取得经核验的人类审批。
- **HIGH 暂不开启**（仍为 L1）。原因：HIGH 动作中存在启动期/后台路径的候选
  （`plugin.*`、`capability.register`、`memory.auto_cleanup`、`event.clear_history`），
  开启前必须逐个审计调用点，否则会把正常流程变成「待审批」。需单独裁决。
- 开启后，HIGH/CRITICAL 动作无人工审批时抛 `PolicyDeferredError` → HTTP **409**
  （`policy_approval_required`，**待审批，不是永久拒绝**）；fail-closed 的硬拒
  （引擎不可达、判决未知）→ HTTP **403**（`policy_denied`）。
- 人工审批入口：`POST /v1/policy/approvals`（需 `Authorization: Bearer <JWT>`），
  列/查/撤：`GET/DELETE /v1/policy/approvals...`，开关快照：`GET /v1/policy/enforcement`。
- **回滚**：清空该环境变量并重启即可（无需改代码、无需发版）。
- ⚠️ 凭据有 TTL（默认 300s，上限 3600s）且可撤销；只接受 HIGH/CRITICAL 动作，
  拼错的名字 / LOW/MEDIUM 动作会**直接报错**（不会静默不生效）；启动日志会显式
  打印开关状态，配置错误按 ERROR 级别报出。
- ⚠️ **已知边界**：目前**没有**「用一个审批凭据去执行某个 CRITICAL 动作」的 HTTP
  端点（这是刻意的，见设计文档 §10.8.3 决策 1：反射式执行会新增攻击面）。执行
  必须在**同一进程**内于 `grant_window(grant)` 窗口中进行。运维侧若要执行
  `capability.retire`，当前途径是：临时把开关置空 → 执行 → 恢复开关。

---

## 7. 已知历史遗留（未在本收口处理，待后续）

- 全量 `pytest` 有 4 个既有 collection error：`src.security` 缺 `VaultTransitCrypto`/`RBACManager`（`test_crypto_audit.py`、`test_sec05_sec06.py`）、`tests/test_memory.py` 与 `tests/kernels/memory/test_memory.py` 重名、`tests/load/test_load_baseline.py`。定向回归是干净口径。
- 网络真实传输（A2A/MCP 出网）与真实 ML 感知（图像/视频/音频）诚实标 NOT_IMPLEMENTED，是下一迭代的扩展点。
