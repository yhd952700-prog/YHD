"""用量账本 ↔ 审计一致性回归。

背景（真实缺陷，已修）：
* 会话主体是 gateway 拼出的 ``liuhao-{账号}-{session}``，而审计页按账号主体
  读取。此前 ``_commit_turn`` / 权限拒绝事件都写 ``principal_id=会话主体``，
  导致同一条调用在用量页有账、在账号审计页查不到 —— 账本与审计对不上。
* ``chat`` 的权限拒绝分支返回体没有 ``agent`` 字段，网关直接取
  ``result["agent"]`` 会 500；预算护栏真的拦住时反而表现为服务端错误。
* 配额输入与配额记账用的是会话主体，导致按账号设置的 COST 配额永远命中
  不到（只能落到 system），"给某账号设预算"形同虚设。
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from src.ai import liuhao as liuhao_module
from src.ai.liuhao import LiuHaoAssistant
from src.ai.providers import BaseProvider
from src.gateway import chat as chat_module
from src.gateway.main import get_app
from src.kernels.audit import audit_query


class _OkProvider(BaseProvider):
    """最小可用 provider：固定回复，不触网。"""

    def __init__(self) -> None:
        super().__init__(name="ok-provider", model="ok-model")

    def generate(self, prompt: str, **kwargs) -> str:
        return "ok"


class _DenyDecision:
    is_allowed = False
    reason = "denied"
    denied_rules: tuple = ()


class _BudgetDenyDecision:
    is_allowed = False
    reason = "denied"
    denied_rules = (type("Rule", (), {"id": "quota_enforcement"})(),)


class _AllowDecision:
    is_allowed = True


class _MustNotGenerateProvider(BaseProvider):
    """建流前被拒时，模型与模型流都不该被碰到。"""

    def __init__(self) -> None:
        super().__init__(name="must-not-generate", model="must-not-generate")

    def generate(self, prompt: str, **kwargs) -> str:
        raise AssertionError("建流前拒绝：模型不应被调用")

    def chat_stream(self, messages):
        raise AssertionError("建流前拒绝：不应建立模型流")


@pytest.fixture(autouse=True)
def clean_sessions():
    with chat_module._sessions_lock:
        chat_module._sessions.clear()
        chat_module._session_users.clear()
        chat_module._session_last_used.clear()
    yield
    with chat_module._sessions_lock:
        chat_module._sessions.clear()
        chat_module._session_users.clear()
        chat_module._session_last_used.clear()


def test_denied_turn_returns_403_and_is_audited_under_account(
    monkeypatch, auth_headers
):
    account = f"acct-deny-{uuid.uuid4().hex[:8]}"
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1", audit_principal=account, provider=_OkProvider()
    )
    monkeypatch.setattr(
        assistant.policy, "authorize", lambda *a, **k: _DenyDecision()
    )
    recorded: list = []
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: recorded.append(kwargs) or True
    )
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)

    client = TestClient(get_app())
    resp = client.post(
        "/v1/chat",
        json={"message": "hello", "session_id": "s-deny", "use_knowledge": False},
        headers=auth_headers,
    )

    # 真权限拒绝用 403；预算拒绝用 402（见下一个用例）。前者不是服务端故障。
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["code"] == "permission_denied"
    assert "权限不足" in body["message"]
    assert body["correlation_id"], "失败调用必须能带回 correlation_id"
    assert resp.headers["X-LiuHao-Correlation-Id"] == body["correlation_id"]
    assert recorded == [], "被拒绝的轮次不得写入用量账本"

    corr = body["correlation_id"]
    events = audit_query(principal_id=account, correlation_id=corr)
    assert len(events) == 1, "账号审计页必须能按 correlation_id 看到这次拒绝"
    event = events[0]
    assert event["event_type"] == "access_denied"
    assert event["outcome"] == "deny"
    assert event["principal_id"] == account
    details = event.get("details") or {}
    assert details.get("action") == "chat"
    assert details.get("reason") == "permission_denied"


def test_budget_denial_returns_402_with_budget_copy(monkeypatch, auth_headers):
    account = f"acct-budget-{uuid.uuid4().hex[:8]}"
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1", audit_principal=account, provider=_OkProvider()
    )
    monkeypatch.setattr(
        assistant.policy, "authorize", lambda *a, **k: _BudgetDenyDecision()
    )
    picked: list = []
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: picked.append(assistant) or assistant)
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: pytest.fail("预算拒绝不得记账")
    )

    client = TestClient(get_app())
    resp = client.post(
        "/v1/chat",
        json={"message": "hello", "session_id": "s-budget", "use_knowledge": False},
        headers=auth_headers,
    )

    assert resp.status_code == 402, resp.text
    body = resp.json()
    assert body["code"] == "budget_exceeded"
    assert "预算" in body["message"] and "权限" not in body["message"]
    corr = body["correlation_id"]
    events = audit_query(principal_id=account, correlation_id=corr)
    assert len(events) == 1
    details = events[0].get("details") or {}
    assert details.get("reason") == "budget_exceeded"


def test_completed_turn_ledger_and_audit_share_account_and_correlation(
    monkeypatch,
):
    account = f"acct-ok-{uuid.uuid4().hex[:8]}"
    child = f"{account}-child"
    provider = _OkProvider()
    provider.owner_principal = child
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1", audit_principal=account, provider=provider
    )

    result = assistant.chat("hello")
    assert result["status"] == "completed"
    corr = result["correlation_id"]

    # 审计：归账号，并注明 Key 归属（账本按 key_owner 记费）。
    events = audit_query(principal_id=account, correlation_id=corr)
    assert len(events) == 1
    details = events[0].get("details") or {}
    assert events[0]["principal_id"] == account
    assert events[0]["outcome"] == "allow"
    assert details.get("action") == "chat"
    assert details.get("key_owner_account_id") == child
    assert details.get("effective_account_id") == account

    # 账本：actor=实际调用账号，key_owner=Key 归属。
    captured: list = []
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: captured.append(kwargs) or True
    )
    req = chat_module.ChatRequest(
        message="hello", session_id="s1", use_knowledge=False
    )
    wrote = chat_module._record_chat_usage(
        assistant,
        account,
        req,
        tokens_in=10,
        tokens_out=5,
        correlation_id=corr,
        estimated=False,
    )
    assert wrote is True
    assert len(captured) == 1
    assert captured[0]["actor"] == account
    assert captured[0]["key_owner"] == child
    assert captured[0]["correlation_id"] == corr


def test_quota_inputs_use_account_principal(monkeypatch):
    account = f"acct-quota-{uuid.uuid4().hex[:8]}"
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1", audit_principal=account, provider=_OkProvider()
    )
    seen: list = []

    def capture(**kwargs):
        seen.append(dict(kwargs))
        return {}

    monkeypatch.setattr(liuhao_module, "economy_inputs", capture)
    result = assistant.chat("hello")
    assert result["status"] == "completed"
    assert seen, "配额护栏必须真的评估成本"
    assert seen[0]["principal"] == account, (
        "按账号设置的 COST 配额必须命中账号主体，而不是会话主体"
    )


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_code", "must_have", "must_not_have"),
    [
        (_BudgetDenyDecision(), 402, "budget_exceeded", "预算", "权限"),
        (_DenyDecision(), 403, "permission_denied", "权限", "预算"),
    ],
)
def test_stream_denial_is_physical_before_sse(
    monkeypatch, auth_headers, caplog, decision,
    expected_status, expected_code, must_have, must_not_have,
):
    """预算/权限拒绝必须发生在建流之前：物理 402/403，没有 text/event-stream。

    这是本次契约变化的核心：此前拒绝要等 SSE 建立后走 done 帧，用户先看到
    200，再看事件里说"被拒绝"。现在响应码本身就是拒绝，且模型完全没被调用。
    """
    account = f"acct-stream-deny-{uuid.uuid4().hex[:8]}"
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1",
        audit_principal=account,
        provider=_MustNotGenerateProvider(),
    )
    monkeypatch.setattr(assistant.policy, "authorize", lambda *a, **k: decision)
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: pytest.fail("拒绝不得记账")
    )
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)

    caplog.set_level("INFO")
    client = TestClient(get_app())
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"message": "hello", "session_id": "s-stream", "use_knowledge": False},
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == expected_status, resp.text
        content_type = resp.headers.get("content-type", "")
        assert "text/event-stream" not in content_type, (
            "拒绝时不得建立 SSE：拿到 text/event-stream 就说明判定被推迟了"
        )
        assert content_type.startswith("application/json")
        chunks = list(resp.iter_text())

    body_text = "".join(chunks)
    assert "data: " not in body_text, "拒绝响应不能带任何 SSE 分块"
    body = json.loads(body_text)
    assert body["code"] == expected_code
    assert must_have in body["message"] and must_not_have not in body["message"]
    assert body["correlation_id"]
    assert resp.headers["X-LiuHao-Correlation-Id"] == body["correlation_id"]

    events = audit_query(principal_id=account, correlation_id=body["correlation_id"])
    assert len(events) == 1
    details = events[0].get("details") or {}
    assert details.get("reason") == expected_code
    assert details.get("phase") == "pre_stream", (
        "审计要能与流中拒绝区分：建流前 = pre_stream"
    )
    assert "phase=pre_stream" in caplog.text, "日志必须标明拒绝发生在建流前"


def test_in_stream_fallback_denial_still_uses_done_frame(monkeypatch, auth_headers):
    """建流后才发生的兜底拒绝（如等锁期间额度被别处用光）继续走 done 帧。

    第一次授权是网关的建流前闸门（放行），第二次是生成器里的兜底复查（拒绝）。
    此时 HTTP 200 已经发出，改不了状态码，只能在事件流里如实说明。
    """
    account = f"acct-stream-fallback-{uuid.uuid4().hex[:8]}"
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1", audit_principal=account, provider=_OkProvider()
    )
    calls = {"count": 0}

    def authorize(*args, **kwargs):
        calls["count"] += 1
        return _AllowDecision() if calls["count"] == 1 else _BudgetDenyDecision()

    monkeypatch.setattr(assistant.policy, "authorize", authorize)
    monkeypatch.setattr(chat_module, "get_assistant", lambda *a, **k: assistant)
    monkeypatch.setattr(
        chat_module, "record_usage", lambda **kwargs: pytest.fail("拒绝不得记账")
    )

    client = TestClient(get_app())
    with client.stream(
        "POST",
        "/v1/chat/stream",
        json={"message": "hello", "session_id": "s-fallback", "use_knowledge": False},
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        events = [
            json.loads(line[len("data: "):])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]

    assert calls["count"] == 2, "兜底复查必须真的发生一次"
    done = events[-1]
    assert done["type"] == "done"
    assert done["status"] == "denied"
    assert done["reason"] == "budget_exceeded"
    assert done["usage_recorded"] is False
    assert "预算" in done["error"] and "权限" not in done["error"]

    corr = done["correlation_id"]
    audit_events = audit_query(principal_id=account, correlation_id=corr)
    assert len(audit_events) == 1
    details = audit_events[0].get("details") or {}
    assert details.get("phase") == "during_stream", (
        "兜底拒绝必须标成 during_stream，与建流前拒绝区分开"
    )
