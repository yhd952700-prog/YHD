#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_compliance_lint.py — standalone (pytest-free) verifier for the
COMPLIANCE-LIABILITY-CLAIMS rule group in scripts/lint_audit_claims.py.

WHY STANDALONE
---------------
This repo's CI runs pytest, but a developer may not have pytest installed
locally. This script proves the rule's hit/miss behaviour WITHOUT any test
framework so it can be run anywhere:

    python scripts/verify_compliance_lint.py

Exit 0 = all expectations met; exit 1 = at least one case failed.

It is NOT scanned by the lint script itself (scripts/ is outside docs/).
The companion pytest test is tests/scripts/test_lint_compliance_claims.py
(identical cases, for CI).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]  # scripts/ -> repo root
SCRIPT = REPO_ROOT / "scripts" / "lint_audit_claims.py"

# A gate that only imports when some other process already fixed sys.path is a
# gate that silently fails to run. Bootstrap here so `python scripts/x.py` from
# the repo root works (asserted for every verify_*.py by
# tests/test_guardrail_scripts.py::test_every_verify_script_bootstraps_sys_path).
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location("lint_audit_claims_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)

GROUP = "COMPLIANCE-LIABILITY-CLAIMS"


def _scan(text: str):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        return mod.scan_file(path, 2)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _fires(text: str) -> bool:
    return any(g == GROUP for (_ln, g, _tok, _snip) in _scan(text))


def _check(name: str, got: bool, want: bool) -> bool:
    ok = got == want
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: fires={got} (expected={want})")
    return ok


def main() -> int:
    results = []

    # --- HIT cases: an un-caveated affirmative compliance/liability claim ---
    results.append(_check(
        "GDPR compliant (no caveat) -> FIRES",
        _fires("LIUHAO is fully GDPR compliant and PII protected.\n"), True))
    results.append(_check(
        "CCPA compliant (no caveat) -> FIRES",
        _fires("We are CCPA compliant out of the box.\n"), True))
    results.append(_check(
        "legally accountable for autonomous actions (no caveat) -> FIRES",
        _fires("The owner is legally accountable for every autonomous action.\n"), True))
    results.append(_check(
        "PII protected, ZH (no caveat) -> FIRES",
        _fires("我们的系统已确保 PII 受保护。\n"), True))
    results.append(_check(
        "data-subject rights implemented (no caveat) -> FIRES",
        _fires("Data-subject rights are fully implemented and enforced.\n"), True))
    # --- HIT (extended): other legal-assertion forms that must fire ---
    results.append(_check(
        "authorized by law (no caveat) -> FIRES",
        _fires("We are authorized by law to process your personal data.\n"), True))
    results.append(_check(
        "authorized to act on your behalf (no caveat) -> FIRES",
        _fires("LIUHAO is authorized to act on your behalf.\n"), True))
    results.append(_check(
        "ISO 27001 certified (no caveat) -> FIRES",
        _fires("Our infrastructure is ISO 27001 certified.\n"), True))
    results.append(_check(
        "SOC 2 compliant (no caveat) -> FIRES",
        _fires("We are SOC 2 compliant across all services.\n"), True))

    # --- MISS cases: honestly caveated / negated / out-of-scope ---
    results.append(_check(
        "GDPR compliant WITH Frozen/TBD + U36 + HUMAN DECISION caveat -> no fire",
        _fires("We are GDPR compliant (Frozen/TBD; HUMAN DECISION REQUIRED — see U36).\n"), False))
    results.append(_check(
        "negated claim 'NOT GDPR compliant' -> no fire",
        _fires("We are NOT GDPR compliant yet.\n"), False))
    results.append(_check(
        "PM-PRD checkbox '- [ ] Production ready' (token not in scope) -> no fire",
        _fires("- [ ] Production ready\n"), False))
    results.append(_check(
        "production-ready / enterprise-grade deferred (not in scope) -> no fire",
        _fires("The system is production-ready and enterprise-grade.\n"), False))
    results.append(_check(
        "bare 'compliant' without legal qualifier -> no fire",
        _fires("The code is PEP8 compliant and spec-compliant.\n"), False))
    results.append(_check(
        "technical 'authorized' without legal qualifier -> no fire",
        _fires("The adapter is authorized via the default-allow policy.\n"), False))
    results.append(_check(
        "identity '认证' mention (not a certification claim) -> no fire",
        _fires("身份认证与授权框架已实现。\n"), False))
    # --- MISS (extended): principal/agent authorization must NOT fire ---
    results.append(_check(
        "user authorized the agent to act (principal authorization) -> no fire",
        _fires("The user authorized the agent to act on the task.\n"), False))
    results.append(_check(
        "authorized legally (principal authorization, not 'by law') -> no fire",
        _fires("The principal authorized the agent legally to proceed.\n"), False))

    print()
    if all(results):
        print("ALL PASS: COMPLIANCE-LIABILITY-CLAIMS hit/miss behaviour verified.")
        return 0
    print("FAILURES: one or more cases did not match expectation.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
