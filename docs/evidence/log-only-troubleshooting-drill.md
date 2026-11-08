# 只看日志能不能排障：6 个真实故障演练报告

> 结论：6 个故障里，**3 个能在 3 分钟内靠日志定位，2 个勉强，1 个在日志里完全看不出来**。
> 核心缺口不是"日志太少"，而是三类问题：
> 1）关键失败写在用户不会看的通道（WARNING / 控制台 / stderr），`error.log` 是空的；
> 2）失败日志缺"路径 + 错误码"，只能看出"坏了"，看不出"哪个文件坏了"；
> 3）有一整类失败（前端白屏）被 SPA 兜底伪装成 HTTP 200，日志里连一条 WARNING 都没有。

---

## 一、演练口径

| 项 | 内容 |
| --- | --- |
| 代码版本 | `git bee8d0d6`（工作区有未提交改动，采集期间 `src/ai/providers.py` 被并发修改过一次，见第六节） |
| 系统 | Windows / Python 虚拟环境 `.venv` / 网关 `python -m src.gateway` |
| 日志通道 | `liuhao.log`（INFO 及以上）、`error.log`（仅 ERROR）、控制台（WARNING 及以上）；配置见 `config/logging.yaml` |
| 隔离方式 | 每个故障独立 `LIUHAO_DATA_DIR` / `LIUHAO_INSTANCE_DIR` / 端口；独立模型注册表；临时登录身份；不改源码 |
| "能定位"定义 | 只拿 `launcher.log` / `liuhao.log` / `error.log` / stderr 转储，不看代码、不看进程，3 分钟内能说出"哪一层坏了 + 下一步做什么" |
| 检索成本 | 对下面每个关键字做 `Select-String` 实测 **1–48 ms**。真正的成本是"选错文件"和"看不懂错误码"，不是搜索速度 |

---

## 二、总览

| # | 故障 | 只看日志的结论 | 决定性证据 | 主要缺口 |
| --- | --- | --- | --- | --- |
| 1 | 端口被占用 | ✅ 能定位（启动器路径） | `launcher.log` 一行给出端口、PID、进程名、处置命令 | 直连网关时没有占用者 PID；Windows 双绑定会让"占用"看起来像"启动成功" |
| 2 | 数据目录不可写 | 🔴 完全看不出（日志目录不可写）<br>🟡 勉强（日志可写、数据不可写） | 只有 stderr 一行；或大量 `unable to open database file` | 没有任何日志文件；失败消息不含路径和 errno；服务仍报 ready |
| 3 | 模型 Key 无效 | ✅ 能定位（但只在 `liuhao.log`） | `status=401` + `invalid_api_key` traceback | `error.log` 为空；UI 文案与"网络不通"完全一样；无 key 指纹/模型/域名结构化字段 |
| 4 | 模型服务网络不通 | ✅ 能定位（三种形态可区分） | `WinError 10061` / `read timeout=30.0` / `getaddrinfo failed` | 同上，都在 WARNING traceback 里；没说超时值来自哪里 |
| 5 | 数据库文件损坏 | 🟡 勉强 | 500 + `DatabaseError: file is not a database` | 不说是哪个库文件；audit 被静默跳过却仍打印"checkpointer started"；启动仍 ready |
| 6 | 前端静态资源缺失（白屏） | 🔴 完全看不出 | 缺失 JS 被兜底成 **200 text/html**，访问日志也是 200 | 零 WARNING / 零 ERROR；dist 缺失的 WARNING 只进 stderr，没进日志文件 |

---

## 三、逐故障记录

### 故障 1：端口被占用导致起不来

**注入**：用独占绑定（`SO_EXCLUSIVEADDRUSE`）占住 `127.0.0.1:18221`，再跑正常启动器。

**日志原文**（`logs/launcher.log`，与 stdout 一致）：

```text
[FAIL] 网关端口 18221 已被占用（占用者：PID 12944 python.exe）。
       为避免地址变化导致书签 / 旧标签 / PWA 失联，启动器不会自动换端口。
       处理：结束占用该端口的进程后重试，或用 --api-port 指定其它端口（例如 --api-port 18222）。
```

**判定：能定位。** 端口、占用者 PID、进程名、处置动作都给全了，30 秒内可结论。代码位置：[start_liuhao.py](file:///d:/LiuHao-AI-OS/scripts/start_liuhao.py#L202-L208)。

**补充样本：直接启动网关（绕过启动器）**，`error.log` 里是：

```text
2026-11-07 12:18:22 | ERROR | uvicorn.error | [Errno 10048] error while attempting to bind on address ('127.0.0.1', 18222): [winerror 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次。
```

且 `liuhao.log` 中这条 ERROR 之前先出现了：

```text
2026-11-07 12:18:22 | INFO | uvicorn.error | Application startup complete.
```

即**先报"启动完成"再报绑定失败**，顺序有误导性。这里也没有占用者 PID。

**缺什么**：直连路径缺"谁占用"；错误顺序容易让人以为启动成功了。

**建议加的一条日志**（在 `_port_in_use` 里记录探测结果，绑定失败时补 owner）：

```text
evt=net.bind.checked host=127.0.0.1 port=18221 result=occupied owner_pid=12944 owner_name=python.exe
evt=net.bind.failed  host=127.0.0.1 port=18221 winerror=10048 owner_pid=12944 action="kill pid or --api-port 18222"
```

字段：`host / port / result / owner_pid / owner_name / winerror / action`。

**附加发现（比日志更值得修）**：如果占用方**没有**设置 `SO_EXCLUSIVEADDRUSE`（很多 Python/Node 小程序都没有），Windows 允许第二个进程绑定同一端口。此时启动器的端口探测返回"空闲"，网关照常报"就绪"，但请求可能仍被旧进程接走——日志上完全正常。建议探测时用独占绑定，或在启动后回读一个带随机 nonce 的 `/v1/startup` 字段确认"这个监听者是我"。

---

### 故障 2：数据目录不可写

同一个故障有两种形态，日志表现完全不同。

#### 2A. 日志目录不可写（更接近"程序装在只读目录 / 数据盘满 / 杀软拦截"）

**注入**：把目录 ACL 设为只读（`Everyone:(OI)(CI)(RX)`，实测写入被拒绝），`LIUHAO_LOG_DIR` 指向它的 `logs` 子目录。

**日志原文**：**没有任何日志文件**（`logs/` 都建不出来）。唯一证据是 stderr：

```text
[FAIL] 无法准备日志目录：数据目录可能不可写、磁盘空间不足，或安全软件拦截了写入。请确认数据目录可写、磁盘有余量后重试。
```

**判定：完全看不出。** 用户拿到的是"安装目录为空 / 没有日志"，而不是"哪条路径、什么错误码"。文案列了三种可能，却没有任何字段能排除其中两种。

**缺什么**：路径、`errno/winerror`、剩余空间、失败发生在哪一步。

**建议加的一条日志**：写到一个**不依赖业务数据目录**的引导日志（固定 `%LOCALAPPDATA%\LiuHao\logs\bootstrap.log`，失败再退到 `%TEMP%`）：

```text
evt=boot.logdir.failed path=D:\LiuHao\data\logs winerror=5 errno=13 free_bytes=8123456789 action="授予写权限 / 更换数据目录 / 关闭拦截"
```

字段：`path / winerror / errno / free_bytes / action`。

#### 2B. 日志可写、数据目录不可写

**注入**：数据目录只读，但 `LIUHAO_LOG_DIR` 指到可写目录；`audit.db`、`conversation.db` 落在只读目录里。

**日志原文**（`error.log`）：

```text
2026-11-07 12:20:34 | ERROR | src.gateway.main | EXECUTOR FENCE INSTALL FAILED -- autonomous actions will run UN-FENCED (fail-open risk): unable to open database file
...
sqlite3.OperationalError: unable to open database file
```

`liuhao.log` 随后是大量同因告警：

```text
2026-11-07 12:20:34 | INFO  | src.gateway.main | Gateway startup complete
2026-11-07 12:20:34 | WARNING | liuhao.kernel.policy | POLICY RULE MUTATION NOT RECORDED rule=human_sovereignty change=rule_added reason=None: unable to open database file
```

**判定：勉强。** 能看出"SQLite 打不开"，但：
- 不知道是**哪个文件**（`unable to open database file` 本身不带路径，traceback 被脱敏成 `<本地路径>`）；
- 不知道是权限、目录不存在、还是磁盘满；
- 网关照样 `Gateway startup complete`，用户以为正常；
- 严重的是：审计围栏安装失败后仍继续跑（`UN-FENCED (fail-open risk)`），这是安全降级，却只是一条 ERROR + 一堆 WARNING。

**缺什么**：库文件相对路径、`winerror=5`、失败动作（是继续降级还是拒绝启动）。

**建议加的一条日志**：

```text
evt=db.open.failed subsystem=audit path=data/audit.db winerror=5 errno=13 sqlite_msg="unable to open database file" action="degraded:audit+policy; set LIUHAO_DATA_DIR to a writable dir"
```

并增加启动汇总（见第四节 P0-1），让"哪些子系统降级"在一条日志里可见。

---

### 故障 3：模型 Key 无效

**注入**：模型注册表只放一个 OpenAI 模型，`AI_PROVIDER_KEY=sk-invalid-drill-...`，请求真实 `api.openai.com`（实测返回 401）。

**日志原文**（`liuhao.log`）：

```text
2026-11-07 12:26:10 | INFO | liuhao.ai.providers | 模型服务 upstream HTTP failure status=401

2026-11-07 12:26:10 | WARNING | liuhao.liuhao | chat generation failed turn=1 correlation=f32a55469e6e4bea provider=OpenAIProvider: 模型服务返回 HTTP 401，详情已记录到服务端日志。
Traceback (most recent call last):
  ...
openai.AuthenticationError: Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-inva****************drill. ...', 'type': 'invalid_request_error', 'code': 'invalid_api_key'}}
```

**判定：能定位。** 看到 `401` + `invalid_api_key` 就能结论"Key 无效/被吊销"。

**但有三个坑**：
1. **`error.log` 是空的**（这条是 WARNING），只看错误日志的用户会完全漏掉；
2. 日志说"详情已记录到服务端日志"，但详情其实就在同一条 WARNING 的 traceback 里，`error.log` 里没有；
3. UI 返回的文案与"网络不通"完全相同（都是"检查 API Key 与网络连接"），用户无法从界面区分。

**缺什么**：provider / model / 目标域名 / key 指纹（不出密钥本身）/ 上游 request-id；以及"这是鉴权问题，不是网络问题"的明确归类。

**建议加的一条日志**（用 ERROR，让它进 `error.log`）：

```text
evt=llm.call.failed provider=openai model=gpt-4o-mini host=api.openai.com status=401 error_class=AuthenticationError key_fp=sha256:ab12cd34 request_id=req_drill_invalid_key hint=invalid_api_key action="更换 API Key"
```

字段：`provider / model / host / port / status / error_class / key_fp / request_id / duration_ms / hint / action`。

---

### 故障 4：模型服务网络不通

**注入**：同一套对话链路，分别指向三种坏目标。

**日志原文**（均为 `liuhao.log` 的 WARNING headline）：

拒绝连接（端口没人监听）：

```text
chat generation failed turn=1 correlation=9aa2294a09f145ae provider=OllamaProvider: HTTPConnectionPool(host='127.0.0.1', port=11500): Max retries exceeded with url: /api/chat (Caused by NewConnectionError("... [WinError 10061] 由于目标计算机积极拒绝，无法连接。"))
```

连接超时（对端接受连接但永不响应）：

```text
chat generation failed turn=1 correlation=d008904f21c54fb5 provider=OllamaProvider: HTTPConnectionPool(host='127.0.0.1', port=11501): Read timed out. (read timeout=30.0)
```

DNS 失败（域名不存在）：

```text
chat generation failed turn=1 correlation=574e0376b6c845cc provider=OllamaProvider: HTTPConnectionPool(host='nonexistent-drill-host.invalid', port=11434): Max retries exceeded with url: /api/chat (Caused by NameResolutionError("... Failed to resolve 'nonexistent-drill-host.invalid' ([Errno 11001] getaddrinfo failed)"))
```

**判定：能定位。** 三种形态在 headline 里就能区分，不需要展开 traceback：
- `10061 / 积极拒绝` → 服务没起或端口写错；
- `read timeout=30.0` → 被防火墙丢包、服务卡死或地址不可达；
- `getaddrinfo failed / 11001` → 域名写错或 DNS 不可用。

**缺什么**：
- 同样只在 WARNING，`error.log` 为空；
- 超时实际是 **30.0s**，而本次环境变量 `AI_PROVIDER_TIMEOUT=20` 没生效（模型注册表里的值覆盖了环境变量），日志没说明"这个 30s 是哪来的"；
- 没有 `duration_ms`、`attempt` 次数、`request_id`，线上定位"是不是偶发"要靠猜。

**建议加的一条日志**（ERROR）：

```text
evt=llm.call.failed provider=ollama host=127.0.0.1 port=11501 error_class=Timeout errno=10060 timeout_s=30.0 timeout_source=registry duration_ms=30011 attempt=1 request_id=...
evt=llm.call.failed ... error_class=DNSFailure errno=11001 dns_host=nonexistent-drill-host.invalid
evt=llm.call.failed ... error_class=ConnectionRefused errno=10061
```

字段：`provider / host / port / error_class / errno / dns_host / timeout_s / timeout_source / duration_ms / attempt / request_id`。

---

### 故障 5：数据库文件损坏

**注入**：把 `audit.db` 与 `conversation.db` 写成非 SQLite 的垃圾字节后启动。

**日志原文**（`error.log`）：

```text
2026-11-07 12:29:04 | ERROR | src.gateway.main | EXECUTOR FENCE INSTALL FAILED -- autonomous actions will run UN-FENCED (fail-open risk): file is not a database
...
sqlite3.DatabaseError: file is not a database
2026-11-07 12:29:07 | ERROR | liuhao.conversation_store | ERROR conversation.get_conversation_store exc=DatabaseError elapsed_ms=109.5
2026-11-07 12:29:07 | ERROR | src.gateway.main | Unhandled exception trace_id=9c9af326-... path=http://127.0.0.1:18271/v1/chat method=POST: DatabaseError: file is not a database
```

同时 `liuhao.log` 里有两处**互相矛盾**的记录：

```text
2026-11-07 12:29:04 | INFO | src.gateway.main | Kernel lifecycle: initialized=['identity'], skipped_not_present=['audit', 'capability', ...]
2026-11-07 12:29:04 | INFO | src.gateway.main | Audit checkpointer started (...) against AUDIT_DB_PATH=<本地路径>\audit.db
```

**判定：勉强。** 能看出"有库文件损坏"，但：
- **不知道是哪个库**：`file is not a database` 不带文件名，traceback 里的路径全被脱敏成 `<本地路径>`；
- audit 实际被跳过，却又打印 "Audit checkpointer started"；
- 启动仍是 ready，用户只有发消息时才看到 500；
- 每条请求都失败，没有自动隔离损坏文件，也没有补救提示。

**缺什么**：库文件相对路径、损坏判定、处置动作（隔离/重建/拒绝启动）。

**建议加的一条日志**：

```text
evt=db.open.failed subsystem=audit path=data/audit.db sqlite_msg="file is not a database" action="quarantined -> data/audit.db.corrupt.20261107T122904"
evt=startup.degraded components=["audit","conversation_store"] ready=false
```

字段：`subsystem / path（相对数据目录）/ sqlite_msg / action / components / ready`。
路径建议保留"相对数据目录"的形态（如 `data/audit.db`），既能定位，又不泄露绝对路径，比 `<本地路径>` 有用。

---

### 故障 6：前端静态资源缺失（页面白屏）

**注入**：保留 `dist/index.html`，删除它引用的 `/assets/index-B0ASXmTE.js`。

**日志原文**（`liuhao.log` 的访问日志）：

```text
2026-11-07 12:30:27 | INFO | src.gateway.main | GET / 200 0.6220s trace_id=520c3e0c-...
2026-11-07 12:30:27 | INFO | uvicorn.access | 127.0.0.1:60373 - "GET / HTTP/1.1" 200
2026-11-07 12:30:27 | INFO | src.gateway.main | GET /assets/index-B0ASXmTE.js 200 0.0216s trace_id=9247af91-...
2026-11-07 12:30:27 | INFO | uvicorn.access | 127.0.0.1:60373 - "GET /assets/index-B0ASXmTE.js HTTP/1.1" 200
```

实测响应：`GET /assets/index-B0ASXmTE.js` 返回 **200 `text/html`**，body 是 `index.html`。
该进程整份 `liuhao.log` 中 **WARNING/ERROR 条数为 0**。

**判定：完全看不出。** 浏览器因为"JS 位置拿到 HTML"而白屏，但日志里是一串 200，看起来一切正常。

**补充样本：dist 完全不存在**

```text
console build output at D:\LiuHao-AI-OS\.logdrill\f6\emptydist has no index.html -- not serving it (run `npm run build` in apps/console/console)
```

这条 WARNING **只出现在 stderr**，`liuhao.log` 里没有。原因：`python -m src.gateway` 会先执行包的 `__init__`，而 [__init__.py](file:///d:/LiuHao-AI-OS/src/gateway/__init__.py#L21) 在导入 `main` 时就构建了 app，此时日志还没初始化；INFO 被丢弃，WARNING 走 `lastResort` 打到 stderr。对应代码：[console_static.py](file:///d:/LiuHao-AI-OS/src/gateway/console_static.py#L135-L154) 与 [console_static.py](file:///d:/LiuHao-AI-OS/src/gateway/console_static.py#L189)。

**缺什么**：缺失资源的 404、dist 路径、构建时间/文件数、是否走了兜底。

**建议加的一条日志**（同时要改行为，不然日志也救不了）：

1. 对 `/assets/` 前缀或带静态扩展名（`.js/.css/.map/.woff2/.svg/.png`）的 404 **不要回 `index.html`**，直接返回 404；
2. 打一条 WARNING：

```text
evt=console.asset.missing path=/assets/index-B0ASXmTE.js ext=js dist=D:\LiuHao\apps\console\console\dist hint="run npm run build; dist may be stale"
```

3. 挂载成功时补一条（要保证它在日志初始化之后输出）：

```text
evt=console.mount dist=D:\...\dist index_html=true assets=137 built_at=2026-11-06T18:20:00
```

字段：`dist / index_html / assets / built_at / path / ext / hint`。

---

## 四、最小日志补充清单

按"投入最小、收益最大"排序。每条都给出可直接照抄的事件名与字段。

### P0（不做，用户就永远只能猜）

1. **启动自检一行**（日志初始化完成后、任何模型/DB 调用之前）
   ```text
   evt=startup.check build=Y1.0 cwd=D:\LiuHao data_dir=D:\LiuHao\data data_writable=true log_dir=D:\LiuHao\data\logs log_writable=true db=data/liuhao.db db_open=ok port=8080 port_bindable=true console_dist=D:\...\dist console_index=true provider=openai model=gpt-4o-mini key_fp=sha256:ab12cd34
   ```
   字段：`build / cwd / data_dir / data_writable / log_dir / log_writable / db / db_open / port / port_bindable / console_dist / console_index / provider / model / key_fp`。
   要求：**绝对不能包含密钥明文**，只放 `sha256` 前 8 位。

2. **独立 bootstrap 日志**
   路径固定 `%LOCALAPPDATA%\LiuHao\logs\bootstrap.log`（失败退 `%TEMP%`），不依赖任何业务目录。任何"日志目录都建不出来"的失败都写这里，且必须带 `path / winerror / errno / free_bytes / action`。

3. **修启动日志丢失**
   让 `src/gateway/__init__.py` 不要在导入时构建 app（改成惰性 `get_app()`），或在导入 `main` 之前就完成日志初始化。否则所有"挂载/配置/路由"阶段的 INFO 永远不会进日志文件，WARNING 只会进 stderr。

4. **模型调用失败统一为一条 ERROR**
   ```text
   evt=llm.call.failed provider=... model=... host=... port=... status=... error_class=... errno=... dns_host=... key_fp=... timeout_s=... timeout_source=... duration_ms=... attempt=... request_id=... hint=... action=...
   ```
   同时保留现有 WARNING traceback 便于开发排查。这样 `error.log` 才真正可用。

### P1（能让"哪个文件坏了"从猜测变成事实）

5. **数据库打开失败**
   ```text
   evt=db.open.failed subsystem=audit|conversation_store|memory|... path=data/xxx.db sqlite_msg=... winerror=... action=quarantine|rebuild|abort
   ```
   外加启动汇总：
   ```text
   evt=startup.degraded components=["audit"] ready=false
   ```
   并让 `/v1/startup` 返回同一份 `degraded` 列表。

6. **静态资源**
   `evt=console.asset.missing` + 禁止 SPA 兜底吞掉 `/assets/*`；启动时补 `evt=console.mount`。

### P2（体验优化）

7. **端口探测**
   `evt=net.bind.checked` / `evt=net.bind.failed`，带 `owner_pid / owner_name / winerror / action`。

8. **审计降级一致性**
   修掉"audit 初始化失败却打印 `Audit checkpointer started`"的矛盾；降级时不要输出成功语义的 INFO。

---

## 五、给用户的 60 秒自助排查顺序

1. 先看 `data\logs\error.log`。**空文件不等于没问题**（本次 6 个故障里有 3 个不会写 `error.log`），接着看 `liuhao.log`。
2. 在 `liuhao.log` 里按顺序搜这些关键字：

| 搜到 | 含义 | 下一步 |
| --- | --- | --- |
| `10048` / `已被占用` | 端口被占 | 结束占用进程，或 `--api-port 18222` |
| `10061` / `积极拒绝` | 模型服务没起 / 端口错 | 启动 Ollama 或改 `OLLAMA_BASE_URL` |
| `10060` / `read timeout` | 被墙 / 服务卡死 | 查代理、防火墙、服务进程 |
| `11001` / `getaddrinfo` | 域名解析失败 | 检查 DNS / 域名拼写 |
| `401` / `invalid_api_key` | Key 无效 | 换 Key |
| `file is not a database` | 数据库损坏 | 备份后删/改名对应 `.db`，重启重建 |
| `unable to open database file` | 目录不可写/磁盘满 | 查数据目录权限与剩余空间 |
| `GET /assets/... 200` + 白屏 | 静态资源被 SPA 兜底 | 重新 `npm run build`，或删浏览器缓存重试 |
| 完全没有日志文件 | 日志目录不可写 | 查 ACL / 磁盘 / 杀软，见 `bootstrap.log` |

3. 如果连日志目录都没有：只可能是权限/磁盘/杀软三类问题，直接看启动窗口的最后一行 stderr（当前唯一证据）。

---

## 六、证据与限制

- 所有日志片段均为本次演练的真实输出；采集器把绝对路径脱敏为 `<本地路径>` / `<程序目录>` / `<环境变量>` 是产品既有行为。
- 采集期间工作区有并发修改：`src/ai/providers.py` 在 12:19:25 曾被改到短暂无法解析（第 526 行缩进错误），因此故障 2 的首轮样本作废、已重跑；本报告只使用重跑后的稳定样本。
- 未覆盖：Docker 单容器启动、多 worker、日志轮转后的历史检索、`systemd`/服务方式（stderr 不落盘的场景）。其中"服务方式 + 数据目录不可写"会把故障 2A 变成**零证据**，风险最高。

