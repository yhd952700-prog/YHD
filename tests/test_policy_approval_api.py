"""Policy C-4 — the *authenticated* approval entry point (HTTP).

The whole point of these tests is the security property that C-3 alone did not
have: **the authorising principal can only come from a verified identity
token**. A caller that puts a different ``principal`` in the request body must
not be able to approve anything on someone else's behalf.

Beyond auth, they cover the operational surface: issue / list / fetch / revoke
approvals, the enforcement-config snapshot, and the fail-loud 400s for
non-gated or unknown actions.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway import policy as policy_module
from src.gateway.policy import router as policy_router
from src.kernels._sovereignty import clear_grants, get_grant
from src.kernels.identity import IdentityManager, IdentityScope, IdentityStatus
from src.security import get_jwt_handler

HUMAN_ID = "human-api-approver"
OTHER_HUMAN_ID = "human-api-someone-else"


@pytest.fixture
def humans(monkeypatch):
    mgr = IdentityManager()
    for subject in (HUMAN_ID, OTHER_HUMAN_ID):
        ident = mgr.create_identity(
            subject, scope=IdentityScope.L0, metadata={"kind": "human"}
        )
        assert ident.status == IdentityStatus.ACTIVE
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    clear_grants()
    yield mgr
    clear_grants()


@pytest.fixture
def client(humans):
    app = FastAPI()
    app.include_router(policy_router)
    return TestClient(app)


def _auth(subject: str) -> dict:
    token, _payload = get_jwt_handler().create_token(subject=subject)
    return {"Authorization": f"Bearer {token}"}


class TestAuthenticationIsRequired:
    def test_missing_token_is_401(self, client):
        assert client.post("/v1/policy/approvals", json={"actions": ["capability.retire"]}).status_code == 401

    def test_malformed_header_is_401(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers={"Authorization": "Token abc"},
        )
        assert resp.status_code == 401

    def test_garbage_token_is_401(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers={"Authorization": "Bearer not-a-jwt"},
        )
        assert resp.status_code == 401

    def test_valid_token_but_unregistered_principal_is_400(self, client):
        # Signature is fine, but the subject is not an ACTIVE human identity.
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers=_auth("ghost-user"),
        )
        assert resp.status_code == 400
        assert "unknown principal" in resp.json()["detail"]

    def test_listing_requires_auth(self, client):
        assert client.get("/v1/policy/approvals").status_code == 401
        assert client.get("/v1/policy/enforcement").status_code == 401


class TestIssueApproval:
    def test_issue_returns_grant_bound_to_the_token_subject(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={
                "actions": ["capability.retire"],
                "reason": "planned retirement",
                "ttl_seconds": 60,
            },
            headers=_auth(HUMAN_ID),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["approved"] is True
        assert body["grant"]["principal"] == HUMAN_ID
        assert body["grant"]["actions"] == ["capability.retire"]
        assert body["grant"]["reason"] == "planned retirement"
        assert body["grant"]["is_active"] is True
        assert get_grant(body["grant"]["grant_id"]) is not None

    def test_body_cannot_name_someone_else_as_the_approver(self, client, humans):
        """The核心安全断言：请求体里的 principal 必须被忽略。"""
        resp = client.post(
            "/v1/policy/approvals",
            json={
                "actions": ["capability.retire"],
                "principal": OTHER_HUMAN_ID,
                "issued_by": OTHER_HUMAN_ID,
            },
            headers=_auth(HUMAN_ID),
        )
        assert resp.status_code == 200
        grant = resp.json()["grant"]
        assert grant["principal"] == HUMAN_ID
        assert grant["principal"] != OTHER_HUMAN_ID
        assert grant["issued_by"] == HUMAN_ID

    def test_unknown_action_is_400(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": ["not.a.real.action"]},
            headers=_auth(HUMAN_ID),
        )
        assert resp.status_code == 400
        assert "unknown kernel action" in resp.json()["detail"]

    def test_non_gated_action_is_400(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": ["memory.store"]},
            headers=_auth(HUMAN_ID),
        )
        assert resp.status_code == 400
        assert "enforcement-gated" in resp.json()["detail"]

    def test_empty_action_list_is_422(self, client):
        resp = client.post(
            "/v1/policy/approvals", json={"actions": []}, headers=_auth(HUMAN_ID)
        )
        assert resp.status_code == 422

    def test_out_of_range_ttl_is_422(self, client):
        for ttl in (0, -5, 99999):
            resp = client.post(
                "/v1/policy/approvals",
                json={"actions": ["capability.retire"], "ttl_seconds": ttl},
                headers=_auth(HUMAN_ID),
            )
            assert resp.status_code == 422

    def test_multiple_critical_actions(self, client):
        resp = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire", "security.set_abac_rule"]},
            headers=_auth(HUMAN_ID),
        )
        assert resp.status_code == 200
        assert resp.json()["grant"]["actions"] == [
            "capability.retire",
            "security.set_abac_rule",
        ]


class TestListAndFetch:
    def test_list_contains_the_grant_and_the_enforcement_snapshot(self, client):
        created = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers=_auth(HUMAN_ID),
        ).json()["grant"]

        body = client.get("/v1/policy/approvals", headers=_auth(HUMAN_ID)).json()
        assert body["count"] == 1
        assert body["grants"][0]["grant_id"] == created["grant_id"]
        # Enforcement defaults to OFF: the kernel layer is still record-only.
        assert body["enforcement"]["enabled"] is False

    def test_fetch_by_id(self, client):
        created = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers=_auth(HUMAN_ID),
        ).json()["grant"]

        resp = client.get(
            f"/v1/policy/approvals/{created['grant_id']}", headers=_auth(HUMAN_ID)
        )
        assert resp.status_code == 200
        assert resp.json()["grant_id"] == created["grant_id"]

    def test_fetch_unknown_is_404(self, client):
        resp = client.get("/v1/policy/approvals/nope", headers=_auth(HUMAN_ID))
        assert resp.status_code == 404


class TestRevoke:
    def test_revoke_records_revoker_and_reason(self, client):
        created = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers=_auth(HUMAN_ID),
        ).json()["grant"]

        resp = client.delete(
            f"/v1/policy/approvals/{created['grant_id']}",
            params={"reason": "no longer needed"},
            headers=_auth(HUMAN_ID),
        )
        assert resp.status_code == 200
        grant = resp.json()["grant"]
        assert grant["is_active"] is False
        assert grant["revoke_reason"] == "no longer needed"
        assert grant["revoked_at"] is not None

    def test_revoked_grant_leaves_the_active_listing(self, client):
        created = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers=_auth(HUMAN_ID),
        ).json()["grant"]
        client.delete(
            f"/v1/policy/approvals/{created['grant_id']}", headers=_auth(HUMAN_ID)
        )
        assert client.get("/v1/policy/approvals", headers=_auth(HUMAN_ID)).json()["count"] == 0

    def test_revoke_is_idempotent_via_api(self, client):
        created = client.post(
            "/v1/policy/approvals",
            json={"actions": ["capability.retire"]},
            headers=_auth(HUMAN_ID),
        ).json()["grant"]
        url = f"/v1/policy/approvals/{created['grant_id']}"
        first = client.delete(url, headers=_auth(HUMAN_ID)).json()["grant"]
        second = client.delete(url, headers=_auth(HUMAN_ID)).json()["grant"]
        assert first["revoked_at"] == second["revoked_at"]

    def test_revoke_unknown_is_404(self, client):
        resp = client.delete("/v1/policy/approvals/nope", headers=_auth(HUMAN_ID))
        assert resp.status_code == 404


class TestEnforcementSnapshot:
    def test_default_is_off(self, client):
        body = client.get("/v1/policy/enforcement", headers=_auth(HUMAN_ID)).json()
        assert body["env_var"] == policy_module.describe_enforcement()["env_var"]
        assert body["enabled"] is False
        assert body["enforced_actions"] == []
