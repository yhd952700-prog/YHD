"""R9：客户端断开后必须尽快释放 ``serialized_turn`` 的会话锁。

背景（真实缺陷）：``StreamingResponse`` 在客户端断开时取消消费任务，但同步
生成器默认由 ``iterate_in_threadpool`` 消费；anyio 的取消会等待阻塞在 provider
读取上的线程返回。结果是"客户端已经走了，锁还被占着"，下一条请求只能干等。

修复后网关用「生产者线程 + 队列」桥接：消费端取消时立刻置位
``TurnCancelToken``，生产端在检查点收口并归还锁。本用例断言：
* 断开（取消消费任务）后，下一条请求能立刻拿到同一把会话锁；
* 生成线程真的结束了，而不是被丢在半路。
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest

from src.ai.liuhao import LiuHaoAssistant
from src.ai.providers import BaseProvider, TurnCancelToken
from src.gateway.chat import _stream_with_cancel


class _Allow:
    is_allowed = True


class _SlowStreamProvider(BaseProvider):
    """持续缓慢产出 token，直到取消令牌置位。"""

    def __init__(self) -> None:
        super().__init__(name="slow-stream", model="slow-model")
        self.started = threading.Event()
        self.finished = threading.Event()

    def generate(self, prompt: str, **kwargs) -> str:
        return "ok"

    def chat_stream(self, messages, **kwargs):
        token = kwargs.get("cancel_token")
        try:
            for index in range(1000):
                if token is not None and token.cancelled:
                    return
                self.started.set()
                yield f"tok{index}"
                time.sleep(0.01)
        finally:
            self.finished.set()


@pytest.mark.asyncio
async def test_disconnect_releases_turn_lock_immediately(monkeypatch):
    account = f"acct-lock-{uuid.uuid4().hex[:8]}"
    provider = _SlowStreamProvider()
    assistant = LiuHaoAssistant(
        name=f"liuhao-{account}-s1", audit_principal=account, provider=provider
    )
    monkeypatch.setattr(assistant.policy, "authorize", lambda *a, **k: _Allow())

    token = TurnCancelToken()

    def source():
        with assistant.serialized_turn(cancel_token=token):
            yield from assistant.chat_stream("你好", cancel_token=token)

    async def consume():
        async for _ in _stream_with_cancel(source(), token):
            pass

    task = asyncio.create_task(consume())
    assert await asyncio.to_thread(provider.started.wait, 2.0), "生成应已开始"
    await asyncio.sleep(0.05)

    # 模拟客户端断开：取消消费任务（Starlette 收到 http.disconnect 时也是这样）。
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 下一条请求必须能立刻拿到锁；这里给 2 秒上限，只为排除"永远拿不到"。
    def acquire() -> float:
        started = time.monotonic()
        with assistant.serialized_turn():
            pass
        return time.monotonic() - started

    waited = await asyncio.wait_for(asyncio.to_thread(acquire), timeout=2.0)
    assert waited < 2.0
    assert await asyncio.to_thread(provider.finished.wait, 2.0), "生成线程应收口退出"
