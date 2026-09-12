"""Reproducible verification for Policy C-3 (dynamic human principal + DEFER).

Run:  .venv/Scripts/python.exe scripts/verify_c3_sovereignty.py
Exit code 0 = all assertions green.

What this proves:
  1. PolicyDeferredError exists, subclasses PolicyDeniedError, verdict="defer".
  2. PolicyEffect.DEFER exists in the policy model.
  3. WITHOUT a delegation a HIGH/CRITICAL action stays denied (service actor).
  4. WITH a verified human delegation, the HIGH/CRITICAL action is adjudicated
     as human and ALLOWED (resolves the C-2 self-lock).
  5. A spoofed principal (service id labelled human) is rejected -> deny.
  6. Enforced HIGH/CRITICAL WITHOUT delegation => PolicyDeferredError("defer");
     WITH delegation => executes; fail-closed (engine down) => PolicyDeniedError("error").
  7. SAFETY GUARDS: no production @kernel_action flips enforce=True; no production
     code OPENS a sovereignty channel (reading get_active_sovereignty is fine).
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


def _fresh_manager_with_human():
    from src.kernels.identity import IdentityManager, IdentityScope

    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-c3-verify", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    return mgr, human


def _discover_enforce_flags() -> dict:
    """Map of action-name -> True for any production call site passing enforce=True."""
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
                for kw in dec.keywords:
                    if kw.arg == "enforce" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is True:
                        enforced[arg0.value] = True
    return enforced


def _production_opens_sovereignty() -> bool:
    """True if any production kernel module OPENS a sovereignty channel.

    Reading the channel (``get_active_sovereignty``) is the mechanism itself and
    is fine; only *opening* calls (human_sovereign / set_active_sovereignty /
    ActiveSovereignty(...)) in production code are guarded against. The defining
    module is excluded; docstrings are not AST call nodes so they don't false-positive.
    """
    markers = ("human_sovereign", "set_active_sovereignty", "ActiveSovereignty")
    for path in KERNELS_DIR.rglob("*.py"):
        if path.name == "_sovereignty.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                fname = getattr(func, "id", None) or getattr(func, "attr", None)
                if fname in markers:
                    return True
    return False


def main() -> int:
    xc = importlib.import_module("src.kernels._crosscutting")
    sov = importlib.import_module("src.kernels._sovereignty")
    pol = importlib.import_module("src.kernels.policy")

    # 1. mechanism
    check("PolicyDeferredError defined", hasattr(xc, "PolicyDeferredError"))
    check("PolicyDeferredError subclasses PolicyDeniedError",
          issubclass(xc.PolicyDeferredError, xc.PolicyDeniedError))
    check("PolicyDeferredError verdict is 'defer'",
          xc.PolicyDeferredError("a").verdict == "defer")

    # 2. enum
    check("PolicyEffect.DEFER exists",
          hasattr(pol.PolicyEffect, "DEFER") and pol.PolicyEffect.DEFER.value == "defer")

    # 3-5. adjudication with an isolated verified human identity
    import src.kernels.identity as idmod

    mgr, human = _fresh_manager_with_human()
    orig = idmod.get_identity_manager
    idmod.get_identity_manager = lambda: mgr
    try:
        sov.clear_active_sovereignty()
        v, r = xc._adjudicate("capability.retire", "CRITICAL")
        check("no delegation -> HIGH/CRITICAL denied (service)",
              v == "deny" and r == "default_deny", f"{v}/{r}")

        with sov.human_sovereign(human.id, ["capability.retire"]):
            v, r = xc._adjudicate("capability.retire", "CRITICAL")
        check("human delegation -> HIGH/CRITICAL allowed",
              v == "allow" and r == "human_sovereignty", f"{v}/{r}")

        with sov.human_sovereign("liuhao-internal-service", ["capability.retire"]):
            v, r = xc._adjudicate("capability.retire", "CRITICAL")
        check("spoofed service-as-human rejected",
              v == "deny" and r == "default_deny", f"{v}/{r}")

        with sov.human_sovereign(human.id, ["capability.retire"]):
            v, r = xc._adjudicate("security.set_abac_rule", "CRITICAL")
        check("out-of-scope action still denied",
              v == "deny" and r == "default_deny", f"{v}/{r}")

        with sov.human_sovereign(human.id, ["memory.store"]):
            v, r = xc._adjudicate("memory.store", "LOW")
        check("LOW not escalated under delegation",
              v == "allow" and r == "internal_service_allow", f"{v}/{r}")
    finally:
        idmod.get_identity_manager = orig

    # 6. enforcement gate
    sov.clear_active_sovereignty()

    @xc.kernel_action("capability.retire", enforce=True)
    def crit():
        return "ran"

    raised_defer = False
    try:
        crit()
    except xc.PolicyDeferredError as err:
        raised_defer = True
        check("enforced HIGH w/o delegation -> PolicyDeferredError(defer)",
              err.verdict == "defer", f"verdict={err.verdict}")
    except xc.PolicyDeniedError:
        pass
    check("enforced HIGH w/o delegation actually raised", raised_defer)

    # WITH delegation -> executes
    mgr2, human2 = _fresh_manager_with_human()
    idmod.get_identity_manager = lambda: mgr2
    try:
        @xc.kernel_action("capability.retire", enforce=True)
        def crit2():
            return "ran-under-human"

        with sov.human_sovereign(human2.id, ["capability.retire"]):
            out = crit2()
        check("enforced HIGH w/ delegation executes", out == "ran-under-human", str(out))
    finally:
        idmod.get_identity_manager = orig

    # fail-closed -> PolicyDeniedError(error)
    orig_adj = xc._adjudicate
    xc._adjudicate = lambda a, r: (None, None)
    try:
        @xc.kernel_action("security.set_abac_rule", enforce=True)
        def crit3():
            return "ran"

        try:
            crit3()
            check("fail-closed blocks", False, "did not raise")
        except xc.PolicyDeniedError as err:
            check("fail-closed -> PolicyDeniedError(error)",
                  err.verdict == "error", f"verdict={err.verdict}")
    finally:
        xc._adjudicate = orig_adj

    # 7. safety guards
    enforced_sites = _discover_enforce_flags()
    check("no production @kernel_action has enforce=True",
          not enforced_sites,
          f"flipped={sorted(enforced_sites)}" if enforced_sites else "")
    check("no production code opens a sovereignty channel",
          not _production_opens_sovereignty())

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
