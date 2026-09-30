"""F5/F6 regression tests: deterministic fork detection + live no-fork gate.

F5 (RCA-1 remediation, team-owned engineering under HC-01-Autonomous-
Decision-Memo.md): the audit verifier must deterministically detect a forked
chain -- duplicate ``seq`` numbers, the RCA-1 concurrency signature -- and the
fork metric must be independently countable.

F6: the fail-closed live-DB no-fork monitor (scripts/verify_audit_chain_no_fork.py)
must (a) SKIP with exit 0 when no live store is deployed, (b) PASS when the
live store is intact, and (c) FAIL (non-zero) when the live store is forked.

These tests never touch the real deployed audit_store.db; everything runs on
per-test temp files.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.kernels.audit import (
    AuditEventType,
    AuditScope,
    AuditStore,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "scripts" / "verify_audit_chain_no_fork.py"


def _build_clean_store(db_path: str, n: int = 5) -> AuditStore:
    store = AuditStore(db_path=db_path)
    for i in range(n):
        store.log_event(
            AuditEventType.STATE_CHANGE, "p%d" % i, AuditScope.L0, "ok",
            details={"i": i}, correlation_id="c%d" % i,
        )
    return store


def test_clean_store_has_no_duplicate_seq_and_verifies(tmp_path):
    db = str(tmp_path / "clean.db")
    store = _build_clean_store(db, n=5)
    ok, total = store.verify_integrity()
    assert ok is True, "clean store must verify ok"
    assert total == 5
    assert store.duplicate_seq_count() == 0, "clean store must have no dup seq"
    store._conn.close()


def test_forked_store_detected_by_verifier_and_metric(tmp_path, monkeypatch):
    """Inject an RCA-1 fork out-of-band (duplicate seq) and prove both the
    verifier and the independent dup metric catch it.

    The live code refuses to OPEN a forked store (UNIQUE(seq) backstop in
    _apply_schema), mirroring the cold-storage forked copy. To exercise the
    verifier's read path we neutralise only the index creation so the legacy
    forked store can be opened and read (the heavy reads go through the
    read-only snapshot, which never runs _apply_schema).
    """
    db = str(tmp_path / "fork.db")
    # Build the forked DB directly with raw sqlite: no UNIQUE(seq) backstop,
    # mirroring a store created before the index existed.
    raw = sqlite3.connect(db)
    raw.execute(
        "CREATE TABLE audit_events ("
        "seq INTEGER, event_id TEXT, event_type TEXT, principal_id TEXT, "
        "scope TEXT, timestamp REAL, correlation_id TEXT, outcome TEXT, "
        "details TEXT, event_hash TEXT, prev_event_hash TEXT, "
        "hash_alg TEXT, link_hash TEXT)"
    )
    raw.execute(
        "CREATE TABLE chain_state (id INTEGER PRIMARY KEY, last_seq INTEGER, "
        "last_hash TEXT, last_link_hash TEXT)"
    )
    # 4 events, then a 5th that DUPLICATES seq=3 (the fork).
    rows = [
        (1, "e1", "STATE_CHANGE", "p", "L0", 1.0, "c1", "ok", "{}", "h1", None, "sha256", None),
        (2, "e2", "STATE_CHANGE", "p", "L0", 2.0, "c2", "ok", "{}", "h2", "h1", "sha256", None),
        (3, "e3", "STATE_CHANGE", "p", "L0", 3.0, "c3", "ok", "{}", "h3", "h2", "sha256", None),
        (3, "e3-dup", "STATE_CHANGE", "p", "L0", 3.5, "c3b", "ok", "{}", "h3b", "h2", "sha256", None),
        (4, "e4", "STATE_CHANGE", "p", "L0", 4.0, "c4", "ok", "{}", "h4", "h3b", "sha256", None),
    ]
    raw.executemany(
        "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    raw.execute("INSERT INTO chain_state VALUES (1, 4, 'h4', NULL)")
    raw.commit()
    raw.close()

    # Neutralise ONLY the UNIQUE(seq) index creation so the forked store can be
    # opened; the lease table is created by SqliteWriterLease itself.
    def _noop_apply_schema(self):
        try:
            self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    monkeypatch.setattr(AuditStore, "_apply_schema", _noop_apply_schema)

    store = AuditStore(db_path=db)
    ok, total = store.verify_integrity()
    assert ok is False, "forked store must FAIL integrity verification"
    assert store.duplicate_seq_count() == 1, "exactly one duplicate row expected"
    store._conn.close()


def _run_gate(db_path) -> tuple[int, str]:
    env = dict(os.environ)
    env["AUDIT_DB_PATH"] = str(db_path)
    proc = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_no_fork_gate_skips_when_no_live_store(tmp_path):
    missing = tmp_path / "does_not_exist.db"
    rc, out = _run_gate(missing)
    assert rc == 0, "missing live store must SKIP (rc 0), got %d: %s" % (rc, out)
    assert "SKIP" in out


def test_no_fork_gate_passes_on_clean_live_store(tmp_path):
    db = tmp_path / "clean_live.db"
    _build_clean_store(str(db), n=4)._conn.close()
    rc, out = _run_gate(db)
    assert rc == 0, "clean live store must PASS (rc 0), got %d: %s" % (rc, out)
    assert "PASS" in out


def test_no_fork_gate_fails_on_forked_live_store(tmp_path):
    db = tmp_path / "fork_live.db"
    _build_clean_store(str(db), n=4)._conn.close()
    # Inject a fork out-of-band, then drop the UNIQUE(seq) index so the store
    # still opens (mirrors a legacy forked store the live code refuses to open
    # via the index backstop -- the gate must still alert).
    raw = sqlite3.connect(str(db))
    raw.execute("DROP INDEX IF EXISTS uidx_seq")
    raw.execute(
        "INSERT INTO audit_events "
        "(seq, event_id, event_type, principal_id, scope, timestamp, "
        "correlation_id, outcome, details, event_hash, prev_event_hash, "
        "hash_alg, link_hash) "
        "SELECT seq, event_id || '-dup', event_type, principal_id, scope, "
        "timestamp, correlation_id, outcome, details, event_hash, "
        "prev_event_hash, hash_alg, link_hash FROM audit_events WHERE seq = 2"
    )
    raw.commit()
    raw.close()
    rc, out = _run_gate(db)
    assert rc != 0, "forked live store must FAIL (non-zero), got %d: %s" % (rc, out)
    assert "FAIL" in out
