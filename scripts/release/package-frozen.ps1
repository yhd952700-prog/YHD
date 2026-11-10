<#
.SYNOPSIS
  冻结后一键打包（可重复执行、失败即停）。

.DESCRIPTION
  把「冻结之后怎么打包」从口头约定固化成脚本，顺序固定：

      前置门禁 → 环境闸 → 依赖链 → 前端基线 → 前端构建
               → stage_payload → smoke_payload → 环境复检 → 前端同源
               → ISCC 编译 → 成品包合规校验 → 产物与 SHA256 记录

  四条历史踩坑在这里是**硬失败**，不靠人记：
    1. `_MEASURE_CODE` 传递依赖遗漏  → `freeze_checks.py deps`（真跑 import 图）
    2. 机器级 LIUHAO_* 污染冒烟      → `freeze_checks.py env` + 子进程环境清洗
    3. 旧工作树打包致前端模块数不一致 → 前端源码指纹/模块数基线 + payload 同源校验
    4. 许可证/SBOM 不齐却已出包      → `verify_package_compliance.py --exe`；不 PASS 不写 `SHA256SUMS.txt`

  产物命名：
    <OutRoot>\installer\LiuHao-Setup-<Version>.exe
    <OutRoot>\release\liuhao-<Version>-upgrade.zip
    <OutRoot>\installer\SHA256SUMS.txt
    <OutRoot>\FREEZE-RECORD.json      ← 版本/提交/解释器/ISCC/模块数/指纹/哈希

.PARAMETER DryRun
  只跑只读门禁：不写 dist\、不构建前端、不组 payload、不冒烟、不编译。
  打印逐条结论；任一门禁失败则退出码 1。

.PARAMETER AcceptFrontendBaseline
  承认「前端源码/模块数就是本次这样」，重采基线。只在确认当前工作树是本次
  要发布的代码时使用；否则请切到冻结提交再打包。

.PARAMETER ReleasePageFile
  发布页 HTML 的本地副本（必填）。归档到 dist\installer 之后、打印「冻结打包完成」
  之前，脚本会用 scripts\verify_release_page_values.py 逐处核对页面上的版本/哈希/
  字节/文件名/data-copy；任何一处不一致就 throw，不打印成功横幅。
  页面在独立仓库时先下载一份到本地再传本参数。

.PARAMETER SkipReleasePage
  显式跳过 §12.2 的发布页数值核对，检查表里记为 SKIP（不是 PASS/FAIL 改名）。
  仅用于「页面换版在包产出之后执行」的场景：出包可以跳，发布必须拿换版校验
  的 PASS 输出到 verify_publish_source.py --release-page-evidence 补偿，否则拒发。
  必须与 -SkipReleasePageReason 同时给出；只给开关不给理由直接 throw。

.PARAMETER SkipReleasePageReason
  跳过的理由原文，写入控制台日志、检查表 Detail 与 FREEZE-RECORD.release_page.reason。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\package-frozen.ps1 -DryRun

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\release\package-frozen.ps1 -Version 0.1.1
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$Version = "",
    [string]$OutRoot = "",
    [string]$PythonExe = "",
    [string]$ReleaseGateDir = "",
    [string]$ReleasePageFile = "",
    [switch]$SkipReleasePage,
    [string]$SkipReleasePageReason = "",
    [string]$IsccPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    [switch]$DryRun,
    [switch]$AcceptFrontendBaseline
)

$ErrorActionPreference = "Stop"
$script:DryRun = [bool]$DryRun
$script:Checks = New-Object System.Collections.Generic.List[object]

# 跳过发布页核对是「有痕迹的例外」，不是静默开关：开关与理由必须成对出现。
# 只给理由不给开关时按「没传开关」处理，行为与历史完全一致。
if ($SkipReleasePage -and -not $SkipReleasePageReason.Trim()) {
    throw "传了 -SkipReleasePage 但没有 -SkipReleasePageReason：跳过必须写明理由（会落进 FREEZE-RECORD.release_page）"
}

function Add-Check {
    param([string]$Name, [string]$Status, [string]$Detail)
    $script:Checks.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail })
    $color = switch ($Status) { "PASS" { "Green" } "WARN" { "Yellow" } "FAIL" { "Red" } default { "Gray" } }
    Write-Host ("  [{0}] {1}：{2}" -f $Status, $Name, $Detail) -ForegroundColor $color
    if ($Status -eq "FAIL" -and -not $script:DryRun) {
        throw ("门禁失败：{0} — {1}" -f $Name, $Detail)
    }
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-PyGate {
    param([string]$Name, [string[]]$Arguments)
    $output = & $PythonExe $ChecksPy @Arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    $lines = @($output.Trim() -split "`r?`n" | Where-Object { $_ -ne "" })
    $last = if ($lines.Count -gt 0) { $lines[-1] } else { "（无输出）" }
    if ($code -eq 0) {
        Add-Check $Name "PASS" $last
    } else {
        Write-Host $output.TrimEnd()
        Add-Check $Name "FAIL" $last
    }
}

# 直接跑仓库里的独立校验脚本（契约 / 测试收集）。失败时把完整输出打出来，
# 只留最后一行进汇总表——这样人能看到根因，表格也不会被日志淹没。
function Invoke-RepoGate {
    param([string]$Name, [string]$RelativeScript)
    $scriptPath = Join-Path $RepoRoot $RelativeScript
    if (-not (Test-Path $scriptPath)) {
        Add-Check $Name "FAIL" "找不到 $RelativeScript"
        return
    }
    $prevEap = $ErrorActionPreference
    # 网关/契约脚本会把应用日志写到 stderr；在 $ErrorActionPreference='Stop' 下
    # PowerShell 5.1 会把原生命令的 stderr 当成 NativeCommandError 终止整个脚本。
    $ErrorActionPreference = "Continue"
    try {
        $output = & $PythonExe $scriptPath 2>&1 | Out-String
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $lines = @($output.Trim() -split "`r?`n" | Where-Object { $_ -ne "" })
    $last = if ($lines.Count -gt 0) { $lines[-1] } else { "（无输出）" }
    if ($code -eq 0) {
        Add-Check $Name "PASS" $last
    } else {
        Write-Host $output.TrimEnd()
        Add-Check $Name "FAIL" $last
    }
}

# ---------------------------------------------------------------- 0. 基本路径
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$RepoRoot = (Resolve-Path $RepoRoot).Path
$ChecksPy = Join-Path $RepoRoot "scripts\release\freeze_checks.py"
if (-not (Test-Path $ChecksPy)) { throw "找不到门禁脚本 $ChecksPy" }

if (-not $PythonExe) {
    if ($env:LIUHAO_RELEASE_PYTHON) {
        $PythonExe = $env:LIUHAO_RELEASE_PYTHON
    } elseif (Test-Path "D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe") {
        $PythonExe = "D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe"
    } else {
        $PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    }
}

if (-not $Version) {
    $pyproject = Join-Path $RepoRoot "pyproject.toml"
    $match = Select-String -Path $pyproject -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { throw "无法从 pyproject.toml 读取版本号" }
    $Version = $match.Matches[0].Groups[1].Value
}
if (-not $OutRoot) { $OutRoot = Join-Path $RepoRoot ("dist\freeze-" + $Version) }

Write-Host ""
Write-Host "LiuHao AI OS · 冻结后一键打包" -ForegroundColor Cyan
Write-Host "  模式    $(if ($DryRun) { 'DRY-RUN（只校验，不写 dist、不编译）' } else { '真实构建' })"
Write-Host "  版本    $Version"
Write-Host "  仓库    $RepoRoot"
Write-Host "  输出    $OutRoot"
Write-Host "  解释器  $PythonExe"
Write-Host "  ISCC    $IsccPath"

# -------------------------------------------------------------- 1. 前置门禁
Write-Step "前置门禁"

# 1.1 工作树必须干净：冻结包必须能由一个提交完整重建。
# 失败时逐条列出「具体要提交/还原的文件」，不接受只说一句「有未提交」。
$porcelain = @(& git -C $RepoRoot status --porcelain)
if ($LASTEXITCODE -ne 0) {
    Add-Check "工作树干净" "FAIL" "git status 执行失败（不是 git 仓库？）"
} elseif ($porcelain.Count -eq 0) {
    Add-Check "工作树干净" "PASS" "0 项未提交"
} else {
    $statusLabels = @{
        "M " = "已修改(暂存)"; " M" = "已修改"; "MM" = "已修改(暂存+工作区)"
        "A " = "新增(暂存)"; "AM" = "新增(暂存+工作区)"; " D" = "已删除"; "D " = "已删除(暂存)"
        "R " = "重命名"; "RM" = "重命名(暂存+工作区)"; "C " = "复制"; "UU" = "冲突"
        "??" = "未跟踪"
    }
    Write-Host "  以下文件必须先提交（或还原/删除）才能出包：" -ForegroundColor Yellow
    foreach ($line in $porcelain) {
        $code = if ($line.Length -ge 2) { $line.Substring(0, 2) } else { $line }
        $path = if ($line.Length -ge 4) { $line.Substring(3) } else { $line }
        $label = $statusLabels[$code]
        if (-not $label) { $label = "变更" }
        Write-Host ("    [{0}] {1}" -f $label, $path) -ForegroundColor Yellow
    }
    $paths = @($porcelain | ForEach-Object { if ($_.Length -ge 4) { $_.Substring(3) } else { $_ } })
    $preview = ($paths | Select-Object -First 10) -join "; "
    if ($paths.Count -gt 10) { $preview += " …（共 $($paths.Count) 项，完整清单见上方逐条输出）" }
    Add-Check "工作树干净" "FAIL" ("$($porcelain.Count) 项未提交，待提交文件：" + $preview)
}

# 1.1b HEAD 必须已经推送：冻结包要能被别人按提交号复现，本地领先的提交等于包不可追溯。
$branch = (& git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null)
$upstream = (& git -C $RepoRoot rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $upstream) {
    Add-Check "HEAD 已推送" "FAIL" ("当前分支 {0} 没有上游分支（detached HEAD 或未设置 upstream）；请先推送到远端再出包。" -f $branch)
} else {
    $ahead = 0
    $behind = 0
    [void][int]::TryParse((& git -C $RepoRoot rev-list --count '@{u}..HEAD'), [ref]$ahead)
    [void][int]::TryParse((& git -C $RepoRoot rev-list --count 'HEAD..@{u}'), [ref]$behind)
    if ($ahead -gt 0) {
        $commits = @(& git -C $RepoRoot log --oneline '@{u}..HEAD')
        Add-Check "HEAD 已推送" "FAIL" ("$branch 领先 $upstream $ahead 个提交，未推送：" + ($commits -join " / "))
    } elseif ($behind -gt 0) {
        Add-Check "HEAD 已推送" "FAIL" "$branch 落后 $upstream $behind 个提交；请先同步再出包。"
    } else {
        $shortHead = (& git -C $RepoRoot rev-parse --short HEAD)
        Add-Check "HEAD 已推送" "PASS" "$branch 与 $upstream 同步（$shortHead）"
    }
}

# 1.1c 契约与测试收集必须 PASS：这两个红点会直接让交付包与代码不一致/不可测。
Invoke-RepoGate "OpenAPI 契约" "scripts\verify_openapi_contract.py"
Invoke-RepoGate "测试收集" "scripts\verify_test_collection.py"

# 1.2 不能有别的会话正在写产品文件（并发构建会让产物与源码错位）。
$since = (Get-Date).AddSeconds(-180)
$watchDirs = @("src", "apps", "packaging", "installer", "config", "tests", "scripts") |
    ForEach-Object { Join-Path $RepoRoot $_ } | Where-Object { Test-Path $_ }
$recent = @()
if ($watchDirs.Count -gt 0) {
    $recent = @(Get-ChildItem $watchDirs -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.LastWriteTime -gt $since -and
            $_.FullName -notmatch "\\node_modules\\|\\__pycache__\\|\\dist\\|\\\.git\\|\\scripts\\release\\"
        } |
        Sort-Object LastWriteTime -Descending)
}
if ($recent.Count -eq 0) {
    Add-Check "无并发写文件" "PASS" "近 180 秒内产品目录无改动"
} else {
    $head = ($recent | Select-Object -First 5 | ForEach-Object {
        "{0} ({1:HH:mm:ss})" -f $_.FullName.Substring($RepoRoot.Length + 1), $_.LastWriteTime
    }) -join " | "
    Add-Check "无并发写文件" "FAIL" "近 180 秒有 $($recent.Count) 个文件被改动，疑似并发会话在写：$head"
}

# 1.3 版本号全链一致（pyproject / src/version.py / 安装脚本）。
$pyVersion = (Select-String -Path (Join-Path $RepoRoot "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1).Matches[0].Groups[1].Value
$srcVersion = (Select-String -Path (Join-Path $RepoRoot "src\version.py") -Pattern 'APP_VERSION\s*=\s*"([^"]+)"' |
    Select-Object -First 1).Matches[0].Groups[1].Value
$issVersion = (Select-String -Path (Join-Path $RepoRoot "installer\liuhao.iss") -Pattern '#define AppVersion "([^"]+)"' |
    Select-Object -First 1).Matches[0].Groups[1].Value
$versionSet = @(@($pyVersion, $srcVersion, $issVersion) | Select-Object -Unique)
if ($versionSet.Count -eq 1 -and $versionSet[0] -eq $Version) {
    Add-Check "版本一致" "PASS" "$Version（pyproject / src/version.py / liuhao.iss）"
} else {
    Add-Check "版本一致" "FAIL" ("pyproject=$pyVersion，src/version.py=$srcVersion，liuhao.iss=$issVersion，脚本=$Version")
}

# 1.4 解释器：存在且为 3.12（随包运行时的来源）。
if (-not (Test-Path $PythonExe)) {
    Add-Check "Python 解释器" "FAIL" "找不到 $PythonExe"
} else {
    $pyInfo = @(& $PythonExe -c "import sys;print(sys.version.split()[0]);print(sys.base_prefix)")
    if ($LASTEXITCODE -ne 0) {
        Add-Check "Python 解释器" "FAIL" "解释器无法启动：$PythonExe"
    } elseif ($pyInfo[0] -notmatch '^3\.12\.') {
        Add-Check "Python 解释器" "FAIL" ("版本 $($pyInfo[0])，要求 3.12.x（与 pyproject requires-python 对齐）")
    } else {
        Add-Check "Python 解释器" "PASS" ("$($pyInfo[0]) @ $PythonExe（base $($pyInfo[1])）")
    }
}

# 1.5 ISCC：路径写死在这里，缺失就停，不伪造 exe。
if (Test-Path $IsccPath) {
    $isccVersion = (Get-Item $IsccPath).VersionInfo.FileVersion
    if (-not $isccVersion -or $isccVersion -eq "0.0.0.0") { $isccVersion = "未标注版本资源" }
    Add-Check "ISCC 编译器" "PASS" "$IsccPath（$isccVersion）"
} else {
    Add-Check "ISCC 编译器" "FAIL" "找不到 $IsccPath。安装 Inno Setup 6 或传 -IsccPath。"
}

# 1.6 输出目录必须全新：复用旧目录会把上一版 payload 混进本版。
if (Test-Path $OutRoot) {
    $existing = @(Get-ChildItem $OutRoot -Force -ErrorAction SilentlyContinue)
    if ($existing.Count -eq 0) {
        Add-Check "输出目录全新" "PASS" "$OutRoot 存在但为空"
    } elseif ($DryRun) {
        Add-Check "输出目录全新" "WARN" "$OutRoot 已有 $($existing.Count) 项，真打会拒绝复用"
    } else {
        Add-Check "输出目录全新" "FAIL" "$OutRoot 已有 $($existing.Count) 项，拒绝复用旧产物"
    }
} else {
    Add-Check "输出目录全新" "PASS" "$OutRoot 尚不存在（将在构建时创建）"
}

# 1.7 dist 现状（只报告，不改动）。
$distRoot = Join-Path $RepoRoot "dist"
$distEntries = if (Test-Path $distRoot) { @(Get-ChildItem $distRoot -Force) } else { @() }
$sameVersionExe = Join-Path $distRoot ("installer\LiuHao-Setup-$Version.exe")
if ($distEntries.Count -eq 0) {
    Add-Check "dist 现状" "PASS" "dist\ 为空"
} elseif (Test-Path $sameVersionExe) {
    Add-Check "dist 现状" "WARN" ("dist\ 有 $($distEntries.Count) 项，且已存在同名旧包 $sameVersionExe（新包写入 $OutRoot，不覆盖）")
} else {
    Add-Check "dist 现状" "WARN" "dist\ 有 $($distEntries.Count) 项历史产物（本轮不清理，新包写入 $OutRoot）"
}

# 1.8 明文凭据扫描：仓库 logs\ 里有完整 Key 就停（新工作树无 logs 时 PASS）。
#     安装后的真实数据目录用 runbook 里那条 --data-dir 命令复扫，不在打包阶段
#     读开发机上的本地数据，避免"构建结果取决于这台机器装过什么"。
Write-Step "明文凭据扫描（logs / 诊断包 / 审计库）"
Invoke-PyGate "日志明文凭据" @("log-leak", "--repo", $RepoRoot)

# 1.8b Runbook 同源：仓库根 RELEASE-RUNBOOK.md 是唯一事实源，dist 副本是生成物。
#      先按根文件重新生成副本（只写这一个生成物），再校验内容一致——手改副本
#      会在这一步被抹掉，避免两份清单说法不一致。
Write-Step "Runbook 同源（唯一事实源 → dist 生成物）"
# DryRun 必须真正只读：不带 --sync 只校验，不会生成/覆盖 dist\release-gate 里的副本。
$runbookArgs = @("runbook", "--repo", $RepoRoot)
if (-not $script:DryRun) { $runbookArgs += "--sync" }
Invoke-PyGate "Runbook 同源" $runbookArgs

# 1.9 本机攻击面回归闸：P0/P1 与本机证明、备份导出的可重复探针。
#     这是「本机攻击面」的回归闸——任何一条 FAIL 都阻塞出包（Add-Check 抛错）。
#     --allow-missing-installed：打包阶段安装副本可能尚未存在；装机后按 runbook
#     5.7 去掉该参数复跑，⑤ 必须真跑。
Write-Step "本机攻击面回归闸（回环 / 已存 Key 外带 / 管道 / 备份）"
$attackSurfaceScript = Join-Path $RepoRoot "scripts\acceptance\local_attack_surface_e2e.py"
if (-not (Test-Path $attackSurfaceScript)) {
    Add-Check "本机攻击面回归" "FAIL" "找不到 scripts\acceptance\local_attack_surface_e2e.py"
} else {
    # 与 Invoke-RepoGate 同一个 PS 5.1 陷阱：验收脚本拉起的隔离子进程会把启动
    # 日志（如“未找到控制台构建产物”）写到 stderr，EAP=Stop 下会被当成
    # NativeCommandError 终止整个脚本——那样连真实读数都打不出来。这里只放行
    # stderr，判定仍严格看退出码。
    $prevAttackEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $attackSurfaceOut = & $PythonExe $attackSurfaceScript --allow-missing-installed 2>&1 | Out-String
        $attackSurfaceCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevAttackEap
    }
    $attackSurfaceLines = @($attackSurfaceOut.Trim() -split "`r?`n" | Where-Object { $_ -ne "" })
    $attackSurfaceLast = if ($attackSurfaceLines.Count -gt 0) { $attackSurfaceLines[-1] } else { "（无输出）" }
    if ($attackSurfaceCode -eq 0) {
        Add-Check "本机攻击面回归" "PASS" $attackSurfaceLast
    } else {
        Write-Host $attackSurfaceOut.TrimEnd()
        Add-Check "本机攻击面回归" "FAIL" ("回归探针有 FAIL（exit $attackSurfaceCode）：$attackSurfaceLast")
    }
}

# ------------------------------------------------------- 2. 环境闸（第一次）
Write-Step "环境闸（进程 / 用户 / 机器三级）"
Invoke-PyGate "环境变量闸" @("env", "--repo", $RepoRoot)

# ---------------------------------------------------------- 3. 依赖链硬门禁
Write-Step "运行时依赖链（_MEASURE_CODE 传递依赖）"
Invoke-PyGate "运行时依赖链" @("deps", "--repo", $RepoRoot, "--python", $PythonExe)

# ------------------------------------------------------- 4. 前端基线与新鲜度
Write-Step "前端基线（旧工作树 → 模块数不一致）"
Invoke-PyGate "前端源码基线" @("frontend-source", "--repo", $RepoRoot)

$consoleDir = Join-Path $RepoRoot "apps\console\console"
$consoleIndex = Join-Path $consoleDir "dist\index.html"
if (-not (Test-Path $consoleIndex)) {
    Add-Check "前端产物新鲜度" "FAIL" "缺少 $consoleIndex（需先构建，真实构建会自行执行 npm run build）"
} else {
    $frontendFiles = @(Get-ChildItem (Join-Path $consoleDir "src") -Recurse -File -ErrorAction SilentlyContinue)
    $newestSource = ($frontendFiles | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
    $distTime = (Get-Item $consoleIndex).LastWriteTime
    if ($newestSource -and $newestSource.LastWriteTime -gt $distTime) {
        Add-Check "前端产物新鲜度" "FAIL" ("$($newestSource.Name) 比 dist\index.html 新（{0:yyyy-MM-dd HH:mm:ss} > {1:yyyy-MM-dd HH:mm:ss}），前端产物是旧的" -f $newestSource.LastWriteTime, $distTime)
    } else {
        Add-Check "前端产物新鲜度" "PASS" ("dist\index.html ({0:yyyy-MM-dd HH:mm:ss}) 不早于源码" -f $distTime)
    }
}
$tsbuildinfo = @(Get-ChildItem (Join-Path $consoleDir "node_modules\.tmp") -Filter "*.tsbuildinfo" -ErrorAction SilentlyContinue)
if ($tsbuildinfo.Count -gt 0) {
    Add-Check "tsc 增量缓存" "WARN" ("存在 $($tsbuildinfo.Count) 个 tsbuildinfo（历史坑：缓存过时会给出假诊断），真实构建会先删除")
} else {
    Add-Check "tsc 增量缓存" "PASS" "无残留 tsbuildinfo"
}

# --------------------------------------------------- 5. stage_payload 可导入
Write-Step "stage_payload 入口可执行（不写 payload）"
$stageHelp = & $PythonExe (Join-Path $RepoRoot "packaging\stage_payload.py") --help 2>&1 | Out-String
if ($LASTEXITCODE -eq 0) {
    Add-Check "stage_payload 入口" "PASS" "python packaging/stage_payload.py --help 正常（src 可导入）"
} else {
    Write-Host $stageHelp.TrimEnd()
    Add-Check "stage_payload 入口" "FAIL" "stage_payload 无法导入（src 尚未修好？）"
}

# ------------------------------------------------------------ dry-run 收尾
if ($DryRun) {
    Write-Host ""
    Write-Host "================ DRY-RUN 结论 ================" -ForegroundColor Cyan
    $script:Checks | Format-Table -AutoSize Name, Status, Detail | Out-String -Width 200 | Write-Host
    $failCount = @($script:Checks | Where-Object { $_.Status -eq "FAIL" }).Count
    $warnCount = @($script:Checks | Where-Object { $_.Status -eq "WARN" }).Count
    if ($failCount -gt 0) {
        Write-Host "未通过：$failCount 条 FAIL、$warnCount 条 WARN。未写 dist\、未构建、未编译。" -ForegroundColor Red
        exit 1
    }
    Write-Host "全部前置条件通过（$warnCount 条 WARN）。未写 dist\、未构建、未编译。" -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------ 6. 真实构建
$sourceCommit = (& git -C $RepoRoot rev-parse HEAD).Trim()
# 只记 commit 不够：同一个 commit 在不同 index/工作树状态下能打出不同字节的包。
# 树 ID 是"这次构建实际用的目录内容"的可比对指纹；前置门禁已要求工作树干净，
# 因此 HEAD^{tree} 与实际打包内容一致。
$sourceTree = (& git -C $RepoRoot rev-parse "HEAD^{tree}").Trim()
New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null
$buildLog = Join-Path $OutRoot "build.log"

Write-Step "清理 tsc 增量缓存并构建前端 + payload + 冒烟 + 编译"
Write-Host "  SOURCE-COMMIT $sourceCommit"
Write-Host "  SOURCE-TREE   $sourceTree"

# 历史坑：tsbuildinfo 缓存过时会跳过真实检查并报假诊断。每次冻结构建前删干净。
$tsCache = Join-Path $consoleDir "node_modules\.tmp"
if (Test-Path $tsCache) {
    Get-ChildItem $tsCache -Filter "*.tsbuildinfo" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
}

# 子进程环境清洗：即使有人在构建中途设了 LIUHAO_*，也不让它进入 npm/uvicorn/冒烟。
$watchedExact = @("AUDIT_DB_PATH", "AUDIT_DB_BACKUP_PATH", "CONVERSATION_DB_PATH",
                  "MEMORY_DB_PATH", "PERSONAL_CONTEXT_DB_PATH", "WORKSPACE_ROOT")
$savedEnv = @{}
foreach ($item in @(Get-ChildItem Env:)) {
    if ($item.Name -like "LIUHAO_*" -or $watchedExact -contains $item.Name) {
        $savedEnv[$item.Name] = $item.Value
        Remove-Item ("Env:" + $item.Name) -ErrorAction SilentlyContinue
    }
}
if ($savedEnv.Count -gt 0) {
    Write-Host ("  已从构建进程剥离 {0} 个可疑变量：{1}" -f $savedEnv.Count, (($savedEnv.Keys | Sort-Object) -join "、"))
}

try {
    $buildScript = Join-Path $RepoRoot "packaging\build_windows_installer.ps1"
    # 开发目录路径扫描必须是硬门禁：0.1.1 曾因未传 -StrictReleaseDevPaths 而让
    # 2 处命中静默放行。发布构建默认强制，不提供关闭开关。
    $buildArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $buildScript,
                   "-Version", $Version, "-OutRoot", $OutRoot, "-PythonExe", $PythonExe,
                   "-StrictReleaseDevPaths")
    # npm/vite 把体积告警写到 stderr；PS 5.1 在 EAP=Stop 下会把原生命令 stderr 当终止
    # 错误。这里临时放行，成功与否只看 $LASTEXITCODE。
    $previousEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & powershell.exe @buildArgs 2>&1 | Tee-Object -FilePath $buildLog
        $buildExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousEap
    }
} finally {
    foreach ($name in $savedEnv.Keys) { Set-Item ("Env:" + $name) $savedEnv[$name] -ErrorAction SilentlyContinue }
}
if ($buildExit -ne 0) { throw "build_windows_installer.ps1 失败（exit $buildExit），见 $buildLog" }

$payloadApp = Join-Path $OutRoot "payload\app"
$buildText = Get-Content -Raw -LiteralPath $buildLog -ErrorAction SilentlyContinue
$moduleMatch = [regex]::Match($buildText, '(\d+)\s+modules transformed')
if (-not $moduleMatch.Success) {
    throw "构建日志里找不到 'N modules transformed'，无法核对前端模块数（见 $buildLog）"
}
$moduleCount = [int]$moduleMatch.Groups[1].Value
Write-Host "  前端模块数 $moduleCount"

# ---------------------------------------- 6.1 payload 裁剪硬断言（缺台账即失败）
# 背景：2026-11-10 出现过一个没跑 prune 的 36.9 MB 同名产物，签名有效、版本号 0.1.2，
# 躺在 dist\freeze-0.1.2\installer\ 里，只靠人眼比文件数才被发现。是否裁剪过
# payload 从来没被断言过，这里补上：没有 prune-import-ledger.jsonl 直接失败。
Write-Step "payload 裁剪断言（prune-import-ledger.jsonl 必须存在）"
$pruneLedger = Join-Path $payloadApp "prune-import-ledger.jsonl"
if (-not (Test-Path $pruneLedger -PathType Leaf)) {
    Add-Check "payload 已裁剪" "FAIL" ("payload 缺 prune-import-ledger.jsonl（{0}）：本轮未跑 prune，拒绝出包。若确需回滚裁剪，必须显式重打并在记录里说明。" -f $pruneLedger)
} elseif ((Get-Item $pruneLedger).Length -le 0) {
    Add-Check "payload 已裁剪" "FAIL" ("prune-import-ledger.jsonl 为空（0 字节）：无法证明裁剪规则被执行过")
} else {
    $pruneEnabled = $null
    $payloadManifestForPrune = Join-Path $OutRoot "payload\manifest.json"
    if (Test-Path $payloadManifestForPrune) {
        $pmPrune = Get-Content -Raw -LiteralPath $payloadManifestForPrune | ConvertFrom-Json
        if ($pmPrune.prune) { $pruneEnabled = $pmPrune.prune.enabled }
    }
    if ($pruneEnabled -eq $false) {
        Add-Check "payload 已裁剪" "FAIL" "manifest.json 记录 prune.enabled=false（回滚模式产物不得发布）"
    } else {
        Add-Check "payload 已裁剪" "PASS" ("台账 {0} 字节；manifest.prune.enabled={1}" -f (Get-Item $pruneLedger).Length, $pruneEnabled)
    }
}

# payload 指纹：文件数 + 字节数 + 路径/大小清单摘要。写进 FREEZE-RECORD 后，
# 发布复核可以拿它反问「这个 exe 对应的 payload 到底是什么」。
$payloadFiles = @(Get-ChildItem $payloadApp -Recurse -File -Force -ErrorAction SilentlyContinue)
$payloadCount = $payloadFiles.Count
$payloadBytes = 0
if ($payloadCount -gt 0) { $payloadBytes = [int64](($payloadFiles | Measure-Object Length -Sum).Sum) }
$payloadIndexLines = @($payloadFiles | ForEach-Object {
    ($_.FullName.Substring($payloadApp.Length).TrimStart('\') + '|' + $_.Length)
})
# 必须用序数排序（ordinal），与 verify_publish_source.py 的 sorted() 同一口径；
# Sort-Object 走的是区域文化排序，跨语言复核会对不上。
[Array]::Sort($payloadIndexLines, [System.StringComparer]::Ordinal)
$payloadIndex = $payloadIndexLines -join "`n"
$payloadHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $payloadDigest = ([BitConverter]::ToString(
        $payloadHasher.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($payloadIndex))
    ) -replace '-', '')
} finally { $payloadHasher.Dispose() }
Write-Host ("  payload 指纹 {0} 文件 / {1} 字节 / {2}…{3}" -f $payloadCount, $payloadBytes, $payloadDigest.Substring(0, 8), $payloadDigest.Substring($payloadDigest.Length - 4))

# ------------------------------------------------- 7. 环境复检（编译前）
Write-Step "环境复检（编译前，确认构建期间没人动环境）"
Invoke-PyGate "环境变量复检" @("env", "--repo", $RepoRoot)

# ------------------------------------------- 8. 前端同源 + 模块数基线
Write-Step "前端同源校验"
$captureArgs = @("frontend-source", "--repo", $RepoRoot, "--module-count", "$moduleCount")
if ($AcceptFrontendBaseline) { $captureArgs += "--capture" }
Invoke-PyGate "前端模块数基线" $captureArgs
Invoke-PyGate "payload 前端同源" @("frontend-payload", "--repo", $RepoRoot, "--payload", $payloadApp, "--version", $Version)

# ------------------------------------------------------------ 9. 产物记录
Write-Step "产物与 SHA256"
$setupExe = Join-Path $OutRoot ("installer\LiuHao-Setup-$Version.exe")
if (-not (Test-Path $setupExe)) {
    $candidates = @(Get-ChildItem (Join-Path $OutRoot "installer") -Filter "LiuHao-Setup-*.exe" -ErrorAction SilentlyContinue)
    if ($candidates.Count -eq 1) { $setupExe = $candidates[0].FullName }
}
if (-not (Test-Path $setupExe)) { throw "编译结束但找不到 LiuHao-Setup-$Version.exe（见 $buildLog）" }

$exeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setupExe).Hash
$exeBytes = (Get-Item $setupExe).Length
$exeSizeMb = [math]::Round($exeBytes / 1MB, 1)
$releaseZip = Join-Path $OutRoot ("release\liuhao-$Version-upgrade.zip")
$zipHash = if (Test-Path $releaseZip) { (Get-FileHash -Algorithm SHA256 -LiteralPath $releaseZip).Hash } else { "" }
$zipBytes = if (Test-Path $releaseZip) { (Get-Item $releaseZip).Length } else { 0 }
$zipSizeMb = if (Test-Path $releaseZip) { [math]::Round($zipBytes / 1MB, 1) } else { 0 }
if (-not (Test-Path $releaseZip)) { Add-Check "升级包" "FAIL" "缺少 $releaseZip" } else { Add-Check "升级包" "PASS" "$releaseZip（$zipSizeMb MB）" }

# --------------------------------------- 9.1 成品包合规（硬门禁，先于 SHA256SUMS）
Write-Step "成品包合规校验（不 PASS 不写 SHA256SUMS.txt）"
if (-not $ReleaseGateDir) {
    if ($env:LIUHAO_RELEASE_GATE) {
        $ReleaseGateDir = $env:LIUHAO_RELEASE_GATE
    } elseif (Test-Path (Join-Path $RepoRoot "dist\release-gate")) {
        $ReleaseGateDir = Join-Path $RepoRoot "dist\release-gate"
    }
}
$verifyScript = if ($ReleaseGateDir) { Join-Path $ReleaseGateDir "verify_package_compliance.py" } else { "" }
if (-not $verifyScript -or -not (Test-Path $verifyScript)) {
    Add-Check "合规校验器" "FAIL" "找不到 verify_package_compliance.py；请用 -ReleaseGateDir 指向合规线目录（dist\release-gate），或设置 LIUHAO_RELEASE_GATE"
}

# 本次构建专属 SBOM：不复用基线，避免“用旧 SBOM 验新包”。
# SBOM 审计会调用 payload 里的 runtime\python.exe 探测版本，探针导入标准库时
# 默认会往 payload 里写 __pycache__。那批 .pyc 产生于 ISCC 编译之后（不在 exe 里），
# 却会让 §6.1 的 payload 指纹与盘上树对不上——校验动作不能改构建输入，故禁写字节码。
#
# 全链路已排查「会执行 payload\runtime\python.exe 的动作」，逐一确认保护：
#   * scripts\release\freeze_checks.py deps       → _run_measure 已设 PYTHONDONTWRITEBYTECODE=1
#   * packaging\smoke_payload.py                  → _isolated_env 已设，且 build §4 会清 __pycache__/.pyc
#   * dist\release-gate\sbom_audit.py probe_runtime → 由本处（调用方）设，见下
#   * verify_package_compliance.py / verify_publish_source.py / verify_release_page_values.py
#                                                 → 只读解包与哈希，不执行包内任何内容
# 新增会跑 payload 解释器的校验时，必须走同一道保护并在上面这行清单里补一条。
$rgOut = Join-Path $OutRoot "release-gate"
New-Item -ItemType Directory -Force -Path $rgOut | Out-Null
$sbomJson = Join-Path $rgOut "sbom-cyclonedx.json"
$auditScript = Join-Path $ReleaseGateDir "sbom_audit.py"
$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$prevPycEnv = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = "1"
try {
    $auditOut = & $PythonExe $auditScript --app $payloadApp --out $rgOut --report (Join-Path $rgOut "SBOM-LICENSE-COMPLIANCE-REPORT.md") 2>&1 | Out-String
    $auditCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousEap
    if ($null -eq $prevPycEnv) {
        Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONDONTWRITEBYTECODE = $prevPycEnv
    }
}
if ($auditCode -eq 0 -and (Test-Path $sbomJson)) {
    Add-Check "本次构建 SBOM" "PASS" $sbomJson
} else {
    Write-Host $auditOut.TrimEnd()
    Add-Check "本次构建 SBOM" "FAIL" ("sbom_audit.py 未生成 $sbomJson（exit $auditCode）")
}

# 成品包校验：只读解包（innounp），不执行 setup.exe；退出码 0=PASS / 1=FAIL / 2=BLOCKED。
$complianceJson = Join-Path $OutRoot "compliance-installer.json"
$lgplSources = Join-Path $ReleaseGateDir "lgpl-sources"
$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $complianceOut = & $PythonExe $verifyScript --exe $setupExe --sbom $sbomJson --lgpl-sources $lgplSources --json-out $complianceJson 2>&1 | Out-String
    $complianceCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousEap
}
$complianceLines = @($complianceOut.Trim() -split "`r?`n" | Where-Object { $_ -ne "" })
$complianceVerdict = ($complianceLines | Where-Object { $_ -match '^结论：' } | Select-Object -Last 1)
$complianceLast = if ($complianceVerdict) { $complianceVerdict } elseif ($complianceLines.Count -gt 0) { $complianceLines[-1] } else { "（无输出）" }
if ($complianceCode -eq 0) {
    Add-Check "成品包合规（exe）" "PASS" $complianceLast
} else {
    Write-Host $complianceOut.TrimEnd()
    $hint = if ($complianceCode -eq 2) { "BLOCKED（退出码 2：解包器或路径问题，未写 SHA256SUMS.txt）" } else { "FAIL（退出码 1：缺项见 $complianceJson，未写 SHA256SUMS.txt）" }
    Add-Check "成品包合规（exe）" "FAIL" $hint
}

# 升级包合规：与 exe 并列，两者都过才放行；只读解包 zip，不执行包内任何内容。
$zipComplianceJson = Join-Path $OutRoot "compliance-upgrade.json"
$previousEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $zipComplianceOut = & $PythonExe $verifyScript --upgrade-zip $releaseZip --json-out $zipComplianceJson 2>&1 | Out-String
    $zipComplianceCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previousEap
}
$zipLines = @($zipComplianceOut.Trim() -split "`r?`n" | Where-Object { $_ -ne "" })
$zipVerdict = ($zipLines | Where-Object { $_ -match '^结论：' } | Select-Object -Last 1)
$zipLast = if ($zipVerdict) { $zipVerdict } elseif ($zipLines.Count -gt 0) { $zipLines[-1] } else { "（无输出）" }
if ($zipComplianceCode -eq 0) {
    Add-Check "升级包合规（zip）" "PASS" $zipLast
} else {
    Write-Host $zipComplianceOut.TrimEnd()
    $zipHint = if ($zipComplianceCode -eq 2) { "BLOCKED（退出码 2：zip 无法读取，未写 SHA256SUMS.txt）" } else { "FAIL（退出码 1：缺项见 $zipComplianceJson，未写 SHA256SUMS.txt）" }
    Add-Check "升级包合规（zip）" "FAIL" $zipHint
}

# ------------------------------- 9.2 包内自检（入口 / 关键文件 / 清单计数）
# 三条历史缺口都在这里钉死：
#   * 0.1.2 早期包缺 LiuHao.bat / START-HERE.txt / assets\liuhao.ico → 升级会删掉旧版对应文件；
#   * 0.1.1 包内 rate_limiter.py 还是 11-05 旧版 → 修了没进包，等于没修；
#   * 包内文件数与 LIUHAO_RELEASE.json 的 files 条数不一致 → 升级误删或多装。
Write-Step "包内自检：入口文件、关键文件内容哈希、文件清单计数"
$innounp = if ($ReleaseGateDir) { Join-Path $ReleaseGateDir "tools\innounp-2\innounp.exe" } else { "" }
$extractDir = Join-Path $OutRoot "installer-extract"
$appRoot = Join-Path $extractDir "{app}"
$unpackOk = $false
if (-not $innounp -or -not (Test-Path $innounp)) {
    Add-Check "包内解包" "FAIL" "找不到 innounp：$innounp"
} else {
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $innounp -x -b -q -y "-d$extractDir" $setupExe | Out-Null; $unpackCode = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prevEap }
    if ($unpackCode -ne 0 -or -not (Test-Path $appRoot)) {
        Add-Check "包内解包" "FAIL" ("innounp 解包失败（exit {0}）：{1}" -f $unpackCode, $extractDir)
    } else {
        $unpackOk = $true
        Add-Check "包内解包" "PASS" $extractDir
    }
}

if ($unpackOk) {
    # 9.2.1 入口文件：缺一个都会让升级删掉用户还能用的文件。
    $appTreeRootFiles = @(
        "capability-registry.yaml", "pyproject.toml", "requirements.txt",
        "oss-registry.yaml", "LiuHao.bat", "START-HERE.txt", "assets\liuhao.ico"
    )
    $missingEntries = @()
    foreach ($rel in $appTreeRootFiles) {
        if (-not (Test-Path (Join-Path $appRoot $rel) -PathType Leaf)) { $missingEntries += $rel }
    }
    if ($missingEntries.Count -gt 0) {
        $missingEntries | ForEach-Object { Write-Host "  !! 包内缺失 $_" -ForegroundColor Red }
        Add-Check "包内入口文件" "FAIL" ("缺：" + ($missingEntries -join "、"))
    } else {
        Add-Check "包内入口文件" "PASS" ("{0} 项齐全（含 assets\liuhao.ico）" -f $appTreeRootFiles.Count)
    }

    # 9.2.2 关键文件内容哈希：包内文件必须与源码 HEAD 内容一致，不看日期/大小。
    $keyFiles = @(
        "src/gateway/rate_limiter.py",
        "src/gateway/health.py",
        "src/gateway/main.py",
        "src/ai/personal_context.py",
        "src/upgrade/manager.py",
        "src/common/data_paths.py"
    )
    $keyProblems = @()
    foreach ($rel in $keyFiles) {
        $winRel = $rel -replace '/', '\'
        $srcPath = Join-Path $RepoRoot $winRel
        $packedPath = Join-Path $appRoot $winRel
        if (-not (Test-Path $srcPath -PathType Leaf)) {
            $keyProblems += "$rel（源码缺失）"
            continue
        }
        if (-not (Test-Path $packedPath -PathType Leaf)) {
            $keyProblems += "$rel（包内缺失）"
            continue
        }
        # 源码侧再确认与 HEAD 一致，避免拿未提交的工作区文件当基线。
        & git -C $RepoRoot diff --quiet HEAD -- $rel
        if ($LASTEXITCODE -ne 0) {
            $keyProblems += "$rel（工作区与 HEAD 不一致）"
            continue
        }
        $srcHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $srcPath).Hash
        $packedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $packedPath).Hash
        if ($srcHash -ne $packedHash) {
            $keyProblems += ("{0}（源码 {1}… / 包内 {2}…）" -f $rel, $srcHash.Substring(0, 12), $packedHash.Substring(0, 12))
        }
    }
    if ($keyProblems.Count -gt 0) {
        $keyProblems | ForEach-Object { Write-Host "  !! 包内旧文件 $_" -ForegroundColor Red }
        Add-Check "关键文件内容一致" "FAIL" (($keyProblems -join "；") + " → 拒绝出包")
    } else {
        Add-Check "关键文件内容一致" "PASS" ("{0} 个关键文件与源码 HEAD 内容哈希一致" -f $keyFiles.Count)
    }

    # 9.2.3 payload 清单里的入口文件存在性（供“装完能启动”追溯）。
    $payloadManifest = Join-Path $OutRoot "payload\manifest.json"
    if (-not (Test-Path $payloadManifest)) {
        Add-Check "payload 清单入口" "FAIL" "缺少 $payloadManifest"
    } else {
        $pm = Get-Content -Raw -LiteralPath $payloadManifest | ConvertFrom-Json
        $entryBad = @()
        foreach ($name in @("LiuHao.bat", "START-HERE.txt", "assets/liuhao.ico")) {
            if (-not $pm.entry_files -or $pm.entry_files.$name -ne $true) { $entryBad += $name }
        }
        if ($entryBad.Count -gt 0) {
            Add-Check "payload 清单入口" "FAIL" ("manifest.json 标注缺失：" + ($entryBad -join "、"))
        } else {
            Add-Check "payload 清单入口" "PASS" ("entry_files 三项均为 true（file_count={0}）" -f $pm.file_count)
        }
    }

    # 9.2.4 裁剪台账必须出现在「解包后的成品树」里：签名的是 exe，不是 payload 目录。
    # 只查 payload 目录不够——被签走的可能是另一棵树（2026-11-10 的 36.9 MB 事件）。
    $extractLedger = Join-Path $appRoot "prune-import-ledger.jsonl"
    if (-not (Test-Path $extractLedger -PathType Leaf)) {
        Add-Check "成品包含裁剪台账" "FAIL" ("解包后 {app}\ 里没有 prune-import-ledger.jsonl：这个 exe 是用未裁剪 payload 编的，拒绝出包")
    } else {
        Add-Check "成品包含裁剪台账" "PASS" ("{0}（{1} 字节）" -f $extractLedger, (Get-Item $extractLedger).Length)
    }

    # 9.2.5 升级包文件数必须等于 LIUHAO_RELEASE.json 的 files 清单条数。
    $releaseManifestPath = Join-Path $OutRoot "release\LIUHAO_RELEASE.json"
    if (-not (Test-Path $releaseManifestPath)) {
        Add-Check "升级包清单计数" "FAIL" "缺少 $releaseManifestPath"
    } elseif (-not (Test-Path $releaseZip)) {
        Add-Check "升级包清单计数" "FAIL" "缺少 $releaseZip"
    } else {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
        $rm = Get-Content -Raw -LiteralPath $releaseManifestPath | ConvertFrom-Json
        # files 清单在 releases[0] 里，顶层没有 files：顶层取不到时 @($null).Count 会等于 1，
        # 不要回退到那种写法（会把「读不到」当成「1 条」静默放行）。必须先判「有没有」再计数。
        $rel = @($rm.releases)[0]
        if ($rel -and $rel.files) {
            $listedFiles = @($rel.files)
        } elseif ($rm.files) {
            $listedFiles = @($rm.files)
        } else {
            throw "升级包清单缺少 files（顶层与 releases[0] 均未找到）：$releaseManifestPath"
        }
        $listedCount = $listedFiles.Count
        $zipHandle = [System.IO.Compression.ZipFile]::OpenRead($releaseZip)
        try { $zipEntryCount = $zipHandle.Entries.Count } finally { $zipHandle.Dispose() }
        if ($zipEntryCount -ne $listedCount) {
            Add-Check "升级包清单计数" "FAIL" ("包内 {0} 个文件 ≠ 清单 files {1} 条" -f $zipEntryCount, $listedCount)
        } else {
            Add-Check "升级包清单计数" "PASS" ("{0} 个文件与清单 files 条数一致" -f $zipEntryCount)
        }
    }
}

$sums = Join-Path $OutRoot "installer\SHA256SUMS.txt"
$sumLines = @("$exeHash  $(Split-Path -Leaf $setupExe)")
if ($zipHash) { $sumLines += "$zipHash  $(Split-Path -Leaf $releaseZip)" }
Set-Content -Path $sums -Value $sumLines -Encoding ASCII

# PS 5.1 的 ConvertTo-Json 遇到 List[object] 会抛「参数类型不匹配」，必须先转成
# 纯对象数组再写记录。
$checkList = @($script:Checks | ForEach-Object {
    [pscustomobject]@{ Name = $_.Name; Status = $_.Status; Detail = $_.Detail }
})
$record = [ordered]@{
    version = $Version
    source_commit = $sourceCommit
    source_tree = $sourceTree
    built_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    repo_root = $RepoRoot
    python = $PythonExe
    python_version = (& $PythonExe -c "import sys;print(sys.version.split()[0])")
    iscc = $IsccPath
    iscc_version = (Get-Item $IsccPath).VersionInfo.FileVersion
    frontend_module_count = $moduleCount
    # payload 指纹：verify_publish_source.py 在发布态复核这一块（缺字段即拒发）。
    # 这是把「exe 到底是哪棵 payload 编出来的」写进记录，避免同名不同内容的包互相冒充。
    payload = [ordered]@{
        file_count = $payloadCount
        bytes = $payloadBytes
        size_mib = [math]::Round($payloadBytes / 1MB, 1)
        sha256 = $payloadDigest
        prune_ledger = "prune-import-ledger.jsonl"
    }
    # 发布页只从这里取值（docs/RELEASE-PAGE-CONTENT.md）：字节数与 SHA256 是
    # 硬字段，页面不许手抄旧值。
    installer = [ordered]@{
        # verify_publish_source.py 只读 installer.version（无顶层兜底）：这里必须写，
        # 否则门禁报 version=None 拒发；读取方也不许加容差，避免契约各说各话。
        version = $Version
        filename = (Split-Path -Leaf $setupExe)
        size = $exeBytes
        # PowerShell 的 1MB = 1048576 字节，实际是 MiB；单位必须写对。
        size_mib = $exeSizeMb
        sha256 = $exeHash
    }
    setup_exe = $setupExe
    setup_bytes = $exeBytes
    setup_size_mib = $exeSizeMb
    setup_sha256 = $exeHash
    release_zip = $(if ($zipHash) { $releaseZip } else { $null })
    release_bytes = $(if ($zipHash) { $zipBytes } else { $null })
    release_size_mib = $zipSizeMb
    release_sha256 = $(if ($zipHash) { $zipHash } else { $null })
    compliance_status = "PASS"
    compliance_installer_json = $complianceJson
    compliance_upgrade_json = $zipComplianceJson
    compliance_sbom = $sbomJson
    build_log = $buildLog
    checks = $checkList
}
$recordPath = Join-Path $OutRoot "FREEZE-RECORD.json"
$record | ConvertTo-Json -Depth 6 | Set-Content -Path $recordPath -Encoding UTF8
# 交付目录也留一份：发布页/交付核对通常只看 dist\installer，别让它去猜上游路径。
$recordDeliveryPath = Join-Path $OutRoot "installer\FREEZE-RECORD.json"
Copy-Item -LiteralPath $recordPath -Destination $recordDeliveryPath -Force

# 出包后硬自检：记录里的字节数/哈希必须与 SHA256SUMS 首行、实际 exe 三处一致。
# 任何一处不一致都说明「页面拿到的数值」和「用户下载的文件」可能是两个东西。
Write-Step "校验 FREEZE-RECORD ↔ SHA256SUMS ↔ 实际 exe"
$recordReread = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
$sumsFirst = (Get-Content -LiteralPath $sums | Select-Object -First 1)
$sumsHash = ($sumsFirst -split '\s+')[0].Trim()
$exeBytesNow = (Get-Item $setupExe).Length
$exeHashNow = (Get-FileHash -Algorithm SHA256 -LiteralPath $setupExe).Hash
$recordProblems = @()
if ($recordReread.installer.size -ne $exeBytesNow) {
    $recordProblems += "记录 installer.size=$($recordReread.installer.size) ≠ 实际 exe $exeBytesNow"
}
if ($recordReread.installer.sha256 -ne $exeHashNow) {
    $recordProblems += "记录 installer.sha256=$($recordReread.installer.sha256) ≠ 实际 exe $exeHashNow"
}
if ($sumsHash -ne $exeHashNow) {
    $recordProblems += "SHA256SUMS 首行 $sumsHash ≠ 实际 exe $exeHashNow"
}
if (-not $sourceCommit -or -not $sourceTree) {
    $recordProblems += "记录缺 source_commit/source_tree（提交与树 ID 必须写入）"
}
# payload 指纹必须与刚测出来的这棵 payload 树逐项一致；不一致说明记录被改过、
# 或写记录时用的不是本次 payload——两种情况都不许出包。
$recPayload = $recordReread.payload
if (-not $recPayload) {
    $recordProblems += "记录缺 payload 指纹（file_count/bytes/sha256），无法复核 exe 对应的 payload"
} else {
    if ([int64]$recPayload.file_count -ne [int64]$payloadCount) {
        $recordProblems += "记录 payload.file_count=$($recPayload.file_count) ≠ 实际 $payloadCount"
    }
    if ([int64]$recPayload.bytes -ne [int64]$payloadBytes) {
        $recordProblems += "记录 payload.bytes=$($recPayload.bytes) ≠ 实际 $payloadBytes"
    }
    if ([string]$recPayload.sha256 -ne $payloadDigest) {
        $recordProblems += "记录 payload.sha256=$($recPayload.sha256) ≠ 实际 $payloadDigest"
    }
}
$recordProblems += @(Get-Content -LiteralPath $sums | Where-Object {
    $line = $_.Trim(); $line -and ($line -split '\s+').Count -ge 2 -and
    ($line -split '\s+')[1] -eq (Split-Path -Leaf $setupExe) -and ($line -split '\s+')[0] -ne $exeHashNow
} | ForEach-Object { "SHA256SUMS 中安装包行与记录不一致：$_" })
if ($recordProblems.Count -gt 0) {
    $recordProblems | ForEach-Object { Write-Host "  !! $_" -ForegroundColor Red }
    throw "FREEZE-RECORD 一致性自检失败，拒绝出包。"
}
Add-Check "FREEZE-RECORD 一致性" "PASS" ("字节数 {0} / SHA256 {1}…{2}，三处一致" -f $exeBytesNow, $exeHashNow.Substring(0, 8), $exeHashNow.Substring($exeHashNow.Length - 4))
$record.checks = @($script:Checks | ForEach-Object {
    [pscustomobject]@{ Name = $_.Name; Status = $_.Status; Detail = $_.Detail }
})
$record | ConvertTo-Json -Depth 6 | Set-Content -Path $recordPath -Encoding UTF8
Copy-Item -LiteralPath $recordPath -Destination $recordDeliveryPath -Force

# ------------------------- 11. 归档到 dist（唯一可发布位置）
# 约定：最终产物只认 dist\installer\ 顶层与 dist\release\；OutRoot 允许是临时目录，
# 但构建成功后必须归档，且临时目录里的证据不删（失败时留给排障）。
$distRoot = Join-Path $RepoRoot "dist"
$distInstaller = Join-Path $distRoot "installer"
$distRelease = Join-Path $distRoot "release"
if ((Resolve-Path $OutRoot).Path -ne (Resolve-Path $distRoot).Path) {
    Write-Step "归档最终产物到 dist\installer 与 dist\release"
    New-Item -ItemType Directory -Force -Path $distInstaller, $distRelease | Out-Null
    $distSums = Join-Path $distInstaller "SHA256SUMS.txt"
    if (Test-Path $distSums) {
        $prevCandidates = Get-Content -LiteralPath $distSums -ErrorAction SilentlyContinue |
            ForEach-Object { ($_ -split '\s+')[-1] } |
            Where-Object { $_ -match '^LiuHao-Setup-(?<v>\d+\.\d+\.\d+)\.exe$' }
        foreach ($name in $prevCandidates) {
            $v = [regex]::Match($name, '^LiuHao-Setup-(?<v>\d+\.\d+\.\d+)\.exe$').Groups['v'].Value
            $keep = Join-Path $distInstaller "SHA256SUMS-$v.txt"
            if ($v -ne $Version -and -not (Test-Path $keep)) { Copy-Item -LiteralPath $distSums -Destination $keep -Force }
        }
    }
    Copy-Item -LiteralPath $setupExe -Destination $distInstaller -Force
    Copy-Item -LiteralPath $sums -Destination $distSums -Force
    Copy-Item -LiteralPath $sums -Destination (Join-Path $distInstaller "SHA256SUMS-$Version.txt") -Force
    Copy-Item -LiteralPath $recordPath -Destination (Join-Path $distInstaller "FREEZE-RECORD.json") -Force
    Copy-Item -LiteralPath $recordPath -Destination (Join-Path $distInstaller "FREEZE-RECORD-$Version.json") -Force
    if ($zipHash) {
        Copy-Item -LiteralPath $releaseZip -Destination $distRelease -Force
        foreach ($doc in @("LIUHAO_RELEASE.json", "dev-path-report.txt")) {
            $srcDoc = Join-Path $OutRoot "release\$doc"
            if (Test-Path $srcDoc) { Copy-Item -LiteralPath $srcDoc -Destination $distRelease -Force }
        }
    }
    Add-Check "产物归档到 dist" "PASS" ("{0} / {1}" -f $distInstaller, $distRelease)
    $record.checks = @($script:Checks | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; Status = $_.Status; Detail = $_.Detail }
    })
    $record | ConvertTo-Json -Depth 6 | Set-Content -Path $recordPath -Encoding UTF8
    Copy-Item -LiteralPath $recordPath -Destination (Join-Path $distInstaller "FREEZE-RECORD.json") -Force
    Copy-Item -LiteralPath $recordPath -Destination (Join-Path $distInstaller "FREEZE-RECORD-$Version.json") -Force
}

# ---------------------- 11.1 dist 同名产物冲突：同名不同哈希即失败
# 历史事实：dist\freeze-0.1.2\installer\ 与 dist\installer\ 可以各躺一份
# LiuHao-Setup-0.1.2.exe，内容不同，没有任何检查吭声。谁是要发的那份只能靠猜。
# 这里不看版本号，只看「同名文件是否唯一」：多一份不同字节的同名 exe 就失败。
Write-Step "dist 同名产物冲突检查"
$distSetupExes = @(Get-ChildItem (Join-Path $RepoRoot "dist") -Recurse -Filter "LiuHao-Setup-*.exe" -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\_archive\\' })
$sameNameProblems = @()
foreach ($group in ($distSetupExes | Group-Object { $_.Name.ToLower() })) {
    $hashes = @($group.Group | ForEach-Object {
        (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
    } | Sort-Object -Unique)
    if ($hashes.Count -gt 1) {
        $detailParts = foreach ($f in $group.Group) {
            $fh = (Get-FileHash -Algorithm SHA256 -LiteralPath $f.FullName).Hash
            "{0}（{1} 字节 / {2}…{3}）" -f $f.FullName.Replace($RepoRoot + '\', ''), $f.Length, $fh.Substring(0, 8), $fh.Substring(56)
        }
        $sameNameProblems += ("{0} 存在 {1} 个不同哈希：{2}" -f $group.Name, $hashes.Count, ($detailParts -join "；"))
    }
}
if ($sameNameProblems.Count -gt 0) {
    $sameNameProblems | ForEach-Object { Write-Host "  !! $_" -ForegroundColor Red }
    Add-Check "dist 同名产物唯一" "FAIL" (($sameNameProblems -join " | ") + " → 把非发布副本移到 dist\_archive\ 或改名（文件名带哈希）后重跑，别让「哪份要发」靠猜")
} else {
    Add-Check "dist 同名产物唯一" "PASS" ("{0} 个非归档 LiuHao-Setup-*.exe，同名均只有 1 个哈希" -f $distSetupExes.Count)
}

# 本门禁在记录落盘之后才跑，把结果补写回记录，避免「记录里的检查项」与实际门禁脱节。
if ($record -and (Test-Path $recordPath)) {
    $record.checks = @($script:Checks | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; Status = $_.Status; Detail = $_.Detail }
    })
    $record | ConvertTo-Json -Depth 6 | Set-Content -Path $recordPath -Encoding UTF8
    Copy-Item -LiteralPath $recordPath -Destination (Join-Path $distInstaller "FREEZE-RECORD.json") -Force
    Copy-Item -LiteralPath $recordPath -Destination (Join-Path $distInstaller "FREEZE-RECORD-$Version.json") -Force
}

# ------------------- 12. 发布门禁：数值来源唯一 + 发布页数值一致（硬失败）
# 规矩不再靠人记：这两条不过，绝不打印「冻结打包完成」。
Write-Step "发布门禁：数值来源唯一性与发布页数值一致性"
$publishGuard = Join-Path $RepoRoot "scripts\release\verify_publish_source.py"
$pageChecker = Join-Path $RepoRoot "scripts\verify_release_page_values.py"
if (-not (Test-Path $publishGuard)) {
    Add-Check "发布来源唯一性" "FAIL" "缺少 $publishGuard（发布门禁补丁未落地）"
}
if (-not (Test-Path $pageChecker)) {
    Add-Check "发布页数值一致性" "FAIL" "缺少 $pageChecker（补丁 0006 未落地）"
}
if (-not $SkipReleasePage -and (-not $ReleasePageFile -or -not (Test-Path $ReleasePageFile))) {
    Add-Check "发布页数值一致性" "FAIL" "未提供有效的 -ReleasePageFile（发布页数值未核对，拒绝打成功标记）"
}

# 12.1 来源唯一性：必须 dist\installer 顶层；同名 exe 不得有第二个不同哈希；
# SHA256SUMS 首行 / FREEZE-RECORD / 实际 exe 三处一致。
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $guardOut = & $PythonExe $publishGuard --repo-root $RepoRoot --installer-dir $distInstaller --expected-version $Version --require-record --payload-dir $payloadApp 2>&1 | Out-String
    $guardCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevEap
}
$guardLines = @($guardOut.Trim() -split "`r?`n" | Where-Object { $_ -ne "" })
$guardLast = if ($guardLines.Count -gt 0) { $guardLines[-1] } else { "（无输出）" }
if ($guardCode -eq 0) {
    Add-Check "发布来源唯一性" "PASS" $guardLast
} else {
    Write-Host $guardOut.TrimEnd()
    Add-Check "发布来源唯一性" "FAIL" ("退出码 {0}：{1}" -f $guardCode, $guardLast)
}

# 12.2 页面数值：版本/哈希/字节/文件名/data-copy 全部按 dist\installer 实测值核对。
# 跳过时记独立的 SKIP，并把状态/理由/时间写进 FREEZE-RECORD.release_page：
# 出包可以跳，发布不行——verify_publish_source.py 在 --require-record 下看到
# skipped 就必须收到换版校验的 PASS 证据，否则拒发。
if ($SkipReleasePage) {
    $skipStatement = "SKIP：页面换版在包产出之后执行，本轮按 RELEASE-PAGE-CONTENT.md §9 后置补偿；理由：" + $SkipReleasePageReason.Trim()
    Add-Check "发布页数值一致性" "SKIP" $skipStatement
    $record.release_page = [ordered]@{
        status = "skipped"
        reason = $SkipReleasePageReason.Trim()
        skipped_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    }
    $record.checks = @($script:Checks | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; Status = $_.Status; Detail = $_.Detail }
    })
    $record | ConvertTo-Json -Depth 6 | Set-Content -Path $recordPath -Encoding UTF8
    $recordTargets = @(
        $recordDeliveryPath,
        (Join-Path $distInstaller "FREEZE-RECORD.json"),
        (Join-Path $distInstaller "FREEZE-RECORD-$Version.json")
    )
    foreach ($target in $recordTargets) {
        if ([IO.Path]::GetFullPath($target) -ne [IO.Path]::GetFullPath($recordPath)) {
            Copy-Item -LiteralPath $recordPath -Destination $target -Force
        }
    }
} else {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $pageOut = & $PythonExe $pageChecker --page $ReleasePageFile --version $Version --sha256 $exeHash --size $exeBytes 2>&1 | Out-String
        $pageCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
    }
    $pageLines = @($pageOut.Trim() -split "`r?`n" | Where-Object { $_ -ne "" })
    $pageLast = if ($pageLines.Count -gt 0) { $pageLines[-1] } else { "（无输出）" }
    if ($pageCode -eq 0) {
        $pageWarns = @($pageLines | Where-Object { $_.StartsWith("[WARN]") }).Count
        Add-Check "发布页数值一致性" "PASS" ("{0}（WARN {1} 条）" -f $pageLast, $pageWarns)
    } else {
        Write-Host $pageOut.TrimEnd()
        Add-Check "发布页数值一致性" "FAIL" ("退出码 {0}：{1}" -f $pageCode, $pageLast)
    }
}

Write-Host ""
Write-Host "三要素"
Write-Host "  版本    $Version"
Write-Host "  体积    $exeSizeMb MiB（payload 见 manifest.json）"
Write-Host "  SHA256  $exeHash"
if ($zipHash) { Write-Host "升级包  $releaseZip（$zipSizeMb MiB，SHA256 $zipHash）" }
Write-Host "  提交    $sourceCommit"
Write-Host "  树      $sourceTree"
Write-Host "  合规    PASS（$complianceJson）"
Write-Host "  升级包  PASS（$zipComplianceJson）"
Write-Host "  记录    $recordPath"
# 签名说法必须与真实状态一致：历史上这里写死「安装器未签名」，签名之后仍在打印，
# 造成「哈希/说法打架」。这里改成读 Authenticode 真实读数再措辞。
$finalSig = Get-AuthenticodeSignature -LiteralPath $setupExe
if ($finalSig.Status -eq "Valid") {
    $sigNote = "安装器已签名（Status=Valid，$($finalSig.SignerCertificate.Subject)）；项目使用自签名证书，未导入信任的机器在 SmartScreen 上仍等同未签名。"
} else {
    $sigNote = "安装器签名 Status=$($finalSig.Status)（未通过 Authenticode 校验），Windows 会提示「未知发布者」。"
}
Write-Host ("说明：安装包不含模型权重、不含密钥；" + $sigNote) -ForegroundColor Yellow
exit 0
