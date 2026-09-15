"""The colon-permission registry is complete, truthful, and now observable.

Companion to ``src/kernels/security/_permission_map.py``. Three claims:

1. **Complete.** Every permission the Security Kernel actually seeds, and
   every permission literal production code actually asks for, has a registry
   entry. Both sides are read from the *real* sources at test time (the live
   ``_rbac_rules`` table and an AST scan), so the registry cannot drift.
2. **Truthful.** Every ``role`` matches the real seed rule, and every name in
   ``kernel_actions`` is a real decorated ``@kernel_action`` -- nothing here
   is asserted into existence.
3. **Observable.** ``decide_access`` distinguishes "checked and refused"
   from "this permission can never be granted". That distinction is the whole
   point: before this, both looked like a plain deny.

**2026-09-15 (boss-approved full cut).** Authority A no longer seeds any colon
permissions; the registry carries only ``research:read`` (registered but
unseeded). The claims above still hold -- vacuously on the "seeded" side, and
for ``research:read`` on the literal side. The tests that used to measure the
delegation blast radius (2 of the 12 survived) are now moot and assert the
post-cut empty state instead; the historical measurement is preserved in the
module docstring of ``_permission_map.py``.
"""

from __future__ import annotations

import pytest

from src.kernels.security import RBACRole, RBACRule, SecurityEngine, get_security_engine
from src.kernels.security._permission_map import (
    PERMISSION_MAP,
    PermissionMapping,
    b_delegatable_permissions,
    b_denied_under_delegation,
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
        # Full cut 2026-09-15: production seeds nothing, so the completeness
        # invariant is vacuous -- assert it anyway (a future re-seed must be
        # registered) and pin the cut itself.
        missing = sorted(seeded - set(PERMISSION_MAP))
        assert not missing, f"已播种但未登记的权限：{missing}"
        assert seeded == set(), f"全裁后不应再有种子权限：{sorted(seeded)}"

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

    def test_the_registry_is_research_read_only_after_the_full_cut(self):
        """The full cut left exactly one registry entry."""
        assert set(PERMISSION_MAP) == {"research:read"}


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

    def test_no_permission_maps_to_b_after_the_full_cut(self):
        """Post-cut the sole registry entry (``research:read``) maps to no B
        action, so there is nothing left to cross-check against B here."""
        mapped = [p for p, m in PERMISSION_MAP.items() if m.kernel_actions]
        assert mapped == []

    def test_the_unmapped_are_reported_not_hidden(self):
        """B is all-effectful; A is heavy on reads. The gap must be visible."""
        assert unmapped_permissions(), "声称全部有对应，与实测不符"
        for permission in unmapped_permissions():
            assert PERMISSION_MAP[permission].note, f"{permission} 缺说明"


# --------------------------------------------------------------------------- #
# 2b. "mapped to a real B action" is NOT "B would allow it"
# --------------------------------------------------------------------------- #


class TestDelegationToBIsMeasurablyUnviable:
    """"先补映射、再把 A 切到 B" 这个方案早已被否决。

    历史上：实测它会打断 A 的 12 条种子权限里的 10 条（含全部读权限）。
    2026-09-15 全裁之后 A 不再播种任何冒号权限，委派问题随之失去载体 ——
    这组断言改为钉住「全裁后无任何可委派权限」这一现状；历史实测值（12 条里
    只有 2 条幸存）保留在 ``_permission_map.py`` 的模块 docstring 里。
    """

    def test_b_has_no_read_action_at_all(self):
        """方案不可行的结构性原因：B 的允许表只有效应型动作。"""
        from src.kernels.policy import INTERNAL_SERVICE_ALLOWED_ACTIONS

        verbs = {str(a).split(".")[-1] for a in INTERNAL_SERVICE_ALLOWED_ACTIONS}
        readish = verbs & {"read", "get", "list", "query", "lookup", "describe"}
        assert verbs, "B 的允许表为空"
        assert not readish, (
            f"B 出现了读动作 {sorted(readish)} —— 若属实，A 的读权限或许可以下沉，"
            "必须重新评估收敛方案（而不是只改这里）"
        )

    def test_delegation_is_moot_after_the_full_cut(self):
        """全裁后 A 无种子权限 ⇒ 无可委派；两个测量函数都应为空。"""
        assert b_delegatable_permissions() == []
        assert b_denied_under_delegation() == []

    def test_any_survivor_would_really_be_in_bs_allow_list(self):
        """正向对照（现为空集，结论仍可执行）：幸存者必须在允许表里。"""
        from src.kernels.policy import INTERNAL_SERVICE_ALLOWED_ACTIONS

        allowed = frozenset(INTERNAL_SERVICE_ALLOWED_ACTIONS)
        for permission in b_delegatable_permissions():
            actions = PERMISSION_MAP[permission].kernel_actions
            assert actions, f"{permission} 无映射动作，不该出现在幸存者里"
            assert set(actions) & allowed, f"{permission} 的动作不在允许表：{actions}"


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

    def test_a_registered_seeded_permission_is_quiet(
        self, engine, caplog, monkeypatch
    ):
        """Positive control: a permission that is BOTH registered and seeded
        emits no warning and reports its mapping. Production seeds none after
        the full cut, so register one locally for this check."""
        monkeypatch.setitem(
            PERMISSION_MAP,
            "resource:allocate",
            PermissionMapping(
                "resource:allocate", "operator", ("resource.allocate",), "write",
                "test-local fixture (removed from production by the 2026-09-15 cut)",
            ),
        )
        engine._rbac_rules["resource:allocate"] = RBACRule(
            id="resource:allocate", role=RBACRole.OPERATOR, permission="resource:allocate"
        )
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
# 5. verdicts after the full cut
# --------------------------------------------------------------------------- #


class TestVerdictsAfterTheFullCut:
    def test_removed_permissions_now_deny(self, engine):
        """The 12 removed permissions are no longer seeded, so a principal that
        used to be allowed (VIEWER for ``context:read``) is now denied."""
        engine.set_principal_roles("map-probe-viewer", {RBACRole.VIEWER})
        result = engine.decide_access("map-probe-viewer", "context:read")
        assert result["decision"] == "deny"
        assert result["permission_known"] is False

    def test_the_module_facade_still_works(self):
        result = get_security_engine().decide_access("map-probe-facade", "audit:query")
        assert result["decision"] == "deny"  # no roles AND the permission is gone
        assert result["permission_known"] is False

    def test_get_mapping_returns_none_for_unknown(self):
        assert get_mapping("nope:nope") is None

    def test_removed_permissions_are_no_longer_known(self):
        for removed in (
            "context:read", "context:write", "capability:lookup", "capability:manage",
            "execution:plan", "execution:trigger", "resource:allocate", "resource:query",
            "policy:manage", "evaluation:run", "audit:query", "audit:log",
        ):
            assert not is_known(removed), removed
