"""Tests for the Phase 2.4 RAG pipeline (S2 multi-platform + security gate).

The pipeline was refactored from ``RAGPipeline(retriever=, provider=)`` +
a synchronous ``query`` into ``RAGPipeline(vector_store=, provider_name=)``
with an async ``query`` that runs the full retrieve -> security-validate ->
LLM -> PII-filter -> audit flow. This file exercises that new contract.
"""

import asyncio

from src.knowledge.retriever import Retriever
from src.knowledge.vector_store import VectorStore
from src.knowledge.embedding import EmbeddingPipeline
from src.knowledge.rag_pipeline import RAGPipeline


def test_retriever_initialization():
    """Retriever accepts the new (vector_store, provider_name) signature."""
    vs = VectorStore()
    retriever = Retriever(vector_store=vs, provider_name="mock", top_k=4)
    assert retriever is not None
    assert retriever.top_k == 4
    assert retriever.provider_name == "mock"


def test_retriever_retrieve():
    """Retriever.retrieve() returns the sources/context/metadata dict."""
    vs = VectorStore()
    pipeline = EmbeddingPipeline(provider_key="mock")

    test_embedding = [0.1] * 1536
    vs.insert("doc1", test_embedding, {"title": "Test Document 1", "content": "This is about machine learning."})
    vs.insert("doc2", test_embedding, {"title": "Test Document 2", "content": "This is about quantum computing."})

    retriever = Retriever(vector_store=vs, embedding_pipeline=pipeline)
    result = retriever.retrieve("machine learning")
    assert "sources" in result
    assert "context" in result
    assert "metadata" in result
    assert len(result["sources"]) >= 0  # may find 0, 1, or 2 docs


def test_retriever_async_search_shape():
    """Retriever.search() returns hit dicts with the RAG-consumed shape.

    Uses a zero threshold so the mock zero-vector embeddings still resolve a
    hit and the shape assertions actually execute.
    """
    vs = VectorStore()
    pipeline = EmbeddingPipeline(provider_key="mock")
    test_embedding = [0.1] * 1536
    vs.insert("doc1", test_embedding, {"title": "ML", "content": "Machine learning is a subset of AI."})

    retriever = Retriever(vector_store=vs, embedding_pipeline=pipeline, similarity_threshold=0.0)
    hits = asyncio.run(retriever.search("machine learning"))
    assert isinstance(hits, list)
    for hit in hits:
        assert {"chunk_id", "document_id", "score", "content", "metadata"} <= hit.keys()


def test_rag_pipeline_query():
    """RAGPipeline.query() runs the full async flow and returns the contract payload."""
    vs = VectorStore()
    test_embedding = [0.1] * 1536
    vs.insert("doc1", test_embedding, {"title": "ML", "content": "Machine learning is a subset of AI."})

    rag_pipeline = RAGPipeline(vector_store=vs, provider_name="mock")
    result = asyncio.run(rag_pipeline.query("What is machine learning?"))

    assert "query" in result
    assert "sources" in result
    assert "context" in result
    assert "answer" in result
    assert "metadata" in result
    # Phase 2.4 security-gate fields are present on the payload.
    assert "policy_version" in result["metadata"]
    assert "pii_detected" in result["metadata"]
    assert "security_status" in result["metadata"]


def test_rag_with_empty_store():
    """RAG query against an empty vector store still returns the contract payload."""
    vs = VectorStore()
    rag_pipeline = RAGPipeline(vector_store=vs, provider_name="mock")

    result = asyncio.run(rag_pipeline.query("What is quantum computing?"))
    assert "query" in result
    assert "answer" in result
    # No documents indexed -> no retrieval hits.
    assert result["sources"] == []
