"""回归：``Retriever.retrieve`` 的循环变量曾经遮蔽返回的 metadata。

缺陷表现：``for doc_id, similarity, metadata in search_results`` 覆盖了上面
构造的检索 metadata，于是 ``return {"metadata": metadata}`` 返回的是**最后一条
命中**的 metadata，而不是 ``{"query", "top_k", "threshold", "results_count"}``。
调用方按契约读 ``results_count`` 时拿到的是 ``None``。
"""

from __future__ import annotations

from src.knowledge.retriever import Retriever


class _FakeEmbedding:
    def embed(self, query: str):
        return [0.1, 0.2]


class _FakeVectorStore:
    def search(self, query_embedding, top_k, threshold):
        return [
            ("doc-1", 0.9, {"title": "first hit"}),
            ("doc-2", 0.8, {"title": "second hit"}),
        ]


def test_retrieve_returns_retrieval_metadata_not_the_last_hit():
    retriever = Retriever(
        vector_store=_FakeVectorStore(),
        embedding_pipeline=_FakeEmbedding(),
        top_k=4,
        similarity_threshold=0.3,
    )
    result = retriever.retrieve("hello")

    assert result["sources"] == ["doc-1", "doc-2"]
    assert result["metadata"] == {
        "query": "hello",
        "top_k": 4,
        "threshold": 0.3,
        "results_count": 2,
    }
    # 返回结构不变：仍是 sources/context/metadata 三个键。
    assert set(result) == {"sources", "context", "metadata"}
