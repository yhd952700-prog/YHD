"""
Storage Repository for LiuHao AI OS

Provides:
- Repository pattern for data access abstraction
- Standard CRUD operations
- Query capabilities with filtering
- Pagination support
- Integration with storage backends
"""

from typing import Dict, Any, List, Optional, Generic, TypeVar, Callable, Tuple
from datetime import datetime
from .backends import StorageBackend, StorageEntry, StorageStats, create_backend

# Type variable for repository entries
E = TypeVar('E', bound=StorageEntry)


class Repository(Generic[E]):
    """
    Generic Repository pattern for storage operations.
    
    Provides standard CRUD operations and query capabilities
    for working with StorageEntry subclasses.
    """
    
    def __init__(self, backend: StorageBackend[E]):
        self.backend = backend
    
    def get(self, key: str) -> Optional[E]:
        """Get an entry by key."""
        return self.backend.get(key)
    
    def put(self, key: str, value: E, ttl: Optional[float] = None) -> None:
        """Put an entry."""
        self.backend.put(key, value, ttl)
    
    def delete(self, key: str) -> bool:
        """Delete an entry by key."""
        return self.backend.delete(key)
    
    def list(self, filters: Optional[Dict[str, Any]] = None) -> List[E]:
        """List entries with optional filters."""
        entries: List[E] = []
        
        # Try to get all entries from backend
        backend_type = self.backend.storage_type
        
        if hasattr(self.backend, '_entries'):
            # JSON file backend
            for key, entry in self.backend._entries.items():
                if not entry.is_expired():
                    # Apply filters if provided
                    if filters:
                        matched = True
                        for fkey, fvalue in filters.items():
                            entry_value = getattr(entry, fkey, None)
                            if entry_value != fvalue:
                                matched = False
                                break
                        if matched:
                            entries.append(entry)
                    else:
                        entries.append(entry)
            return entries
        
        # In-memory backend
        if hasattr(self.backend, '_entries'):
            for key, value in self.backend._entries.items():
                if not self.backend._is_expired(value):
                    entries.append(value)  # type: ignore
        
        # Apply filters if provided
        if filters:
            filtered: List[E] = []
            for entry in entries:
                matched = True
                for fkey, fvalue in filters.items():
                    entry_value = getattr(entry, fkey, None)
                    if entry_value != fvalue:
                        matched = False
                        break
                if matched:
                    filtered.append(entry)
            return filtered
        
        return entries
    
    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count entries matching filters."""
        entries = self.list(filters)
        return len(entries)
    
    def cleanup(self) -> int:
        """Remove expired entries."""
        return self.backend.cleanup()
    
    def stats(self) -> StorageStats:
        """Get storage statistics."""
        return self.backend.stats
    
    def find(self, key: str) -> Optional[E]:
        """Find an entry by key (alias for get)."""
        return self.get(key)


class PaginatedRepository(Repository[E]):
    """
    Paginated repository supporting offset-based pagination.
    """
    
    def __init__(self, backend: StorageBackend[E], page_size: int = 20):
        super().__init__(backend)
        self.page_size = page_size
    
    def get_page(self, page_number: int, filters: Optional[Dict[str, Any]] = None) -> Tuple[List[E], int]:
        """
        Get a page of entries.
        
        Returns:
            Tuple of (entries_list, total_count)
        """
        entries = self.list(filters)
        total = len(entries)
        
        # Offset-based pagination
        skip = (page_number - 1) * self.page_size
        take = min(self.page_size, len(entries) - skip)
        
        if skip >= len(entries):
            return [], total
        
        page_entries = entries[skip:skip + take]
        return page_entries, total


# ==================== Storage Factory Improvements ====================

def create_repository(backend_type: str, **kwargs) -> Tuple[StorageBackend, Repository]:
    """
    Create a storage backend and its repository.
    
    Args:
        backend_type: "json_file" or "in_memory"
        **kwargs: Backend-specific configuration options
    
    Returns:
        Tuple of (StorageBackend, Repository)
    """
    backend = create_backend(backend_type, **kwargs)
    repository = Repository(backend)
    return backend, repository


# ==================== Convenience Functions ====================

def create_storage(backend_type: str, **kwargs) -> Tuple[StorageBackend, Repository]:
    """Convenience function: create both backend and repository."""
    backend, repository = create_repository(backend_type, **kwargs)
    return backend, repository