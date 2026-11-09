; ==========================================================================
; LiuHao AI OS · 鎏灏智能中枢 —— Windows 安装器（Inno Setup 6）
;
; 设计口径（与产品负责人确认的硬要求一致）：
;   1) 默认装到 D 盘：程序 D:\LiuHao，数据 D:\LiuHaoData —— **两者必须分离**
;      （数据不在程序目录里，卸载删程序目录不会带走账号与 Key）；
;   2) 数据目录默认写 D:\LiuHaoData，并在向导的「数据目录」页显式展示、可改选。
;      升级/覆盖安装时按「注册表 DataDir → data-dir.txt → 旧默认位置探测」找
;      已有目录：只要候选里已有账号/员工/记忆等真实数据，就必须优先老目录并
;      在日志写明来源；**绝不**在空路径上新建空库冒充老目录。记录到的目录都
;      不可用时让用户在这一页选择，没有可写目录就不放行安装。
;      程序目录 D 盘不可用时的回退保持原样：有管理员权限 → %ProgramFiles%\LiuHao，
;      否则 → %LOCALAPPDATA%\Programs\LiuHao（不需要提权、不会装到一半失败）；
;   3) 老用户（原先装在 C 盘）覆盖安装/升级默认沿用上次登记的位置（程序目录取
;      InstallLocation，数据目录取注册表 DataDir → data-dir.txt → 旧默认探测），
;      但会在「数据目录」页显示出来；用户可在这里改到别处（旧数据不会自动搬过去）；
;   4) 卸载默认**保留**用户数据，只有用户在卸载时明确勾选并要求二次确认，
;      才会按 marker 记录的实际数据目录删除（不再写死 C 盘那个路径）。
;
; 构建：packaging\build_windows_installer.ps1（会传 /DAppVersion=...）。
; 直接编译：iscc /DAppVersion=0.1.1 installer\liuhao.iss
; ==========================================================================

#ifndef AppVersion
  #define AppVersion "0.1.2"
#endif

#ifndef PayloadDir
  #define PayloadDir "..\dist\payload\app"
#endif

; 产物输出目录。默认仍是 ..\dist\installer（正式出包路径）；沙箱验证时用
; /DSetupOutputDir=D:\cache\temp\... 指到临时目录，保证验证不会在 dist\installer
; 里留下任何成品。ISCC 的 /O 参数在 6.x 上不会覆盖 [Setup] OutputDir，故用宏。
#ifndef SetupOutputDir
  #define SetupOutputDir "..\dist\installer"
#endif

; 0.1.0 及更早版本的默认数据目录（旧数据"原地"所在）。做成可覆盖的宏只为
; 验收：沙箱编译时用 /DLegacyDataDir=D:\cache\temp\... 指向临时目录，
; 避免把测试数据写进真实用户的 %LOCALAPPDATA%\LiuHao\data。正式包不传该宏，
; 取值与历史版本完全一致。
#ifndef LegacyDataDir
  #define LegacyDataDir "{localappdata}\LiuHao\data"
#endif

; 安装包内最深一条相对路径的字符数（由 packaging\build_windows_installer.ps1
; 在清理完 __pycache__ 等运行残留后实测注入）。用于在安装前预检 MAX_PATH：
; 目标目录 + 这条最深路径一旦超过 259 个字符，安装必然中途失败，必须提前
; 给中文可行动提示，而不是装到一半报错。手工直接编译时用保守默认值。
#ifndef MaxPayloadPathLen
  #define MaxPayloadPathLen "160"
#endif

#define AppName "LiuHao AI OS · 鎏灏智能中枢"
#define AppPublisher "LiuHao"
; GUID 只在这里维护一份：AppId 与卸载键路径都从它派生，避免两处写死后不同步
; （不同步会让升级读不到 DataDir，进而丢数据目录）。
; 沙箱验证会用 /DAppIdGuid=<另一个 GUID> 编译，避免测试安装写进真实产品的
; HKCU 卸载键；默认值必须保持真实产品 GUID 不变。
#ifndef AppIdGuid
  #define AppIdGuid "8F2B9C1E-4C0D-4A57-9D2E-6B1C3A5E7F90"
#endif
#define AppIdValue "{{" + AppIdGuid + "}"

[Setup]
AppId={#AppIdValue}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName=D:\LiuHao
DefaultGroupName=LiuHao
; D 盘可用时：装到 D:\LiuHao + 只写 HKCU，不需要管理员，UAC 不会弹。
; D 盘不可用时：回退到 %ProgramFiles%\LiuHao，[Code] 会带 /ALLUSERS 自重拉并提权。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousAppDir=yes
; 必须保持 no：升级时组名恒为 DefaultGroupName（或命令行 /GROUP=），
; 不沿用上一次安装记录的组名，避免旧记录把组名带偏。
; 注意：2026-11-08「验收安装改坏真实用户开始菜单」的**真正根因**是下一行
; 的程序组页被禁用（实测：DisableProgramGroupPage=yes 时 Inno 无视 /GROUP=），
; 这条 directive 只是防御，光改它并不足够。
UsePreviousGroup=no
; 不能用 yes。实测（Inno 6.7，最小脚本复现）：程序组页被禁用时，Inno 直接采用
; DefaultGroupName，命令行 /GROUP= 形同虚设；于是 /DIR=<临时目录> 的验收安装
; 也写进真实用户的 LiuHao 组，卸载时整组被删（用户看到「开始菜单没了」）。
; 改成「页存在但永远跳过」（见 [Code] 里的 ShouldSkipPage）：真实安装的向导
; 与以前一样不出现该页，同时 /GROUP= 能生效，验收安装落在独立组里。
DisableProgramGroupPage=no
DisableWelcomePage=no
AllowNoIcons=yes
OutputDir={#SetupOutputDir}
OutputBaseFilename=LiuHao-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; x64compatible：x64 Windows 与 ARM64 Windows 11（后者通过 x64 模拟）都允许。
; 写 x64 会被 Inno 6.3+ 当作已弃用的 x64os，把本可运行的 ARM64 机器拒之门外。
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
; 卸载列表里的图标也必须是我们自己的，否则会显示为默认空白图标。
UninstallDisplayIcon={app}\assets\liuhao.ico
; 安装前展示的中文说明：未签名、不含模型、数据保留策略。
InfoBeforeFile=assets\BEFORE-INSTALL.txt
; 图标由构建脚本从前端 PNG 自动转换；缺失时退回默认图标。
#if FileExists("assets\liuhao.ico")
SetupIconFile=assets\liuhao.ico
#endif
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
; 产品界面是中文，安装/卸载主体文案也必须中文，默认不弹语言选择。
ShowLanguageDialog=no
; 升级信任配置（公钥/清单地址）与备份根都要写进 HKCU\Environment，写完必须
; 广播环境变更，否则已在运行的 Explorer 不会刷新环境块，新启动的进程
; （含开始菜单拉起的托盘壳）拿不到刚写入的值，等于白写。
ChangesEnvironment=yes

[VersionInfo]
; 安装包 exe 自身的版本资源。不设时资源管理器里「文件版本/版权/原始文件名」
; 全是空白，审计与用户排障都无法确认拿到的是哪一版。
; VersionInfoVersion 必须是纯数字四段式，故补一个 .0。
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} 安装程序
VersionInfoCopyright=Copyright (C) 2026 {#AppPublisher}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoOriginalFileName=LiuHao-Setup-{#AppVersion}.exe

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; checkedonce：首次安装默认勾选，之后记住用户的选择。
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "launchnow"; Description: "安装完成后立即启动鎏灏"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
; 合规材料单独列示并带 uninsneveruninstall：卸载**保留**许可证记录（用户有权留档，
; LGPL 也要求接收者能继续持有许可证文本）。下方的递归条目必须用 Excludes 排除
; 这些同名文件，否则同一目标会被装两遍（Inno 报重复文件）。
; ISPP 的 #if 表达式里不能写 {#PayloadDir}（插入符只在脚本体里展开，预处理器
; 表达式会拿到字面量而恒为 false）；这里直接用 PayloadDir 变量。
#if FileExists(AddBackslash(PayloadDir) + "LICENSE")
Source: "{#PayloadDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion uninsneveruninstall
#endif
Source: "{#PayloadDir}\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion uninsneveruninstall
#if FileExists(AddBackslash(PayloadDir) + "sbom-cyclonedx.json")
Source: "{#PayloadDir}\sbom-cyclonedx.json"; DestDir: "{app}"; Flags: ignoreversion uninsneveruninstall
#endif
Source: "{#PayloadDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs uninsneveruninstall
; Excludes 里的前导反斜杠表示「只匹配 Source 根目录」：不带它时
; `licenses\*` 会递归匹配任意层级的 licenses 目录，把 runtime 下
; `*.dist-info\licenses\*`（lxml/aiohttp 等 109 个许可证全文）一并排除；
; 同理裸文件名 `LICENSE` 会匹配任意层级同名文件，把 dist-info 里的
; LICENSE 全删掉。实测：`\licenses\*` + `\LICENSE` 只排根级，嵌套仍入包。
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "\licenses\*,\LICENSE,\THIRD-PARTY-NOTICES.txt,\sbom-cyclonedx.json"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\BEFORE-INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; 覆盖升级只复制新清单里的文件，**不会删除**旧版留下、新版已排除的文件。
; 不在这里清掉就会出现「打包已经排除了，升级后程序目录里还留着」——
; config/production.yaml 就是这么被带回来的。完整的逐项清理由
; scripts/purge_installed_config.py 按同一份排除清单执行（见 [Run]），
; 这里先把已知的静态配置项兜底删掉，即使脚本执行失败也不会残留。
Type: files; Name: "{app}\config\production.yaml"
Type: files; Name: "{app}\config\docker.env"
Type: files; Name: "{app}\config\*.lock"
Type: filesandordirs; Name: "{app}\config\production"
Type: filesandordirs; Name: "{app}\config\external_anchor"
; 驾驶舱前端是 Vite 哈希产物（console\assets\index-<hash>.js|css）。覆盖升级时
; [Files] 只会写入新清单里的文件，旧哈希文件不会自动消失：磁盘上新旧 index-*.js
; 并存，一旦浏览器/缓存/回滚路径命中旧文件就会白屏或跑旧版界面。
; 这里在复制新文件前整目录清空 {app}\console\assets —— 只清程序目录里的前端
; 构建产物；用户数据目录是 {code:GetDataDir}（与 {app} 分离），不在此范围内。
Type: filesandordirs; Name: "{app}\console\assets"
; 开始菜单组是**固定名字**：上一次安装（包括用 /DIR= 指向临时目录的验收安装）
; 留下的快捷方式会原样指向旧目录，点开始菜单就拉起另一份副本、另一个数据目录。
; 安装/升级前整组清掉，随后由 [Icons] 按当前 {app} 重新创建。
Type: filesandordirs; Name: "{group}"

[Dirs]
; 数据目录由安装器创建，但卸载器永不删除（删除只在 [Code] 里二次确认后按 marker 执行）。
; 目录由 [Code] 解析：默认 D:\LiuHaoData，D 盘不可用或老安装时按实际位置。
Name: "{code:GetDataDir}"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\config"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\workspace"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\data"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\logs"; Flags: uninsneveruninstall
; 备份根由安装器显式下发（默认取数据目录所在盘 + \LiuHaoBackups），
; 不占系统盘、也不允许升级器静默退回数据目录。卸载永不删除已有备份。
Name: "{code:GetBackupRoot}"; Flags: uninsneveruninstall

; 开箱自动升级的信任锚。客户端只认两样东西即可自举：Ed25519 公钥
; （LIUHAO_UPGRADE_PUBLIC_KEY，base64）与清单地址（LIUHAO_UPGRADE_MANIFEST_URL）。
; 清单自带 manifest_signature + 单调 sequence，因此同一份配置可跨版本一直用，
; 不再需要每次发布下发清单哈希（LIUHAO_UPGRADE_MANIFEST_SHA256 已降级为可选
; 首次引导）。取值由构建脚本参数 -UpgradePublicKey / -UpgradeManifestUrl 注入；
; 没有注入时这两项被预处理剔除，安装后 source_configured=false、检查更新如实
; 返回 409——绝不写一个必然过期的占位值。
; 备份根（LIUHAO_UPGRADE_BACKUP_ROOT）始终下发：装完不需要用户手工设任何环境变量。
; 卸载时只删这几个值（uninsdeletevalue），不动用户其它环境变量。
[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "LIUHAO_UPGRADE_BACKUP_ROOT"; ValueData: "{code:GetBackupRoot}"; Flags: uninsdeletevalue
#ifdef UpgradePublicKey
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "LIUHAO_UPGRADE_PUBLIC_KEY"; ValueData: "{#UpgradePublicKey}"; Flags: uninsdeletevalue
#endif
#ifdef UpgradeManifestUrl
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "LIUHAO_UPGRADE_MANIFEST_URL"; ValueData: "{#UpgradeManifestUrl}"; Flags: uninsdeletevalue
#endif

[Icons]
; 正常入口一律接「托盘壳」（scripts\liuhao_desktop.py app）：
;   * 用 runtime\pythonw.exe，无控制台黑窗口；
;   * 点关闭按钮 = 最小化到托盘，网关继续运行；
;   * 托盘菜单可打开控制台 / 停止服务 / 退出，退出前会优雅收尾。
; 之前指向 launch_installed.py，只有进程没有托盘，关掉窗口服务就停——
; 这是用户报「双击后打开登录不进去 / 关窗就断」的近因之一。
; 首启向导已由 liuhao_desktop.py 在拉起网关前完成（没有账号不会硬启动）。
; LiuHao.bat 只保留为 --console 排障入口，不作为快捷方式目标。
; 三处入口统一引用安装目录里的 liuhao.ico。
Name: "{group}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\scripts\liuhao_desktop.py"" app"; WorkingDir: "{app}"; IconFilename: "{app}\assets\liuhao.ico"; Comment: "启动鎏灏（关窗后驻留托盘，服务继续运行）"
Name: "{group}\使用说明"; Filename: "{app}\START-HERE.txt"
; 离线合规入口：不联网也能看许可证全文与声明。打开程序根目录——licenses\、
; THIRD-PARTY-NOTICES.txt、sbom-cyclonedx.json 与 LICENSE 都在这一层。
Name: "{group}\许可证与合规材料（离线）"; Filename: "{sys}\explorer.exe"; Parameters: """{app}"""; Comment: "断网查看 licenses\（18 项全文）、THIRD-PARTY-NOTICES.txt、sbom-cyclonedx.json 与 LICENSE"
; 离线诊断入口：不启动网关也能出包，服务起不来时用户可直接双击。
Name: "{group}\收集诊断信息（离线）"; Filename: "{app}\scripts\collect_diagnostics.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\liuhao.ico"; Comment: "服务未启动也能采集脱敏诊断包到桌面"
; 手机访问：默认不监听，必须由用户点这里显式开启；开启后窗口保留配对码。
; 「放行防火墙」单独一条，走 UAC 只加 TCP 8090 / 专用网络 / 本子网 的入站规则，
; 不会放开其它端口，也不会开机自启。
Name: "{group}\手机访问（开启）"; Filename: "{app}\scripts\mobile_access.bat"; Parameters: "start"; WorkingDir: "{app}"; IconFilename: "{app}\assets\liuhao.ico"; Comment: "显式开启局域网手机访问；只有一次性配对码能进入，默认不监听"
Name: "{group}\手机访问（开启并放行防火墙）"; Filename: "{app}\scripts\mobile_access.bat"; Parameters: "start --open-firewall"; WorkingDir: "{app}"; IconFilename: "{app}\assets\liuhao.ico"; Comment: "同上；仅新增 TCP 8090（专用网络/本子网）入站规则，需 UAC 确认"
Name: "{group}\手机访问（关闭）"; Filename: "{app}\scripts\mobile_access.bat"; Parameters: "stop"; WorkingDir: "{app}"; IconFilename: "{app}\assets\liuhao.ico"; Comment: "停止中继监听并断开全部已配对设备"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\liuhao.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\scripts\liuhao_desktop.py"" app"; WorkingDir: "{app}"; IconFilename: "{app}\assets\liuhao.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\scripts\liuhao_desktop.py"" app"; WorkingDir: "{app}"; Description: "立即启动鎏灏（首次启动会进入向导）"; Flags: postinstall nowait skipifsilent; Tasks: launchnow
; 覆盖升级清理：按 packaging/stage_payload.py 的同一份排除清单，删除程序目录
; config/ 下“新版已不随包”的旧文件（production.yaml 等）。用 payload 自带运行时
; 执行，静默等待完成；失败不阻断安装（最坏情况只是残留一个已排除文件）。
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\scripts\purge_installed_config.py"" --root ""{app}"""; Flags: runhidden waituntilterminated
; 剪枝旧文件清理：新版 payload 剪掉了 pythonwin/demos/tests 等 1,200+ 个文件
; （发布形态约 940 个，不含构建机 venv 自带 .pyc），
; 但 [Files] 只增不删——老用户升级后会留下一批「清单外残留」。清单由**新包**
; 的 prune-manifest.json 提供（安装时已覆盖到 {app}），因此新版本恢复的文件
; 不会被误删；新版关闭剪枝时清单 enabled=false，本步骤空转。
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\scripts\purge_pruned_payload.py"" --root ""{app}"""; Flags: runhidden waituntilterminated

[UninstallDelete]
; 旧便携版在程序目录里留下的运行目录：安装版已在首次启动时搬走内容，
; 卸载时只清理「已经空了」的目录，不动任何文件（避免破坏数据）。
Type: dirifempty; Name: "{app}\logs"
Type: dirifempty; Name: "{app}\data"
Type: dirifempty; Name: "{app}\plugins"
; 安装时由 [Code] 写下的数据目录 marker（不在安装清单里，卸载器不会自动删）。
Type: files; Name: "{app}\data-dir.txt"
; ── 卸载不留「能直接登录的残迹」（无条件的运行痕迹，不问用户）──────────────
; run\instance.json 含进程 pid 与地址；config\current_url.txt 记录本次控制台地址；
; .liuhao-startup-probe-* 是启动探针临时文件；bootstrap.log 可能含异常堆栈与
; 路径；desktop.json 记录上次选中的数据目录。留着它们等于把「上次怎么登录、
; 数据在哪」写在磁盘上，卸载后照原样点两下就能连回账号。
Type: files; Name: "{app}\run\instance.json"
Type: files; Name: "{app}\bootstrap.log"
Type: files; Name: "{app}\安装位置说明.txt"
Type: files; Name: "{app}\.liuhao-startup-probe-*"
Type: files; Name: "{code:GetDataDir}\run\instance.json"
Type: files; Name: "{code:GetDataDir}\config\current_url.txt"
Type: files; Name: "{code:GetDataDir}\.liuhao-startup-probe-*"
; 旧数据目录（0.1.0 及更早的默认位置）里的同类探针：机器上可能同时存在新旧两个
; 数据目录，只按 {code:GetDataDir} 删会漏掉旧目录里的占位文件（本机实测 42 个）。
Type: files; Name: "{#LegacyDataDir}\.liuhao-startup-probe-*"
Type: files; Name: "{localappdata}\LiuHao\logs\bootstrap.log"
Type: files; Name: "{localappdata}\LiuHao\desktop.json"

[UninstallRun]
; 顺序必须是：先停进程 → 再关自启 → 最后清缓存/删文件。托盘或网关还在跑时，
; runtime\pythonw.exe / python.exe 被占用，文件删不掉，表现就是「卸载完成但
; 服务还在后台跑」。stop 是幂等命令，没有进程时立即返回；内部有 --timeout
; （默认 10s），超时强杀，因此 /SILENT 与交互模式都不会挂住。
Filename: "{app}\runtime\python.exe"; Parameters: "-m src.desktop stop --root ""{app}"" --timeout 10"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopLiuHao"
; 自启项必须走产品自己的幂等入口（src/desktop/autostart.py:disable），
; 与设置页/CLI 共用同一实现；不在这里手写注册表删键。
Filename: "{app}\runtime\python.exe"; Parameters: "-m src.desktop autostart disable --root ""{app}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "DisableAutostart"
; 运行期生成的字节码缓存不在安装清单内，卸载前先递归清掉，
; 否则 runtime/ 下会残留大量 .pyc。runhidden + waituntilterminated 保证
; 清理完成后再进入文件删除阶段。
; 用随包 python 而不是 PowerShell：'...' 字面量遇到路径里的单引号（O'Brien）
; 会提前闭合，& ^ ( ) 也有各自的转义规则；CreateProcess 直传 argv 最安全。
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\scripts\purge_pycache.py"" --root ""{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "PurgePycache"

[Code]
const
  { 安装实际占用约 200 MB，留出解压与日志余量后按 400 MB 预检。 }
  NEEDED_MB = 400;
  { 数据目录所在盘的剩余空间：首启要建库、写日志与审计链，留 100 MB 兜底。
     只查程序盘会在「数据目录选到另一个快满的盘」时安装成功、首启才失败。 }
  NEEDED_DATA_MB = 100;
  { Windows 单条路径上限（含文件名）为 260，257 之后已无安全余量。 }
  MAX_PATH_LIMIT = 259;
  { 安装包内最深相对路径长度，由构建脚本实测注入（见文件头 #define）。 }
  MAX_PAYLOAD_REL = {#MaxPayloadPathLen};
  { 数据目录里最深的运行期相对路径（data\observability\alerts.jsonl 等）长度。
     数据目录不参与安装复制，但首次启动就会在这些位置建文件；它同样受 Windows
     260 字符上限约束，所以安装前必须一起预检（实测 255 字符的数据目录在
     mkdir config 时就报 WinError 206）。 }
  DATA_REL_MAX = 30;
  { 安装/升级互斥：名字固定到产品 AppId，Global\ 覆盖全机全会话；普通用户拿不到
    Global 对象权限时退回 Local\（覆盖同会话重复双击，也就是真实并发来源）。 }
  SETUP_MUTEX_GLOBAL = 'Global\LiuHaoSetup-{#AppIdGuid}';
  SETUP_MUTEX_LOCAL = 'Local\LiuHaoSetup-{#AppIdGuid}';
  DUPLICATE_SETUP_MSG = '另一个安装/升级正在进行，请稍候。';
  ERROR_ALREADY_EXISTS = 183;

var
  SetupMutexHandle: THandle;
  PrevInstallDir: String;
  HasPrevInstall: Boolean;
  ResolvedAppDir: String;
  ResolvedDataDir: String;
  ProgramFallback: Boolean;
  DataFallback: Boolean;
  UninstallDataDir: String;
  { 数据目录选择页：默认值来自 ResolvedDataDir；D 盘不可用或原目录不可写时
    为空，用户必须自己选一个可写目录才能继续（不静默回退 C 盘）。 }
  DataDirPage: TInputDirWizardPage;
  NeedDataDirChoice: Boolean;
  DataDirProblem: String;
  { 数据目录页的「沿用了旧账号」提示只弹一次，别每点一次下一步就弹。 }
  ReuseNoticeShown: Boolean;

{ 卸载键名。AppId 含花括号，而 Pascal 里花括号是注释，所以用 Chr 拼出来。
  花括号必须**左右成对**（123 与 125）：Inno 用 AppId 原样加 "_is1" 做键名。
  少写 125 会得到一个不存在的键路径，RegWriteStringValue 静默失败——实测
  后果是卸载键里没有 DataDir，升级/卸载读不到用户选的数据目录。 }
function UninstallKeyPath: String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
            Chr(123) + '{#AppIdGuid}' + Chr(125) + '_is1';
end;

{ D: 是否是固定盘且当前用户可写（可移动盘 U 盘/光盘/网络盘一律视为不可用）。 }
function GetDriveTypeW(lpRootPathName: String): Cardinal;
external 'GetDriveTypeW@kernel32.dll stdcall';

{ 并发安装互斥：两个 setup.exe 同时启动时，只有第一个能拿到命名互斥，第二个必须
  在写 HKCU 卸载键之前退出，避免同一个 AppId 的卸载键被两份安装同时改写。 }
function CreateMutexW(lpMutexAttributes: Cardinal; bInitialOwner: Cardinal;
  lpName: String): THandle;
external 'CreateMutexW@kernel32.dll stdcall';

function CloseHandle(hObject: THandle): Boolean;
external 'CloseHandle@kernel32.dll stdcall';

{ Inno 的 [Code] 没有内建 GetLastError，只能显式声明。紧跟在 CreateMutexW 之后调用，
  才能可靠读到 ERROR_ALREADY_EXISTS。 }
function GetLastError: Cardinal;
external 'GetLastError@kernel32.dll stdcall';

{ 拿到互斥返回 True（句柄保留到进程退出，由系统回收）；已存在返回 False。 }
function AcquireSetupMutex: Boolean;
var
  LastErr: Cardinal;
begin
  Result := True;
  SetupMutexHandle := CreateMutexW(0, 0, SETUP_MUTEX_GLOBAL);
  { 必须在任何其它 WinAPI（包括 Log 的写文件）之前取错误码，否则 ERROR_ALREADY_EXISTS 会被覆盖。 }
  LastErr := GetLastError;
  if SetupMutexHandle = 0 then
  begin
    SetupMutexHandle := CreateMutexW(0, 0, SETUP_MUTEX_LOCAL);
    LastErr := GetLastError;
    if SetupMutexHandle <> 0 then
      Log('SetupMutex: Global\ 不可用，改用本会话互斥 ' + SETUP_MUTEX_LOCAL);
  end;
  if SetupMutexHandle = 0 then
  begin
    { 连 Local\ 都建不出来属于极罕见环境问题；不因此阻断安装，但留下日志。 }
    Log('SetupMutex: 互斥对象创建失败，跳过并发保护。');
    Exit;
  end;
  if LastErr = ERROR_ALREADY_EXISTS then
  begin
    CloseHandle(SetupMutexHandle);
    SetupMutexHandle := 0;
    Result := False;
    Log('SetupMutex: 另一个安装/升级正在进行，本实例退出。');
  end
  else
    Log('SetupMutex: 已取得互斥 ' + SETUP_MUTEX_GLOBAL);
end;

function DDriveUsable: Boolean;
var
  Probe: String;
begin
  Result := False;
  if GetDriveTypeW('D:\') <> 3 then
    Exit;
  Probe := 'D:\' + '.liuhao-write-probe.tmp';
  if SaveStringToFile(Probe, 'probe', False) then
  begin
    DeleteFile(Probe);
    Result := True;
  end;
end;

{ 读程序目录下的 data-dir.txt，兼容三种真实写法的历史文件：
  * UTF-8 带 BOM（当前安装器与首启向导写的格式）；
  * UTF-8 无 BOM（部分手工/脚本写出的 marker）；
  * 系统 ANSI（0.1.0 及更早，中文系统即 GBK），以及带尾随换行/空白的写法。

  实现要点（Inno 6.7 实测）：
  * UTF8Decode 可用，能把带/不带 BOM 的 UTF-8 正确解成 Unicode；
  * 对 ANSI 字节 UTF8Decode 不抛异常，而是把非法序列解成 U+FFFD —— 因此用
    UTF8Encode 回写做一次往返校验：能原样还原才算 UTF-8，否则按系统 ANSI 用
    String(Raw) 还原；
  * Chr() 只接受字节（Chr($4E2D) 会被截断成 0x2D），不能手写 UTF-8 解码。
  读到就用它；读不到返回空，由调用方按「没有记录」处理，绝不猜。 }
function ReadDataDirMarker(const Path: String): String;
var
  Raw: AnsiString;
  Text: String;
  Reencoded: AnsiString;
  HasBom: Boolean;
begin
  Result := '';
  if not FileExists(Path) then
    Exit;
  if not LoadStringFromFile(Path, Raw) then
    Exit;
  if Length(Raw) = 0 then
    Exit;
  HasBom := (Length(Raw) >= 3) and
            (Ord(Raw[1]) = $EF) and (Ord(Raw[2]) = $BB) and (Ord(Raw[3]) = $BF);
  if HasBom then
  begin
    Text := UTF8Decode(Raw);
    { UTF8Decode 会把 BOM 解成 U+FEFF，必须去掉，否则路径开头多一个不可见字符。 }
    if (Length(Text) > 0) and (Ord(Text[1]) = $FEFF) then
      Delete(Text, 1, 1);
  end
  else
  begin
    Text := UTF8Decode(Raw);
    Reencoded := UTF8Encode(Text);
    if Reencoded <> Raw then
      { 不是合法 UTF-8：按系统 ANSI 代码页还原（0.1.0 老标记）。 }
      Text := String(Raw);
  end;
  { 三种写法都可能带 CRLF / 末尾空格；统一 Trim 后再去掉成对引号。 }
  Text := Trim(Text);
  if (Length(Text) >= 2) and (Text[1] = '"') and (Text[Length(Text)] = '"') then
    Text := Trim(Copy(Text, 2, Length(Text) - 2));
  Result := Text;
end;

{ 新版安装器在卸载键里同时记录解析后的数据目录（REG_SZ，Unicode 原生，
  中文/空格/emoji 都能无损往返）。升级与卸载优先读它；旧版安装没有这个值，
  才回落到 ANSI marker。 }
function RegisteredDataDir: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, UninstallKeyPath, 'DataDir', Result) and (Result <> '') then
    Exit;
  Result := '';
  if RegQueryStringValue(HKLM, UninstallKeyPath, 'DataDir', Result) then
    Exit;
  Result := '';
end;

{ 给定路径所在的盘当前是否可访问（老安装的数据目录可能在已被拔掉的 D 盘上）。 }
function PathDriveUsable(const Path: String): Boolean;
var
  Drive: String;
begin
  Drive := ExtractFileDrive(Path) + '\';
  Result := GetDriveTypeW(Drive) <> 1;
end;

{ 找到所选目录最近的一个已存在父目录，用于写探测与查剩余空间。
  路径不存在时不能直接在目标位置写文件，否则会把“可创建”误判成“不可写”。 }
function WritableAncestor(const Dir: String): String;
var
  P: String;
  N: String;
begin
  P := Dir;
  while (P <> '') and (not DirExists(P)) do
  begin
    N := ExtractFileDir(P);
    if (N = '') or (N = P) then
    begin
      P := '';
      Break;
    end;
    P := N;
  end;
  Result := P;
end;

{ 已存在的目录是否真的可写（写探测文件再删）。目录不存在时**一律返回 False**：
  升级时如果 md 里记的旧目录已被拔盘/删掉，把它当成"可写"会让安装器在空路径上
  新建一个空库并继续用 —— 用户表现就是「升级完东西全没了」。需要允许"创建新
  目录"的场景请用 DirCreatable。 }
function DirWritable(const Dir: String): Boolean;
var
  Probe: String;
begin
  Result := False;
  if not DirExists(Dir) then
    Exit;
  Probe := Dir + '\' + '.liuhao-write-probe.tmp';
  if SaveStringToFile(Probe, 'probe', False) then
  begin
    DeleteFile(Probe);
    Result := True;
  end;
end;

{ 目录（或其最近的已存在父目录）是否可写：允许目标目录尚不存在（全新安装、
  /DATA= 指向新目录时由安装器创建）。只用于"创建新目录"的校验。 }
function DirCreatable(const Dir: String): Boolean;
var
  Base: String;
  Probe: String;
begin
  Result := False;
  Base := WritableAncestor(Dir);
  if Base = '' then
    Exit;
  Probe := Base + '\' + '.liuhao-write-probe.tmp';
  if SaveStringToFile(Probe, 'probe', False) then
  begin
    DeleteFile(Probe);
    Result := True;
  end;
end;

{ 所选目录所在盘剩余空间（MB）；无法获取时返回 -1。 }
function DriveFreeMB(const Dir: String): Int64;
var
  Base: String;
  FreeBytes: Int64;
  TotalBytes: Int64;
begin
  Result := -1;
  Base := WritableAncestor(Dir);
  if Base = '' then
  begin
    Base := ExtractFileDrive(Dir);
    if Base = '' then
      Exit;
    Base := Base + '\';
  end;
  if GetSpaceOnDisk64(Base, FreeBytes, TotalBytes) then
    Result := FreeBytes div (1024 * 1024);
end;

{ 判断数据目录里是否真的有用户内容，避免对空目录做无意义的删除询问。 }
function DataDirHasContent(const Dir: String): Boolean;
var
  FindRec: TFindRec;
  Found: Boolean;
begin
  Found := False;
  if FindFirst(Dir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          Found := True;
        end;
      until Found or not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  Result := Found;
end;

{ 目录里是否已有账号 / 员工 / 记忆这类**真实用户数据**。

  只看"目录存在"或"目录非空"不够：D:\LiuHaoData 可能是上一次安装刚建的空壳
  （config/、logs/ 都在，但没有账号）。判断真实数据的锚点是随包代码实际落盘的
  文件名（见 scripts/launch_installed.py 的环境变量与 src/upgrade/backups.py）：
  * config\human_identities.json / auth_secrets.json —— 账号与登录凭据；
  * workspace\liuhao_employees.json —— 员工；
  * memory_store.db / conversation_store.db / audit_store.db —— 记忆、对话、审计。

  注意：单独一个 config\integrity.key **不算**用户数据。它只是完整性校验材料，
  骨架目录（上一次安装建了 config\ 但没登录过）里也可能有；把它当"有用户数据"
  会让重装选中空骨架、连不上真实账号，旧凭据变孤儿。 }
function DataDirHasUserData(const Dir: String): Boolean;
begin
  Result := False;
  if not DirExists(Dir) then
    Exit;
  Result :=
    FileExists(Dir + '\config\human_identities.json') or
    FileExists(Dir + '\config\auth_secrets.json') or
    FileExists(Dir + '\workspace\liuhao_employees.json') or
    FileExists(Dir + '\memory_store.db') or
    FileExists(Dir + '\conversation_store.db') or
    FileExists(Dir + '\audit_store.db');
end;

{ 目录存在、所在盘可访问、且当前账户可写。升级沿用旧目录时必须三项都成立。 }
function ExistingWritable(const Dir: String): Boolean;
begin
  Result := (Dir <> '') and DirExists(Dir) and PathDriveUsable(Dir) and DirWritable(Dir);
end;

{ 在"上次记录/可探测"的候选目录里挑一个可沿用的，返回空表示没有。

  两轮优先级：
  1. 已存在、可写、且**确实有用户数据** —— 这是必须优先的老目录；即使另一个
     候选是"存在但空"的新目录，也不能选它（否则就是「升级完东西全没了」）。
  2. 已存在且可写（数据可能只是刚跑过一次、还没有账号），作为次优沿用。
  Source 返回命中的来源（注册表 / 标记文件 / 探测），供日志写明依据。 }
function PickRecordedDataDir(const RegData, FileData, LegacyData: String;
  var Source: String): String;
var
  Candidates: TArrayOfString;
  Names: TArrayOfString;
  I: Integer;
begin
  Result := '';
  Source := '';
  SetArrayLength(Candidates, 3);
  SetArrayLength(Names, 3);
  Candidates[0] := RegData;    Names[0] := '注册表';
  Candidates[1] := FileData;   Names[1] := '标记文件';
  Candidates[2] := LegacyData; Names[2] := '探测';
  for I := 0 to 2 do
    if ExistingWritable(Candidates[I]) and DataDirHasUserData(Candidates[I]) then
    begin
      Result := Candidates[I];
      Source := Names[I];
      Exit;
    end;
  for I := 0 to 2 do
    if ExistingWritable(Candidates[I]) then
    begin
      Result := Candidates[I];
      Source := Names[I];
      Exit;
    end;
end;

{ 上一次安装登记的安装位置（HKCU 优先，再 HKLM）。 }
function PreviousInstallLocation: String;
var
  Dir: String;
begin
  Result := '';
  Dir := '';
  if RegQueryStringValue(HKCU, UninstallKeyPath, 'InstallLocation', Dir) and DirExists(Dir) then
    Result := Dir
  else if RegQueryStringValue(HKLM, UninstallKeyPath, 'InstallLocation', Dir) and DirExists(Dir) then
    Result := Dir;
end;

{ 读命令行里的 /NAME=value（用于 /DIR、/DATA 与自重拉标记）。 }
function CmdParamValue(const Name: String): String;
var
  I: Integer;
  P: String;
begin
  Result := '';
  for I := 1 to ParamCount do
  begin
    P := RemoveQuotes(ParamStr(I));
    if CompareText(Copy(P, 1, Length(Name) + 1), Name + '=') = 0 then
      Result := RemoveQuotes(Copy(P, Length(Name) + 2, Length(P)));
  end;
end;

{ 程序组页永远不显示：组名固定（真实安装 = DefaultGroupName=LiuHao），用户没有可
  选择的内容。但页必须「存在」——一旦用 DisableProgramGroupPage=yes 把页关掉，
  Inno 就会无视命令行 /GROUP=，验收安装会写进真实用户的 LiuHao 组并在卸载时把
  整组删掉。ShouldSkipPage 只是跳过展示，/GROUP= 仍照常生效。 }
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectProgramGroup);
end;

function InitializeSetup: Boolean;
var
  Marker: String;
  RegData: String;
  LegacyData: String;
  CmdDir: String;
  CmdData: String;
  SideBySide: Boolean;
  Picked: String;
  Source: String;
begin
  Result := True;

  { 第一件事就是抢互斥：必须在读/写 HKCU 卸载键、探测目录之前完成，第二个实例
    才能干净退出，不留下半写的注册表状态。 }
  if not AcquireSetupMutex then
  begin
    { 交互安装时明确告知；静默安装（/SILENT、/VERYSILENT）下不再弹框，
      直接以「已存在另一个安装」退出，避免自动化/无人值守场景被对话框挂住。 }
    if not WizardSilent then
      MsgBox(DUPLICATE_SETUP_MSG, mbError, MB_OK);
    Result := False;
    Exit;
  end;

#ifdef ProbeHoldSeconds
  { 仅并发自测构建（/DProbeHoldSeconds=N）启用：拿到互斥后停住 N 秒，让第二个
    实例有稳定重叠窗口；然后不安装直接退出。正式构建不会包含这段代码。 }
  Log('PROBE: 已取得互斥，停住 {#ProbeHoldSeconds} 秒供第二个实例撞击。');
  Sleep({#ProbeHoldSeconds} * 1000);
  Result := False;
  Exit;
#endif

  LegacyData := ExpandConstant('{#LegacyDataDir}');

  PrevInstallDir := PreviousInstallLocation;
  HasPrevInstall := PrevInstallDir <> '';

  { ---- 程序目录：老安装保持原路径；全新安装优先 D:\LiuHao ---- }
  CmdDir := CmdParamValue('/DIR');
  if HasPrevInstall then
    ResolvedAppDir := PrevInstallDir
  else if CmdDir <> '' then
    ResolvedAppDir := CmdDir
  else if DDriveUsable then
    ResolvedAppDir := 'D:\LiuHao'
  else
  begin
    { Inno 6.7 的外层加载器独占原始安装包，Setup 无法自重拉（Exec 会被拒绝，
       错误码 5），所以这里不重启：有管理员权限就装真正的 Program Files，
       否则退到当前用户可写的 autopf 目录（非管理员 = %LOCALAPPDATA%\Programs）。
       两条都不需要提权、不会装到一半失败。 }
    if IsAdmin then
      ResolvedAppDir := ExpandConstant('{commonpf}\LiuHao')
    else
      ResolvedAppDir := ExpandConstant('{autopf}\LiuHao');
    ProgramFallback := True;
  end;

  { ---- 数据目录：必须与程序目录分离；默认 D:\LiuHaoData ---- }
  { 解析顺序（覆盖安装丢数据目录的完整修复）：
    1. /DATA= 显式指定，且该目录已有真实用户数据 —— 直接用它；
    2. /DATA= 指向不存在/空目录：优先沿用**已存在且确有用户数据**的记录目录
       （注册表 DataDir → 标记文件 → 旧默认位置探测），日志写「沿用已有数据目录」；
    3. 没带 /DATA= 的升级走同一套"沿用"逻辑；
    4. 全新安装默认 D:\LiuHaoData；只有它没有真实数据、而旧默认位置有时才用旧
       位置（兜底，防止以后再出现别的路径算错）；
    5. 记录到的目录都不存在/不可用时让用户选择，**绝不**在空路径上新建空库冒充
       老目录 —— 那就是「升级完东西全没了」。 }
  CmdData := CmdParamValue('/DATA');
  { 显式 /DIR 指向**另一个**程序目录 = 并装（side-by-side）或验收安装，不是升级。
    此时不能沿用本机已登记的老数据目录：那会让「装到新目录」的实例去读旧目录，
    现场表现为「新装的程序里账号/数据全不是自己的」，验收也永远拿不到干净的
    数据目录。只有 /DATA 明确指向已有用户数据的目录时才尊重它。 }
  SideBySide := HasPrevInstall and (CmdDir <> '') and
    (CompareText(RemoveBackslashUnlessRoot(CmdDir), RemoveBackslashUnlessRoot(PrevInstallDir)) <> 0);
  if SideBySide then
    Log('并装模式：/DIR=' + CmdDir + '，与已安装的程序目录 ' + PrevInstallDir + ' 不同，不沿用旧数据目录。');
  DataFallback := False;
  NeedDataDirChoice := False;
  DataDirProblem := '';

  RegData := '';
  Marker := '';
  if HasPrevInstall then
  begin
    RegData := RegisteredDataDir;
    { 旧版 marker 可能是 ANSI / UTF-8 无 BOM / 带 BOM / 带换行，ReadDataDirMarker 全兼容。 }
    Marker := ReadDataDirMarker(PrevInstallDir + '\data-dir.txt');
  end;

  if CmdData <> '' then
  begin
    if DirExists(CmdData) and DataDirHasUserData(CmdData) then
      ResolvedDataDir := CmdData
    else
    begin
      Picked := '';
      Source := '';
      if HasPrevInstall and (not SideBySide) then
        Picked := PickRecordedDataDir(RegData, Marker, LegacyData, Source);
      if Picked <> '' then
      begin
        ResolvedDataDir := Picked;
        Log('沿用已有数据目录：' + Picked + '（来源：' + Source +
            '）；/DATA= 指定的 ' + CmdData + ' 没有已有用户数据。');
      end
      else if DirExists(CmdData) then
        { 目录真实存在但还没有用户数据，且没有老数据可保护：尊重显式选择。 }
        ResolvedDataDir := CmdData
      else if DirCreatable(CmdData) then
        { 全新安装 / 机器上确实没有老数据：/DATA= 指向的新目录允许创建。 }
        ResolvedDataDir := CmdData
      else
      begin
        ResolvedDataDir := '';
        NeedDataDirChoice := True;
        DataFallback := True;
        DataDirProblem := '/DATA= 指定的目录不可用：' + CmdData +
          '（所在磁盘不存在或当前账户不可写）。';
      end;
    end;
  end
  else if HasPrevInstall then
  begin
    Source := '';
    Picked := PickRecordedDataDir(RegData, Marker, LegacyData, Source);
    if Picked <> '' then
    begin
      ResolvedDataDir := Picked;
      if DataDirHasUserData(Picked) then
        Log('沿用已有数据目录：' + Picked + '（来源：' + Source + '）')
      else
        Log('使用上次记录的数据目录：' + Picked + '（来源：' + Source +
            '；目录里还没有用户数据）');
    end
    else
    begin
      ResolvedDataDir := '';
      NeedDataDirChoice := True;
      DataFallback := True;
      DataDirProblem := '这台机器上记录的数据目录都不存在或不可用' +
        '（注册表 DataDir、data-dir.txt 与旧默认位置都没有可用目录）。' +
        '为避免把新空目录当成老目录，安装器不会自动新建空库；请手动选择。';
      Log('未找到可沿用的数据目录：' + DataDirProblem);
    end;
  end
  else if DirExists(LegacyData) and DataDirHasUserData(LegacyData) and
          (not (DirExists('D:\LiuHaoData') and DataDirHasUserData('D:\LiuHaoData'))) then
  begin
    { 全新安装的兜底：D:\LiuHaoData 还没有真实数据，而旧默认位置有 —— 优先老目录。 }
    ResolvedDataDir := LegacyData;
    Log('沿用已有数据目录：' + LegacyData + '（来源：探测）');
  end
  else if DDriveUsable and DirCreatable('D:\LiuHaoData') then
  begin
    { 全新安装：D 盘可创建就必须落在 D:\LiuHaoData。 }
    ResolvedDataDir := 'D:\LiuHaoData';
  end
  else
  begin
    ResolvedDataDir := '';
    NeedDataDirChoice := True;
    DataFallback := True;
    DataDirProblem := '这台机器上 D 盘不可用（不存在 / 是可移动盘），' +
      '或 D:\LiuHaoData 当前不可写，不能默认把数据放在 D:\LiuHaoData。';
  end;

  { ---- D 盘不可用时程序目录已回退到 Program Files / 用户 Programs，见上；
         这里不再重启安装器（Inno 6.7 加载器独占自身 exe，自重拉会被拒绝）。 ---- }
  if ProgramFallback then
    Log('D 盘不可用，程序目录回退到 ' + ResolvedAppDir + '。');
  if NeedDataDirChoice then
    Log('数据目录需要用户选择：' + DataDirProblem);

end;

function GetDataDir(Param: String): String;
begin
  Result := ResolvedDataDir;
end;

{ 显示用：注册表里的 InstallLocation 常带尾随反斜杠，避免界面上出现 "D:\LiuHao\"。 }
function DirForDisplay(const Dir: String): String;
begin
  Result := Dir;
  if (Length(Result) > 3) and (Copy(Result, Length(Result), 1) = '\') then
    SetLength(Result, Length(Result) - 1);
end;

{ 0.1.0 及更早版本的默认数据目录（没有 marker 时唯一可能的数据在原地）。 }
function DefaultLegacyDataDir: String;
begin
  Result := ExpandConstant('{#LegacyDataDir}');
end;

{ 去尾随反斜杠后按 Windows 规则（大小写不敏感）比较两个目录。 }
function TrimTrailingSlash(const Dir: String): String;
begin
  Result := Dir;
  while (Length(Result) > 3) and (Result[Length(Result)] = '\') do
    SetLength(Result, Length(Result) - 1);
end;

function SameDir(const Left, Right: String): Boolean;
begin
  Result := CompareText(TrimTrailingSlash(Left), TrimTrailingSlash(Right)) = 0;
end;

{ 备份根：升级线明确要求由安装器下发，不允许默认落 C 盘，也不允许升级器
  静默退回数据目录。取值优先级：
    1) 命令行 /BACKUPS=目录；
    2) 构建参数 -UpgradeBackupRoot 注入的预处理宏；
    3) 数据目录所在盘 + \LiuHaoBackups（本项目即 D:\LiuHaoBackups）。
  这里只负责选路径；「默认推导结果落系统盘」的拒绝在 PrepareToInstall 里做。 }
function GetBackupRoot(Param: String): String;
var
  CliPath: String;
begin
  CliPath := Trim(ExpandConstant('{param:BACKUPS|}'));
  if CliPath <> '' then
  begin
    Result := TrimTrailingSlash(CliPath);
    Exit;
  end;
#ifdef UpgradeBackupRoot
  Result := TrimTrailingSlash(ExpandConstant('{#UpgradeBackupRoot}'));
#else
  Result := TrimTrailingSlash(ExtractFileDrive(ResolvedDataDir) + '\LiuHaoBackups');
#endif
end;

{ 检查 Windows 目录名本身的合法性：保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）
  与以点或空格结尾的段。这两类名字在 Win32 层有"别名"语义：

  * `DATA.` / `DATA ` 会被系统自动去掉尾点/尾空格，文件其实落在 `DATA`；
    安装器记下的 marker 与真实目录不同，后续升级就会对不上；
  * `CON`/`NUL` 等是设备名，CreateDir 报英文 "目录名称无效" 或干脆"成功"
    但随后建子目录失败（实测 WinError 267 / 3）。

  用户从向导里选不出可行动的处置办法，所以必须在安装前用中文拒绝。
  返回空字符串表示没有发现问题。 }
function DirNameIssue(const Dir: String): String;
var
  Head, Part, Stem: String;
  Index, Dot: Integer;
begin
  Result := '';
  Head := Dir;
  while Length(Head) > 3 do
  begin
    Index := Length(Head);
    while (Index > 0) and (Copy(Head, Index, 1) <> '\') do
      Index := Index - 1;
    if Index = 0 then
      Break;
    Part := Copy(Head, Index + 1, Length(Head) - Index);
    if Part <> '' then
    begin
      if (Copy(Part, Length(Part), 1) = '.') or
         (Copy(Part, Length(Part), 1) = ' ') then
      begin
        Result := '「' + Part + '」以点或空格结尾。Windows 会自动去掉结尾的点/空格，' +
          '文件实际落在另一个名字下，安装器记录的路径就会对不上。';
        Exit;
      end;
      Stem := Part;
      Dot := Pos('.', Stem);
      if Dot > 0 then
        Stem := Copy(Stem, 1, Dot - 1);
      if CompareText(Stem, 'CON') = 0 then
        Result := '「' + Part + '」是 Windows 保留设备名（CON），不能用作目录名。'
      else if CompareText(Stem, 'PRN') = 0 then
        Result := '「' + Part + '」是 Windows 保留设备名（PRN），不能用作目录名。'
      else if CompareText(Stem, 'AUX') = 0 then
        Result := '「' + Part + '」是 Windows 保留设备名（AUX），不能用作目录名。'
      else if CompareText(Stem, 'NUL') = 0 then
        Result := '「' + Part + '」是 Windows 保留设备名（NUL），不能用作目录名。'
      else if (CompareText(Stem, 'COM1') = 0) or (CompareText(Stem, 'COM2') = 0) or
              (CompareText(Stem, 'COM3') = 0) or (CompareText(Stem, 'COM4') = 0) or
              (CompareText(Stem, 'COM5') = 0) or (CompareText(Stem, 'COM6') = 0) or
              (CompareText(Stem, 'COM7') = 0) or (CompareText(Stem, 'COM8') = 0) or
              (CompareText(Stem, 'COM9') = 0) or (CompareText(Stem, 'LPT1') = 0) or
              (CompareText(Stem, 'LPT2') = 0) or (CompareText(Stem, 'LPT3') = 0) or
              (CompareText(Stem, 'LPT4') = 0) or (CompareText(Stem, 'LPT5') = 0) or
              (CompareText(Stem, 'LPT6') = 0) or (CompareText(Stem, 'LPT7') = 0) or
              (CompareText(Stem, 'LPT8') = 0) or (CompareText(Stem, 'LPT9') = 0) then
        Result := '「' + Part + '」是 Windows 保留设备名（COM/LPT 端口），不能用作目录名。';
      if Result <> '' then
        Exit;
    end;
    SetLength(Head, Index - 1);
  end;
end;

{ Dir 是否位于 Parent 之下（含相等）。用于禁止把数据目录放进程序目录。 }
function IsSameOrChildDir(const Dir, Parent: String): Boolean;
var
  Base: String;
  Head: String;
begin
  Base := TrimTrailingSlash(Parent);
  Head := TrimTrailingSlash(Dir);
  if Length(Head) < Length(Base) then
  begin
    Result := False;
    Exit;
  end;
  Result := CompareText(Copy(Head, 1, Length(Base)), Base) = 0;
  if Result and (Length(Head) > Length(Base)) then
    Result := Head[Length(Base) + 1] = '\';
end;

{ 数据目录不允许落在 Program Files（含 autopf：非管理员时是
  %LOCALAPPDATA%\Programs）。那里是程序目录，卸载/升级会被一起清理；
  用户数据必须与程序目录分开。 }
function IsProtectedProgramDir(const Dir: String): Boolean;
begin
  Result :=
    IsSameOrChildDir(Dir, ExpandConstant('{commonpf}')) or
    IsSameOrChildDir(Dir, ExpandConstant('{commonpf32}')) or
    IsSameOrChildDir(Dir, ExpandConstant('{commonpf64}')) or
    IsSameOrChildDir(Dir, ExpandConstant('{autopf}'));
end;

{ 可移动盘 / 网络盘的数据目录提示（非阻断，用户确认后仍可继续）。 }
function DriveKindHint(const Dir: String): String;
var
  Drive: String;
  Kind: Cardinal;
begin
  Result := '';
  Drive := ExtractFileDrive(Dir);
  if Drive = '' then
    Exit;
  Kind := GetDriveTypeW(Drive + '\');
  if Kind = 2 then
    Result := '这个目录在可移动磁盘（U 盘 / 移动硬盘）上：设备拔掉后鎏灏会找不到数据，无法启动。' + #13#10 +
      '建议改选本机固定磁盘上的目录。'
  else if Kind = 4 then
    Result := '这个目录在网络盘上：断网或对方关机后鎏灏会找不到数据，无法启动。' + #13#10 +
      '建议改选本机固定磁盘上的目录。';
end;

procedure InitializeWizard;
var
  Welcome: String;
begin
  { UsePreviousAppDir 已把安装目录填成上一次的位置；只有全新安装才由我们替换。 }
  if (not HasPrevInstall) and ProgramFallback then
    WizardForm.DirEdit.Text := ResolvedAppDir;

  { 数据目录页：始终展示。用户能看到数据到底落在哪，也能改选。
    解析不出可用目录时预填旧默认位置（%LOCALAPPDATA%\LiuHao\data），而不是留空
    —— 留空会让很多人卡在这一页；路径必须显示出来，用户仍可自行改选。 }
  DataDirPage := CreateInputDirPage(wpSelectDir,
    '数据目录（账号与聊天记录）',
    '数据目录与程序目录分开保存；卸载默认保留，升级/重装不会覆盖你的数据。',
    '默认 D:\LiuHaoData。请选择一个当前 Windows 账户可写的目录，然后点「下一步」。',
    False, '');
  DataDirPage.Add('数据目录：');
  if ResolvedDataDir <> '' then
    DataDirPage.Values[0] := ResolvedDataDir
  else
    DataDirPage.Values[0] := DefaultLegacyDataDir;

  { 首屏先回答两个最容易让人犹豫的问题：自签名怎么继续、装到哪/数据在哪。
    详细说明仍在 BEFORE-INSTALL.txt（向导会自动展示该页）。 }
  if ResolvedDataDir <> '' then
    Welcome :=
      '本安装包使用项目自签名证书（签名者：CN=鎏灏智能中枢），未由公开权威 CA 签发；Windows 可能提示「未知发布者」或「Windows 已保护你的电脑」。' + #13#10 +
      '这是自签名证书的正常现象：点「更多信息」→「仍要运行」即可继续；随包的「信任签名证书.cmd」可消除本机提示。' + #13#10#13#10 +
      '程序装到：' + DirForDisplay(ResolvedAppDir) + #13#10 +
      '数据放在：' + DirForDisplay(ResolvedDataDir) + '（账号与聊天记录；卸载默认保留）'
  else
    Welcome :=
      '本安装包使用项目自签名证书（签名者：CN=鎏灏智能中枢），未由公开权威 CA 签发；Windows 可能提示「未知发布者」或「Windows 已保护你的电脑」。' + #13#10 +
      '这是自签名证书的正常现象：点「更多信息」→「仍要运行」即可继续；随包的「信任签名证书.cmd」可消除本机提示。' + #13#10#13#10 +
      '程序装到：' + DirForDisplay(ResolvedAppDir) + #13#10 +
      '数据目录：请在「数据目录」页确认（已预填 ' +
      DirForDisplay(DefaultLegacyDataDir) + '，可自行改选）。';
  if ProgramFallback then
    Welcome := Welcome + #13#10#13#10 +
      '因为这台机器上 D 盘不可用（不存在 / 不可写 / 是可移动盘），' +
      '程序目录已改到上面这个无需管理员权限的位置。';
  if NeedDataDirChoice then
    Welcome := Welcome + #13#10#13#10 +
      DataDirProblem + #13#10 +
      '「数据目录」页已预填 ' + DirForDisplay(DefaultLegacyDataDir) +
      '；请确认或改选一个可写目录，否则无法继续安装。';
  if (not NeedDataDirChoice) and (not SameDir(ResolvedDataDir, DefaultLegacyDataDir)) and
     DirExists(DefaultLegacyDataDir) and DataDirHasContent(DefaultLegacyDataDir) then
    Welcome := Welcome + #13#10#13#10 +
      '提示：检测到 C 盘旧默认位置已有数据（' + DefaultLegacyDataDir + '）。' +
      '本次仍使用上面的数据目录；如需沿用旧数据，可在「数据目录」页改成那个位置。';
  WizardForm.WelcomeLabel2.Caption := Welcome;
  { 默认高度只够两三行，这里内容更长，按行数放开高度以免被裁切。 }
  WizardForm.WelcomeLabel2.Height := ScaleY(190);
end;

{ 离开「目录选择」与「数据目录」页时校验，并把用户真正填的值写回全局变量。
  返回 False 会让向导停在本页，提示必须可行动（改到哪、为什么不行）。 }
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: String;
  Prev: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    ResolvedAppDir := WizardForm.DirEdit.Text;
    Exit;
  end;

  if (DataDirPage = nil) or (CurPageID <> DataDirPage.ID) then
    Exit;

  Dir := Trim(DataDirPage.Values[0]);
  if Dir = '' then
  begin
    SuppressibleMsgBox('请先选择数据目录。' + #13#10#13#10 +
      '账号与聊天记录都保存在这里；它不能为空，也默认不放 C 盘。',
      mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  if IsSameOrChildDir(Dir, WizardForm.DirEdit.Text) then
  begin
    SuppressibleMsgBox('数据目录不能放在程序目录里：' + #13#10 + Dir + #13#10#13#10 +
      '卸载或升级程序目录时会连带影响数据。请选择程序目录以外的位置' +
      '（默认 D:\LiuHaoData）。', mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  if IsProtectedProgramDir(Dir) then
  begin
    SuppressibleMsgBox('数据目录不能放在 Program Files 里：' + #13#10 + Dir + #13#10#13#10 +
      '那里属于程序目录，卸载、升级或系统清理都可能删掉你的账号与记忆。' +
      '请改选一个普通数据目录（默认 D:\LiuHaoData）。', mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  if DirNameIssue(Dir) <> '' then
  begin
    SuppressibleMsgBox('这个数据目录名不可用：' + #13#10 + Dir + #13#10#13#10 +
      DirNameIssue(Dir) + #13#10 +
      '请改选一个普通目录名（不要用 CON/AUX/NUL 这类设备名，也不要以点或空格结尾）。',
      mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  if Length(Dir) + 1 + DATA_REL_MAX > MAX_PATH_LIMIT then
  begin
    SuppressibleMsgBox('数据目录路径过长：' + #13#10 + Dir + #13#10#13#10 +
      '数据目录里最深的文件还要再占用 ' + IntToStr(DATA_REL_MAX) + ' 个字符，' +
      '当前总长 ' + IntToStr(Length(Dir) + 1 + DATA_REL_MAX) + ' 个字符，' +
      '超过 Windows 的 ' + IntToStr(MAX_PATH_LIMIT + 1) + ' 个字符上限，' +
      '首次启动就无法写入。' + #13#10 +
      '请改选更短的目录（例如 D:\LiuHaoData）。', mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  if not DirCreatable(Dir) then
  begin
    SuppressibleMsgBox('这个数据目录当前不可写：' + #13#10 + Dir + #13#10#13#10 +
      '请改选一个你有写权限的目录（例如 D:\LiuHaoData）。' + #13#10 +
      '如果确实必须写到这里，请关闭安装器，右键安装包选择「以管理员身份运行」后重试。',
      mbError, MB_OK, IDOK);
    Result := False;
    Exit;
  end;
  if DriveKindHint(Dir) <> '' then
  begin
    if MsgBox(DriveKindHint(Dir) + #13#10#13#10 + '仍要使用这个目录吗？',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  end;
  { 换目录的后果必须说清楚：旧目录**不会搬走也不会删除**，新目录是全新空环境。 }
  Prev := RegisteredDataDir;
  if (Prev <> '') and (not SameDir(Prev, Dir)) and DataDirHasUserData(Prev) then
  begin
    if MsgBox('你换了一个新的数据目录：' + #13#10 + Dir + #13#10#13#10 +
              '原来的数据目录：' + #13#10 + Prev + #13#10#13#10 +
              '旧目录里的账号与数据**不会搬走，也不会被删除**；新目录是一个全新的空环境，' +
              '需要重新登录/配置。要继续吗？',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDNO then
    begin
      Result := False;
      Exit;
    end;
  end
  else if (not ReuseNoticeShown) and DataDirHasUserData(Dir) then
  begin
    ReuseNoticeShown := True;
    SuppressibleMsgBox('将沿用这个目录里已有的账号：' + #13#10 + Dir + #13#10#13#10 +
      '重装后可直接用原账号登录，数据与历史都在原处。', mbInformation, MB_OK, IDOK);
  end;
  ResolvedDataDir := Dir;
end;

{ 安装开始前的最后一道关：目录不可写 / 空间不足都给中文、可行动的提示，
  避免装到一半才弹出 “CreateFile failed; code 5（拒绝访问）”。 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Dir: String;
  FreeMB: Int64;
  Deepest: Integer;
  Issue: String;
  BackupRoot, BackupOverride: String;
begin
  Result := '';
  Dir := WizardForm.DirEdit.Text;
  if Dir = '' then
    Exit;

  { 备份根不能默认落在系统盘：D 盘不可用、数据目录回退到 C 盘时，默认推导会
    得到 C:\LiuHaoBackups，等于把「不占 C 盘」的要求落空。此时明确拒绝并给出
    可行动的处置，而不是装完才让用户发现备份在 C 盘。 }
  BackupOverride := Trim(ExpandConstant('{param:BACKUPS|}'));
  BackupRoot := GetBackupRoot('');
  if (BackupOverride = '') and
     (CompareText(ExtractFileDrive(BackupRoot), ExtractFileDrive(ExpandConstant('{sd}'))) = 0) then
  begin
    Result := '备份目录默认会落在系统盘：' + BackupRoot + #13#10#13#10 +
      '数据目录当前是 ' + ResolvedDataDir + '，安装器按「数据目录所在盘」推导备份根，' +
      '结果落在系统盘（' + ExpandConstant('{sd}') + '）。本产品要求备份不占 C 盘。' + #13#10 +
      '请改选 D 盘的数据目录，或显式指定备份目录：' + #13#10 +
      '  安装包命令行加 /BACKUPS=D:\LiuHaoBackups';
    Exit;
  end;

  { MAX_PATH 预检必须在复制文件之前：安装包内最深的文件路径 = 目录 + 相对路径，
    一旦超过 259，安装会在中途失败（用户看到的是半装状态）。

    实测（Inno Setup 6.7.1）：即使把 Windows 的 LongPathsEnabled 打开，安装器
    自身仍会以 "The folder name or path is too long" 拒绝 >260 的目标目录，复制
    阶段也会失败。所以这里不能按 LongPathsEnabled 放行——预检是唯一能保证
    "要么装成功、要么明确拒绝" 的关口。 }
  if MAX_PAYLOAD_REL > 0 then
  begin
    Deepest := Length(Dir) + 1 + MAX_PAYLOAD_REL;
    if Deepest > MAX_PATH_LIMIT then
    begin
      Result := '安装路径过长：' + Dir + #13#10#13#10 +
        '本安装包内有文件路径需要 ' + IntToStr(MAX_PAYLOAD_REL) + ' 个字符，' +
        '加上当前目录后总长 ' + IntToStr(Deepest) + ' 个字符，已超过 Windows 的 ' +
        IntToStr(MAX_PATH_LIMIT + 1) + ' 个字符上限，安装会在中途失败。' + #13#10 +
        '（安装器不支持长路径，即使系统已打开 LongPathsEnabled 也一样。）' + #13#10 +
        '请改选更短的目录（例如 D:\LiuHao，或 ' +
        ExpandConstant('{localappdata}') + '\Programs\LiuHao）。';
      Exit;
    end;
  end;

  { 保留设备名 / 尾点尾空格：Win32 会静默改名或报英文错误，必须在这里用中文拒绝。 }
  Issue := DirNameIssue(Dir);
  if Issue <> '' then
  begin
    Result := '安装路径不可用：' + Dir + #13#10#13#10 + Issue + #13#10 +
      '请改选一个普通目录名（例如 D:\LiuHao）。';
    Exit;
  end;

  if not DirCreatable(Dir) then
  begin
    Result := '安装目录不可写：' + Dir + #13#10#13#10 +
      '请改选一个你有写权限的目录。默认是 D:\LiuHao；如果没有 D 盘，' +
      '安装器会自动改用 ' + ExpandConstant('{localappdata}') + '\Programs\LiuHao。' + #13#10 +
      '如果必须装到这个位置，请关闭安装器，右键安装包选择「以管理员身份运行」。';
    Exit;
  end;

  { 数据目录同样要提前探测：它不在程序目录下面，WizardForm 的目录页也
    校验不到；父目录只读时这里先给出中文提示，避免装到一半才报 "拒绝访问"。
    静默安装（/SILENT）不会经过 NextButtonClick，这里是最后一道防线。 }
  if ResolvedDataDir = '' then
  begin
    Result := '没有可用的数据目录，安装已停止。' + #13#10#13#10 +
      DataDirProblem + #13#10 +
      '请重新运行安装包，在「数据目录」页选择一个可写目录；静默安装请加参数' + #13#10 +
      '/DATA="D:\LiuHaoData"（或其它可写目录）。' + #13#10 +
      '安装器不会自行把数据回退到 C 盘。';
    Exit;
  end;

  if IsSameOrChildDir(ResolvedDataDir, Dir) then
  begin
    Result := '数据目录不能放在程序目录里：' + #13#10 + ResolvedDataDir + #13#10#13#10 +
      '卸载或升级程序目录时会连带影响数据。' + #13#10 +
      '静默安装请用 /DATA 指定程序目录以外的位置（默认 D:\LiuHaoData）。';
    Exit;
  end;

  if IsProtectedProgramDir(ResolvedDataDir) then
  begin
    Result := '数据目录不能放在 Program Files 里：' + ResolvedDataDir + #13#10#13#10 +
      '那里属于程序目录，卸载、升级或系统清理都可能删掉你的账号与记忆。' + #13#10 +
      '请返回上一步改选一个普通数据目录（默认 D:\LiuHaoData）；' +
      '静默安装请用 /DATA="D:\LiuHaoData" 指定。';
    Exit;
  end;

  if not DirCreatable(ResolvedDataDir) then
  begin
    Result := '数据目录不可写：' + ResolvedDataDir + #13#10#13#10 +
      '数据目录不在程序目录里（账号与聊天记录都在这里）。' + #13#10 +
      '请返回上一步，在「数据目录」页改选一个你能写入的位置（默认 D:\LiuHaoData）；' +
      '静默安装时用 /DATA="D:\LiuHaoData" 指定。' + #13#10 +
      '如果必须写到受保护位置，请右键安装包选择「以管理员身份运行」。';
    Exit;
  end;

  { 数据目录的保留名 / 尾点尾空格 / 长度：静默安装走不到向导页的校验，
    这里必须再查一遍，否则会在首次启动时才暴露（且提示只有英文系统错误）。 }
  Issue := DirNameIssue(ResolvedDataDir);
  if Issue <> '' then
  begin
    Result := '数据目录不可用：' + ResolvedDataDir + #13#10#13#10 + Issue + #13#10 +
      '请返回上一步，在「数据目录」页改选一个普通目录名；' +
      '静默安装请用 /DATA="D:\LiuHaoData" 指定。';
    Exit;
  end;

  if Length(ResolvedDataDir) + 1 + DATA_REL_MAX > MAX_PATH_LIMIT then
  begin
    Result := '数据目录路径过长：' + ResolvedDataDir + #13#10#13#10 +
      '数据目录里最深的文件还要再占用 ' + IntToStr(DATA_REL_MAX) + ' 个字符，' +
      '当前总长 ' + IntToStr(Length(ResolvedDataDir) + 1 + DATA_REL_MAX) + ' 个字符，' +
      '超过 Windows 的 ' + IntToStr(MAX_PATH_LIMIT + 1) + ' 个字符上限，首次启动就无法写入。' + #13#10 +
      '请返回上一步改选更短的数据目录（默认 D:\LiuHaoData）；' +
      '静默安装请用 /DATA="D:\LiuHaoData" 指定。';
    Exit;
  end;

  FreeMB := DriveFreeMB(Dir);
  if (FreeMB >= 0) and (FreeMB < NEEDED_MB) then
  begin
    Result := '磁盘空间不足：安装大约需要 ' + IntToStr(NEEDED_MB) + ' MB，' +
      '所选目录所在盘当前只剩 ' + IntToStr(FreeMB) + ' MB。' + #13#10#13#10 +
      '请清理空间或改选其他目录后重试。';
    Exit;
  end;

  { 数据盘也要查：程序装得下、数据盘满了同样会在首启建库时失败。
    这一步在复制文件之前，避免"安装成功、首启失败"的半成品状态。 }
  FreeMB := DriveFreeMB(ResolvedDataDir);
  if (FreeMB >= 0) and (FreeMB < NEEDED_DATA_MB) then
  begin
    Result := '数据目录所在磁盘空间不足：首次启动建库至少需要 ' +
      IntToStr(NEEDED_DATA_MB) + ' MB，' + ResolvedDataDir +
      ' 所在盘当前只剩 ' + IntToStr(FreeMB) + ' MB。' + #13#10#13#10 +
      '请清理该盘空间，或返回上一步在「数据目录」页改选其他磁盘；' +
      '静默安装请用 /DATA="D:\LiuHaoData" 指定。';
    Exit;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Lines: TStrings;
begin
  if CurPageID = wpReady then
  begin
    Lines := WizardForm.ReadyMemo.Lines;
    { 这一页的「数据目录」只写一行：需要用户选择过的场景，说明来源是那一页，
      避免在 Ready 页出现一条空的「数据目录：」（用户会以为路径丢了）。 }
    if NeedDataDirChoice then
      Lines.Insert(0, '数据目录：' + DirForDisplay(ResolvedDataDir) +
        '（在「数据目录」页确认/选择）')
    else
      Lines.Insert(0, '数据目录：' + DirForDisplay(ResolvedDataDir) +
        '（账号与聊天记录都在这里；卸载默认保留）');
    if ProgramFallback then
      Lines.Insert(0, '程序目录：' + DirForDisplay(ResolvedAppDir) +
        '（因为 D 盘不可用，已改到不需要管理员权限的位置）')
    else
      Lines.Insert(0, '程序目录：' + DirForDisplay(ResolvedAppDir));
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Marker: TArrayOfString;
  Notes: TArrayOfString;
  HklmDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    { marker 必须 UTF-8（带 BOM）：安装路径可能含中文/非 ASCII 字符，用系统
      ANSI 代码页写会在非中文系统上变成乱码，启动器就找不到数据目录。
      SaveStringsToUTF8File 实测写出 EF BB BF + UTF-8 + CRLF；启动器按
      utf-8-sig 读，Notepad / PowerShell 默认也能正确识别 BOM。
      LiuHao.bat 本身**不解析**这个文件（cmd 无法可靠解码 UTF-8 路径），
      统一交给 runtime\python.exe 读，见 packaging\stage_payload.py。 }
    SetArrayLength(Marker, 1);
    Marker[0] := ResolvedDataDir;
    SaveStringsToUTF8File(ExpandConstant('{app}') + '\data-dir.txt', Marker, False);
    { 安装器自己回读 marker 时走 ReadDataDirMarker（UTF8Decode + 往返校验），
      能解 UTF-8（带/不带 BOM）与旧版 ANSI；同一份路径另存到卸载键 DataDir
      （REG_SZ，Unicode 原生），供覆盖升级与卸载读取。两处都由同一次解析结果
      写入，不会分叉。
      只在 Inno 实际使用的卸载键下写（HKCU，或已存在的 HKLM 键），
      避免多造一个卸载器清理不掉的孤立键。 }
    RegWriteStringValue(HKCU, UninstallKeyPath, 'DataDir', ResolvedDataDir);
    if RegQueryStringValue(HKLM, UninstallKeyPath, 'InstallLocation', HklmDir) then
      RegWriteStringValue(HKLM, UninstallKeyPath, 'DataDir', ResolvedDataDir);
    { 静默安装时用户看不到向导页；最终位置与选择原因必须落盘可查。 }
    if ProgramFallback or DataFallback then
    begin
      SetArrayLength(Notes, 5);
      if ProgramFallback then
        Notes[0] := '程序目录因 D 盘不可用已改到：'
      else
        Notes[0] := '本次安装的程序目录：';
      Notes[1] := DirForDisplay(ResolvedAppDir);
      Notes[2] := '数据目录：' + DirForDisplay(ResolvedDataDir);
      if DataFallback and (DataDirProblem <> '') then
        Notes[3] := '数据目录说明：' + DataDirProblem
      else
        Notes[3] := '数据目录已按上面的位置写入；安装器没有自动回退到 C 盘。';
      Notes[4] := '数据目录还记录在程序目录下的 data-dir.txt。';
      SaveStringsToUTF8File(ExpandConstant('{app}') + '\安装位置说明.txt', Notes, False);
    end;
  end;
end;

{ 卸载一开始就把实际数据目录读出来：等到 usPostUninstall 时
  程序目录下的 data-dir.txt 已被 [UninstallDelete] 删掉，读不到就只剩 C 盘默认路径。 }
function InitializeUninstall: Boolean;
var
  Marker: String;
begin
  Result := True;
  { 与安装时同一套解析顺序：注册表 DataDir（Unicode 原生）优先，
    旧版安装没有该值才回落到 ANSI marker。 }
  Marker := RegisteredDataDir;
  if Marker = '' then
    Marker := ReadDataDirMarker(ExpandConstant('{app}') + '\data-dir.txt');
  if Marker <> '' then
    UninstallDataDir := Marker
  else
    UninstallDataDir := ExpandConstant('{localappdata}\LiuHao\data');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Parent: String;
  Candidates: TArrayOfString;
  Listing: String;
  Dir: String;
  ExistingCount: Integer;
  I, J: Integer;
  Duplicate: Boolean;
  StopCode: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    { 先停进程并**校验结果**：stop 会在超时后强杀，然后检查安装根下是否还有
      匹配进程、已知端口是否仍在 LISTEN；仍占用时退出码为 1。这里不能静默
      继续 —— 文件删不掉会留下能直接登录的残骸，至少要让用户知道。 }
    if (not UninstallSilent) and
       Exec(ExpandConstant('{app}\runtime\python.exe'),
            '-m src.desktop stop --root "' + ExpandConstant('{app}') + '" --timeout 10',
            ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, StopCode) and
       (StopCode <> 0) then
      MsgBox('警告：仍有鎏灏进程或端口占用未释放。' + #13#10#13#10 +
             '卸载会继续，但部分文件可能删不掉（下次登录仍可能连回旧服务）。' + #13#10 +
             '建议先手动退出托盘图标，再重新运行卸载。', mbInformation, MB_OK);
    { 许可证/第三方声明默认保留（[Files] 里带 uninsneveruninstall），这里只做告知：
      静默卸载不弹窗；没有合规材料的老版本也不打扰。 }
    if (not UninstallSilent) and
       (FileExists(ExpandConstant('{app}\THIRD-PARTY-NOTICES.txt')) or
        DirExists(ExpandConstant('{app}\licenses')) or
        FileExists(ExpandConstant('{app}\sbom-cyclonedx.json')) or
        FileExists(ExpandConstant('{app}\LICENSE'))) then
      MsgBox('本机许可证记录会保留：' + #13#10 +
             ExpandConstant('{app}') + #13#10 + #13#10 +
             '卸载不会删除 licenses\、THIRD-PARTY-NOTICES.txt、sbom-cyclonedx.json 与 LICENSE。' + #13#10 +
             '这些是你可以保留的合规记录；不需要时卸载完成后手动删除安装目录即可。',
             mbInformation, MB_OK);
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    { 静默卸载（/SILENT、/VERYSILENT）无法交互：按安全默认保留数据。 }
    if UninstallSilent then
      Exit;

    { 数据可能不止一个：主数据目录（marker/注册表）之外，本机还可能有
      上一次安装留下的骨架目录（D:\LiuHaoData）与桌面壳目录（%LOCALAPPDATA%\LiuHao，
      含 desktop.json、logs、diagnostics）。只问主目录会让其余目录里的
      JWT 签名密钥与账号材料静默留存。 }
    SetArrayLength(Candidates, 3);
    Candidates[0] := UninstallDataDir;
    Candidates[1] := 'D:\LiuHaoData';
    Candidates[2] := ExpandConstant('{localappdata}\LiuHao');

    Listing := '';
    ExistingCount := 0;
    for I := 0 to 2 do
      if DirExists(Candidates[I]) and DataDirHasContent(Candidates[I]) then
      begin
        { 去重：同一个目录只列一次（UninstallDataDir 常常就是 D:\LiuHaoData）。 }
        Duplicate := False;
        for J := 0 to I - 1 do
          if DirExists(Candidates[J]) and SameDir(Candidates[J], Candidates[I]) then
            Duplicate := True;
        if not Duplicate then
        begin
          ExistingCount := ExistingCount + 1;
          Listing := Listing + Candidates[I] + #13#10;
          if SameDir(Candidates[I], ExpandConstant('{localappdata}\LiuHao')) then
            Listing := Listing +
              '    · desktop.json（上次选中的数据目录）、logs\bootstrap.log、' +
              'diagnostics\（诊断包，含配置快照）' + #13#10
          else if ExtractFileName(TrimTrailingSlash(Candidates[I])) = 'LiuHaoData' then
            Listing := Listing +
              '    · 账号库/会话库/模型库；JWT 签名密钥、模型主密钥、' +
              '账号口令校验材料、模型 Key 密文（如该目录确实登录过）' + #13#10
          else
            Listing := Listing +
              '    · 账号库/会话库/模型库；JWT 签名密钥、模型主密钥、' +
              '账号口令校验材料、模型 Key 密文' + #13#10;
        end;
      end;

    if ExistingCount = 0 then
      Exit;

    { 第一次确认：默认「否」，保留数据是安全默认值。文案必须点名敏感物：
      只说"账号、模型配置与 API Key"会严重低估后果（删掉后无法恢复登录与历史）。 }
    if MsgBox('是否同时删除下面这些数据目录？' + #13#10 + #13#10 + Listing + #13#10 +
              '这些目录里可能有：JWT 签名密钥、模型主密钥、账号口令校验材料、' +
              '模型 Key 密文、对话历史与审计记录。' + #13#10 + #13#10 +
              '选择「否」= 全部保留，以后重装可直接继续使用（仍需重装后重新登录）。' + #13#10 +
              '选择「是」= 进入二次确认，确认后**永久删除，无法恢复**。',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      { 第二次确认：不可逆，必须再点一次「是」。 }
      if MsgBox('二次确认：将永久删除以上 ' + IntToStr(ExistingCount) + ' 个目录，且无法恢复。' + #13#10 + #13#10 +
                Listing + #13#10 +
                '确定要删除吗？',
                mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        for I := 0 to 2 do
        begin
          Dir := Candidates[I];
          if not DirExists(Dir) then
            Continue;
          { 去重后逐个删：先删 children 再删父目录。 }
          Duplicate := False;
          for J := 0 to I - 1 do
            if DirExists(Candidates[J]) and SameDir(Candidates[J], Dir) and (Candidates[J] <> '') then
              Duplicate := True;
          if Duplicate then
            Continue;
          DelTree(Dir, True, True, True);
          { 只清理 LiuHao 自己的数据根，绝不碰盘符根目录或别的目录。 }
          Parent := ExtractFileDir(TrimTrailingSlash(Dir));
          { 护栏：父目录不是盘符根（'D:\' 只有 3 个字符）。D:\LiuHaoData 的父目录
            是 'D:\'，RemoveDir 会对整盘调用 —— 必须挡住。 }
          if (Length(Parent) > 3) and
             ((ExtractFileName(TrimTrailingSlash(Dir)) = 'LiuHaoData') or
              ((ExtractFileName(TrimTrailingSlash(Dir)) = 'data') and
               (ExtractFileName(Parent) = 'LiuHao'))) then
            RemoveDir(Parent);
        end;
      end;
    end;
  end;
end;
