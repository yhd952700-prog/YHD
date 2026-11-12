# 本机攻击面只读审计：监听面与浏览器侧安全属性

- 审计对象：`d:\LiuHao-AI-OS`（源码）+ 本机已安装副本 `%LOCALAPPDATA%\LiuHao`
- 审计日期：2026-11-08
- 审计方式：**只读**。未修改任何代码/配置；未在生产数据目录做写入实验；未启动任何新服务（因此未用到端口 19006 与 `D:\_audit\attack-surface`）；仅做 GET 探测、ACL 读取、SQLite 只读元数据查询。
- 判定口径：`必须修` / `不修也能接受（理由）`

---

## 0. 结论（先看这里）

**一句话：今天这台机器上没有任何已保存的模型 Key，所以“现在就能被读走”的 Key 不存在；但一旦用户保存了 Key，存在一条已成立的“持令牌即可把明文 Key 外带”的路径（P0），以及一条本机非管理员组可读密钥文件的 ACL 路径（P1）。外部网页（任意站点）读不到 Key。**

四条访问路径的判定：

| 访问者 | 能否读到用户 Key | 依据 |
|---|---|---|
| 互联网/任意站点网页 | **不能** | CORS 为精确来源白名单（非 `*`）、HostGuard 校验 Host 与写请求 Origin、无 Cookie、令牌只在同源 localStorage；Vite 文件响应不带 `Access-Control-Allow-Origin` |
| 本机其他 Windows 用户 | **标准配置下不能；本机当前配置下能** | 已安装副本的 `model_config.key` / `app.env` / `auth_secrets.json` 继承了一个非管理员组 `CodexSandboxUsers:(RX)`（环境注入），见 P1-1 |
| 本机同用户任意进程 | **能（且无法用应用层阻止）** | 同用户进程天然可读用户 profile 下的密钥文件、SQLite、日志；这是 Windows 账户边界，不是应用缺陷 |
| 持有 `model:manage` 权限的令牌者 | **能拿到明文 Key（潜在）** | 改 `base_url` + 触发“测试连接”会把已保存的 Key 发往新地址，见 P0-1 |

当前事实（只读核对）：

- 仓库库 `data/model_registry.sqlite3`：模型 2 条，**带 Key 0 条**。
- 安装副本 `%LOCALAPPDATA%\LiuHao\data\data\model_registry.sqlite3`：模型 1 条，**带 Key 0 条**。
- 首次运行暂存目录 `.tmp/graphical-first-run/data/model_registry.sqlite3`：模型 1 条，**带 Key 0 条**。
- 安装版日志 3.0 MB、暂存版日志 8.7 KB：`sk-…` / `Bearer …` 形态命中 **0 行**。

---

## 1. 发现分级

> **修复状态（2026-11-08）**：P0-1、P1-1、P1-2 已修复，并各自有可重复探针；运行入口与覆盖矩阵见 [§5 回归探针](#5-回归探针可重复跑)。
>
> - P0-1 → `src/gateway/models.py`：`base_url` 变更且该模型已有 Key 时，必须重输 Key 或显式 `confirm_key_resend=true`，否则 `409 MODEL_KEY_TARGET_CHANGED`；`/v1/models/test`、`/v1/models/discover` 同样禁止把已存 Key 发往与保存时不同的 `scheme://host:port`。回环仍对所有 provider、任意端口放行（本地 LM Studio / vLLM 需要），但“同 host 换端口”同样算新目标。
> - P1-1 → `scripts/launch_installed.py`：`_ensure_app_env` 写完立即收紧；启动时对 `config/app.env`、`config/auth_secrets.json`、`data/model_config.key` 统一 `tighten_current_user_only` 并读回复核。
> - P1-2 → `apps/console/console/vite.config.ts`：`server.fs.allow` 收窄为 `apps/console/console` + `src/brand.json`。

### P0-1（严重）已保存模型 Key 可被“改 base_url + 测试连接”一键外带

- 位置：
  - 允许改 `base_url` 且**不要求重填 Key**：`src/gateway/models.py:539-559`；`src/ai/model_config.py:798-836`（`api_key` 取自 `payload.get("api_key")`，缺省即保留旧密文）
  - 测试接口解密已存 Key：`src/gateway/models.py:983-1004`；Key 回落逻辑 `src/gateway/models.py:945-950`
  - 真正发请求：`src/gateway/models.py:779-785`（`api_key = cfg.get("api_key")` → `provider.generate(...)`）
  - 回环地址对**所有 provider、任意端口**放行：`src/gateway/models.py:220-249`（`allow_loopback=True`）
- 利用条件（全部满足即可）：
  1. 攻击者持有任意带 `model:manage` 的令牌（例如被授予密钥管理权限的普通成员账号，或被窃取的管理员令牌）；
  2. `PUT /v1/models/{id}`，只改 `base_url` 为攻击者控制的公网地址，或本机 `http://127.0.0.1:<任意端口>`（后者对 openai/deepseek 等 provider 也放行）；
  3. `POST /v1/models/{id}/test`。服务端用 `_store().resolve()` 解密已保存 Key，`_probe()` 把它作为 `Authorization` 发给新 `base_url`。
- 现有防护为什么挡不住：CORS / HostGuard / 同用户校验保护的是“谁能发请求”，而这里攻击者用的是**合法令牌**；`_guard_probe_target`（`models.py:717-776`）只防跨域重定向与 DNS 重绑定，不防“目标就是攻击者自己的合法主机”。
- 影响：任何“能管模型”的角色事实上等于“能读全部已存密钥”，权限边界被打穿。
- 判定：**必须修（已修）**。落地方式：`base_url` 变更时要求重输 Key 或显式确认“把已保存的 Key 发送到新地址”，并把变更写入 `key.base_url_changed` 审计；`resolve()` 的 Key 只允许发往保存时的同一 `scheme://host:port`（含端口）；回环**保持放行**（本地推理需要），但换端口同样算新目标，走同一目标一致性校验。回归探针：§5 的 ①–④。

### P1-1（高）安装副本的密钥文件/环境文件继承了环境注入的非管理员读权限

- 位置（文件）：`%LOCALAPPDATA%\LiuHao\data\data\model_config.key`、`%LOCALAPPDATA%\LiuHao\data\config\app.env`、`%LOCALAPPDATA%\LiuHao\data\config\auth_secrets.json`、同目录 `model_registry.sqlite3`、`logs\liuhao.log`
- 相关代码：
  - 本应把 ACL 收紧为“仅当前用户”：`src/common/windows_acl.py:51-76`；Key 生成/读取路径确实调用它：`src/ai/model_config.py:317-324`、`346-362`
  - 但 `app.env` 的写入**没有任何收紧**：`scripts/launch_installed.py:374-401`（写入 `APP_ENV=production`、`LIUHAO_JWT_SECRET`、`SECRET_KEY`）
  - 旧数据迁移用 `shutil.move`（跨卷退化为复制，不保留受保护 DACL）：`scripts/launch_installed.py:256-274`
- 实测（只读）：
  - 仓库 `data/model_config.key`：DACL **受保护**（`AreAccessRulesProtected=True`），仅 `XNG-20260709YFZ\Administrator` —— 说明收紧逻辑本身有效。
  - 安装副本 `model_config.key` / `app.env` / `auth_secrets.json`：**全部 `inherited=True`、`AreAccessRulesProtected=False`**，ACE 为 `CodexSandboxUsers:(RX)` + `SYSTEM` + `Administrators` + `Administrator`。
- 利用条件：任何属于 `CodexSandboxUsers` 的本机非管理员进程可直接 `type` 出 `model_config.key` 与 `app.env`。该组来自本机父目录（`C:\Users\Administrator\AppData`）的注入式 ACE，不是 LiuHao 代码创建；但它说明**风险取决于 profile 目录 ACL**，而应用目前只有 Key 文件做了自我保护，`app.env` / `auth_secrets.json` 没有。
- 影响：拿到 `model_config.key` 即可离线解开 SQLite 里所有 `enc:v1:` 密文；拿到 `app.env` 的 `LIUHAO_JWT_SECRET` 即可在网关使用文件密钥时伪造令牌。
- 判定：**必须修（已修）**。`_ensure_app_env` 写完后调用 `tighten_current_user_only`，并读回复核；启动时对 `config/app.env`、`config/auth_secrets.json`、`data/model_config.key` 统一收紧，失败显式告警。回归探针：§5 的 ⑤。
- 补充：本次用 `.tmp/graphical-first-run/config/app.env` 里的密钥伪造 JWT 打活动网关，返回 **401**，说明活动实例用的是进程内临时密钥、并未采用该文件密钥；这降低了“立即伪造”的现实性，但不改变文件暴露本身的性质。

### P1-2（高）Vite dev server 以仓库根为 `fs.allow`，可被本机进程读走 `app.env` / `auth_secrets.json` / 数据库

- 位置：`apps/console/console/vite.config.ts:51-62`
  - `server.port: 5173`
  - `server.fs.allow: [fileURLToPath(new URL('../../..', import.meta.url))]` —— 即整个仓库根
  - `proxy['/v1'].target = http://127.0.0.1:8080`
- 实测（只读 GET，Vite 当前仍监听 `127.0.0.1:5173`）：

  | URL | 结果 |
  |---|---|
  | `/@fs/D:/LiuHao-AI-OS/.tmp/graphical-first-run/config/app.env` | **200**，282 B（含 `LIUHAO_JWT_SECRET`、`SECRET_KEY`） |
  | `/@fs/D:/LiuHao-AI-OS/.tmp/graphical-first-run/config/auth_secrets.json` | **200**，`application/json`，232 B |
  | `/@fs/D:/LiuHao-AI-OS/.tmp/graphical-first-run/config/human_identities.json` | **200**，`application/json`，1032 B |
  | `/@fs/D:/LiuHao-AI-OS/.tmp/graphical-first-run/data/model_registry.sqlite3` | **200**，4096 B |
  | `/@fs/D:/LiuHao-AI-OS/data/model_config.key` | **403**（Vite 默认 `fs.deny` 含 `*.key`） |

- 利用条件：本机任意进程可直接 GET；浏览器侧任意站点**不能**读取——实测带 `Origin: http://evil.example` 时响应无 `Access-Control-Allow-Origin`（仅 `Vary: Origin`）。但若恶意页面本身运行在 `localhost` 的其它端口（Vite 默认对 localhost 来源更宽松），则存在被读取的空间，需按“本机可信”对待。
- 影响：`app.env` 泄露 JWT 签名密钥；`auth_secrets.json` 泄露账号口令校验材料；SQLite 泄露模型/归属元数据（Key 为密文，但配合 P1-1 的主密钥即可解密）。
- 判定：**必须修（已修，开发期也）**。`fs.allow` 已收窄为 `apps/console/console` + `src/brand.json` 单文件；`.tmp/`、`data/`、`config/` 不再可被 dev server 读取。此项需另起 Vite，未并入 §5 脚本（手工回归仍是 §3.2 的 `@fs` 探测，应全部 403）。

### P2-1（中）`allow_credentials=true` 但系统根本不使用 Cookie

- 位置：`config/default.yaml:61`；`config/development.yaml:33`；`src/gateway/main.py:371`；中间件安装 `src/gateway/main.py:1403-1415`
- 事实：全仓库无 `Set-Cookie`/`HttpOnly`/`SameSite` 写入（仅 `src/common/redaction.py:177,364` 的日志脱敏规则提到 Cookie）。会话是 `X-Liuhao-Token` / `Authorization: Bearer`（`apps/console/console/src/lib/auth.ts:282-302`）。
- 利用条件：当前来源是精确白名单（`main.py:317-338`，生产还会剔除 localhost 来源），且生产禁止 `*`（`main.py:1396-1402`），因此**没有**“`*` + 凭据”组合。`allow_credentials` 在这里只增加预检/响应面，不带来功能收益。
- 判定：**不修也能接受**（现有组合不构成跨站读取）；建议后续改为 `false` 以减少无意义面。

### P2-2（中）恢复码被暂存在 localStorage

- 位置：`apps/console/console/src/lib/auth.ts:241-256`（`liuhao.setup.recovery`），清除逻辑 `:274-280`
- 利用条件：需要同源 XSS 或能执行同源脚本的扩展。本次全量搜索 `apps/console/console/src` 未发现 `dangerouslySetInnerHTML`、`eval(`、`new Function`、`document.write`、`innerHTML`（0 命中），因此没有现成注入点。
- 影响：恢复码等价于账号接管凭据，且恢复码是“只显示一次”的长期凭据。
- 判定：**不修也能接受（有条件）**：前提是保持“无 HTML 注入面 + 恢复码展示后立即清除”。若未来引入 Markdown→HTML 渲染，必须同步改为不落 localStorage。

### P2-3（中）缺少浏览器侧安全响应头

- 位置：`src/gateway/main.py:1403-1423`（仅 CORS + GZip），未见 CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy
- 利用条件：需要先有注入点（当前无）或点击劫持场景（本机单用户，收益低）。
- 判定：**不修也能接受**（本机单用户、无注入点）；对外部署时应补 CSP 与 `X-Frame-Options: DENY`。

### P2-4（中）开发模式首次注册不校验桌面端一次性证明

- 位置：`src/gateway/accounts.py:301-325`，放行分支 `:314-319`；同用户校验在非可信启动下直接返回 `:283-284`
- 利用条件：直接 `python -m src.gateway` 启动（未设 `LIUHAO_TRUSTED_LAUNCH`）且账号尚未初始化时，本机任意进程可抢先注册根账号。桌面启动会设置该变量（`src/desktop/service.py:399`），安装版走 `APP_ENV=production`（`scripts/launch_installed.py:389-397`）。
- 影响：抢注后获得管理权限，进而叠加 P0-1。
- 判定：**不修也能接受（仅限开发）**；安装版必须确保始终经桌面壳启动。若允许用户手工 `python -m src.gateway` 作为产品用法，则**必须修**。

### P3-1（低）非 `/v1` 的 `/metrics` 会落到 SPA 外壳

- 位置：`src/gateway/health.py:588` 的真实端点是 `/v1/metrics`；`src/gateway/console_static.py:102-116` 的保留前缀不含裸 `metrics`
- 实测：`GET /metrics` → 200 `text/html`（SPA），不是指标。
- 判定：**不修也能接受**（不泄露数据，仅误导排障）；可把 `metrics` 加入保留前缀。

### P3-2（低）公开的启动快照暴露组件阶段与耗时

- 位置：`src/gateway/startup.py:46-76`（`/v1/startup`，无鉴权，`include_in_schema=False`）
- 实测：返回 `state/stages/background/degraded/startup_check`，不含密钥、路径或令牌。
- 判定：**不修也能接受**（信息价值低，便于本机排障）。

### P3-3（低）开发态文档面暴露

- 位置：`src/gateway/main.py:1336-1338`（`docs_url/redoc_url/openapi_url` 仅生产置 `None`）
- 实测：dev 网关 `/docs`、`/redoc`、`/openapi.json` 均 200。
- 判定：**不修也能接受（仅开发）**；安装版 `APP_ENV=production` 时自动关闭（`launch_installed.py:374-401`）。

### P3-4（低）Docker 形态显式绑定 `0.0.0.0`

- 位置：`Dockerfile:122`（`ENV HOST=0.0.0.0`）、`:123`（`EXPOSE 8080`）、`:113`（`USER app`）；默认本机启动仍为 `src/gateway/__main__.py:57` 的 `127.0.0.1`
- 判定：**不修也能接受**（容器必须如此）；`HostGuardMiddleware` 在 `0.0.0.0` 时会补入局域网 IPv4 与主机名（`main.py:210-250`、`:351`），仍需令牌才能访问业务接口。

### P3-5（低）安装器允许命令行覆盖提权

- 位置：`installer/liuhao.iss:54-55`（`PrivilegesRequired=lowest`，`PrivilegesRequiredOverridesAllowed=commandline`）
- 判定：**不修也能接受**（默认最低权限；覆盖是用户显式选择）。

---

## 2. 六个必答问题

### 2.1 后端实际监听地址与端口；是否存在因配置绑 `0.0.0.0` 的路径

- 默认：`127.0.0.1:8080`。`src/gateway/__main__.py:57`（`DEFAULT_HOST`）、`:58`（`DEFAULT_PORT`）、`:137`（`HOST` 环境变量）。
- 会绑 `0.0.0.0` 的**显式**路径只有三条：
  1. `HOST=0.0.0.0` 环境变量（`__main__.py:137`）；
  2. `python -m src.gateway --host 0.0.0.0`（`scripts/start_liuhao.py:607`、`WILDCARD_HOSTS` 定义 `:567`）；
  3. 容器形态 `Dockerfile:122`。
- 没有任何“配置文件里默认写成 `0.0.0.0`”的路径；`config/default.yaml` 不含监听地址项。
- 桌面壳同样默认回环：`src/desktop/service.py:44-45`，并把 `--host/--port` 透传（`:385-387`）。
- 本次实测时点：网关已停止（8080 无监听）；Vite 仍监听 `127.0.0.1:5173`。审计过程中曾观测到 `127.0.0.1:8080`（网关）与 `127.0.0.1:5173`（Vite），均为回环。

### 2.2 CORS：允许哪些来源、是否 `*`、是否带凭据

- 来源：`config/default.yaml:45-47` → `http://localhost:3000`、`http://localhost:5173`；`config/development.yaml:29-32` 另有 `http://127.0.0.1:5173`；`config/production.yaml:30-31` 为显式生产域名（示例值）。
- 未配置时：**开发**回落到 `["http://localhost:3000","http://localhost:5173"]`（`main.py:96`、`:317-330`）；**生产**不回落到 localhost，并会剔除残留的 localhost 来源（`main.py:332-338`）。
- 是否 `*`：生产若出现 `*` 直接 `RuntimeError` 拒绝启动（`main.py:1396-1402`）。
- 是否带凭据：`allow_credentials` 默认 `true`（`config/default.yaml:61`、`main.py:371`），但**不使用 Cookie**，实际凭据是请求头令牌。
- 另有 HostGuard：Host 不在白名单 → 400（`main.py:488-495`）；写方法（POST/PUT/PATCH/DELETE，`main.py:432`）若带 Origin 且不是完整同源 → 403 `origin_not_allowed`（`main.py:500-513`）。
- 实测：恶意 Origin 的预检 400、GET 无 `ACAO`、写请求 403；`Origin: null` 被拒。

### 2.3 Cookie / 会话令牌：HttpOnly、SameSite、Secure

- **没有 Cookie**。全仓库无 `Set-Cookie` 写入（仅日志脱敏规则提及）。
- 会话令牌是 Bearer：私有头 `X-Liuhao-Token` 优先、`Authorization: Bearer` 兜底（`apps/console/console/src/lib/auth.ts:282-302`）。
- 存储：勾选“记住我”→ `localStorage['liuhao.session']`；否则 `sessionStorage['liuhao.session.tab']`（`auth.ts:73-76`、`:214-230`）。
- 因此 `HttpOnly` / `SameSite` / `Secure` 三项**不适用**；本机 http 场景下也没有“Secure 无法下发”的矛盾。代价是令牌对同源 JS 可读，安全边界从“HttpOnly 抗 XSS”换成“无注入点 + 短 TTL”。TTL 默认 12 小时（`src/gateway/auth.py:89`），可配（`:85`、`:445-452`）。
- 结论：对本机 http 单用户是合理取舍；**不建议**为了 HttpOnly 引入 Cookie，因为那会把 CSRF 面重新引进来（现有 Origin 守卫是为无 Cookie 设计的）。

### 2.4 静态资源与 API 是否同端口；是否暴露内部接口

- 生产/安装版：**同一端口**。网关用 404 兜底提供控制台构建产物，并保留 `v1/api/knowledge/docs/redoc/openapi.json` 前缀不回落（`src/gateway/console_static.py:102-116`）；静态资源缺失时返回真 404 而非外壳（`:122-138`）。
- 开发版：**两个端口**（Vite 5173 + 网关 8080，`vite.config.ts:51-62` 代理 `/v1`）。
- 内部接口暴露面：
  - `/docs`、`/redoc`、`/openapi.json`：仅非生产开启（`main.py:1336-1338`）。
  - `/v1/startup`：公开，仅阶段与耗时（`startup.py:46-76`）。
  - `/v1/auth/config`：公开是设计需要，只返回计数/布尔（`auth.py:804-840`）。
  - Vite `@fs`：**实际暴露了仓库根下的 `app.env` / `auth_secrets.json` / `human_identities.json` / SQLite**（见 P1-2），这是本次最需要处理的一处“不需要的路径”。

### 2.5 前端注入面与 Key 落盘/落日志

- `dangerouslySetInnerHTML` / `eval(` / `new Function` / `document.write` / `innerHTML`：在 `apps/console/console/src` **0 命中**。
- 用户输入拼 URL：统一使用 `encodeURIComponent`（如 `ModelManager.tsx:299,406,429,449`、`operator.ts:703`、`knowledge.ts:222`、`chatClient.ts:200-225`）。
- `postMessage` / `message` 监听：无。
- Key 是否进 localStorage：**否**。模型 Key 只存在于 `ModelManager` 的临时表单状态（`ModelManager.tsx:150-192`，注释明确“apiKey 只存在于这份临时状态；重置即清空”），列表只渲染后端 `api_key_masked`（`:793`）。
- localStorage 里唯一的敏感物是**恢复码**（`auth.ts:241-256`），不是模型 Key。
- Key 是否进日志：日志占用 3.0 MB + 8.7 KB，`sk-…` / `Bearer …` 形态 **0 行**；服务端错误文本经 `_sanitize` 脱敏后才进日志/响应（`models.py:696-701`、`:789-795`）。

### 2.6 桌面端：是否高权限、是否写启动项、能否被低权限劫持

- 高权限：无。未见 `runas` / `requireAdministrator` / `highestAvailable`；安装器为 `PrivilegesRequired=lowest`（`installer/liuhao.iss:54`）。审计会话本身是 Administrator，进程完整性级别高是“用户是管理员”，不是应用请求提权。
- 启动项：只写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`（`src/desktop/autostart.py:29`、`:127-141`），无 HKLM、无计划任务、无服务。实测 HKCU Run 与 HKLM Run 均**没有** LiuHao 条目（当前未启用自启）。
- 命令行引用：`subprocess.list2cmdline` 生成（`autostart.py:66-78`），不存在未加引号路径的经典劫持。
- 低权限劫持：安装目录 `D:\LiuHao` 对 `BUILTIN\Users` / `Authenticated Users` 仅 `ReadAndExecute`（实测，无 Write），低权限用户不能替换可执行文件。
- 结论：**不修也能接受**。残余风险是“同用户低完整性进程可改 HKCU Run 或替换用户可写路径下的入口”，这属于同用户等价，应用层无法消除。

---

## 3. 证据附录（只读）

### 3.1 监听

```
LISTEN 127.0.0.1:5173 pid=30440     （报告时点；8080 网关已停止）
审计过程中另观测到 127.0.0.1:8080（网关）、127.0.0.1:5173（Vite）
```

### 3.2 Vite `@fs` 探测（仅 GET，未打印文件内容）

```
.../.tmp/graphical-first-run/config/app.env                      -> 200  282 B
.../.tmp/graphical-first-run/config/auth_secrets.json            -> 200  application/json 232 B
.../.tmp/graphical-first-run/config/human_identities.json        -> 200  application/json 1032 B
.../.tmp/graphical-first-run/data/model_registry.sqlite3         -> 200  4096 B
.../data/model_config.key                                        -> 403
带 Origin: http://evil.example 时：无 Access-Control-Allow-Origin，仅 Vary: Origin
```

`.tmp/graphical-first-run/config/app.env` 的键名（只列键名，不列值）：`APP_ENV`、`LOG_LEVEL`、`LIUHAO_JWT_SECRET`、`SECRET_KEY`。

### 3.3 数据库（只读 URI，仅计数）

```
d:\LiuHao-AI-OS\data\model_registry.sqlite3                                total=2  with_key=0
%LOCALAPPDATA%\LiuHao\data\data\model_registry.sqlite3                     total=1  with_key=0
d:\LiuHao-AI-OS\.tmp\graphical-first-run\data\model_registry.sqlite3       total=1  with_key=0
```

### 3.4 ACL

```
仓库 data/model_config.key           protected=True   仅 Administrator        （收紧逻辑有效）
安装 data/data/model_config.key      protected=False  含 CodexSandboxUsers:RX （继承，未收紧）
安装 data/config/app.env             protected=False  含 CodexSandboxUsers:RX
安装 data/config/auth_secrets.json   protected=False  含 CodexSandboxUsers:RX
安装 data/logs/liuhao.log            protected=False  含 CodexSandboxUsers:RX
D:\LiuHao                            Users/Authenticated Users = ReadAndExecute（不可写）
```

### 3.5 桌面端与启动项

```
HKCU Run：无 LiuHao 条目
HKLM Run：无 LiuHao 条目
installer/liuhao.iss:54  PrivilegesRequired=lowest
src/desktop/autostart.py:29  RUN_KEY = Software\Microsoft\Windows\CurrentVersion\Run
```

### 3.6 网关侧探测（审计过程中，网关运行于 127.0.0.1:8080，dev）

```
恶意 Origin 预检                          -> 400
恶意 Origin GET                           -> 200 但无 Access-Control-Allow-Origin
允许来源 GET                              -> 200 且带 ACAO
恶意 Origin 的写请求                       -> 403 origin_not_allowed
伪 Host                                   -> 400
路径穿越                                  -> 返回 SPA index（1902 B），未读到密钥
/docs /redoc /openapi.json                -> 200（dev）
/v1/startup /v1/auth/config               -> 200，无密钥
/metrics                                  -> 200 text/html（SPA 兜底，非指标）
用 .tmp app.env 的密钥伪造 JWT            -> 401（活动实例用临时密钥，不认该文件密钥）
```

---

## 4. 局限

1. 只读审计：**没有**执行端到端密钥外带（P0-1 的依据是代码路径与参数校验，不是实机搬运），以免在真实数据目录产生副作用。
2. 当前三份数据库均无已保存 Key，因此 P0-1 是“一旦保存 Key 就成立”的潜在路径，而不是已发生的数据泄露。
3. 安装副本未处于运行状态；其 ACL 结论来自静态文件检查。
4. `CodexSandboxUsers` 是本机环境注入的组，不应视为产品自带；但它证明了“仅依赖父目录继承”不成立，P1-1 的修复建议按“显式收紧”给出。
5. 报告时点网关已停止，2.2/2.4/3.6 的网关响应证据来自审计过程中的回环探测记录；Vite 部分为报告时点复测。

---

## 5. 回归探针（可重复跑）

入口：`scripts/acceptance/local_attack_surface_e2e.py`

脚本不是“再手工验一遍”，而是把四条结论固化成断言：

- 自己起一台**隔离网关**（临时数据目录 + 随机回环端口，`APP_ENV=development`），不碰真实数据目录；
- 自己起若干**本地假上游**（回环随机端口），记录每次请求的 `method` / `path` / `Authorization`；
- 跑完全部回收（网关进程、临时目录、监听端口），不留后台服务；
- 每条输出 `PASS` / `FAIL` 与关键证据行；任一 FAIL → 退出码 **1**，全过 → **0**。

### 5.1 用法

在仓库根运行：

```powershell
# 默认安装副本路径：%LOCALAPPDATA%\LiuHao\data
.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py

# 安装副本不在默认位置（或 CI 环境无安装副本）时显式指定
.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py `
    --installed-data-dir "D:\somewhere\LiuHao\data"

# 打包前置门禁（安装副本可能尚未存在）：跳过 ⑤，其余全跑
.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py `
    --allow-missing-installed

# 负向对照：故意把“含密钥的包 / 只授 Users 的管道 / 残留自启与进程”喂给同一条断言，
# 并把 [UninstallRun] 的停进程/关自启两行注释掉，必须 FAIL（0/4、EXIT=1）
.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py --negative-control
```

退出码即结果，可直接接 CI：`if ($LASTEXITCODE -ne 0) { exit 1 }`。

**已接入收口流程（本机攻击面回归闸）**：

- 打包线 `scripts/release/package-frozen.ps1` 的「前置门禁 1.9」自动执行
  `--allow-missing-installed`；任何 FAIL 走 `Add-Check` 抛错，**阻塞出包**；
- 仓库根 `RELEASE-RUNBOOK.md` §5.7 为必跑项（`dist/release-gate/RELEASE-RUNBOOK.md`
  由根文件同步生成，不得手改）；装机后按 §5.7 去掉 `--allow-missing-installed`
  复跑，⑤ 必须真跑。

### 5.2 覆盖矩阵

| 编号 | 断言 | 对应发现 |
|---|---|---|
| ① 改地址外带 | 带 Key 模型可保存；改 `base_url` 不重输 Key → `409 MODEL_KEY_TARGET_CHANGED`；`POST /v1/models/test` 带 `model_id` + 新地址 → 同样 409；假攻击者服务器**零请求、零 `Authorization`**；被拒后保存地址未变 | P0-1 |
| ② 重输/确认 | 重输 Key 改地址 → 200；`confirm_key_resend=true` → 200；`confirm_key_resend` 不落库；确认后新目标测试通过且收到的是重输的 Key；审计库出现 `key.base_url_changed`，含 `key_reentered` / `stored_key_resent` 与前后 `target` | P0-1 |
| ③ 同 host 换端口 | 保存时 `http://127.0.0.1:A`，请求 `http://127.0.0.1:B`（仅端口不同）→ 保存与测试都 409；该端口零请求 | P0-1（端口必须算进目标） |
| ④ 回环多端口 | 两个不同的回环端口（LM Studio / vLLM 形态）都能保存、都能测试通过，且各自收到自己的 Key | P0-1 配套（回环不关死） |
| ⑤ 安装副本 ACL | `config/app.env`、`config/auth_secrets.json`、`data/model_config.key` 读回 DACL：`AreAccessRulesProtected=True`、仅当前用户 SID、不含 `Users` / `CodexSandboxUsers` | P1-1 |
| ⑥ 本机身份证明 | 真实命名管道 DACL：已去继承、只有当前用户 SID、无 `Users` / `Authenticated Users`；受限令牌（当前用户 SID 设为 deny-only，等价同机无授权主体）连真实管道 → `winerror=5`；**负向对照**：只授 `BUILTIN\Users` 的测试管道被判「非仅当前用户」，同一受限令牌能连上它（证明拒绝来自 DACL）；一次性 handoff token：别的进程取到后退出 → `403 ACCOUNT_RECOVERY_PROOF_REQUIRED`，交接文件未被消耗；属主重新取到 → `200` 并取回刚生成的恢复码；交接文件随即删除（取用即焚）；同一令牌重放 → `403`；令牌明文不落实例目录 | 本机证明管道 |
| ⑦ 备份不含密钥 | `scope=all` / `scope=self` 的 `POST /v1/accounts/backup/export`：密文包原文与**口令真解包**后的 payload 都不含 `model_config.key`、`integrity.key`、`auth_secrets.json`、`human_identities.json`、`model_registry.sqlite3` 与明文 API Key（`sk-…`）；`files[].path` 逐项复检；`models.key_material is None`；同时确认确实带模型 Key 密文（样本非空）且 `key_vault` 用备份口令可解出已知 Key（Key 是密封携带，不是整包为空）；**负向对照**：含上述文件名 + `sk-live-…` 的合成 payload 必须被扫描器全部命中 | 备份/打包带走 |
| ⑧ 卸载残留 | **安装器规格硬断言**：`installer/liuhao.iss` 的 `[UninstallRun]` 必须先 `-m src.desktop stop`（停托盘/网关）、再 `-m src.desktop autostart disable`（走产品命令，不手写注册表），`[Setup]` 必须 `CloseApplications=yes`，全文不得出现自启注册表删除；读不到任一条即 FAIL（不得降级为 INFO）。**行为断言**：在临时目录搭“安装完成”足迹（程序目录、数据目录、开始菜单/桌面快捷方式、HKCU 卸载键、自启项、托盘进程与监听端口），用受控注册表/目录操作模拟卸载：**默认保留数据**与**明确删除数据**两种选项下，程序目录、`data-dir.txt`、快捷方式、卸载键、自启项、托盘进程、端口全部清掉；默认选项下数据目录与内容完好保留；**负向对照**：故意留下自启项与托盘进程，同一残留检查器必须判红 | 卸载残留（用户真实会踩） |

P1-2（Vite `fs.allow`）不在本脚本内：它需要另起 dev server。手工回归仍是 §3.2 的 `@fs` 探测——脚本化时应断言 `app.env` / `auth_secrets.json` / 数据库全部 403，而 `src/brand.json` 与 `/` 仍为 200。

### 5.3 期望输出（本机实测，2026-11-08）

```text
[PASS] ①改地址外带 · 改地址不重输 Key 被拒绝: got=(409, 'MODEL_KEY_TARGET_CHANGED') ...
[PASS] ①改地址外带 · 测试连接跨目标被拒绝: got=(409, 'MODEL_KEY_TARGET_CHANGED') ...
[PASS] ①改地址外带 · 攻击者服务器没收到任何请求: got=[] expected=[]
[PASS] ②重输/确认 · 审计记录了已存 Key 外发确认: got=True expected=True
[PASS] ②重输/确认 · 审计含前后 target: got=('http://127.0.0.1:50177', 'http://127.0.0.1:50178') ...
[PASS] ③同host换端口 · 同 host 换端口测试被拒绝: got=(409, 'MODEL_KEY_TARGET_CHANGED') ...
[PASS] ④回环多端口 · 两个本地端口都被接受: got=2 expected=2
[证据] ACL config/app.env: protected=True aces=[XNG-20260709YFZ\Administrator(S-1-5-21-...-500)]
[PASS] ⑤安装副本ACL · config/app.env AreAccessRulesProtected: got=True expected=True

[PASS] ⑥本机身份证明 · 管道 DACL 已去继承: got=True expected=True
[PASS] ⑥本机身份证明 · 管道 DACL 只有当前用户 SID: got=['S-1-5-21-...-500'] expected=['S-1-5-21-...-500']
[PASS] ⑥本机身份证明 · 受限令牌连真实管道被拒绝: got=('denied', 5) expected=('denied', 5)
[PASS] ⑥本机身份证明 · 负向对照：仅授 Users 的管道被判 FAIL: got=False expected=False
[PASS] ⑥本机身份证明 · 负向对照：受限令牌能连上仅授 Users 的管道: got='connected' expected='connected'
[证据] helper 取令牌: child_pid=32144 bound_pid=32144（已确认退出）
[PASS] ⑥本机身份证明 · 别的进程取得的令牌 → 403: got=(403, 'ACCOUNT_RECOVERY_PROOF_REQUIRED') ...
[PASS] ⑥本机身份证明 · 换回属主令牌可用: got=200 expected=200
[PASS] ⑥本机身份证明 · 取用即焚（交接文件已删除）: got=False expected=False
[PASS] ⑥本机身份证明 · 令牌一次性（重放）→ 403: got=(403, 'ACCOUNT_RECOVERY_PROOF_REQUIRED') ...

[PASS] ⑦备份不含密钥 · 负向对照：扫描器认出全部密钥文件名: got=[] expected=[]
[PASS] ⑦备份不含密钥 · scope=all 真解包后不含禁用名/明文 Key: got=[] expected=[]
[PASS] ⑦备份不含密钥 · scope=all 主密钥不随包: got=None expected=None
[PASS] ⑦备份不含密钥 · scope=all Key 以口令密封携带: got=True expected=True

[INFO] 真实 HKCU 自启项：enabled=True value=LiuHaoAIOS
[INFO] 安装器规格审计：{'stop': True, 'autostart': True, 'close': True, 'no_manual_reg': True}
[PASS] ⑧卸载残留 · 安装器规格：卸载前先停托盘/网关（[UninstallRun]）: got=True expected=True
[PASS] ⑧卸载残留 · 安装器规格：卸载时走产品命令关自启（不手写注册表）: got=True expected=True
[PASS] ⑧卸载残留 · 安装器规格：CloseApplications=yes: got=True expected=True
[PASS] ⑧卸载残留 · 安装器规格：全文不手写自启注册表删除: got=True expected=True
[PASS] ⑧卸载残留 · 夹具：自启/快捷方式/托盘进程/端口均已就位: got=(True, True, True, True) expected=(True, True, True, True)
[PASS] ⑧卸载残留 · 默认卸载后无残留（目录/自启/快捷方式/卸载键/进程/端口）: got=[] expected=[]
[PASS] ⑧卸载残留 · 默认卸载后数据目录保留且内容完好: got=True expected=True
[PASS] ⑧卸载残留 · 明确删除数据后无残留且数据目录已删除: got=[] expected=[]
[PASS] ⑧卸载残留 · 负向对照：残留自启项被判红: got=True expected=True
[PASS] ⑧卸载残留 · 负向对照：残留托盘进程/端口被判红: got=True expected=True

结果：78/78 通过
```

负向对照（`--negative-control`）实测必须红，退出码 1：

```text
[证据] 负向管道 \\.\pipe\liuhao-attack-surface-neg-... aces=[{'sid': 'S-1-5-32-545', ...}]
[FAIL] 负向对照⑥ · 只授 Users 的管道被判为仅当前用户: got=['S-1-5-32-545'] expected=['S-1-5-21-...-500']
[证据] 负向备份包命中: ['model_config.key', 'integrity.key', 'auth_secrets.json', 'human_identities.json', 'model_registry.sqlite3', 'sk-live-negative-control-abcdef']
[FAIL] 负向对照⑦ · 含密钥的备份包被判为不含密钥: got=[...] expected=[]
[证据] 负向卸载残留判定: ['autostart', 'process:14576', 'port:50426']
[FAIL] 负向对照⑧ · 残留自启/进程被判为无残留: got=['autostart', 'process:14576', 'port:50426'] expected=[]
[证据] 注释掉 [UninstallRun] 卸载动作后的规格审计: {'stop': False, 'autostart': False, 'close': True, 'no_manual_reg': True}
[FAIL] 负向对照⑨ · 注释掉卸载动作后规格闸必须判红: got={'stop': False, 'autostart': False, 'close': True, 'no_manual_reg': True} expected={'stop': True, 'autostart': True, 'close': True, 'no_manual_reg': True}
结果：0/4 通过
EXIT=1
```
