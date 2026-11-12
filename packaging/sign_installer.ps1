# Jarvis · 贾维斯 —— 给安装包做自签名（Set-AuthenticodeSignature）
#
# 不依赖 signtool / Windows SDK，只用 PowerShell 内置的签名与证书接口。
# 证书来源优先级：
#   1) signing\signing.json 记录的指纹 → Cert:\CurrentUser\My
#   2) signing\private\liuhao-codesign.pfx（口令由 DPAPI 保护，或环境变量
#      LIUHAO_SIGNING_PFX_PASSWORD）
# 两者都没有时：明确打印「本次未签名」，退出码 3（绝不静默跳过）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File packaging\sign_installer.ps1
#   ... -InstallerPath dist\installer\Jarvis-Setup-0.1.1.exe
#   ... -Check      # 只判断证书是否可用：0=可用，3=不可用
#
# 退出码：
#   0  已签名，且 Get-AuthenticodeSignature 的 Status 为 Valid
#   1  出错（找不到 exe / 证书无私钥 / 签名本身失败）
#   2  已签名，但本机尚未信任该自签名证书（Status 非 Valid）
#   3  本次未签名（没有可用证书）

[CmdletBinding()]
param(
    [string]$InstallerPath = "",
    [string]$SigningDir = "",
    [switch]$Check,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $SigningDir) { $SigningDir = Join-Path $Root "signing" }

$ConfigPath = Join-Path $SigningDir "signing.json"
$PfxPath = Join-Path $SigningDir "private\liuhao-codesign.pfx"
$PwdBlobPath = Join-Path $SigningDir "private\liuhao-codesign.pfx.dpapi"
$DpapiEntropy = [System.Text.Encoding]::UTF8.GetBytes("liuhao-code-signing-pfx-v1")

function Say([string]$Message, [string]$Color = "Gray") {
    if (-not $Quiet) { Write-Host $Message -ForegroundColor $Color }
}

function Get-PfxPassword {
    if (Test-Path $PwdBlobPath) {
        Add-Type -AssemblyName System.Security | Out-Null
        $blob = [System.IO.File]::ReadAllBytes($PwdBlobPath)
        $plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
            $blob, $DpapiEntropy,
            [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
        return [System.Text.Encoding]::UTF8.GetString($plain)
    }
    if ($env:LIUHAO_SIGNING_PFX_PASSWORD) {
        return $env:LIUHAO_SIGNING_PFX_PASSWORD
    }
    throw "私钥口令不可用：$PwdBlobPath 不存在，且未设置 LIUHAO_SIGNING_PFX_PASSWORD。"
}

function Resolve-Cert {
    $thumb = ""
    if (Test-Path $ConfigPath) {
        try {
            $cfg = Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json
            $thumb = $cfg.thumbprint
        } catch {
            Say "signing.json 无法解析：$($_.Exception.Message)" "Yellow"
        }
    }
    if ($thumb) {
        $clean = ($thumb -replace '\s', '')
        $cert = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $clean) -ErrorAction SilentlyContinue
        if ($cert -and $cert.HasPrivateKey) { return $cert }
    }
    if (Test-Path $PfxPath) {
        $plain = Get-PfxPassword
        $secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
        $cert = Import-PfxCertificate -FilePath $PfxPath -CertStoreLocation "Cert:\CurrentUser\My" -Password $secure
        Remove-Variable plain, secure
        if ($cert -and $cert.HasPrivateKey) { return $cert }
    }
    return $null
}

$cert = Resolve-Cert
if (-not $cert) {
    if ($Check) { exit 3 }
    Say "" "Gray"
    Say "本次未签名：未找到可用的代码签名证书。" "Yellow"
    Say "  期望配置  $ConfigPath" "Yellow"
    Say "  期望私钥  $PfxPath" "Yellow"
    Say "  生成方式  powershell -ExecutionPolicy Bypass -File packaging\new_signing_cert.ps1" "Yellow"
    exit 3
}

if ($Check) {
    Say "证书可用：$($cert.Subject)" "Green"
    Say "  指纹  $($cert.Thumbprint)" "Green"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $found = Get-ChildItem (Join-Path $Root "dist\installer") -Filter "Jarvis-Setup-*.exe" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $found) {
        Say "找不到待签名安装包：dist\installer\Jarvis-Setup-*.exe 不存在。" "Red"
        exit 1
    }
    $InstallerPath = $found.FullName
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    Say "找不到待签名文件：$InstallerPath" "Red"
    exit 1
}
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path

Say "" "Gray"
Say "==> 签名安装包" "Cyan"
Say "  文件    $InstallerPath" "Gray"
Say "  证书    $($cert.Subject)" "Gray"
Say "  指纹    $($cert.Thumbprint)" "Gray"

# 自签名不附带 RFC3161 时间戳：时间戳服务器只对公开 CA 签发的证书有意义，
# 且需要联网。证书有效期 10 年，到期后重新生成并重新分发即可。
try {
    $signature = Set-AuthenticodeSignature -FilePath $InstallerPath -Certificate $cert -HashAlgorithm SHA256
} catch {
    Say "签名失败：$($_.Exception.Message)" "Red"
    exit 1
}

# 重新读取磁盘上的签名结果，而不是相信上一步的返回值。
$verified = Get-AuthenticodeSignature -FilePath $InstallerPath
$now = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")

Say "" "Gray"
Say "==> 签名结果（Get-AuthenticodeSignature）" "Cyan"
Say "  Status        $($verified.Status)" $(if ($verified.Status -eq "Valid") { "Green" } else { "Yellow" })
Say "  StatusMessage $($verified.StatusMessage)" "Gray"
Say "  签名者主体    $($verified.SignerCertificate.Subject)" "Gray"
Say "  指纹          $($verified.SignerCertificate.Thumbprint)" "Gray"
Say "  签名时间      $now" "Gray"
Say "  时间戳        $(if ($verified.TimeStamperCertificate) { '有' } else { '无（自签名未加 RFC3161 时间戳）' })" "Gray"

if ($verified.Status -ne "Valid") {
    Say "" "Gray"
    Say "注意：签名已写入，但 Status 不是 Valid。常见原因是本机尚未信任该自签名证书。" "Yellow"
    Say "      以管理员或普通用户运行 signing\信任签名证书.cmd 后再复查。" "Yellow"
    exit 2
}

Say "" "Gray"
Say "安装包签名有效（Status=Valid）。" "Green"
exit 0
