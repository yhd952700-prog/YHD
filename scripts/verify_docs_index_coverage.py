#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_docs_index_coverage.py — the docs index must actually index the docs tree.

WHY THIS EXISTS
---------------
`docs/README.md` is the reader's map: it is what tells a person which document to
open first. Measured on 2026-10-04, itcovered **38 of 135** markdown files. The97
it missed were not obscure:

  * `docs/autonomous/` (21 files) -- including **GOVERNANCE.md**, the project's
    governing charter (the owner's autonomous-development mandate, its hard
    boundaries, and the list of matters reserved to the human owner). A reader
    who starts from the index never learns the project has a governance charter,
    let alone which decisions are not the team's to make.
  * `docs/blueprint-upgrades/` (28 files) -- the six UBX upgrade packages plus
    their template.
  * `docs/adr/` (2), `docs/security/` (1), and 14 root-level design docs
    (`CACHE-LAYER-DESIGN.md`, `MODEL-ROUTER-DESIGN.md`, `QUOTA-ACCOUNTING-DESIGN.md`,
    ...).

The index's own "目录结构" block listed 9 directories. The tree has 15. It listed
`kernel-spec/` twice. Its "已知问题" section was frozen at 2026-09-06 while the
tree kept growing.

An index that silently omits 72% of what it indexes is worse than no index,
because it is trusted: a reader concludes the project has no governance charter.

WHAT IT CHECKS
--------------
1. **Coverage** -- every non-archived markdown file under `docs/` must be named in
   `docs/README.md` (by path or by filename). Anything unlisted is reported.
2. **No phantom entries** -- every path the index claims must exist. A link that
   resolves to nothing is a promise the index cannot keep. (`verify_doc_links.py`
   checks inline links; this checks the index's own inventory tables, which are
   usually bare filenames or code spans, not links.)
3. **Directory structure block is not a duplicate** -- catches the "kernel-spec/
   listed twice" class, where a stale block contradicts the tree it describes.

`docs/archive/` is excluded by its own declared policy (preserve as-is), as is
`docs/spec/incoming/` (superseded merge drafts, tracked by MERGE-LEDGER.md).
Both exclusions are printed, not hidden -- a coverage number that quietly excludes
the governance charter would be the very defect this gate exists to prevent.

HONESTY BOUNDARY
----------------
PASS means the index names every live doc and names nothing that is absent. It
does NOT mean the index tells you the RIGHT reading order, that the summaries are
accurate, or that the tree is well-organised -- a complete index of a confusing
tree is still confusing. Ordering and prose quality stay a human judgement; this
gate only makes omission and invention mechanically visible.
"""

from __future__ import annotations

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(REPO_ROOT, "docs")
INDEX = os.path.join(DOCS, "README.md")

# Required by tests/test_guardrail_scripts.py: a verify_*.py must bootstrap
# sys.path so it can import src.* when invoked directly from the repo root.
sys.path.insert(0, REPO_ROOT)

EXCLUDED_PREFIXES = (
    "docs/archive/",
    # Superseded merge drafts; their status is tracked by MERGE-LEDGER.md inside
    # this same directory, which IS indexed.
    "docs/spec/incoming/",
)


def _norm(rel: str) -> str:
    return rel.replace("\\", "/")


def _covered_by_brace_glob(index: str, rel: str) -> bool:
    """True when `index` names `rel` inside a `{a,b,c}` brace expansion.

    `kernel-spec/{audit,capability,...}.md` is how the index legitimately names
    fourteen files at once. Missing that form would report 13 false misses and
    train the reader to ignore this gate, so it is resolved explicitly.

    Comparison is against the doc's path RELATIVE TO `docs/`, because that is the
    form the glob expands to: the pattern is `kernel-spec/audit.md`, while the
    scanned repo-relative path is `docs/kernel-spec/audit.md`. Comparing the
    pattern against the bare basename can never match -- which is exactly the
    silent-no-op bug this function existed to prevent, so it is asserted below.
    """
    rel_in_docs = rel[len("docs/"):] if rel.startswith("docs/") else rel
    for m in re.finditer(r"`([A-Za-z0-9_./-]*)\{([^{}]+)\}([A-Za-z0-9_.-]*)`", index):
        prefix, body, suffix = m.group(1), m.group(2), m.group(3)
        for part in body.split(","):
            if (prefix + part.strip() + suffix) == rel_in_docs:
                return True
    return False


def _selftest_brace_glob() -> None:
    """Prove the glob resolver is not a no-op before trusting its verdict.

    A coverage gate that silently reports zero matches would report 100% FAIL on
    correctly-indexed files, or -- worse, after a future edit -- 100% PASS. Either
    way the number becomes fiction. One cheap assertion removes that possibility.
    """
    sample = "see `kernel-spec/{audit,capability}.md` for details"
    assert _covered_by_brace_glob(sample, "docs/kernel-spec/audit.md"), (
        "brace-glob resolver failed its self-test: a correctly indexed doc was reported missing"
    )
    assert _covered_by_brace_glob(sample, "docs/kernel-spec/capability.md")
    assert not _covered_by_brace_glob(sample, "docs/kernel-spec/plugin.md"), (
        "brace-glob resolver over-matches: it must not claim files outside the expansion"
    )


def _live_docs() -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(DOCS):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__"}]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            rel = _norm(os.path.relpath(full, REPO_ROOT))
            if rel.startswith(EXCLUDED_PREFIXES):
                continue
            out.append(rel)
    return sorted(out)


def main() -> int:
    failures: list = []
    notes: list = []

    _selftest_brace_glob()

    if not os.path.isfile(INDEX):
        print(f"FAIL: no index at {INDEX} -- the docs tree has no entry point.")
        return 1
    with open(INDEX, encoding="utf-8") as fh:
        index = fh.read()

    live = _live_docs()
    # The index cannot be required to name itself.
    targets = [p for p in live if p != "docs/README.md"]

    uncovered = []
    covered_by_glob = []
    for rel in targets:
        name = os.path.basename(rel)
        # Named either by full repo-relative path, by bare filename, or inside a
        # brace-expansion glob such as `kernel-spec/{audit,capability,...}.md`.
        # The glob form IS a real, complete index entry -- counting it as a miss
        # would be a false positive, and a coverage gate that cries wolf on a
        # correctly-indexed file is a gate people disable.
        if rel in index or name in index:
            continue
        if _covered_by_brace_glob(index, rel):
            covered_by_glob.append(rel)
            continue
        uncovered.append(rel)

    # ---- phantom entries: paths the index names but that do not exist ----
    # Only code spans that are NOT the text of a markdown link are treated as
    # claimed paths. An earlier version scanned every `` `x.md` `` and flagged
    # `[`GOVERNANCE.md`](autonomous/GOVERNANCE.md)` -- a link whose LABEL is a
    # short name and whose TARGET is correct. Flagging that would punish a
    # perfectly good index entry, i.e. the same false-positive failure mode as the
    # brace-glob bug. A link is validated by verify_doc_links.py; what this gate
    # catches is a bare path claim that resolves to nothing.
    linked_spans = [m.span() for m in re.finditer(r"\[[^\]]*\]\([^)\s]+\)", index)]

    def _inside_link(start: int, end: int) -> bool:
        return any(s <= start and end <= e for s, e in linked_spans)

    claimed = set()
    claimed_short = set()
    for m in re.finditer(r"`([A-Za-z0-9_./-]+\.md)`", index):
        token = m.group(1)
        if _inside_link(*m.span(1)):
            # It is the LABEL of a markdown link. The label is display text and
            # may legitimately be a short name (`GOVERNANCE.md`) while the target
            # carries the full path. verify_doc_links.py validates the target.
            continue
        if "/" in token:
            claimed.add(token)  # a path claim: must resolve
        else:
            # A bare filename in prose ("每包四篇：ADR.md · architecture-delta.md")
            # names a FILE KIND shared by many packages, not one location. There is
            # no single path it could resolve to, so requiring one would be
            # nonsense. The package tables carry the actual per-file links.
            claimed_short.add(token)

    phantoms = []
    for c in sorted(claimed):
        cands = [c, _norm(os.path.join("docs", c)), _norm(os.path.join(REPO_ROOT, c))]
        if any(os.path.exists(p if os.path.isabs(p) else os.path.join(REPO_ROOT, p)) for p in cands):
            continue
        if c.startswith("docs/") and os.path.exists(os.path.join(REPO_ROOT, c)):
            continue
        phantoms.append(c)

    # ---- duplicated directory entries in the structure block ----
    struct_block = ""
    for m in re.finditer(r"```(?:text)?\n(.*?)```", index, re.S):
        if "docs/" in m.group(1) and ("├──" in m.group(1) or "└──" in m.group(1)):
            struct_block = m.group(1)
            break
    dupes = []
    if struct_block:
        seen = {}
        for line in struct_block.split("\n"):
            m = re.search(r"[├└]──\s*([A-Za-z0-9_.-]+)/", line)
            if not m:
                continue
            d = m.group(1)
            seen[d] = seen.get(d, 0) + 1
        dupes = [f"{d} (x{n})" for d, n in seen.items() if n > 1]

    # ---- report -------------------------------------------------------
    total = len(targets)
    covered = total - len(uncovered)
    pct = (covered / total * 100) if total else 0.0

    print(f"live docs (excl. archive/ + spec/incoming/): {total}")
    print(f"named in docs/README.md:                    {covered}  ({pct:.1f}%)")
    print(f"  of which via brace-glob rows:              {len(covered_by_glob)}")
    print(f"NOT named:                                   {len(uncovered)}")

    if uncovered:
        from collections import Counter

        print("\nuncovered by directory:")
        for d, n in sorted(Counter(os.path.dirname(p) for p in uncovered).items(), key=lambda kv: -kv[1]):
            print(f"  {n:3d}  {d}/")
        print("\n  the full list:")
        for p in uncovered:
            print(f"    {p}")
        failures.append(
            f"{len(uncovered)} of {total} live docs are not named in docs/README.md."
        )

    if phantoms:
        print(f"\nphantom entries -- the index names {len(phantoms)} path(s) that do not exist:")
        for p in phantoms:
            print(f"    {p}")
        failures.append(
            f"{len(phantoms)} path(s) named in the index do not exist. An index entry is a "
            "promise; a promise that cannot be kept misleads exactly the readers who "
            "trusted it."
        )

    if dupes:
        print(f"\nduplicated directory entries in the structure block: {dupes}")
        failures.append(
            f"the index's directory-structure block lists {dupes} more than once. A block "
            "that contradicts the tree it describes is the staleness this gate is meant "
            "to stop."
        )

    if struct_block:
        listed = set(re.findall(r"[├└]──\s*([A-Za-z0-9_.-]+)/", struct_block))
        # `drafts` and `incoming` are nested one level down (archive/drafts/,
        # spec/incoming/) and are legitimately drawn in the structure diagram.
        # They are not top-level directories, so comparing the flat set against
        # the top-level listing produced a phantom "directory that does not
        # exist" -- the gate accusing the index of the very nesting it documents.
        nested = {
            d
            for d in listed
            if os.path.isdir(os.path.join(DOCS, "archive", d))
            or os.path.isdir(os.path.join(DOCS, "spec", d))
        }
        listed -= nested
        listed.discard("archive")
        actual = {
            d
            for d in os.listdir(DOCS)
            if os.path.isdir(os.path.join(DOCS, d)) and d != "__pycache__"
        }
        actual.discard("archive")  # listed explicitly, just with children inline
        missing = sorted(actual - listed)
        if missing:
            print(f"\ndirectories present in docs/ but absent from the structure block: {missing}")
            notes.append(
                f"structure block omits {len(missing)} existing director(ies): {missing}"
            )
        phantom_dirs = sorted(listed - actual)
        if phantom_dirs:
            print(f"\ndirectories in the structure block that do not exist: {phantom_dirs}")
            failures.append(
                f"the structure block lists non-existent directories {phantom_dirs}."
            )

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)} problem(s))")
        for f in failures:
            print(f"  - {f}")
        for n in notes:
            print(f"  (note) {n}")
        return 1

    print("RESULT: PASS")
    print(f"  - all {total} live docs are named in the index")
    print("  - every path the index names exists")
    print("  - the directory-structure block neither duplicates nor invents a directory")
    for n in notes:
        print(f"  (note) {n}")
    print()
    print("  BOUNDARY: coverage, not judgement. PASS does NOT mean the reading order")
    print("  is right or the summaries are accurate -- only that nothing is omitted and")
    print("  nothing is invented. Ordering remains a human decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
