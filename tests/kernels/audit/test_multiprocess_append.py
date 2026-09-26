"""Multi-process concurrency against ONE audit database (U39 / ADR Option B).

Before this change a second live process could not obtain the writer lease at
all, so the fail-closed gate denied every governed action in it -- a child
process could not even build the capability registry, because
``capability.register`` is itself a governed action.

These tests assert the property that was previously impossible, while holding
the HC-01 anti-fork guarantees that motivated the lease in the first place:

  * several live processes can append to one database
  * no lost append          (row count is exact)
  * no duplicate seq        (seq is a permutation of 1..N)
  * no gap in seq           (contiguous, so no event vanished)
  * no broken chain join    (verify_integrity)
  * chain_state anchor matches the tail

Failure to keep ANY of these is a fork -- the exact corruption HC-01 already
suffered (284 broken joins / 169 duplicate seq).
"""
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


_CHILD_APPEND = """
import sys
sys.path.insert(0, %(root)r)
from src.kernels.audit import AuditStore, AuditEventType, AuditScope

db, count, tag = sys.argv[1], int(sys.argv[2]), sys.argv[3]
store = AuditStore(db_path=db)
store.initialize()
for i in range(count):
    store.log_event(
        AuditEventType.STATE_CHANGE,
        "proc-" + tag,
        AuditScope.L0,
        "success",
        {"i": i, "proc": tag},
        tag + "-" + str(i),
    )
print("OK")
"""

_CHILD_CAPABILITY = """
import sys
sys.path.insert(0, %(root)r)
from src.kernels.capability import get_capability_registry
get_capability_registry()
print("REGISTRY-OK")
"""


def _run_child(source: str, *args, audit_db: str = None,
               timeout: float = 180.0) -> subprocess.CompletedProcess:
    """Run a child process. ``audit_db`` pins the child's AUDIT_DB_PATH so it
    contends for the SAME database as the parent -- without that, parent and
    child silently use different databases and the test proves nothing."""
    script_src = source % {"root": str(ROOT)}
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    if audit_db is not None:
        env["AUDIT_DB_PATH"] = audit_db
    return subprocess.run(
        [sys.executable, "-c", script_src, *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )


def _spawn_appenders(db: str, processes: int, per_process: int) -> None:
    """Run ``processes`` concurrent child processes, each appending."""
    procs = []
    for p in range(processes):
        # Launch them all, then wait -- real concurrency, not sequential.
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", _CHILD_APPEND % {"root": str(ROOT)},
                 db, str(per_process), f"p{p}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=dict(os.environ, PYTHONPATH=str(ROOT)),
            )
        )
    failures = []
    for idx, proc in enumerate(procs):
        out, err = proc.communicate(timeout=180)
        if proc.returncode != 0:
            failures.append(f"child p{idx} rc={proc.returncode} stderr={err[-500:]}")
    assert not failures, "child processes failed:\n" + "\n".join(failures)


def test_n_processes_append_without_forking_the_chain(tmp_path):
    """The core U39 property: N live processes append to one DB, chain intact."""
    db = str(tmp_path / "audit.db")
    processes, per_process = 4, 25
    _spawn_appenders(db, processes, per_process)

    expected = processes * per_process
    conn = sqlite3.connect(db)

    rows = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert rows == expected, f"lost appends: expected {expected}, got {rows}"

    seqs = [r[0] for r in conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    assert seqs == list(range(1, expected + 1)), (
        "seq must be exactly 1..N with no duplicates and no gaps "
        "(a duplicate or a gap means the chain forked)"
    )

    # Linkage: every event's prev_event_hash equals the previous event's hash.
    broken = conn.execute(
        """
        SELECT COUNT(*) FROM audit_events e
        WHERE e.seq > 1 AND e.prev_event_hash IS NOT DISTINCT FROM NULL
        """
    ).fetchone()[0]
    assert broken == 0, "events after the first must carry a prev_event_hash"

    mismatch = conn.execute(
        """
        SELECT COUNT(*) FROM audit_events e
        JOIN audit_events p ON p.seq = e.seq - 1
        WHERE e.prev_event_hash <> p.event_hash
        """
    ).fetchone()[0]
    assert mismatch == 0, f"{mismatch} broken joins -- the chain forked"

    # Tail anchor must agree with the last stored event.
    anchor = conn.execute(
        "SELECT last_seq, last_hash FROM chain_state WHERE id = 1"
    ).fetchone()
    tail = conn.execute(
        "SELECT seq, event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    assert anchor == tail, f"chain_state anchor {anchor} != tail {tail}"
    conn.close()

    # And the store's own verifier must agree.
    from src.kernels.audit import AuditStore
    store = AuditStore(db_path=db)
    store.initialize()
    ok, _checked = store.verify_integrity()
    assert ok is True, "verify_integrity() must pass after concurrent appends"


def test_second_process_can_build_the_capability_registry(tmp_path):
    """The exact U39 symptom. A second live process used to be denied
    ``capability.register`` and died before it could build the registry.

    The parent appends FIRST on purpose: under the old process-lifetime lease
    that left the parent holding the lease, so the child was refused. Without
    the parent write this test would pass either way and prove nothing.
    """
    db = str(tmp_path / "audit.db")
    from src.kernels.audit import AuditStore, AuditEventType, AuditScope

    parent = AuditStore(db_path=db)
    parent.initialize()
    parent.log_event(
        AuditEventType.STATE_CHANGE, "parent", AuditScope.L0, "success",
        {"from": "parent"}, "c-warmup",
    )

    proc = _run_child(_CHILD_CAPABILITY, audit_db=db)
    assert proc.returncode == 0, (
        "child process could not build the capability registry "
        f"(this is the U39 regression): rc={proc.returncode}\n{proc.stderr[-800:]}"
    )
    assert "REGISTRY-OK" in proc.stdout


def test_parent_and_child_both_append_to_one_database(tmp_path):
    """Parent appends, then a child appends to the SAME database, then the
    parent appends again. All must land, in one contiguous chain."""
    db = str(tmp_path / "audit.db")
    from src.kernels.audit import AuditStore, AuditEventType, AuditScope

    parent = AuditStore(db_path=db)
    parent.initialize()
    parent.log_event(
        AuditEventType.STATE_CHANGE, "parent", AuditScope.L0, "success",
        {"from": "parent", "i": 0}, "c-parent-0",
    )

    proc = _run_child(_CHILD_APPEND, db, "3", "child")
    assert proc.returncode == 0, f"child failed: {proc.stderr[-800:]}"

    # Parent must still be able to append after the child took over the lease.
    parent.log_event(
        AuditEventType.STATE_CHANGE, "parent", AuditScope.L0, "success",
        {"from": "parent", "i": 1}, "c-parent-1",
    )

    conn = sqlite3.connect(db)
    seqs = [r[0] for r in conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    conn.close()
    assert seqs == [1, 2, 3, 4, 5], f"expected contiguous seq 1..5, got {seqs}"

    ok, _ = AuditStore(db_path=db).verify_integrity()
    assert ok is True


@pytest.mark.parametrize("processes", [2, 6])
def test_concurrency_scales_without_corruption(tmp_path, processes):
    """More processes must not degrade integrity (only contention)."""
    db = str(tmp_path / f"audit_{processes}.db")
    _spawn_appenders(db, processes, 15)
    conn = sqlite3.connect(db)
    seqs = [r[0] for r in conn.execute("SELECT seq FROM audit_events ORDER BY seq")]
    conn.close()
    assert seqs == list(range(1, processes * 15 + 1))
    from src.kernels.audit import AuditStore
    ok, _ = AuditStore(db_path=db).verify_integrity()
    assert ok is True
