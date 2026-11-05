"""Path policy for edits.

Self-modification may only touch ordinary source files *inside* the workspace.
Protected runtime data (``data/``), VCS metadata, virtualenvs, secrets and the
module's own state/backups are refused. Absolute paths and ``..`` traversal are
refused before the filesystem is touched.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Tuple

from .errors import PathNotAllowed

DEFAULT_BLOCKED_PREFIXES = (
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "data",
    "upgrade-backups",
    "upgrade-state",
    ".selfmod-state",
    "secrets",
    "dist",
    "build",
    "__pycache__",
)

_BLOCKED_SUFFIXES = (".pem", ".key", ".crt", ".pfx")
_BLOCKED_NAMES = (".env",)


class PathPolicy:
    def __init__(
        self,
        workspace: str | Path,
        *,
        blocked_prefixes: Iterable[str] = DEFAULT_BLOCKED_PREFIXES,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.blocked_prefixes: Tuple[str, ...] = tuple(
            str(item).replace("\\", "/").strip("/") for item in blocked_prefixes
        )

    def normalise(self, raw: str) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise PathNotAllowed("文件路径不能为空")
        text = raw.replace("\\", "/").strip()
        if text.startswith("/") or text.startswith("~"):
            raise PathNotAllowed(f"拒绝绝对路径：{raw!r}")
        first = text.split("/", 1)[0]
        if ":" in first:
            raise PathNotAllowed(f"拒绝盘符路径：{raw!r}")
        parts = [part for part in text.split("/") if part]
        if not parts or any(part == ".." for part in parts):
            raise PathNotAllowed(f"拒绝路径穿越：{raw!r}")
        rel = "/".join(parts)
        for prefix in self.blocked_prefixes:
            if rel == prefix or rel.startswith(prefix + "/"):
                raise PathNotAllowed(f"拒绝修改受保护路径：{rel}")
        name = parts[-1]
        if name in _BLOCKED_NAMES or name.startswith(".env."):
            raise PathNotAllowed(f"拒绝修改敏感文件：{rel}")
        if name.lower().endswith(_BLOCKED_SUFFIXES):
            raise PathNotAllowed(f"拒绝修改密钥/证书文件：{rel}")

        target = self.workspace / rel
        resolved = target.resolve()
        try:
            common = Path(os.path.commonpath([str(self.workspace), str(resolved)]))
        except ValueError as exc:
            raise PathNotAllowed(f"路径不在工作区内：{rel}") from exc
        if common != self.workspace:
            raise PathNotAllowed(f"路径解析后不在工作区内：{rel}")
        return rel

    def absolute(self, rel: str) -> Path:
        return self.workspace / self.normalise(rel)
