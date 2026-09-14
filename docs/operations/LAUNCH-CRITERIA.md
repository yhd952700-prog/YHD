# LIUHAO X — 上线验收标准（LAUNCH CRITERIA）

> **权威范围**：本文件定义「上线」的可核实判据，给出**当前对照**与**前置清单**。
> **纪律**：任何一条判据都必须能在运行时实测；文档承诺不作为证据
> （仲裁优先级：**代码事实 > KERNEL-CANON > 其余**）。
> 维护者：Vigil（首席架构师 / AI 系统工程师 / 产品负责人）。首次成文 2026-09-13。

---

## 1. 「上线」在本项目里的定义

上线 **不等于**「14 个内核都有代码」——那只说明骨架完整。上线意味着：

> **一个真实自主任务能端到端跑完（真执行，非模拟），且系统对它做不到的事诚实。**

即：`Human Sovereignty Above All` + `More Capability ≠ More Authority` 不只是口号，
而是**运行时可观测的行为**。拆成 10 条可验证判据（§2）。

---

## 2. 验收维度（10 条）

| # | 维度 | 可核实判据（the bar） |
|---|---|---|
| **A1** | 真实执行 | 至少一条「自然语言目标 → 能力路由 → 真实执行 → 真值」的端到端链路。运行时探针必须返回**真实计算结果**，而非 `status:"simulated"`。 |
| **A2** | 诚实失败 | 不可执行 / 被安全策略拒绝 / 未接线，三种情况都必须 **loud fail**（`success=False` + 可操作原因）；任何路径都不得把「没做成」报成「做成」。 |
| **A3** | 能力状态诚实 | 能力注册表与运行时代码一致；已知空转项列入 `known-open-items`。 |
| **A4** | 人类主权护栏 | C-1..C-7 接线；C-2 默认 OFF 且默认值被 AST 护栏锁死；生产 arm 前身份表已挂（否则 fail-closed）。 |
| **A5** | 可观测 | 审计哈希链可 `verify_integrity`；事件总线带 `correlation_id`；关键动作有 trace。 |
| **A6** | 可复现部署 | 至少一条路径能在干净环境从零跑到可访问（云发布包 / Docker / 本机单端口）。 |
| **A7** | 驾驶舱可用 | 外链可访问；展示的是真实遥测而非占位。 |
| **A8** | 测试与门禁 | CI 全绿（含 Architecture Gate **真拦截**，非 `skipped`）；本地五重验证（compileall / flake8 / AST / importlib / runtime）。 |
| **A9** | 文档与手册 | 运行手册与**真实单端口架构**一致（不是泛化的 Docker/K8s 模板）。 |
| **A10** | 供应链/依赖 | 新依赖四方同步（pyproject / requirements / oss-registry / lock）；无未声明依赖。 |

---

## 3. 当前对照（2026-09-13 实测）

| # | 维度 | 状态 | 证据（运行时） |
|---|---|---|---|
| A1 | 真实执行 | ✅ **通过（WS1，本轮）** | 探针：目标 `"python: result = sum(i*i for i in range(1,11))"` → `status=executed`、`result='385'`。提交 `d992de46`。 |
| A2 | 诚实失败 | ✅ **通过（WS1，本轮）** | 探针三路径：真执行 385 / 注入 `import os` → `REJECTED: ImportError` / 未接线 → `no active tool for capability python_compute`。护栏 `tests/test_execution_tool_bridge.py::(h)`。 |
| A3 | 能力状态诚实 | ✅ **通过（WS2，本轮）** | `capability-registry.yaml` 新增 `local-capabilities`（python_compute）与 `known-open-items`（OPEN-001..006）；名册端点 20 项测试通过。 |
| A4 | 人类主权护栏 | ✅ **通过（WS3，本轮复验）** | 4 个验证脚本 ALL GREEN：C-1(43 动作平衡) / C-2(11，含 fail-closed 阻断) / C-3(15，DEFER+委托) / C-4(52，生产清单 arm 面 == 审计面 16=16)。默认 OFF 由 AST 护栏锁定（`no production @kernel_action has enforce=True`）。C-7 身份正向白名单测试通过。生产 arm 属**部署决定**（前置：身份表已挂）。 |
| A5 | 可观测 | ✅ 通过 | 审计哈希链 + 事件总线 + trace（既有实现与测试）。 |
| A6 | 可复现部署 | ✅ **通过（WS4，本轮）** | 重建发布包 227 文件（src 213 + config 6 + console 10 + 根文件）；本地冒烟：`/v1/ready`→200、`/v1/dashboard/roster`→200（`available=true`，14 内核 + 14 层 = 28 员工）、`/`→200。补 `RestrictedPython` 显式运行时依赖 + `capability-registry.yaml` 入包。 |
| A7 | 驾驶舱可用 | ✅ 通过 | 外链在线：`https://liuhao-cockpit-26430.app.workbuddy.host/`；发布包名册端点返回真实 28 条目（非占位）。模型仍为 mock（见 §5）。 |
| A8 | 测试与门禁 | ✅ 通过 | 历史 6 连全绿 + WS1 CI run（见 §6）。 |
| A9 | 文档与手册 | ✅ **通过（WS4，本轮）** | `production-runbook.md` 重写为 v2.0（真实单端口/SQLite；移除 Redis/Postgres/K8s 假设与占位联系方式）。 |
| A10 | 供应链/依赖 | ✅ 通过 | 本轮**零新依赖**。 |

图例：✅ 通过　🟡 部分 / 进行中　❌ 未达

---

## 4. 上线前置清单（must-do，按序）

1. [x] **WS3**：复验人类主权护栏 fail-closed —— ✅ 已完成（C-1..C-7 四脚本 ALL GREEN + 141 测试通过）。
2. [x] **WS4-a**：重建云发布包 —— ✅ 已完成（227 文件；`RestrictedPython` + `capability-registry.yaml` 已补入）。
3. [x] **WS4-b**：重写运行手册为真实单端口架构 —— ✅ 已完成（`production-runbook.md` v2.0）。
4. [x] **WS4-c**：复验驾驶舱在线且名册返回真实数据 —— ✅ 已完成（冒烟：ready/roster/console 全 200）。
5. [ ] **提交 + 推送 + CI 全绿**（Git Data API 链条脚本；逐个 SHA 校验 `MATCH`）。
6. [ ] （**生产**）挂载身份表 + 决策 C-2 是否 arm（arm 前必须确认身份表已挂）。

---

## 5. 诚实边界（上线**不**声称什么）

以下为已知限制，上线**不**改变它们；对外陈述时必须一并说明：

- **本地沙箱只提供能力隔离，无资源隔离**：禁 `import`/`open`/`eval`，CPU 靠子进程超时兜底，
  但**无内存上限**（`memory_limit_enforced=False`）。
- **LLM 默认 mock**：`AI_PROVIDER_TYPE=mock` 时不产生真实模型智能；真实 provider 需配置密钥。
- **WebSocket 适配器不投递**：无 WS 依赖时**诚实拒绝**，不伪造成功。
- **Vault 未安装**（`vault_connect`）：读密恒为空操作。
- **Context 内核默认空转**：HYBRID/UNIFORM 默认权重下典型输入全部 discarded。

> 完整机器可读清单：`capability-registry.yaml` → `known-open-items`。

---

## 6. 证据索引

- **提交**：`d992de46`（WS1）、`c123f977`（WS2+WS4），远端 tip 已对齐（逐个 SHA `MATCH`）。
- **CI**：run `34796348631`（`d992de46`）与 run `34797538140`（`c123f977`）**均 12/12 作业 `success`**
  （零 `skipped`、零 `failure`），run 结论 `success`（`gh run view <id> --json jobs` 直查）。
- **可复现验证脚本**：`scripts/verify_real_execution.py`（4 项 ALL GREEN：真执行 385 / 拒绝 unsafe /
  无工具诚实失败 / 显式 `success:False` 不被掩盖）。同族脚本：`verify_policy_c1.py`、
  `verify_c2_enforcement.py`、`verify_c3_sovereignty.py`、`verify_c4_approval_channel.py`。
- **测试**：`tests/test_local_tool_execution.py`（7）、`tests/test_execution_tool_bridge.py`（8，含诚实护栏）、
  `tests/kernels/`（663）、ai 层关键集（226，1 skipped）。
- **本地五重验证**：`compileall` OK；`flake8 src/` exit 0；`importlib` 208 模块 `FAILED=0`；AST/运行时见探针。

---

*本文件随每个工作流（WS1..WS4）推进而更新；每条状态的证据必须可复现。*
