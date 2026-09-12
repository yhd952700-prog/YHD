"""Policy C-1 — internal service principal + kernel action allow-list.

Policy C-1 makes the kernel layer's recorded verdict *carry information*: the
internal service principal (the system's own code) is pre-approved for an
explicit allow-list of kernel actions, and everything else falls through to
``default_deny``. The decorator remains additive — this is still "record
only", not enforcement (that would be C-2).

Every claim below is backed by a runtime assertion, never by a keyword scan of
the implementation (except the allow-list completeness guard, which parses the
decorator usages themselves).
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from src.kernels._crosscutting import kernel_action
from src.kernels.identity import (
    INTERNAL_SERVICE_PRINCIPAL,
    IdentityScope,
    IdentityStatus,
    create_identity,
    get_identity_manager,
)
from src.kernels.policy import (
    INTERNAL_SERVICE_ALLOWED_ACTIONS,
    INTERNAL_SERVICE_DENIED_ACTIONS,
    PolicyEffect,
    get_policy_engine,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
KERNELS_DIR = REPO_ROOT / "src" / "kernels"


def _service_actor() -> dict:
    return {"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL}


def _decide(action_name: str, actor: dict | None = None):
    return get_policy_engine().evaluate_simple(
        actor=actor if actor is not None else _service_actor(),
        action={"name": action_name, "risk_level": "LOW"},
    )


# --------------------------------------------------------------------------- #
# 1. The principal exists and is marked as a service
# --------------------------------------------------------------------------- #


class TestInternalServiceIdentity:
    def test_identity_is_registered_active_and_marked(self):
        ident = get_identity_manager().get_identity(INTERNAL_SERVICE_PRINCIPAL)
        assert ident is not None, "内置 service 身份未注册"
        assert ident.status is IdentityStatus.ACTIVE
        assert (ident.metadata or {}).get("kind") == "service"
        # 该主体自身不持有任何权限：它的放行完全来自策略白名单，而非权限集
        assert ident.permissions == set()

    def test_identity_is_reachable_by_principal(self):
        by_principal = get_identity_manager().get_identity_by_principal(
            INTERNAL_SERVICE_PRINCIPAL
        )
        assert by_principal is not None
        assert by_principal.id == INTERNAL_SERVICE_PRINCIPAL


# --------------------------------------------------------------------------- #
# 2. The verdict carries information (allow-list vs default deny)
# --------------------------------------------------------------------------- #


class TestServiceVerdict:
    @pytest.mark.parametrize("name", sorted(INTERNAL_SERVICE_ALLOWED_ACTIONS))
    def test_allowlisted_action_is_allowed(self, name):
        decision = _decide(name)
        assert decision.decision is PolicyEffect.ALLOW, name
        assert "RULE:internal_service_allow:allow" in decision.traceability

    @pytest.mark.parametrize("name", sorted(INTERNAL_SERVICE_DENIED_ACTIONS))
    def test_denied_action_falls_through_to_default_deny(self, name):
        decision = _decide(name)
        assert decision.decision is PolicyEffect.DENY, name
        assert "RULE:default_deny:deny" in decision.traceability

    def test_verdict_is_not_a_constant(self):
        """回归护栏：判决必须随动作变化（这正是 C-1 要消除的『空转』）。"""
        verdicts = {
            _decide(n).decision
            for n in list(INTERNAL_SERVICE_ALLOWED_ACTIONS)
            + list(INTERNAL_SERVICE_DENIED_ACTIONS)
        }
        assert verdicts == {PolicyEffect.ALLOW, PolicyEffect.DENY}

    def test_human_sovereignty_still_outranks_service_allow(self):
        """precedence: human_sovereignty(1000) > internal_service_allow(900) > deny。"""
        rules = get_policy_engine().list_rules()
        by_id = {r.id: r for r in rules}
        assert (
            by_id["human_sovereignty"].precedence
            > by_id["internal_service_allow"].precedence
        )
        assert by_id["internal_service_allow"].precedence > max(
            r.precedence for r in rules if r.action.value == "deny"
        )


# --------------------------------------------------------------------------- #
# 3. Adversarial: the allow cannot be self-declared
# --------------------------------------------------------------------------- #


class TestServiceClaimCannotBeForged:
    @pytest.mark.parametrize(
        "actor",
        [
            pytest.param({"type": "service"}, id="no-principal"),
            pytest.param(
                {"type": "service", "principal": "does-not-exist"},
                id="unknown-principal",
            ),
            pytest.param(
                {"type": "service", "principal": "system"},
                id="system-identity-is-not-a-service",
            ),
        ],
    )
    def test_forged_claims_are_denied(self, actor):
        assert _decide("memory.store", actor=dict(actor)).is_denied

    def test_caller_supplied_verified_flag_does_not_grant_allow(self):
        """引擎重算 verified：调用方自填 True 不能凭空放行一个未知主体。"""
        actor = {"type": "service", "principal": "forged", "verified": True}
        assert _decide("memory.store", actor=actor).is_denied

    def test_canonical_principal_is_verified_by_the_engine(self):
        """同一动作 + 真实主体 = 放行，证明差异来自核验而非调用方声明。"""
        assert _decide("memory.store").is_allowed

    def test_non_service_identity_cannot_be_used_as_a_service(self):
        """kind 标记把两类主体隔开：非 service 身份不能顶替 service 放行。"""
        human = create_identity(
            principal=f"c1-probe-human-{uuid.uuid4().hex[:8]}",
            scope=IdentityScope.L0,
            trust_score=1.0,
        )
        assert human is not None
        decision = _decide(
            "memory.store",
            actor={"type": "service", "principal": human.id},
        )
        assert decision.is_denied

    def test_legacy_system_actor_behaviour_is_unchanged(self):
        """回归：旧 ``{"type": "system", "verified": True}`` 路径仍恒 deny。"""
        for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            decision = get_policy_engine().evaluate_simple(
                actor={"type": "system", "verified": True},
                action={"name": "memory.store", "risk_level": risk},
            )
            assert decision.is_denied, f"risk={risk}"


# --------------------------------------------------------------------------- #
# 4. Verification is revocable (kill switch)
# --------------------------------------------------------------------------- #


class TestRevocableVerification:
    def test_suspending_the_identity_kills_the_allow(self, monkeypatch):
        ident = get_identity_manager().get_identity(INTERNAL_SERVICE_PRINCIPAL)
        monkeypatch.setattr(ident, "status", IdentityStatus.SUSPENDED)
        assert _decide("memory.store").is_denied

    def test_deactivating_the_identity_kills_the_allow(self, monkeypatch):
        ident = get_identity_manager().get_identity(INTERNAL_SERVICE_PRINCIPAL)
        monkeypatch.setattr(ident, "status", IdentityStatus.DEACTIVATED)
        assert _decide("memory.store").is_denied

    def test_removing_the_kind_marker_kills_the_allow(self, monkeypatch):
        ident = get_identity_manager().get_identity(INTERNAL_SERVICE_PRINCIPAL)
        monkeypatch.setattr(ident, "metadata", {"description": "marker removed"})
        assert _decide("memory.store").is_denied


# --------------------------------------------------------------------------- #
# 5. The allow-list is explicit, complete and cannot drift
# --------------------------------------------------------------------------- #


def _decorated_action_names() -> set:
    """Every kernel action name passed to ``@kernel_action("...")`` in src/."""
    names: set = set()
    for path in KERNELS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not dec.args:
                    continue
                func = dec.func
                fname = getattr(func, "id", None) or getattr(func, "attr", None)
                if fname != "kernel_action":
                    continue
                arg0 = dec.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    names.add(arg0.value)
    return names


class TestAllowListIntegrity:
    def test_allow_and_deny_lists_are_disjoint(self):
        assert not (INTERNAL_SERVICE_ALLOWED_ACTIONS & INTERNAL_SERVICE_DENIED_ACTIONS)

    def test_no_wildcard_entries(self):
        for name in INTERNAL_SERVICE_ALLOWED_ACTIONS | INTERNAL_SERVICE_DENIED_ACTIONS:
            assert "*" not in name, f"白名单禁止通配: {name}"

    def test_every_decorated_kernel_action_is_classified(self):
        """新增内核动作必须被显式分类，否则本测试失败（刻意的摩擦）。"""
        decorated = _decorated_action_names()
        classified = INTERNAL_SERVICE_ALLOWED_ACTIONS | INTERNAL_SERVICE_DENIED_ACTIONS
        assert decorated, "未扫描到任何 @kernel_action 装饰点，测试本身失效"
        unclassified = sorted(decorated - classified)
        stale = sorted(classified - decorated)
        assert not unclassified, (
            "以下内核动作未在策略中显式分类（新增动作默认拒绝，须显式登记）："
            f"{unclassified}"
        )
        assert not stale, f"策略中登记了已不存在的内核动作：{stale}"


# --------------------------------------------------------------------------- #
# 6. End-to-end through the real decorator (additive, informative audit)
# --------------------------------------------------------------------------- #


class _DecoratedThing:
    @kernel_action("memory.store")
    def allowed_action(self):
        return "ran"

    @kernel_action("identity.grant_permission")
    def denied_action(self):
        return "ran"


def _details_for(action_name: str):
    from src.kernels.audit import audit_query

    for ev in audit_query(principal_id="kernel", limit=200, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") == action_name:
            return det
    return None


class TestDecoratorRecordsInformativeDecision:
    def test_allowlisted_action_records_allow_and_rule(self):
        thing = _DecoratedThing()
        assert thing.allowed_action() == "ran", "装饰器必须仍可执行（additive）"
        det = _details_for("memory.store")
        assert det is not None, "未写入审计事件"
        assert det.get("policy_decision") == "allow"
        assert det.get("policy_rule") == "internal_service_allow"

    def test_non_allowlisted_action_records_deny_and_rule(self):
        thing = _DecoratedThing()
        assert thing.denied_action() == "ran", (
            "判决为 deny 时动作仍必须执行 —— 装饰器是 additive，不是控制点"
        )
        det = _details_for("identity.grant_permission")
        assert det is not None
        assert det.get("policy_decision") == "deny"
        assert det.get("policy_rule") == "default_deny"
        assert det.get("policy_enforced") is False, (
            "C-1 仍是『记录』：policy_enforced 必须为 False"
        )
