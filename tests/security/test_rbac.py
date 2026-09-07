"""Tests for Phase 5 RBAC and access control."""

from src.security.rbac import (
    RBACManager, Permission, PermissionAction, ResourceType,
    Role, get_rbac_manager, create_role, check_access
)


def test_rbac_manager_creation():
    """Test RBACManager can be initialized."""
    manager = RBACManager()
    assert manager is not None


def test_get_rbac_manager_singleton():
    """Test get_rbac_manager returns singleton."""
    m1 = get_rbac_manager()
    m2 = get_rbac_manager()
    assert m1 is m2


def test_create_role():
    """Test create_role convenience function."""
    role = create_role(name="test_role", description="Test role")
    assert role.name == "test_role"


def test_check_access_convenience():
    """Test check_access convenience function."""
    result = check_access(
        user_id="user-1",
        resource_type=ResourceType.MEMORY,
        action=PermissionAction.READ,
    )
    assert "allowed" in result


def test_permission_creation():
    """Test Permission creation and matching."""
    perm = Permission(
        resource_type=ResourceType.MEMORY,
        action=PermissionAction.READ,
        target="agent-123",
    )
    assert perm.resource_type == ResourceType.MEMORY
    assert perm.action == PermissionAction.READ
    assert perm.target == "agent-123"


def test_resource_type_enum():
    """Test ResourceType enum values."""
    assert ResourceType.MEMORY.value == "memory"
    assert ResourceType.SYSTEM.value == "system"


def test_permission_action_enum():
    """Test PermissionAction enum values."""
    assert PermissionAction.READ.value == "read"
    assert PermissionAction.WRITE.value == "write"


def test_role_creation():
    """Test Role creation with required id field."""
    role = Role(id="role-1", name="admin", description="Administrator role")
    assert role.id == "role-1"
    assert role.name == "admin"


def test_rbac_export_policy():
    """Test RBAC policy export."""
    manager = RBACManager()
    policy = manager.export_policy()
    assert "roles" in policy
    assert "audit_log_count" in policy


def test_rbac_clear_audit_log():
    """Test RBAC audit log clearing."""
    manager = RBACManager()
    manager.clear_audit_log()
    assert len(manager._audit_log) == 0