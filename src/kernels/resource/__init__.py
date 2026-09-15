"""Resource Kernel — CPU/Mem/Storage/Token/Time/$ Quotas

The Resource Kernel manages resource quotas and lifecycle across all kernels.
Handles allocation, release, monitoring, and enforcement of resource limits.

依据 Definition Lock §112: Resource Kernel 必须能够
- Define resource types (CPU, Memory, Storage, Token, Time, $)
- Allocate quotas with scope awareness
- Track usage and enforce limits
- Support quota inheritance and delegation
- Provide real-time usage metrics
"""
from __future__ import annotations
from src.kernels._base import KernelLifecycle, KernelStateError

from dataclasses import dataclass, field
from datetime import datetime
from src._time import utc_now
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import logging
import os
import threading
import uuid

from src.kernels._crosscutting import kernel_action

_log = logging.getLogger(__name__)

# The system-wide default COST budget. It is deliberately overridable: the
# point of the cost guard is to have a budget that an operator can tighten,
# and "edit the source" is not an operator interface. The default value is
# unchanged so nothing moves until someone sets the variable.
DEFAULT_SYSTEM_COST_QUOTA = 100.0
COST_QUOTA_ENV = "LIUHAO_DEFAULT_COST_QUOTA"


def default_system_cost_quota() -> float:
    """Read the system-wide default COST budget (env-overridable).

    Malformed or negative values fall back to :data:`DEFAULT_SYSTEM_COST_QUOTA`
    **with a warning** -- silently ignoring a typo'd budget would leave the
    guard running on a number nobody chose.
    """
    raw = os.environ.get(COST_QUOTA_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_SYSTEM_COST_QUOTA
    try:
        value = float(raw.strip())
    except ValueError:
        _log.warning(
            "%s=%r is not a number; falling back to the default COST quota %.1f",
            COST_QUOTA_ENV, raw, DEFAULT_SYSTEM_COST_QUOTA,
        )
        return DEFAULT_SYSTEM_COST_QUOTA
    if value < 0:
        _log.warning(
            "%s=%r is negative; falling back to the default COST quota %.1f",
            COST_QUOTA_ENV, raw, DEFAULT_SYSTEM_COST_QUOTA,
        )
        return DEFAULT_SYSTEM_COST_QUOTA
    return value


class ResourceType(str, Enum):
    """Resource types managed by the kernel."""
    CPU = "cpu"           # CPU cores/percentage
    MEMORY = "memory"     # Memory in bytes
    STORAGE = "storage"   # Disk storage in bytes
    TOKEN = "token"       # LLM token budget
    TIME = "time"         # Time budget in seconds
    COST = "cost"         # Monetary cost in USD


class ResourceScope(str, Enum):
    """Resource quota scope L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


@dataclass
class Quota:
    """Resource quota definition."""
    resource_type: ResourceType
    scope: ResourceScope
    owner: str  # kernel, agent, or component
    limit: float  # Maximum allowed
    used: float = 0.0  # Currently used
    reserved: float = 0.0  # Reserved but not yet used
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def available(self) -> float:
        return max(0.0, self.limit - self.used - self.reserved)

    @property
    def utilization(self) -> float:
        if self.limit <= 0:
            return 0.0
        return (self.used + self.reserved) / self.limit

    @property
    def is_exhausted(self) -> bool:
        return self.available <= 0

    def can_allocate(self, amount: float) -> bool:
        return self.available >= amount


@dataclass
class Allocation:
    """Active resource allocation."""
    id: str
    quota_id: str  # References the quota
    amount: float
    owner: str
    purpose: str
    created_at: datetime = field(default_factory=utc_now)
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: True once ``commit`` has moved this allocation's amount from the quota's
    #: ``reserved`` bucket into ``used``. ``release`` needs it to know which
    #: bucket actually holds the capacity -- see the release() docstring.
    committed: bool = False


@dataclass
class ResourceUsage:
    """Resource usage snapshot."""
    resource_type: ResourceType
    scope: ResourceScope
    owner: str
    limit: float
    used: float
    reserved: float
    available: float
    utilization: float
    allocations: List[Allocation]
    timestamp: datetime = field(default_factory=utc_now)


class ResourceQuotaManager:
    """Manages resource quotas and allocations."""
    lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

    def __init__(self):
        self._quotas: Dict[str, Quota] = {}  # key: {scope}:{owner}:{resource_type}
        self._allocations: Dict[str, Allocation] = {}  # allocation_id -> Allocation
        self._owner_quotas: Dict[str, Set[str]] = {}  # owner -> set of quota_keys
        self._lock = threading.RLock()

        # Initialize default system quotas
        self._init_default_quotas()

    def _quota_key(self, scope: ResourceScope, owner: str, resource_type: ResourceType) -> str:
        return f"{scope.value}:{owner}:{resource_type.value}"

    def _init_default_quotas(self) -> None:
        """Initialize default system quotas."""
        defaults = [
            # System-wide defaults
            (ResourceScope.L3, "system", ResourceType.CPU, 16.0),
            (ResourceScope.L3, "system", ResourceType.MEMORY, 32 * 1024**3),  # 32 GB
            (ResourceScope.L3, "system", ResourceType.STORAGE, 500 * 1024**3),  # 500 GB
            (ResourceScope.L3, "system", ResourceType.TOKEN, 1_000_000),
            (ResourceScope.L3, "system", ResourceType.TIME, 3600.0),  # 1 hour
            (ResourceScope.L3, "system", ResourceType.COST, default_system_cost_quota()),
        ]

        for scope, owner, rtype, limit in defaults:
            self.create_quota(scope, owner, rtype, limit)

    @kernel_action("resource.create_quota")
    def create_quota(
        self,
        scope: ResourceScope,
        owner: str,
        resource_type: ResourceType,
        limit: float,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Quota:
        """Create or update a resource quota."""
        key = self._quota_key(scope, owner, resource_type)

        with self._lock:
            if key in self._quotas:
                quota = self._quotas[key]
                quota.limit = limit
                quota.metadata = metadata or {}
                quota.updated_at = utc_now()
            else:
                quota = Quota(
                    resource_type=resource_type,
                    scope=scope,
                    owner=owner,
                    limit=limit,
                    metadata=metadata or {},
                )
                self._quotas[key] = quota

                # Update owner index
                if owner not in self._owner_quotas:
                    self._owner_quotas[owner] = set()
                self._owner_quotas[owner].add(key)

            return quota

    def get_quota(
        self,
        scope: ResourceScope,
        owner: str,
        resource_type: ResourceType
    ) -> Optional[Quota]:
        """Get quota by scope, owner, and resource type."""
        key = self._quota_key(scope, owner, resource_type)
        with self._lock:
            return self._quotas.get(key)

    @kernel_action("resource.allocate")
    def allocate(
        self,
        resource_type: ResourceType,
        amount: float,
        scope: ResourceScope,
        owner: str,
        purpose: str = "",
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Allocation]:
        """Allocate resources from quota."""
        if amount <= 0:
            return None

        quota = self.get_quota(scope, owner, resource_type)
        if not quota:
            # Try to find parent scope quota (L3 -> L2 -> L1 -> L0)
            quota = self._find_parent_quota(scope, owner, resource_type)
            if not quota:
                return None

        if not quota.can_allocate(amount):
            return None

        with self._lock:
            # Reserve from quota
            quota.reserved += amount
            quota.updated_at = utc_now()

            # Create allocation.
            #
            # ``quota_id`` must point at the quota that was actually charged:
            # ``_find_parent_quota`` above may have returned a quota living in a
            # PARENT scope. Using the requested scope's key here produced an id
            # that no quota answers to, so commit()/release() could never resolve
            # it and the parent quota's ``reserved`` leaked forever.
            allocation = Allocation(
                id=str(uuid.uuid4())[:8],
                quota_id=self._quota_key(quota.scope, quota.owner, quota.resource_type),
                amount=amount,
                owner=owner,
                purpose=purpose,
                expires_at=expires_at,
                metadata=metadata or {},
            )
            self._allocations[allocation.id] = allocation

            return allocation

    def _find_parent_quota(
        self,
        scope: ResourceScope,
        owner: str,
        resource_type: ResourceType
    ) -> Optional[Quota]:
        """Find quota in parent scope (higher number = more autonomy)."""
        scope_order = [ResourceScope.L0, ResourceScope.L1, ResourceScope.L2,
                       ResourceScope.L3, ResourceScope.L4, ResourceScope.L5,
                       ResourceScope.L6, ResourceScope.L7]
        try:
            idx = scope_order.index(scope)
            # Look in higher scopes (more autonomy)
            for i in range(idx + 1, len(scope_order)):
                parent_scope = scope_order[i]
                quota = self.get_quota(parent_scope, owner, resource_type)
                if quota and quota.can_allocate(0.001):  # Has some available
                    return quota
        except ValueError:
            pass
        return None

    @kernel_action("resource.commit")
    def commit(self, allocation_id: str) -> bool:
        """Commit a reserved allocation (move from reserved to used)."""
        with self._lock:
            allocation = self._allocations.get(allocation_id)
            if not allocation:
                return False

            quota = self._quotas.get(allocation.quota_id)
            if not quota:
                return False

            # Explicit idempotency: an already-committed allocation must not
            # charge ``used`` a second time.
            if allocation.committed:
                return False

            # Move from reserved to used
            if quota.reserved < allocation.amount:
                return False

            quota.reserved -= allocation.amount
            quota.used += allocation.amount
            quota.updated_at = utc_now()
            allocation.committed = True

            return True

    @kernel_action("resource.account_spend")
    def account_spend(
        self,
        resource_type: ResourceType,
        amount: float,
        owner: str,
    ) -> int:
        """Record an *already incurred* spend against ``owner``'s quotas.

        Deliberately narrower than ``allocate`` / ``commit``: it only ever moves
        ``used`` upwards, so it can never grant capacity. ``resource.release``
        -- which moves ``used`` *downwards* and therefore *grants* capacity --
        is already pre-approved for the internal service, so this is the more
        conservative direction of the two.

        It exists because the cost guard reads ``available``: with no caller
        recording spend, ``used`` stays at 0 forever and the guard only bites
        when a quota is set artificially tight (see
        ``docs/QUOTA-ACCOUNTING-DESIGN.md``).

        Returns the number of quotas charged (0 when none matched), and never
        raises -- bookkeeping must not be able to break the turn that incurred it.
        """
        if amount <= 0:
            return 0

        try:
            quotas = [
                q
                for q in self.get_all_quotas(owner)
                if q.resource_type == resource_type
            ]
        except Exception:  # pragma: no cover - defensive
            return 0

        if not quotas:
            return 0

        with self._lock:
            for quota in quotas:
                quota.used += amount
                quota.updated_at = utc_now()
        return len(quotas)

    @kernel_action("resource.release")
    def release(
        self,
        resource_type: ResourceType,
        amount: float,
        scope: ResourceScope,
        owner: str,
        allocation_id: Optional[str] = None
    ) -> bool:
        """Release resources back to quota.

        With an ``allocation_id`` the capacity must be returned from whichever
        bucket actually holds it: ``allocate`` puts it in ``reserved`` and only
        ``commit`` moves it to ``used``. The old code always decremented
        ``used``, so releasing an un-committed reservation changed nothing --
        yet it still returned ``True``, silently reporting a release that never
        happened and leaking ``reserved`` forever. A release that frees nothing
        now reports ``False`` so callers can tell the difference.
        """
        with self._lock:
            if allocation_id:
                allocation = self._allocations.get(allocation_id)
                if not allocation:
                    return False

                quota = self._quotas.get(allocation.quota_id)
                if not quota:
                    return False

                # Release from the bucket that actually holds the capacity.
                if allocation.committed:
                    release_amount = min(amount, quota.used)
                    if release_amount <= 0:
                        return False
                    quota.used -= release_amount
                else:
                    release_amount = min(amount, quota.reserved)
                    if release_amount <= 0:
                        return False
                    quota.reserved -= release_amount
                quota.updated_at = utc_now()

                # Remove allocation if fully released
                if allocation.amount <= release_amount:
                    del self._allocations[allocation_id]
                else:
                    allocation.amount -= release_amount

                return True

            # Release without specific allocation - find matching
            quota = self.get_quota(scope, owner, resource_type)
            if not quota:
                return False

            release_amount = min(amount, quota.used)
            if release_amount <= 0:
                # Nothing to release: report it honestly rather than dressing a
                # no-op up as success.
                return False
            quota.used -= release_amount
            quota.updated_at = utc_now()

            return True

    def get_usage(
        self,
        scope: Optional[ResourceScope] = None,
        owner: Optional[str] = None,
        resource_type: Optional[ResourceType] = None
    ) -> List[ResourceUsage]:
        """Get resource usage with optional filters."""
        with self._lock:
            results = []

            for quota in self._quotas.values():
                if scope and quota.scope != scope:
                    continue
                if owner and quota.owner != owner:
                    continue
                if resource_type and quota.resource_type != resource_type:
                    continue

                # Find allocations for this quota
                quota_allocations = [
                    a for a in self._allocations.values()
                    if a.quota_id == self._quota_key(quota.scope, quota.owner, quota.resource_type)
                ]

                usage = ResourceUsage(
                    resource_type=quota.resource_type,
                    scope=quota.scope,
                    owner=quota.owner,
                    limit=quota.limit,
                    used=quota.used,
                    reserved=quota.reserved,
                    available=quota.available,
                    utilization=quota.utilization,
                    allocations=quota_allocations,
                )
                results.append(usage)

            return results

    def get_all_quotas(self, owner: Optional[str] = None) -> List[Quota]:
        """Get all quotas, optionally filtered by owner."""
        with self._lock:
            if owner:
                keys = self._owner_quotas.get(owner, set())
                return [self._quotas[k] for k in keys if k in self._quotas]
            return list(self._quotas.values())

    def get_allocation(self, allocation_id: str) -> Optional[Allocation]:
        """Get allocation by ID."""
        with self._lock:
            return self._allocations.get(allocation_id)

    def get_allocations_by_owner(self, owner: str) -> List[Allocation]:
        """Get all allocations for an owner."""
        with self._lock:
            return [a for a in self._allocations.values() if a.owner == owner]

    def check_availability(
        self,
        resource_type: ResourceType,
        amount: float,
        scope: ResourceScope,
        owner: str
    ) -> bool:
        """Check if resources are available without allocating."""
        quota = self.get_quota(scope, owner, resource_type)
        if not quota:
            quota = self._find_parent_quota(scope, owner, resource_type)
        return quota is not None and quota.can_allocate(amount)

    def enforce_limits(self) -> List[str]:
        """Check for quota violations and return list of violated quota keys."""
        with self._lock:
            violations = []
            for key, quota in self._quotas.items():
                if quota.used > quota.limit:
                    violations.append(key)
            return violations

    def stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        with self._lock:
            total_quotas = len(self._quotas)
            total_allocations = len(self._allocations)
            by_type = {}
            for quota in self._quotas.values():
                t = quota.resource_type.value
                if t not in by_type:
                    by_type[t] = {"count": 0, "total_limit": 0, "total_used": 0}
                by_type[t]["count"] += 1
                by_type[t]["total_limit"] += quota.limit
                by_type[t]["total_used"] += quota.used

            return {
                "total_quotas": total_quotas,
                "total_allocations": total_allocations,
                "by_type": by_type,
                "violations": len(self.enforce_limits()),
            }

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


# Global resource manager instance
_global_manager: Optional[ResourceQuotaManager] = None
_global_lock = threading.Lock()


def get_resource_manager() -> ResourceQuotaManager:
    """Get or create the global resource manager."""
    global _global_manager
    if _global_manager is None:
        with _global_lock:
            if _global_manager is None:
                _global_manager = ResourceQuotaManager()
                _global_manager.initialize()  # 存在即 READY：构造完成即视为就绪
    return _global_manager


# Convenience functions
def create_quota(
    scope: ResourceScope,
    owner: str,
    resource_type: ResourceType,
    limit: float,
    metadata: Optional[Dict[str, Any]] = None
) -> Quota:
    """Create a resource quota."""
    return get_resource_manager().create_quota(scope, owner, resource_type, limit, metadata)


def allocate_resource(
    resource_type: ResourceType,
    amount: float,
    scope: ResourceScope,
    owner: str,
    purpose: str = "",
    expires_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[Allocation]:
    """Allocate resources."""
    return get_resource_manager().allocate(
        resource_type, amount, scope, owner, purpose, expires_at, metadata
    )


def commit_allocation(allocation_id: str) -> bool:
    """Commit a reserved allocation."""
    return get_resource_manager().commit(allocation_id)


def release_resource(
    resource_type: ResourceType,
    amount: float,
    scope: ResourceScope,
    owner: str,
    allocation_id: Optional[str] = None
) -> bool:
    """Release resources."""
    return get_resource_manager().release(resource_type, amount, scope, owner, allocation_id)


def get_resource_usage(
    scope: Optional[ResourceScope] = None,
    owner: Optional[str] = None,
    resource_type: Optional[ResourceType] = None
) -> List[ResourceUsage]:
    """Get resource usage."""
    return get_resource_manager().get_usage(scope, owner, resource_type)


def check_resource_availability(
    resource_type: ResourceType,
    amount: float,
    scope: ResourceScope,
    owner: str
) -> bool:
    """Check resource availability."""
    return get_resource_manager().check_availability(resource_type, amount, scope, owner)
