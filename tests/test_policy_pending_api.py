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


def _reject_events(correlation: str | None = None) -> list:
    from src.gateway.audit import _read_events

    events = _read_events(outcome="denied", limit=100, reverse=True)
    return [
        event for event in events
        if (event.get("details") or {}).get("policy_decision") == "reject"
        and (correlation is None or event.get("correlation_id") == correlation)
    ]


class TestRejectPendingApproval:
    """审批不能只有「同意」：拒绝必须可用、留痕、且不产生任何放行。"""

    def test_reject_removes_card_and_writes_audit(self, client):
        _seed_deferred(correlation="exec-reject")
        item = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["items"][0]

        resp = client.post(
            "/v1/policy/approvals/reject",
            headers=_auth(ADMIN_ID),
            json={"pending_id": item["pending_id"], "reason": "这笔删除不该做"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rejected"] is True
        assert body["already_rejected"] is False
        assert body["action"] == _ACTION

        after = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()
        assert after["count"] == 0, "拒绝后这张卡片不应继续显示为等待中"

        rejects = _reject_events("exec-reject")
        assert len(rejects) == 1
        details = rejects[0]["details"]
        assert details["action"] == _ACTION
        assert details["pending_id"] == item["pending_id"]
        assert details["rejection_reason"] == "这笔删除不该做"
        assert rejects[0]["outcome"] == "denied"
        assert rejects[0]["correlation_id"] == "exec-reject"

    def test_reject_reason_may_be_empty_but_field_is_recorded(self, client):
        _seed_deferred(correlation="exec-empty-reason")
        item = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["items"][0]
        resp = client.post(
            "/v1/policy/approvals/reject",
            headers=_auth(ADMIN_ID),
            json={"pending_id": item["pending_id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["reason"] == ""
        assert "rejection_reason" in _reject_events("exec-empty-reason")[0]["details"]

    def test_reject_is_idempotent_and_audited_once(self, client):
        _seed_deferred(correlation="exec-dup-reject")
        item = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["items"][0]
        payload = {"pending_id": item["pending_id"], "reason": "不做"}
        first = client.post(
            "/v1/policy/approvals/reject", headers=_auth(ADMIN_ID), json=payload
        )
        second = client.post(
            "/v1/policy/approvals/reject", headers=_auth(ADMIN_ID), json=payload
        )
        assert first.status_code == 200 and second.status_code == 200
        assert second.json()["already_rejected"] is True
        assert len(_reject_events("exec-dup-reject")) == 1, "重复拒绝不能再写第二条审计"

    def test_rejected_action_gets_no_authority_and_is_not_replayed(self, client):
        """拒绝之后：没有凭据、没有自动重放，同一动作仍只能继续被拦。"""
        from src.kernels._sovereignty import list_grants

        _seed_deferred(correlation="exec-no-replay")
        item = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["items"][0]
        client.post(
            "/v1/policy/approvals/reject",
            headers=_auth(ADMIN_ID),
            json={"pending_id": item["pending_id"], "reason": "拒绝"},
        )
        assert list_grants() == [], "拒绝不能签发任何授权"
        # 审计里只有「拒绝」，没有「批准」或「已执行」这类放行痕迹。
        assert len(_reject_events("exec-no-replay")) == 1
        assert (event := _reject_events("exec-no-replay")[0])["outcome"] == "denied"
        assert (event.get("details") or {})["policy_decision"] == "reject"
        # 同一张卡片再查一次仍是 0：拒绝是终态，不会过一会儿自动变回待批。
        assert client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["count"] == 0

    def test_unknown_pending_id_is_404(self, client):
        resp = client.post(
            "/v1/policy/approvals/reject",
            headers=_auth(ADMIN_ID),
            json={"pending_id": "pending:does-not-exist", "reason": ""},
        )
        assert resp.status_code == 404

    def test_ordinary_human_cannot_reject(self, client):
        _seed_deferred(correlation="exec-ordinary-reject")
        item = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["items"][0]
        resp = client.post(
            "/v1/policy/approvals/reject",
            headers=_auth(ORDINARY_ID, "L1"),
            json={"pending_id": item["pending_id"], "reason": ""},
        )
        assert resp.status_code == 403


class TestApproveOnceOnly:
    """一次批准只生效一次：同一张卡片重复提交不叠加第二份授权。"""

    def test_repeated_approve_reuses_the_same_grant(self, client):
        from src.kernels._sovereignty import list_grants

        _seed_deferred(correlation="exec-approve-once")
        item = client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["items"][0]
        payload = dict(item["approve"]["body"])
        payload["confirm_wildcard"] = True

        first = client.post(
            "/v1/policy/approvals", headers=_auth(ADMIN_ID), json=payload
        )
        second = client.post(
            "/v1/policy/approvals", headers=_auth(ADMIN_ID), json=payload
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["already_approved"] is True
        assert (
            second.json()["grant"]["grant_id"]
            == first.json()["grant"]["grant_id"]
        )
        assert len(list_grants()) == 1
        assert client.get(
            "/v1/policy/approvals/pending", headers=_auth(ADMIN_ID)
        ).json()["count"] == 0
