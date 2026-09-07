"""Identity Kernel — Agent Identity + Permissions

The Identity Kernel manages agent identities, principals, and permissions.
Provides the single Identity Authority for the system.

依据 Definition Lock §112: Identity Kernel 必须能够
- Create and manage agent identities
- Grant and revoke permissions
- Audit identity operations
- Support scope-aware permissions (L0-L7)
- Maintain identity traceability
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set
import uuid
import threading


class IdentityScope(str, Enum):
    """Identity permission scope L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


class IdentityStatus(str, Enum):
    """Identity lifecycle status."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    PENDING = "pending"


@dataclass
class Principal:
    """Human or system principal that owns identities."""
    id: str
    name: str
    type: str  # human, system, service
    email: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: IdentityStatus = IdentityStatus.ACTIVE


@dataclass
class AgentIdentity:
    """Agent identity with permissions and trust."""
    id: str
    principal_id: str  # Owning principal
    name: str
    description: str = ""
    scope: IdentityScope = IdentityScope.L1
    permissions: Set[str] = field(default_factory=set)  # Permission IDs
    capabilities: Set[str] = field(default_factory=set)  # Capability IDs
    trust_score: float = 0.5  # 0.0 - 1.0
    status: IdentityStatus = IdentityStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    last_active: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        if self.status != IdentityStatus.ACTIVE:
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True


@dataclass
class Permission:
    """Permission definition."""
    id: str
    name: str
    description: str
    scope: IdentityScope
    resource_type: str  # e.g., "capability", "resource", "data"
    resource_pattern: str = "*"  # Pattern for resource matching
    actions: List[str] = field(default_factory=list)  # read, write, execute, admin
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class IdentityAuditEntry:
    """Audit trail for identity operations."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = field(default_factory=datetime.utcnow)
    operation: str = ""  # create_identity, grant_permission, revoke, etc.
    actor: str = ""  # principal_id or agent_id performing operation
    target: str = ""  # identity_id or permission_id affected
    details: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    result: str = "success"  # success, failure
    error: Optional[str] = None


class IdentityManager:
    """Identity Authority — Single source of truth for identities."""

    def __init__(self):
        self._principals: Dict[str, Principal] = {}
        self._identities: Dict[str, AgentIdentity] = {}
        self._permissions: Dict[str, Permission] = {}
        self._principal_identities: Dict[str, Set[str]] = {}  # principal_id -> identity_ids
        self._audit_log: List[IdentityAuditEntry] = []
        self._lock = threading.RLock()

        # Register built-in permissions
        self._register_builtin_permissions()

    def _register_builtin_permissions(self):
        """Register system built-in permissions."""
        builtins = [
            Permission(
                id="capability.invoke",
                name="Invoke Capability",
                description="Invoke registered capabilities",
                scope=IdentityScope.L1,
                resource_type="capability",
                actions=["invoke"],
            ),
            Permission(
                id="resource.allocate",
                name="Allocate Resources",
                description="Request resource quotas",
                scope=IdentityScope.L2,
                resource_type="resource",
                actions=["allocate", "release"],
            ),
            Permission(
                id="policy.evaluate",
                name="Evaluate Policy",
                description="Request policy decisions",
                scope=IdentityScope.L2,
                resource_type="policy",
                actions=["evaluate"],
            ),
            Permission(
                id="event.publish",
                name="Publish Events",
                description="Publish to event bus",
                scope=IdentityScope.L1,
                resource_type="event",
                actions=["publish"],
            ),
            Permission(
                id="event.subscribe",
                name="Subscribe Events",
                description="Subscribe to event bus",
                scope=IdentityScope.L2,
                resource_type="event",
                actions=["subscribe"],
            ),
            Permission(
                id="network.send",
                name="Send Network Messages",
                description="Send messages via network bus",
                scope=IdentityScope.L2,
                resource_type="network",
                actions=["send"],
            ),
            Permission(
                id="trust.manage",
                name="Manage Trust",
                description="Assign/update trust scores",
                scope=IdentityScope.L3,
                resource_type="trust",
                actions=["assign", "update", "revoke"],
            ),
            Permission(
                id="evaluation.request",
                name="Request Evaluation",
                description="Request outcome evaluation",
                scope=IdentityScope.L2,
                resource_type="evaluation",
                actions=["evaluate"],
            ),
            Permission(
                id="identity.manage",
                name="Manage Identities",
                description="Create/revoke agent identities",
                scope=IdentityScope.L4,
                resource_type="identity",
                actions=["create", "revoke", "grant", "audit"],
            ),
            Permission(
                id="admin.full",
                name="Full Admin Access",
                description="Unrestricted administrative access",
                scope=IdentityScope.L0,
                resource_type="*",
                actions=["*"],
            ),
        ]

        for perm in builtins:
            self._permissions[perm.id] = perm

    def _log_audit(
        self,
        operation: str,
        actor: str,
        target: str,
        details: Dict[str, Any],
        result: str = "success",
        error: Optional[str] = None,
        correlation_id: Optional[str] = None
    ):
        """Log identity operation to audit trail."""
        entry = IdentityAuditEntry(
            operation=operation,
            actor=actor,
            target=target,
            details=details,
            result=result,
            error=error,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        self._audit_log.append(entry)

        # Keep last 10000 entries
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-10000:]

    def create_principal(
        self,
        name: str,
        type: str,
        email: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Principal:
        """Create a new principal (human or system)."""
        with self._lock:
            principal = Principal(
                id=str(uuid.uuid4())[:8],
                name=name,
                type=type,
                email=email,
                metadata=metadata or {},
            )
            self._principals[principal.id] = principal
            self._principal_identities[principal.id] = set()

            self._log_audit(
                operation="create_principal",
                actor="system",
                target=principal.id,
                details={"name": name, "type": type, "email": email},
            )

            return principal

    def get_principal(self, principal_id: str) -> Optional[Principal]:
        """Get principal by ID."""
        with self._lock:
            return self._principals.get(principal_id)

    def create_identity(
        self,
        principal_id: str,
        name: str,
        description: str = "",
        scope: IdentityScope = IdentityScope.L1,
        permissions: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        trust_score: float = 0.5,
        expires_in_days: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AgentIdentity:
        """Create a new agent identity."""
        with self._lock:
            # Verify principal exists
            if principal_id not in self._principals:
                raise ValueError(f"Principal {principal_id} not found")

            principal = self._principals[principal_id]
            if principal.status != IdentityStatus.ACTIVE:
                raise ValueError(f"Principal {principal_id} is not active")

            # Validate scope - agent cannot exceed principal's authority
            # (simplified: principals at L0 can create any scope)
            if principal.type != "human" and scope == IdentityScope.L0:
                raise ValueError("Only human principals can create L0 identities")

            # Validate permissions exist
            perm_set = set(permissions or [])
            for perm_id in perm_set:
                if perm_id not in self._permissions:
                    raise ValueError(f"Permission {perm_id} not found")

            identity = AgentIdentity(
                id=str(uuid.uuid4())[:8],
                principal_id=principal_id,
                name=name,
                description=description,
                scope=scope,
                permissions=perm_set,
                capabilities=set(capabilities or []),
                trust_score=max(0.0, min(1.0, trust_score)),
                metadata=metadata or {},
            )

            if expires_in_days:
                identity.expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

            self._identities[identity.id] = identity
            self._principal_identities[principal_id].add(identity.id)

            self._log_audit(
                operation="create_identity",
                actor=principal_id,
                target=identity.id,
                details={
                    "name": name,
                    "scope": scope.value,
                    "permissions": list(perm_set),
                    "capabilities": list(identity.capabilities),
                    "trust_score": trust_score,
                },
            )

            return identity

    def get_identity(self, identity_id: str) -> Optional[AgentIdentity]:
        """Get agent identity by ID."""
        with self._lock:
            return self._identities.get(identity_id)

    def get_identities_by_principal(self, principal_id: str) -> List[AgentIdentity]:
        """Get all identities for a principal."""
        with self._lock:
            ids = self._principal_identities.get(principal_id, set())
            return [self._identities[i] for i in ids if i in self._identities]

    def grant_permission(
        self,
        identity_id: str,
        permission_id: str,
        actor: str,
        correlation_id: Optional[str] = None
    ) -> bool:
        """Grant permission to an identity."""
        with self._lock:
            identity = self._identities.get(identity_id)
            if not identity:
                self._log_audit(
                    operation="grant_permission",
                    actor=actor,
                    target=identity_id,
                    details={"permission": permission_id, "error": "Identity not found"},
                    result="failure",
                    error="Identity not found",
                    correlation_id=correlation_id,
                )
                return False

            if permission_id not in self._permissions:
                self._log_audit(
                    operation="grant_permission",
                    actor=actor,
                    target=identity_id,
                    details={"permission": permission_id, "error": "Permission not found"},
                    result="failure",
                    error="Permission not found",
                    correlation_id=correlation_id,
                )
                return False

            # Check if actor has permission to grant
            actor_identity = self._identities.get(actor)
            if not actor_identity or not self._can_grant(actor_identity, permission_id):
                self._log_audit(
                    operation="grant_permission",
                    actor=actor,
                    target=identity_id,
                    details={"permission": permission_id, "error": "Insufficient authority to grant"},
                    result="failure",
                    error="Insufficient authority to grant",
                    correlation_id=correlation_id,
                )
                return False

            identity.permissions.add(permission_id)
            identity.updated_at = datetime.utcnow()

            self._log_audit(
                operation="grant_permission",
                actor=actor,
                target=identity_id,
                details={"permission": permission_id},
                correlation_id=correlation_id,
            )

            return True

    def revoke_permission(
        self,
        identity_id: str,
        permission_id: str,
        actor: str,
        correlation_id: Optional[str] = None
    ) -> bool:
        """Revoke permission from an identity."""
        with self._lock:
            identity = self._identities.get(identity_id)
            if not identity:
                return False

            identity.permissions.discard(permission_id)
            identity.updated_at = datetime.utcnow()

            self._log_audit(
                operation="revoke_permission",
                actor=actor,
                target=identity_id,
                details={"permission": permission_id},
                correlation_id=correlation_id,
            )

            return True

    def _can_grant(self, actor: AgentIdentity, permission_id: str) -> bool:
        """Check if actor can grant a permission."""
        # L0 can grant anything
        if actor.scope == IdentityScope.L0:
            return True

        # Check if actor has the permission themselves
        if permission_id not in actor.permissions:
            return False

        # Check if actor's scope is sufficient for the permission's scope
        perm = self._permissions.get(permission_id)
        if not perm:
            return False

        scope_order = {s: i for i, s in enumerate(IdentityScope)}
        return scope_order[actor.scope] <= scope_order[perm.scope]

    def check_capability(
        self,
        identity_id: str,
        capability_id: str,
        correlation_id: Optional[str] = None
    ) -> bool:
        """Check if identity has a capability."""
        with self._lock:
            identity = self._identities.get(identity_id)
            if not identity or not identity.is_active:
                return False
            return capability_id in identity.capabilities

    def grant_capability(
        self,
        identity_id: str,
        capability_id: str,
        actor: str,
        correlation_id: Optional[str] = None
    ) -> bool:
        """Grant capability to an identity."""
        with self._lock:
            identity = self._identities.get(identity_id)
            if not identity:
                return False

            identity.capabilities.add(capability_id)
            identity.updated_at = datetime.utcnow()

            self._log_audit(
                operation="grant_capability",
                actor=actor,
                target=identity_id,
                details={"capability": capability_id},
                correlation_id=correlation_id,
            )

            return True

    def revoke_identity(
        self,
        identity_id: str,
        actor: str,
        reason: str = "",
        correlation_id: Optional[str] = None
    ) -> bool:
        """Revoke an agent identity."""
        with self._lock:
            identity = self._identities.get(identity_id)
            if not identity:
                return False

            identity.status = IdentityStatus.REVOKED
            identity.updated_at = datetime.utcnow()

            self._log_audit(
                operation="revoke_identity",
                actor=actor,
                target=identity_id,
                details={"reason": reason},
                correlation_id=correlation_id,
            )

            return True

    def audit_trail(
        self,
        identity_id: Optional[str] = None,
        principal_id: Optional[str] = None,
        operation: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100
    ) -> List[IdentityAuditEntry]:
        """Query audit trail."""
        with self._lock:
            entries = self._audit_log

            if identity_id:
                entries = [e for e in entries if e.target == identity_id]
            if principal_id:
                entries = [e for e in entries if e.actor == principal_id]
            if operation:
                entries = [e for e in entries if e.operation == operation]
            if since:
                entries = [e for e in entries if e.timestamp >= since]

            return entries[-limit:]

    def register_permission(
        self,
        id: str,
        name: str,
        description: str,
        scope: IdentityScope,
        resource_type: str,
        resource_pattern: str = "*",
        actions: Optional[List[str]] = None
    ) -> Permission:
        """Register a new permission."""
        with self._lock:
            if id in self._permissions:
                raise ValueError(f"Permission {id} already exists")

            perm = Permission(
                id=id,
                name=name,
                description=description,
                scope=scope,
                resource_type=resource_type,
                resource_pattern=resource_pattern,
                actions=actions or [],
            )
            self._permissions[id] = perm
            return perm

    def get_permission(self, permission_id: str) -> Optional[Permission]:
        """Get permission by ID."""
        with self._lock:
            return self._permissions.get(permission_id)

    def list_permissions(self, scope: Optional[IdentityScope] = None) -> List[Permission]:
        """List permissions, optionally filtered by scope."""
        with self._lock:
            perms = list(self._permissions.values())
            if scope:
                perms = [p for p in perms if p.scope == scope]
            return perms

    def stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        with self._lock:
            by_scope = {}
            by_status = {}
            for ident in self._identities.values():
                by_scope[ident.scope.value] = by_scope.get(ident.scope.value, 0) + 1
                by_status[ident.status.value] = by_status.get(ident.status.value, 0) + 1

            return {
                "total_principals": len(self._principals),
                "total_identities": len(self._identities),
                "total_permissions": len(self._permissions),
                "audit_entries": len(self._audit_log),
                "by_scope": by_scope,
                "by_status": by_status,
            }


from datetime import timedelta

# Global identity manager instance
_global_manager: Optional[IdentityManager] = None
_global_lock = threading.Lock()


def get_identity_manager() -> IdentityManager:
    """Get or create the global identity manager."""
    global _global_manager
    if _global_manager is None:
        with _global_lock:
            if _global_manager is None:
                _global_manager = IdentityManager()
    return _global_manager


# Convenience functions
def create_principal(
    name: str,
    type: str,
    email: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Principal:
    """Create a principal."""
    return get_identity_manager().create_principal(name, type, email, metadata)


def create_identity(
    principal_id: str,
    name: str,
    description: str = "",
    scope: IdentityScope = IdentityScope.L1,
    permissions: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
    trust_score: float = 0.5,
    expires_in_days: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> AgentIdentity:
    """Create an agent identity."""
    return get_identity_manager().create_identity(
        principal_id, name, description, scope, permissions, capabilities,
        trust_score, expires_in_days, metadata
    )


def grant_permission(
    identity_id: str,
    permission_id: str,
    actor: str,
    correlation_id: Optional[str] = None
) -> bool:
    """Grant permission to identity."""
    return get_identity_manager().grant_permission(identity_id, permission_id, actor, correlation_id)


def check_capability(
    identity_id: str,
    capability_id: str,
    correlation_id: Optional[str] = None
) -> bool:
    """Check if identity has capability."""
    return get_identity_manager().check_capability(identity_id, capability_id, correlation_id)


def revoke_identity(
    identity_id: str,
    actor: str,
    reason: str = "",
    correlation_id: Optional[str] = None
) -> bool:
    """Revoke an identity."""
    return get_identity_manager().revoke_identity(identity_id, actor, reason, correlation_id)


def audit_identity(
    identity_id: Optional[str] = None,
    principal_id: Optional[str] = None,
    operation: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: int = 100
) -> List[IdentityAuditEntry]:
    """Query identity audit trail."""
    return get_identity_manager().audit_trail(identity_id, principal_id, operation, since, limit)