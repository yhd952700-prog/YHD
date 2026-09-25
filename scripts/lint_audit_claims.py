#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lint_audit_claims.py — HC-01 audit tamper/evidence-source claim guard.

WHY THIS EXISTS
---------------
Per GOVERNANCE.md §7 and the D19/D20 red line, the HC-01 audit chain's
*design* is tamper-evident (SHA-256 hash chain), but its *runtime* chain-of-
custody integrity is currently **UNVERIFIED** (a forked chain was found:
284 broken joins / 169 duplicate-seq rows, pending F1–F6 remediation + an
independent verification). Therefore no documentation may assert that the
audit is tamper-proof / a verified true evidence source / an authoritative
intact source WITHOUT pairing that claim with the UNVERIFIED / fork caveat.

This script FAILS (exit 1) when a scanned markdown file contains an HC-01
tamper or evidence-source claim that is NOT accompanied by the required
caveat (on the same line or within a small context window).

CLAIM PATTERNS (must be paired with a caveat)
---------------------------------------------
  TAMPER        tamper-proof, tamper-evident, 防篡改, 不可篡改
  EVIDENCE      real-evidence / real-data source, true evidence source,
                true source of truth, authoritative evidence/chain/source/sink

CAVEAT PATTERNS (any present near the claim → OK)
  UNVERIFIED, 未验证, 分叉, fork(ed), 待 F1, 设计为防篡改,
  designed-to-be-tamper, runtime integrity, 当前运行时完整性,
  NON-AUTHORITATIVE, not persisted / not audit-grade / not tamper,
  不具防篡改链, volatile, in-memory only, 内存态, 非持久,
  不视为…防篡改, pending F1

USAGE
-----
  # CI mode (default): scan markdown files changed vs HEAD (filtered to docs/)
  python scripts/lint_audit_claims.py

  # Scan the entire default tree (one-time baseline audit)
  python scripts/lint_audit_claims.py --all

  # Scan explicit paths (used for the planted-bad-line dry-run test)
  python scripts/lint_audit_claims.py path/to/file.md [...]

EXIT CODES
  0 = no un-caveated HC-01 claims found
  1 = at least one un-caveated HC-01 claim found (CI must fail)
  2 = usage / environment error
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from typing import List, Tuple

# --------------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------------- #

# Claim groups. Each entry is a compiled regex (case-insensitive where useful).
CLAIM_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    # Tamper-proof / tamper-evident / 防篡改 / 不可篡改
    ("TAMPER", re.compile(r"tamper[- ]?proof", re.IGNORECASE)),
    ("TAMPER", re.compile(r"tamper[- ]?evident", re.IGNORECASE)),
    ("TAMPER", re.compile(r"防篡改")),
    ("TAMPER", re.compile(r"不可篡改")),
    # True / authoritative evidence source
    # (narrowed to audit/evidence domain to avoid flagging capability-registry
    #  or data-source "source of truth" statements that are not HC-01 claims)
    ("EVIDENCE", re.compile(r"真实证据源")),
    ("EVIDENCE", re.compile(r"true\s+(evidence\s+source|source\s+of\s+truth)", re.IGNORECASE)),
    ("EVIDENCE", re.compile(r"authoritative\s+(audit|evidence|chain|source|sink)", re.IGNORECASE)),
    ("EVIDENCE", re.compile(r"权威.*(审计|证据).*(事实来源|源)")),
    ("EVIDENCE", re.compile(r"权威.*证据源")),
]

# Caveat tokens. If any is found on the claim line or within the context
# window, the claim is considered honestly qualified.
CAVEAT_PATTERNS: List["re.Pattern[str]"] = [
    re.compile(r"UNVERIFIED"),
    re.compile(r"未验证"),
    re.compile(r"分叉"),
    re.compile(r"fork(ed)?", re.IGNORECASE),
    re.compile(r"待\s*F1"),
    re.compile(r"设计为防篡改"),
    re.compile(r"设计为.*防篡改"),
    re.compile(r"designed[- ]to[- ]be[- ]tamper", re.IGNORECASE),
    re.compile(r"runtime integrity", re.IGNORECASE),
    re.compile(r"当前运行时完整性"),
    re.compile(r"NON-AUTHORITATIVE"),
    re.compile(r"not\s+(persisted|audit-grade|tamper)", re.IGNORECASE),
    re.compile(r"不具防篡改链"),
    re.compile(r"volatile", re.IGNORECASE),
    re.compile(r"in-memory only", re.IGNORECASE),
    re.compile(r"内存态"),
    re.compile(r"非持久"),
    re.compile(r"不视为.*防篡改"),
    re.compile(r"pending\s+F1", re.IGNORECASE),
]

# Default tree to scan, and paths excluded from the scan.
DEFAULT_BASE = "docs"
EXCLUDE_GLOBS = [
    "docs/archive",
    "docs/archive/**",
    "docs/autonomous",
    "docs/autonomous/**",
    "docs/adr",
    "docs/adr/**",
    "docs/HUMAN-DECISIONS.md",
    # "Authoritative audit = src.kernels/audit" here is a designated-sink
    # locator, not a tamper-proofness claim; the UNVERIFIED status is tracked
    # elsewhere (GOVERNANCE.md §7). Excluded to avoid a false positive.
    "docs/architecture/migration-matrix.md",
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def repo_root() -> str:
    """Best-effort repo root: parent of this script's directory."""
    here = os.path.dirname(os.path.abspath(__file__))
    # scripts/ -> repo root
    return os.path.dirname(here)


def is_excluded(rel_path: str) -> bool:
    for glob in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(rel_path.replace(os.sep, "/"), glob):
            return True
    return False


def changed_doc_files(base_dir: str) -> List[str]:
    """Return docs/**/*.md files changed vs HEAD (CI mode)."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
            cwd=base_dir, stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace")
    except Exception:
        try:
            out = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=base_dir, stderr=subprocess.DEVNULL,
            ).decode("utf-8", "replace")
            files = []
            for line in out.splitlines():
                if len(line) > 3:
                    files.append(line[3:].strip())
            return files
        except Exception:
            return []
    files = [f.strip() for f in out.splitlines() if f.strip()]
    return files


def collect_targets(args) -> List[str]:
    base_dir = os.path.abspath(args.base_dir) if args.base_dir else repo_root()
    targets: List[str] = []
    if args.paths:
        for p in args.paths:
            ap = os.path.abspath(p)
            if os.path.isfile(ap):
                targets.append(ap)
            elif os.path.isdir(ap):
                for root, _dirs, files in os.walk(ap):
                    for f in files:
                        if f.lower().endswith(".md"):
                            targets.append(os.path.join(root, f))
    elif args.all:
        walk_root = os.path.join(base_dir, DEFAULT_BASE)
        if not os.path.isdir(walk_root):
            walk_root = base_dir
        for root, dirs, files in os.walk(walk_root):
            rel_dir = os.path.relpath(root, base_dir).replace(os.sep, "/")
            # prune excluded subtrees so we never descend into archives
            dirs[:] = [
                d for d in dirs
                if not is_excluded(os.path.join(rel_dir, d).replace(os.sep, "/"))
            ]
            for f in files:
                if not f.lower().endswith(".md"):
                    continue
                relp = os.path.join(rel_dir, f).replace(os.sep, "/")
                if is_excluded(relp):
                    continue
                targets.append(os.path.join(root, f))
    else:
        # CI mode: changed files under docs/
        for f in changed_doc_files(base_dir):
            if f.lower().endswith(".md") and f.replace(os.sep, "/").startswith("docs/"):
                full = os.path.join(base_dir, f)
                relp = f.replace(os.sep, "/")
                if not is_excluded(relp):
                    targets.append(full)
    # de-dup, keep order
    seen = set()
    uniq = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def line_has_caveat(line: str) -> bool:
    return any(p.search(line) for p in CAVEAT_PATTERNS)


def scan_file(path: str, window: int) -> List[Tuple[int, str, str, str]]:
    """Return violations: (line_no, claim_group, matched_text, snippet)."""
    violations: List[Tuple[int, str, str, str]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  ! could not read {path}: {exc}", file=sys.stderr)
        return violations

    n = len(lines)
    for i, line in enumerate(lines):
        for group, pat in CLAIM_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            # Build context window [i-window, i+window]
            lo = max(0, i - window)
            hi = min(n, i + window + 1)
            context = lines[lo:hi]
            if any(line_has_caveat(c) for c in context):
                continue  # honestly qualified
            snippet = line.strip()
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            violations.append((i + 1, group, m.group(0), snippet))
            break  # one report per line is enough
    return violations


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fail CI if docs assert HC-01 audit is tamper-proof / a "
                    "verified true evidence source without the UNVERIFIED/fork caveat."
    )
    parser.add_argument("paths", nargs="*", help="Explicit file/dir paths to scan")
    parser.add_argument("--all", action="store_true",
                        help="Scan the entire default tree (baseline audit)")
    parser.add_argument("--base-dir", default=None,
                        help="Repo base dir (default: auto-detected)")
    parser.add_argument("--window", type=int, default=1,
                        help="Context window (lines) around a claim to look for a caveat")
    args = parser.parse_args(argv)

    base_dir = os.path.abspath(args.base_dir) if args.base_dir else repo_root()
    targets = collect_targets(args)

    print("=" * 72)
    print("HC-01 audit claim lint")
    print(f"  base-dir : {base_dir}")
    print(f"  mode     : {'explicit' if args.paths else ('all' if args.all else 'CI/changed')}")
    print(f"  window   : ±{args.window} line(s)")
    print(f"  targets  : {len(targets)} markdown file(s)")
    print("=" * 72)

    all_violations: List[Tuple[str, int, str, str, str]] = []
    for path in targets:
        rel = os.path.relpath(path, base_dir).replace(os.sep, "/")
        v = scan_file(path, args.window)
        if v:
            for (ln, grp, tok, snippet) in v:
                all_violations.append((rel, ln, grp, tok, snippet))

    if not all_violations:
        print("PASS: no un-caveated HC-01 tamper / evidence-source claims found.")
        return 0

    print(f"FAIL: {len(all_violations)} un-caveated HC-01 claim(s) found:\n")
    for (rel, ln, grp, tok, snippet) in all_violations:
        print(f"  [{grp}] {rel}:{ln}")
        print(f"        claim token : {tok!r}")
        print(f"        snippet     : {snippet}")
        print(f"        fix         : add the UNVERIFIED / forked-chain caveat"
              f" (e.g. 'designed tamper-evident; runtime integrity currently"
              f" UNVERIFIED — forked chain, pending F1–F6 + independent verification')")
        print()
    print("RULE: HC-01 audit may be described as 'designed tamper-evident' ONLY when")
    print("paired with the UNVERIFIED / forked-chain caveat (GOVERNANCE.md §7).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
