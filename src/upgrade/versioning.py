"""Version discovery and comparison.

``src/version.py`` (``APP_VERSION``) is the single source of truth and wins
whenever it exists in the application tree — source checkout and installed
payload both ship it, so the running code and the upgrade logic cannot drift.
``pyproject.toml`` and ``LIUHAO_RELEASE.json`` are packaging projections kept
for older trees; installed distribution metadata is the last resort. It is
never guessed: if all of them are missing the caller gets a
``VersionReadError`` and the user is told checking is impossible.
"""

from __future__ import annotations

import json
import re
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


_APP_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def _read_src_version(app_root: Path) -> Optional[str]:
    """Read ``APP_VERSION`` from ``<app_root>/src/version.py``.

    只读源码文本、不 import：升级检查可能在别的安装树上下文中运行，
    import 会把"正在运行的进程版本"和"被检查的树"混为一谈。
    """
    module = app_root / "src" / "version.py"
    if not module.is_file():
        return None
    try:
        text = module.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = _APP_VERSION_RE.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


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
    value = _read_src_version(root)
    if value:
        parse_version(value, context="src/version.py 版本号")
        return value, "src/version.py"

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
