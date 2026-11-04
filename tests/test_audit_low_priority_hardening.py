"""Low-priority hardening items from the audit.

Four independent gaps, each pinned by a real-behaviour test:

1. ``/v1/metrics/prometheus`` and ``/v1/ready/subsystems`` were reachable with no
   auth at all. The metrics scrape now requires either a configured bearer token
   or an internal (loopback/private) source; the subsystem report now sits behind
   the same human-principal gate as every other read surface.
2. ``/v1/events/history`` reads the durable audit store instead of pretending the
   in-memory ring buffer is history.
3. Model management stamps pricing state on every model response, so a new model
   with no entry in ``config/model_pricing.json`` is visibly *unpriced* instead of
   silently looking free.
4. ``describe_posture()`` claimed "never raises" but propagated
   ``PostureUndeterminable`` in strict mode. It now reports the ambiguity
   (``strict_raised=True``) and callers who need fail-closed still call
   ``is_production()``.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.gateway.main import get_app
from src.gateway.models import pricing_status
from src.gateway.observability import METRICS_TOKEN_ENV, _authorize_metrics
from src.security.posture import (
    ENVIRONMENT_ENV,
    LIUHAO_ENV,
    STRICT_ENV,
    PostureUndeterminable,
    describe_posture,
    is_production,
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    with TestClient(get_app()) as test_client:
        yield test_client


def _headers(roles=None, scope=None):
    from src.security import get_jwt_handler

    metadata = {"scope": scope} if scope else {}
    token, _ = get_jwt_handler().create_token(
        subject="reader", roles=roles or [], metadata=metadata
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reader_headers():
    return _headers()


@pytest.fixture
def admin_headers():
    return _headers(roles=["admin"])


def _request(host: str, headers=None) -> Request:
    """Build a minimal ASGI request so source-IP logic can be exercised directly."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/v1/metrics/prometheus",
        "query_string": b"",
        "headers": raw_headers,
        "client": (host, 4321),
        "server": ("testserver", 80),
        "scheme": "http",
    })


class TestMetricsScrapeRestriction:
    def test_public_source_without_token_is_refused(self, monkeypatch):
        monkeypatch.delenv(METRICS_TOKEN_ENV, raising=False)
        with pytest.raises(Exception) as exc:
            _authorize_metrics(_request("8.8.8.8"))
        assert getattr(exc.value, "status_code", None) == 403
        assert METRICS_TOKEN_ENV in str(exc.value.detail)

    def test_loopback_and_private_sources_are_allowed(self, monkeypatch):
        monkeypatch.delenv(METRICS_TOKEN_ENV, raising=False)
        for host in ("127.0.0.1", "::1", "10.1.2.3", "172.18.0.5", "192.168.1.20"):
            _authorize_metrics(_request(host))  # must not raise

    def test_token_mode_requires_the_exact_bearer(self, monkeypatch):
        monkeypatch.setenv(METRICS_TOKEN_ENV, "scrape-secret")
        with pytest.raises(Exception) as exc:
            _authorize_metrics(_request("8.8.8.8", {"authorization": "Bearer wrong"}))
        assert exc.value.status_code == 403

        _authorize_metrics(_request("8.8.8.8", {"authorization": "Bearer scrape-secret"}))
        _authorize_metrics(_request("8.8.8.8", {"x-liuhao-metrics-token": "scrape-secret"}))

    def test_endpoint_rejects_unknown_client(self, client, monkeypatch):
        monkeypatch.delenv(METRICS_TOKEN_ENV, raising=False)
        # TestClient's default client host is the literal "testclient", not an
        # IP -- exactly what a non-internal caller looks like to this check.
        response = client.get("/v1/metrics/prometheus")
        assert response.status_code == 403, response.text


class TestSubsystemsReadRequiresAuth:
    def test_without_token_is_401(self, client):
        assert client.get("/v1/ready/subsystems").status_code == 401

    def test_with_token_reports(self, client, reader_headers):
        response = client.get("/v1/ready/subsystems", headers=reader_headers)
        assert response.status_code in (200, 503), response.text
        assert "subsystems" in response.json()


class TestDurableEventHistory:
    def test_history_reads_the_audit_store(self, client, reader_headers, monkeypatch):
        import src.gateway.audit as audit_module

        recorded = [{
            "seq": 7,
            "event_id": "e-7",
            "event_type": "execution.execute",
            "principal_id": "liuhao",
            "scope": "L0",
            "timestamp": 1700000000.0,
            "correlation_id": "c-1",
            "outcome": "success",
            "details": {"action": "execution.execute"},
        }]
        calls = {}

        def fake_read(**kwargs):
            calls.update(kwargs)
            return recorded

        monkeypatch.setattr(audit_module, "_read_events", fake_read)
        response = client.get(
            "/v1/events/history",
            headers=reader_headers,
            params={"limit": 5, "outcome": "success", "reverse": "true"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "audit_store"
        assert body["durable"] is True
        assert body["count"] == 1
        assert body["events"] == recorded
        assert body["filters"]["outcome"] == "success"
        assert calls["limit"] == 5 and calls["reverse"] is True

    def test_history_reports_an_unreadable_store_as_503(self, client, reader_headers, monkeypatch):
        import src.gateway.audit as audit_module

        def broken(**_kwargs):
            raise RuntimeError("audit db is locked")

        monkeypatch.setattr(audit_module, "_read_events", broken)
        response = client.get("/v1/events/history", headers=reader_headers)
        assert response.status_code == 503
        assert "audit db is locked" in response.json()["detail"]

    def test_live_buffer_is_labelled_non_durable(self, client, reader_headers):
        body = client.get("/v1/events", headers=reader_headers).json()
        assert body["source"] == "event_bus_history"
        assert body["durable"] is False
        assert "/v1/events/history" in body["note"]


class TestModelPricingVisibility:
    def test_priced_model_reports_its_price(self):
        status = pricing_status("gpt-5.6-sol")
        assert status["priced"] is True
        assert status["price_per_1k_tokens"] == 0.01

    def test_unpriced_model_says_so_and_is_not_free(self):
        status = pricing_status("qwen2.5:3b")
        assert status["priced"] is False
        assert status["price_per_1k_tokens"] is None
        assert "未定价" in status["note"]

    def test_pricing_file_override_is_honoured(self, monkeypatch, tmp_path):
        path = tmp_path / "pricing.json"
        path.write_text(json.dumps({
            "_comment": "docs, not a price",
            "local-model": 0.0,
            "bad": "not-a-number",
            "negative": -1,
        }), encoding="utf-8")
        monkeypatch.setenv("LIUHAO_MODEL_PRICING_FILE", str(path))
        assert pricing_status("local-model")["priced"] is True
        assert pricing_status("bad")["priced"] is False
        assert pricing_status("negative")["priced"] is False
        assert pricing_status("_comment")["priced"] is False

    def test_create_response_marks_a_new_model_unpriced(self, client, admin_headers):
        response = client.post("/v1/models", headers=admin_headers, json={
            "name": "brand-new", "provider": "mock", "model": "never-priced", "timeout": 10,
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pricing"]["priced"] is False
        assert "未定价" in body["pricing"]["note"]

    def test_listed_models_carry_pricing(self, client, reader_headers):
        response = client.get("/v1/models", headers=reader_headers)
        assert response.status_code == 200
        for model in response.json()["models"]:
            assert "pricing" in model
            assert model["pricing"]["priced"] in (True, False)


class TestDescribePostureStrictMode:
    def test_strict_undeterminable_does_not_raise_but_reports_it(self):
        env = {STRICT_ENV: "1"}
        report = describe_posture(env=env)
        assert report["determinable"] is False
        assert report["strict"] is True
        assert report["strict_raised"] is True
        assert report["is_production"] is False
        assert report["vars"][LIUHAO_ENV] is None

    def test_fail_closed_primitive_still_raises(self):
        with pytest.raises(PostureUndeterminable):
            is_production(env={STRICT_ENV: "1"})

    def test_non_strict_undeterminable_is_not_flagged(self):
        report = describe_posture(env={})
        assert report["determinable"] is False
        assert report["strict"] is False
        assert report["strict_raised"] is False

    def test_determined_posture_is_unaffected(self):
        report = describe_posture(env={ENVIRONMENT_ENV: "production", STRICT_ENV: "1"})
        assert report["determinable"] is True
        assert report["is_production"] is True
        assert report["strict_raised"] is False
