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

### 6.1 内核层策略拦截开关（Policy C-4 / C-5 / C-6 裁决）

内核层 43 个 `@kernel_action` 默认**只记录、不拦截**（L1）。把它们变成**真拦截**（L2）
不需要改代码 —— 用一处运维开关：

```bash
# 当前生产默认（已写入 docker-compose.prod.yml）：武装全部 16 个 HIGH/CRITICAL 动作
export LIUHAO_KERNEL_POLICY_ENFORCE=HIGH,CRITICAL
# 逐动作精确开启
export LIUHAO_KERNEL_POLICY_ENFORCE=capability.retire
# 整体回滚为记录型（L1）
export LIUHAO_KERNEL_POLICY_ENFORCE=
```

- **当前生产状态（2026-09-12 Round 75 / C-6 裁决）**：生产清单默认
  **`HIGH,CRITICAL`** —— 16 个动作，即全部 HIGH/CRITICAL **减去审计化豁免**。
  逐动作实测（10 条内核引导热路径 + 应用层套件 + 全仓 AST 调用点扫描）确认这 16 个
  在 `src/` **没有活的生产调用点**，因此该默认值是**零行为变更**的；门就位后，
  任何后续（或受损）的调用路径都必须先取得经核验的人类审批。
- **唯一豁免：`capability.register`**（仍为 L1）。原因是它**有活的启动调用点**：
  `get_capability_registry()` 懒加载时经 `_register_builtin_capabilities()` 注册
  12 个内置内核能力。武装它会让 `PolicyDeferredError` 从注册表引导里逃出，
  使所有能力内核消费方失败。豁免理由与复访条件记录在
  `src/kernels/_enforcement.py` 的 `EXEMPT_ACTIONS`；显式点名该动作会**直接报错**。
- ⚠️ **武装面的操作含义**：若后续给某个已武装动作新增生产调用点，CI 的
  `verify_armed_actions_are_inert.py` 会**立刻变红**（它逐个动作单独特武装并跑生产
  热路径）。此时二选一：把该调用点改为经人工审批，或把该动作加入 `EXEMPT_ACTIONS`
  并写清理由。**不要**为了让它变绿而清空开关。
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
- ⚠️ **签发凭据 ≠ 放行**（Round 76 实测）：武装后即便经 `/v1/policy/approvals` 签发了
  凭据，**HTTP 重试同一请求仍会得到 409** —— 凭据是审计化授权记录，解除拦截需要
  `grant_window`。409 的 `detail` 已改为如实说明这一点。

### 6.1.1 驾驶舱审批中心（Round 76）

`apps/console/console` 的「审批中心」（侧栏 → 审批中心）已接线到上述端点，是**唯一**
面向操作者的人工授权界面。使用步骤：

1. **登记人类（一次）**。自 Policy C-7（Round 77）起，判据是**正向白名单**：
   只有 `metadata.kind == "human"` 的主体能持有主权，内置 `system` 已被拒。
   身份内核是**内存态**，所以登记必须落到种子文件才能活过重启：
   ```bash
   python scripts/register_human_identity.py --list
   python scripts/register_human_identity.py --principal xin.hongda --display-name "辛宏达"
   ```
   写入 `config/human_identities.json`（已 gitignore，含真实姓名属运维数据；
   样例见 `config/human_identities.example.json`），并在服务环境设置：
   ```bash
   LIUHAO_HUMAN_IDENTITIES_FILE=/app/config/human_identities.json
   ```
   **未设置 = 零个已登记人类**（fail-closed，审批通道没有可用主体）。
   脚本写完会**启动一个全新内核验证闭环**，验证失败则非零退出。
2. 签发令牌（**本机**执行，网关没有也不应有 `/v1/auth/login`）：
   ```bash
   python scripts/issue_console_token.py --list                 # 看当前哪些人可以批
   python scripts/issue_console_token.py --principal <id>       # 打印令牌
   ```
   令牌的信任锚是**本机文件系统访问**——能跑这个脚本的人本就能读 JWT 签名密钥，
   所以登录表单只增加仪式感，不增加安全。
3. 打开驾驶舱 →「审批中心」→ 粘贴令牌 → 保存（只存 sessionStorage，关标签页即失效）。
4. 面板显示真实拦截态势（含豁免项）、有效凭据、以及「记录授权 / 撤销」。

- ⚠️ **未认证时不显示任何编造状态**：快照会暴露**未受管**的动作集合，因此
  `GET /v1/policy/enforcement` 也要求令牌；未认证时侧栏如实显示 `UNVERIFIED`。
- ⚠️ **面板按钮是「记录授权」，不是「放行」**——理由见上面的已知边界。
- ✅ **C-7 已修复（Round 77）**：内置 `system` 不再被当作「已核验人类」
  （`is_human_identity` = ACTIVE **且** `kind == "human"`），签发凭据会被内核以 400 拒绝。
  若令牌主体是内置机器身份，面板会明确提示「无法持有主权」。详见设计文档 §10.14。

### 6.2 安全护栏已接入 CI（Round 74；Round 76 增补前端构建）

安全不变量此前只靠「人记得跑脚本」维持。自 Round 74 起 `scripts/verify_*.py`
全部作为硬门禁跑在 CI 里（`ci.yml` 的 `guardrails` job）。

| 护栏 | 守住的不变量 |
|---|---|
| `verify_policy_c1.py` | 白名单驱动判决；allow / deny 集合不重叠且无陈旧项 |
| `verify_d8_risk_classification.py` | 43 个内核动作的风险分级注册表与装饰器接线一致 |
| `verify_c2_enforcement.py` | **无任何生产调用点**自行把 `enforce` 翻为 `True` |
| `verify_c3_sovereignty.py` | DEFER 语义；主权通道未被生产代码开启 |
| `verify_c4_approval_channel.py` | 审批主体只来自 JWT；`ApprovalRequest` 不含 `principal`；武装面 == 审计许可面 |
| `verify_armed_actions_are_inert.py` | 每个被武装的动作都**实测**不可从生产路径到达（逐动作单独特武装 + 捕获 `POLICY ENFORCED` 日志）|
| `verify_ai_layer_audit.py` | AI 层审计下沉的对照探针自校验（探针失效即失败） |
| `verify_orm_vs_db.py` | ORM 模型与 alembic 迁移后的 schema 无列级分歧 |
| `verify_persistence.py` | 跨会话（重连）持久化可读回 |
| `verify_metrics_persist.py` | 指标样本真实落库（自有工作流，不在 `ci.yml` 内） |
| `npm run build`（`build` job） | 驾驶舱前端**类型检查 + 构建**通过（Round 76 接入；此前前端在 CI 中零覆盖） |

- **元护栏**：`tests/test_guardrail_scripts.py` 断言每个 `verify_*.py` 都有
  `sys.path` 引导、都存在能产生非零退出的路径、且都被某个 workflow 调用。
  因此「新增一个永远通过的假护栏」在本仓库已不可能 —— CI 会直接红。
- **手工复跑**：`.venv/Scripts/python.exe scripts/verify_c4_approval_channel.py`；
  惰性护栏：`.venv/Scripts/python.exe scripts/verify_armed_actions_are_inert.py`
  （约 3 分钟：广度部分跑一次应用层套件，逐动作部分 16 次子进程探测）。
  两个 DB 护栏需先建库：`DATABASE_URL=sqlite:///./scratch.db python -m alembic upgrade head`。

---

## 7. 已知历史遗留（未在本收口处理，待后续）

- 全量 `pytest` 有 4 个既有 collection error：`src.security` 缺 `VaultTransitCrypto`/`RBACManager`（`test_crypto_audit.py`、`test_sec05_sec06.py`）、`tests/test_memory.py` 与 `tests/kernels/memory/test_memory.py` 重名、`tests/load/test_load_baseline.py`。定向回归是干净口径。
- 网络真实传输（A2A/MCP 出网）与真实 ML 感知（图像/视频/音频）诚实标 NOT_IMPLEMENTED，是下一迭代的扩展点。
