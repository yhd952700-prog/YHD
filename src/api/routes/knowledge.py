"""Knowledge API routes for Phase 2.3 RAG."""

from fastapi import APIRouter, Depends

from src.knowledge.rag_pipeline import rag_query
from src.knowledge.vector_store import VectorStore
from src.knowledge.embedding import EmbeddingPipeline
from src.providers import get_provider

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _get_vector_store() -> VectorStore:
    """Get a default VectorStore instance."""
    return VectorStore()


def _get_embedding_pipeline() -> EmbeddingPipeline:
    """Get a default EmbeddingPipeline instance."""
    return EmbeddingPipeline(provider_key="mock")


def _get_provider() -> Any:
    """Get a default LLM provider instance."""
    return get_provider("mock")


@router.post("/search", response_model=dict)
async def knowledge_search(
    query: str,
    vector_store: VectorStore = Depends(_get_vector_store),
    embedding_pipeline: EmbeddingPipeline = Depends(_get_embedding_pipeline),
):
    """Perform a knowledge search query.

    Args:
        query: The user's knowledge query.

    Returns:
        Dict with sources, context, and metadata.
    """
    retriever = __import__("src.knowledge.retriever").Retriever(
        vector_store=vector_store,
        embedding_pipeline=embedding_pipeline,
    )
    result = retriever.retrieve(query)
    return result


@router.post("/query", response_model=dict)
async def knowledge_query(
    query: str,
    vector_store: VectorStore = Depends(_get_vector_store),
    embedding_pipeline: EmbeddingPipeline = Depends(_get_embedding_pipeline),
    provider: Any = Depends(_get_provider),
):
    """Run a full RAG query: retrieve context and generate answer.

    Args:
        query: The user's natural language question.

    Returns:
        Dict with query, sources, context, answer, and metadata matching
        the Phase 2.3 RAG contract.
    """
    rag = __import__("src.knowledge.rag_pipeline").RAGPipeline(
        retriever=__import__("src.knowledge.retriever").Retriever(
            vector_store=vector_store,
            embedding_pipeline=embedding_pipeline,
        ),
        provider=provider,
    )
    return rag.query(query)