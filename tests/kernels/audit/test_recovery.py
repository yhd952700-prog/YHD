"""Tests for the HC-01 chain-fork recovery framework (src/kernels/audit/recovery).

These tests build a SYNTHETIC forked audit database entirely in memory. They
never open, read, or write the production ``audit_store.db`` or any Evidence
file. They validate the non-destructive recovery pipeline
(detect -> quarantine -> re-derive -> re-verify) against a controlled fixture.
"""

import hashlib
import json
import sqlite3

from src.kernels.audit.recovery import (
    detect,
    quarantine,
    rederive,
    reverified,
    run,
    QUARANTINE_TABLE,
    ANCHOR_TABLE,
)


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE audit_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            timestamp REAL NOT NULL,
            correlation_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            details TEXT,
            event_hash TEXT NOT NULL,
            prev_event_hash TEXT,
            seq INTEGER NOT NULL DEFAULT 0,
            hash_alg TEXT NOT NULL DEFAULT 'sha256',
            created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
        );
        CREATE TABLE chain_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_seq INTEGER NOT NULL,
            last_hash TEXT
        );
        """
    )


def _ehash(event_id: str, prev) -> str:
    data = {"event_id": event_id, "prev": prev}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()


def _insert(conn, eid, seq, prev, outcome="allow"):
    h = _ehash(eid, prev)
    conn.execute(
        "INSERT INTO audit_events "
        "(event_id, event_type, principal_id, scope, timestamp, "
        "correlation_id, outcome, details, event_hash, prev_event_hash, seq, "
        "hash_alg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            eid,
            "state_change",
            "kernel",
            "L0",
            float(seq),
            "c1",
            outcome,
            None,
            h,
            prev,
            seq,
            "sha256",
        ),
    )
    return h


def _build_forked_db() -> sqlite3.Connection:
    """Clean prefix seq 1..5, a 2-way fork at seq 6, continuation at seq 7."""
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    prev = None
    for s in range(1, 6):
        prev = _insert(conn, f"e{s}", s, prev)
    # Fork at seq 6: two rows, both referencing prev (seq 5 hash).
    h6a = _insert(conn, "e6a", 6, prev)
    h6b = _insert(conn, "e6b", 6, prev)
    # Continuation from branch a.
    h7 = _insert(conn, "e7", 7, h6a)
    conn.execute(
        "INSERT INTO chain_state (id, last_seq, last_hash) VALUES (1, 7, ?)",
        (h7,),
    )
    conn.commit()
    return conn


def _build_clean_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    prev = None
    for s in range(1, 8):
        prev = _insert(conn, f"e{s}", s, prev)
    conn.execute(
        "INSERT INTO chain_state (id, last_seq, last_hash) VALUES (1, 7, ?)",
        (prev,),
    )
    conn.commit()
    return conn


def test_detect_forked():
    conn = _build_forked_db()
    r = detect(conn)
    assert r.total_events == 8  # e1..e5 (5) + e6a,e6b (2) + e7 (1)
    assert r.distinct_seq == 7  # seqs 1..7, 6 duplicated
    assert r.max_seq == 7
    assert r.duplicate_seq_count == 1
    assert r.redundant_rows == 1
    assert r.fork_seqs == [6]
    # Robust lower bound: e6b (doesn't follow rowid-predecessor e6a) and
    # e7 (references h6a, but rowid-predecessor is e6b) -> 2 broken joins.
    assert r.broken_joins_rowid == 2
    assert r.first_event_prev_not_null is False
    assert r.chain_state_consistent is True


def test_detect_clean():
    conn = _build_clean_db()
    r = detect(conn)
    assert r.total_events == 7
    assert r.duplicate_seq_count == 0
    assert r.redundant_rows == 0
    assert r.fork_seqs == []
    assert r.broken_joins_rowid == 0
    assert r.broken_joins_seq == 0


def test_detect_chain_state_inconsistent():
    conn = _build_forked_db()
    conn.execute("UPDATE chain_state SET last_seq = 99, last_hash = 'x'")
    conn.commit()
    r = detect(conn)
    assert r.chain_state_consistent is False


def test_quarantine_non_destructive():
    conn = _build_forked_db()
    pre = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    r = detect(conn)
    q = quarantine(conn, r, "epoch-test", reason="unit-test")
    post = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert post == pre, "quarantine must not delete or modify original rows"
    assert q == 2, "both forked seq-6 rows must be copied"
    qrows = conn.execute(
        f"SELECT seq, authority FROM {QUARANTINE_TABLE}"
    ).fetchall()
    assert all(seq == 6 for seq, _ in qrows)
    assert all(auth == "NON-AUTHORITATIVE" for _, auth in qrows)


def test_rederive_anchor_above_history():
    conn = _build_forked_db()
    r = detect(conn)
    anchor_seq, anchor_hash = rederive(conn, r, "epoch-test")
    assert anchor_seq == 7
    row = conn.execute(
        f"SELECT last_seq, last_hash, epoch FROM {ANCHOR_TABLE} WHERE id = 1"
    ).fetchone()
    assert row[0] == 7
    assert row[1] == anchor_hash
    assert row[2] == "epoch-test"
    # Original chain_state untouched.
    orig = conn.execute(
        "SELECT last_seq FROM chain_state WHERE id = 1"
    ).fetchone()[0]
    assert orig == 7


def test_reverify_invariants():
    conn = _build_forked_db()
    pre = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    r = detect(conn)
    quarantine(conn, r, "epoch-test")
    rederive(conn, r, "epoch-test")
    v = reverified(conn, r, "epoch-test", pre_row_count=pre)
    assert v["all_ok"] is True
    assert v["checks"]["no_delete_row_count_unchanged"] is True
    assert v["checks"]["quarantine_captured_fork_rows"] is True
    assert v["checks"]["chain_state_untouched"] is True
    assert v["checks"]["anchor_consistent_with_tail"] is True


def test_run_end_to_end():
    conn = _build_forked_db()
    result = run(
        conn,
        epoch="e2e",
        historical_baseline_rows=331053,
        historical_baseline_hash="b6e27d",
        live_schema_version="9",
    )
    assert result["epoch"] == "e2e"
    assert result["quarantined_rows"] == 2
    assert result["anchor_seq"] == 7
    assert result["verification"]["all_ok"] is True
    # Manifest recorded with FIXED historical baseline + captured live state.
    manifest = conn.execute(
        "SELECT historical_baseline_rows, live_row_count, fork_clusters "
        "FROM migration_manifest WHERE epoch = ?",
        ("e2e",),
    ).fetchone()
    assert manifest[0] == 331053  # FIXED historical baseline
    assert manifest[1] == 8  # captured live row count
    assert manifest[2] == 1  # fork clusters


def test_run_clean_db_no_fork():
    conn = _build_clean_db()
    result = run(conn, epoch="clean")
    assert result["quarantined_rows"] == 0
    assert result["anchor_seq"] == 7
    assert result["verification"]["all_ok"] is True
