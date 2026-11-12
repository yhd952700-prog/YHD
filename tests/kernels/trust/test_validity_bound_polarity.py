"""A4 follow-up: the validity-bound invariant, across all three write paths.

Why this file exists
--------------------
A4 gave ``TrustScore`` a validity window and made the **read** path honour it.
The **write** path was left with three different answers to the same question
-- "the caller did not pass a TTL" -- and one of them was fail-open:

    assign_score     CLEARED the existing bound  (``valid_until = None``)
    update_score     preserved it
    establish_trust  preserved the derived aggregate, but a re-establishment
                     CLEARED the link's ``expires_at``

In a trust system "I forgot to pass the TTL" must never mean "this grant never
expires", so the invariant these tests pin down is:

    **a validity bound, once set, cannot be cleared by omission.**

Only an explicit ``valid_in`` / ``expires_in`` moves a bound.

Discriminating vs. pinning, stated honestly:

* ``TestAssignScoreObeysTheInvariant`` -- discriminating for ``assign_score``
  (the fail-open writer).
* ``TestEstablishTrustObeysTheInvariant`` -- the *link* tests are
  discriminating; the derived-aggregate test is a pin, because b6cf2b13 already
  preserved that side.
* ``TestUpdateScoreObeysTheInvariant`` -- ``update_score`` already obeyed the
  invariant, so nothing here can fail on b6cf2b13; these are regression guards.

The ``*_explicit_*`` tests pass on both trees on purpose: they stop the fix from
overshooting into "a bound can never be changed at all".

Measured (clean ``AUDIT_DB_PATH``, ``TrustManager()``), b6cf2b13:

    assign_score("e1", 0.9, L0, valid_in=timedelta(seconds=60))
        -> valid_until = 2026-09-21 15:12:11.875297
    assign_score("e1", 0.9, L0)          # TTL omitted
        -> valid_until = None            <-- bound CLEARED

Run:
    cd D:/LiuHao-AI-OS && AUDIT_DB_PATH=D:/cache/temp/audit_x.db \
        .venv/Scripts/python.exe -m pytest \
        tests/kernels/trust/test_validity_bound_polarity.py -q
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from src.kernels.trust import TrustManager, TrustScope

INVARIANT = "a validity bound, once set, cannot be cleared by omission"


@pytest.fixture
def tm() -> TrustManager:
    return TrustManager()


class TestAssignScoreObeysTheInvariant:
    """``assign_score`` used to be the fail-open path."""

    def test_omitting_valid_in_does_not_clear_an_existing_bound(self, tm):
        bound = tm.assign_score(
            "e1", 0.9, scope=TrustScope.L0, valid_in=timedelta(days=1)
        )
        # Read the *value* out now: both calls return the same mutated object,
        # so comparing ``again.valid_until`` against ``bound.valid_until`` would
        # be a tautology that passes on the broken tree too.
        bound_valid_until = bound.valid_until
        assert bound_valid_until is not None

        # The defect: this call used to set valid_until back to None, turning a
        # bounded TTL grant into a permanent one.
        again = tm.assign_score("e1", 0.9, scope=TrustScope.L0)

        assert again.valid_until == bound_valid_until, (
            f"{INVARIANT}: assign_score() without valid_in erased the existing "
            f"window ({bound_valid_until} -> {again.valid_until})"
        )

    def test_an_omitted_ttl_cannot_reopen_a_closed_window(self, tm):
        # The security consequence, stated directly: the window has already
        # closed (read path returns None), and a later assignment that merely
        # forgets the TTL must not bring the grant back to life.
        tm.assign_score(
            "e1", 0.9, scope=TrustScope.L0, valid_in=timedelta(seconds=-1)
        )
        assert tm.get_score("e1", TrustScope.L0) is None, "precondition: window closed"

        tm.assign_score("e1", 0.9, scope=TrustScope.L0)  # no valid_in

        assert tm.get_score("e1", TrustScope.L0) is None, (
            f"{INVARIANT}: omitting valid_in reopened a window that had closed"
        )

    def test_an_explicit_valid_in_still_moves_the_bound(self, tm):
        tm.assign_score(
            "e1", 0.9, scope=TrustScope.L0, valid_in=timedelta(seconds=-1)
        )
        tm.assign_score(
            "e1", 0.9, scope=TrustScope.L0, valid_in=timedelta(days=1)
        )
        stored = tm.get_score("e1", TrustScope.L0)
        assert stored is not None and stored.is_expired is False, (
            "an explicit valid_in must still be able to (re)set the bound -- the "
            "fix is 'omission preserves', not 'a bound is frozen'"
        )

    def test_a_fresh_entity_without_a_ttl_is_still_unbounded(self, tm):
        # The fix must not invent a bound nobody asked for, either: an entity
        # that never had a window keeps having none.
        fresh = tm.assign_score("brand-new", 0.5, scope=TrustScope.L0)
        assert fresh.valid_until is None
        assert tm.get_score("brand-new", TrustScope.L0) is not None


class TestUpdateScoreObeysTheInvariant:
    """``update_score`` was already correct; pinned so it stays that way.

    This path therefore has no test that can fail on b6cf2b13 -- a regression
    guard here is the strongest evidence available, and that absence is stated
    rather than papered over.
    """

    def test_omitting_valid_in_does_not_clear_an_existing_bound(self, tm):
        bound = tm.assign_score(
            "e1", 0.5, scope=TrustScope.L0, valid_in=timedelta(days=1)
        )
        bound_valid_until = bound.valid_until  # scalar, not the shared object
        assert bound_valid_until is not None

        updated = tm.update_score("e1", 0.1, scope=TrustScope.L0)

        assert updated.valid_until == bound_valid_until, INVARIANT

    def test_an_explicit_valid_in_still_moves_the_bound(self, tm):
        tm.assign_score("e1", 0.5, scope=TrustScope.L0, valid_in=timedelta(seconds=-1))
        moved = tm.update_score(
            "e1", 0.1, scope=TrustScope.L0, valid_in=timedelta(days=1)
        )
        assert moved.valid_until is not None and moved.is_expired is False


class TestEstablishTrustObeysTheInvariant:
    """Both the link *and* the derived aggregate are bounds."""

    def test_omitting_expires_in_does_not_clear_the_link_bound(self, tm):
        link = tm.establish_trust(
            "a", "b", 0.9, scope=TrustScope.L0, expires_in=timedelta(days=1)
        )
        bound = link.expires_at
        assert bound is not None

        re_established = tm.establish_trust("a", "b", 0.9, scope=TrustScope.L0)

        assert re_established is link, "precondition: the same link is updated in place"
        assert re_established.expires_at == bound, (
            f"{INVARIANT}: re-establishing without a TTL erased the link's "
            f"expires_at ({bound} -> {re_established.expires_at})"
        )

    def test_omitting_expires_in_does_not_clear_the_derived_aggregate_bound(self, tm):
        # NOT discriminating: b6cf2b13 already preserved the derived aggregate
        # (it only wrote it when a TTL was supplied). Kept as the regression
        # guard for the half of ``establish_trust`` that was already correct.
        #
        # Give the aggregate an explicit window first, then establish a link
        # with a TTL so the derived aggregate carries one too.
        tm.assign_score("b", 0.5, scope=TrustScope.L0)
        tm.establish_trust(
            "a", "b", 0.9, scope=TrustScope.L0, expires_in=timedelta(days=1)
        )
        derived = tm._scores["b"][TrustScope.L0]
        bound = derived.valid_until
        assert bound is not None

        tm.establish_trust("a", "b", 0.9, scope=TrustScope.L0)  # no expires_in

        assert derived.valid_until == bound, INVARIANT

    def test_omitting_expires_in_does_not_revive_an_expired_link(self, tm):
        tm.establish_trust(
            "a", "b", 0.9, scope=TrustScope.L0, expires_in=timedelta(seconds=-1)
        )
        assert tm.get_trust_chain("a", "b", TrustScope.L0).valid is False

        tm.establish_trust("a", "b", 0.9, scope=TrustScope.L0)  # no expires_in

        assert tm.get_trust_chain("a", "b", TrustScope.L0).valid is False, (
            f"{INVARIANT}: a bare re-establishment revived an expired link"
        )
