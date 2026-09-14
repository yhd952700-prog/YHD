"""
Cache Implementation for LiuHao AI OS

Provides:
- In-memory cache with LRU/FIFO/LFU/TTL eviction policies
- Multi-tier cache (memory + redis + disk) with a **real** cascade
- Cache statistics and monitoring (per tier)
- Cache entry expiration and cleanup

诚实性说明（Phase 8，2026-09-14）
--------------------------------
重写 ``MultiTierCache`` 之前的实测缺陷（详见 ``docs/CACHE-LAYER-DESIGN.md``）：
docstring 宣称 ``memory -> redis -> disk``，但 ``put(tier=REDIS/DISK)`` **没有 else 分支**，
静默什么都不做却让调用方以为写成功；``get()`` 永不查 redis/disk；``stats()`` 只返回 memory
却自称 "all tiers"；``_ensure_initialized`` 只翻一个 flag。

现在三层都是真的：**disk 层零依赖可真机验证**，**redis 层 guarded import + 可注入 client**。
未启用的层被写时**抛 :class:`CacheTierUnavailableError` 并说明如何启用**，不再假装成功。
``LRUCache`` / ``CacheEntry`` / ``CacheStats`` / ``BatchCache`` 保持不变。
"""

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .cache_models import CacheEntry, CacheStats, CacheTier


class CacheTierUnavailableError(RuntimeError):
    """某个缓存层不可用（未启用 / 依赖缺失 / 连不上）。

    抛出它而不是静默丢弃写入，是本模块的硬纪律：一个"写成功但其实没存"的缓存
    会在读回时才暴露，且无从定位。
    """


class LRUCache:
    """Least Recently Used cache implementation."""

    def __init__(self, max_size: int = 128):
        self.max_size = max_size
        self.cache: Dict[str, CacheEntry] = {}
        self.order: List[str] = []  # Ordered by access, most recent at end
        # RLock, not Lock: ``BatchCache.put_batch`` holds this lock across the
        # loop and then calls ``put``, which re-acquires it. With a plain Lock
        # that nested acquisition deadlocked on *every* call (Phase 8 tests
        # caught it: BatchCache had zero callers and zero tests until now).
        self._lock = threading.RLock()
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


def _redact_url(url: Optional[str]) -> str:
    """Strip the password out of a DSN before it reaches a log/exception.

    Kept local (rather than importing the distribution helper) so that
    ``src.performance`` does not depend on ``src.distribution``.
    """
    if not url:
        return "<none>"
    try:
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    except ValueError:
        return "<unparseable url>"


class DiskCache:
    """A genuine file-backed cache tier.

    Each key becomes one JSON file named ``sha256(key).json`` inside
    ``directory``.  Writes are atomic (temp file + :func:`os.replace`) so a
    crash mid-write can never leave a half-written entry that later reads as
    corrupt.  TTL is stored as an absolute ``expiry`` timestamp.
    """

    def __init__(self, directory: str, default_ttl: Optional[int] = None):
        self.directory = os.path.abspath(directory)
        os.makedirs(self.directory, exist_ok=True)
        self.default_ttl = default_ttl
        self._lock = threading.Lock()
        self.stats = CacheStats()

    # -- internals ---------------------------------------------------------
    def _path(self, key: str) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(self.directory, digest + ".json")

    def _read(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            # A corrupt file is treated as a miss, not as a crash; the caller
            # re-populates it on the next put.
            return None
        return data if isinstance(data, dict) else None

    def _unlink(self, path: str) -> bool:
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False

    # -- tier interface ----------------------------------------------------
    def put(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl if ttl is not None else self.default_ttl
        now = time.time()
        entry = {
            "key": key,
            "value": value,
            "created_at": now,
            "expiry": (now + ttl) if ttl else None,
            "ttl": ttl,
        }
        try:
            blob = json.dumps(entry, ensure_ascii=False)
        except TypeError as exc:
            raise ValueError(
                f"value for key {key!r} is not JSON-serialisable, so the disk "
                f"tier cannot store it: {exc}"
            ) from exc
        path = self._path(key)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with self._lock:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)

    def get(self, key: str) -> Optional[Any]:
        path = self._path(key)
        with self._lock:
            self.stats.total_requests += 1
            data = self._read(path)
            if data is None:
                self.stats.cache_misses += 1
                return None
            expiry = data.get("expiry")
            if expiry is not None and time.time() > expiry:
                self._unlink(path)
                self.stats.expired_count += 1
                self.stats.cache_misses += 1
                return None
            self.stats.cache_hits += 1
            return data.get("value")

    def ttl(self, key: str) -> Optional[int]:
        """Remaining seconds for ``key``; ``None`` means "no expiry"."""
        data = self._read(self._path(key))
        if not data:
            return None
        expiry = data.get("expiry")
        if expiry is None:
            return None
        return max(0, int(expiry - time.time()))

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._unlink(self._path(key))

    def contains(self, key: str) -> bool:
        path = self._path(key)
        with self._lock:
            data = self._read(path)
            if data is None:
                return False
            expiry = data.get("expiry")
            if expiry is not None and time.time() > expiry:
                self._unlink(path)
                return False
            return True

    def cleanup(self) -> int:
        with self._lock:
            removed = 0
            for name in os.listdir(self.directory):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(self.directory, name)
                data = self._read(path)
                if data is None:
                    self._unlink(path)
                    removed += 1
                    continue
                expiry = data.get("expiry")
                if expiry is not None and time.time() > expiry:
                    self._unlink(path)
                    self.stats.expired_count += 1
                    removed += 1
            return removed

    def clear(self) -> None:
        with self._lock:
            for name in os.listdir(self.directory):
                if name.endswith(".json"):
                    self._unlink(os.path.join(self.directory, name))

    def stats_snapshot(self) -> CacheStats:
        self.stats.current_size = sum(
            1 for n in os.listdir(self.directory) if n.endswith(".json")
        )
        return self.stats

    def close(self) -> None:
        """No open handles are held between calls; present for symmetry."""


class RedisCacheTier:
    """A genuine Redis-backed cache tier.

    Uses ``SETEX``/``SET``/``GET``/``DEL``/``EXISTS``.  The client can be
    injected (``client=``) so the real command path is testable without a
    live server; otherwise ``redis-py`` is imported lazily and the connection
    is verified with ``PING`` at construction.  Failure to reach Redis raises
    :class:`CacheTierUnavailableError` -- never a silent downgrade.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        client: Any = None,
        prefix: str = "liuhao:cache:",
        connect_timeout: float = 1.0,
    ):
        self.prefix = prefix
        self.stats = CacheStats()
        if client is not None:
            self._client = client
            return
        try:
            import redis  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise CacheTierUnavailableError(
                "redis tier requires the 'redis' package (pip install redis)"
            ) from exc
        if not url:
            raise CacheTierUnavailableError(
                "redis tier requires a url when no client is injected"
            )
        try:
            self._client = redis.Redis.from_url(
                url, socket_connect_timeout=connect_timeout, socket_timeout=connect_timeout
            )
            self._client.ping()
        except Exception as exc:  # noqa: BLE001 - any driver error means "unavailable"
            raise CacheTierUnavailableError(
                f"redis unreachable at {_redact_url(url)}: {type(exc).__name__}"
            ) from exc

    def _k(self, key: str) -> str:
        return self.prefix + key

    @staticmethod
    def _decode(raw: Any) -> Any:
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("utf-8")
        return raw

    def put(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            blob = json.dumps(value, ensure_ascii=False)
        except TypeError as exc:
            raise ValueError(
                f"value for key {key!r} is not JSON-serialisable, so the redis "
                f"tier cannot store it: {exc}"
            ) from exc
        if ttl:
            self._client.setex(self._k(key), int(ttl), blob)
        else:
            self._client.set(self._k(key), blob)

    def get(self, key: str) -> Optional[Any]:
        self.stats.total_requests += 1
        raw = self._client.get(self._k(key))
        if raw is None:
            self.stats.cache_misses += 1
            return None
        self.stats.cache_hits += 1
        return json.loads(self._decode(raw))

    def ttl(self, key: str) -> Optional[int]:
        """Remaining seconds; ``None`` when the key has no expiry."""
        try:
            remaining = self._client.ttl(self._k(key))
        except Exception:  # noqa: BLE001 - TTL is advisory
            return None
        if isinstance(remaining, int) and remaining >= 0:
            return remaining
        return None

    def delete(self, key: str) -> bool:
        return bool(self._client.delete(self._k(key)))

    def contains(self, key: str) -> bool:
        return bool(self._client.exists(self._k(key)))

    def cleanup(self) -> int:
        """Redis expires keys itself; nothing to sweep here."""
        return 0

    def clear(self) -> None:
        keys = self._client.keys(self.prefix + "*")
        if keys:
            self._client.delete(*keys)

    def stats_snapshot(self) -> CacheStats:
        try:
            self.stats.current_size = len(self._client.keys(self.prefix + "*"))
        except Exception:  # noqa: BLE001
            self.stats.current_size = 0
        return self.stats

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass


class MultiTierCache:
    """Multi-tier cache with a **real** ``memory -> redis -> disk`` cascade.

    Tiers are opt-in: memory is always on, redis turns on when ``redis_url``
    or ``redis_client`` is supplied, disk turns on when ``disk_dir`` is
    supplied.  Writing to a tier that is not enabled raises
    :class:`CacheTierUnavailableError` instead of silently doing nothing.

    ``get`` walks the cascade and **promotes** a lower-tier hit into memory
    (preserving the remaining TTL).  ``put`` writes only the requested tier,
    which keeps the historical signature and semantics intact.
    """

    def __init__(
        self,
        memory_max_size: int = 1000,
        disk_dir: Optional[str] = None,
        redis_url: Optional[str] = None,
        redis_client: Any = None,
        redis_prefix: str = "liuhao:cache:",
    ):
        self.memory_cache = LRUCache(max_size=memory_max_size)
        self.redis_cache: Optional[RedisCacheTier] = None
        self.disk_cache: Optional[DiskCache] = None
        if redis_url or redis_client is not None:
            self.redis_cache = RedisCacheTier(
                url=redis_url, client=redis_client, prefix=redis_prefix
            )
        if disk_dir:
            self.disk_cache = DiskCache(disk_dir)
        # Construction now really performs setup, so this is honest.
        self.initialized = True

    # -- introspection -----------------------------------------------------
    def available_tiers(self) -> List[str]:
        tiers = [CacheTier.MEMORY]
        if self.redis_cache is not None:
            tiers.append(CacheTier.REDIS)
        if self.disk_cache is not None:
            tiers.append(CacheTier.DISK)
        return tiers

    def _promote(self, tier_obj: Any, key: str, value: Any) -> None:
        ttl = None
        ttl_fn = getattr(tier_obj, "ttl", None)
        if callable(ttl_fn):
            try:
                ttl = ttl_fn(key)
            except Exception:  # noqa: BLE001 - promotion is best-effort
                ttl = None
        self.memory_cache.put(key, value, ttl)

    # -- cascade -----------------------------------------------------------
    def get(self, key: str) -> Optional[Any]:
        value = self.memory_cache.get(key)
        if value is not None:
            return value
        if self.redis_cache is not None:
            value = self.redis_cache.get(key)
            if value is not None:
                self._promote(self.redis_cache, key, value)
                return value
        if self.disk_cache is not None:
            value = self.disk_cache.get(key)
            if value is not None:
                self._promote(self.disk_cache, key, value)
                return value
        return None

    def put(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        tier: str = CacheTier.MEMORY,
    ) -> None:
        if tier == CacheTier.MEMORY:
            self.memory_cache.put(key, value, ttl)
            return
        if tier == CacheTier.REDIS:
            if self.redis_cache is None:
                raise CacheTierUnavailableError(
                    "redis tier is not enabled on this cache; construct "
                    "MultiTierCache(redis_url=...) or pass redis_client=..."
                )
            self.redis_cache.put(key, value, ttl)
            return
        if tier == CacheTier.DISK:
            if self.disk_cache is None:
                raise CacheTierUnavailableError(
                    "disk tier is not enabled on this cache; construct "
                    "MultiTierCache(disk_dir=...)"
                )
            self.disk_cache.put(key, value, ttl)
            return
        raise ValueError(f"unknown cache tier: {tier!r}")

    def delete(self, key: str) -> bool:
        deleted = self.memory_cache.delete(key)
        if self.redis_cache is not None:
            deleted = self.redis_cache.delete(key) or deleted
        if self.disk_cache is not None:
            deleted = self.disk_cache.delete(key) or deleted
        return deleted

    def contains(self, key: str) -> bool:
        if self.memory_cache.contains(key):
            return True
        if self.redis_cache is not None and self.redis_cache.contains(key):
            return True
        if self.disk_cache is not None and self.disk_cache.contains(key):
            return True
        return False

    def clear(self) -> None:
        # Delete in place rather than rebuilding the LRUCache: rebuilding would
        # silently reset the hit/miss counters that stats() reports.
        for key in list(self.memory_cache.cache.keys()):
            self.memory_cache.delete(key)
        if self.redis_cache is not None:
            self.redis_cache.clear()
        if self.disk_cache is not None:
            self.disk_cache.clear()

    def cleanup(self) -> int:
        removed = self.memory_cache.cleanup()
        if self.redis_cache is not None:
            removed += self.redis_cache.cleanup()
        if self.disk_cache is not None:
            removed += self.disk_cache.cleanup()
        return removed

    def stats(self) -> Dict[str, CacheStats]:
        """Per-tier statistics -- each tier reports its own hits/misses."""
        out: Dict[str, CacheStats] = {"memory": self.memory_cache.get_stats()}
        if self.redis_cache is not None:
            out[CacheTier.REDIS] = self.redis_cache.stats_snapshot()
        if self.disk_cache is not None:
            out[CacheTier.DISK] = self.disk_cache.stats_snapshot()
        return out

    def close(self) -> None:
        if self.redis_cache is not None:
            self.redis_cache.close()
        if self.disk_cache is not None:
            self.disk_cache.close()


def cache_tiers_available(
    redis_url: Optional[str] = None, disk_dir: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """Honest capability probe: which cache tiers can actually be used here.

    Returns ``{tier: {"available": bool, "reason": Optional[str]}}``.  Never
    raises -- an unreachable Redis is reported, not thrown.
    """
    out: Dict[str, Dict[str, Any]] = {
        CacheTier.MEMORY: {"available": True, "reason": None}
    }

    if disk_dir:
        try:
            os.makedirs(disk_dir, exist_ok=True)
            probe = os.path.join(disk_dir, ".liuhao_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            out[CacheTier.DISK] = {"available": True, "reason": None}
        except OSError as exc:
            out[CacheTier.DISK] = {
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
    else:
        out[CacheTier.DISK] = {"available": False, "reason": "no disk_dir configured"}

    try:
        import redis  # type: ignore  # noqa: F401
    except ImportError:
        out[CacheTier.REDIS] = {
            "available": False,
            "reason": "redis-py is not installed",
        }
        return out
    if not redis_url:
        out[CacheTier.REDIS] = {
            "available": False,
            "reason": "redis-py installed but no url configured",
        }
        return out
    try:
        client = redis.Redis.from_url(
            redis_url, socket_connect_timeout=1.0, socket_timeout=1.0
        )
        client.ping()
        out[CacheTier.REDIS] = {"available": True, "reason": None}
    except Exception as exc:  # noqa: BLE001
        out[CacheTier.REDIS] = {
            "available": False,
            "reason": f"unreachable at {_redact_url(redis_url)}: {type(exc).__name__}",
        }
    return out


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
