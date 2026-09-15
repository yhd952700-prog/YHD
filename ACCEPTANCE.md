# 鎏灏 可用性验收报告（ACCEPTANCE.md）

- 验收时间：2026-09-15（会话内实测）
- 验收人：可用性验收组（general-purpose-3）
- 目标：判定运行中的鎏灏「什么能用 / 什么不能用」，以认证其**可部署测试**状态。
- 探针环境：`.venv/Scripts/python.exe`；所有本地 HTTP 调用均显式绕过代理
  （`NO_PROXY=127.0.0.1,localhost` 或 `curl --noproxy '*'`），否则 localhost 会被环境代理拦截。

> 诚实性声明：本次验收**没有用任何 mock 冒充真实答复**。未能打通的「经网关鉴权后 chat 真实回复」路径，如实记为**阻塞缺口**，并给出可在各层证明真实性的替代证据。

---

## 1. 钉死环境与存活状态

| 组件 | 端口 | 后台任务 | 实测状态 |
|------|------|----------|----------|
| 网关 `uvicorn src.gateway.main:app` | :8080 | 1Fxq5U | ✅ 存活，`/v1/health`=200、`/v1/ready`=200、`/openapi.json`=200 |
| 前端 Vite dev | :5173 | 43ypLj | ✅ 存活，首页 200，`/v1` 代理到后端 200 |
| Ollama（qwen2.5:3b） | :11434 | （非本组任务，系统服务） | ⚠️ 验收中途一度宕机（端口无监听），已重启恢复并验证真实生成 |

---

## 2. 可用项（PASS）

1. **网关存活探针 `/v1/health`** → 200，返回真实结构
   `{"status":"ok","service":"liuhao-gateway","timestamp":...,"version":"1.0.0"}`。
2. **网关就绪探针 `/v1/ready`** → 200，且是**真实依赖检查**（非恒 `ready` 假探针）：
   `api_key_manager / jwt_handler / rbac_manager / encryption_manager / rate_limiter` 五项全部 `healthy`。
3. **前端加载与代理正确**：
   - `GET :5173/` → 200；
   - `GET :5173/v1/health` → 200（代理转发到 :8080 正常）；
   - `GET :5173/v1/ready` → 200（代理转发到 :8080 正常）。
4. **鉴权门是活的**（对受保护路由生效）：
   - `POST /v1/chat` 无令牌 → **401** `missing bearer token`；
   - `POST /v1/chat` 带伪造令牌 → **401** `invalid bearer token: Not enough segments`；
   - `POST /v1/auth/login {principal:boss,secret:x}` → **401** `凭据无效`（端点可达，凭据不可伪造）。
5. **模型后端真实可用（非 mock）**：直连 Ollama `qwen2.5:3b` 生成，返回真实推理文本
   `我是一个阿里云开发的预训练模型，今天是星期五。`（`eval_count=16`）。
   当前部署配置的 provider 即本地 Ollama（`AI_PROVIDER_TYPE=ollama`，见 `.env`）。
6. **路由表真实挂载**：`/openapi.json` 返回 23 条路由，含 `/v1/chat`、`/v1/chat/stream`、
   `/v1/kernels`、`/v1/profile`、`/v1/dashboard/*`、`/v1/policy/*`、`/v1/auth/*` ——
   证明对话/内核/画像/驾驶舱/审批等路由均已挂载。

---

## 3. 坏项 / 异常项（BROKEN / ANOMALY）

1. **当前工作树无法导入 AI 对话链路**
   `src/knowledge/rag_pipeline.py` 为**本地未提交改动**（129 增 / 132 删），
   其顶部 `from src.knowledge.embedding import EmbeddingService`，
   但 `embedding.py` 当前只导出 `EmbeddingPipeline` / `embed_text` → `ImportError`。
   后果：若按**当前源码**重启网关，`/v1/chat` 及所有依赖 `liuhao` 的路由将**挂载失败**。
2. **当前工作树无法导入安全包**
   `import src.security` 触发 `src/security/__init__.py` → `vault_crypto` →
   `src.integrations.vault.client` → `import src.integrations.models`，
   而 `src/integrations/models.py` **不存在**（仅有 `cloud_models.py` / `orm_models.py` / `storage.py` / `vault/`）→ `ModuleNotFoundError`。
   后果：当前进程内无法签发/校验令牌（影响 `issue_console_token.py` 等工具）。
3. **运行态与源码不一致（关键）**
   运行中的网关（1Fxq5U）挂载了全部路由且能对 `/v1/chat` 做真实鉴权，
   说明它跑在**上述两处断链改动之前的快照**上；当前工作树已偏离该快照。
   这意味着「现在能用的网关」与「磁盘上的代码」不是同一份 —— 重启会破坏当前可用状态。

---

## 4. 阻塞可部署测试的缺口（GAP）

1. **身份存储为空 → 无人可登录**
   - 运行网关的身份内核存储 `data/human_identities.sqlite3` **不存在**，
     且未设置 `LIUHAO_HUMAN_IDENTITIES_FILE` / `LIUHAO_HUMAN_IDENTITIES_BACKEND`。
   - `scripts/issue_console_token.py --list` 实测报告：`No identity may currently approve: no registered human exists.`
   - 尽管 `config/human_identities.json` 里有 `boss`，但该文件**未被身份内核读取**（内核默认后端是 `file` 且路径未指向它）。
   - 影响：`/v1/auth/login` 对任意主体都失败（密码错→401；即便密码对，内核 store 无该人类→403）。
     所有受保护路由（chat / dashboard / profile / kernels / policy）在本次验收中**无法用真实身份打通**。
2. **无法签发网关可接受的令牌**
   - 网关 JWT 签名密钥是 **per-process 随机值**：`.env` 的 `JWT_SECRET=***` 是占位符（被 `jwt_handler` 拒绝），
     且本次会话**未把 `LIUHAO_JWT_SECRET` 注入本 shell**，故本会话无法 mint 出网关会验签通过的令牌。
   - 实测：本进程 self-mint 的令牌被网关以 `invalid bearer token` 拒收（密钥不匹配）。
   - 影响：「经网关发 chat 拿到真实回复」这条路**本次会话走不通** —— 这是配置/密钥缺口，不是 mock 能补的。
3. **Ollama 中途宕机**
   - 验收期间 `:11434` 一度无监听进程（首次探测 200，数分钟后变 000，tasklist 无 ollama 进程）。
   - 已重启恢复并验证真实生成，但该依赖**不稳定**，是部署测试的前置风险（模型后端掉线则 chat 全挂）。

---

## 5. 端到端实测小结（诚实）

- **真·端到端（网关鉴权后 chat 真实回复）**：❌ 未达成（受 §4 身份/密钥缺口限制）。**未用 mock 冒充。**
- **替代真实证据（各层均验证为真实，非 mock）**：
  - 模型层：Ollama `qwen2.5:3b` 直连生成 → 真实文本（§2.5）。
  - 网关层：路由真实挂载 + 鉴权门真实生效（§2.4 / §2.6）。
  - 前端层：加载 + `/v1` 代理真实转发（§2.3）。
- **路径说明**：部署配置的 provider 即本地 Ollama `qwen2.5:3b`（`.env` 未启用上游 `gpt-5.6-sol @ jiefuai.vip`，无 `AI_PROVIDER_KEY`）。
  因此「真实路径」就是本地 Ollama；代码层支持上游抖动时回退 Ollama，但本次未触达该分支（上游未启用，本地即主路径）。

---

## 6. 环境坑（PITFALLS）

1. **HTTP 代理破坏 localhost**：环境代理会拦截/破坏对 `127.0.0.1` 的调用。
   所有本地探测必须 `NO_PROXY=127.0.0.1,localhost` 或 `curl --noproxy '*'` 或直连 socket，
   否则 `:11434`/`:8080` 会返回 `000`/异常。这也是 Ollama 首次 `000` 的排查入口。
2. **令牌头是 `X-Liuhao-Token`**：受保护路由认 `X-Liuhao-Token: Bearer <jwt>`（**不是** `Authorization`，
   因为托管前置网关会改写 `Authorization`）。实测 `Authorization` 仍被作为兜底接受，但本机/直连必须用 `X-Liuhao-Token`。
3. **本地需要真实 JWT 签名密钥**：`.env` 的 `JWT_SECRET=***` 是占位符，会被应用拒绝并 per-process 随机生成，
   导致多 worker / 重启即全端登出且互不可验。必须设 `LIUHAO_JWT_SECRET`（或 `JWT_SECRET`）为真实随机串。
   本次网关密钥未与本 shell 共享，故无法签发令牌。
4. **Ollama 启动依赖 Windows 原生环境**：在 Git Bash 下直接 `ollama serve` 会 `panic: %userprofile% is not defined`；
   必须在 PowerShell / 原生 Windows env（或显式 `USERPROFILE`）下启动。且 Ollama 进程非持久（本次中途消失），
   建议作为持久服务或容器托管。
5. **源码 / 运行态不一致**：当前工作树有破坏导入的未提交改动（§3），重启网关即崩。
   验收基于「当前运行快照」，但**部署前必须先把工作树修回可导入状态**。

---

## 7. 结论与建议

**可部署测试就绪度：部分就绪（Partial）。**

- ✅ 已验证可用：公开面（health / ready / openapi / 前端代理）、模型后端真实推理、鉴权门生效。
- ❌ 阻塞项（不解决则受保护功能无法在部署测试中走通）：
  1. 身份存储未配置 → 无人类可登录（`LIUHAO_HUMAN_IDENTITIES_FILE` 未指向 `config/human_identities.json`，且 store 文件缺失）；
  2. 网关 JWT 密钥未与本环境共享 → 无法签发令牌（`.env` 占位符 + 本 shell 无 `LIUHAO_JWT_SECRET`）；
  3. 当前源码有未提交破坏导入改动（`rag_pipeline.py` → `EmbeddingService`；`src.integrations.models` 缺失）→ 重启即崩；
  4. Ollama 依赖不稳定（中途宕机）。

**建议（部署测试前必做）**：
- 设置 `LIUHAO_HUMAN_IDENTITIES_FILE=config/human_identities.json`（已含 `boss`），并为 `boss` 设置/确认 `config/auth_secrets.json` 凭据；
- 固化 `LIUHAO_JWT_SECRET` 为真实随机串（写 `.env`，勿用 `***`/`replace-me`）；
- 修复 §3 两处断链：回退或修正 `rag_pipeline.py` 的 `EmbeddingService` 引用、补回 `src/integrations/models`；
- 将 Ollama 设为持久服务（或容器化），并在验收脚本中带 `NO_PROXY`/重试。
