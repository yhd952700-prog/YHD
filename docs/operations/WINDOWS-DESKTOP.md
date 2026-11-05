# Windows 桌面集成（托盘 / 自启 / 诊断包）

> 系统层支撑，不含业务逻辑。实现位于 `src/desktop/**` 与 `scripts/liuhao_desktop.py`；
> 不修改网关、知识库、自动化、情报、驾驶舱与安装脚本。

## 1. 模块与入口

| 模块 | 职责 |
|---|---|
| `src/desktop/tray.py` | pywin32 系统托盘（`Shell_NotifyIcon`，**不用 LGPL 的 pystray**） |
| `src/desktop/service.py` | 网关子进程启动 / 停止（托盘与主服务的唯一边界） |
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
```

## 2. 系统托盘

菜单：**打开控制台 / 查看状态 / 停止服务 / 退出**（顺序固定）。
左键单击 = 显示/隐藏状态窗口；左键双击 = 打开控制台；右键 = 菜单。

**消息循环与主服务的关系**

* 托盘图标属于创建它的线程，因此 `SystemTray` 在**独立线程**里注册窗口类、
  创建隐藏窗口、`Shell_NotifyIcon(NIM_ADD)` 并泵 Win32 消息。
* 主服务是 `uvicorn` **独立子进程**；状态窗口是主线程的 tkinter。
  三者互不阻塞：托盘线程不碰 tkinter，只把动作放进队列，由 UI 线程执行。
* `stop()` 可从任意线程调用：置停止标志 + `PostMessage(WM_CLOSE)` 唤醒循环，
  循环退出前在同一线程 `NIM_DELETE` 并销毁窗口，不遗留图标。

**窗口关闭行为（明确、不留半截）**

* 点标题栏关闭按钮 = **最小化到托盘**，网关继续运行
  （`WM_DELETE_WINDOW` 绑定到 `withdraw()`，不是退出）。
* 真正退出只有两条路：托盘菜单「退出」、窗口内「退出程序」。
* 「停止服务」只停网关子进程，窗口与托盘仍驻留，方便排障或再次启动。

**已知限制（如实记录）**

* Windows 的 `TerminateProcess` 没有可捕获的优雅退出信号，「停止服务」是
  **立即结束**网关进程；SQLite WAL 本身可安全恢复。
* 未做托盘气泡通知（pywin32 的 `Shell_NotifyIcon` 便捷元组不支持 `NIF_INFO`
  信息字段）；状态通过窗口与悬停提示呈现。
* 本机实测：图标可添加 / 移除，菜单命令处理链路可验证；**真实鼠标点击菜单项
  未做自动化**（需人工操作通知区）。

## 3. 开机自启

* 只写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，
  值名 `LiuHaoAIOS`；**不写 HKLM**，**不请求管理员权限**。
* 默认**关闭**，由用户显式开启。
* 默认「自启但不自动打开浏览器」（命令里带 `--autostart`，不带
  `--open-browser`）；确实需要开机弹浏览器时才加 `--open-browser`。
* 使用 `.venv\Scripts\pythonw.exe` 启动，避免开机闪控制台窗口。

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
"C:\...\pythonw.exe" "C:\...\scripts\liuhao_desktop.py" --autostart
```

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
托盘图标添加/菜单命令/移除、防火墙只读结构与 `auto_modify=false`。
