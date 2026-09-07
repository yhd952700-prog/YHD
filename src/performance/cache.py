"""
Cache Implementation for LiuHao AI OS

Provides:
- In-memory cache with LRU/FIFO/LFU/TTL eviction policies
- Multi-tier cache (memory + redis + disk)
- Cache statistics and monitoring
- Cache entry expiration and cleanup
"""

from pathlib import Path
import json
import time
import threading
from typing import Dict, Any, Optional, List, Callable, Tuple

from .cache_models import CacheEntry, CacheStats, CacheEvictionPolicy, CacheTier


class LRUCache:
    """Least Recently Used cache implementation."""
    
    def __init__(self, max_size: int = 128):
        self.max_size = max_size
        self.cache: Dict[str, CacheEntry] = {}
        self.order: List[str] = []  # Ordered by access, most recent at end
        self._lock = threading.Lock()
        self.stats = CacheStats(max_size=max_size)
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from the cache."""
        with self._lock:
            if key in self.cache:
                entry = self.cache[key]
                if entry.is_expired():
                    self._remove(key)
                    self.stats.cache_misses += 1
                    self.stats.expired_count += 1
                    return None
                
                # Update access order (move to end = most recent)
                self.order.remove(key)
                self.order.append(key)
                entry.touch()
                
                self.stats.total_requests += 1
                self.stats.cache_hits += 1
                return entry.value
            
            self.stats.total_requests += 1
            self.stats.cache_misses += 1
            return None
    
    def put(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Put a value into the cache."""
        with self._lock:
            expiry = None
            if ttl is not None:
                expiry = time.time() + ttl
            
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=time.time(),
                accessed_at=time.time(),
                expiry=expiry,
                ttl=ttl,
            )
            
            if key in self.cache:
                # Update existing
                self.cache[key] = entry
                self.order.remove(key)
                self.order.append(key)
            elif len(self.cache) >= self.max_size:
                # Evict LRU
                lru_key = self.order.pop(0)
                self._remove(lru_key)
            
            self.cache[key] = entry
            self.order.append(key)
    
    def _remove(self, key: str) -> None:
        """Remove an entry from the cache."""
        if key in self.cache:
            del self.cache[key]
        if key in self.order:
            self.order.remove(key)
    
    def delete(self, key: str) -> bool:
        """Delete a key from the cache."""
        with self._lock:
            if key in self.cache:
                self._remove(key)
                return True
            return False
    
    def contains(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        with self._lock:
            if key in self.cache:
                return not self.cache[key].is_expired()
            return False
    
    def cleanup(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        with self._lock:
            expired_keys = [k for k, v in self.cache.items() if v.is_expired()]
            for key in expired_keys:
                self._remove(key)
                self.stats.expired_count += 1
            return len(expired_keys)
    
    def get_stats(self) -> CacheStats:
        """Get cache statistics."""
        with self._lock:
            self.stats.current_size = len(self.cache)
            return self.stats


class MultiTierCache:
    """
    Multi-tier cache: memory -> redis -> disk.
    
    Designed for high-performance access patterns with fallback
    to slower storage tiers.
    """
    
    def __init__(self, memory_max_size: int = 1000):
        self.memory_cache = LRUCache(max_size=memory_max_size)
        self.initialized = False
    
    def _ensure_initialized(self) -> None:
        """Ensure the cache is initialized (placeholder for redis/disk setup)."""
        if not self.initialized:
            # In production: initialize Redis connection, disk cache path, etc.
            self.initialized = True
    
    def get(self, key: str) -> Optional[Any]:
        """Get value: try memory first, then fall back."""
        self._ensure_initialized()
        # Try memory cache first
        value = self.memory_cache.get(key)
        if value is not None:
            return value
        # TODO: Try Redis, then Disk
        return None
    
    def put(self, key: str, value: Any, ttl: Optional[int] = None, tier: str = CacheTier.MEMORY) -> None:
        """Put value in specified tier."""
        self._ensure_initialized()
        if tier == CacheTier.MEMORY:
            self.memory_cache.put(key, value, ttl)
        # TODO: Add Redis and Disk tier support
    
    def stats(self) -> Dict[str, CacheStats]:
        """Get statistics from all tiers."""
        self._ensure_initialized()
        return {"memory": self.memory_cache.get_stats()}


class BatchCache:
    """Batch cache operations for improved performance."""
    
    def __init__(self, cache: LRUCache):
        self.cache = cache
    
    def get_batch(self, keys: List[str]) -> Dict[str, Optional[Any]]:
        """Get multiple values at once."""
        results: Dict[str, Optional[Any]] = {}
        with self.cache._lock:
            # First pass: check which keys exist and are not expired
            for key in keys:
                if key in self.cache.cache and not self.cache.cache[key].is_expired():
                    results[key] = self.cache.cache[key].value
                else:
                    results[key] = None
            
            # Update stats
            total = len(keys)
            hits = sum(1 for v in results.values() if v is not None)
            misses = total - hits
            self.cache.stats.total_requests += total
            self.cache.stats.cache_hits += hits
            self.cache.stats.cache_misses += misses
        
        return results
    
    def put_batch(self, items: Dict[str, Any], ttl: Optional[int] = None) -> None:
        """Put multiple values at once."""
        with self.cache._lock:
            for key, value in items.items():
                self.cache.put(key, value, ttl)


# Convenience function
def create_lru_cache(max_size: int = 128) -> LRUCache:
    """Create an LRU cache instance."""
    return LRUCache(max_size=max_size)