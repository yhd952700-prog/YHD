"""Tests for Phase 5 governance and audit."""

from src.security.rbac import RBACManager, get_rbac_manager
from src.security.abac import ABACPolicy, ABACSubject, ABACResource, ABACEngine


def test_governance_rbac_manager():
    """Test governance RBAC manager operations."""
    manager = get_rbac_manager()
    assert manager is not None


def test_governance_audit_export():
    """Test governance audit export."""
    manager = get_rbac_manager()
    policy = manager.export_policy()
    assert "roles" in policy
    assert "audit_log_count" in policy


def test_governance_abac_policy_creation():
    """Test ABAC policy creation."""
    policy = ABACPolicy(
        policy_id="sales_policy",
        description="Sales department policy",
        effect="permit",
        condition={"department": "sales"},
    )
    assert policy.policy_id == "sales_policy"
    assert policy.effect == "permit"


def test_governance_abac_subject():
    """Test ABAC subject creation."""
    from src.security.abac import ABACSubject
    
    subject = ABACSubject(user_id="user-1", departments=["sales"])
    assert subject.user_id == "user-1"
    assert "sales" in subject.departments


def test_governance_abac_resource():
    """Test ABAC resource creation."""
    from src.security.abac import ABACResource
    from src.security.rbac import ResourceType
    
    resource = ABACResource(
        resource_id="res-1",
        resource_type=ResourceType.MEMORY,
        owner_id="user-1",
    )
    assert resource.resource_id == "res-1"
    assert resource.resource_type == ResourceType.MEMORY


def test_governance_abac_environment():
    """Test ABAC environment creation."""
    from src.security.abac import ABACEnvironment
    
    env = ABACEnvironment(
        ip_address="192.168.1.1",
        user_agent="test-agent",
    )
    assert env.ip_address == "192.168.1.1"
    assert env.user_agent == "test-agent"


def test_governance_full_export():
    """Test full governance export from RBAC manager."""
    manager = get_rbac_manager()
    policy = manager.export_policy()
    assert isinstance(policy, dict)
    assert len(policy) > 0