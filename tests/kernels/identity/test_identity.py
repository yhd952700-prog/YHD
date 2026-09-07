"""Identity Kernel unit tests.

Covers: identity creation, permission grant/revoke/check, scope-aware
filtering (L0-L7), audit trail, and statistics.

Defect-evidence tests (names prefixed with ``test_defect_``) assert the
behavior REQUIRED BY SPEC (Definition Lock section 112). They are
expected to fail until the kernel is fixed; each failure is a recorded
defect.
"""
import pytest

from src.kernels.identity import (
    AgentIdentity,
    AuditEntry,
    IdentityManager,
    IdentityScope,
    IdentityStatus,
    get_identity_manager,
)


@pytest.fixture
def manager() -> IdentityManager:
    """Fresh manager per test (contains only the default system identity)."""
    return IdentityManager()


def make_identity(manager, principal="agent-1", scope=IdentityScope.L1,
                  permissions=None, trust=0.5):
    return manager.create_identity(principal, permissions, scope, trust)


# =====================================================================
# Identity creation
# =====================================================================

class TestCreateIdentity:
    def test_default_system_identity_exists(self, manager):
        system = manager.get_identity("system")
        assert system is not None
        assert system.principal == "system"
        assert system.permissions == {"admin"}
        assert system.scope is IdentityScope.L0
        assert system.trust_score == 1.0
        assert system.status is IdentityStatus.ACTIVE

    def test_create_defaults(self, manager):
        ident = make_identity(manager)
        assert ident.principal == "agent-1"
        assert ident.permissions == set()
        assert ident.scope is IdentityScope.L1
        assert ident.trust_score == 0.5
        assert ident.status is IdentityStatus.ACTIVE
        assert ident.id != "system"
        assert ident.correlation_id

    def test_create_with_permissions_scope_trust(self, manager):
        ident = make_identity(
            manager, principal="op", scope=IdentityScope.L3,
            permissions={"read"}, trust=0.9,
        )
        assert ident.permissions == {"read"}
        assert ident.scope is IdentityScope.L3
        assert ident.trust_score == 0.9

    def test_create_clamps_trust_score(self, manager):
        assert make_identity(manager, principal="trust-hi", trust=2.0).trust_score == 1.0
        assert make_identity(manager, principal="trust-lo", trust=-0.5).trust_score == 0.0

    def test_create_records_audit_entry(self, manager):
        ident = make_identity(manager, principal="audited")
        trail = manager.audit_trail(ident.id)
        creates = [e for e in trail if e.operation == "create"]
        assert len(creates) == 1
        assert creates[0].result == "allowed"
        assert "audited" in creates[0].reason

    def test_create_generates_unique_ids(self, manager):
        a = make_identity(manager, principal="pa")
        b = make_identity(manager, principal="pb")
        assert a.id != b.id

    def test_get_identity_missing_returns_none(self, manager):
        assert manager.get_identity("nope") is None


class TestListIdentities:
    def test_list_includes_system_and_created(self, manager):
        make_identity(manager, principal="a")
        idents = manager.list_identities()
        principals = {i.principal for i in idents}
        assert {"system", "a"} <= principals

    def test_list_scope_filter_returns_at_or_above(self, manager):
        make_identity(manager, principal="low", scope=IdentityScope.L1)
        make_identity(manager, principal="mid", scope=IdentityScope.L3)
        make_identity(manager, principal="high", scope=IdentityScope.L5)
        result = manager.list_identities(scope=IdentityScope.L3)
        principals = {i.principal for i in result}
        # system is L0 and must be excluded; L3/L5 included; L1 excluded
        assert principals == {"mid", "high"}


# =====================================================================
# Permission grant / revoke / check
# =====================================================================

class TestGrantPermission:
    def test_grant_within_scope_succeeds(self, manager):
        ident = make_identity(manager)
        assert manager.grant_permission(ident.id, "doc:read") is True
        assert "doc:read" in ident.permissions

    def test_grant_records_allowed_audit(self, manager):
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "doc:read", reason="onboarding")
        trail = manager.audit_trail(ident.id)
        grants = [e for e in trail
                  if e.operation == "grant" and e.permission == "doc:read"]
        assert len(grants) == 1
        assert grants[0].result == "allowed"
        assert grants[0].reason == "onboarding"

    def test_grant_unknown_identity_returns_false(self, manager):
        assert manager.grant_permission("ghost", "doc:read") is False

    def test_grant_scope_above_identity_denied(self, manager):
        ident = make_identity(manager, scope=IdentityScope.L1)
        result = manager.grant_permission(ident.id, "doc:read",
                                          scope=IdentityScope.L3)
        assert result is False
        assert "doc:read" not in ident.permissions
        trail = manager.audit_trail(ident.id)
        denied = [e for e in trail if e.result == "denied"]
        assert len(denied) == 1
        assert "exceeds" in denied[0].reason

    def test_grant_at_identity_scope_ok(self, manager):
        ident = make_identity(manager, scope=IdentityScope.L3)
        assert manager.grant_permission(ident.id, "doc:read",
                                        scope=IdentityScope.L3) is True

    def test_grant_below_identity_scope_ok(self, manager):
        ident = make_identity(manager, scope=IdentityScope.L3)
        assert manager.grant_permission(ident.id, "doc:read",
                                        scope=IdentityScope.L1) is True


class TestRevokePermission:
    def test_revoke_held_permission(self, manager):
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "doc:read")
        assert manager.revoke_permission(ident.id, "doc:read") is True
        assert "doc:read" not in ident.permissions

    def test_revoke_records_audit(self, manager):
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "doc:read")
        manager.revoke_permission(ident.id, "doc:read", reason="audit finding")
        trail = manager.audit_trail(ident.id)
        revokes = [e for e in trail if e.operation == "revoke"]
        assert len(revokes) == 1
        assert revokes[0].result == "allowed"
        assert revokes[0].reason == "audit finding"

    def test_revoke_unheld_permission_returns_false(self, manager):
        ident = make_identity(manager)
        assert manager.revoke_permission(ident.id, "never:granted") is False

    def test_revoke_unknown_identity_returns_false(self, manager):
        assert manager.revoke_permission("ghost", "doc:read") is False


class TestCheckPermission:
    def test_check_held_permission_true(self, manager):
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "doc:read")
        assert manager.check_permission(ident.id, "doc:read") is True

    def test_check_unheld_permission_false(self, manager):
        ident = make_identity(manager)
        assert manager.check_permission(ident.id, "doc:read") is False

    def test_check_unknown_identity_false(self, manager):
        assert manager.check_permission("ghost", "doc:read") is False

    def test_check_scope_above_identity_false(self, manager):
        ident = make_identity(manager, scope=IdentityScope.L1)
        manager.grant_permission(ident.id, "doc:read")  # granted at L1
        # requesting the permission at a scope above the identity must fail
        assert manager.check_permission(
            ident.id, "doc:read", scope=IdentityScope.L4
        ) is False

    def test_system_identity_admin_check(self, manager):
        # system identity lives at L0 (human only); its permissions are
        # exercisable at L0 and correctly refused at higher scopes
        assert manager.check_permission(
            "system", "admin", scope=IdentityScope.L0
        ) is True
        assert manager.check_permission(
            "system", "admin", scope=IdentityScope.L1
        ) is False


# =====================================================================
# Audit trail
# =====================================================================

class TestAuditTrail:
    def test_filter_by_identity(self, manager):
        a = make_identity(manager, principal="a")
        b = make_identity(manager, principal="b")
        manager.grant_permission(a.id, "doc:read")
        manager.grant_permission(b.id, "doc:read")
        trail = manager.audit_trail(a.id)
        assert trail
        assert all(e.identity_id == a.id for e in trail)

    def test_filter_by_scope(self, manager):
        ident = make_identity(manager, scope=IdentityScope.L1)
        manager.grant_permission(ident.id, "doc:read",
                                 scope=IdentityScope.L1)
        # entry recorded at L1: visible from L0, hidden from L2+
        assert len(manager.audit_trail(ident.id, scope=IdentityScope.L0)) >= 1
        assert manager.audit_trail(ident.id, scope=IdentityScope.L2) == []

    def test_filter_by_since(self, manager):
        from datetime import datetime, timedelta
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "doc:read")
        future = datetime.utcnow() + timedelta(hours=1)
        assert manager.audit_trail(ident.id, since=future) == []
        past = datetime.utcnow() - timedelta(hours=1)
        assert len(manager.audit_trail(ident.id, since=past)) >= 1

    def test_trail_sorted_newest_first(self, manager):
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "a")
        manager.grant_permission(ident.id, "b")
        trail = manager.audit_trail(ident.id)
        timestamps = [e.timestamp for e in trail]
        assert timestamps == sorted(timestamps, reverse=True)


# =====================================================================
# Stats / singleton / convenience
# =====================================================================

class TestStatsAndGlobals:
    def test_stats_counts(self, manager):
        make_identity(manager, principal="a", scope=IdentityScope.L2)
        stats = manager.stats()
        assert stats["total_identities"] == 2  # system + a
        assert stats["active_identities"] == 2
        assert stats["identities_by_scope"]["L0"] == 1
        assert stats["identities_by_scope"]["L2"] == 1
        # NOTE: only the explicitly created identity is audited; the
        # default system identity is seeded in __init__ without an
        # audit entry (recorded as an observation for the backlog).
        assert stats["total_audit_entries"] == 1

    def test_get_identity_manager_singleton(self):
        assert get_identity_manager() is get_identity_manager()

    def test_create_identity_with_permissions_convenience(self):
        from src.kernels.identity import create_identity_with_permissions
        ident = create_identity_with_permissions(
            "convenience-bot", {"tool:run"}, scope=IdentityScope.L2
        )
        assert "tool:run" in ident.permissions
        assert get_identity_manager().check_permission(
            ident.id, "tool:run", scope=IdentityScope.L2
        ) is True


# =====================================================================
# Defect-evidence tests (expected to FAIL until kernel fixed)
# Spec basis: Definition Lock section 112.
# =====================================================================

class TestDefects:
    def test_defect_principal_uniqueness_enforced(self, manager):
        """DEFECT ID-1: principal uniqueness is not enforced.

        AgentIdentity.principal is documented as the "Unique principal
        identifier", but create_identity happily creates multiple
        identities for the same principal, making permission lookups
        ambiguous. Expected: creating a second identity for an existing
        principal is rejected.
        """
        make_identity(manager, principal="dup-agent")
        second = manager.create_identity("dup-agent")
        assert second is None or second.id is None, (
            "duplicate principal accepted"
        )

    def test_defect_suspended_identity_cannot_act(self, manager):
        """DEFECT ID-2: identity status is never enforced.

        IdentityStatus defines SUSPENDED / DEACTIVATED lifecycle states,
        but check_permission ignores status entirely -- a suspended
        identity retains full access. (There is also no API to change
        status; the test sets it directly on the dataclass.)
        Expected: a suspended identity fails permission checks.
        """
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "doc:read")
        ident.status = IdentityStatus.SUSPENDED
        assert manager.check_permission(ident.id, "doc:read") is False, (
            "suspended identity still passes permission checks"
        )

    def test_defect_deactivated_identity_cannot_act(self, manager):
        """DEFECT ID-2 (variant): deactivated identity retains access."""
        ident = make_identity(manager)
        manager.grant_permission(ident.id, "doc:read")
        ident.status = IdentityStatus.DEACTIVATED
        assert manager.check_permission(ident.id, "doc:read") is False, (
            "deactivated identity still passes permission checks"
        )
