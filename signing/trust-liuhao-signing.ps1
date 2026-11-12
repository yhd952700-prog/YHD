# LiuHao AI OS · 鎏灏智能中枢 —— 一键导入自签名证书信任
#
# 作用：把项目的自签名代码签名证书（LiuHao-CodeSigning.cer）导入到
#   Cert:\CurrentUser\TrustedPublisher   （让签名包显示「已验证的发布者」）
#   Cert:\CurrentUser\Root               （让证书链本身可信）
# 默认只影响当前 Windows 用户，不需要管理员权限；加 -AllUsers 才写机器级存储，
# 此时必须用管理员身份运行。
#
# 幂等：重复运行只会报告「已存在」，不会重复导入、不会报错。
#
# 诚实说明：导入信任后，「属性 → 数字签名」可以看到真实签名者；但 SmartScreen
# 仍可能因「文件信誉不足」给出提示 —— 自签名证书不会获得公开 CA 的信誉，
# 这不是证书没装好。若仍提示，请核对 SHA256 后选择「仍要运行」。

[CmdletBinding()]
param(
    [string]$CerPath = "",
    [switch]$AllUsers
)

$ErrorActionPreference = "Stop"

function Say([string]$Message, [string]$Color = "Gray") {
    Write-Host $Message -ForegroundColor $Color
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Store([string]$StorePath, [string]$Thumbprint) {
    return [bool](Get-ChildItem $StorePath -ErrorAction SilentlyContinue |
        Where-Object { ($_.Thumbprint -replace '\s', '') -eq $Thumbprint })
}

function Import-IntoStore([string]$StorePath, [string]$Thumbprint, [string]$Label) {
    if (Test-Store $StorePath $Thumbprint) {
        Say "  [已存在] $Label" "Green"
        return
    }
    Import-Certificate -FilePath $script:CerPath -CertStoreLocation $StorePath | Out-Null
    if (Test-Store $StorePath $Thumbprint) {
        Say "  [已导入] $Label" "Green"
    } else {
        throw "导入 $Label 后仍查不到该证书，导入未生效。"
    }
}

# ---------------------------------------------------------------- 1. 找证书
if (-not $CerPath) {
    $candidate = Join-Path $PSScriptRoot "LiuHao-CodeSigning.cer"
    if (Test-Path $candidate) {
        $CerPath = $candidate
    }
}
if (-not $CerPath -or -not (Test-Path -LiteralPath $CerPath)) {
    Say "找不到公钥证书 LiuHao-CodeSigning.cer。" "Red"
    Say "  查找位置：$PSScriptRoot" "Red"
    Say "  请把本脚本与 LiuHao-CodeSigning.cer 放在同一目录，或指定 -CerPath。" "Yellow"
    exit 1
}
$script:CerPath = (Resolve-Path -LiteralPath $CerPath).Path

try {
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($script:CerPath)
} catch {
    Say "无法读取证书：$($_.Exception.Message)" "Red"
    Say "  文件  $script:CerPath" "Red"
    exit 1
}
$thumbprint = ($cert.Thumbprint -replace '\s', '').ToUpperInvariant()

Say ""
Say "==> 准备导入自签名证书信任" "Cyan"
Say "  证书文件  $script:CerPath" "Gray"
Say "  主体名    $($cert.Subject)" "Gray"
Say "  指纹（导入前）  $thumbprint" "Gray"
Say "  有效期至  $($cert.NotAfter.ToString('yyyy-MM-dd'))" "Gray"

if (-not $AllUsers) {
    Say "  作用范围  当前用户（不需要管理员权限）" "Gray"
} else {
    if (-not (Assert-Admin)) {
        Say "" "Gray"
        Say "已指定 -AllUsers，但当前不是管理员。" "Red"
        Say "请右键本脚本（或 信任签名证书.cmd），选择「以管理员身份运行」，或去掉 -AllUsers 只信任当前用户。" "Yellow"
        exit 2
    }
    Say "  作用范围  所有用户（机器级存储，需要管理员）" "Gray"
}

# ---------------------------------------------------------------- 2. 导入
$failed = $false
try {
    Say ""
    Say "==> 导入到证书存储" "Cyan"
    Import-IntoStore "Cert:\CurrentUser\TrustedPublisher" $thumbprint "CurrentUser\TrustedPublisher（受信任的发布者）"
    Import-IntoStore "Cert:\CurrentUser\Root" $thumbprint "CurrentUser\Root（受信任的根证书颁发机构）"
    if ($AllUsers) {
        Import-IntoStore "Cert:\LocalMachine\TrustedPublisher" $thumbprint "LocalMachine\TrustedPublisher（所有用户）"
        Import-IntoStore "Cert:\LocalMachine\Root" $thumbprint "LocalMachine\Root（所有用户）"
    }
} catch {
    $failed = $true
    Say ""
    Say "导入失败：$($_.Exception.Message)" "Red"
    Say "可能原因：证书文件损坏、存储被策略锁定、或缺少管理员权限。" "Yellow"
    Say "修复建议：确认 .cer 来自本项目的 signing 目录；以管理员身份重试；仍失败请把本窗口输出发给维护者。" "Yellow"
}

# ---------------------------------------------------------------- 3. 复查
Say ""
Say "==> 复查结果" "Cyan"
$userPublisher = Test-Store "Cert:\CurrentUser\TrustedPublisher" $thumbprint
$userRoot = Test-Store "Cert:\CurrentUser\Root" $thumbprint
Say "  CurrentUser\TrustedPublisher  $(if ($userPublisher) { '已信任' } else { '未信任' })" $(if ($userPublisher) { "Green" } else { "Red" })
Say "  CurrentUser\Root              $(if ($userRoot) { '已信任' } else { '未信任' })" $(if ($userRoot) { "Green" } else { "Red" })
if ($AllUsers) {
    $machinePublisher = Test-Store "Cert:\LocalMachine\TrustedPublisher" $thumbprint
    $machineRoot = Test-Store "Cert:\LocalMachine\Root" $thumbprint
    Say "  LocalMachine\TrustedPublisher $(if ($machinePublisher) { '已信任' } else { '未信任' })" $(if ($machinePublisher) { "Green" } else { "Red" })
    Say "  LocalMachine\Root             $(if ($machineRoot) { '已信任' } else { '未信任' })" $(if ($machineRoot) { "Green" } else { "Red" })
}
Say "  指纹（导入后）  $thumbprint" "Gray"

if ($failed -or -not $userPublisher -or -not $userRoot) {
    Say ""
    Say "本次导入未完成，请按上面的原因处理后重试。" "Red"
    exit 1
}

# ---------------------------------------------------------------- 4. 验证提示
Say ""
Say "==> 如何验证签名" "Cyan"
Say "  方法一：右键安装包 → 属性 → 数字签名 → 选中签名条目 →「详细信息」" "Gray"
Say "  方法二：PowerShell 一行命令（把路径替换成实际文件）：" "Gray"
Say "    Get-AuthenticodeSignature `"D:\LiuHao-Setup-0.1.1.exe`" | Format-List Status, SignerCertificate" "Gray"
Say ""
Say "完成：本机已信任该签名证书。" "Green"
Say "提醒：SmartScreen 仍可能因文件信誉不足给出提示 —— 自签名证书没有公开 CA 的信誉积累，这是正常现象；" "Yellow"
Say "      请先核对发布页的 SHA256，再选择「仍要运行」。" "Yellow"
exit 0
