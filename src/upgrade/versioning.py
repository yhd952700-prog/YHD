"""Version discovery and comparison.

The version is read from the application's ``pyproject.toml`` (the source of
truth for a source checkout), then from ``LIUHAO_RELEASE.json`` (present in
release packages), and only as a last resort from installed distribution
metadata. It is never guessed: if all three are missing the caller gets a
``VersionReadError`` and the user is told checking is impossible.
"""

from __future__ import annotations

import json
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Optional, Tuple

from packaging.version import InvalidVersion, Version

from .errors import VersionReadError


def parse_version(text: str, *, context: str = "版本号") -> Version:
    try:
        return Version(str(text).strip())
    except InvalidVersion as exc:
        raise VersionReadError(f"{context}无效：{text!r}") from exc


def _read_pyproject(version_file: Path) -> Optional[str]:
    try:
        data = tomllib.loads(version_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    value = project.get("version")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_release_marker(app_root: Path) -> Optional[str]:
    marker = app_root / "LIUHAO_RELEASE.json"
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    value = data.get("version") if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_current_version(app_root: str | Path) -> Tuple[str, str]:
    """Return ``(version, source)`` for the application at ``app_root``.

    Raises ``VersionReadError`` rather than returning a fabricated fallback.
    """
    root = Path(app_root)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        value = _read_pyproject(pyproject)
        if value:
            # Validate before trusting it.
            parse_version(value, context="pyproject.toml 版本号")
            return value, "pyproject.toml"

    value = _read_release_marker(root)
    if value:
        parse_version(value, context="LIUHAO_RELEASE.json 版本号")
        return value, "LIUHAO_RELEASE.json"

    try:
        value = metadata.version("liuhao-ai-os")
    except metadata.PackageNotFoundError as exc:
        raise VersionReadError(
            f"无法确定当前版本：{root} 下没有 pyproject.toml / LIUHAO_RELEASE.json，"
            "且未安装 liuhao-ai-os 包"
        ) from exc

    parse_version(value, context="已安装包版本号")
    return value, "installed-metadata"


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)
