"""R10：客户端断开只记一条「用户中断」审计，且不落历史/记忆/成功记账。

背景（真实缺陷）：客户端断开后若被当成普通异常或正常完成，会分别产生
``outcome="error"`` 或 ``outcome="allow"``，并且把半截回复写进会话历史与记忆。
用户既看不到完整回答，又会在下一轮上下文里看到这段残句。

断言（逐条）：
* 审计恰好一条，``outcome == "interrupted"``，没有 ``error`` 也没有 ``allow``；
* ``details.reason == "client_disconnected"``；
* 会话历史、会话存储、记忆均未写入；
* 不按成功轮次记账（``_account_turn_spend`` 未被调用）。
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest

from src.ai.liuhao import LiuHaoAssistant
from src.ai.providers import BaseProvider, TurnCancelToken
from src.kernels.audit import audit_query


class _Allow:
    is_allowed = True


class _SlowStreamProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(name="cancel-audit", model="cancel-audit")
        self.started = threading.Event()

    def generate(self, prompt: str, **kwargs) -> str:
        return "ok"

    def chat_stream(self, messages, **kwargs):
        token = kwargs.get("cancel_token")
        for index in range(1000):
            if token is not None and token.cancelled:
                return
            self.started.set()
            yield f"tok{index}"
            time.sleep(0.01)


@pytest.mark.asyncio
async def test_client_disconnect_records_single_interrupted_audit(monkeypatch):
    account = f"acct-cancel-{uuid.uuid4().hex[:8]}"
    provider = _SlowStreamProvider()
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1", audit_principal=account, provider=provider
    )
    monkeypatch.setattr(assistant.policy, "authorize", lambda *a, **k: _Allow())

    memory_stores: list = []
    conv_appends: list = []
    spend_calls: list = []
    monkeypatch.setattr(
        assistant.memory, "store", lambda *a, **k: memory_stores.append((a, k))
    )
    monkeypatch.setattr(
        assistant._conv_store,
        "append_turn",
        lambda *a, **k: conv_appends.append((a, k)),
    )
    monkeypatch.setattr(
        assistant, "_account_turn_spend", lambda: spend_calls.append(True)
    )

    token = TurnCancelToken()

    def consume() -> None:
        with assistant.serialized_turn(cancel_token=token):
            for _ in assistant.chat_stream("你好", cancel_token=token):
                pass

    task = asyncio.create_task(asyncio.to_thread(consume))
    assert await asyncio.to_thread(provider.started.wait, 2.0), "生成应已开始"
    token.cancel()  # 客户端断开
    await asyncio.wait_for(task, timeout=10.0)

    correlation_id = assistant.last_turn_correlation_id
    assert correlation_id, "中断轮次仍必须带回 correlation_id"

    events = audit_query(principal_id=account, correlation_id=correlation_id)
    assert len(events) == 1, f"中断只能留一条审计，实际：{events}"
    event = events[0]
    assert event["outcome"] == "interrupted", event
    outcomes = {item["outcome"] for item in events}
    assert "error" not in outcomes, "中断不是失败"
    assert "allow" not in outcomes, "中断不是成功"
    details = event.get("details") or {}
    assert details.get("reason") == "client_disconnected"

    assert assistant.history == [], "中断轮次不得写入会话历史"
    assert conv_appends == [], "中断轮次不得写入会话存储"
    assert memory_stores == [], "中断轮次不得写入记忆"
    assert spend_calls == [], "中断轮次不得按成功记账"
