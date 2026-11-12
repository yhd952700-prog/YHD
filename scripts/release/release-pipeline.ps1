<#
.SYNOPSIS
  发布流水线唯一入口：构建 → 签名 → SHA256 → 清单 → 自检（一条命令）。

.DESCRIPTION
  收敛之前手工串联的发布步骤，顺序固定，任一步失败即停：

      package-frozen.ps1（前置门禁/环境闸/前端/组 payload/冒烟/ISCC 编译/签名/合规）
        → 发布自检（payload 计数、dist-info、__pycache__、签名、三方哈希、快捷方式参数）
        → 退出码 0/1

  自检项全部打印**真实读数**，不打印"OK"了事：
    1. payload 文件总数与总字节
    2. 每个 <pkg>.dist-info 都有 METADATA（缺失数必须为 0）
    3. payload 内 __pycache__ 目录数为 0
    4. 安装包签名 Status=Valid（并打印 Subject）
    5. exe / zip 的 SHA256 与字节数，在「实际文件」「SHA256SUMS.txt」「发布清单」三处一致
       （zip 额外与公网发布页清单比对，可用 -CheckPublished 开启）
    6. 安装器快捷方式参数不含 --open-browser

  与 package-frozen.ps1 的差异：那个脚本负责"怎么建"，本脚本负责"建完怎么验"，
  并作为唯一对外命令。构建步骤直接复用 package-frozen.ps1，不重复实现。

.PARAMETER DryRun
  只做只读自检：不构建、不签名、不写 SHA256SUMS、不覆盖 dist\installer 里现有文件。
  任一项自检失败则退出码 1。

.PARAMETER CheckPublished
  额外抓取公网发布清单（默认 updates/LIUHAO_RELEASE.json），与本地 zip 对账。
  只读 HTTP GET，不写线上。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\release-pipeline.ps1 -DryRun -CheckPublished

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\release-pipeline.ps1 -Version 0.1.1
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Version = "",
    [string]$OutRoot = "",
    [string]$PythonExe = "",
    [string]$ReleaseGateDir = "",
    [string]$IsccPath = "",
    [switch]$DryRun,
    [switch]$CheckPublished,
    [string]$PublishedManifestUrl = "https://yhd952700-prog.github.io/liuhao-release/updates/LIUHAO_RELEASE.json"
)

$ErrorActionPreference = "Stop"
$script:DryRun = [bool]$DryRun
$script:Checks = New-Object System.Collections.Generic.List[object]

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Add-Check {
    param([string]$Name, [string]$Status, [string]$Detail)
    $script:Checks.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail })
    $color = switch ($Status) { "PASS" { "Green" } "WARN" { "Yellow" } "FAIL" { "Red" } default { "Gray" } }
    Write-Host ("  [{0}] {1}：{2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Get-Hash([string]$Path) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToUpperInvariant()
}

function Get-Size([string]$Path) {
    (Get-Item -LiteralPath $Path).Length
}

if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
if (-not $OutRoot) {
    $OutRoot = Join-Path $RepoRoot "dist"
}
if (-not $PythonExe) {
    $PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
}
if (-not $Version) {
    $versionPy = Join-Path $RepoRoot "src\version.py"
    if (-not (Test-Path $versionPy)) { throw "找不到版本号真源：$versionPy" }
    $m = Select-String -Path $versionPy -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $m) { throw "无法从 $versionPy 解析 APP_VERSION" }
    $Version = $m.Matches[0].Groups[1].Value
}

$mode = if ($script:DryRun) { "DRY-RUN（只读自检，不产出、不覆盖、不发布）" } else { "FULL（构建 + 签名 + 自检）" }
Write-Host "LiuHao 发布流水线  mode=$mode"
Write-Host "  仓库    $RepoRoot"
Write-Host "  输出    $OutRoot"
Write-Host "  版本    $Version"
Write-Host ("  时间    " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"))

# ---------------------------------------------------------------- 1. 构建
if (-not $script:DryRun) {
    Write-Step "构建 + 签名（package-frozen.ps1）"
    $frozen = Join-Path $RepoRoot "scripts\release\package-frozen.ps1"
    if (-not (Test-Path $frozen)) { throw "找不到 $frozen" }
    $buildArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $frozen,
                   "-RepoRoot", $RepoRoot, "-Version", $Version, "-OutRoot", $OutRoot)
    if ($PythonExe) { $buildArgs += @("-PythonExe", $PythonExe) }
    if ($ReleaseGateDir) { $buildArgs += @("-ReleaseGateDir", $ReleaseGateDir) }
    if ($IsccPath) { $buildArgs += @("-IsccPath", $IsccPath) }
    $eap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & powershell.exe @buildArgs; $buildExit = $LASTEXITCODE } finally { $ErrorActionPreference = $eap }
    if ($buildExit -ne 0) {
        Add-Check "构建与签名" "FAIL" "package-frozen.ps1 退出码 $buildExit"
        Write-Host ""
        Write-Host "发布流水线失败：构建未通过，自检未执行。" -ForegroundColor Red
        exit 1
    }
    Add-Check "构建与签名" "PASS" "package-frozen.ps1 退出码 0"
} else {
    Write-Step "前置门禁（DryRun：package-frozen.ps1 -DryRun，只读）"
    $frozen = Join-Path $RepoRoot "scripts\release\package-frozen.ps1"
    if (-not (Test-Path $frozen)) { throw "找不到 $frozen" }
    $gateArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $frozen,
                  "-RepoRoot", $RepoRoot, "-Version", $Version, "-OutRoot", $OutRoot, "-DryRun")
    if ($PythonExe) { $gateArgs += @("-PythonExe", $PythonExe) }
    if ($ReleaseGateDir) { $gateArgs += @("-ReleaseGateDir", $ReleaseGateDir) }
    if ($IsccPath) { $gateArgs += @("-IsccPath", $IsccPath) }
    $eap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & powershell.exe @gateArgs; $gateExit = $LASTEXITCODE } finally { $ErrorActionPreference = $eap }
    if ($gateExit -ne 0) {
        Add-Check "前置门禁" "FAIL" "package-frozen.ps1 -DryRun 退出码 $gateExit"
    } else {
        Add-Check "前置门禁" "PASS" "package-frozen.ps1 -DryRun 退出码 0"
    }
    Write-Host "  未构建、未签名、未编译；不对 dist 写入任何文件。"
}

# ------------------------------------------------------------ 2. 自检：产物体
Write-Step "自检 1/6：payload 文件数与体积"
$payloadApp = Join-Path $OutRoot "payload\app"
if (-not (Test-Path $payloadApp)) {
    Add-Check "payload 存在" "FAIL" "缺少 $payloadApp"
} else {
    $allFiles = @(Get-ChildItem -LiteralPath $payloadApp -Recurse -File -ErrorAction SilentlyContinue)
    $totalBytes = ($allFiles | Measure-Object -Property Length -Sum).Sum
    if (-not $totalBytes) { $totalBytes = 0 }
    $detail = ("文件 {0} 个，合计 {1} 字节（{2} MB）" -f $allFiles.Count, $totalBytes, [math]::Round($totalBytes / 1MB, 1))
    if ($allFiles.Count -gt 0) { Add-Check "payload 文件数" "PASS" $detail } else { Add-Check "payload 文件数" "FAIL" $detail }
}

Write-Step "自检 2/6：dist-info 的 METADATA 完整性"
$sitePackages = Join-Path $payloadApp "runtime\Lib\site-packages"
if (-not (Test-Path $sitePackages)) {
    Add-Check "site-packages 存在" "FAIL" "缺少 $sitePackages"
} else {
    $distInfos = @(Get-ChildItem -LiteralPath $sitePackages -Directory -Filter "*.dist-info" -ErrorAction SilentlyContinue)
    $missing = @()
    foreach ($d in $distInfos) {
        if (-not (Test-Path (Join-Path $d.FullName "METADATA"))) { $missing += $d.Name }
    }
    $detail = ("dist-info {0} 个，缺 METADATA {1} 个" -f $distInfos.Count, $missing.Count)
    if ($missing.Count -gt 0) { $detail += ("；缺失：" + ($missing -join "、")) }
    $status = if ($distInfos.Count -gt 0 -and $missing.Count -eq 0) { "PASS" } else { "FAIL" }
    Add-Check "dist-info METADATA" $status $detail
}

Write-Step "自检 3/6：__pycache__ 残留"
if (-not (Test-Path $payloadApp)) {
    Add-Check "__pycache__" "FAIL" "payload 不存在，无法统计"
} else {
    $pyc = @(Get-ChildItem -LiteralPath $payloadApp -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue)
    $detail = ("__pycache__ 目录 {0} 个" -f $pyc.Count)
    if ($pyc.Count -gt 0) { $detail += ("；前 5 个：" + (($pyc | Select-Object -First 5 | ForEach-Object { $_.FullName.Substring($payloadApp.Length + 1) }) -join "、")) }
    $status = if ($pyc.Count -eq 0) { "PASS" } else { "FAIL" }
    Add-Check "__pycache__ 数量" $status $detail
}

# ------------------------------------------------- 3. 自检：定位 exe 与 zip
Write-Step "自检 4/6：安装包签名"
$installerDir = Join-Path $OutRoot "installer"
$exePath = Join-Path $installerDir ("Jarvis-Setup-$Version.exe")
if (-not (Test-Path $exePath)) {
    $candidates = @(Get-ChildItem -LiteralPath $installerDir -Filter "Jarvis-Setup-*.exe" -ErrorAction SilentlyContinue)
    if ($candidates.Count -eq 1) { $exePath = $candidates[0].FullName }
}
if (-not (Test-Path $exePath)) {
    Add-Check "安装包存在" "FAIL" "在 $installerDir 下找不到 Jarvis-Setup-*.exe"
} else {
    $sig = Get-AuthenticodeSignature -LiteralPath $exePath
    $subject = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { "（无签名者证书）" }
    $thumb = if ($sig.SignerCertificate) { $sig.SignerCertificate.Thumbprint } else { "-" }
    $detail = ("Status={0}；Subject={1}；证书指纹={2}" -f $sig.Status, $subject, $thumb)
    if ($sig.Status -eq "Valid") { Add-Check "安装包签名" "PASS" $detail } else { Add-Check "安装包签名" "FAIL" $detail }
}

Write-Step "自检 5/6：exe / zip 的三方哈希一致"
$zipCandidates = @(
    (Join-Path $installerDir ("jarvis-$Version-upgrade.zip")),
    (Join-Path $OutRoot ("release\jarvis-$Version-upgrade.zip")),
    (Join-Path $OutRoot ("release\liuhao-$Version.zip")),
    (Join-Path $installerDir ("liuhao-$Version.zip"))
) | Where-Object { Test-Path $_ }
$zipPath = $zipCandidates | Select-Object -First 1

$sumsPath = Join-Path $installerDir "SHA256SUMS.txt"
$sums = @{}
if (Test-Path $sumsPath) {
    foreach ($line in Get-Content -LiteralPath $sumsPath -Encoding ASCII) {
        $mm = [regex]::Match($line, '^([0-9A-Fa-f]{64})\s+(.+)$')
        if ($mm.Success) { $sums[$mm.Groups[2].Value.Trim()] = $mm.Groups[1].Value.ToUpperInvariant() }
    }
}

$manifestPath = Join-Path $OutRoot "release\LIUHAO_RELEASE.json"
$manifest = $null
if (Test-Path $manifestPath) {
    try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch {
        Add-Check "发布清单可解析" "FAIL" ("$manifestPath 解析失败：" + $_.Exception.Message)
    }
} else {
    Add-Check "发布清单存在" "FAIL" "缺少 $manifestPath"
}

if (Test-Path $exePath) {
    $exeHash = Get-Hash $exePath
    $exeSize = Get-Size $exePath
    $exeName = Split-Path -Leaf $exePath
    $sumHash = $sums[$exeName]
    $sumDetail = if ($sumHash) { "SHA256SUMS=$sumHash" } else { "SHA256SUMS 无该条目" }
    $same = ($sumHash -ne $null) -and ($sumHash -eq $exeHash)
    Add-Check "exe 哈希" $(if ($same) { "PASS" } else { "FAIL" }) `
        ("实际 {0} 字节 / {1}；{2}" -f $exeSize, $exeHash, $sumDetail)

    $freeze = Join-Path $OutRoot "FREEZE-RECORD.json"
    if (Test-Path $freeze) {
        $fr = Get-Content -LiteralPath $freeze -Raw -Encoding UTF8 | ConvertFrom-Json
        $frHash = [string]$fr.setup_sha256
        Add-Check "exe 与冻结记录" $(if ($frHash.ToUpperInvariant() -eq $exeHash) { "PASS" } else { "FAIL" }) `
            ("FREEZE-RECORD.setup_sha256=$frHash")
    } else {
        Add-Check "exe 与冻结记录" "WARN" "缺少 FREEZE-RECORD.json（真实构建才会写），跳过该比对"
    }
}

if (-not $zipPath) {
    Add-Check "升级包存在" "FAIL" ("在 installer/release 下找不到 jarvis-$Version-upgrade.zip")
} else {
    $zipHash = Get-Hash $zipPath
    $zipSize = Get-Size $zipPath
    $zipName = Split-Path -Leaf $zipPath
    $zipSum = $sums[$zipName]
    Add-Check "zip 哈希（对照 SHA256SUMS）" $(if ($zipSum -and $zipSum -eq $zipHash) { "PASS" } else { "FAIL" }) `
        ("实际 {0} 字节 / {1}；SHA256SUMS={2}" -f $zipSize, $zipHash, $(if ($zipSum) { $zipSum } else { "无该条目" }))

    if ($manifest) {
        $rel = @($manifest.releases)[0]
        $mHash = ([string]$rel.sha256).ToUpperInvariant()
        $mSize = [int]$rel.size
        $ok = ($mHash -eq $zipHash) -and ($mSize -eq $zipSize) -and ([string]$rel.artifact -eq $zipName)
        Add-Check "zip 哈希（对照发布清单）" $(if ($ok) { "PASS" } else { "FAIL" }) `
            ("清单 version={0} artifact={1} sha256={2} size={3}" -f $rel.version, $rel.artifact, $rel.sha256, $rel.size)
    }

    if ($CheckPublished) {
        $tmp = Join-Path $env:TEMP ("liuhao-published-" + [guid]::NewGuid().ToString("N") + ".json")
        $curlCode = 0
        try {
            curl.exe -sS --max-time 60 -o $tmp $PublishedManifestUrl
            $curlCode = $LASTEXITCODE
        } catch { $curlCode = 1 }
        if ($curlCode -eq 0 -and (Test-Path $tmp)) {
            try {
                $pub = Get-Content -LiteralPath $tmp -Raw -Encoding UTF8 | ConvertFrom-Json
                $prel = @($pub.releases)[0]
                $pHash = ([string]$prel.sha256).ToUpperInvariant()
                $pSize = [int]$prel.size
                $pOk = ($pHash -eq $zipHash) -and ($pSize -eq $zipSize)
                Add-Check "zip 哈希（对照公网发布页）" $(if ($pOk) { "PASS" } else { "FAIL" }) `
                    ("发布页 version={0} sha256={1} size={2} sequence={3}" -f $prel.version, $prel.sha256, $prel.size, $pub.sequence)
            } catch {
                Add-Check "公网清单可解析" "FAIL" ("$PublishedManifestUrl 解析失败：" + $_.Exception.Message)
            } finally {
                Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            }
        } else {
            Add-Check "公网清单抓取" "FAIL" ("HTTP 抓取失败（退出码 $curlCode）：$PublishedManifestUrl")
        }
    } else {
        Write-Host "  [SKIP] 公网发布页比对：未加 -CheckPublished（只读 HTTP，不会改动线上）" -ForegroundColor Gray
    }
}

Write-Step "自检 6/6：安装器快捷方式参数"
$issPath = Join-Path $RepoRoot "installer\liuhao.iss"
if (-not (Test-Path $issPath)) {
    Add-Check "安装器脚本存在" "FAIL" "缺少 $issPath"
} else {
    $hits = @(Select-String -LiteralPath $issPath -Pattern "--open-browser")
    $iconLines = @()
    $inIcons = $false
    foreach ($line in Get-Content -LiteralPath $issPath -Encoding UTF8) {
        $trim = $line.Trim()
        if ($trim -match '^\[') { $inIcons = ($trim -match '^\[Icons\]') }
        if ($inIcons -and $trim -match 'Parameters:') { $iconLines += $trim }
    }
    $detail = ("含 --open-browser 的行 {0} 行" -f $hits.Count)
    if ($iconLines.Count -gt 0) { $detail += ("；快捷方式 Parameters " + $iconLines.Count + " 处：" + ($iconLines -join " | ")) }
    $status = if ($hits.Count -eq 0) { "PASS" } else { "FAIL" }
    Add-Check "快捷方式参数不含 --open-browser" $status $detail
}

# ---------------------------------------------------------------- 汇总
Write-Host ""
$failed = @($script:Checks | Where-Object { $_.Status -eq "FAIL" })
$warned = @($script:Checks | Where-Object { $_.Status -eq "WARN" })
$passed = @($script:Checks | Where-Object { $_.Status -eq "PASS" })
Write-Host ("================ 自检汇总 ================") -ForegroundColor Cyan
Write-Host ("  PASS {0} / WARN {1} / FAIL {2}（共 {3} 项）" -f $passed.Count, $warned.Count, $failed.Count, $script:Checks.Count)
if ($failed.Count -gt 0) {
    foreach ($f in $failed) { Write-Host ("  FAIL  " + $f.Name + "：" + $f.Detail) -ForegroundColor Red }
    Write-Host ("结论：FAIL。发布物不可用于对外分发。") -ForegroundColor Red
    exit 1
}
Write-Host ("结论：自检通过（" + $mode + "）") -ForegroundColor Green
if ($script:DryRun) {
    Write-Host "  本次未构建、未签名、未写 SHA256SUMS、未覆盖 dist\installer、未发布。" -ForegroundColor Yellow
}
exit 0
