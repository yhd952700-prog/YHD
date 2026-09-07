"""
Cache Models for LiuHao AI OS

Defines the data model for cache entries and cache policies.
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import time


class CacheEvictionPolicy:
    """Cache eviction policy enumeration."""
    LRU = "lru"  # Least Recently Used
    FIFO = "fifo"  # First In First Out
    LFU = "lfu"  # Least Frequently Used
    TTL = "ttl"  # Time To Live


class CacheTier:
    """Cache tier enumeration for multi-level caching."""
    MEMORY = "memory"
    REDIS = "redis"
    DISK = "disk"


@dataclass
class CacheEntry:
    """A cache entry with value and metadata."""
    key: str
    value: Any
    created_at: float
    accessed_at: float
    expiry: Optional[float] = None
    hit_count: int = 0
    ttl: Optional[int] = None  # Time To Live in seconds
    tags: List[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        """Check if the cache entry has expired."""
        if self.expiry is None:
            return False
        return time.time() > self.expiry

    def touch(self) -> None:
        """Update access time and hit count."""
        self.accessed_at = time.time()
        self.hit_count += 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "key": self.key,
            "value": self.value,
            "created_at": self.created_at,
            "accessed_at": self.accessed_at,
            "expiry": self.expiry,
            "hit_count": self.hit_count,
            "ttl": self.ttl,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheEntry":
        """Create CacheEntry from dictionary."""
        return cls(
            key=data.get("key", ""),
            value=data.get("value"),
            created_at=data.get("created_at", time.time()),
            accessed_at=data.get("accessed_at", time.time()),
            expiry=data.get("expiry"),
            hit_count=data.get("hit_count", 0),
            ttl=data.get("ttl"),
            tags=data.get("tags", []),
        )


@dataclass
class CacheStats:
    """Statistics for a cache."""
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    eviction_count: int = 0
    expired_count: int = 0
    current_size: int = 0
    max_size: int = 0

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests

    @property
    def miss_rate(self) -> float:
        """Calculate cache miss rate."""
        if self.total_requests == 0:
            return 1.0
        return self.cache_misses / self.total_requests

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_requests": self.total_requests,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "eviction_count": self.eviction_count,
            "expired_count": self.expired_count,
            "current_size": self.current_size,
            "max_size": self.max_size,
            "hit_rate": round(self.hit_rate, 4),
            "miss_rate": round(self.miss_rate, 4),
        }
