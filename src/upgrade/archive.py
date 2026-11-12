"""Archive safety and atomic installation helpers.

A release archive is untrusted input. Before a single byte reaches the real
application tree we reject:

* absolute paths, ``..`` traversal and Windows drive prefixes;
* symlink entries (they would let a package write outside its declared roots);
* entries outside the configured install roots (``data/`` can never be shipped);
* zip bombs above a configurable uncompressed-size ceiling.

Installation itself is atomic per file (temp file + ``fsync`` + ``os.replace``)
so a crash cannot leave a half-written source file.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from .errors import PackageValidationError

_SYMLINK_MODE = 0o120000


def safe_relpath(name: str) -> str:
    """Normalise a ZIP member name to a safe relative POSIX path."""
    raw = name.replace("\\", "/").strip()
    if not raw or raw in (".", ".."):
        raise PackageValidationError(f"发布包包含无效路径：{name!r}")
    if raw.startswith("/") or raw.startswith("~"):
        raise PackageValidationError(f"发布包包含绝对路径：{name!r}")
    if ":" in raw.split("/", 1)[0]:
        raise PackageValidationError(f"发布包包含盘符路径：{name!r}")
    parts = [part for part in raw.split("/") if part]
    if any(part == ".." for part in parts):
        raise PackageValidationError(f"发布包包含路径穿越：{name!r}")
    return "/".join(parts)


def is_under(relpath: str, roots: Iterable[str]) -> bool:
    for root in roots:
        if relpath == root or relpath.startswith(root.rstrip("/") + "/"):
            return True
    return False


def is_protected(relpath: str, patterns: Iterable[str]) -> bool:
    """Match protected prefixes, plus simple ``*.pem``-style suffix patterns."""
    for pattern in patterns:
        text = str(pattern)
        if text.startswith("*") and relpath.endswith(text[1:]):
            return True
        if is_under(relpath, (text,)):
            return True
    return False


def validate_release_zip(
    archive: zipfile.ZipFile,
    *,
    install_roots: Sequence[str],
    protected_prefixes: Sequence[str],
    max_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024,
    max_entries: int = 100_000,
) -> List[Tuple[str, zipfile.ZipInfo]]:
    """Validate every member; return ``(relpath, info)`` for regular files."""
    infos = archive.infolist()
    if len(infos) > max_entries:
        raise PackageValidationError(f"发布包文件数量过多（{len(infos)}）")
    total = 0
    result: List[Tuple[str, zipfile.ZipInfo]] = []
    seen: set[str] = set()
    for info in infos:
        rel = safe_relpath(info.filename)
        if rel in seen:
            raise PackageValidationError(f"发布包包含重复路径：{rel}")
        seen.add(rel)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == _SYMLINK_MODE:
            raise PackageValidationError(f"发布包含有符号链接，拒绝应用：{rel}")
        if info.is_dir():
            continue
        if not is_under(rel, install_roots):
            raise PackageValidationError(
                f"发布包试图写入未允许的路径：{rel}（允许：{', '.join(install_roots)}）"
            )
        if is_protected(rel, protected_prefixes):
            raise PackageValidationError(f"发布包试图写入受保护路径：{rel}")
        total += info.file_size
        if total > max_uncompressed_bytes:
            raise PackageValidationError(
                f"发布包解压后超过上限（{max_uncompressed_bytes} 字节）"
            )
        result.append((rel, info))
    if not result:
        raise PackageValidationError("发布包中没有可安装的文件")
    return result


def _atomic_stream_copy(src, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_copy_file(source: Path, target: Path) -> None:
    with open(source, "rb") as src:
        _atomic_stream_copy(src, target)


def extract_zip_members(
    archive: zipfile.ZipFile,
    members: Sequence[Tuple[str, zipfile.ZipInfo]],
    target_root: Path,
) -> List[str]:
    """Extract validated members into ``target_root`` atomically."""
    installed: List[str] = []
    for rel, info in members:
        with archive.open(info, "r") as src:
            _atomic_stream_copy(src, target_root / rel)
        installed.append(rel)
    return installed


def read_package_version(directory: str | Path) -> str:
    """Read a version from a staged package, without falling back to metadata."""
    root = Path(directory)
    marker = root / "LIUHAO_RELEASE.json"
    if marker.is_file():
        try:
            value = json.loads(marker.read_text(encoding="utf-8")).get("version")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            data = {}
        project = data.get("project") if isinstance(data, dict) else None
        value = project.get("version") if isinstance(project, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise PackageValidationError(
        "发布包无法确定版本：缺少 LIUHAO_RELEASE.json 或带 version 的 pyproject.toml"
    )
