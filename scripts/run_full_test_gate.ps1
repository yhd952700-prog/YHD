# 全量测试门禁：失败必然非零退出，且日志里的退出码不许说谎。
#
# 为什么要这个脚本：0.3.0 门禁曾用一条命令记账
#   python -m pytest tests -q 2>&1 | Out-File $log; "END ... HEAD=$(git rev-parse --short HEAD) EXITCODE=$LASTEXITCODE"
# 结果 3 failed 的那一遍在日志尾部写着 EXITCODE=0。原因不是 pytest，而是那条汇总行：
# PowerShell 会先求值字符串里的 $(git rev-parse ...) 子表达式，而它会**覆盖**
# $LASTEXITCODE（实测：exit 3 后的 $LASTEXITCODE 在 git 跑完后变成 0），于是
# 紧随其后的 $LASTEXITCODE 读到的是 git 的 0，不是 pytest 的 1。
#
# 这个脚本的三条规矩：
#   1. pytest 一结束立刻把 $LASTEXITCODE 存进 $rc，之后的所有取值/写日志都用 $rc；
#   2. 日志尾部出现 "N failed" / "N error(s)" 却拿到 rc=0，说明门禁自身坏了 —— 退出码 2，
#      不把它当成"通过"；
#   3. 无论如何 exit $rc，让调用方（CI / 出包脚本）拿到真实结论，而不是"日志一看没事"。
param(
    [int]$Parallel = 0,               # >0 时用 xdist：-n N；0 表示串行
    [string]$Path = "tests",          # 目标路径；自检/定向时传具体文件
    [string]$LogPath = "D:\cache\temp\pytest-full-gate.log",
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Continue"
$pytestArgs = @($Path, "-q")
if ($Parallel -gt 0) { $pytestArgs += @("-n", "$Parallel") }

$headBefore = (git rev-parse --short HEAD).Trim()
$started = Get-Date

New-Item -ItemType Directory -Force (Split-Path -Parent $LogPath) | Out-Null
"START $($started.ToString('yyyy-MM-dd HH:mm:ss')) HEAD=$headBefore PARALLEL=$Parallel ARGS=$($pytestArgs -join ' ')" |
    Out-File -FilePath $LogPath -Encoding utf8

& $Python -m pytest @pytestArgs 2>&1 | Out-File -FilePath $LogPath -Append -Encoding utf8
$rc = $LASTEXITCODE                    # 先存，别让后面的 git/Get-Date 把它冲掉

$headAfter = (git rev-parse --short HEAD).Trim()
$finished = Get-Date
$tail = (Get-Content $LogPath -Tail 60) -join "`n"

$lieDetected = ($rc -eq 0) -and ($tail -match '(?m)\b\d+\s+(failed|errors?)\b')
if ($lieDetected) { $rc = 2 }

"END $($finished.ToString('yyyy-MM-dd HH:mm:ss')) HEAD=$headAfter ELAPSED=$([int]($finished - $started).TotalSeconds)s EXITCODE=$rc" |
    Out-File -FilePath $LogPath -Encoding utf8 -Append
if ($headAfter -ne $headBefore) {
    "TREE-MOVED: 起跑 HEAD=$headBefore 结束 HEAD=$headAfter，这一遍结果不算发布依据" |
        Out-File -FilePath $LogPath -Encoding utf8 -Append
}
if ($lieDetected) {
    "GATE-BROKEN: 日志里有 failed/error 汇总，pytest 却返回 0 —— 按失败处理（退出码 2）" |
        Out-File -FilePath $LogPath -Encoding utf8 -Append
}

Write-Host ("EXITCODE=" + $rc + "  (HEAD " + $headBefore + " -> " + $headAfter + ")")
exit $rc
