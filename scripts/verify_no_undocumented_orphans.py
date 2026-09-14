#!/usr/bin/env python
"""Fail when a ``src/`` module becomes unreferenced without being acknowledged.

Why this exists
---------------
Every silent defect found in this repository so far shared one shape: a module
was **written but never called**.  ``src/distribution/msg_bus.py`` reported
success while dropping messages; ``MultiTierCache`` claimed three tiers and had
one; ``BatchCache.put_batch`` deadlocked on every call; ``Evaluator`` and
``generate_metrics()`` were orphans.  None of it could be caught by tests,
because nothing exercised the code.

An orphan is not automatically a bug -- some modules are libraries, some are
deliberately staged.  The problem is an orphan that **nobody decided about**.
This script turns that into a mechanical question:

    every zero-reference ``src/`` module must be listed in
    ``orphan-registry.yaml`` together with a reason.

Exit codes
----------
0  every orphan is acknowledged (or there are none)
1  at least one unacknowledged orphan -- a decision is missing
2  the registry itself is malformed (unreadable / missing a reason)

Limitations, stated honestly
----------------------------
* Reachability is decided by a **text heuristic** (dotted path, import
  statement, or quoted dotted path), not by an import graph.  It can miss
  exotic dynamic loading and can be fooled by generic short names, which is
  precisely why the registry exists: the script forces a *human decision*
  rather than pretending to be omniscient.
* "Referenced by tests only" is reported but **not** gated on.  It is a signal
  worth seeing, not a pass/fail contract.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

# --- sys.path bootstrap ----------------------------------------------------
# tests/test_guardrail_scripts.py requires every scripts/verify_*.py to be
# runnable directly (`python scripts/foo.py` from the repository root), so
# `import src...` must resolve without PYTHONPATH being set.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SKIP_DIR_PARTS = {
    "__pycache__", "node_modules", ".venv", "venv", "dist", "build",
    ".git", ".mypy_cache", ".pytest_cache", "site-packages",
}
#: Directories whose contents count as "code that could import something".
SCAN_DIRS = ("src", "tests", "scripts", "apps", "LiuHao-O", "configs", "alembic")
#: Only these extensions are treated as part of the reference corpus.  Docs are
#: deliberately excluded: a mention in prose is not a call site.
SCAN_SUFFIXES = {".py", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini"}
DEFAULT_REGISTRY = REPO_ROOT / "orphan-registry.yaml"


def module_name(path: pathlib.Path, root: pathlib.Path) -> str:
    """Dotted module name for ``path``, stripping a trailing ``__init__``."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def iter_modules(root: pathlib.Path):
    """Yield ``(module_name, path)`` for every module under ``src/``."""
    src = root / "src"
    if not src.is_dir():
        return
    for path in sorted(src.rglob("*.py")):
        if SKIP_DIR_PARTS & set(path.parts):
            continue
        name = module_name(path, root)
        if name and name != "src":
            yield name, path


def read_corpus(root: pathlib.Path):
    """Return ``[(path, text)]`` for every file that could reference a module."""
    corpus = []
    for d in SCAN_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if SKIP_DIR_PARTS & set(path.parts):
                continue
            try:
                corpus.append((path, path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return corpus


def classify(mod: str, self_path: pathlib.Path, corpus, root: pathlib.Path) -> str:
    """Return ``"prod"``, ``"tests"`` or ``"none"`` for ``mod``'s reachability."""
    short = mod.rsplit(".", 1)[-1]
    dotted_re = re.compile(re.escape(mod))
    short_re = re.compile(rf"(?:from|import)\s+[\w\.]*\b{re.escape(short)}\b")
    quoted_re = re.compile(rf"[\"']{re.escape(mod)}[\"']")
    tests_dir = root / "tests"
    seen_tests_only = False
    for path, text in corpus:
        if path == self_path:
            continue
        if not (dotted_re.search(text) or short_re.search(text) or quoted_re.search(text)):
            continue
        try:
            is_test = path.relative_to(root).parts[0] == tests_dir.name
        except ValueError:
            is_test = False
        if not is_test:
            return "prod"
        seen_tests_only = True
    return "tests" if seen_tests_only else "none"


def find_orphans(root: pathlib.Path):
    """Return ``(orphans, test_only)`` -- both as sorted lists of module names."""
    corpus = read_corpus(root)
    orphans, test_only = [], []
    for name, path in iter_modules(root):
        verdict = classify(name, path, corpus, root)
        if verdict == "none":
            orphans.append(name)
        elif verdict == "tests":
            test_only.append(name)
    return sorted(orphans), sorted(test_only)


def load_registry(registry_path: pathlib.Path):
    """Load acknowledgements: ``{module: {"reason": str, "classification": str}}``."""
    if not registry_path.is_file():
        return {}
    try:
        import yaml  # noqa: PLC0415 - optional at call time, required in CI
    except ImportError as exc:  # pragma: no cover - CI installs pyyaml
        raise SystemExit(f"ERROR: pyyaml is required to read {registry_path}: {exc}")
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    entries = data.get("acknowledged") or []
    if not isinstance(entries, list):
        raise SystemExit("ERROR: orphan-registry.yaml 'acknowledged' must be a list")
    out = {}
    for item in entries:
        if not isinstance(item, dict) or not item.get("module"):
            raise SystemExit(f"ERROR: malformed registry entry: {item!r}")
        reason = (item.get("reason") or "").strip()
        if not reason:
            raise SystemExit(
                f"ERROR: {item['module']} has no 'reason'. An acknowledgement "
                "without a reason is not a decision."
            )
        out[item["module"]] = {
            "reason": reason,
            "classification": item.get("classification", "unspecified"),
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--list", action="store_true", help="report and always exit 0")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    registry_path = pathlib.Path(args.registry)
    if not registry_path.is_absolute():
        registry_path = root / registry_path

    orphans, test_only = find_orphans(root)
    acknowledged = load_registry(registry_path)
    unacknowledged = [m for m in orphans if m not in acknowledged]
    stale = [m for m in acknowledged if m not in orphans]

    print(f"[orphan-audit] scanned {root}")
    print(f"[orphan-audit] zero-reference modules : {len(orphans)}")
    print(f"[orphan-audit] acknowledged           : {len(acknowledged)}")
    print(f"[orphan-audit] test-only reachable    : {len(test_only)}")

    if test_only:
        print("\n  test-only reachable (informational, not gated):")
        for m in test_only:
            print(f"    - {m}")

    if orphans:
        print("\n  zero-reference modules:")
        for m in orphans:
            mark = "ack" if m in acknowledged else "UNACKNOWLEDGED"
            note = acknowledged.get(m, {}).get("classification", "")
            print(f"    [{mark}] {m}" + (f"  ({note})" if note else ""))

    if stale:
        print(
            "\n  WARNING: acknowledged but no longer an orphan -- remove these "
            "entries so the registry cannot rot:",
            file=sys.stderr,
        )
        for m in stale:
            print(f"    - {m}", file=sys.stderr)

    if args.list:
        return 0

    if unacknowledged:
        print(
            f"\nFAIL: {len(unacknowledged)} src/ module(s) have zero references and "
            "are not acknowledged.\n"
            "Either wire them up, delete them, or add them to "
            "orphan-registry.yaml with a reason. Silently unused code is where "
            "this repository's 'reports success but does nothing' defects live.",
            file=sys.stderr,
        )
        return 1

    print("\nOK: every zero-reference module is acknowledged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
