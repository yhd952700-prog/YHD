#!/usr/bin/env python3
"""check_unknown_register.py -- reconciliation gate for UNKNOWN-TO-OWNER.md.

WHY THIS EXISTS
---------------
The discovery register (``docs/autonomous/UNKNOWN-TO-OWNER.md``) records system
blind spots, each with a status. Nothing forced a re-check when a fix landed, so
an entry could stay ``DISCOVERED`` for weeks after the commit that fixed it was
already an ancestor of ``HEAD``. That is register item **U58**: "The discovery
register has no reconciliation gate." This script is that gate.

RULE
----
An entry FAILS the gate when its status is still **open** AND it cites a commit
that is already an **ancestor of HEAD**. "Open" = the status text carries an open
marker and no closure marker, and is not a HUMAN DECISION (a pending human call
is legitimately, durably open and must not be failed).

Two *precision* exclusions keep the gate honest -- a cited commit is NOT treated
as this entry's fix when:

  (i) it is written as an arrow endpoint ``X -> Y`` (that reports a state
      *transition*, e.g. "HEAD moved c438313e -> 9595b51b", not a fix); or
  (ii) it is cited by MORE THAN ONE entry (a shared / context commit -- it
      "belongs" to another entry's closure), **unless** it is cited inside this
      entry's own status text. A commit named in the status region is always
      treated as a fix claim (the status region is where fix claims belong).

Evidence for these exclusions (measured on the register at HEAD 197a0962, when
this gate was written): a naive "any cited commit" rule produced exactly three
false positives -- U45 (cites ``5d618ac5``, which is U43's fix), U57 (cites the
movement endpoints ``c438313e``->``9595b51b``) and U58 (cites the fix commits of
U47/U48/U49/U50/U51 as evidence). All three are legitimate, correctly-open
entries. The two exclusions remove them without weakening the status-region
rule that catches the real failure mode.

RESIDUAL BLIND SPOT (honest): an entry that is fixed only by a *shared* commit
and whose status region does NOT name the commit can still escape exclusion (ii).
The going-forward discipline that closes this is: **when you fix an item, name
the fix commit in that item's ``- **Status:**`` line and flip the marker.**
--fail-on-context disables exclusion (ii) for callers who want the naive rule and
accept its false positives.

USAGE
-----
  python scripts/check_unknown_register.py [--register PATH] [--repo PATH] [--head REV]

Exit codes: 0 = clean (PASS), 1 = stale entries found (FAIL), 2 = usage/parse error.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# --- status vocabulary -------------------------------------------------------
# ASCII markers are matched on word boundaries so e.g. UNRESOLVED does not count
# as RESOLVED. CJK markers are matched as plain substrings.
_OPEN_ASCII = ("DISCOVERED", "NOT REPAIRED", "NOT FIXED", "OPEN", "TODO",
               "DOING", "IN PROGRESS", "UNVERIFIED")
_OPEN_CJK = ("未修复", "未解决", "待办")
_CLOSED_ASCII = ("REMEDIATED", "RESOLVED", "FIXED", "DONE", "BUILT", "SHIPPED",
                 "SUPERSEDED", "WON'T FIX", "WONT FIX", "RETIRED", "CLOSED",
                 "VERIFIED")
_CLOSED_CJK = ("已修复", "已解决", "已建成")
_HUMAN_ASCII = ("HUMAN DECISION", "HUMAN-DECISION")
_HUMAN_CJK = ("人类决策",)

_HEX = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
_ARROW = "\u2192"  # ->
_SECTION = re.compile(r"^##\s+(U\d+)\b")
_MINOR_HEADING = re.compile(r"^##\s")
_TABLE_ROW = re.compile(r"^\|\s*(U\d+)\s*\|")
_RULE_LINE = re.compile(r"^-{3,}\s*$")


def _has_ascii(text: str, markers) -> bool:
    up = text.upper()
    return any(re.search(r"\b" + re.escape(m) + r"\b", up) for m in markers)


def _has_cjk(text: str, markers) -> bool:
    return any(m in text for m in markers)


def _status_is_open(status_text: str) -> bool:
    if not status_text.strip():
        return False
    if _has_cjk(status_text, _HUMAN_CJK) or _has_ascii(status_text, _HUMAN_ASCII):
        return False
    closed = _has_ascii(status_text, _CLOSED_ASCII) or _has_cjk(status_text, _CLOSED_CJK)
    opened = _has_ascii(status_text, _OPEN_ASCII) or _has_cjk(status_text, _OPEN_CJK)
    return opened and not closed


def _tokens_without_arrow(line: str) -> List[str]:
    """Commit-looking tokens in ``line``, excluding arrow endpoints (X -> Y)."""
    arrows = [i for i, ch in enumerate(line) if ch == _ARROW]
    out: List[str] = []
    for m in _HEX.finditer(line):
        if any(abs(a - m.start()) <= 3 or abs(a - m.end()) <= 3 for a in arrows):
            continue
        out.append(m.group(0))
    return out


def parse_entries(text: str) -> Dict[str, Dict[str, List[str]]]:
    """Split the register into ``{entry_id: {"lines": [...], "status_texts": [...]}}``."""
    entries: Dict[str, Dict[str, List[str]]] = {}
    cur: str | None = None

    def _entry(eid: str) -> Dict[str, List[str]]:
        return entries.setdefault(eid, {"lines": [], "status_texts": []})

    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            cur = m.group(1)
            _entry(cur)
            continue
        if _MINOR_HEADING.match(line) or _RULE_LINE.match(line):
            # A non-U heading or a horizontal rule ends the current section, so a
            # section never absorbs a following table.
            cur = None
            continue
        mrow = _TABLE_ROW.match(line)
        if mrow:
            eid = mrow.group(1)
            e = _entry(eid)
            e["lines"].append(line)
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 7:
                e["status_texts"].append(cells[6])
            continue
        if cur is not None:
            entries[cur]["lines"].append(line)
            if line.strip().startswith("- **Status:**"):
                entries[cur]["status_texts"].append(line.strip())
    return entries


class _Git:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self._commit: Dict[str, bool] = {}
        self._ancestor: Dict[str, bool] = {}

    def _run(self, *args: str) -> bool:
        try:
            return subprocess.run(
                ["git", *args], cwd=str(self.repo),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def is_commit(self, tok: str) -> bool:
        if re.fullmatch(r"[0-9]+", tok) or not re.search(r"[a-f]", tok, re.I):
            return False
        if len(tok) < 7:
            return False
        if tok not in self._commit:
            self._commit[tok] = self._run("cat-file", "-e", tok + "^{commit}")
        return self._commit[tok]

    def is_ancestor_of(self, tok: str, head: str) -> bool:
        key = tok + "@" + head
        if key not in self._ancestor:
            self._ancestor[key] = self._run("merge-base", "--is-ancestor", tok, head)
        return self._ancestor[key]


def find_mismatches(register_path: Path, repo: Path, head: str = "HEAD",
                    fail_on_context: bool = False) -> List[Tuple[str, str, str]]:
    """Return ``[(entry_id, reason, evidence)]`` for every stale entry."""
    text = Path(register_path).read_text(encoding="utf-8")
    entries = parse_entries(text)
    git = _Git(Path(repo))

    # Per-entry citation sets (arrow endpoints already removed).
    all_refs: Dict[str, Set[str]] = {}
    status_refs: Dict[str, Set[str]] = {}
    for eid, e in entries.items():
        status_refs[eid] = {t for s in e["status_texts"] for t in _tokens_without_arrow(s)}
        line_toks: Set[str] = set()
        for ln in e["lines"]:
            line_toks.update(_tokens_without_arrow(ln))
        all_refs[eid] = set(status_refs[eid]) | line_toks

    # How many entries cite each commit (shared => context, see exclusion (ii)).
    shared: Dict[str, int] = {}
    for refs in all_refs.values():
        for tok in refs:
            shared[tok] = shared.get(tok, 0) + 1

    mismatches: List[Tuple[str, str, str]] = []
    for eid in sorted(entries, key=lambda k: int(k[1:])):
        e = entries[eid]
        status_text = " ".join(e["status_texts"])
        if not _status_is_open(status_text):
            continue

        ancestor = lambda t: git.is_commit(t) and git.is_ancestor_of(t, head)

        # (1) status-region citation of a landed commit => always stale.
        bad = sorted({t for t in status_refs[eid] if ancestor(t)})
        if bad:
            mismatches.append((eid, "status names a landed fix commit but stays open",
                               ", ".join(bad)))
            continue

        # (2) body citation of a landed commit => stale. Exclusion (ii) (shared /
        # context commits) is relaxed when --fail-on-context is set.
        body = all_refs[eid] - status_refs[eid]
        cand = {t for t in body if ancestor(t)}
        if not fail_on_context:
            cand = {t for t in cand if shared.get(t, 0) == 1}
        bad = sorted(cand)
        if bad:
            mismatches.append((eid, "entry cites its own landed fix commit but stays open",
                               ", ".join(bad)))
    return mismatches


def main(argv: List[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    repo_default = here.parent
    ap = argparse.ArgumentParser(description="Reconcile UNKNOWN-TO-OWNER.md vs HEAD.")
    ap.add_argument("--register", default=str(repo_default / "docs" / "autonomous" / "UNKNOWN-TO-OWNER.md"))
    ap.add_argument("--repo", default=str(repo_default))
    ap.add_argument("--head", default="HEAD", help="revision the fix commits are compared against")
    ap.add_argument("--fail-on-context", action="store_true",
                    help="disable the shared-commit exclusion (naive rule; known false positives)")
    args = ap.parse_args(argv)

    register = Path(args.register)
    repo = Path(args.repo)
    if not register.is_file():
        print(f"[unknown-register] ERROR: register not found: {register}", file=sys.stderr)
        return 2

    mismatches = find_mismatches(register, repo, args.head, args.fail_on_context)
    if mismatches:
        print(f"[unknown-register] FAIL: {len(mismatches)} stale entry/entries "
              f"(status open, but the cited fix commit is already an ancestor of {args.head}):")
        for eid, reason, evidence in mismatches:
            print(f"  - {eid}: {reason} [{evidence}]")
        print("  Fix: name the fix commit in the entry's `- **Status:**` line and flip "
              "its marker (or, if truly a context citation, it is excluded when shared).")
        return 1
    print(f"[unknown-register] OK: no stale entries "
          f"({len(parse_entries(register.read_text(encoding='utf-8')))} entries checked vs {args.head}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
