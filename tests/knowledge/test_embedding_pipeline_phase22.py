"""Tests for Phase 2.2 embedding pipeline."""

from src.knowledge.chunker import chunk_text, chunk_by_sentences
from src.knowledge.embedding import EmbeddingPipeline, embed_text
from src.knowledge.vector_store import VectorStore


def test_chunk_text():
    """Test basic text chunking."""
    text = "This is a test sentence. " * 100
    chunks = chunk_text(text, chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 0
    assert all(isinstance(c, str) for c in chunks)


def test_chunk_by_sentences():
    """Test sentence-based chunking."""
    text = "Hello world. This is a test. Another sentence here."
    chunks = chunk_by_sentences(text, chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 0


def test_embedding_pipeline():
    """Test embedding pipeline with mock provider."""
    pipeline = EmbeddingPipeline(provider_key="mock")
    text = "Hello, world! This is a test document."
    
    # Test chunking
    chunks = pipeline.chunk(text)
    assert len(chunks) > 0
    
    # Test embedding
    emb = pipeline.embed(text)
    assert len(emb) == 1536  # default dimension
    assert all(v == 0.0 for v in emb)  # mock returns zeros
    
    # Test embed_documents
    docs = ["Doc one.", "Doc two."]
    embs = pipeline.embed_documents(docs)
    assert len(embs) == 2
    assert all(len(e) == 1536 for e in embs)


def test_embed_text():
    """Test convenience function."""
    emb = embed_text("Test text", provider_key="mock")
    assert len(emb) == 1536
    assert all(v == 0.0 for v in emb)


def test_vector_store():
    """Test vector store operations."""
    vs = VectorStore()
    
    # Test insert and get
    emb = [0.1] * 1536
    vs.insert("doc1", emb, {"title": "Test Doc 1"})
    retrieved = vs.get("doc1")
    assert retrieved is not None
    assert retrieved[0] == emb
    assert retrieved[1]["title"] == "Test Doc 1"
    
    # Test search
    results = vs.search([0.1] * 1536, top_k=1)
    assert len(results) >= 1
    assert results[0][0] == "doc1"
    assert results[0][1] >= 1.0  # identical vector, similarity = 1
    
    # Test count
    assert vs.count() == 1
    
    # Test delete
    deleted = vs.delete("doc1")
    assert deleted is True
    assert vs.count() == 0
    assert vs.get("doc1") is None
    
    # Test search after delete
    results = vs.search([0.1] * 1536, top_k=1)
    assert len(results) == 0


def test_vector_store_empty():
    """Test vector store with no documents."""
    vs = VectorStore()
    assert vs.count() == 0
    assert vs.search([0.1] * 1536, top_k=1) == []
    assert vs.get("nonexistent") is None
    deleted = vs.delete("nonexistent")
    assert deleted is False


def test_chunk_by_sentences_empty():
    """Test sentence chunking with empty text."""
    chunks = chunk_by_sentences("", chunk_size=50, chunk_overlap=10)
    assert chunks == []


def test_chunk_text_empty():
    """Test text chunking with empty text."""
    chunks = chunk_text("", chunk_size=50, chunk_overlap=10)
    assert chunks == []