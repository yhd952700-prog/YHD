"""CRIT-1C (PHASE 3.6 / D17) — Layer 1 verification.

Layer 1 makes the audit Evidence layer OBSERVABLE: audit write failures are
no longer silently swallowed. This test proves:

  * the global failure counter increments on every failed authoritative write;
  * the failure is re-raised (so the caller's allow/block policy still decides);
  * get_stats() / audit_stats() surface the ``failures`` signal;
  * the dashboard no longer masks an audit outage as ``{"total_events": 0}``;
  * the ``/v1/ready`` health endpoint reports the audit subsystem.

Layer 2 (blocking for critical / sovereignty-sensitive actions) is a separate,
deliberate decision owned by the human (see H-D17 :163) and is covered by the
A4 same-decision-point closure guard (P0-2).
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from src.kernels import audit as kernel_audit
from src.kernels.audit import (
    AuditEventType,
    AuditScope,
    AuditStore,
    audit_failure_count,
    audit_failure_occurred,
    get_audit_failure_count,
    record_audit_failure,
)


def test_record_audit_failure_counter():
    before = get_audit_failure_count()
    record_audit_failure()
    assert get_audit_failure_count() == before + 1
    assert audit_failure_occurred() is True
    assert audit_failure_count() == get_audit_failure_count()


def test_log_event_failure_increments_counter_and_reraises(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "crit1c.db"))

    # sqlite3.Connection.commit is an immutable type attribute, so it cannot be
    # rebound or patched. Instead wrap the real connection in a plain object
    # whose execute delegates to the real backend but whose commit raises an
    # injected backend failure -- this is the exact point the CRIT-1C fix wraps.
    class _FailingCommit:
        def __init__(self, real):
            self._real = real

        def execute(self, *a, **k):
            return self._real.execute(*a, **k)

        def executemany(self, *a, **k):
            return self._real.executemany(*a, **k)

        def commit(self, *a, **k):
            raise sqlite3.OperationalError("injected audit backend failure")

        def __getattr__(self, name):
            return getattr(self._real, name)

    store._conn = _FailingCommit(store._conn)
    before = get_audit_failure_count()
    with pytest.raises(sqlite3.OperationalError):
        store.log_event(
            event_type=AuditEventType.STATE_CHANGE,
            principal_id="boss",
            scope=AuditScope.L0,
            outcome="probe",
            details={"probe": True},
        )
    # CRIT-1C Layer 1: the failure was recorded, not swallowed silently.
    assert get_audit_failure_count() == before + 1


def test_get_stats_includes_failures(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "crit1c_stats.db"))
    stats = store.get_stats()
    assert "failures" in stats
    assert stats["failures"] == get_audit_failure_count()


def test_dashboard_exposes_audit_outage_honestly():
    from src.gateway import dashboard

    def _raise(*a, **k):
        raise RuntimeError("audit store unreachable")

    with patch.object(kernel_audit, "audit_stats", _raise), patch.object(
        kernel_audit, "audit_failure_count", lambda: 7
    ):
        summary = dashboard.dashboard_summary()
    block = summary["audit"]
    # CRIT-1C: an audit outage must be honestly exposed, not faked as "0 events".
    assert block.get("available") is False
    assert block.get("failures") == 7
    assert "error" in block
    assert block.get("total_events") != 0 or "total_events" not in block


async def test_health_readiness_reports_audit_subsystem():
    from src.gateway import health

    class _Req:
        headers = {}

    def _healthy():
        return {"total_events": 3, "failures": 0, "db_path": "x"}

    with patch.object(kernel_audit, "audit_stats", _healthy):
        resp = await health.readiness_probe(_Req())
    data = json.loads(resp.body)
    assert "audit_store" in data["checks"]
    assert data["checks"]["audit_store"]["status"] == "healthy"
