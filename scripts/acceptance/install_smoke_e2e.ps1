#requires -Version 5.1
<#
.SYNOPSIS
    鎏灏安装冒烟 E2E：一条命令跑完 docs/DELIVERY-CHECKLIST.md §0.2 的 S-01～S-11，
    外加安装树洁净度与机器级密钥二次启动复用（S-12～S-14）。

.DESCRIPTION
    设计原则
    ----------
    * 只在 dist\release-gate\smoke\<时间戳>\ 下落证据，不改产品代码、不改安装器。
    * 每一步都有超时：安装 / 启动 / 登录 / 对话 / 卸载各自独立超时，任何一步卡住都不会
      让整脚本无限等待。
    * 包不存在时不报错退出：安装类检查全部记 SKIP(未取证)，退出码 0，并提示待包就绪后重跑。
    * 全程不弹 UAC、不依赖鼠标键盘；只有 S-09（关窗驻留 / 托盘退出）明确标 NEEDS_HUMAN。
    * Key 只走环境变量或参数，**不写进任何证据文件**；证据里统一脱敏为 sk-***。

    退出码
    ----------
    0 = 没有 FAIL（允许 SKIP / NEEDS_HUMAN / S-11 观测项命中）
    1 = 存在 FAIL
    2 = 脚本自身前置条件不成立（例如 -SkipInstall 但目录里没有可用安装）

.PARAMETER InstallerPath
    安装包路径。留空时只自动查找 dist\installer\*.exe（非递归），不会静默使用历史包。

.PARAMETER InstallDir
    安装目录。默认 D:\LiuHaoSmoke\LiuHao，专用于冒烟、避免覆盖真实用户安装。
    要验证出厂默认目录时显式传 -InstallDir D:\LiuHao。

.PARAMETER DataDir
    数据目录。默认 D:\LiuHaoSmoke\LiuHaoData。

.PARAMETER ModelKey
    真实模型 Key。环境变量 LIUHAO_SMOKE_MODEL_KEY 优先；两者都没有时 S-06 / S-07(写链路) / S-08
    记 SKIP(未取证)，其余检查照跑。

.PARAMETER Port
    控制台/API 端口。默认自动挑一个空闲端口。

.PARAMETER SkipInstall
    跳过安装，直接对现有 InstallDir 做 S-03～S-09（S-01/S-02/S-10 记 SKIP）。

.PARAMETER SkipUninstall
    跳过卸载（S-10 记 SKIP）。

.PARAMETER AllowHistoricalInstaller
    没有 dist\installer\*.exe 时，允许从 dist\installer\_archive\** 里挑最新的历史包。
    默认关闭，防止把过期包当成候选包来验。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\acceptance\install_smoke_e2e.ps1

.EXAMPLE
    $env:LIUHAO_SMOKE_MODEL_KEY = 'sk-...'
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\acceptance\install_smoke_e2e.ps1 -InstallerPath dist\installer\LiuHao-Setup-0.1.1.exe
#>
[CmdletBinding()]
param(
    [string]$InstallerPath = '',
    [string]$InstallDir = 'D:\LiuHaoSmoke\LiuHao',
    [string]$DataDir = 'D:\LiuHaoSmoke\LiuHaoData',
    # 验收专用开始菜单组名：真实安装默认组名是 LiuHao，验收安装若沿用同名组，
    # 会把用户的开始菜单项改成指向 D:\LiuHaoSmoke，正是「点开始菜单拉起另一份
    # 副本」那个缺陷的复现源。验收一律装进独立组，跑完随卸载一起删除。
    [string]$StartMenuGroup = 'LiuHaoSmoke',
    [string]$ModelKey = '',
    [int]$Port = 0,
    [switch]$SkipInstall,
    [switch]$SkipUninstall,
    [switch]$AllowHistoricalInstaller,
    [switch]$KeepRunning,
    [string]$AdminPrincipal = 'liuhao-smoke',
    [string]$AdminPassword = '',
    [string]$EvidenceRoot = '',
    [int]$InstallTimeoutSec = 300,
    [int]$UninstallTimeoutSec = 300,
    [int]$StartupTimeoutSec = 90,
    [int]$LoginTimeoutSec = 60,
    [int]$ChatTimeoutSec = 120
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- 基础环境
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:Results = New-Object System.Collections.ArrayList
$script:Token = ''
$script:AdminSecret = ''
# 注意：$ModelKey（param 变量）与 $script:ModelKey 是同一个脚本作用域变量。
# 这里**不能**先写 `$script:ModelKey = ''`，否则等于把传进来的 -ModelKey 清空，
# 表现为「传了 Key 却判成未提供」。
if ($env:LIUHAO_SMOKE_MODEL_KEY) {
    $script:ModelKey = $env:LIUHAO_SMOKE_MODEL_KEY.Trim()
} elseif ($ModelKey) {
    $script:ModelKey = $ModelKey.Trim()
} else {
    $script:ModelKey = ''
}

$script:AdminSecretSource = 'param'
if (-not $AdminPassword) {
    # 只存在内存里；不写日志、不写证据。
    $script:AdminSecret = ([guid]::NewGuid().ToString('N').Substring(0, 16))
    $script:AdminSecretSource = 'generated'
} else {
    $script:AdminSecret = $AdminPassword
}
$AdminPassword = ''

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $EvidenceRoot) {
    $EvidenceRoot = Join-Path $script:RepoRoot 'dist\release-gate\smoke'
}
$script:EvidenceDir = Join-Path $EvidenceRoot $stamp
New-Item -ItemType Directory -Path $script:EvidenceDir -Force | Out-Null
$script:RunLog = Join-Path $script:EvidenceDir 'run.log'

function Protect-Text {
    param([string]$Text)
    if (-not $Text) { return '' }
    $value = [string]$Text
    if ($script:ModelKey) { $value = $value.Replace($script:ModelKey, 'sk-***') }
    # 兜底：任何形似 sk- 的长串都打码，防止上游返回体里出现别的写法
    $value = [regex]::Replace($value, 'sk-[A-Za-z0-9_\-]{12,}', 'sk-***')
    if ($script:Token) { $value = $value.Replace($script:Token, '<token-redacted>') }
    if ($script:AdminSecret) {
        $value = $value.Replace($script:AdminSecret, '<password-redacted>')
    }
    return $value
}

function Write-RunLog {
    param([string]$Text)
    $safe = Protect-Text $Text
    Write-Host $safe
    [System.IO.File]::AppendAllText($script:RunLog, $safe + "`r`n", $script:Utf8NoBom)
}

function Write-Evidence {
    param([string]$Name, [string]$Text)
    $path = Join-Path $script:EvidenceDir $Name
    [System.IO.File]::WriteAllText($path, (Protect-Text $Text), $script:Utf8NoBom)
    return $path
}

function Add-Result {
    param([string]$Id, [string]$Status, [string]$Evidence, [string]$Note)
    $item = [pscustomobject]@{ Item = $Id; Status = $Status; Evidence = $Evidence; Note = $Note }
    [void]$script:Results.Add($item)
    Write-RunLog ("{0} | {1} | {2} | {3}" -f $Id, $Status, $Evidence, $Note)
}

Write-RunLog ("# 管理员凭据：来源={0}，长度={1}（只记长度，不记内容）" -f $script:AdminSecretSource, $script:AdminSecret.Length)

function Get-FileFingerprint {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return [pscustomobject]@{ Exists = $false; Hash = ''; Mtime = '' }
    }
    $hash = '<读取失败>'
    try { $hash = (Get-FileHash -Path $Path -Algorithm SHA256 -ErrorAction Stop).Hash } catch { }
    $mtime = ''
    try { $mtime = (Get-Item $Path -ErrorAction Stop).LastWriteTime.ToString('o') } catch { }
    return [pscustomobject]@{ Exists = $true; Hash = $hash; Mtime = $mtime }
}

function Count-PycacheDirs {
    param([string]$Root)
    if (-not (Test-Path $Root)) { return 0 }
    return @(Get-ChildItem -Path $Root -Recurse -Directory -Filter '__pycache__' -Force -ErrorAction SilentlyContinue).Count
}

# 凭据回读自检：运行时按 UTF-8 读入同一个口令，回读凭据库做校验。
# 只输出长度/哈希匹配结论与 ok/mismatch；哈希与口令本身不落盘。
$script:CredentialVerifyCode = @"
import os, sys, hashlib, io
sys.stdin = io.StringIO(os.environ['LIUHAO_SMOKE_SECRET'] + '\n')
sys.path.insert(0, os.environ['LIUHAO_SMOKE_ROOT'])
os.environ['LIUHAO_DATA_DIR'] = os.environ['LIUHAO_SMOKE_DATA']
os.environ['LIUHAO_ALLOW_ENV_OVERRIDE'] = '1'
from scripts.launch_installed import prepare_environment
prepare_environment()
from src.gateway.auth import resolve_secret_store, verify_credential
secret = os.environ['LIUHAO_SMOKE_SECRET']
print('SEED_SECRET_LEN=' + str(len(secret)))
print('SEED_SECRET_SHA=' + hashlib.sha256(secret.encode('utf-8')).hexdigest())
record = resolve_secret_store().credential_for(os.environ['LIUHAO_SMOKE_PRINCIPAL'])
print('SEED_VERIFY=' + ('ok' if verify_credential(secret, record) else 'mismatch'))
"@

function Get-StringSha256 {
    param([string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))
        return ([BitConverter]::ToString($bytes) -replace '-', '').ToLower()
    } finally { $sha.Dispose() }
}

# 口令通过进程环境变量交给子进程，并由 Python 把 sys.stdin 换成 StringIO。
# 原因：PS 5.1 把字符串管道给原生进程时可能按「UTF-8 带 BOM」编码，子进程会读到
# 前置 U+FEFF，凭据就存成「BOM+口令」，随后登录必然 401。走环境变量可绕开编码，
# 且变量只存在于该子进程，脚本结束前即清除；不写日志、不写证据。
function Invoke-PythonWithSecret {
    param([string]$Python, [string]$Code, [string]$Secret)
    $prevSecret = $env:LIUHAO_SMOKE_SECRET
    $prevIo = $env:PYTHONIOENCODING
    try {
        $env:LIUHAO_SMOKE_SECRET = $Secret
        $env:PYTHONIOENCODING = 'utf-8'
        return (& $Python -c $Code 2>&1 | Out-String)
    } finally {
        if ($prevSecret) { $env:LIUHAO_SMOKE_SECRET = $prevSecret }
        else { Remove-Item Env:LIUHAO_SMOKE_SECRET -ErrorAction SilentlyContinue }
        if ($prevIo) { $env:PYTHONIOENCODING = $prevIo }
        else { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    }
}

function Get-FreePort {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return [int]$listener.LocalEndpoint.Port
    } finally {
        try { $listener.Stop() } catch { }
    }
}

function Invoke-SmokeHttp {
    param(
        [string]$Method,
        [string]$Uri,
        $Body = $null,
        [string]$Token = '',
        [int]$TimeoutSec = 30
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $headers = @{}
    if ($Token) {
        # 网关的 require_bearer_payload 读 Authorization: Bearer；X-Liuhao-Token 是
        # 旧调用方的兼容头，一并带上，避免只带其一被 401。
        $headers['Authorization'] = 'Bearer ' + $Token
        $headers['X-Liuhao-Token'] = $Token
    }
    $params = @{
        Uri            = $Uri
        Method         = $Method
        TimeoutSec     = $TimeoutSec
        UseBasicParsing = $true
        Headers        = $headers
    }
    if ($null -ne $Body) {
        $params['ContentType'] = 'application/json; charset=utf-8'
        $params['Body'] = ($Body | ConvertTo-Json -Depth 8 -Compress)
    }
    try {
        $resp = Invoke-WebRequest @params
        $sw.Stop()
        return [pscustomobject]@{
            Ok = $true; StatusCode = [int]$resp.StatusCode; Body = [string]$resp.Content
            Elapsed = $sw.Elapsed.TotalSeconds; TimedOut = $false
        }
    } catch {
        $sw.Stop()
        $status = 0
        $body = ''
        $timedOut = $false
        $ex = $_.Exception
        try {
            if ($ex.Response -ne $null) { $status = [int]$ex.Response.StatusCode.value__ }
        } catch { }
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $body = [string]$_.ErrorDetails.Message
        } elseif ($ex.Message) {
            $body = [string]$ex.Message
        }
        if ($ex -is [System.Net.WebException]) {
            if ($ex.Status -eq [System.Net.WebExceptionStatus]::Timeout) { $timedOut = $true }
        }
        if (-not $timedOut -and $body -match 'timed?\s*out|超时') { $timedOut = $true }
        return [pscustomobject]@{
            Ok = $false; StatusCode = $status; Body = $body
            Elapsed = $sw.Elapsed.TotalSeconds; TimedOut = $timedOut
        }
    }
}

function Wait-HttpOk {
    param([string]$Uri, [int]$TimeoutSec)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $last = $null
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $last = Invoke-SmokeHttp -Method GET -Uri $Uri -TimeoutSec 8
        if ($last.Ok -and $last.StatusCode -ge 200 -and $last.StatusCode -lt 300) {
            return [pscustomobject]@{ Ok = $true; Elapsed = $sw.Elapsed.TotalSeconds; Last = $last }
        }
        Start-Sleep -Milliseconds 1000
    }
    return [pscustomobject]@{ Ok = $false; Elapsed = $sw.Elapsed.TotalSeconds; Last = $last }
}

function Wait-ProcessExit {
    param($Process, [int]$TimeoutSec)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while (-not $Process.HasExited) {
        if ($sw.Elapsed.TotalSeconds -ge $TimeoutSec) { return $false }
        Start-Sleep -Milliseconds 500
    }
    return $true
}

function Get-Descendants {
    param([int]$RootId)
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $byParent = @{}
    foreach ($p in $all) {
        $par = [int]$p.ParentProcessId
        if (-not $byParent.ContainsKey($par)) {
            $byParent[$par] = New-Object System.Collections.ArrayList
        }
        [void]$byParent[$par].Add($p)
    }
    $result = New-Object System.Collections.ArrayList
    $seen = @{}
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue($RootId)
    while ($queue.Count -gt 0) {
        $cur = [int]$queue.Dequeue()
        if (-not $byParent.ContainsKey($cur)) { continue }
        foreach ($child in $byParent[$cur]) {
            $cp = [int]$child.ProcessId
            if ($seen.ContainsKey($cp)) { continue }
            $seen[$cp] = $true
            [void]$result.Add($child)
            $queue.Enqueue($cp)
        }
    }
    return $result
}

function Get-GatewayPid {
    param([int]$LauncherPid)
    $desc = Get-Descendants -RootId $LauncherPid
    foreach ($d in $desc) {
        if ($d.CommandLine -and $d.CommandLine -match 'src\.gateway') {
            return [int]$d.ProcessId
        }
    }
    return 0
}

function Stop-ProcessTree {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return }
    try { & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null } catch { }
}

function Get-ShortcutInfo {
    param([string]$Path)
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($Path)
        $info = [pscustomobject]@{
            Path = $Path
            TargetPath = [string]$lnk.TargetPath
            Arguments = [string]$lnk.Arguments
            IconLocation = [string]$lnk.IconLocation
            WorkingDirectory = [string]$lnk.WorkingDirectory
        }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($lnk)
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        return $info
    } catch {
        return [pscustomobject]@{
            Path = $Path; TargetPath = ''; Arguments = ''; IconLocation = ''
            WorkingDirectory = ''
        }
    }
}

function Test-IcoFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        if ($bytes.Length -lt 8) { return $false }
        # ICO 头：00 00 01 00
        return ($bytes[0] -eq 0 -and $bytes[1] -eq 0 -and $bytes[2] -eq 1 -and $bytes[3] -eq 0)
    } catch {
        return $false
    }
}

function Save-Screenshot {
    param([string]$Name)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $path = Join-Path $script:EvidenceDir $Name
        $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose()
        $bmp.Dispose()
        return $path
    } catch {
        return ''
    }
}

function Get-FileSizeSafe {
    param([string]$Path)
    if (Test-Path $Path) {
        try { return (Get-Item $Path).Length } catch { return 0 }
    }
    return 0
}

function Get-LogTail {
    param([string]$Path, [int]$Lines = 40)
    if (Test-Path $Path) {
        try { return (Get-Content -Path $Path -Tail $Lines -Encoding UTF8 -ErrorAction Stop) -join "`n" } catch { return '' }
    }
    return ''
}

function Format-Elapsed {
    param([double]$Seconds)
    if ($Seconds -lt 0) { return '<未执行>' }
    return ('{0:N2}s' -f $Seconds)
}

# 安装/启动子进程会读取这些路径变量；为避免继承到别的会话的仓库路径，先清干净再显式设置。
$script:ManagedEnvNames = @(
    'LIUHAO_DATA_DIR', 'LIUHAO_WORKSPACE_ROOT', 'LIUHAO_HUMAN_IDENTITIES_FILE',
    'LIUHAO_AUTH_SECRETS_FILE', 'LIUHAO_MODEL_CONFIG_DB', 'LIUHAO_MODEL_CONFIG_KEY_FILE',
    'CONVERSATION_DB_PATH', 'AUDIT_DB_PATH', 'AUDIT_DB_BACKUP_PATH',
    'LIUHAO_JWT_REVOCATION_DB', 'MEMORY_DB_PATH', 'PERSONAL_CONTEXT_DB_PATH',
    'LIUHAO_KNOWLEDGE_DB', 'LIUHAO_ALERTS_STORE_PATH', 'LIUHAO_CONSOLE_DIST'
)
$script:SavedEnv = @{}

function Set-GatewayEnv {
    $script:SavedEnv = @{}
    foreach ($name in $script:ManagedEnvNames) {
        $script:SavedEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    [Environment]::SetEnvironmentVariable('LIUHAO_ALLOW_ENV_OVERRIDE', '1', 'Process')
    [Environment]::SetEnvironmentVariable('LIUHAO_DATA_DIR', $DataDir, 'Process')
    # 冒烟进程不许往安装目录写字节码：否则 S-14 会把「运行期正常产生的
    # __pycache__」误判成安装包污染，也无法证明「装完是干净的」。
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', '1', 'Process')
}

function Restore-GatewayEnv {
    foreach ($name in $script:ManagedEnvNames) {
        $value = $script:SavedEnv[$name]
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
    [Environment]::SetEnvironmentVariable('LIUHAO_ALLOW_ENV_OVERRIDE', $null, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $null, 'Process')
}

function Resolve-Installer {
    if ($SkipInstall) {
        return [pscustomobject]@{ Path = ''; Source = 'skip'; Note = '指定 -SkipInstall，跳过安装' }
    }
    if ($InstallerPath) {
        $resolved = $InstallerPath
        if (-not [System.IO.Path]::IsPathRooted($resolved)) {
            $resolved = Join-Path $script:RepoRoot $resolved
        }
        if (Test-Path $resolved) {
            return [pscustomobject]@{ Path = (Resolve-Path $resolved).Path; Source = 'explicit'; Note = '使用 -InstallerPath' }
        }
        return [pscustomobject]@{ Path = ''; Source = 'missing-explicit'; Note = "指定的安装包不存在：$InstallerPath" }
    }
    $candidateDir = Join-Path $script:RepoRoot 'dist\installer'
    $candidates = @(Get-ChildItem -Path $candidateDir -Filter *.exe -File -ErrorAction SilentlyContinue)
    if ($candidates.Count -gt 0) {
        $newest = $candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        return [pscustomobject]@{ Path = $newest.FullName; Source = 'candidate'; Note = 'dist\installer 下的候选安装包' }
    }
    $archive = @(Get-ChildItem -Path (Join-Path $candidateDir '_archive') -Filter *.exe -File -Recurse -ErrorAction SilentlyContinue)
    if ($AllowHistoricalInstaller -and $archive.Count -gt 0) {
        $newest = $archive | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        return [pscustomobject]@{ Path = $newest.FullName; Source = 'historical'; Note = '显式允许使用历史包（非候选包）' }
    }
    $hint = ''
    if ($archive.Count -gt 0) {
        $hint = "（dist\installer\_archive 有 $($archive.Count) 个历史包；如确需干跑请显式传 -AllowHistoricalInstaller 或 -InstallerPath）"
    }
    return [pscustomobject]@{ Path = ''; Source = 'missing'; Note = "未找到安装包 dist\installer\*.exe$hint" }
}

# ---------------------------------------------------------------- 主流程
Write-RunLog ("# 鎏灏安装冒烟 E2E  {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Write-RunLog ("# 仓库根目录：{0}" -f $script:RepoRoot)
Write-RunLog ("# 证据目录：{0}" -f $script:EvidenceDir)
Write-RunLog ("# 安装目录：{0}" -f $InstallDir)
Write-RunLog ("# 数据目录：{0}" -f $DataDir)
Write-RunLog ("# 模型 Key：{0}" -f $(if ($script:ModelKey) { '已提供（将脱敏，绝不落盘）' } else { '未提供 → S-06/S-07(写链路)/S-08 记未取证' }))

if ($Port -le 0) { $Port = Get-FreePort }
Write-RunLog ("# 端口：{0}" -f $Port)

$installer = Resolve-Installer
Write-RunLog ("# 安装包解析：{0} -> {1}（{2}）" -f $installer.Source, $(if ($installer.Path) { $installer.Path } else { '<无>' }), $installer.Note)

$script:InstallPerformed = $false
$script:LauncherProc = $null
$script:GatewayPidBefore = 0
$script:ChatArtifactsBefore = @{}
$script:S11Hit = $false
$script:S11Detail = ''
$script:LoginElapsed = -1
$script:ChatElapsed = -1

# ---- S-01 / 安装
if ($installer.Path -and -not $SkipInstall) {
    $driveRoot = [System.IO.Path]::GetPathRoot($InstallDir)
    if (-not (Test-Path $driveRoot)) {
        $ev = Write-Evidence 's01_install_dir.txt' ("目标盘不存在：{0}`r`n安装目录：{1}" -f $driveRoot, $InstallDir)
        Add-Result 'S-01' 'SKIP(未取证)' $ev "目标盘 $driveRoot 不存在，无法安全安装（不会回落到 C 盘）"
    } else {
        $installLog = Join-Path $script:EvidenceDir 's01_install.log'
        $setupArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCLOSEAPPLICATIONS /DIR="' +
            $InstallDir + '" /DATA="' + $DataDir + '" /GROUP="' + $StartMenuGroup + '"' +
            ' /MERGETASKS=desktopicon,startmenuicon /LOG="' + $installLog + '"'
        Write-RunLog ("# 静默安装：{0} {1}" -f $installer.Path, $setupArgs)
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $setupProc = Start-Process -FilePath $installer.Path -ArgumentList $setupArgs -WindowStyle Hidden -PassThru
            $finished = Wait-ProcessExit -Process $setupProc -TimeoutSec $InstallTimeoutSec
            if (-not $finished) {
                Stop-ProcessTree -ProcessId $setupProc.Id
                $ev = Write-Evidence 's01_install_result.txt' ("安装超时（> {0}s），已强制结束 PID={1}" -f $InstallTimeoutSec, $setupProc.Id)
                Add-Result 'S-01' 'FAIL' $ev ("静默安装超时（>{0}s），安装未完成" -f $InstallTimeoutSec)
            } else {
                $sw.Stop()
                $code = [int]$setupProc.ExitCode
                $script:InstallPerformed = ($code -eq 0 -and (Test-Path (Join-Path $InstallDir 'runtime\python.exe')))
                $ev = Write-Evidence 's01_install_result.txt' ("退出码={0}`r`n耗时={1:N1}s`r`n安装目录存在={2}`r`n日志={3}" -f `
                        $code, $sw.Elapsed.TotalSeconds, (Test-Path $InstallDir), $installLog)
                if (-not $script:InstallPerformed) {
                    Add-Result 'S-01' 'FAIL' $ev ("静默安装退出码 {0} 或安装目录缺 runtime\python.exe" -f $code)
                }
            }
        } catch {
            $ev = Write-Evidence 's01_install_result.txt' ("安装启动失败：{0}" -f $_.Exception.Message)
            Add-Result 'S-01' 'FAIL' $ev ("安装程序无法启动：{0}" -f $_.Exception.Message)
        }
    }
}

if ($SkipInstall -or -not $installer.Path) {
    $exists = Test-Path (Join-Path $InstallDir 'runtime\python.exe')
    $ev = Write-Evidence 's01_install_dir.txt' ("SkipInstall={0}`r`n安装器={1}`r`n安装目录存在={2}`r`ndata-dir.txt={3}" -f `
            $SkipInstall.IsPresent, $(if ($installer.Path) { $installer.Path } else { '<无>' }), $exists,
            $(if (Test-Path (Join-Path $InstallDir 'data-dir.txt')) { (Get-Content (Join-Path $InstallDir 'data-dir.txt') -Raw).Trim() } else { '<缺失>' }))
    if ($SkipInstall) {
        Add-Result 'S-01' 'SKIP(未取证)' $ev '指定 -SkipInstall，未执行安装落盘检查'
    } else {
        Add-Result 'S-01' 'SKIP(未取证)' $ev ("未找到安装包，跳过安装类检查；{0}" -f $installer.Note)
    }
}

# S-01 断言（只在真正安装过时执行）
if ($script:InstallPerformed) {
    $installFull = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    $localAppData = [System.IO.Path]::GetFullPath([Environment]::GetFolderPath('LocalApplicationData')).TrimEnd('\')
    $programFiles = [System.IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\')
    $programFilesX86 = ''
    if (${env:ProgramFiles(x86)}) { $programFilesX86 = [System.IO.Path]::GetFullPath(${env:ProgramFiles(x86)}).TrimEnd('\') }
    $problems = New-Object System.Collections.ArrayList
    if (-not (Test-Path $InstallDir)) { [void]$problems.Add('安装目录不存在') }
    if (-not (Test-Path (Join-Path $InstallDir 'runtime\python.exe'))) { [void]$problems.Add('缺 runtime\python.exe') }
    if ($installFull.ToLower().StartsWith($localAppData.ToLower())) { [void]$problems.Add('落到了 %LOCALAPPDATA%') }
    if ($installFull.ToLower().StartsWith($programFiles.ToLower())) { [void]$problems.Add('落到了 Program Files') }
    if ($programFilesX86 -and $installFull.ToLower().StartsWith($programFilesX86.ToLower())) { [void]$problems.Add('落到了 Program Files (x86)') }
    if (-not $installFull.ToLower().StartsWith($driveRoot.ToLower())) { [void]$problems.Add("不在指定盘 $driveRoot") }

    $markerPath = Join-Path $InstallDir 'data-dir.txt'
    $markerText = ''
    if (Test-Path $markerPath) {
        $markerText = (Get-Content $markerPath -Raw -Encoding UTF8).Trim().Trim('"')
    }
    $expectedData = [System.IO.Path]::GetFullPath($DataDir).TrimEnd('\')
    $markerOk = ($markerText -and ([System.IO.Path]::GetFullPath($markerText).TrimEnd('\').ToLower() -eq $expectedData.ToLower()))
    if (-not $markerOk) { [void]$problems.Add("data-dir.txt 缺失或指向别处（实际：$markerText）") }
    if (-not (Test-Path $DataDir)) { [void]$problems.Add('数据目录不存在') }

    $listing = ''
    try { $listing = (Get-ChildItem $InstallDir -ErrorAction SilentlyContinue | Select-Object Name, Length | Format-Table -AutoSize | Out-String) } catch { }
    $ev = Write-Evidence 's01_install_dir.txt' ("安装目录：{0}`r`n指定盘：{1}`r`nLOCALAPPDATA：{2}`r`ndata-dir.txt：{3}`r`n数据目录存在：{4}`r`n`r`n目录内容：`r`n{5}" -f `
            $InstallDir, $driveRoot, $localAppData, $markerText, (Test-Path $DataDir), $listing)
    if ($problems.Count -eq 0) {
        Add-Result 'S-01' 'PASS' $ev ("安装目录 {0} 在 {1}，data-dir.txt 指向 {2}" -f $InstallDir, $driveRoot, $DataDir)
    } else {
        Add-Result 'S-01' 'FAIL' $ev ("S-01 断言未通过：{0}" -f ($problems -join '；'))
    }
}

# ---- S-02 快捷方式与图标
if ($script:InstallPerformed) {
    $desktopDir = [Environment]::GetFolderPath('Desktop')
    if (-not $desktopDir) { $desktopDir = Join-Path $env:USERPROFILE 'Desktop' }
    $programsDir = [Environment]::GetFolderPath('Programs')
    $searchDirs = @($desktopDir, (Join-Path $programsDir $StartMenuGroup), (Join-Path $programsDir 'LiuHao'), $programsDir)
    $lnks = New-Object System.Collections.ArrayList
    foreach ($dir in $searchDirs) {
        if (-not (Test-Path $dir)) { continue }
        $files = Get-ChildItem -Path $dir -Filter *.lnk -File -Recurse -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            if (-not ($lnks | Where-Object { $_.Path -eq $f.FullName })) { [void]$lnks.Add((Get-ShortcutInfo $f.FullName)) }
        }
    }
    $launchers = @($lnks | Where-Object { $_.Arguments -match 'liuhao_desktop\.py' })
    $desktopLnk = @($launchers | Where-Object { $_.Path.ToLower().StartsWith($desktopDir.ToLower()) } | Select-Object -First 1)
    $menuLnk = @($launchers | Where-Object { $_.Path.ToLower().StartsWith($programsDir.ToLower()) } | Select-Object -First 1)

    $problems = New-Object System.Collections.ArrayList
    if ($desktopLnk.Count -eq 0) { [void]$problems.Add('桌面快捷方式缺失') }
    if ($menuLnk.Count -eq 0) { [void]$problems.Add('开始菜单快捷方式缺失') }
    # 开始菜单项必须落在验收专用组目录里。安装器带 UsePreviousGroup=yes 时会沿用
    # 上一次安装记录的组名（产品组 LiuHao），/GROUP= 形同虚设 —— 于是验收安装把
    # 真实用户的开始菜单项改成指向验收目录，正是「点开始菜单拉起另一份副本」的成因。
    if ($menuLnk.Count -gt 0) {
        $expectedGroupDir = Join-Path $programsDir $StartMenuGroup
        if (-not $menuLnk.Path.ToLower().StartsWith($expectedGroupDir.ToLower())) {
            [void]$problems.Add("开始菜单项不在验收组 $StartMenuGroup 下（实际：$($menuLnk.Path)）")
        }
    }
    # 开始菜单/桌面快捷方式绝不能指向验收临时目录或 pytest 临时目录：那会让
    # 用户点图标时拉起另一份副本、另一个数据目录，表现为“账号数据丢了”。
    $forbiddenLnk = @('cache\temp', 'cache\\temp', 'pytest-of-', 'appdata\local\temp', 'appdata\\local\\temp')
    $details = New-Object System.Collections.ArrayList
    foreach ($lnk in @($desktopLnk + $menuLnk)) {
        if (-not $lnk) { continue }
        $lnkText = ("{0} {1} {2}" -f $lnk.TargetPath, $lnk.Arguments, $lnk.WorkingDirectory).ToLower()
        $targetOk = $lnk.TargetPath -and (Test-Path $lnk.TargetPath) -and
            $lnk.TargetPath.ToLower().EndsWith('\runtime\pythonw.exe') -and
            $lnk.TargetPath.ToLower().StartsWith($installFull.ToLower())
        # 2026-11 桌面端口径：快捷方式只传 `app`，不再带 --open-browser
        # （该参数曾把原生窗口切到浏览器）。这里不能放宽成"参数随便"：
        # 必须仍是 liuhao_desktop.py + app，且不得再出现 --open-browser / --web。
        $argsOk = ($lnk.Arguments -match 'liuhao_desktop\.py') -and ($lnk.Arguments -match '\bapp\b') -and
            ($lnk.Arguments -notmatch '--open-browser') -and ($lnk.Arguments -notmatch '\s--web\b')
        $cleanOk = $true
        foreach ($marker in $forbiddenLnk) {
            if ($lnkText.Contains($marker)) { $cleanOk = $false }
        }
        if (-not $cleanOk) { [void]$problems.Add("$($lnk.Path) 指向临时目录（目标/参数/工作目录含 cache\temp、pytest-of- 或 AppData\Local\Temp）") }
        $iconRaw = $lnk.IconLocation
        $iconPath = $iconRaw
        if ($iconRaw -match '^(.*?),\s*-?\d+$') { $iconPath = $Matches[1] }
        $iconPath = $iconPath.Trim().Trim('"')
        $iconOk = Test-IcoFile $iconPath
        $wdOk = $lnk.WorkingDirectory -and ([System.IO.Path]::GetFullPath($lnk.WorkingDirectory).TrimEnd('\').ToLower() -eq $installFull.ToLower())
        if (-not $targetOk) { [void]$problems.Add("$($lnk.Path) 目标不是 runtime\pythonw.exe 或目标不存在") }
        if (-not $argsOk) { [void]$problems.Add("$($lnk.Path) 参数不是 liuhao_desktop.py app（含 --open-browser/--web 也判失败）") }
        if (-not $iconOk) { [void]$problems.Add("$($lnk.Path) 图标不是有效 ICO 或文件缺失：$iconPath") }
        if (-not $wdOk) { [void]$problems.Add("$($lnk.Path) 工作目录不是安装目录") }
        [void]$details.Add(("路径={0}`r`n  目标={1}`r`n  参数={2}`r`n  图标={3}（有效={4}）`r`n  工作目录={5}" -f `
                $lnk.Path, $lnk.TargetPath, $lnk.Arguments, $iconPath, $iconOk, $lnk.WorkingDirectory))
    }
    $shot = Save-Screenshot 's02_desktop.png'
    $ev = Write-Evidence 's02_shortcuts.txt' ("桌面目录：{0}`r`n开始菜单：{1}`r`n匹配快捷方式数：{2}`r`n`r`n{3}`r`n截图：{4}" -f `
            $desktopDir, $programsDir, $launchers.Count, ($details -join "`r`n`r`n"), $(if ($shot) { $shot } else { '<当前会话无桌面，未能截图>' }))
    if ($problems.Count -eq 0) {
        Add-Result 'S-02' 'PASS' $ev ("桌面与开始菜单快捷方式均指向 runtime\pythonw.exe，参数为 app（不含 --open-browser），图标 liuhao.ico 有效")
    } else {
        Add-Result 'S-02' 'FAIL' $ev ("S-02 断言未通过：{0}" -f ($problems -join '；'))
    }
} else {
    $ev = Write-Evidence 's02_shortcuts.txt' '未执行安装，无法校验安装器创建的快捷方式与图标。'
    Add-Result 'S-02' 'SKIP(未取证)' $ev '未执行安装，跳过图标与快捷方式检查'
}

# __pycache__ 基线：安装完成后（或 -SkipInstall 时的既有安装树）先记一次，
# S-14 只判「本次冒烟有没有新增字节码目录」。
$script:PycacheBaseline = Count-PycacheDirs $InstallDir

# ---- 预置登录凭据（用产品自带的注册 CLI，等价于首启向导建号）
$script:IdentityReady = $false
$script:SeedVerified = $false
$runtimePython = Join-Path $InstallDir 'runtime\python.exe'
$runtimePythonw = Join-Path $InstallDir 'runtime\pythonw.exe'
$launcherScript = Join-Path $InstallDir 'scripts\launch_installed.py'
$seedLog = Join-Path $script:EvidenceDir 's05_seed_identity.log'

if ((Test-Path $runtimePython) -and (Test-Path (Join-Path $InstallDir 'scripts\register_human_identity.py'))) {
    Set-GatewayEnv
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_ROOT', $InstallDir, 'Process')
        [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_DATA', $DataDir, 'Process')
        [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_PRINCIPAL', $AdminPrincipal, 'Process')
        $code = @"
import os, sys, io, runpy
ROOT = os.environ['LIUHAO_SMOKE_ROOT']
DATA = os.environ['LIUHAO_SMOKE_DATA']
sys.stdin = io.StringIO(os.environ['LIUHAO_SMOKE_SECRET'] + '\n')
sys.path.insert(0, ROOT)
os.environ['LIUHAO_DATA_DIR'] = DATA
os.environ['LIUHAO_ALLOW_ENV_OVERRIDE'] = '1'
from scripts.launch_installed import prepare_environment
prepare_environment()
sys.argv = ['register_human_identity.py', '--principal', os.environ['LIUHAO_SMOKE_PRINCIPAL'], '--display-name', 'Install Smoke Admin', '--permissions', 'admin', '--password-stdin']
runpy.run_path(os.path.join(ROOT, 'scripts', 'register_human_identity.py'), run_name='__main__')
"@
        New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
        $seedOut = Invoke-PythonWithSecret -Python $runtimePython -Code $code -Secret $script:AdminSecret
        $seedCode = $LASTEXITCODE
        # 自检：用 PowerShell 侧持有的同一口令回读凭据库做校验。
        # 同时比对「Python 读到的口令」与「PowerShell 持有的口令」的 SHA256，
        # 只留布尔结果，哈希与口令本身都不落盘。
        $verifyCode = $script:CredentialVerifyCode
        $rawVerify = Invoke-PythonWithSecret -Python $runtimePython -Code $verifyCode -Secret $script:AdminSecret
        $pySha = ''
        if ($rawVerify -match 'SEED_SECRET_SHA=([0-9a-f]{64})') { $pySha = $Matches[1] }
        $pipeMatch = ($pySha -ne '' -and $pySha -eq (Get-StringSha256 $script:AdminSecret))
        $script:SeedVerified = (@($rawVerify -match 'SEED_VERIFY=ok').Count -gt 0 -and $pipeMatch)
        $verifyOut = ($rawVerify -replace 'SEED_SECRET_SHA=[0-9a-f]{64}', 'SEED_SECRET_SHA=<已比对>') + ("STDIN_MATCH={0}" -f $pipeMatch)
        [System.IO.File]::WriteAllText($seedLog, (Protect-Text ($seedOut + "`r`n---- 凭据回读自检 ----`r`n" + $verifyOut)), $script:Utf8NoBom)
        $identities = Join-Path $DataDir 'config\human_identities.json'
        $script:IdentityReady = ($seedCode -eq 0 -and $script:SeedVerified -and (Test-Path $identities) -and ((Get-Item $identities).Length -gt 2))
        Write-RunLog ("# 预置管理员凭据：退出码={0} 凭据回读={1} 登记表={2}" -f $seedCode, $script:SeedVerified, $identities)
    } catch {
        $script:IdentityReady = $false
        Write-RunLog ("# 预置管理员凭据失败：{0}" -f $_.Exception.Message)
        [System.IO.File]::WriteAllText($seedLog, (Protect-Text $_.Exception.Message), $script:Utf8NoBom)
    } finally {
        [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_ROOT', $null, 'Process')
        [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_DATA', $null, 'Process')
        [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_PRINCIPAL', $null, 'Process')
        $ErrorActionPreference = $prevEap
        Restore-GatewayEnv
    }
}

# ---- S-03 启动与健康检查
$baseUrl = "http://127.0.0.1:$Port"
$healthUrl = "$baseUrl/v1/health"
$readyUrl = "$baseUrl/v1/ready"
$startupOut = Join-Path $script:EvidenceDir 's03_launcher.out.log'
$startupErr = Join-Path $script:EvidenceDir 's03_launcher.err.log'

if (Test-Path $runtimePython) {
    if (-not (Test-Path $launcherScript)) {
        $ev = Write-Evidence 's03_process.txt' ("缺少启动脚本：{0}" -f $launcherScript)
        Add-Result 'S-03' 'FAIL' $ev '安装目录缺少 scripts\launch_installed.py，无法启动控制台'
    } else {
        Set-GatewayEnv
        try {
            $argStr = '-u "' + $launcherScript + '" --skip-wizard --api-port ' + $Port
            $script:LauncherProc = Start-Process -FilePath $runtimePython -ArgumentList $argStr `
                -WorkingDirectory $InstallDir -RedirectStandardOutput $startupOut -RedirectStandardError $startupErr `
                -WindowStyle Hidden -PassThru
        } catch {
            $ev = Write-Evidence 's03_process.txt' ("启动失败：{0}" -f $_.Exception.Message)
            Add-Result 'S-03' 'FAIL' $ev ("启动器无法启动：{0}" -f $_.Exception.Message)
        } finally {
            Restore-GatewayEnv
        }

        if ($script:LauncherProc) {
            $health = Wait-HttpOk -Uri $healthUrl -TimeoutSec $StartupTimeoutSec
            $pidNow = 0
            if ($health.Ok) { $pidNow = Get-GatewayPid -LauncherPid $script:LauncherProc.Id }
            $listenInfo = ''
            try { $listenInfo = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Format-Table -AutoSize | Out-String) } catch { }
            $launcherTail = Get-LogTail $startupOut 60
            $ev = Write-Evidence 's03_process.txt' ("进程 PID={0}（存活={1}）`r`n网关 PID={2}`r`n健康检查={3}（HTTP {4}，耗时 {5:N1}s）`r`n监听：`r`n{6}`r`n启动器输出尾部：`r`n{7}" -f `
                    $script:LauncherProc.Id, (-not $script:LauncherProc.HasExited), $pidNow,
                    $(if ($health.Ok) { 'OK' } else { 'FAIL' }), $(if ($health.Last) { $health.Last.StatusCode } else { 0 }), $health.Elapsed,
                    $listenInfo, $launcherTail)
            if ($health.Ok -and -not $script:LauncherProc.HasExited) {
                $script:GatewayPidBefore = $pidNow
                $healthBody = Protect-Text ([string]$health.Last.Body)
                [System.IO.File]::WriteAllText((Join-Path $script:EvidenceDir 's03_health.json'), $healthBody, $script:Utf8NoBom)
                Add-Result 'S-03' 'PASS' $ev ("进程已起、端口 {0} 监听、/v1/health 返回 {1}" -f $Port, $health.Last.StatusCode)
            } else {
                if (-not $health.Ok) {
                    Add-Result 'S-03' 'FAIL' $ev ("{0}s 内 /v1/health 未返回 2xx；启动器存活={1}" -f $StartupTimeoutSec, (-not $script:LauncherProc.HasExited))
                } else {
                    Add-Result 'S-03' 'FAIL' $ev '健康检查通过但启动器进程已退出，服务无法持续提供'
                }
            }
        }
    }
} else {
    $ev = Write-Evidence 's03_process.txt' ("安装目录不可用，缺少 {0}" -f $runtimePython)
    Add-Result 'S-03' 'SKIP(未取证)' $ev '安装目录不存在 runtime\python.exe，无法启动控制台'
}

$serviceUsable = ($script:GatewayPidBefore -gt 0)

# ---- S-04 浏览器自动打开
if ($serviceUsable) {
    $urlFile = Join-Path $DataDir 'config\current_url.txt'
    $urlRaw = ''
    $urlRecord = ''
    if (Test-Path $urlFile) {
        $urlRaw = (Get-Content $urlFile -Raw -Encoding UTF8).Trim()
        # 启动器写的是 key=value 多行格式（url=/port=/pid=/started_at=），也可能只有一行 URL
        if ($urlRaw -match '(?m)^url=(.+)$') {
            $urlRecord = $Matches[1].Trim()
        } else {
            $urlRecord = $urlRaw
        }
    }
    $consoleResp = Invoke-SmokeHttp -Method GET -Uri ($baseUrl + '/') -TimeoutSec 15
    $progId = ''
    $browserCmd = ''
    $browserExe = ''
    try {
        $choice = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice' -ErrorAction SilentlyContinue
        if ($choice) { $progId = [string]$choice.ProgId }
        if ($progId) {
            $cmdKey = Get-Item ("Registry::HKEY_CLASSES_ROOT\$progId\shell\open\command") -ErrorAction SilentlyContinue
            if ($cmdKey) { $browserCmd = [string]$cmdKey.GetValue('') }
        }
    } catch { }
    if ($browserCmd -match '"([^"]+\.exe)"') {
        $browserExe = $Matches[1]
    } elseif ($browserCmd -match '^(\S+\.exe)') {
        $browserExe = $Matches[1]
    }
    $browserExeOk = $browserExe -and (Test-Path $browserExe)
    $urlOk = $urlRecord -match ('^(?:http://127\.0\.0\.1:' + [regex]::Escape([string]$Port) + ')(?:/.*)?$')
    $consoleOk = ($consoleResp.Ok -and $consoleResp.StatusCode -eq 200)
    $shot = Save-Screenshot 's04_console.png'
    $ev = Write-Evidence 's04_browser.txt' ("current_url.txt 原始内容：`r`n{0}`r`n解析出的 URL：{1}（存在={2}，端口匹配={3}）`r`n控制台 URL HTTP：{4}（{5}）`r`n默认浏览器 ProgId：{6}`r`n默认浏览器命令：{7}`r`n默认浏览器可执行存在：{8}`r`n截图：{9}`r`n判定口径：启动器在 --skip-wizard 启动成功后会写 current_url.txt 并调用 webbrowser.open；脚本用「URL 记录 + URL 可访问 + 系统默认浏览器可解析」三条证据判定自动打开链路可用，真实窗口是否弹出需人工确认。" -f `
            $urlRaw, $urlRecord, (Test-Path $urlFile), $urlOk, $consoleResp.StatusCode, $consoleResp.Ok, $progId, $browserCmd, $browserExeOk,
            $(if ($shot) { $shot } else { '<当前会话无桌面，未能截图>' }))
    if ($urlOk -and $consoleOk -and $browserExeOk) {
        Add-Result 'S-04' 'PASS' $ev ('current_url.txt 记录 {0}/ 且可访问，默认浏览器 {1} 可解析' -f $baseUrl, (Split-Path $browserExe -Leaf))
    } elseif ($urlOk -and $consoleOk) {
        Add-Result 'S-04' 'NEEDS_HUMAN' $ev 'URL 记录与控制台访问均正常，但系统没有可解析的默认浏览器；需人工在有桌面/浏览器的机器上双击确认'
    } else {
        Add-Result 'S-04' 'FAIL' $ev '未发现 current_url.txt 记录或控制台 URL 不可访问，自动打开链路不成立'
    }
} else {
    $ev = Write-Evidence 's04_browser.txt' '服务未启动，无法观测浏览器自动打开。'
    Add-Result 'S-04' 'SKIP(未取证)' $ev '服务未启动，跳过浏览器自动打开检查'
}

# ---- S-05 登录
$script:LoginElapsed = -1
if ($serviceUsable -and $script:IdentityReady) {
    $login = Invoke-SmokeHttp -Method POST -Uri "$baseUrl/v1/auth/login" `
        -Body @{ principal = $AdminPrincipal; secret = $script:AdminSecret; client = 'desktop' } `
        -TimeoutSec $LoginTimeoutSec
    $script:LoginElapsed = $login.Elapsed
    $parsed = $null
    if ($login.Ok -and $login.Body) {
        try { $parsed = $login.Body | ConvertFrom-Json } catch { }
    }
    if ($parsed -and $parsed.access_token) {
        $script:Token = [string]$parsed.access_token
    }
    $me = $null
    if ($script:Token) { $me = Invoke-SmokeHttp -Method GET -Uri "$baseUrl/v1/auth/me" -Token $script:Token -TimeoutSec 20 }
    # 登录失败时的定位证据：凭据库与脚本口令是否一致（只留 ok / mismatch）。
    $credDiag = '<未执行>'
    if (-not ($login.Ok -and $script:Token) -and (Test-Path $runtimePython)) {
        Set-GatewayEnv
        $prevEapDiag = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_ROOT', $InstallDir, 'Process')
            [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_DATA', $DataDir, 'Process')
            [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_PRINCIPAL', $AdminPrincipal, 'Process')
            $diagOut = Invoke-PythonWithSecret -Python $runtimePython -Code $script:CredentialVerifyCode -Secret $script:AdminSecret
            if ($diagOut -match 'SEED_VERIFY=ok') { $credDiag = 'ok（凭据库匹配脚本口令，问题在网关侧读取）' }
            elseif ($diagOut -match 'SEED_VERIFY=mismatch') { $credDiag = 'mismatch（凭据库与脚本口令不一致）' }
            else { $credDiag = 'unknown（自检无结论）' }
        } catch { $credDiag = 'error（自检执行失败）' } finally {
            [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_ROOT', $null, 'Process')
            [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_DATA', $null, 'Process')
            [Environment]::SetEnvironmentVariable('LIUHAO_SMOKE_PRINCIPAL', $null, 'Process')
            $ErrorActionPreference = $prevEapDiag
            Restore-GatewayEnv
        }
    }
    $ev = Write-Evidence 's05_login.json' ("状态码={0}`r`n耗时={1:N2}s`r`n令牌已获取={2}`r`n/auth/me 状态码={3}`r`n凭据回读={4}`r`n响应体（已脱敏）：`r`n{5}`r`n/auth/me（已脱敏）：`r`n{6}" -f `
            $login.StatusCode, $login.Elapsed, [bool]$script:Token, $(if ($me) { $me.StatusCode } else { 0 }), $credDiag,
            $login.Body, $(if ($me) { $me.Body } else { '' }))
    if ($login.Ok -and $script:Token -and $me -and $me.Ok) {
        Add-Result 'S-05' 'PASS' $ev ("登录成功拿到令牌，/auth/me 校验通过（{0:N1}s）" -f $login.Elapsed)
    } else {
        $reason = if ($login.TimedOut) { '登录请求超时（已计入 S-11 观测）' } else { '登录未拿到令牌或 /auth/me 校验失败' }
        Add-Result 'S-05' 'FAIL' $ev $reason
    }
    if ($login.TimedOut -or $login.Elapsed -ge 60) {
        $script:S11Hit = $true
        $script:S11Detail = ("登录请求 pending {0:N1}s（超时={1}）" -f $login.Elapsed, $login.TimedOut)
    }
} else {
    $ev = Write-Evidence 's05_login.json' ("服务可用={0}`r`n身份预置成功={1}" -f $serviceUsable, $script:IdentityReady)
    Add-Result 'S-05' 'SKIP(未取证)' $ev '服务未启动或管理员凭据未预置，无法执行登录'
}

# ---- S-06 填 Key 即用（不重启）
$gatewayPidBeforeChat = 0
if ($serviceUsable -and $script:Token) {
    $gatewayPidBeforeChat = Get-GatewayPid -LauncherPid $script:LauncherProc.Id
} elseif ($serviceUsable) {
    $gatewayPidBeforeChat = $script:GatewayPidBefore
}

if ($serviceUsable -and $script:Token -and $script:ModelKey) {
    $modelName = "install-smoke-deepseek-$stamp"
    $create = Invoke-SmokeHttp -Method POST -Uri "$baseUrl/v1/models" -Token $script:Token -TimeoutSec 30 `
        -Body @{
            name = $modelName
            provider = 'deepseek'
            model = 'deepseek-chat'
            base_url = 'https://api.deepseek.com/v1'
            api_key = $script:ModelKey
            timeout = 60.0
            enabled = $true
            # 不带 is_default：新建模型还没「测试连接」通过，产品会按设计返回
            # 409（"新建的模型还没有测试通过，不能直接设为默认"）。界面保存
            # 新模型时同样不会带这个字段，脚本必须与界面同一条路径。
        }
    $list = Invoke-SmokeHttp -Method GET -Uri "$baseUrl/v1/models" -Token $script:Token -TimeoutSec 20
    $modelId = ''
    if ($create.Ok -and $create.Body) {
        try {
            $created = $create.Body | ConvertFrom-Json
            if ($created.id) { $modelId = [string]$created.id }
        } catch { }
    }
    $conversationBefore = Get-FileSizeSafe (Join-Path $DataDir 'conversation_store.db')
    $chat = Invoke-SmokeHttp -Method POST -Uri "$baseUrl/v1/chat" -Token $script:Token -TimeoutSec $ChatTimeoutSec `
        -Body @{
            message = '只回复「安装冒烟通过」四个字，不要解释。'
            session_id = "install-smoke-$stamp"
            model_id = $modelId
            use_knowledge = $false
        }
    $script:ChatElapsed = $chat.Elapsed
    $chatJson = $null
    if ($chat.Ok -and $chat.Body) {
        try { $chatJson = $chat.Body | ConvertFrom-Json } catch { }
    }
    $gatewayPidAfterChat = Get-GatewayPid -LauncherPid $script:LauncherProc.Id
    $pidSame = ($gatewayPidBeforeChat -gt 0 -and $gatewayPidAfterChat -eq $gatewayPidBeforeChat)
    $replyOk = ($chatJson -and $chatJson.reply -and ([string]$chatJson.reply).Trim().Length -gt 0)
    $statusOk = ($chatJson -and ([string]$chatJson.status) -notin @('error', 'failed'))
    $ev = Write-Evidence 's06_model_chat.json' ("建模型：HTTP {0}，耗时 {1:N2}s`r`n模型 id：{2}`r`n模型列表：HTTP {3}`r`n对话：HTTP {4}，耗时 {5:N2}s，status={6}，reply 非空={7}`r`n网关 PID 前={8} 后={9}（未重启={10}）`r`n模型列表响应（已脱敏）：`r`n{11}`r`n对话响应（已脱敏）：`r`n{12}" -f `
            $create.StatusCode, $create.Elapsed, $modelId, $list.StatusCode, $chat.StatusCode, $chat.Elapsed,
            $(if ($chatJson) { [string]$chatJson.status } else { '<无>' }), [bool]$replyOk,
            $gatewayPidBeforeChat, $gatewayPidAfterChat, $pidSame, $list.Body, $chat.Body)
    if ($create.Ok -and $replyOk -and $statusOk -and $pidSame) {
        Add-Result 'S-06' 'PASS' $ev ("填入真实 DeepSeek Key 后未重启后端（PID {0} 不变）即收到非错误回复（{1:N1}s）" -f $gatewayPidBeforeChat, $chat.Elapsed)
    } elseif ($create.Ok -and $statusOk -and $replyOk -and -not $pidSame) {
        Add-Result 'S-06' 'FAIL' $ev '对话成功但网关 PID 发生变化，疑似后端重启后才生效'
    } else {
        $detail = ''
        if ($chatJson -and $chatJson.reason) { $detail = [string]$chatJson.reason }
        Add-Result 'S-06' 'FAIL' $ev ("填 Key 后对话未拿到非错误回复（模型 HTTP {0} / 对话 HTTP {1} / reason={2}）" -f $create.StatusCode, $chat.StatusCode, $detail)
    }
    if ($chat.TimedOut -or $chat.Elapsed -ge 60) {
        $script:S11Hit = $true
        if ($script:S11Detail) { $script:S11Detail += '；' }
        $script:S11Detail += ("对话请求 pending {0:N1}s（超时={1}）" -f $chat.Elapsed, $chat.TimedOut)
    }
} else {
    $reason = '未提供服务或未提供模型 Key'
    if ($serviceUsable -and -not $script:ModelKey) { $reason = '未提供真实模型 Key（-ModelKey 或环境变量 LIUHAO_SMOKE_MODEL_KEY）' }
    $ev = Write-Evidence 's06_model_chat.json' ("服务可用={0}`r`n令牌可用={1}`r`nKey 提供={2}`r`n原因={3}" -f `
            $serviceUsable, [bool]$script:Token, [bool]$script:ModelKey, $reason)
    Add-Result 'S-06' 'SKIP(未取证)' $ev $reason
}

# ---- S-07 回环写入（登录 / 消息 / 目标）
$script:ChatArtifactsBefore = @{
    conversation = Get-FileSizeSafe (Join-Path $DataDir 'conversation_store.db')
    goals = Get-FileSizeSafe (Join-Path $DataDir 'workspace\liuhao_goals.json')
    audit = Get-FileSizeSafe (Join-Path $DataDir 'audit_store.db')
}
if ($serviceUsable -and $script:Token) {
    $goalId = ''
    $goalCreate = Invoke-SmokeHttp -Method POST -Uri "$baseUrl/v1/goals" -Token $script:Token -TimeoutSec 45 `
        -Body @{
            natural_language = '安装冒烟写入验证：这是一条由验收脚本创建的临时目标，无需真正执行。'
            scope = 'L1'
            background = $true
        }
    if ($goalCreate.Ok -and $goalCreate.Body) {
        try {
            $goalJson = $goalCreate.Body | ConvertFrom-Json
            if ($goalJson.goal_id) { $goalId = [string]$goalJson.goal_id }
        } catch { }
    }
    $goalStop = $null
    if ($goalId) {
        $goalStop = Invoke-SmokeHttp -Method POST -Uri ("$baseUrl/v1/goals/$goalId/stop") -Token $script:Token -TimeoutSec 20
    }
    Start-Sleep -Milliseconds 800
    $goalsFile = Join-Path $DataDir 'workspace\liuhao_goals.json'
    $goalsSize = Get-FileSizeSafe $goalsFile
    $goalsHasId = $false
    if ($goalId -and (Test-Path $goalsFile)) {
        try { $goalsHasId = ((Get-Content $goalsFile -Raw -Encoding UTF8) -match [regex]::Escape($goalId)) } catch { }
    }
    $convSize = Get-FileSizeSafe (Join-Path $DataDir 'conversation_store.db')
    $auditSize = Get-FileSizeSafe (Join-Path $DataDir 'audit_store.db')
    # 会话库是 WAL 模式：消息先写进 -wal，主库文件在检查点时才会变大。
    # 只看主库文件会把"写成功但还没 checkpoint"误判成"未增长"，
    # 所以主库里以 /v1/chat/history 的实际消息数为准，文件尺寸只作旁证。
    $hist = Invoke-SmokeHttp -Method GET -Uri ("$baseUrl/v1/chat/history?session_id=install-smoke-$stamp") -Token $script:Token -TimeoutSec 20
    $histCount = -1
    if ($hist.Ok -and $hist.Body) {
        try {
            $histJson = $hist.Body | ConvertFrom-Json
            $histCount = @($histJson.messages).Count
        } catch { }
    }
    $walSize = Get-FileSizeSafe ((Join-Path $DataDir 'conversation_store.db') + '-wal')
    $convBytesNow = $convSize + $walSize
    $convGrew = ($histCount -ge 2) -or ($convBytesNow -gt $script:ChatArtifactsBefore.conversation)
    # audit 可能在会话结束时异步落库，允许相等但不得变小
    $auditOk = ($auditSize -ge $script:ChatArtifactsBefore.audit)
    $appLog = Join-Path $DataDir 'logs\liuhao.log'
    $securityCount = 0
    if (Test-Path $appLog) {
        try { $securityCount = @(Select-String -Path $appLog -Pattern 'SECURITY_REJECTED' -SimpleMatch -ErrorAction SilentlyContinue).Count } catch { }
    }
    $loginOk = [bool]$script:Token
    $chatOk = ($script:ChatElapsed -ge 0 -and $script:ChatElapsed -lt $ChatTimeoutSec)
    $goalOk = ($goalCreate.Ok -and $goalId)
    $ev = Write-Evidence 's07_loopback_write.txt' ("登录写：{0}`r`n消息写：{1}（耗时 {2:N1}s）`r`n目标写：HTTP {3}，goal_id={4}，停止 HTTP {5}`r`n目标文件 {6} 大小={7}，含 goal_id={8}`r`nconversation_store.db 大小={9}（前 {10}，增长={11}）`r`naudit_store.db 大小={12}（前 {13}）`r`nSECURITY_REJECTED 行数={14}（日志 {15}）`r`n会话历史：HTTP {16}，消息数={17}（-wal 大小={18}，主库+wal={19}）" -f `
            $loginOk, $chatOk, $script:ChatElapsed, $goalCreate.StatusCode, $goalId,
            $(if ($goalStop) { $goalStop.StatusCode } else { 0 }), $goalsFile, $goalsSize, $goalsHasId,
            $convSize, $script:ChatArtifactsBefore.conversation, $convGrew, $auditSize, $script:ChatArtifactsBefore.audit,
            $securityCount, $appLog, $hist.StatusCode, $histCount, $walSize, $convBytesNow)
    if (-not $script:ModelKey -or $script:ChatElapsed -lt 0) {
        Add-Result 'S-07' 'SKIP(未取证)' $ev ("登录写={0}、目标写={1}；缺少真实 Key 无法完成消息写，整项不判通过" -f $loginOk, $goalOk)
    } elseif ($loginOk -and $chatOk -and $goalOk -and $goalsHasId -and $convGrew -and $auditOk -and $securityCount -eq 0) {
        Add-Result 'S-07' 'PASS' $ev ("登录/消息/目标三次写入均成功，liuhao_goals.json 含 goal_id，conversation_store.db 增长，SECURITY_REJECTED=0")
    } else {
        $issues = New-Object System.Collections.ArrayList
        if (-not $goalOk) { [void]$issues.Add('目标创建失败') }
        if (-not $goalsHasId) { [void]$issues.Add('目标未落盘') }
        if (-not $convGrew) { [void]$issues.Add('会话库未增长') }
        if ($securityCount -ne 0) { [void]$issues.Add("SECURITY_REJECTED=$securityCount") }
        Add-Result 'S-07' 'FAIL' $ev ("回环写入未全部满足：{0}" -f ($issues -join '；'))
    }
} else {
    $ev = Write-Evidence 's07_loopback_write.txt' ("服务可用={0}`r`n令牌可用={1}" -f $serviceUsable, [bool]$script:Token)
    Add-Result 'S-07' 'SKIP(未取证)' $ev '服务未启动或未登录，无法执行回环写入'
}

# ---- S-08 Key 不落明文
if ($script:ModelKey -and $serviceUsable) {
    $scanRoots = @($DataDir, (Join-Path $InstallDir 'config'))
    $hits = New-Object System.Collections.ArrayList
    $scanned = 0
    $scanErrors = 0
    foreach ($root in $scanRoots) {
        if (-not (Test-Path $root)) { continue }
        $files = Get-ChildItem -Path $root -Recurse -File -ErrorAction SilentlyContinue
        foreach ($f in $files) {
            if ($f.Length -gt 52428800) { continue }
            $scanned++
            try {
                $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
                $text = [System.Text.Encoding]::UTF8.GetString($bytes)
                if ($text.IndexOf($script:ModelKey, [System.StringComparison]::Ordinal) -ge 0) {
                    [void]$hits.Add($f.FullName)
                }
            } catch {
                $scanErrors++
            }
        }
    }
    $modelDb = Join-Path $DataDir 'data\model_registry.sqlite3'
    $keyFile = Join-Path $DataDir 'data\model_config.key'
    $ev = Write-Evidence 's08_key_scan.txt' ("扫描根：{0}`r`n扫描文件数={1}，读取失败={2}`r`n完整 Key 命中文件数={3}`r`n命中文件：{4}`r`nmodel_registry.sqlite3 存在={5}（大小 {6}）`r`nmodel_config.key 存在={7}（大小 {8}）" -f `
            ($scanRoots -join '；'), $scanned, $scanErrors, $hits.Count, $(if ($hits.Count -gt 0) { $hits -join '；' } else { '<无>' }),
            (Test-Path $modelDb), (Get-FileSizeSafe $modelDb), (Test-Path $keyFile), (Get-FileSizeSafe $keyFile))
    if ($hits.Count -eq 0) {
        Add-Result 'S-08' 'PASS' $ev ("扫描 {0} 个文件，未发现完整 Key 明文；加密库与密钥文件均存在" -f $scanned)
    } else {
        Add-Result 'S-08' 'FAIL' $ev ("在 {0} 个文件中发现完整 Key 明文（文件名已记录，内容未写入证据）" -f $hits.Count)
    }
} else {
    $reason = '未提供模型 Key，无法验证明文是否落盘'
    if (-not $serviceUsable) { $reason = '服务未启动，无法验证 Key 落盘' }
    $ev = Write-Evidence 's08_key_scan.txt' $reason
    Add-Result 'S-08' 'SKIP(未取证)' $ev $reason
}

# ---- S-09 关窗驻留 / 托盘退出（无桌面交互环境无法自动判定）
$ev = Write-Evidence 's09_manual.txt' @"
S-09 关窗驻留 / 托盘退出 —— 手工步骤与判定标准

自动化限制
  本脚本以静默方式启动网关，不创建托盘窗口；无桌面交互的会话无法点击「关闭按钮」或托盘菜单，
  因此本项不自动判定，标记 NEEDS_HUMAN。

手工步骤
  1. 双击桌面「LiuHao AI OS · 鎏灏智能中枢」快捷方式，等待控制台页面打开。
  2. 记下控制台端口，并在浏览器里登录成功（能落在总览页）。
  3. 点击状态窗口右上角「关闭」按钮（不是托盘「退出」）。
  4. 回到浏览器发一条消息：应仍能成功；同时执行
       Get-Process pythonw | Select-Object Id,CPU
       Get-NetTCPConnection -State Listen | Where-Object { `$_.LocalPort -eq <端口> }
     应仍能看到 pythonw 进程与端口监听。
  5. 右键任务栏通知区鎏灏图标 →「退出」。
  6. 再执行上面两条命令：pythonw 进程应消失，端口应释放（Get-NetTCPConnection 无输出）。

判定标准
  PASS：第 4 步消息成功且进程/端口仍在；第 6 步进程消失、端口释放。
  FAIL：关窗即断服（消息失败/端口消失），或托盘退出后仍有 pythonw 占用端口。
"@
if ($serviceUsable -or $script:InstallPerformed) {
    Add-Result 'S-09' 'NEEDS_HUMAN' $ev '托盘/关窗行为需要桌面交互，已给出可执行手工步骤与判定标准'
} else {
    Add-Result 'S-09' 'SKIP(未取证)' $ev '未执行安装且服务未启动，关窗驻留/托盘退出无法取证'
}

# ---- S-12 机器级密钥落盘 + 二次启动复用（只走安装版正常入口，不手工直起 uvicorn）
# 首启由 launch_installed.py 生成并持久化机器级密钥：config\app.env 里的
# LIUHAO_JWT_SECRET / SECRET_KEY，以及身份登记表完整性密钥 config\integrity.key。
# 判定：首启后这些文件存在且含密钥行；停进程再走同一入口二次启动，文件内容与时间戳不变；
# 第二次启动后登录仍可用。证据只记路径/哈希/时间戳，不打印密钥内容。
$jwtEnvFile = Join-Path $DataDir 'config\app.env'
$integrityKeyFile = Join-Path $DataDir 'config\integrity.key'
if ($serviceUsable -and $script:LauncherProc) {
    $keyBeforeEnv = Get-FileFingerprint $jwtEnvFile
    $keyBeforeInt = Get-FileFingerprint $integrityKeyFile
    $jwtLineCount = 0
    $secretKeyLineCount = 0
    if ($keyBeforeEnv.Exists) {
        try {
            $jwtLineCount = @(Select-String -Path $jwtEnvFile -Pattern '^LIUHAO_JWT_SECRET=' -ErrorAction SilentlyContinue).Count
            $secretKeyLineCount = @(Select-String -Path $jwtEnvFile -Pattern '^SECRET_KEY=' -ErrorAction SilentlyContinue).Count
        } catch { }
    }

    $firstLauncherPid = $script:LauncherProc.Id
    $firstProc = $script:LauncherProc
    Stop-ProcessTree -ProcessId $firstLauncherPid
    if ($script:GatewayPidBefore -gt 0) { Stop-ProcessTree -ProcessId $script:GatewayPidBefore }
    $firstStopped = Wait-ProcessExit -Process $firstProc -TimeoutSec 30
    Start-Sleep -Milliseconds 800

    $secondOut = Join-Path $script:EvidenceDir 's12_second_start.out.log'
    $secondErr = Join-Path $script:EvidenceDir 's12_second_start.err.log'
    $launcher2 = $null
    Set-GatewayEnv
    try {
        $argStr2 = '-u "' + $launcherScript + '" --skip-wizard --api-port ' + $Port
        $launcher2 = Start-Process -FilePath $runtimePython -ArgumentList $argStr2 `
            -WorkingDirectory $InstallDir -RedirectStandardOutput $secondOut -RedirectStandardError $secondErr `
            -WindowStyle Hidden -PassThru
    } catch {
        $launcher2 = $null
    } finally {
        Restore-GatewayEnv
    }

    $secondHealthOk = $false
    $secondHealthElapsed = -1
    $secondGwPid = 0
    if ($launcher2) {
        $h2 = Wait-HttpOk -Uri $healthUrl -TimeoutSec $StartupTimeoutSec
        $secondHealthOk = [bool]$h2.Ok
        $secondHealthElapsed = $h2.Elapsed
        if ($secondHealthOk) { $secondGwPid = Get-GatewayPid -LauncherPid $launcher2.Id }
    }
    $secondLoginOk = $false
    $secondLoginStatus = 0
    $secondLoginElapsed = -1
    if ($secondHealthOk -and $script:IdentityReady) {
        $l2 = Invoke-SmokeHttp -Method POST -Uri "$baseUrl/v1/auth/login" `
            -Body @{ principal = $AdminPrincipal; secret = $script:AdminSecret; client = 'desktop' } `
            -TimeoutSec $LoginTimeoutSec
        $secondLoginStatus = $l2.StatusCode
        $secondLoginElapsed = $l2.Elapsed
        if ($l2.Ok -and $l2.Body) {
            try {
                $parsed2 = $l2.Body | ConvertFrom-Json
                if ($parsed2.access_token) { $secondLoginOk = $true }
            } catch { }
        }
    }

    $keyAfterEnv = Get-FileFingerprint $jwtEnvFile
    $keyAfterInt = Get-FileFingerprint $integrityKeyFile
    $keyUnchanged = ($keyBeforeEnv.Exists -and $keyBeforeEnv.Hash -eq $keyAfterEnv.Hash -and $keyBeforeEnv.Mtime -eq $keyAfterEnv.Mtime -and
        $keyBeforeInt.Exists -and $keyBeforeInt.Hash -eq $keyAfterInt.Hash -and $keyBeforeInt.Mtime -eq $keyAfterInt.Mtime)

    if ($launcher2) {
        Stop-ProcessTree -ProcessId $launcher2.Id
        if ($secondGwPid -gt 0) { Stop-ProcessTree -ProcessId $secondGwPid }
        [void](Wait-ProcessExit -Process $launcher2 -TimeoutSec 20)
    }
    $script:LauncherProc = $null
    $script:GatewayPidBefore = 0

    $ev = Write-Evidence 's12_key_persist.txt' ("JWT/会话密钥文件：{0}`r`n  存在={1}；LIUHAO_JWT_SECRET 行数={2}；SECRET_KEY 行数={3}`r`n  首启 SHA256={4}；时间戳={5}`r`n  二次启动后 SHA256={6}；时间戳={7}`r`n身份完整性密钥文件：{8}`r`n  存在={9}；首启 SHA256={10}；二次启动后 SHA256={11}`r`n密钥内容是否变化={12}`r`n首启进程 PID={13}（已停={14}）`r`n二次启动网关 PID={15}；/v1/health 200={16}（耗时 {17:N1}s）`r`n二次启动登录：HTTP {18}，耗时 {19:N1}s，拿到令牌={20}`r`n二次启动输出尾部：`r`n{21}" -f `
        $jwtEnvFile, $keyBeforeEnv.Exists, $jwtLineCount, $secretKeyLineCount,
        $keyBeforeEnv.Hash, $keyBeforeEnv.Mtime, $keyAfterEnv.Hash, $keyAfterEnv.Mtime,
        $integrityKeyFile, $keyBeforeInt.Exists, $keyBeforeInt.Hash, $keyAfterInt.Hash,
        (-not $keyUnchanged), $firstLauncherPid, $firstStopped, $secondGwPid, $secondHealthOk,
        $secondHealthElapsed, $secondLoginStatus, $secondLoginElapsed, $secondLoginOk,
        (Get-LogTail $secondOut 40))
    if ($keyBeforeEnv.Exists -and $jwtLineCount -gt 0 -and $secondHealthOk -and $secondLoginOk -and $keyUnchanged) {
        Add-Result 'S-12' 'PASS' $ev '首启落盘机器级 JWT/完整性密钥；二次启动走安装版入口，密钥内容与时间戳不变且登录仍可用'
    } else {
        $issues = New-Object System.Collections.ArrayList
        if (-not $keyBeforeEnv.Exists) { [void]$issues.Add('首启未生成 config\app.env') }
        if ($jwtLineCount -eq 0) { [void]$issues.Add('app.env 无 LIUHAO_JWT_SECRET') }
        if (-not $secondHealthOk) { [void]$issues.Add('二次启动健康检查未通过') }
        if (-not $secondLoginOk) { [void]$issues.Add('二次启动登录失败') }
        if (-not $keyUnchanged) { [void]$issues.Add('二次启动后密钥文件被重新生成/改动') }
        Add-Result 'S-12' 'FAIL' $ev ("二次启动复用未成立：{0}" -f ($issues -join '；'))
    }
} else {
    $ev = Write-Evidence 's12_key_persist.txt' ("服务可用={0}`r`n原因=未通过安装版入口启动，无法验证密钥落盘与二次复用" -f $serviceUsable)
    Add-Result 'S-12' 'SKIP(未取证)' $ev '未通过安装版入口启动服务，跳过机器级密钥落盘与二次启动复用检查'
}

# ---- S-13 安装目录不得出现运行时落盘文件（手工直起造成的污染不应进安装树）
if (Test-Path $InstallDir) {
    $runtimeArtifacts = @(
        'data\model_registry.sqlite3', 'data\model_config.key', 'liuhao_jwt_revocation.db',
        '.liuhao_jwt_revocation.db', 'conversation_store.db', 'audit_store.db',
        'memory_store.db', 'personal_context.db', 'workspace\liuhao_goals.json',
        'config\auth_secrets.json'
    )
    $artifactHits = New-Object System.Collections.ArrayList
    foreach ($rel in $runtimeArtifacts) {
        if (Test-Path (Join-Path $InstallDir $rel)) { [void]$artifactHits.Add($rel) }
    }
    $ev = Write-Evidence 's13_install_tree_clean.txt' ("安装目录={0}`r`n检查项：{1}`r`n命中={2}" -f `
            $InstallDir, ($runtimeArtifacts -join '；'), $(if ($artifactHits.Count -gt 0) { $artifactHits -join '；' } else { '<无>' }))
    if ($artifactHits.Count -eq 0) {
        Add-Result 'S-13' 'PASS' $ev '安装目录未出现模型库/JWT 吊销库/会话库等运行时文件（数据都在数据目录）'
    } else {
        Add-Result 'S-13' 'FAIL' $ev ("安装目录出现运行时文件：{0}" -f ($artifactHits -join '；'))
    }
} else {
    $ev = Write-Evidence 's13_install_tree_clean.txt' '安装目录不存在，无法检查运行时文件污染。'
    Add-Result 'S-13' 'SKIP(未取证)' $ev '安装目录不存在，跳过运行时文件污染检查'
}

# ---- S-14 安装目录 __pycache__ 数量为 0（冒烟副作用不许进包）
if (Test-Path $InstallDir) {
    $pycacheAfter = Count-PycacheDirs $InstallDir
    $pycacheBaseline = $script:PycacheBaseline
    $ev = Write-Evidence 's14_no_pycache.txt' ("安装目录={0}`r`n启动前基线={1}`r`n结束后数量={2}`r`n增量={3}" -f `
            $InstallDir, $pycacheBaseline, $pycacheAfter, ($pycacheAfter - $pycacheBaseline))
    if ($pycacheAfter -eq 0) {
        Add-Result 'S-14' 'PASS' $ev '__pycache__ 数量为 0，安装树未被 Python 字节码污染'
    } elseif ($pycacheBaseline -gt 0 -and $pycacheAfter -le $pycacheBaseline) {
        Add-Result 'S-14' 'PASS' $ev ("既有安装自带 {0} 个 __pycache__（非本次安装产物）；本次冒烟未新增" -f $pycacheBaseline)
    } else {
        Add-Result 'S-14' 'FAIL' $ev ("__pycache__ 数量={0}（基线 {1}），本次运行新增了字节码目录" -f $pycacheAfter, $pycacheBaseline)
    }
} else {
    $ev = Write-Evidence 's14_no_pycache.txt' '安装目录不存在，无法统计 __pycache__。'
    Add-Result 'S-14' 'SKIP(未取证)' $ev '安装目录不存在，跳过 __pycache__ 统计'
}

# ---- S-10 卸载干净
if ($script:InstallPerformed -and -not $SkipUninstall) {
    $uninsExe = Join-Path $InstallDir 'unins000.exe'
    if (-not (Test-Path $uninsExe)) {
        $ev = Write-Evidence 's10_uninstall.txt' ("缺少卸载器：{0}" -f $uninsExe)
        Add-Result 'S-10' 'FAIL' $ev '安装目录缺少 unins000.exe，无法执行静默卸载'
    } else {
        # 先停掉本脚本启动的进程，避免文件被占用导致卸载卡住
        if ($script:LauncherProc) { Stop-ProcessTree -ProcessId $script:LauncherProc.Id }
        if ($script:GatewayPidBefore -gt 0) { Stop-ProcessTree -ProcessId $script:GatewayPidBefore }
        Start-Sleep -Milliseconds 800

        $uninsLog = Join-Path $script:EvidenceDir 's10_uninstall.log'
        $uninsArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="' + $uninsLog + '"'
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $uninsProc = Start-Process -FilePath $uninsExe -ArgumentList $uninsArgs -WindowStyle Hidden -PassThru
            $finished = Wait-ProcessExit -Process $uninsProc -TimeoutSec $UninstallTimeoutSec
            $sw.Stop()
            if (-not $finished) {
                Stop-ProcessTree -ProcessId $uninsProc.Id
                $ev = Write-Evidence 's10_uninstall.txt' ("静默卸载超时（> {0}s），已强制结束；日志={1}" -f $UninstallTimeoutSec, $uninsLog)
                Add-Result 'S-10' 'FAIL' $ev ("静默卸载挂住超过 {0}s（/VERYSILENT 不应挂住）" -f $UninstallTimeoutSec)
            } else {
                $code = [int]$uninsProc.ExitCode
                $allowed = @('licenses', 'LICENSE', 'THIRD-PARTY-NOTICES.txt', 'sbom-cyclonedx.json')
                # Inno 的卸载器在进程退出后才删自身 exe；同步 WaitForExit 返回时
                # unins000.exe 往往还在。给它 10s 宽限，别把正常的自删延迟判成残留。
                $uninsExePath = Join-Path $InstallDir 'unins000.exe'
                for ($i = 0; $i -lt 20; $i++) {
                    if (-not (Test-Path $uninsExePath)) { break }
                    Start-Sleep -Milliseconds 500
                }
                $leftovers = @()
                if (Test-Path $InstallDir) {
                    $leftovers = @(Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue |
                        Where-Object { $allowed -notcontains $_.Name } |
                        Select-Object -ExpandProperty Name)
                }
                $desktopDir = [Environment]::GetFolderPath('Desktop')
                if (-not $desktopDir) { $desktopDir = Join-Path $env:USERPROFILE 'Desktop' }
                $programsDir = [Environment]::GetFolderPath('Programs')
                $shortcutsLeft = New-Object System.Collections.ArrayList
                foreach ($dir in @($desktopDir, (Join-Path $programsDir 'LiuHao'),
                                    (Join-Path $programsDir $StartMenuGroup))) {
                    if (-not (Test-Path $dir)) { continue }
                    foreach ($f in (Get-ChildItem $dir -Filter *.lnk -File -ErrorAction SilentlyContinue)) {
                        $info = Get-ShortcutInfo $f.FullName
                        if (($info.TargetPath -and $info.TargetPath.ToLower().StartsWith($InstallDir.ToLower())) -or
                            ($info.Arguments -match 'liuhao_desktop\.py')) {
                            [void]$shortcutsLeft.Add($f.FullName)
                        }
                    }
                }
                $runHits = New-Object System.Collections.ArrayList
                foreach ($root in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run')) {
                    try {
                        $props = Get-ItemProperty $root -ErrorAction SilentlyContinue
                        if ($props) {
                            $props.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | ForEach-Object {
                                if ([string]$_.Value -like "*$InstallDir*" -or [string]$_.Value -match 'liuhao_desktop') {
                                    [void]$runHits.Add("$root\$($_.Name)=$($_.Value)")
                                }
                            }
                        }
                    } catch { }
                }
                $procsLeft = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                    Where-Object { $_.CommandLine -and ($_.CommandLine -like "*$InstallDir*") })
                $dataKept = Test-Path $DataDir
                $dataSize = 0
                if ($dataKept) {
                    try { $dataSize = (Get-ChildItem $DataDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum } catch { }
                }
                $ev = Write-Evidence 's10_uninstall.txt' ("卸载退出码={0}，耗时={1:N1}s`r`n安装目录存在={2}`r`n允许保留项={3}`r`n非允许残留={4}`r`n快捷方式残留={5}`r`nRun 键残留={6}`r`n相关进程残留={7}`r`n数据目录保留={8}（大小 {9} 字节）`r`n卸载日志={10}" -f `
                        $code, $sw.Elapsed.TotalSeconds, (Test-Path $InstallDir), ($allowed -join ','), ($leftovers -join ','),
                        ($shortcutsLeft -join '；'), ($runHits -join '；'), $procsLeft.Count, $dataKept, $dataSize, $uninsLog)
                $ok = ($code -eq 0 -and $leftovers.Count -eq 0 -and $shortcutsLeft.Count -eq 0 -and $runHits.Count -eq 0 -and $procsLeft.Count -eq 0 -and $dataKept)
                if ($ok) {
                    Add-Result 'S-10' 'PASS' $ev '静默卸载未挂住、退出码 0；程序目录仅留合规材料；快捷方式/Run 键/进程均清理；数据目录保留（交互式卸载未由脚本执行，需人工）'
                } else {
                    Add-Result 'S-10' 'FAIL' $ev ("卸载后仍有残留或退出码非 0（残留项：{0}）" -f (($leftovers + $shortcutsLeft + $runHits) -join '；'))
                }
            }
        } catch {
            $ev = Write-Evidence 's10_uninstall.txt' ("卸载启动失败：{0}" -f $_.Exception.Message)
            Add-Result 'S-10' 'FAIL' $ev ("卸载器无法启动：{0}" -f $_.Exception.Message)
        }
    }
} else {
    $reason = '未执行安装，未执行卸载'
    if ($SkipUninstall) { $reason = '指定 -SkipUninstall，跳过卸载检查' }
    $ev = Write-Evidence 's10_uninstall.txt' $reason
    Add-Result 'S-10' 'SKIP(未取证)' $ev $reason
}

# ---- S-11 登录转圈观测项
if ($serviceUsable -and ($script:LoginElapsed -ge 0 -or $script:ChatElapsed -ge 0)) {
    if ($script:S11Hit) {
        # 命中：先取证，不重试到通过。健康/就绪探针 + CPU 两次采样 + 6 分钟内是否自愈。
        $healthProbe = Invoke-SmokeHttp -Method GET -Uri $healthUrl -TimeoutSec 5
        $readyProbe = Invoke-SmokeHttp -Method GET -Uri $readyUrl -TimeoutSec 5
        $cpu1 = $null
        $cpu2 = $null
        $gwPid = if ($script:GatewayPidBefore -gt 0) { $script:GatewayPidBefore } else { Get-GatewayPid -LauncherPid $script:LauncherProc.Id }
        if ($gwPid -gt 0) {
            try { $cpu1 = (Get-Process -Id $gwPid -ErrorAction SilentlyContinue).CPU } catch { }
            Start-Sleep -Seconds 5
            try { $cpu2 = (Get-Process -Id $gwPid -ErrorAction SilentlyContinue).CPU } catch { }
        }
        $cpuDelta = ''
        if ($cpu1 -ne $null -and $cpu2 -ne $null) { $cpuDelta = ('{0:N2}' -f ([double]$cpu2 - [double]$cpu1)) }
        $healedAt = -1
        $watchSw = [System.Diagnostics.Stopwatch]::StartNew()
        while ($watchSw.Elapsed.TotalSeconds -lt 360) {
            $probe = Invoke-SmokeHttp -Method GET -Uri $healthUrl -TimeoutSec 5
            if ($probe.Ok -and $probe.StatusCode -eq 200) { $healedAt = $watchSw.Elapsed.TotalSeconds; break }
            Start-Sleep -Seconds 15
        }
        $logsCopy = Join-Path $script:EvidenceDir 's11_logs'
        New-Item -ItemType Directory -Path $logsCopy -Force | Out-Null
        $srcLogs = Join-Path $DataDir 'logs'
        if (Test-Path $srcLogs) {
            try { Copy-Item (Join-Path $srcLogs '*') $logsCopy -Recurse -Force -ErrorAction SilentlyContinue } catch { }
        }
        $shot = Save-Screenshot 's11_hit.png'
        $ev = Write-Evidence 's11_probe.json' ("被观测现象：{0}`r`n/health：HTTP {1}，耗时 {2:N1}s，超时={3}`r`n/ready：HTTP {4}，耗时 {5:N1}s，超时={6}`r`n网关 PID={7}`r`nCPU 采样1={8}，采样2={9}，增量={10}`r`n6 分钟内自愈={11}`r`n日志副本={12}`r`n截图={13}" -f `
                $script:S11Detail, $healthProbe.StatusCode, $healthProbe.Elapsed, $healthProbe.TimedOut,
                $readyProbe.StatusCode, $readyProbe.Elapsed, $readyProbe.TimedOut, $gwPid, $cpu1, $cpu2, $cpuDelta,
                $(if ($healedAt -ge 0) { "是（$([math]::Round($healedAt,1))s）" } else { '否（≥360s 仍不可用）' }), $logsCopy,
                $(if ($shot) { $shot } else { '<当前会话无桌面，未能截图>' }))
        Add-Result 'S-11' 'HIT(已知问题命中)' $ev ("观测项：{0}；未重试到通过，已留存探针/CPU/日志证据" -f $script:S11Detail)
    } else {
        $loginText = Format-Elapsed $script:LoginElapsed
        $chatText = Format-Elapsed $script:ChatElapsed
        $ev = Write-Evidence 's11_probe.json' ("登录耗时={0}`r`n对话耗时={1}`r`n判定阈值=60s`r`n结论=本次未复现（未出现 60 秒以上 pending）" -f $loginText, $chatText)
        Add-Result 'S-11' 'PASS(观测项未复现)' $ev ("观测项：登录 {0}、对话 {1}，本次未撞上 60 秒以上转圈" -f $loginText, $chatText)
    }
} else {
    $ev = Write-Evidence 's11_probe.json' '服务未启动，无法观测登录转圈。'
    Add-Result 'S-11' 'SKIP(未取证)' $ev '服务未启动，无法观测登录转圈'
}

# ---------------------------------------------------------------- 收尾
if ($script:LauncherProc -and -not $KeepRunning) {
    Stop-ProcessTree -ProcessId $script:LauncherProc.Id
    if ($script:GatewayPidBefore -gt 0) { Stop-ProcessTree -ProcessId $script:GatewayPidBefore }
}
Restore-GatewayEnv

# 汇总表
$summary = New-Object System.Collections.ArrayList
[void]$summary.Add('S-xx | 状态 | 证据文件路径 | 说明')
[void]$summary.Add(('-' * 100))
$failCount = 0
foreach ($r in $script:Results) {
    [void]$summary.Add(("{0} | {1} | {2} | {3}" -f $r.Item, $r.Status, $r.Evidence, $r.Note))
    if ($r.Status -eq 'FAIL') { $failCount++ }
}

$exitCode = 0
if ($failCount -gt 0) { $exitCode = 1 }
$verdict = '全部检查无 FAIL'
if ($failCount -gt 0) { $verdict = "存在 $failCount 项 FAIL" }
if (-not $installer.Path -and -not $SkipInstall) {
    $verdict = '未找到安装包：安装类检查全部 SKIP(未取证)；待 dist\installer 出包后重跑本脚本'
}

$json = [pscustomobject]@{
    timestamp = $stamp
    installer = $installer.Path
    installer_source = $installer.Source
    install_dir = $InstallDir
    data_dir = $DataDir
    port = $Port
    model_key_supplied = [bool]$script:ModelKey
    results = @($script:Results)
    fail_count = $failCount
    exit_code = $exitCode
    verdict = $verdict
}
[System.IO.File]::WriteAllText((Join-Path $script:EvidenceDir 'summary.json'), (Protect-Text ($json | ConvertTo-Json -Depth 8)), $script:Utf8NoBom)

[System.IO.File]::WriteAllText((Join-Path $script:EvidenceDir 'summary.txt'), (Protect-Text (($summary -join "`r`n") + "`r`n`r`n$verdict`r`n退出码：$exitCode`r`n")), $script:Utf8NoBom)

Write-Host ''
Write-Host ('=' * 100)
Write-Host '鎏灏安装冒烟 S-01～S-14 汇总'
Write-Host ('=' * 100)
foreach ($line in $summary) { Write-Host $line }
Write-Host ''
Write-Host ("证据目录：{0}" -f $script:EvidenceDir)
Write-Host ("结论：{0}；退出码：{1}" -f $verdict, $exitCode)
if (-not $installer.Path -and -not $SkipInstall) {
    Write-Host '提示：dist\installer 里还没有 .exe。安装包就绪后重跑本脚本即可自动取证。'
}

exit $exitCode
