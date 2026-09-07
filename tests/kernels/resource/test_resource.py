"""Resource Kernel unit tests.

Covers: default system quotas, quota creation/lookup, quota properties
(available/utilization/exhausted/can_allocate), allocate (reserve),
rejection of zero/over-limit allocations, commit (reserved->used),
release, availability checks, limit enforcement, usage filtering, and
stats.
"""
import pytest

from src.kernels.resource import (
    Allocation,
    Quota,
    ResourceQuotaManager,
    ResourceScope,
    ResourceType,
)


@pytest.fixture
def mgr() -> ResourceQuotaManager:
    return ResourceQuotaManager()


# =====================================================================
# Quota properties
# =====================================================================

class TestQuotaProperties:
    def test_available_and_utilization(self):
        q = Quota(
            resource_type=ResourceType.CPU, scope=ResourceScope.L3,
            owner="o", limit=100, used=30, reserved=20,
        )
        assert q.available == 50
        assert q.utilization == 0.5
        assert q.is_exhausted is False
        assert q.can_allocate(50) is True
        assert q.can_allocate(51) is False

    def test_exhausted_when_used_equals_limit(self):
        q = Quota(
            resource_type=ResourceType.CPU, scope=ResourceScope.L3,
            owner="o", limit=10, used=10,
        )
        assert q.is_exhausted is True


# =====================================================================
# Default quotas & create/get
# =====================================================================

class TestQuotas:
    def test_default_system_quotas(self, mgr):
        quotas = mgr.get_all_quotas(owner="system")
        assert len(quotas) == 6

    def test_create_and_get_quota(self, mgr):
        q = mgr.create_quota(ResourceScope.L2, "agent1", ResourceType.TOKEN, 500)
        assert q.limit == 500
        got = mgr.get_quota(ResourceScope.L2, "agent1", ResourceType.TOKEN)
        assert got is q


# =====================================================================
# Allocation
# =====================================================================

class TestAllocate:
    def test_allocate_reserves(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 100)
        alloc = mgr.allocate(ResourceType.TOKEN, 40, ResourceScope.L3, "a", purpose="t")
        assert isinstance(alloc, Allocation)
        assert alloc.amount == 40
        quota = mgr.get_quota(ResourceScope.L3, "a", ResourceType.TOKEN)
        assert quota.reserved == 40
        assert quota.used == 0

    def test_allocate_zero_returns_none(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 100)
        assert mgr.allocate(ResourceType.TOKEN, 0, ResourceScope.L3, "a") is None

    def test_allocate_exceeds_limit_returns_none(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 10)
        assert mgr.allocate(ResourceType.TOKEN, 20, ResourceScope.L3, "a") is None

    def test_commit_moves_reserved_to_used(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 100)
        alloc = mgr.allocate(ResourceType.TOKEN, 40, ResourceScope.L3, "a")
        assert mgr.commit(alloc.id) is True
        quota = mgr.get_quota(ResourceScope.L3, "a", ResourceType.TOKEN)
        assert quota.used == 40
        assert quota.reserved == 0
        # Committing again fails (already moved).
        assert mgr.commit(alloc.id) is False

    def test_release(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 100)
        alloc = mgr.allocate(ResourceType.TOKEN, 40, ResourceScope.L3, "a")
        mgr.commit(alloc.id)
        assert mgr.release(
            ResourceType.TOKEN, 40, ResourceScope.L3, "a", allocation_id=alloc.id
        ) is True
        quota = mgr.get_quota(ResourceScope.L3, "a", ResourceType.TOKEN)
        assert quota.used == 0
        assert mgr.get_allocation(alloc.id) is None


# =====================================================================
# Availability & enforcement
# =====================================================================

class TestAvailability:
    def test_check_availability(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 100)
        assert mgr.check_availability(ResourceType.TOKEN, 50, ResourceScope.L3, "a") is True
        assert mgr.check_availability(ResourceType.TOKEN, 150, ResourceScope.L3, "a") is False

    def test_enforce_limits_no_violation(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 100)
        assert mgr.enforce_limits() == []


# =====================================================================
# Usage & stats
# =====================================================================

class TestUsage:
    def test_get_usage_filter(self, mgr):
        mgr.create_quota(ResourceScope.L3, "a", ResourceType.TOKEN, 100)
        usages = mgr.get_usage(owner="a")
        assert len(usages) == 1
        assert usages[0].limit == 100
        assert usages[0].available == 100

    def test_stats(self, mgr):
        s = mgr.stats()
        assert s["total_quotas"] == 6
        assert "token" in s["by_type"]
        assert s["violations"] == 0
