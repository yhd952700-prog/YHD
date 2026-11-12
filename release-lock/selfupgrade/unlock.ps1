param(
    [string]$Repo = "D:\LiuHao-AI-OS",
    [Parameter(Mandatory = $true)][string]$Reason
)
$ErrorActionPreference = "Stop"
$py = $env:LIUHAO_RELEASE_PYTHON
if (-not $py) { $py = "D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }
Push-Location $Repo
try { & $py -m src.selfupgrade unlock --approve --reason $Reason; exit $LASTEXITCODE } finally { Pop-Location }
