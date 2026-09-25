"""Tests for src.reliability.audit_metrics (non-intrusive audit-chain signals).

These tests monkeypatch the *public* API of ``src.kernels.audit`` so the test
never touches a real database and never writes to the audit store.
"""
from __future__ import annotations

import pytest

from src.reliability import audit_metrics


@pytest.fixture(autouse=True)
def _reset_audit_metrics():
    # Reset shared gauges/counter before each test to avoid leakage.
    audit_metrics.audit_evidence_status.set(0)
    audit_metrics.audit_failure_total.set(0)
    audit_metrics.audit_total_events.set(0)
    audit_metrics.audit_integrity_ok.set(0)
    yield


def test_refresh_success_path(monkeypatch):
    monkeypatch.setattr("src.kernels.audit.audit_failure_count", lambda: 7)
    monkeypatch.setattr(
        "src.kernels.audit.audit_stats",
        lambda: {"total_events": 1234, "failures": 7},
    )

    status = audit_metrics.refresh_audit_metrics()

    assert status["store_reachable"] is True
    assert status["failures"] == 7
    assert status["total_events"] == 1234
    assert status["error"] is None
    assert audit_metrics.audit_evidence_status.sample() == 1
    assert audit_metrics.audit_failure_total.sample() == 7
    assert audit_metrics.audit_total_events.sample() == 1234


def test_refresh_integrity_check_pass(monkeypatch):
    monkeypatch.setattr("src.kernels.audit.audit_failure_count", lambda: 0)
    monkeypatch.setattr(
        "src.kernels.audit.audit_stats", lambda: {"total_events": 5, "failures": 0}
    )
    monkeypatch.setattr(
        "src.kernels.audit.verify_audit_integrity", lambda: (True, 5)
    )

    status = audit_metrics.refresh_audit_metrics(with_integrity_check=True)

    assert status["integrity_ok"] is True
    assert audit_metrics.audit_integrity_ok.sample() == 1
    assert audit_metrics.audit_evidence_status.sample() == 1


def test_refresh_integrity_check_fail(monkeypatch):
    monkeypatch.setattr("src.kernels.audit.audit_failure_count", lambda: 0)
    monkeypatch.setattr(
        "src.kernels.audit.audit_stats", lambda: {"total_events": 5, "failures": 0}
    )
    monkeypatch.setattr(
        "src.kernels.audit.verify_audit_integrity", lambda: (False, 3)
    )

    status = audit_metrics.refresh_audit_metrics(with_integrity_check=True)

    assert status["integrity_ok"] is False
    assert audit_metrics.audit_integrity_ok.sample() == 0
    # store still reachable -> evidence status stays 1
    assert audit_metrics.audit_evidence_status.sample() == 1


def test_refresh_import_failure_reports_evidence_missing(monkeypatch):
    # Force the lazy import to fail -> Evidence=missing, never silent.
    real_import = __import__

    def _broken_import(name, *a, **k):
        if name == "src.kernels.audit":
            raise ImportError("kernel-not-available-in-this-env")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", _broken_import)

    status = audit_metrics.refresh_audit_metrics()

    assert status["store_reachable"] is False
    assert status["error"].startswith("import_failed:")
    assert audit_metrics.audit_evidence_status.sample() == 0


def test_refresh_store_call_error_reports_evidence_missing(monkeypatch):
    monkeypatch.setattr(
        "src.kernels.audit.audit_failure_count",
        lambda: (_ for _ in ()).throw(RuntimeError("db locked")),
    )
    monkeypatch.setattr(
        "src.kernels.audit.audit_stats", lambda: {"total_events": 0}
    )

    status = audit_metrics.refresh_audit_metrics()

    assert status["store_reachable"] is False
    assert status["error"].startswith("store_error:")
    assert audit_metrics.audit_evidence_status.sample() == 0


def test_fork_count_reserved_zero():
    # Fork detection is owned by U3; the gauge exists but is reserved.
    assert audit_metrics.audit_fork_count.sample() == 0
