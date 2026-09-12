"""Capability Kernel — Registry + Traceability + Scoping

The Capability Kernel maintains the authoritative registry of all
capabilities across the system, enabling capability lookup, versioning,
traceability, and scope-based access control.

依据 Definition Lock §112: Capability Kernel 必须能够
- Register capabilities with full metadata
- Lookup capabilities by ID + version
- Trace capability → kernel → owner chain
- Check scope permissions (L0-L7)
- Support capability deprecation and migration
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from src._time import utc_now
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from src.kernels._crosscutting import kernel_action


class CapabilityScope(str, Enum):
    """Capability permission scope L0-L7."""
    L0 = "L0"  # Human only
    L1 = "L1"  # Supervised
    L2 = "L2"  # Assisted
    L3 = "L3"  # Collaborative
    L4 = "L4"  # Semi-autonomous
    L5 = "L5"  # Mostly autonomous
    L6 = "L6"  # Highly autonomous
    L7 = "L7"  # Full autonomy


class CapabilityStatus(str, Enum):
    """Capability lifecycle status."""
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"
    EXPERIMENTAL = "experimental"


@dataclass
class CapabilityEntry:
    """Single capability registry entry."""
    id: str
    version: str
    namespace: str
    name: str
    description: str
    scope: CapabilityScope
    owner: str  # kernel or component that owns this capability
    status: CapabilityStatus = CapabilityStatus.ACTIVE
    tags: Set[str] = field(default_factory=set)
    dependencies: List[str] = field(default_factory=list)  # other capability IDs
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    deprecated_at: Optional[datetime] = None
    traceability_chain: List[str] = field(default_factory=list)  # [kernel, capability, owner, ...]

    def __post_init__(self):
        if not self.traceability_chain:
            self.traceability_chain = [self.owner, self.id, f"v{self.version}"]

    @property
    def full_id(self) -> str:
        return f"{self.namespace}.{self.id}@v{self.version}"

    def is_compatible_with(self, other: "CapabilityEntry") -> bool:
        """Check if this capability is compatible with another (same major version)."""
        try:
            self_major = int(self.version.split(".")[0])
            other_major = int(other.version.split(".")[0])
            return self_major == other_major
        except (ValueError, IndexError):
            return False


@dataclass
class CapabilityQuery:
    """Query parameters for capability lookup."""
    id: Optional[str] = None
    namespace: Optional[str] = None
    version: Optional[str] = None
    scope: Optional[CapabilityScope] = None
    status: Optional[CapabilityStatus] = None
    owner: Optional[str] = None
    tags: Optional[Set[str]] = None


@dataclass
class ScopeCheckResult:
    """Result of a scope permission check."""
    allowed: bool
    requested_scope: CapabilityScope
    capability_scope: CapabilityScope
    reason: str
    traceability: List[str]


class CapabilityRegistry:
    """Authoritative capability registry with traceability."""

    def __init__(self):
        self._capabilities: Dict[str, CapabilityEntry] = {}  # key: full_id
        self._by_namespace: Dict[str, Set[str]] = {}  # namespace -> set of full_ids
        self._by_owner: Dict[str, Set[str]] = {}  # owner -> set of full_ids
        self._by_tag: Dict[str, Set[str]] = {}  # tag -> set of full_ids
        self._deprecated_aliases: Dict[str, str] = {}  # old_full_id -> new_full_id

    @kernel_action("capability.register")
    def register(self, capability: CapabilityEntry) -> bool:
        """Register a new capability. Returns True if new, False if updated."""
        key = capability.full_id
        is_new = key not in self._capabilities

        # Update indexes
        self._capabilities[key] = capability

        # Namespace index
        if capability.namespace not in self._by_namespace:
            self._by_namespace[capability.namespace] = set()
        self._by_namespace[capability.namespace].add(key)

        # Owner index
        if capability.owner not in self._by_owner:
            self._by_owner[capability.owner] = set()
        self._by_owner[capability.owner].add(key)

        # Tag indexes
        for tag in capability.tags:
            if tag not in self._by_tag:
                self._by_tag[tag] = set()
            self._by_tag[tag].add(key)

        capability.updated_at = utc_now()
        return is_new

    def lookup(
        self,
        id: str,
        namespace: str = "core",
        version: Optional[str] = None
    ) -> Optional[CapabilityEntry]:
        """Lookup capability by ID, namespace, and optional version."""
        if version:
            key = f"{namespace}.{id}@v{version}"
            return self._capabilities.get(key)

        # Find latest version if not specified
        candidates = [
            cap for cap in self._capabilities.values()
            if cap.id == id and cap.namespace == namespace and cap.status == CapabilityStatus.ACTIVE
        ]
        if not candidates:
            return None

        # Return highest version
        def version_key(cap: CapabilityEntry):
            try:
                parts = cap.version.split(".")
                return tuple(int(p) for p in parts)
            except (ValueError, IndexError):
                return (0,)

        return max(candidates, key=version_key)

    def query(self, query: CapabilityQuery) -> List[CapabilityEntry]:
        """Query capabilities with flexible filters."""
        results = list(self._capabilities.values())

        if query.id:
            results = [c for c in results if c.id == query.id]
        if query.namespace:
            results = [c for c in results if c.namespace == query.namespace]
        if query.version:
            results = [c for c in results if c.version == query.version]
        if query.scope:
            results = [c for c in results if c.scope == query.scope]
        if query.status:
            results = [c for c in results if c.status == query.status]
        if query.owner:
            results = [c for c in results if c.owner == query.owner]
        if query.tags:
            results = [c for c in results if query.tags.issubset(c.tags)]

        return results

    def check_scope(
        self,
        capability_id: str,
        requested_scope: CapabilityScope,
        namespace: str = "core",
        version: Optional[str] = None
    ) -> ScopeCheckResult:
        """Check if requested scope is allowed for capability."""
        cap = self.lookup(capability_id, namespace, version)
        if not cap:
            return ScopeCheckResult(
                allowed=False,
                requested_scope=requested_scope,
                capability_scope=CapabilityScope.L0,
                reason=f"Capability not found: {namespace}.{capability_id}",
                traceability=[]
            )

        # Scope hierarchy: L7 > L6 > ... > L0
        scope_order = {s: i for i, s in enumerate(CapabilityScope)}
        allowed = scope_order[requested_scope] <= scope_order[cap.scope]

        return ScopeCheckResult(
            allowed=allowed,
            requested_scope=requested_scope,
            capability_scope=cap.scope,
            reason="Scope permitted" if allowed else f"Requested {requested_scope} exceeds capability {cap.scope}",
            traceability=cap.traceability_chain
        )

    def get_traceability(self, capability_id: str, namespace: str = "core") -> List[str]:
        """Get full traceability chain for a capability."""
        cap = self.lookup(capability_id, namespace)
        if not cap:
            return []
        return cap.traceability_chain

    @kernel_action("capability.deprecate")
    def deprecate(
        self,
        capability_id: str,
        namespace: str = "core",
        replacement_id: Optional[str] = None,
        replacement_namespace: Optional[str] = None
    ) -> bool:
        """Deprecate a capability, optionally with replacement."""
        cap = self.lookup(capability_id, namespace)
        if not cap:
            return False

        cap.status = CapabilityStatus.DEPRECATED
        cap.deprecated_at = utc_now()
        cap.updated_at = utc_now()

        if replacement_id and replacement_namespace:
            old_key = cap.full_id
            new_key = f"{replacement_namespace}.{replacement_id}@v{self.lookup(replacement_id, replacement_namespace).version if self.lookup(replacement_id, replacement_namespace) else '1.0.0'}"
            self._deprecated_aliases[old_key] = new_key

        return True

    @kernel_action("capability.retire")
    def retire(self, capability_id: str, namespace: str = "core") -> bool:
        """Retire a capability (no longer usable)."""
        cap = self.lookup(capability_id, namespace)
        if not cap:
            return False
        cap.status = CapabilityStatus.RETIRED
        cap.updated_at = utc_now()
        return True

    def get_all_by_owner(self, owner: str) -> List[CapabilityEntry]:
        """Get all capabilities owned by a kernel/component."""
        keys = self._by_owner.get(owner, set())
        return [self._capabilities[k] for k in keys if k in self._capabilities]

    def get_all_by_namespace(self, namespace: str) -> List[CapabilityEntry]:
        """Get all capabilities in a namespace."""
        keys = self._by_namespace.get(namespace, set())
        return [self._capabilities[k] for k in keys if k in self._capabilities]

    def get_all_by_tag(self, tag: str) -> List[CapabilityEntry]:
        """Get all capabilities with a specific tag."""
        keys = self._by_tag.get(tag, set())
        return [self._capabilities[k] for k in keys if k in self._capabilities]

    def resolve_deprecated(self, full_id: str) -> Optional[str]:
        """Resolve deprecated capability to its replacement."""
        return self._deprecated_aliases.get(full_id)

    def count(self) -> int:
        return len(self._capabilities)

    def count_active(self) -> int:
        return sum(1 for c in self._capabilities.values() if c.status == CapabilityStatus.ACTIVE)


# Global registry instance
_global_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    """Get or create the global capability registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = CapabilityRegistry()
        # Register built-in kernel capabilities
        _register_builtin_capabilities(_global_registry)
    return _global_registry


def _register_builtin_capabilities(registry: CapabilityRegistry) -> None:
    """Register the 12 kernel built-in capabilities."""
    builtins = [
        # Context Kernel
        CapabilityEntry(
            id="context_compression",
            version="1.0.0",
            namespace="kernel",
            name="Context Compression",
            description="12 inputs → compression → model-ready context",
            scope=CapabilityScope.L3,
            owner="context_kernel",
            tags={"kernel", "context", "compression"},
        ),
        # Capability Kernel (self)
        CapabilityEntry(
            id="capability_registry",
            version="1.0.0",
            namespace="kernel",
            name="Capability Registry",
            description="Registry + traceability + scoping",
            scope=CapabilityScope.L3,
            owner="capability_kernel",
            tags={"kernel", "registry", "traceability"},
        ),
        # Event Kernel
        CapabilityEntry(
            id="event_bus",
            version="1.0.0",
            namespace="kernel",
            name="Event Bus",
            description="Unified event bus + correlation IDs",
            scope=CapabilityScope.L2,
            owner="event_kernel",
            tags={"kernel", "event", "bus", "correlation"},
        ),
        # Execution Kernel
        CapabilityEntry(
            id="execution_pipeline",
            version="1.0.0",
            namespace="kernel",
            name="Execution Pipeline",
            description="Goal→Task→Plan→Action→Verify",
            scope=CapabilityScope.L3,
            owner="execution_kernel",
            tags={"kernel", "execution", "pipeline"},
        ),
        # Resource Kernel
        CapabilityEntry(
            id="resource_quotas",
            version="1.0.0",
            namespace="kernel",
            name="Resource Quotas",
            description="CPU/Mem/Storage/Token/Time/$ Quotas",
            scope=CapabilityScope.L3,
            owner="resource_kernel",
            tags={"kernel", "resource", "quotas"},
        ),
        # Policy Kernel
        CapabilityEntry(
            id="policy_engine",
            version="1.0.0",
            namespace="kernel",
            name="Policy Engine",
            description="Permission boundaries + policy engine",
            scope=CapabilityScope.L4,
            owner="policy_kernel",
            tags={"kernel", "policy", "permissions"},
        ),
        # Network Kernel
        CapabilityEntry(
            id="network_bus",
            version="1.0.0",
            namespace="kernel",
            name="Network Bus",
            description="Protocol adapters + communication bus",
            scope=CapabilityScope.L3,
            owner="network_kernel",
            tags={"kernel", "network", "protocol"},
        ),
        # Trust Kernel
        CapabilityEntry(
            id="trust_chain",
            version="1.0.0",
            namespace="kernel",
            name="Trust Chain",
            description="Trust scores + chain + revocation",
            scope=CapabilityScope.L4,
            owner="trust_kernel",
            tags={"kernel", "trust", "chain"},
        ),
        # Evaluation Kernel
        CapabilityEntry(
            id="evaluation_engine",
            version="1.0.0",
            namespace="kernel",
            name="Evaluation Engine",
            description="Outcome evaluation + feedback loop",
            scope=CapabilityScope.L3,
            owner="evaluation_kernel",
            tags={"kernel", "evaluation", "feedback"},
        ),
        # Identity Kernel
        CapabilityEntry(
            id="agent_identity",
            version="1.0.0",
            namespace="kernel",
            name="Agent Identity",
            description="Agent identity + permissions",
            scope=CapabilityScope.L4,
            owner="identity_kernel",
            tags={"kernel", "identity", "permissions"},
        ),
        # Memory Kernel
        CapabilityEntry(
            id="multi_tier_memory",
            version="1.0.0",
            namespace="kernel",
            name="Multi-Tier Memory",
            description="Multi-tier memory + scoping",
            scope=CapabilityScope.L3,
            owner="memory_kernel",
            tags={"kernel", "memory", "multi-tier"},
        ),
        # Security Kernel
        CapabilityEntry(
            id="security_enforcement",
            version="1.0.0",
            namespace="kernel",
            name="Security Enforcement",
            description="RBAC + ABAC + Vault + Audit",
            scope=CapabilityScope.L5,
            owner="security_kernel",
            tags={"kernel", "security", "rbac", "abac"},
        ),
    ]

    for cap in builtins:
        registry.register(cap)


# Convenience functions
def register_capability(
    id: str,
    version: str,
    namespace: str,
    name: str,
    description: str,
    scope: CapabilityScope,
    owner: str,
    tags: Optional[Set[str]] = None,
    dependencies: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> CapabilityEntry:
    """Register a capability and return the entry."""
    cap = CapabilityEntry(
        id=id,
        version=version,
        namespace=namespace,
        name=name,
        description=description,
        scope=scope,
        owner=owner,
        tags=tags or set(),
        dependencies=dependencies or [],
        metadata=metadata or {},
    )
    get_capability_registry().register(cap)
    return cap


def lookup_capability(id: str, namespace: str = "kernel", version: Optional[str] = None) -> Optional[CapabilityEntry]:
    """Lookup a capability.

    The convenience default is ``"kernel"`` (not ``"core"``) because the
    12 built-in kernel capabilities are registered under the ``kernel``
    namespace. Using ``"core"`` as the default made builtins
    undiscoverable through the default lookup (Sprint 2 defect).
    """
    return get_capability_registry().lookup(id, namespace, version)


def check_capability_scope(
    capability_id: str,
    requested_scope: CapabilityScope,
    namespace: str = "kernel",
    version: Optional[str] = None
) -> ScopeCheckResult:
    """Check if scope is permitted for capability."""
    return get_capability_registry().check_scope(capability_id, requested_scope, namespace, version)


def get_capability_traceability(capability_id: str, namespace: str = "kernel") -> List[str]:
    """Get capability traceability chain."""
    return get_capability_registry().get_traceability(capability_id, namespace)
