"""Identity Kernel — Agent identity + permissions

The Identity Kernel manages agent identity, permissions, and audit trails.
Supports scope-aware filtering (L0-L7) and complete audit logging.

依据 Definition Lock §112: Identity Kernel 必须能够
- create_identity(principal, permissions, scope, trust_score)
- grant_permission(identity, permission, scope)
- audit_trail(identity.id, scope)
- Support scope-aware filtering L0-L7
- Provide complete audit trail for all permission operations
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from src._time import utc_now
from enum import Enum
import json
import logging
import os
from typing import Any, Dict, List, Optional, Set
import uuid
import threading

from src.kernels._crosscutting import kernel_action

logger = logging.getLogger("liuhao.kernel.identity")

#: Principal / id of the built-in internal service identity.
#:
#: Canonical home: the Identity Kernel owns which identities exist. The
#: Policy Kernel references the identity only through its ``metadata["kind"]``
#: marker and never needs this name; ``_crosscutting`` imports it lazily to
#: attribute kernel actions.
INTERNAL_SERVICE_PRINCIPAL = "liuhao-internal-service"

#: ``metadata`` key holding an identity's kind, and its two sanctioned values.
#:
#: These three names are the single source of truth for "is this actor a
#: human?". Both consumers -- the Policy Kernel's ``_is_verified_human`` and
#: the sovereignty channel's ``_validated_principal`` -- must ask
#: ``is_human_identity`` rather than re-deriving the answer from raw metadata.
METADATA_KIND_KEY = "kind"
SERVICE_KIND = "service"
HUMAN_KIND = "human"

#: Where registered human identities are persisted.
#:
#: ``IdentityManager`` holds identities in memory only, so a human registered
#: through a one-off call would vanish on restart -- which would make the
#: approval channel look wired while having nobody able to approve. Setting
#: this variable to a JSON seed file is what makes registration durable.
#: Unset (the default) means **no humans are registered**: fail-closed and
#: honest, rather than falling back to a machine identity.
HUMAN_IDENTITIES_FILE_ENV = "LIUHAO_HUMAN_IDENTITIES_FILE"

#: ``metadata`` key for a human-friendly name, shown by operators' tooling.
METADATA_DISPLAY_NAME_KEY = "display_name"


def is_human_identity(identity: Optional[AgentIdentity]) -> bool:
    """Return True iff ``identity`` is a registered, ACTIVE *human*.

    This is a **positive allowlist**, not a reverse exclusion. An identity
    qualifies only by carrying ``metadata["kind"] == "human"``; merely *not*
    being a service is not evidence of being human.

    Why that distinction matters (Policy C-7): the previous test was
    ``metadata.get("kind") != "service"``, which **fails open** for any
    identity that simply lacks the marker. The built-in ``system`` identity
    (auto-created, ``metadata={}``) therefore satisfied "verified human" and
    could hold human sovereignty -- the audit trail recorded a machine as the
    approver of a CRITICAL action, defeating the accountability chain that
    OD-010 exists to establish.
    """
    if identity is None:
        return False
    if getattr(identity, "status", None) != IdentityStatus.ACTIVE:
        return False
    metadata = identity.metadata if isinstance(identity.metadata, dict) else {}
    return metadata.get(METADATA_KIND_KEY) == HUMAN_KIND


class IdentityScope(str, Enum):
    """Identity permission scope L0-L7."""
    L0 = "L0"  # Human only
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
    DEACTIVATED = "deactivated"


@dataclass
class AgentIdentity:
    """Agent identity with permissions and trust."""
    id: str
    principal: str  # Unique principal identifier
    permissions: Set[str] = field(default_factory=set)
    scope: IdentityScope = IdentityScope.L1
    trust_score: float = 0.5
    status: IdentityStatus = IdentityStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    last_modified: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class AuditEntry:
    """Audit log entry for permission operations."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    identity_id: str = ""
    operation: str = ""  # "grant", "revoke", "modify"
    permission: Optional[str] = None
    scope: IdentityScope = IdentityScope.L0
    result: str = ""  # "allowed", "denied", "audit"
    reason: str = ""
    timestamp: datetime = field(default_factory=utc_now)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class IdentityManager:
    """Manages agent identities, permissions, and audit trails."""

    def __init__(self):
        self._identities: Dict[str, AgentIdentity] = {}  # id -> AgentIdentity
        self._principal_index: Dict[str, str] = {}  # principal -> id
        self._audit_log: List[AuditEntry] = []
        self._lock = threading.RLock()

        # Default system identity
        if "system" not in self._identities:
            system_identity = AgentIdentity(
                id="system",
                principal="system",
                permissions={"admin"},
                scope=IdentityScope.L0,
                trust_score=1.0,
            )
            self._identities["system"] = system_identity
            self._principal_index["system"] = "system"

        # Built-in internal service identity (Policy C-1).
        #
        # Kernel actions are performed by the system's own code. Attributing
        # them to this principal (instead of an anonymous {"type": "system"}
        # actor that no built-in rule could ever allow) is what lets the
        # Policy Kernel record an informative verdict.
        #
        # ``metadata["kind"] == "service"`` is the marker the Policy Kernel
        # requires (``_is_verified_service``): it keeps this identity from
        # being usable as a human identity and vice versa. Scope is L0 and
        # ``permissions`` is empty on purpose -- the service holds no
        # authority of its own; it is only pre-approved for the action
        # allow-list held in the Policy Kernel.
        if INTERNAL_SERVICE_PRINCIPAL not in self._identities:
            service_identity = AgentIdentity(
                id=INTERNAL_SERVICE_PRINCIPAL,
                principal=INTERNAL_SERVICE_PRINCIPAL,
                permissions=set(),
                scope=IdentityScope.L0,
                trust_score=1.0,
                metadata={
                    "kind": "service",
                    "description": "Internal kernel service principal",
                },
            )
            self._identities[INTERNAL_SERVICE_PRINCIPAL] = service_identity
            self._principal_index[INTERNAL_SERVICE_PRINCIPAL] = INTERNAL_SERVICE_PRINCIPAL

        self._seed_human_identities()

    def _seed_human_identities(self) -> int:
        """Load registered humans from the file named by the env var.

        Returns how many were loaded. **Unset variable means zero humans** --
        that is deliberately fail-closed rather than a silent fallback to a
        machine identity (Policy C-7).

        A malformed or unreadable file is logged loudly and loads nothing.
        Crashing on it would take down every kernel consumer for a
        configuration problem; silently ignoring it would leave operators
        believing a human was registered when none was.
        """
        path = (os.environ.get(HUMAN_IDENTITIES_FILE_ENV) or "").strip()
        if not path:
            return 0
        if not os.path.isfile(path):
            logger.warning(
                "%s is set to %r but no such file exists -- no human identity "
                "can approve. Create it with scripts/register_human_identity.py.",
                HUMAN_IDENTITIES_FILE_ENV, path,
            )
            return 0
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            logger.error(
                "could not read %s (%r): %s -- no human identity loaded",
                HUMAN_IDENTITIES_FILE_ENV, path, exc,
            )
            return 0

        if isinstance(payload, dict):
            entries: List[Dict[str, Any]] = list(payload.get("humans") or [])
        elif isinstance(payload, list):
            entries = list(payload)
        else:
            logger.error(
                "%s (%r) must be a JSON list or {'humans': [...]} -- got %s",
                HUMAN_IDENTITIES_FILE_ENV, path, type(payload).__name__,
            )
            return 0

        loaded = 0
        for entry in entries:
            if not isinstance(entry, dict):
                logger.error("skipping non-object entry in %r: %r", path, entry)
                continue
            principal = str(entry.get("principal") or "").strip()
            if not principal:
                logger.error("skipping entry without a principal in %r", path)
                continue
            with self._lock:
                if principal in self._principal_index:
                    logger.warning(
                        "human identity %r already registered -- skipping", principal,
                    )
                    continue
                # Built directly instead of via create_identity(): that method
                # is a @kernel_action, whose policy verdict calls back into
                # IdentityManager. Seeding happens *inside* __init__, before
                # the global singleton is published, so the callback would
                # construct another manager and recurse without bound. The
                # built-in system/service identities are built the same way.
                metadata: Dict[str, Any] = {
                    METADATA_KIND_KEY: HUMAN_KIND,
                    "seeded_from": path,
                }
                display_name = entry.get(METADATA_DISPLAY_NAME_KEY)
                if display_name:
                    metadata[METADATA_DISPLAY_NAME_KEY] = display_name
                if isinstance(entry.get("registered_at"), str):
                    metadata["registered_at"] = entry["registered_at"]
                else:
                    metadata["registered_at"] = utc_now().isoformat()
                try:
                    scope = IdentityScope(entry.get("scope") or IdentityScope.L0.value)
                except ValueError:
                    scope = IdentityScope.L0
                identity = AgentIdentity(
                    id=principal,
                    principal=principal,
                    permissions=set(entry.get("permissions") or []),
                    scope=scope,
                    trust_score=1.0,
                    metadata=metadata,
                )
                self._identities[identity.id] = identity
                self._principal_index[principal] = identity.id
                self._audit_log.append(
                    AuditEntry(
                        identity_id=identity.id,
                        operation="create",
                        permission=None,
                        scope=identity.scope,
                        result="allowed",
                        reason=f"Human identity seeded for principal: {principal}",
                    )
                )
            loaded += 1
        if loaded:
            logger.info("loaded %d registered human identities from %r", loaded, path)
        return loaded

    def _get_identity(self, identity_id: str) -> Optional[AgentIdentity]:
        """Get identity by ID."""
        with self._lock:
            return self._identities.get(identity_id)

    @kernel_action("identity.create_identity")
    def create_identity(
        self,
        principal: str,
        permissions: Optional[Set[str]] = None,
        scope: IdentityScope = IdentityScope.L1,
        trust_score: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentIdentity:
        """Create a new agent identity."""
        with self._lock:
            # Principal uniqueness: a principal identifies exactly one identity.
            if principal in self._principal_index:
                audit = AuditEntry(
                    identity_id=self._principal_index[principal],
                    operation="create",
                    permission=None,
                    scope=scope,
                    result="denied",
                    reason=f"Principal {principal!r} already has an identity",
                )
                self._audit_log.append(audit)
                return None

            # Clamp trust score
            trust_score = max(0.0, min(1.0, trust_score))

            identity = AgentIdentity(
                id=str(uuid.uuid4())[:8],
                principal=principal,
                permissions=permissions or set(),
                scope=scope,
                trust_score=trust_score,
                metadata=metadata or {},
            )

            self._identities[identity.id] = identity
            self._principal_index[principal] = identity.id

            # Record audit event
            audit = AuditEntry(
                identity_id=identity.id,
                operation="create",
                permission=None,
                scope=scope,
                result="allowed",
                reason=f"Identity created for principal: {principal}",
            )
            self._audit_log.append(audit)

            return identity

    def create_human_identity(
        self,
        principal: str,
        permissions: Optional[Set[str]] = None,
        trust_score: float = 1.0,
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentIdentity]:
        """Register ``principal`` as a human who may hold sovereignty.

        The sanctioned way to create a human identity: it stamps
        ``metadata["kind"] == "human"`` -- the only marker
        :func:`is_human_identity` accepts -- so callers cannot forget it.
        Humans are ``L0`` (the scope the codebase already labels "Human only").

        Returns the identity, or ``None`` if the principal already exists
        (matching :meth:`create_identity`).
        """
        merged: Dict[str, Any] = dict(metadata or {})
        merged[METADATA_KIND_KEY] = HUMAN_KIND
        if display_name:
            merged[METADATA_DISPLAY_NAME_KEY] = display_name
        merged.setdefault("registered_at", utc_now().isoformat())
        return self.create_identity(
            principal=principal,
            permissions=permissions,
            scope=IdentityScope.L0,
            trust_score=trust_score,
            metadata=merged,
        )

    def get_identity(self, identity_id: str) -> Optional[AgentIdentity]:
        """Get identity by ID."""
        with self._lock:
            return self._get_identity(identity_id)

    def get_identity_by_principal(self, principal: str) -> Optional[AgentIdentity]:
        """Get identity by its principal identifier (P10 verification)."""
        with self._lock:
            identity_id = self._principal_index.get(principal)
            if identity_id is None:
                return None
            return self._identities.get(identity_id)

    def list_identities(self, scope: Optional[IdentityScope] = None) -> List[AgentIdentity]:
        """List identities, optionally filtered by scope."""
        with self._lock:
            if scope:
                scope_order = {s: i for i, s in enumerate(IdentityScope)}
                min_idx = scope_order[scope]
                return [
                    ident for ident in self._identities.values()
                    if scope_order[ident.scope] >= min_idx
                ]
            return list(self._identities.values())

    @kernel_action("identity.grant_permission")
    def grant_permission(
        self,
        identity_id: str,
        permission: str,
        scope: IdentityScope = IdentityScope.L1,
        reason: str = ""
    ) -> bool:
        """Grant a permission to an identity."""
        with self._lock:
            identity = self._get_identity(identity_id)
            if not identity:
                return False

            # Scope check: permission scope cannot exceed identity scope
            scope_order = {s: i for i, s in enumerate(IdentityScope)}
            if scope_order[scope] > scope_order[identity.scope]:
                # Record denied audit
                audit = AuditEntry(
                    identity_id=identity_id,
                    operation="grant",
                    permission=permission,
                    scope=scope,
                    result="denied",
                    reason=f"Permission scope {scope} exceeds identity scope {identity.scope}",
                )
                self._audit_log.append(audit)
                return False

            identity.permissions.add(permission)
            identity.last_modified = utc_now()

            # Record audit event
            audit = AuditEntry(
                identity_id=identity_id,
                operation="grant",
                permission=permission,
                scope=scope,
                result="allowed",
                reason=reason or f"Permission {permission} granted to identity {identity_id}",
            )
            self._audit_log.append(audit)

            return True

    @kernel_action("identity.revoke_permission")
    def revoke_permission(
        self,
        identity_id: str,
        permission: str,
        reason: str = ""
    ) -> bool:
        """Revoke a permission from an identity."""
        with self._lock:
            identity = self._get_identity(identity_id)
            if not identity:
                return False

            if permission not in identity.permissions:
                return False

            identity.permissions.discard(permission)
            identity.last_modified = utc_now()

            # Record audit event
            audit = AuditEntry(
                identity_id=identity_id,
                operation="revoke",
                permission=permission,
                scope=identity.scope,
                result="allowed",
                reason=reason or f"Permission {permission} revoked from identity {identity_id}",
            )
            self._audit_log.append(audit)

            return True

    def audit_trail(
        self,
        identity_id: str,
        scope: IdentityScope = IdentityScope.L0,
        since: Optional[datetime] = None
    ) -> List[AuditEntry]:
        """Get audit trail for an identity."""
        with self._lock:
            entries = self._audit_log

            if identity_id:
                entries = [e for e in entries if e.identity_id == identity_id]

            if scope:
                scope_order = {s: i for i, s in enumerate(IdentityScope)}
                min_idx = scope_order[scope]
                entries = [e for e in entries if scope_order[e.scope] >= min_idx]

            if since:
                entries = [e for e in entries if e.timestamp >= since]

            return sorted(entries, key=lambda e: e.timestamp, reverse=True)

    def check_permission(
        self,
        identity_id: str,
        permission: str,
        scope: IdentityScope = IdentityScope.L1,
    ) -> bool:
        """Check if an identity has a specific permission."""
        with self._lock:
            identity = self._get_identity(identity_id)
            if not identity:
                return False

            # Lifecycle enforcement: only ACTIVE identities may act.
            if identity.status != IdentityStatus.ACTIVE:
                return False

            # Scope check
            scope_order = {s: i for i, s in enumerate(IdentityScope)}
            if scope_order[scope] > scope_order[identity.scope]:
                return False

            return permission in identity.permissions

    def stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        with self._lock:
            return {
                "total_identities": len(self._identities),
                "active_identities": sum(1 for i in self._identities.values() if i.status == IdentityStatus.ACTIVE),
                "total_audit_entries": len(self._audit_log),
                "identities_by_scope": {
                    s.value: sum(1 for i in self._identities.values() if i.scope == IdentityScope(s))
                    for s in IdentityScope
                },
            }


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
def create_identity(
    principal: str,
    permissions: Optional[Set[str]] = None,
    scope: IdentityScope = IdentityScope.L1,
    trust_score: float = 0.5
) -> AgentIdentity:
    """Create a new identity."""
    return get_identity_manager().create_identity(principal, permissions, scope, trust_score)


def grant_permission(
    identity_id: str,
    permission: str,
    scope: IdentityScope = IdentityScope.L1,
    reason: str = ""
) -> bool:
    """Grant a permission to an identity."""
    return get_identity_manager().grant_permission(identity_id, permission, scope, reason)


def revoke_permission(
    identity_id: str,
    permission: str,
    reason: str = ""
) -> bool:
    """Revoke a permission from an identity."""
    return get_identity_manager().revoke_permission(identity_id, permission, reason)


def audit_trail(
    identity_id: str,
    scope: IdentityScope = IdentityScope.L0,
    since: Optional[datetime] = None
) -> List[AuditEntry]:
    """Get audit trail for an identity."""
    return get_identity_manager().audit_trail(identity_id, scope, since)


def check_permission(
    identity_id: str,
    permission: str,
    scope: IdentityScope = IdentityScope.L1
) -> bool:
    """Check if an identity has a specific permission."""
    return get_identity_manager().check_permission(identity_id, permission, scope)


def create_identity_with_permissions(
    principal: str,
    permissions: Set[str],
    scope: IdentityScope = IdentityScope.L1,
    trust_score: float = 0.5
) -> AgentIdentity:
    """Create identity and immediately grant permissions."""
    identity = create_identity(principal, permissions, scope, trust_score)
    for perm in permissions:
        grant_permission(identity.id, perm, scope)
    return identity


def get_identity_stats() -> Dict[str, Any]:
    """Get identity manager statistics."""
    return get_identity_manager().stats()
