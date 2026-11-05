"""D 回归：内容级去重（同一内容换文件名不重复入库，并返回命中的文档）。"""

from __future__ import annotations

import sqlite3
from typing import List

from src.knowledge.documents import KnowledgeDocumentService, content_hash
from src.knowledge.vector_store import PersistentVectorStore, VectorStore


class _FakeEmbedder:
    name = "fake"
    model = "fake"

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [[1.0, float(len(text) % 31)] for text in texts]


class TestContentHash:
    def test_whitespace_and_line_endings_are_normalized(self):
        assert content_hash("甲 乙") == content_hash("甲\n乙")
        assert content_hash("甲\r\n\r\n乙") == content_hash("甲 乙")

    def test_different_text_is_different(self):
        assert content_hash("甲") != content_hash("乙")


class TestIngestDedup:
    def test_same_content_different_filename_is_not_stored_twice(self):
        service = KnowledgeDocumentService(embedder=_FakeEmbedder())
        body = "# 操作手册\n\n重启服务前先备份配置。"
        first = service.ingest(body, doc_id="manual-a", metadata={"filename": "a.md"})
        assert first["deduplicated"] is False
        count_after_first = first["vectors"]

        second = service.ingest(body, doc_id="manual-b", metadata={"filename": "b.md"})
        assert second["deduplicated"] is True
        assert second["matched_document_id"] == "manual-a"
        assert second["chunks"] == 0
        assert second["vectors"] == count_after_first

        hits = service.search("重启服务", top_k=10)["hits"]
        doc_ids = {hit["metadata"].get("document_id") for hit in hits}
        assert doc_ids == {"manual-a"}, "重复内容不得以第二个文件名再次占据检索名额"

    def test_whitespace_only_difference_still_deduplicates(self):
        service = KnowledgeDocumentService(embedder=_FakeEmbedder())
        service.ingest("第一行\n第二行", doc_id="one")
        second = service.ingest("第一行 \r\n\r\n 第二行", doc_id="two")
        assert second["deduplicated"] is True
        assert second["matched_document_id"] == "one"

    def test_different_content_is_kept(self):
        service = KnowledgeDocumentService(embedder=_FakeEmbedder())
        first = service.ingest("完全不同的内容 A。", doc_id="a")
        second = service.ingest("完全不同的内容 B。", doc_id="b")
        assert first["deduplicated"] is False
        assert second["deduplicated"] is False
        assert service._store().get("a#0") is not None
        assert service._store().get("b#0") is not None

    def test_reimporting_same_document_same_content_is_idempotent(self):
        service = KnowledgeDocumentService(embedder=_FakeEmbedder())
        body = "稳定不变的内容。" * 20
        first = service.ingest(body, doc_id="stable")
        count = first["vectors"]
        second = service.ingest(body, doc_id="stable")
        assert second["deduplicated"] is False, "同一 doc_id 允许原地刷新"
        assert second["chunks"] == first["chunks"]
        assert service._store().count() == count

    def test_editing_a_document_frees_its_previous_content_hash(self):
        service = KnowledgeDocumentService(embedder=_FakeEmbedder())
        old_body = "旧版本内容，包含唯一标记 OLD-TEXT。"
        new_body = "新版本内容，包含唯一标记 NEW-TEXT。"
        service.ingest(old_body, doc_id="doc")
        refreshed = service.ingest(new_body, doc_id="doc")
        assert refreshed["deduplicated"] is False

        # 旧内容已与 doc 解绑，另一个文档现在可以正常导入它
        reused = service.ingest(old_body, doc_id="archive")
        assert reused["deduplicated"] is False
        assert reused["matched_document_id"] is None
        assert service._store().get("archive#0") is not None
        assert service._store().get("doc#0") is not None
        assert "OLD-TEXT" in service._store().get("archive#0")[1]["text"]
        assert "NEW-TEXT" in service._store().get("doc#0")[1]["text"]


class TestInMemoryStoreDedup:
    def test_base_store_skips_duplicate_hash(self):
        store = VectorStore()
        first = store.upsert_document(
            "a#", [("a#0", [1.0], {"text": "同一份内容"})], "hash-1", "a"
        )
        second = store.upsert_document(
            "b#", [("b#0", [1.0], {"text": "同一份内容"})], "hash-1", "b"
        )
        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert second["matched_document_id"] == "a"
        assert store.count() == 1


class TestLegacyDatabaseCompatibility:
    def test_hash_table_is_created_on_existing_database(self, tmp_path):
        db_path = tmp_path / "legacy.sqlite3"
        raw = sqlite3.connect(str(db_path))
        raw.execute(
            """
            CREATE TABLE knowledge_vectors (
                doc_id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        raw.commit()
        raw.close()

        store = PersistentVectorStore(str(db_path))
        tables = {
            row[0]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }
        assert "knowledge_document_hashes" in tables
        store.close()
