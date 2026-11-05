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

## 3. 开机自启

* 只写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，
  值名 `LiuHaoAIOS`；**不写 HKLM**，**不请求管理员权限**。
* 默认**关闭**，由用户显式开启。
* 默认「自启但不自动打开浏览器」；确实需要开机弹浏览器时才加 `--open-browser`。
* 使用 `pythonw.exe` 启动，避免开机闪控制台窗口。解释器与入口按**真实目录布局**解析：

  | 布局 | 解释器 | 入口 | 行为 |
  |---|---|---|---|
  | 源码/开发目录 | `.venv\Scripts\pythonw.exe` | `scripts\liuhao_desktop.py` | 带 `--autostart`，常驻托盘，不弹浏览器 |
  | 安装目录（无 `.venv`） | `runtime\pythonw.exe` | `scripts\launch_installed.py` | 带 `--no-browser`，自启网关与控制台 |
  | 都找不到入口 | — | — | `autostart enable` **明确报错并拒绝写入**，不写一条注定失败的注册表命令 |

  安装目录的 `runtime\pythonw.exe` 与 `launch_installed.py` 已在 `dist/payload/app`
  实际存在；`build_command` 对两种布局都有单元测试（含路径带空格的引用）。

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

# 安装目录（当前安装包未收录桌面入口脚本，只能走安装版启动器）
"C:\...\app\runtime\pythonw.exe" "C:\...\app\scripts\launch_installed.py" --no-browser
```

**安装版已知限制（如实记录）**：当前 `packaging/stage_payload.py` 的
`PAYLOAD_SCRIPTS` 只收录 `start_liuhao.py` 与 `launch_installed.py`，**没有**
`scripts/liuhao_desktop.py`。因此安装目录下无法"开机直接进托盘"，只能自启
网关 + 控制台（`launch_installed.py --no-browser`）。要变成托盘自启，需要打包侧
把 `scripts/liuhao_desktop.py` 加进载荷（不在本模块文件所有权内，已在此登记）。

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
* 安装目录整机自启：已用假安装布局单测 `runtime\pythonw.exe` +
  `launch_installed.py --no-browser`；但安装包当前不含 `scripts/liuhao_desktop.py`，
  无法在安装版验证"托盘自启"（见第 3 节限制）。
