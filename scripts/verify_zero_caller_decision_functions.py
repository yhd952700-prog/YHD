#!/usr/bin/env python3
"""REPORT-ONLY inventory of public zero-caller DECISION functions (step 1).

Scope, deliberately bounded (team-lead ruling)
----------------------------------------------
Step 1 of the generic zero-caller gate is INVENTORY ONLY:

  1. list every PUBLIC, DECISION-SHAPED function that has ZERO references,
  2. give file:line,
  3. mark whether the zero-reference verdict is plausibly a FALSE POSITIVE
     caused by dynamic dispatch / registry registration / framework handling,
  4. MEASURE THE FALSE-POSITIVE RATE.

It is NOT wired to CI and does NOT fail the build. The precedent that forces
this posture is docs-brand's docstring AST gate candidate (``59c7496c``):
strict mode produced a **92.9% false-positive rate (13 of 14)**, so it was
correctly kept report-only. The expected outcome here is the same shape: a
naive "name never appears" test is fooled constantly by dynamic dispatch, so
this gate stays report-only until the FP rate is acceptable.

Definitions used here (stated so the numbers can be argued with)
----------------------------------------------------------------
* PUBLIC          — the function's own name does not start with ``_``.
* DECISION-SHAPED — returns ``bool`` (annotation), or returns a literal
                    ``True``/``False``, or is named with a decision prefix
                    (``is_``/``has_``/``can_``/``should_``/``check_``/
                    ``verify_``/``deny_``/``allow_``/``decide_``/
                    ``evaluate_``). Helpers, dataclasses and dunders are out of
                    scope by construction.
* REFERENCE       — the identifier appears ANYWHERE as a loaded ``Name`` or as
                    an ``Attribute.attr`` outside its own ``def``. A callback
                    passed by name counts as a reference (it is genuinely used);
                    only a literal call is counted separately as a CALL.
* ZERO-CALLER     — zero references in the whole scanned corpus.

Why this is honest about itself
-------------------------------
"Name never appears" is a heuristic, not an import graph. Every plausible
dynamic-dispatch / registration escape hatch is surfaced as an explicit
``fp_signal`` so a human can decide, rather than pretending the count is a
measured truth:
  * ``string-literal``  — the name appears as a quoted string (getattr,
                          registry dict, __all__, config, importlib).
  * ``decorated``       — has decorators; frameworks call decorated callables
                          (FastAPI routes, ``@kernel_action``, registries).
  * ``route-handler``   — decorated with an HTTP verb (``.get/.post/...``).
  * ``exported``        — re-exported from a package ``__init__.py``.
  * ``protocol-impl``   — defined in a class deriving from ABC/Protocol/Base.
  * ``dunder``          — ``__x__`` (excluded anyway).

Usage
-----
    python scripts/verify_zero_caller_decision_functions.py
    python scripts/verify_zero_caller_decision_functions.py --sample 20
    python scripts/verify_zero_caller_decision_functions.py --json out.json

Exit code is always 0 in report-only mode (default). ``--strict`` exists for a
future hard gate and is OFF — do not wire it until the FP rate is acceptable.
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SKIP_DIR_PARTS = {
    "__pycache__", "node_modules", ".venv", "venv", "dist", "build",
    ".git", ".mypy_cache", ".pytest_cache", "site-packages",
}
# Definitions come from production code only: scripts/ and tests/ are entry
# points invoked by a human / pytest, so counting them would be pure noise.
DEF_DIRS = ("src",)
# References may come from anywhere python-ish that could call into src/.
REF_DIRS = ("src", "tests", "scripts")

DECISION_PREFIXES = (
    "is_", "has_", "have_", "can_", "should_", "will_", "check_", "verify_",
    "deny_", "allow_", "decide_", "evaluate_", "permit_", "needs_",
)
HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "route"}
BASE_SUFFIXES = ("Base", "ABC", "Protocol", "Interface", "Abstract", "Mixin")


def iter_py(root: pathlib.Path, dirs):
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if SKIP_DIR_PARTS & set(path.parts):
                continue
            yield path


def parse(path: pathlib.Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return None


def is_public(name: str) -> bool:
    return not name.startswith("_") and not name.startswith("__")


def decorator_names(node) -> list[str]:
    out = []
    for dec in getattr(node, "decorator_list", []) or []:
        if isinstance(dec, ast.Call):
            target = dec.func
        else:
            target = dec
        if isinstance(target, ast.Attribute):
            out.append(target.attr)
        elif isinstance(target, ast.Name):
            out.append(target.id)
    return out


def returns_bool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    ann = node.returns
    if ann is not None:
        try:
            txt = ast.unparse(ann)
        except Exception:
            txt = ""
        if "bool" in txt:
            return True
    for sub in ast.walk(node):
        if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant):
            if isinstance(sub.value.value, bool):
                return True
    return False


def class_bases_are_abstract(cls: ast.ClassDef) -> bool:
    for b in cls.bases:
        try:
            txt = ast.unparse(b)
        except Exception:
            continue
        if any(txt.endswith(s) or s in txt for s in BASE_SUFFIXES):
            return True
    return False


def collect_definitions(root: pathlib.Path):
    """Yield decision-shaped public function defs with their fp signals."""
    defs = []
    for path in iter_py(root, DEF_DIRS):
        tree = parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        # package __init__ exports (used for the `exported` signal)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if not is_public(name):
                continue
            if not (returns_bool(node) or name.startswith(DECISION_PREFIXES)):
                continue
            decs = decorator_names(node)
            signals = []
            if decs:
                signals.append("decorated")
            if any(v in decs for v in HTTP_VERBS):
                signals.append("route-handler")
            defs.append({
                "file": rel,
                "line": node.lineno,
                "name": name,
                "kind": "method" if _owner_class(tree, node) else "function",
                "owner": _owner_class(tree, node),
                "abstract_owner": _owner_is_abstract(tree, node),
                "decorators": decs,
                "fp_signals": signals,
            })
    return defs


def _owner_class(tree: ast.Module, target) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if sub is target:
                    return node.name
    return None


def _owner_is_abstract(tree: ast.Module, target) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if sub is target:
                    return class_bases_are_abstract(node)
    return False


def collect_references(root: pathlib.Path):
    """Return (refs, calls, strings) name -> count, over the reference corpus."""
    refs: dict[str, int] = {}
    calls: dict[str, int] = {}
    strings: set[str] = set()
    for path in iter_py(root, REF_DIRS):
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                refs[node.id] = refs.get(node.id, 0) + 1
            elif isinstance(node, ast.Attribute):
                refs[node.attr] = refs.get(node.attr, 0) + 1
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    calls[f.id] = calls.get(f.id, 0) + 1
                elif isinstance(f, ast.Attribute):
                    calls[f.attr] = calls.get(f.attr, 0) + 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # quoted names: getattr/registry/__all__/importlib escape hatches
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.value):
                    strings.add(node.value)
    return refs, calls, strings


def collect_exports(root: pathlib.Path):
    """Names re-exported from any package __init__.py."""
    exported: set[str] = set()
    for path in iter_py(root, DEF_DIRS):
        if path.name != "__init__.py":
            continue
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    exported.add(a.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "__all__":
                        if isinstance(node.value, (ast.List, ast.Tuple)):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    exported.add(elt.value)
    return exported


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--sample", type=int, default=0,
                    help="print N zero-caller entries for manual FP verification")
    ap.add_argument("--json", dest="json_out", help="write the full inventory")
    ap.add_argument("--strict", action="store_true",
                    help="FUTURE hard gate. OFF by default; do not wire to CI "
                         "until the measured FP rate is acceptable.")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    defs = collect_definitions(root)
    refs, calls, strings = collect_references(root)
    exported = collect_exports(root)

    zero = []
    for d in defs:
        name = d["name"]
        n_refs = refs.get(name, 0)
        # subtract nothing: `refs` already counts every Name load / Attribute,
        # including the def's own name if it appears as a Name (it does not --
        # a FunctionDef is not a Name load), so no adjustment is needed.
        if n_refs > 0:
            continue
        sig = list(d["fp_signals"])
        if name in strings:
            sig.append("string-literal")
        if name in exported:
            sig.append("exported")
        if d["abstract_owner"]:
            sig.append("protocol-impl")
        d = dict(d)
        d["fp_signals"] = sig
        d["likely_fp"] = bool(sig)
        zero.append(d)

    total = len(defs)
    hard = [z for z in zero if not z["likely_fp"]]

    print("=" * 78)
    print("ZERO-CALLER DECISION FUNCTIONS — REPORT ONLY (not a CI gate)")
    print("=" * 78)
    print(f"decision-shaped public defs scanned : {total}")
    print(f"with ZERO references                : {len(zero)}")
    print(f"  of those, carrying an FP signal   : {len(zero) - len(hard)}")
    print(f"  no FP signal (candidate real dead): {len(hard)}")
    if zero:
        fp_pct = (len(zero) - len(hard)) / len(zero) * 100
        print(f"  implied FALSE-POSITIVE rate       : {fp_pct:.1f}%")
    print()
    print("FP signals are HEURISTIC escape hatches (dynamic dispatch / registry /")
    print("framework handling), not proof of being called. They exist to show the")
    print("naive 'name never appears' test cannot be trusted as a hard gate.")

    if args.sample:
        print()
        print("=" * 78)
        print(f"SAMPLE of {min(args.sample, len(zero))} zero-caller entries "
              "(verify by hand before trusting any count)")
        print("=" * 78)
        for z in zero[:args.sample]:
            print(f"  {z['file']}:{z['line']}  {z['name']}  "
                  f"[{z['kind']}]  signals={z['fp_signals'] or 'NONE'}")

    if args.json_out:
        payload = {
            "status": "REPORT_ONLY",
            "gate": False,
            "totals": {
                "decision_defs": total,
                "zero_referenced": len(zero),
                "with_fp_signal": len(zero) - len(hard),
                "no_fp_signal": len(hard),
                "implied_fp_pct": round((len(zero) - len(hard)) / len(zero) * 100, 2) if zero else 0.0,
            },
            "zero_callers": sorted(zero, key=lambda z: (z["file"], z["line"])),
        }
        p = pathlib.Path(args.json_out)
        p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {p}")

    if args.strict and hard:
        print("\nSTRICT (explicitly requested): failing on candidates with no FP signal.")
        return 1
    print("\nREPORT-ONLY: exit 0 regardless. Gate stays OFF until FP rate is acceptable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
