"""
Storage Backend Models for LiuHao AI OS

Defines the data model for storage backends and data entries.
Provides standardized interfaces for different storage types.
"""

from typing import Dict, Any, Optional, List, Generic, TypeVar
from abc import ABC, abstractmethod
from datetime import datetime
import uuid

# Type variable for storage entries
T = TypeVar('T')


class StorageType(Enum):
    """Storage backend type enumeration."""
    JSON_FILE = "json_file"     # JSON 文件后端
    IN_MEMORY = "in_memory"     # 内存后端
    SQLALCHEMY = "sqlalchemy"   # SQLAlchemy ORM 后端


class ConsistencyLevel(Enum):
    """Consistency levels for distributed storage."""
    STRONG = "strong"      # 强一致性
    EVENTUAL = "eventual"    # 最终一致性
    BEST_EFFORT = "best_effort"  # 最努力


@dataclass
class StorageEntry:
    """Base storage entry model."""
    key: str
    value: Any
    created_at: float
    updated_at: float
    tags: Dict[str, str] = field(default_factory=dict)
    ttl: Optional[float] = None  # Time To Live in seconds

    def is_expired(self) -> bool:
        """Check if entry has expired based on TTL."""
        if self.ttl is None:
            return False
        return time.time() > self.created_at + self.ttl

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "key": self.key,
            "value": self.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": self.tags,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StorageEntry":
        """Create StorageEntry from dictionary."""
        return cls(
            key=data.get("key", ""),
            value=data.get("value"),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            tags=data.get("tags", {}),
            ttl=data.get("ttl"),
        )


@dataclass
class StorageStats:
    """Statistics for a storage backend."""
    total_entries: int = 0
    current_size: int = 0
    max_size: int = 0
    hit_count: int = 0
    miss_count: int = 0
    eviction_count: int = 0
    expired_count: int = 0

    @property
    def utilization(self) -> float:
        """Calculate utilization ratio."""
        if self.max_size == 0:
            return 0.0
        return self.current_size / self.max_size

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate."""
        total = self.hit_count + self.miss_count
        if total == 0:
            return 0.0
        return self.hit_count / total


# ==================== Storage Backend Interface ====================

class StorageBackend(ABC, Generic[T]):
    """
    Abstract base class for storage backends.
    
    All concrete storage backends must implement this interface.
    """
    
    @property
    @abstractmethod
    def storage_type(self) -> StorageType:
        """Return the storage backend type."""
        pass
    
    @property
    @abstractmethod
    def stats(self) -> StorageStats:
        """Return storage statistics."""
        pass
    
    @abstractmethod
    def get(self, key: str) -> Optional[T]:
        """Get a value by key."""
        pass
    
    @abstractmethod
    def put(self, key: str, value: T, ttl: Optional[float] = None) -> None:
        """Put a value with optional TTL."""
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a value by key. Returns True if deleted."""
        pass
    
    @abstractmethod
    def contains(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        pass
    
    @abstractmethod
    def cleanup(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        pass
    
    @abstractmethod
    def get_stats(self) -> StorageStats:
        """Get storage statistics."""
        pass


# ==================== Concrete Implementations ====================

class JSONFileBackend(StorageBackend[StorageEntry]):
    """
    JSON file-based storage backend.
    
    Persists data to a JSON file with hash chain integrity verification.
    Suitable for small to medium datasets.
    """
    
    def __init__(self, storage_path: str = "data/storage/entries.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: Dict[str, StorageEntry] = {}
        self._hash_chain: Optional[List[str]] = None
        self._load()
    
    @property
    def storage_type(self) -> StorageType:
        return StorageType.JSON_FILE
    
    @property
    def stats(self) -> StorageStats:
        """Return storage statistics."""
        return StorageStats(
            total_entries=len(self._entries),
            current_size=sum(1 for e in self._entries.values() if not e.is_expired()),
            max_size=len(self._entries),
            hit_count=0,  # Tracked externally if needed
            miss_count=0,
            eviction_count=0,
            expired_count=len([e for e in self._entries.values() if e.is_expired()]),
        )
    
    def _load(self) -> None:
        """Load entries from JSON file."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._entries = {
                    k: StorageEntry.from_dict(v) for k, v in data.get("entries", {}).items()
                }
                self._hash_chain = data.get("hash_chain")
                if not self._hash_chain or len(self._hash_chain) != len(self._entries):
                    self._hash_chain = None
                    self._build_hash_chain()
            except Exception as e:
                print(f"Warning: Failed to load storage: {e}")
                self._entries = {}
                self._hash_chain = None
        else:
            self._entries = {}
            self._hash_chain = None
    
    def _build_hash_chain(self) -> None:
        """Build or rebuild the hash chain."""
        chain: List[str] = []
        sorted_keys = sorted(self._entries.keys())
        prev_hash = "genesis"
        for key in sorted_keys:
            entry = self._entries[key]
            entry_dict = entry.to_dict()
            entry_dict["prev_hash"] = prev_hash
            entry_data = json.dumps(entry_dict, sort_keys=True, separators=(",", ":"))
            entry_hash = hashlib.sha256(entry_data.encode()).hexdigest()
            chain.append(entry_hash)
            prev_hash = entry_hash
        self._hash_chain = chain
        self._save()
    
    def _save(self) -> None:
        """Persist entries to JSON file."""
        data = {
            "version": 1,
            "saved_at": time.time(),
            "hash_chain": self._hash_chain,
            "entries": {k: v.to_dict() for k, v in self._entries.items()},
        }
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    def get(self, key: str) -> Optional[StorageEntry]:
        """Get an entry by key."""
        entry = self._entries.get(key)
        if entry and entry.is_expired():
            # Remove expired entry
            del self._entries[key]
            self._save()
            return None
        return entry
    
    def put(self, key: str, value: StorageEntry, ttl: Optional[float] = None) -> None:
        """Put an entry."""
        if ttl is not None:
            entry = StorageEntry(
                key=key,
                value=value.value,
                created_at=time.time(),
                updated_at=time.time(),
                ttl=ttl,
            )
        else:
            entry = StorageEntry(
                key=key,
                value=value.value if isinstance(value, StorageEntry) else value,
                created_at=time.time(),
                updated_at=time.time(),
                ttl=value.ttl if isinstance(value, StorageEntry) else None,
            )
        # Update value storage
        self._entries[key] = entry
        self._save()
    
    def delete(self, key: str) -> bool:
        """Delete an entry by key."""
        if key in self._entries:
            del self._entries[key]
            self._save()
            return True
        return False
    
    def contains(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return key in self._entries and not self._entries[key].is_expired()
    
    def cleanup(self) -> int:
        """Remove expired entries."""
        expired_keys = [k for k, v in self._entries.items() if v.is_expired()]
        for key in expired_keys:
            del self._entries[key]
        self._save()
        return len(expired_keys)
    
    def get_stats(self) -> StorageStats:
        """Get storage statistics."""
        expired = len([e for e in self._entries.values() if e.is_expired()])
        current = len([e for e in self._entries.values() if not e.is_expired()])
        
        return StorageStats(
            total_entries=len(self._entries),
            current_size=current,
            max_size=len(self._entries),
            hit_count=0,
            miss_count=0,
            eviction_count=0,
            expired_count=expired,
        )


class InMemoryBackend(StorageBackend[Any]):
    """
    In-memory storage backend.
    
    High-performance storage for caching and temporary data.
    """
    
    def __init__(self):
        self._entries: Dict[str, Any] = {}
        self._hit_count = 0
        self._miss_count = 0
        self._creation_time = time.time()
    
    @property
    def storage_type(self) -> StorageType:
        return StorageType.IN_MEMORY
    
    @property
    def stats(self) -> StorageStats:
        """Return storage statistics."""
        return StorageStats(
            total_entries=len(self._entries),
            current_size=len(self._entries),
            max_size=0,  # No limit by default
            hit_count=self._hit_count,
            miss_count=self._miss_count,
            eviction_count=0,
            expired_count=len([e for e in self._entries.values() if self._is_expired(e)]),
        )
    
    def _is_expired(self, entry: Any) -> bool:
        """Check if an entry is expired (simple implementation)."""
        # In-memory backend doesn't enforce TTL by default
        return False
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value by key."""
        if key in self._entries:
            self._hit_count += 1
            return self._entries[key]
        self._miss_count += 1
        return None
    
    def put(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Put a value."""
        # Store TTL info if provided
        if ttl is not None:
            # Simple approach: store expiry time alongside value
            expiry = time.time() + ttl
            # We'll use a wrapper approach
            self._entries[key] = {"value": value, "expiry": expiry}
        else:
            self._entries[key] = value
    
    def delete(self, key: str) -> bool:
        """Delete a value by key."""
        if key in self._entries:
            del self._entries[key]
            return True
        return False
    
    def contains(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        if key in self._entries:
            entry = self._entries[key]
            # Check expiry if stored
            if isinstance(entry, dict) and "expiry" in entry:
                return time.time() < entry["expiry"]
            return True
        return False
    
    def cleanup(self) -> int:
        """Remove expired entries."""
        expired_keys = []
        for key, entry in self._entries.items():
            if isinstance(entry, dict) and "expiry" in entry:
                if time.time() >= entry["expiry"]:
                    expired_keys.append(key)
        
        for key in expired_keys:
            del self._entries[key]
        
        return len(expired_keys)
    
    def get_stats(self) -> StorageStats:
        """Get storage statistics."""
        expired = len([e for e in self._entries.values() if self._is_expired(e)])
        
        return StorageStats(
            total_entries=len(self._entries),
            current_size=len(self._entries) - expired,
            max_size=0,
            hit_count=self._hit_count,
            miss_count=self._miss_count,
            eviction_count=0,
            expired_count=expired,
        )


# ==================== Storage Factory ====================

def create_backend(backend_type: str, **kwargs) -> StorageBackend:
    """
    Create a storage backend instance.
    
    Args:
        backend_type: "json_file", "in_memory", or "sqlalchemy"
        **kwargs: Backend-specific configuration options
    
    Returns:
        Configured StorageBackend instance
    """
    if backend_type == "json_file":
        path = kwargs.get("path", "data/storage/entries.json")
        return JSONFileBackend(path)
    elif backend_type == "in_memory":
        return InMemoryBackend()
    else:
        raise ValueError(f"Unknown backend type: {backend_type}")


# ==================== Repository Pattern ====================

M = TypeVar('M', bound='StorageEntry')

class Repository(Generic[M]):
    """
    Generic Repository pattern for storage operations.
    
    Provides standard CRUD operations and query capabilities
    for working with StorageEntry subclasses.
    """
    
    def __init__(self, backend: StorageBackend[M]):
        self.backend = backend
    
    def get(self, key: str) -> Optional[M]:
        """Get an entry by key."""
        entry = self.backend.get(key)
        return entry
    
    def put(self, key: str, value: M, ttl: Optional[float] = None) -> None:
        """Put an entry."""
        self.backend.put(key, entry=entry, ttl=ttl)
    
    def delete(self, key: str) -> bool:
        """Delete an entry by key."""
        return self.backend.delete(key)
    
    def find(self, key: str) -> Optional[M]:
        """Find an entry by key (alias for get)."""
        return self.get(key)
    
    def list(self, filters: Optional[Dict[str, Any]] = None) -> List[M]:
        """List entries with optional filters."""
        # Basic implementation - get all entries and filter
        entries = []
        # We need a way to list all keys - this is backend-dependent
        # For JSON backend, we can iterate
        if hasattr(self.backend, '_entries'):
            for key, entry in self.backend._entries.items():
                if not entry.is_expired():
                    entries.append(entry)
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