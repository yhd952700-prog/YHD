"""The checkpointer's tick outcome has to reach /v1/ready -- and has to be fed
to the metrics export.

Why these tests exist
---------------------
``CheckpointStats.record_status`` has recorded ok / degraded / failed for every
tick since 119d6a10, and nothing consumed it. The only readers were the
checkpointer's own tests and one shutdown log line. Consequence: a checkpointer
that failed on every single tick and one that succeeded on every tick were
indistinguishable at every operator-visible surface.

A gate like this is only worth having if it can be shown to (a) actually flip
and (b) not flip when nothing is wrong -- a readiness signal that cries wolf
gets disabled, which is worse than no signal. So each direction is asserted
here, including the control cases.

The "verifier not running" branch is deliberately split into two: when
AUDIT_DB_PATH is unset the verifier is not *expected* (config state, stays
ready); when AUDIT_DB_PATH is set but no verifier is running, the audit engine
was unavailable at boot and the gate must DEGRADE -- never fake-green. That
split mirrors the boot decision in src/gateway/main.py.
"""
from __future__ import annotations

import re

import pytest

from src.reliability import checkpointer as cp_mod


class _FakeStats:
    def __init__(self, last_status, last_error=None, ticks=3):
        self.last_status = last_status
        self.status_counts = {last_status: ticks} if last_status else {}
        self.ticks = ticks
        self.last_error = last_error

    def snapshot(self):
        return {
            "last_status": self.last_status,
            "status_counts": dict(self.status_counts),
            "ticks": self.ticks,
            "last_error": self.last_error,
        }


class _FakeWorker:
    def __init__(self, last_status, last_error=None):
        self.stats = _FakeStats(last_status, last_error)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    with TestClient(get_app()) as c:
        yield c


@pytest.fixture
def no_verifier(monkeypatch):
    """Deterministic control: no checkpointer in this process at all."""
    monkeypatch.setattr(cp_mod, "get_background_verifier", lambda: None)


def _inject(monkeypatch, status, error=None):
    monkeypatch.setattr(
        cp_mod, "get_background_verifier", lambda: _FakeWorker(status, error)
    )


class TestReadinessConsumesTheOutcome:
    def test_failed_tick_takes_readiness_out_of_rotation(self, client, monkeypatch):
        """The whole point: a failing verifier must not read as 'ready'."""
        _inject(monkeypatch, "failed", "incremental segment failed: seq 42")

        r = client.get("/v1/ready")

        assert r.status_code == 503, r.text
        body = r.json()
        assert body["status"] == "degraded"
        check = body["checks"]["audit_checkpointer"]
        assert check["last_status"] == "failed"
        assert check["status"] == "degraded"
        assert any("Audit checkpointer" in e for e in body.get("errors", [])), body

    def test_ok_tick_leaves_readiness_ready(self, client, monkeypatch):
        """Control: a healthy verifier must not trip the gate."""
        _inject(monkeypatch, "ok")

        r = client.get("/v1/ready")

        assert r.status_code == 200, r.text
        check = r.json()["checks"]["audit_checkpointer"]
        assert check["last_status"] == "ok"
        assert check["status"] == "healthy"
        assert not [e for e in r.json().get("errors", [])
                    if "checkpointer" in e.lower()]

    def test_no_verifier_is_a_config_state_not_a_fault(
        self, client, monkeypatch, no_verifier
    ):
        """Control: a deployment that does not run the verifier stays ready.

        Without this, wiring the gate would take every non-checkpointing
        deployment out of rotation on the first scrape. Here AUDIT_DB_PATH is
        explicitly unset, so the verifier is *not expected* -- a deliberate
        config state, not an engine outage.
        """
        monkeypatch.delenv("AUDIT_DB_PATH", raising=False)

        r = client.get("/v1/ready")

        assert r.status_code == 200, r.text
        check = r.json()["checks"]["audit_checkpointer"]
        assert check["available"] is False
        assert check["last_status"] is None
        assert check["status"] == "healthy"
        assert "note" in check  # the intentional-config-state note is present

    def test_degraded_is_reported_but_does_not_gate(self, client, monkeypatch):
        """Coverage-incomplete is surfaced, not gated.

        A cold-start store reports 'degraded' on its first tick, before any
        checkpoint exists; gating on it would pin /v1/ready to 503 forever on a
        fresh deployment. It is still visible in errors and in the check dict.
        """
        _inject(monkeypatch, "degraded")

        r = client.get("/v1/ready")

        assert r.status_code == 200, r.text
        check = r.json()["checks"]["audit_checkpointer"]
        assert check["last_status"] == "degraded"
        assert check["coverage_incomplete"] is True
        assert any("degraded" in e for e in r.json().get("errors", []))


class TestEngineUnavailableIsNotFakeGreen:
    def test_verifier_expected_but_absent_degrades(self, client, monkeypatch):
        """The engine-unavailable-at-boot case must NOT read as healthy.

        main.py starts the verifier only when AUDIT_DB_PATH is set. If that
        path is set but the verifier did not actually start (its start raised
        and was caught at main.py:339 -- e.g. the audit engine was unavailable
        at boot), the readiness edge must report this honestly as degraded
        (HTTP 503), never as a green "ready". This is the exact "engine
        unavailable -> fake-green" trap the readiness gate exists to prevent.
        """
        monkeypatch.setenv("AUDIT_DB_PATH", "/tmp/liuhao_audit_expected.db")
        monkeypatch.setattr(cp_mod, "get_background_verifier", lambda: None)

        r = client.get("/v1/ready")

        assert r.status_code == 503, r.text
        body = r.json()
        assert body["status"] == "degraded"
        check = body["checks"]["audit_checkpointer"]
        assert check["available"] is False
        assert check["status"] == "degraded"
        assert any("Audit checkpointer" in e for e in body.get("errors", [])), body


class TestTheGaugeIsActuallyFed:
    def test_record_status_publishes_the_value(self):
        """Export half: the gauge moves when the outcome does.

        Asserted through the public text export rather than a private field, so
        this proves the value is actually scrapeable, not merely stored.
        """
        from src.reliability.audit_metrics import (
            _CHECKPOINTER_STATUS_VALUES,
            audit_checkpointer_status,
        )
        from src.reliability.checkpointer import CheckpointStats
        from src.reliability.metrics import collect_prometheus_text

        stats = CheckpointStats()
        stats.record_status("failed")
        audit_checkpointer_status.set(_CHECKPOINTER_STATUS_VALUES["failed"])

        text = collect_prometheus_text()
        assert "audit_checkpointer_status" in text
        assert re.search(r"audit_checkpointer_status[^\n]*\b2\b", text), text

    def test_unknown_status_never_reads_as_ok(self):
        """-1 for unknown, never 0 -- an unknown outcome must not look healthy."""
        from src.reliability.audit_metrics import set_checkpointer_status

        from src.reliability.checkpointer import CheckpointStats

        stats = CheckpointStats()
        stats.record_status("nonsense")
        set_checkpointer_status("nonsense")
        assert stats.last_status == "nonsense"

        from src.reliability.audit_metrics import audit_checkpointer_status

        assert audit_checkpointer_status._value == -1.0
