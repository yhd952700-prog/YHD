# 「8 类故障只看日志」重演（2026-11-08，补丁后重跑）

这次不是纸面推演：用临时数据目录真的造出 8 类故障，判断时**只给日志文本**
（不提供代码、不提供字段说明、不看进程 stdout/stderr），看能否定位到正确原因。
每类记录「造了什么 / 日志里哪几行足够判断 / 实际耗时 / 判定档位」。

本文档是**第二轮**：先跑出 6/8，然后按缺口补了三处日志字段，再用同一套
演练脚本重跑。补丁内容与前后对照见文末「三处改动」与「路径折叠对照」。

## 演练口径

- 仓库 `d:\LiuHao-AI-OS` 未改动业务逻辑之外的任何安装产物；未运行安装器；
  未触碰 `installer\`、`dist\`；未提交 commit。
- 每类故障的真实日志目录原样复制到 `D:\cache\temp\liuhao-drill-1108\evidence\fN\`
  （仅 `*.log`），判断只用这些文本。
- 计时起点：f1/f2/f3/f7/f8 为启动网关前（f2 含占位进程启动）；f4–f6 为启动
  真实对话子进程前（含模块导入、助手构造与上游请求耗时）。终点为日志里首次
  出现该判断行的墙钟时刻。两次运行的机器负载不同，耗时只用于说明"能在秒级
  拿到结论"，不与第一轮逐项比较。
- 判定档位：
  - **能定位**：哪一步、因为什么、下一步三件事都能从日志写出；
  - **勉强**：能看出坏了，但原因或下一步只能猜；
  - **不能**：日志里没有任何一行能指认。

## 结论先行

| # | 故障类别 | 档位 | 实际耗时（秒） |
|---|----------|------|----------------|
| f1 | 服务没起来（数据目录路径被同名普通文件挡住） | **能定位** | 5.8 |
| f2 | 端口被占（8080 独占监听） | 能定位 | 7.1 |
| f3 | 数据目录不可写（ACL 拒绝写） | **能定位** | 7.2 |
| f4 | Key 无效（401 invalid_api_key） | 能定位 | 11.3 |
| f5 | Key 额度耗尽（402 insufficient_quota） | 能定位 | 11.6 |
| f6 | 模型名不存在（404 model_not_found） | 能定位 | 10.7 |
| f7 | 数据库损坏（file is not a database） | 能定位 | 6.2 |
| f8 | 前端静态资源缺失（/assets/app.js 404） | 能定位 | 8.3 |

**判定率：8/8 能只靠日志定位（100%）。**

上一轮的两处「勉强」都来自同一个问题——日志只写了"失败了"，没写"失败在哪
一层"。补丁把这一层补上之后，f1 与 f3 的日志本身就能写出"因为什么"，不再
需要 `installation.json` 才能钉死原因。

仍保留的**本质边界**（不是缺陷，而是日志的物理上限）：

- f2 的日志能指认"8080 被独占"，但**端口占用者的进程名/PID 不在日志里**；
  这需要枚举系统套接字，属于诊断包 `ports.json` 的职责。
- 若上游返回的错误体**既没有 `code` 也没有 `type`**，日志只能给出
  `status` + `upstream_kind`（归类结果）；"具体新错误码是什么"上游没给，
  日志无法凭空创造。
- ACL 拒绝与安全软件拦截如果最终抛出的都是 `PermissionError`，日志能指认
  "写权限被拒"与具体阶段，但"是谁拒绝的"需要系统审计日志。

---

## f1 服务没起来（数据目录路径被同名普通文件挡住）

**造了什么**：把 `LIUHAO_DATA_DIR` 指向普通文件 `work\f1\data`（不是目录），
不设置 `LIUHAO_INSTANCE_DIR`，然后执行 `python -m src.gateway`。进程在建立
单实例锁阶段退出，服务未起来。

**日志里足够判断的原文行**（`evidence/f1/bootstrap.log`）：

```text
2026-11-07 17:47:38 evt=boot.instance_lock.failed path=<本地路径>/f1/data path_kind=file detail=数据目录路径存在但不是文件夹：<本地路径>/f1/data（请改名或移走它） exc="FileExistsError: [WinError 183] 当文件已存在时，无法创建该文件。: '<本地路径>/f1/data'" winerror=183 errno=17 free_bytes=198451802112 action="先按 detail 处理路径；再授予数据目录写权限 / 更换数据目录 / 清理磁盘 / 关闭安全软件拦截后重试"
```

**实际耗时**：5.8 秒

**判定**：能定位

**只靠这一行能写出**：

- 哪一步：建立单实例锁 / 创建数据目录（`evt=boot.instance_lock.failed`）；
- 因为什么：`path_kind=file` + `detail=数据目录路径存在但不是文件夹` +
  `winerror=183 errno=17`（183 = 目标已存在），异常原文也在同一行；
- 下一步：按 `detail` 改名/移走 `data`，或换 `LIUHAO_DATA_DIR`。

上一轮这里只有 `winerror=183`，读者得自己知道 183 的含义；现在把"路径类型
+ 人话结论 + 异常原文"直接写在同一行，不需要再查错误码表。

---

## f2 端口被占（8080 被独占监听）

**造了什么**：先用 `SO_EXCLUSIVEADDRUSE` 在 `127.0.0.1:8080` 独占监听，再以
`PORT=8080`、`HOST=127.0.0.1` 启动网关。

**日志里足够判断的原文行**：

```text
2026-11-07 17:47:45 | ERROR    | uvicorn.error | [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8080): [winerror 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次。
```

同目录的 `evt=startup.check` 还给出 `port=8080 port_bindable=0 port_state=occupied`。

**实际耗时**：7.1 秒

**判定**：能定位

**理由**：`evt=startup.check` 直接给出 `port=8080`、`port_bindable=0`、
`port_state=occupied`；紧随其后的 `WinError 10048` 与绑定地址再次确认。下一步
可明确写为"结束占用 8080 的进程，或更换 PORT"。日志没有占用进程 PID（见文末
本质边界），但不影响定位。

---

## f3 数据目录不可写（ACL 拒绝写，日志目录可写）

**造了什么**：`icacls work\f3\data /deny "Administrator:(OI)(CI)(W)"`；把
`LIUHAO_INSTANCE_DIR` 放到可写目录，`LIUHAO_LOG_DIR` 保持可写，然后启动网关。
演练后已 `icacls ... /remove:d Administrator` 恢复。

**日志里足够判断的原文行**：

```text
2026-11-07 17:47:52 | ERROR    | liuhao.boot | evt=boot.data_dir.unwritable path=<本地路径>/data path_kind=directory winerror=5 errno=13 free_bytes=None step=create_temp_file exc="PermissionError: [Errno 13] Permission denied: '<本地路径>/data/.liuhao-startup-probe-20940-1794102472840689700.tmp'" action="授予写权限 / 更换到可写目录 / 关闭安全软件拦截 / 清理磁盘后重启"
```

**实际耗时**：7.2 秒

**判定**：能定位

**只靠这一行能写出**：

- 哪一步：真实写探测停在 `create_temp_file`（`step=create_temp_file`）；
- 因为什么：`PermissionError`、`errno=13`、`winerror=5`、`path_kind=directory`；
- 下一步：给该目录授写权限，或换到可写目录；日志目录 `log_writable=1` 在同一
  时间的 `startup.check` 里，说明只需改数据目录。

**为什么上一轮会吞异常**：`tempfile.mkstemp` 在 Windows ACL 拒绝写的目录里会
卡住数十秒（系统过滤驱动），2 秒有界探测只能报"超时"，真实异常永远等不到。
本轮改成 `open("xb")` 走同一路径——同样的 ACL 拒绝会**立刻**抛
`PermissionError`（实测 0.001 秒），所以超时预算仍然保留，但正常路径不再超时。

**installation.json 里的对应证据**（真实生成的一次诊断包，ACL 拒绝写）：

```json
"data_dir_write_probe": {
  "path": "D:\\cache\\temp\\f3bundle\\data",
  "path_kind": "directory",
  "exists": true,
  "created": false,
  "writable": false,
  "method": "create-temp-file",
  "error": {
    "type": "PermissionError",
    "errno": 13,
    "winerror": 5,
    "stage": "write_temp_file",
    "message": "PermissionError: [Errno 13] Permission denied: '<本地路径>/data/.liuhao-diag-7760-1794102566322197500.tmp'"
  }
}
```

同一次诊断包的 `SUMMARY.txt` 也已经把人话结论写出来（日志目录可写时只点名数据目录，
不再把两个目录一起判死）：

```text
数据目录不可写：数据目录（PermissionError，错误码 5，停在 write_temp_file），程序无法保存数据或日志
```

`installation.json · logs_dir_write_probe.writable = true` 与
`startup_check.json · log_writable=True` 同步说明"只需要改数据目录"。

---

## f4 Key 无效（上游 401）

**造了什么**：用 stdlib `http.server` 起假上游，对 `/v1/chat/completions` 返回
HTTP 401（`invalid_api_key`）；在同一 Python 进程内构造真实 `OpenAIProvider`
（`base_url=http://127.0.0.1:<port>/v1`）与 `LiuHaoAssistant`，调用真实的
`assistant.chat("drill hello")`。

**日志里足够判断的原文行**：

```text
2026-11-07 17:48:05 | ERROR    | liuhao.liuhao | evt=llm.call.failed provider=openai model=drill-model host=127.0.0.1 port=60155 status=401 error_class=AuthenticationError errno=- dns_host=- upstream_code=invalid_api_key upstream_type=invalid_request_error upstream_kind=invalid_key key_fp=sha256:6fa9a332 timeout_s=10.0 timeout_source=default duration_ms=8798 attempt=1 request_id=bfbb5d7671da48bf
```

**实际耗时**：11.3 秒

**判定**：能定位

**理由**：`status=401`、`upstream_code=invalid_api_key`、`upstream_kind=invalid_key`
三处一致，事件行本身就能写"调模型时鉴权失败、Key 无效"，不再依赖 traceback。

---

## f5 Key 额度耗尽（上游 402 + insufficient_quota）

**造了什么**：同一假上游改为返回 HTTP 402，错误体 `type/code=insufficient_quota`、
消息含 "You exceeded your current quota"；仍走真实 `LiuHaoAssistant.chat`。

**日志里足够判断的原文行**：

```text
2026-11-07 17:48:17 | ERROR    | liuhao.liuhao | evt=llm.call.failed provider=openai model=drill-model host=127.0.0.1 port=60242 status=402 error_class=HTTP402 errno=- dns_host=- upstream_code=insufficient_quota upstream_type=insufficient_quota upstream_kind=quota_exhausted key_fp=sha256:6fa9a332 timeout_s=10.0 timeout_source=default duration_ms=8873 attempt=1 request_id=ce8486778c78474a
```

**实际耗时**：11.6 秒

**判定**：能定位

**理由**：事件行把上游 `code`/`type` 原样记下来了
（`upstream_code=insufficient_quota`），并附归类 `upstream_kind=quota_exhausted`；
`status=402` 不再是唯一线索。下一步明确是充值/提升额度或换有额度的 Key。

---

## f6 模型名不存在（上游 404 + model_not_found）

**造了什么**：同一假上游改为返回 HTTP 404，错误体 `code=model_not_found`、消息
"The model 'drill-model' does not exist"；仍走真实 `LiuHaoAssistant.chat`。

**日志里足够判断的原文行**：

```text
2026-11-07 17:48:28 | ERROR    | liuhao.liuhao | evt=llm.call.failed provider=openai model=drill-model host=127.0.0.1 port=60307 status=404 error_class=HTTP404 errno=- dns_host=- upstream_code=model_not_found upstream_type=invalid_request_error upstream_kind=model_not_found key_fp=sha256:6fa9a332 timeout_s=10.0 timeout_source=default duration_ms=8597 attempt=1 request_id=127044270cb145de
```

**实际耗时**：10.7 秒

**判定**：能定位

**理由**：`upstream_code=model_not_found` 与 `model=drill-model` 直接指认
"模型名不存在"；即使上游 404 其实是路由/路径错误，`upstream_code` 也会原样
显示成别的值，靠 `status` 猜的日子结束了。

---

## f7 数据库损坏（audit.db 写入非 SQLite 字节）

**造了什么**：向 `work\f7\data\audit.db` 写入 `b"not a sqlite database\n" * 100`，
设置 `AUDIT_DB_PATH` 指向它，再启动网关。

**日志里足够判断的原文行**：

```text
2026-11-07 17:48:34 | ERROR    | liuhao.boot | evt=db.open.failed subsystem=audit path=data/audit.db sqlite_msg="DatabaseError: file is not a database" winerror=- errno=- action="degraded: 审计链停止写入检查点；修复数据目录写权限、隔离/重建损坏库或配置 <环境变量> 后重启"
```

**实际耗时**：6.2 秒

**判定**：能定位

**理由**：事件直接给出 `subsystem=audit`、`path=data/audit.db`、
`DatabaseError: file is not a database`，能写"打开审计库失败、库文件损坏"；
下一步隔离/重建损坏库，修复前审计检查点停止。

---

## f8 前端静态资源缺失（dist 只有 index.html，/assets/app.js 不存在）

**造了什么**：创建 `work\f8\dist\index.html`（引用 `/assets/app.js`），目录中
没有 `assets\`；设置 `LIUHAO_CONSOLE_DIST` 指向该 dist，启动网关后请求
`/assets/app.js`。

**日志里足够判断的原文行**：

```text
2026-11-07 17:48:43 | WARNING  | src.gateway.console_static | evt=console.asset.missing path=/assets/app.js method=GET action="缺少构建产物：在 apps/console/console 执行 npm run build 后重新部署（或检查 <环境变量>）"
```

同目录的 `evt=startup.check` 给出 `console_dist=<本地路径>/work/f8/dist console_index=1`。

**实际耗时**：8.3 秒

**判定**：能定位

**理由**：警告直接指出缺失的请求路径 `/assets/app.js` 和 GET 方法，配合
`startup.check` 里的 `console_dist` 可定位到是哪个构建目录缺资源；下一步按
action 重新构建并部署，或检查静态资源目录配置。

---

## 三处改动（日志原文对照）

### 1. f3：不再吞异常，超时也带"最后一步"

改动前：

```text
evt=boot.data_dir.unwritable path=<本地路径>/data winerror=- errno=- free_bytes=None action="授予写权限 / ..."
evt=startup.degraded subsystem=data_dir path=<本地路径>/data sqlite_msg="probe did not finish within 2.0s" action="..."
```

改动后（真实异常立刻落盘）：

```text
evt=boot.data_dir.unwritable path=<本地路径>/data path_kind=directory winerror=5 errno=13 free_bytes=None step=create_temp_file exc="PermissionError: [Errno 13] Permission denied: '<本地路径>/data/.liuhao-startup-probe-20940-1794102472840689700.tmp'" action="授予写权限 / 更换到可写目录 / 关闭安全软件拦截 / 清理磁盘后重启"
```

实现要点：写探测改为 `open(path, "xb")`（不再用 `tempfile.mkstemp`）；每个阶段
（`resolve_path` → `create_directory` → `create_temp_file` → `write_byte` →
`remove_temp_file`）都有打点；若真的超过 2 秒，超时消息带
`last step` 与 `last error`，且工作线程结束后补一条
`evt=boot.<label>.probe_late` 记录真实异常与总耗时。

### 2. f1：直接写"路径存在但不是文件夹"

改动前：

```text
evt=boot.instance_lock.failed path=D:\cache\temp\liuhao-drill-1108\work\f1\data winerror=183 errno=17 free_bytes=199834759168 action="..."
```

改动后：

```text
evt=boot.instance_lock.failed path=<本地路径>/f1/data path_kind=file detail=数据目录路径存在但不是文件夹：<本地路径>/f1/data（请改名或移走它） exc="FileExistsError: [WinError 183] 当文件已存在时，无法创建该文件。: '<本地路径>/f1/data'" winerror=183 errno=17 free_bytes=198451802112 action="先按 detail 处理路径；..."
```

实现要点：启动失败时先判定目标路径类型（`file` / `missing` / `other` /
`directory`），按类型给出人话 `detail`，再附异常原文；控制台打印的是
`detail` 而不是原始错误码。

### 3. f5/f6：上游 `code` / `type` 原样进事件

改动前：

```text
evt=llm.call.failed ... status=402 error_class=HTTP402 ... （只有 402，靠状态码猜）
evt=llm.call.failed ... status=404 error_class=HTTP404 ... （只有 404，靠状态码猜）
```

改动后：

```text
evt=llm.call.failed ... status=402 error_class=HTTP402 ... upstream_code=insufficient_quota upstream_type=insufficient_quota upstream_kind=quota_exhausted ...
evt=llm.call.failed ... status=404 error_class=HTTP404 ... upstream_code=model_not_found upstream_type=invalid_request_error upstream_kind=model_not_found ...
```

实现要点：`ProviderHTTPError` 在**脱敏前**保存上游错误体的 `code` / `type`
（只收短标识符，绝不带正文）；日志事件同时输出原始 `upstream_code` /
`upstream_type` 与归类 `upstream_kind`，以后上游新增错误码不需要改代码也能被
日志认出来。

---

## 顺带修掉的真实缺陷（不计入日志改进）

### 启动探测在 ACL 拒绝目录里曾卡数十秒

**症状**：数据目录被 ACL 拒绝写时，启动探测（`probe_writable` /
`probe_directory_writable`）在 `tempfile.mkstemp` 上卡住数十秒才返回；2 秒的有界
预算因此总是先到点，界面与日志只能报 **"probe did not finish within 2.0s"**。
用户侧表现就是"启动先卡几十秒，然后只得到一句超时"，真实原因（`PermissionError` /
`winerror=5`）永远等不到。

**根因**：`tempfile.mkstemp` 在 Windows 上会做多次探测性创建与改名重试，遇到 ACL
拒绝（或被安全软件过滤驱动拦截）时，每次失败都落进重试路径，累计到数十秒。

**修复**：写探测改为一次 `open(name, "xb")` → `write` → `remove`，不再经过 `tempfile`。

**实测对比**（同一台机器、同一 ACL 拒绝目录，`icacls /deny "...:(OI)(CI)(W)"`）：

| 实现 | 抛异常耗时 | 得到的错误 |
|---|---|---|
| `tempfile.mkstemp` | 数十秒，且被 2 秒预算截断 | 无真实异常，只有 `probe did not finish within 2.0s` |
| `open(path, "xb")` | **0.001 秒** | `PermissionError: [Errno 13] Permission denied`（`winerror=5`，`step=create_temp_file`） |

**影响面**：这是用户侧真实存在的启动卡顿，不只是日志好不好看的问题。发版说明建议
单列一条："修复数据目录无写权限时启动探测卡顿数十秒的问题，并直接提示真实原因。"

**保留的兜底**：2 秒有界预算不变；若线程确实超时，消息带 `last step` / `last error`，
线程结束后再补一条 `evt=boot.<label>.probe_late`，记录真实异常与总耗时。

### 同批修正（探测对象与结论口径，同样不属于日志美化）

- `installation.json` 的日志目录曾写死 `<数据目录>/logs`，`LIUHAO_LOG_DIR` 生效时
  会探测一个**没在用**的目录。已改为与运行期同一契约
  （`resolve_log_file("logs/liuhao.log").parent`），结论才对得上实际写入位置。
- `SUMMARY.txt` 原来不管哪处失败都写"数据目录、日志目录不可写"。已改为只点名真正
  失败的目录（仅数据目录失败时写"数据目录不可写：…"），避免误报。

---

## 路径折叠对照（脱敏后还能不能指到用户自己的文件）

SUMMARY 与日志里的本机绝对路径会折成本地占位路径。**命中当前实例的数据/日志
目录**时用 `<数据目录>` / `<日志目录>`，其余路径用 `<本地路径>/<最后两段>`。
对照示例（假设该机器的数据目录是 `D:\LiuHaoData\data`，日志在其下 `logs`）：

| 折叠前（用户机器上的真实路径） | 折叠后（SUMMARY / 日志里的写法） | 用户怎么打开 |
|---|---|---|
| `D:\LiuHaoData\data\logs\liuhao.log` | `<日志目录>/liuhao.log` | 打开日志目录下的 `liuhao.log` |
| `D:\LiuHaoData\data\audit.db` | `<数据目录>/audit.db` | 打开数据目录下的 `audit.db` |
| `D:\LiuHaoData\data` | `<数据目录>` | 数据目录本身 |
| `C:\Users\Administrator\AppData\Local\LiuHao\data\logs\error.log`（数据目录为默认位置时） | `<日志目录>/error.log` | 打开日志目录下的 `error.log` |
| `D:\Other\project\build\out.txt`（不在数据/日志目录内） | `<本地路径>/build/out.txt` | 按最后两段在自己的磁盘上查找 |

保留**最后两段**而不是只留文件名，原因是 `data/audit.db` 与 `logs/audit.db`
折叠成同一个 `audit.db` 会让"打开哪个文件"失去意义；能对上当前实例时直接给出
`<数据目录>` / `<日志目录>`，连"最后两段"都不需要用户再猜。

`<数据目录>` / `<日志目录>` 指向哪里，随诊断包 README 一起说明：

```text
`<数据目录>` / `<日志目录>` 就是你机器上正在使用的两个目录：默认
`%LOCALAPPDATA%\LiuHao\data`（日志在其下的 `logs` 子目录），安装时改过目录的
以「关于」页显示的数据目录为准。例如 `<日志目录>/liuhao.log` 就是该目录下真实
存在的 `liuhao.log`，可以直接打开。其它路径写成 `<本地路径>/<最后两段>`，
按最后两段在你自己的磁盘上查找即可；相对写法（如 `logs/error.log`）同理。
```

结论：折叠去掉了盘符、用户名和中间层级，但**保留了到哪里找**（数据/日志目录
占位符或最后两段）；配合 README 的默认位置说明与「关于 → 数据目录」，用户仍能
直接打开自己的文件。`https://...` 这类 URL 不会被折叠（正则排除 `http://` /
`https://`），动作文本里的相对路径（`logs/liuhao.log`）也原样保留。
