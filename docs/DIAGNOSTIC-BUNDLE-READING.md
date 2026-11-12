# 读包指南：拿到诊断 zip 后怎么 30 秒说出结论

这份指南写给两类人：**拿到用户发来的 zip 的技术支持**，以及**想自己看懂一半的用户**。
不需要会写代码，只需要会用记事本（或 WinRAR / 7-Zip）打开 zip。

诊断包由 `python scripts/start_liuhao.py --diagnose` 生成，
**服务没有启动也能生成**，因为它只做离线检查（不通过 HTTP、不依赖网关）。

---

## 一、30 秒顺序（照着做就行）

1. **解开 zip，打开 `SUMMARY.txt`。**
   第一行就是一句人话结论，后面是 3–5 条依据，最后一行是“下一步”。
   80% 的工单看完这一份就够了，不用再往下翻。
2. **结论里点名了哪个文件，就只打开那个文件。**
   例如 `结论说"数据目录/日志目录不可写"` → 打开 `startup_check.json` 看
   `data_writable` / `log_writable`；结论说"静态资源缺失" → 打开 `static_assets.json`。
3. **只有当 `SUMMARY.txt` 第一行写着“无法确定”时**，才需要去翻 `logs/liuhao.log` 与
   `logs/error.log` 的末尾——这表示本地一切正常，问题发生在模型调用或使用过程里。

`SUMMARY.txt` 里“无法确定”的那句话一定会指名**看哪个文件**，不会只说“请检查日志”。

---

## 二、每个文件是什么

按“先看谁”排序。**加粗的四个是必看**，其余是补充材料。

| 文件 | 是什么 | 什么时候看 |
| --- | --- | --- |
| **`SUMMARY.txt`** | 一句结论 + 3–5 条依据 + **唯一一个**「下一步」动作（其余动作降级到依据）；末行 `# 结论来源：<check_id>` 供自检交叉校验 | 永远第一个看 |
| `README.txt` | 包内容说明（同一份说明，随包走） | 想确认包里有什么 |
| `REDACTED-MEMBERS.txt` | **只在有文件被剔除时出现**：列出被剔除的文件名与命中条数（不含密钥内容） | 发现包里“少了”文件时先看这个 |
| `scan_rule_hit.json` | **只在有文件被剔除时出现**：哪条规则在哪个文件上命中几次（不含任何命中内容），用于判断是用户数据还是扫描规则误判 | 怀疑“是不是误报” |
| **`startup_check.json`** | 离线启动自检：数据/日志目录是否可写、端口能不能绑、每个库能不能打开、`console/index.html` 是否存在、模型与 Key 指纹，以及 `actions`（该做什么） | 结论提到“不可写 / 端口 / 库 / 白屏” |
| **`diagnosis.json`** | 9 条固定检查的判定（`detected` / `clear` / `inconclusive`），每条带证据定位与 `next_step` | 想确认“到底查了哪几项” |
| **`logs/liuhao.log.txt`** | 服务主日志的尾部（正常与失败都在这里） | 结论说“无法确定”时看最后 |
| `logs/issue.log.txt` | 源文件是 `error.log` 的尾部（包内改名，避免“error”字样） | Key / 网络 / 模型名问题 |
| `logs/launcher.log.txt` | 启动器日志：窗口拉起、端口选择、退出码 | 窗口一闪就没了 |
| `bootstrap.log.txt` | **数据目录和日志目录都写不进去时**唯一留下的记录（放在 `%LOCALAPPDATA%\LiuHao\logs`） | 结论说“不可写”，或日志文件是空的 |
| `recent_events.txt` | 最近 20 条 `evt=` 结构化事件（跨日志汇总） | 想快速看“最近发生了什么” |
| `static_assets.json` | `index.html` 引用的 JS/CSS 是否存在、缺失清单 | 页面白屏 |
| `installation.json` | 程序目录完整性（`src` / `runtime` / `console` 是否齐）、两个目录的写入探测结果 | 安装被删了一半 |
| `db_check.json` | 每个 SQLite 库的只读 `quick_check` 结论（**不含任何行内容**） | 审计/会话数据坏了 |
| `ports.json` | 端口监听状态与占用进程（进程名 + PID） | 端口被占用 |
| `processes.json` | 与贾维斯相关的进程清单 | 判断是不是有僵尸进程 |
| `ready_probe.json` | 对 `/v1/health` 的探测结果（服务活着时才有内容） | 不确定服务到底起没起 |
| `model_health.json` | 模型端点的 DNS/TCP 探测与日志里的鉴权/网络信号 | Key / 连不上 |
| `providers.json` | 当前生效的 provider / model / base_url（**不含 Key**） | 模型名填错 |
| `data_inventory.json` | 数据目录顶层清单与各目录占用 | 判断“是不是数据太大” |
| `log_retention.json` | 日志保留/清理策略的执行情况 | 日志被清理过，找不到历史 |
| `environment.json` | 系统、Python、区域、编码 | 环境类怪问题 |
| `about.json` | 版本号、构建号、安装形态（安装版 / 源码） | 报工单先填版本 |
| `dependencies.json` | 关键依赖版本 | 升级后出问题 |
| `manifest.json` | 每个文件的 sha256、大小、脱敏后的版本号 | 校验包是否完整、是否被改过 |
| `service.json` | 桌面壳认为的端口 / 可达性 / 期望端口 | 桌面壳与服务不一致 |

包里**不含**密钥、不含数据库内容、不含聊天记录；路径中的用户名显示为 `<用户>`。

---

## 二·补充：包里少了几个文件意味着什么

如果解压后看到 `REDACTED-MEMBERS.txt`，说明导出前的扫描发现**个别成员仍含明文凭据**
（常见是旧日志里有一条脱敏只遮了一半的 Key）。程序把它**剔除**了，其余文件照常打包——
这是保护用户的密钥，不是软件故障。

- **会不会影响技术支持判断？** 常见问题（目录不可写、磁盘满、程序没装全、数据库损坏、
  端口被占、Key 无效/额度耗尽、模型连不上）的结论来自 `SUMMARY.txt`、`startup_check.json`、
  `installation.json`、`ports.json`、`db_check.json`、`model_health.json` 等文件，这些不受影响。
  唯一受影响的是“需要逐条看那份日志原文”的场景；`REDACTED-MEMBERS.txt` 会写明少了哪份，
  支持可以直接说明还需要它。
- **需不需要额外提供什么？** 一般不需要，先把 zip 发出去。若支持明确要那份日志，
  让用户**先退出贾维斯**，把数据目录 `logs\` 下的 `liuhao.log`、`error.log`、
  `desktop-gateway.log` 都**改名备份**（把后缀 `.log` 改成 `.log.bak-日期`，
  例如 `liuhao.log` → `liuhao.log.bak-20261108`）后再导出一次——**不要删掉**，
  删除等于销毁排查证据。改名后的文件不会再被收进诊断包。
  `.log.1` 这类轮转文件：网关（控制台）导出只收 `*.log`，本来就不会收；
  桌面「生成诊断包」会收，建议一并改名。
- **用户自查（两步）**：① 打开 `REDACTED-MEMBERS.txt`；② **里面没有条目，就说明所有文件
  都在包里**。有条目时也只写“文件名 + 命中条数”，不会显示密钥本身，可以直接连同 zip 一起发。
- **怎么确认包是完整的？** 打开 `README.txt` 的「本次是否完整」一段：里面写着
  “本次包含 N 个文件，未包含 M 个（原因）”。**N + M 就是本次计划收集的文件总数**，
  N 应当等于解压后看到的文件数（含 `README.txt` 与 `manifest.json`），不用自己数。
  想核对规则误报时再看 `scan_rule_hit.json`：它只写规则名、文件名与次数，不含任何命中内容。

---

## 三、`SUMMARY.txt` 的结论是怎么算出来的

结论只允许从下面这些字段推出，判不出来就写“无法确定”：

| 结论（第一行大意） | 依据字段（原样） |
| --- | --- |
| 数据目录/日志目录不可写 | `startup_check.json → data_writable=false` / `log_writable=false`；`installation.json → *_write_probe.writable=false`（带 `errno` / `winerror`）；`bootstrap.log.txt` 的 `evt=boot.log_dir.unwritable` |
| 磁盘空间不足 | `startup_check.json → data_free_bytes` 低于 64 MB；或写入探测报 `errno 28` / `winerror 112` |
| 程序目录不完整（白屏） | `static_assets.json → missing` 非空；`installation.json → integrity.missing` 非空 |
| 数据库损坏 | `db_check.json → databases[].result=failed` |
| 网关没有启动：端口被占 | `ports.json → port_conflicts[].owner`（进程名 + PID）；`startup_check.json → port_bindable=false, port_state=serving/occupied` |
| 网关没有启动：没有响应 | `ready_probe.json` 没有网关响应、`processes.json` 没有网关进程 |
| 模型 Key 无效 / 额度耗尽 | `model_health.json → log_signals` 里的 `status=401` / `402`（`kind=invalid_key` / `quota_exhausted`） |
| 模型服务连不上 | `model_health.json → endpoint_verdict=unreachable/partial`；日志里的 `10061` / `10060` / `11001` |
| 无法确定 | 以上全部正常，或关键项是 `inconclusive`；此时会指名看 `logs/liuhao.log` 与 `logs/error.log` |

优先级从上到下：**目录/磁盘 > 程序完整性 > 数据库 > 端口 > 模型**。
理由是先修“根本写不了/根本没装全”，否则后面的结论都会变。

---

## 四、常见组合 → 故障（不懂代码也能对照）

1. **`data_writable=false` + `log_writable=false` + 主日志几乎是空的**
   → 目录被 ACL/安全软件挡住，日志自己都写不出来。
   证据在 `bootstrap.log.txt`（`evt=boot.data_dir.unwritable` / `evt=boot.log_dir.unwritable`）。
   处理：换到 `%LOCALAPPDATA%\LiuHao\data` 或让管理员给写权限。

2. **`data_free_bytes` 很小（< 64 MB）+ 写入探测报 `errno 28`**
   → 磁盘满。处理：清盘或换卷，重启。

3. **`static_assets.json → missing` 有值，`index_present=true`**
   → 入口还在、它引用的 JS/CSS 被删了 → 页面白屏。
   处理：用同一个安装包覆盖安装，别只拷可执行文件。

4. **`installation.json → integrity.missing` 包含 `src` 或 `runtime`**
   → 程序被删了一半，网关根本起不来。
   处理：覆盖安装。

5. **`db_check.json` 有 `result=failed`（`file is not a database`）**
   → 某个库损坏。日志里同时有 `evt=db.open.failed` 与 `AUDIT CHECKPOINTER NOT STARTED`。
   处理：**先备份**数据目录，再把损坏文件改名/移走，重启自动重建空库。

6. **`port_conflicts` 里有 `owner.process` / `pid`，`port_bindable=false`**
   → 端口被别的程序占着（Windows 上非独占绑定也会显示 `port_bindable=true`，
   所以要看 `port_state=serving` 与 `owner`）。
   处理：结束该进程，或换端口。

7. **`logs/issue.log.txt`（源文件 `error.log`）里有 `evt=llm.call.failed ... status=401 key_fp=sha256:xxxx`**
   → Key 错/被撤销；`status=402 kind=quota_exhausted` → 额度用完（去充值/换 Key）。
   `key_fp` 只是指纹，用来确认“换没换 Key”。

8. **`evt=llm.call.failed ... errno=10061 / 10060 / 11001`**
   → `10061` 拒绝连接（地址/端口错或服务没开）；`10060` 超时（网络/代理/防火墙）；
   `11001` 域名解析失败（DNS 或域名拼错）。看 `host=` / `dns_host=` 就知道连的是哪。

9. **`SUMMARY.txt` 说“无法确定”，但 `logs/issue.log.txt` 有 `HTTP 404 model_not_found`**
   → 模型名写错。处理：打开「系统设置 → 模型管理」，把模型名改成服务商列出的名字。

10. **什么都不缺，但 `logs/liuhao.log.txt` 最新一条 `evt=startup.check` 之后就没动静**
    → 服务卡在某一步（常见是安全软件扫描）。看 `logs/launcher.log.txt` 尾部与退出码。

---

## 五、三种极端场景的实际输出（2026-11-08 演练）

演练用临时目录造故障，结束后已清理；下面是原样输出。

### ① 数据目录和日志目录都不可写（真实 ACL 拒绝）

```
数据目录、日志目录不可写：数据目录（PermissionError，错误码 5，停在 write_temp_file）、日志目录（PermissionError，错误码 5，停在 create_directory），程序无法保存数据或日志

依据：
- startup_check.json · data_writable=False, log_writable=False, port=8080, port_bindable=True, port_state=free, db_open=True, console_index=True
- bootstrap.log.txt · 最后一条 = 2026-11-07 16:20:15 evt=boot.log_dir.unwritable path=C:\Users\<用户>\AppData\Local\Temp\...\data\logs winerror=5 errno=13 action="授予写权限 / 更换到…
- installation.json · data_dir_write_probe.writable = False（PermissionError，winerror=5，errno=13，stage=write_temp_file）
- installation.json · logs_dir_write_probe.writable = False（PermissionError，winerror=5，errno=13，stage=create_directory）
- 其余动作（按需再做）：让管理员给当前目录授予写权限（右键属性 → 安全 → 编辑）；检查安全软件是否拦截写入；日志目录不可写时把它同样换到可写位置

下一步：把数据目录改到 %LOCALAPPDATA%\LiuHao\data，然后重启贾维斯。

# 结论来源：directory_not_writable
```

### ② 安装目录被删了一半（缺静态资源）

```
程序目录不完整：驾驶舱静态资源缺失 2 个（assets/app-123.js、assets/app-123.css），打开会白屏

依据：
- startup_check.json · data_writable=True, log_writable=True, port=8080, port_bindable=True, port_state=free, db_open=True, console_index=True, data_free_bytes=12447567872
- static_assets.json · missing = ["assets/app-123.js", "assets/app-123.css"]
- diagnosis.json · headline = 发现 3 个可定位的问题，建议先处理：静态资源缺失（白屏）
- diagnosis.json · 共 9 项检查：检出 3 项、无法判定 2 项、其余正常
- 其余动作（按需再做）：源码运行时重新构建驾驶舱（console 目录下执行 npm run build）

下一步：用同一个安装包覆盖安装一次（不要只复制可执行文件）。

# 结论来源：console_assets_missing
```

### ③ 磁盘满

```
磁盘空间不足：数据目录所在卷只剩 0 MB（低于 64 MB），新建文件随时会失败

依据：
- startup_check.json · data_writable=True, log_writable=True, port=8080, port_bindable=True, port_state=free, db_open=True, console_index=True, data_free_bytes=0
- startup_check.json · data_free_bytes = 0
- diagnosis.json · headline = 发现 2 个可定位的问题，建议先处理：磁盘剩余空间不足
- diagnosis.json · 共 9 项检查：检出 2 项、无法判定 2 项、其余正常
- 其余动作（按需再做）：若无法清理，把数据目录换到其它剩余空间充足的卷后重启

下一步：清理磁盘上数据目录所在卷的可用空间，然后重启贾维斯。

# 结论来源：disk_space_low
```

> 格式说明：`下一步` 永远只有**一个**动作，其余动作降级到「依据」里的
> 「其余动作（按需再做）」一行；末行 `# 结论来源：<check_id>` 是机器可读标记，
> `verify_bundle` 用它和 `diagnosis.json` 的首个 detected 检查比对，
> 两份文件各说各话时这个包会被判失败。

> 说明（诚实标注）：真实“磁盘写满”需要先把用户盘写满，不适合在用户机器上做。
> 上述③使用 `LIUHAO_DIAG_FREE_BYTES=0` 这一**只改读数**的演练开关注入剩余空间；
> 结论仍然由 `startup_check.json → data_free_bytes` 推出，不存在“字段正常、结论说满”的错位。
> 真实 ENOSPC（`errno 28` / `winerror 112`）走同一条判定分支，并会在结论后追加“写入已失败”。

---

## 六、用户自己能做的三步

1. 打不开就先导包：程序目录命令行执行
   `runtime\python.exe scripts\start_liuhao.py --diagnose`（源码目录用 `.venv\Scripts\python.exe`）。
   不需要服务在运行，最后会打印 `[OK] 诊断包已生成：<路径>` 与 `[结论] <第一行>`。
2. 打开包里的 `SUMMARY.txt`，照“下一步”那一行做。
3. 做完还是不行，把整个 zip 发给技术支持（不含密钥、不含聊天内容）。

自查包里有没有密钥（两步）：① 打开 `REDACTED-MEMBERS.txt`；② 没有条目 → 所有文件都在包里；
有条目 → 只列出“文件名 + 命中条数”，不会显示密钥，可以直接连同 zip 一起发。
