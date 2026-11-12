"""Path policy for edits.

Self-modification may only touch ordinary source files *inside* the workspace.
The blocklist is aligned with ``docs/spec/SELF-EVOLUTION.md`` §2 (permanent
prohibited zones): authentication, secrets, database schema, installer/packaging,
dependency manifests, release gates, evidence ledgers, the rollback machinery and
tests are all refused. Absolute paths and ``..`` traversal are refused before the
filesystem is touched.

Alignment is asserted by ``tests/test_selfmod_path_policy.py``: every probe path
derived from §2 must be refused, and ordinary source files must still be
accepted (so the assertion cannot pass vacuously).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Tuple

from .errors import PathNotAllowed

# Directory or file prefixes (repo-relative, forward slashes) that may never be
# edited. A prefix matches itself and every descendant.
DEFAULT_BLOCKED_PREFIXES = (
    # VCS metadata, virtualenvs, caches
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".github",
    # Runtime data, logs and local evidence (not source)
    "data",
    "memory_data",
    "logs",
    "reports",
    "run",
    "plugins",
    ".acceptance",
    ".probe_tmp",
    "ui-evidence",
    # Self-modification machinery: state/evidence ledger, backups, rollback
    ".selfmod-state",
    "upgrade-state",
    "upgrade-backups",
    "src/selfmod",
    "src/upgrade",
    # Secret material
    "secrets",
    "config",
    # Authentication / authorization (§2.1)
    "src/security",
    "src/identity",
    "src/gateway/auth.py",
    "src/gateway/policy.py",
    # Database schema (§2.3)
    "alembic",
    "migrations",
    "init-scripts",
    "src/integrations/orm_models.py",
    "src/knowledge/db.py",
    # Installer / packaging (§2.4)
    "installer",
    "packaging",
    # Dependency manifests and build config (§2.5)
    "requirements.txt",
    "pyproject.toml",
    "uv.lock",
    "oss-registry.yaml",
    "package.json",
    "package-lock.json",
    ".nvmrc",
    ".python-version",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    ".coveragerc",
    # Release gates and tooling (§2.6)
    "scripts",
    "dist",
    "build",
    # Tests (§2.8)
    "tests",
    # Documentation governance / evidence ledgers (§2.7)
    "docs/README.md",
    "docs/MASTER-TASK-LEDGER.md",
    "docs/known-gaps.md",
    "docs/spec/SELF-EVOLUTION.md",
    "RELEASE-READINESS-CONTRACT.md",
    "RELEASE-RUNBOOK.md",
)

_BLOCKED_SUFFIXES = (
    # Keys and certificates
    ".pem",
    ".key",
    ".crt",
    ".pfx",
    ".p12",
    ".jks",
    # Installer / launch scripts
    ".bat",
    ".ps1",
)

_BLOCKED_NAMES = (".env",)

# Names that are blocked anywhere in the tree, not only at the repository root.
_BLOCKED_ANYWHERE_NAMES = (
    "package.json",
    "package-lock.json",
    "conftest.py",
)

# A gateway module whose name contains one of these markers decides who may do
# what; §2.1 forbids editing any of them.
_AUTH_MARKERS = ("auth", "policy", "permission", "rbac", "principal")


def _root_requirements(name: str) -> bool:
    return name.startswith("requirements") and name.endswith(".txt")


def _blocked_gateway_module(rel: str, name: str) -> bool:
    if not rel.startswith("src/gateway/"):
        return False
    lowered = name.lower()
    return any(marker in lowered for marker in _AUTH_MARKERS)


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
            raise PathNotAllowed(f"拒绝修改密钥/证书/安装脚本：{rel}")
        if name in _BLOCKED_ANYWHERE_NAMES:
            raise PathNotAllowed(f"拒绝修改依赖/测试配置文件：{rel}")
        if rel.count("/") == 0 and _root_requirements(name):
            raise PathNotAllowed(f"拒绝修改依赖清单：{rel}")
        if _blocked_gateway_module(rel, name):
            raise PathNotAllowed(f"拒绝修改鉴权模块：{rel}")

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
