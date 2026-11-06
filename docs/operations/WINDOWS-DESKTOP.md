# Windows 桌面集成（托盘 / 自启 / 诊断包）

> 系统层支撑，不含业务逻辑。实现位于 `src/desktop/**` 与 `scripts/liuhao_desktop.py`；
> 不修改网关、知识库、自动化、情报、驾驶舱与安装脚本。

## 1. 模块与入口

| 模块 | 职责 |
|---|---|
| `src/desktop/tray.py` | pywin32 系统托盘（`Shell_NotifyIcon`，**不用 LGPL 的 pystray**） |
| `src/desktop/service.py` | 网关子进程启动 / 停止（托盘与主服务的唯一边界） |
| `src/desktop/_runner.py` | 网关子进程入口：把 stdin 的 `shutdown` 转成 uvicorn 优雅收尾 |
| `src/desktop/app.py` | 状态窗口 + 托盘 + 网关装配；关闭窗口语义 |
| `src/desktop/autostart.py` | `HKCU\...\Run` 自启登记（默认关闭，不写 HKLM） |
| `src/desktop/firewall.py` | 防火墙 / 局域网可达性**只读**检测 + 用户自助指引 |
| `src/desktop/diagnostics.py` | 一键诊断 zip（白名单 + 强制脱敏 + 写入后自检） |
| `src/desktop/about.py` | 版本 / 构建时间 / 数据目录读取入口（关于页数据源） |
| `scripts/liuhao_desktop.py` | `pythonw.exe` 自启入口（显式设置仓库根到 `sys.path`） |

```powershell
# 日常使用（会启动网关、显示状态窗口、常驻托盘）
.venv\Scripts\python.exe -m src.desktop

# 只驻留托盘（开机自启用）
.venv\Scripts\python.exe -m src.desktop app --hidden

# 其余能力
.venv\Scripts\python.exe -m src.desktop about
.venv\Scripts\python.exe -m src.desktop autostart status
.venv\Scripts\python.exe -m src.desktop diagnose --verify
.venv\Scripts\python.exe -m src.desktop firewall --port 8080
.venv\Scripts\python.exe -m src.desktop status
.venv\Scripts\python.exe -m src.desktop tray-demo --seconds 120   # 手工验证右键菜单
```

## 2. 系统托盘与停止语义

菜单（顺序固定）：

1. 打开控制台
2. 查看状态
3. **停止服务（仅停网关，托盘保留）**
4. **退出（停止网关并关闭托盘）**

标签里写清楚两者的区别：点「停止服务」不会关掉窗口或托盘，随时可以再启动；
只有「退出」才会结束整个桌面程序。

左键单击 = 显示/隐藏状态窗口；左键双击 = 打开控制台；右键 = 菜单。

**图标三态**（用户一眼看得出）：

| 图标 | 含义 |
|---|---|
| 绿色圆点 | 运行中（网关健康检查通过） |
| 红色圆点 | 有错误（启动失败，或进程在但健康检查失败） |
| 灰色圆点 | 已停止 |

图标由 `tray.write_state_icon` 现场生成 16×16 ICO（纯 stdlib 写 ICONDIR + 32bpp DIB），
不引入 Pillow，也不往仓库塞二进制图片；生成失败时退回 Windows 内置图标。

**消息循环与主服务的关系**

* 托盘图标属于创建它的线程，因此 `SystemTray` 在**独立线程**里注册窗口类、
  创建隐藏窗口、`Shell_NotifyIcon(NIM_ADD)` 并泵 Win32 消息。
* 主服务是 `uvicorn` **独立子进程**；状态窗口是主线程的 tkinter。
  三者互不阻塞：托盘线程不碰 tkinter，只把动作放进队列，由 UI 线程执行。
* `stop()` 可从任意线程调用：置停止标志 + `PostMessage(WM_CLOSE)` 唤醒循环，
  循环退出前在同一线程 `NIM_DELETE` 并销毁窗口，不遗留图标。

### 2.1 停止 = 先优雅收尾，超时才强杀

Windows 的 `TerminateProcess` 没有可捕获的退出信号，所以停止走**父子进程
stdin 管道协议**：

1. 桌面壳往网关子进程 stdin 写一行 `shutdown`；
2. 子进程入口 `src.desktop._runner` 收到后置 `uvicorn.Server.should_exit = True`；
3. uvicorn 关闭监听 socket（**新请求连接被拒 = 不再 ready**）→ 等在途请求结束
   （子进程侧上限 `GRACEFUL_INSIDE = 8s`）→ 执行 lifespan shutdown
   （网关在此 flush、关库、停后台线程）→ 打印 `Application shutdown complete`
   → 进程自行退出；
4. 父进程最多等 `GRACEFUL_TIMEOUT = 10s`；**超时才 `kill()`**，标记 `forced=True`；
5. 判定口径：
   * `graceful=True` 要求：进程自然退出 + 日志出现 shutdown marker + **没有任务被取消**；
   * 日志出现 `timeout graceful shutdown exceeded`（uvicorn 取消了超过 8s 的在途任务）
     时标记 `inflight_cancelled`，**不算优雅**，界面如实写「在途任务因超时被取消」。

**展示与记录（不静默降级）**

* 窗口「最近停止」一行给出结论：服务本来未运行 / 优雅停止（Xs）/
  已停止但在途任务被取消 / 超时强杀（已记录日志）/ 进程已退出但未确认优雅收尾；
* 常驻时托盘气泡同步提示；「退出」路径因为托盘马上移除，改用 messagebox 警告框；
* 日志双份：`logs/desktop.log`（判定与原因）、`logs/desktop-gateway.log`（uvicorn 原始输出）；
* 退出时若发生强杀或任务取消，警告框直接列出这两个日志路径。

**已知边界**

* 停止后新请求是「连接被拒绝」而不是 HTTP 503 —— uvicorn 已关闭监听 socket，
  无法再回响应；对调用方语义等同 not-ready。
* 在途请求超过 8s 仍未结束 → uvicorn 取消它并打 WARNING；系统会如实标注
  「有任务被取消」，不会假装优雅。
* 父进程异常消失（崩溃/被杀）→ 子进程 stdin EOF → 同样走优雅收尾，不留孤儿网关。

### 2.2 气泡通知

| 事件 | 通知 |
|---|---|
| 服务就绪 | 「鎏灏已就绪」+ 实际访问地址 |
| 启动失败 | 「鎏灏启动失败」（警告图标，附原因摘要） |
| 停止（优雅） | 「服务已停止」+ 用时 |
| 停止（有任务被取消） | 「服务已停止（有任务被取消）」（警告） |
| 停止（超时强杀） | 「服务已强制停止」（警告） |

限制：Windows 的专注助手、通知开关、通知历史策略都可能吞掉气泡。代码层只能
确认 `Shell_NotifyIcon(NIM_INFO)` 提交成功，**无法保证肉眼可见**；测试断言的是
提交成功。

「开机自启开启 / 关闭」事件不做托盘气泡：它由 CLI（`-m src.desktop autostart ...`）
在**另一个进程**里执行，桌面壳没有 IPC 通道接收；CLI 会把结果直接打印在终端，
托盘运行期间的状态变化以窗口与图标为准。

### 2.3 右键菜单手工验证（无法自动化）

`TrackPopupMenu` 会阻塞等待真人点击，CI 里没有真实鼠标，因此**无法自动化**。
照着下面做一遍即可：

1. 打开 PowerShell，执行
   `.\venv\Scripts\python.exe -m src.desktop tray-demo --seconds 120`
2. 到任务栏通知区找到新图标（可能折叠在 `^` 里）；悬停确认 tooltip。
3. 右键展开菜单，核对四项文字与顺序（含「仅停网关」「关闭托盘」说明）。
4. 依次点「查看状态」「停止服务（仅停网关，托盘保留）」「退出（停止网关并关闭托盘）」，
   命令行会打印每次选中的 action；`tray-demo` 不会真的启停网关。
5. 观察图标颜色：`tray-demo` 会每 3 秒自动循环绿/红/灰三态（命令行同步打印
   `[tray] state=...`）。
6. 截图保存位置（人工放这里，便于后续对照）：
   * 右键菜单：`docs/operations/assets/tray-menu.png`
   * 三态图标：`docs/operations/assets/tray-states.png`

### 2.4 窗口关闭行为（明确、不留半截）

* 点标题栏关闭按钮 = **最小化到托盘**，网关继续运行
  （`WM_DELETE_WINDOW` 绑定到 `withdraw()`，不是退出）。
* 真正退出只有两条路：托盘菜单「退出」、窗口内「退出程序」。
* 「停止服务」只停网关子进程，窗口与托盘仍驻留，方便排障或再次启动。

### 2.5 已知限制

* 真实鼠标点击菜单项未做自动化（见 2.3 的手工步骤）。
* 气泡是否弹出取决于系统通知设置（见 2.2）。
* 托盘需要任务栏通知区可用；远程会话/精简系统里 `Shell_NotifyIcon` 可能失败，
  此时窗口仍可用，界面会写「托盘：不可用（原因）」。
* 网关首次启动要初始化数据库与模型注册表，本机并发负载下曾用 425.9 秒；
  桌面壳默认给 120 秒，超时会判「启动失败」并在窗口/日志给出原因。这是
  启动耗时问题，与停机链路无关；干净机器上通常 20 秒内就绪。

### 2.6 首启：启动门（不给用户看错误页）

网关在导入路由、跑完 lifespan 之后才绑定端口；在此之前浏览器只能拿到
**连接失败**。所以"启动完再打开浏览器"不能只看进程是否拉起，必须看网关自己
的 `GET /v1/startup`（契约见 `docs/startup-profile.md`）。

| 探测结果 | 用户看到 | 是否进控制台 |
|---|---|---|
| 连接失败 / `state=starting` | 窗口「启动：端口尚未监听（已 Xs）」；托盘「正在启动…」 | 否，继续等 |
| `state=ready` | 「启动：已就绪（Xs）」+ 气泡「鎏灏已就绪」 | 是 |
| `state=degraded` | 「核心可用，但后台组件未就绪：<真实 errors>」+ 警告气泡 | **是**（核心可用，不卡门口） |
| 到点未就绪 | 「等待 Xs 仍未就绪（最后阶段：…）」+ 日志路径与 `python -m src.desktop diagnose --verify` | 否 |
| 网关进程中途退出 | 「网关进程在启动过程中退出」+ 同样的日志/诊断指引 | 否 |
| 旧版网关（`/v1/startup` 404） | 退回 `/v1/health` 判定，通了就算 ready | 是 |

* 探测间隔 250ms 起、每次翻倍、上限 500ms；启动门自身上限 60s（`start()` 已等
  `/v1/health` 最多 120s，正常情况下到这一步已经就绪）。
* 窗口**先出现**（"正在启动…"），网关启动与启动门在后台线程跑，界面不冻结；
  启动门结果经队列回到 UI 线程。这样慢启动时用户看到的是进度，而不是空白。
* `degraded` 把 `background.errors` 原样写进窗口与气泡（步骤：原因），
  不吞成"启动失败"。
* 无法自动化验证的部分（真实浏览器/真实窗口）见 8.2 的手工步骤。

### 2.7 安装器 / 快捷方式 / 托盘的真实链路（启动门覆盖到哪）

用户点哪个图标、走哪条代码，必须能对上号，否则"首启不给错误页"只覆盖了一半：

| 入口 | 实际链路 | 可见反馈 | 启动门 |
|---|---|---|---|
| 桌面 / 开始菜单快捷方式、安装完成页「立即启动」 | `{app}\LiuHao.bat` → `runtime\python.exe scripts\launch_installed.py` →（首次运行向导）→ `scripts/start_liuhao.py` | **控制台窗口**（阶段与进度逐行打印） | 是（`/v1/startup`，ready/degraded 才开浏览器） |
| 托盘 / 开机自启 | `scripts/liuhao_desktop.py` → `src.desktop app` | 状态窗口 + 托盘图标 | 是（同一模块 `src/desktop/startup.py`） |

证据（当前代码）：

* `installer/liuhao.iss` 的 `[Icons]`（开始菜单）、桌面快捷方式与 `[Run]`
  的「立即启动」三项都指向 `{app}\LiuHao.bat`；
* `packaging/stage_payload.py` 生成的 `LiuHao.bat` 内容是
  `runtime\python.exe scripts\launch_installed.py %*`；
* `scripts/launch_installed.py` 的 `main()` 把参数原样交给
  `scripts.start_liuhao.main()`（首次运行时先跑 `first_run_wizard`）；
* `src/desktop/autostart.py` 的 `_entry()` 优先取 `scripts/liuhao_desktop.py`，
  该脚本已在 `PAYLOAD_SCRIPTS` 里，因此安装版自启也是托盘壳。

两条壳共用 `src/desktop/startup.py` 里的同一套判定（连接失败=`starting`、
250→500ms 退避、ready/degraded 才放行、超时给日志与诊断命令），不会出现
"快捷方式正常、托盘报错页"的分裂。差异只在**外壳形态**：快捷方式是控制台
（首次运行向导需要交互），托盘是状态窗口。

启动器侧的日志提示指向它真正写的文件（`logs/liuhao.log`、`logs/error.log`，
安装版在 `%LOCALAPPDATA%\LiuHao\data\logs`），诊断命令统一走
`scripts\liuhao_desktop.py diagnose --verify`——该入口会先把日志目录切到安装版
布局，不会把用户指到空目录。

**没有覆盖到的**：`LiuHao.bat` 本身仍是控制台启动器，不是托盘壳；改成托盘
需要在打包侧换快捷方式目标，且首次运行向导会失去交互宿主，暂不做。

## 3. 开机自启

* 只写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，
  值名 `LiuHaoAIOS`；**不写 HKLM**，**不请求管理员权限**。
* 默认**关闭**，由用户显式开启。
* 默认「自启但不自动打开浏览器」；确实需要开机弹浏览器时才加 `--open-browser`。
* 使用 `pythonw.exe` 启动，避免开机闪控制台窗口。解释器与入口按**真实目录布局**解析：

  | 布局 | 解释器 | 入口 | 行为 |
  |---|---|---|---|
  | 源码/开发目录 | `.venv\Scripts\pythonw.exe` | `scripts\liuhao_desktop.py` | 带 `--autostart`，常驻托盘，不弹浏览器 |
  | 安装目录（无 `.venv`） | `runtime\pythonw.exe` | `scripts\liuhao_desktop.py` | 带 `--autostart`，常驻托盘（走启动门） |
  | 旧载荷（缺桌面入口脚本） | `runtime\pythonw.exe` | `scripts\launch_installed.py` | 带 `--no-browser`，自启网关与控制台 |
  | 都找不到入口 | — | — | `autostart enable` **明确报错并拒绝写入**，不写一条注定失败的注册表命令 |

  安装目录的 `runtime\pythonw.exe`、`scripts\liuhao_desktop.py` 与
  `launch_installed.py` 已在构建产物 `dist-mid-20261106\payload\app` 实际存在；
  `build_command` 对两种布局都有单元测试（含路径带空格的引用）。

```powershell
# 开启（默认不打开浏览器）
.venv\Scripts\python.exe -m src.desktop autostart enable
# 开启且自启时打开浏览器
.venv\Scripts\python.exe -m src.desktop autostart enable --open-browser
# 查看 / 关闭
.venv\Scripts\python.exe -m src.desktop autostart status
.venv\Scripts\python.exe -m src.desktop autostart disable
```

关闭方法除上面的 CLI，还可在「任务管理器 → 启动」或「设置 → 应用 → 启动」里
禁用 `pythonw.exe` / `LiuHaoAIOS` 项。写入的命令形如：

```
# 源码目录
"C:\...\.venv\Scripts\pythonw.exe" "C:\...\scripts\liuhao_desktop.py" --autostart

# 安装目录（载荷已收录桌面入口：自启进托盘）
"C:\...\app\runtime\pythonw.exe" "C:\...\app\scripts\liuhao_desktop.py" --autostart

# 旧载荷没有桌面入口脚本时的退路（自启网关 + 控制台，不弹浏览器）
"C:\...\app\runtime\pythonw.exe" "C:\...\app\scripts\launch_installed.py" --no-browser
```

**安装版的入口选择**：`packaging/stage_payload.py` 的 `PAYLOAD_SCRIPTS` 已收录
`scripts/liuhao_desktop.py`，`src/desktop/autostart.py` 的 `_entry()` 会优先选
它，因此安装目录下自启走**托盘壳**（带启动门、不弹浏览器）；只有旧载荷缺这个
脚本时才退回 `launch_installed.py`（控制台启动器）。构建产物已实测：
`dist-mid-20261106\payload\app\scripts\liuhao_desktop.py` 与
`runtime\pythonw.exe` 都在，`autostart enable` 的前置校验（安装目录内解释器
+ 安装目录内入口）能通过。

## 4. 防火墙与局域网可达性

只读检测项：

* `Get-NetFirewallProfile`：各配置文件的启用状态；
* `Get-NetConnectionProfile`：当前网络是「公用」还是「专用」（公用默认阻断入站）；
* `Get-NetTCPConnection`：端口是否监听、监听在 `0.0.0.0` 还是仅 `127.0.0.1`；
* `Get-NetFirewallRule` / `Get-NetFirewallPortFilter`：是否已有该端口的入站放行规则。

输出含 `risk`（low/medium/high）、`reasons` 与 `guidance`（用户自己可执行的步骤）。
**硬约束**：`auto_modify` 恒为 `false`；不执行 `New-NetFirewallRule` /
`Set-NetFirewallProfile` / `netsh advfirewall set`，不请求管理员权限，不操作安全软件。
工具只会在指引里给出「供你自己执行」的命令文本。

手机连不上时的排查顺序：端口是否只听回环 → 是否处于「公用」网络 →
防火墙是否开启且无放行规则 → 公司/学校网络是否隔离客户端（AP isolation）。

## 5. 一键诊断包

```powershell
.venv\Scripts\python.exe -m src.desktop diagnose --verify
```

默认输出到 `%LOCALAPPDATA%\LiuHao\diagnostics\liuhao-diagnostics-<时间戳>.zip`
（可用 `LIUHAO_DIAGNOSTICS_DIR` 覆盖，或 `--out` 指定目录/文件）。

**收录**：`manifest.json`（sha256）、`about.json`、`environment.json`（仅白名单环境
变量摘要）、`ports.json`、`providers.json`（模型**名称/provider 类型/模型名/地址**）、
`service.json`（`/v1/health`、`/v1/ready` 原始快照）、`dependencies.json`、
`logs/*.txt`（最近日志尾部）、`firewall.json`（如当时服务可达）。

**明确排除**：数据库文件（`.db/.sqlite/.sqlite3` 及 WAL/SHM）、密钥、token、
完整性键、签名私钥、身份登记表、凭据文件、`.env` 原文、完整环境变量转储。
实现方式是**白名单生成**：zip 每个成员都由代码在内存构造，从不遍历目录。

**脱敏 + 自检**：所有文本先经 `redact_text`（PEM / `sk-` / Bearer / JWT /
`api_key=` 等键值对）；写完后 `verify_bundle` 重新打开 zip 逐成员扫描，
发现疑似密钥或非白名单成员就**删除产物并报错**（fail-closed）。

## 6. 关于页数据源

`collect_about()`（`src/desktop/about.py`）返回：

| 字段 | 含义 |
|---|---|
| `app` / `version` | 产品名 / 版本（包元数据，回退 `pyproject.toml`） |
| `build_time` / `build_time_source` | 构建时间及来源：驾驶舱产物 mtime → git 提交时间 → pyproject mtime |
| `commit` | 当前 HEAD（取不到为 `null`） |
| `data_dir` / `log_dir` / `diagnostics_dir` | 真实目录 |
| `python_version` / `python_executable` / `platform` / `machine` | 运行环境 |

前端只需读取该 JSON；`apps/console` 未做任何改动。

## 7. 依赖登记

* 新增依赖：`pywin32>=306; sys_platform == 'win32'`（`pyproject.toml` + `requirements.txt`）。
* `oss-registry.yaml` 已登记 `pywin32`，实测许可证为
  `Python Software Foundation License`（PSF，OSI 批准、非 copyleft，无源码披露义务）。
* 不使用 `pystray`（LGPL）；托盘直接调用 Win32。
* Linux CI / Docker 因平台标记不会安装 pywin32；代码侧由
  `src/desktop/tray.py` 平台限定加载，非 Windows 不 import `win32*` 模块。

## 8. 验证记录

```powershell
.venv\Scripts\python.exe -m pytest tests\desktop -q
.venv\Scripts\python.exe scripts\verify_oss_licenses.py
.venv\Scripts\python.exe scripts\verify_dependency_declaration_surface.py
```

已覆盖：关于页字段、脱敏规则、诊断包白名单/排除/自检、自启 HKCU 写入读回关闭、
托盘图标添加/菜单命令/移除、三态图标切换与气泡提交、防火墙只读结构与
`auto_modify=false`、停止判定（优雅 / 超时强杀 / stdin 不可用 / 在途任务被取消）。

本轮实测（2026-11-05，Windows，本机）：

```
20 passed in 23.93s        # pytest tests/desktop -q
All checks passed!         # ruff check src/desktop tests/desktop
```

**停机协议（真进程、真 TCP，自动化）** ——
`test_runner_drains_inflight_request_and_refuses_new_ones`：
启动 `python -m src.desktop._runner`（真 uvicorn）→ 发起一个 3s 的 `/slow` 请求
→ 请求在途时通过 stdin 发送停止 → 断言：

* 在途请求 **200 且返回 `{"done": true}`**（未被砍断）；
* `shutdown_confirmed=True`、`graceful=True`、`forced=False`、`exit_code=0`；
* `elapsed < 10s`（远小于强杀上限）；
* 停止后端口可重新绑定、HTTP 请求失败（新请求不再被接受）。

**真实网关（部分验证）** —— 本机并发负载下网关首次启动用了 425.9 秒才就绪，
实测日志：

```
13:36:08 Application startup complete. / Uvicorn running on http://0.0.0.0:8099
13:36:09 Shutting down → Waiting for application shutdown.
13:36:09 Audit checkpointer stopped (... checkpoints_written: 1 ...)
13:36:09 Kernel lifecycle: shutdown=[audit, event, identity, plugin, policy], errors=0
13:36:09 Gateway shutdown complete
13:36:09 Application shutdown complete. / Finished server process [22080]
```

即：真实网关在收到停止指令后完成了 lifespan 收尾、审计检查点落盘、内核各层
shutdown，**没有强杀**。但"就绪状态下停止所需的 `elapsed`/`exit_code` 返回值"
未能取得 —— 后续几次复测时网关导入阶段被本机其他长驻进程阻塞 10 分钟以上
（`CPU` 仅 3s、无日志），属于环境问题，非停机链路；这项在干净机器上应重测。

**tray-demo（真实托盘）** 实测输出：

```
托盘已启动。…… [1003] 停止服务（仅停网关，托盘保留） -> stop_service
图标每 3 秒循环一次：绿=运行中 → 红=有错误 → 灰=已停止。
[tray] state=error
[tray] state=stopped
托盘已移除。
```

**未自动化（如实记录）**

* 右键菜单的真实鼠标点击：`TrackPopupMenu` 阻塞等真人，按 2.3 的手工步骤验证。
* 气泡肉眼可见性：只能断言 `Shell_NotifyIcon(NIF_INFO)` 提交成功，系统通知设置
  可能吞掉气泡。
* 安装目录整机自启：假安装布局单测覆盖 `runtime\pythonw.exe` + 两个入口；
  构建产物已含 `scripts\liuhao_desktop.py`，托盘自启链路由 `autostart enable`
  的写前校验兜住；真机开机自启仍需装一次实测。

### 8.1 CI 回归修复：`port_is_free` 把 TIME_WAIT 误判成"占用"（2026-11-06）

**失败现场**（`origin/p36`，run `42127243953`，Python 3.11 与 3.12 各一次）：

```
tests/desktop/test_desktop_system_layer.py:425: in test_runner_drains_inflight_request_and_refuses_new_ones
    assert port_is_free(port)
E   assert False
E    +  where False = port_is_free(8765)
```

同一用例里更靠前的断言（在途请求 200、`graceful=True`、`exit_code=0`、
`elapsed<10s`）全部通过 —— 只有"停止后端口可重新绑定"这条在 Linux 上失败。
托盘 / 防火墙 / 自启用例在 CI 是 `SKIPPED`（Windows-only），与本次无关。

**根因**（不是"测试假设了有桌面"，是本层探测工具的跨平台口径错了）：
uvicorn 收尾时会关掉 keep-alive 连接，**服务端先发 FIN**，内核在
`127.0.0.1:<port>` 上留 TIME_WAIT。`port_is_free` 当时用**不带
`SO_REUSEADDR`** 的 bind 判断，Linux 返回 `EADDRINUSE`（errno 98），
于是把"没人监听、服务能立刻重绑"误报成"端口被占用"。Windows 不阻止
这种 bind，所以本地一直绿。

影响不止测试：`GatewayService.start()` 用同一函数选端口，Linux 上
"停止后立刻重启"会平白跳到下一个端口；诊断包的端口表也会报错。

**修复**（`src/desktop/probe.py`）：POSIX 上按服务端同样的方式探测（加
`SO_REUSEADDR`），Windows 保持严格 bind —— 那边 `SO_REUSEADDR` 允许绑定到
别人**正在监听**的端口，会把真占用误判成空闲，不能用。已实测：Linux 上带
`SO_REUSEADDR` 时 TIME_WAIT 可绑、真监听仍 `EADDRINUSE`；Windows 上 TIME_WAIT
本就不阻止 bind。

**复验（真实数字）**

```
# Windows（本机，有桌面）
pytest tests/desktop -q                  → 21 passed in 25.50s

# Linux / 无桌面（WSL Ubuntu 24.04，无 GUI，system python3.12）
pytest tests/desktop/test_desktop_system_layer.py -q
                                        → 17 passed, 4 skipped（Windows-only 用例）in 5.58s
```

新增回归用例 `test_port_is_free_treats_time_wait_as_usable`：构造"服务端先关连接"
留下 TIME_WAIT，断言端口仍算可用。Windows 上这条恒真（TIME_WAIT 不阻止 bind），
真正的回归在 Linux CI 上跑。

**取证脚本**（不进仓库）在同一 Linux 环境跑真 uvicorn runner，客户端**保持连接**
让服务端先关：

```
旧口径 port_is_free = False   # errno=98 Address already in use
新口径 port_is_free = True
停止判定：stopped=True graceful=True forced=False elapsed=2.22s
```

与 CI 失败现象一致；修复后新口径为 True。

### 8.2 启动门验证（2026-11-06）

**实现**（`src/desktop/startup.py` + `src/desktop/app.py`）

* 新增 `wait_until_ready()`：按 `/v1/startup` 判定，连接失败=starting，
  250ms→500ms 退避；`ready`/`degraded` 才允许进控制台；超时给出日志与
  `diagnose` 命令；进程退出判 `exited`；旧版网关 404 退回 `/v1/health`。
* `DesktopApp.run()` 改成「窗口先出、启动在后台线程」：窗口立刻显示
  「启动：等待网关端口就绪…」，启动门结果经队列回到 UI 线程再更新界面/托盘/浏览器。
  以前 `service.start()` 在 UI 线程同步阻塞（最长 120s，窗口都不出现）。
* 只有 `console_ok`（ready / degraded）才 `webbrowser.open`；degraded 同时把
  `background.errors` 原样显示（窗口「启动：」行 + 警告气泡）。

**测试**（`tests/desktop/test_startup_gate.py`，13 个用例；慢启动用真 uvicorn，
lifespan 先睡 2s，此时端口**未监听**，与线上冷启动同一现象）

```
# Windows（本机，有桌面）
pytest tests/desktop -q        → 34 passed in 39.44s
# Linux / 无桌面（WSL Ubuntu 24.04）
pytest tests/desktop -q        → 30 passed, 4 skipped in 16.17s
```

三种状态的实测（脚本不进仓库，跑的是真 runner + 真 TCP）：

```
=== slow（lifespan 睡 2s）===
  t= 2.11s state=starting reachable=False
  t= 3.95s state=ready    reachable=True  stage=核心
  结果：state=ready console_ok=True elapsed=3.95s polls=2

=== degraded ===
  t= 1.59s state=degraded reachable=True
  结果：state=degraded console_ok=True elapsed=1.59s
  用户看到：核心可用，但后台组件未就绪（1.59s）：情报：SMTP 未配置：缺少 smtp_host。
           日志：logs/desktop.log、logs/desktop-gateway.log；诊断包：python -m src.desktop diagnose --verify

=== stuck（启动门上限设 3s）===
  t= 1.59/1.89/2.42/2.97/3.48s state=starting
  结果：state=timeout console_ok=False elapsed=3.48s polls=5
  用户看到：等待 3s 仍未就绪（最后阶段：核心）。日志：…；诊断包：python -m src.desktop diagnose --verify
```

说明：慢启动那条在本机第一个探测就吃满了 2s 连接超时（本机连接未监听回环端口
会挂起而不是立即拒绝），所以 `polls` 只有 2；墙钟 3.95s（导入约 1.9s + lifespan 2s）
仍证明"端口监听前一直在等、就绪后才放行"。

**无法自动化的部分（手工一遍即可）**：真实窗口/真实浏览器。

1. 冷启动（建议先 `autostart disable` 避免开机干扰）：
   `.\venv\Scripts\python.exe -m src.desktop app --open-browser`
2. 窗口应**立刻出现**并显示「启动：等待网关端口就绪…」，期间不应弹出浏览器；
3. 「启动：已就绪（Xs）」出现后才打开控制台，页面不是错误页；
4. 托盘气泡依次为「鎏灏 · 正在启动…」（tooltip）→「鎏灏已就绪」。

degraded / 超时的真实窗口表现见上表（自动化已覆盖文案与"是否叫浏览器"的决策，
窗口渲染本身需要真人看一遍）。

### 8.3 安装版快捷方式接上启动门（2026-11-06）

**核实结论**：安装器交付的快捷方式**不是**桌面壳，而是控制台启动器；启动门
原先只覆盖托盘链路，首启"错误页"风险实际落在快捷方式这条线上。

| 入口 | 代码链路 | 修复前 | 修复后 |
|---|---|---|---|
| 桌面 / 开始菜单快捷方式、安装完成页「立即启动」 | `installer/liuhao.iss` → `{app}\LiuHao.bat` → `runtime\python.exe scripts\launch_installed.py` →（首跑向导）→ `scripts/start_liuhao.py` | 等 `/v1/health` 200 后开浏览器；degraded 的后台失败原因看不到；超时只说"详见上方进程输出" | 等 `/v1/startup`：连接失败=启动中，ready/degraded 才开浏览器，degraded 原样打印 `background.errors`，超时/退出给日志路径 + 诊断命令 |
| 托盘 / 开机自启 | `scripts/liuhao_desktop.py` → `src.desktop app` | 已有启动门 | 不变（与上面共用 `src/desktop/startup.py`） |

**改动**

* `src/desktop/startup.py`：`wait_until_ready()` / `unavailable_gate()` /
  `describe_startup()` 增加可选 `log_hint`、`diagnose_hint`。不同外壳日志文件名
  不同（桌面壳 `desktop*.log`、安装版启动器 `liuhao.log/error.log`），否则会把
  用户指到不存在的文件。默认值不变，桌面壳行为无改动。
* `scripts/start_liuhao.py`：新增 `_wait_gateway_ready()`，用同一启动门；
  `main()` 从"等 `/v1/health`"改为"等 `/v1/startup`"，只有 ready/degraded 才
  继续走到开浏览器；超时/进程退出返回非零并打印终点提示。单端口驾驶舱检查
  失败时也补上日志路径。
* `src/desktop/autostart.py`：只更正文档字符串（载荷已收录桌面入口，安装版自启
  是托盘链路）。

**真实复验**（本机 Windows，跑真网关 / 真 uvicorn；脚本不进仓库）

```
=== [1] installer 链路：launch_installed --skip-wizard → start_liuhao（真网关，端口 8125）===
  [..] 网关启动中：连接失败按「启动中」处理，就绪后才打开浏览器
       等待端口监听（已 2.0s）
       核心就绪（登录/对话/模型配置可用），对话链转入后台预热（已 3.4s）
  [ok] 网关就绪（3.39s）
  [ok] 驾驶舱（同端口） 就绪（0.0s，HTTP 200）
  main exit=0  wall=3.92s
  开浏览器那一刻再次探测 /v1/startup：state=ready
  → 浏览器在"未就绪"时一次都没被打开

=== [degraded] 真网关（background.errors 有真实原因）===
  [warn] 核心可用，但后台组件未就绪（1.58s）：情报：SMTP 未配置：缺少 smtp_host
  wall=1.58s，console_ok=True（仍进控制台，不卡门口）
  → 超时/退出会返回 None（不开浏览器）并打印日志与诊断命令

=== [timeout] stuck 网关（启动门上限压到 3s）===
  [FAIL] 等待 3s 仍未就绪（最后阶段：核心）。日志：…\logs\liuhao.log、…\logs\error.log
         ；诊断包：<python> scripts\liuhao_desktop.py diagnose --verify
  wall=3.48s，返回 None

=== [exited] 网关进程中途退出 ===
  [FAIL] 网关进程在启动过程中退出。日志：…；诊断包：…
```

自动化：

```
pytest tests/desktop tests/test_start_liuhao.py -q  → 44 passed in 38.22s
ruff check src/desktop scripts/start_liuhao.py tests/test_start_liuhao.py
                                                    → All checks passed!
```

新增 5 个启动器用例（`tests/test_start_liuhao.py`）：ready/degraded 才返回结果、
degraded 原因原样打印、timeout/exited 返回 None 且打印终点、日志提示指向
`liuhao.log/error.log` 而非 `desktop.log`、`describe_startup` 优先用调用方提示。

**分发流需重测**：安装包重新构建后，双击桌面快捷方式（或安装完成页「立即启动」）
首启应看到控制台逐行进度、就绪后才打开控制台页面；`logs\liuhao.log` 与
`logs\error.log` 出现在数据目录。托盘自启路径未改，但建议一并确认自启进托盘。

### 8.4 中文/空格路径安装实测 + 程序目录首启零残留（2026-11-06）

以下均为本机 Windows 实测数字，未做推断。

**A. 中文/空格路径（候选包 `bb6b654c`）**

| 项目 | 实测值 |
|---|---|
| 安装路径 | `D:\测试 路径\鎏灏`（同时含中文与空格） |
| 静默安装 | exit 0；7,684 个文件 / 134,200,920 B；24.3–25.3 s |
| 数据目录 | `%LOCALAPPDATA%\LiuHao\data`（程序目录内无 `data/`、`*.db`、`*.log`、`app.env`） |
| 首启启动门 | `state=ready`，网关就绪 3.58 s（应用自计 7,344.8 ms） |
| 控制台首页 | `GET /` 200，1,628 B；浏览器在就绪后约 0.8 s 打开 |
| 安装树 CLI | `up_to_date`（不是 `version_unknown`） |
| 日志 | `liuhao.log` 按 UTF-8 写入，无乱码/`UnicodeDecodeError`（6,173 B；中文数据目录 4,723 B） |
| 卸载 | exit 0；8.2–9.2 s；数据目录 27 个文件保全 |

**B. 程序目录首启残留（本轮修复后重测）**

修复前首启后程序目录多出空 `logs\` 与若干 `__pycache__`（`.pyc`）。根因与改法：

* `scripts/launch_installed.py` 在导入应用模块之前直接置 `sys.dont_write_bytecode = True`
  （环境变量只对子进程生效，当前解释器必须就地关闭）；
* 安装包生成的 `LiuHao.bat`（`packaging/stage_payload.py`）与仓库根
  `start-liuhao.bat` 在 `setlocal` 后设置 `PYTHONDONTWRITEBYTECODE=1`；
* `src/gateway/__main__.py` 新增 `resolve_log_dir()`：只 `mkdir` 日志配置里
  `handler.filename` 的真实父目录（安装版=数据目录），不再无条件创建程序目录
  `logs/`。

重测流程：新构建安装到 `D:\修复 验证\鎏灏`（7,694 个文件 / 134,460,747 B，
安装 exit 0）→ 记录文件清单 → 经 `LiuHao.bat` 真实首启到 `state=ready`
（6,691.9 ms）→ 结束进程后再次对比清单：

```
程序目录新增文件数：0
新增目录：无（logs 不存在，__pycache__ 0 个）
```

**C. 控制台打印地址**

同一轮首启控制台原文：

```
==============================================================
鎏灏 LiuHao AI OS — 安装版
  程序目录  D:\修复 验证\鎏灏
  数据目录  C:\Users\Administrator\AppData\Local\LiuHao\data
==============================================================
鎏灏网关启动： http://127.0.0.1:8080  (log_level=info)
```

打印地址由实际绑定配置派生（`scripts/start_liuhao.py` 的 `_display_host()`、
`src/gateway/__main__.py` 的 `display_host()`）：通配绑定 `0.0.0.0`/`::` 展示为
`127.0.0.1`，真实监听地址不变（子进程 `HOST` 仍是用户显式选择的值），
`已对外暴露` 警告路径保留。防硬编码断言：把 `--host` 换成 `10.9.8.7`，打印必须
变成 `http://10.9.8.7:<port>`。

**D. 相关测试**

```
pytest tests/test_start_liuhao.py tests/gateway/test_gateway_log_dir.py \
       tests/test_installed_launcher_hygiene.py \
       tests/gateway/test_loopback_defaults_and_host_guard.py \
       tests/test_gateway_logging_encoding.py -q
→ 44 passed
```

新增用例覆盖：默认/显式对外/任意网卡地址三种打印、`HOST` 环境变量与打印值分离、
`resolve_log_dir` 取配置父目录且只建该目录、入口源码不得出现 `LOG_DIR.mkdir(`、
导入 `launch_installed` 即关闭字节码、安装包 `LiuHao.bat` 含
`PYTHONDONTWRITEBYTECODE=1` 且仍为 ASCII。
