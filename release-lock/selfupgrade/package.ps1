param(
    [string]$Repo = "D:\LiuHao-AI-OS",
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$OutRoot,
    [switch]$AllowReal,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)
$ErrorActionPreference = "Stop"
$py = $env:LIUHAO_RELEASE_PYTHON
if (-not $py) { $py = "D:\cache\venvs\liuhao-release-20261108\Scripts\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }
$args = @("-m", "src.selfupgrade", "package", "--version", $Version, "--out-root", $OutRoot)
if ($AllowReal) { $args += "--allow-real" }
if ($Rest) { $args += $Rest }
Push-Location $Repo
try { & $py @args; exit $LASTEXITCODE } finally { Pop-Location }
