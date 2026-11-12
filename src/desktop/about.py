"""关于页数据源：版本 / 构建时间 / 数据目录 / 提交号。

前端只负责展示；这里提供**只读**读取入口，可被脚本或后端直接调用：

    python -m src.desktop about          # 人读
    python -m src.desktop about --json    # 机读

字段全部是本机真实值，取不到就返回 ``None`` 并在 ``*_source`` 里注明来源，
不编造构建时间。
"""

from __future__ import annotations

import importlib.metadata as metadata
import os
import platform
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import DATA_DIR, LOG_DIR, REPO_ROOT, diagnostics_dir
from ..brand import BRAND_LOCKUP

APP_NAME = BRAND_LOCKUP
_PACKAGE_NAME = "liuhao-ai-os"


def _no_window_flags() -> int:
    """Windows 下运行 git 等子进程时避免闪出控制台窗口。"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _package_version(root: Path) -> str | None:
    try:
        return metadata.version(_PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        pass
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
    return None


#: git 字段缓存：根目录 -> (采样时间, commit, 提交时间)，默认 60 秒过期。
_GIT_CACHE: dict[str, tuple[float, str | None, str | None]] = {}
_GIT_CACHE_TTL = 60.0
_GIT_CACHE_LOCK = threading.Lock()
_GIT_SEP = "\x1f"


def _git_fields(root: Path) -> tuple[str | None, str | None]:
    """返回 ``(commit, 提交时间)``；不是 git 仓库时直接返回空，且不创建子进程。

    安装树里没有 ``.git``，以前每次请求都会 spawn 两次 ``git log``，在部分
    环境里各卡满 5 秒超时（约 10 秒），而结果恒为 ``None``。这里用
    ``.git`` 存在性（``exists()`` 兼容 worktree/submodule 的 ``.git`` 文件）
    短路；结果按根目录缓存 ``_GIT_CACHE_TTL`` 秒，commit 与构建时间共用同一次
    查询，正常情况下一次请求最多 spawn 一次。
    """
    key = str(root)
    now = time.monotonic()
    with _GIT_CACHE_LOCK:
        cached = _GIT_CACHE.get(key)
        if cached is not None and now - cached[0] < _GIT_CACHE_TTL:
            return cached[1], cached[2]
    commit: str | None = None
    commit_time: str | None = None
    if (root / ".git").exists():
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%H%x1f%cI"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=_no_window_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            pass
        else:
            value = completed.stdout.strip()
            if value:
                parts = value.split(_GIT_SEP)
                commit = parts[0].strip() or None
                if len(parts) > 1:
                    commit_time = parts[1].strip() or None
    with _GIT_CACHE_LOCK:
        _GIT_CACHE[key] = (time.monotonic(), commit, commit_time)
    return commit, commit_time


def _iso_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except OSError:
        return None


def _build_time(root: Path) -> tuple[str | None, str]:
    """构建时间优先取驾驶舱产物，其次 git 提交时间，最后 pyproject 写回时间。"""
    dist_index = root / "apps" / "console" / "console" / "dist" / "index.html"
    if dist_index.is_file():
        value = _iso_mtime(dist_index)
        if value:
            return value, "console_dist"
    commit_time = _git_fields(root)[1]
    if commit_time:
        return commit_time, "git_commit"
    value = _iso_mtime(root / "pyproject.toml")
    if value:
        return value, "pyproject_mtime"
    return None, "unknown"


def collect_about(root: Path | None = None) -> dict[str, Any]:
    """返回关于页需要的全部真实字段（无秘密，可直接序列化）。"""
    base = Path(root or REPO_ROOT)
    version = _package_version(base)
    build_time, build_source = _build_time(base)
    return {
        "app": APP_NAME,
        "version": version,
        "build_time": build_time,
        "build_time_source": build_source,
        "commit": _git_fields(base)[0],
        "data_dir": str(DATA_DIR if root is None else base / "data"),
        "log_dir": str(LOG_DIR if root is None else base / "logs"),
        "diagnostics_dir": str(diagnostics_dir()),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


__all__ = ["APP_NAME", "collect_about"]
