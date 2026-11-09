# LiuHao AI OS · 鎏灏智能中枢 —— 自签名代码签名证书（生成 / 复用 / 导出）
#
# 设计要点：
# * 证书默认只生成一次，之后所有构建复用同一张（同一指纹）。换证书会让所有
#   已安装信任脚本的用户必须重新信任，所以除非显式 -Force，绝不重新生成。
# * 私钥导出到 signing\private\*.pfx，口令用 Windows DPAPI（当前用户作用域）
#   加密后存 signing\private\*.pfx.dpapi；两者都在 .gitignore 内，绝不入库。
# * 公钥证书导出为 signing\LiuHao-CodeSigning.cer（可提交、可随信任脚本分发）。
# * 指纹与有效期写入 signing\signing.json（可提交），供打包脚本与用户核对。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File packaging\new_signing_cert.ps1
#   powershell ... -Force     # 仅当明确要换证书时才用（所有用户需重新信任）

[CmdletBinding()]
param(
    [switch]$Force,
    [string]$SigningDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $SigningDir) { $SigningDir = Join-Path $Root "signing" }

$PrivateDir = Join-Path $SigningDir "private"
$ConfigPath = Join-Path $SigningDir "signing.json"
$PfxPath = Join-Path $PrivateDir "liuhao-codesign.pfx"
$PwdBlobPath = Join-Path $PrivateDir "liuhao-codesign.pfx.dpapi"
$CerPath = Join-Path $SigningDir "LiuHao-CodeSigning.cer"
# 口令 DPAPI 的用途熵：与项目 src\security\encryption.py 的 purpose 思路一致，
# 避免同一份 DPAPI 密文被别的用途挪用。
$DpapiEntropy = [System.Text.Encoding]::UTF8.GetBytes("liuhao-code-signing-pfx-v1")

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-BrandSubject {
    $brandPath = Join-Path $Root "src\brand.json"
    if (-not (Test-Path $brandPath)) {
        throw "找不到品牌配置：$brandPath（证书主体名必须取自项目既有品牌名）。"
    }
    $brand = Get-Content -Raw -Encoding UTF8 -LiteralPath $brandPath | ConvertFrom-Json
    $nameZh = $brand.name_zh
    $nameEn = $brand.name_en
    if (-not $nameZh) { throw "src\brand.json 缺少 name_zh，无法确定证书主体名。" }
    # CN 里不放逗号，避免 DN 转义问题；中英文品牌名都保留，便于属性里辨认。
    return "CN=$nameZh, O=$nameEn, C=CN"
}

function Protect-Password([string]$Plain) {
    Add-Type -AssemblyName System.Security | Out-Null
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Plain)
    $blob = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes, $DpapiEntropy,
        [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    [System.IO.File]::WriteAllBytes($PwdBlobPath, $blob)
}

function Unprotect-Password {
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

function Get-RandomPassword {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 24
    $rng.GetBytes($bytes)
    return (-join ($bytes | ForEach-Object { $_.ToString("x2") }))
}

function Restrict-PrivateDir {
    # 失败不致命：文件仍在 .gitignore 内且口令由 DPAPI 保护；但要把原因说出来。
    try {
        & icacls.exe $PrivateDir /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)(R,W)" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "收紧私钥目录权限失败（icacls exit $LASTEXITCODE）；请手工确认 $PrivateDir 仅当前用户可读。"
        }
    } catch {
        Write-Warning "收紧私钥目录权限失败：$($_.Exception.Message)"
    }
}

function Read-Config {
    if (-not (Test-Path $ConfigPath)) { return $null }
    try {
        return (Get-Content -Raw -Encoding UTF8 -LiteralPath $ConfigPath | ConvertFrom-Json)
    } catch {
        Write-Warning "signing.json 无法解析（$($_.Exception.Message)），按无配置处理。"
        return $null
    }
}

function Get-CertFromStore([string]$Thumbprint) {
    if (-not $Thumbprint) { return $null }
    $clean = ($Thumbprint -replace '\s', '')
    $item = Get-Item -LiteralPath ("Cert:\CurrentUser\My\" + $clean) -ErrorAction SilentlyContinue
    return $item
}

function Import-Pfx {
    if (-not (Test-Path $PfxPath)) { return $null }
    $plain = Unprotect-Password
    $secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
    return Import-PfxCertificate -FilePath $PfxPath -CertStoreLocation "Cert:\CurrentUser\My" -Password $secure
}

function Export-PublicCert($Cert) {
    Export-Certificate -Cert $Cert -FilePath $CerPath -Type CERT -Force | Out-Null
}

function Write-Config($Cert, [string]$CreatedAt) {
    $config = [ordered]@{
        subject          = $Cert.Subject
        thumbprint       = ($Cert.Thumbprint -replace '\s', '').ToUpperInvariant()
        not_after        = $Cert.NotAfter.ToString("yyyy-MM-ddTHH:mm:ssK")
        created_at       = $CreatedAt
        pfx_path         = "signing/private/liuhao-codesign.pfx"
        pfx_password     = "signing/private/liuhao-codesign.pfx.dpapi (DPAPI CurrentUser)"
        cer_path         = "signing/LiuHao-CodeSigning.cer"
        trust_script     = "signing/trust-liuhao-signing.ps1"
        note             = "自签名证书，非公开权威 CA；未安装信任脚本前 Windows 仍可能提示未知发布者。"
    }
    $json = $config | ConvertTo-Json -Depth 4
    $utf8Bom = New-Object System.Text.UTF8Encoding($true)
    [System.IO.File]::WriteAllText($ConfigPath, $json, $utf8Bom)
}

New-Item -ItemType Directory -Path $SigningDir -Force | Out-Null

if ($Force) {
    Write-Warning "已指定 -Force：将生成新证书并更换指纹；所有已导入信任的用户都需要重新运行信任脚本。"
}

# ---------------------------------------------------------------- 1. 复用检查
$reused = $false
$cert = $null
$config = Read-Config
if (-not $Force) {
    if ($config -and $config.thumbprint) {
        $cert = Get-CertFromStore $config.thumbprint
        if ($cert -and $cert.HasPrivateKey) {
            $reused = $true
            Write-Step "复用已有证书（指纹 $($cert.Thumbprint)）"
        } elseif ($cert -and -not $cert.HasPrivateKey) {
            Write-Warning "证书存储里有同指纹证书但没有私钥，尝试从 pfx 重新导入。"
            $cert = $null
        }
    }
    if (-not $cert) {
        $imported = Import-Pfx
        if ($imported) {
            $cert = $imported
            $reused = $true
            Write-Step "从 signing\private\liuhao-codesign.pfx 恢复证书（指纹 $($cert.Thumbprint)）"
        }
    }
}

# ---------------------------------------------------------------- 2. 生成新证书
if (-not $cert) {
    if ($Force) {
        Write-Step "生成新的自签名代码签名证书（-Force）"
    } else {
        Write-Step "未找到可复用证书，生成新的自签名代码签名证书"
    }
    $subject = Get-BrandSubject
    Write-Host "  主体名  $subject"
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $subject `
        -FriendlyName "LiuHao AI OS 代码签名（自签名，10 年）" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -KeyExportPolicy Exportable `
        -NotAfter (Get-Date).AddYears(10)

    if (-not $cert -or -not $cert.HasPrivateKey) {
        throw "证书生成失败：未获得带私钥的证书对象。"
    }

    New-Item -ItemType Directory -Path $PrivateDir -Force | Out-Null
    if (Test-Path $PfxPath) { Remove-Item -LiteralPath $PfxPath -Force }

    $plain = Get-RandomPassword
    Protect-Password $plain
    $secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
    Export-PfxCertificate -Cert $cert -FilePath $PfxPath -Password $secure | Out-Null
    if (-not (Test-Path $PfxPath)) {
        throw "私钥导出失败：$PfxPath 未生成。"
    }
    Remove-Variable plain, secure
    Restrict-PrivateDir
}

# ---------------------------------------------------------------- 3. 导出与记录
if (-not (Test-Path $PfxPath)) {
    # 复用了存储里的证书，但私钥文件丢失：补导出，保证换机/重建可恢复。
    Write-Step "补导出私钥到 signing\private\liuhao-codesign.pfx"
    New-Item -ItemType Directory -Path $PrivateDir -Force | Out-Null
    $plain = Get-RandomPassword
    Protect-Password $plain
    $secure = ConvertTo-SecureString -String $plain -AsPlainText -Force
    Export-PfxCertificate -Cert $cert -FilePath $PfxPath -Password $secure | Out-Null
    Remove-Variable plain, secure
    Restrict-PrivateDir
}

Export-PublicCert $cert
# created_at 只在首次生成时记录，复用时不改写，便于追溯「证书是哪天建的」。
$createdAt = if ($config -and $config.created_at) { $config.created_at } else { (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK") }
Write-Config $cert $createdAt

# ---------------------------------------------------------------- 4. 结果
Write-Step "证书就绪"
Write-Host "  主体名    $($cert.Subject)"
Write-Host "  指纹      $($cert.Thumbprint)"
Write-Host "  有效期至  $($cert.NotAfter.ToString('yyyy-MM-dd'))"
Write-Host "  公钥证书  $CerPath"
Write-Host "  私钥      $PfxPath（口令由 DPAPI 保护，均在 .gitignore 内，不入库）"
Write-Host "  配置文件  $ConfigPath"
Write-Host ""
Write-Host "下一步：运行 packaging\sign_installer.ps1 对安装包签名；" -ForegroundColor Yellow
Write-Host "        分发前让用户运行 signing\信任签名证书.cmd 导入信任。" -ForegroundColor Yellow

if ($reused) {
    Write-Host "本次为复用，指纹保持不变。" -ForegroundColor Green
}
exit 0
