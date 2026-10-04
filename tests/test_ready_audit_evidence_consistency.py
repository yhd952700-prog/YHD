"""``/v1/ready`` must not contradict itself: errors and checks must agree.

The defect this file exists for
-------------------------------
``src/gateway/health.py`` computed ``incomplete`` from the audit verification
coverage and appended::

    "Audit chain verification incomplete: N events uncovered, rooted_at_genesis=False"

to ``errors`` -- while leaving ``checks["audit_store"]["status"]`` hard-coded at
``"healthy"``. Since ``overall_status`` is derived from ``unhealthy_checks``
(``status != "healthy"``), the result was a payload that simultaneously said
"the evidence chain is broken" and "everything is healthy", served as::

    HTTP 200, status="ready", checks 9/9 healthy, console KPI 100%

The old docstring claimed the condition was "reported in `checks` + `errors` so
it is visible instead of hidden behind a green light". It was not: ``checks``
carried the green light.

The invariant pinned here
-------------------------
**A readiness payload must never claim the audit evidence is fine while its
``errors`` list says the coverage is incomplete.** ``errors`` is prose for
humans, ``checks`` is what machines and KPIs read; a disagreement between them
means one of the two audiences is being lied to.

Two questions, two fields
-------------------------
The root cause was one field being asked to answer two different questions:

* ``availability``    -- can the store be reached and read at all?
* ``evidence_status`` -- has the chain actually been re-derived?

They are now separate, and ``evidence_status`` has three honest values:

``verified``
    checkpoints cover the tail, rooted at genesis.
``unverified``
    events exist but no checkpoint was ever written. This is the steady state of
    every deployment, because nothing in production calls
    ``verify_incremental``/``verify_rolling`` (``/v1/audit/verify`` runs the
    read-only ``verify_integrity()`` and writes no checkpoint). An honest gap in
    evidence, not a proven break -- and gating on it would 503 every instance
    forever with no mechanism able to advance the coverage back.
``broken``
    checkpoints EXIST but no longer cover the history: a real integrity
    regression, so it does degrade the gate.

These tests are deliberately driven through the kernel accessors
(``monkeypatch``) rather than the global audit-store singleton, so the suite's
audit isolation is preserved.
"""

from __future__ import annotations

import json

import pytest

import src.kernels.audit as audit_mod
from src.gateway.health import readiness_probe
from starlette.requests import Request


#: Coverage that says "events exist, nothing was ever re-derived".
UNVERIFIED = {
    "tail_seq": 100,
    "coverage_ratio": 0.0,
    "uncovered_events": 100,
    "covered_through": 0,
    "checkpoint_count": 0,
    "newest_verified_at": None,
    "rooted_at_genesis": False,
}

#: Coverage that says "checkpoints existed, coverage regressed" -> real fault.
BROKEN = {
    "tail_seq": 100,
    "coverage_ratio": 0.5,
    "uncovered_events": 50,
    "covered_through": 50,
    "checkpoint_count": 1,
    "newest_verified_at": 1700000000.0,
    "rooted_at_genesis": False,
}

#: Coverage that says "fully re-derived, rooted at genesis".
VERIFIED = {
    "tail_seq": 100,
    "coverage_ratio": 1.0,
    "uncovered_events": 0,
    "covered_through": 100,
    "checkpoint_count": 1,
    "newest_verified_at": 1700000000.0,
    "rooted_at_genesis": True,
}


def _request():
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/v1/ready"})


async def _probe(monkeypatch, coverage, stats=None):
    """Call the real readiness probe with a stubbed audit coverage."""
    monkeypatch.setattr(
        audit_mod, "audit_stats",
        lambda: stats or {"total_events": 100, "failures": 0},
    )
    monkeypatch.setattr(audit_mod, "audit_verification_coverage", lambda: dict(coverage))
    response = await readiness_probe(_request())
    return response, json.loads(response.body.decode())


def _assert_no_contradiction(body):
    """The invariant. Errors and checks must never disagree about the chain.

    Absence counts as a violation on purpose. Before the fix there was no
    evidence verdict on the check at all, so the *only* thing a reader had was
    ``status`` -- which was hard-coded ``"healthy"``. A missing field is not a
    neutral gap; it is what let `status` silently speak for the chain.
    """
    audit = body["checks"]["audit_store"]
    errors = body.get("errors", [])
    claims_incomplete = any(
        "Audit chain verification incomplete" in e for e in errors
    )
    assert "evidence_status" in audit, (
        "CONTRADICTION: errors reports the audit chain coverage as incomplete, "
        "but checks['audit_store'] carries NO evidence verdict at all "
        f"(keys: {sorted(audit)}). Without it the only verdict on offer is "
        "'status', which cannot distinguish 'the chain was never re-derived' "
        "from 'the chain is fine'."
    )
    if claims_incomplete:
        assert audit["evidence_status"] != "verified", (
            "CONTRADICTION: errors says the audit chain coverage is incomplete "
            "while checks report evidence_status='verified'. The payload must "
            "not claim the evidence is fine in one field and broken in another."
        )
    # And the availability verdict must be present and honest.
    assert audit.get("availability") in {"available", "unavailable"}, audit


class TestNoSelfContradiction:
    """The core regression: errors and checks must agree."""

    @pytest.mark.parametrize(
        "coverage,expected_evidence",
        [
            (UNVERIFIED, "unverified"),
            (BROKEN, "broken"),
            (VERIFIED, "verified"),
        ],
    )
    async def test_errors_and_checks_never_disagree(
        self, monkeypatch, coverage, expected_evidence
    ):
        _resp, body = await _probe(monkeypatch, coverage)
        _assert_no_contradiction(body)
        assert body["checks"]["audit_store"]["evidence_status"] == expected_evidence

    async def test_incomplete_coverage_is_not_reported_as_healthy_evidence(
        self, monkeypatch
    ):
        """The exact old defect: chain incomplete, yet checks said all-good."""
        _resp, body = await _probe(monkeypatch, UNVERIFIED)
        audit = body["checks"]["audit_store"]

        # It must still be reported -- the errors entry is NOT weakened.
        assert any(
            "Audit chain verification incomplete" in e for e in body.get("errors", [])
        ), "the human-readable errors entry must not be removed"

        # And it must not be dressed up as good evidence.
        assert audit["evidence_status"] == "unverified"
        assert audit["uncovered_events"] == 100
        assert audit["rooted_at_genesis"] is False

    async def test_evidence_status_field_exists_at_all(self, monkeypatch):
        """A missing field is what let `status` silently speak for the chain.

        Before the fix there was no evidence verdict on the check at all, so the
        only thing a reader had was ``status`` -- which was hard-coded healthy.
        """
        _resp, body = await _probe(monkeypatch, BROKEN)
        assert "evidence_status" in body["checks"]["audit_store"]
        assert "availability" in body["checks"]["audit_store"]


class TestReadinessSemantics:
    """What each evidence verdict does to the readiness verdict."""

    async def test_never_verified_does_not_take_the_instance_out_of_rotation(
        self, monkeypatch
    ):
        """`unverified` must NOT 503.

        Nothing in production writes checkpoints, so this is the steady state of
        every real deployment. Gating here would make /v1/ready return 503
        permanently and K8s would pull every instance out of the load balancer
        with no mechanism able to advance coverage back -- a self-inflicted
        outage, not a safety measure.
        """
        resp, body = await _probe(monkeypatch, UNVERIFIED)
        assert body["checks"]["audit_store"]["evidence_status"] == "unverified"
        assert resp.status_code == 200, (
            "an unverified (never-checkpointed) chain must not 503: no "
            "production path advances the coverage, so this would be a "
            "permanent, self-inflicted outage"
        )
        assert body["status"] == "ready"

    async def test_broken_coverage_does_degrade_and_503(self, monkeypatch):
        """`broken` (checkpoints existed, coverage regressed) is a real fault."""
        resp, body = await _probe(monkeypatch, BROKEN)
        audit = body["checks"]["audit_store"]
        assert audit["evidence_status"] == "broken"
        assert audit["status"] == "degraded"
        assert resp.status_code == 503
        assert body["status"] == "degraded"

    async def test_verified_chain_is_healthy_and_silent(self, monkeypatch):
        resp, body = await _probe(monkeypatch, VERIFIED)
        audit = body["checks"]["audit_store"]
        assert audit["evidence_status"] == "verified"
        assert audit["status"] == "healthy"
        assert resp.status_code == 200
        assert not any(
            "Audit chain verification incomplete" in e for e in body.get("errors", [])
        )

    async def test_broken_is_reachable_in_the_unhealthy_check_list(
        self, monkeypatch
    ):
        """The gate must be driven by the check, not only by prose in errors.

        This is what makes the K8s readinessProbe actually react: the existing
        `unhealthy_checks` derivation picks up `status != "healthy"`, and the
        audit check is now non-healthy when -- and only when -- the evidence
        genuinely regressed.
        """
        _resp, body = await _probe(monkeypatch, BROKEN)
        non_healthy = [
            name
            for name, detail in body["checks"].items()
            if detail.get("status") != "healthy"
        ]
        assert "audit_store" in non_healthy


class TestAuditOutageStillUnhealthy:
    """A genuinely unreachable store must still be `unhealthy` (fail-closed)."""

    async def test_store_exception_is_unhealthy(self, monkeypatch):
        def _raise(*_a, **_k):
            raise RuntimeError("audit store unreachable")

        monkeypatch.setattr(audit_mod, "audit_stats", _raise)
        resp = await readiness_probe(_request())
        body = json.loads(resp.body.decode())
        audit = body["checks"]["audit_store"]
        assert audit["status"] == "unhealthy"
        assert audit["availability"] == "unavailable"
        assert audit["evidence_status"] == "unknown"
        assert resp.status_code == 503
        _assert_no_contradiction(body)


class TestCoverageReporterWithoutCheckpointCount:
    """Defensive: a reporter that omits checkpoint_count must not invent a break."""

    async def test_missing_checkpoint_count_is_treated_as_unverified(
        self, monkeypatch
    ):
        coverage = dict(UNVERIFIED)
        coverage.pop("checkpoint_count")
        resp, body = await _probe(monkeypatch, coverage)
        audit = body["checks"]["audit_store"]
        assert audit["evidence_status"] == "unverified", (
            "without checkpoint_count we cannot prove a regression, so we must "
            "not claim the chain is broken"
        )
        assert resp.status_code == 200
        _assert_no_contradiction(body)
