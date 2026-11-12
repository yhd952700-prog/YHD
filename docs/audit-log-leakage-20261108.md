# 日志与诊断包敏感信息泄漏审计（2026-11-08）

- 审计对象：`d:\LiuHao-AI-OS`（只读审计，未修改任何源码）
- 审计方式：静态代码审计 + 隔离实例真实触发失败 + 真实检查落盘日志 / 诊断包 / 审计库
- 隔离实例：`127.0.0.1:19011`，数据目录 `D:\_audit\log-leak\rerun-lh1`
  （首轮在 **19009 / `D:\_audit\log-leak`** 跑通并得到同构结论；该端口与目录被并发审计进程占用且在运行中被清理，故用 19011 干净复跑，结论一致。`D:\LiuHaoData` 全程未读写。）
- 所有测试用 API Key / 密码 / 恢复码均为**合成值**，非真实凭据。

---

## 1. 结论

**有真泄漏。共 2 条实证通道、1 条旁路风险。**

| # | 级别 | 泄漏点 | 泄漏内容 | 是否实证 |
|---|------|--------|----------|----------|
| **F1** | **高（P0）** | 桌面壳 `desktop-gateway.log`（子进程 stdout/stderr 原样落盘） | **完整 API Key 明文**（`sk-...` 全量）、真实绝对路径、上游错误体原文 | ✅ 已实证 |
| **F2** | **中（P1）** | `liuhao.log` / `error.log` / **诊断包** | URL 查询参数里的凭据明文（`?key=...`），以及上游错误体里 `?key=...` 形式的凭据 | ✅ 已实证（诊断包原样带出） |
| F3 | 低 | 审计库 `audit_store.db` 的 `details.reason` | 异常 `str(exc)` 原文（本次触发 0 命中，属旁路风险） | 代码路径确认 |
| F4 | 低 | `bootstrap.log` | 含用户名的路径未被完全掩码 | 实证（`pytest-of-Administrator`） |

关键结论展开：

1. **文件日志通道对 `sk-` 是有效的**：`liuhao.log` / `error.log` 中 `sk-...` 已被写成 `sk-***REDACTED***`，完整 key 不落文件。
2. **但 stdout 通道没有脱敏**：`src.ai.observability._ensure_configured()` 给 `liuhao` logger 额外挂了一个**普通 `logging.Formatter` 的 `StreamHandler`**（`src/ai/observability.py:87-105`），它与 root 的脱敏 console handler 同时生效 → 每条 `liuhao.*` 记录向 stdout **输出两份**：一份明文、一份脱敏。桌面壳把子进程 stdout/stderr **原样**写入 `%LOCALAPPDATA%\LiuHao\logs\desktop-gateway.log`（`src/desktop/service.py:284/292/325-326`，`open(..., "ab")`，无任何脱敏）。因此，只要上游在 401/404 错误体里回显 key（OpenAI 等厂商的常见行为），**用户的完整 API Key 就会以明文长期留在 `desktop-gateway.log`**。
3. **`?key=` 不在任何脱敏规则里**：`_KV_SECRET`（`src/common/redaction.py:157-164`）只认 `api_key/access_key/token/password/...`，不认裸 `key`；`leak_violations()` 的自检清单（`src/common/redaction.py:278-304`）同样不认。`uvicorn.access` 会把**完整 query string** 写进日志，诊断包又会把日志尾部原样导出，且自检不报警 → **带 `?key=` 的凭据会进诊断包**。
4. 用户原文、密码、恢复码、身份库内容：**当前未发现泄漏**（详见 §2.5）。
5. 异常栈：**没有打印配置对象**（无 `base_url` / `api_key` / `messages`），但通过 `raise ... from exc` **保留了 SDK 原始异常链**，上游响应体原文会随 traceback 落盘——这是 F1/F2 的载荷来源。

---

## 2. 实测证据

### 2.1 F1：`desktop-gateway.log` 明文完整 API Key（高）

触发：模型配置 `base_url=http://127.0.0.1:19010/v1`、`api_key=sk-LHLEAK-1234567890abcdef`，mock 上游按真实厂商习惯在 401 响应体回显 `Authorization`。

子进程 stdout（桌面壳会原样追加到 `desktop-gateway.log`）中同一条记录出现两份：

```text
# 第 1 份：observability 的普通 StreamHandler → 明文
2026-11-07 14:08:08,242 ERROR liuhao.liuhao [tid=... cid=...] evt=llm.call.failed provider=openai model=gpt-4o-mini host=127.0.0.1 port=19010 status=401 error_class=AuthenticationError ... key_fp=sha256:a851d032 ...
openai.AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: Bearer sk-LHLEAK-1234567890abcdef. ... https://example.invalid/keys?key=PLAINSECRET-ABCDEF123456', ...}}

# 第 2 份：root 的 RedactingFormatter → 已脱敏
2026-11-07 14:08:08 | ERROR | liuhao.liuhao | evt=llm.call.failed ...
openai.AuthenticationError: ... 'Incorrect API key provided: Bearer sk-***REDACTED***. ... ?key=PLAINSECRET-ABCDEF123456' ...
```

- 第 1 份的格式是 `%(asctime)s %(levelname)s %(name)s [tid=... cid=...]`，正是 `src/ai/observability.py:92-96` 的 Formatter；
- 第 2 份的格式是 `%(asctime)s | %(levelname)s | %(name)s | %(message)s`，即 `config/logging.yaml` 的 `text` + `src/logging_config.py:186` 的 `RedactingFormatter`；
- **证明存在两个 console 输出者，其中一个不脱敏**。
- 佐证：同一条记录在 `logs/liuhao.log` 中只有脱敏后的 `sk-***REDACTED***`（文件通道正常）。

影响面：安装版主路径（桌面壳）默认即命中；`desktop-gateway.log` 不轮转、不脱敏，会持续累积。

### 2.2 F2：`?key=` 在日志与诊断包中明文（中）

(a) 访问日志：

```text
2026-11-07 14:08:02 | INFO | uvicorn.access | 127.0.0.1:63995 - "GET /v1/health?key=LHQUERYSECRET-ABCDEF123456 HTTP/1.1" 200
```

(b) 上游错误体（无 `sk-` 前缀的 URL 凭据）：

```text
openai.AuthenticationError: ... 'Incorrect API key provided: Bearer sk-***REDACTED***. ...
You can find your API key at https://example.invalid/keys?key=PLAINSECRET-ABCDEF123456' ...
```

(c) **诊断包（用户点「生成诊断包」）中同时存在以上两条明文**：

- `bundle.zip → logs/liuhao.log.txt`：包含 `?key=LHQUERYSECRET-ABCDEF123456`
- `bundle.zip → logs/error.log.txt`：包含 `?key=PLAINSECRET-ABCDEF123456`（`sk-` 已脱敏）
- 打包自检**未拦截**：`build_bundle()` 正常返回（HTTP 200，导出成功），`manifest.json` 仍声明 `"redaction": "username-path+secret"`，`README.txt` 仍承诺"不含密钥"。

补充：`src/desktop/diagnostics.py:196-208` 的 `_sanitize_url()` 只去除 userinfo，**保留 query**；因此 `providers.json` 中 `base_url=https://.../v1?key=...` 也会被带入桌面诊断包（`redact_text` 同样不认裸 `key`）。

### 2.3 日志轮转后旧文件是否同样脱敏

**结论：对"已覆盖模式"是安全的；对 `?key=` 一样漏。**

- 机制：脱敏发生在 `RedactingFormatter.format()`（写盘前），轮转只是改名 → `.1~.5` 里已经是脱敏后的字节。
- 真实文件：线上 `%LOCALAPPDATA%\LiuHao\data\logs\liuhao.log.1`（10 MiB）+ `liuhao.log`（约 3 MB）+ `error.log`，对 `sk-* / Bearer / ?key= / "api_key" / password / JWT / PEM` 全量扫描 **0 命中**（当前运行确实没遇到回显场景）。
- 强制轮转实验（`maxBytes=400`）：

```text
rot.log    bytes=201 sk_plaintext=False urlkey_plaintext=True
rot.log.1  bytes=335 sk_plaintext=False urlkey_plaintext=True
rot.log.2  bytes=335 sk_plaintext=False urlkey_plaintext=True
```

- 例外：**`desktop-gateway.log` 不经过任何 Formatter**（原始字节），见 F1；`bootstrap.log` 也不走 `redact_text`（只做路径用户掩码）。
- 诊断包差异：网关版只收 `*.log`（`src/gateway/diagnostic_bundle.py:240`，不含 `.log.1`）；桌面版收 `*.log` 与 `*.log.*`（`src/desktop/diagnostics.py:230-231`，含轮转文件），但两者都会再过一遍 `redact_text`。

### 2.4 异常栈里有没有把配置对象整个打印出来

**没有。**

`evt=llm.call.failed` 记录只包含白名单字段（`src/ai/liuhao.py:164-165`）：

```text
provider=openai model=gpt-4o-mini host=127.0.0.1 port=19010 status=401
error_class=AuthenticationError errno=- dns_host=- key_fp=sha256:a851d032
timeout_s=30.0 timeout_source=registry duration_ms=134 attempt=1 request_id=...
```

- 不含 `base_url`、`api_key`、`messages`、`headers`、配置对象 repr；
- `key_fp` 是 `sha256:<前 8 hex>`，不是明文（但只有 32 bit，见 §6-12）；
- traceback 中只有 SDK 栈帧 + **SDK 异常原文**，没有配置对象；
- 真正的风险是这份"SDK 异常原文"里可能带 key/URL——经 `raise ... from exc`（`src/ai/providers.py:151`、`_neutralize_http_error`）被完整保留，`_neutralize_http_error` 只净化了**新的** `ProviderHTTPError` 文案，没有切断 `__cause__` 链。

### 2.5 用户原文 / 密码 / 恢复码 / 身份库

| 检查项 | 结果 | 证据 |
|--------|------|------|
| 请求体用户原文 | **未泄漏** | 隔离实例发送标记文本 `LH-USER-TEXT-77aa 家庭住址…手机13800138000…邮箱 lh-leak@example.com`；`logs/`、`error.log`、诊断包、`audit.db` 全部 0 命中 |
| 真实日志中的 prompt 字段 | **未发现** | 线上 3 个日志文件约 13 MB 扫描 `"content":` / `role=user` / `messages=` → 0 |
| 密码 / 恢复码 | **未泄漏** | 登录/注册日志只有 principal、`jti`；扫描 `Leak-Pass` / `recovery` / `password` → 0 |
| 身份库内容 | **未进日志/诊断包** | 诊断包成员仅 `README/health/manifest/readiness/selfcheck/summary/logs`；`data` 清单只列文件名+大小，DB 检查只跑 `quick_check`，不导出内容 |
| 模型 API Key（文件通道） | **已脱敏** | `liuhao.log` / `error.log` 中为 `sk-***REDACTED***` |
| 模型 API Key（stdout 通道） | **明文泄漏** | 见 F1 |

---

## 3. 各类日志实际写入的字段

| 通道 | 写入者 | 实际字段 | 脱敏 |
|------|--------|----------|------|
| `logs/liuhao.log` | root `app_file` handler（INFO+） | `时间 \| 级别 \| logger \| 消息`；结构字段：`evt, provider, model, host, port, status, error_class, errno, dns_host, key_fp, timeout_s, timeout_source, duration_ms, attempt, request_id, correlation_id, kernel_action, outcome, policy, risk, jti, elapsed_ms` | ✅ `RedactingFormatter`（路径折叠/环境变量名/密钥） |
| `logs/error.log` | root `error_file` handler（ERROR+） | 同上；**含完整 traceback（含 SDK 原始异常链）** | ✅ 同上（但 `?key=` 不在规则内） |
| `logs/launcher.log` | `RotatingTextWriter`（`src/logging_config.py:330-363`，`redact=True`） | 启动器事件、就绪地址、端口 | ✅ |
| `logs/bootstrap.log` | `src/bootstrap_log.py::write_event` | `evt, path, winerror, errno, free_bytes, action`；路径做用户名掩码 | ⚠️ 仅路径掩码，不经 `redact_text`；`pytest-of-Administrator` 这类路径漏掩 |
| `logs/desktop-gateway.log` | 桌面壳原样捕获子进程 stdout/stderr（`src/desktop/service.py:284/292/325-326`） | 控制台全部输出：WARNING+ 日志、**未脱敏的 `liuhao.*` 副本**、第三方 traceback | ❌ **完全无脱敏** |
| `logs/desktop.log` | 桌面壳 `RotatingFileHandler`（`src/desktop/app.py:71-80`） | 桌面 UI/服务事件 | ✅ `RedactingFormatter` |
| 审计库 `audit_store.db` | `kernels.audit.log_event` | `event_id, event_type, principal_id, scope, timestamp, correlation_id, outcome, details(JSON), 哈希链` | ❌ 写入前无 `redact_text`；`@audited` 的 `reason=f"{type(exc).__name__}: {exc}"[:200]`（`src/ai/audit.py:141`）可能带入异常原文 |
| `uvicorn.access` | uvicorn | `client_addr, request_line(含完整 query string), status_code` | ⚠️ 仅靠 `RedactingFormatter`，裸 `key=` 漏 |

---

## 4. 诊断包实际打包的内容

网关版（用户点「生成诊断包」→ `GET /v1/diagnostics/bundle`）：

```text
README.txt
health.json
readiness.json
selfcheck.json
summary.json
manifest.json            # 声明 redaction=username-path+secret
logs/liuhao.log.txt      # 尾部 200 行 / 200KB
logs/error.log.txt       # 尾部 200 行 / 200KB
```

- 打包前对每个成员执行 `sanitize_text`（`src/gateway/diagnostic_bundle.py:348`），再跑 `leak_violations` 自检（`:351`）；
- **不含**：身份库、凭据库、会话库、`providers.json`、审计库、请求体；
- 结论：**结构上是收敛的；内容上仍会带出 `?key=`**（F2），因为脱敏与自检共用同一份漏规则。

桌面版（`src/desktop/diagnostics.py`）额外包含 `providers.json`（`api_key` 加密态，但 `base_url` 保留 query）、数据目录清单、DB `quick_check`；日志收集含轮转文件（`:230-231`）。

---

## 5. 已覆盖 / 未覆盖 脱敏模式对照表

### 5.1 已覆盖（`redact_text` + `leak_violations` 双侧都认，实测通过）

| 模式 | 规范化结果 |
|------|-----------|
| `-----BEGIN ... PRIVATE KEY-----` | `***PRIVATE-KEY-REDACTED***` |
| `sk-` 开头（OpenAI/DeepSeek/Moonshot/Anthropic） | `sk-***REDACTED***` |
| `AIza`、`gsk_`、`gh[pousr]_`、`AKIA|ASIA`、`xox?-` | 对应掩码 |
| JWT `eyJ...` | 掩码 |
| `Authorization: Bearer/Basic xxx` | `***REDACTED***` |
| `Bearer <token>`（正文中） | 掩码 |
| URL userinfo `scheme://user:pass@host` | 掩码 |
| `api_key` / `apikey` / `api-key` `=`/`:` | 掩码（含 JSON `"api_key": "..."`） |
| `access_key` / `secret_key` / `private_key` / `signing_key` / `integrity_key` / `client_secret` | 掩码 |
| `token` / `access_token` / `refresh_token` / `session_token` / `id_token` | 掩码 |
| `password` / `passwd` / `pwd` | 掩码 |
| `recovery_code` / `passphrase` | 掩码 |
| `cookie=` | 仅掩码到第一个 `;` 前（部分覆盖） |
| 邮箱 | 掩码 |
| 绝对路径 / 环境变量名 / 层级 ID / 策略 ID | 折叠 |

### 5.2 未覆盖（**实测确认会漏**）

| 模式 | 实测 | 影响 |
|------|------|------|
| **裸 `key`**：`?key=`、`&key=`、`"key": "..."`、`key=...` | ❌ 明文 | F2；诊断包原样带出 |
| `?sig=` / `?signature=` / `?auth=` / `?code=`（OAuth 授权码） | ❌ 明文 | 访问日志自带 query |
| `sk_live_` / `sk_test_` / `rk_live_`（Stripe） | ❌ 明文 | `sk-` 规则要求连字符 |
| `hf_`（HuggingFace）、`r8_`（Replicate）、`dapi`（Databricks）、`ya29.`（Google OAuth）、`xapp-`（Slack）、`npm_`、`pypi-`、`github_pat_`、`glpat-`、`ATATT`、`whsec_` | ❌ 明文 | 无前缀规则 |
| Azure 裸 32/64 hex key、AWS Secret Access Key（40 位 base64） | ❌ 明文 | 仅覆盖 AKIA 的 Key ID |
| 火山引擎 AK/SK 等无固定前缀的 base64 | ❌ 明文 | 同上 |
| `Cookie:` 第 2 个及之后的键值 | ⚠️ 部分 | `sid=***; theme=dark` 只剩前半脱敏 |
| `Set-Cookie:` 整行 | ⚠️ 部分 | 同上 |
| 手机号 / 身份证 / 住址等 PII | ❌ 不处理 | 当前未写日志；但诊断包也不做 PII 剥离 |
| **stdout/stderr 原始字节**（`desktop-gateway.log`） | ❌ 完全不经过脱敏 | **F1，最严重** |
| 审计库 `details` | ❌ 无脱敏 | F3 |
| 通用"高熵但无前缀"字符串 | ❌ 无兜底 | 厂商自定义 key |

---

## 6. 需要补的脱敏规则清单（按优先级）

**P0 — 先堵真泄漏**

1. **统一 stdout 通道脱敏**
   - `src/ai/observability.py:87-105`：给自建 `StreamHandler` 换装 `RedactingFormatter`（并复用 `_TraceFilter`），或直接删除该 handler、只保留 `propagate=True`（同时消除双写）。
   - `src/desktop/service.py:284-326`：把子进程输出从"原始字节直写"改为**逐行管道读取 → `redact_log_text(line)` → 写盘**；或在 `Popen` 前移除子进程内所有非脱敏 handler。
   - 验收：单测断言 `desktop-gateway.log` 中出现 `sk-***REDACTED***` 且不存在 `sk-<≥8>` 明文。

2. **裸 `key` 入脱敏与自检**
   - `_KV_SECRET`（`src/common/redaction.py:157-164`）增加 `key`（要求 `["']?\s*[=:]` 紧邻，避免误伤 `keyboard`）；
   - `leak_violations` 的 KV 特征（`:296-304`）同步增加 `key`；
   - 覆盖 `?key=`、`&key=`、`"key":`、`key=` 四种形态；
   - 回归样例：`/v1/x?key=SECRET123456`、`{"key": "SECRET123456"}`、`key=SECRET123456`。

3. **诊断包自检与脱敏"同源同漏"要解除**
   - 增加一组**独立语料回归测试**：对同一份 `corpus`（含 `?key=`、`"key":`、`sk_live_`、`hf_`、手机号等）断言 `leak_violations(redact_text(corpus)) == []`；
   - 自检命中时的行为保持"拒包/删产物"，不要降级为警告。

**P1 — 覆盖常见凭据形态**

4. 新增前缀规则：`sk_(live|test)_`、`rk_(live|test)_`、`hf_`、`r8_`、`dapi`、`ya29.`、`xapp-`、`npm_`、`pypi-`、`github_pat_`、`glpat-`、`ATATT`、`whsec_`。
5. 在 `key=/api-key:/x-api-key:` 语义上下文中，补 Azure 32/64 hex 与 AWS 40 位 Secret Access Key。
6. Query 参数名补：`sig`、`signature`、`auth`、`code`、`ticket`、`access`（仅在 URL query 上下文生效，避免误伤正文）。
7. `Cookie` / `Set-Cookie` 整行脱敏（按 `;` 拆分后逐个 kv 处理）。
8. **URL 规范化**：日志与诊断包输出 URL 时统一丢弃/掩码 query（`src/desktop/diagnostics.py:196-208` 的 `_sanitize_url` 保留 query，应改为只保留 `scheme://host:port/path`，`providers.json` 的 `base_url` 同规则）。

**P2 — 纵深防御**

9. 审计库写入前统一 `redact_text`：`kernels.audit.log_event` 对 `details`（尤其 `reason`）先脱敏；`src/ai/audit.py:141` 的 `reason` 先 `redact_text` 再截断。
10. `leak_violations` 增加"高熵兜底"（长度 20-64、Shannon ≥ 4.0 的 base64/hex），并用白名单排除 `trace_id/correlation_id/jti/sha256:<hex>` 等已知非密字段。
11. `bootstrap.log` 的路径掩码改用与 `redact_log_text` 相同的用户目录规则，覆盖 `*-of-<user>` 之类形态。
12. `key_fp` 由 `sha256[:8]`（32 bit）提升到 `sha256[:16]`，或改为 `HMAC(本地盐, key)`，降低离线枚举风险。
13. 选做：对 `uvicorn.access` 关闭 query 记录（自定义 access formatter 只输出 path），从根上消除"URL 凭据进日志"。

---

## 7. 复现与产物位置

- 复现脚本（不属于仓库）：
  - `D:\_audit\log-leak\rerun-lh1\harness.py`（注册/登录、4 类失败触发、下载诊断包并扫描）
  - `D:\_audit\log-leak\rerun-lh1\verify_rules.py`（规则级验证：`?key=`/`"key":`/`sk-`/`Bearer`）
  - `D:\_audit\log-leak\rerun-lh1\rotation_check.py`（强制轮转验证）
- 产物：
  - `D:\_audit\log-leak\rerun-lh1\logs\`（liuhao.log / error.log）
  - `D:\_audit\log-leak\rerun-lh1\bundle.zip` 及解包目录 `bx\`
  - `D:\_audit\log-leak\rerun-lh1\audit.db`
- 触发场景：无效 key（401 回显）、不可达端点（`127.0.0.1:1`）、错误模型名（404 `no_such_model`）、`base_url` 带 `?key=`、内联 `POST /v1/models/test`、`GET /v1/health?key=...`、带标记文本的 `/v1/chat`。

## 8. 方法与隔离声明

- 本次审计**未修改任何项目源码**；仅新增了本报告。
- `D:\LiuHaoData` 未读写。
- 说明：模型注册表默认不跟随 `LIUHAO_DATA_DIR`，会落到仓库 `data/model_registry.sqlite3`（gitignored）。首轮触发写入的 3 条测试配置已删除并复核（现仅剩既有 `bench-ollama`）。干净复跑已显式设置 `LIUHAO_MODEL_CONFIG_DB` 指向隔离目录。
- 在线真实日志扫描仅做只读聚合统计，报告未输出任何真实凭据；文中所有 key/密码均为合成值。
