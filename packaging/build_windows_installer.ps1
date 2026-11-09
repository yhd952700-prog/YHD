# LiuHao AI OS · 鎏灏智能中枢 —— Windows 安装包一键构建
#
# 一条命令完成：前端构建 → 后端 payload 组装（含离线 Python 运行时）
#              → 冒烟验证 → 升级发布包 ZIP+清单 → Inno Setup 编译
#              → 产物 SHA256/体积
#
# 用法（在仓库根执行）：
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows_installer.ps1
#   powershell ... -Version 0.1.1 -SkipSmoke
#
# 前置条件：
#   * Node.js + npm（构建 apps/console/console）
#   * .venv（Python 3.12，已装项目依赖）
#   * Inno Setup 6 的 ISCC.exe（可选；缺失时只出 payload 与安装脚本，不伪造 exe）
#
# 产物（全部在 dist\，已被 .gitignore 忽略，不提交）：
#   dist\payload\app\              安装内容
#   dist\payload\manifest.json     内容清单
#   dist\smoke-report.json         冒烟结果
#   dist\release\liuhao-<版本>.zip 升级用发布包
#   dist\release\LIUHAO_RELEASE.json  升级发布清单（规范见 docs/self-upgrade.md §2.2）
#   dist\installer\LiuHao-Setup-*.exe   安装包（仅在 ISCC 可用时）
#   dist\installer\SHA256SUMS.txt  校验和

[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$OutRoot = "",
    # 指定构建用 Python。默认为仓库 .venv；发布应指向一个严格按
    # requirements.txt 装好的独立 venv，避免随包版本与受审锁漂移。
    [string]$PythonExe = "",
    # 升级信任配置（开箱自动升级用）：客户端只需「公钥 + 清单地址」两项即可自举，
    # 不再要求每次发布下发 LIUHAO_UPGRADE_MANIFEST_SHA256。取值由发布线给定时
    # 通过这两个参数注入安装器，写入 HKCU\Environment；不传则整段预处理剔除，
    # 安装后 source_configured=false、检查更新如实返回 409（绝不写占位地址）。
    [string]$UpgradeManifestUrl = "",
    [string]$UpgradePublicKey = "",
    # 备份根：由安装器显式下发（写 LIUHAO_UPGRADE_BACKUP_ROOT）。不传时安装器
    # 按「数据目录所在盘 + \LiuHaoBackups」推导（本项目即 D:\LiuHaoBackups）；
    # 推导结果落系统盘时安装器直接拒绝，不会静默装到 C 盘、也不退回数据目录。
    [string]$UpgradeBackupRoot = "",
    [switch]$SkipFrontend,
    [switch]$SkipSmoke,
    [switch]$SkipInstaller,
    [switch]$SkipRelease,
    [switch]$StrictReleaseDevPaths,
    # 跳过 exe 签名。默认不跳：没有证书时脚本会明确打印「本次未签名」，而不是静默放过。
    [switch]$SkipSigning
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutRoot) { $OutRoot = Join-Path $Root "dist" }

$PayloadRoot = Join-Path $OutRoot "payload"
$PayloadApp = Join-Path $PayloadRoot "app"
$InstallerOut = Join-Path $OutRoot "installer"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-Python {
    if ($PythonExe) {
        if (-not (Test-Path $PythonExe)) {
            throw "找不到指定的构建 Python：$PythonExe"
        }
        return (Resolve-Path $PythonExe).Path
    }
    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "找不到 Python：请先创建 .venv 并安装依赖。"
}

function Resolve-Version {
    if ($Version) { return $Version }
    # 版本号唯一可编辑真源是 src/version.py 的 APP_VERSION；pyproject.toml
    # 只是构建元数据的镜像。此前这里与 stage_payload.py 各兜底一个 "0.1.1"
    # 字面量，改版本时会悄悄漂移，已全部删除：读不到真源就明确失败。
    $versionPy = Join-Path $Root "src\version.py"
    if (-not (Test-Path $versionPy)) {
        throw "找不到版本号唯一真源：$versionPy"
    }
    $match = Select-String -Path $versionPy -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' |
        Select-Object -First 1
    if (-not $match) {
        throw "无法从 $versionPy 解析 APP_VERSION（版本号唯一真源缺失）"
    }
    return $match.Matches[0].Groups[1].Value
}

$Python = Resolve-Python
$Version = Resolve-Version

# 升级信任配置只在发布线给出真实取值时注入。URL 必须是 https（客户端拒绝明文
# 远端源）；公钥必须是单行 base64（换行/引号会破坏安装脚本）。两项都给才会让
# 安装后的客户端自举自动升级；都不给则保持「未配置更新源」的真实状态。
if ($UpgradeManifestUrl -and $UpgradeManifestUrl -notmatch '^https://') {
    throw "UpgradeManifestUrl 必须是 https 地址（当前：$UpgradeManifestUrl）"
}
if ($UpgradePublicKey -and ($UpgradePublicKey -match '\s' -or $UpgradePublicKey.Contains('"') -or $UpgradePublicKey.Contains("'")) ) {
    throw "UpgradePublicKey 必须是单行 base64，不得含空白、单引号或双引号"
}
if ($UpgradeManifestUrl -and -not $UpgradePublicKey) {
    Write-Host "  注意：只注入了清单地址、没有公钥——客户端会拒绝未签名清单（SignatureError）。" -ForegroundColor Yellow
}
if ($UpgradePublicKey -and -not $UpgradeManifestUrl) {
    Write-Host "  注意：只注入了公钥、没有清单地址——source_configured 仍为 false，不会自动检查。" -ForegroundColor Yellow
}
if ($UpgradeBackupRoot) {
    if ($UpgradeBackupRoot -notmatch '^[A-Za-z]:\\') {
        throw "UpgradeBackupRoot 必须是绝对路径（当前：$UpgradeBackupRoot）"
    }
    if ($UpgradeBackupRoot.Substring(0, 1).ToUpper() -eq 'C') {
        Write-Host "  注意：备份根被显式指定到系统盘 $UpgradeBackupRoot（默认为数据目录所在盘）。" -ForegroundColor Yellow
    }
}

Write-Host "LiuHao AI OS · 鎏灏智能中枢 安装包构建  version=$Version"
Write-Host "  仓库  $Root"
Write-Host "  输出  $OutRoot"
Write-Host "  Python $Python"
if ($UpgradeManifestUrl) { Write-Host "  更新源  $UpgradeManifestUrl（公钥随装写入 HKCU\Environment）" }
else { Write-Host "  更新源  未注入（安装后 source_configured=false，检查更新如实返回 409）" }
if ($UpgradeBackupRoot) { Write-Host "  备份根  $UpgradeBackupRoot（随装写入 LIUHAO_UPGRADE_BACKUP_ROOT）" }
else { Write-Host "  备份根  未指定（安装器按数据目录所在盘推导，本项目为 D:\LiuHaoBackups）" }

# ---------------------------------------------------------------- 0. 环境闸
# 构建机与开发机同机时，残留的 LIUHAO_* / AUDIT_DB_* 会让注册写进仓库、启动
# 读数据目录，冒烟表现为莫名的 403。开跑前发现即中止，别靠人记得。
Write-Step "前置环境检查（LIUHAO_* / AUDIT_DB_*）"
$guardText = (& $Python (Join-Path $PSScriptRoot "env_guard.py") --repo $Root 2>&1 | Out-String)
Write-Host $guardText.TrimEnd()
if ($LASTEXITCODE -ne 0) {
    throw "构建中止：环境里残留指向源码仓库/开发临时目录的配置变量（见上）。清除后重试。"
}

# ---------------------------------------------------------------- 1. 前端
if ($SkipFrontend) {
    Write-Step "跳过前端构建（-SkipFrontend）"
    if (-not (Test-Path (Join-Path $Root "apps\console\console\dist\index.html"))) {
        throw "跳过前端构建，但 apps\console\console\dist\index.html 不存在。"
    }
} else {
    Write-Step "构建驾驶舱（npm run build）"
    $consoleDir = Join-Path $Root "apps\console\console"
    # npm/vite 会把体积告警写到 stderr；本脚本 EAP=Stop 时会被当成终止错误。
    # 临时放行 stderr，是否成功仍以 $LASTEXITCODE 为准。
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if (-not (Test-Path (Join-Path $consoleDir "node_modules"))) {
            Write-Host "  首次构建：npm install（可能需要几分钟）"
            Push-Location $consoleDir
            try { & npm.cmd install } finally { Pop-Location }
            if ($LASTEXITCODE -ne 0) { throw "npm install 失败（exit $LASTEXITCODE）" }
        }
        Push-Location $consoleDir
        try { & npm.cmd run build } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "npm run build 失败（exit $LASTEXITCODE）" }
    } finally {
        $ErrorActionPreference = $previousEap
    }
    Write-Step "写入控制台构建来源记录（源码指纹 + 产物指纹）"
    & $Python (Join-Path $Root "packaging\build_console.py") `
        --source $consoleDir --dest (Join-Path $consoleDir "dist") `
        --version $Version --skip-build
    if ($LASTEXITCODE -ne 0) { throw "控制台构建来源记录写入失败（exit $LASTEXITCODE）" }
}

# ------------------------------------------------------- 2. payload 组装
Write-Step "组装 payload（源码 + 配置 + 驾驶舱 + 离线运行时）"
& $Python (Join-Path $Root "packaging\stage_payload.py") --out $PayloadRoot --version $Version
if ($LASTEXITCODE -ne 0) { throw "payload 组装失败（exit $LASTEXITCODE）" }

Write-Step "凭据泄漏检查"
# 注意：不能一刀切拦 *.pem —— certifi 等运行依赖会带 CA 证书包。
# 这里只拦「用户凭据 / 私钥 / 私钥锚 / 容器环境文件」。
$forbidden = Get-ChildItem $PayloadApp -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -in @("auth_secrets.json", "human_identities.json", "docker.env",
                      "anchor_key.pem", ".env") -or
        $_.Name -like "*.key"
    }
if ($forbidden) {
    $forbidden | ForEach-Object { Write-Host "  !! $($_.FullName)" -ForegroundColor Red }
    throw "payload 中出现了凭据/私钥文件，构建中止。"
}
Write-Host "  未发现凭据/私钥文件（config 模板已剔除 auth_secrets / identities / anchor_key）"

# ------------------------------------------------------------ 3. 冒烟
$smokeCode = 0
if ($SkipSmoke) {
    Write-Step "跳过冒烟（-SkipSmoke）—— 本次构建**未验证**运行"
} else {
    Write-Step "冒烟验证（启动 → 首页 → 注册 → 登录 → 发消息）"
    & $Python (Join-Path $Root "packaging\smoke_payload.py") --payload $PayloadApp
    $smokeCode = $LASTEXITCODE
    if ($smokeCode -ne 0) {
        Write-Host "  冒烟未通过（exit $smokeCode）。这是真实失败，不继续假装成功。" -ForegroundColor Yellow
    }
}

# -------------------------- 4. 清理运行残留 + 交付树硬门禁
# payload 冒烟与升级器自检会在 app 目录留下 __pycache__、upgrade-state、data
# 与 .db；Inno Setup 会原样复制整个目录，这些绝不能装进用户机器。
# 顺序很关键：必须在**生成升级包之前**清理，否则残留会同时进入 ZIP。
Write-Step "清理 payload 运行残留（__pycache__ / upgrade-state / data / *.db / *.log）"
$purged = 0
Get-ChildItem $PayloadApp -Recurse -Directory -Filter "__pycache__" -Force -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force; $purged++ }
foreach ($junk in @("upgrade-state", "upgrade-backups", "data", "logs")) {
    $junkPath = Join-Path $PayloadApp $junk
    if (Test-Path $junkPath) { Remove-Item $junkPath -Recurse -Force; $purged++ }
}
Get-ChildItem $PayloadApp -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @(".db", ".db-wal", ".db-shm", ".sqlite3",
                                      ".sqlite3-wal", ".sqlite3-shm", ".log", ".pyc", ".pyo") } |
    ForEach-Object { Remove-Item $_.FullName -Force; $purged++ }
Write-Host "  已清理 $purged 项"

Write-Step "交付树硬门禁（残留 / 凭据 / 明文密钥）"
& $Python (Join-Path $Root "packaging\verify_payload_clean.py") --app-root $PayloadApp --label "payload"
if ($LASTEXITCODE -ne 0) {
    throw "交付树卫生检查未通过，构建中止（不生成升级包与安装器）。"
}

# ------------------------------------------- 5. 升级发布包与发布清单
$releaseDir = Join-Path $OutRoot "release"
$releaseZip = $null
$releaseManifest = $null

# 发布说明要写清签名状态，措辞必须与本轮是否真有证书一致，不能一律写「未签名」。
$psExe = Join-Path $PSHOME "powershell.exe"
$signerScript = Join-Path $PSScriptRoot "sign_installer.ps1"
$signAvailable = $false
if (-not $SkipSigning) {
    & $psExe -NoProfile -ExecutionPolicy Bypass -File $signerScript -Check -Quiet | Out-Null
    $signAvailable = ($LASTEXITCODE -eq 0)
}
if ($SkipSigning) {
    $signingNote = "本次构建跳过签名（-SkipSigning），安装器未做代码签名。"
} elseif ($signAvailable) {
    $signingNote = "安装器使用项目自签名证书（非公开权威 CA）；未安装信任脚本前 Windows 仍可能提示未知发布者。"
} else {
    $signingNote = "安装器未做代码签名（未找到签名证书）。"
}

if ($SkipRelease) {
    Write-Step "跳过升级发布包（-SkipRelease）"
} else {
    Write-Step "生成升级发布包与清单（规范：docs/self-upgrade.md §2.2）"
    $releaseTreeNote = "安装树整包（含根级模块与 config/，不含 runtime/ 与数据目录）"
    if (-not $SkipSmoke -and $smokeCode -eq 0) {
        $releaseNotes = "LiuHao AI OS · 鎏灏智能中枢 $Version：$releaseTreeNote；不含模型权重、不含任何密钥。$signingNote 本次构建 payload 冒烟通过。"
    } elseif ($SkipSmoke) {
        $releaseNotes = "LiuHao AI OS · 鎏灏智能中枢 $Version：$releaseTreeNote；不含模型权重、不含任何密钥。$signingNote 本次构建跳过冒烟（未验证运行）。"
    } else {
        $releaseNotes = "LiuHao AI OS · 鎏灏智能中枢 $Version：$releaseTreeNote；不含模型权重、不含任何密钥。$signingNote 本次构建冒烟未通过，产物未验证。"
    }
    $releaseArgs = @((Join-Path $Root "packaging\make_release.py"), "--app-root", $PayloadApp, "--out", $releaseDir, "--notes", $releaseNotes)
    if ($StrictReleaseDevPaths) { $releaseArgs += "--strict-dev-paths" }
    if ($Version) { $releaseArgs += @("--version", $Version) }
    & $Python @releaseArgs
    if ($LASTEXITCODE -ne 0) { throw "发布包生成失败（exit $LASTEXITCODE）" }
    $releaseZip = Get-ChildItem (Join-Path $releaseDir "liuhao-*.zip") |
        Sort-Object LastWriteTime | Select-Object -Last 1
    $releaseManifest = Join-Path $releaseDir "LIUHAO_RELEASE.json"
}

# --------------------------------------------------------- 6. 安装器编译
if ($SkipInstaller) {
    Write-Step "跳过安装器编译（-SkipInstaller）"
    if ($releaseZip) {
        $releaseHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $releaseZip.FullName).Hash
        Write-Host "发布包  $($releaseZip.FullName)"
        Write-Host "  体积    $([math]::Round($releaseZip.Length / 1MB, 1)) MB"
        Write-Host "  SHA256  $releaseHash"
        Write-Host "  清单    $releaseManifest"
    }
    exit 0
}

Write-Step "准备并编译安装器"

# 实测 payload 内最深一条相对路径（文件或目录）。安装器在复制文件**之前**用它
# 预检 MAX_PATH：目标目录 + 这条相对路径 > 259 时 Windows 会中途拒绝创建，
# 必须提前给出中文可行动提示，而不是装到一半失败。清理 __pycache__ 之后才算，
# 否则会按开发残留的长度误判。
$maxRel = 1
$payloadPrefix = $PayloadApp.TrimEnd('\') + '\'
Get-ChildItem $PayloadApp -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.FullName.StartsWith($payloadPrefix)) {
        $rel = $_.FullName.Substring($payloadPrefix.Length)
        if ($rel.Length -gt $maxRel) { $maxRel = $rel.Length }
    }
}
Write-Host "  payload 最深相对路径  $maxRel 字符"
if ($maxRel -le 0) { throw "无法计算 payload 最深相对路径（$PayloadApp 为空？）" }

$buildDir = Join-Path $InstallerOut "build"
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
Copy-Item (Join-Path $Root "installer\*") $buildDir -Recurse -Force

$iconSource = Join-Path $PayloadApp "assets\liuhao.ico"
if (Test-Path $iconSource) {
    New-Item -ItemType Directory -Path (Join-Path $buildDir "assets") -Force | Out-Null
    Copy-Item $iconSource (Join-Path $buildDir "assets\liuhao.ico") -Force
}

# 生成编译用的副本：只替换版本号与 payload 绝对路径，其余原样。
$iss = Join-Path $buildDir "liuhao.iss"
$content = Get-Content -Raw -Encoding UTF8 -LiteralPath $iss
# 用正则替换，而不是匹配固定版本字面量：.iss 的默认版本一旦升版，
# 按旧字面量替换会静默不命中，编出旧版本号的包。
$content = [regex]::Replace($content, '#define AppVersion "[^"]*"', '#define AppVersion "' + $Version + '"')
if ($content -notmatch [regex]::Escape('#define AppVersion "' + $Version + '"')) {
    throw "安装脚本版本号替换失败：liuhao.iss 里没有 #define AppVersion 声明。"
}
$content = $content.Replace('#define PayloadDir "..\dist\payload\app"', '#define PayloadDir "' + $PayloadApp + '"')
# 注入本次 payload 的实测深度；连同默认值一起替换，避免手工编译和构建产物用两套数。
$content = $content.Replace('#define MaxPayloadPathLen "160"', '#define MaxPayloadPathLen "' + $maxRel + '"')
if ($content -notmatch [regex]::Escape('#define MaxPayloadPathLen "' + $maxRel + '"')) {
    throw "安装脚本 MAX_PATH 预检参数注入失败：liuhao.iss 里没有 #define MaxPayloadPathLen 声明。"
}
# 编译副本在 dist\installer\build 下，相对的 OutputDir 会解析错位，改成绝对路径。
# liuhao.iss 的 OutputDir 走 #define SetupOutputDir（默认 ..\dist\installer），
# 既保证这里注入绝对路径，也允许沙箱验证用 /DSetupOutputDir=<临时目录> 编译，
# 不会在 dist\installer 里留下任何验证产物。
$content = $content.Replace('#define SetupOutputDir "..\dist\installer"', '#define SetupOutputDir "' + $InstallerOut + '"')
if ($content -notmatch [regex]::Escape('#define SetupOutputDir "' + $InstallerOut + '"')) {
    throw "安装脚本输出目录注入失败：liuhao.iss 里没有 #define SetupOutputDir 声明。"
}
# Inno Setup 6 是 Unicode 版；显式写 UTF-8 BOM，保证中文界面文案不乱码。
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($iss, $content, $utf8Bom)
$before = Join-Path $buildDir "assets\BEFORE-INSTALL.txt"
[System.IO.File]::WriteAllText($before, (Get-Content -Raw -Encoding UTF8 -LiteralPath $before), $utf8Bom)

Write-Step "品牌字面硬门禁（编译前）"
& $Python (Join-Path $Root "packaging\brand_guard.py") --payload $PayloadApp --iss $iss
if ($LASTEXITCODE -ne 0) {
    throw "品牌字面硬门禁未通过：旧主名 / 错字 / 死域名 / 旧版本号，停止编译安装器。"
}

$iscc = $null
$candidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe"
)
foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path $candidate)) { $iscc = $candidate; break }
}
if (-not $iscc) {
    $cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}

if (-not $iscc) {
    $notice = @"
未在本机构建 .exe —— 缺少 Inno Setup 编译器（ISCC.exe）

已完成：
  payload      $PayloadApp
  内容清单     $(Join-Path $PayloadRoot 'manifest.json')
  冒烟结果     $(Join-Path $OutRoot 'smoke-report.json')
  升级发布包   $(if ($releaseZip) { $releaseZip.FullName } else { '（-SkipRelease 未生成）' })
  发布清单     $(if ($releaseManifest -and (Test-Path $releaseManifest)) { $releaseManifest } else { '（-SkipRelease 未生成）' })

未完成：
  LiuHao-Setup-*.exe（未生成，也未伪造）

补齐方式（二选一）：
  1) 安装 Inno Setup 6：https://jrsoftware.org/isdl.php
     安装后重跑本脚本即可。
  2) 已有 ISCC 时手动编译：
     iscc "$iss"

说明：
  * 安装包不含模型权重，也不含任何密钥。
  * 安装器未做代码签名，Windows 会提示「未知发布者」。
"@
    $noticePath = Join-Path $InstallerOut "NOT-COMPILED.txt"
    New-Item -ItemType Directory -Path $InstallerOut -Force | Out-Null
    Set-Content -Path $noticePath -Value $notice -Encoding UTF8
    Write-Host $notice -ForegroundColor Yellow
    exit 2
}

$isccArgs = @($iss)
if ($UpgradeManifestUrl) { $isccArgs += "/DUpgradeManifestUrl=$UpgradeManifestUrl" }
if ($UpgradePublicKey) { $isccArgs += "/DUpgradePublicKey=$UpgradePublicKey" }
if ($UpgradeBackupRoot) { $isccArgs += "/DUpgradeBackupRoot=$UpgradeBackupRoot" }
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup 编译失败（exit $LASTEXITCODE），详见 $iscc 输出。"
}

# ------------------------------------------------------------ 5. 产物信息
Write-Step "产物信息"
$setup = Get-ChildItem (Join-Path $InstallerOut "LiuHao-Setup-*.exe") |
    Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $setup) { throw "编译成功但找不到 LiuHao-Setup-*.exe" }

# ------------------------------------------------------- 5.1 代码签名（自签名）
# 必须在计算 SHA256 之前签名：签名会改写 PE 校验和与附加数据，先算哈希再签名
# 会让清单里的哈希与实际文件对不上。没有证书时不静默跳过，明确打印后继续。
$signCode = 3
$signState = "本次未签名"
if ($SkipSigning) {
    Write-Step "跳过代码签名（-SkipSigning）—— 本次未签名"
    Write-Host "  本次未签名（-SkipSigning）。" -ForegroundColor Yellow
} else {
    Write-Step "代码签名（Set-AuthenticodeSignature，自签名证书）"
    & $psExe -NoProfile -ExecutionPolicy Bypass -File $signerScript -InstallerPath $setup.FullName
    $signCode = $LASTEXITCODE
    switch ($signCode) {
        0 { $signState = "已签名，Get-AuthenticodeSignature Status=Valid" }
        2 { $signState = "已签名，但本机尚未信任该自签名证书（Status 非 Valid）" }
        3 { $signState = "本次未签名（未找到可用证书，详见上方提示）" }
        default { throw "安装包签名失败（exit $signCode）。" }
    }
}

# 把信任脚本与公钥证书随安装包一起放到产物目录，方便交付后一步信任。
foreach ($name in @("LiuHao-CodeSigning.cer", "trust-liuhao-signing.ps1", "信任签名证书.cmd")) {
    $source = Join-Path $Root "signing\$name"
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $InstallerOut -Force
    }
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup.FullName).Hash
$sizeMb = [math]::Round($setup.Length / 1MB, 1)
$payloadMb = [math]::Round((Get-ChildItem $PayloadApp -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
$sums = Join-Path $InstallerOut "SHA256SUMS.txt"
# 安装包与升级包必须同目录交付：把本次 release zip 复制进 installer，
# 并在同一份 SHA256SUMS.txt 里同时登记 exe 与 zip。两个产物出自同一次
# 构建，校验和只有一个来源，避免再出现「同一版本两个大小」的文档漂移。
$sumsLines = @("$hash  $($setup.Name)")
if ($releaseZip) {
    $installedZip = Join-Path $InstallerOut $releaseZip.Name
    Copy-Item -LiteralPath $releaseZip.FullName -Destination $installedZip -Force
    $releaseHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedZip).Hash
    $sumsLines += "$releaseHash  $($releaseZip.Name)"
}
$sumsLines | Set-Content -Path $sums -Encoding ASCII

Write-Host ""
Write-Host "安装包  $($setup.FullName)"
Write-Host "  版本    $Version"
Write-Host "  体积    $sizeMb MB（payload $payloadMb MB）"
Write-Host "  SHA256  $hash"
Write-Host "  校验    $sums"
Write-Host "  签名    $signState"
if ($releaseZip) {
    $releaseHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $releaseZip.FullName).Hash
    Write-Host ""
    Write-Host "升级包  $($releaseZip.FullName)"
    Write-Host "  体积    $([math]::Round($releaseZip.Length / 1MB, 1)) MB"
    Write-Host "  SHA256  $releaseHash（整包）"
    Write-Host "  清单    $releaseManifest"
}
Write-Host ""
Write-Host "提醒：安装包不含模型权重。" -ForegroundColor Yellow
if ($signCode -eq 0 -or $signCode -eq 2) {
    Write-Host "签名状态：$signState" -ForegroundColor Green
    if ($signCode -eq 2) {
        Write-Host "提醒：本机尚未信任该证书；运行 signing\信任签名证书.cmd 后再复查签名状态。" -ForegroundColor Yellow
    }
} else {
    Write-Host "签名状态：$signState" -ForegroundColor Yellow
    Write-Host "提醒：生成证书后重跑本脚本即可自动签名：powershell -ExecutionPolicy Bypass -File packaging\new_signing_cert.ps1" -ForegroundColor Yellow
}
Write-Host "提醒：前端产物已参与本次同源构建，指纹见 payload\console\console-contract.json。" -ForegroundColor Yellow

if ($smokeCode -ne 0) {
    Write-Host "冒烟未通过：产物已生成，但运行链路**未验证通过**。" -ForegroundColor Yellow
    exit 1
}
exit 0
