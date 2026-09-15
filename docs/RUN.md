# 运行与使用文档（RUN / USAGE）

> 面向「新工程师 / 老板」：照着把 **后端网关 + 前端驾驶舱** 拉起来，并跑通冒烟测试。
> 本文件中的所有命令都已在 `D:\LiuHao-AI-OS` 上**实测跑通**；所有「门禁/端点」结论都来自读源码与真机请求，未凭记忆杜撰。
> 配套的入口清单见 [`docs/README.md`](README.md)；5 分钟快捷版见 [`docs/QUICKSTART-RUN.md`](QUICKSTART-RUN.md)（其登录/门禁部分以本文件为准）。

---

## 0. 先决条件（实测环境）

| 组件 | 版本 | 验证方式 |
|---|---|---|
| Python | 3.11 / 3.12（CI 矩阵双腿同值） | `.venv/Scripts/python.exe --version`（Windows）/ `python --version` |
| Node | 22.x | `node --version`（已见于 `C:\Users\...\node\versions\22.22.2-3`） |
| 后端虚拟环境 | `.venv/` 已存在 | 见仓库根 `.venv` |
| 前端依赖 | `apps/console/console/node_modules/` 已装 | 见 `apps/console/console/node_modules/vite/bin/vite.js` |

> ⚠️ **`npm` 在本沙箱被 WSL 黑名单拦截**。前端一律用 `node node_modules/vite/bin/vite.js ...` 直接拉起，**不要** `npm run dev`（会卡住）。

---

## 1. 必需的环境变量

网关是「有密钥才可用」的实例（fail-closed）。启动前必须准备：

| 变量 | 作用 | 必填 | 备注 |
|---|---|---|---|
| `LIUHAO_JWT_SECRET` | JWT 签名密钥（HS256） | **是** | 网关与令牌脚本**必须用同一把**。占位串（`replace-me`/`change-me`/`secret`/`password` 等）会被拒绝，等同没设 |
| `LIUHAO_HUMAN_IDENTITIES_FILE` | 人类身份登记表路径 | 是* | 默认 `config/human_identities.json`；文件不存在时内核**加载 0 个人类**（无人能审批/登录） |
| `LIUHAO_AUTH_SECRETS_FILE` | 登录凭据库（PBKDF2 哈希）路径 | 否† | 默认 `config/auth_secrets.json`；仅 `/v1/auth/login` 密码登录需要 |
| `LIUHAO_CONSOLE_DIST` | 构建产物目录 | 否 | 设了则网关同源托管驾驶舱（单端口模式）；开发期用独立 vite，可不设 |

\* 不设置也能启动，但 `/v1/auth/me`、`/v1/chat` 等会 401，且无人能审批。
† 仅当你走「密码登录」路径时才需要；走 `issue_console_token.py` 令牌路径不需要凭据库。

生成一把真密钥（**不要**用占位串）：

```bash
.venv/Scripts/python.exe -c "import secrets;print(secrets.token_urlsafe(48))"
```

完整变量清单见仓库根 [`../.env.example`](../.env.example)（已 git 跟踪；本机 `.env` 被 gitignore，按需自建）。

---

## 2. 启动后端（FastAPI 网关）

**入口**：`src.gateway.main:app`（`src/gateway/main.py` 中 `app = get_app()`，FastAPI 实例）。

```bash
cd D:\LiuHao-AI-OS

# 一次性导出（网关与后面第 4 步的令牌脚本必须同一把 LIUHAO_JWT_SECRET）
export LIUHAO_JWT_SECRET="<上一步生成的密钥>"
export LIUHAO_HUMAN_IDENTITIES_FILE="config/human_identities.json"
export LIUHAO_AUTH_SECRETS_FILE="config/auth_secrets.json"

.venv/Scripts/python.exe -m uvicorn src.gateway.main:app --host 127.0.0.1 --port 8080
```

> 已实测：网关在 `127.0.0.1:8080` 监听；`/v1/health`、`/v1/ready`、`/docs`、`/openapi.json` 均返回 **200**。
> `/v1/health` = 存活探针（恒 200）；`/v1/ready` = 真实依赖探针（api_key/jwt/rbac/encryption/rate_limiter 不健康时如实 503）。

后台长期运行可用 `&` 或 `run_in_background`；**勿**用 `0.0.0.0`（网关只应监听回环）。

---

## 3. 启动前端（console 驾驶舱 / Vite SPA）

```bash
cd D:\LiuHao-AI-OS\apps\console\console

# 绕过被拦的 npm，直接用 node 拉 vite
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
```

`apps/console/console/vite.config.ts` 已配置：`/v1` 代理到 `http://127.0.0.1:8080`（`changeOrigin: true`）。
浏览器打开 `http://127.0.0.1:5173` 即驾驶舱。

---

## 4. 登录 / 鉴权（两种真实可用路径）

网关闸门逻辑见 `src/gateway/policy.py`：`require_human_principal` 从请求头解析令牌，**优先** `X-Liuhao-Token: Bearer <jwt>`，**回退** `Authorization: Bearer <jwt>`。主体只来自令牌 `sub`，绝不从请求体读取（C-3 防越权）。

### 路径 A（推荐，无需密码凭据）：`issue_console_token.py` 签令牌

> 前提：boss 已作为「人类身份」登记（见下方一次性引导）。令牌用 `LIUHAO_JWT_SECRET` 签名，因此**签令牌的进程与网关进程必须同一把密钥**。

一次性引导（新克隆/尚无人类身份时）：

```bash
cd D:\LiuHao-AI-OS
export LIUHAO_JWT_SECRET="<与网关相同>"
export LIUHAO_HUMAN_IDENTITIES_FILE="config/human_identities.json"
export LIUHAO_AUTH_SECRETS_FILE="config/auth_secrets.json"

# 登记 boss 为人类身份（--password 顺便写一份登录凭据；令牌路径其实不需要凭据）
.venv/Scripts/python.exe scripts/register_human_identity.py \
  --principal boss --display-name "Boss" --password "<你自己设一个强口令，勿照抄本文档>"
```

签发表单审批令牌（控制台用）：

```bash
.venv/Scripts/python.exe scripts/issue_console_token.py --principal boss
```

输出里那段 JWT 即为令牌。前端/API 这样携带（已实测生效）：

```bash
TOKEN=$(.venv/Scripts/python.exe scripts/issue_console_token.py --principal boss 2>/dev/null \
        | grep -E '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$')

curl --noproxy '*' -H "X-Liuhao-Token: Bearer $TOKEN" http://127.0.0.1:8080/v1/auth/me
# => {"principal":"boss", ... "status":"active", "still_human":true, ...}
```

### 路径 B（密码登录）：`POST /v1/auth/login`

适合在登录页输密码的场景（CI 的 `console-readability` 作业即走此路）：

```bash
curl --noproxy '*' -X POST http://127.0.0.1:8080/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"principal":"boss","secret":"<与登记时用的同一口令>","client":"web"}'
# => {"access_token":"...","token_type":"bearer","expires_in":43200,...}
```

返回的 `access_token` 同样以 `X-Liuhao-Token: Bearer` 或 `Authorization: Bearer` 携带。

> 实测：无令牌访问受保护端点返回 **401**（`/v1/auth/me`、`/v1/chat`）；带令牌的 `/v1/chat` 返回 **200**。`/v1/auth/login` 在凭据未配置时直接不可用（fail-closed），见 `GET /v1/auth/config`。

---

## 5. 冒烟测试（照此跑通即验收）

```bash
# 1) 公开探针（无需令牌）
curl --noproxy '*' -o /dev/null -w "health:%{http_code}\n"  http://127.0.0.1:8080/v1/health
curl --noproxy '*' -o /dev/null -w "ready:%{http_code}\n"   http://127.0.0.1:8080/v1/ready
curl --noproxy '*' -o /dev/null -w "docs:%{http_code}\n"    http://127.0.0.1:8080/docs
curl --noproxy '*' -o /dev/null -w "openapi:%{http_code}\n" http://127.0.0.1:8080/openapi.json
# 期望四个都是 200

# 2) 受保护端点（需令牌，见第 4 步取 $TOKEN）
curl --noproxy '*' -H "X-Liuhao-Token: Bearer $TOKEN" http://127.0.0.1:8080/v1/auth/me
# 期望 principal=boss、status=active

# 3)（可选）端到端 MVP 冒烟脚本
.venv/Scripts/python.exe scripts/smoke_mvp.py
```

> `curl` 后的 `--noproxy '*'` **不能省**：本机 env 有代理，不加会把 localhost 也送代理，报 `upstream connect failed`——那不是服务挂了。

前端冒烟：浏览器开 `http://127.0.0.1:5173`，登录页加载即代表静态资源与 `/v1` 代理就绪；用路径 A/B 拿到的令牌在「审批中心」面板粘贴（仅存 sessionStorage，关页即弃）。

---

## 6. 四个真 CI 门禁（提交前必过）

见 [`../.github/workflows/ci.yml`](../.github/workflows/ci.yml)。以下**四条**是**真阻断**（命中即红、不带 `|| true`）；其余 Trivy/pip-audit/lint 全量 Bandit 均为采集态。

### 门禁 1 — `architecture-gate`（Semgrep，静态架构）
- 作业：`architecture-gate`（ci.yml 约 L422–L459）
- 规则集：`.semgrep/liuhao.yml`（已 git 跟踪）
- 命令：`semgrep --config .semgrep/liuhao.yml --metrics=off --disable-version-check --error --sarif --output semgrep-results.sarif src/ scripts/`
- 关键：`--error` 保证命中即 `exit 1`。规则集在代码树上实测 0 命中并被作业持续钉住。
- ⚠️ 许可证边界：Semgrep 是 LGPL-2.1，只在 CI 进程内以独立工具调用（mere aggregation），**不**写进 `pyproject`/`requirements`。

### 门禁 2 — `security` 作业里的 Bandit（仅 HIGH，阻断）
- 作业：`security`（ci.yml 约 L59–L129）
- 命令：`python -m bandit -r src/ --severity-level high -f json -o bandit-results.json`（ci.yml 约 L91–L92）
- `--severity-level high` = 命中即失败，**绝不 `|| true`**。MEDIUM/LOW 只在 `lint` 作业全量采集不阻断。
- 唯一例外：`src/ai/world_interface.py:117` 的 B602 用单行 `nosec` 抑制（shell=True 显式 opt-in、默认关闭、经授权契约保护）。
- ⚠️ 文档只指向 ci.yml 与 `.semgrep/liuhao.yml` 等 git 跟踪文件；**勿**指向 `bandit-report.json` / `bandit-results.json`（均为 gitignore / CI artifact，本地不入库）。

### 门禁 3 — `guardrails` 作业里的 OSS 字段契约校验器
- 作业：`guardrails`（ci.yml 约 L166–L251）
- 命令：`python oss-ecosystem/pipeline/validate_entries.py`（ci.yml 约 L228–L229）
- 校验 `oss-ecosystem/capabilities/*.yaml` 的字段契约（schema 第 21 行）。与 `tests/test_oss_ecosystem_entries.py` 双保险：测试额外反证「注入违规必须变红」。
- 同作业还跑 12 个 `scripts/verify_*.py`（Policy C-1..C-6、ORM/迁移、跨会话持久化、孤儿确认、许可证漂移等），每个退出非 0 即红。

### 门禁 4 — `Run Tests` 作业里的 flake8（代码质量）
- 作业：`Run Tests`（ci.yml:11，矩阵 3.11 / 3.12 双腿）的 `Verify Code Quality` 步骤（ci.yml L44–L46）
- 命令：`python -m flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`（exit 1 即失败，**不带 `|| true`**）
- 2026-09-15 起转为真门禁：此前它**已连红 3 次** CI run，而本机 `pytest` 全绿，所以长时间没人发现 ⇒
  **本机绿 ≠ CI 绿**，推完必须 `gh run list --branch <branch> --limit 3` 看一眼。
- 另有 `console-readability` 作业（ci.yml L273–L274）跑同参数 flake8，覆盖面仅 `scripts/ops/` 两个文件，同样无 `|| true`。

> 提交前可本地预演：`python -m flake8 src/ --max-line-length=100 --select=E,F,W --ignore=E501,W503`、`python -m bandit -r src/ --severity-level high` 与 `python oss-ecosystem/pipeline/validate_entries.py`；Semgrep 需 `pip install semgrep` 后 `semgrep --config .semgrep/liuhao.yml --error src/ scripts/`。

---

## 7. 常见故障

| 现象 | 原因与处理 |
|---|---|
| `upstream connect failed` | curl 走了代理，加 `--noproxy '*'` |
| 启动报「未找到 node」 | Node 没装/不在 PATH（见第 0 节路径） |
| `npm` 卡住 | 被 WSL 黑名单拦，改用 `node node_modules/vite/bin/vite.js`（第 3 节） |
| `LIUHAO_JWT_SECRET=replace-me` 警告 | 占位串被当没设；换一把真密钥并设置给**网关与令牌脚本同一把** |
| 令牌带上去仍 401 | 网关与 `issue_console_token.py` 的 `LIUHAO_JWT_SECRET` 不一致（每进程各生成一把即无法互验） |
| 登录返回 403 `不是可登录的人类身份` | 该 principal 未登记为 `metadata.kind=human` 且 `ACTIVE`：先 `register_human_identity.py --principal <name>` |
| `/v1/auth/config` 显示 `login_eligible_humans:0` | 身份已登记但没写凭据；若走令牌路径无需凭据，只需身份是 human 且 ACTIVE |
| `GET /v1/policy/*` 返回 401 | 刻意设计，需审批令牌：见第 4 步 |
| 审批中心无可用主体 | 未登记人类身份（C-7 正向白名单）：`scripts/register_human_identity.py --principal <name>` |

---

## 8. 停止

- 网关：启动终端 `Ctrl+C`（或 `kill <pid>`）。
- 前端：vite 终端 `Ctrl+C`。
- 单端口模式（`LIUHAO_CONSOLE_DIST` + 构建产物）下，关网关即同时关掉同源驾驶舱。
