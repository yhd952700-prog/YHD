"""Audit-lag alert semantics: write backlog, not event silence.

The old metric was ``time.time() - MAX(audit_events.timestamp)``, so an idle
system (last event 69s ago, nothing being written) reported "lag = 69s" and
raised a red ``Audit Log Sync Lag``. That is a false positive: silence is not
slowness.

The metric now measures the age of the OLDEST audit-write request that was
issued but has not committed yet, plus a separate failure signal that stays
raised until a later write succeeds. These tests prove BOTH directions:

* idle for 10 minutes (last real event 600s old, no writes issued) -> no alert;
* a real write is blocked/stalled -> the production 60s rule fires;
* a real write fails (disk full) -> the distinct "write failing" rule fires;
* after the write lands / a later write succeeds -> both alerts auto-resolve
  (no permanent red bar).
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import src.kernels.audit as audit_mod
from src.kernels.audit import (
    AuditDiskFullError,
    AuditEventType,
    AuditScope,
    get_audit_store,
    get_audit_write_health,
)
from src.observability.alerts import AlertStore, get_alert_manager, install_production_rules
from src.observability.metrics import MetricCollector


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Per-test audit DB, alert store, rule manager and write-health state."""
    audit_db = tmp_path / "audit.db"
    monkeypatch.setenv("AUDIT_DB_PATH", str(audit_db))
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))

    import src.observability.alerts.store as store_mod
    import src.observability.alerts.models as models_mod

    monkeypatch.setattr(store_mod, "_default_store", None)
    monkeypatch.setattr(models_mod, "_default_manager", None)
    monkeypatch.setattr(audit_mod, "_audit_store", None)
    # The write-health watermarks are process-global; earlier tests in the same
    # pytest process may have deliberately failed audit writes. Start clean.
    monkeypatch.setattr(audit_mod, "_write_health_state", audit_mod._WriteHealthState())

    install_production_rules()
    yield
    monkeypatch.setattr(audit_mod, "_write_health_state", audit_mod._WriteHealthState())


def _snapshot(collector: MetricCollector) -> dict:
    """Snapshot with volatile host metrics pinned so only audit rules can fire."""
    return {
        "memory_usage_percent": 0.0,
        "cpu_usage_percent": 0.0,
        "error_rate_percent": 0.0,
        "latency_p99_ms": 0.0,
        "service_heartbeat_interval": 1.0,
        "audit_log_lag_seconds": collector._audit_log_lag_seconds(),
        "audit_write_failing": collector._audit_write_failing(),
    }


def _write_one_event(store) -> None:
    store.log_event(
        AuditEventType.STATE_CHANGE,
        "test-principal",
        AuditScope.L0,
        "ok",
        {"source": "audit-lag-test"},
    )


def _audit_firing_ids() -> set:
    return {
        a.id for a in AlertStore().list_firing()
        if a.metric_name in ("audit_log_lag_seconds", "audit_write_failing")
    }


def test_idle_ten_minutes_does_not_alert():
    """Last real event 600s old, zero writes issued -> lag 0.0, no red bar."""
    store = get_audit_store()
    _write_one_event(store)

    # Backdate the committed row to 10 minutes ago: this is exactly the state
    # that made the old metric report ~600s. The write path is idle (no reader
    # or writer is waiting), which is what "idle" actually means.
    conn = sqlite3.connect(store._db_path)
    try:
        conn.execute("UPDATE audit_events SET timestamp = ?", (time.time() - 600.0,))
        conn.commit()
        old_timestamp = conn.execute(
            "SELECT MAX(timestamp) FROM audit_events").fetchone()[0]
    finally:
        conn.close()

    old_semantics_lag = time.time() - old_timestamp
    assert old_semantics_lag > 590.0  # would have breached the 60s rule

    collector = MetricCollector()
    health = get_audit_write_health()
    assert health["inflight"] == 0
    assert health["failing"] is False
    assert collector._audit_log_lag_seconds() == 0.0
    assert collector._audit_write_failing() == 0.0

    manager = get_alert_manager()
    for _tick in range(10):  # ten evaluation ticks on a genuinely idle system
        new_alerts = manager.evaluate_all(_snapshot(collector))
        assert [a for a in new_alerts
                if a.metric_name in ("audit_log_lag_seconds", "audit_write_failing")
                ] == []
    assert _audit_firing_ids() == set()


def test_blocked_write_stalls_then_resolves(monkeypatch):
    """A real write waiting on the append lock produces the stall alert."""
    store = get_audit_store()
    release = threading.Event()
    acquired = threading.Event()

    def _hold_lock() -> None:
        with store._lock:
            acquired.set()
            release.wait(timeout=30)

    holder = threading.Thread(target=_hold_lock, name="lock-holder", daemon=True)
    holder.start()
    assert acquired.wait(5), "test could not acquire the audit append lock"

    outcome: dict = {}

    def _blocked_write() -> None:
        try:
            _write_one_event(store)
            outcome["ok"] = True
        except BaseException as exc:  # pragma: no cover - reported via assert
            outcome["error"] = exc

    writer = threading.Thread(target=_blocked_write, name="blocked-writer", daemon=True)
    writer.start()

    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if get_audit_write_health()["inflight"] >= 1:
                break
            time.sleep(0.02)
        assert get_audit_write_health()["inflight"] == 1
        # Windows' wall clock granularity is ~15.6ms, so let a little real time
        # pass before measuring the genuinely blocked request's age.
        time.sleep(0.25)
        real_age = get_audit_write_health()["oldest_inflight_age_seconds"]
        assert 0.2 <= real_age < 5.0, real_age

        # Compress 10 minutes of stall into the same real request instead of
        # sleeping (CI runs pytest --timeout=60). The production threshold stays
        # 60s; only the "now" used for age arithmetic is shifted forward.
        real_health = audit_mod.get_audit_write_health
        monkeypatch.setattr(
            audit_mod,
            "get_audit_write_health",
            lambda: real_health(now=time.time() + 600.0),
        )

        collector = MetricCollector()
        lag = collector._audit_log_lag_seconds()
        assert lag >= 600.0, lag  # age of the oldest uncommitted write

        manager = get_alert_manager()
        new_alerts = manager.evaluate_all(_snapshot(collector))
        stall_alerts = [a for a in new_alerts if a.id == "audit_lag_high"]
        assert stall_alerts, "a 600s stalled write did not breach the 60s rule"
        assert stall_alerts[0].name == "Audit Log Write Stalled"
        assert collector._audit_write_failing() == 0.0
        assert _audit_firing_ids() == {"audit_lag_high"}
    finally:
        release.set()
        holder.join(timeout=10)
        writer.join(timeout=10)

    # The write committed: the backlog is empty and the alert must clear.
    assert outcome.get("ok") is True
    assert get_audit_write_health()["inflight"] == 0
    assert collector._audit_log_lag_seconds() == 0.0
    get_alert_manager().evaluate_all(_snapshot(collector))
    assert _audit_firing_ids() == set(), "stall alert stayed red after recovery"


def test_failed_write_alerts_and_clears_only_on_real_success(monkeypatch):
    """A failed write raises the DISTINCT failure alert; a later success clears it."""
    store = get_audit_store()
    collector = MetricCollector()

    def _boom(*_args, **_kwargs):
        raise AuditDiskFullError("simulated disk full")

    with monkeypatch.context() as mp:
        mp.setattr(store, "_attempt_log_event", _boom)
        with pytest.raises(AuditDiskFullError):
            _write_one_event(store)

    health = get_audit_write_health()
    # Real numbers, not a faked flag: one failed write, one failure streak.
    assert health["failed"] == 1
    assert health["failure_streak"] == 1
    assert health["last_failure_kind"] == "disk_full"
    assert health["failing"] is True
    assert collector._audit_write_failing() == 1.0
    assert collector._audit_log_lag_seconds() == 0.0  # the request ended, it did not stall

    manager = get_alert_manager()
    new_alerts = manager.evaluate_all(_snapshot(collector))
    fail_alerts = [a for a in new_alerts if a.id == "audit_write_failing"]
    assert fail_alerts, "a failed audit write did not raise the failure rule"
    assert fail_alerts[0].name == "Audit Log Write Failing"
    # The two causes are distinguishable by name, not one ambiguous red bar.
    assert manager.get_rule("audit_lag_high").name != fail_alerts[0].name
    assert _audit_firing_ids() == {"audit_write_failing"}

    # Recovery requires positive proof: a REAL append must succeed. A timeout or
    # an operator "ack" does not clear it.
    _write_one_event(store)
    health = get_audit_write_health()
    assert health["failure_streak"] == 0
    assert health["failing"] is False
    assert collector._audit_write_failing() == 0.0

    manager.evaluate_all(_snapshot(collector))
    assert _audit_firing_ids() == set(), "failure alert stayed red after a real success"
