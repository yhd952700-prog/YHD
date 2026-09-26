"""Option C1 -- batched atomic append (src/kernels/audit: log_event_batch).

Batching buys ~10x throughput (2k -> 23k appends/sec at batch=250), but it is
only acceptable if it cannot damage the evidence chain. These tests pin the
semantics that make it safe:

  * order within a batch is the caller's list order, seq stays contiguous
  * a batch is all-or-nothing -- no partial batch, ever
  * re-submitting a committed batch is idempotent (no duplicate, no seq burn)
  * a crash at ANY point leaves the chain intact AND leaves no partial batch
  * batches and single appends interleave correctly
  * several processes / several threads batching into one store stay fork-free

Why "no partial batch" is tested the way it is
----------------------------------------------
A weak version of the crash test would kill a child and then assert only that
the chain still verifies. That proves almost nothing: an empty database also
verifies. Here the child appends *numbered batches* and prints each completed
batch index, so the test can assert the strong property --

    every batch index that appears in the database appears COMPLETELY
    (all N of its events, and no batch is half-present)

-- together with a deterministic variant that hard-kills the writer with
os._exit() *after* the INSERTs but *before* COMMIT.
"""
import os
import pathlib
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.kernels.audit import (
    AuditBatchResult, AuditEvent, AuditEventType, AuditScope, AuditStore,
)

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _ev(i, event_id=None):
    return AuditEvent(
        event_id=event_id or f"e{i}",
        event_type=AuditEventType.STATE_CHANGE,
        principal_id="batch-bench",
        scope=AuditScope.L0,
        timestamp=time.time() + i * 1e-6,
        correlation_id=f"c{i}",
        outcome="ok",
        details={"i": i},
    )


def _table_exists(conn, name):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchall()
    return bool(rows)


def _open_state(db):
    """(seqs, broken_joins, anchor) -- (None, None, None) if not yet created.

    A killed child may die before it ever created the schema; asserting on a
    table that was never created would be an infrastructure error, not a
    product failure. Returning None makes that distinction explicit instead of
    silently passing.
    """
    if not os.path.exists(db):
        return None, None, None
    conn = sqlite3.connect(db)
    try:
        if not _table_exists(conn, "audit_events"):
            return None, None, None
        seqs = [r[0] for r in conn.execute(
            "SELECT seq FROM audit_events ORDER BY seq")]
        broken = conn.execute(
            """SELECT COUNT(*) FROM audit_events e JOIN audit_events p
               ON p.seq = e.seq - 1 WHERE e.prev_event_hash <> p.event_hash"""
        ).fetchone()[0]
        anchor = None
        if _table_exists(conn, "chain_state"):
            anchor = conn.execute(
                "SELECT last_seq, last_hash FROM chain_state WHERE id=1"
            ).fetchone()
        return seqs, broken, anchor
    finally:
        conn.close()


def test_batch_appends_in_order_with_a_contiguous_chain(tmp_path):
    db = str(tmp_path / "a.db")
    store = AuditStore(db_path=db)
    store.initialize()
    result = store.log_event_batch([_ev(i) for i in range(50)])

    assert isinstance(result, AuditBatchResult)
    assert len(result.appended) == 50
    assert result.duplicates == []

    seqs, broken, _ = _open_state(db)
    assert seqs == list(range(1, 51)), "seq must be 1..50"
    assert broken == 0
    ok, _ = store.verify_integrity()
    assert ok is True


def test_batch_order_is_the_callers_list_order(tmp_path):
    """The only defensible ordering rule: seq follows the caller's list.

    Deliberately unsorted input -- sorting by timestamp or by id would be a
    different (and nondeterministic) semantic.
    """
    db = str(tmp_path / "order.db")
    store = AuditStore(db_path=db)
    store.initialize()
    order = ["zeta", "alpha", "mu", "beta", "omega"]
    store.log_event_batch([_ev(i, eid) for i, eid in enumerate(order)])

    conn = sqlite3.connect(db)
    committed = [r[0] for r in conn.execute(
        "SELECT event_id FROM audit_events ORDER BY seq")]
    conn.close()
    assert committed == order, "batch order must be the caller's list order"


def test_batch_is_all_or_nothing(tmp_path):
    """A failure part-way through a batch must commit NOTHING."""
    db = str(tmp_path / "b.db")
    store = AuditStore(db_path=db)
    store.initialize()
    store.log_event_batch([_ev(0, "seed-0")])

    events = [_ev(i, f"batch-{i}") for i in range(20)]
    # Make the LAST event unhashable: compute_hash raises for an unknown alg.
    events[-1].hash_alg = "not-a-real-algorithm"

    with pytest.raises(ValueError):
        store.log_event_batch(events)

    seqs, broken, anchor = _open_state(db)
    assert seqs == [1], f"failed batch must commit nothing, found {len(seqs)} rows"
    assert broken == 0
    assert anchor[0] == 1, "a rolled-back batch must not move the anchor"
    ok, _ = store.verify_integrity()
    assert ok is True


def test_resubmitting_a_committed_batch_is_idempotent(tmp_path):
    """Retry after an uncertain outcome must not duplicate evidence."""
    db = str(tmp_path / "c.db")
    store = AuditStore(db_path=db)
    store.initialize()
    events = [_ev(i, f"idem-{i}") for i in range(10)]

    first = store.log_event_batch(events)
    assert len(first.appended) == 10

    # Same event_ids again -> all recognised as duplicates, nothing re-appended.
    again_events = [_ev(i, f"idem-{i}") for i in range(10)]
    second = store.log_event_batch(again_events)
    assert second.appended == []
    assert sorted(second.duplicates) == sorted([f"idem-{i}" for i in range(10)])

    seqs, _, _ = _open_state(db)
    assert seqs == list(range(1, 11)), "retry must not create rows or burn seq"
    ok, _ = store.verify_integrity()
    assert ok is True


def test_duplicates_inside_one_batch_are_skipped(tmp_path):
    db = str(tmp_path / "d.db")
    store = AuditStore(db_path=db)
    store.initialize()
    result = store.log_event_batch([_ev(0, "dup"), _ev(1, "dup"), _ev(2, "uniq")])
    assert len(result.appended) == 2
    assert result.duplicates == ["dup"]
    seqs, _, _ = _open_state(db)
    assert seqs == [1, 2], "a duplicate inside the batch must not consume a seq"
    ok, _ = store.verify_integrity()
    assert ok is True


def test_empty_batch_is_a_noop(tmp_path):
    store = AuditStore(db_path=str(tmp_path / "e.db"))
    store.initialize()
    result = store.log_event_batch([])
    assert result.appended == [] and result.duplicates == []
    ok, _ = store.verify_integrity()
    assert ok is True


def test_batches_and_single_appends_interleave(tmp_path):
    """1 single + 5 batch + 1 single + 4 batch = 11 events, one chain."""
    db = str(tmp_path / "f.db")
    store = AuditStore(db_path=db)
    store.initialize()
    store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                    details={"i": 0}, correlation_id="s0")
    store.log_event_batch([_ev(i, f"b{i}") for i in range(1, 6)])
    store.log_event(AuditEventType.STATE_CHANGE, "p", AuditScope.L0, "ok",
                    details={"i": 6}, correlation_id="s6")
    store.log_event_batch([_ev(i, f"c{i}") for i in range(7, 11)])

    seqs, broken, anchor = _open_state(db)
    assert seqs == list(range(1, 12)), "1+5+1+4 appends must produce seq 1..11"
    assert broken == 0
    assert anchor[0] == 11
    ok, _ = store.verify_integrity()
    assert ok is True


# --------------------------------------------------------------------------- #
# Child-process sources. __ROOT__ is substituted (never %-formatted) because
# the sources contain %-format specifiers of their own.
# --------------------------------------------------------------------------- #

_CHILD_LOOP = """
import os, sys, time
sys.path.insert(0, __ROOT__)
from src.kernels.audit import AuditStore, AuditEvent, AuditEventType, AuditScope
db, prefix, per, step = (sys.argv[1], sys.argv[2],
                         int(sys.argv[3]), int(sys.argv[4]))
s = AuditStore(db_path=db); s.initialize()

class ProgressProxy(object):
    # Emits a marker every `step` rows so the parent can kill the child at a
    # known point INSIDE a batch, not merely at its boundary.
    def __init__(self, conn, step):
        self._conn = conn
        self._step = step
        self._n = 0
    def execute(self, sql, *a):
        low = sql.lstrip().lower()
        if low.startswith("insert into audit_events"):
            self._n += 1
            if self._n % self._step == 0:
                print("PROGRESS %d" % self._n, flush=True)
        return self._conn.execute(sql, *a)
    def commit(self):
        return self._conn.commit()
    def rollback(self):
        return self._conn.rollback()
    def __getattr__(self, name):
        return getattr(self._conn, name)

s._conn = ProgressProxy(s._conn, step)

k = 0
while True:
    print("BEGIN-BATCH %d" % k, flush=True)
    s._conn._n = 0
    evs = [AuditEvent(event_id="%s-%d-%d" % (prefix, k, j),
                      event_type=AuditEventType.STATE_CHANGE,
                      principal_id=prefix, scope=AuditScope.L0,
                      timestamp=time.time(), correlation_id="%s-%d" % (prefix, k),
                      outcome="ok", details={"k": k, "j": j})
           for j in range(per)]
    s.log_event_batch(evs)
    print("BATCH %d" % k, flush=True)
    k += 1
"""

# One-shot variant of the loop child: append exactly `per` events, then exit.
_CHILD_LOOP_ONE_SHOT = _CHILD_LOOP.replace(
    '    print("BATCH %d" % k, flush=True)\n    k += 1\n',
    '    print("BATCH %d" % k, flush=True)\n    break\n',
)

_CHILD_KILL_MID_BATCH = """
import os, sys, time
sys.path.insert(0, __ROOT__)
from src.kernels.audit import AuditStore, AuditEvent, AuditEventType, AuditScope
db, n, kill_at = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
s = AuditStore(db_path=db); s.initialize()

def one(eid, i):
    return AuditEvent(event_id=eid, event_type=AuditEventType.STATE_CHANGE,
                      principal_id="doomed", scope=AuditScope.L0,
                      timestamp=time.time(), correlation_id="d%d" % i,
                      outcome="ok", details={"i": i})

# Seed one COMMITTED event, so "zero rows" cannot be confused with a fresh DB.
s.log_event_batch([one("seed", -1)])

class KillProxy(object):
    # sqlite3.Connection attributes are read-only, so the crash point is
    # injected by wrapping the connection instead.
    def __init__(self, conn, limit):
        self._conn = conn
        self._limit = limit
        self._n = 0
    def execute(self, sql, *a):
        low = sql.lstrip().lower()
        if low.startswith("insert into audit_events"):
            self._n += 1
            # Hard crash (SIGKILL-like: os._exit skips all cleanup) in the
            # MIDDLE of the batch: after `kill_at` rows were handed to SQLite,
            # long before COMMIT. This is precisely the window in which a
            # per-event-commit implementation has already made rows durable.
            if self._n == self._limit:
                sys.stdout.flush()
                os._exit(9)
        return self._conn.execute(sql, *a)
    def commit(self):
        return self._conn.commit()
    def rollback(self):
        return self._conn.rollback()
    def __getattr__(self, name):
        return getattr(self._conn, name)

s._conn = KillProxy(s._conn, kill_at)

s.log_event_batch([one("doomed-%d" % i, i) for i in range(n)])
print("UNREACHABLE", flush=True)
"""


def _child(source, *args, audit_db=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    if audit_db:
        env["AUDIT_DB_PATH"] = audit_db
    return subprocess.Popen(
        [sys.executable, "-c", source.replace("__ROOT__", repr(str(ROOT))), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )


def _batch_completeness(db, per):
    """Map batch index -> how many of its `per` events are committed."""
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT event_id FROM audit_events").fetchall()
    conn.close()
    counts = {}
    for (eid,) in rows:
        # event_id format: "<prefix>-<batch>-<j>"
        parts = eid.rsplit("-", 2)
        if len(parts) != 3:
            continue
        try:
            k = int(parts[1])
        except ValueError:
            continue
        counts[k] = counts.get(k, 0) + 1
    return counts


@pytest.mark.parametrize("per", [500, 2000])
def test_kill_mid_batch_leaves_no_partial_batch(tmp_path, per):
    """Chaos with a deterministic kill point: inside batch number 3.

    The child prints BEGIN-BATCH k before each batch, so the parent can kill it
    while a *known* batch is in flight -- a timing-only sleep would land
    mid-batch only by luck.

    The strong assertion is not merely "the chain verifies" (an empty DB also
    verifies) but: every batch index present in the database is present
    COMPLETELY. A partial batch is a batch index whose count is neither 0 nor
    `per`.
    """
    target = 3
    step = max(1, per // 4)          # 4 progress markers per batch
    marks_into_batch = 2             # kill at the 2nd marker == mid-batch
    db = str(tmp_path / f"crash_{per}.db")
    proc = _child(_CHILD_LOOP, db, "w", str(per), str(step), audit_db=db)

    deadline = time.time() + 120
    hit = False
    marks = 0
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        text = line.strip()
        if not hit:
            if text == f"BEGIN-BATCH {target}":
                hit = True
            continue
        if text.startswith("PROGRESS"):
            marks += 1
            if marks >= marks_into_batch:
                break
    assert hit and marks >= marks_into_batch, (
        "child never reached mid-batch -- test is vacuous"
    )
    proc.kill()
    proc.wait(timeout=60)

    seqs, broken, anchor = _open_state(db)
    assert seqs is not None, "the child reached batch 3, so the schema must exist"
    counts = _batch_completeness(db, per)
    incomplete = {k: c for k, c in counts.items() if c != per}
    assert not incomplete, f"partial batch detected: {incomplete}"
    assert len(counts) >= target, (
        f"the kill was too early to be meaningful (only {len(counts)} batches)"
    )
    assert seqs == list(range(1, len(seqs) + 1)), (
        "seq must be contiguous after a crash -- a gap means a partial batch"
    )
    assert broken == 0, "crash must not break the chain"
    assert len(seqs) == sum(counts.values()), "every row belongs to a batch"
    if anchor:
        assert anchor[0] == seqs[-1], "chain_state anchor must match the tail"

    # And the store must still be usable afterwards (lease recoverable).
    store = AuditStore(db_path=db)
    store.initialize()
    store.log_event_batch([_ev(999, "after-crash")])
    ok, _ = store.verify_integrity()
    assert ok is True


@pytest.mark.parametrize("kill_at", [1, 10, 250])
def test_hard_kill_mid_batch_commits_nothing(tmp_path, kill_at):
    """Deterministic crash in the MIDDLE of a batch: nothing may be durable.

    This is the test that carries the discrimination. A per-event-commit
    ("naive batching") implementation leaves `kill_at - 1` rows committed here,
    so it fails; the atomic implementation leaves only the seeded event.
    """
    db = str(tmp_path / f"kill_mid_{kill_at}.db")
    proc = _child(_CHILD_KILL_MID_BATCH, db, "500", str(kill_at), audit_db=db)
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 9, (
        f"child must die by os._exit(9), got {proc.returncode}: {err[-400:]}"
    )
    assert "UNREACHABLE" not in out, "child must not reach the code after commit"

    seqs, broken, anchor = _open_state(db)
    assert seqs == [1], (
        f"a crash before COMMIT must leave only the seeded event, found {seqs}"
    )
    assert broken == 0
    assert anchor[0] == 1

    # Recovery: the next batch must continue the chain, not overwrite it.
    store = AuditStore(db_path=db)
    store.initialize()
    res = store.log_event_batch([_ev(1, "post-crash")])
    assert len(res.appended) == 1
    seqs, broken, _ = _open_state(db)
    assert seqs == [1, 2]
    assert broken == 0
    ok, _ = store.verify_integrity()
    assert ok is True


def test_multi_process_batches_stay_fork_free(tmp_path):
    """Three processes, each 150 events, one database: 450 rows, one chain.

    Each child uses a UNIQUE event_id prefix -- if they shared ids, C1's
    idempotency would (correctly) de-duplicate them and the test would measure
    the dedup rule instead of the fork property.
    """
    db = str(tmp_path / "g.db")
    per = 150
    procs = [
        _child(_CHILD_LOOP_ONE_SHOT, db, f"p{p}", str(per), str(per), audit_db=db)
        for p in range(3)
    ]
    errs = []
    for p in procs:
        out, err = p.communicate(timeout=300)
        if p.returncode != 0:
            errs.append(err[-400:])
    assert not errs, "children failed:\n" + "\n".join(errs)

    seqs, broken, _ = _open_state(db)
    assert len(seqs) == 450, f"expected 3x150 committed events, got {len(seqs)}"
    assert seqs == list(range(1, 451)), "concurrent batches must not fork the chain"
    assert broken == 0
    ok, _ = AuditStore(db_path=db).verify_integrity()
    assert ok is True


def test_many_threads_batching_into_one_store_stay_fork_free(tmp_path):
    """N threads sharing one AuditStore (what FastAPI's thread pool does)."""
    db = str(tmp_path / "h.db")
    store = AuditStore(db_path=db)
    store.initialize()

    workers, per = 8, 40
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [
            pool.submit(
                store.log_event_batch,
                [_ev(i, f"t{w}-{i}") for i in range(per)],
            )
            for w in range(workers)
        ]
        results = [f.result() for f in futs]

    assert sum(len(r.appended) for r in results) == workers * per
    assert all(r.duplicates == [] for r in results)

    seqs, broken, _ = _open_state(db)
    assert seqs == list(range(1, workers * per + 1))
    assert broken == 0
    ok, _ = store.verify_integrity()
    assert ok is True
