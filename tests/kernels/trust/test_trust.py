"""Trust Kernel unit tests.

Covers: trust score assignment/update with clamping, trust levels,
confidence, trust chain establishment, BFS path finding with per-hop
decay, scope-aware chains, revocation cascade, trust evaluation for
access, event history, and statistics.

Defect-evidence tests (names prefixed with ``test_defect_``) assert the
behavior REQUIRED BY SPEC (Definition Lock section 112). They are
expected to fail until the kernel is fixed.
"""
from datetime import timedelta
from src._time import utc_now

import pytest

from src.kernels.trust import (
    TrustEventType,
    TrustLevel,
    TrustManager,
    TrustScope,
    get_trust_manager,
)


@pytest.fixture
def tm() -> TrustManager:
    return TrustManager()


# =====================================================================
# Score assignment and update
# =====================================================================

class TestAssignScore:
    def test_assign_default_score(self, tm):
        score = tm.assign_score("e1")
        assert score.score == 0.5
        assert score.level is TrustLevel.MEDIUM
        assert score.confidence == 0.5

    def test_assign_clamps_out_of_range(self, tm):
        assert tm.assign_score("e1", initial_score=2.0).score == 1.0
        assert tm.assign_score("e2", initial_score=-1.0).score == 0.0

    def test_assign_records_event(self, tm):
        tm.assign_score("e1", initial_score=0.9, reasons=["track record"])
        events = tm.get_trust_events(entity_id="e1")
        assert len(events) == 1
        assert events[0].event_type is TrustEventType.POSITIVE
        assert "track record" in events[0].reason

    def test_assign_revoked_entity_raises(self, tm):
        tm.revoke("e1")
        with pytest.raises(ValueError):
            tm.assign_score("e1")


class TestUpdateScore:
    def test_positive_delta(self, tm):
        score = tm.update_score("e1", 0.2, reason="good work")
        assert score.score == pytest.approx(0.7)
        assert score.event_count == 1
        assert score.positive_events == 1
        assert score.negative_events == 0

    def test_negative_delta(self, tm):
        score = tm.update_score("e1", -0.3, reason="violation")
        assert score.score == pytest.approx(0.2)
        assert score.negative_events == 1

    def test_clamped_at_bounds(self, tm):
        score = tm.update_score("e1", 10.0)
        assert score.score == 1.0
        score = tm.update_score("e2", -10.0)
        assert score.score == 0.0

    def test_event_records_actual_delta_after_clamp(self, tm):
        tm.update_score("e1", 0.95)  # score at 1.0 (clamped from 1.45)
        tm.update_score("e1", 0.5)   # clamped: 1.0 + 0.5 -> 1.0, delta 0.0
        events = tm.get_trust_events(entity_id="e1")
        assert events[-1].score_delta == pytest.approx(0.0)

    def test_confidence_grows_with_event_count(self, tm):
        score = tm.update_score("e1", 0.1)
        assert score.confidence == pytest.approx(0.01)
        for _ in range(9):
            score = tm.update_score("e1", 0.0)
        assert score.confidence == pytest.approx(0.10)

    def test_update_revoked_entity_raises(self, tm):
        tm.revoke("e1")
        with pytest.raises(ValueError):
            tm.update_score("e1", 0.1)


class TestScoreLookup:
    def test_get_score_unassigned_returns_none(self, tm):
        assert tm.get_score("ghost") is None

    def test_scores_are_per_scope(self, tm):
        tm.assign_score("e1", initial_score=0.2, scope=TrustScope.L1)
        tm.assign_score("e1", initial_score=0.9, scope=TrustScope.L3)
        assert tm.get_score("e1", TrustScope.L1).score == 0.2
        assert tm.get_score("e1", TrustScope.L3).score == 0.9
        assert tm.get_score("e1", TrustScope.L0) is None
        assert len(tm.get_all_scores("e1")) == 2

    def test_get_score_at_scope_or_higher(self, tm):
        tm.assign_score("e1", initial_score=0.2, scope=TrustScope.L1)
        tm.assign_score("e1", initial_score=0.9, scope=TrustScope.L5)
        # highest scope at or above L3 is L5
        assert tm.get_score_at_scope_or_higher("e1", TrustScope.L3).score == 0.9
        # nothing at or above L6
        assert tm.get_score_at_scope_or_higher("e1", TrustScope.L6) is None

    def test_update_creates_score_at_scope(self, tm):
        score = tm.update_score("fresh", 0.1, scope=TrustScope.L2)
        assert score.score == pytest.approx(0.6)  # default 0.5 + 0.1
        assert tm.get_score("fresh", TrustScope.L2) is score


class TestTrustLevels:
    @pytest.mark.parametrize("score,level", [
        (0.0, TrustLevel.UNTRUSTED),
        (0.2, TrustLevel.UNTRUSTED),
        (0.21, TrustLevel.LOW),
        (0.4, TrustLevel.LOW),
        (0.41, TrustLevel.MEDIUM),
        (0.6, TrustLevel.MEDIUM),
        (0.61, TrustLevel.HIGH),
        (0.8, TrustLevel.HIGH),
        (0.81, TrustLevel.VERY_HIGH),
        (1.0, TrustLevel.VERY_HIGH),
    ])
    def test_level_boundaries(self, tm, score, level):
        assert tm.assign_score(f"e-{score}", initial_score=score).level is level

    def test_is_trusted_boundary(self, tm):
        assert tm.assign_score("t1", initial_score=0.5).is_trusted is True
        assert tm.assign_score("t2", initial_score=0.49).is_trusted is False

    def test_is_highly_trusted_boundary(self, tm):
        assert tm.assign_score("t1", initial_score=0.7).is_highly_trusted is True
        assert tm.assign_score("t2", initial_score=0.69).is_highly_trusted is False


# =====================================================================
# Trust chains
# =====================================================================

class TestEstablishTrust:
    def test_establish_creates_link(self, tm):
        link = tm.establish_trust("a", "b", 0.8)
        assert link.from_entity == "a"
        assert link.to_entity == "b"
        assert link.trust_score == 0.8
        assert link.active is True

    def test_establish_clamps_score(self, tm):
        assert tm.establish_trust("a", "b", 1.5).trust_score == 1.0
        assert tm.establish_trust("a", "c", -0.5).trust_score == 0.0

    def test_establish_records_propagation_event(self, tm):
        tm.establish_trust("a", "b", 0.8)
        events = tm.get_trust_events(entity_id="b")
        assert len(events) == 1
        assert events[0].event_type is TrustEventType.PROPAGATION
        assert events[0].source_entity == "a"

    def test_establish_revoked_raises(self, tm):
        tm.revoke("b")
        with pytest.raises(ValueError):
            tm.establish_trust("a", "b", 0.8)
        with pytest.raises(ValueError):
            tm.establish_trust("b", "a", 0.8)

    def test_establish_updates_existing_link_in_place(self, tm):
        first = tm.establish_trust("a", "b", 0.5)
        second = tm.establish_trust("a", "b", 0.9)
        assert second is first  # same link object, updated
        assert first.trust_score == 0.9
        # only one link stored
        tm.establish_trust("a", "c", 0.7)
        assert tm.stats()["total_chain_links"] == 2

    def test_establish_with_expiry(self, tm):
        link = tm.establish_trust("a", "b", 0.8, expires_in=timedelta(hours=1))
        assert link.expires_at is not None
        assert link.is_expired is False
        stale = tm.establish_trust("a", "c", 0.8,
                                   expires_in=timedelta(hours=-1))
        assert stale.is_expired is True


class TestTrustChain:
    def test_self_trust_is_perfect(self, tm):
        chain = tm.get_trust_chain("a", "a")
        assert chain.valid is True
        assert chain.composite_score == 1.0

    def test_direct_link(self, tm):
        tm.establish_trust("a", "b", 0.8)
        chain = tm.get_trust_chain("a", "b")
        assert chain.valid is True
        assert chain.composite_score == pytest.approx(0.8)

    def test_two_hops_apply_decay(self, tm):
        tm.establish_trust("a", "b", 1.0)
        tm.establish_trust("b", "c", 1.0)
        chain = tm.get_trust_chain("a", "c")
        assert chain.valid is True
        # second hop decays by 0.9: 1.0 * (1.0 * 0.9) = 0.9
        assert chain.composite_score == pytest.approx(0.9)

    def test_no_path_is_invalid(self, tm):
        chain = tm.get_trust_chain("a", "z")
        assert chain.valid is False
        assert chain.composite_score == 0.0

    def test_inactive_link_breaks_chain(self, tm):
        link = tm.establish_trust("a", "b", 0.9)
        link.active = False
        chain = tm.get_trust_chain("a", "b")
        assert chain.valid is False
        assert chain.composite_score == 0.0

    def test_expired_link_breaks_chain(self, tm):
        tm.establish_trust("a", "b", 0.9, expires_in=timedelta(hours=-1))
        chain = tm.get_trust_chain("a", "b")
        assert chain.valid is False

    def test_low_composite_below_threshold_invalid(self, tm):
        # 0.5 * (0.5 * 0.9) = 0.225 still valid; add one more hop:
        # 0.5 * 0.45 * (0.5 * 0.81) = 0.091 < 0.1 threshold
        tm.establish_trust("a", "b", 0.5)
        tm.establish_trust("b", "c", 0.5)
        tm.establish_trust("c", "d", 0.5)
        chain = tm.get_trust_chain("a", "d")
        assert chain.valid is False

    def test_chain_requires_link_at_requested_scope(self, tm):
        # link established at L0 cannot serve an L1 request
        tm.establish_trust("a", "b", 0.9, scope=TrustScope.L0)
        assert tm.get_trust_chain("a", "b", scope=TrustScope.L1).valid is False
        # link at higher scope serves lower requests
        tm2 = TrustManager()
        tm2.establish_trust("a", "b", 0.9, scope=TrustScope.L2)
        assert tm2.get_trust_chain("a", "b", scope=TrustScope.L1).valid is True

    def test_bfs_finds_shortest_path(self, tm):
        tm.establish_trust("a", "b", 0.9)
        tm.establish_trust("b", "c", 0.9)
        tm.establish_trust("a", "c", 0.5)  # shortcut
        chain = tm.get_trust_chain("a", "c")
        assert len(chain.links) == 1
        assert chain.links[0].trust_score == 0.5

    def test_max_hops_limits_chain_length(self, tm):
        # 5-hop chain is within the default max_hops=5
        nodes = ["n0", "n1", "n2", "n3", "n4", "n5"]
        for i in range(5):
            tm.establish_trust(nodes[i], nodes[i + 1], 0.9)
        assert tm.get_trust_chain("n0", "n5").valid is True
        # 6-hop chain exceeds it
        nodes = ["m0", "m1", "m2", "m3", "m4", "m5", "m6"]
        for i in range(6):
            tm.establish_trust(nodes[i], nodes[i + 1], 0.9)
        assert tm.get_trust_chain("m0", "m6").valid is False


class TestPropagateTrust:
    def test_propagate_matches_chain_composite(self, tm):
        tm.establish_trust("a", "b", 0.8)
        assert tm.propagate_trust("a", "b") == pytest.approx(0.8)

    def test_propagate_no_chain_is_zero(self, tm):
        assert tm.propagate_trust("a", "z") == 0.0


# =====================================================================
# Revocation
# =====================================================================

class TestRevocation:
    def test_revoke_returns_true_and_marks_revoked(self, tm):
        assert tm.revoke("e1", reason="policy violation") is True
        assert tm.is_revoked("e1") is True
        events = tm.get_trust_events(entity_id="e1", event_type=TrustEventType.REVOCATION)
        assert len(events) == 1
        assert events[0].reason == "policy violation"

    def test_revoke_removes_scores(self, tm):
        tm.assign_score("e1", 0.9)
        tm.revoke("e1")
        assert tm.get_score("e1") is None

    def test_revoke_cascades_to_chains(self, tm):
        tm.establish_trust("a", "b", 0.9)
        tm.establish_trust("b", "c", 0.9)
        tm.revoke("b")
        # link a->b removed (b is target)
        assert tm.get_trust_chain("a", "b").valid is False
        # link b->c removed (b is source)
        assert tm.get_trust_chain("b", "c").valid is False
        # transitive path a->c through b is gone
        assert tm.get_trust_chain("a", "c").valid is False

    def test_revoked_entity_cannot_get_new_trust(self, tm):
        tm.revoke("b")
        with pytest.raises(ValueError):
            tm.establish_trust("a", "b", 0.9)


# =====================================================================
# Trust evaluation for access
# =====================================================================

class TestEvaluateTrustForAccess:
    def test_meeting_required_level(self, tm):
        tm.assign_score("e1", initial_score=0.5)  # MEDIUM
        assert tm.evaluate_trust_for_access("e1", TrustLevel.MEDIUM) is True
        assert tm.evaluate_trust_for_access("e1", TrustLevel.HIGH) is False

    def test_high_score_meets_high_level(self, tm):
        tm.assign_score("e1", initial_score=0.7)  # HIGH
        assert tm.evaluate_trust_for_access("e1", TrustLevel.HIGH) is True
        assert tm.evaluate_trust_for_access("e1", TrustLevel.VERY_HIGH) is False

    def test_unassigned_entity_fails(self, tm):
        assert tm.evaluate_trust_for_access("ghost") is False

    def test_revoked_entity_fails(self, tm):
        tm.assign_score("e1", initial_score=1.0)
        tm.revoke("e1")
        assert tm.evaluate_trust_for_access("e1") is False

    def test_scope_uses_score_at_or_above_required_scope(self, tm):
        tm.assign_score("e1", initial_score=0.9, scope=TrustScope.L5)
        # L5 score satisfies an L3 requirement
        assert tm.evaluate_trust_for_access(
            "e1", TrustLevel.HIGH, required_scope=TrustScope.L3
        ) is True
        # but not an L6 requirement (no score at or above L6)
        assert tm.evaluate_trust_for_access(
            "e1", TrustLevel.HIGH, required_scope=TrustScope.L6
        ) is False


# =====================================================================
# Events / stats / singleton
# =====================================================================

class TestEventsAndStats:
    def test_get_trust_events_filters(self, tm):
        tm.update_score("e1", 0.1, reason="one")
        tm.update_score("e2", -0.1, reason="two")
        tm.update_score("e1", 0.1, reason="three")
        assert len(tm.get_trust_events(entity_id="e1")) == 2
        future = utc_now() + timedelta(hours=1)
        assert tm.get_trust_events(since=future) == []
        assert len(tm.get_trust_events(limit=1)) == 1

    def test_stats_shape(self, tm):
        tm.assign_score("e1", initial_score=0.9)
        tm.assign_score("e2", initial_score=0.5)
        tm.establish_trust("e1", "e2", 0.8)
        tm.revoke("e3")
        stats = tm.stats()
        # only entities with assigned scores are counted; revoked e3
        # had its scores removed by the revocation cascade
        assert stats["total_entities"] == 2
        assert stats["total_chain_links"] == 1
        assert stats["revoked_entities"] == 1
        assert stats["by_trust_level"]["very_high"] == 1
        assert stats["by_trust_level"]["medium"] == 1
        assert stats["total_events"] >= 3

    def test_get_trust_manager_singleton(self):
        assert get_trust_manager() is get_trust_manager()


# =====================================================================
# Defect-evidence tests (expected to FAIL until kernel fixed)
# Spec basis: Definition Lock section 112 (trust events per entity).
# =====================================================================

class TestDefects:
    def test_defect_event_pruning_is_per_entity(self):
        """DEFECT TR-1: event pruning truncates the GLOBAL event list.

        update_score prunes with
        ``self._events = self._events[-max_events_per_entity:]`` when a
        SINGLE entity exceeds the cap, silently discarding other
        entities' history. Expected: pruning affects only the entity
        that exceeded the cap; other entities keep their events.
        """
        tm = TrustManager()
        tm.update_score("victim", 0.1, reason="important early event")
        for i in range(1001):
            tm.update_score("noise", 0.0, reason=f"noise {i}")
        victim_events = tm.get_trust_events(entity_id="victim")
        assert len(victim_events) >= 1, (
            "victim's only event was pruned by another entity's flood"
        )
