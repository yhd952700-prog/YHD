"""A1 follow-up: the reserved-agent-id predicate must track the allocator.

Why this file exists
--------------------
A1 introduced ``is_agent_id`` to keep the id namespace and the principal
namespace disjoint, and to refuse a *human* whose principal is shaped like an
agent id -- because that ambiguity makes the two lookup paths resolve one name
to two different subjects. But the predicate was pinned to
``^[0-9a-f]{8}$`` while the allocator *widens* an agent id to 16 hex once the
primary space saturates.

Measured on b6cf2b13 (clean ``AUDIT_DB_PATH``), after driving the allocator to
the widening path::

    widened agent id     : f4554a6417f34c4e   (len 16)
    is_agent_id(widened) : False              <-- check left behind
    human accepted?      : True               <-- impersonation admitted
    get_identity()             -> agent victim-agent
    get_identity_by_principal  -> human f4554a6417f34c4e
    PATHS DISAGREE       : True

These tests are **discriminating**: they fail on b6cf2b13 and pass once the
predicate and the allocator derive from one source of truth.

Run:
    cd D:/LiuHao-AI-OS && AUDIT_DB_PATH=D:/cache/temp/audit_x.db \
        .venv/Scripts/python.exe -m pytest \
        tests/kernels/identity/test_agent_id_reservation.py -q
"""
from __future__ import annotations

import pytest

from src.kernels import identity as ident
from src.kernels.identity import (
    METADATA_KIND_KEY,
    HUMAN_KIND,
    IdentityManager,
    IdentityScope,
    is_agent_id,
)


@pytest.fixture
def mgr() -> IdentityManager:
    return IdentityManager()


def _force_widening(monkeypatch) -> None:
    """Make the very next allocation take the saturation / widening branch."""
    monkeypatch.setattr(ident, "_AGENT_ID_ALLOCATION_ATTEMPTS", 0)


class TestThePredicateTracksTheAllocator:
    def test_is_agent_id_accepts_the_widened_id_the_allocator_mints(
        self, mgr, monkeypatch
    ):
        _force_widening(monkeypatch)
        widened = mgr._allocate_agent_id()
        assert len(widened) > 8, "precondition: the allocator took the widening path"
        assert is_agent_id(widened) is True, (
            "the allocator can mint this id, so the reserved-id predicate must "
            "recognise it -- otherwise a human may claim it"
        )

    def test_is_agent_id_accepts_the_normal_id_the_allocator_mints(self, mgr):
        assert is_agent_id(mgr._allocate_agent_id()) is True

    def test_the_predicate_covers_every_width_the_allocator_declares(self):
        # The single-source-of-truth guard: widening the allocator means adding
        # a width to ``_AGENT_ID_HEX_WIDTHS``, and this test then covers that
        # width automatically. A second, hand-written length literal in the
        # predicate would make this fail.
        widths = ident._AGENT_ID_HEX_WIDTHS
        assert widths, "the allocator's width declaration must not be empty"
        for width in widths:
            assert is_agent_id("a" * width) is True, (
                f"width {width} is declared by the allocator but is not reserved"
            )
        # A width the allocator cannot mint must not be reserved: reserving more
        # than the allocator can produce would refuse legitimate humans.
        assert is_agent_id("a" * (max(widths) + 1)) is False


class TestAHumanCanNeverClaimAnAgentId:
    def test_a_human_principal_equal_to_a_widened_agent_id_is_refused(
        self, mgr, monkeypatch
    ):
        _force_widening(monkeypatch)
        victim = mgr.create_identity("victim-agent", scope=IdentityScope.L1)
        widened = victim.id
        assert len(widened) > 8

        attacker = mgr.create_identity(
            principal=widened,
            scope=IdentityScope.L0,
            metadata={METADATA_KIND_KEY: HUMAN_KIND},
        )

        assert attacker is None, (
            "a human whose principal equals a widened agent id was accepted; "
            "the attacker can now act under the victim's name"
        )

    def test_a_human_principal_equal_to_a_normal_agent_id_is_refused(self, mgr):
        agent = mgr.create_identity("victim-agent", scope=IdentityScope.L1)
        assert is_agent_id(agent.id) is True

        attacker = mgr.create_identity(
            principal=agent.id,
            scope=IdentityScope.L0,
            metadata={METADATA_KIND_KEY: HUMAN_KIND},
        )
        assert attacker is None


class TestTheTwoLookupsCannotDisagree:
    def test_one_name_cannot_resolve_to_two_subjects(self, mgr, monkeypatch):
        """``get_identity`` and ``get_identity_by_principal`` must agree.

        This is the property A1 exists to guarantee: an attacker who can choose
        a registration name must not obtain a different subject's identity.
        """
        _force_widening(monkeypatch)
        victim = mgr.create_identity("victim-agent", scope=IdentityScope.L1)
        widened = victim.id
        assert len(widened) > 8
        monkeypatch.setattr(ident, "_AGENT_ID_ALLOCATION_ATTEMPTS", 16)

        # The attack.
        mgr.create_identity(
            principal=widened,
            scope=IdentityScope.L0,
            metadata={METADATA_KIND_KEY: HUMAN_KIND},
        )

        by_id = mgr.get_identity(widened)
        by_principal = mgr.get_identity_by_principal(widened)

        if by_id is None or by_principal is None:
            return  # at most one subject answers to the name: no divergence
        assert (by_id.namespace, by_id.principal) == (
            by_principal.namespace,
            by_principal.principal,
        ), (
            f"the same name {widened!r} resolved to two different subjects: "
            f"{by_id.namespace}:{by_id.principal!r} by id vs "
            f"{by_principal.namespace}:{by_principal.principal!r} by principal"
        )
