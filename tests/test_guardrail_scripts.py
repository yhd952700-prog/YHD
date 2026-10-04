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
import textwrap

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

#: Calls that terminate the process with a status. ``os._exit`` is included
#: because a gate that bypasses ``atexit``/flush handlers still sets the status
#: CI reads.
_EXIT_CALLS = {"sys.exit", "exit", "os._exit"}
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


def _is_nonzero_int(node: ast.AST | None, constants: dict[str, int] | None = None) -> bool:
    if isinstance(node, ast.Name) and constants and node.id in constants:
        return constants[node.id] != 0
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value != 0


def _module_constants(tree: ast.AST) -> dict[str, int]:
    """Module-level ``NAME = <int literal>`` bindings (``EXIT_FAIL = 1`` and friends).

    Needed because a gate that ends with ``return EXIT_FAIL`` is just as able to
    fail as one that ends with ``return 1`` -- a checker that only understood
    literals reported the GO gate as unable to fail, which is both wrong and
    corrosive: a meta-guard that cries wolf gets switched off.
    """
    out: dict[str, int] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, int)
                and not isinstance(value.value, bool)
            ):
                out[target.id] = value.value
    return out


def _name_bindings(scope: ast.AST) -> dict[str, list[ast.AST]]:
    """Every value bound to each simple name anywhere in ``scope``.

    ``try: rc = main() / except: rc = 2 / sys.exit(rc)`` is the idiomatic "run
    the gate, then propagate its status" shape. Without resolving the name the
    walk below concluded that such a script can never fail -- the same
    corrosive false positive this module exists to prevent.
    """
    out: dict[str, list[ast.AST]] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            targets: list[ast.AST] = list(node.targets)
            value: ast.AST | None = node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                out.setdefault(target.id, []).append(value)
    return out


def _argument_can_be_nonzero(
    node: ast.AST | None,
    constants: dict[str, int] | None = None,
    functions: dict[str, ast.AST] | None = None,
    bindings: dict[str, list[ast.AST]] | None = None,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """True for ``1``, ``EXIT_FAIL``, ``1 if bad else 0``, ``rc or 1``, ``rc``.

    Conditional expressions and boolean operators are how a gate turns a
    collected verdict into a status. Treating them as "unknown, therefore
    zero" was the root cause of four false positives: a script ending in
    ``return 0 if ok else 1`` -- the only construct in the file that can fail
    the build -- was reported as unable to fail.
    """
    if node is None:
        return False
    if _is_nonzero_int(node, constants):
        return True
    if isinstance(node, ast.IfExp):
        return (
            _argument_can_be_nonzero(node.body, constants, functions, bindings, seen)
            or _argument_can_be_nonzero(node.orelse, constants, functions, bindings, seen)
        )
    if isinstance(node, ast.BoolOp):
        # ``rc or 1``: any operand that can be non-zero can become the value.
        return any(
            _argument_can_be_nonzero(value, constants, functions, bindings, seen)
            for value in node.values
        )
    if isinstance(node, ast.Name) and bindings:
        if node.id in seen:
            return False  # cyclic rebinding: no new information
        inner = seen | {node.id}
        return any(
            _argument_can_be_nonzero(value, constants, functions, bindings, inner)
            or _call_returns_nonzero(value, constants, functions, bindings, inner)
            for value in bindings.get(node.id, ())
        )
    return False


def _call_returns_nonzero(
    node: ast.AST | None,
    constants: dict[str, int] | None = None,
    functions: dict[str, ast.AST] | None = None,
    bindings: dict[str, list[ast.AST]] | None = None,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """True for ``main()`` when ``main`` has a non-zero return path.

    Follows the whole chain -- ``def main(): return run_checks()`` -- so a gate
    that delegates its verdict is no longer mistaken for one that cannot fail.
    ``seen`` guards against self- and mutual recursion.
    """
    if not isinstance(node, ast.Call) or not functions:
        return False
    name = _dotted(node.func)
    if name in seen:
        return False
    target = functions.get(name)
    if target is None:
        return False
    return _function_returns_nonzero(
        target, constants, functions, bindings, seen | {name}
    )


def _function_returns_nonzero(
    func: ast.AST,
    constants: dict[str, int] | None = None,
    functions: dict[str, ast.AST] | None = None,
    bindings: dict[str, list[ast.AST]] | None = None,
    seen: frozenset[str] = frozenset(),
) -> bool:
    for node in ast.walk(func):
        if not isinstance(node, ast.Return):
            continue
        if _argument_can_be_nonzero(node.value, constants, functions, bindings, seen):
            return True
        if _call_returns_nonzero(node.value, constants, functions, bindings, seen):
            return True
    return False


def _tree_can_exit_nonzero(tree: ast.AST) -> bool:
    """The analysis proper: does ``tree`` hold a path to a non-zero status?"""
    functions = {
        node.name: node
        for node in getattr(tree, "body", [])
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    constants = _module_constants(tree)
    bindings = _name_bindings(tree)

    def status_can_be_nonzero(node: ast.AST | None) -> bool:
        return _argument_can_be_nonzero(
            node, constants, functions, bindings
        ) or _call_returns_nonzero(node, constants, functions, bindings)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) in _EXIT_CALLS:
            if status_can_be_nonzero(node.args[0] if node.args else None):
                return True
        elif isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if isinstance(exc, ast.Call) and _dotted(exc.func) in _SYSTEM_EXIT_CALLS:
                if status_can_be_nonzero(exc.args[0] if exc.args else None):
                    return True
            elif isinstance(exc, ast.Call):
                return True  # raising any other exception aborts with a traceback
            else:
                return True
    return False


def _can_exit_nonzero(path: pathlib.Path) -> bool:
    """AST-accurate test for "this script has a path to a non-zero exit status.

    Regexes cannot tell a helper's ``return 2`` apart from a process exit status,
    which is exactly the mistake that produced a false positive in the first
    version of this module. Walking the tree and resolving ``sys.exit(main())``
    back to ``main()``'s own returns avoids it.
    """
    return _tree_can_exit_nonzero(ast.parse(_source(path)))


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


def _can_exit_nonzero_source(source: str) -> bool:
    """Run the analysis over inline source, so controls need no fixture file."""
    return _tree_can_exit_nonzero(ast.parse(textwrap.dedent(source)))


#: Gates that end in a conditional status. Four real scripts -- verify_deploy_readiness.py
#: (``return 1 if any_fail else 0``), verify_image_build.py (``return 1 if any_fail else
#: (2 if any_nv else 0)``), verify_p6_e2e_file_creation.py (``return 0 if ok else 1``)
#: and verify_real_execution_e2e.py (``sys.exit(rc)`` where ``rc`` comes from ``main()``)
#: -- were reported as unable to fail purely because of these shapes.
_CAN_FAIL_SHAPES = [
    pytest.param(
        "def main(ok):\n    return 0 if ok else 1\nsys.exit(main(ok))\n",
        id="return_zero_if_ok_else_one",
    ),
    pytest.param(
        "def main(bad):\n    return 1 if bad else 0\nsys.exit(main(bad))\n",
        id="return_one_if_bad_else_zero",
    ),
    pytest.param(
        "def main(a, b):\n    return 1 if a else (2 if b else 0)\nsys.exit(main(a, b))\n",
        id="return_nested_ifexp",
    ),
    pytest.param(
        "def main(rc):\n    return rc or 1\nsys.exit(main(rc))\n",
        id="return_boolop",
    ),
    pytest.param(
        "sys.exit(1 if bad else 0)\n",
        id="sys_exit_ifexp_argument",
    ),
    pytest.param(
        "def run_checks():\n    return 1\ndef main():\n    return run_checks()\nsys.exit(main())\n",
        id="transitive_return_chain",
    ),
    pytest.param(
        "def main():\n    return 1\ntry:\n    rc = main()\nexcept Exception:\n    rc = 2\n"
        "sys.exit(rc)\n",
        id="exit_through_local_variable",
    ),
    pytest.param(
        "def main():\n    return 1\nos._exit(main())\n",
        id="os_exit",
    ),
    pytest.param(
        "def inner():\n    raise SystemExit(1)\ndef main():\n    inner()\n    return 0\n"
        "sys.exit(main())\n",
        id="system_exit_in_nested_function",
    ),
]


@pytest.mark.parametrize("source", _CAN_FAIL_SHAPES)
def test_conditional_and_indirect_exit_paths_are_recognised(source: str) -> None:
    """These are all real failure exits; reporting them as unable to fail is a
    false positive, and a meta-guard that cries wolf gets switched off."""
    assert _can_exit_nonzero_source(source), "should be able to fail:\n%s" % source


#: Negative control. If any of these ever starts returning True the gate check has
#: become a rubber stamp and every ``verify_*`` script is effectively exempt.
_CANNOT_FAIL_SHAPES = [
    pytest.param(
        "def main():\n    return 0\nsys.exit(main())\n",
        id="main_returns_zero_literal",
    ),
    pytest.param(
        "def main(ok):\n    return 0 if ok else 0\nsys.exit(main(ok))\n",
        id="ifexp_zero_on_every_branch",
    ),
    pytest.param(
        "def main(ok):\n    return EXIT_OK\nEXIT_OK = 0\nsys.exit(main(ok))\n",
        id="named_zero_constant",
    ),
    pytest.param(
        "def main():\n    return 0\ntry:\n    rc = main()\nexcept Exception:\n    rc = 0\n"
        "sys.exit(rc)\n",
        id="local_variable_always_zero",
    ),
    pytest.param("print('diagnostic report only')\n", id="print_only"),
    pytest.param("sys.exit(0)\n", id="sys_exit_zero"),
    pytest.param("raise SystemExit(0)\n", id="system_exit_zero"),
    pytest.param("sys.exit()\n", id="sys_exit_no_argument"),
]


@pytest.mark.parametrize("source", _CANNOT_FAIL_SHAPES)
def test_scripts_that_cannot_fail_are_still_flagged(source: str) -> None:
    """Negative control: broadening the analysis must not make it vacuous."""
    assert not _can_exit_nonzero_source(source), "cannot fail, must stay flagged:\n%s" % source


def test_previously_misflagged_conditional_gates_can_fail() -> None:
    """Regression: these four were flagged as unable to fail while each has a
    reachable non-zero exit (see :data:`_CAN_FAIL_SHAPES`)."""
    for name in (
        "verify_deploy_readiness.py",
        "verify_image_build.py",
        "verify_p6_e2e_file_creation.py",
        "verify_real_execution_e2e.py",
    ):
        path = SCRIPTS_DIR / name
        assert path.is_file(), "expected gate script is gone: %s" % name
        assert _can_exit_nonzero(path), (
            "%s regressed: no non-zero exit path recognised" % name
        )
