"""
Memory Resource SDK.

Provides programmatic access to the LiuHao AI OS memory system,
supporting storage, retrieval, semantic search, and management
of memory entries.
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field


class MemoryCategory(str, Enum):
    """Category classification for memory entries.

    Categories help organize and filter memories by their nature
    and intended use.
    """
    GENERAL = "general"
    FACT = "fact"
    PREFERENCE = "preference"
    CONVERSATION = "conversation"
    TASK = "task"
    KNOWLEDGE = "knowledge"
    USER_PROFILE = "user_profile"
    CONTEXT = "context"


class MemoryImportance(str, Enum):
    """Importance level for memory entries.

    Used for retrieval prioritization and memory pruning strategies.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class MemoryEntry:
    """Represents a single memory entry.

    Attributes:
        id: Unique memory identifier.
        content: The actual memory content/text.
        category: Memory category classification.
        importance: Importance level.
        tags: List of tags for categorization and search.
        source: Origin of the memory (e.g., "user_input", "agent_observation").
        entity_id: Optional associated entity (user, agent, task, etc.).
        metadata: Arbitrary key-value metadata.
        embedding: Optional embedding vector (not always returned).
        created_at: ISO 8601 creation timestamp.
        updated_at: ISO 8601 last-update timestamp.
        access_count: Number of times this memory has been accessed.
        last_accessed_at: ISO 8601 last access timestamp.
    """
    id: str
    content: str
    category: MemoryCategory = MemoryCategory.GENERAL
    importance: MemoryImportance = MemoryImportance.MEDIUM
    tags: List[str] = field(default_factory=list)
    source: str = ""
    entity_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    access_count: int = 0
    last_accessed_at: Optional[str] = None


@dataclass
class MemorySearchResult:
    """Result from a memory search query.

    Attributes:
        entry: The matched memory entry.
        score: Relevance score (higher = more relevant).
        match_type: Type of match (semantic, keyword, hybrid).
    """
    entry: MemoryEntry
    score: float
    match_type: str = "semantic"


class MemoryResource:
    """Memory operations resource.

    Provides storage, retrieval, search, and management of memory
    entries on the LiuHao AI OS platform.

    Args:
        client: The parent LiuHaoClient instance.
    """

    def __init__(self, client: Any) -> None:
        """Initialize the MemoryResource.

        Args:
            client: The parent SDK client with request capabilities.
        """
        self._client = client
        self._base_path = "/memory"

    # ------------------------------------------------------------------
    # Basic memory operations
    # ------------------------------------------------------------------

    def store(
        self,
        content: str,
        category: MemoryCategory = MemoryCategory.GENERAL,
        importance: MemoryImportance = MemoryImportance.MEDIUM,
        tags: Optional[List[str]] = None,
        source: str = "",
        entity_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store a new memory entry.

        Args:
            content: The memory content to store.
            category: Memory category classification.
            importance: Importance level for prioritization.
            tags: List of tags for categorization.
            source: Origin of the memory.
            entity_id: Optional associated entity ID.
            metadata: Arbitrary key-value metadata.

        Returns:
            Created memory entry data dictionary.
        """
        payload: Dict[str, Any] = {
            "content": content,
            "category": category.value,
            "importance": importance.value,
            "source": source,
        }
        if tags is not None:
            payload["tags"] = tags
        if entity_id is not None:
            payload["entity_id"] = entity_id
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.post(f"{self._base_path}", json=payload)

    def retrieve(self, memory_id: str) -> Dict[str, Any]:
        """Retrieve a single memory entry by ID.

        Args:
            memory_id: Unique identifier of the memory entry.

        Returns:
            Memory entry data dictionary.

        Raises:
            NotFoundError: If no memory entry with the given ID exists.
        """
        return self._client.get(f"{self._base_path}/{memory_id}")

    def update(
        self,
        memory_id: str,
        content: Optional[str] = None,
        category: Optional[MemoryCategory] = None,
        importance: Optional[MemoryImportance] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory entry.

        Only provided fields will be updated.

        Args:
            memory_id: Unique identifier of the memory entry.
            content: New memory content.
            category: New category classification.
            importance: New importance level.
            tags: New tag list (replaces existing).
            metadata: New metadata (replaces existing).

        Returns:
            Updated memory entry data dictionary.
        """
        payload: Dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if category is not None:
            payload["category"] = category.value
        if importance is not None:
            payload["importance"] = importance.value
        if tags is not None:
            payload["tags"] = tags
        if metadata is not None:
            payload["metadata"] = metadata
        return self._client.patch(f"{self._base_path}/{memory_id}", json=payload)

    def delete(self, memory_id: str) -> Dict[str, Any]:
        """Delete a memory entry.

        Args:
            memory_id: Unique identifier of the memory entry.

        Returns:
            Deletion confirmation dictionary.
        """
        return self._client.delete(f"{self._base_path}/{memory_id}")

    def list(
        self,
        limit: int = 20,
        offset: int = 0,
        category: Optional[MemoryCategory] = None,
        importance: Optional[MemoryImportance] = None,
        entity_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List memory entries with filtering.

        Args:
            limit: Maximum number of entries to return.
            offset: Pagination offset.
            category: Optional filter by category.
            importance: Optional filter by importance level.
            entity_id: Optional filter by associated entity.
            tags: Optional filter by tags (match any).

        Returns:
            Dictionary with ``items`` and ``total``.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if category is not None:
            params["category"] = category.value
        if importance is not None:
            params["importance"] = importance.value
        if entity_id is not None:
            params["entity_id"] = entity_id
        if tags is not None:
            params["tags"] = ",".join(tags)
        return self._client.get(f"{self._base_path}", params=params)

    # ------------------------------------------------------------------
    # Semantic search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 10,
        category: Optional[MemoryCategory] = None,
        entity_id: Optional[str] = None,
        threshold: float = 0.0,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Perform semantic search across memory entries.

        Finds memories that are semantically similar to the query.

        Args:
            query: The search query text.
            limit: Maximum number of results to return.
            category: Optional filter by category.
            entity_id: Optional filter by associated entity.
            threshold: Minimum similarity score threshold (0.0 - 1.0).
            tags: Optional filter by tags.

        Returns:
            Dictionary with ``results`` list containing memory entries
            and their similarity scores.
        """
        payload: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "threshold": threshold,
        }
        if category is not None:
            payload["category"] = category.value
        if entity_id is not None:
            payload["entity_id"] = entity_id
        if tags is not None:
            payload["tags"] = tags
        return self._client.post(f"{self._base_path}/search", json=payload)

    def hybrid_search(
        self,
        query: str,
        limit: int = 10,
        keyword_weight: float = 0.3,
        semantic_weight: float = 0.7,
        category: Optional[MemoryCategory] = None,
        entity_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Perform hybrid (keyword + semantic) search.

        Combines keyword matching and semantic similarity for
        more comprehensive search results.

        Args:
            query: The search query text.
            limit: Maximum number of results.
            keyword_weight: Weight for keyword matching component (0-1).
            semantic_weight: Weight for semantic similarity component (0-1).
            category: Optional filter by category.
            entity_id: Optional filter by entity.

        Returns:
            Dictionary with ranked search results.
        """
        payload: Dict[str, Any] = {
            "query": query,
            "limit": limit,
            "keyword_weight": keyword_weight,
            "semantic_weight": semantic_weight,
        }
        if category is not None:
            payload["category"] = category.value
        if entity_id is not None:
            payload["entity_id"] = entity_id
        return self._client.post(f"{self._base_path}/search/hybrid", json=payload)

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def bulk_store(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Store multiple memory entries in one request.

        Args:
            entries: List of memory entry dictionaries, each containing
                at least ``content`` and optionally ``category``,
                ``importance``, ``tags``, ``source``, ``metadata``.

        Returns:
            Dictionary with ``created_count`` and ``items``.
        """
        return self._client.post(f"{self._base_path}/bulk", json={"entries": entries})

    def bulk_delete(self, memory_ids: List[str]) -> Dict[str, Any]:
        """Delete multiple memory entries.

        Args:
            memory_ids: List of memory entry identifiers to delete.

        Returns:
            Dictionary with ``deleted_count``.
        """
        return self._client.post(
            f"{self._base_path}/bulk/delete", json={"ids": memory_ids}
        )

    # ------------------------------------------------------------------
    # Category management
    # ------------------------------------------------------------------

    def list_categories(self) -> Dict[str, Any]:
        """List all available memory categories with their counts.

        Returns:
            Dictionary of category names to their usage counts.
        """
        return self._client.get(f"{self._base_path}/categories")

    def get_category_stats(self, category: MemoryCategory) -> Dict[str, Any]:
        """Get statistics for a specific memory category.

        Args:
            category: The category to get stats for.

        Returns:
            Dictionary with category statistics (count, size, etc.).
        """
        return self._client.get(f"{self._base_path}/categories/{category.value}/stats")
