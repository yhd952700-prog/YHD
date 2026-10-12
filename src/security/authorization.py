"""
Authorization / RBAC module for LiuHao AI OS.

Provides role-based access control (RBAC), permission definitions,
role hierarchy (inheritance), resource-level permissions, ABAC interface,
policy evaluation, and role CRUD management.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PermissionEffect(str, Enum):
    """Effect of a permission statement."""

    ALLOW = "allow"
    DENY = "deny"


class ResourceType(str, Enum):
    """Common resource types for permission scoping."""

    ALL = "*"
    USER = "user"
    ROLE = "role"
    ORGANIZATION = "organization"
    PROJECT = "project"
    API_KEY = "api_key"
    AUDIT_LOG = "audit_log"
    SETTING = "setting"
    MODEL = "model"
    DATASET = "dataset"
    AGENT = "agent"
    KNOWLEDGE = "knowledge"


class Action(str, Enum):
    """Common permission actions."""

    ALL = "*"
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    LIST = "list"
    ADMIN = "admin"
    INVITE = "invite"
    EXPORT = "export"
    IMPORT = "import"


class RoleStatus(str, Enum):
    """Status of a role definition."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPRECATED = "deprecated"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Permission:
    """Represents a single permission statement.

    Format: effect:action:resource_type:resource_id
    Example: allow:read:project:proj_123
    """

    effect: PermissionEffect
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    description: Optional[str] = None
    conditions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("Permission action cannot be empty")
        if not self.resource_type:
            raise ValueError("Resource type cannot be empty")

    @classmethod
    def from_string(cls, perm_str: str) -> "Permission":
        """Parse a permission string like 'allow:read:project:proj_123'."""
        parts = perm_str.split(":")
        if len(parts) < 3:
            raise ValueError(
                f"Invalid permission format: {perm_str}. "
                f"Expected effect:action:resource_type[:resource_id]"
            )
        effect = PermissionEffect(parts[0])
        action = parts[1]
        resource_type = parts[2]
        resource_id = parts[3] if len(parts) > 3 else None
        return cls(
            effect=effect,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )

    def to_string(self) -> str:
        """Serialize permission to string format."""
        base = f"{self.effect.value}:{self.action}:{self.resource_type}"
        if self.resource_id:
            base += f":{self.resource_id}"
        return base

    def matches(self, action: str, resource_type: str, resource_id: Optional[str] = None) -> bool:
        """Check if this permission matches the requested action and resource.

        Supports wildcard matching with '*'.
        """
        # Action match
        if self.action != "*" and self.action != action:
            return False
        # Resource type match
        if self.resource_type != "*" and self.resource_type != resource_type:
            return False
        # Resource ID match
        if self.resource_id is not None and self.resource_id != "*":
            if resource_id is None or self.resource_id != resource_id:
                return False
        return True


@dataclass
class Role:
    """Represents a role with permissions and optional parent roles."""

    role_id: str
    name: str
    description: str = ""
    permissions: list[Permission] = field(default_factory=list)
    parent_roles: list[str] = field(default_factory=list)  # role IDs
    is_system: bool = False
    status: RoleStatus = RoleStatus.ACTIVE
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_permission(self, permission: Permission) -> None:
        """Add a permission to this role."""
        self.permissions.append(permission)
        self.updated_at = time.time()

    def remove_permission(self, perm_str: str) -> bool:
        """Remove a permission by its string representation.

        Returns True if found and removed.
        """
        target = Permission.from_string(perm_str).to_string()
        for i, p in enumerate(self.permissions):
            if p.to_string() == target:
                self.permissions.pop(i)
                self.updated_at = time.time()
                return True
        return False


@dataclass
class UserRoleAssignment:
    """Represents an assignment of a role to a user."""

    assignment_id: str
    user_id: str
    role_id: str
    resource_id: Optional[str] = None  # For scoped assignments
    assigned_by: Optional[str] = None
    assigned_at: float = field(default_factory=lambda: time.time())
    expires_at: Optional[float] = None
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyEvaluationResult:
    """Result of a policy evaluation."""

    allowed: bool
    effect: PermissionEffect
    matched_permission: Optional[Permission] = None
    matched_role: Optional[str] = None
    reason: Optional[str] = None
    evaluated_permissions: int = 0


# ---------------------------------------------------------------------------
# ABAC Policy Interface
# ---------------------------------------------------------------------------


class AbacPolicy(ABC):
    """Abstract base class for Attribute-Based Access Control policies.

    ABAC policies evaluate access based on attributes of the subject (user),
    resource, action, and environment (context).
    """

    @abstractmethod
    def evaluate(
        self,
        user_attributes: dict[str, Any],
        resource_attributes: dict[str, Any],
        action: str,
        context: dict[str, Any],
    ) -> Optional[PermissionEffect]:
        """Evaluate the policy.

        Returns:
            PermissionEffect.ALLOW, PermissionEffect.DENY, or None
            (None means the policy doesn't apply / not applicable).
        """
        ...

    @property
    @abstractmethod
    def policy_id(self) -> str:
        """Unique identifier for this policy."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        ...


class SimpleAbacPolicy(AbacPolicy):
    """A simple ABAC policy using attribute matching rules.

    Supports basic comparison operators for user, resource, and context
    attributes. All conditions within a policy must match (AND logic).
    """

    def __init__(
        self,
        policy_id: str,
        description: str,
        effect: PermissionEffect,
        user_conditions: Optional[dict[str, Any]] = None,
        resource_conditions: Optional[dict[str, Any]] = None,
        context_conditions: Optional[dict[str, Any]] = None,
        action: Optional[str] = None,
    ) -> None:
        self._policy_id = policy_id
        self._description = description
        self._effect = effect
        self._user_conds = user_conditions or {}
        self._resource_conds = resource_conditions or {}
        self._context_conds = context_conditions or {}
        self._action = action

    @property
    def policy_id(self) -> str:
        return self._policy_id

    @property
    def description(self) -> str:
        return self._description

    def _match_condition(self, attributes: dict[str, Any], conditions: dict[str, Any]) -> bool:
        """Check if attributes match all conditions (AND logic)."""
        for key, expected in conditions.items():
            actual = attributes.get(key)
            if callable(expected):
                if not expected(actual):
                    return False
            elif isinstance(expected, list):
                if actual not in expected:
                    return False
            else:
                if actual != expected:
                    return False
        return True

    def evaluate(
        self,
        user_attributes: dict[str, Any],
        resource_attributes: dict[str, Any],
        action: str,
        context: dict[str, Any],
    ) -> Optional[PermissionEffect]:
        # Check action match
        if self._action and self._action != "*" and self._action != action:
            return None

        # Check all conditions
        if not self._match_condition(user_attributes, self._user_conds):
            return None
        if not self._match_condition(resource_attributes, self._resource_conds):
            return None
        if not self._match_condition(context, self._context_conds):
            return None

        return self._effect


# ---------------------------------------------------------------------------
# RBAC Manager
# ---------------------------------------------------------------------------


class RbacManager:
    """Role-Based Access Control manager.

    Manages roles, role hierarchies, user-role assignments, and
    permission checking. Supports role inheritance where child roles
    inherit all permissions from their parent roles.
    """

    def __init__(self) -> None:
        self._roles: dict[str, Role] = {}
        self._assignments: dict[str, list[UserRoleAssignment]] = {}  # user_id -> assignments
        self._abac_policies: list[AbacPolicy] = []

    # ---- Role CRUD ----

    def create_role(
        self,
        role_id: str,
        name: str,
        description: str = "",
        permissions: Optional[list[Permission]] = None,
        parent_roles: Optional[list[str]] = None,
        is_system: bool = False,
    ) -> Role:
        """Create a new role.

        Args:
            role_id: Unique role identifier.
            name: Human-readable role name.
            description: Role description.
            permissions: Initial permissions.
            parent_roles: Parent role IDs for inheritance.
            is_system: Whether this is a system (predefined) role.

        Returns:
            The created Role.

        Raises:
            ValueError: If role_id already exists.
        """
        if role_id in self._roles:
            raise ValueError(f"Role already exists: {role_id}")

        # Validate parent roles exist
        if parent_roles:
            for parent_id in parent_roles:
                if parent_id not in self._roles:
                    raise ValueError(f"Parent role not found: {parent_id}")
                # Check for cycles
                if self._would_create_cycle(role_id, parent_id):
                    raise ValueError(
                        f"Adding parent role {parent_id} would create a cycle"
                    )

        role = Role(
            role_id=role_id,
            name=name,
            description=description,
            permissions=permissions or [],
            parent_roles=parent_roles or [],
            is_system=is_system,
        )
        self._roles[role_id] = role
        return role

    def get_role(self, role_id: str) -> Optional[Role]:
        """Get a role by ID."""
        return self._roles.get(role_id)

    def update_role(self, role_id: str, **kwargs: Any) -> Role:
        """Update role properties.

        Supported kwargs: name, description, status, metadata
        """
        role = self._roles.get(role_id)
        if role is None:
            raise KeyError(f"Role not found: {role_id}")
        if role.is_system and ("name" in kwargs or "permissions" in kwargs):
            raise ValueError("Cannot modify system role name or permissions directly")

        for key, value in kwargs.items():
            if hasattr(role, key):
                setattr(role, key, value)
        role.updated_at = time.time()
        return role

    def delete_role(self, role_id: str) -> bool:
        """Delete a role.

        System roles cannot be deleted. Also removes all assignments
        for this role.

        Returns True if the role was deleted.
        """
        role = self._roles.get(role_id)
        if role is None:
            return False
        if role.is_system:
            raise ValueError("Cannot delete system role")

        # Remove role from children's parent lists
        for child in self._roles.values():
            if role_id in child.parent_roles:
                child.parent_roles.remove(role_id)

        # Remove assignments
        for assignments in self._assignments.values():
            assignments[:] = [a for a in assignments if a.role_id != role_id]

        del self._roles[role_id]
        return True

    def list_roles(self, include_inactive: bool = False) -> list[Role]:
        """List all roles."""
        roles = list(self._roles.values())
        if not include_inactive:
            roles = [r for r in roles if r.status == RoleStatus.ACTIVE]
        return roles

    # ---- Role Hierarchy ----

    def add_parent_role(self, role_id: str, parent_role_id: str) -> None:
        """Add a parent role (inherit permissions)."""
        role = self._roles.get(role_id)
        if role is None:
            raise KeyError(f"Role not found: {role_id}")
        if parent_role_id not in self._roles:
            raise KeyError(f"Parent role not found: {parent_role_id}")
        if self._would_create_cycle(role_id, parent_role_id):
            raise ValueError("Adding this parent would create a cycle")
        if parent_role_id not in role.parent_roles:
            role.parent_roles.append(parent_role_id)
            role.updated_at = time.time()

    def remove_parent_role(self, role_id: str, parent_role_id: str) -> bool:
        """Remove a parent role.

        Returns True if found and removed.
        """
        role = self._roles.get(role_id)
        if role is None:
            return False
        if parent_role_id in role.parent_roles:
            role.parent_roles.remove(parent_role_id)
            role.updated_at = time.time()
            return True
        return False

    def _would_create_cycle(self, role_id: str, parent_role_id: str) -> bool:
        """Check if adding parent_role_id as a parent of role_id would create a cycle."""
        # Walk up from parent_role_id; if we reach role_id, it's a cycle
        visited = set()
        stack = [parent_role_id]
        while stack:
            current = stack.pop()
            if current == role_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            role = self._roles.get(current)
            if role:
                stack.extend(role.parent_roles)
        return False

    def get_effective_permissions(self, role_id: str) -> list[Permission]:
        """Get all permissions for a role including inherited ones.

        Traverses the role hierarchy and collects permissions from
        all ancestor roles.
        """
        permissions: list[Permission] = []
        visited: set[str] = set()
        stack = [role_id]

        while stack:
            current_id = stack.pop()
            if current_id in visited:
                continue
            visited.add(current_id)

            role = self._roles.get(current_id)
            if role is None:
                continue
            if role.status != RoleStatus.ACTIVE:
                continue

            permissions.extend(role.permissions)
            stack.extend(role.parent_roles)

        return permissions

    # ---- User Assignment ----

    def assign_role(
        self,
        user_id: str,
        role_id: str,
        resource_id: Optional[str] = None,
        assigned_by: Optional[str] = None,
        expires_at: Optional[float] = None,
    ) -> UserRoleAssignment:
        """Assign a role to a user.

        Args:
            user_id: User to assign the role to.
            role_id: Role to assign.
            resource_id: Optional resource scope for the assignment.
            assigned_by: User who made the assignment.
            expires_at: Optional expiration timestamp.

        Returns:
            The UserRoleAssignment.
        """
        if role_id not in self._roles:
            raise KeyError(f"Role not found: {role_id}")

        assignment = UserRoleAssignment(
            assignment_id=str(uuid.uuid4()),
            user_id=user_id,
            role_id=role_id,
            resource_id=resource_id,
            assigned_by=assigned_by,
            expires_at=expires_at,
        )

        if user_id not in self._assignments:
            self._assignments[user_id] = []
        self._assignments[user_id].append(assignment)
        return assignment

    def revoke_role(self, user_id: str, role_id: str, resource_id: Optional[str] = None) -> int:
        """Revoke a role assignment from a user.

        Returns the number of revoked assignments.
        """
        if user_id not in self._assignments:
            return 0

        revoked = 0
        assignments = self._assignments[user_id]
        for a in assignments:
            if a.role_id == role_id and a.is_active:
                if resource_id is not None and a.resource_id != resource_id:
                    continue
                a.is_active = False
                revoked += 1
        return revoked

    def get_user_roles(self, user_id: str) -> list[UserRoleAssignment]:
        """Get all active role assignments for a user."""
        now = time.time()
        assignments = self._assignments.get(user_id, [])
        return [
            a
            for a in assignments
            if a.is_active and (a.expires_at is None or now < a.expires_at)
        ]

    def get_user_permissions(self, user_id: str) -> list[Permission]:
        """Get all effective permissions for a user across all roles.

        Includes permissions inherited through role hierarchies.
        """
        permissions: list[Permission] = []
        assignments = self.get_user_roles(user_id)

        for assignment in assignments:
            role_perms = self.get_effective_permissions(assignment.role_id)
            # If assignment is scoped to a resource, filter/override resource_id
            if assignment.resource_id:
                for perm in role_perms:
                    if perm.resource_id is None or perm.resource_id == "*":
                        # Scope the permission to the assignment's resource
                        scoped = Permission(
                            effect=perm.effect,
                            action=perm.action,
                            resource_type=perm.resource_type,
                            resource_id=assignment.resource_id,
                            description=perm.description,
                            conditions=dict(perm.conditions),
                        )
                        permissions.append(scoped)
                    else:
                        permissions.append(perm)
            else:
                permissions.extend(role_perms)

        return permissions

    # ---- Permission Checking ----

    def can_user_do(
        self,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
    ) -> bool:
        """Check if a user has permission to perform an action on a resource.

        Uses deny-override: if any matching permission is DENY, access is denied.
        Otherwise, at least one ALLOW permission must match.

        Args:
            user_id: The user to check.
            action: The action being performed.
            resource_type: Type of resource.
            resource_id: Optional specific resource ID.

        Returns:
            True if the user is allowed.
        """
        result = self.evaluate_policy(user_id, action, resource_type, resource_id)
        return result.allowed

    def evaluate_policy(
        self,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        user_attributes: Optional[dict[str, Any]] = None,
        resource_attributes: Optional[dict[str, Any]] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> PolicyEvaluationResult:
        """Evaluate the full policy (RBAC + ABAC) for a user action.

        Evaluation order:
        1. Check explicit DENY permissions (RBAC) - deny overrides
        2. Check ABAC policies (DENY from ABAC also overrides)
        3. Check ALLOW permissions (RBAC)
        4. Check ALLOW from ABAC
        5. Default: DENY

        Returns:
            PolicyEvaluationResult with the decision.
        """
        permissions = self.get_user_permissions(user_id)
        total_evaluated = len(permissions)
        matched_allow: Optional[Permission] = None
        matched_allow_role: Optional[str] = None

        # First pass: check for explicit DENY (deny overrides)
        for perm in permissions:
            if perm.effect == PermissionEffect.DENY and perm.matches(
                action, resource_type, resource_id
            ):
                # Check conditions if any
                if self._check_conditions(
                    perm, user_attributes or {}, resource_attributes or {}, context or {}
                ):
                    return PolicyEvaluationResult(
                        allowed=False,
                        effect=PermissionEffect.DENY,
                        matched_permission=perm,
                        reason="Explicit deny permission matched",
                        evaluated_permissions=total_evaluated,
                    )

        # Check ABAC policies for DENY
        for policy in self._abac_policies:
            result = policy.evaluate(
                user_attributes or {},
                resource_attributes or {},
                action,
                context or {},
            )
            total_evaluated += 1
            if result == PermissionEffect.DENY:
                return PolicyEvaluationResult(
                    allowed=False,
                    effect=PermissionEffect.DENY,
                    matched_permission=None,
                    reason=f"ABAC policy denied: {policy.policy_id}",
                    evaluated_permissions=total_evaluated,
                )

        # Second pass: check for ALLOW
        assignments = self.get_user_roles(user_id)
        for assignment in assignments:
            role = self._roles.get(assignment.role_id)
            if role is None:
                continue
            role_perms = self.get_effective_permissions(assignment.role_id)
            for perm in role_perms:
                if perm.effect == PermissionEffect.ALLOW and perm.matches(
                    action, resource_type, resource_id
                ):
                    if self._check_conditions(
                        perm,
                        user_attributes or {},
                        resource_attributes or {},
                        context or {},
                    ):
                        matched_allow = perm
                        matched_allow_role = role.role_id
                        break
            if matched_allow:
                break

        # Check ABAC policies for ALLOW
        if not matched_allow:
            for policy in self._abac_policies:
                result = policy.evaluate(
                    user_attributes or {},
                    resource_attributes or {},
                    action,
                    context or {},
                )
                total_evaluated += 1
                if result == PermissionEffect.ALLOW:
                    return PolicyEvaluationResult(
                        allowed=True,
                        effect=PermissionEffect.ALLOW,
                        matched_permission=None,
                        reason=f"ABAC policy allowed: {policy.policy_id}",
                        evaluated_permissions=total_evaluated,
                    )

        if matched_allow:
            return PolicyEvaluationResult(
                allowed=True,
                effect=PermissionEffect.ALLOW,
                matched_permission=matched_allow,
                matched_role=matched_allow_role,
                reason="Allow permission matched",
                evaluated_permissions=total_evaluated,
            )

        return PolicyEvaluationResult(
            allowed=False,
            effect=PermissionEffect.DENY,
            matched_permission=None,
            reason="No matching allow permission found",
            evaluated_permissions=total_evaluated,
        )

    def _check_conditions(
        self,
        permission: Permission,
        user_attrs: dict[str, Any],
        resource_attrs: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        """Check permission-level conditions."""
        if not permission.conditions:
            return True

        # Check user conditions
        user_conds = permission.conditions.get("user", {})
        for key, expected in user_conds.items():
            actual = user_attrs.get(key)
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False

        # Check resource conditions
        resource_conds = permission.conditions.get("resource", {})
        for key, expected in resource_conds.items():
            actual = resource_attrs.get(key)
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False

        # Check context conditions
        context_conds = permission.conditions.get("context", {})
        for key, expected in context_conds.items():
            actual = context.get(key)
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False

        return True

    # ---- ABAC Policy Management ----

    def add_abac_policy(self, policy: AbacPolicy) -> None:
        """Add an ABAC policy."""
        self._abac_policies.append(policy)

    def remove_abac_policy(self, policy_id: str) -> bool:
        """Remove an ABAC policy by ID.

        Returns True if found and removed.
        """
        for i, policy in enumerate(self._abac_policies):
            if policy.policy_id == policy_id:
                self._abac_policies.pop(i)
                return True
        return False

    def list_abac_policies(self) -> list[AbacPolicy]:
        """List all ABAC policies."""
        return list(self._abac_policies)


# ---------------------------------------------------------------------------
# Permission Definitions / Registry
# ---------------------------------------------------------------------------


class PermissionRegistry:
    """Registry for defining and managing available permissions.

    Provides a catalog of all permissions that can be assigned,
    grouped by resource type.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, dict[str, str]] = {}  # resource_type -> {action: description}

    def register_permission(
        self, resource_type: str, action: str, description: str
    ) -> None:
        """Register a permission definition."""
        if resource_type not in self._definitions:
            self._definitions[resource_type] = {}
        self._definitions[resource_type][action] = description

    def register_resource(
        self, resource_type: str, actions: dict[str, str]
    ) -> None:
        """Register all actions for a resource type."""
        if resource_type not in self._definitions:
            self._definitions[resource_type] = {}
        self._definitions[resource_type].update(actions)

    def get_permissions(self, resource_type: Optional[str] = None) -> dict[str, dict[str, str]]:
        """Get all permission definitions, optionally filtered by resource type."""
        if resource_type:
            return {resource_type: self._definitions.get(resource_type, {})}
        return dict(self._definitions)

    def list_resource_types(self) -> list[str]:
        """List all registered resource types."""
        return list(self._definitions.keys())

    def list_actions(self, resource_type: str) -> list[str]:
        """List all actions for a resource type."""
        return list(self._definitions.get(resource_type, {}).keys())

    def is_valid(self, action: str, resource_type: str) -> bool:
        """Check if an action/resource_type combination is registered."""
        if action == "*" or resource_type == "*":
            return True
        actions = self._definitions.get(resource_type, {})
        return action in actions


# ---------------------------------------------------------------------------
# Predefined Roles
# ---------------------------------------------------------------------------


def create_default_rbac() -> RbacManager:
    """Create an RBAC manager with standard predefined roles.

    Standard roles:
    - super_admin: Full access to everything
    - admin: Organization-level administration
    - manager: Team/project management
    - member: Standard user access
    - viewer: Read-only access
    """
    rbac = RbacManager()

    # Super Admin - all permissions
    rbac.create_role(
        role_id="super_admin",
        name="Super Admin",
        description="Full system access with no restrictions",
        permissions=[
            Permission(
                effect=PermissionEffect.ALLOW,
                action="*",
                resource_type="*",
                resource_id="*",
                description="Full access to all resources",
            )
        ],
        is_system=True,
    )

    # Admin
    rbac.create_role(
        role_id="admin",
        name="Admin",
        description="Organization administration access",
        permissions=[
            Permission(PermissionEffect.ALLOW, "*", "user"),
            Permission(PermissionEffect.ALLOW, "*", "role"),
            Permission(PermissionEffect.ALLOW, "*", "project"),
            Permission(PermissionEffect.ALLOW, "*", "api_key"),
            Permission(PermissionEffect.ALLOW, "*", "audit_log"),
            Permission(PermissionEffect.ALLOW, "*", "setting"),
            Permission(PermissionEffect.ALLOW, "*", "model"),
            Permission(PermissionEffect.ALLOW, "*", "dataset"),
            Permission(PermissionEffect.ALLOW, "*", "agent"),
            Permission(PermissionEffect.ALLOW, "*", "knowledge"),
        ],
        is_system=True,
    )

    # Manager
    rbac.create_role(
        role_id="manager",
        name="Manager",
        description="Team and project management access",
        permissions=[
            Permission(PermissionEffect.ALLOW, "read", "user"),
            Permission(PermissionEffect.ALLOW, "list", "user"),
            Permission(PermissionEffect.ALLOW, "invite", "user"),
            Permission(PermissionEffect.ALLOW, "*", "project"),
            Permission(PermissionEffect.ALLOW, "read", "model"),
            Permission(PermissionEffect.ALLOW, "list", "model"),
            Permission(PermissionEffect.ALLOW, "read", "dataset"),
            Permission(PermissionEffect.ALLOW, "list", "dataset"),
            Permission(PermissionEffect.ALLOW, "*", "agent"),
            Permission(PermissionEffect.ALLOW, "*", "knowledge"),
        ],
        is_system=True,
    )

    # Member
    rbac.create_role(
        role_id="member",
        name="Member",
        description="Standard user with access to assigned resources",
        permissions=[
            Permission(PermissionEffect.ALLOW, "read", "user", description="Read own profile"),
            Permission(PermissionEffect.ALLOW, "update", "user", description="Update own profile"),
            Permission(PermissionEffect.ALLOW, "read", "project"),
            Permission(PermissionEffect.ALLOW, "list", "project"),
            Permission(PermissionEffect.ALLOW, "read", "model"),
            Permission(PermissionEffect.ALLOW, "list", "model"),
            Permission(PermissionEffect.ALLOW, "read", "dataset"),
            Permission(PermissionEffect.ALLOW, "list", "dataset"),
            Permission(PermissionEffect.ALLOW, "read", "agent"),
            Permission(PermissionEffect.ALLOW, "list", "agent"),
            Permission(PermissionEffect.ALLOW, "read", "knowledge"),
            Permission(PermissionEffect.ALLOW, "list", "knowledge"),
        ],
        is_system=True,
    )

    # Viewer
    rbac.create_role(
        role_id="viewer",
        name="Viewer",
        description="Read-only access to resources",
        permissions=[
            Permission(PermissionEffect.ALLOW, "read", "project"),
            Permission(PermissionEffect.ALLOW, "list", "project"),
            Permission(PermissionEffect.ALLOW, "read", "model"),
            Permission(PermissionEffect.ALLOW, "list", "model"),
            Permission(PermissionEffect.ALLOW, "read", "dataset"),
            Permission(PermissionEffect.ALLOW, "list", "dataset"),
            Permission(PermissionEffect.ALLOW, "read", "agent"),
            Permission(PermissionEffect.ALLOW, "list", "agent"),
            Permission(PermissionEffect.ALLOW, "read", "knowledge"),
            Permission(PermissionEffect.ALLOW, "list", "knowledge"),
        ],
        is_system=True,
    )

    return rbac
