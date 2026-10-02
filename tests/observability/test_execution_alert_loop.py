"""Prove the execution-failure -> alert closed loop is REAL (not a stub).

Run:
  tests/observability/test_execution_alert_loop.py

What this proves:
  (a) Publishing a real ``action_failed`` / ``task_failed`` event on the
      EventBus results in a NEW alert written to the alerts store (REDIR'd to a
      temp dir via ``LIUHAO_ALERTS_STORE_PATH``).
  (b) The binding is fail-safe: a raising handler chained on the same event type
      does NOT propagate — the bus captures it as a DeadLetter and our binding
      still emits its alert.

No frozen HC-01 evidence (audit_store) is touched.
"""

from __future__ import annotations

import pytest

from src.kernels.event import (
    EventScope,
    EventPriority,
    get_event_bus,
    publish_event,
    subscribe_event,
)
from src.observability.alerts.store import get_alert_store
from src.observability.alerts import execution_binding as eb


@pytest.fixture
def alerts_tmp_store(monkeypatch, tmp_path):
    """REDIR the alerts store to a temp file and reset the singleton.

    The store path is controlled by ``LIUHAO_ALERTS_STORE_PATH`` (added in
    src/observability/alerts/store.py). We set it to a temp file so no real
    ``data/observability/alerts.json`` is written during tests.
    """
    import src.observability.alerts.store as store_mod

    path = str(tmp_path / "alerts.json")
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", path)
    # Drop any singleton built against the real (or another test's) path so the
    # next get_alert_store() rebuilds against the REDIR path.
    monkeypatch.setattr(store_mod, "_default_store", None)

    yield path

    # Keep the global EventBus clean so this test does not leak subscriptions
    # into other tests in the session.
    eb.uninstall_execution_alert_binding()
    monkeypatch.setattr(store_mod, "_default_store", None)


def test_action_failed_emits_high_alert(alerts_tmp_store):
    eb.install_execution_alert_binding()
    store = get_alert_store()
    before = len(store.list_alerts())

    publish_event(
        type="action_failed",
        source="execution_kernel",
        data={"action_id": "a1", "task_id": "t1", "error": "boom"},
        correlation_id="cid-action-1",
        scope=EventScope.L7,
        priority=EventPriority.HIGH,
    )

    after = len(store.list_alerts())
    assert after == before + 1, "action_failed must write exactly one new alert"
    alert = store.list_alerts()[-1]
    assert alert.source == "execution_kernel"
    assert alert.severity.name == "HIGH"
    assert alert.name == "Autonomous Action Failed"
    assert "a1" in (alert.message or "")


def test_action_failed_critical_emits_critical_alert(alerts_tmp_store):
    eb.install_execution_alert_binding()
    store = get_alert_store()
    before = len(store.list_alerts())

    publish_event(
        type="action_failed",
        source="execution_kernel",
        data={"action_id": "a2", "task_id": "t2", "error": "fatal"},
        correlation_id="cid-action-2",
        scope=EventScope.L7,
        priority=EventPriority.CRITICAL,
    )

    after = len(store.list_alerts())
    assert after == before + 1
    alert = store.list_alerts()[-1]
    assert alert.severity.name == "CRITICAL"


def test_task_failed_emits_high_alert(alerts_tmp_store):
    eb.install_execution_alert_binding()
    store = get_alert_store()
    before = len(store.list_alerts())

    publish_event(
        type="task_failed",
        source="execution_kernel",
        data={"task_id": "t9", "goal_id": "g9", "error": "max retries exceeded"},
        correlation_id="cid-task-1",
        scope=EventScope.L7,
        priority=EventPriority.CRITICAL,
    )

    after = len(store.list_alerts())
    assert after == before + 1, "task_failed must write exactly one new alert"
    alert = store.list_alerts()[-1]
    assert alert.severity.name == "HIGH"
    assert alert.name == "Autonomous Task Failed"
    assert "t9" in (alert.message or "")


def test_execution_completed_with_failures_emits_alert(alerts_tmp_store):
    eb.install_execution_alert_binding()
    store = get_alert_store()
    before = len(store.list_alerts())

    publish_event(
        type="execution_completed",
        source="execution_kernel",
        data={
            "goal_id": "g1",
            "plan_id": "p1",
            "status": "done",
            "completed": 3,
            "failed": 2,
        },
        correlation_id="cid-exec-1",
        scope=EventScope.L7,
    )

    after = len(store.list_alerts())
    assert after == before + 1
    alert = store.list_alerts()[-1]
    assert alert.severity.name == "HIGH"
    assert "2" in (alert.message or "")


def test_binding_is_fail_safe(alerts_tmp_store):
    """A raising handler in the chain must NOT propagate; our alert still emits."""
    eb.install_execution_alert_binding()
    store = get_alert_store()
    before = len(store.list_alerts())

    def _boom(event):
        raise RuntimeError("handler boom")

    # Chain a deliberately broken handler on the same event type.
    subscribe_event("action_failed", _boom, scope=EventScope.L7)

    # Must not raise despite _boom raising.
    publish_event(
        type="action_failed",
        source="execution_kernel",
        data={"action_id": "ax", "task_id": "tx", "error": "x"},
        correlation_id="cid-failsafe",
        scope=EventScope.L7,
        priority=EventPriority.HIGH,
    )

    # Our binding still emitted its alert.
    after = len(store.list_alerts())
    assert after == before + 1

    # The broken handler was captured by the bus as a DeadLetter, not propagated.
    dead = get_event_bus().get_dead_letters()
    assert dead, "raising handler should be captured as a DeadLetter"
    assert any("handler boom" in str(dl.error) for dl in dead)
