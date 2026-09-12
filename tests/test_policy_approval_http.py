"""Policy C-4 at the HTTP boundary — the approval channel's real contract.

The grant *semantics* are already pinned in process: 22 cases in
``tests/kernels/test_sovereignty_grants.py`` and the in-process harness
``scripts/verify_c4_approval_channel.py``. Neither ever comes through the
gateway. These tests do, because the console's 「审批中心」 panel is wired to
exactly these three endpoints — and a panel may only claim what is true over
HTTP.

Pinned here:

* **The principal is token-only.** A body that names an approver is not
  believed; the token's ``sub`` is. This is the whole reason the panel needs a
  token input rather than a principal field.
* **The posture snapshot is authenticated.** ``GET /v1/policy/enforcement``
  enumerates the *armed* surface, which is a map of what is **not** gated. It
  must not be readable anonymously.
* **The surface is exactly four endpoints.** No "execute a kernel action over
  HTTP" route may appear without this list changing — design decision
  §10.8.3-1 deliberately refuses remote kernel-action execution.
* **The loop is honest.** Issuing a grant does **not** make an HTTP retry
  succeed; execution stays in-process inside ``grant_window``. Measured
  2026-09-12: ``no-grant=defer`` -> ``after-grant=defer`` ->
  ``inside-window=allow``. That is why the 409 body must name the real
  mechanism instead of promising a retry that cannot work.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.kernels import _enforcement as enf
from src.kernels import _sovereignty as sov
from src.kernels._crosscutting import PolicyDeferredError, kernel_action
from src.kernels.identity import IdentityManager, IdentityScope, IdentityStatus
from src.security.jwt_handler import create_access_token

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

GATED_ACTION = "capability.retire"          # CRITICAL
LOW_ACTION = "event.publish"                # LOW -- never enforcement-gated


@pytest.fixture(autouse=True)
def clean_switch(monkeypatch):
    """Start unarmed, with no leftover grants, and with the switch restored."""
    monkeypatch.delenv(enf.ENV_VAR, raising=False)
    enf.reload()
    sov.clear_grants()
    yield
    enf.reload()
    sov.clear_grants()


@pytest.fixture
def human(monkeypatch):
    """Isolated IdentityManager holding one ACTIVE human identity."""
    mgr = IdentityManager()
    person = mgr.create_identity(
        "human-r76-approver", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    assert person.status == IdentityStatus.ACTIVE
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    return person


@pytest.fixture
def service(monkeypatch):
    """Isolated IdentityManager holding one ACTIVE *service* identity."""
    mgr = IdentityManager()
    account = mgr.create_identity(
        "svc-r76-robot", scope=IdentityScope.L1, metadata={"kind": "service"}
    )
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    return account


@pytest.fixture
def app():
    """A real gateway app. Tests that add probe routes must use *this* one."""
    return get_app()


@pytest.fixture
def client(app):
    # raise_server_exceptions=False so an *unhandled* error surfaces as a 500
    # response we can assert against, rather than escaping as an exception.
    return TestClient(app, raise_server_exceptions=False)


def _token(subject: str, ttl: int = 600) -> str:
    """Mint a real ACCESS token for ``subject`` using the gateway's own handler."""
    token, _payload = create_access_token(subject, ttl=ttl)
    return token


def _auth(subject: str) -> dict:
    return {"Authorization": f"Bearer {_token(subject)}"}


def _http_surface(app) -> list:
    """Every ``(path, methods)`` the app actually serves.

    FastAPI 0.141 no longer flattens ``include_router`` into ``app.routes``:
    an included router is stored as a ``_IncludedRouter`` wrapper carrying no
    ``path``, and the real routes hang off its ``original_router``. An audit
    that walks ``app.routes`` naively therefore sees only the four built-in
    docs routes and reports "nothing suspicious" -- a false negative, not a
    pass. Measured 2026-09-12 on fastapi 0.141.1 / starlette 1.6.0.
    """
    found: list = []
    stack = list(app.routes)
    seen: set = set()
    while stack:
        route = stack.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))

        children = getattr(route, "routes", None)
        if children is None:
            original = getattr(route, "original_router", None)
            children = getattr(original, "routes", None)
        if children:
            stack.extend(children)
            continue

        path = getattr(route, "path", None)
        if path:
            found.append((path, frozenset(getattr(route, "methods", None) or ())))
    return found


class TestPrincipalIsTokenOnly:
    """The approver can only ever be whoever the token says it is."""

    def test_missing_token_is_401(self, client):
        resp = client.post("/v1/policy/approvals", json={"actions": [GATED_ACTION]})
        assert resp.status_code == 401, resp.text

    def test_non_bearer_scheme_is_401(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers={"Authorization": "Basic YWRtaW46YWRtaW4="},
        )
        assert resp.status_code == 401, resp.text

    def test_garbage_token_is_401(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers={"Authorization": "Bearer not.a.jwt"},
        )
        assert resp.status_code == 401, resp.text

    def test_empty_bearer_token_is_401(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers={"Authorization": "Bearer "},
        )
        assert resp.status_code == 401, resp.text

    def test_body_cannot_name_the_approver(self, human, client):
        # The single most important property of this channel: a caller must not
        # be able to approve *as somebody else*. The request model has no such
        # field, and an extra key is ignored by pydantic rather than honoured.
        resp = client.post(
            "/v1/policy/approvals",
            json={
                "actions": [GATED_ACTION],
                "principal": "somebody-else-entirely",
                "issued_by": "somebody-else-entirely",
            },
            headers=_auth(human.id),
        )
        assert resp.status_code == 200, resp.text
        grant = resp.json()["grant"]
        assert grant["principal"] == human.id
        assert grant["issued_by"] == human.id
        assert "somebody-else-entirely" not in str(grant)

    def test_request_model_has_no_approver_field(self):
        # Guards the test above from rotting: if someone adds ``principal`` to
        # the model, the body would start winning and this fails loudly.
        from src.gateway.policy import ApprovalRequest

        fields = set(ApprovalRequest.model_fields)
        assert fields == {"actions", "reason", "ttl_seconds"}, fields

    def test_token_for_an_unknown_principal_is_400(self, human, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers=_auth("no-such-identity"),
        )
        assert resp.status_code == 400, resp.text
        assert "unknown principal" in resp.json()["detail"]

    def test_service_identity_may_not_hold_sovereignty(self, service, client):
        # OD-010: a service account must never be able to approve its own
        # HIGH/CRITICAL action -- that would make the gate self-releasing.
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers=_auth(service.id),
        )
        assert resp.status_code == 400, resp.text
        assert "service" in resp.json()["detail"]

    def test_reads_are_authenticated_too(self, client):
        for path in ("/v1/policy/approvals", "/v1/policy/enforcement"):
            assert client.get(path).status_code == 401, path


class TestPostureSnapshotIsAuthenticated:
    """The enforcement snapshot is a map of what is *not* gated."""

    def test_enforcement_reports_the_real_arming(self, human, client):
        os.environ[enf.ENV_VAR] = "CRITICAL"
        enf.reload()
        body = client.get("/v1/policy/enforcement", headers=_auth(human.id)).json()
        assert body["enabled"] is True
        assert body["spec"] == "CRITICAL"
        assert GATED_ACTION in body["enforced_actions"]
        assert body["config_error"] is None

    def test_unarmed_reports_record_only(self, human, client):
        body = client.get("/v1/policy/enforcement", headers=_auth(human.id)).json()
        assert body["enabled"] is False
        assert body["enforced_actions"] == []


class TestGrantLifecycleOverHttp:
    def test_issue_then_list_then_fetch(self, human, client):
        created = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION], "reason": "quarterly cleanup"},
            headers=_auth(human.id),
        )
        assert created.status_code == 200, created.text
        assert created.json()["approved"] is True
        grant_id = created.json()["grant"]["grant_id"]

        listed = client.get("/v1/policy/approvals", headers=_auth(human.id)).json()
        assert listed["count"] == 1
        assert listed["grants"][0]["grant_id"] == grant_id
        # The list response also carries the posture, so the panel needs one
        # round trip rather than two.
        assert "enforcement" in listed
        assert listed["enforcement"]["env_var"] == enf.ENV_VAR

        fetched = client.get(
            f"/v1/policy/approvals/{grant_id}", headers=_auth(human.id)
        ).json()
        assert fetched["grant_id"] == grant_id
        assert fetched["actions"] == [GATED_ACTION]
        assert fetched["is_active"] is True
        assert fetched["remaining_seconds"] > 0

    def test_ttl_ceiling_is_enforced_over_http(self, human, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION], "ttl_seconds": sov.MAX_GRANT_TTL_SECONDS + 1},
            headers=_auth(human.id),
        )
        assert resp.status_code == 422, resp.text

    def test_a_low_action_cannot_be_granted(self, human, client):
        # A grant for a never-escalated action would look like authority while
        # doing nothing -- refuse it rather than record a no-op.
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": [LOW_ACTION]},
            headers=_auth(human.id),
        )
        assert resp.status_code == 400, resp.text
        assert "enforcement-gated" in resp.json()["detail"]

    def test_an_unknown_action_cannot_be_granted(self, human, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": ["totally.made.up"]},
            headers=_auth(human.id),
        )
        assert resp.status_code == 400, resp.text
        assert "unknown kernel action" in resp.json()["detail"]

    def test_empty_action_list_is_rejected(self, human, client):
        resp = client.post(
            "/v1/policy/approvals", json={"actions": []}, headers=_auth(human.id)
        )
        assert resp.status_code == 422, resp.text

    def test_revoke_then_hidden_from_the_default_listing(self, human, client):
        grant_id = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers=_auth(human.id),
        ).json()["grant"]["grant_id"]

        revoked = client.delete(
            f"/v1/policy/approvals/{grant_id}?reason=changed+my+mind",
            headers=_auth(human.id),
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] is True
        assert revoked.json()["grant"]["revoked_at"] is not None

        assert client.get("/v1/policy/approvals", headers=_auth(human.id)).json()["count"] == 0
        with_revoked = client.get(
            "/v1/policy/approvals?include_revoked=true", headers=_auth(human.id)
        ).json()
        assert with_revoked["count"] == 1
        # Still fetchable by id -- a revoked grant is history, not a 404.
        assert (
            client.get(f"/v1/policy/approvals/{grant_id}", headers=_auth(human.id))
            .json()["revoked_at"]
            is not None
        )

    def test_revoke_is_idempotent(self, human, client):
        grant_id = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers=_auth(human.id),
        ).json()["grant"]["grant_id"]
        first = client.delete(
            f"/v1/policy/approvals/{grant_id}", headers=_auth(human.id)
        ).json()["grant"]["revoked_at"]
        second = client.delete(
            f"/v1/policy/approvals/{grant_id}", headers=_auth(human.id)
        ).json()["grant"]["revoked_at"]
        assert first == second, "re-revoking must not move the revocation time"

    def test_unknown_grant_is_404(self, human, client):
        assert (
            client.get("/v1/policy/approvals/nope", headers=_auth(human.id)).status_code
            == 404
        )
        assert (
            client.delete("/v1/policy/approvals/nope", headers=_auth(human.id)).status_code
            == 404
        )


class TestTheLoopIsHonestOverHttp:
    """A grant is an audited authorization record, not a key to the gate."""

    def test_grant_does_not_open_the_gate_for_an_http_retry(
        self, human, client, app, monkeypatch
    ):
        # Measured 2026-09-12. Kept as a test because the moment this becomes
        # *false* the console may start promising that a retry works -- and it
        # is design decision §10.8.3-1 that it must not.
        monkeypatch.setenv(enf.ENV_VAR, "CRITICAL")
        enf.reload()

        @kernel_action(GATED_ACTION)  # NOTE: enforce NOT set -- the switch decides
        def retire():
            return {"retired": True}

        @app.post("/_test/r76/retire")
        async def _retire():
            return retire()

        first = client.post("/_test/r76/retire")
        assert first.status_code == 409, first.text
        assert first.json()["error"] == "policy_approval_required"
        assert first.json()["verdict"] == "defer"

        issued = client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION], "reason": "r76 loop probe"},
            headers=_auth(human.id),
        )
        assert issued.status_code == 200

        second = client.post("/_test/r76/retire")
        assert second.status_code == 409, (
            "the gate opened on an HTTP retry -- if this is now intended, the "
            "409 body and the console copy must change with it\n" + second.text
        )

    def test_the_409_detail_names_the_real_mechanism(self, client, app, monkeypatch):
        # The old copy said "issue a grant ... then retry inside its window",
        # which no HTTP client can do. The replacement must name the actual
        # remedy, and must not survive as a promise.
        monkeypatch.setenv(enf.ENV_VAR, "CRITICAL")
        enf.reload()

        @app.get("/_test/r76/hint")
        async def _hint():
            raise PolicyDeferredError(GATED_ACTION, "default_deny")

        detail = client.get("/_test/r76/hint").json()["detail"]
        assert "/v1/policy/approvals" in detail, "must still point at the entry point"
        assert "grant_window" in detail, (
            "must name the in-process mechanism that actually unblocks it"
        )

    def test_issuing_a_grant_arms_nothing(self, human, client):
        # A grant must not widen the gate: the posture snapshot is unchanged
        # and stays unchanged after a grant is issued.
        before = client.get("/v1/policy/enforcement", headers=_auth(human.id)).json()
        client.post(
            "/v1/policy/approvals",
            json={"actions": [GATED_ACTION]},
            headers=_auth(human.id),
        )
        after = client.get("/v1/policy/enforcement", headers=_auth(human.id)).json()
        assert before["enforced_actions"] == after["enforced_actions"]
        assert before["enabled"] == after["enabled"]


class TestThePolicySurfaceIsExactlyFourEndpoints:
    """Design decision §10.8.3-1: no remote kernel-action execution, ever."""

    def test_no_extra_policy_route_appeared(self, app):
        surface = sorted(
            {
                (method, path)
                for path, methods in _http_surface(app)
                if path.startswith("/v1/policy")
                for method in methods
            }
        )
        assert surface == [
            ("DELETE", "/v1/policy/approvals/{grant_id}"),
            ("GET", "/v1/policy/approvals"),
            ("GET", "/v1/policy/approvals/{grant_id}"),
            ("GET", "/v1/policy/enforcement"),
            ("POST", "/v1/policy/approvals"),
        ], (
            "the policy surface changed. If this is a new *execution* route, it "
            "is a reversal of §10.8.3-1 and needs its own decision, not a test edit"
        )

    def test_no_route_takes_a_kernel_action_name_to_run(self, app):
        # Weaker but broader than the list above: nothing under /v1/ should be
        # shaped like "give me an action name and I'll call it".
        suspicious = [path for path, _ in _http_surface(app) if path.startswith("/v1/") and "execute" in path]
        assert suspicious == [], suspicious

    def test_the_surface_walk_can_see_the_whole_app(self, app):
        # Guards the two tests above against a false negative. FastAPI 0.141
        # stopped flattening ``include_router`` into ``app.routes``: each
        # included router is a ``_IncludedRouter`` wrapper with no ``path``, so
        # a naive ``for r in app.routes`` audit sees almost nothing and passes
        # vacuously. Measured 2026-09-12 on fastapi 0.141.1 / starlette 1.6.0.
        surface = _http_surface(app)
        assert len(surface) > 20, (
            "the walk is collapsing -- an empty surface would make the two "
            "tests above trivially green"
        )
        assert any(p.startswith("/v1/chat") for p, _ in surface)
        assert any(p.startswith("/v1/policy") for p, _ in surface)


class TestTheConsoleContractStaysInStep:
    """The console is a consumer of these endpoints; its assumptions are pinned too."""

    def test_builtin_machine_principals_match_the_identity_kernel(self):
        # The panel warns when the acting identity is a built-in *machine*
        # account (open item C-7: the gate's deny-list accepts them as human).
        # The list lives in TypeScript, so nothing but this test stops the two
        # sides drifting apart when an identity is added or renamed.
        from src.kernels.identity import INTERNAL_SERVICE_PRINCIPAL

        client_src = (
            REPO_ROOT
            / "apps"
            / "console"
            / "console"
            / "src"
            / "lib"
            / "policyClient.ts"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"BUILTIN_MACHINE_PRINCIPALS:\s*string\[\]\s*=\s*\[([^\]]*)\]", client_src
        )
        assert match, "the console no longer declares BUILTIN_MACHINE_PRINCIPALS"
        declared = set(re.findall(r"'([^']+)'", match.group(1)))
        assert declared == {"system", INTERNAL_SERVICE_PRINCIPAL}, declared

    def test_the_console_build_is_wired_into_ci(self):
        # The console had no CI coverage at all before Round 76, so a broken
        # TypeScript change could land unnoticed. If the steps are dropped,
        # this fails rather than letting the coverage rot silently.
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "apps/console/console" in ci, "the console is no longer built in CI"
        assert "npm run build" in ci, "the console type-check/build step is gone"
        assert "npm run lint" in ci, "the console lint step is gone"

    def test_the_console_declares_no_principal_field_on_the_write_path(self):
        # Mirrors test_request_model_has_no_approver_field on the client side:
        # the panel must not offer a field the backend would have to distrust.
        client_src = (
            REPO_ROOT
            / "apps"
            / "console"
            / "console"
            / "src"
            / "lib"
            / "policyClient.ts"
        ).read_text(encoding="utf-8")
        body = client_src.split("export function issueApproval", 1)[1].split("}", 1)[0]
        assert "principal" not in body, (
            "the issue request body must not carry a principal -- the subject "
            "comes from the token only"
        )


if __name__ == "__main__":  # pragma: no cover - manual run helper
    sys.exit(pytest.main([__file__, "-v"]))
