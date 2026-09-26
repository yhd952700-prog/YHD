#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lint_audit_claims.py — HC-01 / HC-09 / HC-10 audit claim guard + compliance/liability claim guard.

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
violates a red line — i.e. an HC-01 tamper/evidence-source claim, an
HC-09/HC-10 durability/authority claim, or a compliance/liability claim
(GDPR/CCPA/PII/privacy/legal-accountability) made WITHOUT the required
caveat (on the same line or within a small context window). The
compliance/liability guard is a defensive net against fake external legal
assurance (U36/U37/HD-06) — it does NOT constitute compliance and does NOT
replace human legal review.

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
    {
        # ----------------------------------------------------------------- #
        # Third red line (U36 / U37 / HD-06): compliance & legal-liability
        # claims. LIUHAO processes PII but has NO implemented data-protection
        # (GDPR/CCPA) framework, and NO autonomous-action legal-liability /
        # accountability framework — both are HUMAN DECISION REQUIRED and
        # currently Frozen/TBD. A doc may therefore NOT assert we *are*
        # compliant / PII-protected / legally accountable WITHOUT the
        # HUMAN DECISION / Frozen / TBD / 'no framework yet' caveat. This is a
        # defensive net against fake external legal assurance — it does NOT
        # constitute compliance and does NOT replace human legal review.
        # ----------------------------------------------------------------- #
        "group": "COMPLIANCE-LIABILITY-CLAIMS",
        "mode": "claim",
        "window": 2,  # legal caveats often sit in a parenthesis / footnote
        # Affirmative-polarity ONLY, and ALWAYS with a legal/regulatory
        # qualifier. We NEVER bare-match "compliant" / "authorized" (those are
        # overloaded in technical contexts — spec-compliant, the authorization
        # framework U38/U42/U44, roadmap maturity).
        "claims": [
            ("GDPR", re.compile(r"GDPR[- ]?compliant", re.IGNORECASE)),
            ("GDPR", re.compile(r"compl(iant|y)\s+with\s+(the\s+)?GDPR", re.IGNORECASE)),
            ("GDPR", re.compile(r"符合\s*(欧盟\s*)?GDPR")),
            ("GDPR", re.compile(r"GDPR\s*合规")),
            ("CCPA", re.compile(r"CCPA[- ]?compliant", re.IGNORECASE)),
            ("CCPA", re.compile(r"符合\s*CCPA")),
            ("CCPA", re.compile(r"CCPA\s*合规")),
            ("DATA-PROTECTION", re.compile(r"data[- ]?protection\s+compliant", re.IGNORECASE)),
            ("DATA-PROTECTION", re.compile(r"数据保护合规")),
            ("PII", re.compile(r"PII\s+(is|are)\s+(protected|safe|secured)", re.IGNORECASE)),
            ("PII", re.compile(r"PII\s*(已|受)保护")),
            ("PII", re.compile(r"个人信息(已|受)保护")),
            ("PRIVACY", re.compile(r"privacy[- ]?protected", re.IGNORECASE)),
            ("PRIVACY", re.compile(r"隐私(已|受)保护")),
            ("DATA-SUBJECT-RIGHTS", re.compile(r"data[- ]?subject\s+rights?\s+(?:are\s+)?(?:\w+\s+){0,3}(supported|implemented|available|enforced)", re.IGNORECASE)),
            ("DATA-SUBJECT-RIGHTS", re.compile(r"(数据主体)?(访问|更正|删除|被遗忘)权(?:已|已经)?(实现|支持|提供)")),
            ("LEGAL-ACCOUNTABLE", re.compile(r"legally\s+(accountable|liable|responsible|compliant|vetted|binding)", re.IGNORECASE)),
            ("LEGAL-ACCOUNTABLE", re.compile(r"法律(上)?(问责|责任|合规|审查|约束)")),
            ("LIABLE", re.compile(r"liable\s+for\s+(autonomous|agent|ai)\s+(actions|acts|decisions)", re.IGNORECASE)),
            ("LIABLE", re.compile(r"(对)?(自主|智能体)行动(承担)?法律(责任|赔偿)")),
            ("AUTHORIZED-LAW", re.compile(r"authorized\s+(to\s+act|by\s+law|legally)(\s+on\s+your\s+behalf)?", re.IGNORECASE)),
            ("AUTHORIZED-LAW", re.compile(r"经法律授权(代表你)?")),
            ("AUTHORIZED-LAW", re.compile(r"合法授权")),
            ("CERTIFIED", re.compile(r"\bSOC\s?2\b", re.IGNORECASE)),
            ("CERTIFIED", re.compile(r"ISO\s?27001", re.IGNORECASE)),
        ],
        # Negation exclusion: a line asserting absence / non-status is NOT an
        # affirmative claim even if a token matched (defensive; honest
        # discovery / decision text must never fire).
        "negate": [
            re.compile(r"no\s+(data-protection|liability|framework|compliance)", re.IGNORECASE),
            re.compile(r"\bmissing\b|\bgap\b|\blacking\b|\babsent\b", re.IGNORECASE),
            re.compile(r"未(实现|定义|建立|提供|支持|分配)"),
            re.compile(r"缺少|缺失"),
            re.compile(r"没有.{0,10}(框架|合规|数据保护|责任)"),
            re.compile(r"DISCOVERED", re.IGNORECASE),
            re.compile(r"HUMAN\s+DECISION", re.IGNORECASE),
            re.compile(r"Frozen|TBD", re.IGNORECASE),
            re.compile(r"冻结|待定"),
            re.compile(r"\bU36\b|\bU37\b|HD-06"),
        ],
        # Near-token negation (claim mode only): a negation word immediately
        # before the matched token makes the statement negative ("NOT GDPR
        # compliant"), not an affirmative claim — skip it.
        "negate_words": [
            re.compile(r"\b(not|n't|never)\b", re.IGNORECASE),
            re.compile(r"(不|未|没有|无)(是|符合|具备|提供|实现|受|对)?"),
        ],
        # Required caveat for the compliance/liability class.
        "caveats": [
            re.compile(r"HUMAN\s+DECISION\s+REQUIRED", re.IGNORECASE),
            re.compile(r"HUMAN\s+DECISION", re.IGNORECASE),
            re.compile(r"人类决策"),
            re.compile(r"Frozen|TBD", re.IGNORECASE),
            re.compile(r"冻结|待定"),
            re.compile(r"via\s+Amendment", re.IGNORECASE),
            re.compile(r"not\s+(yet\s+)?(implemented|defined|established|allocated|assigned)", re.IGNORECASE),
            re.compile(r"未(实现|定义|建立|分配)"),
            re.compile(r"pending\s+owner\s+(decision|approval)", re.IGNORECASE),
            re.compile(r"待\s*owner(决策|批准)?"),
            re.compile(r"no\s+(data-protection|liability)\s+framework\s*(yet)?", re.IGNORECASE),
            re.compile(r"(尚)?无(数据保护|责任)框架"),
            re.compile(r"discovered\s+(gap|missing)", re.IGNORECASE),
            re.compile(r"DISCOVERED", re.IGNORECASE),
            re.compile(r"我们发现"),
            re.compile(r"\bU36\b|\bU37\b|HD-06"),
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
                    if not m:
                        continue
                    # Negation guard (claim mode only): if a negation word sits
                    # just before the matched token on the same line, the
                    # statement is negative ("NOT GDPR compliant"), not an
                    # affirmative claim — skip it (try the next token).
                    negate_words = rule.get("negate_words")
                    if negate_words:
                        pre = line[max(0, m.start() - 24):m.start()]
                        if any(nw.search(pre) for nw in negate_words):
                            continue
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

            # Per-rule negation exclusion: a line that asserts absence /
            # non-status (e.g. "no framework", "Frozen/TBD", "DISCOVERED",
            # "HUMAN DECISION", "U36/U37/HD-06") is NOT an affirmative claim
            # even if a token matched — skip it so honest discovery / decision
            # text never fires. (Conservative: applied to the claim line only.)
            negate = rule.get("negate")
            if negate and line_caveated(line, negate):
                continue

            # Per-rule context window (default to the global window).
            rule_window = rule.get("window", window)
            # Build context window [i-rule_window, i+rule_window]
            lo = max(0, i - rule_window)
            hi = min(n, i + rule_window + 1)
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
    if group == "COMPLIANCE-LIABILITY-CLAIMS":
        return ("add a HUMAN DECISION REQUIRED / Frozen / TBD / 'no framework "
                "yet' caveat — this project has NO implemented GDPR/CCPA "
                "data-protection or autonomous-action liability framework "
                "(see U36 / U37 / HD-06); these are HUMAN DECISION boundaries, "
                "not team defaults, and must not be pre-asserted in docs")
    return ("add the VOLATILE / NON-AUTHORITATIVE / IN-MEMORY-ONLY caveat "
            "(e.g. 'CryptoAuditLogger is in-memory only, VOLATILE / "
            "NON-AUTHORITATIVE — not persisted, not audit-grade; telemetry, not evidence')")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fail CI/pre-commit if docs assert HC-01 is tamper-proof / a "
                    "verified evidence source, HC-09/HC-10 are durable / "
                    "authoritative, or make a compliance/liability claim "
                    "(GDPR/CCPA/PII/legal-accountability) WITHOUT the required "
                    "caveat."
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
    print("HC-01 / HC-09 / HC-10 / compliance-liability audit & claim lint")
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
        print("PASS: no un-caveated HC-01 / HC-09 / HC-10 / compliance-liability claims found.")
        return 0

    print(f"FAIL: {len(all_violations)} un-caveated claim(s) found:\n")
    for (rel, ln, grp, tok, snippet) in all_violations:
        print(f"  [{grp}] {rel}:{ln}")
        print(f"        claim token : {tok!r}")
        print(f"        snippet     : {snippet}")
        print(f"        fix         : {_caveat_hint(grp)}")
        print()
    print("RULE (GOVERNANCE.md §7 + D19/D20 + U36/U37/HD-06):")
    print("  HC-01 audit may be 'designed tamper-evident' ONLY with the UNVERIFIED /")
    print("  forked-chain caveat. HC-09/HC-10 are VOLATILE / NON-AUTHORITATIVE by")
    print("  design — never describe them as durable / authoritative / evidence-grade.")
    print("  Compliance/liability claims (GDPR/CCPA/PII/privacy/legal-accountability)")
    print("  require a HUMAN DECISION / Frozen / TBD / 'no framework yet' caveat —")
    print("  this project has NO implemented data-protection or autonomous-action")
    print("  liability framework; pre-asserting compliance is a fake external assurance.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
