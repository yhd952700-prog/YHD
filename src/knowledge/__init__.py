"""Knowledge package - embedding pipeline, vector store, PII detection and security policy.

Phase 2.2+ knowledge pipeline components:
- chunker: text splitting
- embedding: provider adapter service
- vector_store: in-memory prototype vector store
- pii: rule-based PII detection and knowledge security policy
- rag_pipeline: retrieval-augmented generation pipeline

Phase 2.4 Knowledge Security Policy:
- KnowledgeSecurityPolicy: access control, PII handling, redaction rules
- PIIType, PIIMatch, PIIResult: PII detection data models
- Convenience functions for PII detection and redaction
"""

from .chunker import chunk_text, chunk_by_sentences
from .embedding import EmbeddingPipeline, embed_text
from .vector_store import VectorStore
from .pii import (
    KnowledgeSecurityPolicy,
    PIIType,
    PIIMatch,
    PIIResult,
    get_knowledge_security_policy,
    set_knowledge_security_policy,
    redact_text,
    detect_pii,
)
from .rag_pipeline import RAGPipeline, rag_query, Retriever

__all__ = [
    "chunk_text",
    "chunk_by_sentences",
    "EmbeddingPipeline",
    "embed_text",
    "VectorStore",
    "KnowledgeSecurityPolicy",
    "PIIType",
    "PIIMatch",
    "PIIResult",
    "get_knowledge_security_policy",
    "set_knowledge_security_policy",
    "redact_text",
    "detect_pii",
    "RAGPipeline",
    "rag_query",
    "Retriever",
]
