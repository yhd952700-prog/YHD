"""Audit verification coverage must be observable at the /v1/ready edge.

Two owner-listed guarantees are pinned here:

1. The readiness probe surfaces the audit hash-chain verification coverage
   (coverage_ratio / uncovered_events / covered_through / newest_verified_at /
   rooted_at_genesis) alongside the existing failure count -- so "the chain has
   not been re-derived in N days" cannot hide behind a green dashboard.

2. When the chain is only partially covered (uncovered_events > 0 or not
   rooted_at_genesis), the condition is reported in the response `errors` list
   (visible) WITHOUT flipping readiness to 503 on its own -- there is no
   mandated periodic verifier yet, so a populated-but-unverified deployment must
   not self-terminate.
"""

from __future__ import annotations

import json

import src.kernels.audit as audit_mod
from src.gateway.main import get_app
from fastapi.testclient import TestClient
from starlette.requests import Request


AUDIT_COVERAGE_KEYS = (
    "total_events",
    "failures",
    "coverage_ratio",
    "uncovered_events",
    "covered_through",
    "newest_verified_at",
    "rooted_at_genesis",
)


def test_ready_surfaces_audit_coverage_keys():
    """/v1/ready must expose the audit verification coverage fields."""
    with TestClient(get_app()) as client:
        body = client.get("/v1/ready").json()
    assert "audit_store" in body["checks"], "audit_store check missing from /v1/ready"
    audit = body["checks"]["audit_store"]
    for key in AUDIT_COVERAGE_KEYS:
        assert key in audit, f"audit_store check missing coverage field {key}"


async def test_ready_reports_incomplete_coverage_in_errors(monkeypatch):
    """An unverified (partially covered) chain is reported, not hidden.

    Driven via monkeypatch on the kernel accessors so the global audit-store
    singleton is never mutated -- isolation is preserved across the suite.
    """
    monkeypatch.setattr(
        audit_mod, "audit_stats",
        lambda: {"total_events": 100, "failures": 0},
    )
    monkeypatch.setattr(
        audit_mod, "audit_verification_coverage",
        lambda: {
            "coverage_ratio": 0.5,
            "uncovered_events": 50,
            "covered_through": 50,
            "newest_verified_at": None,
            "rooted_at_genesis": False,
        },
    )
    from src.gateway.health import readiness_probe

    scope = {"type": "http", "headers": [], "method": "GET", "path": "/v1/ready"}
    request = Request(scope)
    response = await readiness_probe(request)
    body = json.loads(response.body.decode())

    audit = body["checks"]["audit_store"]
    assert audit["coverage_ratio"] == 0.5
    assert audit["uncovered_events"] == 50
    assert audit["rooted_at_genesis"] is False

    errors = body.get("errors", [])
    assert any("Audit chain verification incomplete" in e for e in errors), errors
