"""Candidate AST gate: docstring "claim vs actual" (stub-body) scanner.

PURPOSE
-------
This is a *candidate* gate, not yet wired into CI (see Task #312). Its job is to
find functions/methods whose docstring promises behaviour the body does not
implement -- the grossest class of "documented but not implemented" defect:

    def load_secret(self, name):
        # docstring claims load+decrypt+raise; body does nothing:
        #     "Load and decrypt name from the vault. Raises KeyError if absent."
        pass   # <-- classic documented-but-unimplemented stub

Two finding classes are emitted:

    STUB_BODY     docstring present + body is empty / ``pass`` / ``return`` /
                  ``return None`` / ``raise NotImplementedError`` only.
                  Strong signal: a documented function that does literally
                  nothing executable.
    ELLIPSIS_BODY docstring present + body is ``...`` only.
                  WEAKER signal: ``...`` is also used for genuine interface /
                  abstract-method placeholders, so this class carries a higher
                  false-positive rate and is reported separately for review.

EXCLUSIONS (to keep the false-positive rate low and honest)
----------------------------------------------------------
* ``@abstractmethod`` (and ``@abc.abstractmethod``) -- intentional non-impl.
* Any method inside a class that is an ``abc.ABC`` / ``ABCMeta`` subclass or a
  ``typing.Protocol`` -- ``pass`` / ``...`` there is contractual, not a defect.
* The optional zero-caller check (``--check-zero-callers``) is clearly the
  weakest: it cannot see dynamic dispatch (registry keys, string-routed calls,
  ``getattr``, decorator registration, CLI entry points). It is emitted as
  INFORMATIONAL only and is *not* part of the gating decision until its FP rate
  is measured separately.

OUTPUT / EXIT
-------------
Emits a human-readable report and, with ``--json PATH``, a machine-readable dump.
Default mode is *report only* and exits 0 (so running it never breaks a build).
``--strict`` makes it exit non-zero when any STUB_BODY / ELLIPSIS_BODY finding
exists -- this is the future gate mode, enabled only after the FP rate below is
judged acceptable.

NOTE on naming: this file is deliberately NOT named ``verify_*.py`` while it is
a candidate. The meta-guard ``tests/test_guardrail_scripts.py`` requires every
``verify_*.py`` to be wired into CI; wiring this before its FP rate is acceptable
would be the exact false-green it exists to prevent. Rename + wire only after the
FP rate is signed off.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Required by tests/test_guardrail_scripts.py should this candidate ever be
# promoted to verify_*.py: a verify_*.py must bootstrap sys.path so it can
# import src.* when invoked directly from the repo root.
sys.path.insert(0, REPO_ROOT)

SRC = os.path.join(REPO_ROOT, "src")


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    category: str          # STUB_BODY | ELLIPSIS_BODY | ZERO_CALLERS
    path: str
    lineno: int
    qualname: str          # module:Class.method / module.func
    docstring_excerpt: str
    detail: str = ""


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _is_abstractmethod(node: ast.AST) -> bool:
    for dec in node.decorator_list:
        name = ""
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        if name == "abstractmethod":
            return True
    return False


def _class_is_abstract_class(node: ast.ClassDef) -> bool:
    """True if the class is an ABC or a Protocol (contractual non-impl)."""
    for base in node.bases:
        flat = ""
        if isinstance(base, ast.Name):
            flat = base.id
        elif isinstance(base, ast.Attribute):
            flat = base.attr
        if flat in ("ABC", "Protocol"):
            return True
    # metaclass=ABCMeta
    for kw in getattr(node, "keywords", []) or []:
        if kw.arg == "metaclass":
            val = kw.value
            name = val.id if isinstance(val, ast.Name) else getattr(val, "attr", "")
            if name == "ABCMeta":
                return True
    # Any abstractmethod inside marks it as a contract base too.
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_abstractmethod(child):
            return True
    return False


def _body_kind(body: list[ast.AST]) -> str:
    """Classify a function body as one of: empty, pass, return_none,
    not_implemented, ellipsis, or real (anything else)."""
    # Strip a leading docstring (Expr of a Constant str) for the purpose of
    # body classification -- a docstring is not executable code.
    real = [n for n in body if not (_is_docstring_expr(n))]
    if not real:
        return "empty"
    if len(real) == 1:
        n = real[0]
        if isinstance(n, ast.Pass):
            return "pass"
        if isinstance(n, ast.Return) and (
            n.value is None
            or (isinstance(n.value, ast.Constant) and n.value.value is None)
        ):
            return "return_none"
        if isinstance(n, ast.Raise) and n.exc is not None:
            exc = n.exc
            nm = exc.id if isinstance(exc, ast.Name) else getattr(exc, "attr", "")
            if nm in ("NotImplementedError", "NotImplemented"):
                return "not_implemented"
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, type(...)):
            return "ellipsis"
    return "real"


def _is_docstring_expr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _iter_functions(tree: ast.AST):
    """Yield (node, qualname, enclosing_class_is_abstract_or_none)."""
    class_stack: list[tuple[ast.ClassDef, bool]] = []

    def visit(node: ast.AST, prefix: str):
        if isinstance(node, ast.ClassDef):
            is_abs = _class_is_abstract_class(node)
            class_stack.append((node, is_abs))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qual = "%s.%s" % (node.name, child.name)
                    yield (child, (prefix + "." + qual).lstrip("."), is_abs)
                else:
                    yield from visit(child, prefix + "." + node.name)
            class_stack.pop()
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield (node, (prefix + "." + node.name).lstrip("."), None)
            # functions can be nested; walk their bodies for inner defs
            for child in node.body:
                yield from visit(child, prefix + "." + node.name)
        else:
            for child in ast.iter_child_nodes(node):
                yield from visit(child, prefix)

    yield from visit(tree, "")


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #
def scan_file(path: Path, findings: list[Finding], zero_callers: bool,
              caller_index: Optional[dict]) -> None:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return  # unparseable: not our concern here

    tree.__module_name__ = str(path.relative_to(SRC)).replace(os.sep, ".").removesuffix(".py")
    rel = str(path.relative_to(REPO_ROOT))

    for node, qualname, enclosing_abstract in _iter_functions(tree):
        doc = ast.get_docstring(node)
        if not doc or not doc.strip():
            continue
        if _is_abstractmethod(node):
            continue
        # Methods inside an abstract / Protocol class are contractual stubs.
        if enclosing_abstract is True:
            continue

        kind = _body_kind(node.body)
        if kind in ("empty", "pass", "return_none", "not_implemented"):
            findings.append(Finding(
                category="STUB_BODY",
                path=rel,
                lineno=node.lineno,
                qualname=tree.__module_name__ + ":" + qualname,
                docstring_excerpt=doc.strip().splitlines()[0][:120],
                detail="body is %s" % kind,
            ))
        elif kind == "ellipsis":
            findings.append(Finding(
                category="ELLIPSIS_BODY",
                path=rel,
                lineno=node.lineno,
                qualname=tree.__module_name__ + ":" + qualname,
                docstring_excerpt=doc.strip().splitlines()[0][:120],
                detail="body is ... (likely interface placeholder)",
            ))

        if zero_callers and caller_index is not None:
            name = node.name
            if name.startswith("_") or name in ("__init__", "setup", "teardown"):
                continue
            if caller_index.get(name, 0) == 0:
                findings.append(Finding(
                    category="ZERO_CALLERS",
                    path=rel,
                    lineno=node.lineno,
                    qualname=tree.__module_name__ + ":" + qualname,
                    docstring_excerpt=doc.strip().splitlines()[0][:120],
                    detail="no in-src reference to name '%s' (dynamic dispatch not seen)" % name,
                ))


def build_caller_index() -> dict:
    """Count, per bare identifier, how many times it is referenced in src.

    This is a crude proxy for "is this callable ever used". It cannot see
    dynamic dispatch, so it over-reports. Used only for the informational
    ZERO_CALLERS class.
    """
    counts: Counter = Counter()
    for path in Path(SRC).rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                counts[node.id] += 1
            elif isinstance(node, ast.Attribute):
                counts[node.attr] += 1
    return dict(counts)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def render(findings: list[Finding]) -> str:
    by_cat: Counter = Counter(f.category for f in findings)
    out = []
    out.append("docstring claim-vs-actual candidate scan")
    out.append("=" * 60)
    out.append("totals: " + ", ".join("%s=%d" % (k, v) for k, v in by_cat.most_common()))
    out.append("")
    for cat in ("STUB_BODY", "ELLIPSIS_BODY", "ZERO_CALLERS"):
        group = [f for f in findings if f.category == cat]
        if not group:
            continue
        out.append("--- %s (%d) ---" % (cat, len(group)))
        for f in sorted(group, key=lambda x: (x.path, x.lineno)):
            out.append("  %s:%d  %s" % (f.path, f.lineno, f.qualname))
            out.append("      doc: %s" % f.docstring_excerpt)
            if f.detail:
                out.append("      note: %s" % f.detail)
        out.append("")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=SRC, help="source root to scan (default: src/)")
    parser.add_argument("--check-zero-callers", action="store_true",
                        help="also emit INFORMATIONAL zero-caller findings (high FP)")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="write machine-readable findings to this path")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero when STUB_BODY/ELLIPSIS_BODY findings exist "
                             "(future gate mode; NOT default while a candidate)")
    args = parser.parse_args(argv)

    root = args.root
    findings: list[Finding] = []
    caller_index = build_caller_index() if args.check_zero_callers else None

    for path in sorted(Path(root).rglob("*.py")):
        if path.name == "__init__.py" and path.parent == Path(SRC):
            pass
        scan_file(path, findings, args.check_zero_callers, caller_index)

    report = render(findings)
    print(report)

    if args.json_path:
        payload = [
            {
                "category": f.category,
                "path": f.path,
                "lineno": f.lineno,
                "qualname": f.qualname,
                "docstring_excerpt": f.docstring_excerpt,
                "detail": f.detail,
            }
            for f in findings
        ]
        Path(args.json_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("\nwrote %d findings to %s" % (len(payload), args.json_path))

    stub_or_ellipsis = [f for f in findings if f.category in ("STUB_BODY", "ELLIPSIS_BODY")]
    if args.strict and stub_or_ellipsis:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
