# Windows 兼容性与高 DPI 审计报告

> 审计日期：2026-11-08
> 审计对象：`d:\LiuHao-AI-OS`（源码 + 当前 payload + 已有 0.1.1 测试安装包）
> 审计性质：只读审计，未修改任何产品代码
> 审计范围：125% / 150% / 200% 缩放、多显示器混合 DPI、英文/日文系统、中文/空格/特殊字符用户名、32 位与 ARM 设备

---

## 一、结论摘要

| 审计项 | 结论 | 状态 |
| --- | --- | --- |
| 高 DPI（125/150/200%） | 桌面程序**没有** DPI 感知声明，Windows 对全部 tkinter 界面做位图拉伸；首启向导在常见 200% 场景下按钮会落到屏幕外 | 🔴 NOT DONE |
| 多显示器（不同 DPI 拖动） | 无 Per-Monitor V2、无 `WM_DPICHANGED` 处理，跨屏拖动会重采样/尺寸跳变 | 🔴 NOT DONE |
| 英文 / 日文系统 | 不会乱码（全程 Unicode），但界面与安装器内容不本地化；控制台日期格式中英混用 | 🟡 PARTIAL |
| 用户名含中文/空格/特殊字符 | 安装、数据目录、日志、卸载均实测通过（含 `&`、`#`）；遗留长路径与重定向输出风险 | 🟢 DONE（有 P3 遗留项） |
| 32 位 / ARM | 32 位被 Inno 通用文案拦截；**ARM64 Win11 被错误拒绝**（`x64` 标识实际等价 `x64os`）；产品侧无清楚说明 | 🔴 NOT DONE |

**总体判定：🟡 PARTIAL —— 功能可用，但不具备“高 DPI 兼容”交付条件。** 建议在对外宣称支持高缩放/多显示器之前，至少修复 P1-01、P1-02。

---

## 二、验证方法与证据边界

### 2.1 已实际执行的验证

| 验证项 | 方法 | 结果 |
| --- | --- | --- |
| 进程 DPI 感知级别 | 在安装版 `runtime\python.exe` 中调用 `GetProcessDpiAwareness`，并在创建 Tk 窗口后复测 | `0`（DPI_UNAWARE），Tk 初始化前后不变 |
| 运行时清单 | 解析 PE 资源 `RT_MANIFEST` | `python.exe` 含 `longPathAware=true`，**无 `dpiAware` / `dpiAwareness`** |
| 安装器清单/架构 | 解析 `LiuHao-Setup-0.1.1.exe` 的 PE 头与清单 | PE 机器 `0x014c`（32 位安装器，正常）；清单 `dpiAware=true`（System DPI Aware，非 PerMonitor） |
| 托盘图标规格 | 调用 `write_state_icon()` 生成 ICO 后解析目录项 | **仅 1 个条目，16×16**（150% 需要 24×24，200% 需要 32×32） |
| 状态窗口内容高度 | 用安装版运行时真实构建 tkinter 界面，`winfo_reqheight()` 测量 | 固定 `500×360`，中文路径下内容需要 **494×434** |
| 首启向导内容尺寸 | 实例化 `_WizardWindow` 并逐页 `winfo_req*` | 三步最大 **502×400**，窗口固定 `680×460` |
| 中文/空格/特殊字符路径 | 静默安装到 `D:\tmp\lh-dpi-audit\安装 目录 & #1\LiuHao`，数据目录 `...\数据`，`/NOICONS`，随后静默卸载 | 安装/卸载 exit=0；标记文件 UTF-8 BOM 正确；launcher 成功创建 config/logs；日志 UTF-8 中文路径无损坏 |
| 非中文区域重定向输出 | `PYTHONIOENCODING=cp1252` + 重定向 stdout 打印中文 | `UnicodeEncodeError`（仅影响重定向场景，正常控制台用 `WriteConsoleW` 不受影响） |
| Inno 架构标识语义 | 用本机 Inno Setup 6.7.1 编译 `ArchitecturesAllowed=x64` 的最小脚本 | ISCC 明确警告：`"x64" is deprecated. Substituting "x64os"` |

临时安装目录、诊断包与探针脚本均已清理；测试安装器为 `dist\encpath\installer\LiuHao-Setup-0.1.1.exe`（路径编码专项测试包）。

### 2.2 未执行、需真机目视的验证

- 未把本机显示缩放实际切到 125% / 150% / 200% 后截图（改系统缩放会影响当前会话，属于环境变更，本轮未做）。
- 无第二台不同 DPI 显示器，未做真实跨屏拖动录屏。
- 无英文/日文 Windows、32 位 Windows、ARM64 设备，相关结论来自代码、清单与官方语义，已标注为静态推导。
- 测试用 0.1.1 包是 `encpath` 专项构建，其 payload 未包含 `scripts\first_run_gui.py`（当前 `dist\payload\app` 已包含）。因此向导尺寸从**当前 payload** 实测，而不是从该安装包启动实测。

> 附带发现（不纳入本报告结论，但转正构建前需确认）：`dist\encpath\payload\app\scripts\` 与 `dist\final-72fd9b17\...` 缺少 `first_run_gui.py`；若以这类产物转正，首启 GUI 向导会在导入阶段失败。当前源码 staging 清单已包含该文件。

---

## 三、兼容性矩阵

### 3.1 高 DPI（单显示器，Windows 缩放）

| 界面 | 125% | 150% | 200% | 说明 |
| --- | --- | --- | --- | --- |
| 控制台页面（浏览器） | 🟢 清晰 | 🟢 清晰 | 🟢 清晰 | 浏览器按 Per-Monitor V2 原生渲染；图形全部为 SVG，表格有横向滚动容器；无位图模糊 |
| 控制台布局形态 | 取决于分辨率 | 取决于分辨率 | 取决于分辨率 | 视口 CSS 宽度 = 物理宽度 ÷ 缩放；`<768` mobile、`<1280` web、`≥1280` desktop；默认主题随形态变化（desktop=dark / web=light / mobile=dark） |
| 桌面状态窗口（tkinter） | 🔴 模糊 | 🔴 模糊 | 🔴 模糊 | 进程 DPI-unaware，整窗位图拉伸；内容本身需要 434px，窗口仅 360px，底部按钮/说明被裁 |
| 托盘图标 | 🔴 模糊 | 🔴 模糊 | 🔴 模糊 | 只生成 16×16 单尺寸 ICO，被系统放大到 24/32 |
| 托盘菜单（Win32 PopupMenu） | 🟡 模糊 | 🟡 模糊 | 🟡 模糊 | 菜单由系统绘制，但进程 unaware → 同样按位图缩放；位置由系统统一虚拟化，未见错位证据 |
| 首启向导（tkinter，默认路径） | 🟡 模糊 | 🔴 模糊 + 小屏裁切 | 🔴 模糊 + 按钮出屏 | 固定 `680×460+160+120`、最小 `620×420`；200% 时逻辑屏高仅 540，窗口底边到 611，底部按钮落到工作区外 |
| 首启向导（`--console` 回退） | 🟢 | 🟢 | 🟢 | 纯控制台文本，无 DPI 缩放问题 |
| Inno 安装器向导 | 🟢 清晰 | 🟢 清晰 | 🟢 清晰 | `dpiAware=true`（System DPI Aware），按系统 DPI 原生绘制；但非 Per-Monitor |
| 开机自启托盘进程 | 🔴 模糊 | 🔴 模糊 | 🔴 模糊 | 同一 `pythonw.exe`，无清单声明 |

### 3.2 常见分辨率下的控制台形态（CSS 像素 = 物理 ÷ 缩放）

| 物理分辨率 | 125% | 150% | 175% | 200% | 250% | 300% |
| --- | --- | --- | --- | --- | --- | --- |
| 1920×1080 | 1536 desktop | 1280 desktop | 1097 web | 960 web(浅色) | 768 web | 640 mobile |
| 2560×1440 | 2048 desktop | 1707 desktop | 1463 desktop | 1280 desktop | 1024 web | 853 web |
| 3840×2160 | — | 2560 desktop | — | 1920 desktop | 1536 desktop | 1280 desktop |
| 1600×900 | 1280 desktop | 1067 web | 914 web | 800 web | 640 mobile | — |
| 1366×768 | 1093 web | 911 web | 781 web | 683 mobile | — | — |
| 1280×720 | 1024 web | 853 web | 731 mobile | 640 mobile | — | — |

要点：

- 1920×1080 切到 150% 恰好落在 `1280` 边界，仍是 desktop；切到 175%/200% 会变成 web（默认浅色）。
- 1366×768 只要开 125% 就从 desktop 变 web；开 200% 变 mobile（底部导航）。
- 这是响应式设计的正常结果，不是渲染 bug；但用户会感知为“只是改了缩放，界面形态/主题变了”。控制台设置里可手动覆盖形态与主题（`apps\console\console\src\lib\mode.ts:75`、`:143`）。

### 3.3 多显示器（不同 DPI）

| 界面 | 跨屏拖动结果 | 依据 |
| --- | --- | --- |
| 控制台（浏览器） | 🟢 清晰、尺寸正确 | 浏览器 Per-Monitor V2 |
| 桌面状态窗口 / 首启向导 / 托盘 | 🔴 位图重采样后模糊；Windows 会按新显示器 DPI 重新拉伸，出现尺寸跳变；布局不重排 | 无 per-monitor 声明、无 `WM_DPICHANGED` 处理 |
| Inno 安装器向导 | 🟡 主屏清晰；拖到不同 DPI 屏幕后被系统拉伸，可能变模糊 | System DPI Aware，非 PerMonitor |

### 3.4 系统语言（英文 / 日文）

| 项目 | 英文 Windows | 日文 Windows | 判定 |
| --- | --- | --- | --- |
| Inno 向导框架 | 英文（`ShowLanguageDialog=no` 自动匹配 english） | 中文（无 ja 语言，回落第一个 chinesesimplified） | 🟡 混排/全中文，不是乱码 |
| 安装任务文案 | 硬编码中文（`[Tasks]` 的“创建桌面快捷方式”“安装完成后立即启动鎏灏”） | 中文 | 🟡 英文框架 + 中文任务，观感割裂 |
| `[Code]` 错误提示 | 硬编码中文 | 中文 | 🟡 同上 |
| 安装前说明 | 仅中文 `BEFORE-INSTALL.txt`（UTF-8 BOM） | 中文 | 🟡 |
| 程序提示（托盘/状态窗/向导） | 中文（硬编码），字体 `Microsoft YaHei UI` | 中文 | 🟡 不乱码；若系统精简掉中文字体会出现方块 |
| 控制台页面 | 全中文 UI；日期一部分 `zh-CN`、一部分跟随系统 locale | 同左 | 🟡 日期格式混用（英文系统会出现 AM/PM 与 24 小时并存） |
| CLI / 日志 | 日志 UTF-8；控制台正常显示中文 | 同左 | 🟢 |
| 重定向输出 | cp1252 下打印中文会 `UnicodeEncodeError` | cp932 下部分简体字不可编码 | 🟡 仅重定向场景 |

日期格式证据：`components\Shell.tsx:268-269`、`lib\employees.ts:110`、`components\UpgradePanel.tsx:44` 固定 `zh-CN`；`pages\Audit.tsx:42`、`pages\Events.tsx:30`、`pages\Goals.tsx:88/100`、`pages\Files.tsx:48`、`pages\Identity.tsx:139` 使用无 locale 的 `toLocaleString()`。英文系统上会出现 `11/8/2026, 3:04:05 PM` 与 `11/08 15:04` 并存。

### 3.5 用户名路径（中文 / 空格 / 特殊字符）

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 安装到 `...\安装 目录 & #1\LiuHao` | 🟢 通过 | 静默安装 exit=0，文件齐全 |
| 数据目录 `...\安装 目录 & #1\数据` | 🟢 通过 | `config/`、`logs/`、`workspace/` 正常创建 |
| `data-dir.txt` 标记 | 🟢 正确 | 首字节 `EF BB BF`，UTF-8 中文路径完整 |
| launcher 读取标记并初始化 | 🟢 通过 | 安装版运行时执行 `prepare_environment()`，日志落在中文路径下 |
| 日志内容 | 🟢 正确 | `launcher.log` UTF-8 解码后中文路径完整 |
| 状态窗口显示该路径 | 🟡 显示正常但撑高布局 | 内容高度 434 > 窗口 360 |
| 长路径（>260） | 🟡 有风险 | `python.exe` 声明 `longPathAware=true`，但仍需系统 `LongPathsEnabled=1`；安装器只校验程序目录长度，不校验数据目录 |
| 卸载 | 🟢 通过 | `unins000.exe /VERYSILENT` exit=0，数据目录按策略保留后手工清理 |

标记读取实现具备 UTF-8 BOM / 纯 UTF-8 / ANSI 多级回退（`scripts\launch_installed.py:85-102`），历史上 `C:\Users\张三 三` 类问题在当前代码路径上已消除。

### 3.6 架构（32 位 / ARM）

| 场景 | 行为 | 判定 |
| --- | --- | --- |
| 32 位 Windows + 安装包 | `ArchitecturesAllowed=x64` 直接拒绝，显示 Inno 通用文案：“This program can only be installed on versions of Windows designed for the following processor architectures: x64”（中文版为对应中文） | 🟡 能拦住，但提示是术语，没有“本产品需要 64 位 Windows”与后续指引 |
| 32 位 Windows + 便携 zip | 无任何架构检查；x64 `python.exe` 启动失败（“不是有效的 Win32 应用程序”/错误 216） | 🔴 提示不明确 |
| ARM64 Windows 11 | 安装器**拒绝安装**：Inno 6.7.1 将弃用的 `x64` 解析为 `x64os`，而 ARM64 不属于 x64os（尽管 Win11 ARM64 可通过 x64 模拟运行） | 🔴 误拒绝，且无说明 |
| ARM64 原生 | 无 arm64 运行时/安装器；只能 x64 模拟，性能下降 | 🟡 未声明 |
| Windows 7 / 8 | 未设置 `MinVersion`，Inno 默认允许 Win7 SP1+；运行时为 Python 3.12（最低 Win8.1），会在启动阶段失败且无产品提示 | 🟡 未拦截 |

运行时 `python.exe` PE 机器为 `0x8664`（x64）；安装器自身是 32 位（`0x014c`，正常）。

---

## 四、问题清单

### P1-01 桌面程序完全没有 DPI 感知，125% 以上全部模糊

- **现象**：状态窗口、首启向导、托盘菜单、图标在 125%/150%/200% 下由 Windows 位图拉伸，文字与图标发虚；托盘图标由 16px 放大到 24/32px。
- **证据**：
  - 安装版运行时 `GetProcessDpiAwareness` 返回 `0`（DPI_UNAWARE），创建 Tk 后仍为 `0`。
  - `runtime\python.exe` 清单无 `dpiAware` / `dpiAwareness`。
  - 全仓库无 `SetProcessDpiAwareness*`、无 `PerMonitorV2`、无 `WM_DPICHANGED`。
  - 托盘图标 `_ICON_SIZE = 16`（[tray.py](../src/desktop/tray.py#L81)），ICO 仅 1 个 16×16 条目。
- **影响**：所有非浏览器界面在高缩放下观感差；跨屏拖动出现模糊/尺寸跳变，无法达到“高 DPI 清晰”的基本要求。
- **建议**：进程启动最早处声明 Per-Monitor V2（`SetProcessDpiAwarenessContext`），或给启动器加 manifest；Tk 侧按 `GetDpiForWindow` 设置 `tk scaling` 并在 `WM_DPICHANGED` 时重排；托盘生成 16/20/24/32/40/48 多尺寸 ICO。
- **验证方式**：设置 125/150/200% 后重启会话，用放大镜/截图对比文字边缘；跨屏拖动观察是否重采样。

### P1-02 首启向导固定位置 + 固定尺寸，200% 下按钮出屏

- **现象**：首启窗口 `680×460` 固定在 `+160+120`（[first_run_gui.py](../scripts/first_run_gui.py#L367-L368)），最小 `620×420`。200% 时逻辑屏高 540（工作区约 520），窗口外框底边约在 611，底部“完成/退出”按钮落到工作区之外；用户必须先拖动窗口才能完成首启。
- **证据**：窗口与最小尺寸为代码常量；实测内容自然尺寸 502×400，说明问题不在内容，而在固定位置/尺寸。1920×1080@200% 逻辑屏 960×540；1366×768@150% 工作区约 472，窗口外框约 491，已开始裁切；1366×768@200% 工作区约 364，`minsize` 420 导致无法缩到可见范围，且无滚动条。
- **影响**：首次使用阻塞（非技术用户不知道可以拖动窗口），是最严重的高 DPI 可用性问题。
- **建议**：按 `winfo_screenwidth/height` 与工作区计算位置，尺寸取 `min(设计尺寸, 工作区)`；窗口可滚动或把底部按钮固定在底部；去掉超出屏幕的固定偏移。
- **验证方式**：1920×1080@200%、1366×768@150%/200% 三组实机走完首启。

### P2-01 状态窗口内容高于窗口，底部按钮/说明被裁

- **现象**：固定 `500×360+120+120`（[app.py](../src/desktop/app.py#L195)），内容实测需要 494×434；底部说明必被裁，按钮行也会被部分裁掉。
- **证据**：真实构建界面后 `winfo_reqheight()=434`；8 行信息 + 按钮 + 说明的排列在 [app.py](../src/desktop/app.py#L311-L348)。
- **影响**：与 DPI 无关，但在任何缩放档位都存在；“打开控制台/退出程序”按钮可能只露出一部分。
- **建议**：按内容自适应高度或提供滚动；长路径单独一行折叠/省略显示。

### P2-02 托盘图标单尺寸 16×16

- **现象**：150%/200% 托盘槽位为 24/32px，系统放大 16px 位图，图标发虚。
- **证据**：[tray.py](../src/desktop/tray.py#L81)、`:158`；生成后 ICO 仅 1 条目。
- **建议**：同 P1-01，输出多尺寸 ICO；品牌 `liuhao.ico` 也建议补 16/24/32/48/256 多尺寸（当前构建产物中仅 192×192 单条目）。

### P2-03 ARM64 Windows 11 被安装器错误拒绝

- **现象**：Inno 6.7.1 对 `ArchitecturesAllowed=x64` 给出弃用警告并替换为 `x64os`；`x64os` 不包含 ARM64，导致本可在 x64 模拟下运行的安装包被拒绝。
- **证据**：ISCC 实测警告 `"x64" is deprecated. Substituting "x64os"`；[liuhao.iss](../installer/liuhao.iss#L63)、`:67`。
- **影响**：ARM 设备用户完全无法安装，且提示只列 “x64”，看不出原因。
- **建议**：改为 `x64compatible`（Inno ≥6.3 语义）并在 ARM64 实机验证；若决定不支持 ARM，至少给出明确说明与替代方案。

### P2-04 英文/日文系统下安装器语言混排

- **现象**：`ShowLanguageDialog=no`（[liuhao.iss](../installer/liuhao.iss#L78)）+ `[Languages]` 先中文后英文：英文系统显示英文框架，但任务文案、`[Code]` 提示、安装前说明仍是中文；日文系统回落到中文向导。
- **证据**：`[Tasks]` 的 `Description` / `GroupDescription` 为中文硬编码；`[Code]` 的 `MsgBox` 为中文；`InfoBeforeFile` 仅中文。
- **影响**：不是乱码（Unicode），但英文用户会看到中英混排，日文用户看到全中文；容易被误判为安装器损坏。
- **建议**：把任务/提示抽到 `{cm:...}` 自定义消息并随语言切换；或明确声明“仅提供中文界面”，并在英文系统显示一行语言说明。

### P2-05 控制台默认形态/主题随缩放档位变化

- **现象**：视口 CSS 宽度随系统缩放缩小，`<1280` 变 web、`<768` 变 mobile；默认主题随形态变为浅色/深色。
- **证据**：[mode.ts](../apps/console/console/src/lib/mode.ts#L24-L25)、`mode.ts#L31-35`、`mode.ts#L68-70`。
- **影响**：不是渲染缺陷，但用户会感知为“改缩放后界面变了”；1366×768@200% 会在桌面看到手机布局。
- **建议**：产品层面接受并说明；或在设置里首次进入时提示可覆盖形态（已有覆盖能力）。

### P3-01 日期/数字格式中英混用

- **现象**：Shell/员工/升级面板固定 `zh-CN`，审计/事件/目标/文件/身份页跟随系统 locale。
- **证据**：见 3.4 的代码行。
- **影响**：英文/日文系统上同一页面群出现 12 小时制与 24 小时制混排；不影响功能。
- **建议**：统一走一个格式化模块，固定应用 locale 或完全跟随系统，二选一。

### P3-02 非中文区域重定向 stdout 时中文输出报错

- **现象**：`PYTHONIOENCODING=cp1252` 且重定向时，打印中文触发 `UnicodeEncodeError`；正常控制台（`WriteConsoleW`）不受影响。
- **证据**：实测 `print('鎏灏 AI OS 启动中')` 失败；`collect_diagnostics.bat` 存在 `> temp` 重定向。
- **影响**：英文系统上收集诊断/把输出重定向到文件时可能中断，普通双击使用不受影响。
- **建议**：启动时 `sys.stdout.reconfigure(errors='backslashreplace')`，或在 `.bat` 中设置 `PYTHONIOENCODING=utf-8`。

### P3-03 数据目录长度未做 MAX_PATH 预检

- **现象**：安装器只校验程序目录长度；数据目录只校验可写。中文用户名 + 深目录 + 工作区深层文件可能超过 260。
- **证据**：`python.exe` 有 `longPathAware=true`，但系统 `LongPathsEnabled` 默认关闭；安装脚本无数据目录长度校验。
- **影响**：可能在导入/生成文件时出现路径过长错误，错误信息偏底层。
- **建议**：安装时对“数据目录 + 最长工作区相对路径”做长度预检，或统一使用长路径前缀并提示开启系统长路径。

### P3-04 32 位/ARM/旧系统提示不清楚

- **现象**：32 位被 Inno 通用架构文案拦截；便携包无检查；Win7/8 未被 `MinVersion` 拦截。
- **证据**：见 3.6。
- **建议**：设置 `MinVersion=6.3`（Win8.1+），增加启动前环境自检与中文说明页；在 README/INSTALL 明确“x64、Win10/11、ARM 需 Win11 x64 模拟”。

### P3-05 文档与实际首启流程不一致

- **现象**：`docs\INSTALL-WINDOWS.md:41` 写“浏览器自动打开首启页面”，实际默认是 tkinter 图形向导（`scripts\launch_installed.py:26`、`:794`）。
- **影响**：用户按文档预期找浏览器页面，实际弹窗是桌面窗口；也会影响 DPI 问题排查。
- **建议**：更新文档，补充“高缩放下如按钮不可见，可拖动窗口标题栏上移”的临时说明，直到 P1-02 修复。

---

## 五、真机复核清单（建议按此验收）

1. **缩放**：分别在 125% / 150% / 200% 下重启会话，依次检查状态窗口、首启向导、托盘图标与菜单；
   - 记录：文字是否锐利、按钮是否完整可见、托盘图标是否发虚。
2. **多显示器**：主屏 150%、副屏 100%（或反之），把状态窗口和首启向导拖过去；
   - 记录：是否出现尺寸跳变、模糊、菜单位置偏移。
3. **语言**：英文与日文 Windows 各装一次（`/NOICONS`、临时目录）；
   - 记录：向导语言、任务文案语言、日期格式、中文是否显示为方块。
4. **用户名**：新建本地用户 `张三 三`（或设置 `LIUHAO_DATA_DIR` 到含中文/空格/`&`/`#` 的目录）后安装；
   - 记录：`data-dir.txt`、`launcher.log`、卸载是否正常。
5. **架构**：32 位虚拟机、ARM64 Win11 设备各跑一次安装包；
   - 记录：是否被拦截、提示文案是否可理解、是否能给出替代路径。

---

## 六、建议修复顺序（仅建议，本次未改代码）

1. P1-01：进程级 DPI 感知 + 托盘多尺寸图标（一次性解决“模糊”）。
2. P1-02：首启向导按工作区自适应/居中/可滚动（解决“点不到按钮”）。
3. P2-01：状态窗口按内容自适应（顺带解决长中文路径裁切）。
4. P2-03：`ArchitecturesAllowed=x64compatible` + ARM64 实机验证。
5. P2-04：安装器任务/提示本地化或明确中文声明。
6. P3-01/02/03/04/05：日期格式统一、重定向编码、长路径预检、旧系统拦截、文档更新。

---

## 七、附：本次关键探针命令（可复现）

```powershell
# 1) DPI 感知（在安装版 runtime 下执行）
runtime\python.exe -c "import ctypes; a=ctypes.c_int(); ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(a)); print(a.value)"

# 2) 临时目录安装（含中文/空格/&/#）
LiuHao-Setup-0.1.1.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS `
  /DIR="D:\tmp\lh-dpi-audit\安装 目录 & #1\LiuHao" `
  /DATA="D:\tmp\lh-dpi-audit\安装 目录 & #1\数据" `
  /LOG="D:\tmp\lh-dpi-audit\setup.log"

# 3) 静默卸载
"...\LiuHao\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

> 以上临时目录已删除；未修改注册表之外的系统状态，未改动产品源码。
