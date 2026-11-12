"""CRIT-1C (PHASE 3.6 / D17 Option C) — Layer 2 verification.

Layer 2 makes the fail-closed guarantee REAL for mandatory-evidence actions
(HIGH/CRITICAL): if the authoritative audit Evidence cannot be produced, the
action MUST NOT proceed. This is the inverse of Layer 1's LOW/MEDIUM
degraded-continue path and must itself never fail open.

These are real runtime probes through the REAL ``@kernel_action`` decorator and
the REAL audit store -- not static / config / file / mock / fragmented-layer
tests. The verdict source (``_adjudicate``) is the only thing stubbed, exactly as
``scripts/verify_c2_enforcement.py`` and
``scripts/verify_same_decision_point_closure.py`` do, because we are testing the
fail-closed PROPERTY, not the policy engine's verdict.

Probes:
  1. CRITICAL + Evidence failure  -> PolicyDeniedError, action body NOT executed.
  2. HIGH     + Evidence failure  -> PolicyDeniedError, action body NOT executed.
  3. LOW      + Evidence failure  -> action body EXECUTES (degraded continue; we do
                                    NOT bluntly block every tier).
  4. CRITICAL + working Evidence  -> an "intent" record is written BEFORE the body
                                    runs (the real pre-execution liveness probe).
"""
import importlib
import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from src.kernels import audit as kernel_audit  # noqa: E402
from src.kernels._crosscutting import (  # noqa: E402
    PolicyDeniedError,
    _adjudicate,
    _service_actor_policy_shape,
    kernel_action,
)

# Capture the genuine audit writer ONCE at import time. A test that overwrites
# ``kernel_audit.log_event`` mutates the module *attribute*; re-importing the
# module later returns the same (now-stubbed) attribute, so we must keep a
# direct reference to the original function for honest restore / probing.
REAL_LOG_EVENT = kernel_audit.log_event
_REAL_ADJUDICATE = None


def _real_log_event():
    return REAL_LOG_EVENT


def _reset_audit_writer() -> None:
    """Defensively restore the real audit writer (kills any leftover stub)."""
    kernel_audit.log_event = REAL_LOG_EVENT


def _stub_adjudicate(verdict):
    global _REAL_ADJUDICATE
    import src.kernels._crosscutting as xc
    _REAL_ADJUDICATE = xc._adjudicate
    xc._adjudicate = lambda action, risk_level: (
        verdict, f"{verdict}-rule", _service_actor_policy_shape())


def _restore_adjudicate():
    import src.kernels._crosscutting as xc
    xc._adjudicate = _REAL_ADJUDICATE


def _fresh_audit_store(tmp_path: pathlib.Path) -> None:
    store = kernel_audit.AuditStore(db_path=str(tmp_path / "l2_audit.db"))
    kernel_audit._audit_store = store


def _raise_audit_backend(*a, **k):
    raise sqlite3.OperationalError("injected audit backend failure")


def test_layer2_critical_evidence_failure_blocks_action(tmp_path):
    _reset_audit_writer()
    _fresh_audit_store(tmp_path)
    kernel_audit.log_event = _raise_audit_backend
    _stub_adjudicate("allow")
    ran = {"v": False}
    try:
        @kernel_action("probe.l2.critical", risk_level="CRITICAL", enforce=True)
        def do_critical():
            ran["v"] = True
            return "ran"

        raised = False
        try:
            do_critical()
        except PolicyDeniedError:
            raised = True
        assert raised, "mandatory-evidence action must fail-closed on evidence loss"
        assert ran["v"] is False, "action body must NOT execute when evidence fails"
    finally:
        _reset_audit_writer()
        _restore_adjudicate()


def test_layer2_high_evidence_failure_blocks_action(tmp_path):
    _reset_audit_writer()
    _fresh_audit_store(tmp_path)
    kernel_audit.log_event = _raise_audit_backend
    _stub_adjudicate("allow")
    ran = {"v": False}
    try:
        @kernel_action("probe.l2.high", risk_level="HIGH", enforce=True)
        def do_high():
            ran["v"] = True
            return "ran"

        raised = False
        try:
            do_high()
        except PolicyDeniedError:
            raised = True
        assert raised, "HIGH mandatory-evidence action must fail-closed on evidence loss"
        assert ran["v"] is False, "action body must NOT execute when evidence fails"
    finally:
        _reset_audit_writer()
        _restore_adjudicate()


def test_layer2_low_evidence_failure_continues(tmp_path):
    _reset_audit_writer()
    _fresh_audit_store(tmp_path)
    kernel_audit.log_event = _raise_audit_backend
    _stub_adjudicate("allow")
    ran = {"v": False}
    try:
        @kernel_action("probe.l2.low", risk_level="LOW", enforce=True)
        def do_low():
            ran["v"] = True
            return "ran"

        # LOW is NOT mandatory-evidence: the action must still execute (degraded
        # continue), proving we do NOT bluntly block every tier.
        out = do_low()
        assert out == "ran"
        assert ran["v"] is True, "LOW action must continue despite evidence loss"
    finally:
        _reset_audit_writer()
        _restore_adjudicate()


def test_layer2_intent_written_before_execution(tmp_path):
    _reset_audit_writer()
    _fresh_audit_store(tmp_path)
    real = _real_log_event()
    captured = []
    spy_active = {"v": True}

    def spy(event_type, principal_id, scope, outcome, details=None,
            correlation_id=None, **kw):
        if spy_active["v"]:
            captured.append({"outcome": outcome, "corr": correlation_id})
        return real(event_type, principal_id, scope, outcome, details,
                    correlation_id=correlation_id, **kw)

    kernel_audit.log_event = spy
    _stub_adjudicate("allow")
    ran = {"v": False}
    try:
        @kernel_action("probe.l2.intent", risk_level="CRITICAL", enforce=True)
        def do_critical():
            ran["v"] = True
            return "ran"

        do_critical()
        # The "intent" record (outcome="intent") must be written BEFORE the body
        # runs (it is the liveness probe); the body then runs and writes its own
        # outcome record after. So the first captured outcome is "intent".
        assert captured, "no audit write captured"
        assert captured[0]["outcome"] == "intent", (
            f"pre-execution intent write missing; first outcome={captured[0]['outcome']!r}")
        assert ran["v"] is True, "body must execute when evidence channel is live"
    finally:
        spy_active["v"] = False
        _reset_audit_writer()
        _restore_adjudicate()
