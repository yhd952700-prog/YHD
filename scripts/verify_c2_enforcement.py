"""Reproducible verification for Policy C-2 (kernel-layer enforcement gate).

Run:  .venv/Scripts/python.exe scripts/verify_c2_enforcement.py
Exit code 0 = all assertions green.

What this proves:
  1. The enforcement mechanism exists (PolicyDeniedError + enforce flag).
  2. The C-2 cut line is exactly HIGH/CRITICAL (ENFORCED_TIERS).
  3. The gate BLOCKS a denied HIGH/CRITICAL action when enforce=True, and is
     fail-closed when adjudication is unavailable.
  4. The gate does NOT block: allow verdicts, and LOW/MEDIUM tiers.
  5. SAFETY GUARD: no production ``@kernel_action`` call site has flipped
     enforce=True -- the kernel layer stays additive in production.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

KERNELS_DIR = REPO_ROOT / "src" / "kernels"

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))


def _discover_enforce_flags() -> dict:
    """Map of action-name -> whether any call site passes enforce=True."""
    enforced: dict = {}
    for path in KERNELS_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not dec.args:
                    continue
                func = dec.func
                fname = getattr(func, "id", None) or getattr(func, "attr", None)
                if fname != "kernel_action":
                    continue
                arg0 = dec.args[0]
                if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)):
                    continue
                action = arg0.value
                for kw in dec.keywords:
                    if kw.arg == "enforce" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is True:
                        enforced[action] = True
    return enforced


def main() -> int:
    # --- 1. mechanism exists -------------------------------------------------- #
    xc = importlib.import_module("src.kernels._crosscutting")
    check("PolicyDeniedError defined", hasattr(xc, "PolicyDeniedError"))
    check("PolicyDeniedError is PermissionError",
          issubclass(xc.PolicyDeniedError, PermissionError))
    check("kernel_action accepts enforce kwarg",
          "enforce" in xc.kernel_action.__kwdefaults__ or
          "enforce" in xc.kernel_action.__code__.co_varnames)

    rc = importlib.import_module("src.kernels._risk_classification")
    check("ENFORCED_TIERS == {HIGH, CRITICAL}",
          rc.ENFORCED_TIERS == {rc.RiskTier.HIGH, rc.RiskTier.CRITICAL},
          str(sorted(t.value for t in rc.ENFORCED_TIERS)))

    # --- 2+3+4. gate behaviour through the real decorator -------------------- #
    import pytest  # only used for the raises helper below; fall back if absent

    # HIGH denied + enforce => raises (Policy C-3 reclassifies this as DEFER:
    # the action requires human sovereignty, OD-010, rather than a permanent
    # denial). PolicyDeferredError subclasses PolicyDeniedError, so the block
    # still surfaces as a PermissionError to existing guards.
    @xc.kernel_action("identity.grant_permission", enforce=True)
    def high_enforced():
        return "ran"

    raised = False
    try:
        high_enforced()
    except xc.PolicyDeniedError as err:
        raised = True
        is_defer = isinstance(err, xc.PolicyDeferredError) and err.verdict == "defer"
        check("HIGH+deny+enforce raises PolicyDeferredError(defer)",
              err.action == "identity.grant_permission" and is_defer,
              f"action={err.action} verdict={err.verdict} rule={err.rule_id}")
    check("HIGH denied action was actually blocked", raised)

    # LOW allow + enforce => does NOT raise
    @xc.kernel_action("memory.store", enforce=True)
    def low_enforced_allow():
        return "ok"

    try:
        out = low_enforced_allow()
        check("LOW+allow+enforce does NOT block", out == "ok")
    except xc.PolicyDeniedError:
        check("LOW+allow+enforce does NOT block", False, "unexpectedly raised")

    # MEDIUM denied + enforce => does NOT block (cut line excludes MEDIUM)
    @xc.kernel_action("context.set_scope", enforce=True)
    def medium_enforced_denied():
        return "ok-medium"

    try:
        out = medium_enforced_denied()
        check("MEDIUM denied + enforce does NOT block (cut line)", out == "ok-medium")
    except xc.PolicyDeniedError:
        check("MEDIUM denied + enforce does NOT block (cut line)", False,
              "unexpectedly raised")

    # fail-closed: adjudication unavailable + enforce => blocks
    orig = xc._adjudicate
    xc._adjudicate = lambda a, r: (None, None)
    try:
        @xc.kernel_action("security.set_abac_rule", enforce=True)
        def critical_enforced():
            return "ran"

        try:
            critical_enforced()
            check("fail-closed blocks on adjudication failure", False, "did not raise")
        except xc.PolicyDeniedError as err:
            check("fail-closed blocks on adjudication failure",
                  err.verdict == "error", f"verdict={err.verdict}")
    finally:
        xc._adjudicate = orig

    # --- 5. SAFETY GUARD: no production enforce flip ------------------------- #
    enforced_sites = _discover_enforce_flags()
    check("no production @kernel_action has enforce=True",
          not enforced_sites,
          f"flipped actions={sorted(enforced_sites)}" if enforced_sites else "")

    # Default-off HIGH action still executes (additive preserved in prod)
    @xc.kernel_action("capability.retire")  # enforce defaults False
    def high_default_off():
        return "ran"

    try:
        out = high_default_off()
        check("default-off HIGH action still executes (additive)", out == "ran")
    except xc.PolicyDeniedError:
        check("default-off HIGH action still executes (additive)", False,
              "unexpectedly raised")

    failed = [r for r in RESULTS if not r[0]]
    print()
    if failed:
        print(f"RESULT: {len(failed)} FAILED / {len(RESULTS)} total")
        for ok, name, detail in failed:
            print(f"  - {name} :: {detail}")
        return 1
    print(f"RESULT: ALL GREEN ({len(RESULTS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
