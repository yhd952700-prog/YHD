"""P1-12 入库队列上限回归：超过上限必须明确拒绝，不能把任务塞进无界队列。

覆盖两层：
- ``IngestJobManager`` 达到 ``max_queued`` 时抛 ``IngestQueueFull``（带 code/pending/limit）；
- HTTP 端点把该异常映射成 429 + 稳定错误码，调用方可以区分"队列满"和别的失败。
"""

from __future__ import annotations

import threading
import types

import pytest
from fastapi.testclient import TestClient

from src.knowledge.documents import KnowledgeScope
from src.knowledge.ingest_jobs import IngestJobManager, IngestQueueFull


class _BlockingService:
    """阻塞在 embedding 阶段，确保任务停在非终态，从而占住队列名额。"""

    def __init__(self, release: threading.Event) -> None:
        self.release = release

    def ingest(self, text, **kwargs):
        assert self.release.wait(10), "blocking service never released"
        return {"chunks": 1}


def _manager(release: threading.Event, max_queued: int) -> IngestJobManager:
    return IngestJobManager(
        service_factory=lambda: _BlockingService(release),
        max_workers=1,
        max_queued=max_queued,
    )


def test_p1_12_queue_limit_rejects_extra_jobs_with_clear_code():
    release = threading.Event()
    manager = _manager(release, max_queued=2)
    try:
        first = manager.submit("one")
        second = manager.submit("two")
        with pytest.raises(IngestQueueFull) as raised:
            manager.submit("three")

        assert raised.value.code == "ingest_queue_full"
        assert raised.value.pending == 2
        assert raised.value.limit == 2
        assert "队列已满" in str(raised.value)

        # 放开阻塞后，已入队的两个任务仍能正常完成（拒绝不影响已接收任务）。
        release.set()
        assert manager.wait(first["job_id"], timeout=10)["status"] == "succeeded"
        assert manager.wait(second["job_id"], timeout=10)["status"] == "succeeded"
    finally:
        release.set()
        manager.shutdown()


def test_p1_12_http_surface_maps_queue_full_to_429(monkeypatch, auth_headers):
    import src.api.routes.knowledge as knowledge_routes
    from src.gateway.main import get_app

    release = threading.Event()
    manager = _manager(release, max_queued=1)
    try:
        manager.submit("fill the queue")
        monkeypatch.setattr(
            knowledge_routes, "get_ingest_job_manager", lambda: manager
        )
        monkeypatch.setattr(
            knowledge_routes,
            "get_knowledge_service",
            lambda: types.SimpleNamespace(status=lambda: {"enabled": True}),
        )

        app = get_app()
        app.dependency_overrides[knowledge_routes._knowledge_scope] = (
            lambda: KnowledgeScope(principal="alice", owners=("alice",))
        )
        try:
            client = TestClient(app)
            resp = client.post(
                "/v1/knowledge/ingest/jobs",
                json={"text": "one more document"},
                headers=auth_headers,
            )
        finally:
            app.dependency_overrides.pop(knowledge_routes._knowledge_scope, None)

        assert resp.status_code == 429, resp.text
        # 网关的统一错误体把 HTTPException detail 摊平到顶层（code/message/pending/limit）。
        body = resp.json()
        assert body["code"] == "ingest_queue_full"
        assert body["pending"] == 1
        assert body["limit"] == 1
        assert "队列已满" in body["message"]
    finally:
        release.set()
        for job_id in list(manager._jobs):
            manager.wait(job_id, timeout=10)
        manager.shutdown()
