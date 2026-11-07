"""文档管理补口：列表 / 完整分块 / 事务删除 / 上传 / 异步入库任务。"""

from __future__ import annotations

import io
import threading
import time
from typing import List

import pytest
from fastapi.testclient import TestClient

from src.knowledge.documents import KnowledgeDocumentService
from src.knowledge.ingest_jobs import IngestJobManager, reset_ingest_job_manager

_PROVIDER_ENVS = (
    "LIUHAO_EMBEDDING_PROVIDER",
    "LIUHAO_EMBEDDING_MODEL",
    "LIUHAO_EMBEDDING_API_KEY",
    "LIUHAO_KNOWLEDGE_ALLOW_MOCK",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "LIUHAO_OLLAMA_BASE_URL",
    "LIUHAO_ENV",
    "APP_ENV",
)


class _FakeEmbedder:
    name = "fake"
    model = "fake"

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [[1.0, float(len(text) % 31)] for text in texts]


class _SlowEmbedder:
    name = "slow"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def embed(self, texts: List[str]) -> List[List[float]]:
        self.started.set()
        self.release.wait(5)
        return [[1.0, 0.0] for _ in texts]


def _service(embedder=None) -> KnowledgeDocumentService:
    return KnowledgeDocumentService(embedder=embedder or _FakeEmbedder())


_LONG_BODY = (
    "# 第一节\n\n" + "段落甲。" * 140 + "\n\n"
    "## 第二节\n\n" + "段落乙。" * 140 + "\n"
)


def _pdf(pages: List[str]) -> bytes:
    """A real PDF with a text layer on every page (pypdf writer, not byte soup)."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        content = DecodedStreamObject()
        content.set_data(
            ("BT /F1 24 Tf 72 720 Td (%s) Tj ET" % text).encode("latin-1")
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _reset_job_worker():
    yield
    reset_ingest_job_manager()


class TestDocumentListing:
    def test_list_reports_chunks_time_source_and_hash(self):
        service = _service()
        service.ingest(
            "# 手册\n\n局域网访问驾驶舱。",
            doc_id="doc-a",
            metadata={"source": "manual.md"},
        )
        result = service.list_documents()
        assert result["total"] == 1
        document = result["documents"][0]
        assert document["document_id"] == "doc-a"
        assert document["chunks"] >= 1
        assert document["source"] == "manual.md"
        assert document["content_hash"]
        assert document["ingested_at"]

    def test_list_supports_pagination(self):
        service = _service()
        for name in ("a", "b", "c"):
            service.ingest(f"文档 {name} 的唯一内容。", doc_id=f"doc-{name}")
        first = service.list_documents(limit=2, offset=0)
        second = service.list_documents(limit=2, offset=2)
        assert first["total"] == second["total"] == 3
        assert len(first["documents"]) == 2
        assert len(second["documents"]) == 1
        ids = {d["document_id"] for d in first["documents"] + second["documents"]}
        assert ids == {"doc-a", "doc-b", "doc-c"}


class TestDocumentChunks:
    def test_returns_every_chunk_in_order_with_heading_path(self):
        service = _service()
        ingested = service.ingest(_LONG_BODY, doc_id="long-doc")
        result = service.document_chunks("long-doc")
        assert result["count"] == ingested["chunks"]
        assert result["count"] >= 2, "长文档应被切成多个分块"
        indexes = [chunk["chunk_index"] for chunk in result["chunks"]]
        assert indexes == list(range(len(indexes)))
        assert any(chunk["heading_path"] for chunk in result["chunks"])
        assert any(
            "第二节" in "/".join(chunk["heading_path"])
            for chunk in result["chunks"]
        )
        assert all(chunk["text"] for chunk in result["chunks"])

    def test_unknown_document_has_no_chunks(self):
        assert _service().document_chunks("missing")["count"] == 0


class TestDocumentDeletion:
    def test_delete_removes_vectors_fts_and_content_hash(self):
        service = _service()
        service.ingest("唯一标记 DELETABLE 的文档。", doc_id="doc-a")
        store = service._store()

        before = service.search("DELETABLE", top_k=5)["hits"]
        assert before

        outcome = service.delete_document("doc-a")
        assert outcome["deleted"] is True
        assert outcome["chunks_removed"] >= 1

        assert service.document_chunks("doc-a")["count"] == 0
        assert service.list_documents()["total"] == 0
        assert service.search("DELETABLE", top_k=5)["hits"] == []
        assert store._conn.execute(
            "SELECT COUNT(*) FROM knowledge_document_hashes "
            "WHERE document_id = ?",
            ("doc-a",),
        ).fetchone()[0] == 0
        if store._fts_available:
            assert store._conn.execute(
                "SELECT COUNT(*) FROM knowledge_fts "
                "WHERE chunk_id LIKE 'doc-a#%'"
            ).fetchone()[0] == 0

    def test_deleting_frees_the_content_hash_for_reuse(self):
        service = _service()
        body = "可被复用的内容 COPYABLE。"
        service.ingest(body, doc_id="doc-a")
        service.delete_document("doc-a")
        again = service.ingest(body, doc_id="doc-b")
        assert again["deduplicated"] is False

    def test_deleting_a_missing_document_reports_not_deleted(self):
        assert _service().delete_document("missing")["deleted"] is False


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch


@pytest.fixture
def dev_mock_env(clean_env):
    clean_env.setenv("LIUHAO_ENV", "development")
    clean_env.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    yield clean_env


@pytest.fixture
def client(dev_mock_env, monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    with TestClient(__import__("src.gateway.main", fromlist=["get_app"]).get_app()) as c:
        yield c


def _headers(scope: str = "L0"):
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject="kb-human", metadata={"kind": "human", "scope": scope}
    )
    return {"Authorization": f"Bearer {token}"}


class TestAdminHttpAuth:
    def test_new_endpoints_require_auth(self, client):
        assert client.get("/v1/knowledge/documents").status_code == 401
        assert (
            client.get("/v1/knowledge/documents/doc/chunks").status_code == 401
        )
        assert (
            client.delete("/v1/knowledge/documents/doc").status_code == 401
        )
        assert (
            client.post(
                "/v1/knowledge/ingest/jobs", json={"text": "x"}
            ).status_code
            == 401
        )

    def test_delete_is_scope_checked_not_admin_gated(self, client):
        """删除不再要求管理员声明：按归属判定，越权与不存在同形中性 404。

        旧实现先过 ``require_admin_principal``，成员删自己的文档也会收到带
        "管理员权限/L0"层级词的 403；本用例锁定新契约：普通令牌能删自己的
        文档，删别人的/不存在的都是中性 404。
        """
        headers = _headers(scope="L1")
        assert client.get("/v1/knowledge/documents", headers=headers).status_code == 200

        missing = client.delete(
            "/v1/knowledge/documents/whatever", headers=headers
        )
        assert missing.status_code == 404, missing.text
        detail = missing.json().get("detail")
        message = detail.get("message") if isinstance(detail, dict) else detail
        assert message == "资源不存在或无权访问", missing.text
        for word in ("管理员", "L0", "权限不足", "admin"):
            assert word not in missing.text

        ingested = client.post(
            "/v1/knowledge/documents",
            json={"text": _LONG_BODY, "doc_id": "member-own-doc"},
            headers=headers,
        )
        assert ingested.status_code == 200, ingested.text
        removed = client.delete(
            "/v1/knowledge/documents/member-own-doc", headers=headers
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["deleted"] is True


class TestAdminHttpFlow:
    def test_list_chunks_and_delete_end_to_end(self, client):
        headers = _headers()
        ingested = client.post(
            "/v1/knowledge/documents",
            json={"text": _LONG_BODY, "doc_id": "flow-doc"},
            headers=headers,
        )
        assert ingested.status_code == 200, ingested.text

        listed = client.get(
            "/v1/knowledge/documents", params={"limit": 10, "offset": 0},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["total"] == 1
        assert body["documents"][0]["document_id"] == "flow-doc"

        chunks = client.get(
            "/v1/knowledge/documents/flow-doc/chunks", headers=headers
        )
        assert chunks.status_code == 200, chunks.text
        assert chunks.json()["count"] >= 2

        removed = client.delete(
            "/v1/knowledge/documents/flow-doc", headers=headers
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["deleted"] is True

        missing = client.get(
            "/v1/knowledge/documents/flow-doc/chunks", headers=headers
        )
        assert missing.status_code == 404
        assert (
            client.get("/v1/knowledge/documents", headers=headers).json()["total"]
            == 0
        )

    def test_upload_txt_and_md_ingests_with_source(self, client):
        headers = _headers()
        for filename in ("handbook.txt", "notes.md"):
            uploaded = client.post(
                "/v1/knowledge/documents/upload",
                files={
                    "file": (
                        filename,
                        f"{filename}\n\n局域网访问。",
                        "text/plain",
                    )
                },
                headers=headers,
            )
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()["source"] == filename

        listed = client.get("/v1/knowledge/documents", headers=headers).json()
        sources = {doc["source"] for doc in listed["documents"]}
        assert sources == {"handbook.txt", "notes.md"}

    def test_upload_reports_corrupt_pdf_instead_of_calling_it_unsupported(self, client):
        response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("manual.pdf", b"%PDF-1.4", "application/pdf")},
            headers=_headers(),
        )
        # PDF is now a supported type: a truncated file is a *corrupt* PDF,
        # not an unsupported format. Detailed scanned/encrypted coverage lives
        # in tests/knowledge/test_document_import.py.
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert "损坏" in detail

    def test_upload_rejects_binary_and_empty_files(self, client):
        headers = _headers()
        binary = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("bad.txt", b"\xff\xfe\x00", "text/plain")},
            headers=headers,
        )
        assert binary.status_code == 400
        assert "UTF-8" in binary.json()["detail"]

        empty = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
            headers=headers,
        )
        assert empty.status_code == 400


class TestAsyncIngestJobs:
    def test_job_reports_progress_and_persists_document(self, client):
        headers = _headers()
        submitted = client.post(
            "/v1/knowledge/ingest/jobs",
            json={"text": _LONG_BODY, "doc_id": "async-doc"},
            headers=headers,
        )
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["job_id"]

        from src.knowledge.ingest_jobs import get_ingest_job_manager

        finished = get_ingest_job_manager().wait(job_id, timeout=10)
        assert finished["status"] == "succeeded", finished
        assert finished["progress_percent"] == 100.0

        fetched = client.get(
            f"/v1/knowledge/ingest/jobs/{job_id}", headers=headers
        )
        assert fetched.status_code == 200
        assert fetched.json()["document_id"] == "async-doc"
        assert (
            client.get("/v1/knowledge/documents", headers=headers).json()["total"]
            == 1
        )

    def test_upload_job_endpoint_accepts_a_file_and_reports_pages(self, client):
        headers = _headers()
        submitted = client.post(
            "/v1/knowledge/documents/upload/jobs",
            files={
                "file": (
                    "manual.pdf",
                    _pdf(["Page one text.", "Page two text."]),
                    "application/pdf",
                )
            },
            headers=headers,
        )
        assert submitted.status_code == 202, submitted.text
        queued = submitted.json()
        assert queued["source"] == "file"
        assert queued["file_name"] == "manual.pdf"

        from src.knowledge.ingest_jobs import get_ingest_job_manager

        finished = get_ingest_job_manager().wait(queued["job_id"], timeout=10)
        assert finished["status"] == "succeeded", finished
        assert finished["total_pages"] == 2
        assert finished["extracted_pages"] == 2
        assert finished["embedded_pages"] == 2
        assert finished["progress_percent"] == 100.0
        assert finished["result"]["extraction"]["page_count"] == 2

        chunks = client.get(
            f"/v1/knowledge/documents/{finished['document_id']}/chunks",
            headers=headers,
        ).json()["chunks"]
        assert {chunk["page"] for chunk in chunks} == {1, 2}


class TestFileJobProgressAndCancellation:
    def test_progress_is_counted_in_real_pages_not_a_timer(self):
        slow = _SlowEmbedder()
        service = _service(slow)
        manager = IngestJobManager(service_factory=lambda: service)
        try:
            submitted = manager.submit_file(
                _pdf(["Page one text.", "Page two text.", "Page three text."]),
                "manual.pdf",
            )
            assert slow.started.wait(5), "worker 未开始嵌入"
            running = manager.get(submitted["job_id"])
            assert running["status"] == "running"
            assert running["phase"] == "embedding"
            # Extraction really parsed all 3 pages; embedding has done none yet.
            assert running["total_pages"] == 3
            assert running["extracted_pages"] == 3
            assert running["embedded_pages"] == 0
            assert running["progress_percent"] == 50.0

            slow.release.set()
            finished = manager.wait(submitted["job_id"], timeout=10)
            assert finished["status"] == "succeeded", finished
            assert finished["total_pages"] == 3
            assert finished["embedded_pages"] == 3
            assert finished["progress_percent"] == 100.0
            assert service.document_chunks("manual.pdf")["count"] == 3
        finally:
            manager.shutdown()

    def test_cancelling_a_file_job_is_a_full_rollback(self):
        slow = _SlowEmbedder()
        service = _service(slow)
        manager = IngestJobManager(service_factory=lambda: service)
        try:
            submitted = manager.submit_file(
                _pdf(["Page one text.", "Page two text.", "Page three text."]),
                "cancel-manual.pdf",
            )
            assert slow.started.wait(5), "worker 未开始嵌入"
            accepted = manager.cancel(submitted["job_id"])
            assert accepted["cancel_accepted"] is True
            slow.release.set()

            finished = manager.wait(submitted["job_id"], timeout=10)
            assert finished["status"] == "cancelled", finished
            assert finished["result"]["committed_chunks"] == 0
            assert "全回滚" in finished["note"]
            assert finished["extracted_pages"] == 3

            assert service.document_chunks("cancel-manual.pdf")["count"] == 0
            assert service.list_documents()["total"] == 0
            store = service._store()
            assert store._conn.execute(
                "SELECT COUNT(*) FROM knowledge_document_hashes "
                "WHERE document_id = ?",
                ("cancel-manual.pdf",),
            ).fetchone()[0] == 0
        finally:
            manager.shutdown()

    def test_garbled_file_job_fails_with_quality_and_can_be_resubmitted(self):
        service = _service()
        manager = IngestJobManager(service_factory=lambda: service)
        garbled = ("正常文本。" * 5 + "\ufffd" * 40).encode("utf-8")
        try:
            refused = manager.submit_file(garbled, "broken.txt")
            finished = manager.wait(refused["job_id"], timeout=10)
            assert finished["status"] == "failed", finished
            assert finished["error_code"] == "low_quality_extraction"
            assert finished["quality"]["low_quality"] is True
            assert "allow_low_quality=true" in finished["note"]
            assert service.list_documents()["total"] == 0

            allowed = manager.submit_file(
                garbled, "broken.txt", allow_low_quality=True
            )
            retried = manager.wait(allowed["job_id"], timeout=10)
            assert retried["status"] == "succeeded", retried
            assert retried["quality"]["low_quality"] is True
            assert service.list_documents()["total"] == 1
        finally:
            manager.shutdown()


class TestJobCancellation:
    def test_cancel_lands_at_the_next_chunk_boundary_and_writes_nothing(self):
        slow = _SlowEmbedder()
        service = _service(slow)
        manager = IngestJobManager(service_factory=lambda: service)
        try:
            job = manager.submit(_LONG_BODY, doc_id="cancel-doc")
            assert slow.started.wait(5), "worker 未开始嵌入"
            accepted = manager.cancel(job["job_id"])
            assert accepted["cancel_accepted"] is True
            slow.release.set()

            finished = manager.wait(job["job_id"], timeout=10)
            assert finished["status"] == "cancelled", finished
            assert finished["result"]["committed_chunks"] == 0
            assert "全回滚" in finished["note"]

            assert service.document_chunks("cancel-doc")["count"] == 0
            assert service.list_documents()["total"] == 0
            store = service._store()
            assert store._conn.execute(
                "SELECT COUNT(*) FROM knowledge_document_hashes "
                "WHERE document_id = ?",
                ("cancel-doc",),
            ).fetchone()[0] == 0
        finally:
            manager.shutdown()

    def test_cancel_after_completion_is_reported_as_not_accepted(self):
        manager = IngestJobManager(service_factory=lambda: _service())
        try:
            job = manager.submit("短文档内容。", doc_id="done-doc")
            finished = manager.wait(job["job_id"], timeout=10)
            assert finished["status"] == "succeeded"
            late = manager.cancel(job["job_id"])
            assert late["cancel_accepted"] is False
        finally:
            manager.shutdown()

    def test_finished_job_is_retained_for_inspection(self):
        manager = IngestJobManager(service_factory=lambda: _service())
        try:
            job = manager.submit("短文档内容。", doc_id="kept-doc")
            manager.wait(job["job_id"], timeout=10)
            time.sleep(0.05)
            assert manager.get(job["job_id"])["status"] == "succeeded"
        finally:
            manager.shutdown()
