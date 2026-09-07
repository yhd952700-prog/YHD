"""
Role-Based Access Control (RBAC) for LiuHao AI OS

Provides:
- Role/permission hierarchy (roles inherit from other roles)
- Permission checking with scopes and conditions
- Resource-based access control
- Dynamic permission evaluation
- Audit logging of access decisions
"""

import json
import secrets
import time
from typing import Optional, Dict, Any, Set, List
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum


class PermissionAction(Enum):
    """Standard permission actions"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    CREATE = "create"
    DELETE = "delete"
    ADMIN = "admin"
    CONFIGURE = "configure"
    MONITOR = "monitor"
    INVOKE = "invoke"
    BROWSE = "browse"


class ResourceType(Enum):
    """Standard resource types"""
    MEMORY = "memory"
    AGENT = "agent"
    PROVIDER = "provider"
    WORKFLOW = "workflow"
    TOOL = "tool"
    CONFIG = "config"
    METRICS = "metrics"
    LOG = "log"
    API = "api"
    USER = "user"
    SESSION = "session"
    SYSTEM = "system"


@dataclass(eq=True)
class Permission:
    """
    Permission data model.

    Format: {resource_type}:{action}[:{conditions?}]
    Example: memory:read:agent-123
             provider:invoke:gpt-4
             system:admin:*
    """
    resource_type: ResourceType
    action: PermissionAction
    target: Optional[str] = None  # Specific target (agent ID, provider name, etc.)

    # Conditions for dynamic evaluation
    conditions: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    description: Optional[str] = None
    granted_at: Optional[float] = None
    granted_by: Optional[str] = None

    def __hash__(self) -> int:
        """Hash based on immutable identity fields only."""
        return hash((self.resource_type, self.action, self.target))

    def matches(
        self,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if this permission matches a request"""
        # Resource type must match
        if self.resource_type != resource_type:
            return False

        # Action must match
        if self.action != action:
            return False

        # Target matching
        if self.target is not None and self.target != target:
            # Check for wildcard
            if self.target == "*":
                pass  # Any target matches
            else:
                return False

        # Condition matching
        if extra_conditions and self.conditions:
            for key, value in self.conditions.items():
                if key not in extra_conditions:
                    return False
                if extra_conditions[key] != value:
                    return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_type": self.resource_type.value,
            "action": self.action.value,
            "target": self.target,
            "conditions": self.conditions,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Permission":
        return cls(
            resource_type=ResourceType(data["resource_type"]),
            action=PermissionAction(data["action"]),
            target=data.get("target"),
            conditions=data.get("conditions", {}),
            description=data.get("description"),
        )


class RoleStatus(Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


@dataclass
class Role:
    """
    Role data model with inheritance.

    A role can inherit permissions from parent roles.
    Supports recursive inheritance with cycle detection.
    """
    id: str
    name: str
    description: Optional[str] = None
    status: RoleStatus = RoleStatus.ACTIVE
    parent_ids: List[str] = field(default_factory=list)  # Parent role IDs
    child_ids: List[str] = field(default_factory=list)   # Child role IDs
    inherited_scopes: Dict[str, Any] = field(default_factory=dict)

    # Cached permission set (computed from role + all parents)
    _cached_permissions: Optional[Set[Permission]] = None
    _cached_evaluated_at: float = 0

    def add_parent(self, parent_id: str) -> None:
        """Add parent role (with cycle detection)"""
        if parent_id == self.id:
            return  # Don't self-reference
        if parent_id in self.parent_ids:
            return  # Already parent

        # Check for cycles
        if self._would_create_cycle(parent_id):
            return

        self.parent_ids.append(parent_id)

        # Update child's parent reference
        # This would need to update the parent's child_ids list

    def _would_create_cycle(self, potential_parent_id: str) -> bool:
        """Check if adding a parent would create a cycle"""
        visited = set()
        to_visit = [potential_parent_id]

        while to_visit:
            current = to_visit.pop()
            if current == self.id:
                return True  # Would create cycle
            if current in visited:
                continue
            visited.add(current)

            # Look up this role's parents via the owning manager
            parent_role = self._get_role(current)
            if parent_role:
                to_visit.extend(parent_role.parent_ids)

        return False

    def has_permission(
        self,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if this role has a permission (including inherited)"""
        # Check cached permissions (refresh after 30 seconds)
        now = time.time()
        if (self._cached_permissions is not None
                and now - self._cached_evaluated_at < 30):
            perm_set = self._cached_permissions
        else:
            perm_set = self._compute_permissions()
            self._cached_permissions = perm_set
            self._cached_evaluated_at = now

        # Check if any permission matches
        for perm in perm_set:
            if perm.matches(
                resource_type, action, target, extra_conditions
            ):
                return True
        return False

    # Custom permissions assigned via RBACManager.assign_permission
    custom_permissions: Dict[str, Permission] = field(default_factory=dict)
    # Back-reference to owning RBACManager for parent lookups
    _rbac_manager: Optional["RBACManager"] = None

    def _get_role(self, role_id: str) -> Optional["Role"]:
        """Look up a role by ID from the owning manager, or global store."""
        if self._rbac_manager is not None:
            return self._rbac_manager.get_role(role_id)
        from .rbac_store import rbac_store
        return rbac_store.get_role(role_id)

    def _get_own_permissions(self) -> Set[Permission]:
        """Get permissions directly assigned to this role"""
        return set(self.custom_permissions.values())

    def _compute_permissions(self) -> Set[Permission]:
        """Compute all permissions from this role and parent roles"""
        perm_set: Set[Permission] = set()

        # Add own permissions
        perm_set.update(self._get_own_permissions())

        # Add parent permissions (recursive)
        for parent_id in self.parent_ids:
            parent_role = self._get_role(parent_id)
            if parent_role:
                perm_set.update(parent_role._compute_permissions())

        return perm_set

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "parent_ids": self.parent_ids,
            "child_ids": self.child_ids,
            "custom_permissions": {
                k: v.to_dict() for k, v in self.custom_permissions.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], rbac_store=None) -> "Role":
        role = cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            status=RoleStatus(data.get("status", "active")),
            parent_ids=list(data.get("parent_ids", [])),
            child_ids=list(data.get("child_ids", [])),
        )
        # Load custom permissions
        for perm_key, perm_data in data.get("custom_permissions", {}).items():
            role.custom_permissions[perm_key] = Permission.from_dict(perm_data)
        # Rebuild child references if rbac_store provided
        if rbac_store:
            for parent_id in role.parent_ids:
                parent = rbac_store.get_role(parent_id)
                if parent:
                    parent.child_ids.append(role.id)
        return role


class RBACManager:
    """
    Role-Based Access Control Manager.

    Features:
    - Role hierarchy with inheritance
    - Permission evaluation with conditions
    - Resource-based access control
    - Dynamic permission granting/revocation
    - Audit trail of access decisions
    """

    def __init__(self, store_path: Optional[str] = None):
        """
        Initialize RBAC manager.

        Args:
            store_path: Path to JSON store for persistence
        """
        self.store_path = store_path or Path.home() / ".liuhao" / "rbac.json"
        self._roles: Dict[str, Role] = {}
        self._permissions_by_user: Dict[str, Set[Permission]] = {}  # user_id -> permissions
        self._audit_log: List[Dict[str, Any]] = []
        self._loaded = False

    def _load(self) -> None:
        """Load RBAC data from storage"""
        if self._loaded:
            return

        store = Path(self.store_path)
        if store.exists():
            try:
                data = json.loads(store.read_text())
                for role_data in data.get("roles", []):
                    role = Role.from_dict(role_data, self)
                    role._rbac_manager = self
                    self._roles[role.id] = role
            except Exception as e:
                print(f"Warning: Failed to load RBAC data: {e}")

        self._loaded = True

    def _save(self) -> None:
        """Persist RBAC data to storage"""
        data = {
            "version": 1,
            "saved_at": time.time(),
            "roles": [role.to_dict() for role in self._roles.values()],
        }
        Path(self.store_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.store_path).write_text(json.dumps(data, indent=2))

    # ==================== Role Management ====================

    def create_role(
        self,
        name: str,
        description: Optional[str] = None,
        status: RoleStatus = RoleStatus.ACTIVE,
        parent_ids: Optional[List[str]] = None,
    ) -> Role:
        """Create a new role"""
        self._load()

        role_id = f"role_{secrets.token_urlsafe(12)}"
        # Avoid collision with existing IDs
        while role_id in self._roles:
            role_id = f"role_{secrets.token_urlsafe(12)}"

        role = Role(
            id=role_id,
            name=name,
            description=description,
            status=status,
            parent_ids=parent_ids or [],
        )
        role._rbac_manager = self

        # Update parent-child references
        for parent_id in role.parent_ids:
            if parent_id in self._roles:
                self._roles[parent_id].child_ids.append(role_id)

        self._roles[role_id] = role
        self._save()
        return role

    def get_role(self, role_id: str) -> Optional[Role]:
        """Get role by ID"""
        self._load()
        return self._roles.get(role_id)

    def list_roles(
        self,
        status: Optional[RoleStatus] = None,
        recursive: bool = False,
    ) -> List[Role]:
        """List roles, optionally filtered by status"""
        self._load()

        roles = list(self._roles.values())
        if status:
            roles = [r for r in roles if r.status == status]

        if recursive:
            # Flatten with all descendants
            result = []
            for role in roles:
                result.append(role)
                result.extend(self._get_all_descendants(role))
            return result

        return sorted(roles, key=lambda r: r.name)

    def _get_all_descendants(self, role: Role) -> List[Role]:
        """Get all descendant roles"""
        result = []
        for child_id in role.child_ids:
            child = self.get_role(child_id)
            if child:
                result.append(child)
                result.extend(self._get_all_descendants(child))
        return result

    def delete_role(self, role_id: str) -> bool:
        """Delete a role"""
        self._load()

        if role_id not in self._roles:
            return False

        # Check if role has children (would orphan them)
        role = self._roles[role_id]
        if role.child_ids and len(role.child_ids) > 0:
            # Could reparent children, or prevent deletion
            # For now, prevent deletion if children exist
            return False

        # Remove from parents
        for r in self._roles.values():
            if role_id in r.child_ids:
                r.child_ids.remove(role_id)

        del self._roles[role_id]
        self._save()
        return True

    # ==================== Permission Management ====================

    def assign_permission(
        self,
        role_id: str,
        permission: Permission,
    ) -> bool:
        """Assign a permission to a role"""
        self._load()

        if role_id not in self._roles:
            return False

        # Store permission in role's custom_permissions dict
        role = self._roles[role_id]
        target_str = permission.target if permission.target else ""
        perm_key = permission.resource_type.value + ":" + permission.action.value + ":" + target_str
        role.custom_permissions[perm_key] = permission

        # Invalidate cache so _compute_permissions picks up changes
        role._cached_permissions = None
        self._save()
        return True

    def revoke_permission(
        self,
        role_id: str,
        permission: Permission,
    ) -> bool:
        """Revoke a permission from a role"""
        self._load()

        if role_id not in self._roles:
            return False

        role = self._roles[role_id]
        if role.custom_permissions:
            target_str = permission.target if permission.target else ""
            perm_key = permission.resource_type.value + ":" + permission.action.value + ":" + target_str
            role.custom_permissions.pop(perm_key, None)

        # Invalidate cache
        role._cached_permissions = None
        self._save()
        return True

    def has_role_permission(
        self,
        role_id: str,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if a role has a specific permission"""
        self._load()

        role = self._roles.get(role_id)
        if not role:
            return False

        return role.has_permission(
            resource_type, action, target, extra_conditions
        )

    # ==================== User-Based Access ====================

    def assign_permissions_to_user(
        self,
        user_id: str,
        permissions: List[Permission],
    ) -> None:
        """Assign permissions directly to a user"""
        self._load()

        if user_id not in self._permissions_by_user:
            self._permissions_by_user[user_id] = set()

        for perm in permissions:
            self._permissions_by_user[user_id].add(perm)

        self._save()

    def revoke_permissions_from_user(
        self,
        user_id: str,
        permissions: Optional[List[Permission]] = None,
    ) -> int:
        """Revoke permissions from a user"""
        self._load()

        if user_id not in self._permissions_by_user:
            return 0

        if permissions:
            for perm in permissions:
                self._permissions_by_user[user_id].discard(perm)
        else:
            # Revoke all direct permissions
            len(self._permissions_by_user[user_id])
            self._permissions_by_user[user_id].clear()

        self._save()
        return len(self._permissions_by_user.get(user_id, set()))

    def user_has_permission(
        self,
        user_id: str,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if a user has a permission (own + role-based)"""
        self._load()

        # Check direct user permissions
        user_perms = self._permissions_by_user.get(user_id, set())
        for perm in user_perms:
            if perm.matches(resource_type, action, target, extra_conditions):
                return True

        # Check role-based permissions
        # This would require knowing the user's roles
        # For now, check all roles
        for role in self._roles.values():
            if role.has_permission(resource_type, action, target, extra_conditions):
                return True

        return False

    # ==================== Access Decision ====================

    def check_access(
        self,
        user_id: str,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
    ) -> Dict[str, Any]:
        """
        Check access and return decision.

        Returns dict with:
        - allowed: bool
        - reason: str (if denied)
        - applicable_permissions: List[Permission]
        """
        self._load()

        # Check user permissions
        user_allowed = self.user_has_permission(
            user_id, resource_type, action, target, extra_conditions
        )

        # Check role permissions
        role_allowed = False
        applicable_perms: List[Permission] = []

        # Get user's roles (would need user-role mapping)
        # For now, check all roles
        for role in self._roles.values():
            if role.has_permission(resource_type, action, target, extra_conditions):
                role_allowed = True
                # Collect applicable permissions
                for perm in role._compute_permissions():
                    if perm.matches(resource_type, action, target, extra_conditions):
                        if perm not in applicable_perms:
                            applicable_perms.append(perm)

        # Determine decision
        allowed = user_allowed or role_allowed

        # Build reason
        reason = None
        if not allowed:
            reason = "Insufficient permissions"
            # Check if user has some permissions but not this one
            if user_allowed:
                reason = "Permission denied by policy"
            elif role_allowed:
                reason = "Role permission conflict"

        # Audit log
        self._audit_log.append({
            "timestamp": time.time(),
            "user_id": user_id,
            "resource_type": resource_type.value,
            "action": action.value,
            "target": target,
            "allowed": allowed,
            "applicable_permissions": [p.to_dict() for p in applicable_perms],
        })

        # Keep audit log bounded
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-5000:]

        self._save()

        return {
            "allowed": allowed,
            "reason": reason,
            "applicable_permissions": applicable_perms,
            "user_id": user_id,
            "resource_type": resource_type.value,
            "action": action.value,
        }

    # ==================== Utility Methods ====================

    def export_policy(self) -> Dict[str, Any]:
        """Export RBAC policy for review"""
        self._load()
        return {
            "roles": {rid: role.to_dict() for rid, role in self._roles.items()},
            "user_permissions": {
                uid: [p.to_dict() for p in perms]
                for uid, perms in self._permissions_by_user.items()
            },
            "audit_log_count": len(self._audit_log),
        }

    def clear_audit_log(self) -> None:
        """Clear audit log"""
        self._audit_log = []
        self._save()


# Module-level convenience
_default_rbac: Optional[RBACManager] = None


def get_rbac_manager() -> RBACManager:
    """Get default RBAC manager"""
    global _default_rbac
    if _default_rbac is None:
        _default_rbac = RBACManager()
    return _default_rbac


def create_role(
    name: str,
    description: Optional[str] = None,
    **kwargs
) -> Role:
    """Convenience function to create role"""
    return get_rbac_manager().create_role(name, **kwargs)


def check_access(
    user_id: str,
    resource_type: ResourceType,
    action: PermissionAction,
    **kwargs
) -> Dict[str, Any]:
    """Convenience function to check access"""
    return get_rbac_manager().check_access(user_id, resource_type, action, **kwargs)
