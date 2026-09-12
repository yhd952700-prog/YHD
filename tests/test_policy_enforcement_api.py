"""Policy C-5/C-6 — the enforcement decision, pinned at the HTTP boundary.

The C-2/C-3 gate raises ``PolicyDeferredError`` (needs a verified human's
approval) or ``PolicyDeniedError`` (fail-closed hard deny). Both subclass
``PermissionError`` and nothing handled them, so before this round they fell
through to the gateway's catch-all handler and surfaced as
**500 internal_server_error** -- the one status that tells an operator nothing.

These tests pin two things:

* the boundary contract -- deferred is **409** (not forbidden, awaiting
  approval), hard-denied is **403**, and never 500;
* the deployment decision itself -- exactly one file may arm the switch, and it
  must arm the whole gated surface **minus the audited exemptions** (C-6):
  arming nothing extra, and silently dropping nothing.

Reachability of an armed action cannot be asserted from here (it is a runtime
property, and the dangerous call sites live inside the defining module where
they are textually indistinguishable from inert ones). That half is measured by
``scripts/verify_armed_actions_are_inert.py``.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.kernels import _enforcement as enf
from src.kernels._crosscutting import (
    PolicyDeferredError,
    PolicyDeniedError,
    kernel_action,
)
from src.kernels._risk_classification import KERNEL_ACTION_RISK, RiskTier
from src.kernels._sovereignty import clear_grants
from src.kernels.identity import IdentityManager, IdentityScope, IdentityStatus

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROD_MANIFEST = "docker-compose.prod.yml"

CRITICAL_ACTIONS = frozenset(
    a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.CRITICAL
)
HIGH_ACTIONS = frozenset(
    a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.HIGH
)


@pytest.fixture(autouse=True)
def clean_switch(monkeypatch):
    """Every test starts unarmed, with no leftover approval grants."""
    monkeypatch.delenv(enf.ENV_VAR, raising=False)
    enf.reload()
    clear_grants()
    yield
    enf.reload()
    clear_grants()


@pytest.fixture
def human_env(monkeypatch):
    """Isolated IdentityManager holding one ACTIVE human identity."""
    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-c5-http", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    assert human.status == IdentityStatus.ACTIVE
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    return mgr, human


@pytest.fixture
def app():
    """A real gateway app -- the exception handlers live inside get_app()."""
    return get_app()


@pytest.fixture
def client(app):
    # raise_server_exceptions=False so an *unhandled* error shows up as a 500
    # response we can assert against, rather than escaping as an exception.
    return TestClient(app, raise_server_exceptions=False)


class TestEnforcementStatusIsHonestOverHttp:
    """409 = awaiting approval. 403 = denied. Never 500."""

    def test_deferred_action_is_409_not_500(self, client, app):
        @app.get("/_test/round73/deferred")
        async def _deferred():
            raise PolicyDeferredError("capability.retire", "default_deny")

        resp = client.get("/_test/round73/deferred")
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error"] == "policy_approval_required"
        assert body["action"] == "capability.retire"
        assert body["verdict"] == "defer"
        assert body["rule"] == "default_deny"

    def test_hard_denied_action_is_403_not_500(self, client, app):
        @app.get("/_test/round73/denied")
        async def _denied():
            raise PolicyDeniedError("capability.retire", "error", "default_deny")

        resp = client.get("/_test/round73/denied")
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"] == "policy_denied"
        assert body["action"] == "capability.retire"
        assert body["verdict"] == "error"

    def test_409_tells_the_caller_how_to_get_approved(self, client, app):
        @app.get("/_test/round73/hint")
        async def _hint():
            raise PolicyDeferredError("security.set_abac_rule", "default_deny")

        detail = client.get("/_test/round73/hint").json()["detail"]
        assert "/v1/policy/approvals" in detail

    def test_deferred_is_a_permission_error_so_legacy_guards_still_catch_it(self):
        # The 409 mapping must not cost the property the C-2 design relied on:
        # existing ``except PermissionError`` guards keep working.
        assert issubclass(PolicyDeferredError, PolicyDeniedError)
        assert issubclass(PolicyDeniedError, PermissionError)


class TestArmedGateEndToEndOverHttp:
    """The switch really arms a *production-shaped* call site (enforce=False)."""

    def test_armed_critical_action_defers_over_http(self, human_env, monkeypatch, app, client):
        monkeypatch.setenv(enf.ENV_VAR, "CRITICAL")
        enf.reload()

        @kernel_action("capability.retire")  # NOTE: enforce NOT set
        def retire():
            return {"retired": True}

        @app.post("/_test/round73/retire")
        async def _retire():
            return retire()

        resp = client.post("/_test/round73/retire")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"] == "policy_approval_required"

    def test_unarmed_critical_action_still_runs(self, human_env, app, client):
        # Switch off (the autouse fixture guarantees it): the gate stays inert,
        # so the C-1 record-only (L1) contract is preserved.
        @kernel_action("capability.retire")  # NOTE: enforce NOT set
        def retire():
            return {"retired": True}

        @app.post("/_test/round73/retire-open")
        async def _retire_open():
            return retire()

        resp = client.post("/_test/round73/retire-open")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"retired": True}

    def test_armed_switch_does_not_leak_to_unselected_critical_action(
        self, human_env, monkeypatch, app, client
    ):
        monkeypatch.setenv(enf.ENV_VAR, "capability.retire")
        enf.reload()

        @kernel_action("security.set_abac_rule")  # CRITICAL but NOT selected
        def set_rule():
            return {"set": True}

        @app.post("/_test/round73/set-rule")
        async def _set_rule():
            return set_rule()

        assert client.post("/_test/round73/set-rule").status_code == 200


class TestProductionArmingDecision:
    """C-6: exactly one file arms the switch, and it arms the audited surface."""

    def test_production_manifest_arms_the_audited_surface(self):
        text = (REPO_ROOT / PROD_MANIFEST).read_text(encoding="utf-8")
        match = re.search(r"LIUHAO_KERNEL_POLICY_ENFORCE=[^\n]*:-([^}]*)\}", text)
        assert match, "the production manifest does not arm the switch"
        spec = match.group(1).strip()
        assert spec == "HIGH,CRITICAL", spec

        expected = (HIGH_ACTIONS | CRITICAL_ACTIONS) - set(enf.EXEMPT_ACTIONS)
        armed = enf.parse_spec(spec)
        assert armed == expected, (
            "missing=%s extra=%s"
            % (sorted(expected - armed), sorted(armed - expected))
        )
        assert len(CRITICAL_ACTIONS) == 2
        assert len(HIGH_ACTIONS) == 15
        assert len(expected) == 16

    def test_no_other_file_arms_the_switch(self):
        config_suffixes = {".yml", ".yaml", ".env", ".toml", ".ini", ".cfg", ".sh", ".json"}
        offenders = []
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(rel.startswith(p) for p in (".venv/", ".git/", "node_modules/",
                                               "tests/", "scripts/")):
                continue
            if (path.suffix.lower() not in config_suffixes
                    and not path.name.startswith("Dockerfile")):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"LIUHAO_KERNEL_POLICY_ENFORCE\s*=", text):
                offenders.append(rel)
        assert offenders == [PROD_MANIFEST], offenders

    def test_ci_never_arms_the_switch(self):
        workflows = REPO_ROOT / ".github" / "workflows"
        for path in sorted(workflows.glob("*.yml")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "LIUHAO_KERNEL_POLICY_ENFORCE" not in text, path.name

    def test_no_exempt_action_is_armed(self):
        # An exemption is a recorded call-site measurement, not a policy taste:
        # the action is reached by production during normal operation, so arming
        # it would convert a working flow into "awaiting approval". This test
        # fails the day someone arms one without first removing the call site.
        armed = enf.parse_spec("HIGH,CRITICAL")
        assert not (armed & set(enf.EXEMPT_ACTIONS))
        assert enf.EXEMPT_ACTIONS, "an empty exemption set would make this vacuous"

    def test_the_inertness_guard_is_wired_into_ci(self):
        # The static decision above is only half the invariant; the other half
        # (is an armed action actually reachable?) is measured, not asserted.
        # If that guard is ever dropped from CI, the decision loses its teeth.
        guard = REPO_ROOT / "scripts" / "verify_armed_actions_are_inert.py"
        assert guard.is_file(), "the inertness guard is missing"
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "verify_armed_actions_are_inert.py" in ci

    def test_arming_is_a_deployment_decision_not_a_code_edit(self):
        # The whole point of the single switch: all 43 production call sites
        # keep enforce=False, so arming never requires editing them.
        text = (REPO_ROOT / "src" / "kernels" / "_crosscutting.py").read_text(
            encoding="utf-8"
        )
        assert "is_enforced(action, effective_risk)" in text
