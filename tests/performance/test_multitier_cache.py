"""Phase 8 — 多层缓存诚实化（``src/performance/cache.py``）测试。

覆盖 ``docs/CACHE-LAYER-DESIGN.md`` §2.2 的七类断言：

A) disk 层**真机**验证（真写文件 → 真读回 → 真过期 → 真删除 → 真清理）
B) 级联 ``memory -> redis -> disk`` + 命中低层后**回填 memory**
C) 诚实失败：未启用的层写时必抛；redis 连不上必抛且原因可操作、不泄口令
D) redis 层**真实命令路径**（注入 client，断言 SETEX/SET/GET/DEL/EXISTS 参数）
E) stats 真实性：每层各自计数（修 D4）
F) **反橡皮图章元验证**：逐字复刻旧 ``put``，证明它对 ``tier=REDIS`` 真的什么都没做
G) 零回归：``LRUCache`` / ``BatchCache`` 既有语义不变

外加一个**宽松的挂起守卫**（只拦数量级退化，不是性能 SLA；见设计 §2.3 的诚实前提）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

import pytest

from src.performance.cache import (
    BatchCache,
    CacheTierUnavailableError,
    DiskCache,
    LRUCache,
    MultiTierCache,
    RedisCacheTier,
    cache_tiers_available,
    create_lru_cache,
)
from src.performance.cache_models import CacheTier


# ============================================================
# 测试替身：FakeRedis（模拟 redis-py 的返回值形状与命令名）
# ============================================================
class FakeRedis:
    """A minimal redis-py stand-in that records every command.

    Live Redis is unreachable on this machine, so the *real* ``RedisCacheTier``
    code path is exercised by injecting this client — we assert on the commands
    the tier issues, which is what a live server would receive.
    """

    def __init__(self) -> None:
        self.store: dict = {}
        self.calls: list = []

    # -- writes ------------------------------------------------------------
    def setex(self, key, ttl, value):
        self.calls.append(("setex", key, int(ttl), value))
        self.store[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    def set(self, key, value):
        self.calls.append(("set", key, value))
        self.store[key] = value.encode("utf-8") if isinstance(value, str) else value
        return True

    # -- reads -------------------------------------------------------------
    def get(self, key):
        return self.store.get(key)

    def exists(self, key):
        return 1 if key in self.store else 0

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def ttl(self, key):
        return 123 if key in self.store else -2

    def delete(self, *keys):
        removed = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                removed += 1
        self.calls.append(("delete", tuple(keys)))
        return removed

    def ping(self):
        return True

    def close(self):
        pass


def _redis_tier(client=None, **kw) -> RedisCacheTier:
    if client is not None:
        return RedisCacheTier(client=client, **kw)
    return RedisCacheTier(url="redis://127.0.0.1:6399/0", **kw)


def _force_expire_disk_entry(disk: DiskCache, key: str) -> None:
    """Rewrite the stored JSON with a past expiry — tests the TTL branch in
    milliseconds instead of sleeping a real second."""
    path = disk._path(key)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["expiry"] = time.time() - 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ============================================================
# A) DiskCache 真机验证
# ============================================================
class TestDiskTierForReal:
    def test_roundtrip_writes_a_real_file(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("alpha", {"n": 1})
        assert disk.get("alpha") == {"n": 1}
        files = [p for p in tmp_path.iterdir() if p.suffix == ".json"]
        assert len(files) == 1, "disk tier must actually create a file on disk"

    def test_filename_is_sha256_of_key(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("alpha", 1)
        expected = hashlib.sha256(b"alpha").hexdigest() + ".json"
        assert (tmp_path / expected).is_file()

    def test_get_of_absent_key_is_none_not_crash(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        assert disk.get("nope") is None

    def test_expiry_removes_the_entry_on_read(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("alpha", "v")
        _force_expire_disk_entry(disk, "alpha")
        assert disk.get("alpha") is None
        assert not os.path.exists(disk._path("alpha")), "expired entry must be swept on read"
        assert disk.stats.expired_count >= 1

    def test_contains_respects_expiry(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("alpha", "v")
        assert disk.contains("alpha") is True
        _force_expire_disk_entry(disk, "alpha")
        assert disk.contains("alpha") is False

    def test_ttl_reports_remaining_seconds(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("alpha", "v", ttl=60)
        remaining = disk.ttl("alpha")
        assert remaining is not None and 0 < remaining <= 60
        disk.put("beta", "v")  # no ttl
        assert disk.ttl("beta") is None

    def test_delete_and_clear(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("a", 1)
        disk.put("b", 2)
        assert disk.delete("a") is True
        assert disk.delete("a") is False  # already gone
        disk.clear()
        assert list(tmp_path.glob("*.json")) == []

    def test_cleanup_removes_only_expired(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("fresh", 1)
        disk.put("stale", 2)
        _force_expire_disk_entry(disk, "stale")
        assert disk.cleanup() == 1
        assert disk.get("fresh") == 1
        assert disk.get("stale") is None

    def test_corrupt_file_is_a_miss_not_a_crash(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("alpha", 1)
        with open(disk._path("alpha"), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        assert disk.get("alpha") is None

    def test_non_serialisable_value_is_refused_loudly(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        with pytest.raises(ValueError) as excinfo:
            disk.put("alpha", object())
        assert "not JSON-serialisable" in str(excinfo.value)

    def test_stats_snapshot_counts_real_files(self, tmp_path):
        disk = DiskCache(str(tmp_path))
        disk.put("a", 1)
        disk.put("b", 2)
        assert disk.stats_snapshot().current_size == 2


# ============================================================
# B) 级联 + 回填（promote）
# ============================================================
class TestCascadeAndPromotion:
    def test_disk_hit_promotes_into_memory(self, tmp_path):
        cache = MultiTierCache(disk_dir=str(tmp_path))
        cache.put("k", "from-disk", tier=CacheTier.DISK)
        assert "k" not in cache.memory_cache.cache  # memory cold
        assert cache.get("k") == "from-disk"
        assert "k" in cache.memory_cache.cache, "lower-tier hit must be promoted"

    def test_promotion_preserves_remaining_ttl(self, tmp_path):
        cache = MultiTierCache(disk_dir=str(tmp_path))
        cache.put("k", "v", ttl=120, tier=CacheTier.DISK)
        cache.get("k")
        promoted = cache.memory_cache.cache["k"]
        assert promoted.ttl is not None and 0 < promoted.ttl <= 120

    def test_memory_hit_short_circuits_lower_tiers(self, tmp_path):
        cache = MultiTierCache(disk_dir=str(tmp_path))
        cache.put("k", "offline-version", tier=CacheTier.DISK)
        cache.put("k", "hot-version")  # memory
        assert cache.get("k") == "hot-version"

    def test_total_miss_returns_none(self, tmp_path):
        cache = MultiTierCache(disk_dir=str(tmp_path))
        assert cache.get("absent") is None

    def test_delete_fans_out_to_all_enabled_tiers(self, tmp_path):
        cache = MultiTierCache(disk_dir=str(tmp_path))
        cache.put("k", "v")
        cache.put("k", "v", tier=CacheTier.DISK)
        assert cache.delete("k") is True
        assert cache.contains("k") is False

    def test_available_tiers_reflects_configuration(self, tmp_path):
        assert MultiTierCache().available_tiers() == [CacheTier.MEMORY]
        assert set(MultiTierCache(disk_dir=str(tmp_path)).available_tiers()) == {
            CacheTier.MEMORY,
            CacheTier.DISK,
        }
        with_redis = MultiTierCache(redis_client=FakeRedis())
        assert set(with_redis.available_tiers()) == {CacheTier.MEMORY, CacheTier.REDIS}


# ============================================================
# C) 诚实失败（修 D2/D3/D5 的核心）
# ============================================================
class TestHonestFailure:
    def test_put_redis_without_enabling_it_raises(self):
        cache = MultiTierCache()
        with pytest.raises(CacheTierUnavailableError) as excinfo:
            cache.put("k", "v", tier=CacheTier.REDIS)
        assert "redis_url" in str(excinfo.value), "error must say how to enable the tier"

    def test_put_disk_without_enabling_it_raises(self):
        cache = MultiTierCache()
        with pytest.raises(CacheTierUnavailableError) as excinfo:
            cache.put("k", "v", tier=CacheTier.DISK)
        assert "disk_dir" in str(excinfo.value)

    def test_unknown_tier_is_a_value_error(self):
        cache = MultiTierCache()
        with pytest.raises(ValueError):
            cache.put("k", "v", tier="tape-drive")

    def test_redis_without_url_or_client_raises(self):
        with pytest.raises(CacheTierUnavailableError) as excinfo:
            RedisCacheTier()
        assert "url" in str(excinfo.value)

    def test_unreachable_redis_raises_with_redacted_url(self):
        with pytest.raises(CacheTierUnavailableError) as excinfo:
            RedisCacheTier(url="redis://user:supersecret@127.0.0.1:6399/0")
        message = str(excinfo.value)
        assert "supersecret" not in message, "DSN password must never leak"
        assert "127.0.0.1:6399" in message

    def test_multitier_construction_with_unreachable_redis_raises(self):
        with pytest.raises(CacheTierUnavailableError):
            MultiTierCache(redis_url="redis://127.0.0.1:6399/0")

    def test_retrieval_after_failed_write_does_not_look_like_a_hit(self):
        """The whole point: a write that could not happen must not read back as present."""
        cache = MultiTierCache()
        with pytest.raises(CacheTierUnavailableError):
            cache.put("k", "v", tier=CacheTier.DISK)
        assert cache.get("k") is None
        assert cache.contains("k") is False


# ============================================================
# D) redis 层真实命令路径（注入 client）
# ============================================================
class TestRedisRealPath:
    def test_put_with_ttl_issues_setex_with_ttl(self):
        fake = FakeRedis()
        tier = _redis_tier(fake, prefix="test:")
        tier.put("k", {"a": 1}, ttl=45)
        kind, key, ttl, value = fake.calls[-1]
        assert (kind, key, ttl) == ("setex", "test:k", 45)
        assert json.loads(value) == {"a": 1}

    def test_put_without_ttl_issues_plain_set(self):
        fake = FakeRedis()
        tier = _redis_tier(fake)
        tier.put("k", "v")
        assert fake.calls[-1][0] == "set"

    def test_get_decodes_bytes_from_the_driver(self):
        fake = FakeRedis()
        tier = _redis_tier(fake)
        tier.put("k", {"n": [1, 2]})
        assert tier.get("k") == {"n": [1, 2]}  # stored as bytes, decoded on read

    def test_get_of_missing_key_counts_a_miss(self):
        tier = _redis_tier(FakeRedis())
        assert tier.get("absent") is None
        assert tier.stats.cache_misses == 1

    def test_delete_and_exists_use_the_prefixed_key(self):
        fake = FakeRedis()
        tier = _redis_tier(fake, prefix="p:")
        tier.put("k", "v")
        assert tier.contains("k") is True
        assert tier.delete("k") is True
        assert tier.contains("k") is False

    def test_multitier_with_injected_redis_cascades(self):
        fake = FakeRedis()
        cache = MultiTierCache(redis_client=fake)
        cache.put("k", "redis-value", tier=CacheTier.REDIS)
        assert "k" not in cache.memory_cache.cache
        assert cache.get("k") == "redis-value"
        assert "k" in cache.memory_cache.cache, "redis hit must promote to memory"

    def test_cleanup_is_a_noop_because_redis_expires_itself(self):
        assert _redis_tier(FakeRedis()).cleanup() == 0


# ============================================================
# E) stats 真实性（修 D4）
# ============================================================
class TestStatsHonesty:
    def test_stats_reports_one_entry_per_enabled_tier(self, tmp_path):
        cache = MultiTierCache(disk_dir=str(tmp_path), redis_client=FakeRedis())
        assert set(cache.stats().keys()) == {"memory", CacheTier.REDIS, CacheTier.DISK}

    def test_stats_omits_tiers_that_are_not_enabled(self):
        assert set(MultiTierCache().stats().keys()) == {"memory"}

    def test_disk_stats_count_its_own_hits_and_misses(self, tmp_path):
        cache = MultiTierCache(disk_dir=str(tmp_path))
        cache.put("k", "v", tier=CacheTier.DISK)
        cache.disk_cache.get("k")   # hit
        cache.disk_cache.get("no")  # miss
        stats = cache.stats()[CacheTier.DISK]
        assert stats.cache_hits == 1
        assert stats.cache_misses == 1

    def test_memory_stats_survive_clear(self):
        """``clear()`` must not rebuild the LRUCache — that would silently reset counters."""
        cache = MultiTierCache()
        cache.put("k", "v")
        cache.get("k")
        before = cache.stats()["memory"].total_requests
        cache.clear()
        assert cache.stats()["memory"].total_requests == before

    def test_memory_tier_reports_current_size(self):
        cache = MultiTierCache()
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.stats()["memory"].current_size == 2


# ============================================================
# F) 反橡皮图章元验证：证明旧实现确实在说谎
# ============================================================
class _OldMultiTierPut:
    """Phase 8 之前 ``MultiTierCache.put`` 的语义，**逐字复刻**。

    旧实现只有 ``if tier == MEMORY:`` 分支、**没有 else**，注释是
    ``# TODO: Add Redis and Disk tier support``（见 docs/CACHE-LAYER-DESIGN.md D2）。
    这里保留它，是为了能**双向对照**新旧行为，而不是只断言"新代码能跑"。
    """

    def __init__(self) -> None:
        self.memory_cache = LRUCache()

    def put(self, key, value, ttl=None, tier=CacheTier.MEMORY):
        if tier == CacheTier.MEMORY:
            self.memory_cache.put(key, value, ttl)
        # 旧代码到此结束 —— 没有 else，REDIS/DISK 被静默丢弃


class TestAntiRubberStamp:
    def test_old_put_silently_dropped_redis_and_disk_writes(self):
        old = _OldMultiTierPut()
        old.put("k", "v", tier=CacheTier.REDIS)   # 不抛异常、不报错
        old.put("k2", "v", tier=CacheTier.DISK)   # 同上
        assert old.memory_cache.get("k") is None
        assert old.memory_cache.get("k2") is None
        # 旧实现在"写入"之后，任何一层都查不到它 —— 这正是被修掉的谎报

    def test_new_put_raises_on_the_very_same_input(self):
        cache = MultiTierCache()
        with pytest.raises(CacheTierUnavailableError):
            cache.put("k", "v", tier=CacheTier.REDIS)
        with pytest.raises(CacheTierUnavailableError):
            cache.put("k2", "v", tier=CacheTier.DISK)

    def test_old_get_never_looked_past_memory(self, tmp_path):
        """旧 ``get`` 在 memory 未命中时直接 return None（注释 `# TODO: Try Redis, then Disk`）。"""
        disk = DiskCache(str(tmp_path))
        disk.put("k", "on-disk")
        # 旧行为的等价复刻：只查 memory
        old_like = LRUCache()
        assert old_like.get("k") is None
        # 新行为：级联到底层并回填
        cache = MultiTierCache(disk_dir=str(tmp_path))
        assert cache.get("k") == "on-disk"


# ============================================================
# G) 零回归：LRUCache / BatchCache 语义不变
# ============================================================
class TestExistingCacheUnchanged:
    def test_lru_eviction_order_unchanged(self):
        cache = LRUCache(max_size=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")          # 'a' becomes most-recently-used
        cache.put("c", 3)       # evicts 'b'
        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3

    def test_lru_ttl_expiry_unchanged(self):
        cache = LRUCache()
        cache.put("a", 1, ttl=60)
        entry = cache.cache["a"]
        assert entry.expiry is not None
        entry.expiry = time.time() - 1  # force expiry without sleeping
        assert cache.get("a") is None
        assert cache.stats.expired_count == 1

    def test_batch_cache_roundtrip_unchanged(self):
        cache = LRUCache()
        batch = BatchCache(cache)
        batch.put_batch({"a": 1, "b": 2})
        assert batch.get_batch(["a", "b", "c"]) == {"a": 1, "b": 2, "c": None}

    def test_batch_put_batch_does_not_deadlock(self):
        """回归：``put_batch`` 持锁后调用 ``put``（会再次取同一把锁）。

        旧实现里 ``LRUCache._lock`` 是非重入的 ``threading.Lock`` ⇒
        ``put_batch`` **每次调用都死锁**。``BatchCache`` 零调用方、零测试，
        所以这个缺陷一直没被发现（Phase 8 写测试时才撞上）。

        这里用「子线程 + join 超时」把"再次死锁"变成**失败**而不是挂起整个
        CI —— 一个会挂死的测试比没有测试更糟。
        """
        cache = LRUCache()
        batch = BatchCache(cache)
        finished = []
        worker = threading.Thread(
            target=lambda: (batch.put_batch({"a": 1, "b": 2}), finished.append(True)),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=5)
        assert finished == [True], "BatchCache.put_batch deadlocked again (non-reentrant lock)"
        assert batch.get_batch(["a", "b"]) == {"a": 1, "b": 2}

    def test_create_lru_cache_helper_unchanged(self):
        cache = create_lru_cache(max_size=4)
        assert cache.max_size == 4

    def test_multitier_memory_only_default_semantics_unchanged(self):
        cache = MultiTierCache(memory_max_size=8)
        cache.put("k", "v")            # default tier=MEMORY, positional ttl still 3rd
        assert cache.get("k") == "v"
        assert cache.memory_cache.max_size == 8
        assert cache.initialized is True


# ============================================================
# H) 能力探针：如实报告、永不抛
# ============================================================
class TestCapabilityProbe:
    def test_disk_reported_unavailable_when_unconfigured(self):
        report = cache_tiers_available()
        assert report[CacheTier.DISK]["available"] is False
        assert "disk_dir" in report[CacheTier.DISK]["reason"]

    def test_disk_reported_available_for_a_writable_dir(self, tmp_path):
        report = cache_tiers_available(disk_dir=str(tmp_path))
        assert report[CacheTier.DISK]["available"] is True

    def test_memory_always_available(self):
        assert cache_tiers_available()[CacheTier.MEMORY]["available"] is True

    def test_unreachable_redis_is_reported_not_raised(self):
        report = cache_tiers_available(redis_url="redis://user:pw@127.0.0.1:6399/0")
        redis_report = report[CacheTier.REDIS]
        assert redis_report["available"] is False
        assert "pw" not in (redis_report["reason"] or ""), "password must not leak into the probe"


# ============================================================
# I) 挂起守卫（只拦数量级退化，不是 SLA）
# ============================================================
class TestNoCatastrophicRegression:
    def test_memory_path_is_not_order_of_magnitude_slow(self):
        """A hang/catastrophic guard, deliberately loose.

        This is NOT a performance SLA: it only fails if the memory path becomes
        hundreds of times slower (e.g. an accidental sleep or O(n^2) trim).
        """
        cache = LRUCache(max_size=1000)
        n = 20_000
        started = time.perf_counter()
        for i in range(n):
            cache.put(f"k{i % 500}", i)
            cache.get(f"k{i % 500}")
        elapsed = time.perf_counter() - started
        assert elapsed < 10.0, f"memory cache path took {elapsed:.2f}s for {n} ops"

    def test_multitier_memory_hit_path_stays_fast(self):
        cache = MultiTierCache()
        n = 10_000
        cache.put("hot", "v")
        started = time.perf_counter()
        for _ in range(n):
            cache.get("hot")
        elapsed = time.perf_counter() - started
        assert elapsed < 10.0, f"multi-tier hot path took {elapsed:.2f}s for {n} hits"
