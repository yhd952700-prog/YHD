#!/usr/bin/env python3
"""verify_audit_chain_no_fork.py -- fail-closed monitor for the LIVE deployed
audit_store.db: it must NOT be forked (RCA-1 recurrence guard, F6).

This closes the gap left by the rest of the audit-integrity aggregate
(scripts/verify_audit_integrity.py): those gates drive HERMETIC temporary
stores or re-derive the seven JSON hash chains (HC-02..HC-08), but NONE of
them open the actual DEPLOYED SQLite audit_store.db. So a re-fork of the live
store -- the RCA-1 concurrency defect: concurrent writers assigned the same
sequence number because ``seq`` allocation was not atomic under a single-writer
fence -> duplicate ``seq`` numbers and broken hash-chain joins -- would go
UNNOTICED by CI.

This gate inspects the LIVE store READ-ONLY and asserts two things:

  1. the hash chain verifies ``ok=True``  (no broken joins / tamper /
     missing hash_alg) -- run through ``verify_chain_integrity()``, the
     store's declared read-only entry point, so this is the SAME production
     verification code, not a SQL approximation of it.
  2. ``total_rows - DISTINCT seq == 0``  (no fork signature -- every seq
     unique) -- identical SQL to ``AuditStore.duplicate_seq_count()``.

Fail-closed: any violation => non-zero exit (default 2). A missing live store
(no deployed DB in this environment) is reported as a SKIP (exit 0) -- honest,
not a silent pass; the aggregate flags it PASS (NOTE).

READ-ONLY POSTURE (enforced, not merely asserted)
-------------------------------------------------
``main()`` does NOT construct an ``AuditStore``. Constructing one runs
``_init_db()``, which issues and COMMITS DDL (schema creation, additive
migrations, ``CREATE UNIQUE INDEX uidx_seq``, anchor seeding) against the very
file this gate inspects -- see the warning in ``verify_chain_integrity()``'s
docstring in ``src/kernels/audit/__init__.py``. An earlier revision of this
script DID construct the store while claiming it "never issues a write"; that
claim was false (finding G-3) and is corrected here. Opening read-write would
additionally let SQLite perform WAL recovery on an unclean store, rewriting the
main DB file.

So the file is opened directly as ``file:...?mode=ro&immutable=1`` with
``PRAGMA query_only=ON`` (``immutable=1`` so it does NOT create ``-shm``/``-wal``
sidecars on a WAL store -- see :func:`open_read_only`), and if it cannot be
opened READ-ONLY the gate exits 2
instead of falling back to read-write: against a deployed -- or forensically
frozen -- audit store, mutating it would itself be one of the human-sovereign
actions (repair / restore / migrate / rebuild) frozen under HC-01 (HUMAN
DECISION PENDING / GO=BLOCKED). This gate is team-owned engineering (F1-F6)
under HC-01-Autonomous-Decision-Memo.md; it only prevents a SILENT re-fork of
the live store. It does NOT assert HC-01 is VERIFIED.

The ONLY place this script constructs an ``AuditStore`` is inside
``_build_clean_store()`` / ``_build_forked_store()``, which build THROWAWAY
temp stores used exclusively by ``selftest_detector()`` (finding G-2): because
the deployed DB is gitignored, CI SKIPs the live check, and without this
self-test the gate would be a green no-op that learns nothing. The self-test
proves the detector actually distinguishes a clean store from a forked one.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

from src.kernels.audit import (  # noqa: E402
    AuditStore,
    AuditEventType,
    AuditScope,
    verify_chain_integrity,
)


def _resolve_live_db_path() -> str:
    # Mirror AuditStore.__init__ default resolution so the gate watches the
    # SAME store production writes to.
    env = os.environ.get("AUDIT_DB_PATH")
    if env:
        return env
    return str(REPO / "audit_store.db")


def open_read_only(db_path: str):
    """Open a SQLite DB strictly read-only with ZERO filesystem side effects.

    Uses ``?mode=ro&immutable=1``. ``immutable=1`` is essential: on a WAL-mode
    database, a plain ``mode=ro`` open still creates a ``-shm`` (and possibly a
    ``-wal``) sidecar file on disk -- a filesystem write against the very
    evidence we claim to only inspect (a regression caught by
    ``test_gate_does_not_modify_the_target_store``). ``immutable=1`` tells
    SQLite the file will not change, so it skips all locking and shared-memory
    creation and does NOT write ``-shm``/``-wal``.

    Fail-closed: if the file cannot be opened read-only, the caller is expected
    to abort rather than fall back to read-write (which would let SQLite mutate
    deployed or forensically-frozen evidence -- a human-sovereign action frozen
    under HC-01).
    """
    uri = Path(os.path.abspath(db_path)).as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def check_store_read_only(conn) -> tuple[bool, str]:
    """Inspect an already-open read-only connection. Returns ``(ok, detail)``.

    Identical checks to the production verifier: full hash-chain verification
    plus the RCA-1 fork metric (``COUNT(*) - COUNT(DISTINCT seq) == 0``).
    """
    ok, total = verify_chain_integrity(conn)
    if not ok:
        return (
            False,
            "verify_integrity=False (broken joins / tamper / missing hash_alg)",
        )
    row = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT seq) FROM audit_events"
    ).fetchone()
    dup = int(row[0]) if row else 0
    if dup != 0:
        return (False, "duplicate_seq_count=%d (RCA-1 fork signature)" % dup)
    return (True, "ok on %d events; duplicate_seq_count=0" % total)


def _build_clean_store(db_path: str, n: int = 5) -> None:
    """Build a VALID clean store via the production writer (temp only)."""
    store = AuditStore(db_path=str(db_path))
    for i in range(n):
        store.log_event(
            AuditEventType.STATE_CHANGE, "p%d" % i, AuditScope.L0, "ok",
            details={"i": i}, correlation_id="c%d" % i,
        )
    store._conn.close()


def _build_forked_store(db_path: str, n: int = 5) -> None:
    """Build a clean store then inject an RCA-1 fork (duplicate ``seq``)."""
    _build_clean_store(db_path, n=n)
    raw = sqlite3.connect(str(db_path))
    # Drop the UNIQUE(seq) backstop so the injected duplicate is accepted,
    # mirroring a legacy forked store the live code refuses to open via the
    # index backstop (the gate must still alert on it).
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


def selftest_detector() -> tuple[bool, str]:
    """Prove the fork detector actually distinguishes clean from forked.

    Regression guard for finding G-2: the deployed DB is gitignored so CI SKIPs
    the live check -- without this self-test the gate would be a green no-op
    that learns nothing. It builds THROWAWAY temp stores (never the live
    evidence) and asserts ``check_store_read_only()`` reports the clean one
    healthy and the forked one broken. A neutered/wrong detector therefore
    fails the self-test, which fails the gate (fail-closed).
    """
    try:
        with tempfile.TemporaryDirectory(prefix="liuhao-nofork-selftest-") as tmp:
            clean = Path(tmp) / "clean.db"
            forked = Path(tmp) / "forked.db"
            _build_clean_store(str(clean), n=5)
            _build_forked_store(str(forked), n=5)

            cconn = open_read_only(str(clean))
            try:
                cok, cdetail = check_store_read_only(cconn)
            finally:
                cconn.close()
            if not cok:
                return (
                    False,
                    "BROKEN: detector reports a CLEAN store as broken (%s)"
                    % cdetail,
                )

            fconn = open_read_only(str(forked))
            try:
                fok, fdetail = check_store_read_only(fconn)
            finally:
                fconn.close()
            if fok:
                return (
                    False,
                    "BROKEN: detector FAILS to detect a forked store (%s) -- "
                    "would silently green-light a re-fork" % fdetail,
                )
            return (
                True,
                "detector distinguishes clean (healthy) from forked (broken)",
            )
    except Exception as exc:  # noqa: BLE001
        return (False, "BROKEN: self-test raised: %s" % exc)


def main() -> int:
    db_path = _resolve_live_db_path()
    rc = 0
    if not os.path.exists(db_path):
        # Nothing deployed to verify in this environment. Honest SKIP -- the
        # aggregate's NOTE_MARKERS will flag this as PASS (NOTE), never a
        # silent PASS.
        print(
            "NO-FORK GATE SKIP: live audit store not present at %s "
            "(no deployed DB to verify in this environment)" % db_path
        )
    else:
        try:
            conn = open_read_only(db_path)
        except sqlite3.Error as exc:
            # Fail-closed AND non-destructive: rather than retry read-write
            # (which would let SQLite migrate/recover deployed or frozen
            # evidence), refuse and report loudly.
            print(
                "NO-FORK GATE FAIL: cannot open live store READ-ONLY at %s: %s. "
                "Refusing to fall back to read-write." % (db_path, exc)
            )
            return 2
        try:
            ok, detail = check_store_read_only(conn)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if not ok:
            print(
                "NO-FORK GATE FAIL: %s on live store (%s) -- RCA-1 fork-or-"
                "tamper signature; escalate, do not silently pass"
                % (detail, db_path)
            )
            rc = 2
        else:
            print(
                "NO-FORK GATE PASS: live audit store at %s is not forked (%s)"
                % (db_path, detail)
            )

    # Always self-test the detector so CI is never a green no-op (finding G-2).
    sok, sdetail = selftest_detector()
    if not sok:
        print("NO-FORK GATE SELF-TEST FAIL: %s" % sdetail)
        return 2
    print("NO-FORK GATE SELF-TEST PASS: %s" % sdetail)
    return rc


if __name__ == "__main__":
    sys.exit(main())
