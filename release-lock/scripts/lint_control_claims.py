#!/usr/bin/env python
"""Control-claim guard -- "an assertion that a control is in force must point
at a verifiable basis, or be explicitly marked UNVERIFIED".

WHY THIS EXISTS
===============
This repo's recurring defect shape is *declared capability vs actual execution
with no connective tissue*: an ADR said a `code-level freeze` existed (it did
not -- corrected in 6922c8a5); CI gates were pinned to a non-working branch
(fixed in 2eb15d35); un-wired gate scripts were read as "protection"; a
frontend docstring promised a cross-origin `X-Trace-ID` the backend never
exposed. Same shape every time: a *control claim* with no basis.

Cleaning such claims up "line by line" is an UNREACHABLE, NEVER-CONVERGING
gate. What converges is a **decidable, ratcheted criterion**: the guard fails
on a *new* un-dispositioned assertive control claim; the existing set is frozen
in a baseline ledger and adjudicated there. This is honest in three states and
never collapses to two.

THE CRITERION (three-state; NEVER two-state)
============================================
A line is an ASSERTIVE CONTROL CLAIM when it asserts a control is *currently*
in force (see ASSERTIVE). Soft/non-assertive modality -- "designed to",
"intended to", "should", "recommended", "planned", "roadmap", "may", "target"
(设计为/应当/建议/计划/规划/待) -- is NOT a claim.

For every ASSERTIVE claim a basis marker must appear in a +/- window:
  * an explicit state tag (UNVERIFIED | NOT VERIFIED | 未验证 | 待验证 |
    NOT_IMPLEMENTED | FALSIFIED | 已证伪 | 不成立)  -> NOT VERIFIED (allowed)
  * a repo artefact / commit / file:line / code ident -> DISPOSITIONED
  * nothing                                            -> UNDISPOSITIONED (FAIL)

An UNDISPOSITIONED claim is a **false-guarantee candidate**: a control asserted
in force with no basis pointer. The guard does NOT judge the pointer's truth --
the ledger (/docs/governance/CONTROL-CLAIM-LEDGER.md) adjudicates each one into
PASS / FALSIFIED / NOT-VERIFIED. The guard only forbids leaving one un-looked-at.

CROSS-LAYER RULE (ledger-enforced, flagged here):
a claim about ANOTHER layer's behaviour -- "the backend will send X", "CI
enforces Y", "the server rejects Z" -- needs the basis on THAT layer's artefact,
not only the speaker's. One side alone = false guarantee. Flagged as XL.

Exit: 0 no NEW un-dispositioned claim (or all in baseline); 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import re
from typing import List

# --- ASSERTIVE control claims: a control *currently in force* ----------------
# Deliberately tight: bare "blocks/denies" is noise (a "Blocks" table column,
# "blocks P6"); we require a control object or an explicit modal.
ASSERTIVE = [
    re.compile(r"\b(?:is|are|remains?|becomes?)\s+enforced\b", re.IGNORECASE),
    re.compile(r"\benforces\b", re.IGNORECASE),
    re.compile(r"\bguarantee(?:s|d)?\b", re.IGNORECASE),
    re.compile(r"\bwill\s+(?:block|deny|abort|fail|reject|prevent)\b", re.IGNORECASE),
    re.compile(r"\b(?:blocks|denies|rejects|prevents)\s+(?:the|every|any|all|"
               r"this|that|it)\b", re.IGNORECASE),
    re.compile(r"\bmust\s+(?:reject|block|abort|fail|deny)\b", re.IGNORECASE),
    re.compile(r"\bcannot\s+be\s+(?:changed|removed|altered|read|bypassed|rewritten|"
               r"deleted)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+always\b|\bcan\s+never\s+be\b", re.IGNORECASE),
    re.compile(r"tamper[- ]?proof|\bimmutabl", re.IGNORECASE),
    re.compile(r"不(?:可|能)(?:篡改|删除|修改|绕过|伪造)"),
    # require an explicit control-object after 强制 (bare 强制 is a process
    # instruction -- "强制 SERIALIZE", "强制约束" -- not a control claim)
    re.compile(r"强制(?:执行|拦截|阻断|校验|检查|拒绝|放行)|会阻断|会拒绝|会拦截"),
]

# --- soft / non-assertive modality: NOT a claim -----------------------------
SOFT = [
    re.compile(r"\bdesigned\s+to\b|\bintended\s+to\b|\baims?\s+to\b|"
               r"\bintended\s+guarantee\b|\bintended\s+behaviou?r\b", re.IGNORECASE),
    re.compile(r"\b(?:should|recommend(?:ed|s|ation)?|planned|roadmap|proposed|"
               r"proposal|aspirational|future\s+work|candidate)\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+guarantee|\bno\s+guarantee|\bnot\s+guaranteed\b",
               re.IGNORECASE),
    re.compile(r"设计为|应当|建议|计划|规划|待(?:实现|定|决)|拟|不做.*保证|不保证|不强制"),
]

# --- negation of a claim on the same line: NOT a claim ----------------------
NEGATE = [
    re.compile(r"\bnot\b[^.]*\b(?:enforced|guarantee|block|deny|deleted|changed|read)\b",
               re.IGNORECASE),
    re.compile(r"\bdoes\s+not\b|\bdoesn't\b|\bno\s+longer\b|\bnever\b|\bno\b\s+\w*\s*"
               r"(?:guarantee|enforce|control)", re.IGNORECASE),
    re.compile(r"不(?:是|再|做|会|能)|无(?:法|保证|强制)|未(?:强制执行|强制)"),
]

TAG_UNVERIFIED = re.compile(
    r"UNVERIFIED|NOT\s+VERIFIED|未验证|待验证|NOT_IMPLEMENTED|已知红|FALSIFIED|"
    r"已证伪|不成立|不构成保证|unsupported\s+by\s+code|does\s+not\s+guarantee|"
    r"no\s+guarantee|not\s+guaranteed|telemetry-grade|NON-AUTHORITATIVE|"
    r"NOT\s+guarantee|不做.{0,6}保证",
    re.IGNORECASE,
)
EVIDENCE = re.compile(
    r"[\w][\w./-]*\.(?:py|yml|yaml|ts|tsx|js|sh|md|json|toml|sqlite|db)\b"
    r"|\b[0-9a-f]{7,40}\b"
    r"|:\d{1,6}\b"
    r"|`[^`]+`",
    re.IGNORECASE,
)

CROSS_LAYER = re.compile(
    r"\b(backend|frontend|gateway|server|client|browser|the\s+job|workflow|"
    r"handler|proxy|nginx|cors)\b[^.]*\b(will|returns?|sends?|exposes?|rejects?|"
    r"blocks?|denies?|enforces?|provides?|sets?)\b"
    r"|后端|前端|网关|服务端|客户端|浏览器|作业",
    re.IGNORECASE,
)

EXCLUDE_GLOBS = (
    "docs/archive/",
    "node_modules/",
    ".git/",
    "docs/HUMAN-DECISIONS.md",
    # The ledger is this guard's own rulebook + adjudication registry: it must
    # quote control language to DEFINE the criterion and to record already-
    # adjudicated claims, so scanning it is guaranteed self-noise. It is the
    # place truth is decided, not a document asserting controls about LIUHAO.
    "docs/governance/CONTROL-CLAIM-LEDGER.md",
)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_FILE = os.path.join(_ROOT, "scripts", "control_claim_baseline.txt")

# --- LEDGER proposal-hook check (a DIFFERENT rule from the claim scan) -------
# The ledger is EXCLUDED from the assertive-claim scan above on purpose (it must
# quote control language to define the criterion). This is a separate STRUCTURE
# rule that applies ONLY to the ledger: every proposal line -- a markdown list
# item carrying the proposal phrase -- must carry an explicit review-date hook
#   （提出 YYYY-MM-DD；复核 YYYY-MM-DD）
# so an un-landed proposal becomes VISIBLY due instead of silently rotting. The
# machine only checks the hook is PRESENT (deterministic, clock-free); whether the
# date has passed, and whether to wire or delete, stays human. Explicitly NOT a
# wall-clock-red gate.
LEDGER_FILE = os.path.join(_ROOT, "docs", "governance", "CONTROL-CLAIM-LEDGER.md")
PROPOSAL_PHRASE = "提案，未接线"
PROPOSAL_HOOK = re.compile(r"提出\s*\d{4}-\d{2}-\d{2}.*?复核\s*\d{4}-\d{2}-\d{2}")
_LIST_ITEM = re.compile(r"^\s*[-*]\s+")
DEFAULT_WINDOW = 2


def classify_line(line: str) -> str:
    if any(p.search(line) for p in NEGATE):
        return "na"
    if any(p.search(line) for p in SOFT):
        return "na"
    if any(p.search(line) for p in ASSERTIVE):
        return "assert"
    return "na"


def classify_claim(window_text: str) -> str:
    if TAG_UNVERIFIED.search(window_text):
        return "not_verified"
    if EVIDENCE.search(window_text):
        return "dispositioned"
    return "undispositioned"


def _excluded(path: str, root: str) -> bool:
    rel = os.path.relpath(path, root).replace(os.sep, "/")
    return any(g in rel for g in EXCLUDE_GLOBS)


def scan_file(path: str, window: int = DEFAULT_WINDOW) -> List[dict]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out: List[dict] = []
    for i, line in enumerate(lines, start=1):
        if classify_line(line) != "assert":
            continue
        lo = max(0, i - 1 - window)
        hi = min(len(lines), i + window)
        verdict = classify_claim("\n".join(lines[lo:hi]))
        out.append({
            "file": path, "line": i, "verdict": verdict,
            "cross_layer": bool(CROSS_LAYER.search(line)),
            "text": line.strip()[:150],
        })
    return out


def collect_markdown(root: str) -> List[str]:
    out: List[str] = []
    skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip]
        for fn in fns:
            if fn.endswith(".md"):
                out.append(os.path.join(dp, fn))
    return sorted(out)


def _key(f: dict, root: str) -> str:
    rel = os.path.relpath(f["file"], root).replace(os.sep, "/")
    return f"{rel}:{f['line']}"


def load_baseline() -> set:
    if not os.path.exists(BASELINE_FILE):
        return set()
    with open(BASELINE_FILE, "r", encoding="utf-8") as fh:
        return {ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")}


def check_ledger_proposal_hooks() -> List[str]:
    """LEDGER-ONLY structure check -- NOT the assertive-claim scan (see note above).

    Every markdown list item carrying the proposal phrase must also carry the
    review-date hook ``（提出 YYYY-MM-DD；复核 YYYY-MM-DD）``. A proposal with no
    review date would rot silently -- the very defect this repo keeps hitting.
    Returns ``file:line`` findings (empty = OK). Prose mentions (e.g. the rule
    text that defines the hook) are not list items and are ignored, so the check
    is free of self-noise.
    """
    if not os.path.exists(LEDGER_FILE):
        return []
    findings: List[str] = []
    with open(LEDGER_FILE, "r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, start=1):
            if _LIST_ITEM.match(line) and PROPOSAL_PHRASE in line:
                if not PROPOSAL_HOOK.search(line):
                    rel = os.path.relpath(LEDGER_FILE, _ROOT).replace(os.sep, "/")
                    findings.append(f"{rel}:{i}")
    return findings


def main(argv: List[str] | None = None) -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Control-claim guard (ratcheted)")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--all", action="store_true",
                    help="scan the full default governance corpus (docs/**/*.md); "
                         "explicit form of the default and overrides positional paths")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--update-baseline", action="store_true",
                    help="freeze the current un-dispositioned set into the baseline")
    args = ap.parse_args(argv)

    if args.paths and not args.all:
        targets = [p for p in args.paths if not _excluded(p, root)]
    else:
        # Default governance surface = docs/**/*.md. `--all` is the explicit
        # form of this scan (and overrides positional paths). NOTE: code
        # docstrings (src/, apps/) are intentionally OUT of this surface; see
        # the scope boundary recorded in CONTROL-CLAIM-LEDGER.md.
        targets = [p for p in collect_markdown(os.path.join(root, "docs"))
                   if not _excluded(p, root)]

    counts = {"not_verified": 0, "dispositioned": 0, "undispositioned": 0}
    undispositioned: List[dict] = []
    cross_layer: List[dict] = []
    for path in targets:
        for f in scan_file(path, args.window):
            counts[f["verdict"]] += 1
            if f["verdict"] == "undispositioned":
                undispositioned.append(f)
            if f["cross_layer"]:
                cross_layer.append(f)

    total = sum(counts.values())
    print("=" * 72)
    print("control-claim guard (ratcheted) -- assertive claims need a basis")
    print(f"  files: {len(targets)}   window: +/-{args.window}")
    print(f"  assertive claims        : {total}")
    print(f"    NOT VERIFIED (tagged) : {counts['not_verified']}")
    print(f"    DISPOSITIONED (basis) : {counts['dispositioned']}")
    print(f"    UNDISPOSITIONED       : {counts['undispositioned']}")
    print(f"  cross-layer claims      : {len(cross_layer)}")
    if args.show:
        for f in cross_layer:
            print(f"    [XL] {_key(f, root)} ({f['verdict']}) {f['text']}")

    # LEDGER-only structure check (a SEPARATE rule from the claim scan above;
    # runs on every invocation, not a wall-clock gate).
    prop_findings = check_ledger_proposal_hooks()
    if prop_findings:
        print("-" * 72)
        print("LEDGER proposal-hook check FAILED -- proposal line(s) with no "
              "review-date hook:")
        for k in prop_findings:
            print(f"  FAIL {k}")
        print("=" * 72)
        print(f"FAIL: {len(prop_findings)} ledger proposal(s) lack "
              "（提出 YYYY-MM-DD；复核 YYYY-MM-DD）. Add the hook, or wire/delete "
              "the proposal at its review date.")
        return 1

    baseline = load_baseline()
    if args.update_baseline:
        new = sorted(_key(f, root) for f in undispositioned)
        with open(BASELINE_FILE, "w", encoding="utf-8") as fh:
            fh.write("# adjudicated control-claim baseline (file:line).\n")
            fh.write("# Each line is a known assertive claim pending/refused "
                     "adjudication in CONTROL-CLAIM-LEDGER.md.\n")
            for k in new:
                fh.write(k + "\n")
        print(f"baseline updated: {len(new)} entries -> {BASELINE_FILE}")
        return 0

    new_offenders = [f for f in undispositioned if _key(f, root) not in baseline]
    if new_offenders:
        print("-" * 72)
        print("NEW un-dispositioned control claim(s) (not in baseline):")
        for f in new_offenders:
            print(f"  FAIL {_key(f, root)}  {f['text']}")
        print("=" * 72)
        print(f"FAIL: {len(new_offenders)} new un-dispositioned control claim(s). "
              "Add a basis pointer (path/commit/file:line) or an UNVERIFIED tag; "
              "or adjudicate it in the ledger + baseline.")
        return 1
    print("PASS: no NEW un-dispositioned control claim "
          "(existing set is frozen in the baseline ledger).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
