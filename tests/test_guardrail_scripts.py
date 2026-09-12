"""Meta-guardrail: every ``scripts/verify_*.py`` must be able to fail.

A script whose name starts with ``verify_`` but which can never exit non-zero is
a false safety signal: reviewers assume an invariant is enforced while in
reality nothing is checked. Three such defects were found in this repository
(Round 74): ``verify_orm_vs_db.py`` and ``verify_persistence.py`` could not even
start (``ModuleNotFoundError: No module named 'src'``), and neither of them -- nor
``verify_ai_layer_audit.py`` -- was ever invoked by CI.

This module makes that class of defect mechanically impossible. For every
``scripts/verify_*.py`` it enforces:

1. ``sys.path`` is bootstrapped, so ``import src...`` resolves when the script is
   invoked directly (``python scripts/foo.py`` from the repository root);
2. the script contains a construct that can yield a non-zero process status;
3. the script is invoked by at least one workflow under ``.github/workflows/``.

A script that is deliberately a pure report must be listed in
:data:`DIAGNOSTIC_REPORTERS` with a reason; it is then exempt from (2) and (3),
and the allowlist itself is checked so it cannot rot or be abused. The allowlist
is currently empty, which is the intended steady state.

Limitation, stated honestly: (2) is a *necessary* condition, not a sufficient
one -- a script that raises only on an unreachable branch would still pass.
Sufficiency comes from the scripts actually running in CI (3) plus the negative
controls exercised in Round 74.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

#: ``verify_*`` scripts that report rather than gate, mapped to the reason.
#:
#: An entry here is a *claim that the file has no pass/fail contract*; it is
#: verified in :func:`test_allowlist_entries_are_genuine_reporters`. Giving a
#: listed script a real failure path will break this module -- which is the
#: intended behaviour: remove it from the allowlist so the gate checks apply.
DIAGNOSTIC_REPORTERS: dict[str, str] = {}

_BOOTSTRAP_RE = re.compile(r"sys\.path\.(insert|append)")

_EXIT_CALLS = {"sys.exit", "exit"}
_SYSTEM_EXIT_CALLS = {"SystemExit", "builtins.SystemExit"}


def _all_verify_scripts() -> list[pathlib.Path]:
    return sorted(SCRIPTS_DIR.glob("verify_*.py"))


def _gate_scripts() -> list[pathlib.Path]:
    return [p for p in _all_verify_scripts() if p.name not in DIAGNOSTIC_REPORTERS]


def _source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _workflow_text() -> str:
    if not WORKFLOWS_DIR.is_dir():
        return ""
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(WORKFLOWS_DIR.iterdir())
        if p.suffix in (".yml", ".yaml")
    )


def _dotted(node: ast.AST) -> str:
    """Render ``sys.exit`` / ``main`` / ``builtins.SystemExit`` as a dotted name."""
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return "%s.%s" % (prefix, node.attr) if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_nonzero_int(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value != 0


def _argument_can_be_nonzero(node: ast.AST | None) -> bool:
    """True for ``1``, ``1 if bad else 0`` and ``0 if ok else 1``."""
    if node is None:
        return False
    if _is_nonzero_int(node):
        return True
    if isinstance(node, ast.IfExp):
        return _argument_can_be_nonzero(node.body) or _argument_can_be_nonzero(node.orelse)
    return False


def _function_returns_nonzero(func: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Return) and _is_nonzero_int(node.value)
        for node in ast.walk(func)
    )


def _can_exit_nonzero(path: pathlib.Path) -> bool:
    """AST-accurate test for "this script has a path to a non-zero exit status.

    Regexes cannot tell a helper's ``return 2`` apart from a process exit status,
    which is exactly the mistake that produced a false positive in the first
    version of this module. Walking the tree and resolving ``sys.exit(main())``
    back to ``main()``'s own returns avoids it.
    """
    tree = ast.parse(_source(path))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def call_target_returns_nonzero(node: ast.AST | None) -> bool:
        if not isinstance(node, ast.Call):
            return False
        target = functions.get(_dotted(node.func))
        return target is not None and _function_returns_nonzero(target)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) in _EXIT_CALLS:
            argument = node.args[0] if node.args else None
            if _argument_can_be_nonzero(argument) or call_target_returns_nonzero(argument):
                return True
        elif isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call) and _dotted(exc.func) in _SYSTEM_EXIT_CALLS:
                argument = exc.args[0] if exc.args else None
                if _argument_can_be_nonzero(argument) or call_target_returns_nonzero(argument):
                    return True
            elif isinstance(exc, ast.Call):
                return True  # raising any other exception aborts with a traceback
            else:
                return True
    return False


def test_guardrail_scripts_were_found() -> None:
    """Guards against this module silently passing on a wrong path."""
    names = [p.name for p in _all_verify_scripts()]
    assert "verify_c4_approval_channel.py" in names, names
    assert "verify_orm_vs_db.py" in names, names
    assert "verify_persistence.py" in names, names


@pytest.mark.parametrize("script", _all_verify_scripts(), ids=lambda p: p.name)
def test_every_verify_script_bootstraps_sys_path(script: pathlib.Path) -> None:
    """``python scripts/x.py`` from the repo root must be able to import src."""
    assert _BOOTSTRAP_RE.search(_source(script)), (
        "%s does not bootstrap sys.path, so `import src...` fails when it is run "
        "directly from the repository root" % script.name
    )


@pytest.mark.parametrize("script", _gate_scripts(), ids=lambda p: p.name)
def test_every_gate_script_has_a_failure_exit(script: pathlib.Path) -> None:
    """A ``verify_*`` gate that cannot fail is worse than no gate at all."""
    assert _can_exit_nonzero(script), (
        "%s has no construct that can produce a non-zero exit status; it would "
        "report violations that CI can never notice" % script.name
    )


@pytest.mark.parametrize("script", _gate_scripts(), ids=lambda p: p.name)
def test_every_gate_script_is_wired_into_ci(script: pathlib.Path) -> None:
    """An unwired guardrail is decay: nobody runs it until someone remembers."""
    text = _workflow_text()
    assert text, "no workflow files found under .github/workflows"
    assert script.name in text, (
        "%s is never invoked by any workflow, so it cannot protect anything; "
        "wire it into a job or list it in DIAGNOSTIC_REPORTERS with a reason"
        % script.name
    )


def test_allowlist_entries_exist_and_have_reasons() -> None:
    for name, reason in DIAGNOSTIC_REPORTERS.items():
        assert (SCRIPTS_DIR / name).is_file(), "allowlisted but missing: %s" % name
        assert reason.strip(), "allowlist entry %s has no reason" % name


def test_allowlist_entries_are_genuine_reporters() -> None:
    """A listed 'reporter' that gains a failure path must leave the allowlist."""
    offenders = [
        name for name in DIAGNOSTIC_REPORTERS
        if _can_exit_nonzero(SCRIPTS_DIR / name)
    ]
    assert not offenders, (
        "these files are allowlisted as report-only but can exit non-zero -- "
        "remove them from DIAGNOSTIC_REPORTERS so the gate checks apply: %s"
        % offenders
    )


def test_no_stale_allowlist_entries() -> None:
    existing = {p.name for p in _all_verify_scripts()}
    stale = sorted(set(DIAGNOSTIC_REPORTERS) - existing)
    assert not stale, "allowlist references scripts that no longer exist: %s" % stale
