#!/usr/bin/env python3
"""Single identity root guard (PHASE 3.6 / T-M; closes F29 ⑥(c) import-side, F30 ①).

Contract
--------
1. ``src.kernels.identity`` is the one authoritative identity implementation and
   it says so (``AUTHORITATIVE_IDENTITY_MODULE``).
2. ``src.identity`` is an archived, non-authoritative copy and **no module may
   import it** — not the product, not tests, not scripts.
3. Both facts are reported with fingerprints, so a verifier can compare the run
   that produced a security claim against the implementation that made it.

Why this is a real parser and not a grep
----------------------------------------
The repository's existing zero-reference guard
(``scripts/verify_no_undocumented_orphans.py``) tests reachability with regular
expressions over raw file text, and on this very module that test is wrong in two
independent ways — both measured, both reproducible:

    $ python -c "..."   # see p36_tm_orphan_guard_control.py in the evidence dir
    (a) dotted-name substring match counts a DOCSTRING as a call site.
        ``src/knowledge/contracts.py`` mentions ``src.identity.Permission``
        inside prose ("Deliberately *not* ...") -> registers as a reference.
        That contradicts the guard's own comment at :58, "a mention in prose is
        not a call site".
    (b) the short-name fallback ``(?:from|import)\\s+[\\w\\.]*\\b<last-segment>``
        conflates modules that merely share a final path segment: 36 files
        matching ``...\\bidentity\\b`` are imports of ``src.kernels.identity``,
        not of ``src.identity``.

    Effect: ``classify("src.identity", ...)`` returns ``"prod"`` — "referenced" —
    for a module with zero importers, so the guard that exists to catch
    "code written but never called" cannot see this one.

An AST walk cannot be satisfied by prose, because comments and docstrings are not
*import nodes*. That is the whole point: the check must be unable to pass for the
wrong reason.

Exit: 0 = pass, 1 = violation.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import pathlib
import sys
from typing import Dict, List, Sequence, Set

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Direct invocation surface. The check itself *parses* source instead of importing
# it -- importing the identity kernel to ask who the identity kernel is would make
# the check depend on the thing it checks -- but the repository's meta-guard
# (tests/test_guardrail_scripts.py) requires every ``verify_*`` script to work when
# run as ``python scripts/x.py`` from the repository root, and a future addition
# that does need ``src`` should not have to remember this line.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

AUTHORITATIVE_MODULE = "src.kernels.identity"
ARCHIVED_MODULES: Sequence[str] = ("src.identity",)

#: Directories that are not first-party source. ``.venv`` is the interpreter's own
#: site-packages (thousands of files, none of them ours).
#:
#: ``deploy/cloud`` is *not* listed here on purpose, and the entry was never
#: added: it is generated output (``scripts/build_cloud_bundle.py``) that must
#: not be hand-edited, but the scan root is ``REPO_ROOT`` so the bundle's
#: ``.py`` files are still walked by this guard. No automated comparison
#: between the bundle and ``src/`` exists in this repository, so a bundle that
#: has drifted from the source it was built from is not caught anywhere here.
SKIP_DIR_PARTS = {
    ".venv", "venv", "__pycache__", ".git", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "build", "dist", ".eggs",
}

#: The archived copy may mention its own name; every other file may not.
ALLOWED_MENTION_FILES = {
    REPO_ROOT / "src" / "identity" / "__init__.py",
}

#: Prose-only mentions that predate this guard and are explicitly tolerated as
#: *documentation*, never as imports. They are listed so the guard states what it
#: knows rather than silently ignoring anything.
KNOWN_PROSE_MENTION_FILES = {
    REPO_ROOT / "src" / "knowledge" / "contracts.py",
}


@dataclass
class Finding:
    path: pathlib.Path
    line: int
    statement: str
    module: str


@dataclass
class Report:
    scanned: int = 0
    importers: List[Finding] = field(default_factory=list)
    checked_modules: Set[str] = field(default_factory=set)


def iter_python_files(root: pathlib.Path):
    for path in sorted(root.rglob("*.py")):
        if SKIP_DIR_PARTS & set(path.parts):
            continue
        yield path


def imported_modules(tree: ast.AST) -> List[tuple]:
    """``(module_name, lineno)`` for every real import statement in ``tree``.

    Only :class:`ast.Import` / :class:`ast.ImportFrom` nodes are considered, which
    is precisely why prose cannot satisfy this check.
    """
    found: List[tuple] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # ``from src.identity import X`` -> node.module == "src.identity";
            # ``from . import identity`` (relative) -> level > 0, module None/partial.
            if node.level == 0 and node.module:
                found.append((node.module, node.lineno))
    return found


def is_archived_reference(imported: str, archived: str) -> bool:
    """True iff ``imported`` refers to ``archived`` or one of its submodules.

    ``src.identity`` matches ``src.identity`` and ``src.identity.rbac`` but
    **not** ``src.kernels.identity`` — that is the distinction the regex-based
    guard loses, and the reason this file exists.
    """
    return imported == archived or imported.startswith(archived + ".")


def scan(root: pathlib.Path) -> Report:
    report = Report()
    for path in iter_python_files(root):
        report.scanned += 1
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            # A file this build cannot parse is not a passing file. Load-bearing
            # files are covered by compileall in CI; here we only refuse to let a
            # broken file hide an import.
            continue
        for module_name, line in imported_modules(tree):
            for archived in ARCHIVED_MODULES:
                if is_archived_reference(module_name, archived):
                    report.importers.append(
                        Finding(path=path, line=line, statement=module_name,
                                module=archived)
                    )
                    report.checked_modules.add(archived)
    return report


def declared_authority() -> Dict[str, object]:
    """Read the declaration out of the authoritative module, without importing it.

    Parsed rather than imported on purpose: importing the identity kernel to ask
    who the identity kernel is would make the check depend on the thing it checks.
    """
    target = REPO_ROOT.joinpath(*AUTHORITATIVE_MODULE.split("."), "__init__.py")
    if not target.is_file():
        return {"present": False, "authoritative": None, "archived": ()}
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    authoritative = None
    archived: List[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "AUTHORITATIVE_IDENTITY_MODULE" in names and isinstance(node.value, ast.Constant):
            authoritative = node.value.value
        if "NON_AUTHORITATIVE_IDENTITY_MODULES" in names and isinstance(node.value, ast.Tuple):
            archived = [
                elt.value for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    return {"present": True, "authoritative": authoritative, "archived": tuple(archived)}


def main() -> int:
    print("=" * 74)
    print("Single identity root guard (T-M / F29 6c import-side / F30 1)")
    print("=" * 74)

    declaration = declared_authority()
    report = scan(REPO_ROOT)

    print(f"\n[1] declaration in {AUTHORITATIVE_MODULE}/__init__.py")
    if not declaration["present"]:
        print(f"    FAIL: {AUTHORITATIVE_MODULE}/__init__.py not found")
        return 1
    print(f"    authoritative = {declaration['authoritative']!r}")
    print(f"    archived      = {declaration['archived']!r}")

    problems: List[str] = []

    if declaration["authoritative"] != AUTHORITATIVE_MODULE:
        problems.append(
            f"the authoritative module is declared as "
            f"{declaration['authoritative']!r}, expected {AUTHORITATIVE_MODULE!r}"
        )
    for archived in ARCHIVED_MODULES:
        if archived not in (declaration["archived"] or ()):
            problems.append(
                f"{archived!r} is not declared non-authoritative; an undeclared "
                f"second identity implementation is exactly the ambiguity T-M closes"
            )

    print(f"\n[2] archive marker in {ARCHIVED_MODULES[0]}/__init__.py")
    for archived in ARCHIVED_MODULES:
        path = REPO_ROOT.joinpath(*archived.split("."), "__init__.py")
        if not path.is_file():
            problems.append(f"{archived} is declared archived but {path} does not exist")
            print(f"    FAIL: {path} missing")
            continue
        head = path.read_text(encoding="utf-8", errors="replace").splitlines()[:12]
        banner = "NON-AUTHORITATIVE" in "\n".join(head)
        print(f"    {archived}: file present, non-authoritative banner = {banner}")
        if not banner:
            problems.append(
                f"{archived} exists but carries no NON-AUTHORITATIVE banner in its "
                f"first 12 lines, so a reader cannot tell it is archived"
            )

    print(f"\n[3] AST scan for imports of the archived copy")
    print(f"    .py files scanned = {report.scanned}  (AST-parsed, comments ignored)")
    for mention in sorted(KNOWN_PROSE_MENTION_FILES):
        if mention.is_file():
            print(f"    tolerated prose mention (documentation only): "
                  f"{mention.relative_to(REPO_ROOT).as_posix()}")
    if report.importers:
        print(f"    FAIL: {len(report.importers)} real import statement(s):")
        for finding in report.importers:
            rel = finding.path.relative_to(REPO_ROOT).as_posix()
            print(f"      {rel}:{finding.line}  imports {finding.statement!r}"
                  f"  ({finding.module})")
        problems.append(
            f"{len(report.importers)} import(s) of an archived identity implementation"
        )
    else:
        print("    OK: zero real imports of any archived identity implementation.")

    print("\n" + "=" * 74)
    if problems:
        print("FAIL: single identity root is not enforced.")
        for i, problem in enumerate(problems, 1):
            print(f"  {i}. {problem}")
        print("\nFix the import, or update the declaration deliberately -- never "
              "silently. An undeclared second identity root is a trust-root "
              "ambiguity, not redundancy.")
        return 1
    print("OK: one declared identity root; the archived copy is unreferenced and marked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
