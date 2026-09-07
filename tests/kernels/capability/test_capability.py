"""Capability Kernel unit tests.

Covers: registry register/lookup/query, scope hierarchy checks,
deprecate/retire lifecycle, owner/namespace/tag indexes, deprecated
alias resolution, and the global builtin registry.

Defect-evidence test (``test_defect_*``) asserts behavior REQUIRED by the
Definition Lock but currently violated; marked ``xfail`` and documented.
"""
import pytest

from src.kernels.capability import (
    CapabilityEntry,
    CapabilityQuery,
    CapabilityRegistry,
    CapabilityScope,
    CapabilityStatus,
    get_capability_registry,
    lookup_capability,
    register_capability,
)


@pytest.fixture
def reg() -> CapabilityRegistry:
    return CapabilityRegistry()


def make_entry(
    id="c1",
    ns="core",
    ver="1.0.0",
    scope=CapabilityScope.L3,
    owner="o",
    status=CapabilityStatus.ACTIVE,
    tags=None,
):
    return CapabilityEntry(
        id=id,
        version=ver,
        namespace=ns,
        name=id,
        description="desc",
        scope=scope,
        owner=owner,
        status=status,
        tags=tags or set(),
    )


# =====================================================================
# Registration
# =====================================================================

class TestRegister:
    def test_register_new_returns_true(self, reg):
        assert reg.register(make_entry()) is True

    def test_register_same_returns_false(self, reg):
        e = make_entry()
        assert reg.register(e) is True
        # Re-registering the same full_id is an "update"
        assert reg.register(e) is False

    def test_count_reflects_registrations(self, reg):
        reg.register(make_entry(id="a"))
        reg.register(make_entry(id="b"))
        assert reg.count() == 2


# =====================================================================
# Lookup
# =====================================================================

class TestLookup:
    def test_lookup_by_id_and_version(self, reg):
        reg.register(make_entry(id="x", ver="1.0.0"))
        e = reg.lookup("x", "core", "1.0.0")
        assert e is not None
        assert e.version == "1.0.0"

    def test_lookup_latest_active_version(self, reg):
        reg.register(make_entry(id="x", ver="1.0.0"))
        reg.register(make_entry(id="x", ver="2.0.0"))
        e = reg.lookup("x", "core")
        assert e.version == "2.0.0"

    def test_lookup_excludes_deprecated(self, reg):
        reg.register(make_entry(id="x", ver="1.0.0", status=CapabilityStatus.DEPRECATED))
        reg.register(make_entry(id="x", ver="2.0.0"))
        e = reg.lookup("x", "core")
        assert e is not None
        assert e.version == "2.0.0"

    def test_lookup_unknown_returns_none(self, reg):
        assert reg.lookup("nope", "core") is None


# =====================================================================
# Query
# =====================================================================

class TestQuery:
    def test_query_by_owner(self, reg):
        reg.register(make_entry(id="a", owner="o1"))
        reg.register(make_entry(id="b", owner="o2"))
        res = reg.query(CapabilityQuery(owner="o1"))
        assert len(res) == 1 and res[0].id == "a"

    def test_query_by_scope(self, reg):
        reg.register(make_entry(id="a", scope=CapabilityScope.L3))
        reg.register(make_entry(id="b", scope=CapabilityScope.L5))
        res = reg.query(CapabilityQuery(scope=CapabilityScope.L5))
        assert len(res) == 1 and res[0].id == "b"

    def test_query_by_tag(self, reg):
        reg.register(make_entry(id="a", tags={"t1"}))
        reg.register(make_entry(id="b", tags={"t2"}))
        res = reg.query(CapabilityQuery(tags={"t1"}))
        assert len(res) == 1 and res[0].id == "a"


# =====================================================================
# Scope checking
# =====================================================================

class TestScopeCheck:
    def test_scope_allowed_when_requested_lower(self, reg):
        reg.register(make_entry(id="x", scope=CapabilityScope.L3))
        res = reg.check_scope("x", CapabilityScope.L2)
        assert res.allowed is True
        assert res.capability_scope is CapabilityScope.L3

    def test_scope_denied_when_requested_higher(self, reg):
        reg.register(make_entry(id="x", scope=CapabilityScope.L3))
        res = reg.check_scope("x", CapabilityScope.L5)
        assert res.allowed is False
        assert "exceeds" in res.reason

    def test_scope_not_found(self, reg):
        res = reg.check_scope("nope", CapabilityScope.L3)
        assert res.allowed is False
        assert "not found" in res.reason


# =====================================================================
# Lifecycle: deprecate / retire
# =====================================================================

class TestLifecycle:
    def test_deprecate(self, reg):
        reg.register(make_entry(id="x"))
        assert reg.deprecate("x", "core") is True
        e = reg.lookup("x", "core", "1.0.0")
        assert e.status == CapabilityStatus.DEPRECATED
        assert e.deprecated_at is not None

    def test_retire(self, reg):
        reg.register(make_entry(id="x"))
        assert reg.retire("x", "core") is True
        e = reg.lookup("x", "core", "1.0.0")
        assert e.status == CapabilityStatus.RETIRED

    def test_deprecate_unknown_returns_false(self, reg):
        assert reg.deprecate("ghost", "core") is False

    def test_count_active_excludes_non_active(self, reg):
        reg.register(make_entry(id="a", status=CapabilityStatus.ACTIVE))
        reg.register(make_entry(id="b", status=CapabilityStatus.DEPRECATED))
        assert reg.count() == 2
        assert reg.count_active() == 1


# =====================================================================
# Indexes
# =====================================================================

class TestIndexes:
    def test_get_all_by_owner(self, reg):
        reg.register(make_entry(id="x", owner="o1"))
        assert len(reg.get_all_by_owner("o1")) == 1

    def test_get_all_by_namespace(self, reg):
        reg.register(make_entry(id="x", ns="ns1"))
        assert len(reg.get_all_by_namespace("ns1")) == 1

    def test_get_all_by_tag(self, reg):
        reg.register(make_entry(id="x", tags={"t"}))
        assert len(reg.get_all_by_tag("t")) == 1


# =====================================================================
# Deprecated alias resolution
# =====================================================================

class TestDeprecationAlias:
    def test_resolve_deprecated(self, reg):
        reg.register(make_entry(id="x"))
        reg.deprecate("x", "core", replacement_id="y", replacement_namespace="core")
        e = reg.lookup("x", "core", "1.0.0")
        resolved = reg.resolve_deprecated(e.full_id)
        assert resolved is not None
        assert "y" in resolved


# =====================================================================
# CapabilityEntry helpers
# =====================================================================

class TestEntryHelpers:
    def test_full_id_format(self):
        e = make_entry(id="x", ns="core", ver="2.1.0")
        assert e.full_id == "core.x@v2.1.0"

    def test_traceability_chain_default(self):
        e = make_entry(id="x", ns="core", ver="2.1.0")
        assert e.traceability_chain == ["o", "x", "v2.1.0"]

    def test_is_compatible_with_same_major(self):
        e1 = make_entry(ver="2.1.0")
        e2 = make_entry(ver="2.5.0")
        assert e1.is_compatible_with(e2) is True

    def test_is_compatible_with_diff_major(self):
        e1 = make_entry(ver="2.1.0")
        e3 = make_entry(ver="3.0.0")
        assert e1.is_compatible_with(e3) is False


# =====================================================================
# Global registry / builtins
# =====================================================================

class TestGlobalRegistry:
    def test_global_registry_has_12_builtins(self):
        g = get_capability_registry()
        assert g.count_active() == 12

    def test_global_lookup_builtin_by_explicit_namespace(self):
        g = get_capability_registry()
        e = g.lookup("capability_registry", "kernel", "1.0.0")
        assert e is not None
        assert e.owner == "capability_kernel"

    def test_register_capability_convenience(self):
        e = register_capability(
            "custom1", "1.0.0", "core", "Custom", "desc",
            CapabilityScope.L3, "someowner",
        )
        assert e.id == "custom1"
        assert get_capability_registry().lookup("custom1", "core") is not None

    def test_defect_builtin_lookup_default_namespace(self):
        # Required by spec: convenience lookup should find builtins.
        e = lookup_capability("capability_registry")
        assert e is not None
