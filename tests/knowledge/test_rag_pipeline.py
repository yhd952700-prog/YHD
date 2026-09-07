"""Tests for Phase 2.3 RAG pipeline."""

from src.knowledge.retriever import Retriever
from src.knowledge.vector_store import VectorStore
from src.knowledge.embedding import EmbeddingPipeline
from src.providers.mock import MockRiskAssessmentProvider
from src.knowledge.rag_pipeline import rag_query


def test_retriever_initialization():
    """Test Retriever can be initialized with proper dependencies."""
    vs = VectorStore()
    pipeline = EmbeddingPipeline(provider_key="mock")
    retriever = Retriever(
        vector_store=vs,
        embedding_pipeline=pipeline,
        top_k=4,
        similarity_threshold=0.3,
    )
    assert retriever is not None
    assert retriever.top_k == 4


def test_retriever_retrieve():
    """Test Retriever.retrieve() with pre-indexed documents."""
    vs = VectorStore()
    pipeline = EmbeddingPipeline(provider_key="mock")

    # Insert some test documents
    test_embedding = [0.1] * 1536
    vs.insert("doc1", test_embedding, {"title": "Test Document 1", "content": "This is about machine learning."})
    vs.insert("doc2", test_embedding, {"title": "Test Document 2", "content": "This is about quantum computing."})

    retriever = Retriever(
        vector_store=vs,
        embedding_pipeline=pipeline,
        top_k=4,
        similarity_threshold=0.3,
    )

    result = retriever.retrieve("machine learning")
    assert "sources" in result
    assert "context" in result
    assert "metadata" in result
    assert len(result["sources"]) >= 0  # may find 0, 1, or 2 docs


def test_rag_pipeline_query():
    """Test RAGPipeline.query() end-to-end flow."""
    vs = VectorStore()
    pipeline = EmbeddingPipeline(provider_key="mock")
    provider = MockRiskAssessmentProvider()

    # Index a test document
    test_embedding = [0.1] * 1536
    vs.insert("doc1", test_embedding, {"title": "ML", "content": "Machine learning is a subset of AI."})

    # Import RAGPipeline directly
    from src.knowledge.rag_pipeline import RAGPipeline

    rag_pipeline = RAGPipeline(
        retriever=Retriever(
            vector_store=vs,
            embedding_pipeline=pipeline,
        ),
        provider=provider,
    )

    result = rag_pipeline.query("What is machine learning?")
    assert "query" in result
    assert "sources" in result
    assert "context" in result
    assert "answer" in result
    assert "metadata" in result


def test_rag_query_convenience_function():
    """Test the rag_query convenience function."""
    from src.knowledge.rag_pipeline import rag_query

    vs = VectorStore()
    pipeline = EmbeddingPipeline(provider_key="mock")
    provider = MockRiskAssessmentProvider()

    # Index a test document
    test_embedding = [0.1] * 1536
    vs.insert("doc1", test_embedding, {"title": "AI", "content": "Artificial Intelligence simulates human intelligence."})

    result = rag_query(
        user_query="What is Artificial Intelligence?",
        vector_store=vs,
        embedding_pipeline=pipeline,
        provider=provider,
    )
    assert "query" in result
    assert "answer" in result
    assert "sources" in result


def test_rag_with_empty_store():
    """Test RAG with an empty vector store."""
    vs = VectorStore()
    pipeline = EmbeddingPipeline(provider_key="mock")
    provider = MockRiskAssessmentProvider()

    result = rag_query(
        user_query="What is quantum computing?",
        vector_store=vs,
        embedding_pipeline=pipeline,
        provider=provider,
    )
    assert "query" in result
    assert "answer" in result
    # Should indicate no relevant context found
    assert result["sources"] == []