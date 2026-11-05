"""B 回归：FTS5/BM25 侧路 + RRF 融合 + 旧库回填 + 索引同步。"""

from __future__ import annotations

import json
import sqlite3
from typing import List

from src.knowledge.documents import KnowledgeDocumentService
from src.knowledge.vector_store import (
    PersistentVectorStore,
    query_tokens,
    reciprocal_rank_fusion,
    tokenize_for_index,
)


class _ConstantEmbedder:
    """所有文本同一向量：向量路无法区分，只有关键词路能拉开差距。"""

    name = "constant"
    model = "constant"

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class TestTokenizer:
    def test_cjk_bigrams_and_ascii_words(self):
        tokens = tokenize_for_index("知识库 FTS5 型号XG-9000").split()
        assert "知识" in tokens and "识库" in tokens
        assert "fts5" in tokens
        assert "xg" in tokens and "9000" in tokens

    def test_query_tokens_are_deduped_and_capped(self):
        tokens = query_tokens("知识 知识 知识库")
        assert tokens == ["知识", "识库"]
        long_query = " ".join(f"word{index}" for index in range(50))
        assert len(query_tokens(long_query)) == 32

    def test_single_cjk_char_is_its_own_token_but_misses_bigrams(self):
        # 记录 bigram 方案的已知局限：单字查询无法命中多字词的 bigram 索引
        assert query_tokens("服") == ["服"]
        assert "服" not in tokenize_for_index("服务器重启步骤").split()


class TestRRF:
    def test_document_found_by_both_paths_ranks_first(self):
        fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
        assert [doc_id for doc_id, _ in fused][0] == "b"


class TestHybridSearch:
    def test_lexical_path_recovers_a_chunk_filtered_out_by_threshold(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        store.insert("semantic#0", [0.0, 1.0], {"text": "普通概述", "document_id": "semantic"})
        store.insert(
            "keyword#0", [1.0, 0.0], {"text": "故障单 TICKET-8842 的处理步骤", "document_id": "keyword"}
        )

        results, info = store.hybrid_search(
            "TICKET-8842", [0.0, 1.0], top_k=2, threshold=0.9
        )
        ids = [doc_id for doc_id, _, _ in results]
        assert "keyword#0" in ids, "关键词命中必须能补回向量阈值过滤掉的 chunk"
        assert info["mode"] == "hybrid"
        assert info["lexical_candidates"] >= 1
        store.close()

    def test_part_number_query_ranks_keyword_chunk_first(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        store.insert("a#0", [1.0, 0.0], {"text": "手册目录", "document_id": "a"})
        store.insert("b#0", [1.0, 0.0], {"text": "适配器型号 XG-9000 参数", "document_id": "b"})
        store.insert("c#0", [1.0, 0.0], {"text": "常见问题", "document_id": "c"})

        results, info = store.hybrid_search("XG-9000", [1.0, 0.0], top_k=3)
        assert results[0][0] == "b#0"
        assert info["mode"] == "hybrid"
        store.close()

    def test_replacing_a_document_removes_stale_fts_terms(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        store.insert("doc#0", [1.0, 0.0], {"text": "旧型号 OLD-777", "document_id": "doc"})
        old_rows = store._conn.execute(
            "SELECT chunk_id FROM knowledge_fts WHERE tokens MATCH ?", ('"old" OR "777"',)
        ).fetchall()
        assert old_rows

        store.replace_prefix(
            "doc#",
            [("doc#0", [1.0, 0.0], {"text": "新型号 NEW-888", "document_id": "doc"})],
        )
        remaining = store._conn.execute(
            "SELECT chunk_id FROM knowledge_fts WHERE tokens MATCH ?", ('"old" OR "777"',)
        ).fetchall()
        assert remaining == []
        results, _ = store.hybrid_search("OLD-777", [1.0, 0.0], top_k=5)
        assert [doc_id for doc_id, _, _ in results] == ["doc#0"]
        assert "OLD-777" not in results[0][2]["text"]
        store.close()

    def test_delete_removes_fts_row(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        store.insert("gone#0", [1.0, 0.0], {"text": "唯一关键词 ZETA-1", "document_id": "gone"})
        assert store.delete("gone#0") is True
        rows = store._conn.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()
        assert rows[0] == 0
        store.close()


class TestLegacyCorpusBackfill:
    def test_existing_db_is_indexed_without_rebuild_script(self, tmp_path):
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
        # 批次 2 之前的行：metadata 只有 text，没有 context_header
        raw.execute(
            "INSERT INTO knowledge_vectors (doc_id, embedding, metadata) VALUES (?, ?, ?)",
            ("old#0", json.dumps([1.0, 0.0]), json.dumps({"text": "设备型号 LEGACY-42"}, ensure_ascii=False)),
        )
        raw.commit()
        raw.close()

        store = PersistentVectorStore(str(db_path))
        assert store._fts_available is True
        results, info = store.hybrid_search("LEGACY-42", [1.0, 0.0], top_k=5)
        assert [doc_id for doc_id, _, _ in results] == ["old#0"]
        assert info["lexical_candidates"] >= 1
        store.close()


class TestServiceSearchExposesRetrievalMode:
    def test_search_reports_hybrid_and_keyword_hit(self):
        service = KnowledgeDocumentService(embedder=_ConstantEmbedder())
        service.ingest("适配器型号 XG-9000 的技术参数。", doc_id="adapter")
        service.ingest("完全无关的目录内容。", doc_id="toc")

        result = service.search("XG-9000", top_k=2)
        assert result["retrieval"]["mode"] == "hybrid"
        assert result["retrieval"]["fts_available"] is True
        assert "XG-9000" in result["hits"][0]["text"]

    def test_single_char_query_cannot_use_bigram_index(self):
        service = KnowledgeDocumentService(embedder=_ConstantEmbedder())
        service.ingest("服务器重启步骤。", doc_id="server")
        # 已知局限：单字查询 token 为单字，多字词只索引 bigram -> 无关键词候选
        result = service.search("服", top_k=2)
        assert result["retrieval"]["mode"] == "vector"
        assert result["retrieval"]["lexical_candidates"] == 0
