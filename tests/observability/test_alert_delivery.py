"""Prove alert delivery reaches a REAL sink (webhook) and degrades safely.

Run:
  tests/observability/test_alert_delivery.py

Proves (honestly, with a mock — the sandbox cannot reach a real URL):
  (a) A configured webhook sink (``LIUHAO_ALERT_WEBHOOK``) actually attempts an
      HTTP POST with the correct alert JSON to that URL.
  (b) With no webhook configured, ``emit_alert`` falls back to console logging
      WITHOUT error and WITHOUT attempting any POST.
  (c) A webhook POST failure is swallowed (logged), never raised into the alert
      path — the alert is still persisted (fail-closed).
  (d) A real execution-failure event (the path ``execution_binding`` already
      wires) reaches the configured webhook sink when one is set.

REDIRs ``AUDIT_DB_PATH``, ``LIUHAO_WORKSPACE_ROOT`` and
``LIUHAO_ALERTS_STORE_PATH`` to temp. No frozen HC-01 evidence store is touched.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.observability.alerts.models import (
    Alert,
    AlertSeverity,
    AlertState,
    AlertType,
)
from src.observability.alerts.store import get_alert_store
from src.observability.alerts import sinks
from src.observability.alerts import execution_binding as eb


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """REDIR every FS/env surface that could touch the real store or frozen
    evidence, and reset module singletons between tests."""
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit_store.db"))
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.delenv("LIUHAO_ALERT_WEBHOOK", raising=False)

    import src.observability.alerts.store as store_mod

    monkeypatch.setattr(store_mod, "_default_store", None)

    yield tmp_path

    # Keep the global EventBus clean so subscriptions don't leak across tests.
    eb.uninstall_execution_alert_binding()
    monkeypatch.setattr(store_mod, "_default_store", None)


def _make_alert() -> Alert:
    return Alert(
        id="alert-deliv-1",
        alert_type=AlertType.CUSTOM,
        name="Test Delivery Alert",
        severity=AlertSeverity.HIGH,
        message="delivery test",
        source="test",
        state=AlertState.FIRING,
        tags={"k": "v"},
    )


def _patch_sinks_with_poster(monkeypatch, poster):
    """Replace ``build_sinks`` so any constructed webhook sink uses our poster."""
    real_build = sinks.build_sinks

    def fake_build(*, webhook_url=None):
        built = real_build(webhook_url=webhook_url)
        for sink in built:
            if isinstance(sink, sinks.WebhookSink):
                sink._poster = poster
        return built

    monkeypatch.setattr(sinks, "build_sinks", fake_build)


def test_webhook_sink_attempts_post_with_correct_json():
    poster = MagicMock(return_value=MagicMock(status_code=200))
    url = "http://localhost:9999/alerts"
    sink = sinks.WebhookSink(url, poster=poster)
    sink.send(_make_alert())

    poster.assert_called_once()
    args, kwargs = poster.call_args
    assert args[0] == url
    assert kwargs["json"]["id"] == "alert-deliv-1"
    assert kwargs["json"]["severity"] == "HIGH"
    assert kwargs["json"]["message"] == "delivery test"
    assert kwargs["json"]["name"] == "Test Delivery Alert"


def test_dispatch_delivers_to_configured_webhook(isolated_env, monkeypatch):
    poster = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setenv("LIUHAO_ALERT_WEBHOOK", "http://hook.test/x")
    _patch_sinks_with_poster(monkeypatch, poster)

    store = get_alert_store()
    before = len(store.list_alerts())
    store.emit_alert(_make_alert())
    after = len(store.list_alerts())

    # Fail-closed guarantee: alert is still persisted (file sink).
    assert after == before + 1, "alert must still be persisted when webhook is set"
    poster.assert_called_once()
    sent = poster.call_args.kwargs["json"]
    assert sent["id"] == "alert-deliv-1"


def test_no_webhook_falls_back_without_error(isolated_env, monkeypatch):
    import requests

    called = {}

    def _boom(*a, **k):
        called["yes"] = True
        raise AssertionError("requests.post must NOT be called when no webhook is set")

    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.delenv("LIUHAO_ALERT_WEBHOOK", raising=False)

    store = get_alert_store()
    # Must not raise even though no sink is configured.
    store.emit_alert(_make_alert())
    assert not called.get("yes"), "no webhook configured -> no POST attempted"
    assert len(store.list_alerts()) == 1, "alert still persisted via file sink"


def test_webhook_failure_is_swallowed_not_raised(isolated_env, monkeypatch):
    poster = MagicMock(side_effect=RuntimeError("network down"))
    monkeypatch.setenv("LIUHAO_ALERT_WEBHOOK", "http://hook.test/y")
    _patch_sinks_with_poster(monkeypatch, poster)

    store = get_alert_store()
    # Must not raise despite the webhook failing.
    store.emit_alert(_make_alert())
    # Fail-closed: alert record is still on disk.
    assert len(store.list_alerts()) == 1
    poster.assert_called_once()


def test_execution_failure_reaches_webhook(isolated_env, monkeypatch):
    """Requirement #3: the execution-failure alert (emitted by execution_binding
    on a real failure event) must reach the configured webhook sink."""
    from src.kernels.event import EventScope, EventPriority, publish_event

    poster = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setenv("LIUHAO_ALERT_WEBHOOK", "http://hook.test/exec")
    _patch_sinks_with_poster(monkeypatch, poster)

    eb.install_execution_alert_binding()
    store = get_alert_store()
    before = len(store.list_alerts())

    publish_event(
        type="action_failed",
        source="execution_kernel",
        data={"action_id": "a-deliv", "task_id": "t-deliv", "error": "boom"},
        correlation_id="cid-deliv",
        scope=EventScope.L7,
        priority=EventPriority.HIGH,
    )

    after = len(store.list_alerts())
    assert after == before + 1, "execution failure must emit one alert"
    poster.assert_called_once()
    sent = poster.call_args.kwargs["json"]
    assert "a-deliv" in sent["message"]
    assert sent["severity"] == "HIGH"
    assert sent["source"] == "execution_kernel"
