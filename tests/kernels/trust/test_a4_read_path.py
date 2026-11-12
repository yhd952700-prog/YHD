"""A4 regression: the revocation / expiry **read path** (F33).

Why this file exists
--------------------
F33 identified that the trust kernel had the mechanism but not the effect:
``get_score`` returned ``self._scores[entity_id][scope]`` directly, so it
consulted neither ``self._revoked`` nor the score's validity window. The write
side was fine; the read path was where "revoked" and "expired" silently had no
consequence.

The pre-existing test ``TestRevocation::test_revoke_removes_scores`` asserted
``get_score(e) is None`` after ``revoke(e)`` -- and passed even on the broken
version, because an **unscoped** ``revoke()`` also *deletes* the entry. The
assertion was therefore satisfied by the deletion side effect and could not
distinguish "revocation is honoured" from "the row is gone". A test that passes
for both the broken and the fixed implementation is not evidence.

These tests are written to be **discriminating**: each one fails on the
pre-A4 implementation and passes after it. The mechanism is a *scoped*
revocation (which by design leaves the other scopes in storage) and an
assignment with an explicit validity window (which pre-A4 could not even
express).

Measured Before/After (identical file, both trees, clean AUDIT_DB_PATH):

    baseline 98156dde        -> 5 failed
        (the three scoped-revocation tests fail because get_score / get_all_scores
         / get_score_at_scope_or_higher return the surviving scopes of a revoked
         entity; the two expiry tests fail because assign_score(valid_in=...) did
         not exist, so the read path had no window to honour)
    containment/p36-p0       -> 5 passed

Run:
    cd D:/LiuHao-AI-OS && .venv/Scripts/python.exe -m pytest tests/kernels/trust/test_a4_read_path.py -q
"""
from datetime import timedelta

import pytest

from src.kernels.trust import TrustLevel, TrustManager, TrustScope


@pytest.fixture
def tm() -> TrustManager:
    return TrustManager()


class TestScopedRevocationIsHonouredOnRead:
    """A scoped revoke removes one scope; the entity is still revoked outright."""

    def test_other_scopes_are_not_readable_after_a_scoped_revocation(self, tm):
        tm.assign_score("e1", 0.9, scope=TrustScope.L1)
        tm.assign_score("e1", 0.9, scope=TrustScope.L3)

        tm.revoke("e1", scope=TrustScope.L1, reason="scoped revocation")

        # The L3 row is still *stored* (scoped revoke only deletes L1) -- this is
        # what makes the assertion discriminating: the pre-A4 read path returned
        # it as a live score.
        assert tm.is_revoked("e1") is True
        assert tm.get_score("e1", TrustScope.L3) is None, (
            "a revoked entity must have no readable score, even at a scope the "
            "revocation did not name (F33: revocation must take effect on read)"
        )

    def test_get_all_scores_hides_a_scoped_revoked_entity(self, tm):
        tm.assign_score("e1", 0.9, scope=TrustScope.L1)
        tm.assign_score("e1", 0.9, scope=TrustScope.L3)
        tm.revoke("e1", scope=TrustScope.L1)
        assert tm.get_all_scores("e1") == {}

    def test_get_score_at_scope_or_higher_hides_a_scoped_revoked_entity(self, tm):
        # This is the value ``evaluate_trust_for_access`` reads to grant access,
        # so a stale answer here is an access decision on withdrawn evidence.
        tm.assign_score("e1", 0.9, scope=TrustScope.L3)
        tm.assign_score("e1", 0.9, scope=TrustScope.L1)
        tm.revoke("e1", scope=TrustScope.L1)
        assert tm.get_score_at_scope_or_higher("e1", TrustScope.L1) is None


class TestExpiredScoreIsHonouredOnRead:
    """A score past its validity window is not a live score."""

    def test_a_score_past_its_window_is_not_returned(self, tm):
        tm.assign_score("e1", 0.9, scope=TrustScope.L0, valid_in=timedelta(days=1))
        stored = tm.get_score("e1", TrustScope.L0)
        assert stored is not None, "a fresh window must still be readable"
        assert stored.is_expired is False

        # Close the window by re-assigning with an already-elapsed one.
        tm.assign_score("e1", 0.9, scope=TrustScope.L0, valid_in=timedelta(seconds=-1))
        assert tm.get_score("e1", TrustScope.L0) is None, (
            "a score whose validity window has closed must not be returned as live"
        )

    def test_expired_scores_are_filtered_out_of_get_all_scores(self, tm):
        tm.assign_score("e1", 0.9, scope=TrustScope.L0, valid_in=timedelta(seconds=-1))
        tm.assign_score("e1", 0.9, scope=TrustScope.L1, valid_in=timedelta(days=1))
        remaining = tm.get_all_scores("e1")
        assert TrustScope.L0 not in remaining
        assert TrustScope.L1 in remaining
