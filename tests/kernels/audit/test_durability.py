"""Tests for WAL correctness + transaction-consistent snapshot
(src/kernels/audit/durability).

Never opens, reads, or writes the production ``audit_store.db`` or any Evidence
file. All tests use throwaway temp DBs.
"""

import os
import sqlite3
import tempfile

from src.kernels.audit.durability import (
    configure_audit_durability,
    snapshot_audit_db,
    _VALID_LEVELS,
)


def _make_db(path: str, *, wal: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE audit_events ("
            "event_id TEXT PRIMARY KEY, event_hash TEXT, prev_event_hash TEXT, "
            "seq INTEGER)"
        )
        for s in range(1, 11):
            conn.execute(
                "INSERT INTO audit_events(event_id,event_hash,prev_event_hash,seq) "
                "VALUES(?,?,?,?)",
                (f"e{s}", f"h{s}", f"h{s-1}" if s > 1 else None, s),
            )
        conn.commit()
    finally:
        conn.close()


def _count(path: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    finally:
        conn.close()


def test_configure_durability_levels():
    _INT_TO_NAME = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}
    conn = sqlite3.connect(":memory:")
    for lvl in ("OFF", "NORMAL", "FULL", "EXTRA"):
        got = configure_audit_durability(conn, lvl)
        # configure_audit_durability normalises the returned level to a name.
        assert got.lower() == lvl.lower()
        # Confirm it actually took effect (PRAGMA returns the integer code).
        cur = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert _INT_TO_NAME[cur].lower() == lvl.lower()


def test_configure_durability_invalid_level():
    conn = sqlite3.connect(":memory:")
    for bad in ("BOGUS", "fsync", ""):
        try:
            configure_audit_durability(conn, bad)
            assert False, f"invalid level {bad!r} should raise"
        except ValueError:
            pass


def test_snapshot_matches_counts_journal():
    src = tempfile.mktemp(suffix=".db")
    dst = tempfile.mktemp(suffix=".db")
    if os.path.exists(dst):
        os.remove(dst)
    try:
        _make_db(src, wal=False)
        src_n, dst_n = snapshot_audit_db(src, dst)
        assert src_n == 10
        assert dst_n == 10
        assert _count(dst) == _count(src)
        # Destination is itself a valid db and queryable.
        assert _count(dst) == 10
    finally:
        for p in (src, dst):
            if os.path.exists(p):
                os.remove(p)


def test_snapshot_matches_counts_wal():
    """The real evidence-store scenario is WAL mode. VACUUM INTO must still
    produce a transaction-consistent copy."""
    src = tempfile.mktemp(suffix=".db")
    dst = tempfile.mktemp(suffix=".db")
    if os.path.exists(dst):
        os.remove(dst)
    try:
        _make_db(src, wal=True)
        src_n, dst_n = snapshot_audit_db(src, dst)
        assert src_n == dst_n == 10
        assert _count(dst) == 10
    finally:
        for p in (src, dst):
            if os.path.exists(p):
                os.remove(p)


def test_snapshot_rejects_existing_dst():
    src = tempfile.mktemp(suffix=".db")
    dst = tempfile.mktemp(suffix=".db")
    try:
        _make_db(src, wal=False)
        # Pre-create dst so it exists.
        open(dst, "w").close()
        try:
            snapshot_audit_db(src, dst)
            assert False, "should refuse to overwrite existing destination"
        except FileExistsError:
            pass
    finally:
        for p in (src, dst):
            if os.path.exists(p):
                os.remove(p)
