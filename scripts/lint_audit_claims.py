#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lint_audit_claims.py — HC-01 / HC-09 / HC-10 audit claim guard.

WHY THIS EXISTS
---------------
Two red lines govern how we may describe the audit subsystems in docs:

  * HC-01 (src.kernels.audit) — design is tamper-evident (SHA-256 hash chain),
    but its *runtime* chain-of-custody integrity is currently **UNVERIFIED**
    (forked chain: 284 broken joins / 169 duplicate-seq rows, pending F1-F6 +
    independent verification; see GOVERNANCE.md §7). A doc may call it
    "designed tamper-evident" ONLY when paired with the UNVERIFIED / fork caveat.

  * HC-09 (src/security/audit_logger.py — CryptoAuditLogger) and
    HC-10 (src/security/audit_policy.py — AuditKernel) are **VOLATILE /
    IN-MEMORY ONLY / NON-AUTHORITATIVE** by the D19/D20 decision. Their in-memory
    hash chains are NOT persisted and NOT audit-grade; the high-water-mark /
    dropped-count counters are telemetry, not evidence. A doc may never describe
    them as durable / persistent / authoritative / evidence-grade WITHOUT the
    VOLATILE / NON-AUTHORITATIVE / IN-MEMORY-ONLY caveat.

This script FAILS (exit 1) when a scanned markdown file contains a claim that
violates either red line — i.e. an HC-01 tamper/evidence-source claim, or an
HC-09/HC-10 durability/authority claim, that is NOT accompanied by the required
caveat (on the same line or within a small context window).

USAGE
-----
  # CI mode (default): scan markdown files changed vs HEAD (filtered to docs/)
  python scripts/lint_audit_claims.py

  # Scan the entire default tree (one-time baseline audit)
  python scripts/lint_audit_claims.py --all

  # Scan explicit paths (used for the planted-bad-line dry-run + pre-commit hook)
  python scripts/lint_audit_claims.py path/to/file.md [...]

  # Local pre-commit variant: scripts/pre-commit-lint.sh (stages -> lints)

EXIT CODES
  0 = no un-caveated claims found
  1 = at least one un-caveated claim found (CI / pre-commit must fail)
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
# Rules
# --------------------------------------------------------------------------- #
# Each rule has:
#   group   - label for the report
#   mode    - "claim"      : fires if ANY claim pattern matches (unless caveat)
#             "ref_assert" : fires only if a REF pattern AND an ASSERT pattern
#                            BOTH match on the line (unless caveat)
#   claims  - [(label, regex), ...]           (mode="claim")
#   refs    - [regex, ...]                    (mode="ref_assert")
#   asserts - [regex, ...]                    (mode="ref_assert")
#   caveats - [regex, ...]  (any present near the claim -> honestly qualified)
# --------------------------------------------------------------------------- #

RULES: List[dict] = [
    {
        "group": "HC-01-AUDIT",
        "mode": "claim",
        # Tamper-proof / tamper-evident / 防篡改 / 不可篡改, and true / authoritative
        # evidence-source claims. Narrowed to the audit/evidence domain so we do
        # not flag capability-registry or generic "source of truth" statements.
        "claims": [
            ("TAMPER", re.compile(r"tamper[- ]?proof", re.IGNORECASE)),
            ("TAMPER", re.compile(r"tamper[- ]?evident", re.IGNORECASE)),
            ("TAMPER", re.compile(r"防篡改")),
            ("TAMPER", re.compile(r"不可篡改")),
            ("EVIDENCE", re.compile(r"真实证据源")),
            ("EVIDENCE", re.compile(r"true\s+(evidence\s+source|source\s+of\s+truth)", re.IGNORECASE)),
            ("EVIDENCE", re.compile(r"authoritative\s+(audit|evidence|chain|source|sink)", re.IGNORECASE)),
            ("EVIDENCE", re.compile(r"权威.*(审计|证据).*(事实来源|源)")),
            ("EVIDENCE", re.compile(r"权威.*证据源")),
        ],
        "caveats": [
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
        ],
    },
    {
        "group": "HC-09-10-VOLATILE",
        "mode": "ref_assert",
        # References to the volatile crypto / policy audit subsystems.
        "refs": [
            re.compile(r"CryptoAuditLogger"),
            re.compile(r"audit_logger"),
            re.compile(r"audit_policy"),
            re.compile(r"AuditKernel"),
            re.compile(r"HC-09(/HC-10)?|HC-10|HC09|HC10", re.IGNORECASE),
            re.compile(r"crypto audit", re.IGNORECASE),
            re.compile(r"加密审计"),
            re.compile(r"src\.security\.audit_(logger|policy)"),
        ],
        # Durability / authority / evidence-grade assertions that must be paired
        # with the VOLATILE / NON-AUTHORITATIVE caveat.
        "asserts": [
            re.compile(r"durable|persistent", re.IGNORECASE),
            re.compile(r"持久化"),
            re.compile(r"authoritative\s+(evidence|source|audit|chain|sink)", re.IGNORECASE),
            re.compile(r"权威.*证据"),
            re.compile(r"evidence[- ]?grade|audit[- ]?grade", re.IGNORECASE),
            re.compile(r"证据级|审计级"),
            re.compile(r"real evidence|真实证据", re.IGNORECASE),
            re.compile(r"tamper[- ]?evident", re.IGNORECASE),
            re.compile(r"防篡改|不可篡改"),
        ],
        # Required caveat for HC-09/HC-10: definitively NON-AUTHORITATIVE.
        # (Note: "UNVERIFIED" alone is NOT sufficient here — HC-09/10 are not
        #  "unverified", they are *by design* VOLATILE / NON-AUTHORITATIVE.)
        "caveats": [
            re.compile(r"VOLATILE"),
            re.compile(r"NON-AUTHORITATIVE"),
            re.compile(r"IN-MEMORY ONLY", re.IGNORECASE),
            re.compile(r"内存态"),
            re.compile(r"非持久"),
            re.compile(r"非权威"),
            re.compile(r"not\s+(persisted|audit-grade|tamper)", re.IGNORECASE),
            re.compile(r"telemetry"),
            re.compile(r"不视为证据|非证据"),
            re.compile(r"不具防篡改链"),
        ],
    },
]

# HC-09/HC-10 module-reference patterns, reused to relabel HC-01 violations that
# actually concern the volatile crypto/policy audit subsystems (so the report
# points the author at the right red line + caveat).
HC09_REFS = RULES[1]["refs"]

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
    rel = rel_path.replace(os.sep, "/")
    for glob in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(rel, glob):
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
    return [f.strip() for f in out.splitlines() if f.strip()]


def collect_targets(args) -> List[str]:
    base_dir = os.path.abspath(args.base_dir) if args.base_dir else repo_root()
    targets: List[str] = []
    if args.paths:
        for p in args.paths:
            ap = os.path.abspath(p)
            if os.path.isfile(ap):
                relp = os.path.relpath(ap, base_dir).replace(os.sep, "/")
                if not is_excluded(relp):
                    targets.append(ap)
            elif os.path.isdir(ap):
                for root, _dirs, files in os.walk(ap):
                    for f in files:
                        if not f.lower().endswith(".md"):
                            continue
                        full = os.path.join(root, f)
                        relp = os.path.relpath(full, base_dir).replace(os.sep, "/")
                        if not is_excluded(relp):
                            targets.append(full)
    elif args.all:
        walk_root = os.path.join(base_dir, DEFAULT_BASE)
        if not os.path.isdir(walk_root):
            walk_root = base_dir
        for root, dirs, files in os.walk(walk_root):
            rel_dir = os.path.relpath(root, base_dir).replace(os.sep, "/")
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
            if not f.lower().endswith(".md"):
                continue
            relp = f.replace(os.sep, "/")
            if relp.startswith("docs/") and not is_excluded(relp):
                targets.append(os.path.join(base_dir, f))
    # de-dup, keep order
    seen = set()
    uniq = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def line_caveated(line: str, caveats: List["re.Pattern[str]"]) -> bool:
    return any(p.search(line) for p in caveats)


def scan_file(path: str, window: int) -> List[Tuple[int, str, str, str]]:
    """Return violations: (line_no, group, matched_text, snippet)."""
    violations: List[Tuple[int, str, str, str]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"  ! could not read {path}: {exc}", file=sys.stderr)
        return violations

    n = len(lines)
    for i, line in enumerate(lines):
        for rule in RULES:
            group = rule["group"]
            caveats = rule["caveats"]
            fired = False
            detail = ""
            if rule["mode"] == "claim":
                for label, pat in rule["claims"]:
                    m = pat.search(line)
                    if m:
                        fired = True
                        detail = m.group(0)
                        break
            elif rule["mode"] == "ref_assert":
                if not any(r.search(line) for r in rule["refs"]):
                    continue
                for pat in rule["asserts"]:
                    m = pat.search(line)
                    if m:
                        fired = True
                        detail = m.group(0)
                        break
            else:
                continue

            if not fired:
                continue

            # Build context window [i-window, i+window]
            lo = max(0, i - window)
            hi = min(n, i + window + 1)
            context = lines[lo:hi]
            if any(line_caveated(c, caveats) for c in context):
                continue  # honestly qualified
            snippet = line.strip()
            if len(snippet) > 200:
                snippet = snippet[:197] + "..."
            # A line that references HC-09/HC-10 but trips the HC-01 evidence
            # pattern is really an HC-09/HC-10 volatile-claim issue — relabel so
            # the author gets the VOLATILE / NON-AUTHORITATIVE caveat hint.
            out_group = group
            if group == "HC-01-AUDIT" and any(r.search(line) for r in HC09_REFS):
                out_group = "HC-09-10-VOLATILE"
            violations.append((i + 1, out_group, detail, snippet))
            break  # one report per line is enough
    return violations


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def _caveat_hint(group: str) -> str:
    if group == "HC-01-AUDIT":
        return ("add the UNVERIFIED / forked-chain caveat "
                "(e.g. 'designed tamper-evident; runtime integrity currently "
                "UNVERIFIED — forked chain, pending F1–F6 + independent verification')")
    return ("add the VOLATILE / NON-AUTHORITATIVE / IN-MEMORY-ONLY caveat "
            "(e.g. 'CryptoAuditLogger is in-memory only, VOLATILE / "
            "NON-AUTHORITATIVE — not persisted, not audit-grade; telemetry, not evidence')")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fail CI/pre-commit if docs assert HC-01 is tamper-proof / a "
                    "verified evidence source, or HC-09/HC-10 are durable / "
                    "authoritative, WITHOUT the required caveat."
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
    print("HC-01 / HC-09 / HC-10 audit claim lint")
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
        print("PASS: no un-caveated HC-01 / HC-09 / HC-10 claims found.")
        return 0

    print(f"FAIL: {len(all_violations)} un-caveated claim(s) found:\n")
    for (rel, ln, grp, tok, snippet) in all_violations:
        print(f"  [{grp}] {rel}:{ln}")
        print(f"        claim token : {tok!r}")
        print(f"        snippet     : {snippet}")
        print(f"        fix         : {_caveat_hint(grp)}")
        print()
    print("RULE (GOVERNANCE.md §7 + D19/D20):")
    print("  HC-01 audit may be 'designed tamper-evident' ONLY with the UNVERIFIED /")
    print("  forked-chain caveat. HC-09/HC-10 are VOLATILE / NON-AUTHORITATIVE by")
    print("  design — never describe them as durable / authoritative / evidence-grade.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
