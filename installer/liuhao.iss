; ==========================================================================
; 鎏灏 LiuHao AI OS —— Windows 安装器（Inno Setup 6）
;
; 设计口径（与产品负责人确认的硬要求一致）：
;   1) 默认装到 D 盘：程序 D:\LiuHao，数据 D:\LiuHaoData —— **两者必须分离**
;      （数据不在程序目录里，卸载删程序目录不会带走账号与 Key）；
;   2) D 盘不可用（不存在 / 不可写 / 是可移动盘）时自动回退：
;      程序 → %ProgramFiles%\LiuHao（需要管理员，本安装器会自动以 /ALLUSERS 自重拉），
;      数据 → %LOCALAPPDATA%\LiuHao\data；界面上说明「因为 D 盘不可用，已装到 …」，
;      不报错中断、不装到一半失败；
;   3) 老用户（原先装在 C 盘）覆盖安装/升级保持原路径不动：程序目录以上一次安装
;      登记的位置为准，数据目录以程序目录下的 data-dir.txt（marker）为准，
;      0.1.0 及更早没有 marker 的安装回落到 %LOCALAPPDATA%\LiuHao\data；
;   4) 卸载默认**保留**用户数据，只有用户在卸载时明确勾选并要求二次确认，
;      才会按 marker 记录的实际数据目录删除（不再写死 C 盘那个路径）。
;
; 构建：packaging\build_windows_installer.ps1（会传 /DAppVersion=...）。
; 直接编译：iscc /DAppVersion=0.1.1 installer\liuhao.iss
; ==========================================================================

#ifndef AppVersion
  #define AppVersion "0.1.1"
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
DefaultDirName=D:\LiuHao
DefaultGroupName=LiuHao
; D 盘可用时：装到 D:\LiuHao + 只写 HKCU，不需要管理员，UAC 不会弹。
; D 盘不可用时：回退到 %ProgramFiles%\LiuHao，[Code] 会带 /ALLUSERS 自重拉并提权。
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
; 产品界面是中文，安装/卸载主体文案也必须中文，默认不弹语言选择。
ShowLanguageDialog=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; checkedonce：首次安装默认勾选，之后记住用户的选择。
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "launchnow"; Description: "安装完成后立即启动鎏灏"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
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

[Dirs]
; 数据目录由安装器创建，但卸载器永不删除（删除只在 [Code] 里二次确认后按 marker 执行）。
; 目录由 [Code] 解析：默认 D:\LiuHaoData，D 盘不可用或老安装时按实际位置。
Name: "{code:GetDataDir}"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\config"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\workspace"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\data"; Flags: uninsneveruninstall
Name: "{code:GetDataDir}\logs"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\LiuHao.bat"; WorkingDir: "{app}"; Comment: "启动鎏灏（首次运行会进入向导）"
Name: "{group}\使用说明"; Filename: "{app}\START-HERE.txt"
; 离线诊断入口：不启动网关也能出包，服务起不来时用户可直接双击。
Name: "{group}\收集诊断信息（离线）"; Filename: "{app}\scripts\collect_diagnostics.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\liuhao.ico"; Comment: "服务未启动也能采集脱敏诊断包到桌面"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\LiuHao.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\LiuHao.bat"; WorkingDir: "{app}"; Description: "立即启动鎏灏（首次启动会进入向导）"; Flags: postinstall nowait skipifsilent shellexec; Tasks: launchnow
; 覆盖升级清理：按 packaging/stage_payload.py 的同一份排除清单，删除程序目录
; config/ 下“新版已不随包”的旧文件（production.yaml 等）。用 payload 自带运行时
; 执行，静默等待完成；失败不阻断安装（最坏情况只是残留一个已排除文件）。
Filename: "{app}\runtime\python.exe"; Parameters: """{app}\scripts\purge_installed_config.py"" --root ""{app}"""; Flags: runhidden waituntilterminated

[UninstallDelete]
; 旧便携版在程序目录里留下的运行目录：安装版已在首次启动时搬走内容，
; 卸载时只清理「已经空了」的目录，不动任何文件（避免破坏数据）。
Type: dirifempty; Name: "{app}\logs"
Type: dirifempty; Name: "{app}\data"
Type: dirifempty; Name: "{app}\plugins"
; 安装时由 [Code] 写下的数据目录 marker（不在安装清单里，卸载器不会自动删）。
Type: files; Name: "{app}\data-dir.txt"

[UninstallRun]
; 运行期生成的字节码缓存不在安装清单内，卸载前先递归清掉，
; 否则 runtime/ 下会残留大量 .pyc。runhidden + waituntilterminated 保证
; 清理完成后再进入文件删除阶段。
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-ChildItem -LiteralPath '{app}' -Recurse -Directory -Filter '__pycache__' -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; RunOnceId: "PurgePycache"

[Code]
const
  { 安装实际占用约 200 MB，留出解压与日志余量后按 400 MB 预检。 }
  NEEDED_MB = 400;

var
  PrevInstallDir: String;
  HasPrevInstall: Boolean;
  ResolvedAppDir: String;
  ResolvedDataDir: String;
  ProgramFallback: Boolean;
  DataFallback: Boolean;
  UninstallDataDir: String;

{ 卸载键名。AppId 含花括号，而 Pascal 里花括号是注释，所以用 Chr 拼出来。 }
function UninstallKeyPath: String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
            Chr(123) + '8F2B9C1E-4C0D-4A57-9D2E-6B1C3A5E7F90' + Chr(125) + '_is1';
end;

{ D: 是否是固定盘且当前用户可写（可移动盘 U 盘/光盘/网络盘一律视为不可用）。 }
function GetDriveTypeW(lpRootPathName: String): Cardinal;
external 'GetDriveTypeW@kernel32.dll stdcall';

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

function ReadTextFile(const Path: String): String;
var
  S: AnsiString;
  I, N: Integer;
  B: Byte;
  CP: Cardinal;
begin
  Result := '';
  if not FileExists(Path) then
    Exit;
  if not LoadStringFromFile(Path, S) then
    Exit;

  { 0.1.1 起 marker 写成 UTF-8（带 BOM）：必须按字节解码，否则在非中文
    系统上用系统 ANSI 代码页读 UTF-8 会得到乱码路径。没有 BOM 的是旧版
    (0.1.0) ANSI marker，保持原有的系统代码页解码。 }
  if (Length(S) >= 3) and (Ord(S[1]) = $EF) and (Ord(S[2]) = $BB) and (Ord(S[3]) = $BF) then
  begin
    I := 4;
    N := Length(S);
    while I <= N do
    begin
      B := Ord(S[I]);
      if B < $80 then
      begin
        Result := Result + Chr(B);
        Inc(I);
      end
      else if (B and $E0) = $C0 then
      begin
        if I + 1 <= N then
        begin
          CP := ((B and $1F) shl 6) or (Ord(S[I + 1]) and $3F);
          Result := Result + Chr(CP);
        end;
        I := I + 2;
      end
      else if (B and $F0) = $E0 then
      begin
        if I + 2 <= N then
        begin
          CP := ((B and $0F) shl 12) or
                ((Ord(S[I + 1]) and $3F) shl 6) or
                (Ord(S[I + 2]) and $3F);
          Result := Result + Chr(CP);
        end;
        I := I + 3;
      end
      else if (B and $F8) = $F0 then
      begin
        { BMP 之外的字符（emoji 等）不会出现在合法安装路径里，替换掉。 }
        Result := Result + '?';
        I := I + 4;
      end
      else
      begin
        Result := Result + Chr(B);
        Inc(I);
      end;
    end;
    Result := Trim(Result);
  end
  else
    Result := Trim(String(S));
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

{ 真正写一个探测文件再删掉：只靠 GetDriveType 判断不了 ACL 拒绝访问。 }
function DirWritable(const Dir: String): Boolean;
var
  Base: String;
  Probe: String;
begin
  Result := False;
  if DirExists(Dir) then
    Base := Dir
  else
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

function InitializeSetup: Boolean;
var
  Marker: String;
  LegacyData: String;
  CmdDir: String;
  CmdData: String;
begin
  Result := True;
  LegacyData := ExpandConstant('{localappdata}\LiuHao\data');

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

  { ---- 数据目录：必须与程序目录分离 ---- }
  CmdData := CmdParamValue('/DATA');
  if CmdData <> '' then
    ResolvedDataDir := CmdData
  else if HasPrevInstall then
  begin
    { 新版本安装会写 marker；0.1.0 及更早没有 marker，数据固定在 LOCALAPPDATA。 }
    Marker := ReadTextFile(PrevInstallDir + '\data-dir.txt');
    if (Marker <> '') and PathDriveUsable(Marker) then
      ResolvedDataDir := Marker
    else if Marker <> '' then
    begin
      { 上次记录的数据目录所在盘现在不可用：退回 LOCALAPPDATA，并给出说明。 }
      ResolvedDataDir := LegacyData;
      DataFallback := True;
    end
    else
      ResolvedDataDir := LegacyData;
  end
  else if DirExists(LegacyData) and DataDirHasContent(LegacyData) then
    ResolvedDataDir := LegacyData
  else if DDriveUsable then
    ResolvedDataDir := 'D:\LiuHaoData'
  else
  begin
    ResolvedDataDir := LegacyData;
    DataFallback := True;
  end;

  { ---- D 盘不可用时程序目录已回退到 Program Files / 用户 Programs，见上；
         这里不再重启安装器（Inno 6.7 加载器独占自身 exe，自重拉会被拒绝）。 ---- }
  if ProgramFallback then
    Log('D 盘不可用，程序目录回退到 ' + ResolvedAppDir + '。');

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

procedure InitializeWizard;
var
  Welcome: String;
begin
  { UsePreviousAppDir 已把安装目录填成上一次的位置；只有全新安装才由我们替换。 }
  if (not HasPrevInstall) and ProgramFallback then
    WizardForm.DirEdit.Text := ResolvedAppDir;

  { 首屏先回答两个最容易让人犹豫的问题：未签名怎么继续、装到哪/数据在哪。
    详细说明仍在 BEFORE-INSTALL.txt（向导会自动展示该页）。 }
  Welcome :=
    '本安装包未做代码签名，Windows 可能提示「未知发布者」或「Windows 已保护你的电脑」。' + #13#10 +
    '这是预期行为：点「更多信息」→「仍要运行」即可继续。' + #13#10#13#10 +
    '程序装到：' + DirForDisplay(ResolvedAppDir) + #13#10 +
    '数据放在：' + DirForDisplay(ResolvedDataDir) + '（账号、模型配置与 API Key；卸载默认保留）';
  if ProgramFallback or DataFallback then
    Welcome := Welcome + #13#10#13#10 +
      '因为这台机器上 D 盘不可用（不存在 / 不可写 / 是可移动盘），' +
      '安装器已自动改到上面的位置，不需要管理员权限。';
  WizardForm.WelcomeLabel2.Caption := Welcome;
  { 默认高度只够两三行，这里内容更长，按行数放开高度以免被裁切。 }
  WizardForm.WelcomeLabel2.Height := ScaleY(150);
end;

{ 安装开始前的最后一道关：目录不可写 / 空间不足都给中文、可行动的提示，
  避免装到一半才弹出 “CreateFile failed; code 5（拒绝访问）”。 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Dir: String;
  FreeMB: Int64;
begin
  Result := '';
  Dir := WizardForm.DirEdit.Text;
  if Dir = '' then
    Exit;

  if not DirWritable(Dir) then
  begin
    Result := '安装目录不可写：' + Dir + #13#10#13#10 +
      '请改选一个你有写权限的目录。默认是 D:\LiuHao；如果没有 D 盘，' +
      '安装器会自动改用 ' + ExpandConstant('{localappdata}') + '\Programs\LiuHao。' + #13#10 +
      '如果必须装到这个位置，请关闭安装器，右键安装包选择「以管理员身份运行」。';
    Exit;
  end;

  FreeMB := DriveFreeMB(Dir);
  if (FreeMB >= 0) and (FreeMB < NEEDED_MB) then
  begin
    Result := '磁盘空间不足：安装大约需要 ' + IntToStr(NEEDED_MB) + ' MB，' +
      '所选目录所在盘当前只剩 ' + IntToStr(FreeMB) + ' MB。' + #13#10#13#10 +
      '请清理空间或改选其他目录后重试。';
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Lines: TStrings;
begin
  if CurPageID = wpReady then
  begin
    if ProgramFallback or DataFallback then
    begin
      Lines := WizardForm.ReadyMemo.Lines;
      Lines.Insert(0, '程序启动文件：' + DirForDisplay(ResolvedAppDir) + '\LiuHao.bat');
      Lines.Insert(0, '数据目录：' + DirForDisplay(ResolvedDataDir) +
        '（账号、模型配置与 API Key 都在这里；卸载默认保留）');
      if ProgramFallback then
        Lines.Insert(0, '程序目录：' + DirForDisplay(ResolvedAppDir) +
          '（因为 D 盘不可用，已自动回退；这个位置不需要管理员权限）')
      else
        Lines.Insert(0, '程序目录：' + DirForDisplay(ResolvedAppDir));
      Lines.Insert(0, '因为 D 盘不可用（不存在 / 不可写 / 是可移动盘），已装到下面这些位置，' +
        '之后也能在这里找到程序和数据：');
      Lines.Insert(0, '');
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Marker: TArrayOfString;
  Notes: TArrayOfString;
begin
  if CurStep = ssPostInstall then
  begin
    { marker 必须 UTF-8（带 BOM）：安装路径可能含中文/非 ASCII 字符，用系统
      ANSI 代码页写会在非中文系统上变成乱码，启动器就找不到数据目录。
      SaveStringsToUTF8File 写 UTF-8 BOM（签名取 TArrayOfString）。 }
    SetArrayLength(Marker, 1);
    Marker[0] := ResolvedDataDir;
    SaveStringsToUTF8File(ExpandConstant('{app}') + '\data-dir.txt', Marker, False);
    { 静默安装时用户看不到向导页；回退原因与最终位置必须落盘可查。 }
    if ProgramFallback or DataFallback then
    begin
      SetArrayLength(Notes, 5);
      Notes[0] := '因为 D 盘不可用（不存在 / 不可写 / 是可移动盘），本次安装已自动回退到：';
      Notes[1] := '程序目录：' + DirForDisplay(ResolvedAppDir);
      Notes[2] := '启动文件：' + DirForDisplay(ResolvedAppDir) + '\LiuHao.bat';
      Notes[3] := '数据目录：' + DirForDisplay(ResolvedDataDir);
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
  Marker := ReadTextFile(ExpandConstant('{app}') + '\data-dir.txt');
  if Marker <> '' then
    UninstallDataDir := Marker
  else
    UninstallDataDir := ExpandConstant('{localappdata}\LiuHao\data');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Parent: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { 静默卸载（/SILENT、/VERYSILENT）无法交互：按安全默认保留数据。 }
    if UninstallSilent then
      Exit;

    if DirExists(UninstallDataDir) and DataDirHasContent(UninstallDataDir) then
    begin
      { 第一次确认：默认「否」，保留数据是安全默认值。 }
      if MsgBox('是否同时删除你的数据？' + #13#10 + #13#10 +
                '数据目录：' + UninstallDataDir + #13#10 +
                '包含：账号、模型配置与 API Key、对话历史、审计记录。' + #13#10 + #13#10 +
                '选择「否」= 保留数据，以后重装可直接继续使用。' + #13#10 +
                '选择「是」= 继续进入二次确认。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        { 第二次确认：不可逆，必须再点一次「是」。 }
        if MsgBox('二次确认：将永久删除数据目录，且无法恢复。' + #13#10 + #13#10 +
                  UninstallDataDir + #13#10 + #13#10 +
                  '确定要删除吗？',
                  mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        begin
          DelTree(UninstallDataDir, True, True, True);
          { 只清理 LiuHao 自己的数据根，绝不碰盘符根目录或别的目录。 }
          Parent := ExtractFileDir(UninstallDataDir);
          if (ExtractFileName(UninstallDataDir) = 'data') and
             (ExtractFileName(Parent) = 'LiuHao') then
            RemoveDir(Parent)
          else if ExtractFileName(UninstallDataDir) = 'LiuHaoData' then
            RemoveDir(Parent);
        end;
      end;
    end;
  end;
end;
