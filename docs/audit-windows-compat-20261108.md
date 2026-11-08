# Windows 兼容性只读审计（换机安装/打开可行性）

- 审计日期：2026-11-08
- 审计对象：`D:\LiuHao-AI-OS`（`installer/liuhao.iss`、`packaging/*.ps1`、`scripts/release/package-frozen.ps1`、`dist\payload\app` 运行时、桌面壳/托盘/命名管道/DPI 源码）
- 审计性质：**只读**。未修改任何产品代码，未执行 `setup.exe`，未安装/卸载，未改动 `dist\`（仅做文件读取与 PowerShell 解析）。
- 结论口径：所有结论后附 `文件:行`、实际命令输出或官方文档链接；不使用"应该没问题"。

## 0. 结论摘要

| # | 审计项 | 结论 | 是否挡住"别人安装/打开" |
| --- | --- | --- | --- |
| 1 | PowerShell 5.1 | 3 个第一方脚本：2 个 5.1 可用，`prepare_release_venv.ps1` 在 5.1 下**语法解析失败**（UTF-8 无 BOM）；系统默认执行策略为 Restricted，必须显式 Bypass；这些脚本**不参与**安装/启动链路 | 不挡（仅影响构建机） |
| 2 | 安全软件 | 未签名 + 下载带 MOTW → SmartScreen 必弹（可手动绕过）；Defender ASR/CFA、360、火绒可能弹窗/拦截/隔离；企业策略可硬挡 | 条件挡（普通用户摩擦，企业环境可能硬挡） |
| 3 | 权限与账户 | `PrivilegesRequired=lowest`，常规路径**不触发 UAC**；非管理员在 D 盘根不可写时自动回退 `%LOCALAPPDATA%\Programs`，仍可安装；`C:\Program Files` 仅管理员可装；企业 AppLocker/WDAC/S 模式可硬挡 | 不挡（除企业策略） |
| 4 | 系统版本 | Win10 21H2 / Win11 23H2 上无已知 API 不兼容（DPI 有回退、Tk/托盘/管道依赖齐全）；但两者均已 EOL；托盘缺 `TaskbarCreated` 重建，Explorer 重启后图标会消失 | 不挡（托盘图标缺失为功能缺陷） |
| 5 | 卸载后重装 | 保留数据（默认）/删除数据两种策略都能重装成功；**自定义数据目录在卸载后会被遗忘**；自启项在正常卸载时会被清除；合规文件按设计保留 | 不挡（有数据连续性风险） |

已知事实核对：当前安装包确为未签名 —— 命令输出 `Get-AuthenticodeSignature dist\final-72fd9b17\installer\LiuHao-Setup-0.1.0.exe` → `Status=NotSigned; Type=None`；`installer/liuhao.iss` 全文无 `SignTool=` 配置。

---

## 1. PowerShell 版本（Windows PowerShell 5.1）

### 1.1 审计方法（真实执行）

用系统自带 Windows PowerShell（`HOST PSVersion=5.1.19041.6926`）调用官方解析器 `[System.Management.Automation.Language.Parser]::ParseFile` 对每个第一方 `.ps1` 做语法解析，并提取 AST 中的命令/参数名，逐个用 5.1 的 `Get-Command` 校验；另外用 5.1 实跑脚本依赖的关键语法点。

同时核实编码（BOM 决定 5.1 用 ANSI 还是 UTF-8 解码）：

```
build_windows_installer.ps1 => UTF8-BOM
prepare_release_venv.ps1    => NO-BOM
package-frozen.ps1          => UTF8-BOM
```

### 1.2 逐脚本结论

| 脚本 | 5.1 解析结果 | 5.1 兼容性证据 | 结论 |
| --- | --- | --- | --- |
| `packaging/build_windows_installer.ps1` | `PARSE-PASS tokens=1804 lines=365`；`CMDS-MISSING: (none)`；`PARAMS-MISSING: (none)` | 仅用 5.1 原生能力：`Get-FileHash`(:207,336)、`${env:ProgramFiles(x86)}`(:281-284)、`-in @(...)`(:130,166)、`Set-Content -Encoding UTF8`(:320)；5.1 实跑这些点全部成功（见 1.3） | 🟢 5.1 可运行；依赖外部 `ISCC.exe`/`git`/Python(:86,281-284)，属构建机工具 |
| `packaging/prepare_release_venv.ps1` | `SYNTAX-FAIL L42, L52, L53, L59, L63, L68, L69`；报错文本出现 `鍙戝竷鐢?Python锛?` 等乱码，L69 `The string is missing the terminator: "` | 文件第 1 行起为 UTF-8 **无 BOM**（字节检查 `NO-BOM`）；5.1 按系统 ANSI 代码页解码，中文注释/字符串把引号吞掉 → 整文件解析失败 | 🔴 在 5.1 下**完全不可用**（PowerShell 7 默认按 UTF-8 读无 BOM 文件，才可能通过）。修复只需另存为 UTF-8 BOM |
| `scripts/release/package-frozen.ps1` | `PARSE-PASS tokens=3617 lines=581`；外部命令无缺失 | 使用 `New-Object System.Collections.Generic.List[object]`(:52)、`Tee-Object`(:414)、`ConvertTo-Json -Depth 6`(:566)；5.1 实跑 `List[object]`/`-Depth 6`/`[ordered]`/`-in` 均成功（1.3）；自身用 `powershell.exe` 而非 `pwsh`(:407-414) | 🟢 5.1 可运行；依赖 `git`、Python、`ISCC.exe`(:45,251-257)，属构建机工具 |
| `dist\payload\app\runtime\Lib\venv\scripts\common\Activate.ps1` | `PARSE-PASS tokens=632 lines=248`；缺失项仅为脚本内部函数 `deactivate`/`_OLD_VIRTUAL_PROMPT` | venv 生成的标准激活脚本，不在安装/启动链路中（只有用户手动 activate 才会用到） | 🟢 5.1 可运行，非交付路径 |
| `node_modules/**/*.ps1`、`.venv/**/playwright/**/*.ps1` 等 | 未逐一解析（数量上百，均为第三方 npm/pip 包生成物） | 不由产品安装器或运行时代码调用；不随 payload 分发 | 不在本次结论范围（第三方生成物） |

### 1.3 5.1 实跑语法点（命令证据）

```
List[object] OK count=1
braced-env OK: C:\Program Files (x86)
json-depth OK: {"a":1}
ordered OK: OrderedDictionary
FileHash OK: CD6306A3CB39406D
in-op OK: True
```

### 1.4 执行策略：默认 Restricted 会让"直接双击/直接 -File"全部失败

- Windows 客户端自带的 5.1 默认执行策略为 **Restricted**（不允许运行任何脚本），官方文档：https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies?view=powershell-5.1 （"Restricted for Windows clients. RemoteSigned for Windows servers."）。
- 本机实测 `Get-ExecutionPolicy` = `Bypass`（本机被改过，因此本机测试能过）；**换到用户/干净构建机**时必须按仓库文档显式加 `-ExecutionPolicy Bypass`：`packaging/build_windows_installer.ps1:8`、`scripts/release/package-frozen.ps1:33,36`。
- 用户机器默认自带 5.1、PowerShell 7 需另装：https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows?view=powershell-7.5 （"Windows PowerShell 5.1, which are installed by default on Windows."）。

### 1.5 运行期是否会调用 PowerShell

- 桌面端仅在**诊断**里调用：`src/desktop/firewall.py:32-56` 以 `powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command` 执行只读查询（`Get-NetFirewallProfile`/`Get-NetConnectionProfile`/`Get-NetTCPConnection`/`Get-NetFirewallRule`，:74-111）。
- 调用点被 `try/except` 包裹，失败只写入诊断包 `firewall.json` 的 `error` 字段，不影响启动：`src/desktop/diagnostics.py:2335-2341`。
- 结论：目标机只要还有 Windows PowerShell（Win10/11 自带），诊断可跑；企业若用策略禁用 PowerShell，只是该诊断项降级，不挡打开。

---

## 2. 安全软件拦截（未签名安装包 + 随包 Python + 常驻进程）

### 2.1 用户会看到什么

| 来源 | 触发条件 | 用户看到的现象 | 官方依据 |
| --- | --- | --- | --- |
| SmartScreen | 安装包从浏览器/网盘/聊天工具下载（带 Mark-of-the-Web），且无签名/无信誉 | 蓝色「Windows 已保护你的电脑」，发布者「未知」；必须点「更多信息 → 仍要运行」才能装。企业策略可设为 Warn/Block 并禁止绕过 → 直接装不上 | https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation （"No signature — 'Windows protected your PC'; User must choose 'Run anyway'…"）；MOTW 定义：https://support.microsoft.com/en-us/windows/security/information-about-the-attachment-manager-in-microsoft-windows ；策略开关：https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/available-settings |
| UAC | 仅"以管理员身份运行"或 `/ALLUSERS` 提权路径（常规 `lowest` 不弹 UAC） | 「你要允许此应用对你的设备进行更改吗？」发布者显示「未知发布者」 | https://learn.microsoft.com/en-us/training/modules/configure-user-account-control/4-understand-user-account-control-elevation-prompts （"A program with an unknown publisher… does not have a valid digital signature"） |
| Defender ASR | 企业/安全软件开启了"阻止不符合流行度/年龄/可信列表的程序" | 进程被静默阻止或弹安全中心通知；安装包/`python.exe` 无法启动 | 规则 GUID `01443614-CD74-433A-B99E-2ECDC07BFC25`：https://learn.microsoft.com/en-us/intune/device-configuration/endpoint-security/ref-attack-surface-reduction-settings ；规则说明：https://learn.microsoft.com/en-us/defender-endpoint/attack-surface-reduction-rules-reference |
| Defender 受控文件夹访问（CFA） | 用户在「文档/桌面」等受保护目录选择数据目录 | 写入被拒 → 安装器在复制前就报「数据目录不可写」（`liuhao.iss:909-917`），不会装到一半失败 | https://learn.microsoft.com/en-us/defender-endpoint/controlled-folders （"Apps that aren't included in the list are prevented from making any changes to files inside protected folders."） |
| Defender 实时保护误报 | 未签名安装包 + 随包 `python.exe/pythonw.exe` | 安装中途文件被隔离 → 安装器报创建/复制失败；或装完后双击无反应/托盘不出现（运行器已被隔离） | 误报检测名由引擎动态生成，无法预先确定；Inno 官方明确不解释杀软误报：https://jrsoftware.org/forums.php （"Please do not post messages about your antivirus program…"）；Inno 7 更新日志把"签安装包"作为应对 ASR/信誉拦截的手段：https://jrsoftware.org/files/is7-whatsnew.htm |
| 360 安全卫士 | 程序写敏感启动项/注册表时 | 可能弹「有程序正在修改开机启动项，是否允许」；官方说明会对该类操作做云引擎校验（校验结果影响是否放行） | 官方说明：https://www.360.cn/privacy/v2/360anquanweishi.html （"当有程序试图修改敏感启动项注册表时…进行云引擎验证。"）；误报提交：https://weishi.360.cn/n/10454.html → https://open.soft.360.cn/report.php |
| 火绒 | "系统加固"命中的敏感操作（改启动项等） | 弹拦截窗，需点「信任此文件、不再拦截」；静默模式可能直接拦 | 常见问题：https://www.huorong.cn/service/support/fqa/personal-edition-faq/core-features/core-features1 ；手册：https://cdn-www.huorong.cn/Public/Uploads/uploadfile/files/20241205/huoruanganquanruanjian6.0yonghucaozuoshouce.pdf |
| 企业 AppLocker/WDAC | 默认规则+未签名；安装到用户可写目录 | 「此应用已被系统管理员阻止」；安装包在下载目录直接被拦 | https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/applocker/working-with-applocker-rules （默认规则只放行 Windows/Program Files；警告不要给用户可写目录建路径规则）；提示文案：https://learn.microsoft.com/en-us/training/modules/m365-wvd-security/7-exercise-secure-desktop |
| Windows S 模式 | 只允许 Microsoft Store 应用 | 无法安装/运行非 Store 的 exe，必须先退出 S 模式 | https://support.microsoft.com/zh-CN/Windows/Experience/Platform-variants/windows-10-and-windows-11-in-s-mode-faq （"仅支持 Microsoft Store 提供的兼容应用。"）；退出方式：https://support.microsoft.com/en-us/windows/experience/platform-variants/switching-out-of-s-mode-in-windows |

代码侧事实：安装后自启**默认关闭**（`src/desktop/__main__.py:66` "仅 HKCU，默认关闭"；首启向导无自启写入，`scripts/first_run_gui.py` 无 autostart 命中），因此 360/火绒的"启动项修改"弹窗只会在用户主动开启自启时出现（`src/desktop/autostart.py:127-128`）。产品从不自动改防火墙（`src/desktop/firewall.py:23` `AUTO_MODIFY_FIREWALL = False`），不会触发安全软件对防火墙规则的拦截。

### 2.2 我们能提前做什么

1. **代码签名**（发布流程）：至少签 `LiuHao-Setup-*.exe`，最好连随包 `runtime\python.exe`、`runtime\pythonw.exe` 一起签。官方明确签名是建立 SmartScreen 信誉、绕过 ASR"低流行度"规则的路径（见上表两条官方链接）。EV/OV 证书选择与信誉积累周期需按证书厂商流程执行（本报告不虚构时效）。
2. **误报报白**：对 Microsoft（WDSI 文件提交 https://www.microsoft.com/en-us/wdsi/filesubmission ）、360 报告页、火绒官方渠道提交样本与哈希；每版发版前重跑一次。
3. **发布页给校验信息**：构建脚本已自动产出 `SHA256SUMS`（`packaging/build_windows_installer.ps1:336-340`、`scripts/release/package-frozen.ps1:453-456,540`）。把 SHA256、文件大小、官方下载地址一起展示，便于用户核对未被替换/未被篡改。
4. **安装指引图文**：把"SmartScreen 更多信息→仍要运行""360/火绒 信任/放行""如何解除文件锁定（Unblock）"写进下载页与随包说明（现有 `installer/assets/BEFORE-INSTALL.txt`、`docs/INSTALL-WINDOWS.md:21,29,186` 已有文字版，但缺截图与杀软分支）。
5. **减少敏感行为**：保持"自启默认关闭、绝不自动改防火墙、只写 HKCU"（现状已满足），可显著降低国产杀软弹窗概率。
6. **企业分发**：给 IT 提供签名后的发布者规则/哈希白名单；文档注明"不支持 S 模式"。

---

## 3. 权限与账户

### 3.1 安装权限模型（静态事实）

- `PrivilegesRequired=lowest`：`installer/liuhao.iss:67`；`PrivilegesRequiredOverridesAllowed=commandline`：`installer/liuhao.iss:68`。
- 官方语义：`lowest` = 不提权、始终非管理安装模式（https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm ，"When set to lowest, Setup will not request to be run with administrative privileges… always run in non administrative install mode."）。因此**普通路径不会出现 UAC 弹窗**。
- 卸载键：`RegWriteStringValue(HKCU, UninstallKeyPath, 'DataDir', …)`（`installer/liuhao.iss:992`）；仅当 HKLM 已存在旧键时补写（:993-994）。即全新安装是"按用户"记录（设置 → 应用 里只有当前用户能看到卸载项）。

### 3.2 非管理员 + D 盘（关键实测）

- 安装器判断 D 盘可用，是直接往 **D 盘根目录**写探测文件：`installer/liuhao.iss:315-322`（`Probe := 'D:\' + '.liuhao-write-probe.tmp'`）。
- 本机实测 D 盘根 ACL（命令输出）：

```
D:\  NT AUTHORITY\Authenticated Users | ReadAndExecute, Synchronize | Allow | inherited=False
     BUILTIN\Users                  | ReadAndExecute, Synchronize | Allow | inherited=False
     BUILTIN\Administrators          | FullControl
     NT AUTHORITY\SYSTEM             | FullControl
```

→ 标准用户无法在 D 盘根创建文件，探测失败，`DDriveUsable` 返回 false。
- 后果（`installer/liuhao.iss:526-538`）：非管理员自动回退 `{autopf}\LiuHao` = `%LOCALAPPDATA%\Programs\LiuHao`；管理员则装 `{commonpf}\LiuHao`。安装**不会失败**，但"默认装 D 盘"的承诺在普通用户机器上可能落空。
- 数据目录同样只认可写目录：D 不可用 → `NeedDataDirChoice`，强制用户在向导里选一个可写目录，绝不静默回退 C 盘（`installer/liuhao.iss:585-600,760-768,899-917`）。

### 3.3 装到 `C:\Program Files` 会怎样

- 只有 `IsAdmin` 且 D 盘不可用时才会主动选 Program Files（`installer/liuhao.iss:534-535`）。
- 非管理员手工把目录改成 Program Files：安装前校验直接拒绝并给中文指引「请改选你有写权限的目录……或右键以管理员身份运行」：`installer/liuhao.iss:887-894`（同样校验数据目录 :909-917）。

### 3.4 UAC 拒绝后残留什么

- 常规 `lowest` 路径根本不提权，不存在"拒绝 UAC"。
- 只有用户右键"以管理员身份运行"或传 `/ALLUSERS` 才提权；Inno 在进入安装流程前完成提权。安装器的第一条业务逻辑就是抢互斥，且明确"必须在读/写 HKCU 卸载键、探测目录之前"（`installer/liuhao.iss:494-504`），之后才写 `data-dir.txt` 与 DataDir（:986-994）。
- 结论：UAC 取消时尚未写入任何产品注册表/目录，**不会留下半装状态**；可能残留仅是 Inno 自身的 `/LOG` 临时日志（未加 `/LOG` 则无）。

### 3.5 企业环境"禁用安装程序"

- AppLocker 默认规则：允许 Windows 目录与 Program Files，用户可写目录默认不放行 → 安装包放在"下载"里双击即被拦；装到 `%LOCALAPPDATA%\Programs` 的应用也可能被拦。官方：https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/applocker/working-with-applocker-rules
- WDAC/Defender ASR 的"低流行度/未受信可执行文件"规则同样会拦未签名 exe（GUID `01443614-CD74-433A-B99E-2ECDC07BFC25`，官方 Intune 页）。
- S 模式：禁止非 Store 应用（官方 FAQ，见 §2）。
- 企业静默部署可用 `/VERYSILENT`；静默卸载按安全默认**保留数据**（`installer/liuhao.iss:1054-1058`）。

### 3.6 架构限制

- `ArchitecturesAllowed=x64compatible`、`ArchitecturesInstallIn64BitMode=x64compatible`（`installer/liuhao.iss:81-82`）→ 32 位 Windows 会被 Inno 直接拒绝安装（Inno 通用文案）。与已有审计一致：`docs/WINDOWS-COMPAT-DPI-AUDIT-2026-11-08.md:18`（32 位被拦、ARM64 有误拒历史）。

---

## 4. 系统版本（Win10 21H2 / Win11 23H2）

### 4.1 运行时事实（命令读取）

| 项 | 实测值 | 证据 |
| --- | --- | --- |
| Python 运行时 | `3.12.14` | `Get-Item dist\payload\app\runtime\python.exe` → `ProductVersion=3.12.14` |
| Tcl/Tk | `8.6.12` | `dist\payload\app\runtime\DLLs\tcl86t.dll/tk86t.dll` 文件版本 = 8.6.12 |
| tkinter | 存在 | `DLLs\_tkinter.pyd` = OK |
| pywin32 DLL | 存在 | `Lib\site-packages\pywin32_system32\pywintypes312.dll`、`pythoncom312.dll` = OK |
| MSVC 运行库 | 存在 | `vcruntime140.dll`、`vcruntime140_1.dll` = OK |
| Inno MinVersion | 未显式设置 → 默认 `6.1sp1`（Win7 SP1+） | 官方：https://jrsoftware.org/ishelp/topic_setup_minversion.htm ；`installer/liuhao.iss` 内无 `MinVersion=` |

因此：安装器本身可在 Win7 SP1+ 运行，但 Python 3.12 官方最低要求 **Windows 8.1**（https://docs.python.org/3.12/using/windows.html ，"Python 3.12 supports Windows 8.1 and newer."）。产品文档已声明 Win10/11（`docs/INSTALL-WINDOWS.md:3`），建议把"最低支持"固化为 **Windows 10 x64 22H2 及以上 / Windows 11 22H2 及以上**。

### 4.2 Win10 21H2 / Win11 23H2 的兼容性判断

| 能力 | 兼容性 | 依据 |
| --- | --- | --- |
| DPI API | 21H2/23H2 均支持；代码有分层回退（`SetProcessDpiAwarenessContext` → `SetProcessDpiAwareness`(shcore) → `SetProcessDPIAware`） | `src/desktop/dpi.py:138-178`；API 最低版本：SetProcessDpiAwarenessContext=Win10 1703（https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setprocessdpiawarenesscontext ）、GetDpiForWindow=Win10 1607（https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getdpiforwindow ） |
| 高 DPI 观感 | **不是兼容性崩溃，是观感缺陷**：进程无 DPI 清单，200% 下被位图拉伸；首启向导可能超屏 | 已有实测：`docs/WINDOWS-COMPAT-DPI-AUDIT-2026-11-08.md:14-18,30-36`（本文不重复测量） |
| 托盘图标 | 21H2/23H2 都能显示；但程序只 `NIM_ADD` 一次，**未处理 `TaskbarCreated`**，Explorer 重启后图标消失且不会自恢复 | `src/desktop/tray.py:445`（唯一一次 NIM_ADD）；全仓 grep `TaskbarCreated` 无命中；官方要求：https://learn.microsoft.com/en-us/windows/win32/shell/taskbar （"it should assume that any taskbar icons it added have been removed and add them again."） |
| 命名管道 DACL | 显式 DACL（仅当前用户 SID）+ `SE_DACL_PROTECTED` + 创建后读回复核，Vista+ 到 Win11 行为一致，无已知不兼容 | `src/gateway/local_proof.py:515-567`（`CreateNamedPipe` :527）；若不定 DACL，默认 ACL 对 Everyone 可读：https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights |
| tkinter GUI | 首启向导依赖 tkinter；运行时已随包，缺 tkinter 时给出中文可读错误并提示 `LiuHao.bat --console` | `scripts/liuhao_desktop.py:108-147`（导入失败分支），运行时文件已核验存在 |
| Windows 11 新图标进"溢出区" | **未获官方文档证实**（Microsoft 支持页只描述可手动开关溢出区），不作为结论 | https://support.microsoft.com/en-us/windows/experience/personalization/customize-the-taskbar-in-windows |

### 4.3 EOL（支持终止）事实

- Win10 21H2 Home/Pro：2023-06-13 终止；Enterprise/Edu：2024-06-11 终止（https://learn.microsoft.com/en-us/lifecycle/products/windows-10-home-and-pro ；https://learn.microsoft.com/en-us/lifecycle/products/windows-10-enterprise-and-education ）。
- Win11 23H2 Home/Pro：2025-11-11 终止；Enterprise/Edu：2026-11-10 终止（https://learn.microsoft.com/en-us/lifecycle/products/windows-11-home-and-pro ；https://learn.microsoft.com/en-us/lifecycle/products/windows-11-enterprise-and-education ）。**截至审计日（2026-11-08），两者都已 EOL 或只剩 2 天**。
- 结论：技术上 21H2/23H2 可运行（无已知 API 冲突），但不建议作为"支持基线"对外承诺；建议基线与测试矩阵改为 **Win10 x64 22H2（最后版本）+ Win11 24H2/25H2**，并在文档标注 21H2/23H2 已 EOL。

---

## 5. 卸载后重装（静态推断 + 读码，未真跑安装）

> 按要求未执行 `setup.exe`、未做真实卸载；以下结论全部来自安装器脚本与产品代码。

### 5.1 两种卸载策略的实际行为

| 策略 | 行为 | 证据 |
| --- | --- | --- |
| 保留数据（默认） | 数据目录及其 `config/workspace/data/logs` 全部 `uninsneveruninstall`，卸载不删；卸载完成阶段仅问一次"是否同时删除数据？"默认"否" | `installer/liuhao.iss:157-161`（目录永不移除）、:1060-1068（默认 No） |
| 删除数据 | 两次确认后才 `DelTree` 实际数据目录；静默卸载不弹窗、按安全默认保留 | `installer/liuhao.iss:1063-1084`、:1054-1058 |
| 程序目录清理 | 只删 `data-dir.txt` marker 与空目录；合规文件（LICENSE、NOTICES、SBOM、licenses\*）卸载时保留 | `installer/liuhao.iss:189-196`、:126-132、:1047-1051 |
| 进程与自启 | 卸载先停进程（`-m src.desktop stop --root … --timeout 10`），再走产品自己的 `autostart disable`（删 HKCU Run），再清 `.pyc` | `installer/liuhao.iss:203-212`；自启键定义 `src/desktop/autostart.py:29,127-128,134-142` |
| 文件占用 | `CloseApplications=yes`（Restart Manager 参与）、`RestartApplications=no`（装/卸后不自动重启应用） | `installer/liuhao.iss:92-93` |

### 5.2 重装能不能正常起来

**能，且两种策略都能重装成功**，但有三处需要注意：

1. **自定义数据目录会被遗忘（数据连续性风险，不阻断）**
   - 卸载键里的 `DataDir` 与 `{app}\data-dir.txt` 都会消失：marker 在 `[UninstallDelete]` 显式删除（`installer/liuhao.iss:196`），`DataDir` 写在 Inno 自己的卸载键里（:992），而 Inno 卸载会删整个卸载键。
   - 重装时 `HasPrevInstall=false`（:517-518），全新分支只认 `D:\LiuHaoData`（:585-591）；旧版 `{localappdata}\LiuHao\data` 的自动接管只在"检测到上次安装"分支（:571-574），而 C 盘旧目录只作为**提示**出现（:765-768）。
   - 因此：在"管理员安装且 D 盘可写"的机器上，默认 `D:\LiuHaoData` 重装可无缝继续；但用户若曾把数据放到 `E:\...` 等自定义目录，卸载后重装安装器不会再自动指向它，需要用户手动重选，否则会新建空数据 → 用户主观"数据丢了"。
2. **自启项**：正常卸载会调 `autostart disable` 清除 HKCU Run（`installer/liuhao.iss:206`）；只有在 `runtime\python.exe` 已被杀软隔离/被用户删掉、该步骤执行失败时，才可能留下指向不存在路径的 Run 值（下次登录静默失败，不阻断重装）。
3. **合规文件残留**：卸载后 `{app}` 目录会保留 `LICENSE`、`THIRD-PARTY-NOTICES.txt`、`sbom-cyclonedx.json`、`licenses\`（`installer/liuhao.iss:126-132`，卸载前也有提示 :1047-1051）。同路径重装会覆盖，不影响启动；若换路径重装，旧路径会留下一小份合规文件（磁盘残留，非功能问题）。

### 5.3 注册表 / 自启残留排查（静态）

- 安装器没有 `[Registry]` 段（全文件 grep 无命中）；产品只在 HKCU Run 写自启（`src/desktop/autostart.py:29`），其余业务配置走数据目录文件。
- 卸载会删 Inno 卸载键（含 `DataDir`）；自启由卸载步骤显式删除。**因此"二次安装异常"的注册表根因基本不存在**；真正的二次安装摩擦来自：旧进程未退出导致文件占用（有 stop + Restart Manager 双保护，低概率）、杀软隔离导致运行器缺失（见 §2）。
- 数据目录若被"删除数据"策略清空，重装后首启向导会重新要求建账号（预期行为，`scripts/liuhao_desktop.py:220-230`）。

---

## 6. 风险总表

| 风险项 | 会不会挡住别人安装 | 建议动作 | 文档改还是代码改 |
| --- | --- | --- | --- |
| 安装包未签名 + 下载带 MOTW → SmartScreen「Windows 已保护你的电脑」 | 条件挡：需手动"更多信息→仍要运行"；企业 Block 策略则硬挡 | 采购代码签名证书签 setup（最好连 python.exe/pythonw.exe）；发布页放 SHA256 + 图文绕过指引 | 文档改 + 发布流程改（非产品代码） |
| Defender ASR"低流行度/未受信 exe"、CFA 拦写文档目录 | 条件挡（企业开启 ASR 时硬挡；CFA 会在安装前被中文提示拦住） | 签名 + 误报/白名单提交；文档说明数据目录不要选"文档/桌面" | 文档改（+签名） |
| 360/火绒 启动项/敏感操作弹窗、误报隔离 python.exe | 条件挡（用户点"阻止"或静默模式会失败） | 报白（360 报告页、火绒官方）；自启保持默认关闭；安装指引加信任/放行截图 | 文档改（+报白流程） |
| 企业 AppLocker/WDAC 禁止用户可写目录 exe；S 模式禁止非 Store 应用 | 挡（无法由安装器绕过） | 交付签名包 + 给 IT 发布者规则/哈希白名单；文档注明不支持 S 模式 | 文档改 |
| 非管理员 + D 盘根 ACL 只读 → 回退 `%LOCALAPPDATA%\Programs` | 不挡；但"默认 D 盘"落空 | 文档明示两套落点与查看方式；可选优化：D 盘探测改为"先建 D:\LiuHao 目录再探测" | 文档改（代码可选） |
| 非管理员手工选 `C:\Program Files` | 挡（安装前中文拒绝） | 保持现状；文档说明需管理员或改选默认回退目录 | 文档改 |
| 32 位 Windows | 挡（`ArchitecturesAllowed=x64compatible`） | 文档与下载页显著标注"仅 64 位 Windows 10/11" | 文档改 |
| Win7/8.x：安装器能装、Python 3.12 跑不起来 | 对 Win7 挡（Python 最低 8.1）；Win8.1 可用但产品未声明 | 支持基线写死 Windows 10 x64 22H2+；安装包可考虑加 `MinVersion=10.0` 提前中文拒绝 | 文档改（+可选 iss 改） |
| Win10 21H2 / Win11 23H2 已 EOL | 不挡 | 支持基线改 22H2+/24H2+；文档注明 21H2/23H2 已终止支持 | 文档改 |
| 托盘图标不处理 `TaskbarCreated`，Explorer 重启后消失 | 不挡 | 注册 `TaskbarCreated`，收到后重新 `NIM_ADD` | 代码改（小） |
| 运行时依赖 Windows PowerShell（诊断防火墙项） | 不挡（失败仅诊断降级） | 文档注明"诊断需系统 PowerShell"；无需改代码 | 文档改 |
| `prepare_release_venv.ps1` 无 BOM 在 5.1 解析失败 | 不挡用户（构建机脚本） | 另存为 UTF-8 BOM（或统一要求 PowerShell 7 构建） | 代码/工具改 |
| 系统默认执行策略 Restricted 导致 `.ps1` 直接运行被拒 | 不挡用户（安装/启动不跑 ps1） | 构建文档已用 `-ExecutionPolicy Bypass`，保持并在 README 强调 | 文档改 |
| 自定义数据目录卸载后被遗忘，重装指向默认目录 | 不挡；会造成"数据好像丢了" | 卸载前提示记录路径；重装向导已提示旧 C 盘数据，但不会提示自定义目录 → 文档加"卸载保留数据时记下路径" | 文档改（+可选：卸载时把路径写入独立 HKCU 键） |
| 卸载后 `{app}` 保留许可证/SBOM 等文件 | 不挡 | 保持现状；文档说明这是合规设计 | 文档改 |
| 杀软隔离 `python.exe` → 装到一半失败或装完双击无反应 | 条件挡 | 签名 + 报白；文档提供 `LiuHao.bat --console`/离线诊断入口（已存在） | 文档改（+签名/报白） |
| 命名管道 DACL / DPI API | 无风险（实现正确、API 在目标系统可用） | 无需动作 | 无需 |

---

## 附录 A：本次实际执行的命令（可复现）

```powershell
# 1) 5.1 版本
$PSVersionTable.PSVersion   # => 5.1.19041.6926

# 2) 解析 + 命令/参数兼容性（脚本内容：Parser::ParseFile + Get-Command 校验）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <临时审计脚本>
# => build_windows_installer.ps1: PARSE-PASS tokens=1804；CMDS/PARAMS-MISSING (none)
# => prepare_release_venv.ps1: SYNTAX-FAIL L42/L68-69（乱码 + string missing terminator）
# => package-frozen.ps1: PARSE-PASS tokens=3617；CMDS-MISSING (none)

# 3) 编码
[IO.File]::ReadAllBytes(...)  # build=BOM, prepare_release_venv=NO-BOM, package-frozen=BOM

# 4) 安装包签名
Get-AuthenticodeSignature dist\final-72fd9b17\installer\LiuHao-Setup-0.1.0.exe
# => Status=NotSigned; Type=None

# 5) D 盘根 ACL（非管理员能否写）
(Get-Acl 'D:\').Access
# => Users / Authenticated Users 仅 ReadAndExecute（无 CreateFiles）

# 6) 运行时版本与依赖
(Get-Item dist\payload\app\runtime\python.exe).VersionInfo.ProductVersion  # 3.12.14
# tcl86t.dll/tk86t.dll = 8.6.12；_tkinter.pyd、pywintypes312.dll、pythoncom312.dll、vcruntime140*.dll 均存在

# 7) 托盘是否处理 TaskbarCreated
# 全仓 grep TaskbarCreated => 无命中；tray.py 仅一次 NIM_ADD（:445）
```

## 附录 B：未执行项（边界声明）

- 未运行 `setup.exe`，未做真实安装/卸载/重装（按用户要求，且 `dist\` 保持未改动）。
- 未切换本机显示缩放做 200% 目视（已有独立 DPI 审计覆盖，见 `docs/WINDOWS-COMPAT-DPI-AUDIT-2026-11-08.md`）。
- 未在真机做 SmartScreen/360/火绒 的实际拦截复现；该部分结论基于官方文档 + 安装包未签名事实 + 代码行为（自启默认关闭、不改防火墙、只写 HKCU）。
