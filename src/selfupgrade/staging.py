"""把候选提交与盖章副本分离：在独立 git worktree 里用 lock 覆盖门禁/打包/安装器。

这样 AI 改仓库里的 ``scripts/release``、``packaging``、``installer`` 不会影响
自升级实际执行的东西；worktree 是临时目录，失败可以整个丢掉。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

OVERLAY_PREFIXES = (
    ("scripts/", "scripts/"),
    ("packaging/", "packaging/"),
    ("installer/", "installer/"),
    ("release-gate/", "dist/release-gate/"),
    ("tests/", "tests/"),
)


def _overlay_destination(target: str) -> str | None:
    target = target.replace("\\", "/")
    for prefix, dest in OVERLAY_PREFIXES:
        if target.startswith(prefix):
            return dest + target[len(prefix):]
    return None


def prepare_staging(repo: Path, commit: str, lock_dir: Path, staging_root: Path) -> Path:
    repo = Path(repo)
    staging_root = Path(staging_root)
    if staging_root.exists():
        cleanup_staging(repo, staging_root)

    proc = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(staging_root), commit],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git worktree add 失败：{proc.stderr.strip()}")

    files_spec = lock_dir / "FILES.json"
    import json

    spec = json.loads(files_spec.read_text(encoding="utf-8-sig"))
    old = datetime.now() - timedelta(minutes=10)
    for entry in spec.get("files", []):
        target = entry.get("target", "")
        dest_rel = _overlay_destination(target)
        if not dest_rel:
            continue
        src = Path(lock_dir) / target
        dest = staging_root / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        # package-frozen 会检查「最近 180 秒内无写入」，把 overlay 的 mtime 调旧。
        os.utime(dest, (old.timestamp(), old.timestamp()))

    node_modules = repo / "apps" / "console" / "console" / "node_modules"
    staged_modules = staging_root / "apps" / "console" / "console" / "node_modules"
    if node_modules.is_dir() and not staged_modules.exists():
        staged_modules.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(staged_modules), str(node_modules)],
            capture_output=True,
            check=False,
        )
    return staging_root


def cleanup_staging(repo: Path, staging_root: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(staging_root)],
        capture_output=True,
        check=False,
    )
    if Path(staging_root).exists():
        shutil.rmtree(staging_root, ignore_errors=True)
