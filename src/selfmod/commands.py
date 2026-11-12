"""Deny-by-default allowlist for the command that judges a proposal.

``propose`` used to accept any ``--test-command``. That made the patch author
the owner of the ruler that measures their own patch: the shortest path to a
"passed" verdict is ``python -c "raise SystemExit(0)"``. This module replaces
that hole with an allowlist of commands that already exist in this repository —
the full pytest suite and the documentation gates from
``docs/spec/SELF-EVOLUTION.md`` §3.

Extra arguments are refused as well: allowing ``python -m pytest tests/test_x.py``
would let the author choose *which* tests count, which is the same defect one
level down.

This is an allowlist, not a sandbox: see ``sandbox.py`` for the real (and
limited) process isolation these commands run under.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from .errors import TestCommandNotAllowed

_PYTHON_BASENAMES = frozenset(
    {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}
)
#: npm 在 Windows 上是 ``npm.cmd``；subprocess 直接传 ``"npm"`` 会找不到，
#: 因此与 python 一样在归一化时钉成 ``shutil.which`` 解析出的绝对路径。
_NPM_BASENAMES = frozenset({"npm", "npm.cmd", "npm.exe", "npm.bat"})

#: Commands that exist in this repository today. ``python`` is normalised to
#: ``sys.executable`` so a caller may spell it either way.
DEFAULT_ALLOWED_TEST_COMMANDS: Tuple[Tuple[str, ...], ...] = (
    (sys.executable, "-m", "pytest"),
    (sys.executable, "scripts/verify_doc_links.py"),
    (sys.executable, "scripts/verify_docs_index_coverage.py"),
    (sys.executable, "scripts/check_docs_commands.py"),
    (sys.executable, "scripts/verify_doc_count_claims.py"),
    (sys.executable, "scripts/lint_audit_claims.py"),
    (sys.executable, "scripts/lint_control_claims.py"),
)

#: 门禁编排（``docs/spec/SELF-EVOLUTION.md`` §3）实际执行的固定命令。
#: 这张表与 ``DEFAULT_ALLOWED_TEST_COMMANDS`` 分开：提案作者可以选的「测试命令」
#: 仍然只有上面那张更小的精确匹配表；门禁命令是编排器内部常量，调用方不能挑选。
DEFAULT_GATE_COMMANDS: Tuple[Tuple[str, ...], ...] = (
    (sys.executable, "-m", "pytest"),
    (sys.executable, "-m", "pytest", "tests/test_import_integrity.py", "-q"),
    (sys.executable, "-m", "compileall", "-q", "src"),
    (sys.executable, "scripts/verify_test_collection.py"),
    (sys.executable, "scripts/verify_dependency_declaration_surface.py"),
    (sys.executable, "scripts/release/freeze_checks.py", "deps"),
    (sys.executable, "scripts/verify_orm_vs_db.py"),
    (sys.executable, "scripts/verify_openapi_contract.py"),
    (sys.executable, "scripts/verify_app_instantiation_hygiene.py"),
    (sys.executable, "scripts/verify_doc_links.py"),
    (sys.executable, "scripts/verify_docs_index_coverage.py"),
    (sys.executable, "scripts/check_docs_commands.py"),
    (sys.executable, "scripts/verify_doc_count_claims.py"),
    (sys.executable, "scripts/lint_audit_claims.py", "--all"),
    (sys.executable, "scripts/lint_control_claims.py", "--all"),
    # G6 最小口子：只放行「控制台构建」这一条命令。产物同源校验由 gates.G6 负责，
    # 不放行 npm 的任意子命令（test/lint/dev 仍需另行登记）。
    ("npm", "--prefix", "apps/console/console", "run", "build"),
)


def normalise_command(command: Sequence[str]) -> List[str]:
    """Return the command as tokens with the interpreter pinned to this runtime."""
    tokens = [str(item) for item in command]
    if not tokens:
        raise TestCommandNotAllowed("测试命令为空")
    head = Path(tokens[0]).name.lower()
    if head in _PYTHON_BASENAMES:
        tokens[0] = sys.executable
    elif head in _NPM_BASENAMES:
        resolved = shutil.which(tokens[0]) or shutil.which("npm")
        if resolved:
            tokens[0] = resolved
    return tokens


class TestCommandPolicy:
    """Exact-match allowlist. Anything not enumerated here is refused."""

    def __init__(self, allowed: Iterable[Sequence[str]] = DEFAULT_ALLOWED_TEST_COMMANDS) -> None:
        self.allowed: Tuple[Tuple[str, ...], ...] = tuple(
            tuple(normalise_command(entry)) for entry in allowed
        )

    def validate(self, command: Sequence[str]) -> List[str]:
        tokens = normalise_command(command)
        if tuple(tokens) not in self.allowed:
            raise TestCommandNotAllowed(
                "测试命令不在允许清单内："
                + " ".join(tokens)
                + "\n只允许仓库既有的测试/门禁命令：\n  "
                + "\n  ".join(self.describe())
            )
        return tokens

    def describe(self) -> List[str]:
        return [" ".join(entry) for entry in self.allowed]


class GateCommandPolicy(TestCommandPolicy):
    """门禁编排专用白名单：命令是编排器常量，不接受调用方传参选择子集。"""

    def __init__(self) -> None:
        super().__init__(DEFAULT_GATE_COMMANDS)
