"""控制台构建产物的来源指纹与构建记录。

目的：升级包里的 ``console/`` 必须能证明"是由当前前端源码构建出来的"，
而不是上一版遗留的 dist 重新盖章。打包端读取 ``console/console-build.json``
并与仓库内前端源码指纹、产物文件指纹对照；不一致直接拒绝出包。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping
import shutil
import subprocess

BUILD_META_RELPATH = "console/console-build.json"
BUILD_META_SCHEMA = "liuhao.console-build/1"

_SOURCE_SKIP_DIRS = {
    "node_modules",
    "dist",
    ".git",
    ".vite",
    "coverage",
    "__pycache__",
    ".turbo",
}
_SOURCE_SKIP_SUFFIXES = {".log", ".tsbuildinfo"}
_CONSOLE_SKIP_NAMES = {"console-build.json", "console-contract.json"}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def frontend_source_fingerprint(source_dir: Path) -> str:
    """对前端源码树做稳定指纹（排除 node_modules/dist 等构建目录）。"""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"前端源码目录不存在：{source_dir}")
    digest = hashlib.sha256()
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel_parts = path.relative_to(source_dir).parts
        if any(part in _SOURCE_SKIP_DIRS for part in rel_parts):
            continue
        if path.suffix.lower() in _SOURCE_SKIP_SUFFIXES:
            continue
        rel = path.relative_to(source_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_bytes(path.read_bytes()).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def console_files_fingerprint(files: Mapping[str, bytes]) -> str:
    """与 ``src.upgrade.console_contract.console_fingerprint`` 相同算法。

    键必须是以 ``console/`` 开头的 POSIX 相对路径，打包端与构建端才能对上。
    """
    digest = hashlib.sha256()
    for rel in sorted(files):
        digest.update(rel.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_bytes(files[rel]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def collect_console_files(directory: Path) -> Dict[str, bytes]:
    """读取一个 dist 目录，返回 ``console/<相对路径>`` -> 字节。"""
    directory = Path(directory)
    collected: Dict[str, bytes] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.name in _CONSOLE_SKIP_NAMES:
            # 旧签章/旧构建记录不属于本次构建产物，不能参与指纹。
            continue
        rel = "console/" + path.relative_to(directory).as_posix()
        collected[rel] = path.read_bytes()
    return collected


def copy_console_dist(dist_dir: Path, dest_dir: Path) -> None:
    """把构建产物覆盖到目标 console 目录（目标旧签章一并清除）。"""
    dist_dir = Path(dist_dir)
    dest_dir = Path(dest_dir)
    if dist_dir.resolve() == dest_dir.resolve():
        return
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for item in sorted(dist_dir.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        target = dest_dir / item.relative_to(dist_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def write_build_metadata(
    dest_dir: Path,
    *,
    source_dir: Path,
    version: str,
    command: str,
) -> Dict[str, Any]:
    """在目标 console 目录写入 ``console-build.json`` 并返回内容。"""
    dest_dir = Path(dest_dir)
    if not (dest_dir / "index.html").is_file():
        raise FileNotFoundError(f"构建产物缺少 index.html：{dest_dir}")
    files = collect_console_files(dest_dir)
    meta: Dict[str, Any] = {
        "schema": BUILD_META_SCHEMA,
        "version": version,
        "source": _source_label(source_dir),
        "source_sha256": frontend_source_fingerprint(source_dir),
        "files_sha256": console_files_fingerprint(files),
        "file_count": len(files),
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "command": command,
        "node": _tool_version("node"),
        "npm": _tool_version("npm"),
    }
    meta_path = dest_dir / "console-build.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def _source_label(source_dir: Path) -> str:
    try:
        return Path(source_dir).resolve().as_posix().split("/apps/console/", 1)[-1]
    except Exception:  # noqa: BLE001 - 仅用于展示
        return Path(source_dir).name


def _tool_version(name: str) -> str:
    exe = shutil.which(name) or shutil.which(f"{name}.cmd")
    if not exe:
        return "missing"
    try:
        completed = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return (completed.stdout or "").strip() or (completed.stderr or "").strip()
    except Exception:  # noqa: BLE001 - 版本信息只做记录
        return "unknown"
