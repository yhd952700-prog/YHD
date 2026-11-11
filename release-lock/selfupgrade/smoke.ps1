param(
    [string]$Repo = "D:\LiuHao-AI-OS",
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$InstallDir = "D:\LiuHaoTest",
    [string]$DataDir = "D:\LiuHaoTestData",
    [int]$Port = 18080
)
$ErrorActionPreference = "Stop"
$py = $env:LIUHAO_RELEASE_PYTHON
if (-not $py) { $py = "D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }
Push-Location $Repo
try {
    & $py -m src.selfupgrade run --installer $Installer --version $Version --approve `
        --install-dir $InstallDir --data-dir $DataDir --port $Port
    exit $LASTEXITCODE
} finally { Pop-Location }
