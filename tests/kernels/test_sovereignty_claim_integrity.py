"""F33 stage 2 — integrity of the decision-point sovereignty claim.

Five defects found by adversarial review: two (A, C) of the stage-1 fix
(``c669bc03``), and three more (D-2, D-3, D-4 -- the ``TestAGrant...`` /
``TestABound...`` classes below) of the stage-2 fix itself. They are about the
*claim*, never about whether the wrapped body runs: this suite runs record-only
-- the library default (``enforce`` is ``False`` and
``LIUHAO_KERNEL_POLICY_ENFORCE`` is unset) -- so the verdict path is deliberately
untouched here. That is the configuration *these tests run in*, not a deployment
fact: ``docker-compose.prod.yml:59`` defaults ``LIUHAO_KERNEL_POLICY_ENFORCE``
to ``HIGH,CRITICAL``.

One exception to "never about whether the body runs": D-2 is tested *armed* in a
child process, because a cross-human escalation lands on the **allow** path,
which no enforcement tier can refuse -- so for that defect "the body did not
execute" is the only assertion that means anything.

**A — the re-check could fail open.** ``_sovereignty_claim_state`` was called
inside ``_adjudicate``'s blanket handler, which answers an exception with
``return None, None, _service_actor_policy_shape()`` -- *adjudication
unavailable*. Under the record-only configuration this suite runs in, that
answer still lets the body run, so an exception silently turned "claim refused"
into "no adjudication performed", and
the action executed while the offending value was stamped as
``sovereignty_grant``. The trigger was a caller-supplied unhashable ``grant_id``
reaching the registry lookup.

**C — a refused claim still got human attribution.** ``_adjudicate`` set
``actor.type='human'`` whenever the *grant* re-check passed, but the policy
engine independently recomputes ``verified`` from the Identity Kernel (C-7/P10).
When that recompute refused (human suspended after the window opened; principal
never a human at all) the engine returned ``deny`` while the recorded actor
stayed ``human`` -- so the durable record showed ``actor_kind='human'`` +
``actor_source='sovereignty-window'`` *and* a grant id, for a claim that never
held. That is the false-human record F26/F33 exist to prevent.

Every test below was measured to fail on ``c669bc03`` and pass after the fix.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

from src.kernels import _sovereignty as sov
from src.kernels._crosscutting import (
    _ACTING_PRINCIPAL,
    _adjudicate,
    _resolve_audit_actor,
    _sovereignty_claim_state,
    kernel_action,
)
from src.kernels._sovereignty import ActiveSovereignty
from src.kernels.audit import audit_query
from src.kernels.identity import IdentityManager, IdentityScope, IdentityStatus

ACTION = "capability.retire"          # CRITICAL, enforcement-gated
OTHER_ACTION = "security.set_abac_rule"   # CRITICAL, not covered by ACTION grants
SCOPE = "CRITICAL"


@pytest.fixture
def humans(monkeypatch):
    """Isolated IdentityManager with one ACTIVE human."""
    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-claim-approver", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    monkeypatch.setattr("src.kernels.identity.get_identity_manager", lambda: mgr)
    sov.clear_grants()
    yield mgr, human
    sov.clear_grants()


def _action_row(action_name: str):
    """Newest audit event written for ``action_name``."""
    for ev in audit_query(limit=300, reverse=True):
        if (ev.get("details") or {}).get("action") == action_name:
            return ev
    return None


def _assert_no_human_attribution(details, who):
    """A claim that did not hold must not read as human sovereignty."""
    assert details["actor_kind"] != "human", (
        f"{who}: a refused claim was recorded with actor_kind='human': {details!r}"
    )
    assert details["actor_source"] != "sovereignty-window", (
        f"{who}: a refused claim was recorded as a live sovereignty window: {details!r}"
    )
    assert details["actor_source"] == "sovereignty-window-rejected", details
    assert details["sovereignty_grant"] is None, (
        f"{who}: a refused claim still stamped a grant id that reads as "
        f"authorisation: {details!r}"
    )


class TestClaimRecheckNeverFailsOpen:
    """A: an unanswerable re-check is a refusal, not a silent pass."""

    def test_constructor_refuses_a_non_string_grant_id(self, humans):
        """The trigger cannot be constructed in the first place."""
        _mgr, human = humans
        for bad in (["x"], {"k": "v"}, 123):
            with pytest.raises(ValueError):
                sov.human_sovereign(human.principal, {ACTION}, grant_id=bad)
        # None is the documented bare C-3 channel and must stay legal.
        sov.human_sovereign(human.principal, {ACTION}, grant_id=None)

    def test_claim_state_returns_a_refusal_instead_of_raising(self, humans):
        """Defence in depth: the white-box route must also refuse, not raise.

        Written against ``ActiveSovereignty`` directly so it does not depend on
        the constructor guard -- if the guard were ever removed, this still
        holds.
        """
        _mgr, human = humans
        for bad in (["x"], {"k": "v"}):
            window = ActiveSovereignty(
                principal=human.principal,
                actions=frozenset({ACTION}),
                granted_at=0.0,
                grant_id=bad,
            )
            reason = _sovereignty_claim_state(window, ACTION)   # must not raise
            assert reason is not None, (
                f"an unhashable grant_id {bad!r} produced no refusal reason -- "
                f"the claim would be honoured"
            )
            assert reason == "claim-lookup-error", reason

    def test_a_bad_grant_id_forces_the_service_actor(self, humans):
        """End-to-end: the claim is refused and no grant id reaches the audit.

        The body still runs -- this suite is record-only (the library default,
        not what the production manifest selects: see the module docstring) and
        this fix must not touch that. What must change is the *attribution*.
        """
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION])
        sov.revoke_grant(grant.grant_id)

        @kernel_action(ACTION)
        def retire():
            return "EXECUTED"

        # Force the bad value past the constructor guard, the way an unknown
        # future caller could.
        sov.set_active_sovereignty(
            ActiveSovereignty(
                principal=human.principal,
                actions=frozenset({ACTION}),
                granted_at=0.0,
                grant_id=["x"],
            )
        )
        try:
            retire()
        finally:
            sov.clear_active_sovereignty()

        details = (_action_row(ACTION) or {}).get("details")
        assert details is not None, "no audit row written"
        assert details["policy_decision"] == "deny", details
        assert details["sovereignty_claim"] == "claim-lookup-error", details
        _assert_no_human_attribution(details, "unanswerable re-check")


class TestRefusedClaimGetsNoHumanAttribution:
    """C: ``actor.type='human'`` is not the same as the verdict being the human's."""

    def test_a_human_suspended_after_the_window_opened(self, humans):
        """The grant is live, but the human behind it is no longer verified.

        On ``c669bc03`` this row reads ``decision='deny'`` *together with*
        ``actor_kind='human'``, ``actor_source='sovereignty-window'`` and a
        ``sovereignty_grant`` stamp.
        """
        mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION])
        mgr._identities[human.id].status = IdentityStatus.SUSPENDED

        @kernel_action(ACTION)
        def retire():
            return "ran"

        with sov.grant_window(grant):
            retire()

        details = (_action_row(ACTION) or {}).get("details")
        assert details is not None, "no audit row written"
        assert details["policy_decision"] == "deny", details
        assert details["sovereignty_claim"] == "claim-human-not-verified", details
        _assert_no_human_attribution(details, "suspended human")
        mgr._identities[human.id].status = IdentityStatus.ACTIVE

    @pytest.mark.parametrize(
        "bogus", ["totally-unknown-principal", "", "liuhao-internal-service"]
    )
    def test_a_bare_window_whose_principal_is_not_a_human(self, humans, bogus):
        """The bare C-3 channel still cannot manufacture a human approver."""
        with sov.human_sovereign(bogus, {ACTION}):

            @kernel_action(ACTION)
            def retire():
                return "ran"

            retire()

        details = (_action_row(ACTION) or {}).get("details")
        assert details is not None, "no audit row written"
        assert details["policy_decision"] == "deny", details
        _assert_no_human_attribution(details, f"bare window principal={bogus!r}")

    def test_a_grant_that_is_live_and_human_still_reads_as_a_human_approval(self, humans):
        """Non-regression: the attribution must survive for a claim that held."""
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION])

        @kernel_action(ACTION, enforce=True)
        def retire():
            return "ran-under-grant"

        with sov.grant_window(grant):
            assert retire() == "ran-under-grant"

        details = (_action_row(ACTION) or {}).get("details")
        assert details is not None, "no audit row written"
        assert details["policy_decision"] == "allow", details
        assert details["policy_rule"] == "human_sovereignty", details
        assert details["actor_kind"] == "human", details
        assert details["actor_source"] == "sovereignty-window", details
        assert details["sovereignty_grant"] == grant.grant_id, details

    def test_adjudicate_returns_the_service_actor_for_a_refused_claim(self, humans):
        """The same property at the ``_adjudicate`` boundary, not just in audit."""
        _mgr, human = humans
        with sov.human_sovereign("not-a-human-at-all", {ACTION}):
            verdict, _rule, actor = _adjudicate(ACTION, SCOPE)
        assert verdict == "deny", (verdict, actor)
        assert actor["type"] != "human", actor
        assert actor.get("sovereignty_claim") == "claim-human-not-verified", actor


# --------------------------------------------------------------------------- #
# D-2 / D-3 / D-4 -- three defects of the same family as the stage-1 BLOCKER,
# found by the adversarial pass over the stage-2 fix. Each of the tests below
# was measured to FAIL on ``c669bc03`` and PASS after the fix.
# --------------------------------------------------------------------------- #

#: Child program for the D-2 test. Kept as a literal (not an f-string) so the
#: dict/brace syntax of the embedded source needs no escaping; ``__ROOT__`` and
#: ``__ACTION__`` are substituted by the test.
_ARMED_CHILD = r'''
import sys
sys.path.insert(0, r"__ROOT__")
from src.kernels import identity as idmod
from src.kernels import _sovereignty as sov
from src.kernels._crosscutting import (
    PolicyDeniedError, PolicyDeferredError, kernel_action,
)
from src.kernels.identity import IdentityManager, IdentityScope

mgr = IdentityManager()
alpha = mgr.create_identity("human-vigil-alpha", scope=IdentityScope.L0,
                            metadata={"kind": "human"})
beta = mgr.create_identity("human-vigil-beta", scope=IdentityScope.L0,
                           metadata={"kind": "human"})
idmod.get_identity_manager = lambda: mgr
sov.clear_grants()
grant = sov.issue_grant(alpha.id, ["__ACTION__"])

ran = []


@kernel_action("__ACTION__")
def retire():
    ran.append(1)
    return "EXECUTED"


# The window names BETA but carries ALPHA's live, unrevoked, in-scope grant.
sov.set_active_sovereignty(sov.ActiveSovereignty(
    principal=beta.id, actions=frozenset(["__ACTION__"]),
    granted_at=0.0, grant_id=grant.grant_id))
try:
    try:
        retire()
        outcome = "returned"
    except (PolicyDeferredError, PolicyDeniedError) as exc:
        outcome = type(exc).__name__
finally:
    sov.clear_active_sovereignty()

print("SAME_PRINCIPAL=" + str(alpha.id == beta.id))
print("OUTCOME=" + outcome)
print("RAN=" + ("yes" if ran else "no"))
'''


class TestAGrantIsBoundToItsOwnHuman:
    """D-2: a grant authorises the human it was issued to, not the holder."""

    def test_claim_state_refuses_a_foreign_principal(self, humans):
        mgr, alpha = humans
        beta = mgr.create_identity(
            "human-vigil-beta", scope=IdentityScope.L0, metadata={"kind": "human"}
        )
        grant = sov.issue_grant(alpha.id, [ACTION])
        window = ActiveSovereignty(
            principal=beta.id,
            actions=frozenset({ACTION}),
            granted_at=0.0,
            grant_id=grant.grant_id,
        )
        assert _sovereignty_claim_state(window, ACTION) == "grant-principal-mismatch"

    def test_the_foreign_principal_is_not_escalated_to_human(self, humans):
        """B must not be boosted to a human allow on A's approval."""
        mgr, alpha = humans
        beta = mgr.create_identity(
            "human-vigil-beta", scope=IdentityScope.L0, metadata={"kind": "human"}
        )
        grant = sov.issue_grant(alpha.id, [ACTION])
        with sov.human_sovereign(beta.id, {ACTION}, grant_id=grant.grant_id):
            verdict, _rule, actor = _adjudicate(ACTION, SCOPE)
        assert verdict == "deny", (verdict, actor)
        assert actor["type"] != "human", actor
        assert actor.get("sovereignty_claim") == "grant-principal-mismatch", actor

    def test_the_same_human_spelled_two_ways_is_not_a_mismatch(self, humans):
        """Non-regression: D-2 must not withdraw authority over *spelling*.

        ``human_sovereign`` accepts the identity id (``human:bob``) or the plain
        principal name (``bob``), and a grant records whichever it was issued
        with. A raw string comparison would refuse this legitimate window -- the
        first version of this fix did exactly that, and
        ``test_sovereignty_grants.py::TestAuditKeyspaceConvergence`` caught it.
        """
        _mgr, human = humans
        assert human.principal != human.id, "test premise: the two forms differ"
        grant = sov.issue_grant(human.id, [ACTION])
        with sov.human_sovereign(human.principal, {ACTION}, grant_id=grant.grant_id):
            verdict, _rule, actor = _adjudicate(ACTION, SCOPE)
        assert verdict == "allow", (verdict, actor)
        assert actor.get("type") == "human", actor
        assert actor.get("sovereignty_claim") is None, actor

    def test_an_armed_process_does_not_execute_a_foreign_principal_allow(self, tmp_path):
        """The decisive form: armed, so "it never executed" is an execution fact.

        On the allow path no enforcement tier can refuse the action, so this is
        the one defect here that would really run in an armed deployment.
        """
        root = pathlib.Path(__file__).resolve().parents[2]
        code = _ARMED_CHILD.replace("__ROOT__", str(root)).replace("__ACTION__", ACTION)
        env = dict(os.environ)
        env["LIUHAO_KERNEL_POLICY_ENFORCE"] = "HIGH,CRITICAL"
        env["AUDIT_DB_PATH"] = str(tmp_path / "armed_audit.db")
        proc = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        out = proc.stdout + proc.stderr
        # Guard against a vacuous pass: the two humans really must differ.
        assert "SAME_PRINCIPAL=False" in out, out
        assert "RAN=no" in out, f"a foreign principal executed the action:\n{out}"
        assert (
            "OUTCOME=PolicyDeferredError" in out
            or "OUTCOME=PolicyDeniedError" in out
        ), out


class TestABoundSubjectKeepsTheRefusalReason:
    """D-3: an explicit binding outranks inference but must not erase evidence."""

    def test_a_bound_actor_still_carries_the_sovereignty_claim(self, humans):
        policy_actor = {
            "type": "service",
            "principal": "liuhao-internal-service",
            "sovereignty_claim": "grant-revoked",
        }
        token = _ACTING_PRINCIPAL.set(
            {"kind": "agent", "principal": "agent:vigil-probe"}
        )
        try:
            resolved = _resolve_audit_actor(policy_actor)
        finally:
            _ACTING_PRINCIPAL.reset(token)
        assert resolved["source"] == "bound", resolved
        assert resolved["kind"] == "agent", resolved
        assert resolved.get("sovereignty_claim") == "grant-revoked", resolved

    def test_a_bound_actor_without_a_claim_keeps_its_exact_shape(self, humans):
        """No spurious key: the bound shape is unchanged when nothing was refused."""
        token = _ACTING_PRINCIPAL.set(
            {"kind": "agent", "principal": "agent:vigil-probe"}
        )
        try:
            resolved = _resolve_audit_actor(
                {"type": "service", "principal": "liuhao-internal-service"}
            )
        finally:
            _ACTING_PRINCIPAL.reset(token)
        assert resolved == {
            "kind": "agent",
            "principal": "agent:vigil-probe",
            "source": "bound",
        }


class TestAGrantIsNotStampedWhenItDidNotAuthorise:
    """D-4: "no refusal reason" is not the same as "the human did this"."""

    def test_a_window_that_does_not_cover_the_action_stamps_no_grant(self, humans):
        _mgr, human = humans
        grant = sov.issue_grant(human.id, [ACTION])

        @kernel_action(OTHER_ACTION)
        def other():
            return "ran-out-of-scope"

        with sov.grant_window(grant):
            other()

        details = (_action_row(OTHER_ACTION) or {}).get("details")
        assert details is not None, "no audit row written"
        assert details["policy_decision"] == "deny", details
        assert details["sovereignty_grant"] is None, (
            "a window that never covered this action stamped its grant id as "
            f"authorisation: {details!r}"
        )
        assert details["sovereignty_claim"] is None, details
