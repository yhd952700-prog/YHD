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
    PIIType,
    PIIMatch,
    PIIResult,
    get_knowledge_security_policy,
    set_knowledge_security_policy,
    redact_text,
    detect_pii,
)
from .rag_pipeline import RAGPipeline
from .retriever import Retriever
from .models import (
    MessageDirection,
    MessageStatus,
    PlatformAccount,
    PlatformAccountStatus,
    PlatformContact,
    PlatformMessage,
    PlatformType,
)
from .service import PlatformService
from .translation import LANGUAGE_LIST, SUPPORTED_LANGUAGES
from .security import KnowledgeSecurityPolicy

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
    "Retriever",
    "MessageDirection",
    "MessageStatus",
    "PlatformAccount",
    "PlatformAccountStatus",
    "PlatformContact",
    "PlatformMessage",
    "PlatformType",
    "PlatformService",
    "LANGUAGE_LIST",
    "SUPPORTED_LANGUAGES",
]
