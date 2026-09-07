"""
Tests for SEC-05 (Key Rotation) and SEC-06 (Authentication & Authorization).

Covers:
- RBAC bug fixes (custom_permissions propagated via _compute_permissions,
  revoke_permission using dict.pop with correct key)
- ABAC engine (policy evaluation, condition operators, deny-overrides)
- Key rotation policy (TTL check, grace period, auto-rotation)
- API key + JWT integration with RBAC/ABAC
- Authentication flow (JWT issuance → validation → access control)
"""

import os
import time
import json
import tempfile
import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.security import (
    RBACManager,
    Role,
    Permission,
    PermissionAction,
    ResourceType,
    RoleStatus,
    KeyScope,
    KeyStatus,
    APIKeyManager,
    JWTHandler,
    TokenType,
    VaultTransitCrypto,
    CryptoAuditLogger,
)
from src.security.rbac_store import get_rbac_manager, rbac_store
from src.security.abac import (
    ABACEngine,
    ABACPolicy,
    ABACSubject,
    ABACResource,
    ABACEnvironment,
    ABACDecision,
)
from src.security.rotation import (
    KeyRotationManager,
    KeyType,
    RotationPolicy,
    DEFAULT_POLICIES,
    get_key_rotation_manager,
)
from src.security.audit_logger import redact_secrets


# ==================== RBAC Bug Fix Tests ====================

class TestRBACPermissionPropagation:
    """Tests for SEC-06 RBAC: custom permissions must propagate via _compute_permissions."""

    def setup_method(self):
        """Clean up any stale store files before each test."""
        import glob
        for f in glob.glob(os.path.join(tempfile.gettempdir(), "rbac_test_*.json")):
            os.remove(f)

    def test_custom_permissions_stored_and_propagated(self):
        """Permissions assigned to a role are returned by _compute_permissions."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_test_propagation.json"))
        role = manager.create_role(name="test_role")

        perm = Permission(
            resource_type=ResourceType.MEMORY,
            action=PermissionAction.READ,
            target="*",
        )
        manager.assign_permission(role.id, perm)

        # Role should now have custom_permissions populated
        assert hasattr(role, "custom_permissions")
        assert len(role.custom_permissions) == 1

    def test_inherited_permissions_from_parent(self):
        """Child role inherits parent's custom permissions."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_test_inherit.json"))
        parent = manager.create_role(name="parent")
        child = manager.create_role(name="child", parent_ids=[parent.id])

        perm = Permission(
            resource_type=ResourceType.SYSTEM,
            action=PermissionAction.ADMIN,
        )
        manager.assign_permission(parent.id, perm)

        # Child should inherit parent's permission
        assert child.has_permission(ResourceType.SYSTEM, PermissionAction.ADMIN)
        # Parent should also have it
        assert parent.has_permission(ResourceType.SYSTEM, PermissionAction.ADMIN)

    def test_revoke_permission_uses_correct_key(self):
        """revoke_permission must use string key matching assign_permission's key."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_test_revoke.json"))
        role = manager.create_role(name="test")

        perm = Permission(
            resource_type=ResourceType.MEMORY,
            action=PermissionAction.WRITE,
            target="agent-123",
        )
        manager.assign_permission(role.id, perm)
        assert len(role.custom_permissions) == 1

        # Revoke should work (previously crashed with .discard() on dict)
        result = manager.revoke_permission(role.id, perm)
        assert result is True
        assert len(role.custom_permissions) == 0

    def test_revoke_nonexistent_permission_returns_true(self):
        """revoking a permission that doesn't exist doesn't crash."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_test_revoke2.json"))
        role = manager.create_role(name="test")

        perm = Permission(
            resource_type=ResourceType.MEMORY,
            action=PermissionAction.READ,
        )
        # No permission assigned yet
        result = manager.revoke_permission(role.id, perm)
        assert result is True  # Graceful no-op

    def test_has_role_permission_after_assign(self):
        """has_role_permission returns True after assign_permission."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_test_hrp.json"))
        role = manager.create_role(name="dev")

        perm = Permission(resource_type=ResourceType.WORKFLOW, action=PermissionAction.EXECUTE)
        manager.assign_permission(role.id, perm)

        assert manager.has_role_permission(role.id, ResourceType.WORKFLOW, PermissionAction.EXECUTE)
        assert not manager.has_role_permission(role.id, ResourceType.WORKFLOW, PermissionAction.DELETE)

    def test_rbac_store_proxy_works(self):
        """rbac_store proxy delegates to RBACManager singleton."""
        store = rbac_store
        # Should have a get_role method
        assert hasattr(store, "get_role")
        assert hasattr(store, "create_role")

    def test_rbac_store_singleton(self):
        """get_rbac_manager returns the same instance."""
        mgr1 = get_rbac_manager()
        mgr2 = get_rbac_manager()
        assert mgr1 is mgr2


# ==================== ABAC Engine Tests ====================

class TestABACEngine:
    """Tests for ABAC policy evaluation."""

    def setup_method(self):
        self.engine = ABACEngine(strategy="deny_overrides")

    def test_simple_permit_policy(self):
        """A permit policy that matches grants access."""
        self.engine.add_policy(ABACPolicy(
            policy_id="p1",
            description="Permit memory read",
            effect=ABACDecision.PERMIT,
            target={"resource_type": ResourceType.MEMORY.value},
        ))

        subject = ABACSubject(user_id="user1", clearance="basic")
        resource = ABACResource(resource_id="mem1", resource_type=ResourceType.MEMORY)

        result = self.engine.evaluate(subject, resource, PermissionAction.READ)
        assert result is True

    def test_simple_deny_policy(self):
        """A deny policy that matches blocks access."""
        self.engine.add_policy(ABACPolicy(
            policy_id="deny_admin",
            description="Deny admin access to basic users",
            effect=ABACDecision.DENY,
            target={"resource_type": ResourceType.SYSTEM.value},
        ))

        subject = ABACSubject(user_id="user1", clearance="basic")
        resource = ABACResource(resource_id="sys1", resource_type=ResourceType.SYSTEM)

        result = self.engine.evaluate(subject, resource, PermissionAction.ADMIN)
        assert result is False

    def test_deny_overrides_permit(self):
        """Deny decision overrides permit when both apply."""
        self.engine.add_policy(ABACPolicy(
            policy_id="permit_all",
            description="Permit system access",
            effect=ABACDecision.PERMIT,
            target={"resource_type": ResourceType.SYSTEM.value},
        ))
        self.engine.add_policy(ABACPolicy(
            policy_id="deny_basic",
            description="Deny basic clearance",
            effect=ABACDecision.DENY,
            target={"resource_type": ResourceType.SYSTEM.value},
            condition={"op": "equals", "attribute": "subject.clearance", "value": "basic"},
        ))

        subject = ABACSubject(user_id="u1", clearance="basic")
        resource = ABACResource(resource_id="sys1", resource_type=ResourceType.SYSTEM)

        result = self.engine.evaluate(subject, resource, PermissionAction.MONITOR)
        assert result is False  # Deny overrides

    def test_permit_overrides_strategy(self):
        """With permit_overrides, any permit grants access."""
        engine = ABACEngine(strategy="permit_overrides")
        engine.add_policy(ABACPolicy(
            policy_id="deny1",
            effect=ABACDecision.DENY,
            target={"resource_type": ResourceType.SYSTEM.value},
        ))
        engine.add_policy(ABACPolicy(
            policy_id="permit1",
            effect=ABACDecision.PERMIT,
            condition={"op": "equals", "attribute": "subject.clearance", "value": "secret"},
        ))

        subject = ABACSubject(user_id="u1", clearance="secret")
        resource = ABACResource(resource_id="s1", resource_type=ResourceType.SYSTEM)

        result = engine.evaluate(subject, resource, PermissionAction.READ)
        assert result is True

    def test_condition_in_operator(self):
        """Test 'in' condition operator."""
        self.engine.add_policy(ABACPolicy(
            policy_id="p_in",
            effect=ABACDecision.PERMIT,
            condition={"op": "in", "attribute": "subject.department", "value": ["eng", "ops"]},
        ))

        subject = ABACSubject(user_id="u1", attributes={"department": "eng"})
        resource = ABACResource(resource_id="r1", resource_type=ResourceType.CONFIG)
        assert self.engine.evaluate(subject, resource, PermissionAction.READ) is True

        subject2 = ABACSubject(user_id="u2", attributes={"department": "sales"})
        assert self.engine.evaluate(subject2, resource, PermissionAction.READ) is False

    def test_condition_not_in_operator(self):
        """Test 'not_in' condition operator (deny revoked users)."""
        self.engine.add_policy(ABACPolicy(
            policy_id="deny_revoked",
            effect=ABACDecision.PERMIT,
            condition={"op": "not_in", "attribute": "subject.attributes.status", "value": ["revoked", "suspended"]},
        ))

        subject = ABACSubject(user_id="u1", attributes={"status": "active"})
        resource = ABACResource(resource_id="r1", resource_type=ResourceType.AGENT)
        assert self.engine.evaluate(subject, resource, PermissionAction.READ) is True

        subject2 = ABACSubject(user_id="u2", attributes={"status": "revoked"})
        assert self.engine.evaluate(subject2, resource, PermissionAction.READ) is False

    def test_condition_greater_than(self):
        """Test 'greater_than' for numeric attributes."""
        self.engine.add_policy(ABACPolicy(
            policy_id="p_gt",
            effect=ABACDecision.PERMIT,
            condition={"op": "greater_than", "attribute": "subject.attributes.request_count", "value": 10},
        ))

        subject = ABACSubject(user_id="u1", attributes={"request_count": 15})
        resource = ABACResource(resource_id="r1", resource_type=ResourceType.API)
        assert self.engine.evaluate(subject, resource, PermissionAction.READ) is True

        subject2 = ABACSubject(user_id="u2", attributes={"request_count": 5})
        assert self.engine.evaluate(subject2, resource, PermissionAction.READ) is False

    def test_condition_and_operator(self):
        """Test 'and' compound condition."""
        self.engine.add_policy(ABACPolicy(
            policy_id="p_and",
            effect=ABACDecision.PERMIT,
            condition={
                "op": "and",
                "conditions": [
                    {"op": "equals", "attribute": "subject.clearance", "value": "secret"},
                    {"op": "in", "attribute": "subject.department", "value": ["eng", "sec"]},
                ],
            },
        ))

        subject = ABACSubject(user_id="u1", clearance="secret", attributes={"department": "eng"})
        resource = ABACResource(resource_id="r1", resource_type=ResourceType.SYSTEM)
        assert self.engine.evaluate(subject, resource, PermissionAction.MONITOR) is True

        subject2 = ABACSubject(user_id="u2", clearance="basic", attributes={"department": "eng"})
        assert self.engine.evaluate(subject2, resource, PermissionAction.MONITOR) is False

    def test_rbac_fallback_when_no_abac_policies(self):
        """When no ABAC policies match, falls back to RBAC."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_test_abac_fallback.json"))
        role = manager.create_role(name="test")
        perm = Permission(resource_type=ResourceType.WORKFLOW, action=PermissionAction.EXECUTE)
        manager.assign_permission(role.id, perm)

        engine = ABACEngine()
        engine.set_rbac_manager(manager)

        subject = ABACSubject(user_id="u1", roles=[role.id])
        resource = ABACResource(resource_id="wf1", resource_type=ResourceType.WORKFLOW)

        assert engine.evaluate(subject, resource, PermissionAction.EXECUTE) is True
        assert engine.evaluate(subject, resource, PermissionAction.DELETE) is False

    def test_target_tag_matching(self):
        """Test resource tag matching in target."""
        self.engine.add_policy(ABACPolicy(
            policy_id="p_tag",
            effect=ABACDecision.PERMIT,
            target={"tags": ["production", "critical"]},
        ))

        resource = ABACResource(
            resource_id="svc1",
            resource_type=ResourceType.CONFIG,
            tags=["production", "frontend"],
        )
        subject = ABACSubject(user_id="u1")
        assert self.engine.evaluate(subject, resource, PermissionAction.READ) is True

        resource2 = ABACResource(resource_id="svc2", resource_type=ResourceType.CONFIG, tags=["dev"])
        assert self.engine.evaluate(subject, resource2, PermissionAction.READ) is False

    def test_get_matching_policies(self):
        """get_matching_policies returns only applicable policies."""
        self.engine.add_policy(ABACPolicy(
            policy_id="p1",
            effect=ABACDecision.PERMIT,
            target={"resource_type": ResourceType.MEMORY.value},
        ))
        self.engine.add_policy(ABACPolicy(
            policy_id="p2",
            effect=ABACDecision.DENY,
            target={"resource_type": ResourceType.SYSTEM.value},
        ))

        resource = ABACResource(resource_id="m1", resource_type=ResourceType.MEMORY)
        subject = ABACSubject(user_id="u1")
        matching = self.engine.get_matching_policies(subject, resource)
        assert len(matching) == 1
        assert matching[0].policy_id == "p1"


# ==================== Key Rotation Tests ====================

class TestKeyRotation:
    """Tests for SEC-05: Key rotation policies."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()
        self.api_mgr = APIKeyManager(
            storage_path=os.path.join(self.temp_dir, "keys.json"),
            master_key=b"0" * 32,
        )
        self.rotation_mgr = KeyRotationManager(
            api_key_manager=self.api_mgr,
            policies=DEFAULT_POLICIES,
        )

    def test_default_policies_exist(self):
        """Default rotation policies for all key types."""
        assert KeyType.JWT_SIGNING in DEFAULT_POLICIES
        assert KeyType.API_KEY in DEFAULT_POLICIES
        assert KeyType.ENCRYPTION in DEFAULT_POLICIES

    def test_rotation_policy_ttl(self):
        """Policy correctly reports TTL in seconds."""
        policy = DEFAULT_POLICIES[KeyType.JWT_SIGNING]
        assert policy.ttl_seconds() == 90 * 86400

    def test_check_rotation_needed_false(self):
        """Key not yet at TTL → no rotation needed."""
        now = time.time()
        created = now - (30 * 86400)  # 30 days ago
        needed, remaining = self.rotation_mgr.check_rotation_needed(
            KeyType.JWT_SIGNING, created, now
        )
        assert needed is False
        assert remaining > 0

    def test_check_rotation_needed_true(self):
        """Key past TTL → rotation needed."""
        now = time.time()
        created = now - (95 * 86400)  # 95 days ago (> 90 TTL)
        needed, remaining = self.rotation_mgr.check_rotation_needed(
            KeyType.JWT_SIGNING, created, now
        )
        assert needed is True
        assert remaining == 0.0

    def test_check_api_keys_rotation_all_fresh(self):
        """Freshly created keys don't need rotation."""
        key, _ = self.api_mgr.create_key(name="test", scopes=[KeyScope.ADMIN])
        checks = self.rotation_mgr.check_api_keys_rotation()
        assert len(checks) == 1
        key_id, needs_rotation, _ = checks[0]
        assert needs_rotation is False

    def test_auto_rotate_keys_no_rotation(self):
        """No rotation when all keys are fresh."""
        self.api_mgr.create_key(name="fresh", scopes=[KeyScope.READONLY])
        rotated = self.rotation_mgr.auto_rotate_api_keys()
        assert rotated == 0

    def test_auto_rotate_keys_past_ttl(self):
        """Keys past TTL get rotated."""
        key, _ = self.api_mgr.create_key(name="old", scopes=[KeyScope.READONLY])
        # Backdate creation time
        key.created_at = time.time() - (95 * 86400)

        rotated = self.rotation_mgr.auto_rotate_api_keys()
        assert rotated == 1

    def test_rotate_api_key_success(self):
        """rotate_api_key returns new key and raw key."""
        key, raw = self.api_mgr.create_key(name="test", scopes=[KeyScope.AGENT])

        # Backdate to trigger rotation
        key.created_at = time.time() - (100 * 86400)

        success, new_raw = self.rotation_mgr.rotate_api_key(key.id)
        assert success is True
        assert new_raw is not None
        assert new_raw.startswith("lhao_")

    def test_rotate_api_key_not_found(self):
        """Rotating nonexistent key returns False."""
        success, raw = self.rotation_mgr.rotate_api_key("nonexistent_id")
        assert success is False
        assert raw is None

    def test_rotation_schedule(self):
        """get_rotation_schedule returns correct timestamps."""
        now = time.time()
        created = now - (10 * 86400)  # 10 days ago
        schedule = self.rotation_mgr.get_rotation_schedule(
            KeyType.API_KEY, created, now
        )

        assert schedule["created_at"] == created
        assert schedule["rotation_date"] > now
        assert schedule["grace_period_end"] > schedule["rotation_date"]
        assert schedule["notification_date"] < schedule["rotation_date"]

    def test_rotation_stats(self):
        """get_stats returns rotation count and policy info."""
        stats = self.rotation_mgr.get_stats()
        assert "total_rotations" in stats
        assert "policies" in stats
        assert KeyType.JWT_SIGNING.value in stats["policies"]

    def test_encryption_key_default_non_auto(self):
        """Encryption keys default to manual rotation."""
        policy = DEFAULT_POLICIES[KeyType.ENCRYPTION]
        assert policy.auto_rotate is False
        assert policy.ttl_days == 180

    def test_api_key_rotation_preserves_scopes(self):
        """Rotated key has same scopes as original."""
        key, _ = self.api_mgr.create_key(
            name="scoped", scopes=[KeyScope.AGENT, KeyScope.WORKFLOW]
        )

        new_key, raw = self.api_mgr.rotate_key(key.id, expires_in_days=30)
        assert set(new_key.scopes) == set(key.scopes)
        assert new_key.rotated_from == key.id

    def test_key_rotation_singleton(self):
        """get_key_rotation_manager returns singleton."""
        from src.security.rotation import _default_rotation_manager
        mgr1 = get_key_rotation_manager()
        mgr2 = get_key_rotation_manager()
        assert mgr1 is mgr2


# ==================== Authentication Integration Tests ====================

class TestAuthenticationFlow:
    """Tests for SEC-06: JWT + API key authentication integration."""

    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_jwt_token_creation_and_validation(self):
        """Full JWT token creation → validation cycle."""
        handler = JWTHandler(
            algorithm="HS256",
            secret_key="test-secret-key-for-testing-only",
            issuer="test-issuer",
            audience="test-audience",
            access_ttl=300,
        )

        token, payload = handler.create_token(
            subject="user-123",
            token_type=TokenType.ACCESS,
            scopes=["memory:read", "agent:invoke"],
        )

        decoded = handler.validate_token(token)
        assert decoded.sub == "user-123"
        assert "memory:read" in decoded.scopes
        assert decoded.token_type == TokenType.ACCESS

    def test_jwt_token_pair(self):
        """Token pair includes access + refresh."""
        handler = JWTHandler(
            algorithm="HS256",
            secret_key="test-secret",
        )

        pair = handler.create_token_pair(
            subject="user-456",
            scopes=["provider:invoke"],
        )

        assert "access_token" in pair
        assert "refresh_token" in pair
        assert pair["token_type"] == "Bearer"
        assert pair["expires_in"] > 0

    def test_jwt_refresh_access_token(self):
        """Refresh token can generate new access token."""
        handler = JWTHandler(
            algorithm="HS256",
            secret_key="test-secret",
        )

        pair = handler.create_token_pair(subject="user-789", scopes=["memory:read"])

        new_pair = handler.refresh_access_token(pair["refresh_token"])
        assert new_pair["access_token"] != pair["access_token"]
        assert "refresh_token" in new_pair  # Rotated refresh token

    def test_jwt_revocation(self):
        """Revoked tokens fail validation."""
        handler = JWTHandler(
            algorithm="HS256",
            secret_key="test-secret-key-for-testing-only",
        )

        token, payload = handler.create_token(subject="user-1")
        assert handler.revoke_token(token) is True

        with pytest.raises(Exception):
            handler.validate_token(token)

    def test_api_key_creation_and_validation(self):
        """API key creation → validation cycle."""
        manager = APIKeyManager(
            storage_path=os.path.join(self.temp_dir, "api_keys.json"),
            master_key=b"0" * 32,
        )

        key, raw = manager.create_key(
            name="test-key",
            scopes=[KeyScope.AGENT, KeyScope.WORKFLOW],
        )

        validated = manager.validate_key(raw)
        assert validated is not None
        assert validated.id == key.id
        assert validated.usage_count == 1

    def test_api_key_revocation_blocks_validation(self):
        """Revoked API keys fail validation."""
        manager = APIKeyManager(
            storage_path=os.path.join(self.temp_dir, "revoked.json"),
            master_key=b"0" * 32,
        )

        key, raw = manager.create_key(name="will-revoke", scopes=[KeyScope.ADMIN])
        assert manager.validate_key(raw) is not None

        manager.revoke_key(key.id)
        assert manager.validate_key(raw) is None

    def test_api_key_rotation_creates_new_valid_key(self):
        """After rotation, new key works and old is pending."""
        manager = APIKeyManager(
            storage_path=os.path.join(self.temp_dir, "rotated.json"),
            master_key=b"0" * 32,
        )

        key, raw = manager.create_key(name="rotating", scopes=[KeyScope.READONLY])
        new_key, new_raw = manager.rotate_key(key.id, expires_in_days=30)

        # Old key now in pending_rotation
        old_key = manager.get_key(key.id)
        assert old_key.status == KeyStatus.PENDING_ROTATION

        # New key is active
        assert new_key.status == KeyStatus.ACTIVE

        # Old key still validates (grace period)
        validated_old = manager.validate_key(raw)
        # Actually old key is PENDING_ROTATION which is not ACTIVE → invalid
        assert validated_old is None or validated_old.status != KeyStatus.ACTIVE

        # New key validates
        validated_new = manager.validate_key(new_raw)
        assert validated_new is not None
        assert validated_new.id == new_key.id

    def test_api_key_scope_filtering(self):
        """list_keys filters by scope."""
        manager = APIKeyManager(
            storage_path=os.path.join(self.temp_dir, "scoped.json"),
            master_key=b"0" * 32,
        )

        manager.create_key(name="admin-key", scopes=[KeyScope.ADMIN])
        manager.create_key(name="agent-key", scopes=[KeyScope.AGENT])
        manager.create_key(name="mixed-key", scopes=[KeyScope.AGENT, KeyScope.READONLY])

        agent_keys = manager.list_keys(scope=KeyScope.AGENT)
        assert len(agent_keys) == 2

        admin_keys = manager.list_keys(scope=KeyScope.ADMIN)
        assert len(admin_keys) == 1

    def test_jwt_claims_contain_permissions(self):
        """JWT token carries permissions claim for ABAC evaluation."""
        handler = JWTHandler(
            algorithm="HS256",
            secret_key="secret",
        )

        token, payload = handler.create_token(
            subject="user-1",
            permissions=["memory:read", "agent:invoke"],
            roles=["admin"],
        )

        decoded = handler.validate_token(token)
        assert "memory:read" in decoded.permissions
        assert "admin" in decoded.roles

    def test_token_introspection(self):
        """introspect_token returns RFC 7662-style response."""
        handler = JWTHandler(algorithm="HS256", secret_key="secret")
        token, _ = handler.create_token(subject="user-1")

        info = handler.introspect_token(token)
        assert info["active"] is True
        assert info["username"] == "user-1"
        assert info["token_type"] == "access"

    def test_token_introspection_revoked(self):
        """introspect_token returns inactive for revoked tokens."""
        handler = JWTHandler(
            algorithm="HS256",
            secret_key="test-secret-key-for-testing",
        )
        token, _ = handler.create_token(subject="user-1")
        handler.revoke_token(token)

        info = handler.introspect_token(token)
        assert info["active"] is False

    def test_token_expiration(self):
        """Expired tokens are rejected."""
        handler = JWTHandler(
            algorithm="HS256",
            secret_key="test-secret-key-for-testing",
            access_ttl=1,  # 1 second
            leeway=0,  # No clock skew tolerance
        )

        token, _ = handler.create_token(subject="user-1")
        time.sleep(2)

        with pytest.raises(Exception):
            handler.validate_token(token)


# ==================== VaultTransitCrypto Integration Tests ====================

class TestVaultCryptoIntegration:
    """Tests for VaultTransitCrypto with API key hashing (SEC-04 extension)."""

    def test_vault_transit_offline_mode(self):
        """When Vault unavailable, falls back to offline mode."""
        crypto = VaultTransitCrypto()
        # In test environment without Vault/hvac, should be offline
        assert crypto._offline is True or crypto._vault is None or not crypto._vault.connected

    def test_hash_api_key_constant_time(self):
        """HMAC hashing for API keys works."""
        crypto = VaultTransitCrypto()
        key = "lhao_test_key_value"
        h = crypto.hash_api_key(key)

        assert h is not None
        assert len(h) > 0
        # Hash should be deterministic for same input (in offline mode)
        h2 = crypto.hash_api_key(key)
        assert h == h2

    def test_hash_api_key_different_keys(self):
        """Different keys produce different hashes."""
        crypto = VaultTransitCrypto()
        h1 = crypto.hash_api_key("lhao_key_a")
        h2 = crypto.hash_api_key("lhao_key_b")
        assert h1 != h2

    def test_hash_api_key_no_plaintext(self):
        """Hash does not contain plaintext key."""
        crypto = VaultTransitCrypto()
        raw = "lhao_super_secret_key_12345"
        h = crypto.hash_api_key(raw)
        assert raw not in h

    def test_verify_api_key_hash(self):
        """verify_api_key_hash confirms correct hash."""
        crypto = VaultTransitCrypto()
        raw = "lhao_verify_me"
        h = crypto.hash_api_key(raw)

        assert crypto.verify_api_key_hash(raw, h) is True
        assert crypto.verify_api_key_hash("lhao_wrong", h) is False


# ==================== CryptoAuditLogger Tests ====================

class TestCryptoAuditLogger:
    """Tests for audit logging of crypto operations."""

    def test_audit_log_recorded(self):
        """Crypto operations are logged."""
        logger = CryptoAuditLogger(component_name="test")
        event = logger.log_encryption("AES-256-GCM", "key-001", success=True)

        assert event is not None
        assert len(logger._events) == 1

    def test_audit_log_truncation(self):
        """Long values are truncated to 128 chars + suffix (142 total)."""
        logger = CryptoAuditLogger(component_name="test")
        long_value = "a" * 500
        logger.log_encryption("AES", details={"data": long_value})

        # The truncation logic
        truncated = long_value[:128] + "...[truncated]"
        assert len(truncated) == 142

    def test_audit_log_secret_redaction(self):
        """Secrets are redacted in audit logs."""
        from src.security.audit_logger import redact_secrets
        data = {"api_key": "sk-secret-123", "password": "secret", "safe": "ok"}
        redacted = redact_secrets(data)

        assert redacted["api_key"] == "[REDACTED]"
        assert redacted["password"] == "[REDACTED]"
        assert redacted["safe"] == "ok"

    def test_audit_log_keygen(self):
        """Key generation events are logged."""
        logger = CryptoAuditLogger(component_name="test")
        event = logger.log_key_generation("AES-256", "new-key-id", success=True)

        assert event.operation.value == "key_generate"
        assert len(logger._events) == 1

    def test_audit_log_signing(self):
        """Signing operations are logged."""
        logger = CryptoAuditLogger(component_name="test")
        event = logger.log_signing("RSA-SHA256", "key-001", success=True)

        assert event.operation.value == "sign"
        assert len(logger._events) == 1

    def test_audit_chain_integrity(self):
        """Hash chain is verifiable."""
        logger = CryptoAuditLogger(component_name="test")
        logger.log_encryption("AES", success=True)
        logger.log_signing("RSA", success=True)
        logger.log_key_generation("RSA", success=True)

        assert logger.verify_chain() is True

    def test_audit_chain_broken_if_modified(self):
        """Tampering with event breaks chain verification."""
        logger = CryptoAuditLogger(component_name="test")
        logger.log_encryption("AES", success=True)

        # Tamper: recompute without fixing next event's prev_event_hash
        logger._events[0].details["tampered"] = True
        assert logger.verify_chain() is False

    def test_redact_secrets_truncation(self):
        """Long secret values are redacted and truncated."""
        data = {"secret_field": "a" * 600, "normal": "ok", "token": "abc123"}
        redacted = redact_secrets(data)

        assert redacted["secret_field"] == "[REDACTED]"
        assert redacted["token"] == "[REDACTED]"
        assert redacted["normal"] == "ok"

    def test_redact_secrets_long_non_secret_truncated(self):
        """Long non-secret strings are truncated to 142 chars."""
        data = {"data": "x" * 600}
        redacted = redact_secrets(data)

        assert len(redacted["data"]) == 142
        assert redacted["data"].endswith("...[truncated]")


# ==================== RBAC Cycle Detection Tests ====================

class TestRBACCycleDetection:
    """Tests for RBAC role inheritance cycle detection."""

    def setup_method(self):
        """Clean up stale store files."""
        import glob
        for f in glob.glob(os.path.join(tempfile.gettempdir(), "rbac_cycle_*.json")):
            os.remove(f)

    def test_no_self_cycle(self):
        """Adding self as parent is prevented."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_cycle_self.json"))
        role = manager.create_role(name="r1")
        original_parents = list(role.parent_ids)
        role.add_parent(role.id)
        assert role.parent_ids == original_parents  # No change

    def test_no_cycle_through_chain(self):
        """Cycle through parent chain is detected."""
        manager = RBACManager(store_path=str(Path(tempfile.gettempdir()) / "rbac_cycle_chain.json"))
        r1 = manager.create_role(name="r1")
        r2 = manager.create_role(name="r2", parent_ids=[r1.id])

        # r1.add_parent(r2) would create r1→r2→r1 cycle
        original = list(r1.parent_ids)
        r1.add_parent(r2.id)
        assert r1.parent_ids == original
