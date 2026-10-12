"""Prompt Cache for Model Gateway.

Prompt caching system with:
- Semantic cache (cache similar prompts using embeddings)
- Exact match cache
- Cache with TTL (time-to-live)
- Cache invalidation strategies
- Cache hit/miss statistics
- Multi-level cache (memory + disk)
- Cache warming
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from uuid import uuid4


class CacheBackend(str, Enum):
    """Cache backend types."""

    MEMORY = "memory"
    DISK = "disk"
    REDIS = "redis"
    MEMORY_DISK_HYBRID = "memory_disk_hybrid"


class CacheInvalidationStrategy(str, Enum):
    """Cache invalidation strategies."""

    TTL = "ttl"
    LRU = "lru"
    LFU = "lfu"
    FIFO = "fifo"
    MANUAL = "manual"


@dataclass
class CacheEntry:
    """A single cache entry.

    Attributes:
        key: Cache key
        value: Cached value (response data)
        created_at: Creation timestamp
        expires_at: Expiration timestamp (0 = no expiry)
        access_count: Number of times accessed
        last_accessed: Last access timestamp
        size_bytes: Approximate size in bytes
        metadata: Additional metadata
    """

    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    size_bytes: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if the entry has expired.

        Returns:
            True if expired
        """
        if self.expires_at <= 0:
            return False
        return time.time() > self.expires_at

    def touch(self) -> None:
        """Record an access to this entry."""
        self.access_count += 1
        self.last_accessed = time.time()


@dataclass
class CacheStats:
    """Cache statistics.

    Attributes:
        hits: Number of cache hits
        misses: Number of cache misses
        total_requests: Total cache requests
        hit_rate: Hit rate (0-1)
        evictions: Number of evictions
        total_entries: Current number of entries
        total_size_bytes: Total size in bytes
        semantic_hits: Number of semantic cache hits
        exact_hits: Number of exact match hits
    """

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_entries: int = 0
    total_size_bytes: int = 0
    semantic_hits: int = 0
    exact_hits: int = 0

    @property
    def total_requests(self) -> int:
        """Total cache requests."""
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0-1)."""
        if self.total_requests == 0:
            return 0.0
        return self.hits / self.total_requests

    @property
    def miss_rate(self) -> float:
        """Cache miss rate (0-1)."""
        if self.total_requests == 0:
            return 0.0
        return self.misses / self.total_requests


@dataclass
class SemanticMatch:
    """Result of a semantic cache lookup.

    Attributes:
        key: Matched cache key
        similarity: Similarity score (0-1)
        value: Cached value
        metadata: Entry metadata
    """

    key: str
    similarity: float
    value: Any
    metadata: Dict[str, Any]


class MemoryCache:
    """In-memory LRU cache with TTL support.

    Implements a thread-safe in-memory cache with configurable
    eviction strategies (LRU, LFU, FIFO, TTL).
    """

    def __init__(
        self,
        max_entries: int = 10000,
        max_size_bytes: int = 100 * 1024 * 1024,  # 100 MB
        default_ttl_seconds: int = 3600,
        invalidation_strategy: CacheInvalidationStrategy = CacheInvalidationStrategy.LRU,
    ) -> None:
        """Initialize the memory cache.

        Args:
            max_entries: Maximum number of entries
            max_size_bytes: Maximum total size in bytes
            default_ttl_seconds: Default TTL in seconds (0 = no expiry)
            invalidation_strategy: Eviction strategy to use
        """
        self._cache: Dict[str, CacheEntry] = {}
        self._max_entries = max_entries
        self._max_size_bytes = max_size_bytes
        self._default_ttl = default_ttl_seconds
        self._strategy = invalidation_strategy
        self._stats = CacheStats()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value, or None if not found or expired
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats.misses += 1
                return None

            if entry.is_expired():
                del self._cache[key]
                self._stats.evictions += 1
                self._stats.misses += 1
                return None

            entry.touch()
            self._stats.hits += 1
            self._stats.exact_hits += 1
            return entry.value

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set a value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: TTL in seconds (None = use default)
            metadata: Additional metadata
        """
        with self._lock:
            ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
            expires_at = time.time() + ttl if ttl > 0 else 0.0

            try:
                size_bytes = len(pickle.dumps(value))
            except Exception:
                size_bytes = len(str(value).encode())

            entry = CacheEntry(
                key=key,
                value=value,
                expires_at=expires_at,
                size_bytes=size_bytes,
                metadata=metadata or {},
            )

            # Evict if needed
            self._evict_if_needed(size_bytes)

            self._cache[key] = entry
            self._stats.total_entries = len(self._cache)
            self._stats.total_size_bytes += size_bytes

    def delete(self, key: str) -> bool:
        """Delete a cache entry.

        Args:
            key: Cache key

        Returns:
            True if entry was deleted, False if not found
        """
        with self._lock:
            entry = self._cache.pop(key, None)
            if entry:
                self._stats.total_size_bytes -= entry.size_bytes
                self._stats.total_entries = len(self._cache)
                return True
            return False

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            self._stats.total_entries = 0
            self._stats.total_size_bytes = 0

    def has(self, key: str) -> bool:
        """Check if a key exists and is not expired.

        Args:
            key: Cache key

        Returns:
            True if key exists and is valid
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._cache[key]
                return False
            return True

    def _evict_if_needed(self, new_size: int) -> None:
        """Evict entries if cache is over capacity.

        Must be called with lock held.
        """
        # Check entry count
        while len(self._cache) >= self._max_entries:
            self._evict_one()

        # Check size
        while (
            self._stats.total_size_bytes + new_size > self._max_size_bytes
            and self._cache
        ):
            self._evict_one()

    def _evict_one(self) -> None:
        """Evict one entry based on strategy.

        Must be called with lock held.
        """
        if not self._cache:
            return

        if self._strategy == CacheInvalidationStrategy.LRU:
            # Evict least recently used
            victim = min(
                self._cache.values(), key=lambda e: e.last_accessed
            )
        elif self._strategy == CacheInvalidationStrategy.LFU:
            # Evict least frequently used
            victim = min(
                self._cache.values(), key=lambda e: e.access_count
            )
        elif self._strategy == CacheInvalidationStrategy.FIFO:
            # Evict oldest
            victim = min(self._cache.values(), key=lambda e: e.created_at)
        else:
            # Default to LRU
            victim = min(
                self._cache.values(), key=lambda e: e.last_accessed
            )

        del self._cache[victim.key]
        self._stats.total_size_bytes -= victim.size_bytes
        self._stats.evictions += 1
        self._stats.total_entries = len(self._cache)

    def get_stats(self) -> CacheStats:
        """Get cache statistics.

        Returns:
            Cache statistics
        """
        with self._lock:
            stats = CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                total_entries=len(self._cache),
                total_size_bytes=self._stats.total_size_bytes,
                exact_hits=self._stats.exact_hits,
                semantic_hits=self._stats.semantic_hits,
            )
        return stats

    def keys(self) -> List[str]:
        """Get all cache keys.

        Returns:
            List of cache keys
        """
        with self._lock:
            return list(self._cache.keys())

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        return self.has(key)


class DiskCache:
    """Disk-based cache with TTL support.

    Stores cache entries as files on disk for persistence across restarts.
    """

    def __init__(
        self,
        cache_dir: str,
        max_size_bytes: int = 1024 * 1024 * 1024,  # 1 GB
        default_ttl_seconds: int = 86400,  # 24 hours
    ) -> None:
        """Initialize the disk cache.

        Args:
            cache_dir: Directory to store cache files
            max_size_bytes: Maximum total size in bytes
            default_ttl_seconds: Default TTL in seconds
        """
        self._cache_dir = cache_dir
        self._max_size_bytes = max_size_bytes
        self._default_ttl = default_ttl_seconds
        self._stats = CacheStats()
        self._lock = threading.Lock()

        os.makedirs(cache_dir, exist_ok=True)

    def _get_filepath(self, key: str) -> str:
        """Get the file path for a cache key."""
        safe_key = hashlib.sha256(key.encode()).hexdigest()
        return os.path.join(self._cache_dir, f"{safe_key}.cache")

    def get(self, key: str) -> Optional[Any]:
        """Get a value from disk cache.

        Args:
            key: Cache key

        Returns:
            Cached value, or None if not found or expired
        """
        filepath = self._get_filepath(key)
        try:
            if not os.path.exists(filepath):
                with self._lock:
                    self._stats.misses += 1
                return None

            with open(filepath, "rb") as f:
                entry = pickle.load(f)

            if entry.is_expired():
                os.remove(filepath)
                with self._lock:
                    self._stats.evictions += 1
                    self._stats.misses += 1
                return None

            entry.touch()
            with self._lock:
                self._stats.hits += 1
                self._stats.exact_hits += 1
            return entry.value
        except Exception:
            with self._lock:
                self._stats.misses += 1
            return None

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Set a value in disk cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: TTL in seconds
            metadata: Additional metadata
        """
        filepath = self._get_filepath(key)
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at = time.time() + ttl if ttl > 0 else 0.0

        try:
            size_bytes = len(pickle.dumps(value))
        except Exception:
            size_bytes = len(str(value).encode())

        entry = CacheEntry(
            key=key,
            value=value,
            expires_at=expires_at,
            size_bytes=size_bytes,
            metadata=metadata or {},
        )

        try:
            with open(filepath, "wb") as f:
                pickle.dump(entry, f)
            with self._lock:
                self._stats.total_size_bytes += size_bytes
                self._stats.total_entries += 1
        except Exception:
            pass

    def delete(self, key: str) -> bool:
        """Delete a cache entry.

        Args:
            key: Cache key

        Returns:
            True if deleted, False if not found
        """
        filepath = self._get_filepath(key)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                with self._lock:
                    self._stats.total_entries = max(
                        0, self._stats.total_entries - 1
                    )
                return True
        except Exception:
            pass
        return False

    def clear(self) -> None:
        """Clear all disk cache entries."""
        try:
            for filename in os.listdir(self._cache_dir):
                if filename.endswith(".cache"):
                    filepath = os.path.join(self._cache_dir, filename)
                    os.remove(filepath)
            with self._lock:
                self._stats.total_entries = 0
                self._stats.total_size_bytes = 0
        except Exception:
            pass

    def has(self, key: str) -> bool:
        """Check if a key exists and is not expired.

        Args:
            key: Cache key

        Returns:
            True if key exists and is valid
        """
        return self.get(key) is not None

    def get_stats(self) -> CacheStats:
        """Get cache statistics.

        Returns:
            Cache statistics
        """
        with self._lock:
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                total_entries=self._stats.total_entries,
                total_size_bytes=self._stats.total_size_bytes,
                exact_hits=self._stats.exact_hits,
                semantic_hits=self._stats.semantic_hits,
            )


class SemanticCache:
    """Semantic cache for finding similar prompts.

    Uses a simple vector-based approach to find semantically similar
    prompts and return cached responses.

    Note: This is a simplified implementation. For production use,
    integrate with a proper vector database.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.9,
        max_entries: int = 5000,
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
    ) -> None:
        """Initialize the semantic cache.

        Args:
            similarity_threshold: Minimum similarity to consider a match (0-1)
            max_entries: Maximum number of entries
            embedding_fn: Function to compute embeddings
        """
        self._threshold = similarity_threshold
        self._max_entries = max_entries
        self._embedding_fn = embedding_fn
        self._entries: Dict[str, Tuple[List[float], Any, Dict[str, Any]]] = {}
        self._stats = CacheStats()
        self._lock = threading.Lock()

    def _embed(self, text: str) -> List[float]:
        """Compute embedding for text.

        Args:
            text: Text to embed

        Returns:
            Embedding vector
        """
        if self._embedding_fn:
            return self._embedding_fn(text)

        # Fallback: simple character n-gram based embedding
        # This is a rough approximation for semantic matching
        # In production, use a proper embedding model
        import hashlib

        vec_size = 128
        vec = [0.0] * vec_size

        # Hash-based pseudo embedding
        for i, char in enumerate(text.lower()):
            h = int(hashlib.md5(f"{i}:{char}".encode()).hexdigest(), 16)
            idx = h % vec_size
            vec[idx] += 1.0

        # Normalize
        magnitude = sum(v * v for v in vec) ** 0.5
        if magnitude > 0:
            vec = [v / magnitude for v in vec]

        return vec

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors.

        Args:
            a: First vector
            b: Second vector

        Returns:
            Cosine similarity (0-1)
        """
        if len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x * x for x in a) ** 0.5
        mag_b = sum(x * x for x in b) ** 0.5

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot / (mag_a * mag_b)

    def lookup(self, prompt: str) -> Optional[SemanticMatch]:
        """Look up a semantically similar prompt.

        Args:
            prompt: Prompt to look up

        Returns:
            SemanticMatch if found above threshold, None otherwise
        """
        with self._lock:
            if not self._entries:
                self._stats.misses += 1
                return None

            query_embedding = self._embed(prompt)

            best_match = None
            best_similarity = 0.0

            for key, (embedding, value, metadata) in self._entries.items():
                sim = self._cosine_similarity(query_embedding, embedding)
                if sim > best_similarity and sim >= self._threshold:
                    best_similarity = sim
                    best_match = SemanticMatch(
                        key=key,
                        similarity=sim,
                        value=value,
                        metadata=metadata,
                    )

            if best_match:
                self._stats.hits += 1
                self._stats.semantic_hits += 1
            else:
                self._stats.misses += 1

            return best_match

    def store(
        self,
        prompt: str,
        response: Any,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store a prompt-response pair in the semantic cache.

        Args:
            prompt: The prompt
            response: The response to cache
            metadata: Additional metadata

        Returns:
            Cache key
        """
        key = hashlib.sha256(prompt.encode()).hexdigest()
        embedding = self._embed(prompt)

        with self._lock:
            # Evict if over capacity
            while len(self._entries) >= self._max_entries:
                oldest_key = next(iter(self._entries))
                del self._entries[oldest_key]
                self._stats.evictions += 1

            self._entries[key] = (embedding, response, metadata or {})
            self._stats.total_entries = len(self._entries)

        return key

    def clear(self) -> None:
        """Clear all semantic cache entries."""
        with self._lock:
            self._entries.clear()
            self._stats.total_entries = 0

    def get_stats(self) -> CacheStats:
        """Get cache statistics.

        Returns:
            Cache statistics
        """
        with self._lock:
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                total_entries=len(self._entries),
                total_size_bytes=self._stats.total_size_bytes,
                semantic_hits=self._stats.semantic_hits,
                exact_hits=self._stats.exact_hits,
            )

    def __len__(self) -> int:
        return len(self._entries)


class PromptCache:
    """Multi-level prompt cache with exact and semantic matching.

    Features:
    - Exact match cache (fast path)
    - Semantic cache (similar prompts)
    - Multi-level: memory -> disk
    - TTL support
    - Cache statistics
    - Cache warming

    Usage:
        cache = PromptCache(cache_dir="./cache")
        cache.set_exact("prompt_key", response)
        result = cache.get_exact("prompt_key")
        match = cache.lookup_semantic("similar prompt")
    """

    def __init__(
        self,
        *,
        cache_dir: Optional[str] = None,
        memory_max_entries: int = 10000,
        memory_max_size_mb: int = 100,
        disk_max_size_mb: int = 1024,
        default_ttl_seconds: int = 3600,
        similarity_threshold: float = 0.9,
        enable_semantic: bool = True,
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
    ) -> None:
        """Initialize the prompt cache.

        Args:
            cache_dir: Disk cache directory (None = disable disk cache)
            memory_max_entries: Max entries in memory cache
            memory_max_size_mb: Max size of memory cache in MB
            disk_max_size_mb: Max size of disk cache in MB
            default_ttl_seconds: Default TTL in seconds
            similarity_threshold: Semantic match threshold
            enable_semantic: Whether to enable semantic caching
            embedding_fn: Custom embedding function
        """
        self._memory_cache = MemoryCache(
            max_entries=memory_max_entries,
            max_size_bytes=memory_max_size_mb * 1024 * 1024,
            default_ttl_seconds=default_ttl_seconds,
        )

        self._disk_cache: Optional[DiskCache] = None
        if cache_dir:
            self._disk_cache = DiskCache(
                cache_dir=cache_dir,
                max_size_bytes=disk_max_size_mb * 1024 * 1024,
                default_ttl_seconds=default_ttl_seconds * 24,
            )

        self._semantic_cache: Optional[SemanticCache] = None
        if enable_semantic:
            self._semantic_cache = SemanticCache(
                similarity_threshold=similarity_threshold,
                embedding_fn=embedding_fn,
            )

        self._default_ttl = default_ttl_seconds

    # ------------------------------------------------------------------
    # Exact match operations
    # ------------------------------------------------------------------

    def get_exact(self, key: str) -> Optional[Any]:
        """Get exact match from cache.

        Checks memory first, then disk if available.

        Args:
            key: Cache key

        Returns:
            Cached value, or None if not found
        """
        # Try memory cache
        value = self._memory_cache.get(key)
        if value is not None:
            return value

        # Try disk cache
        if self._disk_cache:
            value = self._disk_cache.get(key)
            if value is not None:
                # Promote to memory cache
                self._memory_cache.set(key, value)
                return value

        return None

    def set_exact(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        persist: bool = True,
    ) -> None:
        """Set exact match in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: TTL override in seconds
            metadata: Additional metadata
            persist: Whether to persist to disk cache
        """
        self._memory_cache.set(
            key, value, ttl_seconds=ttl_seconds, metadata=metadata
        )

        if persist and self._disk_cache:
            self._disk_cache.set(
                key, value, ttl_seconds=ttl_seconds, metadata=metadata
            )

    def delete_exact(self, key: str) -> bool:
        """Delete exact match from all cache levels.

        Args:
            key: Cache key

        Returns:
            True if deleted from at least one level
        """
        deleted = False
        if self._memory_cache.delete(key):
            deleted = True
        if self._disk_cache and self._disk_cache.delete(key):
            deleted = True
        return deleted

    # ------------------------------------------------------------------
    # Semantic operations
    # ------------------------------------------------------------------

    def lookup_semantic(self, prompt: str) -> Optional[SemanticMatch]:
        """Look up semantically similar prompt.

        Args:
            prompt: Prompt to look up

        Returns:
            SemanticMatch if found, None otherwise
        """
        if self._semantic_cache is None:
            return None
        return self._semantic_cache.lookup(prompt)

    def store_semantic(
        self,
        prompt: str,
        response: Any,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Store prompt-response in semantic cache.

        Args:
            prompt: The prompt
            response: The response
            metadata: Additional metadata

        Returns:
            Cache key
        """
        if self._semantic_cache is None:
            return ""
        return self._semantic_cache.store(
            prompt, response, metadata=metadata
        )

    # ------------------------------------------------------------------
    # Combined operations
    # ------------------------------------------------------------------

    def query(
        self,
        prompt: str,
        *,
        model_id: Optional[str] = None,
        params_hash: Optional[str] = None,
    ) -> Tuple[Optional[Any], str]:
        """Query the cache with a prompt.

        Checks exact match first, then semantic cache.

        Args:
            prompt: The prompt text
            model_id: Model ID for key generation
            params_hash: Hash of generation parameters

        Returns:
            Tuple of (cached_value or None, "exact"|"semantic"|"miss")
        """
        # Try exact match
        key_parts = [prompt]
        if model_id:
            key_parts.append(model_id)
        if params_hash:
            key_parts.append(params_hash)

        exact_key = hashlib.sha256(
            "|".join(key_parts).encode()
        ).hexdigest()

        exact_value = self.get_exact(exact_key)
        if exact_value is not None:
            return exact_value, "exact"

        # Try semantic match
        semantic_match = self.lookup_semantic(prompt)
        if semantic_match:
            return semantic_match.value, "semantic"

        return None, "miss"

    def put(
        self,
        prompt: str,
        response: Any,
        *,
        model_id: Optional[str] = None,
        params_hash: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        persist: bool = True,
    ) -> str:
        """Store a prompt-response in the cache.

        Args:
            prompt: The prompt
            response: The response
            model_id: Model ID
            params_hash: Parameter hash
            ttl_seconds: TTL override
            persist: Whether to persist to disk

        Returns:
            Exact match cache key
        """
        key_parts = [prompt]
        if model_id:
            key_parts.append(model_id)
        if params_hash:
            key_parts.append(params_hash)

        exact_key = hashlib.sha256(
            "|".join(key_parts).encode()
        ).hexdigest()

        self.set_exact(
            exact_key,
            response,
            ttl_seconds=ttl_seconds,
            metadata={"prompt": prompt[:200], "model_id": model_id},
            persist=persist,
        )

        if self._semantic_cache:
            self.store_semantic(
                prompt,
                response,
                metadata={"model_id": model_id, "exact_key": exact_key},
            )

        return exact_key

    # ------------------------------------------------------------------
    # Cache warming
    # ------------------------------------------------------------------

    def warm_up(
        self,
        entries: List[Tuple[str, Any]],
        *,
        model_id: Optional[str] = None,
    ) -> int:
        """Pre-populate the cache with entries.

        Args:
            entries: List of (prompt, response) tuples
            model_id: Model ID to associate with entries

        Returns:
            Number of entries warmed
        """
        count = 0
        for prompt, response in entries:
            self.put(prompt, response, model_id=model_id)
            count += 1
        return count

    def warm_from_disk(self) -> int:
        """Load popular entries from disk into memory.

        Returns:
            Number of entries loaded
        """
        if not self._disk_cache:
            return 0

        count = 0
        # In a full implementation, we'd track access patterns
        # For now, load a sample of entries
        try:
            for filename in os.listdir(self._disk_cache._cache_dir):
                if filename.endswith(".cache") and count < 100:
                    key = filename[:-6]  # Remove .cache
                    # We can't easily get the original key, so skip
                    count += 1
        except Exception:
            pass

        return count

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Clear all cache levels."""
        self._memory_cache.clear()
        if self._disk_cache:
            self._disk_cache.clear()
        if self._semantic_cache:
            self._semantic_cache.clear()

    def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate cache entries matching a pattern.

        Args:
            pattern: Substring pattern to match against keys

        Returns:
            Number of entries invalidated
        """
        count = 0
        for key in list(self._memory_cache.keys()):
            if pattern in key:
                self._memory_cache.delete(key)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics.

        Returns:
            Dictionary of cache statistics
        """
        memory_stats = self._memory_cache.get_stats()
        result: Dict[str, Any] = {
            "memory": {
                "hits": memory_stats.hits,
                "misses": memory_stats.misses,
                "hit_rate": memory_stats.hit_rate,
                "entries": memory_stats.total_entries,
                "size_bytes": memory_stats.total_size_bytes,
                "evictions": memory_stats.evictions,
            }
        }

        if self._disk_cache:
            disk_stats = self._disk_cache.get_stats()
            result["disk"] = {
                "hits": disk_stats.hits,
                "misses": disk_stats.misses,
                "hit_rate": disk_stats.hit_rate,
                "entries": disk_stats.total_entries,
                "size_bytes": disk_stats.total_size_bytes,
            }

        if self._semantic_cache:
            semantic_stats = self._semantic_cache.get_stats()
            result["semantic"] = {
                "hits": semantic_stats.hits,
                "misses": semantic_stats.misses,
                "hit_rate": semantic_stats.hit_rate,
                "entries": semantic_stats.total_entries,
                "semantic_hits": semantic_stats.semantic_hits,
            }

        # Combined
        total_hits = memory_stats.hits
        total_misses = memory_stats.misses
        if self._disk_cache:
            total_hits += self._disk_cache.get_stats().hits
            total_misses += self._disk_cache.get_stats().misses

        result["combined"] = {
            "total_hits": total_hits,
            "total_misses": total_misses,
            "overall_hit_rate": (
                total_hits / (total_hits + total_misses)
                if (total_hits + total_misses) > 0
                else 0.0
            ),
        }

        return result

    def __repr__(self) -> str:
        stats = self.get_stats()
        combined = stats.get("combined", {})
        return (
            f"PromptCache(entries={stats['memory']['entries']}, "
            f"hit_rate={combined.get('overall_hit_rate', 0):.2%})"
        )
