"""Audit Kernel unit tests.

Covers: hash-chain linkage, hash determinism, event logging, filtered
queries, integrity verification, statistics, and the global store.

Defect-evidence tests (names prefixed with ``test_defect_``) assert the
behavior REQUIRED BY SPEC (Definition Lock section 113: tamper-evident
audit trail + correlation-aware querying). They are expected to fail
until the kernel is fixed; each failure is a recorded defect.
"""
import re

import pytest

from src.kernels.audit import (
    AuditEventType,
    AuditScope,
    AuditStore,
    AuditEvent,
    get_audit_store,
)


@pytest.fixture
def store(tmp_path):
    """Fresh store backed by a per-test SQLite file."""
    return AuditStore(db_path=str(tmp_path / "audit.db"))


def log_three(store):
    """Log three events and return them in order."""
    return [
        store.log_event(
            AuditEventType.ACCESS_CHECK, f"p{i}", AuditScope.L1, "allow",
            details={"op": f"op{i}"}, correlation_id=f"corr-{i}",
        )
        for i in range(3)
    ]


# =====================================================================
# Logging and hash chain
# =====================================================================

class TestLogEvent:
    def test_first_event_has_no_previous_hash(self, store):
        event = store.log_event(AuditEventType.ACCESS_CHECK, "p1",
                                AuditScope.L1, "allow")
        assert event.prev_event_hash is None
        assert re.fullmatch(r"[0-9a-f]{64}", event.event_hash)

    def test_chain_links_each_event_to_previous(self, store):
        events = log_three(store)
        assert events[0].prev_event_hash is None
        assert events[1].prev_event_hash == events[0].event_hash
        assert events[2].prev_event_hash == events[1].event_hash
        assert len({e.event_hash for e in events}) == 3

    def test_explicit_correlation_id_is_preserved(self, store):
        event = store.log_event(AuditEventType.POLICY_EVAL, "p1",
                                AuditScope.L2, "deny",
                                correlation_id="my-correlation")
        assert event.correlation_id == "my-correlation"
        assert store.get_event(event.event_id)["correlation_id"] == "my-correlation"

    def test_correlation_id_generated_when_missing(self, store):
        event = store.log_event(AuditEventType.ACCESS_CHECK, "p1",
                                AuditScope.L1, "allow")
        assert event.correlation_id

    def test_details_default_to_empty_dict(self, store):
        event = store.log_event(AuditEventType.ACCESS_CHECK, "p1",
                                AuditScope.L1, "allow")
        assert event.details == {}
        assert store.get_event(event.event_id)["details"] == {}


class TestComputeHash:
    def make_event(self, **overrides):
        fields = dict(
            event_id="e1",
            event_type=AuditEventType.ACCESS_CHECK,
            principal_id="p1",
            scope=AuditScope.L1,
            timestamp=1000.0,
            correlation_id="c1",
            outcome="allow",
            details={"k": "v"},
        )
        fields.update(overrides)
        return AuditEvent(**fields)

    def test_hash_is_deterministic(self):
        assert (self.make_event().compute_hash()
                == self.make_event().compute_hash())

    def test_hash_sensitive_to_outcome(self):
        base = self.make_event().compute_hash()
        tampered = self.make_event(outcome="deny").compute_hash()
        assert base != tampered

    def test_hash_sensitive_to_details(self):
        base = self.make_event().compute_hash()
        tampered = self.make_event(details={"k": "TAMPERED"}).compute_hash()
        assert base != tampered

    def test_hash_sensitive_to_principal(self):
        base = self.make_event().compute_hash()
        tampered = self.make_event(principal_id="attacker").compute_hash()
        assert base != tampered

    def test_to_dict_roundtrip(self):
        event = self.make_event()
        d = event.to_dict()
        assert d["event_id"] == "e1"
        assert d["event_type"] == "access_check"
        assert d["scope"] == "L1"
        assert d["details"] == {"k": "v"}


# =====================================================================
# Queries
# =====================================================================

class TestQueryEvents:
    def test_query_by_principal(self, store):
        log_three(store)
        rows = store.query_events(principal_id="p1")
        assert len(rows) == 1
        assert rows[0]["principal_id"] == "p1"

    def test_query_by_scope(self, store):
        store.log_event(AuditEventType.ACCESS_CHECK, "p1", AuditScope.L1, "allow")
        store.log_event(AuditEventType.ACCESS_CHECK, "p2", AuditScope.L5, "allow")
        rows = store.query_events(scope=AuditScope.L5)
        assert len(rows) == 1
        assert rows[0]["scope"] == "L5"

    def test_query_by_outcome(self, store):
        log_three(store)
        store.log_event(AuditEventType.ACCESS_DENIED, "p9", AuditScope.L1, "deny")
        rows = store.query_events(outcome="deny")
        assert len(rows) == 1
        assert rows[0]["principal_id"] == "p9"

    def test_query_by_event_type(self, store):
        store.log_event(AuditEventType.ACCESS_CHECK, "p1", AuditScope.L1, "allow")
        store.log_event(AuditEventType.ROLE_GRANT, "p1", AuditScope.L1, "allow")
        rows = store.query_events(event_type=AuditEventType.ROLE_GRANT)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "role_grant"

    def test_query_by_time_range(self, store):
        import time
        # ensure distinct timestamps (time.time() can tie on Windows)
        e1 = store.log_event(AuditEventType.ACCESS_CHECK, "p1",
                             AuditScope.L1, "allow")
        time.sleep(0.02)
        e2 = store.log_event(AuditEventType.ACCESS_CHECK, "p2",
                             AuditScope.L1, "allow")
        time.sleep(0.02)
        store.log_event(AuditEventType.ACCESS_CHECK, "p3",
                        AuditScope.L1, "allow")
        rows = store.query_events(start_time=e1.timestamp, end_time=e2.timestamp)
        assert len(rows) == 2
        assert {r["principal_id"] for r in rows} == {"p1", "p2"}

    def test_query_time_boundaries(self, store):
        events = log_three(store)
        ts = events[0].timestamp
        # everything at or after the first event
        assert len(store.query_events(start_time=ts)) == 3
        # nothing before the first event minus a margin
        assert store.query_events(end_time=ts - 1000) == []
        # nothing after the last event plus a margin
        assert store.query_events(start_time=events[2].timestamp + 1000) == []

    def test_query_limit(self, store):
        log_three(store)
        rows = store.query_events(limit=2)
        assert len(rows) == 2
        # default order is ascending; first two events returned
        assert rows[0]["principal_id"] == "p0"

    def test_query_returns_details_as_dict(self, store):
        log_three(store)
        rows = store.query_events(principal_id="p0")
        assert rows[0]["details"] == {"op": "op0"}


class TestGetEvent:
    def test_get_event_found(self, store):
        events = log_three(store)
        row = store.get_event(events[1].event_id)
        assert row["event_id"] == events[1].event_id
        assert row["prev_event_hash"] == events[0].event_hash

    def test_get_event_missing_returns_none(self, store):
        assert store.get_event("no-such-id") is None


class TestStats:
    def test_stats_totals_and_breakdown(self, store):
        log_three(store)
        store.log_event(AuditEventType.ACCESS_DENIED, "p3", AuditScope.L1, "deny")
        stats = store.get_stats()
        assert stats["total_events"] == 4
        assert stats["breakdown"]["access_check:allow"] == 3
        assert stats["breakdown"]["access_denied:deny"] == 1


# =====================================================================
# Integrity verification
# =====================================================================

class TestVerifyIntegrity:
    def test_empty_store_is_valid(self, store):
        ok, total = store.verify_integrity()
        assert ok is True
        assert total == 0

    def test_untampered_chain_is_valid(self, store):
        log_three(store)
        ok, total = store.verify_integrity()
        assert ok is True
        assert total == 3

    def test_deleted_middle_event_detected(self, store):
        events = log_three(store)
        store._conn.execute(
            "DELETE FROM audit_events WHERE event_id = ?", (events[1].event_id,)
        )
        store._conn.commit()
        ok, total = store.verify_integrity()
        assert ok is False, "deleted middle event not detected"
        assert total == 2

    def test_broken_prev_link_detected(self, store):
        events = log_three(store)
        store._conn.execute(
            "UPDATE audit_events SET prev_event_hash = ? WHERE event_id = ?",
            ("deadbeef", events[1].event_id),
        )
        store._conn.commit()
        ok, _ = store.verify_integrity()
        assert ok is False, "broken chain link not detected"


# =====================================================================
# Global singleton
# =====================================================================

class TestGlobalStore:
    def test_get_audit_store_returns_same_instance(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "global_audit.db"))
        assert get_audit_store() is get_audit_store()


# =====================================================================
# Defect-evidence tests (expected to FAIL until kernel fixed)
# Spec basis: Definition Lock section 113 (tamper-evident audit trail,
# correlation-aware querying).
# =====================================================================

class TestDefects:
    def test_defect_content_tampering_detected(self, store):
        """DEFECT AUD-1 (P0): content tampering is NOT detected.

        Definition Lock section 113 requires a tamper-evident audit
        trail. verify_integrity() only checks prev_event_hash linkage;
        it never recomputes event hashes, so editing the outcome or
        details of a stored event leaves verification green. Expected:
        a modified event invalidates the chain.
        """
        events = log_three(store)
        store._conn.execute(
            "UPDATE audit_events SET outcome = ? WHERE event_id = ?",
            ("deny", events[1].event_id),
        )
        store._conn.commit()
        ok, _ = store.verify_integrity()
        assert ok is False, (
            "outcome of a stored audit event was modified but "
            "verify_integrity still passed"
        )

    def test_defect_details_tampering_detected(self, store):
        """DEFECT AUD-1 (P0), variant: details tampering is NOT detected."""
        events = log_three(store)
        store._conn.execute(
            "UPDATE audit_events SET details = ? WHERE event_id = ?",
            ('{"evil": true}', events[1].event_id),
        )
        store._conn.commit()
        ok, _ = store.verify_integrity()
        assert ok is False, (
            "details of a stored audit event were modified but "
            "verify_integrity still passed"
        )

    def test_defect_truncation_of_last_event_detected(self, store):
        """DEFECT AUD-2: truncating the tail of the log is NOT detected.

        Deleting the newest event leaves a fully consistent chain, so
        verify_integrity returns True. Expected: removal of any recorded
        event (including the last) invalidates the chain.
        """
        events = log_three(store)
        store._conn.execute(
            "DELETE FROM audit_events WHERE event_id = ?", (events[2].event_id,)
        )
        store._conn.commit()
        ok, _ = store.verify_integrity()
        assert ok is False, "last audit event was deleted but verification passed"

    def test_defect_query_supports_correlation_id(self, store):
        """DEFECT AUD-3: correlation-aware querying is not implemented.

        The module docstring (Definition Lock section 113) promises
        correlation-aware querying, and the schema even has an index on
        correlation_id, but query_events() has no correlation_id filter.
        Expected: events can be filtered by correlation_id.
        """
        events = log_three(store)
        rows = store.query_events(correlation_id="corr-1")
        assert [r["event_id"] for r in rows] == [events[1].event_id]

    def test_defect_query_reverse_returns_descending_order(self, store):
        """DEFECT AUD-4: reverse=True generates invalid SQL and crashes.

        query_events appends " DESC" after "ORDER BY timestamp ASC",
        producing "ORDER BY timestamp ASC DESC" and an
        sqlite3.OperationalError. Expected: reverse=True returns events
        in descending timestamp order.
        """
        log_three(store)
        rows = store.query_events(reverse=True)
        assert [r["principal_id"] for r in rows] == ["p2", "p1", "p0"]
