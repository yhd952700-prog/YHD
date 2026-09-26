"""U36/U37/HD-06 — unit tests for the COMPLIANCE-LIABILITY-CLAIMS rule group.

The group guards against docs asserting legal/compliance/liability claims
(GDPR/CCPA/PII/privacy/legal-accountability) WITHOUT the required
HUMAN DECISION / Frozen / TBD / 'no framework yet' caveat. This is a
defensive net against fake external legal assurance — it does NOT constitute
compliance and does NOT replace human legal review.

Mirrors scripts/verify_compliance_lint.py (which runs without pytest).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "lint_audit_claims.py"

_spec = importlib.util.spec_from_file_location("lint_audit_claims_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # required before exec_module so imports resolve
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


# --- HIT: un-caveated affirmative compliance/liability claims ---
def test_gdpr_compliant_fires() -> None:
    assert _fires("LIUHAO is fully GDPR compliant and PII protected.\n") is True


def test_ccpa_compliant_fires() -> None:
    assert _fires("We are CCPA compliant out of the box.\n") is True


def test_legally_accountable_fires() -> None:
    assert _fires("The owner is legally accountable for every autonomous action.\n") is True


def test_pii_protected_zh_fires() -> None:
    assert _fires("我们的系统已确保 PII 受保护。\n") is True


def test_data_subject_rights_fires() -> None:
    assert _fires("Data-subject rights are fully implemented and enforced.\n") is True


# --- MISS: honestly caveated / negated / out-of-scope ---
def test_gdpr_compliant_caveated_does_not_fire() -> None:
    assert _fires("We are GDPR compliant (Frozen/TBD; HUMAN DECISION REQUIRED — see U36).\n") is False


def test_negated_claim_does_not_fire() -> None:
    assert _fires("We are NOT GDPR compliant yet.\n") is False


def test_pm_prd_checkbox_not_in_scope() -> None:
    # PM-PRD-v3.0.md:156 '- [ ] Production ready' must not trigger (token deferred).
    assert _fires("- [ ] Production ready\n") is False


def test_production_ready_deferred() -> None:
    assert _fires("The system is production-ready and enterprise-grade.\n") is False


def test_bare_compliant_not_in_scope() -> None:
    assert _fires("The code is PEP8 compliant and spec-compliant.\n") is False


def test_technical_authorized_not_in_scope() -> None:
    assert _fires("The adapter is authorized via the default-allow policy.\n") is False


def test_identity_auth_mention_not_in_scope() -> None:
    assert _fires("身份认证与授权框架已实现。\n") is False
