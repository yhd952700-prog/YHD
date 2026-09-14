"""The colon-permission registry is complete, truthful, and now observable.

Companion to ``src/kernels/security/_permission_map.py``. Three claims:

1. **Complete.** Every permission the Security Kernel actually seeds, and
   every permission literal production code actually asks for, has a registry
   entry. Both sides are read from the *real* sources at test time (the live
   ``_rbac_rules`` table and an AST scan), so the registry cannot drift.
2. **Truthful.** Every ``role`` matches the real seed rule, and every name in
   ``kernel_actions`` is a real decorated ``@kernel_action`` -- nothing here
   is asserted into existence.
3. **Observable.** ``decide_access`` now distinguishes "checked and refused"
   from "this permission can never be granted". That distinction is the whole
   point: before this, both looked like a plain deny.

Verdicts are deliberately unchanged -- this is the mapping half of the
convergence, not the switch.
"""

from __future__ import annotations

import pytest

from src.kernels.security import RBACRole, SecurityEngine, get_security_engine
from src.kernels.security._permission_map import (
    PERMISSION_MAP,
    discover_permission_literals,
    get_mapping,
    is_known,
    is_seeded,
    unmapped_permissions,
    unseeded_permissions,
)


@pytest.fixture
def engine() -> SecurityEngine:
    return SecurityEngine()


def _all_roles(engine: SecurityEngine, principal: str) -> str:
    engine.set_principal_roles(
        principal,
        {
            RBACRole.ADMIN,
            RBACRole.OPERATOR,
            RBACRole.VIEWER,
            RBACRole.AUDITOR,
            RBACRole.SERVICE,
        },
    )
    return principal


# --------------------------------------------------------------------------- #
# 1. completeness against the REAL seed table
# --------------------------------------------------------------------------- #


class TestRegistryCoversTheSeededPermissions:
    def test_every_seeded_permission_is_registered(self, engine):
        seeded = {rule.permission for rule in engine._rbac_rules.values()}
        assert seeded, "种子表为空，测试本身失效"
        missing = sorted(seeded - set(PERMISSION_MAP))
        assert not missing, f"已播种但未登记的权限：{missing}"

    def test_the_registered_role_matches_the_real_seed_rule(self, engine):
        """The registry must describe the rules that actually exist."""
        for permission, rule in engine._rbac_rules.items():
            entry = PERMISSION_MAP.get(permission)
            assert entry is not None, permission
            assert entry.role == rule.role.value, (
                f"{permission}: 登记 role={entry.role!r}，实际种子 rule={rule.role.value!r}"
            )

    def test_no_stale_entries_that_are_no_longer_seeded(self, engine):
        seeded = {rule.permission for rule in engine._rbac_rules.values()}
        # ``research:read`` is intentionally registered while unseeded: it is
        # asked for by production code and must stay visible.
        stale = sorted(set(PERMISSION_MAP) - seeded - {"research:read"})
        assert not stale, f"注册表里有已不再播种的权限：{stale}"


# --------------------------------------------------------------------------- #
# 2. kernel_actions must be real actions in authority B
# --------------------------------------------------------------------------- #


class TestMappedActionsReallyExist:
    def test_every_mapped_action_is_a_decorated_kernel_action(self):
        from src.kernels._risk_classification import discover_kernel_action_names

        real = discover_kernel_action_names()
        bogus = sorted(
            action
            for entry in PERMISSION_MAP.values()
            for action in entry.kernel_actions
            if action not in real
        )
        assert not bogus, f"映射到了不存在的内核动作：{bogus}"

    def test_at_least_one_permission_maps_cleanly(self):
        """Sanity: the scanner above is not passing because nothing maps."""
        mapped = [p for p, m in PERMISSION_MAP.items() if m.kernel_actions]
        assert mapped, "没有任何权限映射到 B，交叉校验形同虚设"

    def test_the_unmapped_are_reported_not_hidden(self):
        """B is all-effectful; A is heavy on reads. The gap must be visible."""
        assert unmapped_permissions(), "声称全部有对应，与实测不符"
        for permission in unmapped_permissions():
            assert PERMISSION_MAP[permission].note, f"{permission} 缺说明"


# --------------------------------------------------------------------------- #
# 3. completeness against what production code actually asks for
# --------------------------------------------------------------------------- #


class TestProductionCallSitesAreRegistered:
    def test_the_discovery_scanner_finds_something(self):
        """A guard that scans nothing proves nothing."""
        assert discover_permission_literals(), "AST 未扫到任何权限字面量"

    def test_the_benchmarks_research_read_is_discovered(self):
        assert "research:read" in discover_permission_literals()

    def test_audit_fields_are_not_mistaken_for_permissions(self):
        """``audit_log(permission="plugin:manage")`` stamps a string; it is
        never checked against anything, so it must not reach the registry."""
        discovered = discover_permission_literals()
        assert "plugin:manage" not in discovered

    def test_every_discovered_literal_is_registered(self):
        discovered = discover_permission_literals()
        missing = sorted(p for p in discovered if not is_known(p))
        assert not missing, f"代码里用到但未登记的权限：{missing}"


# --------------------------------------------------------------------------- #
# 4. the unseeded permission is a known, reported gap -- not a silent one
# --------------------------------------------------------------------------- #


class TestTheUnseededPermissionIsVisible:
    def test_research_read_is_registered_but_unseeded(self):
        assert is_known("research:read")
        assert not is_seeded("research:read")
        assert "research:read" in unseeded_permissions()

    def test_it_denies_and_says_why(self, engine, caplog):
        principal = _all_roles(engine, "map-probe-all-roles")
        with caplog.at_level("WARNING"):
            result = engine.decide_access(principal, "research:read")

        assert result["decision"] == "deny"
        assert result["permission_known"] is True
        assert result["permission_seeded"] is False
        assert "UNSEEDED" in caplog.text

    def test_an_unregistered_permission_denies_and_says_why(self, engine, caplog):
        principal = _all_roles(engine, "map-probe-unknown")
        with caplog.at_level("WARNING"):
            result = engine.decide_access(principal, "totally:madeup")

        assert result["decision"] == "deny"
        assert result["permission_known"] is False
        assert result["kernel_actions"] == []
        assert "UNREGISTERED" in caplog.text

    def test_a_seeded_permission_is_quiet_and_reports_its_mapping(
        self, engine, caplog
    ):
        principal = _all_roles(engine, "map-probe-seeded")
        with caplog.at_level("WARNING"):
            result = engine.decide_access(principal, "resource:allocate")

        assert result["decision"] == "allow"
        assert result["permission_known"] is True
        assert result["permission_seeded"] is True
        assert result["kernel_actions"] == ["resource.allocate"]
        assert "UNSEEDED" not in caplog.text
        assert "UNREGISTERED" not in caplog.text


# --------------------------------------------------------------------------- #
# 5. verdicts did not move (this round only adds visibility)
# --------------------------------------------------------------------------- #


class TestVerdictsAreUnchanged:
    def test_a_principal_without_the_role_is_still_denied(self, engine):
        engine.set_principal_roles("map-probe-viewer", {RBACRole.VIEWER})
        assert engine.decide_access("map-probe-viewer", "context:read")["decision"] == "allow"
        assert engine.decide_access("map-probe-viewer", "context:write")["decision"] == "deny"

    def test_the_module_facade_still_works(self):
        result = get_security_engine().decide_access("map-probe-facade", "audit:query")
        assert result["decision"] == "deny"  # principal has no roles
        assert result["permission_known"] is True

    def test_get_mapping_returns_none_for_unknown(self):
        assert get_mapping("nope:nope") is None
