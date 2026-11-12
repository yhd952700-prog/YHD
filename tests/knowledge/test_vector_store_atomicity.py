"""M1/M2 回归：重新导入不留旧 chunk、原子替换、检索并发安全。"""

from __future__ import annotations

import sqlite3
import threading
from typing import List

import pytest

from src.knowledge.documents import KnowledgeDocumentService
from src.knowledge.vector_store import PersistentVectorStore, VectorStore

_PROVIDER_ENVS = (
    "LIUHAO_EMBEDDING_PROVIDER",
    "LIUHAO_EMBEDDING_MODEL",
    "LIUHAO_EMBEDDING_API_KEY",
    "LIUHAO_KNOWLEDGE_ALLOW_MOCK",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "LIUHAO_OLLAMA_BASE_URL",
)


@pytest.fixture
def dev_mock_env(monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "development")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    return monkeypatch


class _SlowEmbedder:
    """Deterministic non-zero vectors so hits are meaningful in assertions."""

    name = "test-embedder"
    model = "test-model"

    def embed(self, texts):
        return [[1.0, float(len(text) % 7), 0.5] for text in texts]


def _long_text() -> str:
    # 2000 字符 -> 5 个 512 字符窗口（步长 462）；尾部标记只存在于长版本。
    body = ("鎏灏知识库旧版本内容。" * 200) + "旧版本尾部秘密标记"
    return body


def _short_text() -> str:
    return "全新短版本内容。" * 60


class TestReimportDropsStaleChunks:
    def test_shorter_reimport_removes_old_tail_chunks(self, dev_mock_env):
        service = KnowledgeDocumentService(embedder=_SlowEmbedder())
        first = service.ingest(_long_text(), doc_id="manual")
        assert first["chunks"] >= 3
        old_chunks = first["chunks"]

        second = service.ingest(_short_text(), doc_id="manual")
        assert second["chunks"] < old_chunks

        hits = service.search("内容", top_k=100)["hits"]
        leaked = [hit for hit in hits if "旧版本尾部秘密标记" in hit["text"]]
        assert leaked == [], "重新导入短版本后，旧尾部 chunk 仍然可检索"

        store = service._store()
        assert store.get(f"manual#{old_chunks - 1}") is None
        assert store.get("manual#0") is not None

    def test_reimport_does_not_touch_other_documents(self, dev_mock_env):
        service = KnowledgeDocumentService(embedder=_SlowEmbedder())
        service.ingest("A 文档内容。" * 100, doc_id="a")
        service.ingest("B 文档内容。" * 100, doc_id="b")
        count_before = service._store().count()

        reimported = service.ingest("A 短版本。", doc_id="a")

        keys = _keys(service._store())
        assert service._store().count() == count_before - 2 + reimported["chunks"]
        assert all(key.startswith("b#") or key.startswith("a#") for key in keys)
        assert sum(1 for key in keys if key.startswith("b#")) == 2
        assert service._store().get("b#1") is not None


def _keys(store) -> List[str]:
    with store._lock:
        return list(store._store.keys())


class TestAtomicReplace:
    def test_persistent_replace_failure_rolls_back_db_and_memory(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        store.insert("doc#0", [1.0, 0.0], {"text": "旧内容"})
        store.insert("doc#1", [0.0, 1.0], {"text": "旧尾部"})

        bad_items = [
            ("doc#0", [2.0, 0.0], {"text": "新内容"}),
            # object 无法 JSON 序列化 -> 事务中途失败
            ("doc#1", [object()], {"text": "坏 chunk"}),
        ]
        with pytest.raises(TypeError):
            store.replace_prefix("doc#", bad_items)

        assert store.get("doc#0") == ([1.0, 0.0], {"text": "旧内容"})
        assert store.get("doc#1") == ([0.0, 1.0], {"text": "旧尾部"})

        raw = sqlite3.connect(str(tmp_path / "vectors.sqlite3"))
        try:
            rows = raw.execute(
                "SELECT doc_id, metadata FROM knowledge_vectors ORDER BY doc_id"
            ).fetchall()
        finally:
            raw.close()
        assert [row[0] for row in rows] == ["doc#0", "doc#1"]
        assert "旧内容" in rows[0][1]
        store.close()

    def test_replace_prefix_only_matches_exact_prefix(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        store.insert("a#0", [1.0], {"text": "a"})
        store.insert("ab#0", [1.0], {"text": "ab"})
        removed = store.replace_prefix("a#", [("a#0", [2.0], {"text": "new a"})])
        assert removed == 1
        assert store.get("ab#0") is not None
        assert store.get("a#0") == ([2.0], {"text": "new a"})
        store.close()


class TestFtsPrefixDelete:
    """删除一份文档，不能连带删掉"名字只差一个下划线"的另一份的词法行。"""

    @staticmethod
    def _count(store, table: str, chunk_id: str) -> int:
        column = "doc_id" if table == "knowledge_vectors" else "chunk_id"
        row = store._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (chunk_id,)
        ).fetchone()
        return int(row[0])

    def test_underscore_sibling_keeps_fts_rows_and_vectors(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        if not store._fts_available:
            pytest.skip("当前 SQLite 未编译 FTS5，词法索引不可用")

        for name, vector in (
            ("inspect_a_zone.md", [1.0, 0.0]),
            ("inspectxa_zone.md", [0.0, 1.0]),
        ):
            store.upsert_document(
                f"{name}#",
                [
                    (
                        f"{name}#0",
                        vector,
                        {"text": "同名前缀文档", "document_id": name},
                    )
                ],
                content_hash=f"hash-{name}",
                document_id=name,
            )

        assert self._count(store, "knowledge_vectors", "inspectxa_zone.md#0") == 1
        assert self._count(store, "knowledge_fts", "inspectxa_zone.md#0") == 1

        removed = store.delete_document("inspect_a_zone.md")
        assert removed["chunks_removed"] == 1

        # 被删的文档必须干净移除
        assert self._count(store, "knowledge_vectors", "inspect_a_zone.md#0") == 0
        assert self._count(store, "knowledge_fts", "inspect_a_zone.md#0") == 0
        # 关键断言：只差一个下划线的邻居，向量与词法行都原样保留
        assert self._count(store, "knowledge_vectors", "inspectxa_zone.md#0") == 1
        assert self._count(store, "knowledge_fts", "inspectxa_zone.md#0") == 1
        store.close()


class TestConcurrentSearchAndInsert:
    def test_search_survives_concurrent_inserts(self):
        store = VectorStore()
        stop = threading.Event()
        errors: List[BaseException] = []

        def writer():
            index = 0
            while not stop.is_set() and index < 5000:
                store.insert(f"doc-{index}", [1.0, float(index % 11), 0.2])
                index += 1

        def reader():
            try:
                while not stop.is_set():
                    store.search([1.0, 0.0, 0.0], top_k=5)
            except BaseException as exc:  # pragma: no cover - 失败路径
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        stop.wait(1.0)
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

        assert errors == [], f"并发检索/写入出现异常: {errors}"

    def test_replace_during_search_is_safe(self):
        store = VectorStore()
        store.insert("doc#0", [1.0], {"text": "old"})
        stop = threading.Event()
        errors: List[BaseException] = []

        def replacer():
            index = 0
            while not stop.is_set() and index < 500:
                store.replace_prefix("doc#", [
                    (f"doc#{i}", [float(i % 3)], {"text": f"v{index}-{i}"})
                    for i in range(10)
                ])
                index += 1

        def reader():
            try:
                while not stop.is_set():
                    store.search([1.0, 0.0], top_k=10)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=replacer), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        stop.wait(0.5)
        stop.set()
        for thread in threads:
            thread.join(timeout=5)

        assert errors == [], f"并发替换/检索出现异常: {errors}"
