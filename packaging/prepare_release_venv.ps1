# 创建/更新一个严格按 requirements.txt 安装的独立发布 venv。
#
# 目的：随包依赖必须等于受审锁。仓库里的 .venv 同时被开发/测试使用，版本
# 常与锁漂移（例如 protobuf 5.x vs 6.x），不能直接拿它组装 payload。
#
# 用法：
#   powershell -File packaging\prepare_release_venv.ps1
#   powershell -File packaging\prepare_release_venv.ps1 -EnvDir D:\cache\venvs\liuhao-release
#
# 之后构建：
#   powershell -File packaging\build_windows_installer.ps1 `
#       -PythonExe <EnvDir>\Scripts\python.exe
#
# 注意：本脚本只建环境，不构建安装包，也不改动 dist\。

[CmdletBinding()]
param(
    [string]$EnvDir = "",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Requirements = Join-Path $Root "requirements.txt"

if (-not (Test-Path $Requirements)) {
    throw "缺少 requirements.txt：$Requirements"
}
if (-not $EnvDir) {
    $EnvDir = Join-Path $env:LOCALAPPDATA "LiuHao\release-venv"
}

# 与仓库 .venv 使用同一个基础解释器，保证离线运行时版本一致。
function Resolve-BasePython {
    $pyvenv = Join-Path $Root ".venv\pyvenv.cfg"
    if (Test-Path $pyvenv) {
        $match = Select-String -Path $pyvenv -Pattern '^home\s*=\s*(.+)$' | Select-Object -First 1
        if ($match) {
            $candidate = Join-Path $match.Matches[0].Groups[1].Value.Trim() "python.exe"
            if (Test-Path $candidate) { return $candidate }
        }
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "找不到 Python 3.12：请先准备仓库 .venv 或在 PATH 中提供 python.exe。"
}

$BasePython = Resolve-BasePython
$EnvPython = Join-Path $EnvDir "Scripts\python.exe"

if ($Recreate -and (Test-Path $EnvDir)) {
    Write-Host "==> 删除旧环境 $EnvDir" -ForegroundColor Yellow
    Remove-Item -Recurse -Force $EnvDir
}
if (-not (Test-Path $EnvPython)) {
    Write-Host "==> 创建 venv：$EnvDir" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force (Split-Path -Parent $EnvDir) | Out-Null
    & $BasePython -m venv $EnvDir
    if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败（exit $LASTEXITCODE）" }
}

Write-Host "==> 按 requirements.txt 安装（严格等于锁）" -ForegroundColor Cyan
& $EnvPython -m pip install --disable-pip-version-check --no-cache-dir -r $Requirements
if ($LASTEXITCODE -ne 0) {
    throw "按锁安装失败（exit $LASTEXITCODE）。不要降级绕过：先检查冲突链。"
}

Write-Host "==> pip check" -ForegroundColor Cyan
& $EnvPython -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "依赖冲突（见上面的 pip check 输出）。不要降级绕过：先检查冲突链。"
}

Write-Host ""
Write-Host "发布用 Python：$EnvPython" -ForegroundColor Green
Write-Host "构建时传：-PythonExe `"$EnvPython`""
