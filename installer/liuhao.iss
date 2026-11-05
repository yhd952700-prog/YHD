; ==========================================================================
; 鎏灏 LiuHao AI OS —— Windows 安装器（Inno Setup 6）
;
; 设计口径（与产品负责人确认的三条硬要求一致）：
;   1) 安装到 %LOCALAPPDATA%\LiuHao\app，**不要求管理员权限**；
;   2) 程序与数据分离：数据固定在 %LOCALAPPDATA%\LiuHao\data，
;      同名覆盖升级不会改动它；
;   3) 卸载默认**保留**用户数据，只有用户在卸载时明确勾选并要求二次确认，
;      才会删除数据目录。
;
; 构建：packaging\build_windows_installer.ps1（会传 /DAppVersion=...）。
; 直接编译：iscc /DAppVersion=0.1.0 installer\liuhao.iss
; ==========================================================================

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#ifndef PayloadDir
  #define PayloadDir "..\dist\payload\app"
#endif

#define AppName "鎏灏 LiuHao AI OS"
#define AppPublisher "LiuHao"
#define AppIdValue "{{8F2B9C1E-4C0D-4A57-9D2E-6B1C3A5E7F90}"

[Setup]
AppId={#AppIdValue}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\LiuHao\app
DefaultGroupName=LiuHao
; 安装到用户目录 + 只写 HKCU，因此不需要管理员。UAC 不会弹。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousAppDir=yes
UsePreviousGroup=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
AllowNoIcons=yes
OutputDir=..\dist\installer
OutputBaseFilename=LiuHao-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayName={#AppName}
; 安装前展示的中文说明：未签名、不含模型、数据保留策略。
InfoBeforeFile=assets\BEFORE-INSTALL.txt
; 图标由构建脚本从前端 PNG 自动转换；缺失时退回默认图标。
#if FileExists("assets\liuhao.ico")
SetupIconFile=assets\liuhao.ico
#endif
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; checkedonce：首次安装默认勾选，之后记住用户的选择。
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "launchnow"; Description: "安装完成后立即启动鎏灏"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\BEFORE-INSTALL.txt"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; 数据目录由安装器创建，但卸载器永不删除（删除只在 [Code] 里二次确认后执行）。
Name: "{localappdata}\LiuHao\data"; Flags: uninsneveruninstall
Name: "{localappdata}\LiuHao\data\config"; Flags: uninsneveruninstall
Name: "{localappdata}\LiuHao\data\workspace"; Flags: uninsneveruninstall
Name: "{localappdata}\LiuHao\data\data"; Flags: uninsneveruninstall
Name: "{localappdata}\LiuHao\data\logs"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\LiuHao.bat"; WorkingDir: "{app}"; Comment: "启动鎏灏（首次运行会进入向导）"
Name: "{group}\使用说明"; Filename: "{app}\START-HERE.txt"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LiuHao.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\LiuHao.bat"; WorkingDir: "{app}"; Description: "立即启动鎏灏（首次启动会进入向导）"; Flags: postinstall nowait skipifsilent shellexec; Tasks: launchnow

[UninstallDelete]
; 旧便携版在程序目录里留下的运行目录：安装版已在首次启动时搬走内容，
; 卸载时只清理「已经空了」的目录，不动任何文件（避免破坏数据）。
Type: dirifempty; Name: "{app}\logs"
Type: dirifempty; Name: "{app}\data"
Type: dirifempty; Name: "{app}\plugins"

[UninstallRun]
; 运行期生成的字节码缓存不在安装清单内，卸载前先递归清掉，
; 否则 runtime/ 下会残留大量 .pyc。runhidden + waituntilterminated 保证
; 清理完成后再进入文件删除阶段。
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-ChildItem -LiteralPath '{app}' -Recurse -Directory -Filter '__pycache__' -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; RunOnceId: "PurgePycache"

[Code]
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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { 静默卸载（/SILENT、/VERYSILENT）无法交互：按安全默认保留数据。 }
    if UninstallSilent then
      Exit;

    DataDir := ExpandConstant('{localappdata}\LiuHao\data');
    if DirExists(DataDir) and DataDirHasContent(DataDir) then
    begin
      { 第一次确认：默认「否」，保留数据是安全默认值。 }
      if MsgBox('是否同时删除你的数据？' + #13#10 + #13#10 +
                '数据目录：' + DataDir + #13#10 +
                '包含：账号、模型配置与 API Key、对话历史、审计记录。' + #13#10 + #13#10 +
                '选择「否」= 保留数据，以后重装可直接继续使用。' + #13#10 +
                '选择「是」= 继续进入二次确认。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        { 第二次确认：不可逆，必须再点一次「是」。 }
        if MsgBox('二次确认：将永久删除数据目录，且无法恢复。' + #13#10 + #13#10 +
                  DataDir + #13#10 + #13#10 +
                  '确定要删除吗？',
                  mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        begin
          DelTree(DataDir, True, True, True);
          RemoveDir(ExpandConstant('{localappdata}\LiuHao'));
        end;
      end;
    end;
  end;
end;
