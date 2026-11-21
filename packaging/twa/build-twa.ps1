# Jarvis 安卓 App（TWA）一键打包脚本 —— 在 Hughes 的 Windows 电脑上运行。
#
# 用法：在本目录（packaging\twa）打开 PowerShell，执行
#     powershell -ExecutionPolicy Bypass -File .\build-twa.ps1
#
# 它做的事：检查 Node → 检查 twa-manifest.json 里的 host 是否已填真实地址 →
# 首次先用 Bubblewrap 生成安卓工程，再正式打包出 APK（侧载安装用）与
# AAB（将来上架 Google Play 用）。签名密钥 android.keystore 若不存在，
# Bubblewrap 会在打包时现场引导生成——密码由你本人在提示框输入，
# 本脚本不记录、不保存任何密码。完整说明见同目录 README.md。
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Fail($msg) {
    Write-Host ""
    Write-Host "停下：$msg" -ForegroundColor Red
    exit 1
}

Write-Host "== Jarvis 安卓 App（TWA）打包 ==" -ForegroundColor Cyan

# 1) Node.js（Bubblewrap 靠它运行）
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Fail "没找到 Node.js。请先到 https://nodejs.org 装 LTS 版，装完重开 PowerShell 再跑本脚本。"
}
$nodeMajor = [int]((node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 18) {
    Fail "Node.js 版本太旧（当前 $(node --version)，需要 18 以上）。请到 https://nodejs.org 升级 LTS 版。"
}
Write-Host "Node.js $(node --version) OK"

# 2) host 必须是真实 HTTPS 地址：还是占位地址就先改
$manifestPath = Join-Path $PSScriptRoot 'twa-manifest.json'
$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
if ($manifest.host -like '*example.com*' -or [string]::IsNullOrWhiteSpace($manifest.host)) {
    Fail "twa-manifest.json 里的 host 还是占位地址（$($manifest.host)）。先按 README.md 第 1 步准备好贾维斯的 HTTPS 入口，把 host 与 webManifestUrl 改成真实地址再跑。"
}
Write-Host "打包目标地址：https://$($manifest.host) OK"

# 3) 首次：生成安卓工程（会从上面的地址下载图标，地址必须能打开）
if (-not (Test-Path (Join-Path $PSScriptRoot 'build.gradle'))) {
    Write-Host "首次打包：正在生成安卓工程（Bubblewrap 会顺带准备它自己的 JDK 与安卓工具，第一次会慢一些，按提示确认即可）…"
    npx -y @bubblewrap/cli update --skipVersionUpgrade
    if ($LASTEXITCODE -ne 0) { Fail "安卓工程生成失败（上面有原因）。最常见的是目标地址打不开或图标下载失败，先在浏览器里确认 https://$($manifest.host)/manifest.webmanifest 能打开。" }
}

# 4) 正式打包
Write-Host "正在打包（若 android.keystore 不存在，会先引导你生成签名密钥：姓名/单位随便填真信息，密码务必记牢并按 README.md 保管）…"
npx -y @bubblewrap/cli build
if ($LASTEXITCODE -ne 0) { Fail "打包失败（上面有原因）。把报错原样发给 Muse 看，不要删任何文件重来。" }

Write-Host ""
Write-Host "打包完成，产物在本目录：" -ForegroundColor Green
Write-Host "  app-release-signed.apk   —— 传到安卓手机点开即可安装（侧载）"
Write-Host "  app-release-bundle.aab   —— 将来上架 Google Play 时用"
Write-Host ""
Write-Host "下一步（重要）：按 README.md 第 4 步把签名密钥备份好，再按第 5 步取指纹、部署 assetlinks.json，否则 App 里会留着浏览器地址栏、不算真 App。"
