"""
Knowledge Base Resource SDK.

Provides programmatic access to knowledge base management,
document upload and indexing, semantic search, and collection
management on the LiuHao AI OS platform.
"""

from typing import Optional, Dict, Any, List, IO, Union
from enum import Enum
from dataclasses import dataclass, field


class KnowledgeBaseStatus(str, Enum):
    """Lifecycle status of a knowledge base."""
    CREATING = "creating"
    READY = "ready"
    INDEXING = "indexing"
    ERROR = "error"
    DELETED = "deleted"


class DocumentStatus(str, Enum):
    """Processing status of a document in a knowledge base."""
    UPLOADED = "uploaded"
    PARSING = "parsing"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class DocumentFormat(str, Enum):
    """Supported document formats for knowledge base upload."""
    TXT = "txt"
    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"
    HTML = "html"
    CSV = "csv"
    JSON = "json"


class SearchMode(str, Enum):
    """Search mode for knowledge base queries."""
    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


@dataclass
class KnowledgeBase:
    """Represents a knowledge base.

    Attributes:
        id: Unique knowledge base identifier.
        name: Human-readable knowledge base name.
        description: Brief description of the knowledge base content.
        status: Current lifecycle status.
        document_count: Number of documents in the base.
        chunk_count: Number of text chunks indexed.
        embedding_model: Model used for vector embeddings.
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last-update timestamp.
        metadata: Arbitrary key-value metadata.
    """
    id: str
    name: str
    description: str = ""
    status: KnowledgeBaseStatus = KnowledgeBaseStatus.CREATING
    document_count: int = 0
    chunk_count: int = 0
    embedding_model: str = "default"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeDocument:
    """Represents a document within a knowledge base.

    Attributes:
        id: Unique document identifier.
        knowledge_base_id: Parent knowledge base ID.
        filename: Original filename.
        format: Document format/type.
        status: Processing status.
        size_bytes: File size in bytes.
        chunk_count: Number of chunks created from this document.
        error_message: Error details if processing failed.
        created_at: ISO 8601 upload timestamp.
        updated_at: ISO 8601 last-update timestamp.
        metadata: Arbitrary key-value metadata.
    """
    id: str
    knowledge_base_id: str
    filename: str
    format: DocumentFormat = DocumentFormat.TXT
    status: DocumentStatus = DocumentStatus.UPLOADED
    size_bytes: int = 0
    chunk_count: int = 0
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Collection:
    """Represents a named collection of documents within a knowledge base.

    Attributes:
        id: Unique collection identifier.
        knowledge_base_id: Parent knowledge base ID.
        name: Collection name.
        description: Collection description.
        document_count: Number of documents in the collection.
        created_at: ISO 8601 creation timestamp.
        metadata: Arbitrary key-value metadata.
    """
    id: str
    knowledge_base_id: str
    name: str
    description: str = ""
    document_count: int = 0
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeSearchResult:
    """Result from a knowledge base search.

    Attributes:
        chunk_id: Identifier of the matching text chunk.
        document_id: Source document identifier.
        knowledge_base_id: Knowledge base identifier.
        content: The matching text content.
        score: Relevance score.
        source: Document source reference.
        page_number: Optional page number in the source document.
    """
    chunk_id: str
    document_id: str
    knowledge_base_id: str
    content: str
    score: float
    source: str = ""
    page_number: Optional[int] = None


class KnowledgeResource:
    """Knowledge base management resource.

    Provides CRUD operations for knowledge bases, document
    upload and indexing, semantic search, and collection
    management.

    Args:
        client: The parent LiuHaoClient instance.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the KnowledgeResource.

        Args:
            client: The parent SDK client with request capabilities.
        """
        self._client = client
        self._base_path = "/knowledge"

    # ------------------------------------------------------------------
    # Knowledge base management
    # ------------------------------------------------------------------

    def list_bases(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[KnowledgeBaseStatus] = None,
    ) -> Dict[str, Any]:
        """List knowledge bases with pagination.

        Args:
            limit: Maximum number of knowledge bases to return.
            offset: Pagination offset.
            status: Optional filter by status.

        Returns:
            Dictionary with ``items`` and ``total``.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status.value
        return self._client.get(f"{self._base_path}/bases", params=params)

    def get_base(self, base_id: str) -> Dict[str, Any]:
        """Retrieve a knowledge base by ID.

        Args:
            base_id: Unique identifier of the knowledge base.

        Returns:
            Knowledge base data dictionary.
        """
        return self._client.get(f"{self._base_path}/bases/{base_id}")

    def create_base(
        self,
        name: str,
        description: str = "",
        embedding_model: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new knowledge base.

        Args:
            name: Human-readable knowledge base name.
            description: Brief description.
            embedding_model: Embedding model to use.
            metadata: Arbitrary key-value metadata.

        Returns:
            Created knowledge base data dictionary.
        """
        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "embedding_model": embedding_model,
        }
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.post(f"{self._base_path}/bases", json=payload)

    def update_base(
        self,
        base_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing knowledge base.

        Args:
            base_id: Unique identifier of the knowledge base.
            name: New name.
            description: New description.
            metadata: New metadata (replaces existing).

        Returns:
            Updated knowledge base data dictionary.
        """
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.patch(f"{self._base_path}/bases/{base_id}", json=payload)

    def delete_base(self, base_id: str) -> Dict[str, Any]:
        """Delete a knowledge base and all its contents.

        Args:
            base_id: Unique identifier of the knowledge base.

        Returns:
            Deletion confirmation dictionary.
        """
        return self._client.delete(f"{self._base_path}/bases/{base_id}")

    # ------------------------------------------------------------------
    # Document upload and indexing
    # ------------------------------------------------------------------

    def upload_document(
        self,
        base_id: str,
        filename: str,
        content: Union[str, bytes, IO],
        doc_format: Optional[DocumentFormat] = None,
        collection_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Upload a document to a knowledge base.

        Args:
            base_id: Target knowledge base identifier.
            filename: Original filename.
            content: Document content (string, bytes, or file-like object).
            doc_format: Document format. Inferred from filename if not given.
            collection_id: Optional collection to add the document to.
            metadata: Arbitrary key-value metadata.

        Returns:
            Uploaded document data dictionary.
        """
        if doc_format is None:
            # Infer from filename extension
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
            try:
                doc_format = DocumentFormat(ext)
            except ValueError:
                doc_format = DocumentFormat.TXT

        payload: Dict[str, Any] = {
            "filename": filename,
            "format": doc_format.value,
            "content": content if isinstance(content, str) else None,
        }
        if collection_id is not None:
            payload["collection_id"] = collection_id
        if metadata is not None:
            payload["metadata"] = metadata

        # For binary content, would use multipart in real implementation
        return self._client.post(
            f"{self._base_path}/bases/{base_id}/documents",
            json=payload,
        )

    def get_document(self, base_id: str, document_id: str) -> Dict[str, Any]:
        """Get details of a specific document.

        Args:
            base_id: Knowledge base identifier.
            document_id: Document identifier.

        Returns:
            Document data dictionary.
        """
        return self._client.get(
            f"{self._base_path}/bases/{base_id}/documents/{document_id}"
        )

    def list_documents(
        self,
        base_id: str,
        limit: int = 20,
        offset: int = 0,
        status: Optional[DocumentStatus] = None,
        collection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List documents in a knowledge base.

        Args:
            base_id: Knowledge base identifier.
            limit: Maximum number of documents.
            offset: Pagination offset.
            status: Optional filter by status.
            collection_id: Optional filter by collection.

        Returns:
            Dictionary with ``items`` and ``total``.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status.value
        if collection_id is not None:
            params["collection_id"] = collection_id
        return self._client.get(
            f"{self._base_path}/bases/{base_id}/documents", params=params
        )

    def delete_document(self, base_id: str, document_id: str) -> Dict[str, Any]:
        """Delete a document from a knowledge base.

        Args:
            base_id: Knowledge base identifier.
            document_id: Document identifier.

        Returns:
            Deletion confirmation dictionary.
        """
        return self._client.delete(
            f"{self._base_path}/bases/{base_id}/documents/{document_id}"
        )

    def reindex_document(self, base_id: str, document_id: str) -> Dict[str, Any]:
        """Trigger re-indexing of a document.

        Args:
            base_id: Knowledge base identifier.
            document_id: Document identifier.

        Returns:
            Re-index confirmation with status.
        """
        return self._client.post(
            f"{self._base_path}/bases/{base_id}/documents/{document_id}/reindex"
        )

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    def search(
        self,
        base_id: str,
        query: str,
        limit: int = 10,
        mode: SearchMode = SearchMode.SEMANTIC,
        threshold: float = 0.0,
        collection_id: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Search within a knowledge base.

        Args:
            base_id: Knowledge base identifier.
            query: The search query text.
            limit: Maximum number of results.
            mode: Search mode (semantic, keyword, or hybrid).
            threshold: Minimum relevance score (0.0 - 1.0).
            collection_id: Optional filter by collection.
            filters: Optional metadata filters.

        Returns:
            Dictionary with ``results`` list of search results.
        """
        payload: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "mode": mode.value,
            "threshold": threshold,
        }
        if collection_id is not None:
            payload["collection_id"] = collection_id
        if filters is not None:
            payload["filters"] = filters
        return self._client.post(
            f"{self._base_path}/bases/{base_id}/search", json=payload
        )

    def multi_base_search(
        self,
        base_ids: List[str],
        query: str,
        limit: int = 10,
        mode: SearchMode = SearchMode.SEMANTIC,
        threshold: float = 0.0,
    ) -> Dict[str, Any]:
        """Search across multiple knowledge bases.

        Args:
            base_ids: List of knowledge base identifiers.
            query: The search query text.
            limit: Maximum number of total results.
            mode: Search mode.
            threshold: Minimum relevance score.

        Returns:
            Dictionary with aggregated search results.
        """
        payload: Dict[str, Any] = {
            "base_ids": base_ids,
            "query": query,
            "limit": limit,
            "mode": mode.value,
            "threshold": threshold,
        }
        return self._client.post(f"{self._base_path}/search", json=payload)

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def list_collections(
        self,
        base_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List collections in a knowledge base.

        Args:
            base_id: Knowledge base identifier.
            limit: Maximum number of collections.
            offset: Pagination offset.

        Returns:
            Dictionary with ``items`` and ``total``.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        return self._client.get(
            f"{self._base_path}/bases/{base_id}/collections", params=params
        )

    def create_collection(
        self,
        base_id: str,
        name: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new collection within a knowledge base.

        Args:
            base_id: Parent knowledge base identifier.
            name: Collection name.
            description: Collection description.
            metadata: Arbitrary key-value metadata.

        Returns:
            Created collection data dictionary.
        """
        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
        }
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.post(
            f"{self._base_path}/bases/{base_id}/collections", json=payload
        )

    def get_collection(self, base_id: str, collection_id: str) -> Dict[str, Any]:
        """Get details of a collection.

        Args:
            base_id: Knowledge base identifier.
            collection_id: Collection identifier.

        Returns:
            Collection data dictionary.
        """
        return self._client.get(
            f"{self._base_path}/bases/{base_id}/collections/{collection_id}"
        )

    def update_collection(
        self,
        base_id: str,
        collection_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update a collection.

        Args:
            base_id: Knowledge base identifier.
            collection_id: Collection identifier.
            name: New collection name.
            description: New description.
            metadata: New metadata.

        Returns:
            Updated collection data dictionary.
        """
        payload: Dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.patch(
            f"{self._base_path}/bases/{base_id}/collections/{collection_id}",
            json=payload,
        )

    def delete_collection(self, base_id: str, collection_id: str) -> Dict[str, Any]:
        """Delete a collection (documents are not deleted, only unassigned).

        Args:
            base_id: Knowledge base identifier.
            collection_id: Collection identifier.

        Returns:
            Deletion confirmation dictionary.
        """
        return self._client.delete(
            f"{self._base_path}/bases/{base_id}/collections/{collection_id}"
        )

    def add_document_to_collection(
        self,
        base_id: str,
        collection_id: str,
        document_id: str,
    ) -> Dict[str, Any]:
        """Add a document to a collection.

        Args:
            base_id: Knowledge base identifier.
            collection_id: Collection identifier.
            document_id: Document identifier to add.

        Returns:
            Operation confirmation dictionary.
        """
        return self._client.post(
            f"{self._base_path}/bases/{base_id}/collections/{collection_id}/documents",
            json={"document_id": document_id},
        )

    def remove_document_from_collection(
        self,
        base_id: str,
        collection_id: str,
        document_id: str,
    ) -> Dict[str, Any]:
        """Remove a document from a collection.

        Args:
            base_id: Knowledge base identifier.
            collection_id: Collection identifier.
            document_id: Document identifier to remove.

        Returns:
            Operation confirmation dictionary.
        """
        return self._client.delete(
            f"{self._base_path}/bases/{base_id}/collections/{collection_id}/documents/{document_id}"
        )
