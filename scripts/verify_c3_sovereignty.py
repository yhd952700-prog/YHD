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
  7. SAFETY GUARDS: no production @kernel_action flips enforce=True; and, within
     the shapes this checker covers (see _production_opens_sovereignty), no
     opener of a sovereignty channel is reachable from a statically resolvable
     name under ``src/``. Reading the channel (get_active_sovereignty) is the
     mechanism itself and is fine.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys
from typing import Dict, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# D-1a: the guard is about *production* code, and "production" is ``src/``, not
# merely the kernel package. Scanning only ``src/kernels/`` could not see an
# opener placed in the gateway or another non-kernel module.
SRC_DIR = REPO_ROOT / "src"

#: The module that *defines* the channel-opener symbols, exempted because a
#: definition is not a production opener. Exempted by **resolved identity**, not
#: by basename: skipping any file called ``_sovereignty.py`` would silently blind
#: this guard to a same-named file placed anywhere else under ``src/`` -- the
#: same "exempt by name, not by identity" defect class as T-M / F29.
DEFINING_MODULE = (SRC_DIR / "kernels" / "_sovereignty.py").resolve()

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
    for path in SRC_DIR.rglob("*.py"):
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


#: Symbols that *open* the channel. Distinguished by name from the readers
#: below, so an over-broad "anything imported from _sovereignty" rule -- which
#: would flag legitimate reads -- is never needed. ``grant_window`` is an opener
#: by the module's own account (``_sovereignty.py``:48 names it alongside
#: ``human_sovereign`` / ``set_active_sovereignty`` as a way to open the channel)
#: and returns a ``human_sovereign`` bound to the grant.
_OPENER_SYMBOLS = frozenset(
    {"human_sovereign", "set_active_sovereignty", "ActiveSovereignty", "grant_window"}
)


def _folded_str(node: ast.AST) -> Optional[str]:
    """Statically fold a string expression, or ``None`` if it is not one.

    Covers ``"set_active_" + "sovereignty"`` and placeholder-free f-strings --
    the shapes used to keep a symbol name out of a naive
    ``getattr(func, "id", None)`` comparison.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _folded_str(node.left)
        right = _folded_str(node.right)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None


def _import_aliases(tree: ast.AST) -> Dict[str, str]:
    """Map each locally bound name to the symbol it actually refers to.

    ``from src.kernels._sovereignty import human_sovereign as hs`` binds ``hs``
    to ``human_sovereign``, so an aliased call resolves back to the opener
    instead of looking like an unknown function.
    """
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name.rsplit(".", 1)[-1]
    return aliases


def _callee_symbol(func: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    """The symbol a call target resolves to, or ``None`` if unresolvable.

    Handles ``f(...)``, ``mod.f(...)``, an aliased ``f``, and the folded
    ``getattr(obj, "a" + "b")(...)`` form -- where ``func`` is itself a
    ``ast.Call`` and both ``id`` and ``attr`` are ``None``, which is exactly why
    the previous marker comparison could never see it.
    """
    if isinstance(func, ast.Name):
        return aliases.get(func.id, func.id)
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        inner = func.func
        name = getattr(inner, "id", None) or getattr(inner, "attr", None)
        if name == "getattr" and len(func.args) >= 2:
            return _folded_str(func.args[1])
    return None


def _production_opens_sovereignty() -> bool:
    """True if any scanned production module OPENS a sovereignty channel.

    The claim is bounded to the shapes actually covered:

    * a **direct** call ``human_sovereign(...)`` / ``set_active_sovereignty(...)``
      / ``ActiveSovereignty(...)`` / ``grant_window(...)``;
    * an **attribute** call ``mod.set_active_sovereignty(...)``;
    * an **import alias** (``from ... import human_sovereign as hs`` then
      ``hs(...)``), resolved through the module's own import table;
    * a **folded** ``getattr(obj, "set_active_" + "sovereignty")(...)``;

    scanning ``src/`` (not only ``src/kernels/``). Reading the channel --
    ``get_active_sovereignty`` -- is the mechanism itself and is *not* flagged in
    any of the same four shapes: the predicate resolves to a symbol *name* and
    only the opener names above are openers.

    Deliberately out of scope, named rather than left implicit: ``tests/`` and
    ``scripts/`` are not scanned at all, because a harness must be able to open
    a window to exercise the channel. Neither is a call whose symbol cannot be
    resolved statically (e.g. ``getattr(obj, runtime_name)``). The defining
    module ``src/kernels/_sovereignty.py`` -- and only it, matched by resolved
    path (:data:`DEFINING_MODULE`) -- is skipped: it is where these symbols are
    *defined*, so a definition there is not a production opener. A different file
    that merely shares the basename is scanned like any other.
    """
    for path in SRC_DIR.rglob("*.py"):
        if path.resolve() == DEFINING_MODULE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _callee_symbol(node.func, aliases) in _OPENER_SYMBOLS:
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
        v, r, actor = xc._adjudicate("capability.retire", "CRITICAL")
        check("no delegation -> HIGH/CRITICAL denied (service)",
              v == "deny" and r == "default_deny", f"{v}/{r}")
        # A2: the adjudicated actor is a return value now, not something the
        # caller must reconstruct. With no window it must be the service actor.
        check("no delegation -> adjudicated actor is the internal service",
              actor == {"type": "service", "principal": "liuhao-internal-service"},
              f"actor={actor}")

        with sov.human_sovereign(human.id, ["capability.retire"]):
            v, r, actor = xc._adjudicate("capability.retire", "CRITICAL")
        check("human delegation -> HIGH/CRITICAL allowed",
              v == "allow" and r == "human_sovereignty", f"{v}/{r}")
        # A2: and the record will name the human, so "who approved this" is
        # answerable from the audit chain instead of collapsing to a constant.
        check("human delegation -> adjudicated actor is the delegating human",
              actor.get("type") == "human" and actor.get("principal") == human.id,
              f"actor={actor}")

        with sov.human_sovereign("liuhao-internal-service", ["capability.retire"]):
            v, r, actor = xc._adjudicate("capability.retire", "CRITICAL")
        check("spoofed service-as-human rejected",
              v == "deny" and r == "default_deny", f"{v}/{r}")

        with sov.human_sovereign(human.id, ["capability.retire"]):
            v, r, actor = xc._adjudicate("security.set_abac_rule", "CRITICAL")
        check("out-of-scope action still denied",
              v == "deny" and r == "default_deny", f"{v}/{r}")
        check("out-of-scope action is not escalated to the human",
              actor.get("type") == "service", f"actor={actor}")

        with sov.human_sovereign(human.id, ["memory.store"]):
            v, r, actor = xc._adjudicate("memory.store", "LOW")
        check("LOW not escalated under delegation",
              v == "allow" and r == "internal_service_allow", f"{v}/{r}")
        check("LOW not escalated -> adjudicated actor stays the service",
              actor.get("type") == "service", f"actor={actor}")
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
    # A2: _adjudicate returns the actor it used as a third value; the stub mirrors
    # the real fail-closed return shape.
    xc._adjudicate = lambda a, r: (None, None, xc._service_actor_policy_shape())
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
    # The label must not be broader than what the checker actually proves. The
    # previous wording ("no production code opens a sovereignty channel") was an
    # unqualified universal while `_production_opens_sovereignty` covers only
    # statically resolvable names -- the same defect it was just fixed for,
    # merely moved from the docstring into the line CI prints.
    check("no statically-resolvable opener of a sovereignty channel under src/",
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
