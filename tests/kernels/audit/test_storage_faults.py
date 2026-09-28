"""Storage-fault resilience for the audit store.

Extends the transient-fault recovery in ``test_write_retry.py`` with the
storage faults that are NOT ordinary lock contention:

  * SQLITE_FULL (13)  -- disk / database full: retry (bounded), then fail
    closed with a CLEAR error so evidence is never silently dropped.
  * SQLITE_CORRUPT (11) / SQLITE_IOERR (10) -- a corrupt or I/O-errored DB/WAL.
    These must NOT be blind-retried into a worse state: the first sight of
    corruption quarantines the evidence files (never deletes them) and raises a
    CLEAR error, optionally restoring from a transaction-consistent backup.

And a multi-process test that KILLS a writer mid-transaction and verifies the
other writer recovers with the chain invariant intact -- using POLLING for the
kill instead of a fixed ``sleep`` so it is not timing-fragile.

None of these tests touch the production ``audit_store.db``; every test uses a
throwaway temp database.
"""
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

import pytest

from src.kernels.audit import (
    AuditCorruptionError,
    AuditDiskFullError,
    AuditEventType,
    AuditScope,
    AuditStore,
)
from src.kernels.audit.durability import snapshot_audit_db
from src.kernels import audit as audit_mod

SQLITE_BUSY = 5
SQLITE_READONLY = 8
SQLITE_IOERR = 10
SQLITE_CORRUPT = 11
SQLITE_FULL = 13

ROOT = pathlib.Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- #
# Fault injection (same deterministic technique as test_write_retry.py)
# --------------------------------------------------------------------------- #
class _FlakyConnection:
    """Wrap a real connection and fail the first N BEGIN statements with a
    chosen SQLite result code. Deterministic -- no timing luck involved."""

    def __init__(self, real, fail_code, fail_times):
        self._real = real
        self._fail_code = fail_code
        self._fail_times = fail_times
        self.begin_calls = 0

    def execute(self, sql, *args):
        if sql.strip().upper().startswith("BEGIN"):
            self.begin_calls += 1
            if self.begin_calls <= self._fail_times:
                exc = sqlite3.OperationalError(
                    f"injected fault code={self._fail_code}")
                exc.sqlite_errorcode = self._fail_code
                raise exc
        return self._real.execute(sql, *args)

    def commit(self):
        return self._real.commit()

    def rollback(self):
        return self._real.rollback()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _install_fault(store, fail_code, fail_times):
    proxy = _FlakyConnection(store._conn, fail_code, fail_times)
    store._conn = proxy
    return proxy


def _rows(db):
    conn = sqlite3.connect(db)
    try:
        seqs = [r[0] for r in
                conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    finally:
        conn.close()
    return seqs


# --------------------------------------------------------------------------- #
# (a) disk-full / READONLY retried, then fails closed with a CLEAR error
# --------------------------------------------------------------------------- #
def test_disk_full_is_retried_then_fails_closed_with_clear_error(tmp_path):
    """SQLITE_FULL must be retried a bounded number of times, then fail closed
    with a CLEAR AuditDiskFullError -- the event is NOT written, so no silent
    data loss."""
    db = str(tmp_path / "full.db")
    store = AuditStore(db_path=db)
    store.initialize()
    proxy = _install_fault(store, SQLITE_FULL, fail_times=9999)

    with pytest.raises(AuditDiskFullError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 0}, "c0")

    # Bounded retry, not an infinite loop.
    assert proxy.begin_calls >= 1
    assert proxy.begin_calls <= 8, "FULL must be bounded, not retried forever"
    # No silent data loss: nothing was persisted.
    assert _rows(db) == [], "a failed write must not leave a phantom event"
    # The clear error carries the cause.
    with pytest.raises(AuditDiskFullError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 1}, "c1")


def test_readonly_exhaustion_fails_closed_with_a_clear_error(tmp_path, monkeypatch):
    """Complements (a): a READONLY fault that NEVER clears must fail closed --
    the retry budget is exhausted and the error is reported, not swallowed.

    We pin the poisoned handle in place (``_reopen`` is a no-op) so the injected
    fault cannot be escaped by reopening, mirroring a genuinely readonly store.
    """
    db = str(tmp_path / "ro.db")
    store = AuditStore(db_path=db)
    store.initialize()
    monkeypatch.setattr(store, "_reopen", lambda: None)
    proxy = _install_fault(store, SQLITE_READONLY, fail_times=9999)

    with pytest.raises(sqlite3.OperationalError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 0}, "c0")
    assert proxy.begin_calls == audit_mod._MAX_WRITE_ATTEMPTS
    assert _rows(db) == [], "a failed write must not leave a phantom event"


# --------------------------------------------------------------------------- #
# (b) corruption / IOERR detected and handled: quarantine + clear error, NOT a
#     crash-loop
# --------------------------------------------------------------------------- #
def test_corruption_is_quarantined_not_crash_looped(tmp_path):
    """A SQLITE_CORRUPT write fault must NOT be blind-retried into a worse
    state. It is quarantined (evidence preserved, never deleted) and raises a
    CLEAR AuditCorruptionError -- exactly once, not in a retry loop."""
    db = str(tmp_path / "corrupt.db")
    store = AuditStore(db_path=db)
    store.initialize()
    store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                    {"i": 0}, "c0")
    proxy = _install_fault(store, SQLITE_CORRUPT, fail_times=1)

    with pytest.raises(AuditCorruptionError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 1}, "c1")

    # The corrupt fault was handled on the FIRST attempt -- no crash-loop.
    assert proxy.begin_calls == 1, "corruption must not be retried in a loop"
    # Evidence was quarantined (moved aside), not deleted.
    assert store._last_quarantine_dir is not None
    assert os.path.isdir(store._last_quarantine_dir)
    quarantined = os.listdir(store._last_quarantine_dir)
    assert any(f.startswith("corrupt.db") for f in quarantined), quarantined
    # The triggering write did NOT land: the quarantined evidence still holds
    # exactly the one pre-existing event, not the corrupt (never-committed) one.
    quar_db = os.path.join(store._last_quarantine_dir, "corrupt.db")
    assert _rows(quar_db) == [1], "the corrupt write must not have persisted"


def test_ioerr_is_quarantined_like_corruption(tmp_path):
    """SQLITE_IOERR is handled the same careful way as corruption: quarantine +
    clear error, no blind retry."""
    db = str(tmp_path / "ioerr.db")
    store = AuditStore(db_path=db)
    store.initialize()
    proxy = _install_fault(store, SQLITE_IOERR, fail_times=1)

    with pytest.raises(AuditCorruptionError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 0}, "c0")

    assert proxy.begin_calls == 1, "IOERR must not be retried in a loop"
    assert store._last_quarantine_dir is not None


def test_corruption_restores_from_backup(tmp_path):
    """When a transaction-consistent backup exists, corruption recovery restores
    it so the store stays usable on known-good evidence."""
    db = str(tmp_path / "restore.db")
    backup = str(tmp_path / "restore.db.backup")
    healthy = AuditStore(db_path=db)
    healthy.initialize()
    for i in range(50):
        healthy.log_event(
            AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok", {"i": i},
            f"c{i}")
    snapshot_audit_db(db, backup)

    # A fresh store pointed at the backup.
    store = AuditStore(db_path=db, backup_path=backup)
    store.initialize()
    proxy = _install_fault(store, SQLITE_CORRUPT, fail_times=1)

    with pytest.raises(AuditCorruptionError):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": 999}, "c999")
    # No crash-loop even on the restore path.
    assert proxy.begin_calls == 1

    # Restored from the backup: the store is online on the 50 known-good rows.
    ok, n = store.verify_integrity()
    assert ok is True
    assert n == 50, "corruption recovery should restore the backup's evidence"
    # The backup itself is preserved (not consumed).
    assert os.path.exists(backup)


def test_real_file_corruption_detected_clearly(tmp_path):
    """End-to-end: a genuinely corrupt DB file is detected by the health check
    and by verify_integrity as a CLEAR AuditCorruptionError -- not a crash-loop,
    and the (corrupt) evidence file is left in place for forensics."""
    db = str(tmp_path / "real.db")
    store = AuditStore(db_path=db)
    store.initialize()
    for i in range(200):
        store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                        {"i": i}, f"c{i}")
    # Push everything into the main db file so the corruption is visible there.
    store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store._conn.close()

    # Flip a byte in the middle of the file (a data page, not the 100-byte
    # header) so the schema still opens but the data is malformed.
    size = os.path.getsize(db)
    off = max(4096, size // 2)
    with open(db, "r+b") as f:
        f.seek(off)
        original = f.read(1)
        f.seek(off)
        f.write(bytes([original[0] ^ 0xFF]))

    store2 = AuditStore(db_path=db)
    store2.initialize()

    with pytest.raises(AuditCorruptionError):
        store2.check_storage_health()
    with pytest.raises(AuditCorruptionError):
        store2.verify_integrity()

    # Evidence must NOT be deleted -- operators need it for forensics.
    assert os.path.exists(db)


# --------------------------------------------------------------------------- #
# (c) multi-process: KILL a writer mid-transaction, the other recovers
# --------------------------------------------------------------------------- #
_CHILD_VICTIM = """
import sys, time
sys.path.insert(0, %(root)r)
from src.kernels.audit import AuditStore, AuditEventType, AuditScope
db, prog = sys.argv[1], sys.argv[2]
store = AuditStore(db_path=db); store.initialize()
n = 0
while True:
    store.log_event(AuditEventType.STATE_CHANGE, "victim", AuditScope.L0, "ok",
                    {"i": n}, "v" + str(n))
    n += 1
    with open(prog, "w") as f:
        f.write(str(n))
"""

_CHILD_APPENDER = """
import sys
sys.path.insert(0, %(root)r)
from src.kernels.audit import AuditStore, AuditEventType, AuditScope
db, count, tag = sys.argv[1], int(sys.argv[2]), sys.argv[3]
store = AuditStore(db_path=db); store.initialize()
for i in range(count):
    store.log_event(AuditEventType.STATE_CHANGE, "proc-" + tag, AuditScope.L0,
                    "success", {"i": i, "proc": tag}, tag + "-" + str(i))
print("OK")
"""

_CHILD_APPENDER_PROG = """
import sys
sys.path.insert(0, %(root)r)
from src.kernels.audit import AuditStore, AuditEventType, AuditScope
db, count, tag, prog = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
store = AuditStore(db_path=db); store.initialize()
for i in range(count):
    store.log_event(AuditEventType.STATE_CHANGE, "proc-" + tag, AuditScope.L0,
                    "success", {"i": i, "proc": tag}, tag + "-" + str(i))
    with open(prog, "w") as f:
        f.write(str(i + 1))
print("OK")
"""


def _run_child(source, *args, timeout=180.0):
    script = source % {"root": str(ROOT)}
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


def test_killing_a_writer_mid_transaction_recovers_chain(tmp_path):
    """Kill a live writer mid-transaction and assert a sibling writer still
    recovers and the audit chain stays contiguous (no fork).

    The fragile part of the old CI was a fixed ``sleep(0.4)`` before the kill.
    Here we POLL the victim's progress file until it has committed, then POLL
    until the process is actually dead -- no fixed sleep.
    """
    db = str(tmp_path / "audit.db")
    prog = str(tmp_path / "victim.prog")

    # Seed the schema so both processes contend for the SAME database.
    AuditStore(db_path=db).initialize()

    victim = subprocess.Popen(
        [sys.executable, "-c", _CHILD_VICTIM % {"root": str(ROOT)}, db, prog],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
    )

    # POLL (not sleep) until the victim has committed several events.
    deadline = time.time() + 30
    committed = 0
    while time.time() < deadline:
        try:
            committed = int(open(prog).read())
        except (FileNotFoundError, ValueError):
            committed = 0
        if committed >= 5:
            break
        time.sleep(0.01)
    if committed < 5:
        victim.kill()
        raise AssertionError("victim never made progress before kill")

    # Hard kill == process crash, while it is mid-transaction.
    victim.kill()
    # POLL (not sleep) until the victim process is actually gone.
    while victim.poll() is None and time.time() < deadline + 10:
        time.sleep(0.01)
    assert victim.poll() is not None, "victim process did not die"

    # A sibling writer appends after the kill; if it could not take over the
    # lease / WAL the chain would fork or the append would fail.
    survivor = _run_child(_CHILD_APPENDER, db, "25", "survivor")
    assert survivor.returncode == 0, survivor.stderr[-800:]
    # A brand-new process also recovers and appends more.
    rec = _run_child(_CHILD_APPENDER, db, "25", "recover")
    assert rec.returncode == 0, rec.stderr[-800:]

    # The chain invariant: seq is exactly 1..N, contiguous, no gaps / dupes.
    conn = sqlite3.connect(db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        seqs = [r[0] for r in
                conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    finally:
        conn.close()
    assert seqs == list(range(1, total + 1)), (
        f"chain forked under kill: seq not contiguous (len={total})")
    ok, _ = AuditStore(db_path=db).verify_integrity()
    assert ok is True


def test_concurrent_kill_and_restart_keeps_chain_invariant(tmp_path):
    """Chaos/soak flavour: several writers run at once, one is killed mid-run,
    the others finish, then the killed writer is RESTARTED as a fresh process.
    The chain must stay contiguous and verify -- a fork here would be the
    HC-01 regression."""
    db = str(tmp_path / "audit.db")
    AuditStore(db_path=db).initialize()

    n_procs, per = 3, 20
    procs, progs = [], []
    for p in range(n_procs):
        prog = str(tmp_path / f"prog{p}.txt")
        progs.append(prog)
        procs.append(subprocess.Popen(
            [sys.executable, "-c", _CHILD_APPENDER_PROG % {"root": str(ROOT)},
             db, str(per), f"p{p}", prog],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, PYTHONPATH=str(ROOT)),
        ))

    # POLL until one writer has committed enough to be worth killing.
    deadline = time.time() + 30
    killed = -1
    while time.time() < deadline:
        for p, prog in enumerate(progs):
            try:
                v = int(open(prog).read())
            except (FileNotFoundError, ValueError):
                v = 0
            if v >= 3 and killed == -1:
                procs[p].kill()
                killed = p
                break
        if killed != -1:
            break
        time.sleep(0.01)
    if killed == -1:
        for pr in procs:
            pr.kill()
        raise AssertionError("no concurrent writer made progress to kill")

    # POLL until the killed writer is actually dead.
    while procs[killed].poll() is None and time.time() < deadline + 10:
        time.sleep(0.01)

    # Wait for the survivors.
    for p, pr in enumerate(procs):
        if p == killed:
            continue
        out, err = pr.communicate(timeout=120)
        assert pr.returncode == 0, f"writer p{p} failed: {err[-500:]}"

    # Restart the killed writer fresh -- proves recovery, not just survival.
    rec = _run_child(_CHILD_APPENDER, db, str(per), "recovered")
    assert rec.returncode == 0, rec.stderr[-800:]

    conn = sqlite3.connect(db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        seqs = [r[0] for r in
                conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    finally:
        conn.close()
    assert seqs == list(range(1, total + 1)), "chain forked under concurrent kill"
    ok, _ = AuditStore(db_path=db).verify_integrity()
    assert ok is True
