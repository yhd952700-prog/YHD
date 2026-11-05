"""审批中心「待处理」列表（GET /v1/policy/approvals/pending）。

审批中心此前只有"已签发凭据"，没有"还有什么在等我"。这里验证三件事：

1. 一个被策略挂起（``defer``）的真实拦截记录会出现在待处理列表里；
2. 一旦有有效凭据覆盖该动作，它就不再"待处理"（不是一份静态清单）；
3. **跨主体不可见**：没有签发权限的主体拿不到这个队列（403），
   而不是"看到别人的待办"。

数据来源是内核真实的审计事件（``outcome="blocked"`` +
``policy_decision="defer"``），不是另造一套台账。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.policy import router as policy_router
from src.kernels._sovereignty import clear_grants, issue_grant
from src.kernels.audit import AuditEventType, AuditScope, log_event
from src.kernels.identity import IdentityManager, IdentityScope
from src.security import get_jwt_handler

ADMIN_ID = "human-pending-admin"
ORDINARY_ID = "human-pending-ordinary"

_ACTION = "capability.retire"
_TARGET = "kernel-target:pending-demo"


@pytest.fixture
def humans(monkeypatch):
    mgr = IdentityManager()
    mgr.create_identity(ADMIN_ID, scope=IdentityScope.L0, metadata={"kind": "human"})
    mgr.create_identity(ORDINARY_ID, scope=IdentityScope.L1,
                        metadata={"kind": "human"})
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    clear_grants()
    yield mgr
    clear_grants()


@pytest.fixture
def client(humans):
    app = FastAPI()
    app.include_router(policy_router)
    return TestClient(app)


def _auth(subject: str, scope: str = "L0") -> dict:
    token, _payload = get_jwt_handler().create_token(
        subject=subject, metadata={"kind": "human", "scope": scope}
    )
    return {"Authorization": f"Bearer {token}"}


def _seed_deferred(action: str = _ACTION, correlation: str = "exec-1") -> None:
    """写入一条与内核拦截路径同形的审计事件（defer）。"""
    log_event(
        AuditEventType.STATE_CHANGE,
        principal_id="service:execution_kernel",
        scope=AuditScope.L0,
        outcome="blocked",
        details={
            "action": action,
            "risk_level": "HIGH",
            "policy_decision": "defer",
            "policy_rule": "default_deny",
            "policy_enforced": True,
            "sovereignty_grant": None,
        },
        correlation_id=correlation,
    )


class TestPendingApprovals:
    def test_deferred_action_appears_with_actionable_next_step(self, client):
        _seed_deferred()
        resp = client.get("/v1/policy/approvals/pending", headers=_auth(ADMIN_ID))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 1
        item = body["items"][0]
        assert item["action"] == _ACTION
        assert item["risk_level"] == "HIGH"
        assert item["correlation_id"] == "exec-1"
        # 界面要能直接处理，而不只是展示
        assert item["approve"]["method"] == "POST"
        assert item["approve"]["path"] == "/v1/policy/approvals"
        assert item["approve"]["body"]["actions"] == [_ACTION]

    def test_hard_deny_is_not_pending(self, client):
        """deny/error 是硬拒绝，改代码才行，不该混进"等我确认"。"""
        log_event(
            AuditEventType.STATE_CHANGE,
            principal_id="service:execution_kernel",
            scope=AuditScope.L0,
            outcome="blocked",
            details={
                "action": _ACTION,
                "risk_level": "CRITICAL",
                "policy_decision": "deny",
                "policy_rule": "default_deny",
            },
            correlation_id="exec-deny",
        )
        resp = client.get("/v1/policy/approvals/pending", headers=_auth(ADMIN_ID))
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_active_grant_removes_item(self, client):
        _seed_deferred()
        before = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()
        assert before["count"] == 1

        issue_grant(
            ADMIN_ID,
            [_ACTION],
            target=_TARGET,
            reason="同意该动作",
            ttl_seconds=600,
            issued_by=ADMIN_ID,
        )

        after = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()
        assert after["count"] == 0
        assert after["items"] == []

    def test_cross_subject_is_not_visible(self, client):
        """没有签发权限的主体看不到这个队列 —— 404 空列表也不给。"""
        _seed_deferred()
        resp = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ORDINARY_ID, "L1")
        )
        assert resp.status_code == 403

    def test_missing_token_is_401(self, client):
        _seed_deferred()
        assert client.get("/v1/policy/approvals/pending").status_code == 401

    def test_duplicate_blocks_collapse_to_one_card(self, client):
        _seed_deferred(correlation="exec-1")
        _seed_deferred(correlation="exec-1")
        resp = client.get("/v1/policy/approvals/pending", headers=_auth(ADMIN_ID))
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
