"""Policy C-4 — deployment-side kernel enforcement switch (default OFF).

These tests prove:

  * the switch defaults to "nothing enforced" (production stays record-only/L1);
  * a tier token expands to exactly that tier's kernel actions;
  * an explicit action name selects exactly that action;
  * unknown tokens, non-gated tiers, and LOW/MEDIUM action names are rejected
    **loudly** -- a typo must never leave the system quietly unenforced;
  * flipping the switch actually ARMS the C-2 gate for *production* call sites
    (which keep ``enforce=False``), and an audited approval grant then lets the
    action through.
"""

from __future__ import annotations

import pytest

from src.kernels import _enforcement as enf
from src.kernels._crosscutting import PolicyDeferredError, kernel_action
from src.kernels._risk_classification import KERNEL_ACTION_RISK, RiskTier
from src.kernels._sovereignty import clear_grants, grant_window, issue_grant
from src.kernels.identity import IdentityManager, IdentityScope, IdentityStatus

CRITICAL_ACTIONS = frozenset(
    a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.CRITICAL
)
HIGH_ACTIONS = frozenset(a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.HIGH)


@pytest.fixture(autouse=True)
def clean_switch(monkeypatch):
    """Every test starts with the switch OFF and no leftover grants."""
    monkeypatch.delenv(enf.ENV_VAR, raising=False)
    enf.reload()
    clear_grants()
    yield
    enf.reload()
    clear_grants()


@pytest.fixture
def human_env(monkeypatch):
    """Isolated IdentityManager holding one ACTIVE human identity."""
    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-c4-switch", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    assert human.status == IdentityStatus.ACTIVE
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    return mgr, human


class TestParseSpec:
    def test_empty_means_nothing_enforced(self):
        assert enf.parse_spec("") == frozenset()
        assert enf.parse_spec("   ") == frozenset()

    def test_default_snapshot_is_off(self):
        snap = enf.describe()
        assert snap["enabled"] is False
        assert snap["count"] == 0
        assert snap["enforced_actions"] == []
        assert snap["config_error"] is None

    def test_critical_tier_expands_to_its_actions(self):
        assert enf.parse_spec("CRITICAL") == CRITICAL_ACTIONS
        assert len(enf.parse_spec("CRITICAL")) == 2

    def test_high_tier_expands_to_its_actions(self):
        assert enf.parse_spec("HIGH") == HIGH_ACTIONS
        assert len(HIGH_ACTIONS) == 15

    def test_explicit_action_name(self):
        assert enf.parse_spec("capability.retire") == frozenset({"capability.retire"})

    def test_mixed_tier_and_action(self):
        assert enf.parse_spec("CRITICAL,memory.auto_cleanup") == (
            CRITICAL_ACTIONS | {"memory.auto_cleanup"}
        )

    def test_tier_token_is_case_insensitive(self):
        assert enf.parse_spec("critical") == enf.parse_spec("CRITICAL")

    def test_whitespace_tolerated(self):
        assert enf.parse_spec(" CRITICAL , capability.retire ") == CRITICAL_ACTIONS

    def test_unknown_token_rejected(self):
        with pytest.raises(ValueError):
            enf.parse_spec("NOPE")

    def test_misspelled_action_rejected(self):
        with pytest.raises(ValueError):
            enf.parse_spec("capability.reitre")

    def test_low_and_medium_actions_rejected(self):
        for action in ("memory.store", "context.compress", "trust.assign_score"):
            with pytest.raises(ValueError):
                enf.parse_spec(action)

    def test_non_gated_tier_rejected(self):
        for tier in ("LOW", "MEDIUM"):
            with pytest.raises(ValueError):
                enf.parse_spec(tier)


class TestIsEnforced:
    def test_off_by_default(self):
        assert enf.is_enforced("capability.retire", "CRITICAL") is False
        assert enf.enforced_actions() == frozenset()

    def test_selected_action_only(self, monkeypatch):
        monkeypatch.setenv(enf.ENV_VAR, "capability.retire")
        enf.reload()
        assert enf.is_enforced("capability.retire", "CRITICAL") is True
        assert enf.is_enforced("security.set_abac_rule", "CRITICAL") is False

    def test_low_tier_can_never_be_enforced(self, monkeypatch):
        # Belt and braces: even if a LOW action were selected, a LOW tier
        # short-circuits to False -- the C-3 channel never escalates LOW.
        monkeypatch.setenv(enf.ENV_VAR, "capability.retire")
        enf.reload()
        assert enf.is_enforced("capability.retire", "LOW") is False

    def test_config_error_is_surfaced_not_swallowed(self, monkeypatch):
        monkeypatch.setenv(enf.ENV_VAR, "capability.reitre")
        enf.reload()
        snap = enf.describe()
        assert snap["enabled"] is False
        assert snap["config_error"]


class TestSwitchArmsProductionCallSites:
    """生产点保持 enforce=False；仅靠开关即可把拦截门 armed。"""

    def test_disarmed_by_default(self, human_env):
        @kernel_action("capability.retire")
        def retire():
            return "ran"

        # No grant, no switch: the C-2 gate is inert -> additive (L1) contract.
        assert retire() == "ran"

    def test_armed_by_switch_defers_without_grant(self, human_env, monkeypatch):
        monkeypatch.setenv(enf.ENV_VAR, "capability.retire")
        enf.reload()

        @kernel_action("capability.retire")
        def retire():
            return "ran"

        with pytest.raises(PolicyDeferredError):
            retire()

    def test_armed_by_switch_allows_with_grant(self, human_env, monkeypatch):
        monkeypatch.setenv(enf.ENV_VAR, "capability.retire")
        enf.reload()
        _mgr, human = human_env

        @kernel_action("capability.retire")
        def retire():
            return "ran-under-grant"

        grant = issue_grant(human.id, ["capability.retire"], reason="ops approval")
        with grant_window(grant):
            assert retire() == "ran-under-grant"

    def test_switch_does_not_leak_to_other_actions(self, human_env, monkeypatch):
        monkeypatch.setenv(enf.ENV_VAR, "capability.retire")
        enf.reload()

        @kernel_action("security.set_abac_rule")
        def set_rule():
            return "ran"

        # Not selected -> still record-only, even though it is CRITICAL.
        assert set_rule() == "ran"
