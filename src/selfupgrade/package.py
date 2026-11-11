"""出包：在 staging worktree 里调用盖章的 ``package-frozen.ps1``。

本轮不跑真实出包（Boss 会在所有线落完后统一发起）；这里只提供能力，
``dry-run`` 默认开启，真实构建必须显式 ``allow_real=True``。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .gates import resolve_python
from .install import resolve_pwsh
from .staging import prepare_staging


def run_package(
    repo: Path,
    version: str,
    out_root: Path,
    *,
    commit: str = "HEAD",
    staging_root: Path,
    dry_run: bool = True,
    allow_real: bool = False,
    skip_release_page: bool = True,
) -> int:
    if not dry_run and not allow_real:
        raise RuntimeError("真实出包需要 allow_real=True（本轮 Boss 未放行）")

    staging = prepare_staging(Path(repo), commit, Path(repo) / "release-lock", Path(staging_root))
    script = staging / "scripts" / "release" / "package-frozen.ps1"
    args = [
        resolve_pwsh(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-RepoRoot",
        str(staging),
        "-Version",
        version,
        "-OutRoot",
        str(out_root),
        "-PythonExe",
        resolve_python(),
        "-ReleaseGateDir",
        str(staging / "dist" / "release-gate"),
    ]
    if dry_run:
        args.append("-DryRun")
    if skip_release_page:
        args.extend(["-SkipReleasePage", "-SkipReleasePageReason", "自升级测试出包：发布页换版在包产出之后"])
    proc = subprocess.run(args, cwd=str(staging), capture_output=True)
    return proc.returncode
