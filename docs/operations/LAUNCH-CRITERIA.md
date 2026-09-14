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
而是**运行时可观测的行为**。拆成 11 条可验证判据（§2）。

---

## 2. 验收维度（11 条）

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
| **A11** | 访问控制 | 控制台**登录才能用**：业务端点（chat / dashboard / roster / profile / knowledge）无令牌一律 401；只有 `/v1/health`、`/v1/ready`、`/v1/auth/*` 与登录页公开；且至少存在 1 个**可登录的人类**，否则 fail-closed 变成"谁都进不去"。 |

---

## 3. 当前对照（2026-09-13 实测）

| # | 维度 | 状态 | 证据（运行时） |
|---|---|---|---|
| A1 | 真实执行 | ✅ **通过（WS1，本轮）** | 探针：目标 `"python: result = sum(i*i for i in range(1,11))"` → `status=executed`、`result='385'`。提交 `d992de46`。 |
| A2 | 诚实失败 | ✅ **通过（WS1，本轮）** | 探针三路径：真执行 385 / 注入 `import os` → `REJECTED: ImportError` / 未接线 → `no active tool for capability python_compute`。护栏 `tests/test_execution_tool_bridge.py::(h)`。 |
| A3 | 能力状态诚实 | ✅ **通过（WS2，本轮）** | `capability-registry.yaml` 新增 `local-capabilities`（python_compute）与 `known-open-items`（OPEN-001..006）；名册端点 20 项测试通过。 |
| A4 | 人类主权护栏 | ✅ **通过（WS3 复验 + P2 裁决）** | 4 个验证脚本 ALL GREEN：C-1(43 动作平衡) / C-2(11，含 fail-closed 阻断) / C-3(15，DEFER+委托) / C-4(52，生产清单 arm 面 == 审计面 16=16)。源码默认 OFF 仍由 AST 护栏锁死（`no production @kernel_action has enforce=True`）。**P2 裁决：发布包 serve.py `setdefault LIUHAO_KERNEL_POLICY_ENFORCE=HIGH,CRITICAL`**（与生产 compose 同值）—— 前提是身份表已挂（见 A11），实测线上 `enabled=true / 16 个动作`；回滚 = 导出空值重启。 |
| A5 | 可观测 | ✅ 通过 | 审计哈希链 + 事件总线 + trace（既有实现与测试）。 |
| A6 | 可复现部署 | ✅ **通过（WS4 + P0 重建）** | 发布包 229 文件（src 213 + config 7 + console 10 + 根文件）；`RestrictedPython`、`capability-registry.yaml`、**`config/human_identities.json`、`config/auth_secrets.json`** 全部入包。整包冒烟：无令牌 `roster`→401、`/v1/health`→200、登录→令牌、带令牌 roster/summary/profile/enforcement 全 200。 |
| A7 | 驾驶舱可用 | ✅ **通过（Round 88 重新发布）** | **当前外链：`https://liuhao-cockpit-26430.app.workbuddy.host/`**（appId `wbapp_Ca767ODcz2AiVn7eutyxnq`，`verified:true`）。实测线上 `/v1/health` 200、无令牌 `roster` 401、`auth/config` 报 `secret_store.configured=true` 且 `login_eligible_humans=1`、SPA 标题 `<title>LiuHao AI OS · 鎏灏智能中枢</title>`。⚠️ **旧链接 `liuhao-cockpit-84759`（appId `wbapp_AAy6Aj792OFtebl532XN9S`，旧内容）仍在线**：`unpublish` 被平台 403 挡住（`ListArtifactReleases: 10085:permission denied`），需手动在「设置—数据管理—应用」下线。 |
| A8 | 测试与门禁 | ✅ 通过 | WS1–WS5 四个提交均 12/12 `success`（见 §6）；本轮本地：顶层 994 passed / importlib 208 modules FAILED=0。 |
| A9 | 文档与手册 | ✅ **通过（WS4）** | `production-runbook.md` 重写为 v2.0（真实单端口/SQLite）。 |
| A10 | 供应链/依赖 | ✅ 通过 | 本轮**零新依赖**（Ollama 走 `requests`，已在包内）。 |
| A11 | 访问控制 | ✅ **通过（P0 + Round 88 令牌头）** | 业务路由在 `include_router(dependencies=[Depends(require_human_principal)])` 上挂闸门；护栏 `tests/test_gateway_auth.py::TestProtectedRouters`（6 项）全绿；实测无令牌 401 / 登录后 200。已挂初始人类 `boss`（`login_eligible_humans=1`）。**Round 88 补**：托管边缘网关会改写 `Authorization` ⇒ 控制台改用私有头 `X-Liuhao-Token`，后端优先读它、`Authorization` 兜底；护栏 `tests/test_gateway_token_header.py`（20 项）。 |

图例：✅ 通过　🟡 部分 / 进行中　❌ 未达

---

## 4. 上线前置清单（must-do，按序）

1. [x] **WS3**：复验人类主权护栏 fail-closed —— ✅ 已完成（C-1..C-7 四脚本 ALL GREEN + 141 测试通过）。
2. [x] **WS4-a**：重建云发布包 —— ✅ 已完成（227 文件；`RestrictedPython` + `capability-registry.yaml` 已补入）。
3. [x] **WS4-b**：重写运行手册为真实单端口架构 —— ✅ 已完成（`production-runbook.md` v2.0）。
4. [x] **WS4-c**：复验驾驶舱在线且名册返回真实数据 —— ✅ 已完成（冒烟：ready/roster/console 全 200）。
5. [x] **提交 + 推送 + CI 全绿** —— ✅ 已完成（`d992de46`/`c123f977`/`85163ce4`/`5a211d2a` 四次均 12/12 `success`；远端 tip = `5a211d2a`）。
6. [x] **WS5**：线上发布新版 —— ✅ 已完成。原 appId 发布环境不可复用 ⇒ 新建应用上线：
   `https://liuhao-cockpit-84759.app.workbuddy.host/`（线上四端点全 200、名册真实）。
7. [x] **P0**：控制台「登录才能用」—— ✅ 已完成。业务路由统一挂 `require_human_principal`
   （`src/gateway/main.py` 的 `include_router(dependencies=[...])`）；注册初始主权人类 `boss`；
   新增护栏 `TestProtectedRouters`（6 项）。附带修复：`register_human_identity.py` 的
   `--file` 默认值此前只存在于帮助文本，导致裸命令报 `could not write to the store (file @ )`。
8. [x] **P2**：生产治理裁决 —— ✅ 已完成（见 A4）。身份表 + 凭据随包发布；发布包
   `serve.py` 默认 arm `LIUHAO_KERNEL_POLICY_ENFORCE=HIGH,CRITICAL`，回滚 = 导出空值重启。
9. [x] **P1-b**：线上接真实 LLM —— ✅ **已完成（Round 88，boss 提供云端 key）**。
   构建期把 `AI_PROVIDER_TYPE=openai` / `AI_PROVIDER_MODEL=gpt-5.6-sol` /
   `OPENAI_BASE_URL=https://jiefuai.vip/v1` / `AI_PROVIDER_KEY`（打码入日志）烘焙进 `serve.py`。
   **整包冒烟（temp 副本）**：真登录 → `POST /v1/chat` → 真模型答 `2187`（3⁷，13.8s，非 mock）。
   ⚠️ **未端到端自证**：线上"已认证对话"需要 boss 口令登录（口令未留档；且 JWT 密钥每进程随机，
   无法自铸令牌）。发布沙箱有公网（`pip install` 成功）是可得的旁证。
10. [x] **Round 88**：令牌头修复收口 + 前端真接线 —— ✅ 已完成（`f70f7ea5`，8 文件；CI success）。
    ⚠️ 关键发现：上一轮只加了前端 `tokenHeaders()` 而**零调用点** ⇒ 生产问题原样存在；本轮把私有头
    接进 `api.ts`/`chatClient.ts`/`policyClient.ts`/`auth.ts`(logout+me) 并补 20 项回归。
11. [x] **Round 88**：重建发布包 + 重新发布 —— ✅ 已完成（229 文件；新外链见 A7）。

---

## 5. 诚实边界（上线**不**声称什么）

以下为已知限制，上线**不**改变它们；对外陈述时必须一并说明：

- **本地沙箱只提供能力隔离，无资源隔离**：禁 `import`/`open`/`eval`，CPU 靠子进程超时兜底，
  但**无内存上限**（`memory_limit_enforced=False`）。
- **LLM 分两种部署，别混为一谈**：
  - **本机**：`.env` 已配 `AI_PROVIDER_TYPE=ollama` + `qwen2.5:3b`，是**真模型**。
    实测：`1+1等于几` → 4.4s 真回答；`帮我算 1..1000 平方和` → 1 次 `python_compute` 调用
    → 真值 `333833500` → 散文收口。`qwen2.5:7b` 在 CPU 上 30s 读超时，不可用。
  - **线上发布包（Round 88 起）**：**已接入真云模型** —— 构建期把 `AI_PROVIDER_TYPE=openai` /
    `gpt-5.6-sol` / `OPENAI_BASE_URL=https://jiefuai.vip/v1` / key 烘焙进 `serve.py`
    （key 只进 gitignore 的 `deploy/cloud/`，不入库）。本地整包走 HTTP 真答 `2187` 已验证。
    ⚠️ 唯一未证的一环：**发布沙箱能否够到该端点**（需线上登录发一句话确认）。在确认前，
    对外不宜声称"线上已是真 AI"，可说「已配置真云模型，待一次线上对话确认」。
- **WebSocket 适配器不投递**：无 WS 依赖时**诚实拒绝**，不伪造成功。
- **Vault 未安装**（`vault_connect`）：读密恒为空操作。
- **Context 内核默认空转**：HYBRID/UNIFORM 默认权重下典型输入全部 discarded。
- **公开面**只有 `/v1/health`、`/v1/ready`、`/v1/auth/*` 与登录页；其余端点均需令牌。
  `auth_required` 恒为 `true`（fail-closed）——**没有凭据就没人能登录**，这是设计而非故障。

> 完整机器可读清单：`capability-registry.yaml` → `known-open-items`。

---

## 6. 证据索引

- **提交链**（远端 tip，逐个 SHA `MATCH`）：`f70f7ea5`(Round 88 令牌头收口) → `63f16723`(线上真模型
  provider + 前端聊天补鉴权) → `665054b8` → `8684f1bf`(P0 闸门+P2) → `fd08591e` → `5a211d2a` →
  `85163ce4`(CI 修复) → `2a9920d4`(verify 脚本) → `c123f977`(WS2+WS4) → `d992de46`(WS1)。
- **CI**：`f70f7ea5`(run `34832993125`) **`success`**；`63f16723`(run `34827500694`) **`success`**；
  `d992de46`(run `34796348631`) 与 `c123f977`(run `34797538140`) 均 **12/12 `success`**。
  ⚠️ 插曲：`2a9920d4` 曾红 —— 新增 `scripts/verify_real_execution.py` 却未接进任何 workflow，
  被 `tests/test_guardrail_scripts.py` 元护栏判红（Run Tests 双版本失败、下游 skipped）；
  `85163ce4` 把它加进 `ci.yml` 的 `guardrails` 作业后修复。
- **可复现验证脚本**：`scripts/verify_real_execution.py`（4 项 ALL GREEN：真执行 385 / 拒绝 unsafe /
  无工具诚实失败 / 显式 `success:False` 不被掩盖）。同族脚本：`verify_policy_c1.py`、
  `verify_c2_enforcement.py`、`verify_c3_sovereignty.py`、`verify_c4_approval_channel.py`。
- **测试**：`tests/test_local_tool_execution.py`（7）、`tests/test_execution_tool_bridge.py`（8，含诚实护栏）、
  `tests/kernels/`（663）、ai 层关键集（226，1 skipped）；
  Round 88 新增 `tests/test_gateway_token_header.py`（20，私有头契约）。
  关联集（gateway/policy 七个文件）140 passed。
- **本地五重验证**：`compileall` OK；`flake8 src/` exit 0；`importlib` 208 模块 `FAILED=0`；AST/运行时见探针。

---

*本文件随每个工作流推进而更新；每条状态的证据必须可复现。最近一轮：Round 88（令牌头收口 + 线上真云模型 + 重新发布）。*
